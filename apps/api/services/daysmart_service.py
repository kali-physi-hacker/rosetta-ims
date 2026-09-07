"""Daysmart adapter for purchase orders (CIS-09, phase 2).

The ONLY module that speaks Daysmart's language: URLs, login recipe,
headers, envelope shapes. Everything it returns has passed through the
``schemas.purchase_orders`` contracts — raw Daysmart dicts do not escape.

Failure taxonomy (the flow layer dispatches on these):

- ``DaysmartUnavailable``   — the request never took effect (connection
  refused, 5xx). Retried here with backoff; safe by definition.
- ``DaysmartTimeout``       — the request MAY have landed. Never retried at
  this layer: exactly one attempt, reported up. Only the flow layer, which
  can run the snapshot-diff, may follow up — a blind re-send here is how
  duplicate POs are made.
- ``DaysmartAuthFailure``   — the API token was rejected, or a legacy-only
  route could not establish its web session. Retrying won't fix credentials.
- ``DaysmartMalformedResponse`` — a reply that isn't the JSON envelope at
  all (the classic case: login HTML with a 200). No payload → no order to
  attach a validation issue to → this is transport, not shape. Shape
  problems inside a well-formed envelope come back as named issues in the
  parse result instead, per the DEV-305 contract.

Exceptions for transport, issues for shape — the same boundary the
contracts draw.

Configuration is constructor-explicit; ``DaysmartService.from_env()`` is
the production convenience. The session factory is injectable so tests run
against a scripted fake — no test traffic ever reaches Daysmart.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from schemas.purchase_orders import (
    DaysmartOrderItemPageMeta,
    DaysmartOrderItemPageParseResult,
    parse_daysmart_order_item_page,
    DaysmartCreatedOrderParseResult,
    DaysmartOrderPageParseResult,
    parse_daysmart_created_order,
    parse_daysmart_order_page,
    DaysmartReceiptDetailParseResult,
    DaysmartReceiptPageParseResult,
    DaysmartReceiptRowsParseResult,
    ROW_IDENTITY_PATHS,
    parse_daysmart_receipt_detail,
    parse_daysmart_receipt_page,
)

logger = logging.getLogger(__name__)

#: Daysmart's own endpoint spelling, typo included — it is load-bearing.
_FIND_DISTRIBUTOR_PATH = "/barramundi/account/find-distibutor"


@dataclass(frozen=True)
class DaysmartOrderItemRef:
    """The two identities needed to backfill one local PO line."""

    daysmart_item_id: int       # purchase_order_item_id on later writes
    inventory_item_id: str      # stable DIT-... inventory identity


@dataclass(frozen=True)
class DaysmartReceiptItemRef:
    """Identity of one receipt row auto-created from a selected PO line."""

    daysmart_receipt_item_id: int
    daysmart_po_item_id: int | None
    purchase_order_id: int | None
    post_to_item_id: str | None


class DaysmartError(Exception):
    """Base for all Daysmart transport failures."""


class DaysmartUnavailable(DaysmartError):
    """Daysmart could not be reached / kept failing; the request had no effect."""


class DaysmartTimeout(DaysmartError):
    """No answer in time. The request MAY have landed — resolve via the
    snapshot-diff at the flow layer, never by re-sending blind."""


class DaysmartAuthFailure(DaysmartError):
    """Login produced no session, or rejection persisted after re-login."""


class DaysmartMalformedResponse(DaysmartError):
    """A reply that is not the known JSON envelope."""


class DaysmartRefused(DaysmartError):
    """Daysmart answered with a WELL-FORMED "no" — the envelope parsed but
    ``response.message.code`` was not 200 (or the ``messages.errors``
    dialect carried errors). Neither transport failure nor drift: the
    request reached Daysmart and was rejected on its merits. Never retried
    anywhere — the same request gets the same no."""


def _default_session_factory() -> requests.Session:
    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    })
    return session


class DaysmartService:
    """Authenticated client for the Daysmart browser API ("barramundi")."""

    def __init__(
        self,
        *,
        base_url: str,
        username: str,
        password: str,
        vetter_token: str,
        session_factory: Callable[[], Any] = _default_session_factory,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 2.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.vetter_token = vetter_token
        self._session_factory = session_factory
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds
        self.timeout_seconds = timeout_seconds
        self._session: Any = None
        # True only after the browser-style business switch has produced a
        # fresh token AND the legacy-session cookies used by /apps/index.php
        # are present.  Kept separately from authentication because the
        # /barramundi API can work while the legacy endpoints still 401.
        #: Serializes login/re-login: the service is shared across FastAPI's
        #: threadpool, and two threads discovering an expired session at once
        #: must not both re-authenticate (or tear the session out from under
        #: each other mid-flight).
        self._login_lock = threading.Lock()

    @classmethod
    def from_env(cls, **overrides: Any) -> "DaysmartService":
        """Production wiring: credentials from the environment."""
        kwargs: dict[str, Any] = dict(
            base_url=os.environ.get("DAYSMART_VETTER_BASE_URL", "https://vettersoftware.com"),
            username=os.environ.get("DAYSMART_USERNAME", ""),
            password=os.environ.get("DAYSMART_PASSWORD", ""),
            vetter_token=os.environ.get("DAYSMART_API_TOKEN", ""),
        )
        kwargs.update(overrides)
        return cls(**kwargs)

    # ------------------------------------------------------------------ auth

    def _login(self) -> None:
        """Create the web session the browser API requires. Raises typed
        errors; on success ``self._session`` is authenticated."""
        session = self._session_factory()
        try:
            response = session.post(
                f"{self.base_url}/apps/index.php/welcome/validateLogin",
                data={
                    "fm_login_referrer": "october",
                    "fm_login_redirect": "",
                    "fm_login_redirect_to": "",
                    "fm_login_email": self.username,
                    "fm_login_password": self.password,
                },
                headers={
                    "Accept": "application/json, text/javascript, */*; q=0.01",
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                    "Origin": self.base_url,
                    "Referer": f"{self.base_url}/apps/index.php/october/login",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=self.timeout_seconds,
            )
        except requests.Timeout as exc:
            raise DaysmartTimeout(f"login timed out: {exc}") from exc
        except requests.RequestException as exc:
            raise DaysmartUnavailable(f"login request failed: {exc}") from exc

        if getattr(response, "status_code", 200) >= 400:
            raise DaysmartAuthFailure(f"login rejected with HTTP {response.status_code}")
        try:
            login_body = response.json()
        except ValueError as exc:
            raise DaysmartAuthFailure(
                "login returned HTTP 200 without the expected JSON result") from exc
        if not isinstance(login_body, dict) or login_body.get("success") is not True:
            # DaySmart issues a vettersession cookie even when credentials are
            # rejected.  The JSON success flag—not cookie presence—is the
            # authentication proof.  Do not echo credentials or arbitrary
            # response material into logs/errors.
            reason = login_body.get("message") if isinstance(login_body, dict) else None
            if reason == "Invalid email or password":
                detail = "invalid DAYSMART_USERNAME or DAYSMART_PASSWORD"
            else:
                detail = "the login response did not confirm success"
            raise DaysmartAuthFailure(f"login rejected: {detail}")
        if "vettersession" not in session.cookies:
            raise DaysmartAuthFailure(
                "login returned no vettersession cookie — web credentials "
                "(DAYSMART_USERNAME/PASSWORD) are wrong or the login flow changed"
            )

        # Warm the session the way the browser does before API calls.
        # NOTE: path must not contain the substring "order" — tests (and
        # sanity) count order-endpoint calls by substring.
        try:
            session.get(f"{self.base_url}/view/inventory/dashboard", timeout=self.timeout_seconds)
        except requests.Timeout as exc:
            raise DaysmartTimeout(f"post-login warm-up timed out: {exc}") from exc
        except requests.RequestException as exc:
            raise DaysmartUnavailable(f"post-login warm-up failed: {exc}") from exc

        self._session = session

    def _ensure_session(self) -> None:
        if self._session is not None:
            return
        with self._login_lock:
            if self._session is None:  # re-check under the lock
                self._login()

    def _drop_session(self) -> None:
        with self._login_lock:
            self._session = None

    # ------------------------------------------------------------- transport

    def _api_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.vetter_token:
            headers["vetter-token"] = self.vetter_token
        return headers

    def _request_json(self, method: str, path: str, *, params: dict | None = None,
                      json_body: dict | list | None = None, attempts: int | None = None,
                      allow_list: bool = False):
        """One logical API call with the full failure policy applied.

        ``/barramundi`` is token-authenticated and must not be gated on the
        separate PHP web login. Legacy ``/apps/index.php`` paths continue
        through the browser-session lifecycle below.

        Transient failures (connection, 5xx) retry up to ``attempts``
        (default ``max_attempts``) with linear backoff. A 401/403 triggers
        exactly one re-login. A timeout raises immediately — never retried
        here (see module doc).

        WRITES pass ``attempts=1``: a mutating request is never auto-retried
        on ANY transport failure, because a connection reset (unlike a
        refused connection) can arrive after the request was already sent —
        and a retry then duplicates the mutation. Only the flow layer, which
        can verify via the snapshot-diff, may resubmit.
        """
        if path.startswith("/barramundi/"):
            return self._request_token_json(
                method, path, params=params, json_body=json_body,
                attempts=attempts, allow_list=allow_list)

        max_attempts = attempts if attempts is not None else self.max_attempts
        url = f"{self.base_url}{path}"
        auth_retried = False
        attempt = 0
        while True:
            attempt += 1
            # Bind the session locally EVERY iteration: another thread's
            # _drop_session (401 handling) may null self._session between
            # our check and the request — a local binding turns that race
            # into a harmless re-login instead of an AttributeError that
            # escapes the typed taxonomy (review finding H6).
            self._ensure_session()
            session = self._session
            if session is None:
                continue  # dropped between ensure and bind — re-ensure
            try:
                response = session.request(
                    method, url,
                    headers=self._api_headers(),
                    params=params or {},
                    json=json_body,
                    timeout=self.timeout_seconds,
                )
            except requests.Timeout as exc:
                raise DaysmartTimeout(f"{method} {path} timed out after {self.timeout_seconds}s") from exc
            except requests.RequestException as exc:
                if attempt >= max_attempts:
                    raise DaysmartUnavailable(
                        f"{method} {path} failed after {attempt} attempts: {exc}") from exc
                time.sleep(self.retry_backoff_seconds * attempt)
                continue

            status = response.status_code
            if status in (401, 403):
                if auth_retried:
                    raise DaysmartAuthFailure(
                        f"{method} {path} still rejected (HTTP {status}) after a fresh "
                        "login — check the vetter-token as well as the web credentials")
                logger.info("Daysmart session expired; logging in again")
                auth_retried = True
                self._drop_session()
                self._ensure_session()
                continue
            if status >= 500:
                if attempt >= max_attempts:
                    raise DaysmartUnavailable(
                        f"{method} {path} kept failing with HTTP {status} after {attempt} attempts")
                time.sleep(self.retry_backoff_seconds * attempt)
                continue
            if status >= 400:
                raise DaysmartMalformedResponse(f"{method} {path} returned HTTP {status}")

            try:
                body = response.json()
            except ValueError as exc:
                raise DaysmartMalformedResponse(
                    f"{method} {path} returned a 200 that is not JSON — likely the "
                    "login page; the session or the endpoint shape has changed") from exc
            if isinstance(body, list):
                if allow_list:
                    return body
                raise DaysmartMalformedResponse(f"{method} {path} returned a JSON array")
            if not isinstance(body, dict):
                raise DaysmartMalformedResponse(f"{method} {path} returned non-object JSON")
            return body

    def _request_token_json(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | list | None = None,
        attempts: int | None = None,
        allow_list: bool = False,
    ):
        """Call a ``/barramundi`` endpoint using the vetter-token only.

        Reads use the configured retry budget. Writes pass ``attempts=1``
        and retain the same no-blind-retry guarantee as the legacy
        transport. The token session is deliberately local and never
        becomes ``self._session`` (which is reserved for legacy PHP routes).
        """
        if not self.vetter_token:
            raise DaysmartAuthFailure(
                f"{method} {path} requires DAYSMART_API_TOKEN")
        session = self._session_factory()
        url = f"{self.base_url}{path}"
        max_attempts = attempts if attempts is not None else self.max_attempts
        attempt = 0
        while True:
            attempt += 1
            try:
                response = session.request(
                    method, url, headers=self._api_headers(),
                    params=params or {}, json=json_body,
                    timeout=self.timeout_seconds)
            except requests.Timeout as exc:
                raise DaysmartTimeout(
                    f"{method} {path} timed out after {self.timeout_seconds}s") from exc
            except requests.RequestException as exc:
                if attempt >= max_attempts:
                    raise DaysmartUnavailable(
                        f"{method} {path} failed after {attempt} attempts: {exc}") from exc
                time.sleep(self.retry_backoff_seconds * attempt)
                continue

            status = response.status_code
            if status in (401, 403):
                raise DaysmartAuthFailure(
                    f"{method} {path} rejected DAYSMART_API_TOKEN (HTTP {status})")
            if status >= 500:
                if attempt >= max_attempts:
                    raise DaysmartUnavailable(
                        f"{method} {path} kept failing with HTTP {status} "
                        f"after {attempt} attempts")
                time.sleep(self.retry_backoff_seconds * attempt)
                continue
            if status >= 400:
                raise DaysmartMalformedResponse(
                    f"{method} {path} returned HTTP {status}")
            try:
                body = response.json()
            except ValueError as exc:
                raise DaysmartMalformedResponse(
                    f"{method} {path} returned a non-JSON response") from exc
            if isinstance(body, list):
                if allow_list:
                    return body
                raise DaysmartMalformedResponse(
                    f"{method} {path} returned a JSON array")
            if not isinstance(body, dict):
                raise DaysmartMalformedResponse(
                    f"{method} {path} returned non-object JSON")
            return body

    # ------------------------------------------------------------------ API

    def fetch_orders(self, page: int = 1, order_type: str = "all") -> DaysmartOrderPageParseResult:
        """One page of the order list, contract-validated.

        Shape problems in individual orders come back as named issues in the
        result — one bad order never poisons its page-mates.
        """
        body = self._request_json(
            "GET", "/barramundi/inventory/order",
            params={"type": order_type, "page": page},
        )
        response = body.get("response")
        if not isinstance(response, dict) or not isinstance(response.get("resources"), list):
            raise DaysmartMalformedResponse(
                "order list did not carry the response/resources envelope — "
                "Daysmart's list shape has changed")
        return parse_daysmart_order_page(body)

    def get_suppliers(self) -> list[dict]:
        """Supplier picker data → ``[{daysmart_id, name}]`` sorted by name.

        A PO's supplier_daysmart_id must come from here (like a line's item
        id comes from the item search) — Daysmart returns a ``{id: name}``
        map under ``response.resources``.
        """
        body = self._request_json("GET", "/barramundi/inventory/supplier/list")
        resources = body.get("response", {}).get("resources")
        if not isinstance(resources, dict):
            raise DaysmartMalformedResponse(
                "supplier list did not carry a {id: name} resources object")
        return sorted(
            ({"daysmart_id": key, "name": (value or "").strip()}
             for key, value in resources.items()),
            key=lambda s: s["name"].lower())

    def search_inventory_items(self, query: str) -> list[dict]:
        """Item search for line entry → ``[{item_id, name, container_type}]``.

        Uses the token-authenticated ``/barramundi/inventory/search`` rather
        than the legacy ``filter_all_catalogs`` endpoint.  The legacy route
        needs a valid PHP web login; the configured web credentials are
        currently rejected, while this observed route returns the same
        required ``DIT-…`` identity in ``raw_id`` using the working token.

        Its unusual response stores rows under numeric string keys beside
        ``message``/``total``.  ``raw_id`` is the inventory item id;
        ``id`` is an encoded navigation value and must never be persisted.
        """
        body = self._request_token_json(
            "GET", "/barramundi/inventory/search",
            params={"query": query, "page": 1, "per_page": 50})
        response = body.get("response")
        if not isinstance(response, dict):
            raise DaysmartMalformedResponse(
                "inventory search did not carry a response object")

        numeric_keys = sorted(
            (key for key in response if str(key).isdigit()),
            key=lambda key: int(key))
        results = []
        for key in numeric_keys:
            row = response[key]
            if not isinstance(row, dict):
                continue
            item_id = row.get("raw_id")
            if not isinstance(item_id, str) or not item_id.startswith("DIT-"):
                continue
            results.append({
                "item_id": item_id,
                "name": row.get("name") or "",
                "container_type": row.get("uom"),
            })
        return results

    def get_order(self, daysmart_order_id: int) -> DaysmartCreatedOrderParseResult:
        """One order's detail (``order/{id}``). READ semantics (normal
        retries). The detail surface speaks the create dialect — PHP-UTC
        dates, bare-int status — so it parses through the same contract;
        its extra fields (addresses, supplier object) are tolerated."""
        body = self._request_json(
            "GET", f"/barramundi/inventory/order/{daysmart_order_id}")
        return parse_daysmart_created_order(body)

    def create_order(self, payload: dict) -> DaysmartCreatedOrderParseResult:
        """Create a purchase-order HEADER in Daysmart (``order/add``).

        WRITE semantics: exactly one attempt — no transport-level retry of
        any kind (see ``_request_json``). A token rejection fails without
        falling back to web login or resending. The response is
        contract-parsed; an unreadable body comes back as named issues
        (order=None) for the flow layer to quarantine — the order MAY exist
        in Daysmart at that point, which is exactly why this method must not
        guess.
        """
        body = self._request_json(
            "POST", "/barramundi/inventory/order/add",
            json_body=payload, attempts=1,
        )
        return parse_daysmart_created_order(body)

    def save_order_items(self, payloads: list[dict]) -> None:
        """Add/update PO lines (``order/item/save``). Body is an ARRAY —
        batch-capable at the adapter level. WRITE semantics: one attempt,
        never retried here. The explicit line flow sends one new line at a
        time and re-fetches the item list to learn its id; an ambiguous
        write still goes straight to a human. Nothing useful is read from
        this ack."""
        body = self._request_json(
            "POST", "/barramundi/inventory/order/item/save",
            json_body=payloads, attempts=1,
        )
        self._check_ack(body, "order/item/save")

    def delete_order_item(self, daysmart_item_id: int) -> None:
        """Delete one PO line (``DELETE order/item/{id}``). SOURCE tier:
        their UI makes this call, but its response has not been captured on
        the wire yet — so the ack is checked exactly like every other write
        (``message.code == 200``) and the first live use confirms. WRITE
        semantics: one attempt, never retried here."""
        body = self._request_json(
            "DELETE", f"/barramundi/inventory/order/item/{daysmart_item_id}",
            attempts=1,
        )
        self._check_ack(body, "order/item/delete")

    def fetch_order_lines(self, daysmart_order_id: int) -> DaysmartOrderItemPageParseResult:
        """Every line of a Daysmart PO, contract-parsed, all pages merged.

        The ONE reader for line rows: the lines inbox mirrors from it and
        the add-line backfill (``fetch_order_items``) derives its ids from
        it — one field map, no second parser to drift (the H8 lesson).
        Token-authenticated read; a bad row becomes a named issue, never an
        exception, and never poisons its page-mates.
        """
        items: list = []
        issues: list = []
        page = 1
        last_page = 1
        total = 0
        while page <= last_page:
            body = self._request_token_json(
                "GET", f"/barramundi/inventory/order/item/list/{daysmart_order_id}",
                params={"page": page})
            resources = body.get("response", {}).get("resources")
            if not isinstance(resources, dict) or not isinstance(resources.get("data"), list):
                raise DaysmartMalformedResponse(
                    "order item list did not carry response/resources/data")
            result = parse_daysmart_order_item_page(body)
            items.extend(result.items)
            issues.extend(result.issues)
            last_page = max(result.meta.last_page, 1)
            total = result.meta.total
            page += 1
        return DaysmartOrderItemPageParseResult(
            items=items, issues=issues,
            meta=DaysmartOrderItemPageMeta(current_page=1, last_page=last_page,
                                           per_page=50, total=total))

    def fetch_order_items(self, daysmart_order_id: int) -> list[DaysmartOrderItemRef]:
        """The two ids per line needed by the add-line backfill — derived
        from ``fetch_order_lines`` so there is exactly one line parser.
        Backfill must never guess: an unreadable line IDENTITY on any row
        aborts (a bad price on some row does not — that is the inbox's
        concern, recorded as an issue there)."""
        page = self.fetch_order_lines(daysmart_order_id)
        if any(issue.field_path in ("id", "item_id") for issue in page.issues):
            raise DaysmartMalformedResponse(
                "order item list contained an unreadable line identity")
        return [DaysmartOrderItemRef(daysmart_item_id=item.daysmart_line_id,
                                     inventory_item_id=item.inventory_item_id)
                for item in page.items]

    def update_order(self, daysmart_order_id: int, payload: dict) -> DaysmartCreatedOrderParseResult:
        """Header update (``PUT order/{id}``) — WIRE (PO-update.har). The echo
        is the updated order in the create dialect, parsed and returned so
        the caller can mirror exactly what Daysmart stored. WRITE
        semantics: one attempt, never retried here."""
        body = self._request_json(
            "PUT", f"/barramundi/inventory/order/{daysmart_order_id}",
            json_body=payload, attempts=1,
        )
        self._check_ack(body, "order/update")
        return parse_daysmart_created_order(body)

    def delete_order(self, daysmart_order_id: int) -> DaysmartCreatedOrderParseResult:
        """SOFT delete (``DELETE order/{id}``) — WIRE: the echo is the order
        with ``is_active: false`` and its notes rewritten to "[removed]".
        Parsed and returned for mirroring. One attempt."""
        body = self._request_json(
            "DELETE", f"/barramundi/inventory/order/{daysmart_order_id}", attempts=1,
        )
        self._check_ack(body, "order/delete")
        return parse_daysmart_created_order(body)

    def submit_order(self, daysmart_order_id: int) -> None:
        """The general submission step (their UI's "Submit Order": needs
        items and status Open; likely moves status 1 → 2). Exposed as an
        explicit operation — the create flow deliberately does NOT call
        this; submission is a human decision, mirroring their workflow."""
        body = self._request_json(
            "POST", "/barramundi/inventory/order/submit-order",
            json_body={"purchase_order_id": daysmart_order_id}, attempts=1,
        )
        self._check_ack(body, "submit-order")

    def create_receipt(self, payload: dict) -> int:
        """Create a receiving receipt against a PO. Returns the new
        receipt id (``response.resources.id``)."""
        body = self._request_json(
            "POST", "/barramundi/inventory/receipt",
            json_body=payload, attempts=1,
        )
        self._check_ack(body, "inventory/receipt")
        resources = body.get("response", {}).get("resources")
        receipt_id = resources.get("id") if isinstance(resources, dict) else None
        if not isinstance(receipt_id, int) or isinstance(receipt_id, bool):
            raise DaysmartMalformedResponse(
                "receipt create acked but carried no resources.id — "
                "the receipt may exist unreadably; do not blindly recreate")
        return receipt_id

    def fetch_receipts(self, page: int = 1, *,
                       purchase_order_id: int | None = None) -> DaysmartReceiptPageParseResult:
        """One page of the receipt list (``receipt?status=all&page=``),
        contract-parsed. READ semantics. ``purchase_order_id`` narrows the
        list to one order's receipts (the single-PO refresh)."""
        params: dict = {"status": "all", "page": page}
        if purchase_order_id is not None:
            params["purchase_order_id"] = purchase_order_id
        body = self._request_json("GET", "/barramundi/inventory/receipt", params=params)
        return parse_daysmart_receipt_page(body)

    def fetch_receipt(self, daysmart_receipt_id: int) -> DaysmartReceiptDetailParseResult:
        """ONE receipt from the DETAIL surface (``receipt/{id}``): its header
        (detail dialect) AND its generated rows — the only read that carries
        ``purchase_order_item_id``, and, for an UNPOSTED receipt, the only
        place its order is named (on the rows). The ONE parser for receipt
        rows: the inbox mirrors from it and the write flow's identity read
        (``fetch_receipt_items``) derives from it."""
        body = self._request_json(
            "GET", f"/barramundi/inventory/receipt/{daysmart_receipt_id}")
        resources = body.get("response", {}).get("resources")
        raw_items = resources.get("receipt_items") if isinstance(resources, dict) else None
        if not isinstance(raw_items, list):
            raise DaysmartMalformedResponse(
                "receipt detail did not carry response/resources/receipt_items")
        return parse_daysmart_receipt_detail(body)

    def fetch_receipt_rows(self, daysmart_receipt_id: int) -> DaysmartReceiptRowsParseResult:
        """The generated rows alone (from the same detail read)."""
        detail = self.fetch_receipt(daysmart_receipt_id)
        return DaysmartReceiptRowsParseResult(
            rows=detail.rows, issues=detail.issues,
            receipt_daysmart_id=detail.receipt_daysmart_id)

    def update_receipt(self, daysmart_receipt_id: int, payload: dict) -> None:
        """Header update (``PUT receipt/{id}``). WIRE (receipt-edit.har):
        ``{date:<epoch int>, tax, shipping, invoice_number, notes}`` — tax
        and shipping were accepted both as numbers and as strings. The echo
        is the create dialect; nothing is read from it (the flow pulls the
        detail afterwards). WRITE semantics: one attempt, never retried."""
        body = self._request_json(
            "PUT", f"/barramundi/inventory/receipt/{daysmart_receipt_id}",
            json_body=payload, attempts=1,
        )
        self._check_ack(body, "receipt/update")

    def fetch_receipt_items(
        self, daysmart_receipt_id: int,
    ) -> list[DaysmartReceiptItemRef]:
        """The identities of the rows Daysmart generated for a receipt —
        for the write flow's one-to-one reconciliation, which must never
        guess: an unreadable identity on ANY row aborts."""
        result = self.fetch_receipt_rows(daysmart_receipt_id)
        if any(issue.field_path in ROW_IDENTITY_PATHS for issue in result.issues):
            raise DaysmartMalformedResponse(
                "receipt detail contained an unreadable receipt-row identity")
        return [DaysmartReceiptItemRef(
            daysmart_receipt_item_id=row.daysmart_row_id,
            daysmart_po_item_id=row.po_line_id,
            purchase_order_id=row.purchase_order_id,
            post_to_item_id=row.post_to_item_id,
        ) for row in result.rows]

    def update_receipt_item(self, receipt_item_id: int, payload: dict) -> None:
        """Adjust one auto-created receipt row by its DaySmart identity."""
        body = self._request_json(
            "PUT", f"/barramundi/inventory/receipt/items/{receipt_item_id}",
            json_body=payload, attempts=1,
        )
        response = body.get("response")
        if isinstance(response, dict) and response.get("errors"):
            raise DaysmartRefused(
                f"receipt item update refused: {response['errors']}")
        self._check_ack(body, f"receipt/items/{receipt_item_id}")

    def add_receipt_item(self, receipt_id: int, payload: dict) -> None:
        """Record one received item on a receipt. NOTE: this endpoint's
        error dialect is ``data.messages.errors`` — plural "messages",
        verbatim from their frontend source — checked before the standard
        ack convention."""
        body = self._request_json(
            "POST", f"/barramundi/inventory/receipt/items/{receipt_id}",
            json_body=payload, attempts=1,
        )
        messages = body.get("messages")
        if isinstance(messages, dict) and messages.get("errors"):
            raise DaysmartRefused(f"receipt item refused: {messages['errors']}")
        self._check_ack(body, f"receipt/items/{receipt_id}")

    def post_receipt(self, receipt_id: int) -> None:
        """Commit a receipt to stock (their "Post Receipt" — the point of
        no return; until posted a receipt is a draft)."""
        body = self._request_json(
            "POST", "/barramundi/inventory/post-receipt",
            json_body={"purchase_order_receipt_id": receipt_id}, attempts=1,
        )
        self._check_ack(body, "post-receipt")

    @staticmethod
    def _check_ack(body: dict, context: str) -> None:
        """The ack convention their UI relies on: ``response.message.code
        === 200`` means yes. A parseable non-200 is a REFUSAL (their no),
        anything shapeless is malformed (can't tell what they said)."""
        response = body.get("response")
        if not isinstance(response, dict):
            # add_receipt_item's messages-dialect successes have no
            # response envelope only in the ERROR case, which is handled
            # before this — reaching here shapeless means malformed.
            raise DaysmartMalformedResponse(f"{context}: no response envelope in ack")
        message = response.get("message")
        code = message.get("code") if isinstance(message, dict) else None
        if code != 200:
            detail = message.get("errors") if isinstance(message, dict) else None
            raise DaysmartRefused(
                f"{context}: Daysmart refused (message.code={code!r}"
                + (f", errors={detail!r}" if detail else "") + ")")

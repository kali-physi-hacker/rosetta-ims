"""Phase 2 (adapter) — the reworked Daysmart service, tested against a fake
HTTP session. No test here ever touches real Daysmart.

TDD: these tests define the service that will replace the spike's
``daysmart-service.py`` when it moves to ``services/daysmart_service.py``.

The decisions they pin:

- Every failure is a TYPED error, never ``return None``. Exceptions are for
  transport only; shape problems surface as named validation issues inside
  the parse result (the phase-1 contract), not as exceptions.
- A TIMEOUT is never retried at this layer: exactly one attempt, then
  ``DaysmartTimeout``. Only the flow layer may follow up (snapshot-diff) —
  a blind retry after a timeout is how duplicate POs are made.
- Connection errors and 5xx are transient: retried with backoff (injectable
  as zero for tests), then ``DaysmartUnavailable``.
- ``/barramundi`` calls use the vetter token without touching the separate
  PHP web login. A token rejection is ``DaysmartAuthFailure`` immediately.
- Legacy login still requires both JSON ``success: true`` and a
  ``vettersession`` cookie.
- A 200 whose body is not the JSON envelope is ``DaysmartMalformedResponse``
  (there is no payload to attach issues to — that is what makes it transport).
- ``fetch_orders`` returns ``DaysmartOrderPageParseResult`` — raw dicts do
  not escape the module, and a mangled order becomes a named issue while its
  49 page-mates parse (contract at the boundary, proven end-to-end).
- Configuration comes from the constructor. No test sets an env var.

Interface under test:

    services/daysmart_service.py
        class DaysmartService:
            def __init__(base_url, username, password, vetter_token,
                         session_factory=..., max_attempts=3,
                         retry_backoff_seconds=...)
            def fetch_orders(page=1, order_type="all")
                -> DaysmartOrderPageParseResult
        DaysmartError (base)
        ├── DaysmartUnavailable
        ├── DaysmartTimeout
        ├── DaysmartAuthFailure
        └── DaysmartMalformedResponse

Order-detail / item-list methods are NOT tested yet: the line contract
(B-group) awaits the HAR detail capture as its golden fixture.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from schemas.purchase_orders import DaysmartOrderPageParseResult, IssueCode
from services.daysmart_service import (
    DaysmartAuthFailure,
    DaysmartError,
    DaysmartMalformedResponse,
    DaysmartService,
    DaysmartTimeout,
    DaysmartUnavailable,
)

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
PAGE1 = json.loads((FIXTURES / "daysmart_order_list_page1.json").read_text(encoding="utf-8"))
ORDER_ITEMS = json.loads(
    (FIXTURES / "daysmart_order_item_list_793009.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Fake HTTP plumbing
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text_body=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text_body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}", response=self)

    def json(self):
        if self._json is None:
            raise ValueError("not JSON")
        return self._json


class FakeSession:
    """Scripted session. Each entry handles one HTTP call, in order.

    An entry is ``(matcher_substring, action)`` where action is either a
    FakeResponse, an exception instance to raise, or a callable
    ``(session) -> FakeResponse`` (used to set cookies on login).
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, str]] = []
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}

    def _play(self, method, url):
        self.calls.append((method, url))
        assert self.script, f"unscripted call: {method} {url}"
        matcher, action = self.script.pop(0)
        assert matcher in url, f"expected call matching {matcher!r}, got {url}"
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action(self)
        return action

    def post(self, url, **kwargs):
        return self._play("POST", url)

    def get(self, url, **kwargs):
        return self._play("GET", url)

    def request(self, method, url, **kwargs):
        return self._play(method, url)


def login_ok(session: FakeSession) -> FakeResponse:
    # Production creates a new requests.Session on every login.  This fake
    # object is reused across a scripted re-login, so clear its cookie jar to
    # preserve the real lifecycle.
    session.cookies.clear()
    session.cookies["vettersession"] = "abc123"
    return FakeResponse(200, json_body={"success": True})


def login_no_cookie(session: FakeSession) -> FakeResponse:
    return FakeResponse(200, json_body={"success": True})


def login_invalid_with_cookie(session: FakeSession) -> FakeResponse:
    session.cookies["vettersession"] = "misleading-cookie"
    return FakeResponse(200, json_body={
        "success": False, "message": "Invalid email or password"})


LOGIN_OK = [("validateLogin", login_ok), ("dashboard", FakeResponse(200, json_body={}))]


def make_service(script, **overrides):
    session = FakeSession(script)
    kwargs = dict(
        base_url="https://daysmart.fake",
        username="user@fake.test",
        password="secret",
        vetter_token="tok-123",
        session_factory=lambda: session,
        max_attempts=3,
        retry_backoff_seconds=0,
    )
    kwargs.update(overrides)
    return DaysmartService(**kwargs), session


def order_page_response():
    return FakeResponse(200, json_body=json.loads(json.dumps(PAGE1)))


# ---------------------------------------------------------------------------
# S1/S2 — the contract guards the boundary
# ---------------------------------------------------------------------------


class TestContractAtTheBoundary:
    def test_s1_fetch_orders_returns_parsed_contract_result(self):
        service, session = make_service([("order", order_page_response())])
        result = service.fetch_orders(page=1)
        assert isinstance(result, DaysmartOrderPageParseResult)
        assert len(result.orders) == 50
        assert result.issues == []
        assert result.meta.total == 468
        assert result.meta.last_page == 10
        # Typed all the way: this is the contract model, not a dict.
        assert result.orders[0].daysmart_id == 791640
        assert result.orders[0].supplier.name == "Alfamedic"

    def test_s2_mangled_order_becomes_named_issue_not_exception(self):
        body = json.loads(json.dumps(PAGE1))
        body["response"]["resources"][3]["total"] = "not-money"
        service, session = make_service([("order", FakeResponse(200, json_body=body))])
        result = service.fetch_orders(page=1)
        assert len(result.orders) == 49
        assert len(result.issues) == 1
        assert result.issues[0].code == IssueCode.PO_MONEY_UNPARSEABLE
        assert result.issues[0].daysmart_id == body["response"]["resources"][3]["id"]


# ---------------------------------------------------------------------------
# S3/S4/S10 — transient failures retry, then fail loudly
# ---------------------------------------------------------------------------


class TestTransientRetry:
    def test_s3_connection_error_retried_then_succeeds(self):
        service, session = make_service([
            ("order", requests.ConnectionError("refused")),
            ("order", order_page_response()),
        ])
        result = service.fetch_orders(page=1)
        assert len(result.orders) == 50
        order_calls = [c for c in session.calls if "order" in c[1]]
        assert len(order_calls) == 2

    def test_s4_connection_error_exhausts_attempts_then_unavailable(self):
        service, session = make_service([
            ("order", requests.ConnectionError("refused")),
            ("order", requests.ConnectionError("refused")),
            ("order", requests.ConnectionError("refused")),
        ])
        with pytest.raises(DaysmartUnavailable):
            service.fetch_orders(page=1)
        order_calls = [c for c in session.calls if "order" in c[1]]
        assert len(order_calls) == 3  # max_attempts honoured, no more

    def test_s10_server_5xx_is_transient(self):
        service, session = make_service([
            ("order", FakeResponse(500)),
            ("order", order_page_response()),
        ])
        result = service.fetch_orders(page=1)
        assert len(result.orders) == 50


# ---------------------------------------------------------------------------
# S5 — a timeout is NEVER retried here
# ---------------------------------------------------------------------------


class TestTimeout:
    def test_s5_timeout_raises_after_exactly_one_attempt(self):
        service, session = make_service([
            ("order", requests.Timeout("no response")),
            # Deliberately NO second scripted order response: a retry would
            # hit the unscripted-call assertion and fail this test.
        ])
        with pytest.raises(DaysmartTimeout):
            service.fetch_orders(page=1)
        order_calls = [c for c in session.calls if "order" in c[1]]
        assert len(order_calls) == 1


# ---------------------------------------------------------------------------
# S6/S7/S8 — token auth is independent from legacy web credentials
# ---------------------------------------------------------------------------


class TestAuth:
    def test_s6_token_rejection_does_not_attempt_web_login(self):
        service, session = make_service([("order", FakeResponse(401))])
        with pytest.raises(DaysmartAuthFailure) as caught:
            service.fetch_orders(page=1)
        assert "rejected DAYSMART_API_TOKEN" in str(caught.value)
        assert not any("validateLogin" in url for _, url in session.calls)

    def test_s7_missing_token_fails_before_any_request(self):
        service, session = make_service([], vetter_token="")
        with pytest.raises(DaysmartAuthFailure) as caught:
            service.fetch_orders(page=1)
        assert "requires DAYSMART_API_TOKEN" in str(caught.value)
        assert session.calls == []

    def test_s8_login_without_vettersession_cookie_is_auth_failure(self):
        service, session = make_service([("validateLogin", login_no_cookie)])
        with pytest.raises(DaysmartAuthFailure):
            service._ensure_session()

    def test_s8b_failed_login_cookie_is_not_mistaken_for_authentication(self):
        service, _ = make_service([("validateLogin", login_invalid_with_cookie)])
        with pytest.raises(DaysmartAuthFailure) as caught:
            service._ensure_session()
        assert "invalid DAYSMART_USERNAME or DAYSMART_PASSWORD" in str(caught.value)


# ---------------------------------------------------------------------------
# S9 — a 200 that isn't the envelope
# ---------------------------------------------------------------------------


class TestMalformedResponse:
    def test_s9_non_json_200_is_malformed_response(self):
        # Classic failure shape: Daysmart returns its login HTML with a 200.
        service, session = make_service([
            ("order", FakeResponse(200, text_body="<html>please log in</html>")),
        ])
        with pytest.raises(DaysmartMalformedResponse):
            service.fetch_orders(page=1)

    def test_s9b_json_without_response_envelope_is_malformed(self):
        service, session = make_service([
            ("order", FakeResponse(200, json_body={"unexpected": "shape"})),
        ])
        with pytest.raises(DaysmartMalformedResponse):
            service.fetch_orders(page=1)


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


class TestErrorTaxonomy:
    def test_all_typed_errors_share_one_base(self):
        # The flow layer catches DaysmartError to mean "transport, not shape".
        for exc_type in (DaysmartUnavailable, DaysmartTimeout,
                         DaysmartAuthFailure, DaysmartMalformedResponse):
            assert issubclass(exc_type, DaysmartError)

    def test_config_comes_from_constructor_not_env(self, monkeypatch):
        # Explicit constructor args win; nothing is read from os.environ.
        monkeypatch.setenv("DAYSMART_USERNAME", "env-should-be-ignored")
        service, session = make_service([("order", order_page_response())])
        result = service.fetch_orders(page=1)
        assert len(result.orders) == 50


class TestTokenInventorySearch:
    def test_supplier_list_uses_token_without_attempting_web_login(self):
        body = {"response": {"resources": {
            "supplier-b": "Zulu Supplies",
            "supplier-a": "  Alpha Medical  ",
        }}}
        service, session = make_service([
            ("inventory/supplier/list", FakeResponse(200, json_body=body)),
        ])

        assert service.get_suppliers() == [
            {"daysmart_id": "supplier-a", "name": "Alpha Medical"},
            {"daysmart_id": "supplier-b", "name": "Zulu Supplies"},
        ]
        assert not any("validateLogin" in url for _, url in session.calls)
        assert service._session is None

    def test_maps_raw_dit_id_without_attempting_web_login(self):
        body = {"response": {
            "0": {"id": "encoded-navigation-token", "raw_id": "DIT-one",
                  "name": "First item", "uom": "Bottle"},
            "1": {"id": "another-token", "raw_id": "DIT-two",
                  "name": "Second item", "uom": "Bag"},
            "message": {"code": 200}, "total": 2,
        }}
        service, session = make_service([
            ("inventory/search", FakeResponse(200, json_body=body)),
        ])
        assert service.search_inventory_items("hills") == [
            {"item_id": "DIT-one", "name": "First item",
             "container_type": "Bottle"},
            {"item_id": "DIT-two", "name": "Second item",
             "container_type": "Bag"},
        ]
        assert not any("validateLogin" in url for _, url in session.calls)
        assert service._session is None

    def test_token_rejection_does_not_fall_back_to_invalid_web_login(self):
        service, session = make_service([
            ("inventory/search", FakeResponse(401, json_body={})),
        ])
        with pytest.raises(DaysmartAuthFailure) as caught:
            service.search_inventory_items("hills")
        assert "rejected DAYSMART_API_TOKEN" in str(caught.value)
        assert len(session.calls) == 1

    def test_order_item_refs_use_dit_id_not_embedded_navigation_tokens(self):
        service, session = make_service([
            ("order/item/list/793009", FakeResponse(
                200, json_body=json.loads(json.dumps(ORDER_ITEMS)))),
        ])
        items = service.fetch_order_items(793009)
        assert len(items) == 4
        assert items[0].daysmart_item_id == 3969381
        assert items[0].inventory_item_id == "DIT-ebbd2f4c-6e6"
        assert not any("validateLogin" in url for _, url in session.calls)

"""IDEXX HK — a supplier whose catalogue is a webshop behind a real login.

IDEXX publishes no price file to this account. Their ordering portal is SAP
ITS, server-rendered, and sign-in federates to Auth0. Two things were measured
before this was written, and both shaped it:

* there is NO data API — no OCC REST, no OData, no UI5 service. The XHR traffic
  on the page is analytics only.
* the login cannot be scripted over plain HTTP. Auth0's risk engine answers a
  scripted client with a captcha (400 at the identifier step) and lets a real
  browser through untouched. So a browser is not a convenience here, it is the
  only way in.

Everything a browser is needed for happens in ONE place — sign in, walk the
category leaves, read the product blocks — and what comes out is a CSV snapshot
carrying IDEXX's own field names. From there this supplier is an ordinary
delimited source: read deterministically, conformed by a declared contract,
matched, reviewed and published by the machinery every other supplier uses. No
test, no golden set and no conformance run needs a browser.

Two facts about this account, both visible on the page:

* prices are OUR prices. The portal signs in as a named practice and quotes
  what that practice pays, so a snapshot is account-specific, not a list.
* about a third of the catalogue is marked "Free item" — consumables IDEXX
  supplies at no charge alongside an analyser contract. That is a price of
  zero, not a missing price, and the snapshot says so explicitly rather than
  leaving a blank for the desk to puzzle over.
"""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Iterable

BASE_URL = os.environ.get("IDEXX_ORDER_URL", "https://order.idexx.com")

#: The whole catalogue is ~20 leaves under ~40 category pages. This is a stop
#: for a walk that has gone wrong, not a limit on how much IDEXX may sell.
MAX_PAGES = 200

#: Where the catalogue starts. Every product sits under one of these; the
#: connector walks down until a page yields product blocks rather than more
#: categories.
ROOT_CATEGORY_PATH = "/6N/category/Browse-products"

#: The snapshot's headings — IDEXX's own words where it has them.
SNAPSHOT_COLUMNS = (
    "material",
    "description",
    "category",
    "pack_text",
    "units_per_item",
    "pack_noun",
    "price_hkd",
    "is_free_item",
    "previously_ordered",
    "source_url",
)

#: "5 tests per item", "12 panels per item". The count and the thing counted,
#: which is what one orderable item holds.
_PACK = re.compile(r"(\d[\d,]*)\s+([A-Za-z][A-Za-z /-]{1,24}?)\s+per\s+item", re.I)
_MATERIAL = re.compile(r"Product:\s*([\w.\-]+)")
#: Our price carries a trailing asterisk on the page; the unmarked figure
#: beside it is IDEXX's list price and is NOT what this account pays.
_OUR_PRICE = re.compile(r"HKD\s*([\d,]+\.\d{2})\s*\*")
_FREE_ITEM = re.compile(r"\bFree item\b", re.I)

WARN_NO_PRICE = "NO_PRICE"
#: The same word Royal Canin uses. One vocabulary across connectors means
#: the desk reads one sentence whoever the supplier is.
WARN_NO_SUPPLIER_CODE = "NO_SUPPLIER_CODE"
WARN_NO_PACK = "NO_PACK"


class IdexxConnectorError(RuntimeError):
    """Something a person can act on: the login moved, or the catalogue did."""


def redacted(exc: BaseException, *secrets: str) -> IdexxConnectorError:
    """Re-raise a browser failure with the credentials stripped out.

    Playwright reports a failed action by quoting its arguments — a timeout on
    the password step renders as `fill("<the password>")`. That text then
    reaches the log, the audit trail and the 502 body. Nothing derived from a
    browser exception may escape this module unfiltered.
    """
    text = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[redacted]")
    return IdexxConnectorError(text)


@dataclass(frozen=True)
class SnapshotWarning:
    """One product's note: what kind of problem, and how it reads."""

    code: str
    message: str


@dataclass(frozen=True)
class Snapshot:
    """One complete read of the catalogue, ready to submit as a source document."""

    csv_bytes: bytes
    checksum: str
    row_count: int
    filename: str
    warnings: tuple[SnapshotWarning, ...] = field(default_factory=tuple)
    #: How many leaf pages the walk actually reached, so a short read is visible.
    pages_read: int = 0


def parse_block(text: str, *, category: str = "", url: str = "") -> dict[str, str] | None:
    """One product tile's visible text, as a snapshot row.

    The page prints a block per product: name, "Product: <material>", the pack
    ("5 tests per item"), and either a price or the words "Free item". Reading
    the rendered TEXT rather than the markup is deliberate — SAP ITS class
    names are generated and change with the theme, while what a person reads on
    the page is the thing IDEXX intends to say.
    """
    material = _MATERIAL.search(text or "")
    if not material:
        return None
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    # The product name is the first line that is not a price, a control, or the
    # material line itself.
    name = ""
    for line in lines:
        if _MATERIAL.search(line) or _OUR_PRICE.search(line) or _PACK.search(line):
            continue
        if line.lower() in {"add to order", "previously ordered", "free item*", "free item"}:
            continue
        if line.startswith("HKD") or line.startswith("List Price"):
            continue
        name = line
        break

    free = bool(_FREE_ITEM.search(text))
    price = _OUR_PRICE.search(text)
    pack = _PACK.search(text)
    return {
        "material": material.group(1).strip(),
        "description": name,
        "category": category,
        "pack_text": pack.group(0).strip() if pack else "",
        "units_per_item": pack.group(1).replace(",", "") if pack else "",
        "pack_noun": pack.group(2).strip().lower() if pack else "",
        # A free item is priced at zero and says so. Leaving it blank would put
        # a third of the catalogue in front of a reviewer as "price unknown"
        # when IDEXX has stated the price plainly.
        "price_hkd": "0.00" if (free and not price) else (price.group(1).replace(",", "") if price else ""),
        "is_free_item": "TRUE" if free else "FALSE",
        "previously_ordered": "TRUE" if "previously ordered" in text.lower() else "FALSE",
        "source_url": url,
    }


def build_snapshot(
    blocks: Iterable[tuple[str, str, str]],
    *,
    captured_on: str,
    pages_read: int = 0,
) -> Snapshot:
    """Turn (block_text, category, url) triples into the canonical CSV.

    Sorted by material so an unchanged catalogue always produces the same
    bytes: the checksum then only moves when IDEXX moves, and re-reading
    submits nothing rather than manufacturing a duplicate run per button press.
    """
    rows: dict[str, dict[str, str]] = {}
    warnings: list[SnapshotWarning] = []
    for text, category, url in blocks:
        row = parse_block(text, category=category, url=url)
        if row is None:
            warnings.append(SnapshotWarning(
                WARN_NO_SUPPLIER_CODE, "a product tile carried no material number and was skipped"))
            continue
        if not row["price_hkd"]:
            warnings.append(SnapshotWarning(
                WARN_NO_PRICE, f"{row['material']}: no price and not marked free"))
        if not row["units_per_item"]:
            warnings.append(SnapshotWarning(
                WARN_NO_PACK, f"{row['material']}: the page states no pack size"))
        # The same product is listed under more than one category; first wins so
        # the snapshot stays one row per orderable thing.
        rows.setdefault(row["material"], row)

    if not rows:
        # Never an empty catalogue: downstream, zero rows delists everything
        # IDEXX sells. A portal that yielded no products has failed to answer.
        raise IdexxConnectorError(
            f"IDEXX's portal returned no products across {pages_read} page(s). "
            f"Nothing was captured; sign in and check the account can still browse."
        )

    ordered = [rows[key] for key in sorted(rows)]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(SNAPSHOT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in ordered:
        writer.writerow(row)
    payload = buffer.getvalue().encode("utf-8")
    return Snapshot(
        csv_bytes=payload,
        checksum=hashlib.sha256(payload).hexdigest(),
        row_count=len(ordered),
        filename=f"idexx-hk-{captured_on}.csv",
        warnings=tuple(warnings),
        pages_read=pages_read,
    )


def unit_price(row: dict[str, str]) -> Decimal | None:
    """What ONE unit inside the item costs, when the page states the pack.

    An item of 5 tests at HKD1,034 is HKD206.80 a test. Where the pack is not
    printed there is nothing to divide by and this answers None rather than
    dividing by one and calling the item a unit.
    """
    try:
        price = Decimal(str(row.get("price_hkd") or "").strip() or "x")
        units = Decimal(str(row.get("units_per_item") or "").strip() or "x")
    except InvalidOperation:
        return None
    if units <= 0:
        return None
    return price / units


# ── the only part that needs a browser ──────────────────────────────────────

_BLOCK_TEXT = """() => Array.from(document.querySelectorAll('.productlistview_addtobasket'))
  .map(el => { let b = el; while (b && !/Product:/.test(b.innerText)) b = b.parentElement; return b; })
  .filter(Boolean).map(b => b.innerText.replace(/\\n{2,}/g, '\\n').trim())"""

_CATEGORY_HREFS = "els => els.map(a => a.getAttribute('href'))"


def _sign_in(page, username: str, password: str) -> None:
    """Auth0 universal login, identifier first.

    Plain HTTP cannot do this: the risk engine answers a scripted client with a
    captcha and returns 400 at the identifier step, while a real browser is let
    through. Measured 2026-09-02, and the reason this module carries a browser
    at all.
    """
    page.goto(BASE_URL, timeout=60_000)
    page.click("text=Sign in")
    page.wait_for_load_state("networkidle", timeout=60_000)
    page.fill('input[type="email"], input[name="username"], #username', username)
    page.keyboard.press("Enter")
    page.wait_for_timeout(3_500)
    field = page.locator('input[type="password"]:visible')
    if not field.count():
        raise IdexxConnectorError(
            "IDEXX asked for something other than a password after the email step — "
            "a second factor or a changed login. Sign in by hand and see what it wants."
        )
    field.first.fill(password)
    page.keyboard.press("Enter")
    page.wait_for_timeout(6_000)
    page.wait_for_load_state("networkidle", timeout=60_000)
    if "Sign out" not in page.inner_text("body"):
        raise IdexxConnectorError(
            "Signed in but the shop did not come back with a session. The credentials may "
            "have expired, or the account may be locked — try the site by hand."
        )


def capture(*, captured_on: str, username: str | None = None, password: str | None = None,
            headless: bool = True) -> Snapshot:
    """Read the live catalogue and return one snapshot.

    Walks categories breadth-first, treating any page that yields product
    blocks as a leaf. A product listed under two categories is one row.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise IdexxConnectorError(
            "The IDEXX connector needs a browser (playwright) and this server has none."
        ) from exc

    username = username or os.environ.get("IDEXX_ORDER_USERNAME", "")
    password = password or os.environ.get("IDEXX_ORDER_PASSWORD", "")
    if not username or not password:
        raise IdexxConnectorError(
            "No IDEXX credentials are configured on this server "
            "(IDEXX_ORDER_USERNAME / IDEXX_ORDER_PASSWORD)."
        )

    blocks: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    leaves = 0
    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(
                headless=headless, args=["--no-sandbox", "--disable-dev-shm-usage"])
        except Exception as exc:
            raise IdexxConnectorError(
                "This server has playwright but no Chromium to drive "
                "(`playwright install chromium`)."
            ) from exc
        try:
            page = browser.new_context(viewport={"width": 1440, "height": 1600}).new_page()
            try:
                _sign_in(page, username, password)
            except IdexxConnectorError:
                raise
            except Exception as exc:
                # Never let a raw browser exception out of the sign-in step:
                # playwright quotes the arguments of the action that failed.
                raise redacted(exc, password, username) from None
            queue = [href for href in page.eval_on_selector_all(
                "a[href*='/category/']", _CATEGORY_HREFS) if href]
            while queue and len(seen) < MAX_PAGES:
                href = queue.pop(0)
                url = href if href.startswith("http") else BASE_URL + href
                if url in seen:
                    continue
                seen.add(url)
                page.goto(url, timeout=60_000)
                page.wait_for_load_state("networkidle", timeout=60_000)
                page.wait_for_timeout(1_000)
                found = page.evaluate(_BLOCK_TEXT)
                if found:
                    leaves += 1
                    category = url.split("/Browse-products/")[-1]
                    blocks.extend((text, category, url) for text in found)
                else:
                    queue.extend(h for h in page.eval_on_selector_all(
                        "a[href*='/category/']", _CATEGORY_HREFS) if h)
        except IdexxConnectorError:
            raise
        except Exception as exc:
            raise redacted(exc, password, username) from None
        finally:
            browser.close()

    if not blocks:
        raise IdexxConnectorError(
            f"Signed in and walked {len(seen)} category page(s) but found no products. "
            "The catalogue layout has changed — re-check the site before trusting this."
        )
    return build_snapshot(blocks, captured_on=captured_on, pages_read=leaves)


@dataclass(frozen=True)
class CompletenessVerdict:
    """Whether a snapshot may be trusted to say what is no longer sold."""

    trustworthy: bool
    reason: str
    previous_rows: int
    current_rows: int


def assess_completeness(*, current_rows: int, previous_rows: int | None,
                        tolerance: float = 0.10) -> CompletenessVerdict:
    """Guard the failure mode a network read has that a file does not.

    A walk that dies halfway — a category page that never rendered, a session
    that lapsed — yields a SHORTER catalogue, which downstream is
    indistinguishable from IDEXX delisting half their range.
    """
    if previous_rows is None:
        return CompletenessVerdict(True, "first snapshot — nothing to compare against", 0, current_rows)
    if previous_rows <= 0:
        return CompletenessVerdict(True, "previous snapshot held no rows", previous_rows, current_rows)
    if current_rows >= previous_rows:
        return CompletenessVerdict(True, f"{current_rows} rows, up from {previous_rows}",
                                   previous_rows, current_rows)
    shrinkage = (previous_rows - current_rows) / previous_rows
    if shrinkage <= tolerance:
        return CompletenessVerdict(
            True,
            f"{current_rows} rows, {previous_rows - current_rows} fewer than last time "
            f"({shrinkage:.0%}) — within the normal range",
            previous_rows, current_rows)
    return CompletenessVerdict(
        False,
        f"{current_rows} rows against {previous_rows} last time — {shrinkage:.0%} of the "
        f"catalogue is missing. A part-finished walk looks exactly like this, so these rows "
        f"must not be treated as proof that the missing products were delisted.",
        previous_rows, current_rows)

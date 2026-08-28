"""Royal Canin HK — a supplier whose catalogue is a webshop, not a file.

Royal Canin issues no price list. Their B2B webshop is Magento 2 running the
Algolia search extension, and the shop's own JavaScript is handed a
search-only key so the browser can query the product index directly. That
index carries the whole catalogue INCLUDING our prices, because Magento
indexes a price per customer group — so the connector is an API client, not a
page scraper: no browser, no session, no markup to break against.

What it produces is a CSV snapshot with Royal Canin's own field names as
headings. That is the whole point: from there this supplier is an ordinary
delimited source, read deterministically (no vision spend), conformed by a
declared contract, matched, reviewed and published by the machinery every
other supplier already uses.

Two facts the snapshot depends on, both established against the live shop and
both re-checkable by `verify_credentials`:

* our customer group is **879** — the price to read is ``group_879_tier`` and
  the catalogue is the products whose ``customer_groups_ids`` contains it
  (products outside it 404 on their own URL: they are not ours to sell);
* ``price.HKD.default`` is the sentinel 999999999, meaning "no price without
  an account". It is never money and is never written.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

#: The shop's own public search credentials, as handed to its browser JS at
#: `window.CgiTool.algoliaConfig`. Search-only and world-readable — this is not
#: a secret, and the account password is deliberately NOT needed here.
DEFAULT_APP_ID = os.environ.get("ROYAL_CANIN_ALGOLIA_APP_ID", "GD1LHQ79KB")
DEFAULT_SEARCH_KEY = os.environ.get(
    "ROYAL_CANIN_ALGOLIA_SEARCH_KEY", "565ad0e43990661ca61cac57dc0e0073"
)
DEFAULT_INDEX = os.environ.get(
    "ROYAL_CANIN_ALGOLIA_INDEX", "rcb2b_royalcanin_hk_en_products"
)
DEFAULT_CUSTOMER_GROUP = os.environ.get("ROYAL_CANIN_CUSTOMER_GROUP", "879")

#: Magento's "this product has no price for you" marker.
NO_PRICE_SENTINEL = 999999999
PAGE_SIZE = 100
#: The whole catalogue is about five pages. A ceiling, not a limit.
MAX_PAGES = 100
CURRENCY = "HKD"

#: Royal Canin trades as two suppliers, and the difference is money: the
#: veterinary account and the retail account carry different rebate ladders,
#: credit terms and minimum orders. One webshop login shows both ranges, so
#: every row must be filed under the supplier that actually invoices it.
RANGE_VET = "vet"
RANGE_NON_VET = "non_vet"

#: Royal Canin files each product code under a top-level channel of its own —
#: "VET CAT 獸醫系列 - 貓" against "PET SHOP CAT 貓糧". That filing is the
#: supplier's own answer, so it decides the range.
#:
#: Both spellings and the Chinese, because a rename is not a re-categorisation:
#: "VET CAT" becoming "VETERINARY CAT" would move the whole veterinary range
#: onto the retail account's rebate ladder, and \bVET\b alone does not match
#: VETERINARY. 獸醫 is Royal Canin's own word for it and rides along on the
#: live category names.
_VET_CATEGORY = re.compile(r"\bVET(?:ERINARY)?\b|獸醫")
#: Buckets that are not sales channels and say nothing about who invoices a
#: product. "Buy again" is this ACCOUNT's own reorder list — it sits on 449 of
#: 454 rows, so counting it as a retail channel would call almost every
#: veterinary product dual-filed.
_NON_CHANNEL_CATEGORIES = ("BUY AGAIN", "PROMOTION", "RECOMMEND", "NEW ARRIVAL")
#: Fallback only, for a row the shop filed under no category at all: the
#: veterinary product lines name themselves (VHN = Veterinary Health
#: Nutrition, VD = Veterinary Diet). A retail line never carries these.
_VET_NAME_PREFIXES = ("VHN ", "VD ", "VCN ")

#: The snapshot's headings — Royal Canin's own field names, so the contract
#: maps what the source calls things rather than what we wish it called them.
SNAPSHOT_COLUMNS = (
    "original_sku",
    "sku",
    "name",
    "ean_code",
    "price_hkd",
    "nav_uom",
    "navision_weight",
    "gtm_category",
    "nav_animal_type",
    "category_path",
    "in_stock",
    "qty_in_stock",
    "min_sale_qty",
    "qty_increments",
    "url",
)


class RoyalCaninConnectorError(RuntimeError):
    """Something a person can act on: the index moved, or the key stopped working."""


#: What a per-product note IS. Carried as a code beside its sentence because
#: the three kinds want three different answers from a person — decide the
#: account, chase the missing price, or tell Royal Canin their data is broken —
#: and a screen given only sentences cannot tell them apart.
WARN_DUAL_LISTED = "DUAL_LISTED"
WARN_NO_PRICE = "NO_PRICE"
WARN_NO_SUPPLIER_CODE = "NO_SUPPLIER_CODE"


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
    #: Which Royal Canin supplier this snapshot belongs to.
    product_range: str = RANGE_VET
    #: Per-product notes that did not stop the snapshot — a row without a price
    #: for our group, a product Royal Canin files under both channels.
    warnings: tuple[SnapshotWarning, ...] = field(default_factory=tuple)
    #: What the index said it holds, before our group filter.
    index_total: int = 0


def _post(url: str, payload: dict, headers: dict, timeout: int = 30) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:300]
        raise RoyalCaninConnectorError(
            f"Royal Canin's search index refused the request ({exc.code}). "
            f"The public search key may have been rotated — re-read it from the "
            f"shop and update ROYAL_CANIN_ALGOLIA_SEARCH_KEY. Detail: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise RoyalCaninConnectorError(
            f"Royal Canin's search index could not be reached: {exc.reason}"
        ) from exc


def fetch_index(
    *,
    app_id: str | None = None,
    search_key: str | None = None,
    index_name: str | None = None,
) -> list[dict]:
    """Every product in the index, page by page, exactly as the shop reads it."""

    app_id = app_id or DEFAULT_APP_ID
    search_key = search_key or DEFAULT_SEARCH_KEY
    index_name = index_name or DEFAULT_INDEX
    headers = {
        "X-Algolia-Application-Id": app_id,
        "X-Algolia-API-Key": search_key,
        "Content-Type": "application/json",
    }
    url = f"https://{app_id}-dsn.algolia.net/1/indexes/{index_name}/query"

    hits: list[dict] = []
    page = 0
    while True:
        payload = {"params": f"query=&hitsPerPage={PAGE_SIZE}&page={page}"}
        body = _post(url, payload, headers)
        if "hits" not in body:
            raise RoyalCaninConnectorError(
                f"Royal Canin's search index returned no hits field "
                f"(message: {str(body.get('message'))[:200]})"
            )
        hits.extend(body["hits"])
        pages = int(body.get("nbPages") or 1)
        if page >= pages - 1:
            break
        page += 1
        if page >= MAX_PAGES:
            # The catalogue is ~5 pages. Reaching this means the index is
            # answering something other than what we asked, and a read that
            # never ends is worse than one that says so.
            raise RoyalCaninConnectorError(
                f"Royal Canin's search index reported {pages} pages of results, past the "
                f"{MAX_PAGES}-page ceiling. The index or the query has changed — re-verify "
                f"against the shop before trusting anything this read returned."
            )
    return hits


def group_price(hit: dict, customer_group: str) -> Decimal | None:
    """Our price for one product, or None when the catalogue holds none.

    Reads only the group tier. The `default` field is the no-account sentinel
    and a `0` is not a price either — a free product is not what that means.

    Money is carried as a Decimal read from the source's own digits. A float
    would be a second rounding on top of whatever the index already did, and
    what leaves here is written verbatim into the snapshot.
    """

    prices = (hit.get("price") or {}).get(CURRENCY) or {}
    value = prices.get(f"group_{customer_group}_tier")
    if value is None or value == "":
        return None
    try:
        amount = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        return None
    # The sentinel is checked AFTER conversion, never against the raw value:
    # a JSON number and the string "999999999" are the same non-price, and an
    # equality test on the raw field only catches the first. Getting this
    # wrong writes HK$999,999,999 as a real cost, and conformance — rightly —
    # has no way to know it is not money.
    if not amount.is_finite() or amount <= 0 or amount == Decimal(NO_PRICE_SENTINEL):
        return None
    return amount


def is_ours(hit: dict, customer_group: str) -> bool:
    """Is this product part of OUR catalogue?

    The index serves every customer group. A product not offered to our group
    is not merely unpriced — its own page 404s for us — so it must never reach
    the desk as something to sell.
    """

    return customer_group in [str(value) for value in (hit.get("customer_groups_ids") or [])]


def _top_categories(hit: dict) -> list[str]:
    """The channels Royal Canin files this product under, top level only."""
    categories = hit.get("categories") or {}
    if not isinstance(categories, dict):
        return []
    return [str(value) for value in (categories.get("level0") or [])]


def classify(hit: dict) -> tuple[str, bool]:
    """Which Royal Canin supplier invoices this product, and is it dual-filed?

    Returns (range, dual_listed). A product filed under any veterinary channel
    belongs to the vet account; everything else is retail. `dual_listed` marks
    the handful of codes Royal Canin files under BOTH a vet and a retail
    channel — those are reported rather than silently assigned, because the
    choice decides which rebate they earn.
    """
    channels = [
        name for name in _top_categories(hit)
        if not any(bucket in name.upper() for bucket in _NON_CHANNEL_CATEGORIES)
    ]
    vet = any(_VET_CATEGORY.search(name.upper()) for name in channels)
    retail = any(not _VET_CATEGORY.search(name.upper()) for name in channels)
    if not channels:
        # Nothing filed it. The veterinary lines name themselves, and misfiling
        # a vet product as retail attaches the wrong rebate, so the name is
        # allowed to speak when the shop said nothing.
        name = str(hit.get("name") or "").upper()
        return (RANGE_VET if name.startswith(_VET_NAME_PREFIXES) else RANGE_NON_VET), False
    return (RANGE_VET if vet else RANGE_NON_VET), (vet and retail)


def _level_number(key: Any) -> int:
    """The depth "level3" states, so levels sort by number and not by spelling."""
    digits = re.sub(r"\D", "", str(key))
    return int(digits) if digits else -1


def _category_path(hit: dict) -> str:
    categories = hit.get("categories") or {}
    if isinstance(categories, dict) and categories:
        # By depth, not alphabetically: sorted as text "level9" outranks
        # "level10" and the deepest placement would be the one dropped.
        deepest = max(categories.keys(), key=_level_number)
        values = categories.get(deepest) or []
        if values:
            return str(values[0])
    return ""


def _row_for(hit: dict, customer_group: str) -> dict[str, str]:
    stock = hit.get("stock_configuration") or {}
    price = group_price(hit, customer_group)
    return {
        "original_sku": str(hit.get("original_sku") or ""),
        "sku": str(hit.get("sku") or ""),
        "name": str(hit.get("name") or ""),
        "ean_code": str(hit.get("ean_code") or ""),
        # format(…, "f") and never "%g": six significant digits turns
        # 12345.67 into 12345.7 and 10,000,000 into "1e+07", and this string
        # IS the cost the pipeline goes on to publish.
        "price_hkd": "" if price is None else format(price, "f"),
        "nav_uom": str(hit.get("nav_uom") or ""),
        "navision_weight": str(hit.get("navision_weight") or ""),
        "gtm_category": str(hit.get("gtm_category") or ""),
        "nav_animal_type": str(hit.get("nav_animal_type") or ""),
        "category_path": _category_path(hit),
        "in_stock": "TRUE" if hit.get("in_stock") else "FALSE",
        "qty_in_stock": str(hit.get("qty_in_stock") if hit.get("qty_in_stock") is not None else ""),
        "min_sale_qty": str(stock.get("min_sale_qty") if stock.get("min_sale_qty") is not None else ""),
        "qty_increments": str(stock.get("qty_increments") if stock.get("qty_increments") is not None else ""),
        "url": str(hit.get("url") or ""),
    }


def build_snapshot(
    hits: Iterable[dict],
    *,
    customer_group: str | None = None,
    captured_on: str,
    product_range: str = RANGE_VET,
) -> Snapshot:
    """Turn index hits into the canonical CSV this supplier is ingested from.

    Sorted by the supplier's own product code so the same catalogue always
    produces the same bytes: an unchanged catalogue then has an unchanged
    checksum, and re-reading it submits nothing rather than manufacturing a
    duplicate run every time someone presses the button.

    `captured_on` is the caller's date (YYYY-MM-DD). It names the file only —
    it is deliberately NOT part of the checksum, or every read would look like
    a change.
    """

    customer_group = customer_group or DEFAULT_CUSTOMER_GROUP
    hits = list(hits)
    warnings: list[SnapshotWarning] = []
    rows: list[dict[str, str]] = []
    for hit in hits:
        if not is_ours(hit, customer_group):
            continue
        row_range, dual_listed = classify(hit)
        if row_range != product_range:
            continue
        row = _row_for(hit, customer_group)
        if not row["original_sku"]:
            warnings.append(SnapshotWarning(
                WARN_NO_SUPPLIER_CODE,
                f"skipped a product with no supplier code (objectID {hit.get('objectID')})",
            ))
            continue
        # After the code check, not before: a row that is about to be dropped
        # should not also be reported as a filing question nobody can answer
        # without a code to answer it about.
        if dual_listed:
            warnings.append(SnapshotWarning(
                WARN_DUAL_LISTED,
                f"{row['original_sku']}: Royal Canin files this under both a veterinary "
                f"and a retail channel; ingested as {product_range}",
            ))
        if not row["price_hkd"]:
            # Kept, not dropped: the row still describes a real product, and
            # conformance holds it for a person to look at rather than the
            # connector deciding silently that it does not exist.
            warnings.append(SnapshotWarning(
                WARN_NO_PRICE,
                f"{row['original_sku']}: no price for customer group {customer_group}",
            ))
        rows.append(row)

    rows.sort(key=lambda row: (row["original_sku"], row["sku"]))

    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(SNAPSHOT_COLUMNS), lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    text = buffer.getvalue()
    payload = text.encode("utf-8")
    checksum = hashlib.sha256(payload).hexdigest()
    return Snapshot(
        csv_bytes=payload,
        checksum=checksum,
        row_count=len(rows),
        filename=f"royal-canin-hk-{product_range.replace('_', '-')}-{captured_on}.csv",
        product_range=product_range,
        warnings=tuple(warnings),
        index_total=len(hits),
    )


def capture_snapshots(
    *,
    captured_on: str,
    customer_group: str | None = None,
    app_id: str | None = None,
    search_key: str | None = None,
    index_name: str | None = None,
) -> dict[str, Snapshot]:
    """One read of the shop, split into the two suppliers that invoice it.

    Read ONCE and divided here rather than fetched twice: the two ranges must
    describe the same moment, or a price that moved between two reads would
    look like a change in one supplier and not the other.
    """

    hits = fetch_index(app_id=app_id, search_key=search_key, index_name=index_name)
    snapshots: dict[str, Snapshot] = {}
    for product_range in (RANGE_VET, RANGE_NON_VET):
        snapshots[product_range] = build_snapshot(
            hits, customer_group=customer_group, captured_on=captured_on,
            product_range=product_range,
        )
    if not any(snapshot.row_count for snapshot in snapshots.values()):
        raise RoyalCaninConnectorError(
            f"Read {len(hits)} products from Royal Canin but none belong to customer "
            f"group {customer_group or DEFAULT_CUSTOMER_GROUP}. Either the group changed "
            f"or the index did — re-verify against the shop before publishing anything."
        )
    return snapshots


@dataclass(frozen=True)
class CompletenessVerdict:
    """Whether a snapshot may be trusted to say what is no longer sold."""

    trustworthy: bool
    reason: str
    previous_rows: int
    current_rows: int


def assess_completeness(
    *, current_rows: int, previous_rows: int | None, tolerance: float = 0.10
) -> CompletenessVerdict:
    """Guard the one failure mode a network read has that a file does not.

    A page that dies halfway yields a SHORTER catalogue, which downstream is
    indistinguishable from Royal Canin delisting half their range. A file
    cannot fail this way; a fetch can. So a snapshot that shrinks by more than
    the tolerance is not trusted to prove absence, and says so — it is for a
    person to release, not for the pipeline to act on quietly.
    """

    if previous_rows is None:
        return CompletenessVerdict(True, "first snapshot — nothing to compare against", 0, current_rows)
    if previous_rows <= 0:
        return CompletenessVerdict(True, "previous snapshot held no rows", previous_rows, current_rows)
    if current_rows >= previous_rows:
        return CompletenessVerdict(
            True, f"{current_rows} rows, up from {previous_rows}", previous_rows, current_rows
        )
    shrinkage = (previous_rows - current_rows) / previous_rows
    if shrinkage <= tolerance:
        return CompletenessVerdict(
            True,
            f"{current_rows} rows, {previous_rows - current_rows} fewer than last time "
            f"({shrinkage:.0%}) — within the normal range",
            previous_rows,
            current_rows,
        )
    return CompletenessVerdict(
        False,
        f"{current_rows} rows against {previous_rows} last time — {shrinkage:.0%} of the "
        f"catalogue is missing. A part-finished read looks exactly like this, so these rows "
        f"must not be treated as proof that the missing products were delisted.",
        previous_rows,
        current_rows,
    )


def verify_credentials(html: str) -> dict[str, Any]:
    """Re-read the shop's own search config and our group from a logged-in page.

    The only step that needs a real browser session, and it is needed rarely:
    when the public search key is rotated or our account is moved to another
    price group. Pass the authenticated page's HTML; what comes back is what
    the constants at the top of this module should say.
    """

    found: dict[str, Any] = {}
    config = re.search(r"algoliaConfig\s*=\s*(\{.{0,2000}?\})\s*;", html, re.S)
    if config:
        try:
            parsed = json.loads(config.group(1))
            found.update(
                app_id=parsed.get("app_id"),
                search_key=parsed.get("search_only_api_key"),
                index_name=parsed.get("product_index_name"),
            )
        except json.JSONDecodeError:
            found["config_error"] = "algoliaConfig was found but is not valid JSON"
    group = re.search(r'"(?:customer_)?group_id"\s*:\s*"?(\d+)', html)
    if group:
        found["customer_group"] = group.group(1)
    found["matches_current_settings"] = (
        found.get("app_id") == DEFAULT_APP_ID
        and found.get("search_key") == DEFAULT_SEARCH_KEY
        and found.get("index_name") == DEFAULT_INDEX
        and found.get("customer_group") == DEFAULT_CUSTOMER_GROUP
    )
    return found

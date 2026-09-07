"""DaysmartOrderV1 — the OBSERVED shape of a Daysmart purchase order.

Everything here was learned from real captures (the golden fixture
``tests/fixtures/purchase_orders/daysmart_order_list_page1.json``), not from
documentation — Daysmart publishes none for this surface. The rules this
module enforces, each traceable to the capture:

- Money arrives as strings; ``""`` means UNKNOWN and parses to ``None`` —
  never zero. ``"0.00"`` parses to ``Decimal("0.00")``. The two stay
  distinguishable forever. No float ever touches a money value.
- Status is a 2-of-N sample: only ``{1: Open, 4: Closed}`` have been
  observed. An unseen id is a named issue with the raw id preserved, and the
  mapped status is explicitly ``None`` — never a silent default.
- ``supplier.encoded_id`` is a per-response signed token (the same supplier
  carries a different value in every response) — the contract does not even
  have the attribute. ``supplier.id`` is the identity.
- ``index`` is the user-facing order number and is provably non-contiguous;
  nothing may derive "next number" from it.
- Unknown EXTRA fields are tolerated (Daysmart may add things); fields the
  contract RELIES ON are strict (missing/of the wrong shape → named issue).

Parsing never raises on shape problems. The only failure channel is the
result object: ``.order is None`` plus named issues for blocking problems;
order returned WITH issues for non-blocking ones (see ``issues.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import ValidationError as PydanticValidationError

from .base import ContractModel, register_contract
from .issues import IssueCode, PoValidationIssue

#: Plain decimal strings only. Deliberately refuses what Decimal() would
#: accept: "NaN", "Infinity", "1e5" are not money.
_MONEY_RE = re.compile(r"^-?\d+(\.\d+)?$")

#: The complete observed status vocabulary. Growing this set is a deliberate
#: act (new member + test), triggered by a PO_STATUS_UNKNOWN issue in the
#: wild — never by a default branch.
STATUS_VOCABULARY: dict[int, tuple[str, str]] = {
    1: ("open", "Open"),
    #: Wire-proven 2026-09-01: submit-order moved order 795607 from 1 → 2,
    #: and the list showed {"id": 2, "label": "Submitted"}.
    2: ("submitted", "Submitted"),
    4: ("closed", "Closed"),
}


@register_contract
class DaysmartSupplierRefV1(ContractModel):
    """Supplier identity as an order carries it. ``encoded_id`` is excluded
    by design — it is a per-response token, not an identifier."""

    contract_id: ClassVar[str] = "purchase_orders.daysmart_supplier_ref.v1"

    daysmart_id: str
    name: str


@register_contract
class DaysmartOrderV1(ContractModel):
    """One purchase order as Daysmart's list/detail surface returns it."""

    contract_id: ClassVar[str] = "purchase_orders.daysmart_order.v1"

    daysmart_id: int
    #: User-facing order number ("Order #518" → 518). Display and citation
    #: only: provably non-contiguous, never a local sequence source.
    index: int
    name: str
    account_no: str
    order_date: datetime
    ship_to_id: int | None
    bill_to_id: int | None
    #: None means Daysmart sent "" — unknown, not zero.
    sub_total: Decimal | None
    shipping: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    payment_terms: str
    notes: str
    #: Raw status verbatim, always kept …
    status_id: int
    status_label: str
    #: … and the mapped value, None when the id is outside the vocabulary.
    #: Typed ``str`` on purpose: STATUS_VOCABULARY is the ONE declaration of
    #: the vocabulary — a Literal here would hard-code it a second time and
    #: silently block newly-classified statuses (the H8 drift class).
    status: str | None
    order_name: str
    is_active: bool
    business_id: str
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    item_count: int
    supplier: DaysmartSupplierRefV1


@register_contract
class DaysmartPageMetaV1(ContractModel):
    """Pagination meta. Parsed tolerantly: the real capture lies in places
    the sync never relies on (``count`` is 0 beside 50 resources;
    ``prevPageUrl`` points at page 1 while on page 1)."""

    contract_id: ClassVar[str] = "purchase_orders.daysmart_page_meta.v1"

    total: int
    per_page: int
    current_page: int
    last_page: int


@dataclass(frozen=True)
class DaysmartOrderParseResult:
    order: DaysmartOrderV1 | None
    issues: list[PoValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class DaysmartOrderPageParseResult:
    #: Successfully parsed orders only — a bad order lands in ``issues``,
    #: never poisons its page-mates.
    orders: list[DaysmartOrderV1]
    issues: list[PoValidationIssue]
    meta: DaysmartPageMetaV1


#: Sentinel distinguishing "key absent" from "key present with value None".
#: JSON null is a present-but-invalid value, not a missing field.
_ABSENT = object()


class _OrderReader:
    """Collects typed values and named issues from one raw order dict."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.issues: list[PoValidationIssue] = []
        raw_id = raw.get("id")
        self.daysmart_id: int | None = raw_id if _is_int(raw_id) else None

    def _issue(self, code: IssueCode, path: str, value: Any, expected: str) -> None:
        self.issues.append(PoValidationIssue(
            code=code, field_path=path, raw_value=value,
            expected=expected, daysmart_id=self.daysmart_id,
        ))

    def require(self, path: str) -> Any:
        """The raw value, or ``_ABSENT`` (with a missing-field issue) if the
        key is not there at all. A present ``None`` is returned as ``None``
        for the caller's shape check to reject as invalid."""
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return _ABSENT
        return self.raw[path]

    def integer(self, path: str) -> int | None:
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        value = self.raw[path]
        if not _is_int(value):
            self._issue(IssueCode.PO_FIELD_INVALID, path, value, "integer")
            return None
        return value

    def optional_integer(self, path: str) -> int | None:
        """Absent or null → None silently (normal). PRESENT with the wrong
        shape → None plus a non-blocking issue: silent nulling of drifted
        data is exactly the failure mode the review caught here."""
        if path not in self.raw or self.raw[path] is None:
            return None
        value = self.raw[path]
        if not _is_int(value):
            self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, path, value,
                        "integer or null")
            return None
        return value

    def text(self, path: str, *, required: bool = False) -> str:
        if path not in self.raw:
            if required:
                self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return ""
        value = self.raw[path]
        if not isinstance(value, str):
            if required:
                self._issue(IssueCode.PO_FIELD_INVALID, path, value, "string")
            return ""
        return value

    def money(self, path: str) -> Decimal | None:
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        value = self.raw[path]
        if value == "":
            return None  # unknown — deliberately NOT zero
        if not isinstance(value, str) or not _MONEY_RE.match(value):
            self._issue(IssueCode.PO_MONEY_UNPARSEABLE, path, value,
                        'decimal string like "690.00", or "" for unknown')
            return None
        return Decimal(value)

    def timestamp(self, path: str) -> datetime | None:
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        value = self.raw[path]
        parsed = None
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                parsed = None
        if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
            self._issue(IssueCode.PO_FIELD_INVALID, path, value,
                        "timezone-aware ISO-8601 timestamp")
            return None
        return parsed

    def status(self) -> tuple[int | None, str, str | None]:
        """(status_id, status_label, mapped) — mapped is None off-vocabulary."""
        value = self.require("status")
        if value is _ABSENT:
            return None, "", None
        if not isinstance(value, dict) or not _is_int(value.get("id")):
            self._issue(IssueCode.PO_FIELD_INVALID, "status", value,
                        'object like {"id": 1, "label": "Open"}')
            return None, "", None
        status_id = value["id"]
        label = value.get("label", "") if isinstance(value.get("label", ""), str) else ""
        known = STATUS_VOCABULARY.get(status_id)
        if known is None:
            self._issue(IssueCode.PO_STATUS_UNKNOWN, "status.id", status_id,
                        f"one of {sorted(STATUS_VOCABULARY)}")
            return status_id, label, None
        mapped, expected_label = known
        if label != expected_label:
            self._issue(IssueCode.PO_STATUS_LABEL_DRIFT, "status.label", label,
                        f'"{expected_label}" for status id {status_id}')
        return status_id, label, mapped

    def supplier(self) -> DaysmartSupplierRefV1 | None:
        value = self.require("supplier")
        if value is _ABSENT:
            return None
        if (not isinstance(value, dict)
                or not isinstance(value.get("id"), str) or not value["id"]
                or not isinstance(value.get("name"), str)):
            self._issue(IssueCode.PO_FIELD_INVALID, "supplier", value,
                        'object with string "id" and "name"')
            return None
        return DaysmartSupplierRefV1(daysmart_id=value["id"], name=value["name"])


def parse_daysmart_order(raw: dict[str, Any]) -> DaysmartOrderParseResult:
    """Parse one raw Daysmart order. Never raises on shape problems."""
    reader = _OrderReader(raw if isinstance(raw, dict) else {})

    daysmart_id = reader.integer("id")
    index = reader.integer("index")
    order_date = reader.timestamp("order_date")
    created_at = reader.timestamp("create_at")
    updated_at = reader.timestamp("update_at")
    sub_total = reader.money("sub_total")
    shipping = reader.money("shipping")
    tax = reader.money("tax")
    total = reader.money("total")
    status_id, status_label, status = reader.status()
    supplier = reader.supplier()
    item_count = reader.integer("item_count")
    business_id = reader.text("business_id", required=True)
    created_by = reader.text("create_by", required=True)
    updated_by = reader.text("update_by", required=True)

    if any(issue.blocking for issue in reader.issues):
        return DaysmartOrderParseResult(order=None, issues=reader.issues)

    try:
        order = _build_order(reader, raw, daysmart_id=daysmart_id, index=index,
                             order_date=order_date, created_at=created_at,
                             updated_at=updated_at, sub_total=sub_total,
                             shipping=shipping, tax=tax, total=total,
                             status_id=status_id, status_label=status_label,
                             status=status, supplier=supplier,
                             item_count=item_count, business_id=business_id,
                             created_by=created_by, updated_by=updated_by)
    except PydanticValidationError as exc:
        # Structural backstop for the never-raise guarantee: a value the
        # reader failed to reject becomes a named issue, not an escaping
        # exception. Each occurrence is a reader gap worth closing.
        for error in exc.errors():
            reader._issue(
                IssueCode.PO_FIELD_INVALID,
                ".".join(str(part) for part in error["loc"]) or "(order)",
                error.get("input"),
                error["msg"],
            )
        return DaysmartOrderParseResult(order=None, issues=reader.issues)
    return DaysmartOrderParseResult(order=order, issues=reader.issues)


def _build_order(reader: _OrderReader, raw: dict[str, Any], **typed: Any) -> DaysmartOrderV1:
    return DaysmartOrderV1(
        name=reader.text("name"),
        account_no=reader.text("account_no"),
        ship_to_id=reader.optional_integer("ship_to_id"),
        bill_to_id=reader.optional_integer("bill_to_id"),
        payment_terms=reader.text("payment_terms"),
        notes=reader.text("notes"),
        order_name=reader.text("order_name"),
        is_active=bool(raw.get("is_active", True)),
        **typed,
    )


def parse_daysmart_order_page(payload: dict[str, Any]) -> DaysmartOrderPageParseResult:
    """Parse a full order-list page (the ``{"response": {...}}`` envelope)."""
    response = payload.get("response", {}) if isinstance(payload, dict) else {}
    if not isinstance(response, dict):
        response = {}

    raw_meta = response.get("meta", {})
    if not isinstance(raw_meta, dict):
        raw_meta = {}
    meta = DaysmartPageMetaV1(
        total=_int_or(raw_meta.get("total"), 0),
        per_page=_int_or(raw_meta.get("perPage"), 0),
        current_page=_int_or(raw_meta.get("currentPage"), 0),
        last_page=_int_or(raw_meta.get("lastPage"), 0),
    )

    resources = response.get("resources", [])
    if not isinstance(resources, list):
        resources = []

    orders: list[DaysmartOrderV1] = []
    issues: list[PoValidationIssue] = []
    for raw in resources:
        result = parse_daysmart_order(raw if isinstance(raw, dict) else {})
        if result.order is not None:
            orders.append(result.order)
        issues.extend(result.issues)
    return DaysmartOrderPageParseResult(orders=orders, issues=issues, meta=meta)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _int_or(value: Any, default: int) -> int:
    return value if _is_int(value) else default

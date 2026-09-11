"""Contract for Daysmart RECEIPTS as the list and detail surfaces return them.

Receipts are their own resource family with their OWN status vocabulary
(``RECEIPT_STATUS_VOCABULARY``: 0 = in process / unposted, 1 = Posted) —
never the order map, where 1 means Open. One list row MIXES dialects
inside a single payload (wire, ``daysmart_receipt_list_page1.json``):
``date`` and ``post_at`` are PHP-UTC objects while ``create_at`` /
``update_at`` are ISO ``+08:00`` strings; money is a string beside a
bare-number ``total_amount``; empties are "" on the list and null on the
detail; ``index`` is an int on the list and ``"Receipt #493"`` on the
detail. The reader accepts both spellings of every field, so one contract
serves both surfaces.

Rows (``receipt_items``) come only from the DETAIL surface
(``receipt/{id}``) — the flat ``receipt/items/{id}`` list has no
``purchase_order_item_id`` and is never used for identity. Row identities
(``id``, ``purchase_order_item_id``, ``purchase_order_id``,
``purchase_order_receipt_id``, ``item_id``) BLOCK the row when malformed —
a receipt row must never be matched by guess; everything else is
withheld with a non-blocking issue.

Same discipline as every other contract: parsing NEVER raises on shape;
``(value | None, issues)``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import ValidationError as PydanticValidationError

from .base import ContractModel, register_contract
from .daysmart_order_v1 import _MONEY_RE, DaysmartPageMetaV1, DaysmartSupplierRefV1
from .issues import IssueCode, PoValidationIssue

#: Receipt status → (mapped, expected label). Both labels are wire-observed
#: (receipt-edit.har, 2026-09-02: a fresh receipt lists as
#: ``{"id": 0, "label": "In Process"}``). ``None`` would mean "label not
#: yet observed — accept any"; nothing needs it today.
RECEIPT_STATUS_VOCABULARY: dict[int, tuple[str, str | None]] = {
    0: ("in_process", "In Process"),
    1: ("posted", "Posted"),
}

_ABSENT = object()
_INDEX_RE = re.compile(r"#\s*(\d+)\s*$")

#: Field paths whose problems mean a row's IDENTITY is unreadable.
ROW_IDENTITY_PATHS = frozenset({
    "id", "purchase_order_item_id", "purchase_order_id",
    "purchase_order_receipt_id", "item_id", "(row)", "receipt_items",
    "response.resources",
})


@register_contract
class DaysmartReceiptV1(ContractModel):
    """One receipt header, as the list (or detail) surface returns it."""

    contract_id: ClassVar[str] = "purchase_orders.daysmart_receipt.v1"

    daysmart_id: int
    #: List: int. Detail: "Receipt #493" → 493. None when unreadable.
    index: int | None
    name: str
    status_id: int
    status_label: str
    #: Mapped through RECEIPT_STATUS_VOCABULARY; None off-vocabulary.
    status: str | None
    receipt_date: datetime
    post_at: datetime | None
    invoice_number: str
    #: None = Daysmart sent "" / null — unknown, never zero.
    amount: Decimal | None
    tax: Decimal | None
    shipping: Decimal | None
    tax_percentage: Decimal | None
    tax_type: int | None
    notes: str
    is_active: bool
    is_with_order: int | None
    #: The linked order; null after a soft delete.
    purchase_order_id: int | None
    supplier: DaysmartSupplierRefV1 | None
    supplier_daysmart_id: str
    business_id: str
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
    item_count: int | None


@register_contract
class DaysmartReceiptRowV1(ContractModel):
    """One generated receipt row from the detail's ``receipt_items``."""

    contract_id: ClassVar[str] = "purchase_orders.daysmart_receipt_row.v1"

    daysmart_row_id: int
    receipt_id: int | None
    #: Daysmart's PO line id (``purchase_order_item_id``) — the link to our line.
    po_line_id: int | None
    purchase_order_id: int | None
    #: The inventory item stock posts to — a "DIT-…" string.
    post_to_item_id: str | None
    quantity_received: int | None
    quantity_in_stock: int | None
    quantity_rejected: int | None
    #: Money VERBATIM (the wire mixes bare numbers and strings).
    amount_raw: str | None
    tax_raw: str | None
    shipping_raw: str | None
    lot_number: str | None
    #: Epoch seconds, as sent.
    expiration_date: int | None
    manufacturer: str | None
    ndc_code: str | None
    #: Row-level status, raw — its vocabulary is unobserved (0 seen on an
    #: unposted receipt, 2 on a posted one).
    status_id: int | None
    is_active: bool
    updated_at: datetime | None


@dataclass(frozen=True)
class DaysmartReceiptParseResult:
    receipt: DaysmartReceiptV1 | None
    issues: list[PoValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class DaysmartReceiptPageParseResult:
    receipts: list[DaysmartReceiptV1]
    issues: list[PoValidationIssue]
    meta: DaysmartPageMetaV1


@dataclass(frozen=True)
class DaysmartReceiptRowParseResult:
    row: DaysmartReceiptRowV1 | None
    issues: list[PoValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class DaysmartReceiptDetailParseResult:
    """``receipt/{id}``: the header (detail dialect) and its generated rows."""
    receipt: DaysmartReceiptV1 | None
    rows: list[DaysmartReceiptRowV1]
    issues: list[PoValidationIssue]
    receipt_daysmart_id: int | None


@dataclass(frozen=True)
class DaysmartReceiptRowsParseResult:
    rows: list[DaysmartReceiptRowV1]
    issues: list[PoValidationIssue]
    #: The receipt the detail describes (``resources.id``), when readable.
    receipt_daysmart_id: int | None


class _Reader:
    """Typed values + named issues from one raw dict, accepting both the
    list and the detail spelling of every field."""

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.issues: list[PoValidationIssue] = []
        raw_id = raw.get("id")
        self.daysmart_id: int | None = raw_id if _is_int(raw_id) else None

    def _issue(self, code: IssueCode, path: str, value: Any, expected: str) -> None:
        self.issues.append(PoValidationIssue(
            code=code, field_path=path, raw_value=value,
            expected=expected, daysmart_id=self.daysmart_id))

    def integer(self, path: str) -> int | None:
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        value = self.raw[path]
        if not _is_int(value):
            self._issue(IssueCode.PO_FIELD_INVALID, path, value, "integer")
            return None
        return value

    def optional_integer(self, path: str, *, identity: bool = False) -> int | None:
        """Absent/null → None. Present with the wrong shape → None plus an
        issue: BLOCKING when the field is an identity, non-blocking
        otherwise (silent nulling of drifted data is the failure mode the
        review caught)."""
        if path not in self.raw or self.raw[path] is None or self.raw[path] == "":
            return None                      # "" is the list dialect's null
        value = self.raw[path]
        if not _is_int(value):
            code = IssueCode.PO_FIELD_INVALID if identity else IssueCode.PO_OPTIONAL_FIELD_INVALID
            self._issue(code, path, value, "integer or null")
            return None
        return value

    def text(self, path: str) -> str:
        """"" for absent / null; a present non-string is withheld (non-blocking)."""
        value = self.raw.get(path)
        if value is None:
            return ""
        if not isinstance(value, str):
            self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, path, value, "string or null")
            return ""
        return value

    def optional_text(self, path: str) -> str | None:
        value = self.raw.get(path)
        if value is None:
            return None
        if not isinstance(value, str):
            self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, path, value, "string or null")
            return None
        return value

    def required_text(self, path: str) -> str | None:
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        value = self.raw[path]
        if not isinstance(value, str) or not value:
            self._issue(IssueCode.PO_FIELD_INVALID, path, value, "non-empty string")
            return None
        return value

    def money(self, path: str) -> Decimal | None:
        """"" / null → None (unknown, never zero). A decimal string OR a bare
        number (the wire sends both) → Decimal. Anything else BLOCKS."""
        value = self.raw.get(path)
        if value is None or value == "":
            return None
        if _is_number(value):
            return Decimal(str(value))
        if isinstance(value, str) and _MONEY_RE.match(value):
            return Decimal(value)
        self._issue(IssueCode.PO_MONEY_UNPARSEABLE, path, value,
                    'decimal string like "690.00", a bare number, or ""/null for unknown')
        return None

    def money_verbatim(self, path: str) -> str | None:
        """Money kept exactly as sent (rows): number → its text, string
        → stripped; anything else withheld (non-blocking)."""
        value = self.raw.get(path)
        if value is None or value == "":
            return None
        if _is_number(value):
            return str(value)
        if isinstance(value, str) and _MONEY_RE.match(value.strip()):
            return value.strip()
        self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, path, value,
                    "decimal string or bare number")
        return None

    def timestamp(self, path: str, *, required: bool = True) -> datetime | None:
        """ISO-8601 with an offset (list) OR a PHP object
        ``{"date": "...", "timezone": "UTC"}`` (detail / mixed rows)."""
        if path not in self.raw or self.raw[path] is None:
            if required:
                self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        value = self.raw[path]
        parsed: datetime | None = None
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                parsed = None
            if parsed is not None and parsed.utcoffset() is None:
                parsed = None
        elif isinstance(value, dict) and isinstance(value.get("date"), str):
            if value.get("timezone") != "UTC":
                # Only UTC has ever been observed; guessing another zone
                # would silently shift a date — block instead.
                self._issue(IssueCode.PO_FIELD_INVALID, path, value,
                            'timezone "UTC" (the only observed value)')
                return None
            try:
                parsed = datetime.fromisoformat(value["date"]).replace(tzinfo=timezone.utc)
            except ValueError:
                parsed = None
        if parsed is None:
            code = IssueCode.PO_FIELD_INVALID if required else IssueCode.PO_OPTIONAL_FIELD_INVALID
            self._issue(code, path, value,
                        'timezone-aware ISO-8601 string or {"date": "...", "timezone": "UTC"}')
            return None
        return parsed

    def status(self) -> tuple[int | None, str, str | None]:
        """(status_id, label, mapped). Accepts ``{"id", "label"}`` (list /
        detail) and a bare int (echoes). Unknown ids are non-blocking."""
        value = self.raw.get("status", _ABSENT)
        if value is _ABSENT:
            self._issue(IssueCode.PO_FIELD_MISSING, "status", None, "field present")
            return None, "", None
        label = ""
        labelled = False
        if isinstance(value, dict) and _is_int(value.get("id")):
            status_id = value["id"]
            raw_label = value.get("label", "")
            label = raw_label if isinstance(raw_label, str) else ""
            labelled = True
        elif _is_int(value):
            status_id = value
        else:
            self._issue(IssueCode.PO_FIELD_INVALID, "status", value,
                        'object like {"id": 1, "label": "Posted"} or a bare integer')
            return None, "", None
        known = RECEIPT_STATUS_VOCABULARY.get(status_id)
        if known is None:
            self._issue(IssueCode.PO_STATUS_UNKNOWN, "status.id", status_id,
                        f"one of {sorted(RECEIPT_STATUS_VOCABULARY)}")
            return status_id, label, None
        mapped, expected_label = known
        if labelled and expected_label is not None and label != expected_label:
            self._issue(IssueCode.PO_STATUS_LABEL_DRIFT, "status.label", label,
                        f'"{expected_label}" for receipt status id {status_id}')
        return status_id, label, mapped

    def supplier(self) -> DaysmartSupplierRefV1 | None:
        value = self.raw.get("supplier")
        if value is None:
            return None
        if (not isinstance(value, dict) or not isinstance(value.get("id"), str)
                or not value["id"] or not isinstance(value.get("name"), str)):
            self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, "supplier", value,
                        'object with string "id" and "name", or null')
            return None
        return DaysmartSupplierRefV1(daysmart_id=value["id"], name=value["name"])

    def supplier_identity(self) -> str | None:
        """``supplier_id`` (detail / echoes) or ``supplier.id`` (the list row
        carries only the object). Blocking when neither is readable."""
        value = self.raw.get("supplier_id")
        if isinstance(value, str) and value:
            return value
        supplier = self.raw.get("supplier")
        if isinstance(supplier, dict) and isinstance(supplier.get("id"), str) and supplier["id"]:
            return supplier["id"]
        present = "supplier_id" in self.raw or supplier is not None
        self._issue(IssueCode.PO_FIELD_INVALID if present else IssueCode.PO_FIELD_MISSING,
                    "supplier_id", value,
                    'non-empty string, or a "supplier" object with a string "id"')
        return None

    def index(self) -> int | None:
        value = self.raw.get("index")
        if value is None:
            return None
        if _is_int(value):
            return value
        if isinstance(value, str):
            match = _INDEX_RE.search(value)
            if match:
                return int(match.group(1))
        self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, "index", value,
                    'integer or "Receipt #<n>"')
        return None

    def post_to(self) -> str | None:
        value = self.raw.get("item_id")
        if value is None:
            return None
        if not isinstance(value, str) or not value.startswith("DIT-"):
            self._issue(IssueCode.PO_FIELD_INVALID, "item_id", value,
                        'inventory item id string ("DIT-…") or null')
            return None
        return value


def parse_daysmart_receipt(raw: dict[str, Any]) -> DaysmartReceiptParseResult:
    """Parse one raw receipt header. Never raises on shape problems."""
    reader = _Reader(raw if isinstance(raw, dict) else {})

    daysmart_id = reader.integer("id")
    supplier_daysmart_id = reader.supplier_identity()
    receipt_date = reader.timestamp("date")
    created_at = reader.timestamp("create_at")
    updated_at = reader.timestamp("update_at")
    post_at = reader.timestamp("post_at", required=False)
    amount = reader.money("amount")
    tax = reader.money("tax")
    shipping = reader.money("shipping")
    tax_percentage = reader.money("tax_percentage")
    status_id, status_label, status = reader.status()
    business_id = reader.required_text("business_id")
    created_by = reader.required_text("create_by")
    updated_by = reader.required_text("update_by")

    if any(issue.blocking for issue in reader.issues):
        return DaysmartReceiptParseResult(receipt=None, issues=reader.issues)

    try:
        receipt = DaysmartReceiptV1(
            daysmart_id=daysmart_id,
            index=reader.index(),
            name=reader.text("name"),
            status_id=status_id,
            status_label=status_label,
            status=status,
            receipt_date=receipt_date,
            post_at=post_at,
            invoice_number=reader.text("invoice_number"),
            amount=amount, tax=tax, shipping=shipping, tax_percentage=tax_percentage,
            tax_type=reader.optional_integer("tax_type"),
            notes=reader.text("notes"),
            is_active=bool(reader.raw.get("is_active", True)),
            is_with_order=reader.optional_integer("is_with_order"),
            purchase_order_id=reader.optional_integer("purchase_order_id"),
            supplier=reader.supplier(),
            supplier_daysmart_id=supplier_daysmart_id,
            business_id=business_id,
            created_by=created_by,
            created_at=created_at,
            updated_by=updated_by,
            updated_at=updated_at,
            item_count=reader.optional_integer("item_count"),
        )
    except PydanticValidationError as exc:
        for error in exc.errors():
            reader._issue(IssueCode.PO_FIELD_INVALID,
                          ".".join(str(part) for part in error["loc"]) or "(receipt)",
                          error.get("input"), error["msg"])
        return DaysmartReceiptParseResult(receipt=None, issues=reader.issues)
    return DaysmartReceiptParseResult(receipt=receipt, issues=reader.issues)


def parse_daysmart_receipt_page(payload: Any) -> DaysmartReceiptPageParseResult:
    """Parse a receipt-list page (the ``{"response": {meta, resources}}``
    envelope — the same shape as the order list)."""
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
    receipts: list[DaysmartReceiptV1] = []
    issues: list[PoValidationIssue] = []
    for raw in resources:
        result = parse_daysmart_receipt(raw if isinstance(raw, dict) else {})
        if result.receipt is not None:
            receipts.append(result.receipt)
        issues.extend(result.issues)
    return DaysmartReceiptPageParseResult(receipts=receipts, issues=issues, meta=meta)


def parse_daysmart_receipt_row(raw: dict[str, Any]) -> DaysmartReceiptRowParseResult:
    """Parse one generated row. Identity problems block the row."""
    reader = _Reader(raw if isinstance(raw, dict) else {})
    row_id = reader.integer("id")
    receipt_id = reader.optional_integer("purchase_order_receipt_id", identity=True)
    po_line_id = reader.optional_integer("purchase_order_item_id", identity=True)
    purchase_order_id = reader.optional_integer("purchase_order_id", identity=True)
    post_to_item_id = reader.post_to()
    if any(issue.blocking for issue in reader.issues):
        return DaysmartReceiptRowParseResult(row=None, issues=reader.issues)

    try:
        row = DaysmartReceiptRowV1(
            daysmart_row_id=row_id,
            receipt_id=receipt_id,
            po_line_id=po_line_id,
            purchase_order_id=purchase_order_id,
            post_to_item_id=post_to_item_id,
            quantity_received=reader.optional_integer("quantity_received"),
            quantity_in_stock=reader.optional_integer("quantity_in_stock"),
            quantity_rejected=reader.optional_integer("quantity_rejected"),
            amount_raw=reader.money_verbatim("amount"),
            tax_raw=reader.money_verbatim("tax"),
            shipping_raw=reader.money_verbatim("shipping"),
            lot_number=reader.optional_text("lot_number"),
            expiration_date=reader.optional_integer("expiration_date"),
            manufacturer=reader.optional_text("manufacturer"),
            ndc_code=reader.optional_text("ndc_code"),
            status_id=reader.optional_integer("status"),
            is_active=bool(reader.raw.get("is_active", True)),
            updated_at=reader.timestamp("update_at", required=False),
        )
    except PydanticValidationError as exc:
        for error in exc.errors():
            reader._issue(IssueCode.PO_FIELD_INVALID,
                          ".".join(str(part) for part in error["loc"]) or "(row)",
                          error.get("input"), error["msg"])
        return DaysmartReceiptRowParseResult(row=None, issues=reader.issues)
    return DaysmartReceiptRowParseResult(row=row, issues=reader.issues)


def parse_daysmart_receipt_rows(payload: Any) -> DaysmartReceiptRowsParseResult:
    """Parse the detail envelope's ``receipt_items``. Never raises."""
    resources: Any = _ABSENT
    if isinstance(payload, dict):
        response = payload.get("response")
        if isinstance(response, dict):
            resources = response.get("resources", _ABSENT)
    if resources is _ABSENT or not isinstance(resources, dict):
        return DaysmartReceiptRowsParseResult(rows=[], receipt_daysmart_id=None, issues=[
            PoValidationIssue(
                code=IssueCode.PO_FIELD_MISSING if resources is _ABSENT else IssueCode.PO_FIELD_INVALID,
                field_path="response.resources",
                raw_value=None if resources is _ABSENT else resources,
                expected="envelope with a resources object")])
    raw_id = resources.get("id")
    receipt_daysmart_id = raw_id if _is_int(raw_id) else None
    raw_items = resources.get("receipt_items", _ABSENT)
    if raw_items is _ABSENT or not isinstance(raw_items, list):
        return DaysmartReceiptRowsParseResult(rows=[], receipt_daysmart_id=receipt_daysmart_id, issues=[
            PoValidationIssue(
                code=IssueCode.PO_FIELD_MISSING if raw_items is _ABSENT else IssueCode.PO_FIELD_INVALID,
                field_path="receipt_items",
                raw_value=None if raw_items is _ABSENT else raw_items,
                expected="list of receipt rows", daysmart_id=receipt_daysmart_id)])
    rows: list[DaysmartReceiptRowV1] = []
    issues: list[PoValidationIssue] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            issues.append(PoValidationIssue(
                code=IssueCode.PO_FIELD_INVALID, field_path="(row)", raw_value=raw,
                expected="object", daysmart_id=receipt_daysmart_id))
            continue
        result = parse_daysmart_receipt_row(raw)
        if result.row is not None:
            rows.append(result.row)
        issues.extend(result.issues)
    return DaysmartReceiptRowsParseResult(rows=rows, issues=issues,
                                          receipt_daysmart_id=receipt_daysmart_id)


def parse_daysmart_receipt_detail(payload: Any) -> DaysmartReceiptDetailParseResult:
    """The detail envelope: header AND rows. The header speaks the detail
    dialect (PHP-UTC dates everywhere, "Receipt #N" index, null empties,
    status object, supplier object) — the same reader handles it. An
    UNPOSTED receipt's header carries NO order link here (null); its rows
    do. Never raises."""
    rows = parse_daysmart_receipt_rows(payload)
    resources: Any = None
    if isinstance(payload, dict) and isinstance(payload.get("response"), dict):
        resources = payload["response"].get("resources")
    if not isinstance(resources, dict):
        return DaysmartReceiptDetailParseResult(
            receipt=None, rows=rows.rows, issues=rows.issues,
            receipt_daysmart_id=rows.receipt_daysmart_id)
    header = parse_daysmart_receipt(resources)
    return DaysmartReceiptDetailParseResult(
        receipt=header.receipt, rows=rows.rows,
        issues=header.issues + rows.issues,
        receipt_daysmart_id=rows.receipt_daysmart_id)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _int_or(value: Any, default: int) -> int:
    return value if _is_int(value) else default

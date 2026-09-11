"""CreatePurchaseOrderCommandV1 — what OUR clients send to create a PO.

The strict side of the boundary. The Daysmart read contract tolerates
unknown additions because we don't own that surface; this command is ours,
so unknown fields are refused, and every rule the business owns is enforced
at the door:

- Header creation and line creation are separate commands.  A PO header can
  therefore be created without lines; line validation lives in
  ``parse_line_commands`` and remains strict.
- Every quantity carries a UOM ("unknown ≠ 1 unit").
- Quantity is a positive integer. Unit price is a non-negative decimal
  STRING — "0.00" is legal (free goods are business reality here), floats
  never are (money is never float), negatives never are.
- ``order_date`` must be timezone-aware.
- Addresses are optional; the MAPPER applies the configured clinic
  defaults — clients don't hand-type magic numbers.

Same result style as the read contract — ``(command | None, issues)`` — and
ALL problems are collected in one pass, so a client's 422 names everything
at once instead of failing one field per attempt. Every command issue is
blocking: nothing half-valid proceeds.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, ClassVar

from .base import ContractModel, register_contract
from .issues import IssueCode, PoValidationIssue

_PRICE_RE = re.compile(r"^\d+(\.\d+)?$")  # non-negative decimal string

_COMMAND_FIELDS = {
    "supplier_daysmart_id", "supplier_name", "order_date", "account_no",
    "notes", "template_id", "ship_to_id", "bill_to_id",
}
_LINE_FIELDS = {
    "description", "quantity", "quantity_uom", "unit_price",
    "supplier_offering_id", "daysmart_inventory_item_id", "notes",
}


@register_contract
class CreatePurchaseOrderLineCommandV1(ContractModel):
    contract_id: ClassVar[str] = "purchase_orders.create_line_command.v1"

    description: str
    quantity: int
    quantity_uom: str
    unit_price: Decimal
    supplier_offering_id: int | None = None
    #: Daysmart's INVENTORY item id — required for the line to be sendable
    #: (the item/save payload's ``item_id``). A "DIT-…" STRING, wire-proven
    #: (order line rows carry item_id "DIT-ebbd2f4c-6e6"-style values — the
    #: minified spike source hid this). None = free-typed line: saved
    #: locally, never sent; OPS completes it in Daysmart.
    daysmart_inventory_item_id: str | None = None
    notes: str = ""


@register_contract
class CreatePurchaseOrderCommandV1(ContractModel):
    contract_id: ClassVar[str] = "purchase_orders.create_command.v1"

    supplier_daysmart_id: str
    supplier_name: str
    order_date: datetime
    account_no: str = ""
    notes: str = ""
    template_id: str | None = None
    #: None means "use the configured default" — resolved by the mapper.
    ship_to_id: int | None = None
    bill_to_id: int | None = None


@dataclass(frozen=True)
class CreateCommandParseResult:
    command: CreatePurchaseOrderCommandV1 | None
    issues: list[PoValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class LineCommandsParseResult:
    lines: tuple[CreatePurchaseOrderLineCommandV1, ...] | None
    issues: list[PoValidationIssue] = field(default_factory=list)


class _CommandReader:
    def __init__(self) -> None:
        self.issues: list[PoValidationIssue] = []

    def issue(self, code: IssueCode, path: str, value: Any, expected: str) -> None:
        self.issues.append(PoValidationIssue(code=code, field_path=path,
                                             raw_value=value, expected=expected))

    def required_text(self, raw: dict, path: str, prefix: str = "") -> str | None:
        full = f"{prefix}{path}"
        if path not in raw:
            self.issue(IssueCode.PO_FIELD_MISSING, full, None, "field present")
            return None
        value = raw[path]
        if not isinstance(value, str) or not value.strip():
            self.issue(IssueCode.PO_FIELD_INVALID, full, value, "non-empty string")
            return None
        return value

    def optional_text(self, raw: dict, path: str, prefix: str = "") -> str | None:
        if path not in raw or raw[path] is None:
            return ""
        value = raw[path]
        if not isinstance(value, str):
            self.issue(IssueCode.PO_FIELD_INVALID, f"{prefix}{path}", value, "string")
            return None
        return value

    def optional_int(self, raw: dict, path: str, prefix: str = "") -> int | None:
        if path not in raw or raw[path] is None:
            return None
        value = raw[path]
        if not _is_int(value):
            self.issue(IssueCode.PO_FIELD_INVALID, f"{prefix}{path}", value, "integer")
        return value if _is_int(value) else None


def parse_create_command(raw: dict[str, Any]) -> CreateCommandParseResult:
    """Validate a raw create request. Collects EVERY problem; never raises."""
    reader = _CommandReader()
    if not isinstance(raw, dict):
        reader.issue(IssueCode.PO_FIELD_INVALID, "(request)", raw, "JSON object")
        return CreateCommandParseResult(command=None, issues=reader.issues)

    for key in raw:
        if key not in _COMMAND_FIELDS:
            reader.issue(IssueCode.PO_FIELD_INVALID, key, raw[key], "no such field")

    supplier_daysmart_id = reader.required_text(raw, "supplier_daysmart_id")
    supplier_name = reader.required_text(raw, "supplier_name")
    order_date = _tz_aware_datetime(reader, raw, "order_date")
    account_no = reader.optional_text(raw, "account_no")
    notes = reader.optional_text(raw, "notes")
    template_id = raw.get("template_id")
    if template_id is not None and not isinstance(template_id, str):
        reader.issue(IssueCode.PO_FIELD_INVALID, "template_id", template_id, "string or null")
        template_id = None
    ship_to_id = reader.optional_int(raw, "ship_to_id")
    bill_to_id = reader.optional_int(raw, "bill_to_id")
    if reader.issues:
        return CreateCommandParseResult(command=None, issues=reader.issues)

    command = CreatePurchaseOrderCommandV1(
        supplier_daysmart_id=supplier_daysmart_id,
        supplier_name=supplier_name,
        order_date=order_date,
        account_no=account_no,
        notes=notes,
        template_id=template_id,
        ship_to_id=ship_to_id,
        bill_to_id=bill_to_id,
    )
    return CreateCommandParseResult(command=command, issues=[])


def parse_line_commands(raw: Any) -> LineCommandsParseResult:
    """Validate the JSON array accepted by ``POST /{po_uuid}/lines``."""
    reader = _CommandReader()
    if not isinstance(raw, list) or not raw:
        reader.issue(IssueCode.PO_FIELD_INVALID, "(request)", raw,
                     "non-empty JSON array of purchase-order lines")
        return LineCommandsParseResult(lines=None, issues=reader.issues)

    lines = _parse_line_values(reader, raw, root="")
    if reader.issues:
        return LineCommandsParseResult(lines=None, issues=reader.issues)
    return LineCommandsParseResult(lines=tuple(lines), issues=[])


def _parse_line_values(reader: _CommandReader, raw: list,
                       *, root: str) -> list[CreatePurchaseOrderLineCommandV1]:
    lines: list[CreatePurchaseOrderLineCommandV1] = []
    for i, raw_line in enumerate(raw):
        item_path = f"{root}[{i}]" if root else f"[{i}]"
        prefix = f"{item_path}."
        if not isinstance(raw_line, dict):
            reader.issue(IssueCode.PO_FIELD_INVALID, item_path, raw_line, "object")
            continue
        for key in raw_line:
            if key not in _LINE_FIELDS:
                reader.issue(IssueCode.PO_FIELD_INVALID, f"{prefix}{key}",
                             raw_line[key], "no such field")

        description = reader.required_text(raw_line, "description", prefix)
        quantity_uom = reader.required_text(raw_line, "quantity_uom", prefix)
        quantity = _positive_int(reader, raw_line, "quantity", prefix)
        unit_price = _price(reader, raw_line, "unit_price", prefix)
        offering_id = reader.optional_int(raw_line, "supplier_offering_id", prefix)
        daysmart_item_id = raw_line.get("daysmart_inventory_item_id")
        if daysmart_item_id is not None and (
                not isinstance(daysmart_item_id, str) or not daysmart_item_id.strip()):
            reader.issue(IssueCode.PO_FIELD_INVALID,
                         f"{prefix}daysmart_inventory_item_id", daysmart_item_id,
                         'Daysmart inventory item id string ("DIT-…") or null')
            daysmart_item_id = None
        line_notes = reader.optional_text(raw_line, "notes", prefix)

        if None in (description, quantity_uom, quantity, unit_price):
            continue  # issues already recorded
        lines.append(CreatePurchaseOrderLineCommandV1(
            description=description, quantity=quantity, quantity_uom=quantity_uom,
            unit_price=unit_price, supplier_offering_id=offering_id,
            daysmart_inventory_item_id=daysmart_item_id,
            notes=line_notes or "",
        ))
    return lines


def _tz_aware_datetime(reader: _CommandReader, raw: dict, path: str) -> datetime | None:
    if path not in raw:
        reader.issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
        return None
    value = raw[path]
    parsed = None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            parsed = None
    if parsed is None or parsed.tzinfo is None or parsed.utcoffset() is None:
        reader.issue(IssueCode.PO_FIELD_INVALID, path, value,
                     "timezone-aware ISO-8601 timestamp")
        return None
    return parsed


def _positive_int(reader: _CommandReader, raw: dict, path: str, prefix: str) -> int | None:
    full = f"{prefix}{path}"
    if path not in raw:
        reader.issue(IssueCode.PO_FIELD_MISSING, full, None, "field present")
        return None
    value = raw[path]
    if not _is_int(value) or value < 1:
        reader.issue(IssueCode.PO_FIELD_INVALID, full, value,
                     "positive integer — zero-quantity lines have no meaning")
        return None
    return value


def _price(reader: _CommandReader, raw: dict, path: str, prefix: str) -> Decimal | None:
    full = f"{prefix}{path}"
    if path not in raw:
        reader.issue(IssueCode.PO_FIELD_MISSING, full, None, "field present")
        return None
    value = raw[path]
    # Money is never float, never negative. "0.00" is legal — free goods.
    if not isinstance(value, str) or not _PRICE_RE.match(value):
        reader.issue(IssueCode.PO_FIELD_INVALID, full, value,
                     'non-negative decimal string like "96.00" ("0.00" for free goods)')
        return None
    return Decimal(value)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Update — the header fields Daysmart lets us change after creation
# ---------------------------------------------------------------------------


@register_contract
class UpdatePurchaseOrderCommandV1(ContractModel):
    """A full replacement of the editable header (wire, PO-update.har:
    ``PUT order/{id}`` takes account_no, order_date, ship_to_id, bill_to_id,
    notes — the supplier is not editable). Address ids left null keep the
    PO's current ones."""

    contract_id: ClassVar[str] = "purchase_orders.update_command.v1"

    account_no: str
    order_date: datetime
    ship_to_id: int | None = None
    bill_to_id: int | None = None
    notes: str = ""


@dataclass(frozen=True)
class UpdateCommandParseResult:
    command: UpdatePurchaseOrderCommandV1 | None
    issues: list[PoValidationIssue] = field(default_factory=list)


_UPDATE_FIELDS = {"account_no", "order_date", "ship_to_id", "bill_to_id", "notes"}


def parse_update_command(raw: Any) -> UpdateCommandParseResult:
    """Validate the JSON body of ``PUT /purchase-orders/{po_uuid}``."""
    reader = _CommandReader()
    if not isinstance(raw, dict):
        reader.issue(IssueCode.PO_FIELD_INVALID, "(request)", raw, "JSON object")
        return UpdateCommandParseResult(command=None, issues=reader.issues)
    for key in raw:
        if key not in _UPDATE_FIELDS:
            reader.issue(IssueCode.PO_FIELD_INVALID, key, raw[key], "no such field")
    order_date = _tz_aware_datetime(reader, raw, "order_date")
    account_no = reader.optional_text(raw, "account_no")
    notes = reader.optional_text(raw, "notes")
    ship_to_id = reader.optional_int(raw, "ship_to_id")
    bill_to_id = reader.optional_int(raw, "bill_to_id")
    if reader.issues:
        return UpdateCommandParseResult(command=None, issues=reader.issues)
    return UpdateCommandParseResult(command=UpdatePurchaseOrderCommandV1(
        account_no=account_no or "", order_date=order_date,
        ship_to_id=ship_to_id, bill_to_id=bill_to_id, notes=notes or ""), issues=[])

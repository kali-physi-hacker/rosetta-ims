"""DaysmartOrderItemV1 — one purchase-order LINE as Daysmart's item list
returns it (``GET /barramundi/inventory/order/item/list/{id}``).

Written against a real capture (order #524's four lines). Wire facts:

- ``id`` is the LINE id (``purchase_order_item_id`` on writes/receipts);
  ``item_id`` is the inventory item — a "DIT-…" STRING.
- ``price`` arrives as a FLOAT (their UI divides line_total by quantity
  and the repeating decimal lands in storage). It is kept VERBATIM as a
  string — never rounded, never re-derived. ``line_total`` likewise.
- The embedded ``item`` object carries the catalogue ``cost`` — a
  different number from the line price; read when present.
- Laravel paginator envelope: ``data`` / ``last_page`` / ``per_page`` /
  ``total``.
- Their own key typo ``puchase_order_receipt_items`` exists; extras are
  tolerated and a field map must never "correct" their spelling.

Same discipline as the sibling contracts: never raises; ``(item | None,
issues)``; identity/quantity/price problems block, wrong-shaped optional
fields are non-blocking and withheld.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, ClassVar

from pydantic import ValidationError as PydanticValidationError

from .base import ContractModel, register_contract
from .daysmart_order_v1 import _MONEY_RE
from .issues import IssueCode, PoValidationIssue


@register_contract
class DaysmartOrderItemV1(ContractModel):
    contract_id: ClassVar[str] = "purchase_orders.daysmart_order_item.v1"

    daysmart_line_id: int
    inventory_item_id: str
    purchase_order_id: int | None
    item_name: str
    quantity_ordered: int
    quantity_received: int
    back_ordered: int
    #: Verbatim wire numbers as strings — "2.5083333333333", "650".
    price_raw: str
    line_total_raw: str
    notes: str
    is_active: bool
    container_type: str | None
    catalogue_cost_raw: str | None
    created_at: datetime | None
    updated_at: datetime | None


@dataclass(frozen=True)
class DaysmartOrderItemParseResult:
    item: DaysmartOrderItemV1 | None
    issues: list[PoValidationIssue] = field(default_factory=list)


@dataclass(frozen=True)
class DaysmartOrderItemPageMeta:
    current_page: int
    last_page: int
    per_page: int
    total: int


@dataclass(frozen=True)
class DaysmartOrderItemPageParseResult:
    items: list[DaysmartOrderItemV1]
    issues: list[PoValidationIssue]
    meta: DaysmartOrderItemPageMeta


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _number_verbatim(value: Any) -> str | None:
    """A wire number (int/float/decimal-string) as the exact string it
    came as; None when it is not money."""
    if _is_int(value) or isinstance(value, float):
        return str(value)
    if isinstance(value, str) and _MONEY_RE.match(value.strip()):
        return value.strip()
    return None


class _Reader:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.issues: list[PoValidationIssue] = []
        raw_id = raw.get("id")
        self.line_id = raw_id if _is_int(raw_id) else None

    def _issue(self, code: IssueCode, path: str, value: Any, expected: str) -> None:
        self.issues.append(PoValidationIssue(
            code=code, field_path=path, raw_value=value,
            expected=expected, daysmart_id=self.line_id))

    def required_int(self, path: str) -> int | None:
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        value = self.raw[path]
        if not _is_int(value):
            self._issue(IssueCode.PO_FIELD_INVALID, path, value, "integer")
            return None
        return value

    def optional_int(self, path: str, default: int = 0) -> int:
        value = self.raw.get(path)
        if value is None:
            return default
        if not _is_int(value):
            self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, path, value, "integer or null")
            return default
        return value

    def dit(self, path: str) -> str | None:
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        value = self.raw[path]
        if not isinstance(value, str) or not value.startswith("DIT-"):
            self._issue(IssueCode.PO_FIELD_INVALID, path, value, 'inventory item id string ("DIT-…")')
            return None
        return value

    def money(self, path: str) -> str | None:
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        text = _number_verbatim(self.raw[path])
        if text is None:
            self._issue(IssueCode.PO_MONEY_UNPARSEABLE, path, self.raw[path],
                        "a wire number (int/float) or a plain decimal string")
        return text

    def text(self, path: str) -> str:
        value = self.raw.get(path)
        return value if isinstance(value, str) else ""

    def php_timestamp(self, path: str) -> datetime | None:
        value = self.raw.get(path)
        if value is None:
            return None
        if (isinstance(value, dict) and isinstance(value.get("date"), str)
                and value.get("timezone") == "UTC"):
            try:
                return datetime.fromisoformat(value["date"]).replace(tzinfo=timezone.utc)
            except ValueError:
                pass
        self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, path, value,
                    'object like {"date": "...", "timezone": "UTC"}')
        return None

    def catalogue_cost(self) -> str | None:
        embedded = self.raw.get("item")
        if not isinstance(embedded, dict) or embedded.get("cost") is None:
            return None
        return _number_verbatim(embedded.get("cost"))


def parse_daysmart_order_item(raw: dict[str, Any]) -> DaysmartOrderItemParseResult:
    """Parse one raw line row. Never raises on shape problems."""
    reader = _Reader(raw if isinstance(raw, dict) else {})
    line_id = reader.required_int("id")
    inventory_item_id = reader.dit("item_id")
    quantity_ordered = reader.required_int("quantity_ordered")
    price_raw = reader.money("price")
    line_total_raw = reader.money("line_total") if "line_total" in reader.raw else "0"

    if any(issue.blocking for issue in reader.issues):
        return DaysmartOrderItemParseResult(item=None, issues=reader.issues)

    try:
        item = DaysmartOrderItemV1(
            daysmart_line_id=line_id,
            inventory_item_id=inventory_item_id,
            purchase_order_id=(reader.raw.get("purchase_order_id")
                               if _is_int(reader.raw.get("purchase_order_id")) else None),
            item_name=reader.text("item_name"),
            quantity_ordered=quantity_ordered,
            quantity_received=reader.optional_int("quantity_received"),
            back_ordered=reader.optional_int("back_ordered"),
            price_raw=price_raw,
            line_total_raw=line_total_raw if line_total_raw is not None else "0",
            notes=reader.text("notes"),
            is_active=bool(reader.raw.get("is_active", True)),
            container_type=(reader.raw.get("container_type")
                            if isinstance(reader.raw.get("container_type"), str) else None),
            catalogue_cost_raw=reader.catalogue_cost(),
            created_at=reader.php_timestamp("create_at"),
            updated_at=reader.php_timestamp("update_at"),
        )
    except PydanticValidationError as exc:
        for error in exc.errors():
            reader._issue(IssueCode.PO_FIELD_INVALID,
                          ".".join(str(part) for part in error["loc"]) or "(line)",
                          error.get("input"), error["msg"])
        return DaysmartOrderItemParseResult(item=None, issues=reader.issues)
    return DaysmartOrderItemParseResult(item=item, issues=reader.issues)


def parse_daysmart_order_item_page(body: dict[str, Any]) -> DaysmartOrderItemPageParseResult:
    """Parse one page of the Laravel paginator envelope. Never raises."""
    empty_meta = DaysmartOrderItemPageMeta(current_page=1, last_page=1, per_page=50, total=0)
    resources: Any = None
    if isinstance(body, dict):
        response = body.get("response")
        if isinstance(response, dict):
            resources = response.get("resources")
    if not isinstance(resources, dict) or not isinstance(resources.get("data"), list):
        issue = PoValidationIssue(
            code=IssueCode.PO_FIELD_INVALID, field_path="response.resources.data",
            raw_value=None, expected="Laravel paginator envelope with a data array")
        return DaysmartOrderItemPageParseResult(items=[], issues=[issue], meta=empty_meta)

    def meta_int(key: str, default: int) -> int:
        value = resources.get(key)
        return value if _is_int(value) and value >= 0 else default

    meta = DaysmartOrderItemPageMeta(
        current_page=meta_int("current_page", 1), last_page=max(meta_int("last_page", 1), 1),
        per_page=meta_int("per_page", 50), total=meta_int("total", len(resources["data"])))
    items: list[DaysmartOrderItemV1] = []
    issues: list[PoValidationIssue] = []
    for raw in resources["data"]:
        result = parse_daysmart_order_item(raw)
        issues.extend(result.issues)
        if result.item is not None:
            items.append(result.item)
    return DaysmartOrderItemPageParseResult(items=items, issues=issues, meta=meta)

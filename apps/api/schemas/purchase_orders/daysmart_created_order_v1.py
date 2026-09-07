"""DaysmartCreatedOrderV1 — the create/detail response dialect.

Daysmart's write surface answers in a DIFFERENT dialect than its list
surface, discovered the day the real create response was captured
(fixture ``daysmart_order_create_response.json``):

- timestamps are PHP DateTime objects
  ``{"date": "2026-08-28 07:59:37.000000", "timezone_type": 3,
  "timezone": "UTC"}`` — in UTC, where the list speaks ISO ``+08:00``;
- ``status`` is a bare int, where the list sends ``{"id", "label"}``;
- empties are ``null``, where the list sends ``""``;
- supplier is a flat ``supplier_id`` string on create/update responses; the
  DETAIL surface (order/{id}) adds a ``supplier`` object and ``status_label``
  — read optionally, null where a surface does not carry them;
- the success message key is misspelled ``"sucess"`` (ignored here).

Parsing rules mirror the sibling contract: never raises; result is
``(order | None, issues)``; an UNKNOWN status is non-blocking because the
one job of this parse is to keep ``daysmart_id``/``index`` usable for
identity linking; missing identity or unreadable money/timestamps block.
A timezone other than UTC blocks rather than guesses — these timestamps
feed the snapshot-diff window, and a mis-stamped instant is worse there
than an absent one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, ClassVar

from pydantic import ValidationError as PydanticValidationError

from .base import ContractModel, register_contract
from .daysmart_order_v1 import _MONEY_RE, STATUS_VOCABULARY
from .issues import IssueCode, PoValidationIssue


@register_contract
class DaysmartCreatedOrderV1(ContractModel):
    """One order as the create/detail surface returns it."""

    contract_id: ClassVar[str] = "purchase_orders.daysmart_created_order.v1"

    daysmart_id: int
    index: int
    supplier_daysmart_id: str
    #: Only the DETAIL surface carries these (create/update answer without
    #: them). Optional reads: null means "not on this surface", and a
    #: malformed value is withheld with a non-blocking issue — identity
    #: never depends on them.
    supplier_name: str | None = None
    status_label: str | None = None
    account_no: str
    order_date: datetime
    ship_to_id: int | None
    bill_to_id: int | None
    #: None means Daysmart sent null — unknown, not zero.
    sub_total: Decimal | None
    shipping: Decimal | None
    tax: Decimal | None
    total: Decimal | None
    payment_terms: str
    notes: str
    #: Raw bare-int status, and the shared-vocabulary mapping. ``str`` on
    #: purpose — STATUS_VOCABULARY is the single vocabulary declaration
    #: (see the sibling contract's note on the H8 drift class).
    status: str | None
    status_id: int
    order_name: str
    is_active: bool
    business_id: str
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime


@dataclass(frozen=True)
class DaysmartCreatedOrderParseResult:
    order: DaysmartCreatedOrderV1 | None
    issues: list[PoValidationIssue] = field(default_factory=list)


_ABSENT = object()


class _Reader:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw
        self.issues: list[PoValidationIssue] = []
        raw_id = raw.get("id")
        self.daysmart_id = raw_id if _is_int(raw_id) else None

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

    def optional_integer(self, path: str) -> int | None:
        value = self.raw.get(path)
        if value is None:
            return None
        if not _is_int(value):
            # Non-blocking, same rule as the list parser: withhold the value,
            # record the drift, keep the order usable (identity must survive).
            self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, path, value,
                        "integer or null")
            return None
        return value

    def text(self, path: str) -> str:
        """This dialect uses null for empty text — normalize to ""."""
        value = self.raw.get(path)
        if value is None:
            return ""
        if not isinstance(value, str):
            self._issue(IssueCode.PO_FIELD_INVALID, path, value, "string or null")
            return ""
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

    def optional_text(self, path: str) -> str | None:
        """Text only SOME surfaces carry. Absent/null → None; a non-string is
        recorded (non-blocking) and withheld."""
        value = self.raw.get(path)
        if value is None:
            return None
        if not isinstance(value, str):
            self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, path, value, "string or null")
            return None
        return value

    def optional_supplier_name(self) -> str | None:
        """``supplier.name`` from the detail surface's embedded object."""
        supplier = self.raw.get("supplier")
        if supplier is None:
            return None
        if not isinstance(supplier, dict):
            self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, "supplier", supplier, "object or null")
            return None
        name = supplier.get("name")
        if name is None:
            return None
        if not isinstance(name, str):
            self._issue(IssueCode.PO_OPTIONAL_FIELD_INVALID, "supplier.name", name, "string or null")
            return None
        return name

    def money(self, path: str) -> Decimal | None:
        value = self.raw.get(path)
        if value is None:
            return None  # null = unknown, deliberately NOT zero
        if not isinstance(value, str) or not _MONEY_RE.match(value):
            self._issue(IssueCode.PO_MONEY_UNPARSEABLE, path, value,
                        'decimal string like "690.00", or null for unknown')
            return None
        return Decimal(value)

    def php_timestamp(self, path: str) -> datetime | None:
        """{"date": "YYYY-MM-DD HH:MM:SS.ffffff", "timezone": "UTC"}."""
        if path not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, path, None, "field present")
            return None
        value = self.raw[path]
        expected = 'object like {"date": "...", "timezone": "UTC"}'
        if not isinstance(value, dict) or not isinstance(value.get("date"), str):
            self._issue(IssueCode.PO_FIELD_INVALID, path, value, expected)
            return None
        if value.get("timezone") != "UTC":
            # Only UTC has ever been observed. Guessing another zone would
            # silently shift the snapshot-diff window — block instead.
            self._issue(IssueCode.PO_FIELD_INVALID, path, value,
                        'timezone "UTC" (the only observed value)')
            return None
        try:
            parsed = datetime.fromisoformat(value["date"])
        except ValueError:
            self._issue(IssueCode.PO_FIELD_INVALID, path, value, expected)
            return None
        return parsed.replace(tzinfo=timezone.utc)

    def bare_status(self) -> tuple[int | None, str | None]:
        if "status" not in self.raw:
            self._issue(IssueCode.PO_FIELD_MISSING, "status", None, "field present")
            return None, None
        value = self.raw["status"]
        if not _is_int(value):
            self._issue(IssueCode.PO_FIELD_INVALID, "status", value,
                        "bare integer status (this dialect)")
            return None, None
        known = STATUS_VOCABULARY.get(value)
        if known is None:
            # Non-blocking on purpose: an unclassified status must never
            # cost us the daysmart_id — linking is why this parse exists.
            self._issue(IssueCode.PO_STATUS_UNKNOWN, "status", value,
                        f"one of {sorted(STATUS_VOCABULARY)}")
            return value, None
        return value, known[0]


def parse_daysmart_created_order(body: dict[str, Any]) -> DaysmartCreatedOrderParseResult:
    """Parse a create/detail response envelope. Never raises on shape."""
    resources: Any = _ABSENT
    if isinstance(body, dict):
        response = body.get("response")
        if isinstance(response, dict):
            resources = response.get("resources", _ABSENT)
    if resources is _ABSENT or not isinstance(resources, dict):
        issue = PoValidationIssue(
            code=IssueCode.PO_FIELD_MISSING if resources is _ABSENT else IssueCode.PO_FIELD_INVALID,
            field_path="response.resources",
            raw_value=None if resources is _ABSENT else resources,
            expected="envelope with a resources object")
        return DaysmartCreatedOrderParseResult(order=None, issues=[issue])

    reader = _Reader(resources)
    daysmart_id = reader.integer("id")
    index = reader.integer("index")
    supplier_daysmart_id = reader.required_text("supplier_id")
    order_date = reader.php_timestamp("order_date")
    created_at = reader.php_timestamp("create_at")
    updated_at = reader.php_timestamp("update_at")
    sub_total = reader.money("sub_total")
    shipping = reader.money("shipping")
    tax = reader.money("tax")
    total = reader.money("total")
    status_id, status = reader.bare_status()

    if any(issue.blocking for issue in reader.issues):
        return DaysmartCreatedOrderParseResult(order=None, issues=reader.issues)

    try:
        order = DaysmartCreatedOrderV1(
            daysmart_id=daysmart_id,
            index=index,
            supplier_daysmart_id=supplier_daysmart_id,
            supplier_name=reader.optional_supplier_name(),
            status_label=reader.optional_text("status_label"),
            account_no=reader.text("account_no"),
            order_date=order_date,
            ship_to_id=reader.optional_integer("ship_to_id"),
            bill_to_id=reader.optional_integer("bill_to_id"),
            sub_total=sub_total,
            shipping=shipping,
            tax=tax,
            total=total,
            payment_terms=reader.text("payment_terms"),
            notes=reader.text("notes"),
            status_id=status_id,
            status=status,
            order_name=reader.text("order_name"),
            is_active=bool(resources.get("is_active", True)),
            business_id=reader.text("business_id"),
            created_by=reader.text("create_by"),
            created_at=created_at,
            updated_by=reader.text("update_by"),
            updated_at=updated_at,
        )
    except PydanticValidationError as exc:
        # Structural backstop, same as the sibling parser: a reader gap
        # becomes named issues, never an escaping exception.
        for error in exc.errors():
            reader._issue(
                IssueCode.PO_FIELD_INVALID,
                ".".join(str(part) for part in error["loc"]) or "(order)",
                error.get("input"),
                error["msg"])
        return DaysmartCreatedOrderParseResult(order=None, issues=reader.issues)
    return DaysmartCreatedOrderParseResult(order=order, issues=reader.issues)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)

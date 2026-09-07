

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from schemas.purchase_orders import (
    IssueCode,
    build_daysmart_create_payload,
    parse_create_command,
    parse_line_commands,
)

HKT = timezone(timedelta(hours=8))


def valid_raw() -> dict:
    return {
        "supplier_daysmart_id": "3cd1ace0-ddd6-4a",
        "supplier_name": "Alfamedic",
        "order_date": "2026-08-28T11:00:00+08:00",
        "account_no": "-",
        "notes": "11+1 for 50010333 - Douxo S3 Seb Shampoo 200ml",
    }


def valid_lines() -> list:
    return [
        {
            "description": "Douxo S3 Seb Shampoo 200ml",
            "quantity": 12,
            "quantity_uom": "BOTTLE",
            "unit_price": "96.00",
        },
        {
            "description": "Douxo S3 Seb Shampoo 200ml (bonus)",
            "quantity": 1,
            "quantity_uom": "BOTTLE",
            "unit_price": "0.00",  # free goods — legal
        },
    ]


def parse(raw):
    return parse_create_command(raw)


# ---------------------------------------------------------------------------
# C — the command contract
# ---------------------------------------------------------------------------


class TestCreateCommand:
    def test_c1_valid_command_parses(self):
        result = parse(valid_raw())
        assert result.issues == []
        command = result.command
        assert command is not None
        assert command.supplier_daysmart_id == "3cd1ace0-ddd6-4a"
        assert command.order_date.utcoffset() == timedelta(hours=8)
        # Optional addresses default to None — the mapper fills them.
        assert command.ship_to_id is None
        assert command.bill_to_id is None

    def test_c2_lines_are_refused_in_the_create(self):
        # Lines are a separate resource (POST /{po_uuid}/lines) with their
        # own outcomes — the header create does not carry them at all.
        raw = valid_raw()
        raw["lines"] = valid_lines()
        result = parse(raw)
        assert result.command is None
        assert [(i.field_path, i.expected) for i in result.issues] == [("lines", "no such field")]
        raw["lines"] = []
        assert parse(raw).command is None


class TestLineCommands:
    """The same rules, on the lines endpoint's own parser."""

    def test_c3_every_line_needs_a_uom(self):
        lines = valid_lines()
        del lines[1]["quantity_uom"]
        result = parse_line_commands(lines)
        assert result.lines is None
        assert len(result.issues) == 1
        assert result.issues[0].code == IssueCode.PO_FIELD_MISSING
        assert result.issues[0].field_path == "[1].quantity_uom"

    def test_c4_zero_quantity_rejected_zero_price_accepted(self):
        lines = valid_lines()
        lines[0]["quantity"] = 0
        result = parse_line_commands(lines)
        assert result.lines is None
        assert result.issues[0].code == IssueCode.PO_FIELD_INVALID
        assert result.issues[0].field_path == "[0].quantity"
        # Zero PRICE is business reality (buy-11-get-1).
        assert parse_line_commands(valid_lines()).lines[1].unit_price == Decimal("0.00")

    def test_c5_money_must_be_decimal_string_never_float(self):
        lines = valid_lines()
        lines[0]["unit_price"] = 96.5  # JSON number → float → refused
        result = parse_line_commands(lines)
        assert result.lines is None
        assert result.issues[0].code == IssueCode.PO_FIELD_INVALID
        assert result.issues[0].field_path == "[0].unit_price"

        lines = valid_lines()
        lines[0]["unit_price"] = "-5.00"  # negative money → refused
        result = parse_line_commands(lines)
        assert result.lines is None
        assert result.issues[0].field_path == "[0].unit_price"

    def test_c7_unknown_field_rejected_our_side_is_strict(self):
        raw = valid_raw()
        raw["surprise_field"] = "hello"
        result = parse(raw)
        assert result.command is None
        assert result.issues[0].code == IssueCode.PO_FIELD_INVALID
        assert result.issues[0].field_path == "surprise_field"

        lines = valid_lines()
        lines[0]["surprise"] = 1
        result = parse_line_commands(lines)
        assert result.lines is None
        assert result.issues[0].field_path == "[0].surprise"

    def test_c8_naive_order_date_rejected(self):
        raw = valid_raw()
        raw["order_date"] = "2026-08-28T11:00:00"  # no offset
        result = parse(raw)
        assert result.command is None
        assert result.issues[0].code == IssueCode.PO_FIELD_INVALID
        assert result.issues[0].field_path == "order_date"

    def test_c9_all_problems_reported_at_once(self):
        # A client fixing a bad request should learn everything in one 422.
        raw = valid_raw()
        raw["supplier_daysmart_id"] = ""
        raw["order_date"] = "2026-08-28T11:00:00"
        result = parse(raw)
        assert result.command is None
        assert {issue.field_path for issue in result.issues} == {"supplier_daysmart_id", "order_date"}
        lines = valid_lines()
        lines[0]["quantity"] = 0
        del lines[1]["quantity_uom"]
        result = parse_line_commands(lines)
        assert {issue.field_path for issue in result.issues} == {"[0].quantity", "[1].quantity_uom"}


# ---------------------------------------------------------------------------
# D — the outbound mapper (header payload)
# ---------------------------------------------------------------------------


class TestCreatePayloadMapper:
    def build(self, command):
        return build_daysmart_create_payload(
            command, default_ship_to_id=861040, default_bill_to_id=861040)

    def test_d1_payload_matches_the_har_proven_shape_exactly(self):
        command = parse(valid_raw()).command
        payload = self.build(command)
        # Exactly the spike's CreateOrderRequest keys — nothing more or less.
        assert set(payload) == {
            "supplier_id", "supplier_name", "account_no", "order_date",
            "ship_to_id", "bill_to_id", "notes", "template_id",
        }
        assert payload["supplier_id"] == "3cd1ace0-ddd6-4a"
        assert payload["supplier_name"] == "Alfamedic"
        assert isinstance(payload["order_date"], int)   # epoch, not ISO
        assert isinstance(payload["ship_to_id"], int)
        assert isinstance(payload["bill_to_id"], int)
        assert payload["notes"] == valid_raw()["notes"]
        assert payload["template_id"] is None

    def test_d2_order_date_epoch_round_trips_to_the_same_instant(self):
        command = parse(valid_raw()).command
        payload = self.build(command)
        recovered = datetime.fromtimestamp(payload["order_date"], tz=HKT)
        assert recovered == command.order_date

    def test_d3_address_defaults_applied_only_when_omitted(self):
        command = parse(valid_raw()).command
        payload = self.build(command)
        assert payload["ship_to_id"] == 861040
        assert payload["bill_to_id"] == 861040

        raw = valid_raw()
        raw["ship_to_id"] = 999999
        command = parse(raw).command
        payload = self.build(command)
        assert payload["ship_to_id"] == 999999   # explicit wins
        assert payload["bill_to_id"] == 861040   # default fills the gap

    def test_d4_lines_never_leak_into_the_header_payload(self):
        # Create is header-then-lines; the header call carries NO line data.
        command = parse(valid_raw()).command
        payload = self.build(command)
        assert "lines" not in payload
        assert "items" not in payload

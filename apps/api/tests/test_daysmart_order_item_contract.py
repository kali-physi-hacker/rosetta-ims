"""OL-group — the order LINE-row read contract (DaysmartOrderItemV1).

The long-deferred B-group, finally written against a real capture:
``daysmart_order_item_list_793009.json`` — the four lines of test order #524.

Wire facts pinned:
- ``id`` is the Daysmart LINE id (int) — ``purchase_order_item_id`` on
  later writes and receipts.
- ``item_id`` is the inventory item, a "DIT-…" STRING.
- ``price`` arrives as a FLOAT (2.5083333333333 — their UI's line_total /
  quantity division lands in storage). We store it VERBATIM as a string;
  we never round, never re-divide.
- ``line_total`` is a bare number; the embedded ``item`` object carries the
  catalogue ``cost`` (2.51) — different from the line price.
- Envelope is a Laravel paginator: ``data`` / ``last_page`` / ``per_page``
  / ``total``.
- Their own key typo ``puchase_order_receipt_items`` is tolerated (never
  "corrected" in a field map).

Same discipline as the sibling contracts: never raises; ``(item | None,
issues)``; identity problems block; unknown extras are tolerated.

Interface under test (``schemas/purchase_orders/daysmart_order_item_v1.py``):

    parse_daysmart_order_item(raw: dict)       -> .item, .issues
    parse_daysmart_order_item_page(body: dict) -> .items, .issues, .meta
"""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from schemas.purchase_orders import (
    IssueCode,
    parse_daysmart_order_item,
    parse_daysmart_order_item_page,
)

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"


@pytest.fixture(scope="module")
def page() -> dict:
    return json.loads(
        (FIXTURES / "daysmart_order_item_list_793009.json").read_text(encoding="utf-8"))


def row(page, line_id: int) -> dict:
    for raw in page["response"]["resources"]["data"]:
        if raw["id"] == line_id:
            return copy.deepcopy(raw)
    raise AssertionError(f"line {line_id} not in fixture")


class TestHappyPath:
    def test_ol1_all_four_fixture_lines_parse_without_issues(self, page):
        result = parse_daysmart_order_item_page(page)
        assert len(result.items) == 4
        assert result.issues == []
        assert result.meta.total == 4
        assert result.meta.last_page == 1
        assert result.meta.per_page == 50

    def test_ol2_identities_are_typed_correctly(self, page):
        item = parse_daysmart_order_item(row(page, 3969381)).item
        assert item is not None
        assert item.daysmart_line_id == 3969381
        assert item.inventory_item_id == "DIT-ebbd2f4c-6e6"
        assert item.purchase_order_id == 793009
        assert item.item_name == "50010313 - Cephalexin (Cephacin) Tablet 200mg"
        assert item.quantity_ordered == 240
        assert item.quantity_received == 0
        assert item.back_ordered == 0
        assert item.is_active is True
        assert item.container_type == "Tablet(s)"

    def test_ol3_float_price_is_kept_verbatim_never_rounded(self, page):
        item = parse_daysmart_order_item(row(page, 3969381)).item
        assert item is not None
        # The wire float, repeating decimals and all — stored as it came.
        assert item.price_raw == "2.5083333333333"
        assert item.line_total_raw == "602"
        # Convertible when arithmetic is needed, exact to what was sent.
        assert Decimal(item.price_raw) == Decimal("2.5083333333333")

    def test_ol4_clean_prices_stay_clean(self, page):
        item = parse_daysmart_order_item(row(page, 3969384)).item
        assert item is not None
        assert item.price_raw == "650"
        assert item.line_total_raw == "1300"

    def test_ol5_catalogue_cost_read_from_embedded_item(self, page):
        item = parse_daysmart_order_item(row(page, 3969381)).item
        assert item is not None
        assert item.catalogue_cost_raw == "2.51"   # item.cost ≠ line price

    def test_ol6_timestamps_are_utc_aware(self, page):
        item = parse_daysmart_order_item(row(page, 3969381)).item
        assert item is not None
        assert item.created_at is not None
        assert item.created_at.utcoffset() is not None
        assert item.updated_at is not None


class TestTolerance:
    def test_ol7_extra_and_typo_keys_are_tolerated(self, page):
        raw = row(page, 3969381)
        assert "puchase_order_receipt_items" in raw     # their typo, on the wire
        raw["brand_new_daysmart_field"] = [1, 2, 3]
        result = parse_daysmart_order_item(raw)
        assert result.item is not None
        assert result.issues == []

    def test_ol8_null_notes_become_empty_string(self, page):
        raw = row(page, 3969381)
        raw["notes"] = None
        item = parse_daysmart_order_item(raw).item
        assert item is not None
        assert item.notes == ""

    def test_ol9_missing_embedded_item_means_no_catalogue_cost(self, page):
        raw = row(page, 3969381)
        del raw["item"]
        result = parse_daysmart_order_item(raw)
        assert result.item is not None
        assert result.item.catalogue_cost_raw is None
        assert result.issues == []


class TestBlockingAndNeverRaise:
    @pytest.mark.parametrize("missing", ["id", "item_id", "quantity_ordered", "price"])
    def test_ol10_missing_essential_field_blocks(self, page, missing):
        raw = row(page, 3969381)
        del raw[missing]
        result = parse_daysmart_order_item(raw)
        assert result.item is None
        assert any(i.code == IssueCode.PO_FIELD_MISSING and i.field_path == missing
                   for i in result.issues)

    def test_ol11_non_dit_inventory_id_blocks(self, page):
        raw = row(page, 3969381)
        raw["item_id"] = 424242
        result = parse_daysmart_order_item(raw)
        assert result.item is None
        assert any(i.field_path == "item_id" for i in result.issues)

    def test_ol12_one_bad_row_does_not_poison_the_page(self, page):
        body = copy.deepcopy(page)
        body["response"]["resources"]["data"][1]["price"] = "not-money"
        result = parse_daysmart_order_item_page(body)
        assert len(result.items) == 3
        assert len(result.issues) == 1
        assert result.issues[0].code == IssueCode.PO_MONEY_UNPARSEABLE

    def test_ol13_shape_problems_never_raise(self, page):
        base = row(page, 3969381)
        mangles = [
            {},
            {**copy.deepcopy(base), "id": "seven"},
            {**copy.deepcopy(base), "quantity_ordered": "lots"},
            {**copy.deepcopy(base), "price": None},
            {**copy.deepcopy(base), "create_at": "yesterday"},
        ]
        for raw in mangles:
            result = parse_daysmart_order_item(raw)   # must not raise
            assert result.issues or result.item is not None

    def test_ol14_page_without_paginator_envelope_yields_issue_not_exception(self):
        result = parse_daysmart_order_item_page({"response": {"resources": "nope"}})
        assert result.items == []
        assert result.issues

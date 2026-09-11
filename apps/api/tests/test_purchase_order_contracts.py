"""DEV-305 — the Daysmart purchase-order READ contract (DaysmartOrderV1).

These tests define the contract before it exists (TDD). They pin the parsing
rules discovered in the real page-1 order-list capture (the golden fixture):

- ``""`` in a money field means UNKNOWN, never zero (order 791640 has
  sub_total "" next to total "0.00" — the two must stay distinguishable).
- Status is a 2-of-N sample: only {1: Open, 4: Closed} have ever been
  observed. An unseen id is a named issue, not a silent default.
- ``supplier.encoded_id`` is a per-response signed token, not an identifier —
  Alfamedic appears 5x in the fixture with 5 different values. The contract
  must not carry it.
- ``index`` is provably non-contiguous (507, 485, 475 are absent) — nothing
  may assert contiguity or derive "next number" from it.
- One bad order must never poison a page: shape problems become named
  validation issues carried in the result, and parsing NEVER raises.

Interface under test (to be implemented in ``schemas/purchase_orders/``):

    parse_daysmart_order(raw: dict)      -> result with .order, .issues
    parse_daysmart_order_page(raw: dict) -> result with .orders, .issues, .meta

Blocking problems (order is not trusted, ``.order is None``): unparseable
money, missing required field. Non-blocking (order returned WITH issues):
unknown status id (mapped status is None, raw id/label preserved), known id
with an unexpected label (drift early-warning).
"""

from __future__ import annotations

import copy
import json
from datetime import timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from schemas.purchase_orders import (
    IssueCode,
    parse_daysmart_order,
    parse_daysmart_order_page,
)

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
HKT = timezone(timedelta(hours=8))


@pytest.fixture(scope="module")
def page1() -> dict:
    return json.loads((FIXTURES / "daysmart_order_list_page1.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def orders_raw(page1) -> list[dict]:
    return page1["response"]["resources"]


def order_raw(orders_raw, daysmart_id: int) -> dict:
    """A deep copy of one fixture order, safe to mutate in a test."""
    for raw in orders_raw:
        if raw["id"] == daysmart_id:
            return copy.deepcopy(raw)
    raise AssertionError(f"order {daysmart_id} not in fixture")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:
    def test_a1_all_50_fixture_orders_parse_without_issues(self, page1):
        result = parse_daysmart_order_page(page1)
        assert len(result.orders) == 50
        assert result.issues == []
        assert all(order is not None for order in result.orders)

    def test_a2_parsed_order_exposes_typed_values(self, orders_raw):
        result = parse_daysmart_order(order_raw(orders_raw, 789502))
        order = result.order
        assert order is not None
        assert order.daysmart_id == 789502
        assert order.index == 518
        assert order.total == Decimal("690.00")
        assert order.order_date.tzinfo is not None
        assert order.order_date.utcoffset() == timedelta(hours=8)
        assert order.updated_at.utcoffset() == timedelta(hours=8)
        assert order.item_count == 1
        assert order.notes == "$148.00/box; $138.00/box for >=5boxes"


# ---------------------------------------------------------------------------
# Money: "" is unknown, "0.00" is zero, floats never appear
# ---------------------------------------------------------------------------


class TestMoney:
    def test_a3_empty_string_money_is_none_not_zero(self, orders_raw):
        # Order 791640: sub_total "", shipping "", tax "", total "0.00".
        result = parse_daysmart_order(order_raw(orders_raw, 791640))
        order = result.order
        assert order is not None
        assert result.issues == []
        assert order.sub_total is None
        assert order.shipping is None
        assert order.tax is None
        assert order.total == Decimal("0.00")

    def test_a4_empty_and_zero_stay_distinguishable(self, orders_raw):
        # Order 789497: shipping "" alongside tax "0.00" on the same order.
        result = parse_daysmart_order(order_raw(orders_raw, 789497))
        order = result.order
        assert order is not None
        assert order.shipping is None
        assert order.tax == Decimal("0.00")
        assert order.shipping != order.tax

    def test_a5_decimal_exactness_no_float_drift(self, orders_raw):
        result = parse_daysmart_order(order_raw(orders_raw, 787439))
        order = result.order
        assert order is not None
        assert isinstance(order.sub_total, Decimal)
        assert order.sub_total == Decimal("2959.20")
        # Exactness a float round-trip would break:
        assert str(order.sub_total) == "2959.20"

    @pytest.mark.parametrize("bad", ["abc", "1,234.00", "12.34.56"])
    def test_a6_unparseable_money_is_a_blocking_named_issue(self, orders_raw, bad):
        raw = order_raw(orders_raw, 789502)
        raw["sub_total"] = bad
        result = parse_daysmart_order(raw)
        assert result.order is None
        assert len(result.issues) == 1
        issue = result.issues[0]
        assert issue.code == IssueCode.PO_MONEY_UNPARSEABLE
        assert issue.field_path == "sub_total"
        assert issue.raw_value == bad
        assert issue.daysmart_id == 789502


# ---------------------------------------------------------------------------
# Status vocabulary: {1: Open, 4: Closed} is the entire observed world
# ---------------------------------------------------------------------------


class TestStatus:
    def test_a7_known_statuses_map_to_our_vocabulary(self, orders_raw):
        open_order = parse_daysmart_order(order_raw(orders_raw, 791640)).order
        closed_order = parse_daysmart_order(order_raw(orders_raw, 787439)).order
        assert open_order is not None and closed_order is not None
        assert open_order.status == "open"
        assert open_order.status_id == 1
        assert open_order.status_label == "Open"
        assert closed_order.status == "closed"
        assert closed_order.status_id == 4
        assert closed_order.status_label == "Closed"

    def test_a8_unknown_status_id_is_an_issue_with_raw_preserved(self, orders_raw):
        raw = order_raw(orders_raw, 789502)
        raw["status"] = {"id": 3, "label": "Partially Received"}
        result = parse_daysmart_order(raw)
        # Non-blocking: the order is still usable (e.g. for linking by id) …
        order = result.order
        assert order is not None
        # … but the mapped status is explicitly unknown, raw values kept.
        assert order.status is None
        assert order.status_id == 3
        assert order.status_label == "Partially Received"
        codes = [issue.code for issue in result.issues]
        assert codes == [IssueCode.PO_STATUS_UNKNOWN]
        assert result.issues[0].raw_value == 3

    def test_a9_known_id_with_drifted_label_is_flagged(self, orders_raw):
        raw = order_raw(orders_raw, 789502)
        raw["status"] = {"id": 1, "label": "Pending"}
        result = parse_daysmart_order(raw)
        order = result.order
        assert order is not None
        # The id still maps — drift on the label is a warning, not a block.
        assert order.status == "open"
        codes = [issue.code for issue in result.issues]
        assert codes == [IssueCode.PO_STATUS_LABEL_DRIFT]


# ---------------------------------------------------------------------------
# Identity: supplier.id is real, encoded_id is noise, index has gaps
# ---------------------------------------------------------------------------


class TestIdentity:
    def test_a10_supplier_identity_ignores_encoded_id(self, orders_raw):
        # Alfamedic in two different orders carries two different encoded_ids.
        first = parse_daysmart_order(order_raw(orders_raw, 791640)).order
        second = parse_daysmart_order(order_raw(orders_raw, 789497)).order
        assert first is not None and second is not None
        assert first.supplier.daysmart_id == "3cd1ace0-ddd6-4a"
        assert first.supplier == second.supplier
        assert not hasattr(first.supplier, "encoded_id")

    def test_a11_index_gaps_are_legal(self, page1):
        result = parse_daysmart_order_page(page1)
        indexes = {order.index for order in result.orders}
        # The fixture provably skips these — parsing must not care.
        assert 519 in indexes and 467 in indexes
        assert {507, 485, 475}.isdisjoint(indexes)
        assert result.issues == []

    def test_a12_truncated_uuids_are_opaque_strings(self, orders_raw):
        order = parse_daysmart_order(order_raw(orders_raw, 791640)).order
        assert order is not None
        # 16-char truncated values — accepted verbatim, no UUID validation.
        assert order.business_id == "9be52b5b-092d-45"
        assert order.created_by == "385eaec8-2ba3-4e"


# ---------------------------------------------------------------------------
# Structure: tolerate Daysmart additions, refuse silent absences
# ---------------------------------------------------------------------------


class TestStructure:
    def test_a13_unknown_extra_field_is_tolerated(self, orders_raw):
        raw = order_raw(orders_raw, 789502)
        raw["brand_new_daysmart_field"] = {"anything": [1, 2, 3]}
        result = parse_daysmart_order(raw)
        assert result.order is not None
        assert result.issues == []

    @pytest.mark.parametrize("missing", ["id", "total", "status", "supplier", "update_at"])
    def test_a14_missing_required_field_is_a_blocking_named_issue(self, orders_raw, missing):
        raw = order_raw(orders_raw, 789502)
        del raw[missing]
        result = parse_daysmart_order(raw)
        assert result.order is None
        assert len(result.issues) == 1
        issue = result.issues[0]
        assert issue.code == IssueCode.PO_FIELD_MISSING
        assert issue.field_path == missing

    def test_a15_one_bad_order_does_not_poison_the_page(self, page1):
        mangled = copy.deepcopy(page1)
        mangled["response"]["resources"][3]["total"] = "not-money"
        result = parse_daysmart_order_page(mangled)
        assert len(result.orders) == 49
        assert len(result.issues) == 1
        assert result.issues[0].code == IssueCode.PO_MONEY_UNPARSEABLE
        assert result.issues[0].daysmart_id == mangled["response"]["resources"][3]["id"]

    def test_a16_pagination_meta_parses_and_tolerates_known_lies(self, page1):
        result = parse_daysmart_order_page(page1)
        meta = result.meta
        assert meta.total == 468
        assert meta.per_page == 50
        assert meta.current_page == 1
        assert meta.last_page == 10
        # Known lies in the real capture, tolerated without issues:
        # meta.count is 0 while 50 resources are present, and prevPageUrl
        # points at page 1 while ON page 1. Neither may fail parsing.

    def test_h3_shape_problems_never_raise(self, orders_raw):
        """The only failure channel is (order=None, issues) — no exceptions."""
        base = order_raw(orders_raw, 789502)
        mangles = [
            {},  # everything missing
            {**copy.deepcopy(base), "status": "Open"},  # wrong type entirely
            {**copy.deepcopy(base), "supplier": None},
            {**copy.deepcopy(base), "order_date": "yesterday"},
            {**copy.deepcopy(base), "id": "seven"},
            {**copy.deepcopy(base), "item_count": None},
        ]
        for raw in mangles:
            result = parse_daysmart_order(raw)  # must not raise
            assert result.order is None
            assert result.issues, f"expected at least one named issue for {raw.keys()}"

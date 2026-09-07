"""CR-group — the create-response contract (DaysmartCreatedOrderV1).

TDD: defines the parser for the SECOND Daysmart dialect, discovered when the
real create response was captured (orders-details.har → fixtures). The
create/detail surface differs from the list surface in every way that
matters, and each difference below is pinned by a fixture fact:

- timestamps are PHP objects {"date": "2026-08-28 07:59:37.000000",
  "timezone_type": 3, "timezone": "UTC"} — in UTC, not ISO +08:00 strings;
- ``status`` is a bare int (1), not {"id": 1, "label": "Open"};
- empties are null, not "" — null money means unknown, never zero;
- supplier is a flat ``supplier_id`` string, no supplier object;
- the success message key is misspelled "sucess" (kept in the fixture as a
  monument; the parser must not care).

Same result discipline as every other contract: parsing NEVER raises;
``(order | None, issues)``; unknown status is non-blocking (the id must
stay usable for identity linking — that is the whole point of parsing a
create response); missing identity is blocking.

Interface under test (to be implemented in
``schemas/purchase_orders/daysmart_created_order_v1.py``):

    parse_daysmart_created_order(body: dict) -> result with .order, .issues
    # ``body`` is the FULL response envelope {"response": {"resources": ...}}
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from schemas.purchase_orders import (
    IssueCode,
    build_daysmart_create_payload,
    parse_create_command,
    parse_daysmart_created_order,
)

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"


@pytest.fixture(scope="module")
def create_response() -> dict:
    return json.loads(
        (FIXTURES / "daysmart_order_create_response.json").read_text(encoding="utf-8"))


def response_copy(create_response) -> dict:
    return copy.deepcopy(create_response)


class TestHappyPath:
    def test_cr1_fixture_response_parses_without_issues(self, create_response):
        result = parse_daysmart_created_order(response_copy(create_response))
        assert result.issues == []
        order = result.order
        assert order is not None
        assert order.daysmart_id == 793009
        assert order.index == 524
        assert order.supplier_daysmart_id == "3cd1ace0-ddd6-4a"

    def test_cr2_php_dates_become_utc_aware_datetimes(self, create_response):
        order = parse_daysmart_created_order(response_copy(create_response)).order
        assert order is not None
        assert order.order_date == datetime(2026, 8, 28, 7, 59, 37, tzinfo=timezone.utc)
        assert order.created_at == datetime(2026, 8, 28, 8, 0, 14, tzinfo=timezone.utc)
        assert order.created_at.utcoffset() is not None  # aware, always

    def test_cr3_null_money_is_none_zero_string_is_zero(self, create_response):
        # Fixture: sub_total/shipping/tax are null, total is "0.00".
        order = parse_daysmart_created_order(response_copy(create_response)).order
        assert order is not None
        assert order.sub_total is None
        assert order.shipping is None
        assert order.tax is None
        assert order.total == Decimal("0.00")

    def test_cr4_null_text_fields_become_empty_strings(self, create_response):
        # Fixture: account_no/notes/order_name are null in this dialect.
        order = parse_daysmart_created_order(response_copy(create_response)).order
        assert order is not None
        assert order.account_no == ""
        assert order.notes == ""

    def test_cr5_bare_int_status_maps_through_the_shared_vocabulary(self, create_response):
        order = parse_daysmart_created_order(response_copy(create_response)).order
        assert order is not None
        assert order.status_id == 1
        assert order.status == "open"


class TestStatusAndDrift:
    def test_cr6_unknown_bare_status_is_non_blocking_id_stays_usable(self, create_response):
        # THE critical property: an unclassified status must never cost us
        # the daysmart_id — identity linking is why we parse this at all.
        body = response_copy(create_response)
        body["response"]["resources"]["status"] = 3
        result = parse_daysmart_created_order(body)
        order = result.order
        assert order is not None
        assert order.daysmart_id == 793009
        assert order.status is None
        assert order.status_id == 3
        assert [i.code for i in result.issues] == [IssueCode.PO_STATUS_UNKNOWN]

    def test_cr7_unknown_extra_fields_are_tolerated(self, create_response):
        body = response_copy(create_response)
        body["response"]["resources"]["brand_new_field"] = {"x": 1}
        result = parse_daysmart_created_order(body)
        assert result.order is not None
        assert result.issues == []

    def test_cr8_unexpected_timezone_is_a_blocking_issue_not_a_guess(self, create_response):
        # Only UTC has been observed; a named zone we don't handle must not
        # be silently mis-stamped — timestamps feed the snapshot-diff window.
        body = response_copy(create_response)
        body["response"]["resources"]["create_at"]["timezone"] = "Asia/Hong_Kong"
        result = parse_daysmart_created_order(body)
        assert result.order is None
        assert any(i.code == IssueCode.PO_FIELD_INVALID and i.field_path == "create_at"
                   for i in result.issues)


class TestBlockingAndNeverRaise:
    @pytest.mark.parametrize("missing", ["id", "index"])
    def test_cr9_missing_identity_is_blocking(self, create_response, missing):
        body = response_copy(create_response)
        del body["response"]["resources"][missing]
        result = parse_daysmart_created_order(body)
        assert result.order is None
        assert any(i.code == IssueCode.PO_FIELD_MISSING and i.field_path == missing
                   for i in result.issues)

    def test_cr10_unparseable_money_is_blocking(self, create_response):
        body = response_copy(create_response)
        body["response"]["resources"]["total"] = "abc"
        result = parse_daysmart_created_order(body)
        assert result.order is None
        assert result.issues[0].code == IssueCode.PO_MONEY_UNPARSEABLE
        assert result.issues[0].field_path == "total"

    def test_cr11_shape_problems_never_raise(self, create_response):
        mangles = [
            {},                                           # no envelope at all
            {"response": {}},                             # no resources
            {"response": {"resources": None}},
            {"response": {"resources": "not a dict"}},
            {"response": {"resources": {"id": "seven"}}},  # wrong types
        ]
        base = response_copy(create_response)
        base["response"]["resources"]["order_date"] = "yesterday"  # not a PHP dict
        mangles.append(base)
        for body in mangles:
            result = parse_daysmart_created_order(body)  # must not raise
            assert result.order is None
            assert result.issues, f"expected issues for {body!r:.60}"


class TestMapperEmptyNotes:
    def test_m1_empty_notes_map_to_null_as_the_wire_showed(self):
        # The captured working create request sent notes: null, not "".
        raw = {
            "supplier_daysmart_id": "3cd1ace0-ddd6-4a",
            "supplier_name": "Alfamedic",
            "order_date": "2026-08-28T11:00:00+08:00",
        }
        command = parse_create_command(raw).command
        assert command is not None
        payload = build_daysmart_create_payload(
            command, default_ship_to_id=861040, default_bill_to_id=861040)
        assert payload["notes"] is None
        assert payload["account_no"] == ""  # the wire sent "" for account_no


# ---------------------------------------------------------------------------
# CR12–14 — what only the DETAIL surface (order/{id}) carries
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def detail_response() -> dict:
    return json.loads(
        (FIXTURES / "daysmart_order_detail_793009.json").read_text(encoding="utf-8"))


class TestDetailSurfaceExtras:
    def test_cr12_detail_carries_supplier_name_and_status_label(self, detail_response):
        result = parse_daysmart_created_order(copy.deepcopy(detail_response))
        assert result.issues == []
        assert result.order.supplier_name == "Alfamedic"
        assert result.order.status_label == "Open"

    def test_cr13_create_response_has_neither_and_that_is_not_an_issue(self, create_response):
        result = parse_daysmart_created_order(copy.deepcopy(create_response))
        assert result.issues == []
        assert result.order.supplier_name is None
        assert result.order.status_label is None

    def test_cr14_malformed_extras_are_withheld_non_blocking(self, detail_response):
        body = copy.deepcopy(detail_response)
        body["response"]["resources"]["status_label"] = 5
        body["response"]["resources"]["supplier"] = {"name": ["not", "text"]}
        result = parse_daysmart_created_order(body)
        assert result.order is not None                      # identity survives
        assert result.order.status_label is None
        assert result.order.supplier_name is None
        assert {i.code for i in result.issues} == {IssueCode.PO_OPTIONAL_FIELD_INVALID}
        assert {i.field_path for i in result.issues} == {"status_label", "supplier.name"}

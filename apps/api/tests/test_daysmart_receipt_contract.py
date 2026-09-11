"""RC-group — the receipt contract (DaysmartReceiptV1 / DaysmartReceiptRowV1).

Receipts speak a MIXED dialect inside one payload and have their OWN
status vocabulary. Every rule below is pinned by a fixture fact:

- ``date`` / ``post_at`` are PHP-UTC objects; ``create_at`` / ``update_at``
  are ISO ``+08:00`` strings — both become aware datetimes;
- ``status`` is ``{"id": 1, "label": "Posted"}`` on the list and detail,
  a bare int on echoes; 1 = posted, 0 = in process (label unobserved →
  never a drift issue), anything else unknown but NON-blocking;
- money is a string ("3850.00"), "" (unknown), null (unknown), or a bare
  number (the create echo's ``amount: 1``) — unparseable text BLOCKS;
- ``index`` is an int on the list and "Receipt #493" on the detail;
- rows come from the DETAIL's ``receipt_items``; a row's identity fields
  (``id``, ``purchase_order_item_id``, ``purchase_order_id``,
  ``purchase_order_receipt_id``, ``item_id``) block the ROW when malformed,
  everything else is withheld with a non-blocking issue.

Interface under test:

    schemas/purchase_orders/daysmart_receipt_v1.py
        parse_daysmart_receipt(raw)        -> (.receipt | None, .issues)
        parse_daysmart_receipt_page(body)  -> (.receipts, .issues, .meta)
        parse_daysmart_receipt_rows(body)  -> (.rows, .issues, .receipt_daysmart_id)
        RECEIPT_STATUS_VOCABULARY, ROW_IDENTITY_PATHS
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from schemas.purchase_orders import (
    RECEIPT_STATUS_VOCABULARY,
    IssueCode,
    parse_daysmart_receipt,
    parse_daysmart_receipt_detail,
    parse_daysmart_receipt_page,
    parse_daysmart_receipt_rows,
)

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
LIST_RAW = json.loads((FIXTURES / "daysmart_receipt_list_page1.json").read_text(encoding="utf-8"))
DETAIL_RAW = json.loads((FIXTURES / "daysmart_receipt_detail_822801.json").read_text(encoding="utf-8"))
UTC = timezone.utc


def row0():
    return copy.deepcopy(LIST_RAW["response"]["resources"][0])


class TestReceiptHeader:
    def test_rc1_fixture_page_parses_clean(self):
        page = parse_daysmart_receipt_page(copy.deepcopy(LIST_RAW))
        assert page.issues == []
        assert len(page.receipts) == 50
        assert (page.meta.total, page.meta.per_page, page.meta.last_page) == (467, 50, 10)
        r = page.receipts[0]
        assert (r.daysmart_id, r.index, r.name) == (822801, 493, "Receipt #493")
        assert (r.status_id, r.status_label, r.status) == (1, "Posted", "posted")
        assert r.purchase_order_id == 785384
        assert r.supplier.name == "Queen's Pharma Limited"
        assert r.supplier_daysmart_id == "c0e4123c-ae98-48"
        assert r.amount == Decimal("3850.00") and r.tax == Decimal("0.00")
        assert r.shipping is None and r.tax_percentage is None      # "" = unknown, never zero
        assert r.invoice_number == "888046260817"
        assert (r.item_count, r.is_with_order, r.is_active) == (2, 1, True)

    def test_rc2_both_date_dialects_become_aware_datetimes(self):
        r = parse_daysmart_receipt(row0()).receipt
        assert r.receipt_date == datetime(2026, 8, 16, 16, 0, tzinfo=UTC)          # PHP object
        assert r.post_at == datetime(2026, 8, 23, 4, 38, 28, tzinfo=UTC)           # PHP object
        assert r.created_at == datetime.fromisoformat("2026-08-23T12:37:59+08:00")  # ISO string
        assert r.created_at.astimezone(UTC) == datetime(2026, 8, 23, 4, 37, 59, tzinfo=UTC)

    def test_rc3_own_status_vocabulary_never_the_order_map(self):
        assert RECEIPT_STATUS_VOCABULARY[1][0] == "posted"       # NOT "open"
        bare = row0(); bare["status"] = 0
        result = parse_daysmart_receipt(bare)
        assert result.issues == [] and result.receipt.status == "in_process"
        labelled = row0(); labelled["status"] = {"id": 0, "label": "In Process"}   # wire, 2026-09-02
        result = parse_daysmart_receipt(labelled)
        assert result.issues == []
        assert result.receipt.status == "in_process" and result.receipt.status_label == "In Process"
        renamed = row0(); renamed["status"] = {"id": 0, "label": "Draft"}
        result = parse_daysmart_receipt(renamed)
        assert result.receipt.status == "in_process"
        assert [i.code for i in result.issues] == [IssueCode.PO_STATUS_LABEL_DRIFT]
        drift = row0(); drift["status"] = {"id": 1, "label": "Complete"}
        result = parse_daysmart_receipt(drift)
        assert result.receipt.status == "posted"
        assert [i.code for i in result.issues] == [IssueCode.PO_STATUS_LABEL_DRIFT]
        unknown = row0(); unknown["status"] = {"id": 7, "label": "Void"}
        result = parse_daysmart_receipt(unknown)
        assert result.receipt is not None and result.receipt.status is None
        assert [(i.code, i.blocking) for i in result.issues] == [(IssueCode.PO_STATUS_UNKNOWN, False)]

    def test_rc4_money_accepts_strings_numbers_and_unknown_but_not_text(self):
        raw = row0(); raw["amount"] = 1; raw["tax"] = ""; raw["shipping"] = None
        r = parse_daysmart_receipt(raw).receipt
        assert (r.amount, r.tax, r.shipping) == (Decimal("1"), None, None)
        raw = row0(); raw["amount"] = "three thousand"
        result = parse_daysmart_receipt(raw)
        assert result.receipt is None
        assert [(i.code, i.field_path) for i in result.issues] == [(IssueCode.PO_MONEY_UNPARSEABLE, "amount")]

    def test_rc5_detail_style_index_is_read_too(self):
        raw = row0(); raw["index"] = "Receipt #493"
        assert parse_daysmart_receipt(raw).receipt.index == 493
        raw["index"] = "weird"
        result = parse_daysmart_receipt(raw)
        assert result.receipt.index is None
        assert [(i.code, i.field_path) for i in result.issues] == [(IssueCode.PO_OPTIONAL_FIELD_INVALID, "index")]

    def test_rc6_identity_and_unguessable_dates_block(self):
        raw = row0(); del raw["id"]
        assert parse_daysmart_receipt(raw).receipt is None
        raw = row0(); del raw["status"]
        result = parse_daysmart_receipt(raw)
        assert result.receipt is None and result.issues[0].field_path == "status"
        raw = row0(); raw["date"]["timezone"] = "Asia/Hong_Kong"
        result = parse_daysmart_receipt(raw)
        assert result.receipt is None
        assert result.issues[0].field_path == "date" and result.issues[0].blocking

    def test_rc7_shape_problems_never_raise(self):
        for body in ("nope", {}, {"response": {"resources": "x"}}, {"response": None}):
            page = parse_daysmart_receipt_page(body)
            assert page.receipts == []
        assert parse_daysmart_receipt("not a dict").receipt is None


class TestReceiptRows:
    def test_rc8_detail_rows_carry_the_po_line_identity(self):
        result = parse_daysmart_receipt_rows(copy.deepcopy(DETAIL_RAW))
        assert result.issues == []
        assert result.receipt_daysmart_id == 822801
        assert len(result.rows) == 2
        row = result.rows[0]
        assert (row.daysmart_row_id, row.receipt_id, row.po_line_id, row.purchase_order_id) == (
            3906196, 822801, 3924916, 785384)
        assert row.post_to_item_id == "DIT-ebc6f1b0-6e6"
        assert (row.quantity_received, row.quantity_in_stock, row.quantity_rejected) == (6, 6, 0)
        assert (row.amount_raw, row.tax_raw, row.shipping_raw) == ("1950", "0.00", None)   # verbatim
        assert row.expiration_date == 1817481600
        assert (row.status_id, row.is_active) == (2, True)
        assert row.updated_at == datetime(2026, 8, 23, 4, 38, 28, tzinfo=UTC)

    def test_rc9_row_identity_problems_block_the_row_others_are_withheld(self):
        body = copy.deepcopy(DETAIL_RAW)
        items = body["response"]["resources"]["receipt_items"]
        items[0]["id"] = "x"                                  # unreadable row id
        items[1]["item_id"] = "ABC-1"                         # not a DIT id
        items.append({"id": 3906198, "purchase_order_item_id": "3924918",    # string identity
                      "purchase_order_id": 785384, "item_id": "DIT-1"})
        items.append("garbage")                               # not an object
        items.append({"id": 3906199, "purchase_order_item_id": 3924919, "purchase_order_id": 785384,
                      "item_id": "DIT-2", "quantity_received": "six", "amount": [1]})
        result = parse_daysmart_receipt_rows(body)
        assert [r.daysmart_row_id for r in result.rows] == [3906199]     # the withheld-only row survives
        survivor = result.rows[0]
        assert survivor.quantity_received is None and survivor.amount_raw is None
        by_path = {i.field_path: i for i in result.issues}
        assert by_path["id"].blocking and by_path["item_id"].blocking
        assert by_path["purchase_order_item_id"].blocking and by_path["(row)"].blocking
        assert not by_path["quantity_received"].blocking and not by_path["amount"].blocking

    def test_rc10_missing_rows_or_envelope_is_a_named_issue_not_an_exception(self):
        result = parse_daysmart_receipt_rows({})
        assert result.rows == [] and result.issues[0].field_path == "response.resources"
        body = copy.deepcopy(DETAIL_RAW); del body["response"]["resources"]["receipt_items"]
        result = parse_daysmart_receipt_rows(body)
        assert result.rows == [] and result.issues[0].field_path == "receipt_items"
        assert result.receipt_daysmart_id == 822801


class TestDetailAndEchoes:
    """receipt-edit.har (2026-09-02): the detail header, the header-update
    echo, the filtered list, and how an UNPOSTED receipt hides its order."""

    def test_rc11_detail_header_parses_through_the_same_contract(self):
        result = parse_daysmart_receipt_detail(copy.deepcopy(DETAIL_RAW))
        assert result.issues == []
        r = result.receipt
        assert (r.daysmart_id, r.index, r.status, r.status_label) == (822801, 493, "posted", "Posted")
        assert r.supplier.name == "Queen's Pharma Limited" and r.supplier_daysmart_id == "c0e4123c-ae98-48"
        assert r.purchase_order_id == 785384                    # posted: the header carries the link
        assert r.item_count is None and r.name == ""            # not on this surface
        assert [row.daysmart_row_id for row in result.rows] == [3906196, 3906197]

    def test_rc12_an_unposted_receipt_has_no_order_link_on_its_header_only_on_rows(self):
        fresh = json.loads((FIXTURES / "daysmart_receipt_detail_829054.json").read_text(encoding="utf-8"))
        result = parse_daysmart_receipt_detail(fresh)
        assert result.issues == []
        assert result.receipt.status == "in_process" and result.receipt.status_label == "In Process"
        assert result.receipt.purchase_order_id is None         # header: nothing
        assert result.rows[0].purchase_order_id == 795607       # row: the order
        assert (result.rows[0].lot_number, result.rows[0].expiration_date,
                result.rows[0].manufacturer, result.rows[0].ndc_code) == ("3", 1790179200, "none", "2345676")
        listed = row0(); listed["status"] = {"id": 0, "label": "In Process"}; listed["purchase_order_id"] = ""
        parsed = parse_daysmart_receipt(listed)
        assert parsed.issues == [] and parsed.receipt.purchase_order_id is None   # "" is the list's null

    def test_rc13_header_update_echo_is_readable(self):
        echo = json.loads((FIXTURES / "daysmart_receipt_header_update_response.json").read_text(encoding="utf-8"))
        result = parse_daysmart_receipt(echo["response"]["resources"])
        assert result.issues == []
        assert (result.receipt.status_id, result.receipt.status, result.receipt.status_label) == (0, "in_process", "")
        assert (result.receipt.invoice_number, result.receipt.notes) == ("888046260817", "Test Receipt")
        assert result.receipt.tax == Decimal("2") and result.receipt.shipping == Decimal("1")   # number and string

    def test_rc14_the_filtered_list_comes_back_with_an_empty_meta_list(self):
        body = json.loads((FIXTURES / "daysmart_receipt_list_by_order.json").read_text(encoding="utf-8"))
        assert body["response"]["meta"] == []                   # a list, not an object
        page = parse_daysmart_receipt_page(body)
        assert page.receipts == [] and page.issues == [] and page.meta.last_page == 0

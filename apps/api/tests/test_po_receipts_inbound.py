"""RI/RR/RP-groups — the RECEIPT inbox: receipts ride along with the PO pull.

Receipts made in Daysmart's own screens were invisible, and our
``post_status`` only knew what WE did. This mirrors receipts (and their
generated rows) onto the local purchase orders they belong to, with the
same discipline as headers and lines — and, per Moses's rule, as part of
the PO pull: the full sync reads the receipt list after the order pages;
a single-PO refresh reads that order's receipts.

Rules pinned here:
- match on the Daysmart receipt id; link to OUR order through the
  receipt's purchase_order_id; a receipt whose order is not local is
  counted as ``unlinked``, never stored blind;
- rows match on the Daysmart row id and link to our PO lines through
  purchase_order_item_id; a row from another receipt is refused;
- ownership split: what WE sent is seeded once for an imported receipt
  and never touched by a pull; ``daysmart_*`` columns are the mirror;
- ``post_status`` follows Daysmart's own status when known — posted is
  posted whoever pressed the button; in-process means NOT posted;
- failures are reported (per receipt, or the list failure itself) and
  never queued; the header watermark is never held by receipts.

Interfaces under test:

    services/po_receipts_inbound.py
        apply_receipt_page(db, page) -> ReceiptsApplySummary(created, updated, unchanged, unlinked, issues_recorded)
        apply_receipt_rows(db, receipt, rows) -> ReceiptRowsApplySummary
        refresh_receipt_rows(db, receipt, daysmart)
    services/po_inbound_sync.py
        run_full_inbound_sync(...).receipts  -> ReceiptPullSummary
        refresh_purchase_order(...).receipts -> ReceiptPullSummary
"""

from __future__ import annotations

import copy
import json
import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_receipts_in.db")

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from schemas.purchase_orders import (
    DaysmartOrderPageParseResult,
    DaysmartPageMetaV1,
    DaysmartReceiptPageParseResult,
    parse_daysmart_created_order,
    parse_daysmart_order_item_page,
    parse_daysmart_order_page,
    parse_daysmart_receipt_page,
    parse_daysmart_receipt_rows,
)
from services.daysmart_service import DaysmartUnavailable
from services.po_inbound_sync import (
    ReceiptFailure,
    refresh_purchase_order,
    run_full_inbound_sync,
)
from schemas.purchase_orders import parse_daysmart_receipt_detail
from services.po_receipts_inbound import (
    apply_receipt_header,
    apply_receipt_page,
    apply_receipt_rows,
    link_receipt_from_rows,
    refresh_receipt,
    refresh_receipt_rows,
)
from services.po_sync_state import get_watermark

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
RECEIPTS_RAW = json.loads((FIXTURES / "daysmart_receipt_list_page1.json").read_text(encoding="utf-8"))
DETAIL_RAW = json.loads((FIXTURES / "daysmart_receipt_detail_822801.json").read_text(encoding="utf-8"))
ORDERS_RAW = json.loads((FIXTURES / "daysmart_order_list_page1.json").read_text(encoding="utf-8"))
ORDER_DETAIL_RAW = json.loads((FIXTURES / "daysmart_order_detail_793009.json").read_text(encoding="utf-8"))
EMPTY_LINES = {"response": {"resources": {
    "current_page": 1, "data": [], "last_page": 1, "per_page": 50, "total": 0},
    "message": {"code": 200}}}
FIRST3 = RECEIPTS_RAW["response"]["resources"][:3]
ORDER_IDS = {o["id"] for o in ORDERS_RAW["response"]["resources"]}


def linked_ids(page) -> list[int]:
    """Receipts on ``page`` whose order is on the order page (fixture truth)."""
    return [r.daysmart_id for r in page.receipts if r.purchase_order_id in ORDER_IDS]


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def local_po(db, daysmart_id, line_ids=()):
    po = models.PurchaseOrder(
        supplier_daysmart_id="x", supplier_name="S", order_date="2026-08-01T00:00:00+08:00",
        sync_status="synced", daysmart_id=daysmart_id, daysmart_index=1,
        created_at="x", updated_at="x")
    for line_id in line_ids:
        po.lines.append(models.PurchaseOrderLine(
            description="L", quantity="1", quantity_uom="EA", unit_price_raw="1.00",
            sync_status="synced", daysmart_item_id=line_id))
    db.add(po)
    db.commit()
    return po


def receipt_page(raw=None, *, last_page=1, rows=None):
    raw = copy.deepcopy(raw or RECEIPTS_RAW)
    if rows is not None:
        raw["response"]["resources"] = rows
    parsed = parse_daysmart_receipt_page(raw)
    return DaysmartReceiptPageParseResult(
        receipts=parsed.receipts, issues=parsed.issues,
        meta=DaysmartPageMetaV1(total=len(parsed.receipts), per_page=50,
                                current_page=1, last_page=last_page))


def receipt_rows(raw=None):
    return parse_daysmart_receipt_rows(copy.deepcopy(raw or DETAIL_RAW))


def empty_rows(receipt_id=0):
    return parse_daysmart_receipt_rows(
        {"response": {"resources": {"id": receipt_id, "receipt_items": []}}})


def _php(iso: str) -> dict:
    from datetime import datetime, timezone
    utc = datetime.fromisoformat(iso).astimezone(timezone.utc)
    return {"date": utc.strftime("%Y-%m-%d %H:%M:%S.%f"), "timezone_type": 3, "timezone": "UTC"}


def unposted(row: dict, **changes) -> dict:
    """The list row of a receipt still being received: In Process, and NO
    order link on the header (wire: "" on the list, null on the detail)."""
    r = copy.deepcopy(row)
    r["status"] = {"id": 0, "label": "In Process"}
    r["purchase_order_id"] = ""
    r["post_at"] = None
    r["post_by"] = None
    r.update(changes)
    return r


def detail_from_list_row(row: dict, rows_raw=(), **changes) -> dict:
    """The same receipt as the DETAIL surface answers it."""
    r = copy.deepcopy(row)
    r["index"] = f"Receipt #{row['index']}"
    r["create_at"] = _php(row["create_at"])
    r["update_at"] = _php(row["update_at"])
    r["purchase_order_id"] = row["purchase_order_id"] or None
    r["notes"] = row["notes"] or None
    r.pop("item_count", None)
    r.pop("name", None)
    r["receipt_items"] = list(rows_raw)
    r.update(changes)
    return {"response": {"resources": r, "message": {"code": 200}}}


ROW822801 = RECEIPTS_RAW["response"]["resources"][0]          # posted, order 785384
DETAIL_ROWS_RAW = DETAIL_RAW["response"]["resources"]["receipt_items"]


def rehomed_receipts(po_daysmart_id):
    """The fixture page with its FIRST receipt (822801) moved onto ``po_daysmart_id``."""
    raw = copy.deepcopy(RECEIPTS_RAW)
    raw["response"]["resources"][0]["purchase_order_id"] = po_daysmart_id
    return receipt_page(raw)


# ---------------------------------------------------------------------------
# RI — receipt headers onto local orders
# ---------------------------------------------------------------------------


class TestReceiptHeaders:
    def test_ri1_receipts_land_on_their_local_orders(self, db):
        ids = {r["purchase_order_id"] for r in FIRST3}
        pos = {i: local_po(db, i) for i in ids}
        expected = sum(1 for r in RECEIPTS_RAW["response"]["resources"]
                       if r["purchase_order_id"] in ids)
        summary = apply_receipt_page(db, receipt_page())
        db.commit()
        assert (summary.created, summary.unlinked) == (expected, 50 - expected)
        r = db.query(models.PoReceipt).filter_by(daysmart_receipt_id=822801).one()
        assert r.purchase_order_id == pos[785384].id
        assert (r.sync_status, r.post_status) == ("synced", "posted")
        assert (r.daysmart_status_id, r.daysmart_status_label, r.daysmart_status) == (1, "Posted", "posted")
        assert r.invoice_number == "888046260817" and r.daysmart_invoice_number == "888046260817"
        assert r.amount == "3850.00" and r.daysmart_amount == "3850.00"
        assert r.receipt_date == "2026-08-16T16:00:00+00:00"
        assert r.daysmart_purchase_order_id == 785384
        assert r.daysmart_supplier_name == "Queen's Pharma Limited"
        assert (r.daysmart_item_count, r.daysmart_is_active) == (2, True)
        assert r.content_hash and r.last_seen_at
        assert r in pos[785384].receipts

    def test_ri2_reapply_is_idempotent(self, db):
        local_po(db, 785384)
        apply_receipt_page(db, receipt_page())
        db.commit()
        summary = apply_receipt_page(db, receipt_page())
        db.commit()
        assert (summary.created, summary.updated) == (0, 0)
        assert summary.unchanged >= 1
        assert db.query(models.PoReceipt).count() == summary.unchanged

    def test_ri3_a_change_updates_the_mirror_and_leaves_what_we_sent(self, db):
        local_po(db, 785384)
        apply_receipt_page(db, receipt_page())
        db.commit()
        raw = copy.deepcopy(RECEIPTS_RAW)
        raw["response"]["resources"][0]["amount"] = "3999.00"
        summary = apply_receipt_page(db, receipt_page(raw))
        db.commit()
        assert summary.updated == 1
        r = db.query(models.PoReceipt).filter_by(daysmart_receipt_id=822801).one()
        assert r.daysmart_amount == "3999.00"
        assert r.amount == "3850.00"                       # seeded once, then ours

    def test_ri4_a_receipt_we_created_is_matched_not_duplicated(self, db):
        po = local_po(db, 785384)
        ours = models.PoReceipt(
            purchase_order_id=po.id, daysmart_receipt_id=822801, sync_status="synced",
            post_status="draft", invoice_number="INV-9", receipt_date="2026-08-16 00:00:00",
            notes="", created_at="x", updated_at="x")
        db.add(ours)
        db.commit()
        summary = apply_receipt_page(db, receipt_page())
        db.commit()
        assert (summary.created, summary.updated) == (0, 1)
        assert db.query(models.PoReceipt).filter_by(daysmart_receipt_id=822801).count() == 1
        assert ours.invoice_number == "INV-9"              # what WE sent
        assert ours.daysmart_invoice_number == "888046260817"
        assert ours.post_status == "posted"                # posted — whoever pressed the button

    def test_ri5_daysmart_in_process_overrides_our_posted_belief_unknown_changes_nothing(self, db):
        po = local_po(db, 785384)
        ours = models.PoReceipt(
            purchase_order_id=po.id, daysmart_receipt_id=822801, sync_status="synced",
            post_status="posted", invoice_number="INV-9", receipt_date="2026-08-16 00:00:00",
            notes="", created_at="x", updated_at="x")
        db.add(ours)
        db.commit()
        raw = copy.deepcopy(RECEIPTS_RAW)
        raw["response"]["resources"][0]["status"] = {"id": 0, "label": "In Process"}
        apply_receipt_page(db, receipt_page(raw))
        db.commit()
        assert (ours.post_status, ours.daysmart_status) == ("draft", "in_process")
        raw["response"]["resources"][0]["status"] = {"id": 9, "label": "Void"}
        apply_receipt_page(db, receipt_page(raw))
        db.commit()
        assert (ours.post_status, ours.daysmart_status, ours.daysmart_status_id) == ("draft", None, 9)

    def test_ri6_unlinked_receipts_are_counted_not_stored(self, db):
        summary = apply_receipt_page(db, receipt_page())
        db.commit()
        assert summary.unlinked == 50
        assert db.query(models.PoReceipt).count() == 0

    def test_ri7_header_issues_land_in_the_review_table_once(self, db):
        local_po(db, 785384)
        raw = copy.deepcopy(RECEIPTS_RAW)
        raw["response"]["resources"][0]["amount"] = "abc"
        summary = apply_receipt_page(db, receipt_page(raw))
        db.commit()
        assert summary.issues_recorded == 1
        assert db.query(models.PoReceipt).filter_by(daysmart_receipt_id=822801).count() == 0
        rec = db.query(models.PoValidationIssueRecord).one()
        assert (rec.code, rec.field_path, rec.daysmart_id, rec.payload_context) == (
            "PO_MONEY_UNPARSEABLE", "amount", 822801, "fetch_receipts")
        assert apply_receipt_page(db, receipt_page(raw)).issues_recorded == 0   # dedup


# ---------------------------------------------------------------------------
# RR — generated rows onto a local receipt
# ---------------------------------------------------------------------------


class TestReceiptRows:
    def linked_receipt(self, db, line_ids=(3924916, 3924917)):
        po = local_po(db, 785384, line_ids=line_ids)
        apply_receipt_page(db, receipt_page())
        db.commit()
        return po, db.query(models.PoReceipt).filter_by(daysmart_receipt_id=822801).one()

    def test_rr1_rows_are_mirrored_and_linked_to_our_lines(self, db):
        po, receipt = self.linked_receipt(db)
        summary = apply_receipt_rows(db, receipt, receipt_rows())
        db.commit()
        assert summary.created == 2
        item = next(i for i in receipt.items if i.daysmart_receipt_item_id == 3906196)
        line = next(l for l in po.lines if l.daysmart_item_id == 3924916)
        assert item.purchase_order_line_id == line.id and item.daysmart_po_item_id == 3924916
        assert item.post_to_item_id == "DIT-ebc6f1b0-6e6"
        assert (item.quantity_received, item.quantity_in_stock, item.amount) == ("6", "6", "1950")
        assert (item.daysmart_quantity_received, item.daysmart_expiration_date,
                item.daysmart_status_id) == (6, 1817481600, 2)
        assert item.sync_status == "synced" and item.content_hash and item.last_seen_at

    def test_rr2_reapply_unchanged_then_an_edit_updates_the_mirror_only(self, db):
        po, receipt = self.linked_receipt(db)
        apply_receipt_rows(db, receipt, receipt_rows())
        db.commit()
        assert apply_receipt_rows(db, receipt, receipt_rows()).unchanged == 2
        raw = copy.deepcopy(DETAIL_RAW)
        raw["response"]["resources"]["receipt_items"][0]["quantity_received"] = 5
        summary = apply_receipt_rows(db, receipt, receipt_rows(raw))
        db.commit()
        assert summary.updated == 1
        item = next(i for i in receipt.items if i.daysmart_receipt_item_id == 3906196)
        assert item.daysmart_quantity_received == 5 and item.quantity_received == "6"

    def test_rr3_a_row_we_created_is_matched_and_its_links_backfilled(self, db):
        po, receipt = self.linked_receipt(db)
        ours = models.PoReceiptItem(
            daysmart_receipt_item_id=3906196, quantity_received="5", quantity_in_stock="5",
            amount="1950.00", lot_number="L-1", sync_status="synced")
        receipt.items.append(ours)
        db.commit()
        summary = apply_receipt_rows(db, receipt, receipt_rows())
        db.commit()
        assert (summary.created, summary.updated) == (1, 1)
        assert ours.quantity_received == "5" and ours.daysmart_quantity_received == 6
        line = next(l for l in po.lines if l.daysmart_item_id == 3924916)
        assert ours.purchase_order_line_id == line.id and ours.daysmart_po_item_id == 3924916
        assert ours.post_to_item_id == "DIT-ebc6f1b0-6e6"

    def test_rr4_a_row_from_another_receipt_is_refused(self, db):
        po, receipt = self.linked_receipt(db)
        raw = copy.deepcopy(DETAIL_RAW)
        raw["response"]["resources"]["receipt_items"][0]["purchase_order_receipt_id"] = 999
        summary = apply_receipt_rows(db, receipt, receipt_rows(raw))
        db.commit()
        assert (summary.created, summary.issues_recorded) == (1, 1)
        rec = db.query(models.PoValidationIssueRecord).one()
        assert rec.field_path == "row[3906196].purchase_order_receipt_id"
        assert (rec.payload_context, rec.daysmart_id, rec.purchase_order_id) == (
            "fetch_receipt_rows", 822801, po.id)

    def test_rr5_row_issues_are_named_and_a_withheld_only_row_survives(self, db):
        po, receipt = self.linked_receipt(db)
        raw = copy.deepcopy(DETAIL_RAW)
        items = raw["response"]["resources"]["receipt_items"]
        items[0]["quantity_received"] = "six"                 # withheld, row kept
        items[1]["item_id"] = "BAD"                           # identity → row dropped
        summary = apply_receipt_rows(db, receipt, receipt_rows(raw))
        db.commit()
        assert summary.created == 1
        assert {r.field_path for r in db.query(models.PoValidationIssueRecord)} == {
            "row[3906196].quantity_received", "row[3906197].item_id"}

    def test_rr7_a_staged_adjustment_is_adopted_by_its_generated_row(self, db):
        po, receipt = self.linked_receipt(db)
        staged = models.PoReceiptItem(
            daysmart_po_item_id=3924916, quantity_received="5", quantity_in_stock="5",
            amount="1950.00", lot_number="L-1", sync_status="pending")   # no row id yet
        receipt.items.append(staged)
        db.commit()
        summary = apply_receipt_rows(db, receipt, receipt_rows())
        db.commit()
        assert (summary.adopted, summary.created) == (1, 1)
        assert staged.daysmart_receipt_item_id == 3906196          # adopted, not duplicated
        assert staged.quantity_received == "5"                     # OUR adjustment stands
        assert staged.daysmart_quantity_received == 6              # the mirror
        assert staged.daysmart_post_to_item_id == "DIT-ebc6f1b0-6e6"
        assert staged.purchase_order_line_id is not None
        assert len(receipt.items) == 2

    def test_rr8_two_staged_adjustments_for_one_line_are_never_guessed(self, db):
        po, receipt = self.linked_receipt(db)
        for _ in range(2):
            receipt.items.append(models.PoReceiptItem(
                daysmart_po_item_id=3924916, quantity_received="5", quantity_in_stock="5",
                amount="1950.00", sync_status="pending"))
        db.commit()
        summary = apply_receipt_rows(db, receipt, receipt_rows())
        db.commit()
        assert (summary.adopted, summary.created, summary.issues_recorded) == (0, 1, 1)
        assert all(i.daysmart_receipt_item_id is None for i in receipt.items if i.sync_status == "pending")
        rec = db.query(models.PoValidationIssueRecord).one()
        assert rec.field_path == "row[3906196].purchase_order_item_id"

    def test_rr6_refresh_rows_needs_a_synced_receipt_and_propagates_transport_errors(self, db):
        po, receipt = self.linked_receipt(db)

        class Down:
            def fetch_receipt_rows(self, receipt_id):
                raise DaysmartUnavailable("down")

        with pytest.raises(DaysmartUnavailable):
            refresh_receipt_rows(db, receipt, Down())
        receipt.daysmart_receipt_id = None
        with pytest.raises(ValueError):
            refresh_receipt_rows(db, receipt, Down())


# ---------------------------------------------------------------------------
# RL — an UNPOSTED receipt names its order only on its rows
# ---------------------------------------------------------------------------


class TestLinkingByRows:
    def test_rl1_a_new_unposted_receipt_is_deferred_then_linked_through_its_rows(self, db):
        po = local_po(db, 785384, line_ids=(3924916, 3924917))
        page = receipt_page(rows=[unposted(ROW822801)])
        summary = apply_receipt_page(db, page)
        db.commit()
        assert (summary.created, summary.unlinked) == (0, 0)
        assert [r.daysmart_id for r in summary.deferred] == [822801]
        assert db.query(models.PoReceipt).count() == 0

        outcome = link_receipt_from_rows(db, summary.deferred[0], receipt_rows())
        db.commit()
        assert outcome.local is not None and outcome.reason is None
        local = outcome.local
        assert local.purchase_order_id == po.id
        assert (local.post_status, local.daysmart_status, local.daysmart_purchase_order_id) == (
            "draft", "in_process", None)
        assert outcome.rows.created == 2 and len(local.items) == 2

    def test_rl2_rows_naming_a_non_local_order_stay_unlinked(self, db):
        page = receipt_page(rows=[unposted(ROW822801)])
        deferred = apply_receipt_page(db, page).deferred[0]
        outcome = link_receipt_from_rows(db, deferred, receipt_rows())
        assert outcome.local is None and "not local" in outcome.reason
        assert db.query(models.PoReceipt).count() == 0

    def test_rl3_rows_naming_two_orders_are_never_guessed(self, db):
        local_po(db, 785384)
        deferred = apply_receipt_page(db, receipt_page(rows=[unposted(ROW822801)])).deferred[0]
        raw = copy.deepcopy(DETAIL_RAW)
        raw["response"]["resources"]["receipt_items"][1]["purchase_order_id"] = 999
        outcome = link_receipt_from_rows(db, deferred, receipt_rows(raw))
        db.commit()
        assert outcome.local is None and outcome.issues_recorded == 1
        rec = db.query(models.PoValidationIssueRecord).one()
        assert rec.field_path == "receipt_items[].purchase_order_id" and rec.daysmart_id == 822801

    def test_rl4_the_full_pull_links_unposted_receipts_and_reports_a_rows_failure(self, db):
        raw = copy.deepcopy(RECEIPTS_RAW)
        raw["response"]["resources"][0] = unposted(ROW822801)               # order only on its rows
        rows = copy.deepcopy(DETAIL_RAW)
        for r in rows["response"]["resources"]["receipt_items"]:
            r["purchase_order_id"] = 791640                                 # a local order
        daysmart = FakeDaysmart(pages={1: order_page()}, receipts={1: receipt_page(raw)},
                                rows={822801: receipt_rows(rows)})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()
        rc = summary.receipts
        assert daysmart.rows_fetched.count(822801) == 1                     # read once, not twice
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        mine = [r for r in po.receipts if r.daysmart_receipt_id == 822801]
        assert len(mine) == 1 and mine[0].post_status == "draft" and len(mine[0].items) == 2
        expected = len(linked_ids(receipt_page(raw))) + 1                  # header-linked + this one
        assert rc.created == expected

        db.query(models.PoReceiptItem).delete()
        db.query(models.PoReceipt).delete()
        db.commit()
        daysmart.rows = {822801: DaysmartUnavailable("down")}
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert summary.receipts.failures == (
            ReceiptFailure(daysmart_receipt_id=822801, error_code="DaysmartUnavailable", message="down"),)
        assert db.query(models.PoReceipt).filter_by(daysmart_receipt_id=822801).count() == 0


# ---------------------------------------------------------------------------
# RH — the detail surface onto an existing receipt (no churn, real changes flow)
# ---------------------------------------------------------------------------


class TestDetailHeader:
    def pulled(self, db):
        po = local_po(db, 785384, line_ids=(3924916, 3924917))
        apply_receipt_page(db, receipt_page())
        db.commit()
        return po, db.query(models.PoReceipt).filter_by(daysmart_receipt_id=822801).one()

    def test_rh1_an_unchanged_receipt_is_unchanged_whichever_surface_says_so(self, db):
        po, local = self.pulled(db)
        before = (local.content_hash, local.daysmart_created_at, local.updated_at)
        daysmart = FakeDaysmart(receipt_details={822801: detail_from_list_row(ROW822801, DETAIL_ROWS_RAW)})
        summary = refresh_receipt(db, local, daysmart)
        db.commit()
        assert summary.header_changed is False
        assert (local.content_hash, local.daysmart_created_at, local.updated_at) == before   # spelling kept
        assert summary.rows.created == 2
        assert apply_receipt_page(db, receipt_page()).unchanged >= 1                     # the list agrees

    def test_rh2_a_real_change_on_the_detail_flows_through(self, db):
        po, local = self.pulled(db)
        daysmart = FakeDaysmart(receipt_details={822801: detail_from_list_row(
            ROW822801, DETAIL_ROWS_RAW, invoice_number="NEW-1", notes="edited in Daysmart")})
        summary = refresh_receipt(db, local, daysmart)
        db.commit()
        assert summary.header_changed is True
        assert (local.daysmart_invoice_number, local.daysmart_notes) == ("NEW-1", "edited in Daysmart")
        assert local.invoice_number == "888046260817"                                    # ours, untouched
        assert local.daysmart_item_count == 2                                            # kept from the list


# ---------------------------------------------------------------------------
# RP — receipts ride along with the full pull and the single-PO refresh
# ---------------------------------------------------------------------------


class FakeDaysmart:
    def __init__(self, *, pages=None, lines=None, details=None, receipts=None, rows=None,
                 receipt_details=None):
        self.pages = pages or {}
        self.lines = lines or {}
        self.details = details or {}
        self.receipts = receipts or {}          # page → page result | Exception
        self.rows = rows or {}                  # receipt id → rows result | Exception
        self.receipt_details = receipt_details or {}   # receipt id → detail body | Exception
        self.receipts_fetched: list[tuple[int, int | None]] = []
        self.rows_fetched: list[int] = []
        self.details_fetched: list[int] = []

    def fetch_receipt(self, daysmart_receipt_id):
        self.details_fetched.append(daysmart_receipt_id)
        action = self.receipt_details[daysmart_receipt_id]
        if isinstance(action, Exception):
            raise action
        return parse_daysmart_receipt_detail(copy.deepcopy(action))

    def fetch_orders(self, page=1, order_type="all"):
        action = self.pages[page]
        if isinstance(action, Exception):
            raise action
        return action

    def fetch_order_lines(self, daysmart_order_id):
        action = self.lines.get(daysmart_order_id, parse_daysmart_order_item_page(EMPTY_LINES))
        if isinstance(action, Exception):
            raise action
        return action

    def get_order(self, daysmart_order_id):
        action = self.details[daysmart_order_id]
        if isinstance(action, Exception):
            raise action
        return parse_daysmart_created_order(copy.deepcopy(action))

    def fetch_receipts(self, page=1, purchase_order_id=None):
        self.receipts_fetched.append((page, purchase_order_id))
        action = self.receipts.get(page, receipt_page(rows=[]))
        if isinstance(action, Exception):
            raise action
        if purchase_order_id is None:
            return action
        mine = [r for r in action.receipts if r.purchase_order_id == purchase_order_id]
        return DaysmartReceiptPageParseResult(
            receipts=mine, issues=[],
            meta=DaysmartPageMetaV1(total=len(mine), per_page=50, current_page=1, last_page=1))

    def fetch_receipt_rows(self, daysmart_receipt_id):
        self.rows_fetched.append(daysmart_receipt_id)
        action = self.rows.get(daysmart_receipt_id, empty_rows(daysmart_receipt_id))
        if isinstance(action, Exception):
            raise action
        return action


def order_page(*, last_page=1):
    parsed = parse_daysmart_order_page(copy.deepcopy(ORDERS_RAW))
    return DaysmartOrderPageParseResult(
        orders=parsed.orders, issues=parsed.issues,
        meta=DaysmartPageMetaV1(total=len(parsed.orders), per_page=50,
                                current_page=1, last_page=last_page))


class TestReceiptsRideAlong:
    def test_rp1_full_sync_pulls_receipts_after_orders_and_rows_for_linked_ones(self, db):
        daysmart = FakeDaysmart(pages={1: order_page()}, receipts={1: rehomed_receipts(791640)},
                                rows={822801: receipt_rows()})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert summary.completed is True and summary.created == 50
        linked = linked_ids(rehomed_receipts(791640))       # the fixture decides how many link
        assert 822801 in linked and 0 < len(linked) < 50
        rc = summary.receipts
        assert rc.completed is True
        assert (rc.created, rc.unlinked) == (len(linked), 50 - len(linked))
        assert (rc.rows_synced, rc.rows_failed, rc.failures) == (len(linked), 0, ())
        assert sorted(daysmart.rows_fetched) == sorted(linked)   # unlinked receipts get no row pull
        assert daysmart.receipts_fetched == [(1, None)]
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        receipt = next(r for r in po.receipts if r.daysmart_receipt_id == 822801)
        assert len(receipt.items) == 2

    def test_rp2_a_receipt_list_failure_is_reported_and_never_holds_the_order_pull(self, db):
        daysmart = FakeDaysmart(pages={1: order_page()}, receipts={1: DaysmartUnavailable("down")})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert summary.completed is True and get_watermark(db) is not None
        assert summary.receipts.completed is False
        assert summary.receipts.error == "DaysmartUnavailable: down"

    def test_rp3_a_row_pull_failure_is_reported_per_receipt(self, db):
        daysmart = FakeDaysmart(pages={1: order_page()}, receipts={1: rehomed_receipts(791640)},
                                rows={822801: DaysmartUnavailable("down")})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()
        linked = linked_ids(rehomed_receipts(791640))
        rc = summary.receipts
        assert rc.completed is True and rc.created == len(linked)
        assert (rc.rows_synced, rc.rows_failed) == (len(linked) - 1, 1)
        assert rc.failures == (ReceiptFailure(daysmart_receipt_id=822801,
                                              error_code="DaysmartUnavailable", message="down"),)

    def test_rp4_an_incomplete_order_pull_skips_receipts_and_says_so(self, db):
        daysmart = FakeDaysmart(pages={1: order_page(last_page=2), 2: DaysmartUnavailable("down")},
                                receipts={1: rehomed_receipts(791640)})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert summary.completed is False
        assert summary.receipts.completed is False
        assert summary.receipts.error.startswith("skipped")
        assert daysmart.receipts_fetched == []

    def test_rp5_refresh_pulls_this_orders_receipts_with_the_filter(self, db):
        po = local_po(db, 793009)
        daysmart = FakeDaysmart(details={793009: ORDER_DETAIL_RAW},
                                receipts={1: rehomed_receipts(793009)},
                                rows={822801: receipt_rows()})
        summary = refresh_purchase_order(db, po, daysmart)
        db.commit()
        assert daysmart.receipts_fetched == [(1, 793009)]
        assert (summary.receipts.created, summary.receipts.rows_synced) == (1, 1)
        assert [r.daysmart_receipt_id for r in po.receipts] == [822801]
        assert len(po.receipts[0].items) == 2

    def test_rp6_refresh_applies_nothing_when_a_receipt_read_fails(self, db):
        po = local_po(db, 793009)
        daysmart = FakeDaysmart(details={793009: ORDER_DETAIL_RAW},
                                receipts={1: rehomed_receipts(793009)},
                                rows={822801: DaysmartUnavailable("down")})
        with pytest.raises(DaysmartUnavailable):
            refresh_purchase_order(db, po, daysmart)
        assert po.daysmart_status is None                  # the header was NOT applied
        assert po.receipts == []


    def test_rp7_refresh_also_pulls_our_own_receipts_the_filter_cannot_see(self, db):
        po = local_po(db, 793009, line_ids=(3980834,))
        ours = models.PoReceipt(
            purchase_order_id=po.id, daysmart_receipt_id=829054, sync_status="synced",
            post_status="draft", invoice_number="", receipt_date="2026-09-02 08:49:26",
            notes="", created_at="x", updated_at="x")
        db.add(ours)
        db.commit()
        fresh = json.loads((FIXTURES / "daysmart_receipt_detail_829054.json").read_text(encoding="utf-8"))
        daysmart = FakeDaysmart(details={793009: ORDER_DETAIL_RAW},
                                receipts={1: receipt_page(rows=[])},        # the filter finds nothing
                                receipt_details={829054: fresh})
        summary = refresh_purchase_order(db, po, daysmart)
        db.commit()
        assert daysmart.details_fetched == [829054]
        assert summary.receipts.rows_synced == 1 and summary.receipts.updated == 1
        assert (ours.daysmart_status, ours.daysmart_invoice_number) == ("in_process", "888046260817")
        assert [i.daysmart_receipt_item_id for i in ours.items] == [3937545]
        assert ours.items[0].purchase_order_line_id == po.lines[0].id
        assert (ours.items[0].daysmart_lot_number, ours.items[0].daysmart_expiration_date) == ("3", 1790179200)

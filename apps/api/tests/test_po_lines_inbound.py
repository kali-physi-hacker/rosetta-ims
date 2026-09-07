"""LI/TR/RF-groups — the order-LINES inbox: lines ride along with every pull.

The header inbox imported headers only; every imported PO was a header
with an empty line list. This closes that gap the same way the header
inbox works — one field map, hash-driven, absence is never a delete.

Ownership split on a line (mirrors the PO header):
- Rosetta-owned: what WE ordered — description, quantity, quantity_uom,
  unit_price_raw, notes. Set once (seeded from Daysmart for an imported
  line) and never touched by fetches.
- Daysmart-owned mirror: their current view — daysmart_quantity_ordered,
  daysmart_quantity_received, daysmart_back_ordered, daysmart_price_raw
  (the wire FLOAT, verbatim), daysmart_line_total_raw, daysmart_is_active,
  daysmart_item_name, daysmart_container_type, daysmart_catalogue_cost_raw,
  daysmart_updated_at — plus content_hash and last_seen_at.

When lines are pulled (Moses's correction, 2026-09-02): with EVERY pull,
for EVERY order pulled — no trigger. An edited line (quantity, price,
notes) changes nothing on the header, not even ``item_count``, so no
header-derived signal can be trusted; ``item_count`` stays a stored
display value that decides nothing. And NO deferred retry: a line pull
that fails is reported per order in the summary (``line_failures``) for a
person to re-run — nothing is marked for a later cycle.

Interfaces under test:

    services/po_lines_inbound.py
        apply_order_lines(db, po, page)   -> LinesApplySummary
        refresh_order_lines(db, po, daysmart) -> LinesApplySummary  (raises DaysmartError)
    services/po_inbound_sync.py
        run_full_inbound_sync(db, daysmart) -> FullSyncSummary
            .lines_synced, .lines_failed, .line_failures: tuple[LineFailure, ...]
        refresh_purchase_order(db, po, daysmart) -> RefreshSummary
            header from the DETAIL surface (get_order) + lines; both reads
            first, then apply; raises DaysmartOrderUnreadable / DaysmartError
    models: PurchaseOrder.item_count, PurchaseOrderLine.daysmart_* mirror columns
"""

from __future__ import annotations

import copy
import json
import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_lines.db")

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from schemas.purchase_orders import (
    DaysmartOrderPageParseResult,
    DaysmartPageMetaV1,
    parse_daysmart_created_order,
    parse_daysmart_order_item_page,
    parse_daysmart_order_page,
)
from services.daysmart_service import DaysmartMalformedResponse, DaysmartUnavailable
from services.po_inbound_sync import (
    DaysmartOrderUnreadable,
    LineFailure,
    refresh_purchase_order,
    run_full_inbound_sync,
)
from services.po_lines_inbound import apply_order_lines, refresh_order_lines
from services.po_sync_state import get_watermark

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
LINES_RAW = json.loads(
    (FIXTURES / "daysmart_order_item_list_793009.json").read_text(encoding="utf-8"))
LIST_RAW = json.loads(
    (FIXTURES / "daysmart_order_list_page1.json").read_text(encoding="utf-8"))

EMPTY_LINES = {"response": {"resources": {
    "current_page": 1, "data": [], "last_page": 1, "per_page": 50, "total": 0},
    "message": {"code": 200}}}


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def synced_po(db, daysmart_id=793009):
    po = models.PurchaseOrder(
        supplier_daysmart_id="3cd1ace0-ddd6-4a", supplier_name="Alfamedic",
        order_date="2026-08-28T11:00:00+08:00", sync_status="synced",
        daysmart_id=daysmart_id, daysmart_index=524,
        created_at="x", updated_at="x")
    db.add(po)
    db.commit()
    return po


def lines_page(raw=None):
    return parse_daysmart_order_item_page(copy.deepcopy(raw or LINES_RAW))


def mutate(line_id: int, **changes) -> dict:
    raw = copy.deepcopy(LINES_RAW)
    for r in raw["response"]["resources"]["data"]:
        if r["id"] == line_id:
            r.update(changes)
    return raw


# ---------------------------------------------------------------------------
# LI — applying a page of lines to one PO
# ---------------------------------------------------------------------------


class TestApplyOrderLines:
    def test_li1_imported_lines_are_seeded_and_synced(self, db):
        po = synced_po(db)
        summary = apply_order_lines(db, po, lines_page())
        db.commit()

        assert summary.created == 4 and summary.updated == 0 and summary.unchanged == 0
        assert len(po.lines) == 4
        line = next(l for l in po.lines if l.daysmart_item_id == 3969381)
        # Identity, both kinds.
        assert line.daysmart_inventory_item_id == "DIT-ebbd2f4c-6e6"
        # Rosetta-owned fields SEEDED from Daysmart for an imported line …
        assert line.description == "50010313 - Cephalexin (Cephacin) Tablet 200mg"
        assert line.quantity == "240"
        assert line.quantity_uom == "Tablet(s)"
        assert line.unit_price_raw == "2.5083333333333"     # verbatim, never rounded
        assert line.sync_status == "synced"                 # it exists in Daysmart
        # … and the Daysmart-owned mirror.
        assert line.daysmart_quantity_ordered == 240
        assert line.daysmart_quantity_received == 0
        assert line.daysmart_back_ordered == 0
        assert line.daysmart_is_active is True
        assert line.daysmart_catalogue_cost_raw == "2.51"
        assert line.content_hash and line.last_seen_at

    def test_li2_reapply_is_idempotent(self, db):
        po = synced_po(db)
        apply_order_lines(db, po, lines_page())
        db.commit()
        second = apply_order_lines(db, po, lines_page())
        db.commit()
        assert (second.created, second.updated, second.unchanged) == (0, 0, 4)
        assert len(po.lines) == 4

    def test_li3_change_updates_the_mirror_not_the_rosetta_fields(self, db):
        po = synced_po(db)
        apply_order_lines(db, po, lines_page())
        db.commit()
        summary = apply_order_lines(
            db, po, lines_page(mutate(3969381, quantity_received=100, quantity_ordered=250)))
        db.commit()
        assert (summary.updated, summary.unchanged) == (1, 3)
        line = next(l for l in po.lines if l.daysmart_item_id == 3969381)
        assert line.daysmart_quantity_received == 100      # mirror moved
        assert line.daysmart_quantity_ordered == 250
        assert line.quantity == "240"                       # what we ordered: untouched

    def test_li4_inactive_is_mirrored_never_deleted(self, db):
        po = synced_po(db)
        apply_order_lines(db, po, lines_page())
        db.commit()
        apply_order_lines(db, po, lines_page(mutate(3969382, is_active=False)))
        db.commit()
        line = next(l for l in po.lines if l.daysmart_item_id == 3969382)
        assert line.daysmart_is_active is False
        assert len(po.lines) == 4                           # row kept

    def test_li5_absence_from_the_page_is_not_a_delete(self, db):
        po = synced_po(db)
        apply_order_lines(db, po, lines_page())
        db.commit()
        three = copy.deepcopy(LINES_RAW)
        three["response"]["resources"]["data"] = [
            r for r in three["response"]["resources"]["data"] if r["id"] != 3969384]
        three["response"]["resources"]["total"] = 3
        summary = apply_order_lines(db, po, lines_page(three))
        db.commit()
        assert summary.unchanged == 3
        assert len(po.lines) == 4                           # the missing one survives
        ghost = next(l for l in po.lines if l.daysmart_item_id == 3969384)
        assert ghost.daysmart_is_active is True             # nothing inferred from absence

    def test_li6_rosetta_created_line_keeps_its_own_fields(self, db):
        po = synced_po(db)
        po.lines = [models.PurchaseOrderLine(
            description="Cephalexin (our wording)", quantity="240",
            quantity_uom="TABLET", unit_price_raw="2.51", notes="ours",
            sync_status="synced", daysmart_item_id=3969381,
            daysmart_inventory_item_id="DIT-ebbd2f4c-6e6")]
        db.commit()
        summary = apply_order_lines(db, po, lines_page())
        db.commit()
        assert summary.created == 3 and summary.updated == 1   # ours matched by line id
        assert len(po.lines) == 4                               # no duplicate
        ours = next(l for l in po.lines if l.daysmart_item_id == 3969381)
        assert ours.description == "Cephalexin (our wording)"   # Rosetta-owned: untouched
        assert ours.unit_price_raw == "2.51"
        assert ours.quantity_uom == "TABLET"
        assert ours.daysmart_price_raw == "2.5083333333333"     # mirror: filled

    def test_li7_bad_row_is_recorded_and_skipped(self, db):
        po = synced_po(db)
        summary = apply_order_lines(db, po, lines_page(mutate(3969383, price="not-money")))
        db.commit()
        assert summary.created == 3
        assert summary.issues_recorded == 1
        issue = db.query(models.PoValidationIssueRecord).one()
        assert issue.payload_context == "fetch_lines"
        assert issue.daysmart_id == 793009
        assert "3969383" in issue.field_path

    def test_li8_line_from_another_order_is_refused_not_adopted(self, db):
        po = synced_po(db, daysmart_id=111111)              # not the fixture's order
        summary = apply_order_lines(db, po, lines_page())
        db.commit()
        assert summary.created == 0
        assert summary.issues_recorded == 4
        assert po.lines == []


class TestRefresh:
    def test_li9_refresh_fetches_and_applies(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(lines={793009: lines_page()})
        summary = refresh_order_lines(db, po, daysmart)
        db.commit()
        assert summary.created == 4
        assert daysmart.lines_fetched == [793009]

    def test_li10_refresh_refuses_an_unsynced_po(self, db):
        po = synced_po(db)
        po.daysmart_id = None
        with pytest.raises(ValueError):
            refresh_order_lines(db, po, FakeDaysmart())

    def test_li11_refresh_transport_failure_propagates_typed(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(lines={793009: DaysmartUnavailable("down")})
        with pytest.raises(DaysmartUnavailable):
            refresh_order_lines(db, po, daysmart)


# ---------------------------------------------------------------------------
# TR — lines ride along with every full pull (no trigger, no deferred retry)
# ---------------------------------------------------------------------------


class FakeDaysmart:
    """fetch_orders scripted per page; fetch_order_lines per order id (a
    page, or an exception); get_order per order id (a detail body, or an
    exception)."""

    def __init__(self, pages=None, lines=None, details=None):
        self.pages = pages or {}
        self.lines = lines or {}
        self.details = details or {}
        self.lines_fetched: list[int] = []
        self.details_fetched: list[int] = []

    def fetch_orders(self, page=1, order_type="all"):
        action = self.pages[page]
        if isinstance(action, Exception):
            raise action
        return action

    def fetch_order_lines(self, daysmart_order_id):
        self.lines_fetched.append(daysmart_order_id)
        action = self.lines.get(daysmart_order_id, lines_page(EMPTY_LINES))
        if isinstance(action, Exception):
            raise action
        return action

    def get_order(self, daysmart_order_id):
        self.details_fetched.append(daysmart_order_id)
        action = self.details[daysmart_order_id]
        if isinstance(action, Exception):
            raise action
        return parse_daysmart_created_order(copy.deepcopy(action))

    def fetch_receipts(self, page=1, purchase_order_id=None):
        # Receipts are out of scope here — a well-formed empty list page.
        from schemas.purchase_orders import parse_daysmart_receipt_page
        return parse_daysmart_receipt_page({"response": {
            "meta": {"total": 0, "perPage": 50, "currentPage": 1, "lastPage": 1},
            "resources": [], "message": {"code": 200}}})

    def fetch_receipt_rows(self, daysmart_receipt_id):
        from schemas.purchase_orders import parse_daysmart_receipt_rows
        return parse_daysmart_receipt_rows(
            {"response": {"resources": {"id": daysmart_receipt_id, "receipt_items": []}}})


def header_page(raw=None, *, last_page=1):
    parsed = parse_daysmart_order_page(copy.deepcopy(raw or LIST_RAW))
    return DaysmartOrderPageParseResult(
        orders=parsed.orders, issues=parsed.issues,
        meta=DaysmartPageMetaV1(total=len(parsed.orders), per_page=50,
                                current_page=1, last_page=last_page))


def lines_for(order_id: int, raw=None):
    """The fixture's 4 lines re-homed onto ``order_id``."""
    raw = copy.deepcopy(raw or LINES_RAW)
    for r in raw["response"]["resources"]["data"]:
        r["purchase_order_id"] = order_id
    return parse_daysmart_order_item_page(raw)


ALL_IDS = sorted(o["id"] for o in LIST_RAW["response"]["resources"])


class TestLinesRideAlong:
    def test_tr1_every_order_on_every_page_gets_its_lines_pulled_every_time(self, db):
        daysmart = FakeDaysmart(pages={1: header_page()})
        first = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert first.created == 50
        assert sorted(daysmart.lines_fetched) == ALL_IDS
        assert (first.lines_synced, first.lines_failed) == (50, 0)

        daysmart.lines_fetched.clear()
        second = run_full_inbound_sync(db, daysmart)      # nothing changed on any header …
        db.commit()
        assert second.unchanged == 50
        assert sorted(daysmart.lines_fetched) == ALL_IDS  # … lines are pulled regardless
        assert second.lines_synced == 50

    def test_tr2_an_edited_line_with_an_untouched_header_is_picked_up(self, db):
        # Moses's case: editing a line's quantity in Daysmart changes NOTHING
        # on the header (not even item_count). No header-derived trigger can
        # see it; pulling lines on every pass can.
        daysmart = FakeDaysmart(pages={1: header_page()}, lines={791640: lines_for(791640)})
        run_full_inbound_sync(db, daysmart)
        db.commit()
        line = db.query(models.PurchaseOrderLine).filter_by(daysmart_item_id=3969381).one()
        assert line.daysmart_quantity_ordered == 240

        raw = copy.deepcopy(LINES_RAW)
        for r in raw["response"]["resources"]["data"]:
            if r["id"] == 3969381:
                r["quantity_ordered"] = 300
        daysmart.lines = {791640: lines_for(791640, raw)}
        summary = run_full_inbound_sync(db, daysmart)     # same header page
        db.commit()
        assert summary.unchanged == 50                    # headers: nothing to see
        line = db.query(models.PurchaseOrderLine).filter_by(daysmart_item_id=3969381).one()
        assert line.daysmart_quantity_ordered == 300      # lines: the edit landed
        assert line.quantity == "240"                     # what WE ordered is untouched

    def test_tr3_a_line_pull_failure_is_reported_per_order_and_nothing_is_queued(self, db):
        daysmart = FakeDaysmart(pages={1: header_page()},
                                lines={791640: DaysmartUnavailable("down")})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert summary.completed is True                  # headers are complete truth
        assert (summary.lines_synced, summary.lines_failed) == (49, 1)
        assert summary.line_failures == (
            LineFailure(daysmart_id=791640, error_code="DaysmartUnavailable", message="down"),)
        assert get_watermark(db) is not None              # a line hiccup never holds the bookmark

        daysmart.lines = {}                               # Daysmart is back
        daysmart.lines_fetched.clear()
        second = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert sorted(daysmart.lines_fetched) == ALL_IDS  # every order again — no memory, no queue
        assert second.line_failures == ()

    def test_tr4_a_header_page_failure_mid_cycle_still_reports_the_lines_pulled(self, db):
        daysmart = FakeDaysmart(pages={1: header_page(last_page=2),
                                       2: DaysmartUnavailable("down")})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert summary.completed is False
        assert summary.pages_applied == 1
        assert summary.lines_synced == 50                 # page 1's orders got their lines
        assert get_watermark(db) is None                  # the bookmark did not move

    def test_tr5_a_quarantined_order_gets_no_line_pull(self, db):
        raw = copy.deepcopy(LIST_RAW)
        raw["response"]["resources"][0]["total"] = "not money"   # blocking → skipped whole
        page = header_page(raw)
        assert len(page.orders) == 49 and page.issues
        daysmart = FakeDaysmart(pages={1: page})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert summary.created == 49
        assert 791640 not in daysmart.lines_fetched
        assert summary.lines_synced == 49


# ---------------------------------------------------------------------------
# RF — refresh ONE purchase order: header (detail surface) + lines
# ---------------------------------------------------------------------------

DETAIL_RAW = json.loads(
    (FIXTURES / "daysmart_order_detail_793009.json").read_text(encoding="utf-8"))


def _php(iso: str) -> dict:
    """A list-dialect ISO string as the detail dialect would render it."""
    from datetime import datetime, timezone
    utc = datetime.fromisoformat(iso).astimezone(timezone.utc)
    return {"date": utc.strftime("%Y-%m-%d %H:%M:%S.%f"), "timezone_type": 3, "timezone": "UTC"}


def detail_from_list_row(row: dict, **changes) -> dict:
    """The SAME order as the detail surface would answer it — every value
    re-spelled the detail way (PHP-UTC dates, bare-int status, null empties)."""
    r = {
        "id": row["id"], "index": row["index"], "supplier_id": row["supplier"]["id"],
        "supplier": {"id": row["supplier"]["id"], "name": row["supplier"]["name"]},
        "account_no": row["account_no"] or None,
        "order_date": _php(row["order_date"]),
        "create_at": _php(row["create_at"]), "update_at": _php(row["update_at"]),
        "ship_to_id": row["ship_to_id"], "bill_to_id": row["bill_to_id"],
        "sub_total": row["sub_total"] or None, "shipping": row["shipping"] or None,
        "tax": row["tax"] or None, "total": row["total"] or None,
        "payment_terms": row["payment_terms"] or None, "notes": row["notes"] or None,
        "status": row["status"]["id"], "status_label": row["status"]["label"],
        "order_name": row["order_name"] or None, "is_active": row["is_active"],
        "business_id": row["business_id"], "create_by": row["create_by"],
        "update_by": row["update_by"], "purchase_order_items": [],
    }
    r.update(changes)
    return {"response": {"resources": r, "message": {"code": 200}}}


ROW0 = LIST_RAW["response"]["resources"][0]      # order 791640, item_count 0


class TestRefreshOnePurchaseOrder:
    def test_rf1_header_and_lines_come_from_the_detail_surface(self, db):
        po = synced_po(db)                                       # 793009, bare header
        daysmart = FakeDaysmart(details={793009: DETAIL_RAW}, lines={793009: lines_page()})
        summary = refresh_purchase_order(db, po, daysmart)
        db.commit()
        assert summary.header_changed is True
        assert summary.lines.created == 4
        assert daysmart.details_fetched == [793009] and daysmart.lines_fetched == [793009]
        assert (po.daysmart_status, po.daysmart_status_label) == ("open", "Open")
        assert po.supplier_name == "Alfamedic"                   # the detail carries it
        assert (po.total, po.sub_total) == ("0.00", None)        # verbatim; null stays unknown
        assert po.order_date == "2026-08-28T07:59:37+00:00"      # the detail's instant, as sent
        assert po.item_count == 4                                # the line list's own total
        assert po.content_hash and po.last_seen_at
        assert len(po.lines) == 4

    def test_rf2_an_unchanged_order_is_unchanged_whichever_surface_says_so(self, db):
        # The two surfaces spell the same facts differently (+08:00 vs UTC,
        # "" vs null). A refresh must not churn the hash on unchanged data,
        # and the next list pull must agree that nothing changed.
        daysmart = FakeDaysmart(pages={1: header_page()},
                                details={791640: detail_from_list_row(ROW0)})
        run_full_inbound_sync(db, daysmart)
        db.commit()
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        before = (po.content_hash, po.order_date, po.updated_at)

        summary = refresh_purchase_order(db, po, daysmart)
        db.commit()
        assert summary.header_changed is False
        assert (po.content_hash, po.order_date, po.updated_at) == before   # spelling kept

        again = run_full_inbound_sync(db, daysmart)
        db.commit()
        assert again.unchanged == 50

    def test_rf3_a_real_change_on_the_detail_flows_through(self, db):
        daysmart = FakeDaysmart(
            pages={1: header_page()},
            details={791640: detail_from_list_row(ROW0, status=2, status_label="Submitted")})
        run_full_inbound_sync(db, daysmart)
        db.commit()
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        summary = refresh_purchase_order(db, po, daysmart)
        db.commit()
        assert summary.header_changed is True
        assert (po.daysmart_status_id, po.daysmart_status, po.daysmart_status_label) == (
            2, "submitted", "Submitted")

    def test_rf4_nothing_is_applied_when_the_line_pull_fails(self, db):
        daysmart = FakeDaysmart(
            pages={1: header_page()},
            details={791640: detail_from_list_row(ROW0, status=2, status_label="Submitted")},
            lines={791640: DaysmartUnavailable("down")})
        run_full_inbound_sync(db, daysmart)
        db.commit()
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        with pytest.raises(DaysmartUnavailable):
            refresh_purchase_order(db, po, daysmart)
        assert po.daysmart_status == "open"                      # NOT half-applied

    def test_rf5_an_unreadable_detail_is_a_named_failure(self, db):
        po = synced_po(db)
        broken = copy.deepcopy(DETAIL_RAW)
        del broken["response"]["resources"]["index"]
        daysmart = FakeDaysmart(details={793009: broken})
        with pytest.raises(DaysmartOrderUnreadable) as info:
            refresh_purchase_order(db, po, daysmart)
        assert [i.field_path for i in info.value.issues] == ["index"]
        assert daysmart.lines_fetched == []

    def test_rf6_the_detail_must_be_the_order_we_asked_for(self, db):
        po = synced_po(db, daysmart_id=791640)
        daysmart = FakeDaysmart(details={791640: DETAIL_RAW})    # answers with 793009
        with pytest.raises(DaysmartMalformedResponse):
            refresh_purchase_order(db, po, daysmart)

    def test_rf7_an_unsynced_po_cannot_be_refreshed(self, db):
        po = synced_po(db)
        po.daysmart_id = None
        with pytest.raises(ValueError):
            refresh_purchase_order(db, po, FakeDaysmart())

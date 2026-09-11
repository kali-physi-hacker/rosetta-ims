from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po-lines.db")

import copy
import json
from pathlib import Path

import models
from database import Base
from schemas.purchase_orders import parse_line_commands
from services.daysmart_service import (
    DaysmartOrderItemRef,
    DaysmartTimeout,
    DaysmartUnavailable,
)
ORDER_DETAIL = json.loads((Path(__file__).parent / "fixtures" / "purchase_orders"
                           / "daysmart_order_detail_793009.json").read_text(encoding="utf-8"))

from services.po_line_flow import (
    LineOperationRefused,
    add_purchase_order_lines,
    delete_purchase_order_line,
    update_purchase_order_line,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def synced_po(db):
    now = datetime.now(timezone.utc).isoformat()
    po = models.PurchaseOrder(
        sync_status="synced", daysmart_id=793009, daysmart_index=524,
        supplier_daysmart_id="supplier-one", supplier_name="Supplier",
        account_no="", order_date="2026-09-01T11:00:00+08:00",
        created_at=now, updated_at=now)
    db.add(po)
    db.flush()
    return po


def commands(*item_ids):
    parsed = parse_line_commands([{
        "description": f"Line {index}", "quantity": index + 1,
        "quantity_uom": "EA", "unit_price": "2.50",
        "daysmart_inventory_item_id": item_id,
    } for index, item_id in enumerate(item_ids)])
    assert parsed.lines is not None, parsed.issues
    return parsed.lines


class FakeDaysmart:
    def __init__(self, fetches, saves=(), lines=None, deletes=()):
        self.fetches = list(fetches)
        self.saves = list(saves)
        self.payloads = []
        self.lines = lines or {}          # order id → page | Exception (default: empty page)
        self.deletes = list(deletes)
        self.deleted = []

    def fetch_order_lines(self, order_id):
        from schemas.purchase_orders import parse_daysmart_order_item_page
        action = self.lines.get(order_id)
        if isinstance(action, Exception):
            raise action
        if action is not None:
            return action
        return parse_daysmart_order_item_page({"response": {"resources": {
            "current_page": 1, "data": [], "last_page": 1, "per_page": 50, "total": 0},
            "message": {"code": 200}}})

    def delete_order_item(self, item_id):
        self.deleted.append(item_id)
        if self.deletes:
            action = self.deletes.pop(0)
            if isinstance(action, Exception):
                raise action

    # The pull after a line write reads the whole PO: header, lines, receipts.
    def get_order(self, order_id):
        from schemas.purchase_orders import parse_daysmart_created_order
        self.details_fetched = getattr(self, "details_fetched", []) + [order_id]
        return parse_daysmart_created_order(copy.deepcopy(ORDER_DETAIL))

    def fetch_receipts(self, page=1, purchase_order_id=None):
        from schemas.purchase_orders import parse_daysmart_receipt_page
        return parse_daysmart_receipt_page({"response": {"meta": [], "resources": [], "message": {"code": 200}}})

    def fetch_receipt(self, receipt_id):
        raise AssertionError("no local receipts in these tests")

    def fetch_order_items(self, order_id):
        assert order_id == 793009
        action = self.fetches.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def save_order_items(self, payloads):
        self.payloads.append(payloads)
        if self.saves:
            action = self.saves.pop(0)
            if isinstance(action, Exception):
                raise action


def ref(line_id, item_id):
    return DaysmartOrderItemRef(
        daysmart_item_id=line_id, inventory_item_id=item_id)


def test_each_acked_line_gets_its_own_backfilled_id(db):
    po = synced_po(db)
    daysmart = FakeDaysmart(fetches=[
        [],
        [ref(1001, "DIT-same")],
        [ref(1001, "DIT-same"), ref(1002, "DIT-same")],
    ])

    results = add_purchase_order_lines(
        db, po, commands("DIT-same", "DIT-same"), daysmart)
    db.commit()

    assert [result.outcome for result in results] == ["synced", "synced"]
    assert [result.line.daysmart_item_id for result in results] == [1001, 1002]
    assert all(result.line.sync_status == "synced" for result in results)
    assert len(daysmart.payloads) == 2
    assert daysmart.payloads[0][0]["purchase_order_id"] == "793009"


def test_idless_line_is_stored_pending_and_never_sent(db):
    po = synced_po(db)
    daysmart = FakeDaysmart(fetches=[[]])
    result = add_purchase_order_lines(db, po, commands(None), daysmart)[0]
    db.commit()

    assert result.outcome == "pending"
    assert result.line.sync_status == "pending"
    assert daysmart.payloads == []


def test_timeout_is_needs_review_and_never_refetched_or_resent(db):
    po = synced_po(db)
    daysmart = FakeDaysmart(fetches=[[]], saves=[DaysmartTimeout("ambiguous")])
    result = add_purchase_order_lines(db, po, commands("DIT-one"), daysmart)[0]
    db.commit()

    assert result.outcome == "needs_review"
    assert result.line.sync_status == "needs_review"
    assert daysmart.fetches == []
    assert [(a.step, a.outcome) for a in po.sync_attempts] == [
        ("add_line", "timeout")]


def test_baseline_failure_stores_line_but_does_not_send(db):
    po = synced_po(db)
    daysmart = FakeDaysmart(fetches=[DaysmartUnavailable("down")])
    result = add_purchase_order_lines(db, po, commands("DIT-one"), daysmart)[0]
    db.commit()

    assert result.outcome == "sync_failed"
    assert result.line.sync_status == "sync_failed"
    assert daysmart.payloads == []


def test_ambiguous_backfill_never_guesses(db):
    po = synced_po(db)
    daysmart = FakeDaysmart(fetches=[[], [
        ref(1001, "DIT-one"), ref(1002, "DIT-one")]])
    result = add_purchase_order_lines(db, po, commands("DIT-one"), daysmart)[0]
    db.commit()

    assert result.outcome == "needs_review"
    assert result.line.daysmart_item_id is None
    assert "2 new matching rows" in result.message


# ---------------------------------------------------------------------------
# edit / delete — the line writes beyond add
# ---------------------------------------------------------------------------


def daysmart_line(db, po, item_id="DIT-1", line_id=3924916):
    line = models.PurchaseOrderLine(
        description="Line", quantity="2", quantity_uom="EA", unit_price_raw="2.50",
        notes="", sync_status="synced", daysmart_item_id=line_id,
        daysmart_inventory_item_id=item_id, daysmart_is_active=True)
    po.lines.append(line)
    db.add(line)
    db.flush()
    return line


def attempts(db, line):
    return [(a.step, a.outcome) for a in
            db.query(models.PoSyncAttempt).filter_by(line_id=line.id).order_by(models.PoSyncAttempt.id)]


def test_update_timeout_is_retryable_and_keeps_the_local_edit(db):
    po = synced_po(db)
    line = daysmart_line(db, po)
    daysmart = FakeDaysmart(fetches=[], saves=[DaysmartTimeout("slow")])
    result = update_purchase_order_line(db, po, line, commands("DIT-1")[0], daysmart)
    assert (result.outcome, result.error_code) == ("sync_failed", "DaysmartTimeout")
    assert line.quantity == "1" and line.sync_status == "sync_failed"    # the edit is kept, retryable
    assert daysmart.payloads[0][0]["purchase_order_item_id"] == 3924916  # an UPDATE
    assert attempts(db, line) == [("update_line", "timeout")]


def test_update_mirror_failure_does_not_undo_the_write(db):
    po = synced_po(db)
    line = daysmart_line(db, po)
    daysmart = FakeDaysmart(fetches=[], lines={793009: DaysmartUnavailable("down")})
    result = update_purchase_order_line(db, po, line, commands("DIT-1")[0], daysmart)
    assert result.outcome == "synced"
    assert result.mirror_error.startswith("DaysmartUnavailable")
    assert line.sync_status == "synced"
    assert attempts(db, line) == [("update_line", "success"), ("fetch_lines", "daysmart_down")]


def test_update_never_changes_the_inventory_item(db):
    po = synced_po(db)
    line = daysmart_line(db, po, item_id="DIT-1")
    with pytest.raises(LineOperationRefused) as info:
        update_purchase_order_line(db, po, line, commands("DIT-2")[0], FakeDaysmart(fetches=[]))
    assert info.value.code == "LINE_IDENTITY_IMMUTABLE"
    assert line.daysmart_inventory_item_id == "DIT-1" and line.quantity == "2"


def test_delete_timeout_needs_review_and_an_inactive_line_finishes_locally(db):
    po = synced_po(db)
    line = daysmart_line(db, po)
    daysmart = FakeDaysmart(fetches=[], deletes=[DaysmartTimeout("slow")])
    result = delete_purchase_order_line(db, po, line, daysmart)
    assert (result.outcome, line.sync_status) == ("needs_review", "needs_review")
    assert attempts(db, line) == [("delete_line", "timeout")]
    # A later refresh showed Daysmart had processed it after all:
    line.daysmart_is_active = False
    result = delete_purchase_order_line(db, po, line, daysmart)
    assert (result.outcome, line.sync_status) == ("deleted", "deleted")
    assert daysmart.deleted == [3924916]                                 # exactly one call


def test_delete_of_a_never_sent_line_removes_it_without_a_call(db):
    po = synced_po(db)
    results = add_purchase_order_lines(db, po, commands(None), FakeDaysmart(fetches=[[]]))
    line = results[0].line
    assert line.sync_status == "pending"
    daysmart = FakeDaysmart(fetches=[])
    result = delete_purchase_order_line(db, po, line, daysmart)
    db.flush()
    assert (result.outcome, result.line) == ("deleted", None)
    assert daysmart.deleted == []
    assert po.lines == []


def test_every_line_write_ends_with_a_pull_of_the_whole_po(db):
    # Moses's rule: everything attached to a PO comes with the PO — so after
    # an add, an edit or a delete, the header (item_count…) and the line
    # mirror are current without anyone calling refresh.
    po = synced_po(db)
    daysmart = FakeDaysmart(fetches=[[], [ref(3924916, "DIT-1")]])
    from schemas.purchase_orders import parse_daysmart_order_item_page
    daysmart.lines = {793009: parse_daysmart_order_item_page({"response": {"resources": {
        "current_page": 1, "last_page": 1, "per_page": 50, "total": 1,
        "data": [{"id": 3924916, "item_id": "DIT-1", "purchase_order_id": 793009,
                  "item_name": "Line", "quantity_ordered": 1, "quantity_received": 0,
                  "back_ordered": 0, "price": 2.5, "line_total": 2.5, "notes": "",
                  "is_active": True, "container_type": "EA"}]}, "message": {"code": 200}}})}
    results = add_purchase_order_lines(db, po, commands("DIT-1"), daysmart)
    db.commit()
    assert results[0].outcome == "synced" and results[0].mirror_error is None
    assert daysmart.details_fetched == [793009]                 # the header was pulled …
    assert po.item_count == 1                                   # … and is current
    assert results[0].line.daysmart_quantity_ordered == 1       # the line mirror too
    assert po.daysmart_status == "open"

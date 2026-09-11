"""HU/HD-groups — the PO header after creation: update and delete.

Wire (PO-update.har): ``PUT order/{id}`` takes ``{account_no, order_date:
<epoch>, ship_to_id, bill_to_id, notes}`` and echoes the order in the
create dialect; ``DELETE order/{id}`` is SOFT (echo: is_active false,
notes "[removed]"). Rules pinned here:

- a PO in Daysmart is written THROUGH: Daysmart first, then its echo is
  mirrored (the header columns are Daysmart-owned mirrors once synced);
  a failed write changes nothing locally and says why;
- a local draft is edited locally (it is what will be sent);
- submitted/closed orders are refused locally (Daysmart blocks the edit);
- delete keeps the local row as 'deleted' with the echo mirrored; a draft
  is cancelled, not deleted; a timed-out delete needs review.

Interfaces under test:

    schemas/purchase_orders: parse_update_command, build_daysmart_update_payload
    services/daysmart_service: update_order(id, payload), delete_order(id)
    services/po_header_flow: update_purchase_order, delete_purchase_order,
                             HeaderOpResult, HeaderOperationRefused
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime, timezone

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_header.db")

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from schemas.purchase_orders import (
    IssueCode,
    build_daysmart_update_payload,
    parse_daysmart_created_order,
    parse_update_command,
)
from services.daysmart_service import (
    DaysmartRefused,
    DaysmartTimeout,
    DaysmartUnavailable,
)
from services.po_header_flow import (
    HeaderOperationRefused,
    delete_purchase_order,
    update_purchase_order,
)

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
UPDATE_REQUEST = json.loads((FIXTURES / "daysmart_order_update_request.json").read_text(encoding="utf-8"))
UPDATE_RESPONSE = json.loads((FIXTURES / "daysmart_order_update_response.json").read_text(encoding="utf-8"))
DELETE_RESPONSE = json.loads((FIXTURES / "daysmart_order_delete_response.json").read_text(encoding="utf-8"))

BODY = {"account_no": "-", "order_date": "2026-09-01T18:42:39+08:00",
        "ship_to_id": 861040, "bill_to_id": 861040, "notes": "Test PO creation"}


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def po_row(db, *, daysmart_id=794868, status="open", sync_status="synced"):
    po = models.PurchaseOrder(
        supplier_daysmart_id="3cd1ace0-ddd6-4a", supplier_name="Alfamedic",
        account_no="", order_date="2026-08-29T11:00:00+08:00", ship_to_id=861040, bill_to_id=861040,
        notes="before", sync_status=sync_status, daysmart_id=daysmart_id, daysmart_index=526,
        daysmart_status=status, daysmart_status_id=1, item_count=0,
        created_at="x", updated_at="x")
    db.add(po)
    db.commit()
    return po


def command(**changes):
    parsed = parse_update_command({**BODY, **changes})
    assert parsed.command is not None, parsed.issues
    return parsed.command


class FakeDaysmart:
    def __init__(self, update=None, delete=None):
        self.update = update          # None = echo the fixture, or an Exception
        self.delete = delete
        self.updates = []
        self.deletes = []

    def update_order(self, daysmart_order_id, payload):
        self.updates.append((daysmart_order_id, payload))
        if isinstance(self.update, Exception):
            raise self.update
        return parse_daysmart_created_order(copy.deepcopy(UPDATE_RESPONSE))

    def delete_order(self, daysmart_order_id):
        self.deletes.append(daysmart_order_id)
        if isinstance(self.delete, Exception):
            raise self.delete
        return parse_daysmart_created_order(copy.deepcopy(DELETE_RESPONSE))


class TestUpdateContract:
    def test_hu1_payload_is_the_captured_request_byte_for_byte(self):
        payload = build_daysmart_update_payload(command(), ship_to_id=861040, bill_to_id=861040)
        assert payload == UPDATE_REQUEST
        assert payload["order_date"] == 1788259359 and isinstance(payload["order_date"], int)

    def test_hu2_command_validation(self):
        assert parse_update_command({**BODY, "order_date": "2026-09-01"}).command is None   # no offset
        bad = parse_update_command({**BODY, "supplier_name": "x"})
        assert bad.command is None and bad.issues[0].field_path == "supplier_name"
        assert parse_update_command("nope").command is None
        minimal = parse_update_command({"order_date": "2026-09-01T18:42:39+08:00"}).command
        assert (minimal.account_no, minimal.notes, minimal.ship_to_id) == ("", "", None)


class TestUpdateFlow:
    def test_hu3_write_through_then_mirror_the_echo(self, db):
        po = po_row(db)
        daysmart = FakeDaysmart()
        result = update_purchase_order(db, po, command(), daysmart)
        db.commit()
        assert result.outcome == "synced"
        assert daysmart.updates == [(794868, UPDATE_REQUEST)]
        assert (po.notes, po.account_no) == ("Test PO creation", "-")           # from the echo
        assert po.order_date == "2026-09-01T10:42:39+00:00"                     # the echo's instant, as sent
        assert po.content_hash is not None
        assert [(a.step, a.outcome) for a in po.sync_attempts] == [("update_order", "success")]

    def test_hu4_null_addresses_keep_the_pos_own(self, db):
        po = po_row(db)
        po.ship_to_id, po.bill_to_id = 111, 222
        daysmart = FakeDaysmart()
        update_purchase_order(db, po, command(ship_to_id=None, bill_to_id=None), daysmart)
        assert daysmart.updates[0][1]["ship_to_id"] == 111 and daysmart.updates[0][1]["bill_to_id"] == 222

    def test_hu5_a_draft_is_edited_locally_only(self, db):
        po = po_row(db, daysmart_id=None, status=None, sync_status="draft")
        daysmart = FakeDaysmart()
        result = update_purchase_order(db, po, command(), daysmart)
        assert result.outcome == "draft" and daysmart.updates == []
        assert po.notes == "Test PO creation" and po.order_date == "2026-09-01T18:42:39+08:00"

    def test_hu6_failures_change_nothing_locally_and_say_why(self, db):
        po = po_row(db)
        result = update_purchase_order(db, po, command(), FakeDaysmart(update=DaysmartRefused("submitted")))
        assert (result.outcome, result.error_code) == ("needs_review", "DaysmartRefused")
        assert po.notes == "before" and po.sync_status == "synced"
        result = update_purchase_order(db, po, command(), FakeDaysmart(update=DaysmartTimeout("slow")))
        assert (result.outcome, result.error_code) == ("sync_failed", "DaysmartTimeout")   # resend-safe
        assert po.notes == "before"

    def test_hu7_submitted_or_closed_orders_are_refused_locally(self, db):
        for status in ("submitted", "closed"):
            po = po_row(db, daysmart_id=1000 + len(status), status=status)
            with pytest.raises(HeaderOperationRefused) as info:
                update_purchase_order(db, po, command(), FakeDaysmart())
            assert info.value.code == "PO_NOT_OPEN"
        cancelled = po_row(db, daysmart_id=None, status=None, sync_status="cancelled")
        with pytest.raises(HeaderOperationRefused) as info:
            update_purchase_order(db, cancelled, command(), FakeDaysmart())
        assert info.value.code == "PO_NOT_EDITABLE"


class TestDeleteFlow:
    def test_hd1_soft_delete_mirrors_the_echo_and_keeps_the_row(self, db):
        po = po_row(db)
        daysmart = FakeDaysmart()
        result = delete_purchase_order(db, po, daysmart)
        db.commit()
        assert result.outcome == "deleted" and daysmart.deletes == [794868]
        assert (po.sync_status, po.is_active, po.notes) == ("deleted", False, "[removed]")
        assert db.query(models.PurchaseOrder).count() == 1
        assert delete_purchase_order(db, po, daysmart).message == "already deleted"
        assert daysmart.deletes == [794868]                                      # no second call

    def test_hd2_a_draft_is_cancelled_not_deleted(self, db):
        po = po_row(db, daysmart_id=None, status=None, sync_status="draft")
        with pytest.raises(HeaderOperationRefused) as info:
            delete_purchase_order(db, po, FakeDaysmart())
        assert info.value.code == "PO_NOT_SYNCED"

    def test_hd3_failures_are_reported_and_the_po_stays(self, db):
        po = po_row(db)
        result = delete_purchase_order(db, po, FakeDaysmart(delete=DaysmartTimeout("slow")))
        assert result.outcome == "needs_review" and "refresh" in result.message
        result = delete_purchase_order(db, po, FakeDaysmart(delete=DaysmartUnavailable("down")))
        assert (result.outcome, result.error_code) == ("sync_failed", "DaysmartUnavailable")
        result = delete_purchase_order(db, po, FakeDaysmart(delete=DaysmartRefused("closed")))
        assert result.outcome == "needs_review"
        assert po.sync_status == "synced" and po.is_active is True

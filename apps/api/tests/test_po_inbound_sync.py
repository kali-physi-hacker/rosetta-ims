"""Phase 3a — inbound apply: fetched Daysmart orders land in our database.

TDD: defines ``services/po_inbound_sync.py``, which does not exist yet.

    apply_order_page(db, page: DaysmartOrderPageParseResult) -> ApplySummary
        .created    — orders we had never seen (inserted, sync_status 'synced')
        .updated    — known orders whose Daysmart-owned content changed
        .unchanged  — known orders whose content hash matched (skip the write)
        .issues_recorded — contract issues persisted for the OPS screen

Rules pinned (each one a design decision from the CIS-09 discussions):

- Match on ``daysmart_id`` ONLY. Never on index, never on content.
- Field ownership is the reconciliation policy: fetches overwrite
  Daysmart-owned columns and NEVER touch Rosetta-owned ones (``po_uuid``,
  ``sync_status``, ``created_by_user_id``).
- Change detection by CONTENT HASH over the Daysmart-owned fields (Moses's
  hashing idea, applied where equality is the right question): same hash →
  no write, just ``last_seen_at``; different → apply.
- Money verbatim: parsed ``None`` (Daysmart sent "") stays NULL, never "0.00".
- Contract issues from the page are persisted (context 'fetch') — one bad
  order is recorded AND skipped while its 49 page-mates apply.
- Applying one page never advances the watermark; only a complete cycle may.
- Absence is NOT deletion: a local PO missing from the fetched page is left
  completely alone.
- Idempotent by construction: applying the same page twice converges
  (second pass: all unchanged). The initial 468-order import is just this
  function with an empty watermark, run to convergence.

The service STAGES changes; the caller owns the commit (repo convention).
NOTE: requires a new ``content_hash`` column on PurchaseOrder.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_sync.db")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from schemas.purchase_orders import parse_daysmart_order_page
from services.po_inbound_sync import apply_order_page
from services.po_sync_state import get_watermark

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture()
def raw_page() -> dict:
    return json.loads((FIXTURES / "daysmart_order_list_page1.json").read_text(encoding="utf-8"))


def parsed(raw_page):
    return parse_daysmart_order_page(raw_page)


class TestFirstContact:
    def test_i1_unknown_orders_are_created_as_synced(self, db, raw_page):
        summary = apply_order_page(db, parsed(raw_page))
        db.commit()
        assert summary.created == 50
        assert summary.updated == 0
        assert summary.unchanged == 0
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        assert po.sync_status == "synced"       # it exists in Daysmart — that IS synced
        assert po.po_uuid and len(po.po_uuid) == 36
        assert po.daysmart_index == 519
        assert po.supplier_daysmart_id == "3cd1ace0-ddd6-4a"
        assert po.daysmart_status == "open"
        assert po.daysmart_status_id == 1
        assert po.last_seen_at is not None
        assert po.daysmart_updated_at == "2026-08-26T13:42:47+08:00"

    def test_i1b_money_verbatim_none_stays_null(self, db, raw_page):
        apply_order_page(db, parsed(raw_page))
        db.commit()
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        assert po.sub_total is None      # Daysmart sent "" — unknown
        assert po.total == "0.00"        # known zero
        other = db.query(models.PurchaseOrder).filter_by(daysmart_id=787439).one()
        assert other.sub_total == "2959.20"

    def test_i6_single_page_apply_does_not_advance_watermark(self, db, raw_page):
        apply_order_page(db, parsed(raw_page))
        db.commit()
        assert get_watermark(db) is None


class TestIdempotence:
    def test_i3_second_apply_of_same_page_is_all_unchanged(self, db, raw_page):
        apply_order_page(db, parsed(raw_page))
        db.commit()
        summary = apply_order_page(db, parsed(raw_page))
        db.commit()
        assert summary.created == 0
        assert summary.updated == 0
        assert summary.unchanged == 50
        assert db.query(models.PurchaseOrder).count() == 50

    def test_i3b_unchanged_still_bumps_last_seen(self, db, raw_page):
        apply_order_page(db, parsed(raw_page))
        db.commit()
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        first_seen = po.last_seen_at
        po.last_seen_at = "2020-01-01T00:00:00+08:00"  # simulate an old scan
        db.commit()
        apply_order_page(db, parsed(raw_page))
        db.commit()
        assert po.last_seen_at != "2020-01-01T00:00:00+08:00"
        assert first_seen is not None

    def test_i4_content_hash_changes_only_with_daysmart_owned_content(self, db, raw_page):
        apply_order_page(db, parsed(raw_page))
        db.commit()
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=789502).one()
        original_hash = po.content_hash
        assert original_hash

        # Same content fetched again → same hash, counted unchanged.
        apply_order_page(db, parsed(raw_page))
        db.commit()
        assert po.content_hash == original_hash

        # Status flips in Daysmart → hash changes, row counted updated.
        mutated = copy.deepcopy(raw_page)
        for raw in mutated["response"]["resources"]:
            if raw["id"] == 789502:
                raw["status"] = {"id": 4, "label": "Closed"}
                raw["update_at"] = "2026-08-27T09:00:00+08:00"
        summary = apply_order_page(db, parse_daysmart_order_page(mutated))
        db.commit()
        assert summary.updated == 1
        assert summary.unchanged == 49
        assert po.content_hash != original_hash
        assert po.daysmart_status == "closed"


class TestFieldOwnership:
    def test_i2_fetch_updates_daysmart_owned_never_rosetta_owned(self, db, raw_page):
        # A local PO we created ourselves, mid-sync, already linked.
        po = models.PurchaseOrder(
            daysmart_id=789502, sync_status="submitting",
            supplier_daysmart_id="9366bbdb-2554-46", supplier_name="Star Medical Supplies LTD",
            order_date="2026-08-24T15:25:54+08:00", notes="our local draft note",
            created_by_user_id=None,
            created_at="2026-08-24T15:00:00+08:00", updated_at="2026-08-24T15:00:00+08:00",
        )
        db.add(po)
        db.commit()
        our_uuid = po.po_uuid

        apply_order_page(db, parsed(raw_page))
        db.commit()

        # Daysmart-owned: overwritten from the fetch.
        assert po.daysmart_status == "open"
        assert po.total == "690.00"
        assert po.notes == "$148.00/box; $138.00/box for >=5boxes"
        assert po.daysmart_index == 518
        # Rosetta-owned: untouched. Resolution owns status transitions —
        # a fetch never flips sync_status.
        assert po.po_uuid == our_uuid
        assert po.sync_status == "submitting"

    def test_i8_absence_is_not_deletion(self, db, raw_page):
        # A PO we synced long ago that no longer appears in the list.
        ghost = models.PurchaseOrder(
            daysmart_id=500000, sync_status="synced",
            supplier_daysmart_id="x", supplier_name="X",
            order_date="2026-01-01T00:00:00+08:00",
            last_seen_at="2026-06-01T00:00:00+08:00", is_active=True,
            created_at="2026-01-01T00:00:00+08:00", updated_at="2026-01-01T00:00:00+08:00",
        )
        db.add(ghost)
        db.commit()
        apply_order_page(db, parsed(raw_page))
        db.commit()
        assert ghost.is_active is True
        assert ghost.sync_status == "synced"
        assert ghost.last_seen_at == "2026-06-01T00:00:00+08:00"  # not bumped, not touched


class TestIssuesPersisted:
    def test_i5_contract_issues_land_in_the_review_table(self, db, raw_page):
        mangled = copy.deepcopy(raw_page)
        mangled["response"]["resources"][3]["total"] = "not-money"
        bad_id = mangled["response"]["resources"][3]["id"]
        summary = apply_order_page(db, parse_daysmart_order_page(mangled))
        db.commit()
        assert summary.created == 49
        assert summary.issues_recorded == 1
        issue = db.query(models.PoValidationIssueRecord).one()
        assert issue.code == "PO_MONEY_UNPARSEABLE"
        assert issue.daysmart_id == bad_id
        assert issue.payload_context == "fetch"
        assert issue.resolution_status == "open"
        # The bad order was skipped, not half-written.
        assert db.query(models.PurchaseOrder).filter_by(daysmart_id=bad_id).count() == 0

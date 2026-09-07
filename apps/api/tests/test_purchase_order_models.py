"""Phase 2 (persistence) — the purchase-order tables and their invariants.

TDD: these tests define models that do not exist yet. They pin STRUCTURE,
not flow — no create-PO business logic is exercised here.

Interface under test (to be implemented):

    models/purchase_orders.py
        PurchaseOrder          — local copy of every PO; sync lifecycle
        PurchaseOrderLine      — per-line state (create is header-then-lines)
        PoSyncAttempt          — append-only log the retry logic reads
        PoValidationIssueRecord— persisted contract issues (OPS review feed)
        PoSyncWatermark        — the single-row bookmark

    services/po_sync_state.py
        get_watermark(db) -> str | None
        advance_watermark(db, value) -> str    # never moves backwards
        assign_daysmart_identity(po, daysmart_id, daysmart_index)
                                               # write-once, refuses rewrites

Invariants pinned here, each traceable to a design decision:
- A draft PO exists with NO Daysmart identity (daysmart_id nullable).
- daysmart_id is unique when present; many drafts (NULLs) may coexist.
- Money is stored verbatim as nullable strings: None (Daysmart sent ""),
  never collapses into "0.00" and vice versa.
- A line's raw price of "0.00" is a legal stored value (free goods) and
  display_cost is an independent column — raw is never overwritten.
- The watermark only moves forward.
- Daysmart identity is write-once: once a PO is linked, relinking to a
  different Daysmart order is refused (that is a human decision, not code).
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_models.db")

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from services.po_sync_state import (
    DaysmartIdentityConflict,
    advance_watermark,
    assign_daysmart_identity,
    get_watermark,
)


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def make_po(**overrides):
    """A minimal valid draft PO; overrides let each test vary one fact."""
    fields = dict(
        supplier_daysmart_id="3cd1ace0-ddd6-4a",
        supplier_name="Alfamedic",
        order_date="2026-08-26T13:42:39+08:00",
        created_at="2026-08-26T13:42:00+08:00",
        updated_at="2026-08-26T13:42:00+08:00",
    )
    fields.update(overrides)
    return models.PurchaseOrder(**fields)


# ---------------------------------------------------------------------------
# PurchaseOrder — identity and lifecycle
# ---------------------------------------------------------------------------


class TestPurchaseOrderIdentity:
    def test_p1_draft_defaults(self, db):
        po = make_po()
        db.add(po)
        db.commit()
        assert po.id is not None
        assert po.po_uuid and len(po.po_uuid) == 36  # generated, not caller-supplied
        assert po.sync_status == "draft"
        assert po.daysmart_id is None
        assert po.daysmart_index is None

    def test_p2_po_uuid_unique(self, db):
        first = make_po()
        db.add(first)
        db.commit()
        clash = make_po()
        clash.po_uuid = first.po_uuid
        db.add(clash)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_p3_daysmart_id_unique_when_set_but_many_drafts_allowed(self, db):
        # Two unsynced drafts (daysmart_id NULL) must coexist.
        db.add(make_po())
        db.add(make_po())
        db.commit()
        # A synced order's daysmart_id is unique.
        synced = make_po(daysmart_id=789502, sync_status="synced")
        db.add(synced)
        db.commit()
        duplicate = make_po(daysmart_id=789502)
        db.add(duplicate)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_p10_daysmart_identity_is_write_once(self, db):
        po = make_po()
        db.add(po)
        db.commit()
        assign_daysmart_identity(po, daysmart_id=789502, daysmart_index=518)
        assert po.daysmart_id == 789502
        assert po.daysmart_index == 518
        # Re-assigning the SAME identity is a harmless no-op (idempotent link).
        assign_daysmart_identity(po, daysmart_id=789502, daysmart_index=518)
        # Re-assigning a DIFFERENT identity is refused — relinking a PO to
        # another Daysmart order is a human decision, never a silent write.
        with pytest.raises(DaysmartIdentityConflict):
            assign_daysmart_identity(po, daysmart_id=791640, daysmart_index=519)
        assert po.daysmart_id == 789502


class TestPurchaseOrderMoney:
    def test_p4_money_verbatim_none_and_zero_stay_distinct(self, db):
        po = make_po(sub_total=None, total="0.00", shipping=None, tax="0.00")
        db.add(po)
        db.commit()
        db.expire_all()
        reloaded = db.query(models.PurchaseOrder).filter_by(id=po.id).one()
        assert reloaded.sub_total is None       # Daysmart sent "" — unknown
        assert reloaded.total == "0.00"          # Daysmart sent zero — known
        assert reloaded.sub_total != reloaded.total

    def test_p11_timestamps_are_iso_strings(self, db):
        po = make_po()
        db.add(po)
        db.commit()
        assert isinstance(po.created_at, str)
        assert "T" in po.created_at  # ISO form, repo convention


# ---------------------------------------------------------------------------
# PurchaseOrderLine — per-line sync state, raw price kept raw
# ---------------------------------------------------------------------------


class TestPurchaseOrderLines:
    def test_p5_lines_belong_to_po_with_pending_default(self, db):
        po = make_po()
        po.lines = [
            models.PurchaseOrderLine(
                description="Douxo S3 Pyo Shampoo 200ml",
                quantity="12", quantity_uom="BOTTLE",
                unit_price_raw="96.00",
            ),
            models.PurchaseOrderLine(
                description="Canaural Ear Drops",
                quantity="5", quantity_uom="BOTTLE",
                unit_price_raw="650.00",
            ),
        ]
        db.add(po)
        db.commit()
        assert len(po.lines) == 2
        assert all(line.line_uuid and len(line.line_uuid) == 36 for line in po.lines)
        assert all(line.sync_status == "pending" for line in po.lines)
        assert all(line.daysmart_item_id is None for line in po.lines)
        assert po.lines[0].purchase_order_id == po.id

    def test_p6_zero_raw_price_is_legal_and_display_cost_is_separate(self, db):
        # Free goods ("Buy 1 Box L4 - Get 1 Box DHPPi Free") are genuinely $0.
        po = make_po()
        po.lines = [models.PurchaseOrderLine(
            description="DHPPi (free with L4)",
            quantity="1", quantity_uom="BOX",
            unit_price_raw="0.00",
            display_cost="490.00",  # derived elsewhere; never overwrites raw
        )]
        db.add(po)
        db.commit()
        db.expire_all()
        line = db.query(models.PurchaseOrderLine).one()
        assert line.unit_price_raw == "0.00"
        assert line.display_cost == "490.00"


class TestReceiptItemIdentity:
    def test_generated_daysmart_receipt_item_id_is_unique_when_set(self, db):
        po = make_po(daysmart_id=793009, sync_status="synced")
        receipt = models.PoReceipt(
            purchase_order=po, receipt_date="2026-09-01T11:00:00+00:00",
            created_at="x", updated_at="x")
        receipt.items = [models.PoReceiptItem(
            daysmart_receipt_item_id=3936614,
            quantity_received="1", quantity_in_stock="1", amount="1")]
        db.add(receipt)
        db.commit()

        other = models.PoReceipt(
            purchase_order=po, receipt_date="2026-09-01T11:00:00+00:00",
            created_at="x", updated_at="x")
        other.items = [models.PoReceiptItem(
            daysmart_receipt_item_id=3936614,
            quantity_received="1", quantity_in_stock="1", amount="1")]
        db.add(other)
        with pytest.raises(IntegrityError):
            db.commit()


# ---------------------------------------------------------------------------
# PoSyncAttempt — the append-only log retry-by-error-type reads
# ---------------------------------------------------------------------------


class TestSyncAttempts:
    def test_p7_attempts_accumulate_per_step_with_outcomes(self, db):
        po = make_po(sync_status="sync_failed")
        db.add(po)
        db.commit()
        db.add_all([
            models.PoSyncAttempt(
                purchase_order_id=po.id, step="create_header",
                outcome="timeout", error_code="DAYSMART_TIMEOUT",
                error_detail="no response after 30s",
                started_at="2026-08-28T10:00:00+08:00",
                finished_at="2026-08-28T10:00:30+08:00",
            ),
            models.PoSyncAttempt(
                purchase_order_id=po.id, step="snapshot_diff",
                outcome="success",
                started_at="2026-08-28T10:00:31+08:00",
                finished_at="2026-08-28T10:00:32+08:00",
            ),
        ])
        db.commit()
        attempts = (db.query(models.PoSyncAttempt)
                    .filter_by(purchase_order_id=po.id)
                    .order_by(models.PoSyncAttempt.id).all())
        assert [a.step for a in attempts] == ["create_header", "snapshot_diff"]
        assert attempts[0].outcome == "timeout"
        assert attempts[0].error_code == "DAYSMART_TIMEOUT"
        assert attempts[1].line_id is None  # header-level attempt

    def test_p7b_line_level_attempt_references_its_line(self, db):
        po = make_po()
        line = models.PurchaseOrderLine(
            description="x", quantity="1", quantity_uom="UNIT", unit_price_raw="1.00")
        po.lines = [line]
        db.add(po)
        db.commit()
        db.add(models.PoSyncAttempt(
            purchase_order_id=po.id, line_id=line.id, step="add_line",
            outcome="daysmart_down", error_code="DAYSMART_UNAVAILABLE",
            started_at="2026-08-28T10:01:00+08:00",
            finished_at="2026-08-28T10:01:05+08:00",
        ))
        db.commit()
        attempt = db.query(models.PoSyncAttempt).filter_by(step="add_line").one()
        assert attempt.line_id == line.id


# ---------------------------------------------------------------------------
# PoValidationIssueRecord — persisted contract issues (OPS review feed)
# ---------------------------------------------------------------------------


class TestValidationIssueRecords:
    def test_p8_issue_row_persists_evidence_and_defaults_open(self, db):
        db.add(models.PoValidationIssueRecord(
            code="PO_STATUS_UNKNOWN",
            field_path="status.id",
            raw_value="3",
            expected="one of [1, 4]",
            daysmart_id=789502,
            payload_context="fetch",
            created_at="2026-08-28T10:00:00+08:00",
        ))
        db.commit()
        issue = db.query(models.PoValidationIssueRecord).one()
        assert issue.resolution_status == "open"
        assert issue.purchase_order_id is None  # not linkable ≠ not recordable
        assert issue.raw_value == "3"

    def test_p8b_issue_can_link_to_a_local_po(self, db):
        po = make_po()
        db.add(po)
        db.commit()
        db.add(models.PoValidationIssueRecord(
            code="PO_MONEY_UNPARSEABLE",
            field_path="total",
            raw_value="abc",
            expected="decimal string",
            purchase_order_id=po.id,
            payload_context="create_response",
            created_at="2026-08-28T10:00:00+08:00",
        ))
        db.commit()
        assert db.query(models.PoValidationIssueRecord).one().purchase_order_id == po.id


# ---------------------------------------------------------------------------
# Watermark — the bookmark only moves forward
# ---------------------------------------------------------------------------


class TestWatermark:
    def test_p9_starts_empty_advances_and_never_regresses(self, db):
        assert get_watermark(db) is None

        advance_watermark(db, "2026-08-22T18:00:18+08:00")
        assert get_watermark(db) == "2026-08-22T18:00:18+08:00"

        # Forward: moves.
        advance_watermark(db, "2026-08-25T11:21:01+08:00")
        assert get_watermark(db) == "2026-08-25T11:21:01+08:00"

        # Backward: refused silently — an out-of-order page must never make
        # the sync re-read weeks of history or, worse, skip changes.
        advance_watermark(db, "2026-08-19T13:03:27+08:00")
        assert get_watermark(db) == "2026-08-25T11:21:01+08:00"

    def test_p9b_watermark_is_a_single_row(self, db):
        advance_watermark(db, "2026-08-22T18:00:18+08:00")
        advance_watermark(db, "2026-08-25T11:21:01+08:00")
        assert db.query(models.PoSyncWatermark).count() == 1

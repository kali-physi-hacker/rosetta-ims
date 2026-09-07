"""RM/RF-groups — receipts the way Daysmart's own screen makes them.

Everything mocked; no Daysmart. The rules pinned here (Moses, 2026-09-02:
"in Daysmart they don't type the lines — they pick the PO, create, and
the rows appear; then they review received against ordered"):

- LOCAL FIRST: the receipt header and any adjustments are staged in our
  database BEFORE any Daysmart call; a failure leaves a retryable record.
- Create = header only. Daysmart generates one row per PO line ("received
  as ordered"); we PULL them back through the receipt inbox and keep them
  as they are. A request with no items is complete on its own.
- Adjustments name OUR line (``line_uuid``); Daysmart ids are looked up.
  A staged adjustment is ADOPTED by its generated row through the exact
  PO-line identity, then PUT onto it. Rows the caller did not mention are
  simply received as ordered — never a review case.
- An adjustment whose line got no generated row → that item needs review;
  the others proceed. Two adjustments for one line → refused before
  staging. An adjustment naming a line that was never sent → refused.
- Write-once identity: ``daysmart_receipt_id`` from the create ack.
- POSTING IS EXPLICIT: ``post_receipt_flow`` is its own call.
- A receipt needs a SYNCED PO (its Daysmart id rides in the payload).
- Timeout on receipt-create → 'needs_review', never resent.

Interfaces under test:

    services/po_receipt_flow.py
        ReceiptDraft(invoice_number, receipt_date, shipping, tax, tax_type, notes, items=[...])
        ReceiptItemDraft(quantity_received, quantity_in_stock, amount, line_uuid=None,
                         post_to_item_id=None, daysmart_po_item_id=None, purchase_order_line_id=None,
                         expiration_date=None, lot_number="", manufacturer=None, ndc_code=None, tax=None)
        create_receipt_for_po(db, po, draft, daysmart) -> result(.receipt, .outcome,
                              .generated_rows, .adjustments_applied, .mirror_error)
        apply_receipt_adjustment(db, receipt, item, daysmart) -> outcome
        post_receipt_flow(db, receipt, daysmart) -> outcome
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_receipts.db")

from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from services.daysmart_service import (
    DaysmartReceiptItemRef,
    DaysmartRefused,
    DaysmartTimeout,
    DaysmartUnavailable,
)
from schemas.purchase_orders import (
    parse_daysmart_receipt_detail,
    parse_daysmart_receipt_rows,
)
from services.po_receipt_flow import (
    ReceiptDraft,
    ReceiptHeaderDraft,
    ReceiptItemChanges,
    ReceiptItemDraft,
    ReceiptOperationRefused,
    ReceiptValidationError,
    apply_receipt_adjustment,
    create_receipt_for_po,
    post_receipt_flow,
    retry_receipt,
    update_receipt_header,
    update_receipt_row,
)


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
        order_date="2026-08-29T11:00:00+08:00", sync_status="synced",
        daysmart_id=daysmart_id, daysmart_index=524,
        created_at="2026-08-29T11:00:00+08:00", updated_at="2026-08-29T11:00:00+08:00")
    po.lines = [
        models.PurchaseOrderLine(
            description="Douxo", quantity="12", quantity_uom="BOTTLE",
            unit_price_raw="96.00", sync_status="synced", daysmart_item_id=555001,
            daysmart_inventory_item_id="DIT-ebc6f1b0-6e6"),
        models.PurchaseOrderLine(
            description="Hills", quantity="6", quantity_uom="BAG",
            unit_price_raw="50.00", sync_status="synced", daysmart_item_id=555002,
            daysmart_inventory_item_id="DIT-other"),
    ]
    db.add(po)
    db.commit()
    return po


def draft(items=None, receipt_date="2026-08-29 11:00:00"):
    return ReceiptDraft(
        invoice_number="INV-9",
        receipt_date=receipt_date,
        shipping="0.00", tax="0.00", tax_type=1, notes="",
        items=items if items is not None else [
            ReceiptItemDraft(quantity_received=12, quantity_in_stock=12,
                             amount="96.00", post_to_item_id="DIT-ebc6f1b0-6e6",
                             daysmart_po_item_id=555001, lot_number="L-1"),
        ])


def rows_raw(*rows, receipt_id=50123):
    """Generated rows as the detail surface returns them:
    (row_id, po_item_id, purchase_order_id, post_to, quantity=12)."""
    items = []
    for spec in rows:
        row_id, po_item_id, order_id, post_to, *rest = spec
        quantity = rest[0] if rest else 12
        items.append({"id": row_id, "purchase_order_item_id": po_item_id,
                      "purchase_order_id": order_id, "item_id": post_to,
                      "purchase_order_receipt_id": receipt_id,
                      "quantity_received": quantity, "quantity_in_stock": quantity,
                      "amount": 96, "tax": "0.00", "status": 0, "is_active": True})
    return items


def rows_result(*rows, receipt_id=50123):
    return parse_daysmart_receipt_rows(
        {"response": {"resources": {"id": receipt_id, "receipt_items": rows_raw(*rows, receipt_id=receipt_id)}}})


PHP = {"date": "2026-08-29 03:00:00.000000", "timezone_type": 3, "timezone": "UTC"}


def detail_result(rows, *, receipt_id=50123, status=0, purchase_order_id=None):
    """The detail surface for a receipt: header (detail dialect — an
    unposted receipt names NO order on its header) + the given rows."""
    raw_items = []
    for row in rows.rows:
        raw_items.append({"id": row.daysmart_row_id, "purchase_order_item_id": row.po_line_id,
                          "purchase_order_id": row.purchase_order_id, "item_id": row.post_to_item_id,
                          "purchase_order_receipt_id": receipt_id,
                          "quantity_received": row.quantity_received,
                          "quantity_in_stock": row.quantity_in_stock,
                          "amount": row.amount_raw, "tax": row.tax_raw,
                          "lot_number": row.lot_number, "expiration_date": row.expiration_date,
                          "manufacturer": row.manufacturer, "ndc_code": row.ndc_code,
                          "status": row.status_id, "is_active": row.is_active})
    label = {0: "In Process", 1: "Posted"}.get(status, "?")
    return parse_daysmart_receipt_detail({"response": {"resources": {
        "id": receipt_id, "index": f"Receipt #{receipt_id}", "status": {"id": status, "label": label},
        "date": PHP, "invoice_number": "INV-9", "amount": "96.00", "tax": "0.00", "shipping": "0.00",
        "tax_percentage": None, "tax_type": 1, "notes": None, "is_active": True, "is_with_order": 1,
        "purchase_order_id": purchase_order_id, "supplier_id": "3cd1ace0-ddd6-4a",
        "supplier": {"id": "3cd1ace0-ddd6-4a", "name": "Alfamedic"},
        "business_id": "b", "post_by": None, "post_at": None,
        "create_by": "u", "create_at": PHP, "update_by": "u", "update_at": PHP,
        "receipt_items": raw_items}, "message": {"code": 200}}})


ONE_ROW = ((700001, 555001, 793009, "DIT-ebc6f1b0-6e6"),)
TWO_ROWS = ((700001, 555001, 793009, "DIT-ebc6f1b0-6e6"), (700002, 555002, 793009, "DIT-other"))


class FakeDaysmart:
    """Create, serve the rows Daysmart generated, take row updates, post."""

    def __init__(self, create=50123, generated=None, item_results=(), post=None):
        self.create = create           # int id, or an Exception
        self.generated = generated if generated is not None else rows_result(*ONE_ROW)
        self.item_results = list(item_results)  # one entry per PUT
        self.post = post               # None = ack, or an Exception
        self.created_payloads: list = []
        self.item_payloads: list = []
        self.header_payloads: list = []
        self.header_result = None      # None = ack, or an Exception
        self.posted: list = []
        self.rows_fetched: list = []
        self.details_fetched: list = []

    def create_receipt(self, payload):
        self.created_payloads.append(payload)
        if isinstance(self.create, Exception):
            raise self.create
        return self.create

    def fetch_receipt(self, receipt_id):
        self.details_fetched.append(receipt_id)
        if isinstance(self.generated, Exception):
            raise self.generated
        return detail_result(self.generated, receipt_id=receipt_id)

    def fetch_receipt_rows(self, receipt_id):
        self.rows_fetched.append(receipt_id)
        if isinstance(self.generated, Exception):
            raise self.generated
        return self.generated

    def update_receipt(self, receipt_id, payload):
        self.header_payloads.append((receipt_id, payload))
        if isinstance(self.header_result, Exception):
            raise self.header_result

    def fetch_receipt_items(self, receipt_id):
        if isinstance(self.generated, Exception):
            raise self.generated
        return [DaysmartReceiptItemRef(
            daysmart_receipt_item_id=row.daysmart_row_id,
            daysmart_po_item_id=row.po_line_id,
            purchase_order_id=row.purchase_order_id,
            post_to_item_id=row.post_to_item_id,
        ) for row in self.generated.rows]

    def update_receipt_item(self, receipt_item_id, payload):
        self.item_payloads.append((receipt_item_id, payload))
        if self.item_results:
            action = self.item_results.pop(0)
            if isinstance(action, Exception):
                raise action

    def post_receipt(self, receipt_id):
        self.posted.append(receipt_id)
        if isinstance(self.post, Exception):
            raise self.post


# ---------------------------------------------------------------------------
# RM — the tables
# ---------------------------------------------------------------------------


class TestReceiptModels:
    def test_rm1_receipt_defaults(self, db):
        po = synced_po(db)
        receipt = models.PoReceipt(
            purchase_order_id=po.id, invoice_number="INV-9",
            receipt_date="2026-08-29 11:00:00",
            created_at="2026-08-29T11:00:00+08:00",
            updated_at="2026-08-29T11:00:00+08:00")
        db.add(receipt)
        db.commit()
        assert receipt.receipt_uuid and len(receipt.receipt_uuid) == 36
        assert receipt.sync_status == "draft"
        assert receipt.daysmart_receipt_id is None
        assert receipt.post_status == "draft"   # not posted until explicit

    def test_rm2_daysmart_receipt_id_unique_when_set(self, db):
        po = synced_po(db)
        first = models.PoReceipt(
            purchase_order_id=po.id, invoice_number="A",
            receipt_date="2026-08-29 11:00:00", daysmart_receipt_id=50123,
            created_at="x", updated_at="x")
        db.add(first)
        db.commit()
        # Many un-synced receipts (NULL) may coexist …
        db.add(models.PoReceipt(purchase_order_id=po.id, invoice_number="B",
                                receipt_date="2026-08-29 11:00:00",
                                created_at="x", updated_at="x"))
        db.add(models.PoReceipt(purchase_order_id=po.id, invoice_number="C",
                                receipt_date="2026-08-29 11:00:00",
                                created_at="x", updated_at="x"))
        db.commit()
        # … but a set daysmart id may not repeat.
        clash = models.PoReceipt(purchase_order_id=po.id, invoice_number="D",
                                 receipt_date="2026-08-29 11:00:00",
                                 daysmart_receipt_id=50123,
                                 created_at="x", updated_at="x")
        db.add(clash)
        with pytest.raises(IntegrityError):
            db.commit()

    def test_rm3_items_belong_to_receipt_with_verbatim_values(self, db):
        po = synced_po(db)
        receipt = models.PoReceipt(
            purchase_order_id=po.id, invoice_number="INV-9",
            receipt_date="2026-08-29 11:00:00", created_at="x", updated_at="x")
        receipt.items = [models.PoReceiptItem(
            quantity_received="12", quantity_in_stock="10",  # short-shipment
            amount="96.00", lot_number="L-1",
            expiration_date="2027-01-31",
            purchase_order_line_id=po.lines[0].id,
        )]
        db.add(receipt)
        db.commit()
        item = receipt.items[0]
        assert item.item_uuid and len(item.item_uuid) == 36
        assert item.sync_status == "pending"
        assert item.quantity_received == "12"
        assert item.quantity_in_stock == "10"    # received ≠ stocked, kept
        assert item.amount == "96.00"
        assert item.purchase_order_line_id == po.lines[0].id
        assert item.daysmart_po_item_id is None
        assert item.daysmart_receipt_item_id is None


# ---------------------------------------------------------------------------
# RF — the flow
# ---------------------------------------------------------------------------


def by_row(receipt, row_id):
    return next(i for i in receipt.items if i.daysmart_receipt_item_id == row_id)


class TestReceiptFlow:
    def test_rf1_happy_header_then_generated_rows_then_adjustment(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(create=50123)
        result = create_receipt_for_po(db, po, draft(), daysmart)   # one adjustment (12)
        db.commit()

        assert result.outcome == "synced"
        assert (result.generated_rows, result.adjustments_applied, result.mirror_error) == (1, 1, None)
        receipt = result.receipt
        assert receipt.daysmart_receipt_id == 50123
        assert receipt.sync_status == "synced"
        assert receipt.post_status == "draft"        # NOT posted — explicit
        assert daysmart.posted == []                 # nothing auto-posts
        payload = daysmart.created_payloads[0]
        assert payload["order_id"] == ["793009"]
        assert payload["date"] == 1788001200
        assert payload["invoice_number"] == "INV-9"
        assert payload["supplier_id"] == "3cd1ace0-ddd6-4a"
        # The staged adjustment was ADOPTED by its generated row and PUT — never POSTed.
        (receipt_item_id, item_payload), = daysmart.item_payloads
        assert receipt_item_id == 700001
        assert item_payload["quantity_received"] == 12
        assert "item_id" not in item_payload         # same post-to as Daysmart's row
        item = by_row(receipt, 700001)
        assert item.sync_status == "synced" and item.quantity_received == "12"
        assert item.daysmart_quantity_received == 12  # the mirror, from the pull after the PUT
        assert daysmart.details_fetched == [50123, 50123]  # pull after create, pull after the PUT

    def test_rf1a_header_only_is_complete_rows_are_received_as_ordered(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(generated=rows_result(*TWO_ROWS))
        result = create_receipt_for_po(db, po, draft(items=[]), daysmart)
        db.commit()

        assert result.outcome == "synced"
        assert (result.generated_rows, result.adjustments_applied) == (2, 0)
        assert daysmart.item_payloads == []          # nothing to send: generated as-is
        assert daysmart.details_fetched == [50123]   # one pull, no second
        receipt = result.receipt
        assert receipt.sync_status == "synced"
        assert sorted(i.daysmart_receipt_item_id for i in receipt.items) == [700001, 700002]
        item = by_row(receipt, 700001)
        assert (item.quantity_received, item.quantity_in_stock, item.sync_status) == ("12", "12", "synced")
        assert item.purchase_order_line_id == po.lines[0].id      # linked to OUR line
        assert item.post_to_item_id == "DIT-ebc6f1b0-6e6"

    def test_rf1b_an_adjustment_names_our_line_daysmart_ids_are_looked_up(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(generated=rows_result(*TWO_ROWS))
        items = [ReceiptItemDraft(quantity_received=10, quantity_in_stock=10, amount="96.00",
                                  line_uuid=po.lines[0].line_uuid, lot_number="L-7")]
        result = create_receipt_for_po(db, po, draft(items=items), daysmart)
        db.commit()

        assert result.outcome == "synced"
        (row_id, payload), = daysmart.item_payloads
        assert row_id == 700001 and payload["quantity_received"] == 10 and payload["lot_number"] == "L-7"
        receipt = result.receipt
        assert by_row(receipt, 700001).daysmart_po_item_id == 555001
        # The line nobody mentioned is simply received as ordered.
        assert by_row(receipt, 700002).sync_status == "synced"
        assert by_row(receipt, 700002).quantity_received == "12"

    def test_rf1c_a_different_post_to_rides_as_item_id(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart()
        items = [ReceiptItemDraft(quantity_received=12, quantity_in_stock=12, amount="96.00",
                                  line_uuid=po.lines[0].line_uuid, post_to_item_id="DIT-elsewhere")]
        create_receipt_for_po(db, po, draft(items=items), daysmart)
        db.commit()
        (_, payload), = daysmart.item_payloads
        assert payload["item_id"] == "DIT-elsewhere"

    def test_rf2_daysmart_down_leaves_retryable_local_receipt(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(create=DaysmartUnavailable("down"))
        result = create_receipt_for_po(db, po, draft(), daysmart)
        db.commit()

        assert result.outcome == "sync_failed"
        receipt = result.receipt
        assert receipt.id is not None                # local record SURVIVES
        assert receipt.sync_status == "sync_failed"
        assert receipt.daysmart_receipt_id is None
        assert daysmart.item_payloads == []          # items never attempted
        assert all(i.sync_status == "pending" for i in receipt.items)

    def test_rf3_timeout_on_create_goes_to_a_human(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(create=DaysmartTimeout("no answer"))
        result = create_receipt_for_po(db, po, draft(), daysmart)
        db.commit()

        assert result.outcome == "needs_review"
        assert result.receipt.sync_status == "sync_failed"
        assert len(daysmart.created_payloads) == 1   # never resent

    def test_rf4_partial_adjustment_failure_keeps_partial_truth(self, db):
        po = synced_po(db)
        items = [
            ReceiptItemDraft(quantity_received=6, quantity_in_stock=6, amount="96.00",
                             line_uuid=po.lines[0].line_uuid),
            ReceiptItemDraft(quantity_received=6, quantity_in_stock=6, amount="96.00",
                             line_uuid=po.lines[1].line_uuid),
        ]
        daysmart = FakeDaysmart(generated=rows_result(*TWO_ROWS),
                                item_results=[None, DaysmartUnavailable("down")])
        result = create_receipt_for_po(db, po, draft(items=items), daysmart)
        db.commit()

        assert result.outcome == "sync_failed"       # retryable
        receipt = result.receipt
        assert receipt.daysmart_receipt_id == 50123  # header link kept
        assert by_row(receipt, 700001).sync_status == "synced"
        assert by_row(receipt, 700002).sync_status == "pending"

    def test_rf5_an_adjustment_must_name_a_line_never_guessed(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart()
        items = [ReceiptItemDraft(quantity_received=12, quantity_in_stock=12, amount="96.00")]
        with pytest.raises(ReceiptValidationError):
            create_receipt_for_po(db, po, draft(items=items), daysmart)
        assert daysmart.created_payloads == []       # nothing sent, nothing staged
        assert db.query(models.PoReceipt).count() == 0

    def test_rf5a_two_adjustments_for_one_line_are_refused_before_staging(self, db):
        po = synced_po(db)
        items = [ReceiptItemDraft(quantity_received=1, quantity_in_stock=1, amount="1",
                                  line_uuid=po.lines[0].line_uuid)] * 2
        with pytest.raises(ReceiptValidationError):
            create_receipt_for_po(db, po, draft(items=items), FakeDaysmart())
        assert db.query(models.PoReceipt).count() == 0

    def test_rf5b_a_line_never_sent_to_daysmart_cannot_be_adjusted(self, db):
        po = synced_po(db)
        po.lines.append(models.PurchaseOrderLine(
            description="local only", quantity="1", quantity_uom="EA", unit_price_raw="1.00",
            sync_status="pending"))
        db.commit()
        items = [ReceiptItemDraft(quantity_received=1, quantity_in_stock=1, amount="1",
                                  line_uuid=po.lines[2].line_uuid)]
        with pytest.raises(ReceiptValidationError):
            create_receipt_for_po(db, po, draft(items=items), FakeDaysmart())

    def test_rf5c_an_adjustment_with_no_generated_row_needs_review_others_proceed(self, db):
        po = synced_po(db)
        items = [
            ReceiptItemDraft(quantity_received=6, quantity_in_stock=6, amount="96.00",
                             line_uuid=po.lines[0].line_uuid),
            ReceiptItemDraft(quantity_received=6, quantity_in_stock=6, amount="96.00",
                             line_uuid=po.lines[1].line_uuid),   # Daysmart made no row for 555002
        ]
        daysmart = FakeDaysmart(generated=rows_result(*ONE_ROW))
        result = create_receipt_for_po(db, po, draft(items=items), daysmart)
        db.commit()

        assert result.outcome == "needs_review"
        assert [rid for rid, _ in daysmart.item_payloads] == [700001]
        receipt = result.receipt
        assert by_row(receipt, 700001).sync_status == "synced"
        orphan = next(i for i in receipt.items if i.daysmart_receipt_item_id is None)
        assert orphan.sync_status == "needs_review" and orphan.daysmart_po_item_id == 555002

    def test_rf6_receipt_for_unsynced_po_is_refused_locally(self, db):
        po = synced_po(db)
        po.daysmart_id = None
        po.sync_status = "draft"
        db.commit()
        daysmart = FakeDaysmart()
        with pytest.raises(ValueError):
            create_receipt_for_po(db, po, draft(), daysmart)
        assert daysmart.created_payloads == []       # nothing sent
        assert db.query(models.PoReceipt).count() == 0

    def test_rf6a_invalid_date_is_rejected_before_local_staging(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart()

        with pytest.raises(ReceiptValidationError):
            create_receipt_for_po(
                db, po, draft(receipt_date="definitely-not-a-date"), daysmart)

        assert daysmart.created_payloads == []
        assert db.query(models.PoReceipt).count() == 0

    def test_rf6c_rows_pull_failure_keeps_header_link(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(generated=DaysmartUnavailable("down"))
        result = create_receipt_for_po(db, po, draft(), daysmart)
        db.commit()

        assert result.outcome == "sync_failed"
        assert result.receipt.daysmart_receipt_id == 50123
        assert result.receipt.sync_status == "sync_failed"
        assert daysmart.item_payloads == []

    def test_rf6d_a_failed_mirror_pull_after_the_put_is_reported_not_undone(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart()
        original_fetch = daysmart.fetch_receipt

        def flaky(receipt_id):
            if len(daysmart.details_fetched) >= 1:
                daysmart.details_fetched.append(receipt_id)
                raise DaysmartUnavailable("down")
            return original_fetch(receipt_id)

        daysmart.fetch_receipt = flaky
        result = create_receipt_for_po(db, po, draft(), daysmart)
        db.commit()
        assert result.outcome == "synced" and result.adjustments_applied == 1
        assert result.mirror_error.startswith("DaysmartUnavailable")

    def test_rf7_posting_is_its_own_flow_and_marks_locally(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(create=50123)
        receipt = create_receipt_for_po(db, po, draft(), daysmart).receipt
        db.commit()

        outcome = post_receipt_flow(db, receipt, daysmart)
        db.commit()
        assert outcome == "posted"
        assert daysmart.posted == [50123]
        assert receipt.post_status == "posted"

    def test_rf7b_post_refusal_stays_unposted(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(create=50123, post=DaysmartRefused("pending items"))
        receipt = create_receipt_for_po(db, po, draft(), daysmart).receipt
        db.commit()

        outcome = post_receipt_flow(db, receipt, daysmart)
        db.commit()
        assert outcome == "refused"
        assert receipt.post_status == "draft"

    def test_rf8_posting_an_unsynced_receipt_is_refused_locally(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(create=DaysmartUnavailable("down"))
        receipt = create_receipt_for_po(db, po, draft(), daysmart).receipt
        db.commit()
        with pytest.raises(ValueError):
            post_receipt_flow(db, receipt, daysmart)
        assert daysmart.posted == []

    def test_rf9_post_preflight_refuses_remote_item_without_post_to(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart()
        receipt = create_receipt_for_po(db, po, draft(), daysmart).receipt
        db.commit()
        daysmart.generated = rows_result((700001, 555001, 793009, None))

        outcome = post_receipt_flow(db, receipt, daysmart)
        db.commit()

        assert outcome == "refused"
        assert daysmart.posted == []
        assert receipt.post_status == "draft"

    def test_rf10_apply_receipt_adjustment_is_the_shared_row_write(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(generated=rows_result(*TWO_ROWS))
        receipt = create_receipt_for_po(db, po, draft(items=[]), daysmart).receipt
        db.commit()
        item = by_row(receipt, 700002)
        item.quantity_received = "4"                 # the adjust step will set fields like this
        item.quantity_in_stock = "4"
        outcome = apply_receipt_adjustment(db, receipt, item, daysmart)
        db.commit()
        assert outcome == "synced"
        assert daysmart.item_payloads[-1] == (700002, {
            "quantity_received": 4, "quantity_in_stock": 4, "amount": "96",
            "expiration_date": None, "lot_number": "", "manufacturer": None, "ndc_code": None,
            "tax": "0.00"})


# ---------------------------------------------------------------------------
# RE — editing a receipt: the header, and one row (received vs ordered)
# ---------------------------------------------------------------------------


HEADER = ReceiptHeaderDraft(invoice_number="888046260817", receipt_date="2026-08-16T16:00:00+00:00",
                            shipping="1", tax="2", notes="Test Receipt")


class TestReceiptEdits:
    def created(self, db, daysmart=None):
        po = synced_po(db)
        daysmart = daysmart or FakeDaysmart(generated=rows_result(*TWO_ROWS))
        receipt = create_receipt_for_po(db, po, draft(items=[]), daysmart).receipt
        db.commit()
        daysmart.details_fetched.clear()
        return po, receipt, daysmart

    def test_re1_header_edit_pushes_the_wire_body_then_pulls_the_receipt(self, db):
        po, receipt, daysmart = self.created(db)
        result = update_receipt_header(db, receipt, HEADER, daysmart)
        db.commit()
        assert result.outcome == "synced" and result.mirror_error is None
        (receipt_id, payload), = daysmart.header_payloads
        assert receipt_id == 50123
        assert payload == {"date": 1786896000, "tax": "2", "shipping": "1",      # receipt-edit.har keys
                           "invoice_number": "888046260817", "notes": "Test Receipt"}
        assert (receipt.invoice_number, receipt.tax, receipt.shipping, receipt.notes) == (
            "888046260817", "2", "1", "Test Receipt")
        assert receipt.sync_status == "synced"
        assert daysmart.details_fetched == [50123]                # pulled after the write
        assert [(a.step, a.outcome) for a in po.sync_attempts if a.step == "update_receipt"] == [
            ("update_receipt", "success")]

    def test_re2_a_posted_receipt_is_refused_locally_no_call(self, db):
        po, receipt, daysmart = self.created(db)
        receipt.post_status = "posted"
        with pytest.raises(ReceiptOperationRefused) as info:
            update_receipt_header(db, receipt, HEADER, daysmart)
        assert info.value.code == "RECEIPT_POSTED"
        receipt.post_status = "draft"
        receipt.daysmart_status = "posted"                          # Daysmart's own word counts too
        with pytest.raises(ReceiptOperationRefused):
            update_receipt_row(db, receipt, by_row(receipt, 700001),
                               ReceiptItemChanges(quantity_received=1, quantity_in_stock=1, amount="1"), daysmart)
        assert daysmart.header_payloads == [] and daysmart.item_payloads == []

    def test_re3_a_refusal_keeps_the_local_edit_and_says_why(self, db):
        po, receipt, daysmart = self.created(db)
        daysmart.header_result = DaysmartRefused("closed")
        result = update_receipt_header(db, receipt, HEADER, daysmart)
        db.commit()
        assert (result.outcome, result.error_code) == ("needs_review", "DaysmartRefused")
        assert receipt.invoice_number == "888046260817"             # what we WANT is kept
        assert receipt.sync_status == "sync_failed"
        assert daysmart.details_fetched == []                       # no pull after a failed write

    def test_re4_a_timeout_on_the_header_put_is_retryable(self, db):
        po, receipt, daysmart = self.created(db)
        daysmart.header_result = DaysmartTimeout("slow")
        result = update_receipt_header(db, receipt, HEADER, daysmart)
        assert (result.outcome, result.error_code) == ("sync_failed", "DaysmartTimeout")
        assert [(a.step, a.outcome) for a in po.sync_attempts if a.step == "update_receipt"] == [
            ("update_receipt", "timeout")]

    def test_re5_row_edit_pushes_every_field_then_pulls(self, db):
        po, receipt, daysmart = self.created(db)
        item = by_row(receipt, 700002)
        result = update_receipt_row(db, receipt, item, ReceiptItemChanges(
            quantity_received=1, quantity_in_stock=1, amount="1", expiration_date=1790179200,
            lot_number="3", manufacturer="none", ndc_code="2345676", tax="2"), daysmart)
        db.commit()
        assert result.outcome == "synced" and result.mirror_error is None
        assert daysmart.item_payloads[-1] == (700002, {                    # receipt-edit.har keys
            "quantity_received": 1, "quantity_in_stock": 1, "amount": "1",
            "expiration_date": 1790179200, "lot_number": "3", "manufacturer": "none",
            "ndc_code": "2345676", "tax": "2"})
        assert (item.quantity_received, item.lot_number, item.expiration_date, item.sync_status) == (
            "1", "3", "1790179200", "synced")
        assert daysmart.details_fetched == [50123]

    def test_re6_a_row_daysmart_has_not_generated_cannot_be_edited(self, db):
        po, receipt, daysmart = self.created(db)
        orphan = models.PoReceiptItem(quantity_received="1", quantity_in_stock="1", amount="1",
                                      sync_status="pending")
        receipt.items.append(orphan)
        db.commit()
        with pytest.raises(ReceiptOperationRefused) as info:
            update_receipt_row(db, receipt, orphan,
                               ReceiptItemChanges(quantity_received=2, quantity_in_stock=2, amount="1"), daysmart)
        assert info.value.code == "RECEIPT_ITEM_NOT_GENERATED"

    def test_re7_a_refused_row_edit_keeps_the_local_change(self, db):
        po, receipt, daysmart = self.created(db)
        daysmart.item_results = [DaysmartRefused("posted")]
        item = by_row(receipt, 700001)
        result = update_receipt_row(db, receipt, item,
                                    ReceiptItemChanges(quantity_received=3, quantity_in_stock=3, amount="1"), daysmart)
        assert (result.outcome, result.error_code) == ("needs_review", "DaysmartRefused")
        assert item.quantity_received == "3" and item.sync_status == "needs_review"
        assert receipt.sync_status == "sync_failed"

    def test_re8_a_bad_expiry_changes_nothing(self, db):
        po, receipt, daysmart = self.created(db)
        item = by_row(receipt, 700001)
        with pytest.raises(ReceiptValidationError):
            update_receipt_row(db, receipt, item, ReceiptItemChanges(
                quantity_received=3, quantity_in_stock=3, amount="1", expiration_date="not-a-date"), daysmart)
        assert item.quantity_received == "12" and daysmart.item_payloads == []


# ---------------------------------------------------------------------------
# RT — retrying a receipt (the lifecycle engine)
# ---------------------------------------------------------------------------


class TestReceiptRetry:
    def test_rt1_a_never_landed_create_is_redriven_same_row_no_duplicate(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(create=DaysmartUnavailable("down"))
        receipt = create_receipt_for_po(db, po, draft(), daysmart).receipt
        db.commit()
        assert receipt.sync_status == "sync_failed" and receipt.daysmart_receipt_id is None
        daysmart.create = 50123
        result = retry_receipt(db, receipt, daysmart)
        db.commit()
        assert result.outcome == "synced" and result.receipt is receipt
        assert receipt.daysmart_receipt_id == 50123
        assert len(daysmart.created_payloads) == 2
        assert daysmart.created_payloads[1] == daysmart.created_payloads[0]      # exactly what was staged
        assert by_row(receipt, 700001).sync_status == "synced"                  # the adjustment went through
        assert db.query(models.PoReceipt).count() == 1

    def test_rt2_a_may_have_landed_create_is_not_resent(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(create=DaysmartTimeout("slow"))
        receipt = create_receipt_for_po(db, po, draft(), daysmart).receipt
        db.commit()
        daysmart.create = 50123
        assert retry_receipt(db, receipt, daysmart).outcome == "needs_review"
        assert len(daysmart.created_payloads) == 1

    def test_rt3_a_landed_header_finishes_what_is_left(self, db):
        po = synced_po(db)
        daysmart = FakeDaysmart(generated=DaysmartUnavailable("down"))     # rows pull failed after create
        receipt = create_receipt_for_po(db, po, draft(), daysmart).receipt
        db.commit()
        assert receipt.daysmart_receipt_id == 50123 and receipt.sync_status == "sync_failed"
        daysmart.generated = rows_result(*ONE_ROW)
        result = retry_receipt(db, receipt, daysmart)
        db.commit()
        assert result.outcome == "synced" and len(daysmart.created_payloads) == 1
        assert by_row(receipt, 700001).sync_status == "synced" and receipt.sync_status == "synced"

    def test_rt4_attempts_name_their_receipt(self, db):
        po = synced_po(db)
        first = create_receipt_for_po(db, po, draft(items=[]), FakeDaysmart(create=DaysmartUnavailable("down"))).receipt
        second = create_receipt_for_po(db, po, draft(items=[]), FakeDaysmart(create=50124)).receipt
        db.commit()
        mine = {a.receipt_id for a in po.sync_attempts if a.step == "create_receipt"}
        assert mine == {first.id, second.id}
        assert retry_receipt(db, second, FakeDaysmart(generated=rows_result(*ONE_ROW, receipt_id=50124))).outcome == "synced"

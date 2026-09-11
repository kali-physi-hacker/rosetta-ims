"""RE/AR/AI-groups — the failed-draft lifecycle engine and its three doors.

Decisions pinned (Moses, 2026-09-01):

- NO automatic resubmission of old drafts — every path is either provably
  safe or an explicit human verb.
- The ENGINE (``resubmit_purchase_order``) re-drives the sync of an
  EXISTING PO — never a new local row, never a second Daysmart order:
  * refuses synced/cancelled POs (caller error);
  * refuses when the last attempt is a MAY-HAVE-LANDED class (timeout /
    contract_mismatch) — returns 'needs_review' without touching Daysmart;
  * a synced header is simply synced — lines are a separate resource;
  * shares the create saga's machinery (snapshot, diff-on-timeout,
    attempt logging) — a retry behaves exactly like a first attempt.
- Door 1: POST /{po_uuid}/retry — the OPS button (409 synced/cancelled).
- Door 2: outcome-aware idempotency replay (Stripe semantics): a same-key
  repeat re-drives ONLY when the first attempt provably never landed
  (daysmart_down / auth_failure); timeout-class failures replay the stored
  response; a PO synced meanwhile returns its current state; different
  body is always 409.
- Door 3: POST /{po_uuid}/cancel — local-only 'cancelled' status (new
  sync_status value), leaves the review buckets, never touches Daysmart.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_retry.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")

from pathlib import Path

import database
import main
import models
from dependencies import require_user
from fastapi.testclient import TestClient
from routers.purchase_orders import get_po_daysmart
from schemas.purchase_orders import (
    DaysmartOrderPageParseResult,
    DaysmartPageMetaV1,
    parse_create_command,
    parse_daysmart_created_order,
)
from services.daysmart_service import DaysmartTimeout, DaysmartUnavailable
from services.po_create_flow import create_purchase_order, resubmit_purchase_order

models.Base.metadata.create_all(bind=database.engine)

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
CREATE_RESPONSE = json.loads(
    (FIXTURES / "daysmart_order_create_response.json").read_text(encoding="utf-8"))

EMPTY_PAGE = DaysmartOrderPageParseResult(
    orders=[], issues=[],
    meta=DaysmartPageMetaV1(total=0, per_page=50, current_page=1, last_page=1))


def command():
    parsed = parse_create_command({
        "supplier_daysmart_id": "3cd1ace0-ddd6-4a",
        "supplier_name": "Alfamedic",
        "order_date": "2026-09-01T11:00:00+08:00",
        "notes": "retry test",
    })
    assert parsed.command is not None, parsed.issues
    return parsed.command


ORDER_DETAIL = json.loads((Path(__file__).parent / "fixtures" / "purchase_orders"
                           / "daysmart_order_detail_793009.json").read_text(encoding="utf-8"))


class FakeDaysmart:
    def __init__(self, creates=(), line_saves=()):
        self.creates = list(creates)
        self.line_saves = list(line_saves)
        self.create_payloads: list = []
        self.line_payloads: list = []
        self.minted = 0

    def fetch_orders(self, page=1, order_type="all"):
        return EMPTY_PAGE

    def create_order(self, payload):
        self.create_payloads.append(payload)
        if not self.creates:
            # Script exhausted mid-saga (e.g. the not_landed resend after a
            # timeout): surface as another timeout so the flow parks the PO
            # with a may-have-landed last attempt.
            raise DaysmartTimeout("script exhausted")
        action = self.creates.pop(0)
        if isinstance(action, Exception):
            raise action
        body = copy.deepcopy(action)
        body["response"]["resources"]["id"] += self.minted
        body["response"]["resources"]["index"] += self.minted
        self.minted += 1
        return parse_daysmart_created_order(body)

    def save_order_items(self, payloads):
        self.line_payloads.append(payloads)
        if self.line_saves:
            action = self.line_saves.pop(0)
            if isinstance(action, Exception):
                raise action

    # The pull after a landed write reads the whole PO: header, lines, receipts.
    def get_order(self, daysmart_order_id):
        body = copy.deepcopy(ORDER_DETAIL)
        body["response"]["resources"]["id"] = daysmart_order_id
        return parse_daysmart_created_order(body)

    def fetch_order_lines(self, daysmart_order_id):
        from schemas.purchase_orders import parse_daysmart_order_item_page
        return parse_daysmart_order_item_page({"response": {"resources": {
            "current_page": 1, "data": [], "last_page": 1, "per_page": 50, "total": 0},
            "message": {"code": 200}}})

    def fetch_receipts(self, page=1, purchase_order_id=None):
        from schemas.purchase_orders import parse_daysmart_receipt_page
        return parse_daysmart_receipt_page({"response": {"meta": [], "resources": [], "message": {"code": 200}}})


@pytest.fixture()
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite://")
    models.Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def failed_draft(db, daysmart_error=None):
    """A PO whose create provably never landed (daysmart_down)."""
    daysmart = FakeDaysmart(creates=[daysmart_error or DaysmartUnavailable("down")])
    result = create_purchase_order(db, command(), daysmart,
                                   default_ship_to_id=861040, default_bill_to_id=861040)
    db.commit()
    assert result.po.sync_status == "sync_failed"
    return result.po


# ---------------------------------------------------------------------------
# RE — the engine
# ---------------------------------------------------------------------------


class TestResubmitEngine:
    def test_re1_never_landed_draft_resubmits_to_synced_no_new_row(self, db):
        po = failed_draft(db)
        daysmart = FakeDaysmart(creates=[CREATE_RESPONSE])
        result = resubmit_purchase_order(db, po, daysmart)
        db.commit()

        assert result.outcome == "synced"
        assert po.daysmart_id == 793009
        assert po.sync_status == "synced"
        assert po.lines == []                      # a header create carries no lines
        assert db.query(models.PurchaseOrder).count() == 1     # SAME row
        # The rebuilt header payload came from the stored PO, not a command.
        payload = daysmart.create_payloads[0]
        assert payload["supplier_id"] == "3cd1ace0-ddd6-4a"
        assert isinstance(payload["order_date"], int)
        # History accumulates across attempts on one PO.
        steps = [(a.step, a.outcome) for a in po.sync_attempts]
        assert ("create_header", "daysmart_down") in steps     # the first try
        assert ("create_header", "success") in steps           # the retry

    def test_re2_header_create_never_sends_or_retries_lines(self, db):
        daysmart = FakeDaysmart(creates=[CREATE_RESPONSE],
                                line_saves=[DaysmartUnavailable("down")])
        po = create_purchase_order(db, command(), daysmart,
                                   default_ship_to_id=861040,
                                   default_bill_to_id=861040).po
        db.commit()
        assert po.daysmart_id == 793009 and po.sync_status == "synced"
        assert daysmart.line_payloads == []
        assert po.lines == []                      # a header create carries no lines
        with pytest.raises(ValueError):
            resubmit_purchase_order(db, po, FakeDaysmart())

    def test_re3_engine_refuses_synced_and_cancelled(self, db):
        po = failed_draft(db)
        po.sync_status = "synced"
        with pytest.raises(ValueError):
            resubmit_purchase_order(db, po, FakeDaysmart())
        po.sync_status = "cancelled"
        with pytest.raises(ValueError):
            resubmit_purchase_order(db, po, FakeDaysmart())

    def test_re4_may_have_landed_class_goes_to_human_without_calls(self, db):
        # First create: timeout → clean not_landed diff → resend → timeout
        # again (script exhausts as timeout) → parked with a may-have-landed
        # last attempt. THAT is the state the engine must refuse to touch.
        po = failed_draft(db, daysmart_error=DaysmartTimeout("no answer"))
        daysmart = FakeDaysmart()
        result = resubmit_purchase_order(db, po, daysmart)
        db.commit()
        assert result.outcome == "needs_review"
        assert daysmart.create_payloads == []       # Daysmart untouched
        assert daysmart.line_payloads == []


# ---------------------------------------------------------------------------
# AR — the endpoints (retry + cancel)
# ---------------------------------------------------------------------------


class _Admin:
    id = 501
    username = "retry-admin"
    display_name = "Retry Admin"
    role = "admin"


@pytest.fixture()
def api_daysmart():
    return FakeDaysmart(creates=[DaysmartUnavailable("down")])


@pytest.fixture()
def client(api_daysmart):
    main.app.dependency_overrides[require_user] = lambda: _Admin()
    main.app.dependency_overrides[get_po_daysmart] = lambda: api_daysmart
    session = database.SessionLocal()
    for model in (models.PoSyncAttempt, models.PoValidationIssueRecord,
                  models.PoReceiptItem, models.PoReceipt,
                  models.PurchaseOrderLine, models.PurchaseOrder,
                  models.PoSyncWatermark, models.PoRequestIdempotency):
        session.query(model).delete()
    session.commit()
    session.close()
    yield TestClient(main.app)
    main.app.dependency_overrides.pop(require_user, None)
    main.app.dependency_overrides.pop(get_po_daysmart, None)


def create_body(**overrides):
    body = {
        "supplier_daysmart_id": "3cd1ace0-ddd6-4a",
        "supplier_name": "Alfamedic",
        "order_date": "2026-09-01T11:00:00+08:00",
        "notes": "api retry test",
    }
    body.update(overrides)
    return body


class TestRetryAndCancelEndpoints:
    def test_ar1_retry_endpoint_redrives_a_failed_po(self, client, api_daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        api_daysmart.creates = [CREATE_RESPONSE]     # Daysmart is back
        response = client.post(f"/purchase-orders/{po_uuid}/retry")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "synced"
        assert data["po"]["daysmart_id"] == 793009

    def test_ar2_retry_refuses_synced_and_unknown(self, client, api_daysmart):
        api_daysmart.creates = [CREATE_RESPONSE]
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        assert client.post(f"/purchase-orders/{po_uuid}/retry").status_code == 409
        assert client.post("/purchase-orders/nope/retry").status_code == 404

    def test_ar3_cancel_is_local_only_and_final_for_retry(self, client, api_daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        response = client.post(f"/purchase-orders/{po_uuid}/cancel",
                               json={"reason": "created manually in Daysmart"})
        assert response.status_code == 200
        assert response.json()["po"]["sync_status"] == "cancelled"
        # Off the review buckets…
        review = client.get("/purchase-orders/review").json()
        assert review["sync_failed"]["total"] == 0
        assert review["drafts"]["total"] == 0
        # …and retry now refuses it.
        assert client.post(f"/purchase-orders/{po_uuid}/retry").status_code == 409

    def test_ar4_cancel_refuses_a_synced_po(self, client, api_daysmart):
        api_daysmart.creates = [CREATE_RESPONSE]
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        assert client.post(f"/purchase-orders/{po_uuid}/cancel").status_code == 409


# ---------------------------------------------------------------------------
# AI — outcome-aware idempotent replay
# ---------------------------------------------------------------------------


class TestOutcomeAwareReplay:
    HEADERS = {"Idempotency-Key": "retry-aware-1"}

    def test_ai1_same_key_redrives_a_provably_never_landed_create(self, client, api_daysmart):
        first = client.post("/purchase-orders", json=create_body(), headers=self.HEADERS)
        assert first.json()["outcome"] == "sync_failed"
        po_uuid = first.json()["po"]["po_uuid"]

        api_daysmart.creates = [CREATE_RESPONSE]     # Daysmart recovered
        second = client.post("/purchase-orders", json=create_body(), headers=self.HEADERS)
        assert second.status_code == 201
        data = second.json()
        assert data["outcome"] == "synced"           # re-driven, not replayed
        assert data["po"]["po_uuid"] == po_uuid      # SAME PO
        session = database.SessionLocal()
        try:
            assert session.query(models.PurchaseOrder).count() == 1
        finally:
            session.close()

        third = client.post("/purchase-orders", json=create_body(), headers=self.HEADERS)
        assert third.json()["outcome"] == "synced"   # now a stable success

    def test_ai2_timeout_class_replays_the_stored_failure(self, client, api_daysmart):
        # timeout → clean diff → resend → timeout again: parked sync_failed
        # with a TIMEOUT-class last attempt (may-have-landed).
        api_daysmart.creates = [DaysmartTimeout("no answer")]
        first = client.post("/purchase-orders", json=create_body(), headers=self.HEADERS)
        assert first.json()["outcome"] == "sync_failed"
        assert first.json()["failure"]["outcome"] == "timeout"

        calls_after_first = len(api_daysmart.create_payloads)
        api_daysmart.creates = [CREATE_RESPONSE]     # up again — must NOT matter
        second = client.post("/purchase-orders", json=create_body(), headers=self.HEADERS)
        assert second.json()["outcome"] == "sync_failed"    # replay, no re-drive
        assert second.json()["failure"]["outcome"] == "timeout"
        assert len(api_daysmart.create_payloads) == calls_after_first  # Daysmart untouched

    def test_ai3_po_synced_meanwhile_returns_current_state(self, client, api_daysmart):
        first = client.post("/purchase-orders", json=create_body(), headers=self.HEADERS)
        po_uuid = first.json()["po"]["po_uuid"]
        session = database.SessionLocal()            # OPS resolved it by hand
        po = session.query(models.PurchaseOrder).filter_by(po_uuid=po_uuid).one()
        po.daysmart_id, po.daysmart_index, po.sync_status = 799999, 530, "synced"
        session.commit(); session.close()

        second = client.post("/purchase-orders", json=create_body(), headers=self.HEADERS)
        data = second.json()
        assert data["outcome"] == "synced"
        assert data["po"]["daysmart_id"] == 799999   # current truth, not the past

    def test_ai4_different_body_is_still_409(self, client, api_daysmart):
        client.post("/purchase-orders", json=create_body(), headers=self.HEADERS)
        clash = client.post("/purchase-orders", json=create_body(notes="different"),
                            headers=self.HEADERS)
        assert clash.status_code == 409

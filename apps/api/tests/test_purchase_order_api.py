"""API-group — the purchase-order endpoints (DEV-304).

TDD: the router does not exist yet. TestClient over the real app; auth and
the Daysmart service are dependency-overridden — no network, no real
Daysmart, ever. The endpoints are a thin skin over the tested service
layer, so these tests focus on the HTTP concerns: status codes, validation
shapes, auth, and that mutations audit + commit.

Interface under test:

    routers/purchase_orders.py
        POST /purchase-orders                      create (the saga)
        GET  /purchase-orders                      list, local DB only
        GET  /purchase-orders/review               the three OPS buckets
        POST /purchase-orders/sync                 run_full_inbound_sync
        GET  /purchase-orders/{po_uuid}            detail + lines
        POST /purchase-orders/{po_uuid}/submit     explicit submit step
        POST /purchase-orders/{po_uuid}/resolve    link by daysmart_id
        POST /purchase-orders/{po_uuid}/refresh    pull ONE PO: header + lines
    routers/purchase_order_lines.py  (line writes, always under the PO)
        POST   /purchase-orders/{po_uuid}/lines
        PUT    /purchase-orders/{po_uuid}/lines/{line_uuid}
        DELETE /purchase-orders/{po_uuid}/lines/{line_uuid}
        POST /purchase-orders/{po_uuid}/receipts   local-first receipt
        POST /purchase-orders/{po_uuid}/receipts/{receipt_uuid}/post
        get_po_daysmart  — the injectable service dependency

    services/daysmart_service.py gains get_order(id) (read semantics),
    parsing through the create/detail dialect contract.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_api.db")
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
    parse_daysmart_created_order,
    parse_daysmart_order_page,
)
from services.daysmart_service import (
    DaysmartOrderItemRef,
    DaysmartReceiptItemRef,
    DaysmartRefused,
    DaysmartTimeout,
    DaysmartUnavailable,
)

models.Base.metadata.create_all(bind=database.engine)

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
CREATE_RESPONSE = json.loads(
    (FIXTURES / "daysmart_order_create_response.json").read_text(encoding="utf-8"))
DETAIL_RESPONSE = json.loads(
    (FIXTURES / "daysmart_order_detail_793009.json").read_text(encoding="utf-8"))
LIST_PAGE1 = json.loads(
    (FIXTURES / "daysmart_order_list_page1.json").read_text(encoding="utf-8"))
UPDATE_RESPONSE = json.loads(
    (FIXTURES / "daysmart_order_update_response.json").read_text(encoding="utf-8"))
DELETE_RESPONSE = json.loads(
    (FIXTURES / "daysmart_order_delete_response.json").read_text(encoding="utf-8"))
RECEIPT_LIST = json.loads(
    (FIXTURES / "daysmart_receipt_list_page1.json").read_text(encoding="utf-8"))
RECEIPT_DETAIL = json.loads(
    (FIXTURES / "daysmart_receipt_detail_822801.json").read_text(encoding="utf-8"))


class _Admin:
    id = 501
    username = "po-admin"
    display_name = "PO Admin"
    role = "admin"


class FakeDaysmart:
    """Everything the router can ask of Daysmart, scripted."""

    def __init__(self):
        self.create_result = CREATE_RESPONSE       # body dict or Exception
        self.line_save_result = None               # None = ok, or Exception
        self.order_item_fetches: list[object] = []
        self.saved_line_payloads: list[list[dict]] = []
        self.get_order_result = DETAIL_RESPONSE
        self.submit_result = None
        # The real fixture's meta says last_page=10; the fake serves ONE
        # page, so its meta must say so — the sync driver trusts last_page.
        parsed = parse_daysmart_order_page(copy.deepcopy(LIST_PAGE1))
        self.pages = {1: DaysmartOrderPageParseResult(
            orders=parsed.orders, issues=[],
            meta=DaysmartPageMetaV1(total=len(parsed.orders), per_page=50,
                                    current_page=1, last_page=1))}
        self.receipt_id = 50123
        self.receipt_items = [DaysmartReceiptItemRef(
            daysmart_receipt_item_id=3936614,
            daysmart_po_item_id=3924916,
            purchase_order_id=793009,
            post_to_item_id="DIT-ebc6f1b0-6e6",
        )]
        self.receipt_payloads: list[dict] = []
        self.receipt_item_updates: list[tuple[int, dict]] = []
        self.receipt_header_updates: list[tuple[int, dict]] = []
        self.receipt_header_result = None
        self.order_updates: list[tuple[int, dict]] = []
        self.update_result = None
        self.order_deletes: list[int] = []
        self.delete_order_result = None
        self.post_result = None
        self.posted_receipts: list[int] = []
        self.submitted: list[int] = []
        self.line_fetch_failures: dict[int, Exception] = {}
        self.receipt_fetch_failures: dict[int, Exception] = {}
        self.deleted_items: list[int] = []
        self.delete_result = None                  # None = ok, or Exception
        self._minted = 0

    def fetch_orders(self, page=1, order_type="all"):
        action = self.pages[page]
        if isinstance(action, Exception):
            raise action
        return action

    def create_order(self, payload):
        if isinstance(self.create_result, Exception):
            raise self.create_result
        # Real Daysmart mints a fresh id per create; so must the fake, or a
        # second create trips the (correct) unique daysmart_id constraint.
        body = copy.deepcopy(self.create_result)
        body["response"]["resources"]["id"] += self._minted
        body["response"]["resources"]["index"] += self._minted
        self._minted += 1
        return parse_daysmart_created_order(body)

    def save_order_items(self, payloads):
        self.saved_line_payloads.append(payloads)
        if isinstance(self.line_save_result, Exception):
            raise self.line_save_result

    def fetch_order_items(self, daysmart_order_id):
        if not self.order_item_fetches:
            return []
        action = self.order_item_fetches.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def get_order(self, daysmart_order_id):
        if isinstance(self.get_order_result, Exception):
            raise self.get_order_result
        return parse_daysmart_created_order(copy.deepcopy(self.get_order_result))

    def update_order(self, daysmart_order_id, payload):
        self.order_updates.append((daysmart_order_id, payload))
        if isinstance(self.update_result, Exception):
            raise self.update_result
        body = copy.deepcopy(UPDATE_RESPONSE)
        body["response"]["resources"]["id"] = daysmart_order_id
        body["response"]["resources"]["notes"] = payload.get("notes")
        body["response"]["resources"]["account_no"] = payload.get("account_no")
        return parse_daysmart_created_order(body)

    def delete_order(self, daysmart_order_id):
        self.order_deletes.append(daysmart_order_id)
        if isinstance(self.delete_order_result, Exception):
            raise self.delete_order_result
        body = copy.deepcopy(DELETE_RESPONSE)
        body["response"]["resources"]["id"] = daysmart_order_id
        return parse_daysmart_created_order(body)

    def submit_order(self, daysmart_order_id):
        self.submitted.append(daysmart_order_id)
        if isinstance(self.submit_result, Exception):
            raise self.submit_result

    def create_receipt(self, payload):
        self.receipt_payloads.append(payload)
        if isinstance(self.receipt_id, Exception):
            raise self.receipt_id
        return self.receipt_id

    def fetch_receipt_items(self, receipt_id):
        if isinstance(self.receipt_items, Exception):
            raise self.receipt_items
        return list(self.receipt_items)

    def update_receipt_item(self, receipt_item_id, payload):
        self.receipt_item_updates.append((receipt_item_id, payload))

    def post_receipt(self, receipt_id):
        if isinstance(self.post_result, Exception):
            raise self.post_result
        self.posted_receipts.append(receipt_id)     # the detail now says Posted

    def fetch_order_lines(self, daysmart_order_id):
        # The fixture's lines belong to order 793009; every other order is
        # served an empty (but well-formed) paginator.
        from schemas.purchase_orders import parse_daysmart_order_item_page
        if daysmart_order_id in self.line_fetch_failures:
            raise self.line_fetch_failures[daysmart_order_id]
        if daysmart_order_id == 793009:
            return parse_daysmart_order_item_page(json.loads(
                (FIXTURES / "daysmart_order_item_list_793009.json").read_text(encoding="utf-8")))
        return parse_daysmart_order_item_page({"response": {"resources": {
            "current_page": 1, "data": [], "last_page": 1, "per_page": 50, "total": 0},
            "message": {"code": 200}}})

    def fetch_receipts(self, page=1, purchase_order_id=None):
        from schemas.purchase_orders import (
            DaysmartReceiptPageParseResult, parse_daysmart_receipt_page)
        raw = copy.deepcopy(RECEIPT_LIST)
        # The fixture's receipts belong to orders older than the order page;
        # the first one (822801) is re-homed onto order 791640 so ONE links.
        raw["response"]["resources"][0]["purchase_order_id"] = 791640
        parsed = parse_daysmart_receipt_page(raw)
        receipts = (parsed.receipts if purchase_order_id is None
                    else [r for r in parsed.receipts if r.purchase_order_id == purchase_order_id])
        return DaysmartReceiptPageParseResult(
            receipts=receipts, issues=[],
            meta=DaysmartPageMetaV1(total=len(receipts), per_page=50, current_page=1, last_page=1))

    def _detail_body(self, daysmart_receipt_id):
        if daysmart_receipt_id in self.receipt_fetch_failures:
            raise self.receipt_fetch_failures[daysmart_receipt_id]
        if daysmart_receipt_id == 822801:
            return copy.deepcopy(RECEIPT_DETAIL)
        items = []
        if daysmart_receipt_id == self.receipt_id:
            # The rows Daysmart generated for the receipt we just created —
            # one per PO line, received as ordered.
            items = [{"id": ref.daysmart_receipt_item_id,
                      "purchase_order_item_id": ref.daysmart_po_item_id,
                      "purchase_order_id": ref.purchase_order_id,
                      "item_id": ref.post_to_item_id,
                      "purchase_order_receipt_id": daysmart_receipt_id,
                      "quantity_received": 12, "quantity_in_stock": 12,
                      "amount": 96, "tax": "0.00", "status": 0, "is_active": True}
                     for ref in self.receipt_items]
        php = {"date": "2026-09-01 03:00:00.000000", "timezone_type": 3, "timezone": "UTC"}
        posted = daysmart_receipt_id in self.posted_receipts
        # An UNPOSTED receipt's detail header names no order (wire).
        return {"response": {"resources": {
            "id": daysmart_receipt_id, "index": f"Receipt #{daysmart_receipt_id}",
            "status": ({"id": 1, "label": "Posted"} if posted else {"id": 0, "label": "In Process"}),
            "date": php,
            "invoice_number": "888046260901", "amount": "96.00", "tax": "0.00", "shipping": "0.00",
            "tax_percentage": None, "tax_type": 1, "notes": None, "is_active": True,
            "is_with_order": 1, "purchase_order_id": None, "supplier_id": "3cd1ace0-ddd6-4a",
            "supplier": {"id": "3cd1ace0-ddd6-4a", "name": "Alfamedic"}, "business_id": "b",
            "post_by": None, "post_at": None, "create_by": "u", "create_at": php,
            "update_by": "u", "update_at": php, "receipt_items": items},
            "message": {"code": 200}}}

    def fetch_receipt(self, daysmart_receipt_id):
        from schemas.purchase_orders import parse_daysmart_receipt_detail
        return parse_daysmart_receipt_detail(self._detail_body(daysmart_receipt_id))

    def fetch_receipt_rows(self, daysmart_receipt_id):
        from schemas.purchase_orders import parse_daysmart_receipt_rows
        return parse_daysmart_receipt_rows(self._detail_body(daysmart_receipt_id))

    def update_receipt(self, daysmart_receipt_id, payload):
        self.receipt_header_updates.append((daysmart_receipt_id, payload))
        if isinstance(self.receipt_header_result, Exception):
            raise self.receipt_header_result

    def delete_order_item(self, daysmart_item_id):
        self.deleted_items.append(daysmart_item_id)
        if isinstance(self.delete_result, Exception):
            raise self.delete_result

    def get_suppliers(self):
        return [{"daysmart_id": "3cd1ace0-ddd6-4a", "name": "Alfamedic"},
                {"daysmart_id": "203aeb37-755c-42", "name": "Advance Vet Supplies"}]

    def search_inventory_items(self, query):
        return [{"item_id": "DIT-ed47392c-6e6",
                 "name": "10009870 - Hills F9 c/d 6kg", "container_type": "Bag(s)"}]


@pytest.fixture()
def daysmart():
    return FakeDaysmart()


@pytest.fixture()
def client(daysmart):
    main.app.dependency_overrides[require_user] = lambda: _Admin()
    main.app.dependency_overrides[get_po_daysmart] = lambda: daysmart
    # Clean PO tables between tests — the app shares one temp DB.
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
        "notes": "api test order",
    }
    body.update(overrides)
    return body


LINE = {"description": "Douxo S3 Pyo Shampoo 200ml", "quantity": 12,
        "quantity_uom": "BOTTLE", "unit_price": "96.00",
        "daysmart_inventory_item_id": "DIT-ebbd2f4c-6e6"}


def pending_line(client, po_uuid) -> str:
    """A line staged locally and never sent (no inventory id → 'pending')."""
    body = {k: v for k, v in LINE.items() if k != "daysmart_inventory_item_id"}
    data = client.post(f"/purchase-orders/{po_uuid}/lines", json=[body]).json()
    assert data["results"][0]["outcome"] == "pending", data
    return data["results"][0]["line"]["line_uuid"]


def synced_line(client, daysmart):
    """A PO (793009) with ONE line that lives in Daysmart (line id 3924916)."""
    po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
    daysmart.order_item_fetches = [
        [], [DaysmartOrderItemRef(daysmart_item_id=3924916, inventory_item_id=LINE["daysmart_inventory_item_id"])]]
    added = client.post(f"/purchase-orders/{po_uuid}/lines", json=[LINE]).json()
    line = added["results"][0]["line"]
    assert line["sync_status"] == "synced", added
    daysmart.saved_line_payloads.clear()
    return po_uuid, line["line_uuid"]


class TestCreate:
    def test_api1_create_lands_in_daysmart_and_locally(self, client):
        response = client.post("/purchase-orders", json=create_body())
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["outcome"] == "synced"
        assert data["po"]["daysmart_id"] == 793009
        assert data["po"]["daysmart_index"] == 524
        assert data["po"]["sync_status"] == "synced"
        assert data["po"]["lines"] == []                       # a header create carries no lines

    def test_api1b_lines_in_the_create_body_are_refused(self, client):
        body = create_body(lines=[LINE])
        response = client.post("/purchase-orders", json=body)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "PO_LINES_NOT_ACCEPTED"
        assert client.get("/purchase-orders").json()["total"] == 0

    def test_api2_invalid_body_is_422_with_named_issues_nothing_saved(self, client):
        body = create_body()
        body["supplier_daysmart_id"] = ""
        response = client.post("/purchase-orders", json=body)
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert detail["code"] == "PO_VALIDATION_FAILED"
        assert any(issue["field_path"] == "supplier_daysmart_id"
                   for issue in detail["issues"])
        session = database.SessionLocal()
        try:
            assert session.query(models.PurchaseOrder).count() == 0
        finally:
            session.close()

    def test_api3_daysmart_down_still_saves_the_draft(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        response = client.post("/purchase-orders", json=create_body())
        assert response.status_code == 201
        data = response.json()
        assert data["outcome"] == "sync_failed"
        assert data["po"]["daysmart_id"] is None
        assert data["po"]["sync_status"] == "sync_failed"   # retryable, saved
        # The CAUSE rides the response — the caller acts without DB access.
        failure = data["failure"]
        assert failure["step"] == "create_header"
        assert failure["outcome"] == "daysmart_down"
        assert failure["error_code"] == "DAYSMART_UNAVAILABLE"
        assert "down" in failure["error_detail"]

    def test_api3b_synced_create_carries_no_failure_block(self, client):
        data = client.post("/purchase-orders", json=create_body()).json()
        assert data["outcome"] == "synced"
        assert "failure" not in data


class TestReferenceData:
    def test_ref1_supplier_list(self, client):
        response = client.get("/purchase-orders/suppliers")
        assert response.status_code == 200
        suppliers = response.json()["suppliers"]
        assert {"daysmart_id": "3cd1ace0-ddd6-4a", "name": "Alfamedic"} in suppliers

    def test_ref2_item_search_returns_dit_ids(self, client):
        response = client.get("/purchase-orders/item-search", params={"q": "hills"})
        assert response.status_code == 200
        items = response.json()["items"]
        assert items[0]["item_id"] == "DIT-ed47392c-6e6"

    def test_ref3_item_search_ignores_too_short_query(self, client):
        assert client.get("/purchase-orders/item-search", params={"q": "h"}).json()["items"] == []

    def test_ref4_reference_endpoints_require_auth(self, daysmart):
        main.app.dependency_overrides.pop(require_user, None)
        main.app.dependency_overrides[get_po_daysmart] = lambda: daysmart
        try:
            c = TestClient(main.app)
            assert c.get("/purchase-orders/suppliers").status_code == 401
            assert c.get("/purchase-orders/item-search", params={"q": "hi"}).status_code == 401
        finally:
            main.app.dependency_overrides.pop(get_po_daysmart, None)


class TestAddLines:
    def test_lines_are_explicitly_added_and_backfilled(self, client, daysmart):
        body = create_body()
        po_uuid = client.post("/purchase-orders", json=body).json()["po"]["po_uuid"]
        item_id = "DIT-ebbd2f4c-6e6"
        daysmart.order_item_fetches = [
            [],
            [DaysmartOrderItemRef(
                daysmart_item_id=3924916,
                inventory_item_id=item_id,
            )],
        ]

        response = client.post(f"/purchase-orders/{po_uuid}/lines", json=[{
            "description": "Douxo S3 Pyo Shampoo 200ml",
            "quantity": 12,
            "quantity_uom": "BOTTLE",
            "unit_price": "96.00",
            "daysmart_inventory_item_id": item_id,
        }])

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["summary"] == {"synced": 1}
        result = data["results"][0]
        assert result["outcome"] == "synced"
        assert result["line"]["daysmart_item_id"] == 3924916
        assert result["line"]["daysmart_inventory_item_id"] == item_id
        assert daysmart.saved_line_payloads[0][0]["purchase_order_id"] == "793009"

    def test_lines_require_a_synced_header(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        po_uuid = client.post(
            "/purchase-orders", json=create_body()).json()["po"]["po_uuid"]

        response = client.post(f"/purchase-orders/{po_uuid}/lines", json=[{
            "description": "not sent",
            "quantity": 1,
            "quantity_uom": "EA",
            "unit_price": "1.00",
            "daysmart_inventory_item_id": "DIT-one",
        }])

        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "PO_NOT_SYNCED"
        assert daysmart.saved_line_payloads == []


class TestReads:
    def test_api4_list_serves_local_data_with_filters(self, client):
        client.post("/purchase-orders", json=create_body())
        response = client.get("/purchase-orders")
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["items"][0]["supplier_name"] == "Alfamedic"

        assert client.get("/purchase-orders",
                          params={"sync_status": "draft"}).json()["total"] == 0
        assert client.get("/purchase-orders",
                          params={"sync_status": "synced"}).json()["total"] == 1

    def test_api5_detail_includes_lines_and_404s_unknown(self, client):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        response = client.get(f"/purchase-orders/{po_uuid}")
        assert response.status_code == 200
        assert "lines" in response.json()["po"] and "receipts" in response.json()["po"]
        assert client.get("/purchase-orders/no-such-uuid").status_code == 404

    def test_api5b_detail_carries_the_full_attempt_history(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        attempts = client.get(f"/purchase-orders/{po_uuid}").json()["sync_attempts"]
        assert attempts, "the attempt log must be visible on the detail"
        newest = attempts[0]   # newest first
        assert newest["step"] == "create_header"
        assert newest["outcome"] == "daysmart_down"
        assert newest["error_code"] == "DAYSMART_UNAVAILABLE"
        assert newest["error_detail"]
        assert newest["started_at"] and newest["finished_at"]


class TestSubmit:
    def test_api6_submit_calls_daysmart_then_pulls_the_po(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        submitted = copy.deepcopy(DETAIL_RESPONSE)
        submitted["response"]["resources"]["status"] = 2       # what Daysmart says after submit
        daysmart.get_order_result = submitted
        response = client.post(f"/purchase-orders/{po_uuid}/submit")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "submitted" and data["mirror_error"] is None
        assert daysmart.submitted == [793009]
        assert data["po"]["daysmart_status"] == "submitted"      # pulled, no refresh needed
        assert len(data["po"]["lines"]) == 4                     # the whole PO came with it

    def test_api6b_submitting_an_unsynced_po_is_409(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        response = client.post(f"/purchase-orders/{po_uuid}/submit")
        assert response.status_code == 409

    def test_api6c_daysmart_refusal_is_409_with_detail(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        daysmart.submit_result = DaysmartRefused("order has no items")
        response = client.post(f"/purchase-orders/{po_uuid}/submit")
        assert response.status_code == 409
        assert "refused" in response.json()["detail"]["code"].lower()


class TestResolve:
    def stuck_draft(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        daysmart.create_result = CREATE_RESPONSE
        return po_uuid

    def test_api7_resolve_links_by_daysmart_id(self, client, daysmart):
        po_uuid = self.stuck_draft(client, daysmart)
        response = client.post(f"/purchase-orders/{po_uuid}/resolve",
                               json={"daysmart_id": 793009})
        assert response.status_code == 200, response.text
        po = response.json()["po"]
        assert po["daysmart_id"] == 793009
        assert po["sync_status"] == "synced"
        assert po["daysmart_status"] == "open" and len(po["lines"]) == 4   # linked AND pulled
        assert response.json()["mirror_error"] is None

    def test_api7b_resolving_to_an_already_linked_order_is_409(self, client, daysmart):
        client.post("/purchase-orders", json=create_body())   # owns 793009
        po_uuid = self.stuck_draft(client, daysmart)
        response = client.post(f"/purchase-orders/{po_uuid}/resolve",
                               json={"daysmart_id": 793009})
        assert response.status_code == 409


class TestSyncAndReview:
    def test_api8_sync_endpoint_runs_a_full_cycle(self, client):
        response = client.post("/purchase-orders/sync")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["completed"] is True
        assert data["created"] == 50          # the whole fixture page landed
        assert client.get("/purchase-orders").json()["total"] == 50

    def test_api9_review_shows_the_three_buckets(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        client.post("/purchase-orders", json=create_body())   # a failed sync
        session = database.SessionLocal()
        session.add(models.PoValidationIssueRecord(
            code="PO_STATUS_UNKNOWN", field_path="status.id", raw_value="3",
            expected="one of [1, 4]", daysmart_id=999, payload_context="fetch",
            created_at="2026-09-01T10:00:00+00:00"))
        session.commit()
        session.close()

        response = client.get("/purchase-orders/review")
        assert response.status_code == 200
        data = response.json()
        assert data["sync_failed"]["total"] == 1
        assert data["open_issues"]["total"] == 1
        assert data["open_issues"]["items"][0]["code"] == "PO_STATUS_UNKNOWN"


class TestReceiptEndpoints:
    RECEIPT_BODY = {
        "invoice_number": "888046260901",
        "receipt_date": "2026-09-01 11:00:00",
        "shipping": "0.00", "tax": "0.00", "tax_type": 1, "notes": "",
        "items": [{"quantity_received": 12, "quantity_in_stock": 12,
                   "amount": "96.00", "post_to_item_id": "DIT-ebc6f1b0-6e6",
                   "daysmart_po_item_id": 3924916, "lot_number": "L-1"}],
    }

    def test_api10_receipt_created_local_first(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        response = client.post(f"/purchase-orders/{po_uuid}/receipts",
                               json=self.RECEIPT_BODY)
        assert response.status_code == 201, response.text
        receipt = response.json()["receipt"]
        assert receipt["daysmart_receipt_id"] == 50123
        assert receipt["sync_status"] == "synced"
        assert receipt["post_status"] == "draft"    # never auto-posted
        assert receipt["items"][0]["daysmart_receipt_item_id"] == 3936614
        assert daysmart.receipt_payloads[0]["order_id"] == ["793009"]
        assert daysmart.receipt_item_updates[0][0] == 3936614

    def test_api10d_header_only_receipt_is_complete_rows_received_as_ordered(self, client, daysmart):
        po_uuid, _ = synced_line(client, daysmart)          # PO with a Daysmart line (3924916)
        body = {k: v for k, v in self.RECEIPT_BODY.items() if k != "items"}
        response = client.post(f"/purchase-orders/{po_uuid}/receipts", json=body)
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["outcome"] == "synced"
        assert (data["generated_rows"], data["adjustments_applied"]) == (1, 0)
        assert daysmart.receipt_item_updates == []           # nothing to send
        item = data["receipt"]["items"][0]
        assert item["daysmart_receipt_item_id"] == 3936614
        assert (item["quantity_received"], item["sync_status"]) == ("12", "synced")
        assert item["daysmart_quantity_received"] == 12

    def test_api10e_an_adjustment_names_our_line(self, client, daysmart):
        po_uuid, line_uuid = synced_line(client, daysmart)
        body = {**self.RECEIPT_BODY, "items": [
            {"line_uuid": line_uuid, "quantity_received": 10, "quantity_in_stock": 10,
             "amount": "96.00", "lot_number": "L-2"}]}
        response = client.post(f"/purchase-orders/{po_uuid}/receipts", json=body)
        assert response.status_code == 201, response.text
        data = response.json()
        assert data["outcome"] == "synced" and data["adjustments_applied"] == 1
        (row_id, payload), = daysmart.receipt_item_updates
        assert row_id == 3936614 and payload["quantity_received"] == 10
        assert data["receipt"]["items"][0]["quantity_received"] == "10"

    def test_api10f_an_adjustment_naming_an_unknown_line_is_422(self, client, daysmart):
        po_uuid, _ = synced_line(client, daysmart)
        body = {**self.RECEIPT_BODY, "items": [
            {"line_uuid": "nope", "quantity_received": 1, "quantity_in_stock": 1, "amount": "1"}]}
        response = client.post(f"/purchase-orders/{po_uuid}/receipts", json=body)
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "RECEIPT_VALIDATION_FAILED"
        assert daysmart.receipt_payloads == []

    def test_api10b_receipt_against_unsynced_po_is_409(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        response = client.post(f"/purchase-orders/{po_uuid}/receipts",
                               json=self.RECEIPT_BODY)
        assert response.status_code == 409

    def test_api10c_invalid_receipt_date_is_422_without_daysmart_call(
            self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        body = {**self.RECEIPT_BODY, "receipt_date": "not-a-date"}

        response = client.post(
            f"/purchase-orders/{po_uuid}/receipts", json=body)

        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "RECEIPT_VALIDATION_FAILED"
        assert daysmart.receipt_payloads == []

    def test_api11_posting_a_receipt(self, client):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        receipt_uuid = client.post(f"/purchase-orders/{po_uuid}/receipts",
                                   json=self.RECEIPT_BODY).json()["receipt"]["receipt_uuid"]
        response = client.post(
            f"/purchase-orders/{po_uuid}/receipts/{receipt_uuid}/post")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "posted" and data["mirror_error"] is None
        receipt = data["receipt"]
        assert (receipt["post_status"], receipt["daysmart_status"]) == ("posted", "posted")   # pulled back


class TestAuth:
    def test_api12_every_endpoint_requires_a_user(self, daysmart):
        main.app.dependency_overrides.pop(require_user, None)
        main.app.dependency_overrides[get_po_daysmart] = lambda: daysmart
        try:
            client = TestClient(main.app)
            assert client.get("/purchase-orders").status_code == 401
            assert client.post("/purchase-orders", json=create_body()).status_code == 401
            assert client.post("/purchase-orders/sync").status_code == 401
            assert client.post("/purchase-orders/x/refresh").status_code == 401
            assert client.put("/purchase-orders/x/lines/y", json=LINE).status_code == 401
            assert client.delete("/purchase-orders/x/lines/y").status_code == 401
            assert client.put("/purchase-orders/x/receipts/y", json={}).status_code == 401
            assert client.put("/purchase-orders/x", json={}).status_code == 401
            assert client.delete("/purchase-orders/x").status_code == 401
            assert client.post("/purchase-orders/x/receipts/y/retry").status_code == 401
            assert client.put("/purchase-orders/x/receipts/y/items/z", json={}).status_code == 401
        finally:
            main.app.dependency_overrides.pop(get_po_daysmart, None)


class TestIdempotency:
    """Client↔Rosetta idempotency for the CREATION endpoints.

    The Daysmart side is covered by the snapshot-diff; this layer covers the
    double-click / client-retry: the same Idempotency-Key replays the stored
    response instead of creating a second PO (and a second Daysmart order).
    Same key + different body = 409 — a key identifies ONE request's
    material, mirroring the catalogue submission convention.
    """

    def test_idem1_same_key_same_body_replays_not_duplicates(self, client):
        headers = {"Idempotency-Key": "po-create-abc123"}
        first = client.post("/purchase-orders", json=create_body(), headers=headers)
        assert first.status_code == 201
        second = client.post("/purchase-orders", json=create_body(), headers=headers)
        assert second.status_code == 201
        assert second.json()["po"]["po_uuid"] == first.json()["po"]["po_uuid"]
        session = database.SessionLocal()
        try:
            assert session.query(models.PurchaseOrder).count() == 1
        finally:
            session.close()

    def test_idem2_same_key_different_body_is_409(self, client):
        headers = {"Idempotency-Key": "po-create-abc123"}
        assert client.post("/purchase-orders", json=create_body(),
                           headers=headers).status_code == 201
        clash = client.post("/purchase-orders", json=create_body(notes="different"),
                            headers=headers)
        assert clash.status_code == 409
        assert clash.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    def test_idem3_no_key_no_protection(self, client):
        client.post("/purchase-orders", json=create_body())
        client.post("/purchase-orders", json=create_body())
        session = database.SessionLocal()
        try:
            # Two identical bodies without a key = two POs, on purpose —
            # repeated identical orders are legitimate (the weekly reorder).
            assert session.query(models.PurchaseOrder).count() == 2
        finally:
            session.close()

    def test_idem4_same_key_redrive_converges_without_duplicating(self, client, daysmart):
        # Outcome-aware semantics (Moses, 2026-09-01): a provably-never-
        # landed failure (daysmart_down) IS re-driven by a same-key repeat —
        # Stripe semantics: the key makes retrying safe, not forbidden.
        # The invariant that survives from the old rule: ONE local PO, ever.
        daysmart.create_result = DaysmartUnavailable("down")
        headers = {"Idempotency-Key": "po-create-failed1"}
        first = client.post("/purchase-orders", json=create_body(), headers=headers)
        assert first.json()["outcome"] == "sync_failed"
        daysmart.create_result = CREATE_RESPONSE   # Daysmart is back up …
        replay = client.post("/purchase-orders", json=create_body(), headers=headers)
        assert replay.json()["outcome"] == "synced"        # … re-driven
        assert replay.json()["po"]["po_uuid"] == first.json()["po"]["po_uuid"]
        session = database.SessionLocal()
        try:
            assert session.query(models.PurchaseOrder).count() == 1
        finally:
            session.close()

    def test_idem5_receipt_creation_honours_the_key_too(self, client):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        body = dict(TestReceiptEndpoints.RECEIPT_BODY)
        headers = {"Idempotency-Key": "receipt-xyz"}
        first = client.post(f"/purchase-orders/{po_uuid}/receipts",
                            json=body, headers=headers)
        assert first.status_code == 201
        second = client.post(f"/purchase-orders/{po_uuid}/receipts",
                             json=body, headers=headers)
        assert second.status_code == 201
        assert (second.json()["receipt"]["receipt_uuid"]
                == first.json()["receipt"]["receipt_uuid"])
        session = database.SessionLocal()
        try:
            assert session.query(models.PoReceipt).count() == 1
        finally:
            session.close()


class TestRefresh:
    """POST /purchase-orders/{po_uuid}/refresh — pull ONE PO now: header
    (detail surface) + lines. Loud on failure; nothing queued."""

    def test_rx1_refresh_pulls_header_and_lines_together(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        submitted = copy.deepcopy(DETAIL_RESPONSE)
        submitted["response"]["resources"]["status"] = 2          # submitted in their UI meanwhile
        submitted["response"]["resources"]["status_label"] = "Submitted"
        daysmart.get_order_result = submitted
        response = client.post(f"/purchase-orders/{po_uuid}/refresh")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["header_changed"] is True
        assert data["lines"]["created"] == 4
        po = data["po"]
        assert po["daysmart_status"] == "submitted"
        assert po["item_count"] == 4
        assert "lines_synced_at" not in po                        # the marker is gone
        mirrored = po["lines"]
        assert len(mirrored) == 4 and all(l["sync_status"] == "synced" for l in mirrored)
        assert {l["daysmart_item_id"] for l in mirrored} == {3969381, 3969382, 3969383, 3969384}

    def test_rx2_refresh_on_an_unsynced_po_is_409(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        response = client.post(f"/purchase-orders/{po_uuid}/refresh")
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "PO_NOT_SYNCED"

    def test_rx3_refresh_fails_loudly_and_applies_nothing_when_daysmart_does(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        daysmart.get_order_result = DaysmartUnavailable("down")
        assert client.post(f"/purchase-orders/{po_uuid}/refresh").status_code == 502
        daysmart.get_order_result = DETAIL_RESPONSE
        daysmart.line_fetch_failures = {793009: DaysmartTimeout("slow")}
        assert client.post(f"/purchase-orders/{po_uuid}/refresh").status_code == 504
        po = client.get(f"/purchase-orders/{po_uuid}").json()["po"]
        assert po["lines"] == []                                            # nothing mirrored

    def test_rx4_sync_reports_line_failures_per_order(self, client, daysmart):
        daysmart.line_fetch_failures = {791640: DaysmartUnavailable("down")}
        response = client.post("/purchase-orders/sync")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["completed"] is True and data["created"] == 50
        assert (data["lines_synced"], data["lines_failed"]) == (49, 1)
        assert data["line_failures"] == [
            {"daysmart_id": 791640, "error_code": "DAYSMART_UNAVAILABLE", "message": "down"}]

    def test_rx5_the_line_only_trigger_endpoint_is_gone(self, client):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        assert client.post(f"/purchase-orders/{po_uuid}/refresh-lines").status_code == 404


class TestEditLine:
    """PUT /purchase-orders/{po_uuid}/lines/{line_uuid} — the line router."""

    def test_el1_editing_a_daysmart_line_is_an_upsert_by_line_id_then_a_pull(self, client, daysmart):
        po_uuid, line_uuid = synced_line(client, daysmart)
        response = client.put(f"/purchase-orders/{po_uuid}/lines/{line_uuid}",
                              json={**LINE, "quantity": 24})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "synced"
        assert data["line"]["quantity"] == "24"                       # the edit, locally
        payload = daysmart.saved_line_payloads[-1][0]
        assert payload["purchase_order_item_id"] == 3924916            # UPDATE, not a second add
        assert payload["quantity_ordered"] == 24
        assert payload["line_total"] == "2304.00"
        assert data["mirror_error"] is None                            # the pull after the write worked

    def test_el2_the_inventory_item_is_identity_and_cannot_be_edited(self, client, daysmart):
        po_uuid, line_uuid = synced_line(client, daysmart)
        response = client.put(f"/purchase-orders/{po_uuid}/lines/{line_uuid}",
                              json={**LINE, "daysmart_inventory_item_id": "DIT-other"})
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "LINE_IDENTITY_IMMUTABLE"
        assert daysmart.saved_line_payloads == []

    def test_el3_a_never_sent_line_is_edited_locally_only(self, client, daysmart):
        po = client.post("/purchase-orders", json=create_body()).json()["po"]
        line_uuid = pending_line(client, po["po_uuid"])
        response = client.put(f"/purchase-orders/{po['po_uuid']}/lines/{line_uuid}",
                              json={**LINE, "description": "renamed"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "pending"
        assert data["line"]["description"] == "renamed"
        assert daysmart.saved_line_payloads == []                      # an edit never sends

    def test_el4_a_refusal_keeps_the_local_edit_and_says_why(self, client, daysmart):
        po_uuid, line_uuid = synced_line(client, daysmart)
        daysmart.line_save_result = DaysmartRefused("order already submitted")
        response = client.put(f"/purchase-orders/{po_uuid}/lines/{line_uuid}",
                              json={**LINE, "quantity": 24})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "needs_review"
        assert data["error_code"] == "DAYSMART_REFUSED"
        assert data["line"]["quantity"] == "24"                        # what we WANT is kept
        assert data["line"]["sync_status"] == "needs_review"

    def test_el5_unknown_line_is_404_and_a_bad_body_is_422(self, client, daysmart):
        po_uuid, line_uuid = synced_line(client, daysmart)
        assert client.put(f"/purchase-orders/{po_uuid}/lines/nope", json=LINE).status_code == 404
        response = client.put(f"/purchase-orders/{po_uuid}/lines/{line_uuid}",
                              json={**LINE, "quantity": -1})
        assert response.status_code == 422
        assert response.json()["detail"]["issues"][0]["field_path"] == "quantity"


class TestDeleteLine:
    """DELETE /purchase-orders/{po_uuid}/lines/{line_uuid}."""

    def test_dl1_deleting_a_daysmart_line_calls_daysmart_and_keeps_the_row_as_deleted(self, client, daysmart):
        po_uuid, line_uuid = synced_line(client, daysmart)
        response = client.delete(f"/purchase-orders/{po_uuid}/lines/{line_uuid}")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "deleted"
        assert daysmart.deleted_items == [3924916]
        assert data["line"]["sync_status"] == "deleted"
        assert data["line"]["daysmart_is_active"] is False
        lines = client.get(f"/purchase-orders/{po_uuid}").json()["po"]["lines"]
        assert [l["sync_status"] for l in lines if l["line_uuid"] == line_uuid] == ["deleted"]

    def test_dl2_a_never_sent_line_is_simply_removed(self, client, daysmart):
        po = client.post("/purchase-orders", json=create_body()).json()["po"]
        line_uuid = pending_line(client, po["po_uuid"])
        keeper = pending_line(client, po["po_uuid"])
        response = client.delete(f"/purchase-orders/{po['po_uuid']}/lines/{line_uuid}")
        assert response.status_code == 200, response.text
        data = response.json()
        assert (data["outcome"], data["line"], data["error_code"]) == ("deleted", None, None)
        assert daysmart.deleted_items == []
        remaining = client.get(f"/purchase-orders/{po['po_uuid']}").json()["po"]["lines"]
        assert [l["line_uuid"] for l in remaining] == [keeper]

    def test_dl3_a_refusal_or_outage_is_reported_and_the_line_stays(self, client, daysmart):
        po_uuid, line_uuid = synced_line(client, daysmart)
        daysmart.delete_result = DaysmartRefused("order already submitted")
        data = client.delete(f"/purchase-orders/{po_uuid}/lines/{line_uuid}").json()
        assert (data["outcome"], data["error_code"]) == ("needs_review", "DAYSMART_REFUSED")
        daysmart.delete_result = DaysmartUnavailable("down")
        data = client.delete(f"/purchase-orders/{po_uuid}/lines/{line_uuid}").json()
        assert (data["outcome"], data["error_code"]) == ("sync_failed", "DAYSMART_UNAVAILABLE")
        assert data["line"]["daysmart_item_id"] == 3924916            # still Daysmart's line


def _po_uuid_of(daysmart_id: int) -> str:
    session = database.SessionLocal()
    try:
        return session.query(models.PurchaseOrder).filter_by(daysmart_id=daysmart_id).one().po_uuid
    finally:
        session.close()


def _linked_receipts() -> list[int]:
    """Fixture truth: receipts (first one re-homed onto 791640) whose order
    is on the order page."""
    order_ids = {o["id"] for o in LIST_PAGE1["response"]["resources"]}
    rows = copy.deepcopy(RECEIPT_LIST["response"]["resources"])
    rows[0]["purchase_order_id"] = 791640
    return [r["id"] for r in rows if r["purchase_order_id"] in order_ids]


class TestReceiptPull:
    """Receipts ride along with the PO pull — sync, refresh, and the PO detail."""

    def test_rq1_sync_pulls_receipts_and_the_po_detail_shows_them(self, client):
        data = client.post("/purchase-orders/sync").json()
        rc = data["receipts"]
        linked = _linked_receipts()
        assert rc["completed"] is True and rc["error"] is None
        assert (rc["created"], rc["unlinked"]) == (len(linked), 50 - len(linked))
        assert (rc["rows_synced"], rc["failures"]) == (len(linked), [])
        po = client.get(f"/purchase-orders/{_po_uuid_of(791640)}").json()["po"]
        receipt = next(r for r in po["receipts"] if r["daysmart_receipt_id"] == 822801)
        assert (receipt["daysmart_status"], receipt["post_status"]) == ("posted", "posted")
        assert receipt["invoice_number"] == "888046260817"
        assert len(receipt["items"]) == 2
        assert receipt["items"][0]["daysmart_quantity_received"] == 6

    def test_rq2_refresh_pulls_this_pos_receipts(self, client, daysmart):
        client.post("/purchase-orders/sync")
        detail = copy.deepcopy(DETAIL_RESPONSE)
        detail["response"]["resources"]["id"] = 791640
        daysmart.get_order_result = detail
        response = client.post(f"/purchase-orders/{_po_uuid_of(791640)}/refresh")
        assert response.status_code == 200, response.text
        data = response.json()
        mine = data["receipts"]
        assert mine["created"] == 0 and mine["unchanged"] >= 1 and mine["rows_synced"] == mine["unchanged"]
        assert 822801 in [r["daysmart_receipt_id"] for r in data["po"]["receipts"]]

    def test_rq3_receipt_row_failures_are_reported_in_the_sync_response(self, client, daysmart):
        daysmart.receipt_fetch_failures = {822801: DaysmartUnavailable("down")}
        rc = client.post("/purchase-orders/sync").json()["receipts"]
        assert (rc["rows_synced"], rc["rows_failed"]) == (len(_linked_receipts()) - 1, 1)
        assert rc["failures"] == [{"daysmart_receipt_id": 822801,
                                   "error_code": "DAYSMART_UNAVAILABLE", "message": "down"}]


class TestReceiptEdits:
    """PUT header / PUT row on an existing receipt — the receipts router."""

    HEADER = {"invoice_number": "888046260817", "receipt_date": "2026-08-16T16:00:00+00:00",
              "shipping": "1", "tax": "2", "notes": "Test Receipt"}

    def receipt(self, client, daysmart):
        po_uuid, _ = synced_line(client, daysmart)
        body = {k: v for k, v in TestReceiptEndpoints.RECEIPT_BODY.items() if k != "items"}
        data = client.post(f"/purchase-orders/{po_uuid}/receipts", json=body).json()
        assert data["outcome"] == "synced", data
        return po_uuid, data["receipt"]

    def test_re1_header_edit(self, client, daysmart):
        po_uuid, receipt = self.receipt(client, daysmart)
        response = client.put(f"/purchase-orders/{po_uuid}/receipts/{receipt['receipt_uuid']}", json=self.HEADER)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "synced" and data["mirror_error"] is None
        (receipt_id, payload), = daysmart.receipt_header_updates
        assert receipt_id == 50123
        assert payload == {"date": 1786896000, "tax": "2", "shipping": "1",
                           "invoice_number": "888046260817", "notes": "Test Receipt"}
        assert data["receipt"]["invoice_number"] == "888046260817"

    def test_re2_row_edit(self, client, daysmart):
        po_uuid, receipt = self.receipt(client, daysmart)
        item_uuid = receipt["items"][0]["item_uuid"]
        response = client.put(
            f"/purchase-orders/{po_uuid}/receipts/{receipt['receipt_uuid']}/items/{item_uuid}",
            json={"quantity_received": 10, "quantity_in_stock": 10, "amount": "96.00",
                  "lot_number": "3", "expiration_date": 1790179200, "manufacturer": "none"})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "synced"
        row_id, payload = daysmart.receipt_item_updates[-1]
        assert row_id == 3936614 and payload["quantity_received"] == 10 and payload["lot_number"] == "3"
        item = data["receipt"]["items"][0]
        assert (item["quantity_received"], item["lot_number"], item["sync_status"]) == ("10", "3", "synced")

    def test_re3_a_posted_receipt_is_409(self, client, daysmart):
        po_uuid, receipt = self.receipt(client, daysmart)
        assert client.post(f"/purchase-orders/{po_uuid}/receipts/{receipt['receipt_uuid']}/post").status_code == 200
        response = client.put(f"/purchase-orders/{po_uuid}/receipts/{receipt['receipt_uuid']}", json=self.HEADER)
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "RECEIPT_POSTED"
        assert daysmart.receipt_header_updates == []

    def test_re4_refusal_is_reported_and_the_edit_stays(self, client, daysmart):
        po_uuid, receipt = self.receipt(client, daysmart)
        daysmart.receipt_header_result = DaysmartRefused("closed")
        data = client.put(f"/purchase-orders/{po_uuid}/receipts/{receipt['receipt_uuid']}", json=self.HEADER).json()
        assert (data["outcome"], data["error_code"]) == ("needs_review", "DAYSMART_REFUSED")
        assert data["receipt"]["invoice_number"] == "888046260817"

    def test_re5_unknown_row_is_404_and_a_bad_date_is_422(self, client, daysmart):
        po_uuid, receipt = self.receipt(client, daysmart)
        base = f"/purchase-orders/{po_uuid}/receipts/{receipt['receipt_uuid']}"
        assert client.put(f"{base}/items/nope", json={"quantity_received": 1, "quantity_in_stock": 1,
                                                      "amount": "1"}).status_code == 404
        response = client.put(base, json={**self.HEADER, "receipt_date": "not-a-date"})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "RECEIPT_VALIDATION_FAILED"
        assert daysmart.receipt_header_updates == []


class TestHeaderUpdateAndDelete:
    """PUT / DELETE /purchase-orders/{po_uuid} — the header after creation."""

    BODY = {"account_no": "-", "order_date": "2026-09-01T18:42:39+08:00",
            "ship_to_id": 861040, "bill_to_id": 861040, "notes": "Test PO creation"}

    def test_pu1_update_writes_through_and_mirrors_the_echo(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        response = client.put(f"/purchase-orders/{po_uuid}", json=self.BODY)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "synced"
        (order_id, payload), = daysmart.order_updates
        assert order_id == 793009
        assert payload == {"account_no": "-", "order_date": 1788259359,        # PO-update.har, verbatim
                           "ship_to_id": 861040, "bill_to_id": 861040, "notes": "Test PO creation"}
        assert (data["po"]["notes"], data["po"]["account_no"]) == ("Test PO creation", "-")

    def test_pu2_a_draft_is_edited_locally(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        data = client.put(f"/purchase-orders/{po_uuid}", json=self.BODY).json()
        assert data["outcome"] == "draft" and data["po"]["notes"] == "Test PO creation"
        assert daysmart.order_updates == []

    def test_pu3_refusal_and_bad_body(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        daysmart.update_result = DaysmartRefused("submitted")
        data = client.put(f"/purchase-orders/{po_uuid}", json=self.BODY).json()
        assert (data["outcome"], data["error_code"]) == ("needs_review", "DAYSMART_REFUSED")
        assert data["po"]["notes"] == "api test order"                        # nothing changed locally
        response = client.put(f"/purchase-orders/{po_uuid}", json={**self.BODY, "order_date": "yesterday"})
        assert response.status_code == 422
        assert response.json()["detail"]["issues"][0]["field_path"] == "order_date"

    def test_pu4_submitted_orders_are_refused_locally(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        session = database.SessionLocal()
        session.query(models.PurchaseOrder).filter_by(po_uuid=po_uuid).update({"daysmart_status": "submitted"})
        session.commit(); session.close()
        response = client.put(f"/purchase-orders/{po_uuid}", json=self.BODY)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "PO_NOT_OPEN"

    def test_pd1_delete_is_soft_in_daysmart_and_kept_locally(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        response = client.delete(f"/purchase-orders/{po_uuid}")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "deleted" and daysmart.order_deletes == [793009]
        assert (data["po"]["sync_status"], data["po"]["is_active"], data["po"]["notes"]) == (
            "deleted", False, "[removed]")
        assert client.get(f"/purchase-orders/{po_uuid}").status_code == 200   # the row stays

    def test_pd2_a_draft_is_cancelled_not_deleted(self, client, daysmart):
        daysmart.create_result = DaysmartUnavailable("down")
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        response = client.delete(f"/purchase-orders/{po_uuid}")
        assert response.status_code == 409 and response.json()["detail"]["code"] == "PO_NOT_SYNCED"

    def test_pd3_delete_failures_are_reported_and_the_po_stays(self, client, daysmart):
        po_uuid = client.post("/purchase-orders", json=create_body()).json()["po"]["po_uuid"]
        daysmart.delete_order_result = DaysmartTimeout("slow")
        data = client.delete(f"/purchase-orders/{po_uuid}").json()
        assert (data["outcome"], data["error_code"]) == ("needs_review", "DAYSMART_TIMEOUT")
        assert data["po"]["sync_status"] == "synced"
        daysmart.delete_order_result = DaysmartUnavailable("down")
        data = client.delete(f"/purchase-orders/{po_uuid}").json()
        assert (data["outcome"], data["error_code"]) == ("sync_failed", "DAYSMART_UNAVAILABLE")


class TestReceiptRetryAndReplay:
    def failed_receipt(self, client, daysmart):
        po_uuid, _ = synced_line(client, daysmart)
        daysmart.receipt_id = DaysmartUnavailable("down")
        body = {k: v for k, v in TestReceiptEndpoints.RECEIPT_BODY.items() if k != "items"}
        data = client.post(f"/purchase-orders/{po_uuid}/receipts", json=body,
                           headers={"Idempotency-Key": "rcpt-1"}).json()
        assert data["outcome"] == "sync_failed" and data["failure"]["outcome"] == "daysmart_down"
        return po_uuid, data["receipt"]["receipt_uuid"], body

    def test_rr1_retry_redrives_a_never_landed_create(self, client, daysmart):
        po_uuid, receipt_uuid, _ = self.failed_receipt(client, daysmart)
        daysmart.receipt_id = 50123                                            # Daysmart is back
        response = client.post(f"/purchase-orders/{po_uuid}/receipts/{receipt_uuid}/retry")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["outcome"] == "synced" and data["receipt"]["daysmart_receipt_id"] == 50123
        assert len(daysmart.receipt_payloads) == 2                             # one per attempt

    def test_rr2_same_key_redrives_only_when_the_first_never_landed(self, client, daysmart):
        po_uuid, receipt_uuid, body = self.failed_receipt(client, daysmart)
        daysmart.receipt_id = 50123
        second = client.post(f"/purchase-orders/{po_uuid}/receipts", json=body,
                             headers={"Idempotency-Key": "rcpt-1"}).json()
        assert second["outcome"] == "synced" and second["receipt"]["receipt_uuid"] == receipt_uuid
        session = database.SessionLocal()
        try:
            assert session.query(models.PoReceipt).count() == 1
        finally:
            session.close()

    def test_rr3_a_timed_out_create_is_never_resent(self, client, daysmart):
        po_uuid, _ = synced_line(client, daysmart)
        daysmart.receipt_id = DaysmartTimeout("slow")
        body = {k: v for k, v in TestReceiptEndpoints.RECEIPT_BODY.items() if k != "items"}
        first = client.post(f"/purchase-orders/{po_uuid}/receipts", json=body,
                            headers={"Idempotency-Key": "rcpt-2"}).json()
        assert first["outcome"] == "needs_review"
        daysmart.receipt_id = 50123
        second = client.post(f"/purchase-orders/{po_uuid}/receipts", json=body,
                             headers={"Idempotency-Key": "rcpt-2"}).json()
        assert second["outcome"] == "needs_review"                             # the stored answer
        retry = client.post(f"/purchase-orders/{po_uuid}/receipts/{first['receipt']['receipt_uuid']}/retry").json()
        assert retry["outcome"] == "needs_review"
        assert len(daysmart.receipt_payloads) == 1                             # never a second create

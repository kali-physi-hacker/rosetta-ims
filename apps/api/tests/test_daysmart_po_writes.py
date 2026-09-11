"""L/W/S/R/FL-groups — line-save, submit-order, receipts, and the
full-scope create saga. TDD, fully mocked; evidence tier: SOURCE
(reconstructed from Daysmart's frontend chunks — see
docs/contracts/daysmart-po-surface.md). First live use confirms; anything
off lands as issues/typed errors, never as corruption.

What this file pins:

- L: the line-payload builder. ``POST inventory/order/item/save`` takes an
  ARRAY of ``{purchase_order_id, item_id, item_name, quantity_ordered,
  price, line_total, notes}``; adding ``purchase_order_item_id`` makes the
  same call an UPDATE (save = upsert). We send exact decimal STRINGS where
  their UI sends floats (probe #2 in the surface doc); ``line_total`` is
  computed exactly (quantity × unit price), never floats.
- W: adapter ``save_order_items`` — write semantics (ONE attempt, no
  transport retry; scripts contain no second response so a hidden retry
  fails structurally); the ack convention ``response.message.code == 200``;
  a well-formed refusal raises the NEW ``DaysmartRefused`` error (their UI
  treats non-200 message codes and ``messages.errors`` as refusals — that
  is neither transport failure nor parse failure, so it gets its own type).
- S: adapter ``submit_order`` — the GENERAL submission step in Daysmart's
  canonical flow (create → add items → Submit Order; their UI requires the
  order to HAVE ITEMS and be status Open). Likely moves status 1 → 2. MWI
  appears only in their error branch, not as a gate. Whether OUR flow
  auto-submits after lines land, or exposes submit as an explicit endpoint,
  is a BizOps decision — these tests pin only the transport call.
- R: adapter receipt calls — ``create_receipt`` (returns the new receipt
  id), ``add_receipt_item`` (NOTE: its error dialect is ``data.messages.
  errors`` — plural, ANOTHER dialect quirk, taken verbatim from their
  source), ``post_receipt``. Receipt FLOW orchestration is out of CIS-09
  v1; these are the transport building blocks.
- FL: the create saga at full scope — header then lines in ONE batched
  save call. Line-send failure semantics: unavailable → retryable
  ``sync_failed`` with the header link KEPT; timeout → ``needs_review``
  and never a blind resend (re-sending item/save duplicates lines — it is
  an upsert only when purchase_order_item_id is present).

Interfaces under test:

    schemas/purchase_orders
        CreatePurchaseOrderLineCommandV1 gains daysmart_inventory_item_id
        build_daysmart_line_payloads(daysmart_order_id, lines) -> list[dict]
    services/daysmart_service.py
        DaysmartRefused(DaysmartError)
        save_order_items(payloads: list[dict]) -> None
        submit_order(daysmart_order_id: int) -> None
        create_receipt(payload: dict) -> int          # new receipt id
        add_receipt_item(receipt_id: int, payload: dict) -> None
        post_receipt(receipt_id: int) -> None
    services/po_create_flow.py
        create_purchase_order(...) now also sends lines after linking
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_writes.db")

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from schemas.purchase_orders import (
    parse_line_commands,
    build_daysmart_line_payloads,
    parse_create_command,
    parse_daysmart_created_order,
    parse_daysmart_order_page,
)
from services.daysmart_service import (
    DaysmartMalformedResponse,
    DaysmartRefused,
    DaysmartService,
    DaysmartTimeout,
    DaysmartUnavailable,
)
from services.po_create_flow import create_purchase_order

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
HKT = timezone(timedelta(hours=8))

CREATE_RESPONSE = json.loads(
    (FIXTURES / "daysmart_order_create_response.json").read_text(encoding="utf-8"))
LIST_PAGE1 = json.loads(
    (FIXTURES / "daysmart_order_list_page1.json").read_text(encoding="utf-8"))

#: Ack shape their UI checks: response.message.code === 200.
ACK_OK = {"response": {"message": {"code": 200}}}
ACK_REFUSED = {"response": {"message": {"code": 400, "errors": "Not allowed"}}}
#: Receipt create returns the new receipt under resources (source tier).
RECEIPT_CREATED = {"response": {"resources": {"id": 50123}, "message": {"code": 200}}}
RECEIPT_DETAIL = json.loads(
    (FIXTURES / "daysmart_receipt_detail_822801.json").read_text(encoding="utf-8"))
RECEIPT_LIST = json.loads(
    (FIXTURES / "daysmart_receipt_list_page1.json").read_text(encoding="utf-8"))
RECEIPT_ITEM_UPDATE_REQUEST = json.loads(
    (FIXTURES / "daysmart_receipt_item_update_request.json").read_text(encoding="utf-8"))
RECEIPT_ITEM_UPDATE_RESPONSE = json.loads(
    (FIXTURES / "daysmart_receipt_item_update_response.json").read_text(encoding="utf-8"))


def command():
    parsed = parse_create_command({
        "supplier_daysmart_id": "3cd1ace0-ddd6-4a",
        "supplier_name": "Alfamedic",
        "order_date": "2026-08-29T11:00:00+08:00",
        "notes": "test order",
    })
    assert parsed.command is not None, parsed.issues
    return parsed.command


def line_commands(with_item_ids=True):
    lines = [
        {"description": "Douxo S3 Pyo Shampoo 200ml", "quantity": 12,
         "quantity_uom": "BOTTLE", "unit_price": "96.00"},
        {"description": "Douxo S3 Pyo Shampoo 200ml (bonus)", "quantity": 1,
         "quantity_uom": "BOTTLE", "unit_price": "0.00"},
    ]
    if with_item_ids:
        lines[0]["daysmart_inventory_item_id"] = "DIT-ebbd2f4c-6e6"
        lines[1]["daysmart_inventory_item_id"] = "DIT-ebbd2f4c-6e6"
    parsed = parse_line_commands(lines)
    assert parsed.lines is not None, parsed.issues
    return parsed.lines


# ---------------------------------------------------------------------------
# L — the line payload builder
# ---------------------------------------------------------------------------


class TestLinePayloads:
    def test_l1_add_payload_matches_the_source_observed_shape(self):
        payloads = build_daysmart_line_payloads(793009, line_commands())
        assert isinstance(payloads, list) and len(payloads) == 2
        first = payloads[0]
        assert set(first) == {"purchase_order_id", "item_id", "item_name",
                              "quantity_ordered", "price", "line_total", "notes"}
        assert first["purchase_order_id"] == 793009
        assert first["item_id"] == "DIT-ebbd2f4c-6e6"
        assert first["item_name"] == "Douxo S3 Pyo Shampoo 200ml"
        assert first["quantity_ordered"] == 12

    def test_l2_money_is_exact_decimal_strings_never_floats(self):
        payloads = build_daysmart_line_payloads(793009, line_commands())
        assert payloads[0]["price"] == "96.00"
        assert payloads[0]["line_total"] == "1152.00"   # 12 × 96.00, exact
        assert all(not isinstance(p["price"], float) for p in payloads)
        assert all(not isinstance(p["line_total"], float) for p in payloads)

    def test_l3_free_goods_line_is_zero_priced_not_dropped(self):
        payloads = build_daysmart_line_payloads(793009, line_commands())
        bonus = payloads[1]
        assert bonus["price"] == "0.00"
        assert bonus["line_total"] == "0.00"

    def test_l4_update_variant_carries_purchase_order_item_id(self):
        # Same endpoint, same shape, plus the Daysmart line id = update.
        payloads = build_daysmart_line_payloads(
            793009, line_commands(), daysmart_item_ids=[555001, None])
        assert payloads[0]["purchase_order_item_id"] == 555001
        assert "purchase_order_item_id" not in payloads[1]

    def test_l5_command_accepts_the_inventory_item_id(self):
        assert line_commands(with_item_ids=True)[0].daysmart_inventory_item_id == "DIT-ebbd2f4c-6e6"
        # And stays optional — a free-typed line has none.
        assert line_commands(with_item_ids=False)[0].daysmart_inventory_item_id is None


# ---------------------------------------------------------------------------
# Fake HTTP session (records request bodies — the array shape must be
# asserted on the wire side, not inferred)
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status_code=200, json_body=None, text_body=""):
        self.status_code = status_code
        self._json = json_body
        self.text = text_body

    def json(self):
        if self._json is None:
            raise ValueError("not JSON")
        return self._json


class FakeSession:
    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, str, dict]] = []
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}

    def _play(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        assert self.script, f"unscripted call: {method} {url}"
        matcher, action = self.script.pop(0)
        assert matcher in url, f"expected call matching {matcher!r}, got {url}"
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action(self)
        return action

    def post(self, url, **kwargs):
        return self._play("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._play("GET", url, kwargs)

    def request(self, method, url, **kwargs):
        return self._play(method, url, kwargs)


def login_ok(session):
    session.cookies.clear()  # a real re-login uses a fresh requests.Session
    session.cookies["vettersession"] = "abc"
    return FakeResponse(200, json_body={"success": True})


LOGIN_OK = [("validateLogin", login_ok), ("dashboard", FakeResponse(200, json_body={})),
            ("actor/staff/id", FakeResponse(200, json_body={"id": "385eaec8-2ba3-4e"})),
            ("actor/staff/businesses", FakeResponse(200, json_body={
                "response": {"resources": [{
                    "id": "861040-business", "isactive": True}]}})),
            ("do_switch", FakeResponse(200, json_body={
                "change": 1, "token": "fresh-vetter-token"}))]


def make_service(script):
    session = FakeSession(script)
    service = DaysmartService(
        base_url="https://daysmart.fake", username="u", password="p",
        vetter_token="tok", session_factory=lambda: session,
        max_attempts=3, retry_backoff_seconds=0)
    return service, session


def sent_json(session, matcher):
    """The json= body of the (single) request whose URL matches."""
    hits = [k for (m, u, k) in session.calls if matcher in u]
    assert len(hits) == 1, f"expected exactly one {matcher} call, got {len(hits)}"
    return hits[0].get("json")


# ---------------------------------------------------------------------------
# W — adapter save_order_items
# ---------------------------------------------------------------------------


class TestSaveOrderItems:
    def payloads(self):
        return build_daysmart_line_payloads(793009, line_commands())

    def test_w1_posts_the_array_body_and_accepts_the_ack(self):
        service, session = make_service(
            [("order/item/save", FakeResponse(200, json_body=copy.deepcopy(ACK_OK)))])
        service.save_order_items(self.payloads())  # returns None on success
        body = sent_json(session, "order/item/save")
        assert isinstance(body, list) and len(body) == 2  # ARRAY — source-proven
        assert body[0]["purchase_order_id"] == 793009

    def test_w2_timeout_is_one_attempt_never_retried(self):
        # item/save is only an upsert when purchase_order_item_id is present;
        # a blind resend of ADD payloads duplicates lines.
        service, session = make_service(
            [("order/item/save", requests.Timeout("no answer"))])
        with pytest.raises(DaysmartTimeout):
            service.save_order_items(self.payloads())
        assert len([c for c in session.calls if "order/item/save" in c[1]]) == 1

    def test_w3_connection_error_is_one_attempt_on_a_write(self):
        service, session = make_service(
            [("order/item/save", requests.ConnectionError("reset"))])
        with pytest.raises(DaysmartUnavailable):
            service.save_order_items(self.payloads())
        assert len([c for c in session.calls if "order/item/save" in c[1]]) == 1

    def test_w4_well_formed_refusal_raises_daysmart_refused(self):
        service, session = make_service(
            [("order/item/save", FakeResponse(200, json_body=copy.deepcopy(ACK_REFUSED)))])
        with pytest.raises(DaysmartRefused):
            service.save_order_items(self.payloads())

    def test_w5_non_json_is_malformed_not_refused(self):
        from services.daysmart_service import DaysmartMalformedResponse
        service, session = make_service(
            [("order/item/save", FakeResponse(200, text_body="<html>login</html>"))])
        with pytest.raises(DaysmartMalformedResponse):
            service.save_order_items(self.payloads())


# ---------------------------------------------------------------------------
# S — adapter submit_order (general submission step: needs items + Open)
# ---------------------------------------------------------------------------


class TestSubmitOrder:
    def test_s1_sends_the_purchase_order_id(self):
        service, session = make_service(
            [("submit-order", FakeResponse(200, json_body=copy.deepcopy(ACK_OK)))])
        service.submit_order(793009)
        assert sent_json(session, "submit-order") == {"purchase_order_id": 793009}

    def test_s2_refusal_raises_daysmart_refused(self):
        # Their UI surfaces message.errors for MWI refusals — same lane here.
        service, session = make_service(
            [("submit-order", FakeResponse(200, json_body=copy.deepcopy(ACK_REFUSED)))])
        with pytest.raises(DaysmartRefused):
            service.submit_order(793009)


# ---------------------------------------------------------------------------
# R — adapter receipt calls (transport building blocks; flow is out of v1)
# ---------------------------------------------------------------------------


class TestReceipts:
    RECEIPT_PAYLOAD = {
        "supplier_id": "3cd1ace0-ddd6-4a", "supplier_name": "Alfamedic",
        "date": 1788001200, "order_id": ["793009"],
        "invoice_number": "INV-9", "amount": None, "shipping": "0.00",
        "notes": "", "tax": "0.00", "tax_type": 1,
    }
    ITEM_PAYLOAD = {
        "post_to_item_id": "DIT-ebc6f1b0-6e6", "purchase_order_item_id": 555001,
        "quantity_received": 12, "quantity_in_stock": 12, "amount": "96.00",
        "expiration_date": None, "lot_number": "L-1",
        "manufacturer": None, "ndc_code": None,
    }

    def test_r1_create_receipt_returns_the_new_receipt_id(self):
        service, session = make_service(
            [("inventory/receipt", FakeResponse(200, json_body=copy.deepcopy(RECEIPT_CREATED)))])
        receipt_id = service.create_receipt(dict(self.RECEIPT_PAYLOAD))
        assert receipt_id == 50123
        assert sent_json(session, "inventory/receipt")["order_id"] == ["793009"]

    def test_r2_create_receipt_without_an_id_is_malformed(self):
        from services.daysmart_service import DaysmartMalformedResponse
        service, session = make_service(
            [("inventory/receipt", FakeResponse(200, json_body=copy.deepcopy(ACK_OK)))])
        with pytest.raises(DaysmartMalformedResponse):
            service.create_receipt(dict(self.RECEIPT_PAYLOAD))

    def test_r3_add_receipt_item_posts_to_the_receipt_path(self):
        service, session = make_service(
            [("receipt/items/50123", FakeResponse(200, json_body=copy.deepcopy(ACK_OK)))])
        service.add_receipt_item(50123, dict(self.ITEM_PAYLOAD))
        assert sent_json(session, "receipt/items/50123")["quantity_received"] == 12

    def test_r3b_fetch_receipt_items_uses_embedded_po_line_identity(self):
        service, session = make_service([
            ("inventory/receipt/822801", FakeResponse(
                200, json_body=copy.deepcopy(RECEIPT_DETAIL))),
        ])
        items = service.fetch_receipt_items(822801)
        assert len(items) == 2
        assert items[0].daysmart_receipt_item_id == 3906196
        assert items[0].daysmart_po_item_id == 3924916
        assert items[0].purchase_order_id == 785384
        assert items[0].post_to_item_id == "DIT-ebc6f1b0-6e6"

    def test_r3c_update_generated_receipt_item_uses_put_payload(self):
        service, session = make_service([
            ("receipt/items/3936614", FakeResponse(
                200, json_body=copy.deepcopy(RECEIPT_ITEM_UPDATE_RESPONSE))),
        ])
        service.update_receipt_item(
            3936614, copy.deepcopy(RECEIPT_ITEM_UPDATE_REQUEST))
        call = [call for call in session.calls
                if "receipt/items/3936614" in call[1]][0]
        assert call[0] == "PUT"
        assert call[2]["json"] == RECEIPT_ITEM_UPDATE_REQUEST

    def test_r4_add_receipt_item_messages_errors_dialect_is_a_refusal(self):
        # Verbatim from their source: THIS endpoint reports errors under
        # data.messages.errors — plural "messages", a third dialect quirk.
        service, session = make_service(
            [("receipt/items/50123",
                         FakeResponse(200, json_body={"messages": {"errors": "Invalid item"}}))])
        with pytest.raises(DaysmartRefused):
            service.add_receipt_item(50123, dict(self.ITEM_PAYLOAD))

    def test_r5_post_receipt_sends_the_receipt_id(self):
        service, session = make_service(
            [("post-receipt", FakeResponse(200, json_body=copy.deepcopy(ACK_OK)))])
        service.post_receipt(50123)
        assert sent_json(session, "post-receipt") == {"purchase_order_receipt_id": 50123}

    def test_r6_receipt_writes_are_one_attempt(self):
        service, session = make_service(
            [("inventory/receipt", requests.Timeout("t"))])
        with pytest.raises(DaysmartTimeout):
            service.create_receipt(dict(self.RECEIPT_PAYLOAD))
        assert len([c for c in session.calls if "inventory/receipt" in c[1]]) == 1


# ---------------------------------------------------------------------------
# FL — header creation is isolated from the explicit line workflow
# ---------------------------------------------------------------------------


def page_result(orders):
    from schemas.purchase_orders import DaysmartOrderPageParseResult, DaysmartPageMetaV1
    return DaysmartOrderPageParseResult(
        orders=orders, issues=[],
        meta=DaysmartPageMetaV1(total=len(orders), per_page=50, current_page=1, last_page=1))


def fixture_page():
    return parse_daysmart_order_page(copy.deepcopy(LIST_PAGE1))


class FakeDaysmart:
    def __init__(self, creates=(), pages=(), line_saves=()):
        self.creates = list(creates)
        self.pages = list(pages)
        self.line_saves = list(line_saves)
        self.create_payloads: list = []
        self.line_payloads: list = []

    def create_order(self, payload):
        self.create_payloads.append(payload)
        action = self.creates.pop(0)
        if isinstance(action, Exception):
            raise action
        return parse_daysmart_created_order(copy.deepcopy(action))

    def fetch_orders(self, page=1, order_type="all"):
        action = self.pages.pop(0)
        if isinstance(action, Exception):
            raise action
        return action

    def save_order_items(self, payloads):
        self.line_payloads.append(payloads)
        assert self.line_saves, "unscripted save_order_items call"
        action = self.line_saves.pop(0)
        if isinstance(action, Exception):
            raise action
        return None


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def run(db, daysmart):
    return create_purchase_order(
        db, command(), daysmart,
        default_ship_to_id=861040, default_bill_to_id=861040)


class TestHeaderOnlyCreate:
    """The PO create carries NO lines (Moses: lines are a separate resource
    with their own endpoint). The contract refuses them; the saga stages
    none and never calls item/save."""

    def test_fl1_header_create_stages_no_lines_and_sends_none(self, db):
        daysmart = FakeDaysmart(
            pages=[fixture_page()], creates=[CREATE_RESPONSE], line_saves=[None])
        result = run(db, daysmart)
        db.commit()
        assert result.outcome == "synced"
        po = result.po
        assert po.daysmart_id == 793009 and po.sync_status == "synced"
        assert po.lines == []
        assert daysmart.line_payloads == []
        steps = [(a.step, a.outcome) for a in po.sync_attempts]
        assert ("create_header", "success") in steps
        assert not any(step == "add_line" for step, _ in steps)
        assert len(daysmart.line_saves) == 1                   # the script was never consumed

    def test_fl2_lines_in_the_create_body_are_refused_by_the_contract(self):
        parsed = parse_create_command({
            "supplier_daysmart_id": "3cd1ace0-ddd6-4a",
            "supplier_name": "Alfamedic",
            "order_date": "2026-08-29T11:00:00+08:00",
            "lines": [{"description": "d", "quantity": 1,
                       "quantity_uom": "BOTTLE", "unit_price": "96.00"}],
        })
        assert parsed.command is None
        assert [(i.field_path, i.expected) for i in parsed.issues] == [("lines", "no such field")]


class TestOrderHeaderWrites:
    """PO-update.har: PUT order/{id} and DELETE order/{id}."""

    def test_ou1_update_order_puts_the_body_and_parses_the_echo(self):
        request = json.loads((FIXTURES / "daysmart_order_update_request.json").read_text(encoding="utf-8"))
        response = json.loads((FIXTURES / "daysmart_order_update_response.json").read_text(encoding="utf-8"))
        service, session = make_service([("inventory/order/794868", FakeResponse(200, json_body=response))])
        result = service.update_order(794868, request)
        call = [c for c in session.calls if "inventory/order/794868" in c[1]][0]
        assert call[0] == "PUT" and call[2]["json"] == request
        assert result.order.daysmart_id == 794868 and result.order.notes == "Test PO creation"

    def test_ou2_delete_order_is_soft_and_parsed(self):
        response = json.loads((FIXTURES / "daysmart_order_delete_response.json").read_text(encoding="utf-8"))
        service, session = make_service([("inventory/order/794868", FakeResponse(200, json_body=response))])
        result = service.delete_order(794868)
        call = [c for c in session.calls if "inventory/order/794868" in c[1]][0]
        assert call[0] == "DELETE"
        assert result.order.is_active is False and result.order.notes == "[removed]"

    def test_ou3_both_are_one_attempt_and_typed(self):
        service, session = make_service([("inventory/order/794868", requests.Timeout("no answer"))])
        with pytest.raises(DaysmartTimeout):
            service.update_order(794868, {"notes": "x"})
        assert len(session.calls) == 1
        service, _ = make_service([("inventory/order/794868", FakeResponse(200, json_body={
            "response": {"message": {"code": 400, "errors": ["submitted"]}}}))])
        with pytest.raises(DaysmartRefused):
            service.delete_order(794868)

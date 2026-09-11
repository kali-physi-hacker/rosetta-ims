"""CA/F-groups — adapter ``create_order`` and the create saga, fully mocked.

NOTHING here touches Daysmart: the adapter tests script a fake HTTP session
(base_url is a fake host), and the saga tests replace the whole Daysmart
service with a scripted stand-in plus in-memory SQLite. The wire truth
comes from fixtures captured out-of-band (orders-details.har).

Adapter rules pinned (CA):
- A mutating call NEVER auto-retries at the transport layer — not on
  timeout, not on connection error. Exactly one POST. Reads may retry
  because they have no effect; a create might have. The scripts contain no
  second response, so a sneaky retry structurally fails the test.
- A token rejection fails immediately and never falls back to the separate
  web-login credentials.
- The response is parsed through the create-response contract; raw dicts
  do not escape.

Saga rules pinned (F):
- draft saved first (header only) → snapshot → submit → parse → link.
- Header-only scope: after a successful header sync the PO is 'synced'
  and every line remains 'pending' (add-line is still unobserved).
- Outcome-by-error-type: daysmart down → 'sync_failed', retried on next
  trigger; timeout → snapshot-diff, NEVER a blind resend (resend only on a
  clean not_landed); unreadable response → 'sync_failed' + quarantined
  issues with payload_context='create_response' (the order may exist);
  ambiguous diff → 'sync_failed' + needs_review, a human decides.
- Every step lands in po_sync_attempts.

Interface under test:

    services/daysmart_service.py
        DaysmartService.create_order(payload: dict)
            -> DaysmartCreatedOrderParseResult   (typed errors as for reads)

    services/po_create_flow.py
        create_purchase_order(db, command, daysmart, *,
                              default_ship_to_id, default_bill_to_id,
                              created_by_user_id=None) -> CreateFlowResult
        # CreateFlowResult: .po (models.PurchaseOrder), .outcome (str:
        #   'synced' | 'sync_failed' | 'needs_review')
"""

from __future__ import annotations

import copy
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_flow.db")

import requests
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from schemas.purchase_orders import (
    DaysmartOrderPageParseResult,
    DaysmartPageMetaV1,
    parse_create_command,
    parse_daysmart_created_order,
    parse_daysmart_order,
    parse_daysmart_order_page,
)
from services.daysmart_service import (
    DaysmartAuthFailure,
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


def command():
    parsed = parse_create_command({
        "supplier_daysmart_id": "3cd1ace0-ddd6-4a",
        "supplier_name": "Alfamedic",
        "order_date": "2026-08-28T11:00:00+08:00",
        "notes": "test order",
    })
    assert parsed.command is not None, parsed.issues
    return parsed.command


# ---------------------------------------------------------------------------
# CA — adapter create_order against a scripted fake session
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
    """Scripted session: ordered (matcher, action) pairs; unscripted calls
    fail the test loudly — which is what makes hidden retries impossible."""

    def __init__(self, script):
        self.script = list(script)
        self.calls: list[tuple[str, str]] = []
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}

    def _play(self, method, url):
        self.calls.append((method, url))
        assert self.script, f"unscripted call: {method} {url}"
        matcher, action = self.script.pop(0)
        assert matcher in url, f"expected call matching {matcher!r}, got {url}"
        if isinstance(action, Exception):
            raise action
        if callable(action):
            return action(self)
        return action

    def post(self, url, **kwargs):
        return self._play("POST", url)

    def get(self, url, **kwargs):
        return self._play("GET", url)

    def request(self, method, url, **kwargs):
        return self._play(method, url)


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


PAYLOAD = {"supplier_id": "3cd1ace0-ddd6-4a", "supplier_name": "Alfamedic",
           "account_no": "", "order_date": 1787903977, "ship_to_id": 861040,
           "bill_to_id": 861040, "notes": None, "template_id": None}


class TestAdapterCreateOrder:
    def test_ca1_success_returns_parsed_contract_result(self):
        service, session = make_service(
            [("order/add", FakeResponse(200, json_body=copy.deepcopy(CREATE_RESPONSE)))])
        result = service.create_order(PAYLOAD)
        assert result.order is not None
        assert result.order.daysmart_id == 793009
        assert result.order.index == 524
        assert result.issues == []
        assert not any("validateLogin" in url for _, url in session.calls)
        assert service._session is None

    def test_ca2_timeout_is_one_attempt_never_retried(self):
        service, session = make_service(
            [("order/add", requests.Timeout("no answer"))])
        with pytest.raises(DaysmartTimeout):
            service.create_order(PAYLOAD)
        assert len([c for c in session.calls if "order/add" in c[1]]) == 1

    def test_ca3_connection_error_is_also_one_attempt_on_a_write(self):
        # Reads retry connection errors; a WRITE must not — a reset can
        # arrive after the request was sent, and a retry then duplicates.
        service, session = make_service(
            [("order/add", requests.ConnectionError("reset"))])
        with pytest.raises(DaysmartUnavailable):
            service.create_order(PAYLOAD)
        assert len([c for c in session.calls if "order/add" in c[1]]) == 1

    def test_ca4_401_is_token_failure_without_web_login_or_resend(self):
        service, session = make_service([("order/add", FakeResponse(401))])
        with pytest.raises(DaysmartAuthFailure):
            service.create_order(PAYLOAD)
        assert len([c for c in session.calls if "order/add" in c[1]]) == 1
        assert not any("validateLogin" in url for _, url in session.calls)

    def test_ca5_unreadable_body_comes_back_as_issues_not_exception(self):
        # Valid envelope, garbage resources: shape problem → issues (the
        # saga quarantines); only non-JSON/transport raises.
        service, session = make_service(
            [("order/add",
                         FakeResponse(200, json_body={"response": {"resources": {"id": "seven"}}}))])
        result = service.create_order(PAYLOAD)
        assert result.order is None
        assert result.issues


# ---------------------------------------------------------------------------
# F — the saga against a fully scripted fake Daysmart service
# ---------------------------------------------------------------------------


def page_result(orders, total=None):
    return DaysmartOrderPageParseResult(
        orders=orders, issues=[],
        meta=DaysmartPageMetaV1(total=total or len(orders), per_page=50,
                                current_page=1, last_page=1))


def fixture_page():
    return parse_daysmart_order_page(copy.deepcopy(LIST_PAGE1))


def new_candidate(daysmart_id=793009, index=524, supplier="3cd1ace0-ddd6-4a",
                  item_count=0, created_at=None):
    raw = copy.deepcopy(LIST_PAGE1["response"]["resources"][0])
    raw["id"] = daysmart_id
    raw["index"] = index
    raw["item_count"] = item_count
    raw["supplier"]["id"] = supplier
    stamp = (created_at or datetime.now(HKT)).isoformat()
    raw["create_at"] = stamp
    raw["update_at"] = stamp
    order = parse_daysmart_order(raw).order
    assert order is not None
    return order


class FakeDaysmart:
    """Scripted stand-in for DaysmartService. ``creates`` is a list of
    outcomes consumed per create_order call: a dict body (parsed and
    returned) or an exception (raised). ``pages`` likewise per fetch."""

    def __init__(self, creates=(), pages=()):
        self.creates = list(creates)
        self.pages = list(pages)
        self.create_payloads: list[dict] = []

    def create_order(self, payload):
        self.create_payloads.append(payload)
        assert self.creates, "unscripted create_order call"
        action = self.creates.pop(0)
        if isinstance(action, Exception):
            raise action
        return parse_daysmart_created_order(copy.deepcopy(action))

    def fetch_orders(self, page=1, order_type="all"):
        assert self.pages, "unscripted fetch_orders call"
        action = self.pages.pop(0)
        if isinstance(action, Exception):
            raise action
        return action


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


class TestCreateSaga:
    def test_f1_happy_path_header_synced_no_lines(self, db):
        daysmart = FakeDaysmart(
            pages=[fixture_page()],            # snapshot
            creates=[CREATE_RESPONSE])         # submit succeeds
        result = run(db, daysmart)
        db.commit()

        assert result.outcome == "synced"
        po = result.po
        assert po.daysmart_id == 793009
        assert po.daysmart_index == 524
        assert po.sync_status == "synced"
        assert po.po_uuid and po.id is not None
        # Header-only scope: the create carries no lines at all — they are a
        # separate resource with their own endpoint.
        assert po.lines == []
        # The submitted payload was built by the mapper, not by hand.
        assert daysmart.create_payloads[0]["supplier_id"] == "3cd1ace0-ddd6-4a"
        assert isinstance(daysmart.create_payloads[0]["order_date"], int)
        # Attempt log has the successful header submit.
        steps = [(a.step, a.outcome) for a in po.sync_attempts]
        assert ("create_header", "success") in steps

    def test_f2_daysmart_down_leaves_a_retryable_failed_draft(self, db):
        daysmart = FakeDaysmart(
            pages=[fixture_page()],
            creates=[DaysmartUnavailable("refused")])
        result = run(db, daysmart)
        db.commit()

        assert result.outcome == "sync_failed"
        po = result.po
        assert po.sync_status == "sync_failed"
        assert po.daysmart_id is None          # nothing linked
        assert po.id is not None               # but the draft SURVIVES —
        # that is the outbox guarantee: nothing owed is ever lost.
        steps = [(a.step, a.outcome) for a in po.sync_attempts]
        assert ("create_header", "daysmart_down") in steps

    def test_f3_timeout_with_clean_diff_match_links_instead_of_resending(self, db):
        ours = new_candidate()  # id 793009, our supplier, 0 items, in window
        daysmart = FakeDaysmart(
            pages=[fixture_page(),             # snapshot (max id 791640)
                   page_result([ours])],       # diff page after timeout
            creates=[DaysmartTimeout("no answer")])
        result = run(db, daysmart)
        db.commit()

        assert result.outcome == "synced"
        po = result.po
        assert po.daysmart_id == 793009        # linked via diff — NO resend
        assert po.sync_status == "synced"
        assert len(daysmart.create_payloads) == 1  # exactly one submit ever
        steps = [(a.step, a.outcome) for a in po.sync_attempts]
        assert ("create_header", "timeout") in steps
        assert ("snapshot_diff", "success") in steps

    def test_f4_timeout_with_clean_not_landed_resends_once(self, db):
        daysmart = FakeDaysmart(
            pages=[fixture_page(),             # snapshot
                   page_result([])],           # diff: provably nothing new
            creates=[DaysmartTimeout("no answer"),
                     CREATE_RESPONSE])         # resend succeeds
        result = run(db, daysmart)
        db.commit()

        assert result.outcome == "synced"
        assert result.po.daysmart_id == 793009
        assert len(daysmart.create_payloads) == 2  # original + one resend

    def test_f5_timeout_with_ambiguous_diff_goes_to_a_human_never_resends(self, db):
        two = [new_candidate(daysmart_id=793009, index=524),
               new_candidate(daysmart_id=793010, index=525)]
        daysmart = FakeDaysmart(
            pages=[fixture_page(), page_result(two)],
            creates=[DaysmartTimeout("no answer")])
        result = run(db, daysmart)
        db.commit()

        assert result.outcome == "needs_review"
        po = result.po
        assert po.sync_status == "sync_failed"
        assert po.daysmart_id is None          # no guessing
        assert len(daysmart.create_payloads) == 1  # and NO blind resend
        steps = [(a.step, a.outcome) for a in po.sync_attempts]
        assert ("snapshot_diff", "needs_review") in steps

    def test_f6_unreadable_create_response_quarantines_with_evidence(self, db):
        # Daysmart answered, we cannot read it: the order MAY exist.
        alien = {"response": {"resources": {"po_number": "X-1"}}}
        daysmart = FakeDaysmart(
            pages=[fixture_page()],
            creates=[alien])
        result = run(db, daysmart)
        db.commit()

        assert result.outcome == "needs_review"
        po = result.po
        assert po.sync_status == "sync_failed"
        assert po.daysmart_id is None
        steps = [(a.step, a.outcome) for a in po.sync_attempts]
        assert ("create_header", "contract_mismatch") in steps
        # The issues are persisted for OPS, tagged to the create response.
        issues = (db.query(models.PoValidationIssueRecord)
                  .filter_by(payload_context="create_response").all())
        assert issues
        assert all(i.purchase_order_id == po.id for i in issues)

    def test_f7_no_step_ever_raises_out_of_the_saga(self, db):
        # Even the snapshot fetch failing must degrade, not explode: without
        # a snapshot the timeout probe is impossible, so the safe outcome is
        # a failed draft for the next trigger.
        daysmart = FakeDaysmart(
            pages=[DaysmartUnavailable("down before snapshot")],
            creates=[])
        result = run(db, daysmart)  # must not raise
        db.commit()
        assert result.outcome == "sync_failed"
        assert result.po.sync_status == "sync_failed"
        assert daysmart.create_payloads == []  # never submitted blind

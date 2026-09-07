"""H-group — fixes for the eight confirmed code-review findings.

Each test reproduces one reviewed bug; the fix makes it green. Mocked
throughout, as always.

H1  Per-page watermark advance loses data: Daysmart pages newest-first, so
    advancing after page 1 skips pages 2..N forever if the cycle dies.
    Fix: apply_order_page no longer touches the watermark; a new
    run_full_inbound_sync drives all pages and advances ONCE, only after
    the whole cycle completed.
H2  The watermark table had no single-row enforcement (insert race).
    Fix: the row is pinned to id=1.
H3  _is_after caught only ValueError — a naive stored value vs an aware
    candidate raised TypeError and crashed every sync. Fix: an unparseable
    or incomparable STORED value is self-healed by a valid aware candidate;
    an invalid CANDIDATE stays a no-op.
H4  optional_integer silently nulled a present-but-wrong-type ship_to_id /
    bill_to_id with no issue — invisible drift. Fix: new NON-BLOCKING code
    PO_OPTIONAL_FIELD_INVALID; order still parses, field is None, and the
    drift is on the review screen.
H5  apply_order_page re-recorded identical issues every poll. Fix: dedup
    against existing OPEN records on (code, field_path, daysmart_id,
    context).
H6  _drop_session could null the session out from under a request mid-
    flight (AttributeError escaping the typed taxonomy). Fix: the request
    loop binds the session locally and re-ensures on entry.
H7  Same daysmart_id on two pages in ONE uncommitted session (an order
    shifting pages mid-fetch) double-inserted under autoflush=False and
    died on the unique constraint at commit. Fix: flush after each insert
    so the next page's query sees it.
H8  _content_hash and _overwrite_daysmart_owned were two hand-kept lists,
    already drifted (name/item_count hashed but never stored; derived
    daysmart_status stored but never hashed — so growing the status
    vocabulary never backfilled). Fix: ONE field map drives both; the
    derived status participates in the hash.
"""

from __future__ import annotations

import copy
import json
import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/po_hardening.db")

from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import models
from database import Base
from schemas.purchase_orders import (
    DaysmartOrderPageParseResult,
    DaysmartPageMetaV1,
    IssueCode,
    parse_daysmart_order,
    parse_daysmart_order_page,
)
from services.daysmart_service import DaysmartUnavailable
from services.po_inbound_sync import apply_order_page, run_full_inbound_sync
from services.po_sync_state import advance_watermark, get_watermark

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
PAGE1 = json.loads((FIXTURES / "daysmart_order_list_page1.json").read_text(encoding="utf-8"))


@pytest.fixture()
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def parsed_orders():
    return parse_daysmart_order_page(copy.deepcopy(PAGE1)).orders


def page_of(orders, *, current=1, last=1):
    return DaysmartOrderPageParseResult(
        orders=orders, issues=[],
        meta=DaysmartPageMetaV1(total=len(orders), per_page=50,
                                current_page=current, last_page=last))


class PagedFake:
    """fetch_orders scripted per page number."""

    def __init__(self, pages: dict):
        self.pages = pages
        self.fetched: list[int] = []

    def fetch_orders(self, page=1, order_type="all"):
        self.fetched.append(page)
        action = self.pages[page]
        if isinstance(action, Exception):
            raise action
        return action

    def fetch_order_lines(self, daysmart_order_id):
        # These tests pin the HEADER watermark; lines are out of scope, so
        # every order simply has none (a well-formed empty paginator).
        from schemas.purchase_orders import parse_daysmart_order_item_page
        return parse_daysmart_order_item_page({"response": {"resources": {
            "current_page": 1, "data": [], "last_page": 1, "per_page": 50, "total": 0},
            "message": {"code": 200}}})

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


# ---------------------------------------------------------------------------
# H1 / H2 / H3 — the watermark
# ---------------------------------------------------------------------------


class TestWatermark:
    def test_h1a_apply_order_page_never_touches_the_watermark(self, db):
        apply_order_page(db, parse_daysmart_order_page(copy.deepcopy(PAGE1)))
        db.commit()
        assert get_watermark(db) is None  # page apply is not cycle completion

    def test_h1b_full_sync_advances_once_after_the_whole_cycle(self, db):
        orders = parsed_orders()
        newest, older = orders[:25], orders[25:]
        daysmart = PagedFake({1: page_of(newest, current=1, last=2),
                              2: page_of(older, current=2, last=2)})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()

        assert summary.completed is True
        assert summary.created == 50
        assert daysmart.fetched == [1, 2]
        # Advanced exactly to the global max update_at of the cycle.
        assert get_watermark(db) == max(o.updated_at for o in orders).isoformat()

    def test_h1c_crash_mid_cycle_leaves_the_watermark_untouched(self, db):
        orders = parsed_orders()
        daysmart = PagedFake({1: page_of(orders[:25], current=1, last=2),
                              2: DaysmartUnavailable("died on page 2")})
        summary = run_full_inbound_sync(db, daysmart)
        db.commit()

        assert summary.completed is False
        assert summary.created == 25          # page 1's data is kept …
        assert get_watermark(db) is None      # … but the bookmark did NOT move:
        # the next cycle re-reads everything instead of skipping pages 2..N.

    def test_h2_watermark_row_is_pinned_to_id_1(self, db):
        advance_watermark(db, "2026-08-22T18:00:18+08:00")
        advance_watermark(db, "2026-08-25T11:21:01+08:00")
        rows = db.query(models.PoSyncWatermark).all()
        assert len(rows) == 1
        assert rows[0].id == 1

    def test_h3a_naive_stored_value_is_self_healed_not_a_crash(self, db):
        # A corrupt (naive) bookmark from a bad seed/backfill.
        db.add(models.PoSyncWatermark(id=1, last_update_at_seen="2026-08-01T00:00:00",
                                      updated_at="x"))
        db.commit()
        # Used to raise TypeError (aware vs naive comparison).
        advance_watermark(db, "2026-08-25T11:21:01+08:00")
        assert get_watermark(db) == "2026-08-25T11:21:01+08:00"

    def test_h3b_invalid_candidate_stays_a_noop(self, db):
        advance_watermark(db, "2026-08-25T11:21:01+08:00")
        advance_watermark(db, "not a timestamp")
        advance_watermark(db, "2026-08-26T00:00:00")   # naive candidate: also no
        assert get_watermark(db) == "2026-08-25T11:21:01+08:00"


# ---------------------------------------------------------------------------
# H4 — drifted optional fields are visible, not silent
# ---------------------------------------------------------------------------


class TestOptionalFieldDrift:
    def test_h4_wrong_type_ship_to_is_an_issue_not_a_silent_null(self):
        raw = copy.deepcopy(PAGE1["response"]["resources"][1])
        raw["ship_to_id"] = "861040"   # string where an int lives
        result = parse_daysmart_order(raw)
        order = result.order
        assert order is not None               # non-blocking: order survives
        assert order.ship_to_id is None        # value withheld, not guessed
        codes = [(i.code, i.field_path) for i in result.issues]
        assert (IssueCode.PO_OPTIONAL_FIELD_INVALID, "ship_to_id") in codes

    def test_h4b_absent_optional_field_is_still_silent(self):
        raw = copy.deepcopy(PAGE1["response"]["resources"][1])
        del raw["ship_to_id"]
        result = parse_daysmart_order(raw)
        assert result.order is not None
        assert result.issues == []             # absence is normal, no noise


# ---------------------------------------------------------------------------
# H5 — issue records don't multiply
# ---------------------------------------------------------------------------


class TestIssueDedup:
    def mangled_page(self):
        raw = copy.deepcopy(PAGE1)
        raw["response"]["resources"][3]["total"] = "not-money"
        return parse_daysmart_order_page(raw)

    def test_h5_same_issue_recorded_once_across_polls(self, db):
        first = apply_order_page(db, self.mangled_page())
        db.commit()
        assert first.issues_recorded == 1
        second = apply_order_page(db, self.mangled_page())
        db.commit()
        assert second.issues_recorded == 0     # already open — not repeated
        assert db.query(models.PoValidationIssueRecord).count() == 1

    def test_h5b_resolved_issue_recurring_is_recorded_again(self, db):
        apply_order_page(db, self.mangled_page())
        db.commit()
        record = db.query(models.PoValidationIssueRecord).one()
        record.resolution_status = "resolved"
        db.commit()
        apply_order_page(db, self.mangled_page())
        db.commit()
        # A recurrence AFTER resolution is new information, not a duplicate.
        assert db.query(models.PoValidationIssueRecord).count() == 2


# ---------------------------------------------------------------------------
# H6 — session drop mid-flight stays inside the typed taxonomy
# ---------------------------------------------------------------------------


class TestSessionRace:
    def test_h6_request_after_drop_session_reensures_instead_of_crashing(self):
        import requests as _requests
        from services.daysmart_service import DaysmartService

        class FakeResponse:
            def __init__(self, body): self.status_code = 200; self._b = body
            def json(self): return self._b

        class FakeSession:
            def __init__(self, owner): self.owner = owner; self.cookies = {}; self.headers = {}
            def post(self, url, **kw):
                self.cookies["vettersession"] = "x"; return FakeResponse({"success": True})
            def get(self, url, **kw): return FakeResponse({})
            def request(self, method, url, **kw):
                return FakeResponse(copy.deepcopy(PAGE1))

        service = DaysmartService(
            base_url="https://f.fake", username="u", password="p", vetter_token="t",
            session_factory=lambda: FakeSession(None),
            max_attempts=3, retry_backoff_seconds=0)
        service._ensure_session()
        service._drop_session()               # another thread's 401 handling
        result = service.fetch_orders(page=1)  # must NOT AttributeError
        assert len(result.orders) == 50


# ---------------------------------------------------------------------------
# H7 — an order shifting pages mid-fetch doesn't blow up the batch
# ---------------------------------------------------------------------------


class TestCrossPageDuplicate:
    def test_h7_same_order_on_two_pages_in_one_transaction(self, db):
        orders = parsed_orders()
        # Order 789502 appears on both "pages" (a new order shifted it).
        page_a = page_of(orders[:10])
        page_b = page_of([orders[5]] + orders[10:20])
        apply_order_page(db, page_a)           # NO commit between pages —
        summary_b = apply_order_page(db, page_b)  # one sync transaction
        db.commit()                            # used to die: IntegrityError

        assert summary_b.unchanged >= 1        # second sighting: unchanged
        assert (db.query(models.PurchaseOrder)
                .filter_by(daysmart_id=orders[5].daysmart_id).count()) == 1


# ---------------------------------------------------------------------------
# H8 — one field map drives the writer AND the hash
# ---------------------------------------------------------------------------


class TestHashWriterUnity:
    def test_h8_vocabulary_growth_backfills_the_derived_status(self, db, monkeypatch):
        # An order arrives with unknown status 2 → stored with
        # daysmart_status NULL and a PO_STATUS_UNKNOWN issue.
        raw_page = copy.deepcopy(PAGE1)
        raw_page["response"]["resources"][0]["status"] = {"id": 3, "label": "Mystery"}
        apply_order_page(db, parse_daysmart_order_page(copy.deepcopy(raw_page)))
        db.commit()
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        assert po.daysmart_status is None
        assert po.daysmart_status_id == 3

        # The team classifies status 2. Re-fetching the UNCHANGED order must
        # now backfill the derived status — the mapped value participates in
        # the hash, so the row cannot short-circuit as 'unchanged'.
        from schemas.purchase_orders import daysmart_order_v1
        monkeypatch.setitem(daysmart_order_v1.STATUS_VOCABULARY, 3, ("mystery", "Mystery"))
        summary = apply_order_page(db, parse_daysmart_order_page(copy.deepcopy(raw_page)))
        db.commit()
        db.expire_all()
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        assert summary.updated >= 1
        assert po.daysmart_status == "mystery"

    def test_h8b_item_count_is_a_persisted_daysmart_owned_value(self, db):
        # Originally: item_count was unpersisted, so changing ONLY it must
        # not dirty the row. It is stored now — a Daysmart-owned display
        # value, hashed like the rest — but it DECIDES nothing: lines are
        # pulled with every sync regardless (an edited line changes no count).
        apply_order_page(db, parse_daysmart_order_page(copy.deepcopy(PAGE1)))
        db.commit()
        raw_page = copy.deepcopy(PAGE1)
        raw_page["response"]["resources"][0]["item_count"] = 99
        summary = apply_order_page(db, parse_daysmart_order_page(raw_page))
        db.commit()
        assert summary.updated == 1
        assert summary.unchanged == 49
        po = db.query(models.PurchaseOrder).filter_by(daysmart_id=791640).one()
        assert po.item_count == 99

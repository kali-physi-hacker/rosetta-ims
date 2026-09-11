"""Phase 3c — the snapshot-diff: "did my timed-out create land?"

TDD: defines ``services/po_snapshot_diff.py``, which does not exist yet.

This is the duplicate-PO defence, designed after the correlation-token
approach was rejected (no Rosetta data in Daysmart payloads). Before
submitting we note the newest Daysmart order id; after a timeout we fetch
orders newer than that snapshot and ask whether OUR order is among them —
by observation only: supplier, creation window, line count.

    resolve_snapshot_diff(
        snapshot_max_daysmart_id=...,   # newest id seen BEFORE submitting
        supplier_daysmart_id=...,       # the draft's supplier
        submitted_between=(start, end), # tz-aware datetimes of our attempt
        expected_line_count=...,        # lines the draft carries
        candidates=[DaysmartOrderV1],   # parsed page fetched AFTER timeout
        page_had_issues=False,          # the diff page carried parse issues
    ) -> verdict with .kind, .order, .candidates, .reason

Three verdicts — the third exists because a boolean here manufactures
duplicates (every near-miss silently becomes "not ours → resend"):

    'matched'      exactly one plausible candidate → link it, done
    'not_landed'   provably no new order for this supplier → safe to resend
    'needs_review' anything else → OPS decides; NEVER auto-resend

Rules pinned:
- Only candidates with daysmart_id > snapshot are considered (ids ascend —
  proven by the capture ordering).
- A partially built order matches: a timeout during the HEADER call leaves
  item_count 0; during line-adds, fewer items than the draft has. So
  item_count <= expected is plausible; item_count > expected is not ours.
- With parse issues on the diff page, absence proves nothing: no-match
  degrades from 'not_landed' to 'needs_review'. A positive single match is
  still trusted — it is affirmative evidence.
- Pure function: no clock reads, no HTTP, no DB — everything comes in as
  arguments, so every edge is table-testable.
"""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from schemas.purchase_orders import parse_daysmart_order
from services.po_snapshot_diff import resolve_snapshot_diff

FIXTURES = Path(__file__).parent / "fixtures" / "purchase_orders"
HKT = timezone(timedelta(hours=8))

SNAPSHOT = 791640  # newest fixture order — "the world before our submit"
WINDOW = (
    datetime(2026, 8, 28, 10, 59, 0, tzinfo=HKT),
    datetime(2026, 8, 28, 11, 2, 0, tzinfo=HKT),
)
ALFAMEDIC = "3cd1ace0-ddd6-4a"


@pytest.fixture(scope="module")
def base_raw() -> dict:
    page = json.loads((FIXTURES / "daysmart_order_list_page1.json").read_text(encoding="utf-8"))
    return page["response"]["resources"][0]  # order 791640, Alfamedic


def candidate(base_raw, *, daysmart_id, supplier=ALFAMEDIC, created="2026-08-28T11:00:05+08:00",
              item_count=0, supplier_name="Alfamedic"):
    raw = copy.deepcopy(base_raw)
    raw["id"] = daysmart_id
    raw["index"] = 520
    raw["create_at"] = created
    raw["order_date"] = created
    raw["item_count"] = item_count
    raw["supplier"] = {"id": supplier, "encoded_id": "irrelevant", "name": supplier_name}
    result = parse_daysmart_order(raw)
    assert result.order is not None, result.issues
    return result.order


def resolve(candidates, *, expected_line_count=2, page_had_issues=False):
    return resolve_snapshot_diff(
        snapshot_max_daysmart_id=SNAPSHOT,
        supplier_daysmart_id=ALFAMEDIC,
        submitted_between=WINDOW,
        expected_line_count=expected_line_count,
        candidates=candidates,
        page_had_issues=page_had_issues,
    )


class TestMatched:
    def test_sd1_exactly_one_plausible_candidate_is_ours(self, base_raw):
        verdict = resolve([candidate(base_raw, daysmart_id=791700, item_count=2)])
        assert verdict.kind == "matched"
        assert verdict.order.daysmart_id == 791700

    def test_sd9_partially_built_order_still_matches(self, base_raw):
        # Timeout hit during the header call: the order exists with 0 items.
        verdict = resolve([candidate(base_raw, daysmart_id=791700, item_count=0)])
        assert verdict.kind == "matched"
        # Timeout mid-line-adds: fewer items than the draft carries.
        verdict = resolve([candidate(base_raw, daysmart_id=791701, item_count=1)])
        assert verdict.kind == "matched"

    def test_sd8b_positive_match_survives_a_noisy_page(self, base_raw):
        verdict = resolve(
            [candidate(base_raw, daysmart_id=791700, item_count=2)],
            page_had_issues=True,
        )
        assert verdict.kind == "matched"


class TestNotLanded:
    def test_sd2_no_new_orders_at_all(self):
        verdict = resolve([])
        assert verdict.kind == "not_landed"

    def test_sd3_new_orders_but_none_for_our_supplier(self, base_raw):
        verdict = resolve([
            candidate(base_raw, daysmart_id=791700,
                      supplier="183ca1e3-9419-4f", supplier_name="IDEXX"),
        ])
        assert verdict.kind == "not_landed"

    def test_sd6_supplier_match_outside_our_window_is_not_ours(self, base_raw):
        verdict = resolve([
            candidate(base_raw, daysmart_id=791700, created="2026-08-28T14:30:00+08:00"),
        ])
        assert verdict.kind == "not_landed"

    def test_sd7_candidates_at_or_below_the_snapshot_are_invisible(self, base_raw):
        # The pre-existing Alfamedic order (id == snapshot) must never match,
        # however similar it looks.
        verdict = resolve([candidate(base_raw, daysmart_id=SNAPSHOT)])
        assert verdict.kind == "not_landed"


class TestNeedsReview:
    def test_sd4_two_plausible_candidates_never_auto_link(self, base_raw):
        verdict = resolve([
            candidate(base_raw, daysmart_id=791700, item_count=2),
            candidate(base_raw, daysmart_id=791701, item_count=0,
                      created="2026-08-28T11:00:40+08:00"),
        ])
        assert verdict.kind == "needs_review"
        assert {order.daysmart_id for order in verdict.candidates} == {791700, 791701}
        assert verdict.order is None

    def test_sd5_more_items_than_the_draft_is_not_ours_but_not_clear_either(self, base_raw):
        # Same supplier, right window, but MORE lines than we ever submitted:
        # someone else's order, or something is badly off. Human, not code.
        verdict = resolve([candidate(base_raw, daysmart_id=791700, item_count=9)])
        assert verdict.kind == "needs_review"

    def test_sd8_absence_proves_nothing_on_a_noisy_page(self, base_raw):
        # The diff page had parse issues: an unparseable order COULD be ours,
        # so "no match" must not license a resend.
        verdict = resolve([], page_had_issues=True)
        assert verdict.kind == "needs_review"
        assert "issue" in verdict.reason.lower() or "parse" in verdict.reason.lower()

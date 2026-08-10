"""Every row ends a run somewhere, and the ones the machine could not read are addressable.

Driven through the golden replay rather than hand-built rows: the invariant is
about what a real run leaves behind, and a synthetic fixture would only prove
the classifier agrees with itself.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

from uuid import UUID  # noqa: E402

import models  # noqa: E402
from schemas.catalogue_pipeline.enums import ReviewStatus  # noqa: E402
from services import catalogue_dead_letters as dl  # noqa: E402
from services import catalogue_pipeline_stages as stages  # noqa: E402
from tests.test_catalogue_golden_suppliers import (  # noqa: E402, F401 — db fixture
    ALFAMEDIC,
    _load_expected,
    _replay_to_published,
    db,
)


def _run_alfamedic(db, monkeypatch) -> str:
    expected = _load_expected(ALFAMEDIC)
    _replay_to_published(
        db, monkeypatch, fixture_dir=ALFAMEDIC, page_names=["page_1.json"],
        supplier_id=1, provider="anthropic", api_key_var="ANTHROPIC_API_KEY",
        only_skus=set(expected), refused={},
    )
    return db.query(models.IngestionRun).order_by(models.IngestionRun.id.desc()).first().run_uuid


def test_every_row_ends_the_run_in_exactly_one_lane(db, monkeypatch):
    """The invariant the lane exists to establish.

    'Exactly one' is structural — a row is classified once — so the assertion
    that carries weight is that the lanes account for every normalized row and
    that none land in UNACCOUNTED, which is the bucket for a row the pipeline
    neither published, queued, rejected, nor blocked.
    """
    run_uuid = _run_alfamedic(db, monkeypatch)
    report = dl.lanes_for_run(db, run_uuid)

    rows = (
        db.query(models.CatalogueNormalizedRow)
        .filter_by(ingestion_run_uuid=run_uuid)
        .count()
    )
    assert report.total == rows, "the lanes must account for every normalized row of the run"
    assert not report.lanes[dl.Lane.UNACCOUNTED.value], (
        f"{len(report.lanes[dl.Lane.UNACCOUNTED.value])} rows are in no lane — "
        f"neither published, awaiting review, rejected nor dead-lettered"
    )

    seen: set[str] = set()
    for items in report.lanes.values():
        overlap = seen & set(items)
        assert not overlap, f"rows appear in more than one lane: {sorted(overlap)[:5]}"
        seen |= set(items)

    # The run must actually exercise the interesting lanes, or this proves nothing.
    assert report.counts[dl.Lane.PUBLISHED.value] > 0
    assert report.counts[dl.Lane.DEAD_LETTERED.value] > 0


def test_a_dead_lettered_row_never_reaches_the_serving_layer(db, monkeypatch):
    """The leak this lane exists to prevent."""
    run_uuid = _run_alfamedic(db, monkeypatch)
    dead = {entry.catalogue_item_uuid for entry in dl.dead_letters(db, run_uuid=run_uuid)}
    assert dead, "no dead letters in this run — the assertion below would be vacuous"

    published = (
        db.query(models.CatalogueServingPublication.catalogue_item_uuid)
        .filter(models.CatalogueServingPublication.is_current == 1)
        .all()
    )
    leaked = dead & {row[0] for row in published}
    assert not leaked, f"dead-lettered rows reached the published snapshot: {sorted(leaked)[:5]}"


def test_dead_letters_are_enumerable_and_identifiable(db, monkeypatch):
    """One query answers what is stuck, under which code, and how long for.

    Each entry must carry enough for a person to recognise the row and for
    DEV-203 to re-drive it — a count is not a handle.
    """
    run_uuid = _run_alfamedic(db, monkeypatch)
    entries = dl.dead_letters(db, run_uuid=run_uuid)
    assert entries

    for entry in entries:
        assert entry.catalogue_item_uuid  # the handle DEV-203 selects on
        assert entry.ingestion_run_uuid == run_uuid
        assert entry.issue_code and entry.stage
        assert entry.age_days is not None and entry.age_days >= 0
    assert any(entry.supplier_sku for entry in entries), (
        "not one dead letter carries the supplier's own code — an entry nobody "
        "can identify is barely better than no entry"
    )

    # Grouped by code, because the operational question is which single rule
    # change clears the most rows.
    tally = dl.counts_by_issue_code(db, run_uuid=run_uuid)
    assert tally and sum(tally.values()) == len(entries)
    assert list(tally.values()) == sorted(tally.values(), reverse=True), "most-blocking code first"

    # Filtering by code returns only that code, and nothing wider.
    worst = next(iter(tally))
    filtered = dl.dead_letters(db, run_uuid=run_uuid, issue_code=worst)
    assert {entry.issue_code for entry in filtered} == {worst}
    assert len(filtered) == tally[worst]


def test_a_row_a_person_rejected_is_not_dead_lettered(db, monkeypatch):
    """Machine failure and human judgement are different facts.

    Conflating them would let "fix the rule and re-drive" silently re-open
    something a person already answered, so a rejection must land in its own
    lane and never appear in the dead-letter queue.
    """
    run_uuid = _run_alfamedic(db, monkeypatch)
    candidate = (
        db.query(models.CatalogueMasteringCandidate)
        .filter_by(ingestion_run_uuid=run_uuid, superseded_by_uuid=None)
        .filter(models.CatalogueMasteringCandidate.review_status == ReviewStatus.PENDING_REVIEW.value)
        .first()
    )
    assert candidate is not None, "no pending candidate to reject"
    item_uuid = candidate.catalogue_item_uuid

    stages.ReviewDecisionService(db).record_decision(
        stages.RecordReviewDecisionCommand(
            mastering_candidate_id=UUID(candidate.mastering_candidate_uuid),
            actor_id="reviewer@example.com",
            review_status=ReviewStatus.REJECTED,
            reason="Not a product we stock.",
        )
    )

    report = dl.lanes_for_run(db, run_uuid)
    assert item_uuid in report.lanes[dl.Lane.REJECTED.value]
    assert item_uuid not in report.lanes[dl.Lane.DEAD_LETTERED.value]
    assert item_uuid not in {e.catalogue_item_uuid for e in dl.dead_letters(db, run_uuid=run_uuid)}

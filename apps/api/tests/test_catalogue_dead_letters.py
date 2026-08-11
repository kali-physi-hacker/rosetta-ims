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

import json  # noqa: E402
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
    tallies = dl.tallies_by_issue_code(db, run_uuid=run_uuid)
    assert tallies
    # rows_blocked double-counts a row held by two codes; rows_cleared_if_fixed
    # never can, which is why it is the number the ordering uses.
    assert sum(t.rows_cleared_if_fixed for t in tallies) <= len(entries)
    assert sum(t.rows_blocked for t in tallies) >= len(entries)
    assert [t.rows_cleared_if_fixed for t in tallies] == sorted(
        (t.rows_cleared_if_fixed for t in tallies), reverse=True
    ), "the code that frees the most rows comes first"

    # Filtering by code narrows the ROWS; each row still reports every code
    # holding it, so a row needing a second fix says so.
    worst = tallies[0].issue_code
    filtered = dl.dead_letters(db, run_uuid=run_uuid, issue_code=worst)
    assert filtered
    assert all(worst in entry.issue_codes for entry in filtered)
    assert len(filtered) == tallies[0].rows_blocked


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


def test_the_raw_layer_reconciles_against_the_lanes(db, monkeypatch):
    """Rows in versus rows accounted for, so dedup cannot pass for loss.

    Counting RAW against normalized gives a difference and no explanation. On
    this catalogue the difference is entirely the same product listed at several
    order quantities, collapsed into one row — but that has to be shown, not
    assumed, because "186 rows fewer" reads identically whether they were merged
    or dropped. Establishing it by hand once is analysis; asserting it is a test.
    """
    run_uuid = _run_alfamedic(db, monkeypatch)
    report = dl.reconcile(db, run_uuid)

    assert report.raw_observations == report.raw_product_rows + report.raw_text_observations
    assert report.lanes_cover_normalized_rows, (
        f"lanes hold {sum(report.lane_counts.values())} rows, staging holds "
        f"{report.normalized_rows} — the classification is not covering the run"
    )

    # Every product row that carried no link to a normalized row must be a
    # repeat of one that did. A code appearing ONLY among the unlinked is a row
    # the pipeline dropped and nothing recorded.
    linked_evidence = {
        row[0]
        for row in db.query(models.CatalogueNormalizedRowEvidence.raw_observation_uuid).all()
    }
    kept_codes: set[str] = set()
    orphan_codes: set[str] = set()
    for observation_uuid, cells_json in db.query(
        models.CatalogueExtractedEvidence.raw_observation_uuid,
        models.CatalogueExtractedEvidence.raw_cells_json,
    ).filter(models.CatalogueExtractedEvidence.ingestion_run_uuid == run_uuid).all():
        cells = json.loads(cells_json or "[]")
        if not cells:
            continue
        first = cells[0]
        code = str((first.get("raw_value") if isinstance(first, dict) else first) or "").strip()
        if not code:
            continue
        (kept_codes if observation_uuid in linked_evidence else orphan_codes).add(code)

    lost = sorted(orphan_codes - kept_codes)
    assert not lost, (
        f"{len(lost)} supplier codes appear only among rows that reached no normalized "
        f"row — dropped between extraction and staging with nothing recorded: {lost[:8]}"
    )
    assert report.unlinked_product_rows > 0, (
        "this catalogue lists products at several order quantities, so some product "
        "rows must collapse — none did, and the check above proves nothing"
    )


def test_the_endpoint_serialises_the_queue_and_what_would_clear_it(db, monkeypatch):
    """Thin wrapper, but the wiring and the shape are what break.

    Called directly rather than over HTTP: the service beneath is covered
    above, so what is worth proving here is that the response carries the
    lanes, the reconciliation and the per-code answer, and that filtering
    reaches the query.
    """
    from routers.catalogue_ingestions import get_dead_letters

    run_uuid = _run_alfamedic(db, monkeypatch)
    body = get_dead_letters(UUID(run_uuid), issue_code=None, stage=None, db=db, _user=None)

    assert body["ingestion_run_id"] == run_uuid
    assert body["count"] == len(body["dead_letters"]) > 0
    assert body["lanes"][dl.Lane.DEAD_LETTERED.value] == body["count"]
    assert body["reconciliation"]["lanes_cover_normalized_rows"] is True
    assert body["reconciliation"]["raw_product_rows"] >= body["reconciliation"]["normalized_rows"]

    top = body["by_issue_code"][0]
    assert top["rows_cleared_if_fixed"] <= top["rows_blocked"]

    entry = body["dead_letters"][0]
    assert entry["catalogue_item_id"] and entry["issue_codes"] and entry["age_days"] is not None

    # Filtering narrows the rows and is not silently ignored.
    filtered = get_dead_letters(
        UUID(run_uuid), issue_code=top["issue_code"], stage=None, db=db, _user=None
    )
    assert 0 < filtered["count"] <= body["count"]
    assert all(top["issue_code"] in e["issue_codes"] for e in filtered["dead_letters"])

    missing = get_dead_letters(
        UUID(run_uuid), issue_code="NO_SUCH_CODE", stage=None, db=db, _user=None
    )
    assert missing["count"] == 0 and missing["dead_letters"] == []


def test_a_re_upload_does_not_turn_published_rows_into_outstanding_work(db, monkeypatch):
    """The same file ingested twice, and the older run read honestly.

    Publications supersede by product, so a second run of the same catalogue
    flips the first run's publications to is_current=0. Classifying only on
    "is a publication current" then reports those rows as awaiting review —
    inventing work nobody has to do, on a run where it was already done. On
    this catalogue that was 36 rows, and re-uploading a price list is routine.
    """
    first_run = _run_alfamedic(db, monkeypatch)
    before = dl.lanes_for_run(db, first_run).counts
    assert before[dl.Lane.PUBLISHED.value] > 0

    second_run = _run_alfamedic(db, monkeypatch)
    assert second_run != first_run

    after = dl.lanes_for_run(db, first_run).counts
    assert after[dl.Lane.AWAITING_REVIEW.value] == before[dl.Lane.AWAITING_REVIEW.value], (
        "the second run moved rows of the FIRST run into awaiting review — "
        "publications it superseded are being read as unreviewed"
    )
    # Everything the first run had live is now history. Its own supersessions
    # are counted too: a catalogue listing one product at several order
    # quantities publishes more than once against the same product, so some
    # rows were already superseded within the first run.
    assert after[dl.Lane.SUPERSEDED.value] == (
        before[dl.Lane.PUBLISHED.value] + before[dl.Lane.SUPERSEDED.value]
    )
    assert after[dl.Lane.PUBLISHED.value] == 0

    # The lanes still cover the run, and the dead-letter population is untouched
    # by a later run — those rows never reached a candidate either time.
    assert dl.reconcile(db, first_run).lanes_cover_normalized_rows
    assert after[dl.Lane.DEAD_LETTERED.value] == before[dl.Lane.DEAD_LETTERED.value]


def test_observation_identities_do_not_repeat_across_runs(db, monkeypatch):
    """The assumption reconcile() rests on, asserted rather than believed.

    reconcile() matches evidence to normalized rows on observation UUID alone,
    because the link table carries no run column. That is only safe while those
    UUIDs are unique per run. The pipeline mints identities with uuid5, which is
    content-addressed, so this is exactly the kind of thing that could quietly
    become untrue — and if it did, links from an earlier run would make dropped
    rows look accounted for.
    """
    first_run = _run_alfamedic(db, monkeypatch)
    second_run = _run_alfamedic(db, monkeypatch)

    def observations(run: str) -> set[str]:
        return {
            row[0]
            for row in db.query(models.CatalogueExtractedEvidence.raw_observation_uuid)
            .filter_by(ingestion_run_uuid=run)
            .all()
        }

    shared = observations(first_run) & observations(second_run)
    assert not shared, (
        f"{len(shared)} observation UUIDs are shared between two runs of the same file — "
        f"reconcile() matches links on UUID alone and would now over-count them"
    )

"""Fix a rule, re-drive exactly the rows it failed, pay nothing.

The story is DEV-203's Definition of Done, told with a real fix this repo
shipped (a75eb5f): under the old parser, nine Alfamedic rows dead-lettered as
CONTRACT_COST_UNPARSEABLE. Six printed a price with a remark — "130.0 (Price
Reduced)" — and the fix reads them. Three printed "10% discount", a relative
tier the strict shape refuses on purpose. So the fixed retrigger clears six,
and the three survivors keep BOTH their codes and a climbing attempt count —
which is the mechanism reporting the truth, not failing to work.

The first test re-installs the old parser to strand all nine, retriggers while
the bug is still in (attempts must count, the queue must not shrink), lands
the fix by dropping the patch, and retriggers again. The vision provider is a
bomb throughout: a retrigger that touches it is the defect these tests exist
to catch.
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

import pytest  # noqa: E402

import models  # noqa: E402
from orchestration import catalogue_reparse  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from schemas.catalogue_pipeline.enums import ReviewStatus  # noqa: E402
from services import catalogue_conformance as conformance  # noqa: E402
from services import catalogue_dead_letters as dl  # noqa: E402
from services import catalogue_evidence_extraction as extraction  # noqa: E402
from services import catalogue_pipeline_stages as stages  # noqa: E402
from services.catalogue_submission import CatalogueSubmissionService, RetryNotAllowedError  # noqa: E402
from tests.test_catalogue_golden_suppliers import (  # noqa: E402, F401 — db fixture
    _load_expected,
    _replay_set,
    db,
    golden_set,
)

ANNOTATED_CODE = "CONTRACT_COST_UNPARSEABLE"

_real_decimal_value = conformance._decimal_value


def _pre_fix_decimal_value(value):
    """The parser as it was before a75eb5f: an annotated price is unreadable."""
    if isinstance(value, str) and "(" in value:
        return None
    return _real_decimal_value(value)


def _arm_vision_bomb(monkeypatch):
    """A retrigger that touches the provider is the defect these tests exist for."""

    def bomb(_content: bytes, *, media_type: str):
        raise AssertionError("the vision provider was called during a retrigger")

    monkeypatch.setattr(extraction, "_call_vision", bomb)


def _run_with_bug(db, monkeypatch) -> str:
    """The parent run, ingested while the annotation bug still existed."""
    spec = golden_set("alfamedic")
    with pytest.MonkeyPatch.context() as bug:
        bug.setattr(conformance, "_decimal_value", _pre_fix_decimal_value)
        _replay_set(db, monkeypatch, spec, only_skus=set(_load_expected(spec.path)), refused={})
    return db.query(models.IngestionRun).order_by(models.IngestionRun.id.desc()).first().run_uuid


def test_fix_then_retrigger_clears_exactly_those_rows(db, monkeypatch):
    parent = _run_with_bug(db, monkeypatch)
    service = CatalogueSubmissionService(db)

    before = dl.dead_letters(db, run_uuid=parent)
    annotated = [entry for entry in before if ANNOTATED_CODE in entry.issue_codes]
    assert len(annotated) == 9, "the bug should strand exactly the nine annotated-price rows"
    assert any(entry.supplier_sku == "ME5701" for entry in annotated), (
        "ME5701 is the sheet's $130.00 row and the reason this fix matters"
    )

    # ── Retrigger while the fix has NOT landed: same failure, counted, not duplicated.
    _arm_vision_bomb(monkeypatch)
    with pytest.MonkeyPatch.context() as bug:
        bug.setattr(conformance, "_decimal_value", _pre_fix_decimal_value)
        first = service.retrigger(UUID(parent), issue_code=ANNOTATED_CODE)
        assert first.rows_selected == 9
        assert first.attempt == 1
        catalogue_ingestion_flow(ingestion_run_id=first.submission.ingestion_run_id)

    still_stuck = dl.dead_letters(db, run_uuid=parent)
    assert len(still_stuck) == len(before), "an unfixed retrigger must not shrink the queue"
    survivors = [entry for entry in still_stuck if ANNOTATED_CODE in entry.issue_codes]
    assert len(survivors) == 9, "one entry per row with a count — never one entry per attempt"
    assert {entry.attempts for entry in survivors} == {2}

    # ── The fix lands (module code is the fixed version), retrigger again.
    # Selection comes from the followed queue: all nine are still stuck, so all
    # nine are re-driven — the fix decides which of them clear, not the caller.
    second = service.retrigger(UUID(parent), issue_code=ANNOTATED_CODE)
    assert second.rows_selected == 9
    assert second.attempt == 2
    catalogue_ingestion_flow(ingestion_run_id=second.submission.ingestion_run_id)

    # The child read ONLY the selection, from stored evidence. Six annotated
    # prices now parse; the three rows whose price is literally "10% discount"
    # fail again, because a relative tier is not an amount and the strict parse
    # shape refuses it on purpose.
    child = db.query(models.IngestionRun).filter_by(
        run_uuid=str(second.submission.ingestion_run_id)
    ).one()
    child_rows = db.query(models.CatalogueNormalizedRow).filter_by(
        ingestion_run_uuid=child.run_uuid
    ).count()
    # Eight rows from nine observations: two of the nine are ME5701 at
    # different order quantities, and with both prices readable the pair
    # collapses into one row plus an MBB term. An absorbed observation is a
    # term captured, not a row stuck — the queue logic treats it as cleared.
    assert second.observation_count == 9
    assert child_rows == 8
    child_dead = dl.dead_letters(db, run_uuid=child.run_uuid, follow_retriggers=False)
    assert {entry.supplier_sku for entry in child_dead} == {"79175", "25000", "24575"}, (
        "exactly the three '10% discount' clipper rows survive the fix"
    )

    after = dl.dead_letters(db, run_uuid=parent)
    assert len(after) == len(before) - 6, "the queue shrinks by exactly the cleared rows"
    assert not [entry for entry in after if entry.supplier_sku == "ME5701"], (
        "the sheet's $130.00 row has left the queue"
    )
    survivors_after = [entry for entry in after if ANNOTATED_CODE in entry.issue_codes]
    assert {entry.supplier_sku for entry in survivors_after} == {"79175", "25000", "24575"}
    assert {entry.attempts for entry in survivors_after} == {3}, (
        "three runs have tried these rows and the entry says so — once, with a count"
    )

    # Lineage: the child names its parent, and evidence resolves to whoever
    # paid for the extraction — so a fifth retrigger would still cost nothing.
    assert child.parent_run_id is not None
    parent_row = db.query(models.IngestionRun).filter_by(run_uuid=parent).one()
    assert child.parent_run_id == parent_row.id
    assert catalogue_reparse.evidence_source_run(db, child).run_uuid == parent
    assert catalogue_reparse.retrigger_selection(child) is not None

    # Lanes never follow the chain: the parent's history is still the parent's.
    parent_lanes = dl.lanes_for_run(db, parent).counts
    assert parent_lanes[dl.Lane.DEAD_LETTERED.value] == len(before)


def test_rows_a_person_decided_on_are_refused_by_name(db, monkeypatch):
    spec = golden_set("alfamedic")
    _replay_set(db, monkeypatch, spec, only_skus=set(_load_expected(spec.path)), refused={})
    parent = db.query(models.IngestionRun).order_by(models.IngestionRun.id.desc()).first().run_uuid
    service = CatalogueSubmissionService(db)

    # Reject a pending candidate — a human decision the retrigger must not touch.
    candidate = (
        db.query(models.CatalogueMasteringCandidate)
        .filter_by(ingestion_run_uuid=parent, superseded_by_uuid=None)
        .filter(models.CatalogueMasteringCandidate.review_status == ReviewStatus.PENDING_REVIEW.value)
        .first()
    )
    stages.ReviewDecisionService(db).record_decision(
        stages.RecordReviewDecisionCommand(
            mastering_candidate_id=UUID(candidate.mastering_candidate_uuid),
            actor_id="reviewer@example.com",
            review_status=ReviewStatus.REJECTED,
            reason="Not a product we stock.",
        )
    )

    with pytest.raises(RetryNotAllowedError) as refusal:
        service.retrigger(UUID(parent), catalogue_item_ids=[UUID(candidate.catalogue_item_uuid)])
    assert "a person rejected this row" in str(refusal.value)

    with pytest.raises(RetryNotAllowedError) as unknown:
        service.retrigger(UUID(parent), catalogue_item_ids=[UUID("00000000-0000-0000-0000-000000000001")])
    assert "not a row of this run" in str(unknown.value)

    with pytest.raises(RetryNotAllowedError) as empty:
        service.retrigger(UUID(parent), issue_code="NO_SUCH_CODE")
    assert "Codes actually holding rows" in str(empty.value)

    with pytest.raises(RetryNotAllowedError):
        service.retrigger(
            UUID(parent),
            issue_code=ANNOTATED_CODE,
            catalogue_item_ids=[UUID(candidate.catalogue_item_uuid)],
        )


def test_a_retrigger_child_is_not_a_retrigger_target(db, monkeypatch):
    """The queue lives on the run that was re-driven, so that is where you aim.

    A retrigger of a retrigger would hang one level below where the followed
    queue ever looks: the origin would keep reporting rows as stuck long after
    a grandchild cleared them. Refusing the child and pointing at the origin
    costs nothing — selection reads the followed queue, so retriggering the
    origin re-drives exactly the rows still failing and nothing else.
    """
    parent = _run_with_bug(db, monkeypatch)
    service = CatalogueSubmissionService(db)
    _arm_vision_bomb(monkeypatch)

    with pytest.MonkeyPatch.context() as bug:
        bug.setattr(conformance, "_decimal_value", _pre_fix_decimal_value)
        first = service.retrigger(UUID(parent), issue_code=ANNOTATED_CODE)
        catalogue_ingestion_flow(ingestion_run_id=first.submission.ingestion_run_id)
    child = str(first.submission.ingestion_run_id)

    runs_before = db.query(models.IngestionRun).count()
    with pytest.raises(RetryNotAllowedError) as refusal:
        service.retrigger(UUID(child), issue_code=ANNOTATED_CODE)
    assert parent in str(refusal.value), "the refusal must name the run to aim at instead"
    assert db.query(models.IngestionRun).count() == runs_before, (
        "a refused retrigger must leave no run behind"
    )

    # The recommended door is open and selects the same nine rows.
    replay = service.retrigger(UUID(parent), issue_code=ANNOTATED_CODE)
    assert replay.rows_selected == 9
    assert replay.attempt == 2


def test_a_retrigger_that_has_not_run_changes_nothing(db, monkeypatch):
    """The 202 window, and the failed child: silence is not success.

    In production the child sits queued until the worker claims it. During
    that window — and forever, if the child then fails — the queue must keep
    saying the rows are stuck, because they are. The first implementation
    read "selection with no rows yet" as "absorbed" and emptied the queue the
    moment the request was accepted.
    """
    from orchestration.catalogue_run_lifecycle import fail_run

    parent = _run_with_bug(db, monkeypatch)
    service = CatalogueSubmissionService(db)
    before = dl.dead_letters(db, run_uuid=parent)

    queued = service.retrigger(UUID(parent), issue_code=ANNOTATED_CODE)
    child = db.query(models.IngestionRun).filter_by(
        run_uuid=str(queued.submission.ingestion_run_id)
    ).one()
    assert child.status == "queued"
    # Selection and lineage were stamped in the SAME commit that queued the
    # run — a crash here leaves a selective retrigger or nothing, never a
    # full re-parse wearing a retrigger's name.
    assert catalogue_reparse.retrigger_selection(child) is not None

    during = dl.dead_letters(db, run_uuid=parent)
    assert len(during) == len(before), "a queued retrigger must not move the queue"
    assert {entry.attempts for entry in during} == {1}, "nothing has been attempted yet"

    # While the child is outstanding, a second retrigger is refused by name:
    # it would select the very same rows and mint a duplicate review candidate
    # for every row both copies clear.
    with pytest.raises(RetryNotAllowedError) as second:
        service.retrigger(UUID(parent), issue_code=ANNOTATED_CODE)
    assert child.run_uuid in str(second.value)
    assert "duplicate review candidate" in str(second.value)

    fail_run(db, ingestion_run_id=UUID(child.run_uuid), error_code="TEST", message="worker died")
    after_failure = dl.dead_letters(db, run_uuid=parent)
    assert len(after_failure) == len(before), (
        "a FAILED retrigger processed nothing — its rows are still stuck and must still say so"
    )

    # A failed child is no longer outstanding: the road reopens, the same rows
    # are selectable, and the request ordinal remembers the attempt that died.
    reopened = service.retrigger(UUID(parent), issue_code=ANNOTATED_CODE)
    assert reopened.rows_selected == 9
    assert reopened.attempt == 2


# ── DEV-303: the held-lane fix-it loop, end to end ───────────────────────────
#
# Two bugs shipped against this loop. (1) "Edit → re-run does nothing": the
# desk fired the retrigger and never showed the in-flight child, so the queue
# looked frozen — the visibility now lives in dl.retrigger_attempts and the
# tests below pin that a correction really does change the outcome. (2) "A
# required-but-unread column cannot be supplied": corrections only replaced
# existing cells, so the one fix a CONTRACT_REQUIRED_FIELD_MISSING row needs
# was impossible — additions are now allowed for exactly the contract-required
# columns the observation lacks.

import json  # noqa: E402
from collections import Counter  # noqa: E402

from orchestration.catalogue_contract_resolution import resolve_recorded_supplier_contract  # noqa: E402
from services import catalogue_evidence_corrections as corrections  # noqa: E402


def _single_annotated_row(db, parent: str):
    """One stranded annotated-price row safe to reason about alone: a single
    observation, a supplier code unique across the whole queue, and the
    parenthesised price cell that stranded it."""
    queue = dl.dead_letters(db, run_uuid=parent)
    sku_counts = Counter(entry.supplier_sku for entry in queue)
    for entry in queue:
        if ANNOTATED_CODE not in entry.issue_codes:
            continue
        if len(entry.observation_uuids) != 1 or sku_counts[entry.supplier_sku] != 1:
            continue
        row = (
            db.query(models.CatalogueExtractedEvidence)
            .filter_by(ingestion_run_uuid=parent, raw_observation_uuid=entry.observation_uuids[0])
            .one()
        )
        for cell in json.loads(row.raw_cells_json or "[]"):
            value = cell.get("raw_value")
            if not cell.get("column_name") or not isinstance(value, str) or "(" not in value:
                continue
            clean = value.split("(")[0].strip()
            if clean and any(ch.isdigit() for ch in clean):
                return entry, row, cell["column_name"], clean
    raise AssertionError("no single-observation annotated row with a parenthesised price found")


def test_corrected_evidence_clears_the_row_on_retrigger(db, monkeypatch):
    """Fix the EVIDENCE (not the parser), re-run, and the row leaves the queue.

    The annotation bug stays armed throughout: the only thing that changes is
    what a person typed into the misread cell — so a row clearing proves the
    retrigger re-read the corrected evidence, which is the loop the held lane
    promises."""
    parent = _run_with_bug(db, monkeypatch)
    service = CatalogueSubmissionService(db)
    entry, row, column, clean_value = _single_annotated_row(db, parent)
    before = dl.dead_letters(db, run_uuid=parent)

    corrections.correct_evidence(
        db,
        run_uuid=UUID(parent),
        raw_observation_uuid=UUID(row.raw_observation_uuid),
        cells={column: clean_value},
        reason="the page prints the price without the remark",
        corrected_by="tester",
    )

    _arm_vision_bomb(monkeypatch)
    with pytest.MonkeyPatch.context() as bug:
        bug.setattr(conformance, "_decimal_value", _pre_fix_decimal_value)
        result = service.retrigger(UUID(parent), issue_code=ANNOTATED_CODE)

        # What the desk polls on: the attempt is visible BEFORE it runs, so a
        # fired re-run never looks like a no-op again.
        (queued,) = dl.retrigger_attempts(db, parent)
        assert queued.status == models.IngestionRunStatus.QUEUED.value
        assert queued.attempt == 1
        assert queued.observations == result.observation_count

        catalogue_ingestion_flow(ingestion_run_id=result.submission.ingestion_run_id)

    (ran,) = dl.retrigger_attempts(db, parent)
    assert ran.status in (
        models.IngestionRunStatus.COMPLETED.value,
        models.IngestionRunStatus.COMPLETED_WITH_WARNINGS.value,
    )

    # Lineage the desk shows up front: the child names the queue it belongs
    # to, the parent names none — so Re-run buttons never appear on a run
    # whose retrigger the service would refuse.
    child_run = db.query(models.IngestionRun).filter_by(
        run_uuid=str(result.submission.ingestion_run_id)
    ).one()
    assert catalogue_reparse.retrigger_source(child_run) == parent
    parent_run = db.query(models.IngestionRun).filter_by(run_uuid=parent).one()
    assert catalogue_reparse.retrigger_source(parent_run) is None

    after = dl.dead_letters(db, run_uuid=parent)
    assert entry.supplier_sku not in {e.supplier_sku for e in after}, (
        "the corrected row must leave the queue — this is the loop the held lane sells"
    )
    assert len(after) == len(before) - 1, "exactly the corrected row clears; the bug still holds the rest"
    survivors = [e for e in after if ANNOTATED_CODE in e.issue_codes]
    assert survivors and {e.attempts for e in survivors} == {2}


def test_added_required_column_clears_the_row_on_retrigger(db, monkeypatch):
    """A cell the scan never produced is added by hand and the re-run reads it.

    The cost cell is stripped outright (the 'scan read nothing here' shape),
    the contract offers it back as an addable REQUIRED column, a person types
    the printed price in, and the retrigger — annotation bug still armed —
    publishes the row from the ADDED cell."""
    parent = _run_with_bug(db, monkeypatch)
    service = CatalogueSubmissionService(db)
    entry, row, _column, clean_value = _single_annotated_row(db, parent)

    # Strip by the cost field's whole name-set (source column AND aliases):
    # the printed heading may be an alias, and leaving a sibling spelling
    # behind would keep the field resolvable — not the "read nothing" shape.
    contract = resolve_recorded_supplier_contract(db, ingestion_run_id=UUID(parent))
    cost_field_key = contract.declaration.pricing.cost_source_field
    cost_field = next(f for f in contract.declaration.fields if f.field_key == cost_field_key)
    cost_keys = {
        key
        for name in (cost_field.source_column, cost_field.source_path, *cost_field.aliases)
        if name
        for key in conformance._column_keys(name)
    }
    original_cells = json.loads(row.raw_cells_json)
    stripped = [
        c
        for c in original_cells
        if not (c.get("column_name") and set(conformance._column_keys(c["column_name"])) & cost_keys)
    ]
    assert len(stripped) < len(original_cells)
    row.raw_cells_json = json.dumps(stripped, ensure_ascii=False)
    db.commit()

    offered = {
        col.field_key: col.column_name
        for col in conformance.addable_required_columns(contract, stripped)
    }
    assert cost_field_key in offered, (
        "with its cell stripped, the REQUIRED cost column must be offered for adding"
    )
    add_column = offered[cost_field_key]

    corrections.correct_evidence(
        db,
        run_uuid=UUID(parent),
        raw_observation_uuid=UUID(row.raw_observation_uuid),
        cells={add_column: clean_value},
        reason="the page prints the price — the scan dropped the cell",
        corrected_by="tester",
    )

    _arm_vision_bomb(monkeypatch)
    with pytest.MonkeyPatch.context() as bug:
        bug.setattr(conformance, "_decimal_value", _pre_fix_decimal_value)
        result = service.retrigger(UUID(parent), issue_code=ANNOTATED_CODE)
        catalogue_ingestion_flow(ingestion_run_id=result.submission.ingestion_run_id)

    after = dl.dead_letters(db, run_uuid=parent)
    assert entry.supplier_sku not in {e.supplier_sku for e in after}, (
        "a row rescued by an ADDED required cell must leave the queue like any other fix"
    )

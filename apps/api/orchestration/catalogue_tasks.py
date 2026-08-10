"""Prefect tasks for catalogue ingestion orchestration."""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from uuid import UUID

from prefect import get_run_logger, task

import database
import models
from services import catalogue_pipeline_stages as stages
from services.catalogue_conformance import (
    ConformanceOutcome,
    conform_observations,
)

from .catalogue_contract_resolution import resolve_recorded_supplier_contract
from .catalogue_extraction_adapter import (
    evidence_outcome_from_result,
    extract_source_result,
)
from .catalogue_raw_stage import complete_raw_stage
from .catalogue_run_lifecycle import (
    claim_queued_run,
    complete_run,
    fail_run,
    mark_stage,
    mark_stage_progress,
    terminal_result_for_replay,
)
from .catalogue_source_loader import load_and_verify_source_asset
from . import catalogue_reparse
from .catalogue_stage_adapter import (
    evidence_from_persisted_observation,
    mastering_command_for_claim,
    raw_input_from_extracted_evidence,
    normalized_row_command_from_conformance,
)
from .catalogue_types import (
    CatalogueFlowResult,
    CatalogueOrchestrationError,
    EvidenceOutcome,
    RawStageResult,
    RunIdentity,
    # Re-exported: __all__ has always advertised it beside
    # terminal_replay_result_task, but it was never imported, so any
    # `from .catalogue_tasks import *` raised AttributeError on the name.
    TerminalRunReplay,
    TransientProviderError,
)


# Nobody watching a progress bar can perceive faster than this, and each write
# is a transaction competing with the extraction it is reporting on.
_PROGRESS_WRITE_INTERVAL_SECONDS = 1.0


@task(name="load-and-claim-catalogue-run", retries=0)
def load_and_claim_run_task(ingestion_run_id: str) -> None:
    db = database.SessionLocal()
    try:
        claim_queued_run(db, ingestion_run_id=UUID(ingestion_run_id))
    finally:
        db.close()


def report_stage(ingestion_run_id: str, stage: str, *, units_total: int | None = None) -> None:
    """Record where the run has reached, for anything watching it.

    Deliberately NOT a task and deliberately silent on failure. Progress is a
    report about the work; a run that completes perfectly but could not write
    "interpreting" to a column has not failed, and must not be recorded as
    having done so.
    """

    db = database.SessionLocal()
    try:
        mark_stage(db, ingestion_run_id=UUID(ingestion_run_id), stage=stage, units_total=units_total)
    except Exception:
        db.rollback()
    finally:
        db.close()


def _unit_progress_writer(ingestion_run_id: str):
    """A callback that writes page progress, throttled to at most one write/sec.

    Pages land from several pool threads at once. Writing every one of them
    would put an unbounded number of small transactions in the path of the
    extraction itself, and nobody watching a bar can perceive faster than about
    a second anyway. The final unit always writes, so the count never rests
    short of its total.
    """

    lock = threading.Lock()
    last_written = 0.0

    def write(done: int, total: int) -> None:
        nonlocal last_written
        now = time.monotonic()
        with lock:
            if done < total and now - last_written < _PROGRESS_WRITE_INTERVAL_SECONDS:
                return
            last_written = now
        db = database.SessionLocal()
        try:
            mark_stage_progress(
                db, ingestion_run_id=UUID(ingestion_run_id), units_done=done, units_total=total
            )
        except Exception:
            db.rollback()
        finally:
            db.close()

    return write


@task(name="terminal-catalogue-run-replay", retries=0)
def terminal_replay_result_task(ingestion_run_id: str) -> CatalogueFlowResult:
    db = database.SessionLocal()
    try:
        return terminal_result_for_replay(db, ingestion_run_id=UUID(ingestion_run_id))
    finally:
        db.close()


@task(name="complete-raw-stage", retries=0)
def raw_stage_task(ingestion_run_id: str) -> RawStageResult:
    """File-only raw stage: verify, audit and describe the stored original.

    Returns identifiers and integrity metadata without file content. No
    extraction, parsing or AI provider is reachable from this task.
    """

    db = database.SessionLocal()
    try:
        # A re-parse never opens the file — see reparse_raw_stage.
        if catalogue_reparse.is_reparse(db, UUID(ingestion_run_id)):
            return catalogue_reparse.reparse_raw_stage(db, ingestion_run_id=UUID(ingestion_run_id))
        return complete_raw_stage(db, ingestion_run_id=UUID(ingestion_run_id))
    finally:
        db.close()


@task(name="resolve-recorded-supplier-contract", retries=0)
def resolve_recorded_contract_task(ingestion_run_id: str):
    db = database.SessionLocal()
    try:
        return resolve_recorded_supplier_contract(db, ingestion_run_id=UUID(ingestion_run_id))
    finally:
        db.close()


def _retry_transient_provider(_task, _run, state) -> bool:
    return _is_transient_failure(state)


@task(
    name="extract-source-evidence",
    retries=2,
    retry_delay_seconds=10,
    retry_condition_fn=_retry_transient_provider,
)
def extract_source_evidence_task(ingestion_run_id: str) -> EvidenceOutcome:
    """Extraction stage: consumes the raw stage's durable source reference.

    Loads (and re-verifies) the stored original itself, so no file bytes ever
    cross a task boundary and extraction never depends on an in-memory upload
    surviving beyond the raw stage.
    """

    # A re-parse already has the evidence — it exists to change what we make of
    # it, not to re-derive it. Reading the RAW layer here keeps the rest of the
    # flow identical and never reaches the provider.
    db = database.SessionLocal()
    try:
        if catalogue_reparse.is_reparse(db, UUID(ingestion_run_id)):
            stored = catalogue_reparse.load_stored_evidence(db, ingestion_run_id=UUID(ingestion_run_id))
            return EvidenceOutcome(
                observations=stored.observations,
                rejected_units=0,
                warnings=(f"re-parsed {len(stored.observations)} stored observations from run {stored.source_run_uuid}",),
            )
        asset = load_and_verify_source_asset(db, ingestion_run_id=UUID(ingestion_run_id))
    finally:
        db.close()
    started_at = datetime.now(timezone.utc)
    result = extract_source_result(asset, on_unit_complete=_unit_progress_writer(ingestion_run_id))
    db = database.SessionLocal()
    try:
        from services.catalogue_extraction_attempts import persist_extraction_attempt

        persist_extraction_attempt(
            db,
            ingestion_run_id=UUID(ingestion_run_id),
            result=result,
            started_at=started_at,
        )
    finally:
        db.close()
    return evidence_outcome_from_result(result)


@task(name="capture-extracted-evidence", retries=0)
def capture_extracted_evidence_task(
    identity: RunIdentity,
    observations: tuple,
) -> tuple[tuple[UUID, ...], int, int]:
    db = database.SessionLocal()
    try:
        result = stages.ExtractedEvidenceService(db).capture(
            stages.CaptureExtractedEvidenceCommand(
                ingestion_run_id=identity.run_uuid,
                supplier_catalogue_id=identity.supplier_catalogue_id,
                source_file_id=identity.source_file_id,
                supplier_id=identity.supplier_id,
                contract_id=identity.contract_id,
                contract_version=identity.contract_version,
                observations=tuple(raw_input_from_extracted_evidence(observation) for observation in observations),
            )
        )
        return (
            tuple(UUID(str(item)) for item in result.output_ids),
            result.metrics.created_count,
            result.metrics.reused_count,
        )
    finally:
        db.close()


@task(name="conform-evidence-to-normalized-rows", retries=0)
def normalize_evidence_task(
    raw_observation_ids: tuple[UUID, ...],
    runtime_contract,
) -> ConformanceOutcome:
    """Intermediate conformance: map persisted Staging evidence to normalized claims.

    Reloads each observation from persistence — never the extraction task's
    in-memory objects — so conformance is grounded against exactly what the
    Staging layer committed. Deterministic: the supplier contract maps source
    columns to fields; no model provider is reachable from this task.
    """

    from services import catalogue_pipeline_persistence as persistence

    db = database.SessionLocal()
    try:
        observations = []
        for raw_id in raw_observation_ids:
            row = (
                db.query(models.CatalogueExtractedEvidence)
                .filter_by(raw_observation_uuid=str(raw_id))
                .first()
            )
            if row is None:
                raise stages.UpstreamRecordNotFound(f"Persisted evidence observation {raw_id} was not found")
            observations.append(evidence_from_persisted_observation(persistence.extracted_evidence_to_contract(row)))
    finally:
        db.close()
    return conform_observations(tuple(observations), tuple(raw_observation_ids), runtime_contract)


@task(name="build-normalized-rows", retries=0)
def build_normalized_rows_task(
    conformance: ConformanceOutcome,
) -> tuple[tuple[UUID, ...], int, int]:
    """Persist normalized claims (Intermediate output) as one atomic batch.

    Each build stages within the shared transaction and a single commit
    lands the whole batch — a mid-batch failure leaves no partial rows.
    """

    db = database.SessionLocal()
    try:
        service = stages.NormalizedRowService(db, commit=False)
        output_ids: list[UUID] = []
        created = reused = 0
        for item in conformance.items:
            result = service.build_item(normalized_row_command_from_conformance(item))
            output_ids.extend(UUID(str(output_id)) for output_id in result.output_ids)
            created += result.metrics.created_count
            reused += result.metrics.reused_count
        db.commit()
        return tuple(output_ids), created, reused
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@task(name="evaluate-normalized-rows", retries=0)
def evaluate_normalized_rows_task(
    run_id: str,
    staging_ids: tuple[UUID, ...],
    contract_ambiguities: tuple = (),
) -> tuple[int, int, int]:
    # ONE atomic batch: a mid-batch failure must leave zero validation output
    # rather than a partially materialized stage on a terminal (non-resumable)
    # failed run.
    db = database.SessionLocal()
    try:
        service = stages.CatalogueValidationService(db, commit=False)
        created = reused = blocking = 0
        ambiguity_result = service.record_run_ambiguities(
            ingestion_run_id=UUID(run_id),
            ambiguities=tuple(contract_ambiguities),
        )
        created += ambiguity_result.metrics.created_count
        reused += ambiguity_result.metrics.reused_count
        for staging_id in staging_ids:
            result = service.evaluate_claim(stages.EvaluateNormalizedRowCommand(catalogue_item_id=staging_id))
            created += result.metrics.created_count
            reused += result.metrics.reused_count
            blocking += result.metrics.blocking_issue_count
        db.commit()
        return created, reused, blocking
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@task(name="prepare-pending-review-candidates", retries=0)
def prepare_eligible_candidates_task(
    identity: RunIdentity,
    staging_ids: tuple[UUID, ...],
    conformance: ConformanceOutcome,
) -> tuple[int, int, tuple[str, ...]]:
    # ONE atomic batch (see evaluate task): blocked rows are an expected,
    # per-row skip; any unexpected failure rolls the whole stage back.
    db = database.SessionLocal()
    try:
        service = stages.MasteringService(db, commit=False)
        created = reused = 0
        warnings: list[str] = []
        for staging_id, item in zip(staging_ids, conformance.items, strict=True):
            try:
                result = service.prepare_candidate(
                    mastering_command_for_claim(
                        run_identity=identity,
                        catalogue_item_id=staging_id,
                        item=item,
                    )
                )
                created += result.metrics.created_count
                reused += result.metrics.reused_count
            except stages.BlockingValidationIssues:
                warnings.append(f"interpreted claim {staging_id} has open blocking validation issues")
        db.commit()
        return created, reused, tuple(warnings)
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@task(name="finalize-catalogue-run", retries=1, retry_delay_seconds=3)
def finalize_run_task(result: CatalogueFlowResult) -> CatalogueFlowResult:
    db = database.SessionLocal()
    try:
        complete_run(db, result=result)
        return result
    finally:
        db.close()


@task(name="record-catalogue-run-failure", retries=1, retry_delay_seconds=3)
def record_run_failure_task(ingestion_run_id: str, error_code: str, message: str) -> None:
    db = database.SessionLocal()
    try:
        fail_run(db, ingestion_run_id=UUID(ingestion_run_id), error_code=error_code, message=message)
    finally:
        db.close()


def log_flow_result(result: CatalogueFlowResult) -> None:
    logger = get_run_logger()
    logger.info(
        (
            "catalogue ingestion run %s finished status=%s rows=%s raw_created=%s "
            "staging_created=%s issues=%s candidates=%s"
        ),
        result.ingestion_run_id,
        result.terminal_status,
        result.rows_extracted,
        result.raw_observations_created,
        result.staging_items_created,
        result.validation_issues_created + result.validation_issues_reused,
        result.mastering_candidates_created + result.mastering_candidates_reused,
    )


def failure_result(ingestion_run_id: str, exc: CatalogueOrchestrationError) -> CatalogueFlowResult:
    return CatalogueFlowResult(
        ingestion_run_id=UUID(ingestion_run_id),
        terminal_status="failed",
        warnings=(exc.public_message(),),
        human_review_required=False,
        error_code=exc.error_code,
    )


def _is_transient_failure(state) -> bool:
    try:
        value = state.result(raise_on_failure=False)
    except Exception:
        return False
    return isinstance(value, TransientProviderError)


__all__ = [
    "CatalogueOrchestrationError",
    "TerminalRunReplay",
    "load_and_claim_run_task",
    "terminal_replay_result_task",
    "raw_stage_task",
    "resolve_recorded_contract_task",
    "extract_source_evidence_task",
    "capture_extracted_evidence_task",
    "normalize_evidence_task",
    "build_normalized_rows_task",
    "evaluate_normalized_rows_task",
    "prepare_eligible_candidates_task",
    "finalize_run_task",
    "record_run_failure_task",
    "log_flow_result",
    "failure_result",
]

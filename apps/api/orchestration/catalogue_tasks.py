"""Prefect tasks for catalogue ingestion orchestration."""

from __future__ import annotations

from uuid import UUID

from prefect import get_run_logger, task

import database
from services import catalogue_pipeline_stages as stages

from .catalogue_contract_resolution import resolve_recorded_supplier_contract
from .catalogue_extraction_adapter import extract_source_evidence
from .catalogue_run_lifecycle import claim_queued_run, complete_run, fail_run, terminal_result_for_replay
from .catalogue_source_loader import load_and_verify_source_asset
from .catalogue_stage_adapter import (
    mastering_command_for_staging,
    raw_input_from_extracted_row,
    staging_command_from_extracted_row,
)
from .catalogue_types import (
    CatalogueFlowResult,
    CatalogueOrchestrationError,
    TransientExtractionError,
    VerifiedSourceAsset,
)


@task(name="load-and-claim-catalogue-run", retries=0)
def load_and_claim_run_task(ingestion_run_id: str) -> None:
    db = database.SessionLocal()
    try:
        claim_queued_run(db, ingestion_run_id=UUID(ingestion_run_id))
    finally:
        db.close()


@task(name="terminal-catalogue-run-replay", retries=0)
def terminal_replay_result_task(ingestion_run_id: str) -> CatalogueFlowResult:
    db = database.SessionLocal()
    try:
        return terminal_result_for_replay(db, ingestion_run_id=UUID(ingestion_run_id))
    finally:
        db.close()


@task(name="load-and-verify-catalogue-source", retries=0)
def load_and_verify_source_task(ingestion_run_id: str) -> VerifiedSourceAsset:
    db = database.SessionLocal()
    try:
        return load_and_verify_source_asset(db, ingestion_run_id=UUID(ingestion_run_id))
    finally:
        db.close()


@task(name="resolve-recorded-supplier-contract", retries=0)
def resolve_recorded_contract_task(ingestion_run_id: str):
    db = database.SessionLocal()
    try:
        return resolve_recorded_supplier_contract(db, ingestion_run_id=UUID(ingestion_run_id))
    finally:
        db.close()


def _retry_transient_extraction(_task, _run, state) -> bool:
    return _is_transient_failure(state)


@task(
    name="extract-source-located-evidence",
    retries=2,
    retry_delay_seconds=10,
    retry_condition_fn=_retry_transient_extraction,
)
def extract_source_evidence_task(source: VerifiedSourceAsset, runtime_contract) -> tuple:
    result = extract_source_evidence(source, runtime_contract)
    return result.rows, result.rejected_count, result.warnings


@task(name="capture-raw-observations", retries=0)
def capture_raw_observations_task(source: VerifiedSourceAsset, rows: tuple) -> tuple[tuple[UUID, ...], int, int]:
    db = database.SessionLocal()
    try:
        result = stages.RawObservationService(db).capture(
            stages.CaptureRawObservationsCommand(
                ingestion_run_id=source.run_identity.run_uuid,
                supplier_catalogue_id=source.run_identity.supplier_catalogue_id,
                source_file_id=source.run_identity.source_file_id,
                supplier_id=source.run_identity.supplier_id,
                contract_id=source.run_identity.contract_id,
                contract_version=source.run_identity.contract_version,
                observations=tuple(raw_input_from_extracted_row(row) for row in rows),
            )
        )
        return (
            tuple(UUID(str(item)) for item in result.output_ids),
            result.metrics.created_count,
            result.metrics.reused_count,
        )
    finally:
        db.close()


@task(name="build-staging-items", retries=0)
def build_staging_items_task(
    rows: tuple,
    raw_observation_ids: tuple[UUID, ...],
    runtime_contract,
) -> tuple[tuple[UUID, ...], int, int]:
    db = database.SessionLocal()
    try:
        service = stages.StagingCatalogueService(db)
        output_ids: list[UUID] = []
        created = reused = 0
        for row, raw_id in zip(rows, raw_observation_ids, strict=True):
            result = service.build_item(
                staging_command_from_extracted_row(
                    row,
                    raw_observation_id=raw_id,
                    runtime_contract=runtime_contract,
                )
            )
            output_ids.extend(UUID(str(item)) for item in result.output_ids)
            created += result.metrics.created_count
            reused += result.metrics.reused_count
        return tuple(output_ids), created, reused
    finally:
        db.close()


@task(name="evaluate-staging-items", retries=0)
def evaluate_staging_items_task(staging_ids: tuple[UUID, ...]) -> tuple[int, int, int]:
    db = database.SessionLocal()
    try:
        service = stages.CatalogueValidationService(db)
        created = reused = blocking = 0
        for staging_id in staging_ids:
            result = service.evaluate_staging(stages.EvaluateStagingCommand(catalogue_item_id=staging_id))
            created += result.metrics.created_count
            reused += result.metrics.reused_count
            blocking += result.metrics.blocking_issue_count
        return created, reused, blocking
    finally:
        db.close()


@task(name="prepare-pending-review-candidates", retries=0)
def prepare_eligible_candidates_task(
    source: VerifiedSourceAsset,
    rows: tuple,
    staging_ids: tuple[UUID, ...],
) -> tuple[int, int, tuple[str, ...]]:
    db = database.SessionLocal()
    try:
        service = stages.MasteringService(db)
        created = reused = 0
        warnings: list[str] = []
        for row, staging_id in zip(rows, staging_ids, strict=True):
            try:
                result = service.prepare_candidate(
                    mastering_command_for_staging(
                        run_identity=source.run_identity,
                        catalogue_item_id=staging_id,
                        row=row,
                    )
                )
                created += result.metrics.created_count
                reused += result.metrics.reused_count
            except stages.BlockingValidationIssues:
                warnings.append(f"staging item {staging_id} has open blocking validation issues")
        return created, reused, tuple(warnings)
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
    return isinstance(value, TransientExtractionError)


__all__ = [
    "CatalogueOrchestrationError",
    "TerminalRunReplay",
    "load_and_claim_run_task",
    "terminal_replay_result_task",
    "load_and_verify_source_task",
    "resolve_recorded_contract_task",
    "extract_source_evidence_task",
    "capture_raw_observations_task",
    "build_staging_items_task",
    "evaluate_staging_items_task",
    "prepare_eligible_candidates_task",
    "finalize_run_task",
    "record_run_failure_task",
    "log_flow_result",
    "failure_result",
]

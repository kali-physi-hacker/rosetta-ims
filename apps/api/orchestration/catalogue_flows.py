"""Prefect flows for catalogue ingestion."""

from __future__ import annotations

from uuid import UUID

from prefect import flow

from .catalogue_tasks import (
    build_staging_items_task,
    capture_raw_observations_task,
    evaluate_staging_items_task,
    extract_source_evidence_task,
    failure_result,
    finalize_run_task,
    load_and_claim_run_task,
    load_and_verify_source_task,
    log_flow_result,
    prepare_eligible_candidates_task,
    record_run_failure_task,
    resolve_recorded_contract_task,
    terminal_replay_result_task,
)
from .catalogue_types import (
    CatalogueFlowResult,
    CatalogueOrchestrationError,
    DuplicateRunClaim,
    RunNotFound,
    TerminalRunReplay,
)


@flow(name="catalogue-ingestion")
def catalogue_ingestion_flow(*, ingestion_run_id: UUID) -> CatalogueFlowResult:
    """Run the machine portion of catalogue ingestion for one queued run UUID."""

    run_id = str(ingestion_run_id)
    try:
        load_and_claim_run_task(run_id)
    except TerminalRunReplay:
        return terminal_replay_result_task(run_id)
    except DuplicateRunClaim as exc:
        return CatalogueFlowResult(
            ingestion_run_id=ingestion_run_id,
            terminal_status="running",
            warnings=(exc.public_message(),),
            error_code=exc.error_code,
        )
    except RunNotFound as exc:
        return failure_result(run_id, exc)
    except CatalogueOrchestrationError as exc:
        record_run_failure_task(run_id, exc.error_code, exc.public_message())
        return failure_result(run_id, exc)

    try:
        source = load_and_verify_source_task(run_id)
        runtime_contract = resolve_recorded_contract_task(run_id)
        rows, rejected_count, extraction_warnings = extract_source_evidence_task(source, runtime_contract)
        raw_ids, raw_created, raw_reused = capture_raw_observations_task(source, rows)
        staging_ids, staging_created, staging_reused = build_staging_items_task(rows, raw_ids, runtime_contract)
        validation_created, validation_reused, blocking_count = evaluate_staging_items_task(staging_ids)
        candidate_created, candidate_reused, candidate_warnings = prepare_eligible_candidates_task(
            source,
            rows,
            staging_ids,
        )
        warnings = tuple(extraction_warnings) + tuple(candidate_warnings)
        if rejected_count or blocking_count or validation_created or validation_reused or warnings:
            terminal_status = "completed_with_warnings"
        else:
            terminal_status = "completed"
        result = CatalogueFlowResult(
            ingestion_run_id=ingestion_run_id,
            terminal_status=terminal_status,
            rows_extracted=len(rows),
            raw_observations_created=raw_created,
            raw_observations_reused=raw_reused,
            staging_items_created=staging_created,
            staging_items_reused=staging_reused,
            validation_issues_created=validation_created,
            validation_issues_reused=validation_reused,
            mastering_candidates_created=candidate_created,
            mastering_candidates_reused=candidate_reused,
            rows_rejected=rejected_count,
            warnings=warnings,
            human_review_required=True,
        )
        finalized = finalize_run_task(result)
        log_flow_result(finalized)
        return finalized
    except CatalogueOrchestrationError as exc:
        if not isinstance(exc, RunNotFound):
            record_run_failure_task(run_id, exc.error_code, exc.public_message())
        return failure_result(run_id, exc)

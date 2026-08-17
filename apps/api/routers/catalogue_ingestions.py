"""Queued catalogue ingestion boundary (evidence-first pipeline)."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

import database
import models
from permissions import has_capability, require_capability
from services import audit_log
from services import catalogue_dead_letters as dead_letters
from services import catalogue_evidence_corrections as evidence_corrections
from services import catalogue_pipeline_persistence as persistence
from services import catalogue_pipeline_stages as stages
from services import catalogue_review_summary as review_summary
from services import variant_similarity
from services import catalogue_golden_export
from orchestration import catalogue_reparse
from orchestration.catalogue_source_loader import load_and_verify_source_asset
from orchestration.catalogue_types import RunNotFound, SourceVerificationError
from schemas.catalogue_pipeline.enums import IssueResolutionStatus, ReviewStatus
from services.catalogue_submission import (
    RetryNotAllowedError,
    SourceFileMissingError,
    CatalogueIngestionStatus,
    CatalogueSubmissionCommand,
    CatalogueSubmissionError,
    CatalogueSubmissionResult,
    CatalogueSubmissionService,
    ContractParameterError,
    EmptyUploadError,
    MalformedFilenameError,
    SubmissionPersistenceError,
    StorageUnavailableError,
    SubmissionIdempotencyConflict,
    SubmissionNotFoundError,
    SupplierContractAmbiguousError,
    SupplierContractMismatchError,
    SupplierContractSelectionError,
    UnknownSupplierError,
    UnsupportedSourceTypeError,
    UploadTooLargeError,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/catalogues", tags=["catalogue-ingestions"])


class CatalogueSubmissionResponse(BaseModel):
    ingestion_run_id: UUID = Field(..., description="Stable ingestion run UUID.")
    supplier_catalogue_id: UUID = Field(..., description="Stable source catalogue UUID.")
    source_file_id: UUID = Field(..., description="Stable source file UUID.")
    supplier_id: int = Field(..., gt=0, description="Supplier ID submitted by the client.")
    contract_id: str = Field(..., description="Resolved supplier-source contract ID.")
    contract_version: str = Field(..., description="Resolved supplier-source contract version.")
    document_type: str = Field(..., description="Resolved supplier document type.")
    status: str = Field(..., description="Queued ingestion run status.")
    submitted_at: str = Field(..., description="Timezone-aware submission timestamp.")
    status_url: str = Field(..., description="Polling URL for this queued run.")


class CatalogueIngestionStatusResponse(BaseModel):
    ingestion_run_id: UUID
    supplier_catalogue_id: UUID | None = None
    source_file_id: UUID | None = None
    supplier_id: int | None = None
    contract_id: str | None = None
    contract_version: str | None = None
    document_type: str | None = None
    status: str
    submitted_at: str
    started_at: str | None = None
    completed_at: str | None = None
    items_extracted: int | None = None
    # The BO-facing rows figure: catalogue PRODUCT rows (normalized rows).
    # items_extracted counts raw observations, which include page-level text
    # lines (banners, effective dates) — extraction accounting, not products.
    product_rows: int | None = None
    metrics: dict[str, Any] | None = None
    error_summary: dict[str, Any] | str | None = None
    retry_of: str | None = None
    superseded_by_run: str | None = None
    source_filename: str | None = None      # the supplier catalogue this run read
    source_received_at: str | None = None
    reparse_of: str | None = None           # source run when this re-read stored evidence

    # Live progress; every field is null unless the run is working right now.
    stage: str | None = None
    stage_label: str | None = None
    stage_started_at: str | None = None
    stage_index: int | None = None
    stage_count: int | None = None
    units_done: int | None = None
    units_total: int | None = None


class ValidationIssueResolutionRequest(BaseModel):
    resolution_status: IssueResolutionStatus
    resolution_note: str | None = None
    resolved_at: datetime | None = None


class MasteringReviewRequest(BaseModel):
    review_status: ReviewStatus
    reason: str | None = None
    override_reason: str | None = None
    expected_candidate_created_at: str | None = None
    decided_at: datetime | None = None


class EvidenceCorrectionRequest(BaseModel):
    """Human correction of misread cells on one extracted-evidence observation.

    Cells are keyed by the observation's own column names and REPLACE what the
    extraction read there — columns the observation does not carry refuse by
    name. The original values, reason, and author are stamped into the
    observation's metadata; re-parse or retrigger then re-reads the corrected
    evidence.
    """

    reason: str = Field(min_length=4)
    cells: dict[str, str | None] = Field(min_length=1)


class MasteringCorrectionRequest(BaseModel):
    """Human correction of one or more candidate resolution sections.

    Produces an immutable revised candidate superseding this one; the revision
    is what gets approved. Sections are validated by the stage service against
    the mastering-candidate contract.
    """

    reason: str
    expected_candidate_created_at: str | None = None
    revised_at: datetime | None = None
    supplier_product_resolution: dict[str, Any] | None = None
    product_variant_resolution: dict[str, Any] | None = None
    packaging_resolution: dict[str, Any] | None = None
    supplier_price_resolution: dict[str, Any] | None = None
    mbb_resolution: dict[str, Any] | None = None
    product_family_resolution: dict[str, Any] | None = None
    brand_resolution: dict[str, Any] | None = None
    category_resolution: dict[str, Any] | None = None


class CommercialApplicationRequest(BaseModel):
    applied_at: datetime | None = None


class ServingPublicationRequest(BaseModel):
    publication_version: str = Field(..., min_length=1)
    published_at: datetime | None = None


class PipelineActionResponse(BaseModel):
    stage: str
    status: str
    output_ids: list[str]
    metrics: dict[str, int]


@router.post("/import", deprecated=True)
def removed_v1_catalogue_import() -> None:
    """Tombstone for the removed v1 synchronous import (and its matching UI):
    an explicit 410 beats a bare 404 for any straggler client."""
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=_detail(
            "ENDPOINT_REMOVED",
            "The synchronous v1 catalogue import was removed. Submit files to "
            "/catalogues/ingestions and review them in the catalogue review UI.",
        ),
    )


@router.get("/supplier-contracts")
def list_supported_supplier_contracts(
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """SUPPORTED document formats per supplier — the upload form's picker.

    A supplier with one entry resolves automatically at submission; with
    more than one, the upload must say which format the file is, because
    supplier-only resolution refuses to guess between layouts.
    """
    from schemas.catalogue_pipeline.supplier_contracts import (
        SupplierContractSupportStatus,
        iter_supplier_source_contracts,
    )

    suppliers: dict[str, list[dict[str, Any]]] = {}
    for item in iter_supplier_source_contracts():
        if item.support_status != SupplierContractSupportStatus.SUPPORTED:
            continue
        declaration = item.declaration
        suppliers.setdefault(str(declaration.supplier.supplier_id), []).append({
            "contract_id": declaration.contract_id,
            "contract_version": declaration.contract_version,
            "format_name": declaration.format_name,
            "document_type": declaration.document_type.value,
        })
    return {"suppliers": suppliers}


@router.post(
    "/ingestions",
    response_model=CatalogueSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_catalogue_ingestion(
    request: Request,
    file: UploadFile = File(...),
    supplier_id: int = Form(..., gt=0),
    contract_id: str | None = Form(None),
    contract_version: str | None = Form(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_onboard")),
):
    service = CatalogueSubmissionService(db)
    try:
        result = service.submit(
            CatalogueSubmissionCommand(
                supplier_id=supplier_id,
                original_filename=file.filename or "",
                content_type=file.content_type,
                stream=file.file,
                contract_id=contract_id,
                contract_version=contract_version,
                idempotency_key=idempotency_key,
                submitted_by=getattr(user, "username", None) or str(getattr(user, "id", "")),
            )
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    # The submission service has already committed the durable source, import
    # and queued run. From here on, audit logging is post-commit observability:
    # a failure here must NOT convert an accepted submission into a 500 (a
    # keyless client retry would create a second run). It stays operationally
    # visible through the application logger instead.
    try:
        audit_log.record(
            db,
            action="catalogue.ingestion_submit",
            actor=user,
            entity_type="ingestion_run",
            entity_id=str(result.ingestion_run_id),
            entity_label=file.filename,
            details={
                "supplier_id": result.supplier_id,
                "contract_id": result.contract_id,
                "contract_version": result.contract_version,
                "status": result.status,
            },
            request=request,
            commit=True,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "catalogue ingestion %s was durably submitted but audit logging failed",
            result.ingestion_run_id,
        )
    return _submission_response(result)

@router.get(
    "/ingestions/run_ids",
    response_model=list[CatalogueIngestionStatusResponse],
)
def get_catalogue_ingestions_run_ids(
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
):
    service = CatalogueSubmissionService(db)
    runs = service.list()
    counts = _product_row_counts(db, [str(run.ingestion_run_id) for run in runs])
    return [_status_response(run, product_rows=counts.get(str(run.ingestion_run_id))) for run in runs]


@router.get(
    "/ingestions/{run_uuid}",
    response_model=CatalogueIngestionStatusResponse,
)
def get_catalogue_ingestion_status(
    run_uuid: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
):
    service = CatalogueSubmissionService(db)
    try:
        result = service.get_status(run_uuid)
    except Exception as exc:
        raise _http_error(exc) from exc
    counts = _product_row_counts(db, [str(run_uuid)])
    return _status_response(result, product_rows=counts.get(str(run_uuid)))


@router.post(
    "/ingestions/{run_uuid}/retry",
    response_model=CatalogueSubmissionResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def retry_catalogue_ingestion(
    run_uuid: UUID,
    request: Request,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_onboard")),
):
    """Retry a failed run: re-submit its stored file as a new run with lineage."""
    service = CatalogueSubmissionService(db)
    try:
        result = service.retry(run_uuid, submitted_by=getattr(user, "username", None) or str(getattr(user, "id", "")))
    except Exception as exc:
        raise _http_error(exc) from exc
    try:
        audit_log.record(
            db,
            action="catalogue.ingestion_retry",
            actor=user,
            entity_type="ingestion_run",
            entity_id=str(result.ingestion_run_id),
            entity_label=result.contract_id,
            details={"retry_of": str(run_uuid), "supplier_id": result.supplier_id, "status": result.status},
            request=request,
            commit=True,
        )
    except Exception:
        db.rollback()
        logger.exception("catalogue retry %s was durably submitted but audit logging failed", result.ingestion_run_id)
    return _submission_response(result)


@router.post(
    "/ingestions/{run_uuid}/validation-issues/{validation_issue_id}/resolve",
    response_model=PipelineActionResponse,
)
def resolve_catalogue_validation_issue(
    run_uuid: UUID,
    validation_issue_id: UUID,
    body: ValidationIssueResolutionRequest,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_onboard")),
):
    _load_run_or_404(db, run_uuid)
    _load_run_validation_issue_or_404(db, run_uuid, validation_issue_id)
    actor_id = _actor_id(user)
    try:
        result = stages.CatalogueValidationService(db, commit=False).resolve_issue(
            stages.ResolveValidationIssueCommand(
                validation_issue_id=validation_issue_id,
                resolver_id=actor_id,
                resolution_status=body.resolution_status,
                resolution_note=body.resolution_note,
                resolved_at=body.resolved_at,
                idempotency_key=idempotency_key,
            )
        )
        _audit_pipeline_action(
            db,
            request=request,
            user=user,
            action="catalogue.pipeline_validation_resolve",
            entity_type="catalogue_validation_issue",
            entity_id=validation_issue_id,
            result=result,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise _stage_http_error(exc) from exc
    return _action_response(result)


@router.post(
    "/ingestions/{run_uuid}/mastering-candidates/{mastering_candidate_id}/review",
    response_model=PipelineActionResponse,
)
def review_catalogue_mastering_candidate(
    run_uuid: UUID,
    mastering_candidate_id: UUID,
    body: MasteringReviewRequest,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_onboard")),
):
    _load_run_or_404(db, run_uuid)
    _load_run_candidate_or_404(db, run_uuid, mastering_candidate_id)
    try:
        result = stages.ReviewDecisionService(db, commit=False).record_decision(
            stages.RecordReviewDecisionCommand(
                mastering_candidate_id=mastering_candidate_id,
                actor_id=_actor_id(user),
                review_status=body.review_status,
                reason=body.reason,
                override_reason=body.override_reason,
                expected_candidate_created_at=body.expected_candidate_created_at,
                decided_at=body.decided_at,
                idempotency_key=idempotency_key,
            )
        )
        _audit_pipeline_action(
            db,
            request=request,
            user=user,
            action="catalogue.pipeline_candidate_review",
            entity_type="catalogue_mastering_candidate",
            entity_id=mastering_candidate_id,
            result=result,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise _stage_http_error(exc) from exc
    return _action_response(result)


@router.get("/ingestions/{run_uuid}/mastering-candidates/{mastering_candidate_id}")
def get_catalogue_mastering_candidate(
    run_uuid: UUID,
    mastering_candidate_id: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """One candidate in full, with its verbatim source evidence and its
    append-only review-decision history."""
    _load_run_or_404(db, run_uuid)
    row = _load_run_candidate_or_404(db, run_uuid, mastering_candidate_id)
    decisions = (
        db.query(models.CatalogueReviewDecision)
        .filter_by(mastering_candidate_uuid=str(mastering_candidate_id))
        .order_by(models.CatalogueReviewDecision.id)
        .all()
    )
    return {
        "ingestion_run_id": str(run_uuid),
        "candidate": {
            **persistence.mastering_candidate_to_contract(row).model_dump(mode="json"),
            "superseded_by": row.superseded_by_uuid,
        },
        "evidence": review_summary.candidate_evidence(db, row),
        "decisions": [
            {
                "review_decision_id": decision.review_decision_uuid,
                "decision_type": decision.decision_type,
                "review_status": decision.review_status,
                "actor_id": decision.actor_id,
                "decided_at": decision.decided_at,
                "reason": decision.reason,
                "override_reason": decision.override_reason,
            }
            for decision in decisions
        ],
    }


@router.post(
    "/ingestions/{run_uuid}/mastering-candidates/{mastering_candidate_id}/correct",
    response_model=PipelineActionResponse,
)
def correct_catalogue_mastering_candidate(
    run_uuid: UUID,
    mastering_candidate_id: UUID,
    body: MasteringCorrectionRequest,
    request: Request,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_onboard")),
):
    """Supersede a pending candidate with a human-corrected revision."""
    _load_run_or_404(db, run_uuid)
    _load_run_candidate_or_404(db, run_uuid, mastering_candidate_id)
    # Correcting a match is ordinary review work. Drafting a *new* canonical
    # product writes name, category and status — the three fields
    # `product_sensitive` already governs everywhere else — so it is held to
    # the same bar rather than riding in on catalogue_onboard.
    variant_resolution = body.product_variant_resolution or {}
    if str(variant_resolution.get("state") or "") == "CONFIRMED_CREATE" and not variant_resolution.get("canonical_sku"):
        if not has_capability(getattr(user, "role", None), "product_sensitive"):
            raise HTTPException(
                status_code=403,
                detail={
                    "error_code": "CREATE_PRODUCT_FORBIDDEN",
                    "message": "Creating a canonical product needs the product_sensitive capability. "
                               "You can still match this row to an existing product, or reject it.",
                },
            )
    try:
        result = stages.MasteringService(db, commit=False).revise_candidate(
            stages.ReviseMasteringCandidateCommand(
                mastering_candidate_id=mastering_candidate_id,
                actor_id=_actor_id(user),
                reason=body.reason,
                expected_candidate_created_at=body.expected_candidate_created_at,
                revised_at=body.revised_at,
                supplier_product_resolution=body.supplier_product_resolution,
                product_variant_resolution=body.product_variant_resolution,
                packaging_resolution=body.packaging_resolution,
                supplier_price_resolution=body.supplier_price_resolution,
                mbb_resolution=body.mbb_resolution,
                product_family_resolution=body.product_family_resolution,
                brand_resolution=body.brand_resolution,
                category_resolution=body.category_resolution,
            )
        )
        _audit_pipeline_action(
            db,
            request=request,
            user=user,
            action="catalogue.pipeline_candidate_correct",
            entity_type="catalogue_mastering_candidate",
            entity_id=mastering_candidate_id,
            result=result,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise _stage_http_error(exc) from exc
    return _action_response(result)


@router.post("/ingestions/{run_uuid}/evidence/{raw_observation_uuid}/correct")
def correct_catalogue_evidence(
    run_uuid: UUID,
    raw_observation_uuid: UUID,
    body: EvidenceCorrectionRequest,
    request: Request,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """Correct misread cells on one observation, with a durable audit stamp.

    Nothing re-runs here: re-parse the run (or retrigger its dead-lettered
    rows) afterwards and the pipeline re-reads the corrected evidence. On a
    re-parse child the correction also lands on the extraction-source run's
    copy, so re-parsing again cannot resurrect the misread.
    """
    try:
        result = evidence_corrections.correct_evidence(
            db,
            run_uuid=run_uuid,
            raw_observation_uuid=raw_observation_uuid,
            cells=body.cells,
            reason=body.reason,
            corrected_by=getattr(user, "username", None) or str(getattr(user, "id", "")),
        )
    except evidence_corrections.EvidenceNotFound as exc:
        raise HTTPException(status_code=404, detail=_detail("EVIDENCE_NOT_FOUND", str(exc))) from exc
    except evidence_corrections.EvidenceCorrectionError as exc:
        raise HTTPException(status_code=422, detail=_detail("EVIDENCE_CORRECTION_REFUSED", str(exc))) from exc
    try:
        audit_log.record(
            db,
            action="catalogue.evidence_correct",
            actor=user,
            entity_type="catalogue_extracted_evidence",
            entity_id=result.raw_observation_id,
            entity_label=f"run {run_uuid}",
            details={
                "ingestion_run_id": str(run_uuid),
                "corrected_columns": list(result.corrected_columns),
                "reason": body.reason,
                "source_run_id": result.source_run_id,
                "source_raw_observation_id": result.source_raw_observation_id,
            },
            request=request,
            commit=True,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "evidence correction %s was saved but audit logging failed", result.raw_observation_id
        )
    return {
        "ingestion_run_id": str(run_uuid),
        "raw_observation_id": result.raw_observation_id,
        "corrected_columns": list(result.corrected_columns),
        "source_run_id": result.source_run_id,
        "source_raw_observation_id": result.source_raw_observation_id,
        "next_step": "re-parse the run (or retrigger its dead-lettered rows) to re-read the corrected evidence",
    }


@router.post(
    "/ingestions/{run_uuid}/mastering-candidates/{mastering_candidate_id}/apply",
    response_model=PipelineActionResponse,
)
def apply_catalogue_mastering_candidate(
    run_uuid: UUID,
    mastering_candidate_id: UUID,
    body: CommercialApplicationRequest,
    request: Request,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_publish")),
):
    _load_run_or_404(db, run_uuid)
    _load_run_candidate_or_404(db, run_uuid, mastering_candidate_id)
    try:
        result = stages.ApprovedCommercialStateService(db, commit=False).apply_approved_candidate(
            stages.ApplyApprovedCandidateCommand(
                mastering_candidate_id=mastering_candidate_id,
                applied_at=body.applied_at,
            )
        )
        _audit_pipeline_action(
            db,
            request=request,
            user=user,
            action="catalogue.pipeline_candidate_apply",
            entity_type="catalogue_mastering_candidate",
            entity_id=mastering_candidate_id,
            result=result,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise _stage_http_error(exc) from exc
    return _action_response(result)


@router.post(
    "/ingestions/{run_uuid}/mastering-candidates/{mastering_candidate_id}/publish",
    response_model=PipelineActionResponse,
)
def publish_catalogue_serving_item(
    run_uuid: UUID,
    mastering_candidate_id: UUID,
    body: ServingPublicationRequest,
    request: Request,
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_publish")),
):
    _load_run_or_404(db, run_uuid)
    _load_run_candidate_or_404(db, run_uuid, mastering_candidate_id)
    try:
        result = stages.ServingPublicationService(db, commit=False).publish(
            stages.PublishServingItemCommand(
                mastering_candidate_id=mastering_candidate_id,
                publication_version=body.publication_version,
                published_at=body.published_at,
                idempotency_key=idempotency_key,
            )
        )
        _audit_pipeline_action(
            db,
            request=request,
            user=user,
            action="catalogue.pipeline_serving_publish",
            entity_type="catalogue_serving_publication",
            entity_id=result.output_ids[0] if result.output_ids else mastering_candidate_id,
            result=result,
        )
        db.commit()
    except Exception as exc:
        db.rollback()
        raise _stage_http_error(exc) from exc
    return _action_response(result)


# ── Per-layer read API ───────────────────────────────────────────────────────
# Read-only views over the durable records each pipeline layer produced for one
# run, named by the RAW -> STAGING -> INTERMEDIATE -> SERVING timeline. Records
# are reconstructed from the persistence contracts.


@router.get("/ingestions/{run_uuid}/raw")
def get_raw_layer(
    run_uuid: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """RAW layer (steps 1-2): the preserved original's file facts and the
    append-only raw-stage verification history. No file content, no meaning."""
    run = _load_run_or_404(db, run_uuid)
    source = run.pipeline_source_document
    if source is None and run.catalogue_source_document_id:
        source = db.get(models.CatalogueSourceDocument, run.catalogue_source_document_id)
    attempts = (
        db.query(models.CatalogueRawStageAttempt)
        .filter_by(ingestion_run_uuid=str(run_uuid))
        .order_by(models.CatalogueRawStageAttempt.id)
        .all()
    )
    return {
        "ingestion_run_id": str(run_uuid),
        "layer": "raw",
        "source": _source_summary(source) if source else None,
        "attempts": [_attempt_summary(a) for a in attempts],
    }


@router.get("/ingestions/{run_uuid}/evidence/{raw_observation_uuid}")
def get_catalogue_evidence(
    run_uuid: UUID,
    raw_observation_uuid: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """One observation's verbatim evidence — cells plus the page-level marks.

    The same shape the candidate detail's evidence entries use, so the held-
    rows lane and the review room render (and correct) evidence identically.
    """
    import json as _json

    _load_run_or_404(db, run_uuid)
    row = (
        db.query(models.CatalogueExtractedEvidence)
        .filter_by(ingestion_run_uuid=str(run_uuid), raw_observation_uuid=str(raw_observation_uuid))
        .first()
    )
    if row is None:
        raise HTTPException(
            status_code=404,
            detail=_detail("EVIDENCE_NOT_FOUND", f"Observation {raw_observation_uuid} is not part of run {run_uuid}"),
        )
    try:
        metadata = _json.loads(row.source_metadata_json or "{}") or {}
    except ValueError:
        metadata = {}
    try:
        cells = _json.loads(row.raw_cells_json or "[]")
    except ValueError:
        cells = []
    return {
        "raw_observation_id": row.raw_observation_uuid,
        "page": row.page_number,
        "raw_text": row.raw_text,
        "cells": [{"column_name": cell.get("column_name"), "value": cell.get("raw_value")} for cell in cells],
        "page_brand_text": metadata.get("page_brand_text"),
        "supplier_identity_text": metadata.get("supplier_identity_text"),
        "page_promotion_text": metadata.get("page_promotion_text"),
    }


@router.get("/ingestions/{run_uuid}/dead-letters")
def get_dead_letters(
    run_uuid: UUID,
    issue_code: str | None = Query(None, description="Only rows this code is holding."),
    stage: str | None = Query(None, description="Only rows blocked at this stage."),
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """Rows the machine could not interpret, and what one fix would clear.

    `lanes` accounts for every normalized row of the run, so a row is here
    because nothing else claimed it — not published, not awaiting review, not
    rejected by a person. `reconciliation` carries the RAW counts beside the
    lanes so a shortfall cannot be mistaken for a silent drop.

    After a retrigger the two views deliberately differ: `lanes` is this run's
    HISTORY and never follows the chain, while `count`, `dead_letters` and
    `by_issue_code` follow retriggers — rows a later run cleared are gone, and
    survivors carry `attempts`. lanes.dead_lettered > count is a run that has
    been partially rescued, not an inconsistency.

    `rows_cleared_if_fixed` is the number worth acting on: rows a code holds
    ALONE. A row held by two codes is freed by neither on its own, so the
    larger `rows_blocked` overstates what a single fix buys.
    """
    _load_run_or_404(db, run_uuid)
    run = str(run_uuid)
    entries = dead_letters.dead_letters(db, run_uuid=run, issue_code=issue_code, stage=stage)
    reconciliation = dead_letters.reconcile(db, run)
    names = _row_names(db, {entry.catalogue_item_uuid for entry in entries})
    return {
        "ingestion_run_id": run,
        "lanes": reconciliation.lane_counts,
        "reconciliation": {
            "raw_observations": reconciliation.raw_observations,
            "raw_text_observations": reconciliation.raw_text_observations,
            "raw_product_rows": reconciliation.raw_product_rows,
            "normalized_rows": reconciliation.normalized_rows,
            # Product rows carrying no link to a normalized row. Usually the
            # same product listed at several order quantities and collapsed.
            "unlinked_product_rows": reconciliation.unlinked_product_rows,
            "lanes_cover_normalized_rows": reconciliation.lanes_cover_normalized_rows,
        },
        "by_issue_code": [
            {
                "issue_code": tally.issue_code,
                "rows_blocked": tally.rows_blocked,
                "rows_cleared_if_fixed": tally.rows_cleared_if_fixed,
            }
            for tally in dead_letters.tallies_by_issue_code(db, run_uuid=run)
        ],
        "count": len(entries),
        "dead_letters": [
            {
                "catalogue_item_id": entry.catalogue_item_uuid,
                "supplier_id": entry.supplier_id,
                "supplier_sku": entry.supplier_sku,
                "product_name": names.get(entry.catalogue_item_uuid),
                "stage": entry.stage,
                "issue_codes": list(entry.issue_codes),
                # Runs that have tried this row: 1 + one per completed
                # retrigger that selected it and still could not read it.
                "attempts": entry.attempts,
                "field_path": entry.field_path,
                "review_guidance": entry.review_guidance,
                "first_seen_at": entry.first_seen_at,
                "age_days": entry.age_days,
                # The observation behind the row — the handle the evidence
                # panel corrects and a retrigger re-drives.
                "raw_observation_id": entry.observation_uuids[0] if entry.observation_uuids else None,
                "observation_ids": list(entry.observation_uuids),
            }
            for entry in entries
        ],
    }


def _row_names(db: Session, item_uuids: set[str]) -> dict[str, str]:
    """The printed product description per held row, so a person can recognise
    it without decoding SKUs."""
    if not item_uuids:
        return {}
    import json as _json

    out: dict[str, str] = {}
    rows = (
        db.query(
            models.CatalogueNormalizedRow.catalogue_item_uuid,
            models.CatalogueNormalizedRow.raw_fields_json,
        )
        .filter(models.CatalogueNormalizedRow.catalogue_item_uuid.in_(item_uuids))
        .all()
    )
    for item_uuid, raw_json in rows:
        try:
            raw = _json.loads(raw_json or "{}")
        except ValueError:
            continue
        name = raw.get("product_name") or raw.get("original_product_name")
        if name:
            out[item_uuid] = str(name)
    return out


@router.get("/ingestions/{run_uuid}/staging")
def get_staging_layer(
    run_uuid: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """STAGING layer (steps 3-4): verbatim, source-located extracted evidence.

    Applying the supplier contract changes evidence into an interpreted
    proposal, so normalized rows belong to Intermediate rather than Staging.
    """
    _load_run_or_404(db, run_uuid)
    run = str(run_uuid)
    extraction_attempts = (
        db.query(models.CatalogueExtractionAttempt)
        .filter_by(ingestion_run_uuid=run)
        .order_by(models.CatalogueExtractionAttempt.id)
        .all()
    )
    evidence = [
        persistence.extracted_evidence_to_contract(r).model_dump(mode="json")
        for r in db.query(models.CatalogueExtractedEvidence)
        .filter_by(ingestion_run_uuid=run)
        .order_by(models.CatalogueExtractedEvidence.id)
        .all()
    ]
    return {
        "ingestion_run_id": run,
        "layer": "staging",
        "extraction_attempts": [
            _extraction_attempt_summary(attempt)
            for attempt in extraction_attempts
        ],
        "evidence_count": len(evidence),
        "evidence": evidence,
    }


def _extraction_attempt_summary(
    attempt: models.CatalogueExtractionAttempt,
) -> dict[str, Any]:
    import json as _json

    return {
        "attempt_id": attempt.attempt_uuid,
        "status": attempt.status,
        "source_format": attempt.source_format,
        "provider": attempt.provider,
        "model": attempt.model_name,
        "started_at": attempt.started_at,
        "completed_at": attempt.completed_at,
        "units_attempted": attempt.units_attempted,
        "units_completed": attempt.units_completed,
        "empty_units": attempt.empty_units,
        "observation_count": attempt.observation_count,
        "unit_outcomes": _json.loads(attempt.unit_outcomes_json or "[]"),
        "warnings": _json.loads(attempt.warnings_json or "[]"),
        "errors": _json.loads(attempt.errors_json or "[]"),
    }


@router.get("/ingestions/{run_uuid}/intermediate")
def get_intermediate_layer(
    run_uuid: UUID,
    view: str | None = Query(None, description="'summary' returns the compact reviewer view instead of full contracts."),
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """INTERMEDIATE layer (steps 5-9): contract-conformed normalized claims,
    validation issues and mastering candidates awaiting human review.

    ``?view=summary`` returns one decision-ready row per candidate (states,
    issue counts, price delta vs the current offering, family evidence,
    channel selling price) instead of full contracts — the review UI's first
    paint. Full contracts remain the default and load per candidate on open.
    """
    _load_run_or_404(db, run_uuid)
    if view == "summary":
        return review_summary.run_review_summary(db, run_uuid)
    run_filter = {"ingestion_run_uuid": str(run_uuid)}
    normalized_rows = [
        persistence.normalized_row_to_contract(r).model_dump(mode="json")
        for r in db.query(models.CatalogueNormalizedRow)
        .filter_by(**run_filter)
        .order_by(models.CatalogueNormalizedRow.id)
        .all()
    ]
    issues = [
        persistence.validation_issue_to_contract(r).model_dump(mode="json")
        for r in db.query(models.CatalogueValidationIssue).filter_by(**run_filter).order_by(models.CatalogueValidationIssue.id).all()
    ]
    candidates = [
        {
            **persistence.mastering_candidate_to_contract(r).model_dump(mode="json"),
            "superseded_by": r.superseded_by_uuid,
        }
        for r in db.query(models.CatalogueMasteringCandidate).filter_by(**run_filter).order_by(models.CatalogueMasteringCandidate.id).all()
    ]
    return {
        "ingestion_run_id": str(run_uuid),
        "layer": "intermediate",
        "normalized_rows": normalized_rows,
        "validation_issues": issues,
        "mastering_candidates": candidates,
    }


@router.get("/ingestions/{run_uuid}/variant-search")
def search_catalogue_product_variants(
    run_uuid: UUID,
    q: str = Query(..., min_length=2, description="Matches sku_code, name, or brand (case-insensitive)."),
    limit: int = Query(8, ge=1, le=25),
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """Product-variant picker for candidate corrections, scoped to the run's
    supplier so each result carries its cost/margin sanity context."""
    _load_run_or_404(db, run_uuid)
    return review_summary.search_product_variants(db, run_uuid, q, limit)



class ReparseRequest(BaseModel):
    """Where to pick the flow back up. Only conformance today — see ReparseStage."""

    from_stage: str = "conformance"


@router.post("/ingestions/{run_uuid}/reparse", response_model=CatalogueSubmissionResponse, status_code=202)
def reparse_catalogue_ingestion(
    run_uuid: UUID,
    request: Request,
    body: ReparseRequest | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_onboard")),
):
    """Re-run the interpretation over evidence this run already holds.

    The supplier contract is consumed at conformance, which reaches no model
    provider — so a mapping change needs the stored observations re-read, not
    the pages re-scanned. Costs nothing at the provider and takes about a
    second where a retry takes a minute and a half.

    Creates a NEW run linked by parent_run_id; the source run's decisions are
    append-only and are left alone.
    """
    stage = (body.from_stage if body else "conformance")
    service = CatalogueSubmissionService(db)
    try:
        result = service.reparse(
            run_uuid,
            from_stage=stage,
            submitted_by=getattr(user, "username", None) or str(getattr(user, "id", "")),
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    try:
        audit_log.record(
            db,
            action="catalogue.ingestion_reparse",
            actor=user,
            entity_type="ingestion_run",
            entity_id=str(result.ingestion_run_id),
            entity_label=result.contract_id,
            details={"reparse_of": str(run_uuid), "from_stage": stage, "supplier_id": result.supplier_id},
            request=request,
            commit=True,
        )
    except Exception:
        db.rollback()
        logger.exception("catalogue re-parse %s was queued but audit logging failed", result.ingestion_run_id)
    return _submission_response(result)


class RetriggerRequest(BaseModel):
    """What to re-drive: one issue code, explicit rows, or the whole queue."""

    issue_code: str | None = None
    catalogue_item_ids: list[UUID] | None = None
    from_stage: str = "conformance"


@router.post("/ingestions/{run_uuid}/retrigger", status_code=202)
def retrigger_catalogue_ingestion(
    run_uuid: UUID,
    request: Request,
    body: RetriggerRequest | None = None,
    db: Session = Depends(database.get_db),
    user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """Re-drive exactly the rows this run's dead-letter queue is holding.

    A re-parse limited to the observations behind the selected rows — stored
    evidence only, no vision call, no spend. Selection comes from the followed
    queue, so rows an earlier retrigger cleared and rows a person decided on
    are not selectable; an explicit id that is not in the queue is refused by
    name with the reason.
    """
    payload = body or RetriggerRequest()
    service = CatalogueSubmissionService(db)
    try:
        result = service.retrigger(
            run_uuid,
            issue_code=payload.issue_code,
            catalogue_item_ids=payload.catalogue_item_ids,
            from_stage=payload.from_stage,
            submitted_by=getattr(user, "username", None) or str(getattr(user, "id", "")),
        )
    except Exception as exc:
        raise _http_error(exc) from exc
    try:
        audit_log.record(
            db,
            action="catalogue.ingestion_retrigger",
            actor=user,
            entity_type="ingestion_run",
            entity_id=str(result.submission.ingestion_run_id),
            entity_label=result.submission.contract_id,
            details={
                "retrigger_of": str(run_uuid),
                "issue_code": payload.issue_code,
                "rows_selected": result.rows_selected,
                "attempt": result.attempt,
            },
            request=request,
            commit=True,
        )
    except Exception:
        db.rollback()
        logger.exception(
            "catalogue retrigger %s was queued but audit logging failed",
            result.submission.ingestion_run_id,
        )
    return {
        "ingestion_run_id": str(result.submission.ingestion_run_id),
        "retrigger_of": str(run_uuid),
        "rows_selected": result.rows_selected,
        "observations": result.observation_count,
        "attempt": result.attempt,
        "issue_codes": list(result.issue_codes),
        "status": result.submission.status,
        "status_url": result.submission.status_url,
    }


# What the browser should do with each source format. A price list is read, not
# downloaded, so anything a browser renders opens inline; the rest downloads.
_INLINE_MEDIA = {
    "PDF": "application/pdf",
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "CSV": "text/csv",
    "TEXT": "text/plain",
}
_DOWNLOAD_MEDIA = {
    "SPREADSHEET": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


@router.get("/ingestions/{run_uuid}/source")
def get_catalogue_source_file(
    run_uuid: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
):
    """The supplier catalogue this run read, as the file itself.

    Goes through the same verified loader the pipeline uses, so a file that has
    been moved, truncated or altered since the scan fails here rather than
    being served as if it were the document the prices came from.

    A re-parse never opened a file, so it serves its source run's document —
    which is the same document, and the one its evidence came from.
    """
    target = run_uuid
    if catalogue_reparse.is_reparse(db, run_uuid):
        run = db.query(models.IngestionRun).filter_by(run_uuid=str(run_uuid)).first()
        origin = catalogue_reparse.evidence_source_run(db, run) if run else None
        if origin is not None:
            target = UUID(origin.run_uuid)
    try:
        asset = load_and_verify_source_asset(db, ingestion_run_id=target)
    except RunNotFound as exc:
        raise HTTPException(404, _detail("INGESTION_RUN_NOT_FOUND", str(exc))) from exc
    except SourceVerificationError as exc:
        # Says which of the checks failed — "missing", "checksum does not match" —
        # because "the file changed since we scanned it" is the answer a reviewer needs.
        raise HTTPException(410, _detail("SOURCE_FILE_UNAVAILABLE", str(exc))) from exc

    fmt = (asset.source_format or "").upper()
    media = _INLINE_MEDIA.get(fmt) or _DOWNLOAD_MEDIA.get(fmt) or "application/octet-stream"
    disposition = "inline" if fmt in _INLINE_MEDIA else "attachment"
    safe_name = asset.original_filename.replace('"', "")
    return Response(
        content=asset.content,
        media_type=media,
        headers={"Content-Disposition": f'{disposition}; filename="{safe_name}"'},
    )


@router.get("/ingestions/{run_uuid}/receipt/golden.csv")
def export_published_golden_csv(
    run_uuid: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
):
    """This run's published items in the golden-sample sheet's exact columns.

    For regression testing: the sheet is 122 hand-filled SKUs that say what the
    packaging, price basis, sellable unit and bulk terms really are. Exporting
    our published output in the same 20 columns, in the same order, makes the
    two directly diffable.
    """
    _load_run_or_404(db, run_uuid)
    body = catalogue_golden_export.golden_csv(db, run_uuid)
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="rosetta-published-{run_uuid}.csv"'},
    )


@router.get("/ingestions/{run_uuid}/receipt/golden")
def export_published_golden_rows(
    run_uuid: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """The same rows as JSON, for a test harness that would rather not parse CSV."""
    _load_run_or_404(db, run_uuid)
    return {
        "ingestion_run_id": str(run_uuid),
        "columns": list(catalogue_golden_export.GOLDEN_COLUMNS),
        "rows": catalogue_golden_export.golden_rows(db, run_uuid),
    }


@router.get("/ingestions/{run_uuid}/duplicate-check")
def check_for_duplicate_product(
    run_uuid: UUID,
    name: str = Query("", description="The draft product name being typed."),
    barcode: str | None = Query(None, description="Barcode from the supplier row, when it has one."),
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """What stands between this create draft and a new SKU.

    Blockers are facts (a barcode or an identical name already owned by a
    product) and cannot be overridden. `similar` is judgement — above
    `threshold` the reviewer must say what makes their row different, and that
    reason is stored on the decision.
    """
    _load_run_or_404(db, run_uuid)
    return variant_similarity.duplicate_check(db, name=name, barcode=barcode)


@router.get("/ingestions/{run_uuid}/receipt")
def get_run_receipt(
    run_uuid: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
):
    """Publish receipt — the offering price rows this run wrote (old → new per SKU)."""
    return review_summary.run_receipt(db, run_uuid)

@router.get("/ingestions/{run_uuid}/serving")
def get_serving_layer(
    run_uuid: UUID,
    db: Session = Depends(database.get_db),
    _user: models.User = Depends(require_capability("catalogue_onboard")),
) -> dict[str, Any]:
    """SERVING layer: immutable approved publication snapshots for this run.

    History remains visible for audit; ``current_publications`` is the
    consumer-safe view and contains only non-superseded snapshots.
    """
    _load_run_or_404(db, run_uuid)
    candidate_ids = [
        value
        for (value,) in db.query(models.CatalogueMasteringCandidate.mastering_candidate_uuid)
        .filter_by(ingestion_run_uuid=str(run_uuid))
        .all()
    ]
    rows = []
    if candidate_ids:
        rows = (
            db.query(models.CatalogueServingPublication)
            .filter(models.CatalogueServingPublication.mastering_candidate_uuid.in_(candidate_ids))
            .order_by(models.CatalogueServingPublication.id)
            .all()
        )
    publications = [
        {
            "is_current": bool(row.is_current),
            "superseded_at": row.superseded_at,
            "snapshot": persistence.serving_item_to_contract(row).model_dump(mode="json"),
        }
        for row in rows
    ]
    return {
        "ingestion_run_id": str(run_uuid),
        "layer": "serving",
        "publication_count": len(publications),
        "current_publications": [item["snapshot"] for item in publications if item["is_current"]],
        "publication_history": publications,
    }


def _load_run_or_404(db: Session, run_uuid: UUID) -> models.IngestionRun:
    run = db.query(models.IngestionRun).filter_by(run_uuid=str(run_uuid)).first()
    if run is None:
        raise HTTPException(status_code=404, detail=_detail("INGESTION_RUN_NOT_FOUND", f"Ingestion run {run_uuid} was not found"))
    return run


def _load_run_candidate_or_404(
    db: Session,
    run_uuid: UUID,
    mastering_candidate_id: UUID,
) -> models.CatalogueMasteringCandidate:
    candidate = db.query(models.CatalogueMasteringCandidate).filter_by(
        mastering_candidate_uuid=str(mastering_candidate_id),
        ingestion_run_uuid=str(run_uuid),
    ).first()
    if candidate is None:
        raise HTTPException(
            status_code=404,
            detail=_detail(
                "MASTERING_CANDIDATE_NOT_FOUND",
                f"Mastering candidate {mastering_candidate_id} was not found in ingestion run {run_uuid}",
            ),
        )
    return candidate


def _load_run_validation_issue_or_404(
    db: Session,
    run_uuid: UUID,
    validation_issue_id: UUID,
) -> models.CatalogueValidationIssue:
    issue = db.query(models.CatalogueValidationIssue).filter_by(
        validation_issue_uuid=str(validation_issue_id),
        ingestion_run_uuid=str(run_uuid),
    ).first()
    if issue is None:
        raise HTTPException(
            status_code=404,
            detail=_detail(
                "VALIDATION_ISSUE_NOT_FOUND",
                f"Validation issue {validation_issue_id} was not found in ingestion run {run_uuid}",
            ),
        )
    return issue


def _actor_id(user: models.User) -> str:
    return getattr(user, "username", None) or str(getattr(user, "id", ""))


def _audit_pipeline_action(
    db: Session,
    *,
    request: Request,
    user: models.User,
    action: str,
    entity_type: str,
    entity_id: UUID | str,
    result: stages.StageResult,
) -> None:
    audit_log.record(
        db,
        action=action,
        actor=user,
        entity_type=entity_type,
        entity_id=entity_id,
        details={
            "stage": result.stage,
            "status": result.status,
            "output_ids": [str(item) for item in result.output_ids],
            "metrics": vars(result.metrics),
        },
        request=request,
    )


def _action_response(result: stages.StageResult) -> PipelineActionResponse:
    return PipelineActionResponse(
        stage=result.stage,
        status=result.status,
        output_ids=[str(item) for item in result.output_ids],
        metrics=vars(result.metrics),
    )


def _source_summary(source: models.CatalogueSourceDocument) -> dict[str, Any]:
    import json as _json

    try:
        metadata = _json.loads(source.source_metadata_json or "{}")
    except (TypeError, ValueError):
        metadata = {}
    return {
        "supplier_catalogue_id": source.supplier_catalogue_uuid,
        "source_file_id": source.source_file_uuid,
        "original_filename": metadata.get("original_filename") or source.filename,
        "content_type": metadata.get("content_type"),
        "byte_size": source.byte_size,
        "page_count": source.page_count,
        "checksum_sha256": source.source_checksum,
        "source_format": source.source_format,
        "supplier_source_contract_id": source.supplier_source_contract_id,
        "supplier_source_contract_version": source.supplier_source_contract_version,
        "document_type": source.document_type,
        "raw_stage_status": source.raw_stage_status,
        "raw_stage_completed_at": source.raw_stage_completed_at,
        "received_at": source.received_at,
    }


def _attempt_summary(attempt: models.CatalogueRawStageAttempt) -> dict[str, Any]:
    return {
        "attempt_id": attempt.attempt_uuid,
        "status": attempt.status,
        "attempted_at": attempt.attempted_at,
        "completed_at": attempt.completed_at,
        "checksum_sha256": attempt.checksum_sha256,
        "byte_size": attempt.byte_size,
        "source_format": attempt.source_format,
        "page_count": attempt.page_count,
        "failure_code": attempt.failure_code,
        "failure_message": attempt.failure_message,
    }


def _submission_response(result: CatalogueSubmissionResult) -> CatalogueSubmissionResponse:
    return CatalogueSubmissionResponse(**result.__dict__)


def _product_row_counts(db: Session, run_uuids: list[str]) -> dict[str, int]:
    """Catalogue product rows per run — normalized rows, the figure a business
    user means by 'rows'. Computed live so historical runs read correctly."""
    if not run_uuids:
        return {}
    rows = (
        db.query(
            models.CatalogueNormalizedRow.ingestion_run_uuid,
            func.count(models.CatalogueNormalizedRow.id),
        )
        .filter(models.CatalogueNormalizedRow.ingestion_run_uuid.in_(run_uuids))
        .group_by(models.CatalogueNormalizedRow.ingestion_run_uuid)
        .all()
    )
    return {run_uuid: count for run_uuid, count in rows}


def _status_response(
    result: CatalogueIngestionStatus, *, product_rows: int | None = None
) -> CatalogueIngestionStatusResponse:
    return CatalogueIngestionStatusResponse(**result.__dict__, product_rows=product_rows)


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, UnknownSupplierError):
        return HTTPException(status_code=404, detail=_detail("UNKNOWN_SUPPLIER", str(exc)))
    if isinstance(exc, ContractParameterError):
        return HTTPException(status_code=422, detail=_detail("INVALID_CONTRACT_PARAMETERS", str(exc)))
    if isinstance(exc, SupplierContractAmbiguousError):
        return HTTPException(status_code=409, detail=_detail("AMBIGUOUS_SUPPLIER_CONTRACT", str(exc)))
    if isinstance(exc, SupplierContractMismatchError):
        return HTTPException(status_code=409, detail=_detail("SUPPLIER_CONTRACT_MISMATCH", str(exc)))
    if isinstance(exc, SupplierContractSelectionError):
        return HTTPException(status_code=422, detail=_detail("UNSUPPORTED_SUPPLIER_CONTRACT", str(exc)))
    if isinstance(exc, RetryNotAllowedError):
        return HTTPException(status_code=409, detail=_detail("RETRY_NOT_ALLOWED", str(exc)))
    if isinstance(exc, SourceFileMissingError):
        return HTTPException(status_code=410, detail=_detail("SOURCE_FILE_MISSING", str(exc)))
    if isinstance(exc, SubmissionIdempotencyConflict):
        return HTTPException(status_code=409, detail=_detail("IDEMPOTENCY_CONFLICT", str(exc)))
    if isinstance(exc, EmptyUploadError):
        return HTTPException(status_code=400, detail=_detail("EMPTY_UPLOAD", str(exc)))
    if isinstance(exc, UnsupportedSourceTypeError):
        return HTTPException(status_code=415, detail=_detail("UNSUPPORTED_SOURCE_TYPE", str(exc)))
    if isinstance(exc, UploadTooLargeError):
        return HTTPException(status_code=413, detail=_detail("UPLOAD_TOO_LARGE", str(exc)))
    if isinstance(exc, MalformedFilenameError):
        return HTTPException(status_code=400, detail=_detail("MALFORMED_FILENAME", str(exc)))
    if isinstance(exc, StorageUnavailableError):
        return HTTPException(status_code=503, detail=_detail("STORAGE_UNAVAILABLE", str(exc)))
    if isinstance(exc, SubmissionPersistenceError):
        return HTTPException(status_code=503, detail=_detail("SUBMISSION_PERSISTENCE_UNAVAILABLE", str(exc)))
    if isinstance(exc, SubmissionNotFoundError):
        return HTTPException(status_code=404, detail=_detail("INGESTION_RUN_NOT_FOUND", str(exc)))
    if isinstance(exc, CatalogueSubmissionError):
        return HTTPException(status_code=400, detail=_detail("CATALOGUE_SUBMISSION_ERROR", str(exc)))
    return HTTPException(status_code=500, detail=_detail("INTERNAL_ERROR", "Catalogue submission failed"))


def _stage_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, stages.UpstreamRecordNotFound):
        return HTTPException(status_code=404, detail=_detail("PIPELINE_RECORD_NOT_FOUND", str(exc)))
    if isinstance(
        exc,
        (
            stages.BlockingValidationIssues,
            stages.ConcurrentModification,
            stages.IdempotencyConflict,
            stages.InvalidStageTransition,
            stages.MissingOrIncompatibleLineage,
            stages.PublicationIneligible,
            stages.StaleCandidateRevision,
            stages.SupplierContractMismatch,
            stages.AmbiguousProductVariant,
            stages.AmbiguousSupplierOffer,
        ),
    ):
        return HTTPException(status_code=409, detail=_detail("PIPELINE_TRANSITION_CONFLICT", str(exc)))
    if isinstance(exc, stages.CatalogueStageError):
        return HTTPException(status_code=422, detail=_detail("PIPELINE_ACTION_INVALID", str(exc)))
    return HTTPException(status_code=500, detail=_detail("INTERNAL_ERROR", "Catalogue pipeline action failed"))


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}

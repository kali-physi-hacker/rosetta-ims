"""Queued catalogue ingestion boundary (evidence-first pipeline)."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

import database
import models
from permissions import require_capability
from services import audit_log
from services.catalogue_submission import (
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
    metrics: dict[str, Any] | None = None
    error_summary: dict[str, Any] | str | None = None


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
    return _submission_response(result)


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
    return _status_response(result)


def _submission_response(result: CatalogueSubmissionResult) -> CatalogueSubmissionResponse:
    return CatalogueSubmissionResponse(**result.__dict__)


def _status_response(result: CatalogueIngestionStatus) -> CatalogueIngestionStatusResponse:
    return CatalogueIngestionStatusResponse(**result.__dict__)


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


def _detail(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}

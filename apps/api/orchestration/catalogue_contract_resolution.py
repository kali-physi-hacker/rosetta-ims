"""Exact recorded supplier-contract resolution for catalogue orchestration."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

import v2.models as v2_models
from services import supplier_source_contract_runtime

from .catalogue_types import RecordedContractError, RecordedSupplierContract, RunNotFound


def resolve_recorded_supplier_contract(db: Session, *, ingestion_run_id: UUID):
    """Resolve the exact supplier-source contract recorded on the queued run."""

    run = db.query(v2_models.IngestionRun).filter_by(run_uuid=str(ingestion_run_id)).first()
    if run is None:
        raise RunNotFound(f"Ingestion run {ingestion_run_id} was not found")
    source = run.pipeline_source_document
    if source is None and run.catalogue_source_document_id:
        source = db.get(v2_models.CatalogueSourceDocument, run.catalogue_source_document_id)
    if source is None:
        raise RecordedContractError("Queued run has no canonical source document")
    if not run.supplier_id or not run.supplier_source_contract_id or not run.supplier_source_contract_version:
        raise RecordedContractError("Queued run is missing exact supplier-source contract identity")
    if not run.document_type:
        raise RecordedContractError("Queued run is missing document_type")
    try:
        runtime_contract = supplier_source_contract_runtime.resolve_supplier_contract(
            supplier_id=run.supplier_id,
            contract_id=run.supplier_source_contract_id,
            contract_version=run.supplier_source_contract_version,
        )
    except supplier_source_contract_runtime.SupplierContractResolutionError as exc:
        raise RecordedContractError(str(exc)) from exc

    if runtime_contract.declaration.document_type.value != run.document_type:
        raise RecordedContractError("Recorded document_type does not match supplier contract")
    if source.document_type and source.document_type != run.document_type:
        raise RecordedContractError("Source document_type does not match ingestion run")
    source_format = (source.source_format or "").upper()
    if not _source_format_matches(source_format, runtime_contract.declaration.source_structure.source_format.value):
        raise RecordedContractError("Source format does not match recorded supplier contract")
    return runtime_contract


def recorded_contract_summary(db: Session, *, ingestion_run_id: UUID) -> RecordedSupplierContract:
    """Return a serializable exact contract summary for Prefect task boundaries."""

    runtime_contract = resolve_recorded_supplier_contract(db, ingestion_run_id=ingestion_run_id)
    return RecordedSupplierContract(
        supplier_id=runtime_contract.supplier_id or 0,
        contract_id=runtime_contract.slug,
        contract_version=runtime_contract.version,
        document_type=runtime_contract.declaration.document_type.value,
        source_format=runtime_contract.declaration.source_structure.source_format.value,
    )


def _source_format_matches(recorded: str, contract_format: str) -> bool:
    if contract_format in {"PDF", "PDF_TABLE"}:
        return recorded == "PDF"
    return recorded == contract_format

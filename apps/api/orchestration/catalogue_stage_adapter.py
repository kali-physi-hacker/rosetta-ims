"""Adapters from extracted source evidence into existing stage service commands."""

from __future__ import annotations

from uuid import UUID

from services import catalogue_pipeline_stages as stages
from services.catalogue_evidence_extraction import ExtractedEvidence

from .catalogue_extraction_adapter import staging_payload_from_extracted_row
from .catalogue_types import ExtractedCatalogueRow, RunIdentity


def raw_input_from_extracted_evidence(evidence: ExtractedEvidence) -> stages.RawObservationInput:
    """Map one evidence observation to one Raw input without semantic mutation."""

    source_metadata = {
        **evidence.source_metadata,
        "observation_key": evidence.observation_key,
        "provider": evidence.provider,
        "provider_version": evidence.provider_version,
        "provider_request_id": evidence.provider_request_id,
        "extraction_warnings": list(evidence.warnings),
    }
    return stages.RawObservationInput(
        idempotency_key=evidence.observation_key,
        source_location=evidence.source_location,
        raw_text=evidence.raw_text,
        raw_cells=evidence.raw_cells,
        extraction_method=evidence.extraction_method,
        extraction_model=evidence.model or evidence.provider,
        extraction_model_version=evidence.model_version or evidence.provider_version,
        extraction_confidence=str(evidence.confidence) if evidence.confidence is not None else None,
        source_metadata=source_metadata,
    )


def raw_input_from_extracted_row(row: ExtractedCatalogueRow) -> stages.RawObservationInput:
    """Compatibility adapter for the legacy combined extraction/parsing row."""

    return stages.RawObservationInput(
        idempotency_key=row.row_key,
        source_location=row.source_location,
        raw_text=row.raw_text,
        raw_cells=row.raw_cells,
        extraction_method=row.extraction_method,
        extraction_model=row.extraction_model,
        extraction_model_version=row.extraction_model_version,
        extraction_confidence=str(row.extraction_confidence) if row.extraction_confidence is not None else None,
        source_metadata={"row_key": row.row_key},
    )


def staging_command_from_extracted_row(
    row: ExtractedCatalogueRow,
    *,
    raw_observation_id: UUID,
    runtime_contract,
) -> stages.BuildStagingItemCommand:
    """Create a Staging command while keeping raw and proposed fields separate."""

    raw_fields, proposed_fields = staging_payload_from_extracted_row(
        row,
        raw_observation_id=raw_observation_id,
        runtime_contract=runtime_contract,
    )
    return stages.BuildStagingItemCommand(
        raw_observation_ids=(raw_observation_id,),
        raw_fields=raw_fields,
        proposed_fields=proposed_fields,
        idempotency_key=row.row_key,
        review_requirement=None,
        metadata={"source_row_key": row.row_key},
    )


def mastering_command_for_staging(
    *,
    run_identity: RunIdentity,
    catalogue_item_id: UUID,
    row: ExtractedCatalogueRow,
) -> stages.PrepareMasteringCandidateCommand:
    """Create a pending-review mastering command without approval/application semantics."""

    fields = row.extracted_fields
    supplier_sku = _text(fields.get("supplier_sku"))
    product_name = _text(fields.get("description"))
    barcode = _text(fields.get("barcode"))
    supplier_resolution = {
        "state": "PROPOSED_CREATE" if supplier_sku else "UNRESOLVED",
        "supplier_id": run_identity.supplier_id,
        "supplier_product_id": f"supplier:{run_identity.supplier_id}:offer:{supplier_sku}" if supplier_sku else None,
        "supplier_sku": supplier_sku,
        "barcode": barcode,
    }
    product_resolution = {
        "state": "PROPOSED_CREATE" if product_name else "UNRESOLVED",
        "canonical_sku": supplier_sku,
        "product_variant_id": supplier_sku,
        "product_variant_name": product_name,
        "proposed_name": product_name,
        "product_family_id": None,
    }
    return stages.PrepareMasteringCandidateCommand(
        catalogue_item_id=catalogue_item_id,
        idempotency_key=row.row_key,
        supplier_product_resolution=supplier_resolution,
        product_variant_resolution=product_resolution,
        metadata={"source_row_key": row.row_key, "human_review_required": True},
    )


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None

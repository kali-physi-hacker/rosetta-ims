"""Adapters from evidence and interpretation results into stage service commands."""

from __future__ import annotations

from uuid import UUID

from services import catalogue_pipeline_stages as stages
from services.catalogue_evidence_extraction import ExtractedEvidence
from services.catalogue_interpretation import InterpretedItem

from .catalogue_types import RunIdentity


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


def staging_command_from_interpretation(item: InterpretedItem) -> stages.BuildStagingItemCommand:
    """Create a Staging command from one post-Raw interpreted observation."""

    return stages.BuildStagingItemCommand(
        raw_observation_ids=(item.raw_observation_id,),
        raw_fields=item.raw_fields,
        proposed_fields=item.proposed_fields,
        idempotency_key=item.observation_key,
        review_requirement=None,
        metadata={"source_observation_key": item.observation_key},
    )


def mastering_command_for_staging(
    *,
    run_identity: RunIdentity,
    catalogue_item_id: UUID,
    item: InterpretedItem,
) -> stages.PrepareMasteringCandidateCommand:
    """Create a pending-review mastering command from post-Raw interpretation.

    Supplier identity comes from the persisted run; sku/name/barcode come from
    the same interpreted fields that produced the staging item. No approval or
    application semantics are implied here.
    """

    supplier_sku = _proposal_or_raw(item, "supplier_sku")
    product_name = _proposal_or_raw(item, "product_name")
    barcode = _proposal_or_raw(item, "barcode")
    supplier_resolution = {
        "state": "PROPOSED_CREATE" if supplier_sku else "UNRESOLVED",
        "supplier_id": run_identity.supplier_id,
        "supplier_product_id": (
            f"supplier:{run_identity.supplier_id}:offer:{supplier_sku}" if supplier_sku else None
        ),
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
        idempotency_key=item.observation_key,
        supplier_product_resolution=supplier_resolution,
        product_variant_resolution=product_resolution,
        metadata={"source_observation_key": item.observation_key, "human_review_required": True},
    )


def _proposal_or_raw(item: InterpretedItem, field: str) -> str | None:
    proposal = item.proposed_fields.get(field)
    if isinstance(proposal, dict):
        value = proposal.get("value")
        if value is not None and str(value).strip():
            return str(value).strip()
    raw = item.raw_fields.get(field)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    return None

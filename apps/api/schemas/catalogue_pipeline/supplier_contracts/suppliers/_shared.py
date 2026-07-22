"""Shared helpers for supplier-source declarations."""

from __future__ import annotations

from datetime import datetime, timezone

from schemas.catalogue_pipeline.supplier_contracts.common import (
    EvidenceReference,
    PipelineContractMapping,
    SupplierSourceEvidenceType,
)


DECLARATION_CREATED_AT = datetime(2026, 7, 22, tzinfo=timezone.utc)
DECLARATION_CREATED_BY = "cis-103b-catalogue-architecture"


def evidence(evidence_type: SupplierSourceEvidenceType, reference: str, note: str) -> EvidenceReference:
    return EvidenceReference(evidence_type=evidence_type, reference=reference, note=note)


def pipeline_mapping(*field_keys: str) -> PipelineContractMapping:
    return PipelineContractMapping(
        raw_observation_fields=list(field_keys),
        staging_raw_field_keys=list(field_keys),
        staging_proposed_field_keys=list(field_keys),
    )


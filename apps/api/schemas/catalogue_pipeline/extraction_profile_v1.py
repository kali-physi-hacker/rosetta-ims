"""Extraction Profile Contract v1."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .base import ContractModel, register_contract
from .common import JsonObject, SupplierReference, UnitOfMeasure
from .enums import ExtractionProfileStatus, ProfileMatchStrategy, SourceFormat


CONTRACT_ID = "catalogue.extraction_profile.v1"


class ExtractionFieldMapping(ContractModel):
    """High-level source-to-business field mapping for an extraction profile."""

    target_field: str = Field(..., min_length=1, description="Pipeline field this mapping populates.")
    source_column: str | None = Field(None, description="Source column/header when applicable.")
    source_path: str | None = Field(None, description="Path or section selector when the source is not tabular.")
    constant_value: str | None = Field(None, description="Constant value supplied by the profile.")
    required: bool = Field(False, description="Whether missing data should create a validation issue.")
    notes: str | None = Field(None, description="Business-readable mapping notes.")

    @model_validator(mode="after")
    def _mapping_has_source_or_constant(self):
        if not (self.source_column or self.source_path or self.constant_value is not None):
            raise ValueError("field mapping requires source_column, source_path, or constant_value")
        return self


class PricingExtractionRules(ContractModel):
    """Profile rules that identify supplier price and basis fields."""

    cost_source_field: str = Field(..., min_length=1, description="Source field containing supplier cost.")
    rrp_source_field: str | None = Field(None, description="Source field containing RRP when present.")
    price_basis: UnitOfMeasure = Field(..., description="Price basis to apply to the extracted cost.")
    currency: Literal["HKD"] = Field("HKD", description="v1 profile currency.")
    autoswap_cost_rrp: bool = Field(False, description="Whether the profile may correct swapped wholesale/RRP values.")

    @model_validator(mode="after")
    def _basis_required(self):
        self.price_basis.require_known_code("pricing.price_basis")
        return self


class OrderingExtractionRules(ContractModel):
    """Profile rules for order multiple and minimum order extraction."""

    order_increment_source_field: str | None = Field(None, description="Source field for order increments.")
    minimum_order_source_field: str | None = Field(None, description="Source field for minimum order quantity.")
    qualifying_uom: UnitOfMeasure | None = Field(None, description="UOM used by ordering quantities when known.")


class ExtractionProfileV1(ContractModel):
    """Versioned configuration contract for supplier-format extraction profiles."""

    contract_id = CONTRACT_ID

    contract_version: Literal["catalogue.extraction_profile.v1"] = Field(
        ...,
        description="Exact CIS-103 extraction profile contract identifier.",
    )
    profile_id: str = Field(..., min_length=1, description="Stable profile identifier.")
    profile_version: str = Field(..., min_length=1, description="Version of this profile.")
    supplier: SupplierReference = Field(..., description="Supplier this profile applies to.")
    supplier_name: str = Field(..., min_length=1, description="Supplier name copied for readability.")
    source_format: SourceFormat = Field(..., description="Catalogue source format this profile supports.")
    status: ExtractionProfileStatus = Field(..., description="Profile lifecycle status.")
    match_strategy: ProfileMatchStrategy = Field(..., description="Primary strategy for matching rows.")
    field_mappings: list[ExtractionFieldMapping] = Field(
        ...,
        min_length=1,
        description="Typed high-level source-to-pipeline mappings.",
    )
    pricing: PricingExtractionRules = Field(..., description="Price extraction and basis rules.")
    ordering: OrderingExtractionRules | None = Field(None, description="Ordering extraction rules.")
    document_rules: JsonObject = Field(default_factory=dict, description="Typed extension point for document-level facts.")
    normalization_rules: JsonObject = Field(default_factory=dict, description="Typed extension point for normalization rules.")
    validation_rules: list[str] = Field(default_factory=list, description="Business validation expressions or rule identifiers.")
    legacy_source_reference: str | None = Field(
        None,
        description="Deprecated historical pointer only; YAML mappings are not extraction-profile contracts.",
    )
    created_at: datetime = Field(..., description="Timezone-aware profile creation timestamp.")
    created_by: str = Field(..., min_length=1, description="Profile author.")
    updated_at: datetime | None = Field(None, description="Timezone-aware profile update timestamp.")
    updated_by: str | None = Field(None, description="Last updater.")
    metadata: JsonObject = Field(default_factory=dict, description="Explicit extension point for non-contract metadata.")

    @model_validator(mode="after")
    def _validate_profile(self):
        if self.supplier_name != self.supplier.supplier_name:
            raise ValueError("supplier_name must match supplier.supplier_name")
        if self.updated_at is not None and self.updated_at < self.created_at:
            raise ValueError("updated_at cannot be earlier than created_at")
        if self.updated_at is not None and not self.updated_by:
            raise ValueError("updated_by is required when updated_at is supplied")
        return self


register_contract(ExtractionProfileV1)

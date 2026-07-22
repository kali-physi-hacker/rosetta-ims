"""Serving Item Contract v1."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, model_validator

from .base import ContractModel, register_contract
from .common import Cost, ExternalMapping, JsonObject, LineageReference, MbbTerm, Money, PackagingConfiguration
from .enums import ReviewStatus


CONTRACT_ID = "catalogue.serving_item.v1"


class SupplierOffering(ContractModel):
    """Approved supplier offering exposed to inventory consumers."""

    supplier_id: int = Field(..., gt=0, description="Rosetta supplier ID.")
    supplier_name: str = Field(..., min_length=1, description="Supplier display name.")
    supplier_product_id: str | None = Field(None, description="Stable supplier-product identity when available.")
    supplier_sku: str | None = Field(None, description="Supplier SKU.")
    barcode: str | None = Field(None, description="Barcode for this supplier offering.")


class PublicationLineage(LineageReference):
    """Lineage needed to trace a serving publication."""

    mastering_candidate_id: UUID = Field(..., description="Mastering Candidate that authorized publication.")
    publication_version: str = Field(..., min_length=1, description="Serving publication version/reference.")


class NormalizedCosts(ContractModel):
    """Optional derived costs for consumer views."""

    cost_per_sellable_unit: Money | None = Field(None, description="Default normalized cost per sellable unit.")
    cost_per_kg: Money | None = Field(None, description="Derived cost per kg when supported by source data.")
    cost_per_litre: Money | None = Field(None, description="Derived cost per litre when supported by source data.")


class ServingItemV1(ContractModel):
    """Approved catalogue information safe for All Inventory and SKU Details consumers."""

    contract_id = CONTRACT_ID

    contract_version: Literal["catalogue.serving_item.v1"] = Field(
        ...,
        description="Exact CIS-103 Serving Item contract identifier.",
    )
    serving_item_id: UUID = Field(..., description="Serving Item publication identity.")
    canonical_sku: str = Field(..., min_length=1, description="Rosetta canonical SKU code.")
    product_variant_id: str = Field(..., min_length=1, description="Canonical Product Variant identity.")
    product_variant_name: str = Field(..., min_length=1, description="Approved Product Variant name.")
    supplier_offering: SupplierOffering = Field(..., description="Approved supplier-specific offering.")
    purchasing_packaging: PackagingConfiguration = Field(..., description="Approved purchasing packaging.")
    current_approved_cost: Cost = Field(..., description="Approved current supplier cost.")
    cost_per_sellable_unit: Money | None = Field(None, description="Cost per sellable unit when derivable.")
    review_status: ReviewStatus = Field(..., description="Review status authorizing publication.")
    published_at: datetime = Field(..., description="Timezone-aware publication timestamp.")
    lineage: PublicationLineage = Field(..., description="Trace back to mastering, staging, and raw evidence.")
    product_family_id: str | None = Field(None, description="Optional Product Family enrichment.")
    brand: str | None = Field(None, description="Approved brand.")
    categories: list[str] = Field(default_factory=list, description="Approved category labels.")
    active_mbb_terms: list[MbbTerm] = Field(default_factory=list, description="Approved active MBB terms.")
    normalized_costs: NormalizedCosts | None = Field(None, description="Optional normalized cost bundle.")
    external_mappings: list[ExternalMapping] = Field(default_factory=list, description="External product mappings.")
    metadata: JsonObject = Field(default_factory=dict, description="Explicit extension point for non-contract metadata.")

    @model_validator(mode="after")
    def _serving_item_requires_publication_approval(self):
        if self.review_status not in {ReviewStatus.APPROVED, ReviewStatus.APPROVED_WITH_OVERRIDE}:
            raise ValueError("Serving Item requires APPROVED or APPROVED_WITH_OVERRIDE review status")
        if self.purchasing_packaging.sellable_units_per_purchase_unit is not None and self.cost_per_sellable_unit is None:
            raise ValueError("cost_per_sellable_unit is required when derivable from packaging")
        return self


register_contract(ServingItemV1)


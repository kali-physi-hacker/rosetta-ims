"""Kangaroo Pet Nutrition supplier-source contract declarations."""

from __future__ import annotations

from datetime import datetime, timezone

from schemas.catalogue_pipeline.enums import SourceFormat
from schemas.catalogue_pipeline.supplier_contracts.common import (
    SUPPLIER_SOURCE_SCHEMA_VERSION,
    AmbiguityRule,
    MbbSourceSemantics,
    PackagingSourceSemantics,
    PricingSourceSemantics,
    SemanticResolutionStatus,
    SourceFieldContract,
    SourceFieldRequirement,
    SourceFieldRole,
    SourceStructure,
    SourceTableRegion,
    SupplierContractSupportStatus,
    SupplierDocumentType,
    SupplierSourceContractV1,
    SupplierSourceEvidenceType,
    SupplierSourceReference,
)
from schemas.catalogue_pipeline.supplier_contracts.registry import register_supplier_source_contract

from ._shared import evidence, pipeline_mapping


_DECLARATION_CREATED_AT = datetime(2026, 7, 30, tzinfo=timezone.utc)
_DECLARATION_CREATED_BY = "catalogue-contract-integration"

_KANGAROO_PET_NUTRITION_SUPPLIER = SupplierSourceReference(
    supplier_id=81,
    supplier_name="Kangaroo Pet Nutrition",
    supplier_code="KANGAR",
)

_KANGAROO_PET_NUTRITION_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf",
        (
            "The sample contains sections explicitly identified as Kangaroo Pet "
            "Nutrition with ZIWI Peak and Ecuphar catalogue tables."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.BUSINESS_DOMAIN_DOCUMENTATION,
        "docs/technical-debt/kpn-kangaroo-supplier-source-contracts.md",
        "The production supplier identity is supplier ID 81 with code KANGAR.",
    ),
]


KANGAROO_PET_NUTRITION_CATALOGUE_BUNDLE_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kangaroo_pet_nutrition.catalogue_bundle.v1",
        contract_version="v1",
        supplier=_KANGAROO_PET_NUTRITION_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="Kangaroo Pet Nutrition catalogue bundle",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="kangaroo_pet_nutrition_identified_sections",
                    selector=(
                        "Catalogue sections attributed to supplier ID 81 or explicitly "
                        "marked Kangaroo Pet Nutrition / KANGAR"
                    ),
                    notes=(
                        "ZIWI Peak and Ecuphar are observed examples. A valid catalogue "
                        "may contain either, both, or other explicitly attributed brands."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=[
                "產品編號",
                "產品內容",
                "批發價",
                "包裝",
                "重量",
                "每箱",
                "每罐",
                "建議零售價",
                "With effect from",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 81, or the enclosing source section "
                    "must explicitly identify Kangaroo Pet Nutrition / KANGAR."
                ),
                "Never select this contract from page number or brand presence alone.",
                "Rows require a product code, description, and printed wholesale price.",
            ],
            source_location_expectations=[
                "source document and page",
                "supplier identity marker or ingestion supplier identity",
                "brand/section heading",
                "table row",
                "source column",
            ],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品編號",
                aliases=["SKU#", "Product Code"],
                description="Product code printed on the eligible Kangaroo Pet Nutrition row.",
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                # OPTIONAL, not required. The brand is printed as a banner above
                # the table, never as a column on the row, and a REQUIRED field
                # the engine cannot populate raises a BLOCKING
                # CONTRACT_REQUIRED_FIELD_MISSING on every row — a well-formed
                # row included. Whether we know the brand is not a reason to
                # refuse the price the supplier printed.
                requirement=SourceFieldRequirement.OPTIONAL,
                # `section_header` is the one source_path the conformance engine
                # implements: the banner spanning the table, carried on the
                # observation's source_metadata. Prose here reads as a column
                # name, matches nothing, and captures nothing.
                source_path="section_header",
                description="ZIWI Peak or the row-level Ecuphar portfolio brand/product line.",
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品內容",
                aliases=["產品名稱", "Product Description"],
                description="Printed English/Chinese product description.",
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="包裝 / 重量",
                aliases=["包裝", "重量", "Size"],
                description="Printed content size or packaging text.",
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="units_per_case",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="每箱包數 / 每箱罐數",
                aliases=["每箱", "每箱包數", "每箱罐數", "Units Per Case"],
                description="Printed case configuration for layouts that expose it.",
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價",
                aliases=["每包批發價", "每箱批發價", "Wholesale Price"],
                description="Wholesale amount preserved with its exact printed source heading.",
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="建議零售價",
                aliases=["每包建議零售價", "每箱建議零售價", "每罐建議零售價"],
                description="Recommended retail amount preserved with its printed price basis.",
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document or section effective-date label",
                description="Document- or section-level effective date.",
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document, section, or row promotion notes",
                description="Printed promotion or spend-discount terms.",
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="wholesale_price",
            rrp_source_field="rrp",
            price_basis=None,
            price_basis_status=SemanticResolutionStatus.UNRESOLVED,
            notes=(
                "Observed ZIWI dry layouts price packages, ZIWI wet layouts expose case "
                "and per-can amounts, and Ecuphar uses another layout. Preserve the source heading "
                "and leave price basis unresolved at bundle level."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat content size as a measure, not a sellable-unit count.",
                "Treat units per case as case configuration only.",
                "Do not infer purchase UOM, order increment, or break-pack permission from case configuration.",
            ],
            unresolved_semantics=[
                "Purchase UOM varies between ZIWI dry, ZIWI wet, and Ecuphar layouts.",
                "Sellable units per purchase unit are not established at bundle level.",
                "Break-pack permission is not established by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["spend threshold"],
            benefit_patterns=["percentage discount"],
            requires_validation_issue_when=[
                "The qualifying products, threshold basis, or stacking rules are not explicit."
            ],
            notes="Promotion text remains evidence until its scope is confirmed.",
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KANGAROO_PET_NUTRITION_BUNDLE_PRICE_BASIS_VARIES",
                condition="Observed supplier layouts mix package, case, per-can, and simple price bases.",
                review_guidance="Detect the row/table layout before interpreting price amounts.",
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="KANGAROO_PET_NUTRITION_SUPPLIER_IDENTITY_REQUIRED",
                condition="A source may contain multiple suppliers or only a subset of previously observed brands.",
                review_guidance=(
                    "Select this declaration only from ingestion supplier ID 81 or an explicit "
                    "Kangaroo Pet Nutrition / KANGAR source marker; never from page position or brand."
                ),
                blocks_supported_status=True,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "brand",
            "description",
            "pack_size",
            "units_per_case",
            "wholesale_price",
            "rrp",
            "effective_date",
            "promotion_text",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
        metadata={
            "routing_strategy": "supplier_identity_and_content_markers",
            "sample_reference": "KPN_Kangaroo.pdf",
            "observed_brands": "ZIWI Peak, Ecuphar",
        },
    )
)

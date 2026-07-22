"""Alfamedic supplier-source contract declarations."""

from __future__ import annotations

from schemas.catalogue_pipeline.common import SupplierReference, UnitOfMeasure
from schemas.catalogue_pipeline.enums import IssueSeverity, SourceFormat, UnitCode
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
    SupplierValidationRule,
)
from schemas.catalogue_pipeline.supplier_contracts.registry import register_supplier_source_contract

from ._shared import DECLARATION_CREATED_AT, DECLARATION_CREATED_BY, evidence, pipeline_mapping


_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.LEGACY_YAML_ONLY,
        "apps/api/catalogue_contracts/alfamedic.yaml",
        "Legacy extraction configuration names columns and parser expectations; not authoritative by itself.",
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "apps/api/services/catalogue_contract.py",
        "Runtime enforcer parses order increment from packing text, nulls spurious RRP, and normalizes By Quote cost.",
    ),
    evidence(
        SupplierSourceEvidenceType.EXISTING_PRODUCTION_TEST_EXTRACTION_FIXTURE,
        "apps/api/tests/test_catalogue_contract.py::test_alfamedic_parses_order_multiple_and_keeps_per_unit_cost",
        "Tests representative Alfamedic rows for per-piece price behavior and order-multiple separation.",
    ),
    evidence(
        SupplierSourceEvidenceType.BUSINESS_DOMAIN_DOCUMENTATION,
        "docs/architecture/catalogue-domain/catalogue-entity-dictionary.md",
        "Domain dictionary treats legacy YAML as evidence of current state, not canonical truth.",
    ),
]


ALFAMEDIC_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="alfamedic.price_list.v1",
        contract_version="v1",
        supplier=SupplierReference(supplier_id=1, supplier_name="Alfamedic", supplier_code="ALF"),
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Alfamedic HK PDF price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_EVIDENCE,
        legacy_yaml_reference="apps/api/catalogue_contracts/alfamedic.yaml",
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            expected_sections=["therapeutic class sections"],
            table_regions=[
                SourceTableRegion(
                    name="therapeutic_class_price_rows",
                    selector="PDF tables grouped by therapeutic class",
                    notes="No raw PDF source sample is committed; section detail comes from legacy config.",
                )
            ],
            required_headers=[
                "Order Code",
                "Product Name",
                "Brand",
                "Packing / Unit",
                "Price/ Unit (HKD)",
            ],
            row_eligibility_rules=["Catalogue item rows contain an order code and product name."],
            source_location_expectations=["page number", "section header", "table row", "source column"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Order Code",
                description="Stable Alfamedic order code.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Product Name",
                description="Printed product name.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Brand",
                description="Printed brand column when present.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Packing / Unit",
                description="Raw packing text; used by the current parser to derive order increment only.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="cost",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Price/ Unit (HKD)",
                aliases=["Price/ Unit (HKD)", "Price/Unit (HKD)"],
                description="Supplier cost field; By Quote is retained as a null-cost/manual-quote case.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="segment",
                role=SourceFieldRole.SEGMENT,
                requirement=SourceFieldRequirement.OPTIONAL,
                constant_value="vet",
                description="Legacy configuration classifies this catalogue as vet segment.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="category",
                role=SourceFieldRole.CATEGORY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description="Therapeutic section header; Medicine remains a legacy default requiring business review.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="bulk_tier_rows",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.CONDITIONALLY_REQUIRED,
                source_path="multiple rows sharing an order code",
                description="Legacy config notes multi-row tiers; no checked-in source examples prove all tier semantics.",
                evidence=_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="cost",
            rrp_source_field=None,
            price_basis=UnitOfMeasure(code=UnitCode.PIECE),
            price_basis_status=SemanticResolutionStatus.PARTIALLY_VERIFIED,
            null_cost_markers=["By Quote"],
            notes="Current parser treats Price/Unit as per individual sellable piece and does not expose RRP.",
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            price_basis=UnitOfMeasure(code=UnitCode.PIECE),
            order_increment_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Leading count in Packing / Unit is interpreted as supplier order increment, not a price divisor.",
                "Price basis remains per sellable piece in current parser behavior.",
            ],
            unresolved_semantics=[
                "Purchase UOM and break-pack permission are not proven by checked-in source evidence.",
                "Packing text may contain content/count language and must not be treated as canonical packaging without review.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["bulk_tier_rows"],
            condition_patterns=["multiple rows for the same order code"],
            benefit_patterns=["discounted unit price tiers"],
            requires_validation_issue_when=[
                "Rows with repeated order codes do not clearly state tier quantity, basis, or effective period."
            ],
            notes="Multi-row bulk tiers require later supplier-specific parsing evidence before automated normalization.",
        ),
        validation_rules=[
            SupplierValidationRule(
                rule_id="alfamedic.cost_positive_when_present",
                description="Numeric cost must be positive; By Quote is null and reviewed manually.",
                source_expression="cost_price > 0",
                severity=IssueSeverity.ERROR,
                issue_code="ALFAMEDIC_COST_NOT_POSITIVE",
                review_guidance="Confirm whether the row is By Quote or whether the supplier cost was misread.",
                evidence=_EVIDENCE,
            ),
            SupplierValidationRule(
                rule_id="alfamedic.order_increment_positive",
                description="Parsed order increment must be positive when present.",
                source_expression="order_increment_qty >= 1",
                severity=IssueSeverity.ERROR,
                issue_code="ALFAMEDIC_ORDER_INCREMENT_NOT_POSITIVE",
                review_guidance="Confirm the Packing / Unit text before approving the ordering terms.",
                evidence=_EVIDENCE,
            ),
        ],
        known_ambiguities=[
            AmbiguityRule(
                issue_code="ALFAMEDIC_SOURCE_SAMPLE_REQUIRED_FOR_SUPPORTED",
                condition="The repository has parser fixtures but no raw Alfamedic source PDF.",
                review_guidance="Attach the real Alfamedic price list and confirm Price/Unit and Packing/Unit semantics.",
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="ALFAMEDIC_MBB_TIER_BASIS_UNVERIFIED",
                condition="Repeated order-code rows may represent bulk tiers, but tier condition and benefit semantics are not fully evidenced.",
                review_guidance="Confirm how Alfamedic tier rows specify minimum quantity and discounted price before normalizing MBB.",
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "description",
            "brand",
            "pack_size",
            "cost",
            "segment",
            "category",
            "bulk_tier_rows",
        ),
        created_at=DECLARATION_CREATED_AT,
        created_by=DECLARATION_CREATED_BY,
    )
)


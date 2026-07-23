"""Vetapet supplier-source contract declarations."""

from __future__ import annotations

from schemas.catalogue_pipeline.common import UnitOfMeasure
from schemas.catalogue_pipeline.enums import IssueSeverity, SourceFormat, UnitCode
from schemas.catalogue_pipeline.supplier_contracts.common import (
    SUPPLIER_SOURCE_SCHEMA_VERSION,
    AmbiguityRule,
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
    SupplierValidationRule,
)
from schemas.catalogue_pipeline.supplier_contracts.registry import register_supplier_source_contract

from ._shared import DECLARATION_CREATED_AT, DECLARATION_CREATED_BY, evidence, pipeline_mapping


_VET_COMMON_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:Vetapet.pdf",
        "I supplied a 177-page PDF sample confirming Vetapet catalogue sections and multiple table layouts, including CODE NO/Product Name/Packing Per Unit/Unit Price and later Wholesale/Retail/Terms tables.",
    ),
]

_NON_VET_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:Vetapet.pdf",
        "I supplied a 177-page PDF that includes later Chinese/retail sections with weight, wholesale, and retail price labels, but rows require supplier-format review.",
    ),
]


def _vetapet_fields(*, segment: str, evidence_items: list) -> list[SourceFieldContract]:
    return [
        SourceFieldContract(
            field_key="supplier_sku",
            role=SourceFieldRole.SUPPLIER_SKU,
            requirement=SourceFieldRequirement.REQUIRED,
            source_column="CODE NO / 編號",
            description="Vetapet code number.",
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="description",
            role=SourceFieldRole.PRODUCT_NAME,
            requirement=SourceFieldRequirement.REQUIRED,
            source_column="PRODUCT NAME / 產品名稱",
            description="Printed product name.",
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="pack_size",
            role=SourceFieldRole.PACKAGING,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="SIZE / PACK / 重量" if segment == "vet" else "重量 / SIZE",
            description="Raw size/packaging text; may express content measure, pack description, or both.",
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="cost",
            role=SourceFieldRole.SOURCE_PRICE,
            requirement=SourceFieldRequirement.REQUIRED,
            source_column="WHOLESALE PRICE / 批發價" if segment == "vet" else "批發價 / WHOLESALE PRICE",
            description="Wholesale price field.",
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="rrp",
            role=SourceFieldRole.RRP,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="SUGGESTED RETAIL PRICE / RETAIL PRICE / 零售價" if segment == "vet" else "零售價 / RETAIL PRICE",
            description="Suggested retail or retail price field.",
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="species",
            role=SourceFieldRole.SPECIES,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_path="product_name",
            description="Species cue from product name when present.",
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="segment",
            role=SourceFieldRole.SEGMENT,
            requirement=SourceFieldRequirement.OPTIONAL,
            constant_value=segment,
            description="Vetapet catalogue segment split.",
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="category",
            role=SourceFieldRole.CATEGORY,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_path="section_header",
            description="Brand/section-derived category remains supplier-specific and reviewable.",
            evidence=evidence_items,
        ),
    ]


_VET_VALIDATION_RULES = [
    SupplierValidationRule(
        rule_id="vetapet.cost_below_rrp_after_autoswap",
        description="Wholesale should be below retail after any allowed autoswap.",
        source_expression="cost_price < rrp",
        severity=IssueSeverity.ERROR,
        issue_code="VETAPET_COST_NOT_BELOW_RRP",
        review_guidance="Confirm whether wholesale and retail prices were swapped or whether the row has a non-standard promotion.",
        evidence=_VET_COMMON_EVIDENCE,
    ),
    SupplierValidationRule(
        rule_id="vetapet.cost_positive",
        description="Wholesale cost must be positive when present.",
        source_expression="cost_price > 0",
        severity=IssueSeverity.ERROR,
        issue_code="VETAPET_COST_NOT_POSITIVE",
        review_guidance="Confirm the printed wholesale value before approving the supplier cost.",
        evidence=_VET_COMMON_EVIDENCE,
    ),
]


VETAPET_VET_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="vetapet.vet_price_list.v1",
        contract_version="v1",
        supplier=SupplierSourceReference(supplier_id=91, supplier_name="Vetapet Vet", supplier_code=None),
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Vetapet Vet PDF price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_VET_COMMON_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            expected_sections=["Part A Drugs", "Part B Supplements", "IVD", "Dermoscent", "Chung-Li", "Li-Saint DermCare"],
            table_regions=[
                SourceTableRegion(
                    name="vet_clinic_unit_price_sections",
                    selector="CODE NO / PRODUCT NAME / PACKING PER UNIT / UNIT PRICE / REMARKS or TERMS",
                    notes="Observed in the supplied Vetapet.pdf early clinic sections.",
                ),
                SourceTableRegion(
                    name="vet_wholesale_retail_sections",
                    selector="CODE NO / PRODUCT NAME / WHOLESALE PRICE / RETAIL PRICE / TERMS",
                    notes="Observed later in the supplied Vetapet.pdf; representative per-section row fixtures are still needed.",
                )
            ],
            required_headers=["CODE NO", "PRODUCT NAME"],
            optional_headers=[
                "PACKING PER UNIT",
                "UNIT PRICE",
                "WHOLESALE PRICE",
                "RETAIL PRICE",
                "TERMS",
                "SIZE / PACK / 重量",
                "SUGGESTED RETAIL PRICE / RETAIL PRICE / 零售價",
            ],
            row_eligibility_rules=["One product row per code/price entry."],
            source_location_expectations=["page number", "section header", "table row", "source column"],
        ),
        fields=_vetapet_fields(segment="vet", evidence_items=_VET_COMMON_EVIDENCE),
        pricing=PricingSourceSemantics(
            cost_source_field="cost",
            rrp_source_field="rrp",
            price_basis=UnitOfMeasure(code=UnitCode.UNIT),
            price_basis_status=SemanticResolutionStatus.PARTIALLY_VERIFIED,
            autoswap_cost_rrp_allowed=True,
            notes="The source PDF confirms multiple price layouts. Wholesale/Retail sections align with parser tests; Unit Price sections need a dedicated parser rule before production use.",
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            price_basis=UnitOfMeasure(code=UnitCode.UNIT),
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat kg/g/ml size text as content measure, not sellable-unit count.",
                "Pack descriptions such as tubes/pack are not proof of supplier order multiple without explicit terms.",
            ],
            unresolved_semantics=[
                "Purchase UOM, order increment, and break-pack rules are not proven by checked-in source evidence.",
            ],
        ),
        validation_rules=_VET_VALIDATION_RULES,
        known_ambiguities=[
            AmbiguityRule(
                issue_code="VETAPET_VET_MULTIPLE_TABLE_LAYOUTS",
                condition="The supplied Vetapet PDF contains both Unit Price tables and Wholesale/Retail/Terms tables.",
                review_guidance="Decide whether to split Vetapet into multiple supplier-format contracts or add typed per-section interpretation rules before production selection.",
                blocks_supported_status=True,
            )
        ],
        pipeline_mapping=pipeline_mapping("supplier_sku", "description", "pack_size", "cost", "rrp", "species", "segment", "category"),
        created_at=DECLARATION_CREATED_AT,
        created_by=DECLARATION_CREATED_BY,
    )
)


VETAPET_NON_VET_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="vetapet.non_vet_price_list.v1",
        contract_version="v1",
        supplier=SupplierSourceReference(supplier_id=90, supplier_name="Vetapet (Non-Vet)", supplier_code=None),
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Vetapet Non-Vet PDF price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_NON_VET_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            expected_sections=["multi-brand non-vet sections"],
            table_regions=[
                SourceTableRegion(
                    name="non_vet_brand_sections",
                    selector="Chinese-primary price rows",
            notes="The supplied Vetapet.pdf contains matching Chinese wholesale/retail labels, but non-vet row semantics still need representative extraction fixtures.",
                )
            ],
            required_headers=["CODE NO / 編號", "PRODUCT NAME / 產品名稱", "批發價 / WHOLESALE PRICE"],
            optional_headers=["重量 / SIZE", "零售價 / RETAIL PRICE"],
            row_eligibility_rules=["One product row per code/price entry when a real sample confirms this shape."],
            source_location_expectations=["page number", "section header", "table row", "source column"],
        ),
        fields=_vetapet_fields(segment="non_vet", evidence_items=_NON_VET_EVIDENCE),
        pricing=PricingSourceSemantics(
            cost_source_field="cost",
            rrp_source_field="rrp",
            price_basis=None,
            price_basis_status=SemanticResolutionStatus.UNRESOLVED,
            autoswap_cost_rrp_allowed=True,
            notes="The supplied source confirms wholesale/retail labels, but the price basis remains unresolved without row-level business confirmation.",
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat weight/size text as content measure only after a real sample confirms row semantics.",
            ],
            unresolved_semantics=[
                "Price basis, purchase UOM, sellable unit, order increment, and break-pack rules are unresolved.",
            ],
        ),
        validation_rules=[
            SupplierValidationRule(
                rule_id="vetapet_non_vet.cost_below_rrp_unverified",
                description="Wholesale should be below retail when the source section's column mapping is confirmed.",
                source_expression="cost_price < rrp",
                severity=IssueSeverity.WARNING,
                issue_code="VETAPET_NON_VET_COST_RRP_UNVERIFIED",
                review_guidance="Confirm the non-vet source columns before applying wholesale/RRP validation automatically.",
                evidence=_NON_VET_EVIDENCE,
            )
        ],
        known_ambiguities=[
            AmbiguityRule(
                issue_code="VETAPET_NON_VET_ROW_FIXTURE_MISSING",
                condition="The supplied PDF has relevant labels, but no representative extracted non-vet row fixture has been committed.",
                review_guidance="Create representative row fixtures from the source PDF and confirm wholesale, retail, size, and category semantics.",
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="VETAPET_NON_VET_PRICE_BASIS_UNRESOLVED",
                condition="A numeric wholesale price does not prove the supplier price basis.",
                review_guidance="Confirm whether the wholesale price is per sellable unit, pack, case, or another basis.",
            ),
        ],
        pipeline_mapping=pipeline_mapping("supplier_sku", "description", "pack_size", "cost", "rrp", "species", "segment", "category"),
        created_at=DECLARATION_CREATED_AT,
        created_by=DECLARATION_CREATED_BY,
    )
)

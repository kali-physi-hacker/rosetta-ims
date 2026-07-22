"""Vetapet supplier-source contract declarations."""

from __future__ import annotations

from schemas.catalogue_pipeline.common import SupplierReference, UnitOfMeasure
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
    SupplierValidationRule,
)
from schemas.catalogue_pipeline.supplier_contracts.registry import register_supplier_source_contract

from ._shared import DECLARATION_CREATED_AT, DECLARATION_CREATED_BY, evidence, pipeline_mapping


_VET_COMMON_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.LEGACY_YAML_ONLY,
        "apps/api/catalogue_contracts/vetapet_vet.yaml",
        "Legacy vet catalogue config names columns and current parser expectations; not authoritative by itself.",
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "apps/api/services/catalogue_contract.py",
        "Runtime enforcer can autoswap wholesale/RRP when explicitly enabled and parse kg content measure.",
    ),
    evidence(
        SupplierSourceEvidenceType.EXISTING_PRODUCTION_TEST_EXTRACTION_FIXTURE,
        "apps/api/tests/test_catalogue_contract.py::test_vetapet_autoswaps_wholesale_below_rrp",
        "Tests representative Vetapet Vet rows for autoswap behavior and kg parsing.",
    ),
]

_NON_VET_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.LEGACY_YAML_ONLY,
        "apps/api/catalogue_contracts/vetapet_nonvet.yaml",
        "Legacy non-vet catalogue config exists, but there is no representative row fixture beyond load coverage.",
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "apps/api/tests/test_catalogue_contract.py::test_loads_vetapet_contracts",
        "Existing tests only prove the legacy YAML file loads for supplier id 90.",
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
        supplier=SupplierReference(supplier_id=91, supplier_name="Vetapet Vet", supplier_code=None),
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Vetapet Vet PDF price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_VET_COMMON_EVIDENCE,
        legacy_yaml_reference="apps/api/catalogue_contracts/vetapet_vet.yaml",
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            expected_sections=["IVD", "Dermoscent", "Chung-Li", "Li-Saint DermCare"],
            table_regions=[
                SourceTableRegion(
                    name="vet_brand_sections",
                    selector="Brand-section PDF tables",
                    notes="No raw PDF source sample is committed; examples come from parser tests and YAML comments.",
                )
            ],
            required_headers=["CODE NO / 編號", "PRODUCT NAME / 產品名稱", "WHOLESALE PRICE / 批發價"],
            optional_headers=["SIZE / PACK / 重量", "SUGGESTED RETAIL PRICE / RETAIL PRICE / 零售價", "TERMS"],
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
            notes="Autoswap behavior is covered by parser tests; price basis still lacks raw-source confirmation.",
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
                issue_code="VETAPET_VET_SOURCE_SAMPLE_REQUIRED_FOR_SUPPORTED",
                condition="The repository has parser fixtures but no raw Vetapet Vet source PDF.",
                review_guidance="Attach the real Vetapet Vet catalogue and confirm wholesale/RRP and packaging headers.",
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
        supplier=SupplierReference(supplier_id=90, supplier_name="Vetapet (Non-Vet)", supplier_code=None),
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Vetapet Non-Vet PDF price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.UNVERIFIED,
        evidence=_NON_VET_EVIDENCE,
        legacy_yaml_reference="apps/api/catalogue_contracts/vetapet_nonvet.yaml",
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            expected_sections=["multi-brand non-vet sections"],
            table_regions=[
                SourceTableRegion(
                    name="non_vet_brand_sections",
                    selector="Chinese-primary price rows",
                    notes="Only the legacy YAML shape is present in-repo; no representative row fixture validates semantics.",
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
            notes="Legacy YAML claims per-unit wholesale semantics, but this contract leaves the basis unresolved until source evidence is supplied.",
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
                description="Wholesale should be below retail if the legacy column mapping is confirmed.",
                source_expression="cost_price < rrp",
                severity=IssueSeverity.WARNING,
                issue_code="VETAPET_NON_VET_COST_RRP_UNVERIFIED",
                review_guidance="Confirm the non-vet source columns before applying wholesale/RRP validation automatically.",
                evidence=_NON_VET_EVIDENCE,
            )
        ],
        known_ambiguities=[
            AmbiguityRule(
                issue_code="VETAPET_NON_VET_SOURCE_SAMPLE_MISSING",
                condition="No representative non-vet source row or source PDF exists in the repository.",
                review_guidance="Attach a real Vetapet Non-Vet catalogue sample and confirm wholesale, retail, size, and category semantics.",
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


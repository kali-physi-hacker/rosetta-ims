"""Kangaroo/KPN supplier-source contract declarations."""

from __future__ import annotations

from schemas.catalogue_pipeline.common import UnitOfMeasure
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
    SupplierSourceReference,
    SupplierValidationRule,
)
from schemas.catalogue_pipeline.supplier_contracts.registry import register_supplier_source_contract

from ._shared import DECLARATION_CREATED_AT, DECLARATION_CREATED_BY, evidence, pipeline_mapping


_KPN_SUPPLIER = SupplierSourceReference(
    supplier_id=None,
    supplier_name="Kangaroo Pet Nutrition Ltd",
    supplier_code="KPN",
)

_KPN_MIXED_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf",
        "I supplied a 53-page PDF sample with extractable Chinese/English catalogue tables, last-update labels, wholesale prices, retail prices, package sizes, and units per case.",
    ),
    evidence(
        SupplierSourceEvidenceType.BUSINESS_DOMAIN_DOCUMENTATION,
        "label I supplied: KPN Kangaroo",
        "I supplied the supplier identity; numeric Rosetta supplier id was not present in the clean checkout.",
    ),
]

_KPN_PROPLAN_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:✔ Proplan PPVD & PPSD Product List 202412 New packing.pdf",
        "I supplied a 3-page Excel-produced PDF sample with Product List, Packs/Case, Size, Supply Price/Pack HK$, Retail Price/Pack HK$, SKU, and Effective from DEC 2024.",
    ),
    evidence(
        SupplierSourceEvidenceType.BUSINESS_DOMAIN_DOCUMENTATION,
        "label I supplied: KPN Kangaroo",
        "I supplied the supplier identity; numeric Rosetta supplier id was not present in the clean checkout.",
    ),
]

_KPN_EARTHZ_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:(Kangaroo) Earthz Pet.pdf",
        "I supplied a one-page image-only PDF price sheet for Earthz Pet; visual inspection confirms SKU, wholesale, retail, size, and buy-9-get-1 promotion cues.",
    ),
    evidence(
        SupplierSourceEvidenceType.BUSINESS_DOMAIN_DOCUMENTATION,
        "label I supplied: KPN Kangaroo",
        "I supplied the supplier identity; numeric Rosetta supplier id was not present in the clean checkout.",
    ),
]


KANGAROO_MIXED_PRICE_CATALOGUE_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kangaroo.mixed_price_catalogue.v1",
        contract_version="v1",
        supplier=_KPN_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="Kangaroo mixed product catalogue PDF",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_KPN_MIXED_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            expected_sections=[
                "Frozen Raw Dinner Patties",
                "Freeze-Dried Raw Dog Food",
                "New Recyclable Packing",
            ],
            table_regions=[
                SourceTableRegion(
                    name="mixed_wholesale_retail_tables",
                    selector="Chinese/English tables with SKU#, Product Description, Size, Unit Per Case, Wholesale Price Per Unit, Retail Price Per Unit",
                    notes="The supplied PDF contains several table variants; this declaration captures the shared source concepts without assuming one parser layout.",
                )
            ],
            required_headers=["產品編號", "產品名稱", "批發價", "建議零售價"],
            optional_headers=[
                "SKU#",
                "Product Description",
                "Size",
                "Unit Per Case",
                "Wholesale Price Per Unit",
                "Retail Price Per Unit",
                "Last update",
            ],
            row_eligibility_rules=["Rows with a SKU/product code and wholesale price are candidate catalogue rows."],
            source_location_expectations=["page number", "section header", "table row", "source column"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品編號 / SKU#",
                aliases=["產品編號", "SKU#"],
                description="Kangaroo catalogue product code.",
                evidence=_KPN_MIXED_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品名稱 / Product Description",
                aliases=["產品名稱", "產品內容", "Product Description"],
                description="Printed product name or description.",
                evidence=_KPN_MIXED_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="包裝 / Size",
                aliases=["重量", "包裝", "Size"],
                description="Printed size or packaging text.",
                evidence=_KPN_MIXED_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="units_per_case",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="每箱包數 / Unit Per Case",
                aliases=["原箱包數", "每箱包數", "Unit Per Case", "Per Case"],
                description="Case configuration printed by source; not proof of case-only purchasing.",
                evidence=_KPN_MIXED_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="cost",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="每包批發價 / Wholesale Price Per Unit",
                aliases=["批發價", "每包批發價", "Wholesale Price Per Unit", "Price Per Unit"],
                description="Wholesale price source field.",
                evidence=_KPN_MIXED_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="每包建議零售價 / Retail Price Per Unit",
                aliases=["建議零售價", "Recommended Retail Price", "Retail Price Per Unit"],
                description="Recommended retail price source field.",
                evidence=_KPN_MIXED_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="last_update",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description="Section-level last update text.",
                evidence=_KPN_MIXED_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_notes",
                description="Discount or promotion notes, such as spend threshold discounts.",
                evidence=_KPN_MIXED_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="cost",
            rrp_source_field="rrp",
            price_basis=UnitOfMeasure(code=UnitCode.UNIT),
            price_basis_status=SemanticResolutionStatus.PARTIALLY_VERIFIED,
            notes="Source headers say Price Per Unit/Wholesale Price Per Unit, but individual table variants need parser fixtures before production use.",
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            price_basis=UnitOfMeasure(code=UnitCode.UNIT),
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat Unit Per Case as case configuration only unless supplier ordering rules prove it is an order multiple.",
                "Do not use size/content text as sellable-unit count.",
            ],
            unresolved_semantics=[
                "Purchase UOM and break-pack permission are not proven by the source sample alone.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["spend threshold discount notes"],
            benefit_patterns=["percentage discount notes"],
            requires_validation_issue_when=["The qualifying product set or spend basis is not explicit."],
        ),
        validation_rules=[
            SupplierValidationRule(
                rule_id="kangaroo.cost_below_rrp",
                description="Wholesale should be below recommended retail when both are present.",
                source_expression="cost_price < rrp",
                severity=IssueSeverity.ERROR,
                issue_code="KANGAROO_COST_NOT_BELOW_RRP",
                review_guidance="Check whether the wholesale and retail columns were swapped before approving the item.",
                evidence=_KPN_MIXED_EVIDENCE,
            )
        ],
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KANGAROO_NUMERIC_SUPPLIER_ID_MISSING",
                condition="The sample identifies Kangaroo/KPN, but this clean checkout has no numeric supplier id for it.",
                review_guidance="Import or supply the Rosetta supplier id before selecting this contract in runtime ingestion.",
            ),
            AmbiguityRule(
                issue_code="KANGAROO_MULTIPLE_TABLE_LAYOUTS",
                condition="The supplied mixed catalogue contains several table shapes with per-unit and per-case labels.",
                review_guidance="Create representative row fixtures per section before promoting to SUPPORTED.",
                blocks_supported_status=True,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "description",
            "pack_size",
            "units_per_case",
            "cost",
            "rrp",
            "last_update",
            "promotion_text",
        ),
        created_at=DECLARATION_CREATED_AT,
        created_by=DECLARATION_CREATED_BY,
    )
)


KANGAROO_PURINA_PROPLAN_VETERINARY_DIETS_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kangaroo.purina_proplan_veterinary_diets.v1",
        contract_version="v1",
        supplier=_KPN_SUPPLIER,
        document_type=SupplierDocumentType.PRODUCT_LIST,
        format_name="Purina Pro Plan Veterinary Diets Product List",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_KPN_PROPLAN_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            expected_sections=[
                "Purina Pro Plan Veterinary Supplements",
                "Purina Pro Plan Veterinary Diets Canine Formula",
                "Purina Pro Plan Veterinary Diets Feline Formula",
            ],
            table_regions=[
                SourceTableRegion(
                    name="proplan_product_rows",
                    selector="Rows containing SKU#, Packs / Case, Size, Supply Price / Pack HK$, and Retail Price / Pack HK$",
                    notes="Extracted text is table-like but product names span multiple lines.",
                )
            ],
            required_headers=["SKU#", "Packs / Case", "Size", "Supply Price / Pack HK$"],
            optional_headers=["Retail Price / Pack HK$", "Effective from DEC 2024"],
            row_eligibility_rules=["Rows with SKU# and Supply Price / Pack are candidate catalogue rows."],
            source_location_expectations=["page number", "section heading", "row group", "source column"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="SKU#",
                aliases=["SKU#", "OLD SKU# > NEW SKU#"],
                description="Purina SKU shown in the product row.",
                evidence=_KPN_PROPLAN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_path="product row text above SKU#",
                description="English/Chinese product name preceding the SKU line.",
                evidence=_KPN_PROPLAN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="packs_per_case",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Packs / Case",
                description="Case configuration such as 1x6 or 1x24.",
                evidence=_KPN_PROPLAN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Size",
                description="Printed pack size and content measure.",
                evidence=_KPN_PROPLAN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="cost",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Supply Price / Pack HK$",
                description="Supplier price per pack.",
                evidence=_KPN_PROPLAN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Retail Price / Pack HK$",
                description="Recommended retail price per pack or case, as printed.",
                evidence=_KPN_PROPLAN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="page footer",
                constant_value=None,
                description="Effective-from footer text, observed as DEC 2024.",
                evidence=_KPN_PROPLAN_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="cost",
            rrp_source_field="rrp",
            price_basis=UnitOfMeasure(code=UnitCode.PACK),
            price_basis_status=SemanticResolutionStatus.PARTIALLY_VERIFIED,
            notes="Source header states Supply Price / Pack HK$; wet-can retail values can be printed as case and per-can values and need parser fixtures.",
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            price_basis=UnitOfMeasure(code=UnitCode.PACK),
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat Packs / Case as case configuration, not proof that supplier requires case-only purchasing.",
                "Content measures such as 5.5oz (156g) are not sellable-unit counts.",
            ],
            unresolved_semantics=["Order increment and break-pack permission are not proven by the source sample."],
        ),
        validation_rules=[
            SupplierValidationRule(
                rule_id="kangaroo_proplan.cost_below_rrp",
                description="Supply price should be below retail price when both are comparable pack prices.",
                source_expression="cost_price < rrp",
                severity=IssueSeverity.WARNING,
                issue_code="KANGAROO_PROPLAN_COST_RRP_REVIEW",
                review_guidance="Confirm whether retail was printed per pack, per case, or per can before applying this as a blocking rule.",
                evidence=_KPN_PROPLAN_EVIDENCE,
            )
        ],
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KANGAROO_PROPLAN_RETAIL_BASIS_VARIES",
                condition="Some wet-can rows print retail as case and per-can values while supply price is per pack.",
                review_guidance="Create row fixtures for dry, supplement, and wet-can rows before promotion to SUPPORTED.",
                blocks_supported_status=True,
            )
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "description",
            "packs_per_case",
            "pack_size",
            "cost",
            "rrp",
            "effective_date",
        ),
        created_at=DECLARATION_CREATED_AT,
        created_by=DECLARATION_CREATED_BY,
    )
)


KANGAROO_EARTHZ_PET_PRICE_SHEET_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kangaroo.earthz_pet_price_sheet.v1",
        contract_version="v1",
        supplier=_KPN_SUPPLIER,
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Earthz Pet image-only price sheet",
        source_format=SourceFormat.PDF,
        support_status=SupplierContractSupportStatus.UNVERIFIED,
        evidence=_KPN_EARTHZ_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF,
            expected_sections=["Earthz Pet price rows"],
            table_regions=[
                SourceTableRegion(
                    name="earthz_visual_price_rows",
                    selector="Image-only visual table with sku#, wholesale price, recommended retail price, size, and pack-count text",
                    notes="pdftotext produced no text; OCR/vision extraction is required for runtime interpretation.",
                )
            ],
            required_headers=["sku#", "批發價", "建議零售價"],
            optional_headers=["size", "pack count", "promotion text"],
            row_eligibility_rules=["Rows require OCR/vision extraction; no text-layer row parser can be assumed."],
            source_location_expectations=["page number", "visual row group", "bounding box"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="sku#",
                description="Earthz visual SKU column.",
                evidence=_KPN_EARTHZ_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_path="visual product heading",
                description="Earthz product/flavour heading.",
                evidence=_KPN_EARTHZ_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="visual size and pack-count text",
                description="Size such as 35ml/50ml and pack text such as 5-bottle pack.",
                evidence=_KPN_EARTHZ_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="cost",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價",
                description="Wholesale price column.",
                evidence=_KPN_EARTHZ_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="建議零售價",
                description="Recommended retail price column.",
                evidence=_KPN_EARTHZ_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="visual footer",
                description="Visual promotion text, observed as buy 9 get 1.",
                evidence=_KPN_EARTHZ_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="cost",
            rrp_source_field="rrp",
            price_basis=None,
            price_basis_status=SemanticResolutionStatus.UNRESOLVED,
            notes="The image shows wholesale/retail prices, but whether the price is per bottle or pack is not machine-verified.",
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat ml values as content measure.",
                "Do not treat 5-bottle pack text as order multiple without supplier confirmation.",
            ],
            unresolved_semantics=["Price basis, order increment, and break-pack permission are unresolved."],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["buy quantity get free quantity"],
            benefit_patterns=["free quantity"],
            requires_validation_issue_when=["The qualifying scope and mix-and-match rules are not explicit."],
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KANGAROO_EARTHZ_IMAGE_ONLY",
                condition="The source PDF has no text layer and requires OCR/vision extraction.",
                review_guidance="Create OCR/vision fixtures with bounding boxes and confirm price basis before promotion.",
                blocks_supported_status=True,
            )
        ],
        pipeline_mapping=pipeline_mapping("supplier_sku", "description", "pack_size", "cost", "rrp", "promotion_text"),
        created_at=DECLARATION_CREATED_AT,
        created_by=DECLARATION_CREATED_BY,
    )
)

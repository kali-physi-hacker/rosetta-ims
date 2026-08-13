"""Vetapet supplier-source contract declarations."""

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
    fields = [
        SourceFieldContract(
            field_key="supplier_sku",
            role=SourceFieldRole.SUPPLIER_SKU,
            requirement=SourceFieldRequirement.REQUIRED,
            source_column="CODE NO / 編號",
            aliases=["CODE NO", "編號", "CODE"],
            description=(
                "Vetapet code number. Bare aliases cover the retail sections that print "
                "編號 alone (letter-spaced 編 號 matches via CJK-space-insensitive folding) "
                "and the variant tables that print CODE. 'Code No.' (with the period) is "
                "deliberately NOT an alias: that heading belongs to the code→category "
                "legend sidebars, which are reference boxes, not product rows."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="description",
            role=SourceFieldRole.PRODUCT_NAME,
            requirement=SourceFieldRequirement.REQUIRED,
            source_column="PRODUCT NAME / 產品名稱",
            aliases=["PRODUCT NAME", "產品名稱", "產品"],
            description=(
                "Printed product name. Retail sections print 產品 alone (letter-spaced "
                "產 品 in the source; matched via CJK-space-insensitive folding). Variant "
                "tables (e.g. Ferplast beds) name the product only in the banner above the "
                "table and are expected to flag this field for review."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="brand",
            role=SourceFieldRole.BRAND,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_path="product_name or section_header",
            description="Product brand extracted from product name (e.g., Zoetis, Antinol, Dermoscent) or section header.",
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="pack_size",
            role=SourceFieldRole.PACKAGING,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="PACKING PER UNIT" if segment == "vet" else "重量 / SIZE",
            aliases=(
                ["PACKING PER UNIT", "SIZE", "PACK", "重量", "包裝"]
                if segment == "vet"
                else ["重量", "SIZE", "包裝"]
            ),
            description=(
                "Raw size/packaging text; may express content measure, pack description, or "
                "both. The vet sections print it as PACKING PER UNIT (verified on the "
                "sample's diagnostics pages); retail sections use 重量/SIZE."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="cost",
            role=SourceFieldRole.SOURCE_PRICE,
            requirement=SourceFieldRequirement.REQUIRED,
            source_column="WHOLESALE PRICE / 批發價" if segment == "vet" else "批發價 / WHOLESALE PRICE",
            aliases=(
                # Deliberately NO bare 批發價 alias for the vet segment: only the
                # retail sections print it alone, and matching it here would let
                # this contract claim retail rows under a UNIT basis nobody has
                # verified — the non_vet contract owns those rows.
                ["WHOLESALE PRICE", "UNIT PRICE", "PRICE", "UNIT PRICE PER TEST"]
                if segment == "vet"
                else ["批發價", "WHOLESALE PRICE"]
            ),
            description=(
                "Supplier cost. The vet sections price by UNIT PRICE / PRICE — verified on "
                "the sample that UNIT PRICE is the price of one ORDER UNIT (a box/set/pc "
                "named per row), not one test; see packaging.purchase_uom_source_field. "
                "Retail sections print 批發價 (letter-spaced in print; matched via "
                "CJK-space-insensitive folding)."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="rrp",
            role=SourceFieldRole.RRP,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="SUGGESTED RETAIL PRICE / RETAIL PRICE / 零售價" if segment == "vet" else "零售價 / RETAIL PRICE",
            aliases=["SUGGESTED RETAIL PRICE", "RETAIL PRICE", "零售價", "建議零售價"],
            description="Suggested retail or retail price field; retail sections print 建議零售價.",
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="promotion_text",
            role=SourceFieldRole.MBB_TEXT,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="TERMS / REMARKS",
            aliases=["TERMS", "REMARKS"],
            description=(
                "Bulk buy terms, promotional offers, or discount conditions printed as row "
                "columns (e.g., 'Buy 20+ at special price', 'Buy 3 get 1 free'). PAGE-level "
                "promotions (banner text like 'Mix over $1000, 10% off' or brand-scoped "
                "'混合12件 9折') land in text_observations, which no contract field can "
                "reach today — see known_ambiguities."
            ),
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
    if segment == "vet":
        fields.append(
            SourceFieldContract(
                field_key="order_unit",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="ORDER UNIT",
                description=(
                    "The unit one UNIT PRICE buys — '1 box', '1 set', '1 pc', printed per "
                    "row. Verified on the sample: '10 tests / box · 1 box · HK$1958' prices "
                    "the BOX, not the test. Declared as packaging.purchase_uom_source_field "
                    "so the cost's price basis follows this value where it is readable."
                ),
                evidence=evidence_items,
            )
        )
    else:
        fields.append(
            SourceFieldContract(
                field_key="variant",
                role=SourceFieldRole.VARIANT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="unlabeled_column",
                description=(
                    "Colour/variant label printed in an unlabeled first column on variant "
                    "tables (e.g. Ferplast beds: 黑色/灰色/杏色 each with its own CODE). "
                    "Claimed via the unlabeled_column sentinel — resolves only when the row "
                    "carries exactly one non-empty unlabeled value."
                ),
                evidence=evidence_items,
            )
        )
    return fields


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
            null_cost_markers=["By Quote"],
            notes=(
                "Verified on the sample: UNIT PRICE sections price one ORDER UNIT (a "
                "box/set/pc named per row), so the basis follows the order_unit field where "
                "readable (packaging.price_basis_follows_purchase_unit) and falls back to "
                "UNIT for the PRICE/Wholesale layouts that print no order-unit column. "
                "Analyzers and instruments print 'By Quote' instead of a price."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            price_basis=UnitOfMeasure(code=UnitCode.UNIT),
            purchase_uom_source_field="order_unit",
            price_basis_follows_purchase_unit=True,
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat kg/g/ml size text as content measure, not sellable-unit count.",
                "Pack descriptions such as tubes/pack are not proof of supplier order multiple without explicit terms.",
                "ORDER UNIT names what one UNIT PRICE buys; PACKING PER UNIT describes what is inside it.",
            ],
            unresolved_semantics=[
                "Order increment and break-pack rules are not proven by checked-in source evidence.",
                "PRICE-column layouts print no order-unit column; their UNIT fallback basis is not per-row verified.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["buy quantity threshold", "spend threshold"],
            benefit_patterns=["free units", "percentage discount", "fixed price per unit"],
            requires_validation_issue_when=[
                "The qualifying products or exact threshold basis are not explicit.",
                "Stacking rules or exclusions are not defined."
            ],
            notes="Vetapet uses various MBB formats including 'Buy X get Y free', 'Buy X+ at special price', and percentage discounts.",
        ),
        validation_rules=_VET_VALIDATION_RULES,
        known_ambiguities=[
            AmbiguityRule(
                issue_code="VETAPET_VET_MULTIPLE_TABLE_LAYOUTS",
                condition=(
                    "The supplied Vetapet PDF contains Unit Price tables (with ORDER UNIT "
                    "columns), bare PRICE tables (without), and Wholesale/Retail/Terms "
                    "tables. All are now declared via cost aliases with a per-row order-unit "
                    "basis where printed; the PRICE layouts fall back to an unverified UNIT "
                    "basis."
                ),
                review_guidance=(
                    "Confirm with BizOps that PRICE-column layouts price one order unit "
                    "(box/pc) like the UNIT PRICE layouts, or per sellable unit — the "
                    "fallback basis is a declaration, not source-proven."
                ),
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="VETAPET_VET_SPECIAL_OFFER_STRUCK_PRICES",
                condition=(
                    "Some vet pages print 'Special Offer: N% off' bands with the original "
                    "price struck through and a badge price beside it (sample page 20: "
                    "HK$1056 struck, $739 badge). Which amount lands in the UNIT PRICE "
                    "column of the extracted evidence depends on the render; the offer is "
                    "time-limited and neither amount is flagged as promotional in the row."
                ),
                review_guidance=(
                    "Rows from special-offer bands need review before the cost is trusted: "
                    "decide whether standard cost is the struck price with the offer as an "
                    "MBB-style term, or the offer price outright."
                ),
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="VETAPET_PAGE_BANNER_PROMOTIONS_UNREACHABLE",
                condition=(
                    "Order- and brand-scoped promotions print as page banners ('Promotion: "
                    "Mix over $1000, 10% off'; '混合12件 9折 24件 8折'), which extraction "
                    "stores as page-level text_observations — no contract field mechanism "
                    "reaches those today, so they are preserved as evidence but never "
                    "attached to rows."
                ),
                review_guidance=(
                    "Needs a shared mechanism (like supplier_identity_text) threading page "
                    "text into row metadata, or reviewer awareness that banner promos live "
                    "in the run's unmapped text evidence."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping("supplier_sku", "description", "brand", "pack_size", "cost", "rrp", "promotion_text", "species", "segment", "category", "order_unit"),
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
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["buy quantity threshold", "spend threshold"],
            benefit_patterns=["free units", "percentage discount", "fixed price per unit"],
            requires_validation_issue_when=[
                "The qualifying products or exact threshold basis are not explicit.",
                "Stacking rules or exclusions are not defined."
            ],
            notes="Vetapet uses various MBB formats including 'Buy X get Y free', 'Buy X+ at special price', and percentage discounts.",
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
        pipeline_mapping=pipeline_mapping("supplier_sku", "description", "brand", "pack_size", "cost", "rrp", "promotion_text", "species", "segment", "category", "variant"),
        created_at=DECLARATION_CREATED_AT,
        created_by=DECLARATION_CREATED_BY,
    )
)

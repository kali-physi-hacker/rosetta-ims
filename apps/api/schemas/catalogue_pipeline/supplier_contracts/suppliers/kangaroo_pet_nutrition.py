"""Kangaroo Pet Nutrition supplier-source contract declarations."""

from __future__ import annotations

from datetime import datetime, timezone

from schemas.catalogue_pipeline.common import UnitOfMeasure
from schemas.catalogue_pipeline.enums import SourceFormat, UnitCode
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
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="brand, portfolio, or product-line heading",
                description=(
                    "ZIWI Peak or the Ecuphar portfolio brand. OPTIONAL and currently "
                    "unmappable: verified against the sample that brands appear only as "
                    "page-logo IMAGES, and the table section banners read '價錢表 Pricelist' — "
                    "mapping section_header here would emit 'Pricelist' as a brand, which is "
                    "worse than nothing. REQUIRED previously guaranteed 100% of rows failed."
                ),
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
                # role OTHER, not PACKAGING: two fields sharing pack_size's role
                # let this value silently fill the pack_size slot when pack_size
                # failed to resolve (proven on kpn_trading's frozen-raw rows).
                role=SourceFieldRole.OTHER,
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
                aliases=[
                    "批發價 (HKD)",
                    "批發價 (HKD) 每箱 (12罐)",
                    "批發價 (HKD) 每箱*",
                    "每包批發價",
                    "每箱批發價",
                    "Wholesale Price",
                ],
                description=(
                    "Wholesale amount preserved with its exact printed source heading. The "
                    "sample prints '批發價 (HKD)' with a currency suffix on the ZIWI pages "
                    "and case-scoped variants on the wet-food pages."
                ),
                evidence=_KANGAROO_PET_NUTRITION_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="建議零售價",
                aliases=[
                    "建議零售價 (HKD)",
                    "建議零售價 (HKD) 每罐",
                    "零售價",
                    "每包建議零售價",
                    "每箱建議零售價",
                    "每罐建議零售價",
                ],
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
            "superseded_by_layout_specific_contracts": (
                "kangaroo_pet_nutrition.unit_price_list.v1, "
                "kangaroo_pet_nutrition.case_only_price_list.v1"
            ),
            "layout_specific_contracts_note": (
                "This bundle remains the fallback for un-sorted layouts. Once a page's "
                "layout is identified, prefer the layout-specific contract above, which "
                "has a resolved price_basis."
            ),
        },
    )
)


# ─────────────────────────────────────────────────────────────────────────
# Layout-specific contracts.
#
# Full read of the 9 Kangaroo Pet Nutrition pages in KPN_Kangaroo.pdf (5-10,
# 36-38; every page footed 袋鼠寵物營養有限公司 Kangaroo Pet Nutrition Ltd.)
# found exactly four printed layouts in two resolvable groups:
#
#   UNIT       — ZIWI dry/treat pages ('批發價 (HKD)') and Ecuphar pages
#                (bare '批發價'): exactly one wholesale amount per row, no
#                case-level column anywhere. The price buys one sellable
#                unit (a bag, a strip pack).
#   CASE-ONLY  — ZIWI wet-can pages: wholesale printed ONLY at case level
#                ('批發價 (HKD) 每箱 (12罐)' / '每箱*'), with a printed
#                derived per-can average beside it ('$340 (@28.3)' — 28.3 is
#                exactly 340/12) and RRP at both case and per-can level. No
#                per-can wholesale exists to fall back on.
# ─────────────────────────────────────────────────────────────────────────

_KANGAROO_PN_UNIT_EVIDENCE = [
    *_KANGAROO_PET_NUTRITION_EVIDENCE,
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf#pages=5,7,9-10,36-38",
        (
            "Every layout in this group prints exactly one wholesale amount per row "
            "('批發價 (HKD)' on ZIWI dry/treat pages, bare '批發價' on Ecuphar pages) "
            "with no case-level price column anywhere."
        ),
    ),
]

KANGAROO_PET_NUTRITION_UNIT_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kangaroo_pet_nutrition.unit_price_list.v1",
        contract_version="v1",
        supplier=_KANGAROO_PET_NUTRITION_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="Kangaroo Pet Nutrition unit-basis price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_KANGAROO_PN_UNIT_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="kangaroo_pn_unit_price_sections",
                    selector=(
                        "Kangaroo Pet Nutrition sections whose price columns print exactly "
                        "one wholesale amount, with no case-level price column."
                    ),
                    notes="Observed on ZIWI dry/treat pages and Ecuphar pages.",
                )
            ],
            required_headers=[],
            optional_headers=[
                "產品編號", "產品內容", "包裝", "重量",
                "批發價 (HKD)", "建議零售價 (HKD)",
                "產品圖示", "批發價", "零售價",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 81, or the enclosing source section "
                    "must explicitly identify Kangaroo Pet Nutrition / KANGAR."
                ),
                "Select this contract only when the row's table prints one wholesale amount and no case-level price column.",
                "Rows require a product code, description, and printed wholesale price.",
            ],
            source_location_expectations=[
                "source document and page",
                "supplier identity marker or ingestion supplier identity",
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
                aliases=["Product Code"],
                description="Product code printed on the eligible row.",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品內容",
                aliases=["產品名稱", "Product Description"],
                description="Printed English/Chinese product description.",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="brand page logo (image only; not extractable text)",
                description=(
                    "ZIWI Peak / Ecuphar appear only as page-logo images; section banners "
                    "read '價錢表 Pricelist'. Deliberately unmapped rather than wrong."
                ),
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="重量",
                aliases=["包裝", "Size"],
                description="Printed content weight/size (e.g. '454g', '1kg').",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價 (HKD)",
                aliases=["批發價", "Wholesale Price"],
                description="The single printed wholesale amount — one sellable unit.",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="建議零售價 (HKD)",
                aliases=["零售價", "建議零售價"],
                description="Recommended retail amount, same basis as wholesale_price.",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document or section effective-date label",
                description="'With effect from ...' / '生效日期' page labels.",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document, section, or row promotion notes",
                description="Printed promotion or spend-discount terms.",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="wholesale_price",
            rrp_source_field="rrp",
            price_basis=UnitOfMeasure(code=UnitCode.UNIT),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "Verified directly against every page in this group: exactly one wholesale "
                "amount is printed per row and no case-level column exists to compete with "
                "it. Safe to treat as the sellable-unit price."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat content weight/size as a measure, not a sellable-unit count.",
            ],
            unresolved_semantics=[
                "Order increment and break-pack rules are not stated by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["buy quantity", "spend threshold"],
            benefit_patterns=["percentage discount"],
            requires_validation_issue_when=[
                "The qualifying products, threshold basis, or stacking rules are not explicit."
            ],
            notes=(
                "The ZIWI pages print standing offers as page banners (4% off the "
                "air-dried/steam-dried ranges; 8% over $4,000 spend) — banner text lands in "
                "text_observations, which no contract field reaches today."
            ),
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KANGAROO_PN_UNIT_SUPPLIER_IDENTITY_REQUIRED",
                condition="A source may contain multiple suppliers or only a subset of previously observed brands.",
                review_guidance=(
                    "Select this declaration only from ingestion supplier ID 81 or an explicit "
                    "Kangaroo Pet Nutrition / KANGAR source marker; never from page position or brand."
                ),
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="KANGAROO_PN_PAGE_BANNER_PROMOTIONS_UNREACHABLE",
                condition=(
                    "Standing mix/spend promotions print as page banners captured in "
                    "text_observations; no contract field mechanism reaches those today."
                ),
                review_guidance=(
                    "Reviewers must read the run's unmapped text evidence for banner promos "
                    "until a threading mechanism exists."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "description",
            "brand",
            "pack_size",
            "wholesale_price",
            "rrp",
            "effective_date",
            "promotion_text",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
        metadata={
            "routing_strategy": "supplier_identity_and_layout_markers",
            "sample_reference": "KPN_Kangaroo.pdf",
            "price_basis_group": "UNIT",
        },
    )
)


_KANGAROO_PN_CASE_EVIDENCE = [
    *_KANGAROO_PET_NUTRITION_EVIDENCE,
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf#pages=6,8",
        (
            "Wet-can pages print wholesale ONLY at case level ('批發價 (HKD) 每箱 (12罐)' "
            "and '每箱*', the asterisk footnoted '85g貓罐 每箱24罐 / 185g貓罐 每箱12罐'), "
            "with a printed derived per-can average beside the amount ('$340 (@28.3)'; "
            "28.3 = 340/12) and RRP printed at both case and per-can level. No per-can "
            "wholesale figure exists anywhere."
        ),
    ),
]

KANGAROO_PET_NUTRITION_CASE_ONLY_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kangaroo_pet_nutrition.case_only_price_list.v1",
        contract_version="v1",
        supplier=_KANGAROO_PET_NUTRITION_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="Kangaroo Pet Nutrition case-only price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_KANGAROO_PN_CASE_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="kangaroo_pn_case_only_sections",
                    selector=(
                        "Kangaroo Pet Nutrition wet-food sections whose ONLY wholesale amount "
                        "is case-level, with per-can RRP printed separately."
                    ),
                    notes="Observed on the ZIWI wet dog-can and cat-can pages.",
                )
            ],
            required_headers=[],
            optional_headers=[
                "產品編號", "產品內容", "包裝", "重量",
                "批發價 (HKD) 每箱 (12罐)", "批發價 (HKD) 每箱*",
                "建議零售價 (HKD) 每箱 (12罐)", "建議零售價 (HKD) 每箱",
                "建議零售價 (HKD) 每罐",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 81, or the enclosing source section "
                    "must explicitly identify Kangaroo Pet Nutrition / KANGAR."
                ),
                "Select this contract only when wholesale is printed at case level with no per-can wholesale figure present.",
                "Rows require a product code, description, and printed wholesale price.",
            ],
            source_location_expectations=[
                "source document and page",
                "supplier identity marker or ingestion supplier identity",
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
                aliases=["Product Code"],
                description="Product code printed on the eligible row.",
                evidence=_KANGAROO_PN_CASE_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品內容",
                aliases=["產品名稱", "Product Description"],
                description="Printed English/Chinese product description.",
                evidence=_KANGAROO_PN_CASE_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="brand page logo (image only; not extractable text)",
                description="ZIWI Peak appears only as a page-logo image; deliberately unmapped rather than wrong.",
                evidence=_KANGAROO_PN_CASE_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="重量",
                aliases=["包裝", "Size"],
                description="Printed per-can content weight (e.g. '170g', '85g / 3oz').",
                evidence=_KANGAROO_PN_CASE_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價 (HKD) 每箱 (12罐)",
                aliases=["批發價 (HKD) 每箱*", "批發價 每箱"],
                description=(
                    "The only printed wholesale amount — always case-level. The printed "
                    "'(@N)' beside it is the derived per-can average (case total divided by "
                    "can count) and is stripped as annotation, never treated as a price."
                ),
                evidence=_KANGAROO_PN_CASE_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="建議零售價 (HKD) 每罐",
                aliases=["每罐建議零售價"],
                description=(
                    "Preferred RRP is the per-can figure, which the source prints separately "
                    "and more granularly than the case RRP."
                ),
                evidence=_KANGAROO_PN_CASE_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document or section effective-date label",
                description="'With effect from ...' page labels.",
                evidence=_KANGAROO_PN_CASE_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document, section, or row promotion notes",
                description="Printed promotion or spend-discount terms.",
                evidence=_KANGAROO_PN_CASE_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="wholesale_price",
            rrp_source_field="rrp",
            price_basis=UnitOfMeasure(code=UnitCode.CASE),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "Verified directly: the only printed wholesale amount is case-level. The "
                "printed per-can '(@N)' average is derived (case total / can count) and is "
                "never promoted to a price — computing or trusting it would assert a per-can "
                "wholesale the source does not state."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat per-can weight as a measure, not a sellable-unit count.",
                "Can-per-case count lives in the column HEADING ('每箱 (12罐)') or the page footnote, never in a row cell.",
            ],
            unresolved_semantics=[
                "Break-pack permission is not stated by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["buy quantity", "spend threshold"],
            benefit_patterns=["percentage discount"],
            requires_validation_issue_when=[
                "The qualifying products, threshold basis, or stacking rules are not explicit."
            ],
            notes="Banner promotions land in text_observations; see known_ambiguities.",
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KANGAROO_PN_CASE_SUPPLIER_IDENTITY_REQUIRED",
                condition="A source may contain multiple suppliers or only a subset of previously observed brands.",
                review_guidance=(
                    "Select this declaration only from ingestion supplier ID 81 or an explicit "
                    "Kangaroo Pet Nutrition / KANGAR source marker; never from page position or brand."
                ),
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="KANGAROO_PN_CASE_COUNT_NOT_A_COLUMN",
                condition=(
                    "Cans-per-case is printed in the column HEADING ('每箱 (12罐)') on the "
                    "dog-can page and in a FOOTNOTE on the cat-can page ('85g貓罐 每箱24罐 / "
                    "185g貓罐 每箱12罐', varying BY ROW weight) — never as a row cell, so no "
                    "contract field can map it and per-unit cost cannot be derived "
                    "deterministically."
                ),
                review_guidance=(
                    "BizOps must confirm can counts per SKU (heading/footnote values are the "
                    "evidence) before per-can costs are computed downstream."
                ),
                blocks_supported_status=True,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "description",
            "brand",
            "pack_size",
            "wholesale_price",
            "rrp",
            "effective_date",
            "promotion_text",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
        metadata={
            "routing_strategy": "supplier_identity_and_layout_markers",
            "sample_reference": "KPN_Kangaroo.pdf",
            "price_basis_group": "CASE",
        },
    )
)


# ─────────────────────────────────────────────────────────────────────────
# Vet-clinic price list — the layout behind the BizOps golden sheet's
# "Kangaroo" rows (NexGard Spectra sizes, Heartgard Plus). Printed inside
# the Kangaroo half of the combined KPN_Kangaroo document under the banner
# 'Price List (Vet Clinics only)': bare HKD amounts (no currency symbol),
# no retail column, and a per-row 診所優惠 Clinic Offer column carrying
# free-goods terms ('10+2 OR 10+3 (單次購買50或以上) (50 or more per order)').
# The legacy kangaroo.mixed_price_catalogue.v1 cannot read this layout —
# its required headers (產品名稱, 建議零售價) are not printed here — so per
# the layout-split doctrine this layout gets its own contract.
# ─────────────────────────────────────────────────────────────────────────

_KANGAROO_PN_VET_CLINIC_EVIDENCE = [
    *_KANGAROO_PET_NUTRITION_EVIDENCE,
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo-updated.pdf#section=Price List (Vet Clinics only)",
        (
            "The vet-clinic tables print 產品編號 Product Code / 產品內容 Product "
            "Description / 包裝 Packing / 批發價 W/S Price (HKD) / 診所優惠 Clinic "
            "Offer. Amounts are bare numerals; no retail column exists; offers are "
            "free-goods terms per row."
        ),
    ),
]

KANGAROO_PET_NUTRITION_VET_CLINIC_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kangaroo_pet_nutrition.vet_clinic_price_list.v1",
        contract_version="v1",
        supplier=_KANGAROO_PET_NUTRITION_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="Kangaroo Pet Nutrition vet-clinic price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_KANGAROO_PN_VET_CLINIC_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            expected_sections=["Price List (Vet Clinics only)"],
            table_regions=[
                SourceTableRegion(
                    name="kangaroo_pn_vet_clinic_tables",
                    selector=(
                        "'Price List (Vet Clinics only)' tables with a 診所優惠 Clinic "
                        "Offer column and no retail price column."
                    ),
                    notes="Observed for NexGard Spectra and Heartgard Plus rows.",
                )
            ],
            required_headers=[],
            optional_headers=[
                "產品圖片", "產品編號", "產品內容", "包裝",
                "批發價 W/S Price (HKD)", "診所優惠",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 81, or the enclosing source section "
                    "must explicitly identify Kangaroo Pet Nutrition / KANGAR."
                ),
                "Select this contract only for 'Price List (Vet Clinics only)' tables carrying a Clinic Offer column.",
                "Rows require a product code, description, and printed wholesale amount.",
            ],
            source_location_expectations=[
                "source document and page",
                "supplier identity marker or ingestion supplier identity",
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
                aliases=["Product Code"],
                description="Product code printed on the eligible row (e.g. BI-NXS).",
                evidence=_KANGAROO_PN_VET_CLINIC_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品內容",
                aliases=["Product Description"],
                description="Printed product description; the brand line (NexGard SPECTRA, Heartgard PLUS) is embedded in this text, not a separate column.",
                evidence=_KANGAROO_PN_VET_CLINIC_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="brand text embedded in the description column",
                description=(
                    "No brand column exists; the brand prefix lives inside 產品內容. "
                    "Deliberately unmapped rather than derived by splitting text."
                ),
                evidence=_KANGAROO_PN_VET_CLINIC_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="包裝",
                aliases=["Packing"],
                description="Printed packing notation (e.g. \"1x3's\" — one pack of three chewables; '100ml').",
                evidence=_KANGAROO_PN_VET_CLINIC_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價 W/S Price (HKD)",
                aliases=["批發價", "W/S Price"],
                description="Bare HKD numeral (e.g. '260') — the per-pack wholesale amount.",
                evidence=_KANGAROO_PN_VET_CLINIC_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="clinic_offer",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="診所優惠",
                aliases=["Clinic Offer"],
                description=(
                    "Per-row free-goods terms, e.g. '10+2 OR 10+3 (單次購買50或以上) "
                    "(50 or more per order)' — buy 10 get 2 free, or 10 get 3 free at "
                    "50+ per order."
                ),
                evidence=_KANGAROO_PN_VET_CLINIC_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document or section effective-date label",
                description="'With effect from ...' / '生效日期' page labels.",
                evidence=_KANGAROO_PN_VET_CLINIC_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="wholesale_price",
            rrp_source_field=None,
            price_basis=UnitOfMeasure(code=UnitCode.PACK),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "VERIFIED against the BizOps golden sheet itself "
                "(golden_samples_by_supplier/kangaroo.csv): the sheet's hand-filled "
                "rows declare basis PACK/qty 1 for every covered SKU, and every "
                "printed amount matches its per-PACK cost exactly (175/260/280/300/"
                "330). The hand-filled sheet is the human statement of the basis; "
                "the golden e2e run proved the contract reproduces it. Promotion "
                "decision: user sign-off, 2026-08-13."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "\"1x3's\" means one sellable pack containing three tablets/chewables.",
            ],
            unresolved_semantics=[
                "Order increment and break-pack rules are not stated by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["clinic_offer"],
            condition_patterns=["buy quantity", "order-size threshold"],
            benefit_patterns=["free goods (N+M)"],
            requires_validation_issue_when=[
                "The qualifying products, threshold basis, or stacking rules are not explicit."
            ],
            notes=(
                "Offers are free-goods tiers ('10+2', '10+3 at 50+ per order', "
                "'10+1 OR 20+3'), not price tiers — no structured recognizer parses "
                "N+M free goods today, so the text routes to review verbatim."
            ),
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KANGAROO_PN_VET_CLINIC_SUPPLIER_IDENTITY_REQUIRED",
                condition="A source may contain multiple suppliers or only a subset of previously observed brands.",
                review_guidance=(
                    "Select this declaration only from ingestion supplier ID 81 or an explicit "
                    "Kangaroo Pet Nutrition / KANGAR source marker; never from page position or brand. "
                    "Downgraded from blocking at promotion (2026-08-13): supplier 81 has many "
                    "contracts so the runtime demands an explicit contract_id whenever more than "
                    "one is SUPPORTED, the belongs-to-supplier check refuses mismatched "
                    "submissions, and CONTRACT_SUPPLIER_IDENTITY_MISMATCH verifies captured "
                    "identity text automatically where the source provides it."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="KANGAROO_PN_VET_CLINIC_FREE_GOODS_NOT_STRUCTURED",
                condition=(
                    "診所優惠 offers are N+M free-goods terms; no structured MBB recognizer "
                    "handles free goods, so they surface as text-only terms."
                ),
                review_guidance=(
                    "Reviewers must read the clinic-offer text and apply the free-goods "
                    "terms manually until a free-goods recognizer exists."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "description",
            "brand",
            "pack_size",
            "wholesale_price",
            "clinic_offer",
            "effective_date",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
        metadata={
            "routing_strategy": "supplier_identity_and_layout_markers",
            "sample_reference": "KPN_Kangaroo-updated.pdf",
            "price_basis_group": "PACK",
            "golden_sheet": "golden_samples_by_supplier/kangaroo.csv",
        },
    )
)

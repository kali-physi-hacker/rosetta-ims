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
                requirement=SourceFieldRequirement.OPTIONAL,  # name never blocks a row (user ruling 2026-08-25)
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
                "kangaroo_pet_nutrition.unit_price_list.v1 (merged: unit + case-only + vet-clinic)"
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
# THE Kangaroo Pet Nutrition contract (merged 2026-08-25, user ruling:
# "combine the case_only_price, unit_price_list, vet_clinic_price_list all
# to one" — the same one-document-one-contract doctrine as KPN Trading's
# pack/case merge of 2026-08-17).
#
# One document family, three printed layouts, three price bases:
#
#   UNIT — ZIWI dry/treat pages ('批發價 (HKD)') and Ecuphar pages (bare
#          '批發價'): one wholesale amount per row buying one sellable unit.
#   CASE — ZIWI wet-can pages: wholesale printed ONLY per case
#          ('批發價 (HKD) 每箱 (12罐)' / '每箱*', captured with or without
#          the spaces), a derived per-can average beside it ('$340 (@28.3)')
#          that is annotation, never a price, and per-can RRP.
#   PACK — 'Price List (Vet Clinics only)' tables: bare HKD per-pack
#          amounts, no retail column, a 診所優惠 Clinic Offer column with
#          free-goods terms.
#
# Each layout's price column is its own OPTIONAL field declaring the basis
# its printed amount is on; the first-resolved column supplies the row's
# cost AND its basis. A row matching none of them holds as missing its
# required price.
# ─────────────────────────────────────────────────────────────────────────

_KANGAROO_PN_UNIT_EVIDENCE = [
    *_KANGAROO_PET_NUTRITION_EVIDENCE,
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf#pages=5,7,9-10,36-38",
        (
            "The unit-basis layouts print exactly one wholesale amount per row "
            "('批發價 (HKD)' on ZIWI dry/treat pages, bare '批發價' on Ecuphar pages) "
            "with no case-level price column anywhere."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf#pages=6,8",
        (
            "Wet-can pages print wholesale ONLY at case level ('批發價 (HKD) 每箱 (12罐)' "
            "and '每箱*', the asterisk footnoted '85g貓罐 每箱24罐 / 185g貓罐 每箱12罐'), "
            "with a printed derived per-can average beside the amount ('$340 (@28.3)'; "
            "28.3 = 340/12) and RRP printed at both case and per-can level. No per-can "
            "wholesale figure exists anywhere. Vision captures these headings without "
            "spaces before the parens ('批發價(HKD) 每箱(12罐)', run 1382e559)."
        ),
    ),
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

KANGAROO_PET_NUTRITION_UNIT_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kangaroo_pet_nutrition.unit_price_list.v1",
        contract_version="v1",
        supplier=_KANGAROO_PET_NUTRITION_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        # The id keeps its history; the format is the whole document. Merged
        # 2026-08-25 from unit_price_list + case_only_price_list +
        # vet_clinic_price_list per the one-document-one-contract ruling.
        format_name="Kangaroo Pet Nutrition price list",
        source_format=SourceFormat.PDF_TABLE,
        # SUPPORTED 2026-08-17 (unit layouts, six Ziwi pages, kangaroo_ziwi
        # golden set); case layout verified 2026-08-25 against all 24 wet-can
        # observations of run 1382e559 (24/24 conform at CASE basis); vet
        # layout verified 2026-08-13 against the BizOps golden sheet
        # (kangaroo_vet_clinic golden set).
        support_status=SupplierContractSupportStatus.SUPPORTED,
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
                ),
                SourceTableRegion(
                    name="kangaroo_pn_case_only_sections",
                    selector=(
                        "Kangaroo Pet Nutrition wet-food sections whose ONLY wholesale amount "
                        "is case-level, with per-can RRP printed separately."
                    ),
                    notes="Observed on the ZIWI wet dog-can and cat-can pages.",
                ),
                SourceTableRegion(
                    name="kangaroo_pn_vet_clinic_tables",
                    selector=(
                        "'Price List (Vet Clinics only)' tables with a 診所優惠 Clinic "
                        "Offer column and no retail price column."
                    ),
                    notes="Observed for NexGard Spectra and Heartgard Plus rows.",
                ),
            ],
            required_headers=[],
            optional_headers=[
                "產品編號", "產品內容", "包裝", "重量",
                "批發價 (HKD)", "建議零售價 (HKD)",
                "產品圖示", "產品圖片", "批發價", "零售價",
                "批發價 (HKD) 每箱 (12罐)", "批發價 (HKD) 每箱*",
                "建議零售價 (HKD) 每箱 (12罐)", "建議零售價 (HKD) 每箱",
                "建議零售價 (HKD) 每罐",
                "批發價(HKD) 每箱(12罐)", "批發價(HKD) 每箱*",
                "建議零售價(HKD) 每箱(12罐)", "建議零售價(HKD) 每罐",
                "批發價 W/S Price (HKD)", "診所優惠",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 81, or the enclosing source section "
                    "must explicitly identify Kangaroo Pet Nutrition / KANGAR."
                ),
                "Rows require a product code, a description, and a printed wholesale price under any of the declared price columns.",
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
                requirement=SourceFieldRequirement.OPTIONAL,  # name never blocks a row (user ruling 2026-08-25)
                source_column="產品內容",
                aliases=["產品名稱", "Product Description"],
                description="Printed English/Chinese product description.",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                requirement=SourceFieldRequirement.OPTIONAL,
                # page_brand since 2026-08-17: the current per-page capture
                # transcribes the page wordmark ('Ziwi Peak', 'Ziwi Peak Steam
                # Dried 柔蒸溫乾…') into page_brand_text — the logo is no longer
                # image-only. Section banners still read '價錢表 Pricelist' and
                # stay unmapped.
                source_path="page_brand",
                description="The brand wordmark heading the page, verbatim.",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="重量",
                aliases=["包裝", "Size", "Packing"],
                description=(
                    "Printed content weight/size or packing notation — '454g' on unit "
                    "pages, '170g' per can on wet pages, \"1x3's\" / '100ml' on vet rows."
                ),
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            # One price column PER LAYOUT, each OPTIONAL and declaring the
            # basis its printed amount is on. Exactly one family prints on any
            # given row (the heading families never collide), the first
            # resolved supplies the row's cost with its basis, and a row
            # matching none holds as CONTRACT_REQUIRED_FIELD_MISSING.
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="批發價 (HKD)",
                aliases=["批發價", "Wholesale Price"],
                price_basis=UnitOfMeasure(code=UnitCode.UNIT),
                description="Unit layouts: the single printed wholesale amount — one sellable unit.",
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="case_wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="批發價 (HKD) 每箱 (12罐)",
                aliases=[
                    "批發價 (HKD) 每箱*",
                    "批發價 每箱",
                    # The same headings as vision captures them — no space
                    # before the parenthesised qualifiers (run 1382e559).
                    "批發價(HKD) 每箱(12罐)",
                    "批發價(HKD) 每箱*",
                ],
                price_basis=UnitOfMeasure(code=UnitCode.CASE),
                description=(
                    "Wet-can layouts: the only printed wholesale amount — always case-level. "
                    "The printed '(@N)' beside it is the derived per-can average (case total "
                    "divided by can count) and is stripped as annotation, never treated as a "
                    "price."
                ),
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="vet_wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="批發價 W/S Price (HKD)",
                aliases=["W/S Price"],
                price_basis=UnitOfMeasure(code=UnitCode.PACK),
                description=(
                    "Vet-clinic tables: bare HKD numeral (e.g. '260') — the per-pack "
                    "wholesale amount, verified against the BizOps golden sheet "
                    "(basis PACK/qty 1 for every covered SKU)."
                ),
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="建議零售價 (HKD)",
                aliases=[
                    "零售價",
                    "建議零售價",
                    # Wet-can pages: the per-can RRP, printed separately and
                    # more granularly than the case RRP (which stays unmapped).
                    "建議零售價 (HKD) 每罐",
                    "每罐建議零售價",
                    "建議零售價(HKD) 每罐",
                ],
                description=(
                    "Recommended retail amount: same basis as the unit wholesale on unit "
                    "pages, per CAN on wet pages. Vet tables print no retail column."
                ),
                evidence=_KANGAROO_PN_UNIT_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="clinic_offer",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="診所優惠",
                aliases=["Clinic Offer"],
                description=(
                    "Vet-clinic tables: per-row free-goods terms, e.g. '10+2 OR 10+3 "
                    "(單次購買50或以上) (50 or more per order)' — buy 10 get 2 free, or "
                    "10 get 3 free at 50+ per order."
                ),
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
            # No single contract-level basis exists: each price column declares
            # the basis its printed amount is on (UNIT / CASE / PACK), and the
            # resolved column's basis rides with its amount. VERIFIED because
            # every column's basis is individually verified — see the field
            # declarations and the layout evidence.
            price_basis=None,
            price_basis_per_column=True,
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "Unit layouts verified 2026-08-17 (one amount per row, nothing to compete "
                "with it); case layout verified 2026-08-25 (the only printed wholesale is "
                "case-level; the '(@N)' per-can average is derived and never a price); vet "
                "layout verified 2026-08-13 against the BizOps golden sheet (per-PACK, "
                "qty 1). Deriving a per-unit cost from a case total remains forbidden."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat content weight/size as a measure, not a sellable-unit count.",
                "Can-per-case count lives in the column HEADING ('每箱 (12罐)') or the page footnote, never in a row cell.",
                "\"1x3's\" means one sellable pack containing three tablets/chewables.",
            ],
            unresolved_semantics=[
                "Order increment and break-pack rules are not stated by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text", "clinic_offer"],
            condition_patterns=["buy quantity", "spend threshold", "order-size threshold"],
            benefit_patterns=["percentage discount", "free goods (N+M)"],
            requires_validation_issue_when=[
                "The qualifying products, threshold basis, or stacking rules are not explicit."
            ],
            notes=(
                "The ZIWI pages print standing offers as page banners (4% off the "
                "air-dried/steam-dried ranges; 8% over $4,000 spend) — banner text lands in "
                "text_observations, which no contract field reaches today. Vet-clinic "
                "offers are free-goods tiers ('10+2', '10+3 at 50+ per order'), not price "
                "tiers — no structured recognizer parses N+M free goods, so that text "
                "routes to review verbatim."
            ),
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KANGAROO_PN_UNIT_SUPPLIER_IDENTITY_REQUIRED",
                condition="A source may contain multiple suppliers or only a subset of previously observed brands.",
                review_guidance=(
                    "Select this declaration only from ingestion supplier ID 81 or an explicit "
                    "Kangaroo Pet Nutrition / KANGAR source marker; never from page position or "
                    "brand. CONTRACT_SUPPLIER_IDENTITY_MISMATCH verifies this automatically "
                    "whenever a page prints an identity (the current per-page capture shows the "
                    "Ziwi pages print none, so selection anchors to the upload's supplier — the "
                    "same footing every SUPPORTED sibling stands on)."
                ),
                # Non-blocking for the same reason as the KPN and vet-clinic
                # twins: selection is anchored at submission and the mismatch
                # check fires automatically on contrary printed evidence.
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="KANGAROO_PN_PAGE_BANNER_PROMOTIONS_UNREACHABLE",
                condition=(
                    "Standing mix/spend promotions print as page banners. Extraction "
                    "captures them verbatim as page_promotion_text, but this contract "
                    "declares no mbb.page_promotion_shapes — no real Kangaroo page has "
                    "proven a banner notation yet — so banners stay unparsed page "
                    "evidence."
                ),
                review_guidance=(
                    "Reviewers must read the run's page evidence for banner promos. Once "
                    "a real Kangaroo page proves a notation the engine knows (see "
                    "MbbSourceSemantics.page_promotion_shapes), declare it and banners "
                    "conform into ORDER-scoped terms as they do for Vetapet."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="KANGAROO_PN_CASE_COUNT_NOT_A_COLUMN",
                condition=(
                    "Cans-per-case is printed in the column HEADING ('每箱 (12罐)') on the "
                    "wet dog-can page and in a FOOTNOTE on the cat-can page ('85g貓罐 每箱24罐 / "
                    "185g貓罐 每箱12罐', varying BY ROW weight) — never as a row cell, so no "
                    "contract field can map it and per-unit cost cannot be derived "
                    "deterministically."
                ),
                review_guidance=(
                    "Case-basis costs publish as printed. BizOps must confirm can counts per "
                    "SKU (heading/footnote values are the evidence) before per-can costs are "
                    "computed downstream."
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
            "case_wholesale_price",
            "vet_wholesale_price",
            "rrp",
            "clinic_offer",
            "effective_date",
            "promotion_text",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
        metadata={
            "routing_strategy": "supplier_identity_and_layout_markers",
            "sample_reference": "KPN_Kangaroo.pdf",
            "price_basis_group": "PER_COLUMN",
            "golden_sheet": "golden_samples_by_supplier/kangaroo.csv",
            "merged_from": (
                "kangaroo_pet_nutrition.unit_price_list.v1, "
                "kangaroo_pet_nutrition.case_only_price_list.v1, "
                "kangaroo_pet_nutrition.vet_clinic_price_list.v1"
            ),
        },
    )
)

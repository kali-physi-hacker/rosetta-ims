"""K.P.N. Trading supplier-source contract declarations."""

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

_KPN_TRADING_SUPPLIER = SupplierSourceReference(
    supplier_id=15,
    supplier_name="K.P.N. Trading",
    supplier_code="KPNTRADI",
)

_KPN_TRADING_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf",
        (
            "The sample contains sections explicitly identified as K.P.N. Trading "
            "with Stella & Chewy's, Canidae, and NOW FRESH catalogue tables."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.BUSINESS_DOMAIN_DOCUMENTATION,
        "docs/technical-debt/kpn-kangaroo-supplier-source-contracts.md",
        "The production supplier identity is supplier ID 15 with code KPNTRADI.",
    ),
]


KPN_TRADING_CATALOGUE_BUNDLE_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kpn_trading.catalogue_bundle.v1",
        contract_version="v1",
        supplier=_KPN_TRADING_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="K.P.N. Trading catalogue bundle",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_KPN_TRADING_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="kpn_trading_identified_sections",
                    selector=(
                        "Catalogue sections attributed to supplier ID 15 or explicitly "
                        "marked K.P.N. Trading / KPNTRADI"
                    ),
                    notes=(
                        "The sample includes Stella & Chewy's, Canidae, and NOW FRESH, "
                        "but a valid catalogue may contain any subset of brands."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=[
                "產品編號",
                "產品名稱",
                "批發價",
                "SKU#",
                "Product Description",
                "Size",
                "Unit Per Case",
                "建議零售價",
                "Wholesale Price Per Unit",
                "Wholesale Price Per Case",
                "Retail Price Per Unit",
                "Retail Price Per Case",
                "OLD SKU#",
                "NEW SKU#",
                "Last update",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 15, or the enclosing source "
                    "section must explicitly identify K.P.N. Trading / KPNTRADI."
                ),
                "Never select this contract from page number or brand presence alone.",
                "Rows require a product code, product description, and printed wholesale price.",
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
                source_column="產品編號 / SKU#",
                aliases=["產品編號", "SKU#", "Product Code"],
                description="Current product code printed on the eligible K.P.N. Trading row.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="previous_supplier_sku",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="OLD SKU#",
                aliases=["Old SKU", "Previous SKU"],
                description="Previous Canidae supplier code when the source prints an SKU transition.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="replacement_supplier_sku",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="NEW SKU#",
                aliases=["New SKU", "Replacement SKU", "新產品編號"],
                description="Replacement Canidae supplier code when separately printed.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                # OPTIONAL (PR-18 closing audit, finding 1): a brand we cannot
                # read is not a reason to reject the price — REQUIRED here
                # dead-lettered every row of any table whose banner the
                # extraction missed, on all four layouts at once.
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description="Printed row or section brand; observed brands are examples, not routing criteria.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.OPTIONAL,  # name never blocks a row (user ruling 2026-08-25)
                source_column="產品名稱 / Product Description",
                aliases=["產品名稱", "產品內容", "Product Description"],
                description="Printed English/Chinese product description.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="包裝 / Size",
                aliases=["重量", "包裝", "Size"],
                description="Printed content size or packaging text.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="units_per_case",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="原箱包數 / Unit Per Case",
                aliases=["原箱包數", "每箱包數", "Unit Per Case", "Per Case"],
                description="Printed case configuration; it is not an ordering constraint by itself.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價 / Wholesale Price",
                aliases=[
                    "批發價",
                    "每包批發價",
                    "每箱批發價",
                    "Wholesale Price Per Unit",
                    "Wholesale Price Per Case",
                ],
                description="Wholesale amount preserved with its exact printed source heading.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="建議零售價 / Retail Price",
                aliases=[
                    "建議零售價",
                    "Retail Price Per Unit",
                    "Retail Price Per Case",
                ],
                description="Recommended retail amount preserved with its printed price basis.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="availability",
                role=SourceFieldRole.ROW_ELIGIBILITY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="row availability or discontinued marker",
                description="Availability or discontinued state printed for Canidae rows.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document or section effective-date / last-update label",
                description="Document- or section-level effective or last-update date.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document, section, or row promotion notes",
                description="Printed spend, order-discount, or promotional terms.",
                evidence=_KPN_TRADING_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="wholesale_price",
            rrp_source_field="rrp",
            price_basis=None,
            price_basis_status=SemanticResolutionStatus.UNRESOLVED,
            notes=(
                "The bundle mixes per-unit, per-pack, and per-case wholesale columns. "
                "The source heading must be retained and the price basis left unresolved "
                "until the detected table layout is routed to a layout-specific contract."
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
                "Purchase UOM varies by table layout.",
                "Sellable units per purchase unit are not established at bundle level.",
                "Break-pack permission is not established by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["spend threshold", "order discount", "buy quantity"],
            benefit_patterns=["percentage discount", "free quantity"],
            requires_validation_issue_when=[
                "The qualifying products, threshold basis, or mix-and-match rules are not explicit."
            ],
            notes="Promotion text remains evidence until a layout-specific rule proves its scope and benefit.",
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KPN_TRADING_BUNDLE_PRICE_BASIS_VARIES",
                condition="K.P.N. Trading catalogue layouts mix unit, pack, and case price bases.",
                review_guidance=(
                    "Segment the source by brand and table layout before interpreting wholesale or RRP amounts."
                ),
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="KPN_TRADING_SUPPLIER_IDENTITY_REQUIRED",
                condition="A source may contain multiple suppliers or only a subset of previously observed brands.",
                review_guidance=(
                    "Select this declaration only from ingestion supplier ID 15 or an "
                    "explicit K.P.N. Trading / KPNTRADI source marker; never from page position "
                    "or brand. CONTRACT_SUPPLIER_IDENTITY_MISMATCH also verifies this "
                    "automatically from captured evidence — but only once the source has been "
                    "re-extracted with the prompt that captures supplier_identity_text (see "
                    "catalogue_evidence_extraction.py's VISION_EVIDENCE_PROMPT); older or "
                    "not-yet-re-extracted evidence still relies on this manual guidance alone."
                ),
                blocks_supported_status=True,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "previous_supplier_sku",
            "replacement_supplier_sku",
            "brand",
            "description",
            "pack_size",
            "units_per_case",
            "wholesale_price",
            "rrp",
            "availability",
            "effective_date",
            "promotion_text",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
        metadata={
            "routing_strategy": "supplier_identity_and_content_markers",
            "sample_reference": "KPN_Kangaroo.pdf",
            "observed_brands": "Stella & Chewy's, Canidae, NOW FRESH",
            "superseded_by_layout_specific_contracts": (
                "kpn_trading.pack_price_list.v1 (incl. pack+case bulk rows), "
                "kpn_trading.case_only_price_list.v1"
            ),
            "layout_specific_contracts_note": (
                "This bundle contract remains the correct choice only when a source's table "
                "layout has not been pre-sorted. Once a page's layout is identified, prefer "
                "the matching layout-specific contract above, which has a resolved price_basis."
            ),
        },
    )
)


# ─────────────────────────────────────────────────────────────────────────
# Layout-specific contracts.
#
# Full-document analysis of the 44 K.P.N. Trading pages in KPN_Kangaroo.pdf
# found the bundle's mixed price basis is not one ambiguity — it is (at
# least) three distinct, individually resolvable situations, each tied to a
# specific printed table layout:
#
#   PACK        — one wholesale number is printed, always per pack/unit/bag.
#                 No case figure exists to be confused with it.
#   CASE-ONLY   — one wholesale number is printed, always per case/box, with
#                 NO per-pack figure anywhere in the source to fall back on.
#   PACK+CASE   — BOTH a per-pack AND a per-case wholesale number are
#                 printed on the same row. Verified arithmetically (case
#                 price is always cheaper than pack-price x case-quantity,
#                 by a different percentage per SKU) that the case number is
#                 a bulk-quantity discount, not an alternate "real" price —
#                 consistent with this system's MBB semantics ("conditional
#                 discounts, not replacement prices"). The per-pack number is
#                 the standard wholesale_price; the case number is an
#                 OPTIONAL bulk-term column ON THE PACK CONTRACT (user ruling
#                 2026-08-17: "pack_and_case should basically be mbb") — the
#                 short-lived separate pack_and_case contract is retired.
#
# PACK and CASE-ONLY still cannot share: their price_basis answers differ.
# PACK+CASE shares PACK's basis exactly, so it is the same contract with one
# more optional column, not a third answer.
# ─────────────────────────────────────────────────────────────────────────

_LAYOUT_EVIDENCE_NOTE = (
    "Full read of all 44 K.P.N. Trading pages in KPN_Kangaroo.pdf (excludes the "
    "9 pages footed Kangaroo Pet Nutrition Ltd.), grouped by printed table layout "
    "and verified arithmetically against printed case/pack price relationships."
)

_KPN_TRADING_PACK_EVIDENCE = [
    *_KPN_TRADING_EVIDENCE,
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf#pages=11-27,39-47,50-53",
        (
            "Every layout in this group prints exactly one wholesale number, always "
            "per pack/unit/bag (e.g. '每包批發價 Wholesale Price Per Unit', bare "
            "'批發價' with no case column present, CANIDAE's single 批發價 (HKD) "
            "column). " + _LAYOUT_EVIDENCE_NOTE
        ),
    ),
]

_KPN_TRADING_BULK_EVIDENCE = [
    *_KPN_TRADING_EVIDENCE,
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf#pages=1-4,29-30,32",
        (
            "Verified arithmetically against every row on these pages: the case price is "
            "always less than pack_price x units_per_case, by a DIFFERENT percentage per "
            "SKU (e.g. FRB-3: 10.6% off, FRB-6: 6.8% off, FRB-12: 2.4% off) — proving the "
            "case price is a genuine per-SKU bulk discount, not the same price expressed "
            "two ways. The '(平均每包價)' figure on FRB-style rows is exactly "
            "case_price / units_per_case in every sampled row, confirming it is a printed "
            "convenience calculation, not an independently sourced value. " + _LAYOUT_EVIDENCE_NOTE
        ),
    ),
]


KPN_TRADING_PACK_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kpn_trading.pack_price_list.v1",
        contract_version="v1",
        supplier=_KPN_TRADING_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="K.P.N. Trading pack-basis price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_KPN_TRADING_PACK_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="kpn_trading_pack_basis_sections",
                    selector=(
                        "K.P.N. Trading sections whose price columns print exactly one "
                        "wholesale amount, with no separate case/box price column."
                    ),
                    notes=(
                        "Observed across Stella & Chewy's (unit-price pages), NOW FRESH "
                        "(single-price and barcode variants), and Canidae's single-column "
                        "HKD layout."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=[
                "產品編號", "SKU#", "sku#", "產品內容", "產品名稱", "Product Description",
                "包裝", "Size", "重量", "每箱包數", "Unit Per Case", "原箱包數",
                "每包批發價", "Wholesale Price Per Unit", "Wholesale Price Per Pack",
                "批發價", "批發價 (HKD)", "批發價 每包",
                "批發價 每箱 (平均每包價)", "批發價 每箱(平均每包價)",
                "每包 建議零售價", "Recommended Retail Price Per Unit",
                "零售價", "建議零售價 (HKD)", "建議零售價 每包",
                "barcode#", "新產品編號",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 15, or the enclosing source "
                    "section must explicitly identify K.P.N. Trading / KPNTRADI."
                ),
                (
                    "Select this contract for every layout whose STANDARD wholesale "
                    "amount is per pack/unit/bag — with or without an additional case "
                    "column (a printed case price is a bulk term, never the cost)."
                ),
                "Rows require a product code, product description, and printed wholesale price.",
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
                source_column="產品編號 / SKU#",
                # 新產品編號 FIRST: aliases are tried in declared order, and on the
                # CANIDAE transition pages both codes are printed — the new code
                # must win or the row conforms under the superseded SKU. Verified
                # on the sample: 產品編號=1005 / 新產品編號=1005J -> sku 1005J.
                aliases=["新產品編號", "產品編號", "SKU#", "sku#", "Product Code"],
                description="Current product code; 新產品編號 (new code) wins over 產品編號 where both are printed.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="previous_supplier_sku",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="產品編號",
                aliases=["Old SKU", "Previous SKU"],
                description=(
                    "Legacy Canidae code, printed alongside 新產品編號 during an SKU "
                    "transition. On layouts WITHOUT a transition (no 新產品編號 column) "
                    "this mirrors supplier_sku — it is only meaningful when it DIFFERS "
                    "from supplier_sku; no contract mechanism can scope a field to only "
                    "the pages where a sibling column exists."
                ),
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                # OPTIONAL (PR-18 closing audit, finding 1): a brand we cannot
                # read is not a reason to reject the price — REQUIRED here
                # dead-lettered every row of any table whose banner the
                # extraction missed, on all four layouts at once.
                requirement=SourceFieldRequirement.OPTIONAL,
                # page_brand, not section_header (golden calibration): this
                # layout's banners are product-line strips ("- RAW BLEND -",
                # 凍乾生肉外層低溫烘焙乾糧...), not brands. The Stella & Chewy
                # mark heads the PAGE; envelopes captured before
                # page_brand_text existed leave brand empty on purpose.
                source_path="page_brand",
                description="Product brand, read from the brand mark heading the page — never from the table banner, which names a product line.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.OPTIONAL,  # name never blocks a row (user ruling 2026-08-25)
                source_column="產品內容 / Product Description",
                source_path="unlabeled_column",
                aliases=["產品內容", "產品名稱", "Product Description"],
                # Fallback chain, tried in order: labeled heading -> the single
                # unlabeled column (NOW FRESH single-price pages) -> the block
                # heading via section_name (Good Gravy pages print the name
                # ONLY as the section band above the rows — user-confirmed
                # against the rendered page 47, 2026-08-17).
                composed_from=["section_name"],
                description=(
                    "Printed English/Chinese product description. The NOW FRESH single-price "
                    "layout prints the product name under an unlabeled (empty-heading) first "
                    "column — claimed via the unlabeled_column sentinel, which resolves only "
                    "when the row carries exactly one non-empty unlabeled value. The Good "
                    "Gravy layout prints the name only as the section heading above each "
                    "block ('ADULT DOG BEEF 成犬 …牛肉配方'), reached through the "
                    "section_name composition fallback. Labeled headings are always tried "
                    "first, so every other layout in this group is unaffected."
                ),
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="section_name",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description=(
                    "The block heading printed above a group of rows ('ADULT DOG BEEF 成犬 "
                    "香濃火雞骨湯外層乾糧 - 牛肉配方(含古代穀物)'). On the Good Gravy layout this "
                    "is the only place the product name is printed — description composes "
                    "from it when no name column resolves."
                ),
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="包裝 / Size",
                aliases=["包裝", "Size", "重量"],
                description="Printed content size, weight, or packaging text.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="units_per_case",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="每箱包數 / Unit Per Case",
                # 原箱包數 is the frozen-raw spelling, and its cell merges the
                # content size with the count ('3lb 6包') — the counter-aware
                # quantity reader takes the 6, never the 3.
                aliases=["每箱包數", "Unit Per Case", "原箱包數", "每箱罐數"],
                description=(
                    "Printed case configuration where shown; not present on every layout "
                    "in this group. Doubles as the case-term quantity: buying this many "
                    "unlocks the case rate where a case column is printed."
                ),
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價",
                aliases=[
                    "每包批發價", "Wholesale Price Per Unit", "Wholesale Price Per Pack",
                    "批發價 (HKD)", "批發價 每包",
                ],
                description="The STANDARD wholesale amount — always the per-pack/per-tin figure, never a case figure.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="零售價",
                aliases=[
                    "每包 建議零售價", "Recommended Retail Price Per Unit",
                    "建議零售價 (HKD)", "建議零售價 每包",
                ],
                description="Recommended retail amount, same basis as wholesale_price for this group.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="case_wholesale_price",
                role=SourceFieldRole.MBB_TIER_PRICE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="批發價 每箱 (平均每包價)",
                aliases=[
                    # The frozen-raw pages print the heading without a space
                    # before the parenthesis; both spellings are declared.
                    "批發價 每箱(平均每包價)",
                    "每箱(24包) 批發價 Wholesale Price Per Case (24 packs)",
                    "每箱(24罐) 批發價 Wholesale Price Per Case (24 tins)",
                ],
                tier_quantity_field="units_per_case",
                description=(
                    "OPTIONAL case-quantity BULK price — an MBB tier, not a replacement "
                    "for wholesale_price (user ruling 2026-08-17: pack+case is the pack "
                    "layout plus a bulk term, one contract). Buying the quantity "
                    "units_per_case states unlocks the printed per-unit case rate. On "
                    "Stella & Chewy's frozen-raw rows the cell embeds the case total and "
                    "its printed per-pack average in one string ('$1094/箱 ($182/包)'); "
                    "the per-unit rate is the smaller printed amount, and a cell carrying "
                    "only a bundle total is refused by the cheaper-than-gross guard rather "
                    "than divided — no value is computed that the source did not print."
                ),
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="barcode",
                role=SourceFieldRole.BARCODE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="barcode#",
                description="EAN/UPC barcode, printed only on some NOW FRESH pages.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document or section effective-date / last-update label",
                description="Document- or section-level effective or last-update date.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document, section, or row promotion notes",
                description="Printed spend, order-discount, or promotional terms.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="wholesale_price",
            rrp_source_field="rrp",
            price_basis=UnitOfMeasure(code=UnitCode.PACK),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "Verified directly against all pages in this group: exactly one wholesale "
                "amount is printed per row, and no case/box price column exists to compete "
                "with it. Safe to treat as the sellable pack/unit price."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            # True for the whole pack group: every layout prints a PER-PACK
            # price, so buying below a case is the norm this catalogue sells
            # by — carried over from the folded pack+case declaration, and the
            # packaging statement that lets size-less frozen-raw rows (whose
            # size lives inside 原箱包數, '3lb 6包') resolve and publish.
            break_pack_allowed=True,
            interpretation_rules=[
                "Treat content size/weight as a measure, not a sellable-unit count.",
                "Treat units per case as case configuration only; it is not printed as an ordering constraint.",
                "wholesale_price is always pack/tin-basis; never substitute a case figure into it.",
                "The case-average figure printed in parentheses is derived (case_price / units_per_case); do not treat it as an independent price.",
                "A case column printing ONLY a bundle total yields no bulk term (ruling "
                "2026-08-17): dividing the total by units_per_case to derive a per-unit "
                "rate is not acceptable — deals exist only where the page prints the "
                "per-unit rate itself.",
            ],
            unresolved_semantics=[
                "Purchase UOM (pack vs bag vs bottle) varies by sub-layout and is not separately declared.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["promotion_text"],
            condition_patterns=["spend threshold", "order discount"],
            benefit_patterns=["percentage discount", "free quantity"],
            requires_validation_issue_when=[
                "The qualifying products, threshold basis, or mix-and-match rules are not explicit."
            ],
            notes="Promotion text remains evidence until a rule proves its scope and benefit.",
        ),
        known_ambiguities=[
            # The former KPN_TRADING_PACK_CASE_TIER_NEEDS_PRINTED_UNIT_RATE
            # ambiguity is RESOLVED by ruling (2026-08-17): a case column that
            # prints only a bundle total yields NO term — deriving the per-unit
            # rate by division is not acceptable, deals exist only where the
            # page prints the per-unit rate. That is exactly what the engine
            # already does (pinned by
            # test_case_total_without_printed_unit_rate_emits_no_term), so the
            # open question is closed and the standing rule lives in
            # interpretation_rules below.
            AmbiguityRule(
                issue_code="KPN_TRADING_PACK_SUPPLIER_IDENTITY_REQUIRED",
                condition="A source may contain multiple suppliers or only a subset of previously observed brands.",
                review_guidance=(
                    "Select this declaration only from ingestion supplier ID 15 or an "
                    "explicit K.P.N. Trading / KPNTRADI source marker; never from page position "
                    "or brand. CONTRACT_SUPPLIER_IDENTITY_MISMATCH also verifies this "
                    "automatically from captured evidence — but only once the source has been "
                    "re-extracted with the prompt that captures supplier_identity_text (see "
                    "catalogue_evidence_extraction.py's VISION_EVIDENCE_PROMPT); older or "
                    "not-yet-re-extracted evidence still relies on this manual guidance alone. "
                    "Downgraded from blocking at promotion (2026-08-13): the automatic check is "
                    "live-verified on re-extracted KPN evidence (it split the combined document's "
                    "84 Kangaroo rows from the 356 KPN rows exactly), and the current extraction "
                    "prompt captures identity text on every new source."
                ),
                blocks_supported_status=False,
            ),
            # The former KPN_TRADING_PACK_BARCODE_COLUMN_INCONSISTENT ambiguity
            # is RESOLVED (2026-08-17, confirmed against the rendered pages
            # 46-47 and the newer whole-document capture): the SOURCE is clean —
            # every row prints a real barcode or nothing, and the one observed
            # name-in-column ('細細粒 小型犬配方') was a capture artifact, a
            # row-side badge folded into the nearest column. The barcode column
            # stays trusted; description for these rows comes from the section
            # heading via the section_name composition fallback (user-confirmed
            # the name is printed only as the block heading).
            # The former KPN_TRADING_PACK_UNLABELED_COLUMN_SINGLE_VALUE_ONLY
            # ambiguity is retired (2026-08-17): it described the sentinel's
            # working, test-pinned safety (exactly one non-empty unlabeled
            # value resolves; two refuse — never a guess) plus a hypothetical
            # future layout. The 2026-08-17 per-page capture doesn't even
            # exercise it — pages 43-45 came through with a LABELED 產品內容
            # column — and if a two-unlabeled-columns layout ever appears,
            # its rows self-surface in the held lane as name-missing, which
            # is when positional addressing (column_index) would be built.
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "previous_supplier_sku",
            "brand",
            "description",
            "pack_size",
            "units_per_case",
            "wholesale_price",
            "rrp",
            "barcode",
            "effective_date",
            "promotion_text",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
        metadata={
            "routing_strategy": "supplier_identity_and_layout_markers",
            "sample_reference": "KPN_Kangaroo.pdf",
            "price_basis_group": "PACK",
        },
    )
)


_KPN_TRADING_CASE_ONLY_EVIDENCE = [
    *_KPN_TRADING_EVIDENCE,
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:KPN_Kangaroo.pdf#pages=28,31,48-49",
        (
            "Verified directly: wholesale amounts on these pages are printed only at "
            "case/box level (e.g. '批發價 1盒24包', '每箱(12盒)批發價 Wholesale Price "
            "Per Case (12 boxes)') with NO per-pack/per-box wholesale figure anywhere "
            "in the source, even though RRP is separately printed at both case AND "
            "pack level on the same rows. " + _LAYOUT_EVIDENCE_NOTE
        ),
    ),
]

KPN_TRADING_CASE_ONLY_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kpn_trading.case_only_price_list.v1",
        contract_version="v1",
        supplier=_KPN_TRADING_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="K.P.N. Trading case-only price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="kpn_trading_case_only_sections",
                    selector=(
                        "K.P.N. Trading sections whose ONLY printed wholesale amount is "
                        "at case/box level, with a separately printed pack-level RRP but "
                        "no pack-level wholesale figure."
                    ),
                    notes="Observed on wet-food case pages and a 24-pack box layout.",
                )
            ],
            required_headers=[],
            optional_headers=[
                "產品編號 SKU#", "sku#", "產品內容 Product Description", "包裝", "Size",
                "每箱盒數 Unit Per Case",
                "每箱(12盒)批發價 Wholesale Price Per Case (12 boxes)",
                "批發價 1盒24包",
                "每箱 (12盒) 建議零售價 Recommended Retail Price Per Case (12 boxes)",
                "零售價 1盒24包", "零售價 1箱24包",
                "每盒 建議零售價 Recommended Retail Price Per box",
                "零售價 每包",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 15, or the enclosing source "
                    "section must explicitly identify K.P.N. Trading / KPNTRADI."
                ),
                "Select this contract only when the row's table prints wholesale at case/box level with no pack-level wholesale figure present.",
                "Rows require a product code, product description, and printed wholesale price.",
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
                source_column="產品編號 SKU# / sku#",
                aliases=["產品編號 SKU#", "sku#", "SKU#"],
                description="Current product code printed on the eligible row.",
                evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                # OPTIONAL (PR-18 closing audit, finding 1): a brand we cannot
                # read is not a reason to reject the price — REQUIRED here
                # dead-lettered every row of any table whose banner the
                # extraction missed, on all four layouts at once.
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description="Printed row or section brand; observed brands are examples, not routing criteria.",
                evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.OPTIONAL,  # name never blocks a row (user ruling 2026-08-25)
                source_column="產品內容 Product Description",
                aliases=["產品內容", "產品內容 Product Description", "Product Description"],
                description="Printed English/Chinese product description.",
                evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="包裝",
                aliases=["包裝", "Size"],
                description="Printed content size or packaging text.",
                evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="units_per_case",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="每箱盒數 Unit Per Case",
                aliases=["每箱盒數", "Unit Per Case"],
                description="Case configuration — required here since the wholesale price is stated only at this level.",
                evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="每箱(12盒)批發價 Wholesale Price Per Case (12 boxes)",
                aliases=[
                    "批發價 1盒24包",
                    "每箱(12盒) 批發價 Wholesale Price Per Case (12 boxes)",
                    "每箱(12盒)批發價 Wholesale Price Per Case (12 boxes)",
                ],
                description="The only printed wholesale amount for this group — always at case/box level. No per-unit figure exists in the source.",
                evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="每盒 建議零售價 Recommended Retail Price Per box",
                aliases=[
                    "零售價 每包", "每盒 建議零售價 Recommended Retail Price Per box",
                ],
                description=(
                    "Preferred RRP is the pack-level figure ('零售價 每包' / '每盒 建議零售價'), "
                    "which the source prints separately and more granularly than the case RRP."
                ),
                evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document or section effective-date / last-update label",
                description="Document- or section-level effective or last-update date.",
                evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document, section, or row promotion notes",
                description="Printed spend, order-discount, or promotional terms.",
                evidence=_KPN_TRADING_CASE_ONLY_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="wholesale_price",
            rrp_source_field="rrp",
            price_basis=UnitOfMeasure(code=UnitCode.CASE),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "Verified directly against every page in this group: the only printed "
                "wholesale amount is at case/box level. There is no per-pack wholesale "
                "figure to derive — computing one by dividing the case price would invent "
                "a value the source never stated, which this contract does not do."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=False,
            interpretation_rules=[
                "Treat content size as a measure, not a sellable-unit count.",
                "units_per_case is required here — it is the denominator the source itself uses for its own printed per-box RRP.",
            ],
            unresolved_semantics=[
                "Whether break-pack purchase (buying below a full case, at case-derived pricing) is commercially permitted is not stated by the source.",
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
                issue_code="KPN_TRADING_CASE_ONLY_SUPPLIER_IDENTITY_REQUIRED",
                condition="A source may contain multiple suppliers or only a subset of previously observed brands.",
                review_guidance=(
                    "Select this declaration only from ingestion supplier ID 15 or an "
                    "explicit K.P.N. Trading / KPNTRADI source marker; never from page position "
                    "or brand. CONTRACT_SUPPLIER_IDENTITY_MISMATCH also verifies this "
                    "automatically from captured evidence — but only once the source has been "
                    "re-extracted with the prompt that captures supplier_identity_text (see "
                    "catalogue_evidence_extraction.py's VISION_EVIDENCE_PROMPT); older or "
                    "not-yet-re-extracted evidence still relies on this manual guidance alone."
                ),
                blocks_supported_status=True,
            ),
            AmbiguityRule(
                issue_code="KPN_TRADING_CASE_ONLY_BREAK_PACK_UNCONFIRMED",
                condition="No printed evidence confirms whether below-case-quantity purchase is offered for this layout.",
                review_guidance="Confirm with the supplier or BizOps whether break-pack ordering is available before enabling it downstream.",
                blocks_supported_status=False,
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
            "routing_strategy": "supplier_identity_and_layout_markers",
            "sample_reference": "KPN_Kangaroo.pdf",
            "price_basis_group": "CASE",
        },
    )
)

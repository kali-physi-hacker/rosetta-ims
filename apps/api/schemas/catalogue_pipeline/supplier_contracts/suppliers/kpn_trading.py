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
                requirement=SourceFieldRequirement.REQUIRED,
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
                "kpn_trading.pack_price_list.v1, "
                "kpn_trading.case_only_price_list.v1, "
                "kpn_trading.pack_and_case_bulk_list.v1"
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
#                 the standard wholesale_price; the case number is captured
#                 as a bulk term.
#
# A single contract-level price_basis cannot express three different
# answers, so each situation gets its own contract instead of a fourth
# attempt to resolve one field for the whole bundle.
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
                "產品編號", "SKU#", "sku#", "產品內容", "Product Description",
                "包裝", "Size", "重量", "每箱包數", "Unit Per Case",
                "每包批發價", "Wholesale Price Per Unit", "Wholesale Price Per Pack",
                "批發價", "批發價 (HKD)",
                "每包 建議零售價", "Recommended Retail Price Per Unit",
                "零售價", "建議零售價 (HKD)",
                "barcode#", "新產品編號",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 15, or the enclosing source "
                    "section must explicitly identify K.P.N. Trading / KPNTRADI."
                ),
                "Select this contract only when the row's table prints one wholesale amount and no separate case/box price column.",
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
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品內容 / Product Description",
                source_path="unlabeled_column",
                aliases=["產品內容", "產品名稱", "Product Description"],
                description=(
                    "Printed English/Chinese product description. The NOW FRESH single-price "
                    "layout prints the product name under an unlabeled (empty-heading) first "
                    "column — claimed via the unlabeled_column sentinel, which resolves only "
                    "when the row carries exactly one non-empty unlabeled value. Labeled "
                    "headings are always tried first, so every other layout in this group is "
                    "unaffected."
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
                aliases=["每箱包數", "Unit Per Case"],
                description="Printed case configuration where shown; not present on every layout in this group.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價",
                aliases=[
                    "每包批發價", "Wholesale Price Per Unit", "Wholesale Price Per Pack",
                    "批發價 (HKD)",
                ],
                description="The single printed wholesale amount for this group's layouts — always per pack/unit/bag.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="零售價",
                aliases=[
                    "每包 建議零售價", "Recommended Retail Price Per Unit",
                    "建議零售價 (HKD)",
                ],
                description="Recommended retail amount, same basis as wholesale_price for this group.",
                evidence=_KPN_TRADING_PACK_EVIDENCE,
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
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat content size/weight as a measure, not a sellable-unit count.",
                "Treat units per case as case configuration only; it is not printed as an ordering constraint.",
            ],
            unresolved_semantics=[
                "Purchase UOM (pack vs bag vs bottle) varies by sub-layout and is not separately declared.",
                "Break-pack permission is not established by the source.",
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
            AmbiguityRule(
                issue_code="KPN_TRADING_PACK_BARCODE_COLUMN_INCONSISTENT",
                condition=(
                    "The pages 46-47 NOW FRESH layout's 'barcode#' column is inconsistent: on "
                    "page 47 it genuinely holds barcodes (e.g. '8 15260 00767 2'); on page 46 "
                    "the same heading sometimes holds product-name text (e.g. '細細粒 小型犬配方') "
                    "and is otherwise blank. This looks like inconsistent vision extraction "
                    "(a section banner sometimes folded into this column) rather than one "
                    "clean pattern, so this contract does not map 'description' to it — doing "
                    "so would risk misreading real barcodes as product names on page 47."
                ),
                review_guidance=(
                    "Confirm against the actual PDF page images whether page 46's product-name "
                    "text is a genuine section banner the extraction mis-attributed to this "
                    "column, or whether the source itself is inconsistent. description remains "
                    "unresolved for these specific rows until that's confirmed."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="KPN_TRADING_PACK_UNLABELED_COLUMN_SINGLE_VALUE_ONLY",
                condition=(
                    "The NOW FRESH single-price layout (observed on pages 43-45 of the sample; "
                    "identified by its column signature, not page position) prints the product "
                    "name under an unlabeled column, now claimed via the unlabeled_column "
                    "sentinel. The sentinel resolves only when a row carries exactly ONE "
                    "non-empty unlabeled value — a future layout printing values in two "
                    "unlabeled columns on the same row would resolve to nothing (refused as "
                    "ambiguous), never to a guess between them."
                ),
                review_guidance=(
                    "If a layout with multiple populated unlabeled columns ever appears, "
                    "positional addressing (column_index) would be needed — a shared-code "
                    "extension, with the same cross-contract regression rigor as prior "
                    "conformance changes."
                ),
                blocks_supported_status=False,
            ),
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
                requirement=SourceFieldRequirement.REQUIRED,
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

KPN_TRADING_PACK_AND_CASE_BULK_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="kpn_trading.pack_and_case_bulk_list.v1",
        contract_version="v1",
        supplier=_KPN_TRADING_SUPPLIER,
        document_type=SupplierDocumentType.CATALOGUE,
        format_name="K.P.N. Trading pack price with case bulk term",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.PARTIALLY_VERIFIED,
        evidence=_KPN_TRADING_BULK_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="kpn_trading_pack_and_case_sections",
                    selector=(
                        "K.P.N. Trading sections that print BOTH a per-pack/per-tin "
                        "wholesale amount AND a separate per-case wholesale amount on the "
                        "same row."
                    ),
                    notes=(
                        "Stella & Chewy's frozen-raw pages print the case price with an "
                        "embedded per-pack average in the same cell; the 24-pack and "
                        "24-tin case layouts print both amounts as clean, separate columns."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=[
                "產品編號", "產品名稱", "原箱包數", "批發價 每包",
                "批發價 每箱 (平均每包價)", "建議零售價 每包", "建議零售價 每箱 (平均每包價)",
                "產品編號 SKU#", "產品內容 Product Description", "包裝 Size",
                "每箱包數 Unit Per Case",
                "每箱(24包) 批發價 Wholesale Price Per Case (24 packs)",
                "每包批發價 Wholesale Price Per Pack",
                "每箱 (24包) 建議零售價 Recommended Retail Price Per Case (24 Packs)",
                "每包 建議零售價 Recommended Retail Price Per Pack",
                "每箱罐數 Unit Per Case",
                "每箱(24罐) 批發價 Wholesale Price Per Case (24 tins)",
                "每罐批發價 Wholesale Price Per tin",
                "每箱 (24罐) 建議零售價 Recommended Retail Price Per Case (24 tins)",
                "每罐 建議零售價 Recommended Retail Price Per tin",
            ],
            row_eligibility_rules=[
                (
                    "The ingestion supplier must be ID 15, or the enclosing source "
                    "section must explicitly identify K.P.N. Trading / KPNTRADI."
                ),
                "Select this contract only when the row's table prints both a pack/tin wholesale amount and a separate case wholesale amount.",
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
                source_column="產品編號",
                aliases=["產品編號", "產品編號 SKU#", "SKU#"],
                description="Product code printed on the eligible row.",
                evidence=_KPN_TRADING_BULK_EVIDENCE,
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
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="產品名稱",
                aliases=["產品名稱", "產品內容", "產品內容 Product Description"],
                description="Printed English/Chinese product description.",
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="包裝 Size",
                source_path="unlabeled_column",
                aliases=["包裝", "Size"],
                description=(
                    "Printed content size or packaging text. The frozen-raw layout prints "
                    "the size ('3lb') in a column with no heading at all — claimed via the "
                    "unlabeled_column sentinel, which resolves only when the row carries "
                    "exactly one non-empty unlabeled value (the image column is empty on "
                    "these rows, so the size is that one value)."
                ),
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="units_per_case",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="原箱包數",
                aliases=["原箱包數", "每箱包數", "Unit Per Case", "每箱罐數"],
                description="Case configuration — required here since it is the divisor behind the printed case-average figure.",
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="wholesale_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="批發價 每包",
                aliases=[
                    "每包批發價", "Wholesale Price Per Pack", "每罐批發價", "Wholesale Price Per tin",
                ],
                description="The STANDARD wholesale price — always the per-pack/per-tin figure, never the case figure.",
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="case_wholesale_price",
                role=SourceFieldRole.MBB_TIER_PRICE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="批發價 每箱 (平均每包價)",
                aliases=[
                    "每箱(24包) 批發價 Wholesale Price Per Case (24 packs)",
                    "每箱(24罐) 批發價 Wholesale Price Per Case (24 tins)",
                ],
                tier_quantity_field="units_per_case",
                description=(
                    "The case-quantity BULK price — a quantity-conditioned MBB tier, not a "
                    "replacement for wholesale_price. Buying the quantity units_per_case states "
                    "(the declared tier_quantity_field) unlocks the printed per-unit case rate. "
                    "On Stella & Chewy's frozen-raw rows the cell embeds the case total and its "
                    "printed per-pack average in one string ('$1094/箱 ($182/包)'); the per-unit "
                    "rate is the smaller printed amount, and a cell carrying only a bundle total "
                    "is refused by the cheaper-than-gross guard rather than divided — no value is "
                    "computed that the source did not print."
                ),
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="rrp",
                role=SourceFieldRole.RRP,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="建議零售價 每包",
                aliases=[
                    "每包 建議零售價 Recommended Retail Price Per Pack",
                    "每罐 建議零售價 Recommended Retail Price Per tin",
                ],
                description="Recommended retail amount, pack/tin basis — same basis as wholesale_price.",
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="effective_date",
                role=SourceFieldRole.EFFECTIVE_DATE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document or section effective-date / last-update label",
                description="Document- or section-level effective or last-update date.",
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="promotion_text",
                role=SourceFieldRole.MBB_TEXT,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="document, section, or row promotion notes",
                description="Printed spend, order-discount, or promotional terms, distinct from the structured case bulk price.",
                evidence=_KPN_TRADING_BULK_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="wholesale_price",
            rrp_source_field="rrp",
            price_basis=UnitOfMeasure(code=UnitCode.PACK),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "The STANDARD price basis is pack/tin — verified arithmetically that the "
                "case figure is always a cheaper, SKU-specific bulk rate, not an alternate "
                "statement of the same price. The case figure is captured separately as "
                "case_wholesale_price / an MBB bulk term, never merged into wholesale_price."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            content_measure_source_field="pack_size",
            break_pack_allowed=True,
            interpretation_rules=[
                "Treat content size as a measure, not a sellable-unit count.",
                "wholesale_price is always pack/tin-basis; never substitute the case figure into it.",
                "The case-average figure printed in parentheses is derived (case_price / units_per_case); do not treat it as a fourth independent price.",
            ],
            unresolved_semantics=[
                "Whether the case bulk rate requires the full case quantity or applies incrementally is not stated by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["case_wholesale_price", "promotion_text"],
            supported_scopes=["SUPPLIER_SKU"],
            condition_patterns=["buy quantity"],
            benefit_patterns=["case bulk discount"],
            requires_validation_issue_when=[
                "A case column prints only a bundle total (no per-unit rate), so no structured term can be emitted without deriving a value the source did not print.",
            ],
            notes=(
                "case_wholesale_price is a per-SKU case-quantity discount off the standard "
                "pack price, verified arithmetically across every sampled row; never a "
                "replacement for wholesale_price. It is declared MBB_TIER_PRICE with "
                "tier_quantity_field=units_per_case, so rows printing a per-unit case rate "
                "emit a structured minimum_quantity -> discounted_unit_price term."
            ),
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="KPN_TRADING_BULK_SUPPLIER_IDENTITY_REQUIRED",
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
                issue_code="KPN_TRADING_BULK_CASE_TIER_NEEDS_PRINTED_UNIT_RATE",
                condition=(
                    "case_wholesale_price is now a quantity-conditioned MBB_TIER_PRICE and emits "
                    "a structured term whenever the cell prints a per-unit case rate (frozen-raw "
                    "rows print '$1094/箱 ($182/包)' — both amounts verbatim). Sub-layouts that "
                    "print ONLY a case total with no per-unit rate (the 24-pack/24-tin case "
                    "columns) yield no term: the total fails the cheaper-than-gross guard, and "
                    "deriving the rate by dividing would invent a value the source never printed."
                ),
                review_guidance=(
                    "If BizOps wants terms for the total-only case columns, confirm that "
                    "case_total / units_per_case is an acceptable DERIVED per-unit rate — that "
                    "is a policy decision to compute, which this contract deliberately does not "
                    "make on its own."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="KPN_TRADING_BULK_PACK_SIZE_FROM_UNLABELED_COLUMN",
                condition=(
                    "The frozen-raw layout prints pack size ('3lb', '6lb', '12lb') under an "
                    "unlabeled (empty-string heading) column, now claimed via pack_size's "
                    "unlabeled_column sentinel — safe on these rows because the only other "
                    "unlabeled column (the product image) is empty, leaving exactly one "
                    "unlabeled value per row, which is the sentinel's resolution requirement. "
                    "History worth keeping: before units_per_case moved to role=OTHER, its "
                    "value ('6包') silently filled the pack_size slot whenever pack_size "
                    "failed to resolve — wrong data with zero issue raised. Two fields must "
                    "never again share role=PACKAGING in this family."
                ),
                review_guidance=(
                    "If a future frozen-raw edition prints content INTO the image column, rows "
                    "would carry two unlabeled values and pack_size would resolve to nothing "
                    "(refused as ambiguous, never guessed) — positional addressing would then "
                    "be needed."
                ),
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
            "case_wholesale_price",
            "rrp",
            "effective_date",
            "promotion_text",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
        metadata={
            "routing_strategy": "supplier_identity_and_layout_markers",
            "sample_reference": "KPN_Kangaroo.pdf",
            "price_basis_group": "PACK_WITH_CASE_BULK_TERM",
        },
    )
)

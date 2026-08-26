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
            # OPTIONAL since the codeless-products ruling (2026-08-26): pages
            # that print no code (the treat family) are still considered —
            # their rows conform without identity, matching is MANUAL, and
            # once matched to (or creating) a product entity the offering is
            # identified by Rosetta's internal SKU, adopted at apply. Rows
            # that DO print codes still map them through these columns.
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="CODE NO / 編號",
            aliases=["CODE NO", "編號", "CODE", "貨品編號", "貨品編號 (Code no.)"],
            description=(
                "Vetapet code number. Bare aliases cover the retail sections that print "
                "編號 alone (letter-spaced 編 號 matches via CJK-space-insensitive folding) "
                "and the variant tables that print CODE. The accessories pages print "
                "貨品編號 (Code no.) — aliased in full (2026-08-26; 32 held rows). Bare "
                "'Code No.' (with the period) is still deliberately NOT an alias of its "
                "own: that heading belongs to the code→category legend sidebars, which "
                "are reference boxes, not product rows — the current captures emit no "
                "such rows, and if one ever appears it would surface as held, never "
                "published, since legends print no price."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="description",
            role=SourceFieldRole.PRODUCT_NAME,
            requirement=SourceFieldRequirement.OPTIONAL,  # name never blocks a row (user ruling 2026-08-25)
            source_column="PRODUCT NAME / 產品名稱",
            # "English Name" last: on pages that split the name by language
            # ('Chinese Name' / 'English Name' columns), the ENGLISH name IS
            # the name (user ruling 2026-08-25). The Chinese name stays on the
            # evidence card, never in this field.
            aliases=["PRODUCT NAME", "產品名稱", "產品", "產品 (Product)", "Product Name (bilingual)", "貨品名稱", "貨品名稱 (Name)", "English Name"],
            description=(
                "Printed product name. Retail sections print 產品 alone (letter-spaced "
                "產 品 in the source; matched via CJK-space-insensitive folding), and the "
                "dry-food retail layout splits the name into 'Chinese Name' / 'English "
                "Name' columns — the English column is the name there. Variant tables "
                "(e.g. Ferplast beds) name the product only in the banner above the "
                "table and are expected to flag this field for review."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="brand",
            role=SourceFieldRole.BRAND,
            requirement=SourceFieldRequirement.OPTIONAL,
            # `page_brand` — the maker's wordmark heading the PAGE ('zoetis'
            # printed opposite Vetapet's own letterhead). The text above each
            # table is a CATEGORY (PARASITE CONTROL, DRUG) or an origin/promo
            # banner, so section_header here put categories into brand —
            # worse than empty. Envelopes captured before page_brand_text
            # existed leave this field empty on purpose.
            source_path="page_brand",
            description=(
                "Product brand, read from the brand mark heading the page (e.g. the "
                "zoetis or Dermoscent wordmark) — never from the table banner, which "
                "names a category."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="pack_size",
            role=SourceFieldRole.PACKAGING,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="PACKING PER UNIT" if segment == "vet" else "重量 / SIZE",
            aliases=(
                ["PACKING PER UNIT", "SIZE", "PACK", "重量", "重量 1 (Weight)", "包裝"]
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
                # The Wholesale/Retail family (批發價 beside a 建議零售價 column,
                # printed bare, letter-spaced, or as '批發價 (Wholesale)') is
                # claimed since the 2026-08-25 ruling extended the earlier
                # rrp-column ruling to it: a retail column beside the wholesale
                # means the wholesale is the per-unit price. This retires the
                # old deliberate exclusion of bare 批發價, whose reason was
                # exactly that nobody had verified that basis yet.
                # 'Unit Price (box)' is the REGULAR price on the diagnostics
                # Special-Offer tables: the whole-doc capture split the struck
                # render into '(single)'/'(box)' columns, and every '(single)'
                # value is exactly 70% of '(box)' — the promo badge, never a
                # per-test price (a box cannot cost less than two singles).
                # Regular price = cost, per the struck-price ruling.
                [
                    "WHOLESALE PRICE", "UNIT PRICE", "PRICE", "UNIT PRICE PER TEST",
                    "Unit Price (box)", "批發價 (Wholesale)", "批發價", "批發價 1", "Wholesale",
                ]
                if segment == "vet"
                else ["批發價", "WHOLESALE PRICE"]
            ),
            description=(
                "Supplier cost. The vet sections price by UNIT PRICE / PRICE — verified on "
                "the sample that UNIT PRICE is the price of one ORDER UNIT (a box/set/pc "
                "named per row), not one test; see packaging.purchase_uom_source_field. "
                "Retail-style sections print 批發價 — bare, letter-spaced, or as "
                "'批發價 (Wholesale)' — always with a retail column beside it, which is "
                "what confirms the per-unit basis (ruling 2026-08-25)."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="rrp",
            role=SourceFieldRole.RRP,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="SUGGESTED RETAIL PRICE / RETAIL PRICE / 零售價" if segment == "vet" else "零售價 / RETAIL PRICE",
            aliases=[
                "SUGGESTED RETAIL PRICE", "RETAIL PRICE", "SUGGESTED PRICE",
                "零售價", "建議零售價", "建議零售價 (Retail)", "建議零售價 1",
                "建議零售價 (Recommended Retail price)",
            ],
            description=(
                "Suggested retail or retail price field; retail sections print 建議零售價 "
                "(sometimes as '建議零售價 (Retail)'), and the vet catalogue's retail-style "
                "brand tables print 'Suggested Price'."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="box_quantity",
            role=SourceFieldRole.OTHER,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="量 2 (Box)",
            description=(
                "Treat-layout box configuration ('1盒20條' — one box of twenty pieces). "
                "A purchase-format statement, never a deal: no term is emitted without a "
                "printed per-piece rate at box quantity (case-total ruling), and on the "
                "sample the box price is exactly the piece price times the count."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="box_wholesale_price",
            role=SourceFieldRole.OTHER,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="盒批發價 2",
            description=(
                "Treat-layout box wholesale total (prints as '批發價$392' — the heading "
                "leaks into the cell). Evidence for reviewers beside box_quantity; the "
                "per-piece 批發價 1 remains the row's cost."
            ),
            evidence=evidence_items,
        ),
        SourceFieldContract(
            field_key="promotion_text",
            role=SourceFieldRole.MBB_TEXT,
            requirement=SourceFieldRequirement.OPTIONAL,
            source_column="TERMS / REMARKS",
            aliases=["TERMS", "REMARKS", "REMARK"],
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
        # "C.Vetapet & Company" is the canonical trading name (user decision,
        # 2026-08-13): it is what the letterhead prints and what the golden
        # sheet writes. The operations supplier row 91 still says "Vetapet
        # Vet" until BizOps renames it — the identity check reads THIS name,
        # so the letterhead matches either way.
        supplier=SupplierSourceReference(supplier_id=91, supplier_name="C.Vetapet & Company", supplier_code=None),
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Vetapet Vet PDF price list",
        source_format=SourceFormat.PDF_TABLE,
        # SUPPORTED on the strength of the golden set at
        # tests/fixtures/catalogue_pipeline/golden/vetapet_vet — a hand-captured
        # whole-catalogue envelope replayed through the full pipeline and
        # compared field-by-field against the golden sample sheet. The non-vet
        # contract below stays PARTIALLY_VERIFIED: it has no golden evidence.
        support_status=SupplierContractSupportStatus.SUPPORTED,
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
            # Empty since the codeless-products ruling (2026-08-26): the treat
            # pages print neither a code column nor a PRODUCT NAME heading,
            # and both fields are OPTIONAL now — a document (or a re-drive
            # selection) made only of such pages must not blanket-block on
            # headers its rows are allowed to lack.
            required_headers=[],
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
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            autoswap_cost_rrp_allowed=True,
            null_cost_markers=["By Quote"],
            notes=(
                "Verified on the sample: UNIT PRICE sections price one ORDER UNIT (a "
                "box/set/pc named per row), so the basis follows the order_unit field where "
                "readable (packaging.price_basis_follows_purchase_unit) and falls back to "
                "UNIT for the PRICE/Wholesale layouts that print no order-unit column. "
                "The Wholesale/Retail family (批發價 beside 建議零售價) is claimed on the "
                "same footing since the 2026-08-25 ruling: a retail column beside the "
                "wholesale confirms the per-unit basis — even when the product text names "
                "a case ('1箱6罐'), the printed prices are per unit, never per case. "
                "Analyzers and instruments print 'By Quote' instead of a price. "
                "VERIFIED against the golden sample sheet: BizOps hand-filled a per-row "
                "price basis for every golden SKU, including rows from the bare-PRICE "
                "layouts, and the golden set (tests/fixtures/catalogue_pipeline/golden/"
                "vetapet_vet) pins each resolved basis against those answers — residual "
                "disagreements are named in its expectations.json, not hidden."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            price_basis=UnitOfMeasure(code=UnitCode.UNIT),
            purchase_uom_source_field="order_unit",
            price_basis_follows_purchase_unit=True,
            # '3 tubes / pack' and '90 capsules / bottle' print the sellable
            # count (and its noun) in the packing text itself — the Alfamedic
            # trade: the leading number is read as the count, so the content
            # measure claim is dropped rather than conflated (the schema
            # refuses one field serving as proof of both).
            sellable_units_per_purchase_unit_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Treat kg/g/ml size text as content measure, not sellable-unit count.",
                "Pack descriptions such as tubes/pack are not proof of supplier order multiple without explicit terms.",
                "ORDER UNIT names what one UNIT PRICE buys; PACKING PER UNIT describes what is inside it.",
                "A layout that prints an RRP column quotes unit prices: retail is per one "
                "of the row's item, so the cost beside it is too (ruling 2026-08-17). The "
                "bare PRICE tables all print SUGGESTED PRICE, which is why their UNIT "
                "basis is layout-confirmed rather than a fallback guess.",
            ],
            unresolved_semantics=[
                "Order increment and break-pack rules are not proven by checked-in source evidence.",
                "Rows whose order-unit column is absent, whose packing text names no known "
                "unit ('35 cm/length'), AND whose table prints no RRP column keep the "
                "declared UNIT fallback basis without layout confirmation.",
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
            # Both banner notations printed on the sampled vet pages (52/61/73/74):
            # 'Promotion: Mix over $1000, 10% off' and '混合12件 9折 24件 8折'.
            page_promotion_shapes=["mix_over_spend_percent", "mixed_quantity_percent_tiers"],
            # Topizole TOP250 prints its ladder as same-code rows whose ORDER
            # UNIT carries the band ('1-10 bottles' / '11-20 bottles'): the
            # deeper row folds into the first as a minimum-quantity term.
            quantity_band_source_field="order_unit",
            # 'Special Offer: N% off' bands strike the regular price and badge
            # the offer beside it (sample page 20: HK$1056 struck, $739 badge);
            # the cell arrives as two unmarked amounts. Larger = regular cost,
            # smaller = unconditional Special-Offer term.
            struck_price_offer_source_field="cost",
            # The indent-order sections price whole rows as quantity ladders —
            # vaccines ('PRICE: 50 doses or above (per unit)'), drugs ('PRICE
            # PER ORDER QTY: 1-19', 'PRICE: 3-9'), and single-rung 'Price: 1
            # or above' tables. Ruling 2026-08-17: lowest filled rung = base
            # cost (+ its bound as minimum order when above 1), deeper rungs
            # = minimum-quantity terms.
            quantity_ladder_heading_prefixes=["PRICE:", "PRICE PER ORDER QTY:", "Price:"],
            notes="Vetapet uses various MBB formats including 'Buy X get Y free', 'Buy X+ at special price', and percentage discounts.",
        ),
        validation_rules=_VET_VALIDATION_RULES,
        known_ambiguities=[
            # The former VETAPET_VET_MULTIPLE_TABLE_LAYOUTS ambiguity is resolved.
            # Unit Price tables carry their basis per row (ORDER UNIT); the bare
            # PRICE tables all print an RRP column, and an RRP column means the
            # price beside it is a unit price (ruling 2026-08-17, recorded in
            # interpretation_rules) — so no layout's basis is a guess anymore.
            # The golden set additionally pins the resolved basis for the PRICE-
            # layout SKUs (21501/24106/141001/109300) against the sheet's answers.
            # The former VETAPET_VET_SPECIAL_OFFER_STRUCK_PRICES ambiguity is
            # resolved by mbb.struck_price_offer_source_field above: a two-
            # amount struck-price cell now conforms as regular cost (larger)
            # plus an unconditional Special-Offer term (smaller). A cell the
            # guards refuse (equal amounts, three amounts) still dead-letters
            # for a person, and a single-amount render is indistinguishable
            # from a normal row — no banner can fix what the page never said.
            # The former VETAPET_VET_QUANTITY_BAND_ROWS ambiguity is resolved:
            # same-code closed-range band rows fold into the base row as
            # minimum-quantity terms via mbb.quantity_band_source_field above.
            # A band the fold's guards refuse (open range, overlap, unit
            # mismatch, not cheaper, unproven notation like the wound-care
            # 500-00xx piece bands) simply stays two visible candidates on the
            # desk — reviewable per row, needing no standing banner.
            # The former VETAPET_PAGE_BANNER_PROMOTIONS_UNREACHABLE ambiguity is
            # resolved, not reworded: banners now arrive as page_promotion_text and
            # the two printed notations are declared in mbb.page_promotion_shapes
            # above, so they conform into ORDER-scoped percentage terms. A banner in
            # an UNdeclared notation staying verbatim evidence is engine-wide
            # behavior, not a Vetapet ambiguity.
        ],
        pipeline_mapping=pipeline_mapping("supplier_sku", "description", "brand", "pack_size", "cost", "rrp", "promotion_text", "species", "segment", "category", "order_unit", "box_quantity", "box_wholesale_price"),
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

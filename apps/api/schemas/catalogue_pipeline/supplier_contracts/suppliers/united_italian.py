"""United Italian — the general-practice price list.

永義(香港)有限公司 distribute for 43 manufacturers — Abbott, B.Braun, BD,
Baxter, Smith & Nephew, Pentax among them — and price all of it in one
bilingual 40-page list: 26 categories, from gauze and catheters to IV fluids,
sutures, pet wipes and adult diapers.

The largest source we read, and the one that states the most per row.

WHAT THE PAGE PRINTS, AND WHAT IT MEANS

* the PRICE CARRIES ITS OWN BASIS — "$7.00 / roll", "$205.00 / box",
  "$6.00 / pc", "$37.00 / sleeve". Twenty-three different units across the
  list, abbreviated inconsistently (pc/pcs, bot/bottle, pr/pair, bx/box,
  cs/case), so the basis is read from the price cell and normalised, never
  assumed from the contract.
* SOME PRICES NAME A COUNT INSTEAD OF A CONTAINER — "$78.00 / 100's",
  "$305.00 / 50's". A hundred of something, in nothing named. Those resolve to
  a PACK of that many, on the 2026-09-01 ruling that we do not invent a vessel
  the page never printed.
* TWO PRICES ON ONE ROW, on the intravenous pages: "$46.00 / bag" beside
  "$828.00 / box". The first buys one bag, the second a box of eighteen. The
  COST is the per-unit price — BizOps recorded exactly that for AHB1323HK on
  the golden sheet — and the case price is kept beside it as a bulk term
  rather than thrown away.
* THE SECTION HEADING CARRIES THE BRAND. There is no brand column; the banner
  says "B.BRAUN - IVF 注射輸液" or "WAI YUEN TONG 位元堂". Read as evidence for
  the reviewer, never as an authoritative field — the golden sheet leaves brand
  blank on all eighteen of its rows, so nothing corroborates what we extract.

The vision pass reads this document far better than its own text layer does,
which is why the text layer is not used: it linearises the multi-column tables
into per-cell lines that no contract could map. Vision returns real columns,
splits the pack into its own cell, and keeps the two price columns apart.
"""

from __future__ import annotations

from datetime import datetime, timezone

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

_CREATED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)
_CREATED_BY = "united-italian-gp-price-list"

_UNITED_ITALIAN = SupplierSourceReference(
    supplier_id=46,
    supplier_name="United Italian Corp. (HK) Ltd",
    supplier_code="UNITEDIT",
    # The cover prints the Chinese name above the English one, and our own
    # supplier record spells it "Crop." — a typo we file under, not a different
    # company. The page identity must match all three.
    also_trades_as=(
        "永義(香港)有限公司",
        "United Italian Crop.(HK) Limited",
        "UICL",
    ),
)

_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:2025 UI GP Price List (External).pdf",
        (
            "The 2025 general-practice list, 40 pages, born digital from Word on "
            "2025-06-26. 657 priced lines across 26 categories and 43 distributed "
            "brands. Eleven pages recorded: every page carrying a golden-sheet row, "
            "plus one of each layout — the plain three-column table, the two-price "
            "intravenous pages, the '/100's' quantity basis, and a brand-headed "
            "section. 282 rows across those eleven."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "golden:united_italian",
        (
            "Four column shapes appear across the recorded pages: Code/Product/Price, "
            "the same plus a Pack column, the same with the Pack column UNLABELLED, "
            "and the intravenous shape that prints 'Price (HK$)' TWICE. The repeated "
            "heading is addressed by occurrence, the unlabelled one by source_path — "
            "neither is addressable by heading text alone."
        ),
    ),
]

UNITED_ITALIAN_GP_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="united_italian.gp_price_list.v1",
        contract_version="v1",
        supplier=_UNITED_ITALIAN,
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="United Italian general-practice price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="united_italian_price_table",
                    selector="Every product table on pages 4 onward.",
                    notes=(
                        "One row per product. Pages 1-3 are a cover, a list of the 43 "
                        "brands they distribute, and the category index — no products. "
                        "Every page repeats the same running header."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=["Code", "Product", "Pack", "Price (HK$)"],
            row_eligibility_rules=[
                "The ingestion supplier must be United Italian (supplier ID 46).",
                "A row needs a price; section banners and the running header carry none.",
                "'PRICE SUBJECT TO CHANGE WITHOUT NOTICE' and 'BONUS TERMS FOR BULK "
                "PURCHASE' head every page and are not products.",
                "A Code may name a RANGE of variants ('1208A - D', 'VITHC1210B0100100 "
                "- VITHC1210D') where one price covers several sizes. The range is "
                "recorded as printed; splitting it is a decision for the desk.",
            ],
            source_location_expectations=["source document", "page", "table row"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                # OPTIONAL: a handful of rows print no code, and the codeless
                # ruling (2026-08-26) says those are considered, not stranded.
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Code",
                description="United Italian's order code — alphanumeric, occasionally a range.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="product_name",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Product",
                description=(
                    "The description as printed, often bilingual and sometimes Chinese "
                    "only. Carries the size, and on rows with no Pack column the pack "
                    "as well."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="unit_price",
                role=SourceFieldRole.SOURCE_PRICE,
                # OPTIONAL because this list really does print products with no
                # price: page 7 lists the Vacutainer tube range by name alone.
                # Requiring one blocked 26 real products with a message about a
                # missing field, which is not what the page is saying.
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Price (HK$)",
                source_column_prefix="Price",
                # Where a row prints two prices the FIRST is the per-unit one.
                source_column_occurrence=1,
                aliases=["Price(HK$)", "Price HK$", "Price"],
                description=(
                    "What one of whatever the price names costs, with its basis attached: "
                    "'$46.00 / bag', '$205.00 / box', '$78.00 / 100's'."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="case_price",
                role=SourceFieldRole.MBB_TIER_PRICE,
                requirement=SourceFieldRequirement.OPTIONAL,
                # Addressed by POSITION and nothing else. Given any exact
                # heading, an exact match wins outright and hands this field the
                # FIRST price column — and every single-price row in the
                # catalogue then carries a case price equal to its unit price, a
                # bulk term claiming a whole box costs what one piece does. The
                # names cannot discriminate either: a page printing two prices
                # calls the second "Price (HK$) per box", and a page printing
                # one calls its only column exactly the same.
                source_column_prefix="Price",
                source_column_occurrence=2,
                tier_order=1,
                # The condition is a QUANTITY, and the pack cell states it:
                # $828.00 buys the box that "(18's / box)" describes. Reading
                # the threshold off column order instead would be a guess.
                tier_quantity_field="pack",
                description=(
                    "The whole-case price where a row prints one — '$828.00 / box' beside "
                    "'$46.00 / bag'. A bulk term, never the catalogue cost."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Pack",
                # Some tables print the pack in a column with NO heading at all;
                # heading text cannot address those.
                source_path="unlabeled_column",
                description=(
                    "What one purchase holds, as printed: '(48's / box)', "
                    "'(box of 24 rolls)', '(5's x 20pks / box)'."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="section_header",
                role=SourceFieldRole.CATEGORY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description=(
                    "The banner above the row — 'B.BRAUN - IVF 注射輸液', 'GLOVES 手套 / "
                    "Nitrile Gloves'. Carries the category and, on some pages, the brand."
                ),
                evidence=_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="unit_price",
            rrp_source_field=None,
            # No contract-level basis: this source states one PER ROW, in the
            # price cell itself, and a declared default would quietly override
            # twenty-three printed units with a guess.
            price_basis_source_field="unit_price",
            # The basis is inside the price cell, after the slash.
            price_basis_is_suffix_of_price=True,
            # Every spelling the 2025 list actually prints, counted across all
            # 40 pages. Abbreviations are the supplier's own, not ours. Anything
            # they invent next year is unmapped, and an unmapped unit holds the
            # row instead of pricing it on a guess.
            price_basis_value_map={
                "box": UnitCode.BOX.value,
                "bx": UnitCode.BOX.value,
                "case": UnitCode.CASE.value,
                "cs": UnitCode.CASE.value,
                "pc": UnitCode.PIECE.value,
                "pcs": UnitCode.PIECE.value,
                "ea": UnitCode.PIECE.value,
                "unit": UnitCode.UNIT.value,
                "bag": UnitCode.BAG.value,
                "pack": UnitCode.PACK.value,
                "pk": UnitCode.PACK.value,
                "kit": UnitCode.PACK.value,
                "set": UnitCode.PACK.value,
                "bot": UnitCode.BOTTLE.value,
                "bottle": UnitCode.BOTTLE.value,
                "vial": UnitCode.VIAL.value,
                "tube": UnitCode.TUBE.value,
                "can": UnitCode.CAN.value,
                "tin": UnitCode.CAN.value,
                # No code exists for these and inventing one would lose the
                # supplier's own word. OTHER keeps it on the row's label.
                "roll": UnitCode.OTHER.value,
                "sleeve": UnitCode.OTHER.value,
                "pair": UnitCode.OTHER.value,
                "pr": UnitCode.OTHER.value,
                "jar": UnitCode.OTHER.value,
                "sheet": UnitCode.OTHER.value,
            },
            # "$78.00 / 100's" — a hundred of something, in nothing named.
            price_basis_count_unit=UnitCode.PACK.value,
            # "*****" is how the list prints a product it will not price in
            # public — quoted by the sales desk instead. A stated refusal, not
            # an unreadable cell.
            # Two ways this list declines to publish a price, both a stated
            # refusal rather than an unreadable cell. Matched as substrings, so
            # the second catches its full bilingual form.
            null_cost_markers=["*****", "For details, please contact"],
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "The basis is printed in the price cell itself and is read from there, "
                "row by row — twenty-three distinct units across the list. A price that "
                "names a COUNT rather than a container ('$78.00 / 100's') resolves to a "
                "PACK of that many, since the page names no vessel. Where a row prints "
                "two prices the first is the cost and the second a bulk term. VERIFIED "
                "against the golden sheet, which records BOX, CASE, BAG, SLEEVE and "
                "PIECES as per-row bases and takes the per-unit price on the two-price "
                "intravenous rows."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack",
            purchase_uom=None,
            # Deliberately NOT price_basis_follows_purchase_unit: the PRICING
            # side already reads the basis out of the price cell, taking the
            # first of the two units a merged cell can carry. Letting packaging
            # re-read the same cell overrode that with the LAST unit, which
            # priced a $100 bag of Plasma-Lyte as a $100 box of twelve.
            content_measure_source_field=None,
            sellable_units_per_purchase_unit_source_field="pack",
            interpretation_rules=[
                "The purchase unit is whatever the price names, per row.",
                "The Pack cell states what one purchase holds: '(48's / box)' is 48.",
                "'(box of 24 rolls)' and '(5's x 20pks / box)' say the same thing in "
                "other words; a compound count is the product of its parts.",
                "Where no pack is printed the count is one — never a reason to hold a row.",
            ],
            unresolved_semantics=[
                "Break-pack permission is not stated by the source.",
                "A price that names a count ('/ 100's') leaves the container unprinted.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["case_price"],
            condition_patterns=["Buying a whole case rather than a single unit."],
            benefit_patterns=["A lower effective price per unit."],
            requires_validation_issue_when=[],
            notes=(
                "Every page is headed 'BONUS TERMS FOR BULK PURCHASE', but no tier, "
                "threshold or discount is printed anywhere in the document — the terms "
                "are quoted by their sales desk. The only bulk fact the page states is "
                "the case price on the intravenous rows. The cover states a minimum "
                "order of HK$500, which is a condition on the ORDER and not on any "
                "product."
            ),
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="UNITED_ITALIAN_PRICE_NAMES_A_COUNT_NOT_A_CONTAINER",
                condition="A price printed as '$78.00 / 100's' — a count, in nothing named.",
                review_guidance=(
                    "Publish as a PACK of that many. The page states the quantity and "
                    "withholds the vessel; naming one would be our invention, not the "
                    "supplier's statement. 106 of the list's priced lines read this way."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="UNITED_ITALIAN_CODE_COVERS_A_RANGE",
                condition="A Code naming several variants at one price — '1208A - D'.",
                review_guidance=(
                    "One printed row, several orderable things, one price. Recorded as "
                    "printed; whether it becomes one offering or several is the desk's "
                    "decision and cannot be read off the page."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="UNITED_ITALIAN_BRAND_IS_ONLY_IN_THE_BANNER",
                condition="Any row whose brand is stated only by the section heading.",
                review_guidance=(
                    "United Italian distribute 43 brands and print none of them in a "
                    "column. The banner is captured as evidence, but nothing corroborates "
                    "it — the golden sheet leaves brand blank on all eighteen of its "
                    "rows — so treat it as a hint for matching, not as a fact."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku", "product_name", "unit_price", "case_price", "pack",
            "section_header",
        ),
        created_at=_CREATED_AT,
        created_by=_CREATED_BY,
    )
)

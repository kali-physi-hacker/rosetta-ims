"""Alfamedic supplier-source contract declarations."""

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


_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:_alfamedic_HK_pricelist_edition 28_01Mar2026_260226_BW (1).pdf",
        "I supplied a 56-page PDF sample confirming Price List 28, 01 Mar 2026, Order Code, Product Name, Brand, Packing/Unit, Order Units, and Price/Unit headers.",
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "apps/api/services/supplier_source_contract_runtime.py",
        "Runtime adapter applies supported Pydantic source-contract semantics for order increment parsing, absent RRP, and By Quote cost handling.",
    ),
    evidence(
        SupplierSourceEvidenceType.EXISTING_PRODUCTION_TEST_EXTRACTION_FIXTURE,
        "apps/api/tests/test_supplier_source_contract_runtime.py::test_alfamedic_runtime_applies_per_piece_price_and_order_increment",
        "Tests representative Alfamedic rows against the Pydantic-backed runtime adapter.",
    ),
    evidence(
        SupplierSourceEvidenceType.BUSINESS_DOMAIN_DOCUMENTATION,
        "docs/architecture/catalogue-domain/catalogue-entity-dictionary.md",
        "Domain dictionary records current-state extraction behavior as diagnostic evidence, not canonical truth.",
    ),
]


ALFAMEDIC_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="alfamedic.price_list.v1",
        contract_version="v1",
        supplier=SupplierSourceReference(supplier_id=1, supplier_name="Alfamedic", supplier_code="ALF"),
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Alfamedic HK PDF price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            expected_sections=["therapeutic class sections"],
            table_regions=[
                SourceTableRegion(
                    name="therapeutic_class_price_rows",
                    selector="PDF tables grouped by therapeutic class",
                    notes="Raw PDF sample was supplied externally; section detail is captured from source text and parser behavior.",
                )
            ],
            required_headers=[
                "Order Code",
                "Product Name",
                "Brand",
                "Packing / Unit",
                "Price/ Unit (HKD)",
            ],
            row_eligibility_rules=["Catalogue item rows contain an order code and product name."],
            row_identity_fields=["supplier_sku"],
            discontinued_markers=["DISCON"],
            source_location_expectations=["page number", "section header", "table row", "source column"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Order Code",
                description="Stable Alfamedic order code.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                # Where no Product Name is printed — the suture tables — the
                # name is the banner plus what distinguishes this row from its
                # neighbours under it: needle, gauge, thread length. Only used
                # when the column itself is absent, so every page that prints a
                # name keeps its own.
                # One list covers every nameless shape in the document, because
                # a composed value simply omits the parts a row does not carry:
                #   p41 sutures  -> banner + needle + gauge + length
                #   p42 needles  -> circle + point + eye + size
                #   p42 ETHICON  -> material + gauge + needle, or material +
                #                   its free-text description
                # Parts are field keys, not column headings, so a run that
                # renames a heading still resolves through that field's aliases.
                composed_from=[
                    "section_header",
                    "suture_type",
                    "needle_circle_type",
                    "needle_point_type",
                    "needle_eye_type",
                    "size",
                    "needle",
                    "gauge_usp",
                    "thread_length",
                    "product_description_text",
                    # p34 collars: no name column at all, only a size and the
                    # weight range it suits.
                    "body_weight",
                    "suitable_for",
                ],
                # Size variants are listed under one merged name cell: the
                # 250ml line names the product, the 1L line below carries only
                # its own code, packing and price. Both are stocked SKUs.
                inherits_from_row_above=True,
                # Collars and body suits are listed as a family once and then
                # by size alone — "Classic Collar size 7.5cm", then "size
                # 10.0cm", "size 12.5cm". 38 rows across three pages. Separate
                # SKUs at separate prices, whose printed name does not say what
                # they are.
                continues_row_above_when_matching=r"^\s*size\b",
                source_column="Product Name",
                description="Printed product name.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Brand",
                description="Printed brand column when present.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_size",
                role=SourceFieldRole.PACKAGING,
                # Not every line in this catalogue is a packaged good. The
                # diagnostics section sells services — "Spot Platinum+ allergy
                # test", "PAX Complete Test" — which are priced per test and
                # print no packing at all. Requiring one blocked 13 real priced
                # items on a live run for lacking a field their own supplier
                # does not print. Packaging that IS printed is still read, and
                # a row whose packaging is genuinely unresolved still surfaces
                # downstream rather than being assumed.
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Packing / Unit",
                description="Raw packing text; used by the current parser to derive order increment only. Absent on services.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="cost",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Price/ Unit (HKD)",
                # The diagnostics and suture tables head this column plainly
                # "Price" — same meaning, narrower heading because those tables
                # carry no per-unit qualifier. 146 rows on the live 56-page
                # catalogue priced that way, and every one of them was read as
                # having no price at all: 900-100 Pre-anesthetic Panel prints
                # 1,760.0 and reached review as unpriced.
                aliases=["Price/ Unit (HKD)", "Price/Unit (HKD)", "Price"],
                description="Supplier cost field; By Quote is retained as a null-cost/manual-quote case.",
                evidence=_EVIDENCE,
            ),
            # The diagnostics tables carry two attributes that belong to the
            # product and to nothing else in the pipeline: what you put in the
            # test and how much of it. Declared with role OTHER so they are
            # preserved verbatim on the row without being interpreted —
            # "220μL" is the SAMPLE volume a test consumes, not the content of
            # the box, and must never be read as packaging.
            # The suture pages (41) give every row a code, a gauge, a needle
            # and a price, and name the material ONCE — in the band printed
            # across the block:
            #
            #   Surgicryl PGA Polyglycolic (Foil Packing) Violet  DS - 3/8 circle
            #   11201524 | DS24 24mm | EP 2 | USP 3/0 | 75cm | Violet | 328.0
            #
            # Captured as its own field so a name can be composed from it, and
            # so the therapeutic banner on every other page stops being thrown
            # away. Verbatim: no cleaning, no title-casing.
            SourceFieldContract(
                field_key="section_header",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description="Banner printed across the table, above its column headings.",
                evidence=_EVIDENCE,
            ),
            # Suture specifications. EP and USP are the SAME thread gauge in
            # two scales (European and US Pharmacopoeia): USP 3/0 is EP 2. Only
            # USP goes in the composed name — printing both would state the
            # thickness twice — but both are kept, because a buyer may search
            # either.
            SourceFieldContract(
                field_key="needle",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Needle",
                description="Needle code and length, e.g. 'DS24 24mm'. 'Without Needle' for reels.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="gauge_usp",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="USP",
                description="Thread gauge, US Pharmacopoeia scale.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="gauge_ep",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="EP",
                description="The same thread gauge, European Pharmacopoeia scale.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="thread_length",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Length",
                description="Thread length, e.g. '75cm'.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="thread_color",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Color",
                description="Thread colour, e.g. 'Violet', 'Undyed'.",
                evidence=_EVIDENCE,
            ),
            # Page 42's needle tables describe a product across several
            # columns instead of naming it. The heading text is not stable
            # between runs — one run returned "Product Name (Circle Type)" /
            # "(Needle Type)" / "(Eye/Size)", the next "Circle Type" /
            # "Cutting Type" / "Eye Type" / "Size" — so each declares the
            # other form as an alias and the composed name simply skips
            # whichever parts a given run did not produce.
            SourceFieldContract(
                field_key="suture_type",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Type",
                description="Suture material where it is a column rather than a banner: PDS, Vicryl.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="needle_circle_type",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Circle Type",
                aliases=["Product Name (Circle Type)"],
                description="Needle curvature: 1/2 Circle, 3/8 Circle.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="needle_point_type",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Cutting Type",
                aliases=["Product Name (Needle Type)"],
                description="Needle point geometry: Round Body, Reverse Cutting.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="needle_eye_type",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Eye Type",
                aliases=["Product Name (Eye/Size)"],
                description="Needle eye: Regular Eye (RHR/RHC/RTC/RTR), Spring Eye (SHR/STC).",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="size",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Size",
                description="Printed size: a needle's '25mm N°13', or a collar's 'XXS'.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="product_description_text",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Product Description",
                description="Free-text specification where the supplier uses it instead of a name.",
                evidence=_EVIDENCE,
            ),
            # Apparel and collar attributes. Read verbatim under role OTHER:
            # a body length is not packaging and a suitable-for range is not a
            # category, and guessing either would be worse than keeping both
            # as what the supplier printed.
            SourceFieldContract(
                field_key="body_length",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Body Length",
                description="Garment body length, e.g. '25 cm'.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="body_weight",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Body weight",
                description="Animal weight the size suits, e.g. '1-3 kg'.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="suitable_for",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Suitable for",
                description="Breeds or animals a size suits.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="colour",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Colour",
                description="Printed colour of an apparel item.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="breed",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Breed",
                description="Breed guidance printed against a size.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="order_units",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Order Units",
                aliases=["Order Unit"],
                description=(
                    "How many the line is priced for: '1 bot' on a product row, '10 bots' on the "
                    "bulk-tier line beneath it. Read verbatim; the tier logic parses the count."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="sample_type",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Sample Type",
                description="Specimen a diagnostic test accepts (whole blood / plasma / serum).",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="sample_volume",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Volume",
                description="Specimen volume one test consumes. Not pack content.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="segment",
                role=SourceFieldRole.SEGMENT,
                requirement=SourceFieldRequirement.OPTIONAL,
                constant_value="vet",
                description="Legacy configuration classifies this catalogue as vet segment.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="category",
                role=SourceFieldRole.CATEGORY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description="Therapeutic section header; any default category still requires business review.",
                evidence=_EVIDENCE,
            ),
            # Alfamedic states its bulk ladder as extra ROWS under a product,
            # where Hill's states the same thing as extra COLUMNS beside one.
            # Page 20, in printed order:
            #
            #   ALO250  ALOVEEN Shampoo   1 bot     58.0   <- the product
            #   (none)                    10 bots   56.0   <- buy 10, pay 56.0
            #   (none)                    40 bots   54.0   <- buy 40, pay 54.0
            #
            # Both halves are printed, so nothing is inferred: the quantity is
            # in Order Units and the discounted price is in the price column.
            # The identity cell is merged down the tier block, and a vision
            # model renders that two ways on the same document: sometimes
            # blank, sometimes repeating the product's code. The earlier
            # declaration named the repeated form and was right about it; the
            # blank form is just as common. Both are tiers, and reading only
            # one of them made the same SKU appear three times at three prices.
            SourceFieldContract(
                field_key="bulk_tier_rows",
                role=SourceFieldRole.MBB_TIER_ROW,
                requirement=SourceFieldRequirement.CONDITIONALLY_REQUIRED,
                source_path="a priced row beneath a product, its code blank or repeated",
                tier_quantity_field="order_units",
                tier_price_field="cost",
                description="Quantity bulk tier printed as its own row beneath the product it applies to.",
                evidence=_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="cost",
            rrp_source_field=None,
            price_basis=UnitOfMeasure(code=UnitCode.PIECE),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            null_cost_markers=["By Quote"],
            notes="Source sample and parser tests establish Price/Unit as the supplier price basis, with no RRP column.",
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack_size",
            price_basis=UnitOfMeasure(code=UnitCode.PIECE),
            order_increment_source_field="pack_size",
            break_pack_allowed=None,
            interpretation_rules=[
                "Leading count in Packing / Unit is interpreted as supplier order increment, not a price divisor.",
                "Price basis remains per sellable piece in current parser behavior.",
            ],
            unresolved_semantics=[
                "Purchase UOM and break-pack permission are not proven by checked-in source evidence.",
                "Packing text may contain content/count language and must not be treated as canonical packaging without review.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=["bulk_tier_rows"],
            condition_patterns=["multiple rows for the same order code"],
            benefit_patterns=["discounted unit price tiers"],
            requires_validation_issue_when=[
                "Rows with repeated order codes do not clearly state tier quantity, basis, or effective period."
            ],
            notes="Multi-row bulk tiers require later supplier-specific parsing evidence before automated normalization.",
        ),
        validation_rules=[
            SupplierValidationRule(
                rule_id="alfamedic.cost_positive_when_present",
                description="Numeric cost must be positive; By Quote is null and reviewed manually.",
                source_expression="cost_price > 0",
                severity=IssueSeverity.ERROR,
                issue_code="ALFAMEDIC_COST_NOT_POSITIVE",
                review_guidance="Confirm whether the row is By Quote or whether the supplier cost was misread.",
                evidence=_EVIDENCE,
            ),
            SupplierValidationRule(
                rule_id="alfamedic.order_increment_positive",
                description="Parsed order increment must be positive when present.",
                source_expression="order_increment_qty >= 1",
                severity=IssueSeverity.ERROR,
                issue_code="ALFAMEDIC_ORDER_INCREMENT_NOT_POSITIVE",
                review_guidance="Confirm the Packing / Unit text before approving the ordering terms.",
                evidence=_EVIDENCE,
            ),
        ],
        known_ambiguities=[
            AmbiguityRule(
                issue_code="ALFAMEDIC_MBB_TIER_BASIS_UNVERIFIED",
                condition="Repeated order-code rows may represent bulk tiers, but tier condition and benefit semantics are not fully evidenced.",
                review_guidance="Confirm how Alfamedic tier rows specify minimum quantity and discounted price before normalizing MBB.",
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "description",
            "brand",
            "pack_size",
            "cost",
            "segment",
            "category",
            "bulk_tier_rows",
        ),
        created_at=DECLARATION_CREATED_AT,
        created_by=DECLARATION_CREATED_BY,
    )
)

"""Covetrus — the branded-products catalogue, sold to us by Kruuse Hong Kong.

Two documents reach us from supplier 62. The first is ProVet's plain price
list; this is the other — a 32-page illustrated catalogue of Covetrus- and
Cvet-branded consumables, which is mostly pictures and specifications and, in
eleven of its fifty-four tables, a price list.

Only those eleven are read. The rest describe products by dimension, gauge,
colour and quantity per box and name no price at all, which makes them a
reference for a buyer rather than something to cost.

The priced tables come in two shapes and this contract reads both:

    ItemNumber  | Description | List Price HK$ | Page Number
    Provet Code | Description | Pack Size      | Price

The Page Number is a cross-reference into the catalogue's own illustrated
pages, not a fact about the product. It is captured so a reviewer can turn to
the picture, and never interpreted.

WHAT THE PAGE DOES NOT SAY:

* NO BASIS. One price against one product, and nothing about what the money
  buys. Read as the generic PACK, on the same 2026-09-03 ruling ProVet's list
  stands on — a distributor price list prices the pack.
* THE COUNT IS IN THE DESCRIPTION, at the end: "Luer centre 3ml 100s",
  "Scalpel Blades # 10 Carbon 100s". Only that form is read. The Pack Size
  column, where it appears, holds a MEASURE ("20mL") and is content rather
  than a count of things.
"""

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

_CREATED_AT = datetime(2026, 9, 6, tzinfo=timezone.utc)
_CREATED_BY = "covetrus-branded-products-catalogue"

_KRUUSE = SupplierSourceReference(
    supplier_id=62,
    supplier_name="ProVet Kruuse HK",
    supplier_code="PROVETKR",
    # This document heads itself KRUUSE HONG KONG LIMITED and prices two code
    # families — PV- for ProVet, CV- for Cvet. One company, two ranges, and the
    # golden sheet files the same supplier under a third name again.
    also_trades_as=(
        "KRUUSE HONG KONG LIMITED", "Kruuse Hong Kong Ltd",
        "Provet Hong Kong", "Kruuse", "Provet", "Covetrus",
    ),
)

_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:Covetrus Catalogue 2025.pdf",
        (
            "The Covetrus branded-products catalogue, 32 pages. 122 priced rows across "
            "eleven price tables — syringes, needles, IV infusion, catheters, wound care, "
            "gloves, surgical consumables and the Maxicam injection — every one carrying "
            "an item number. The remaining 43 tables carry no price."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "golden:covetrus",
        (
            "The priced tables come in two shapes, one headed 'ItemNumber / Description / "
            "List Price HK$ / Page Number' and one 'Provet Code / Description / Pack Size "
            "/ Price', so both spellings of each column are declared. The 43 unpriced "
            "tables use a dozen further shapes (Item / Description / Dimensions / "
            "Quantity and variations) and SHARE the Description column with the priced "
            "ones — so what keeps them out is row_requires_any_field, not their columns. "
            "Many of them describe products priced elsewhere in the same document, so "
            "reading them would duplicate the catalogue against itself."
        ),
    ),
]

COVETRUS_BRANDED_PRODUCTS_CATALOGUE_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="covetrus.branded_products_catalogue.v1",
        contract_version="v1",
        supplier=_KRUUSE,
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Covetrus branded-products catalogue (via Kruuse Hong Kong)",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="covetrus_price_tables",
                    selector="The eleven tables that carry a price column.",
                    notes=(
                        "One row per product. The catalogue's other 43 tables list "
                        "dimensions, gauges, colours and quantities per box with no price; "
                        "they answer none of this contract's columns and are skipped."
                    ),
                )
            ],
            required_headers=[],
            # The descriptive tables share a Description column with the priced
            # ones, so nothing about a row's columns tells them apart. What does:
            # a price table gives every row an item number and a price, and the
            # picture pages give neither.
            row_requires_any_field=("supplier_sku", "unit_price"),
            optional_headers=[
                "ItemNumber", "Item Number", "Provet Code", "Description",
                "List Price HK$", "Price", "Pack Size", "Page Number",
            ],
            row_eligibility_rules=[
                "The ingestion supplier must be ProVet Kruuse HK (supplier ID 62).",
                "A row needs an item number and a price.",
                "A table with no price column is a specification, not a price list.",
                "Page Number cross-references the catalogue's illustrated pages and is "
                "never a fact about the product.",
            ],
            source_location_expectations=["source document", "page", "table row"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="ItemNumber",
                # CV- for Cvet, PV- for ProVet: two ranges, one company, and the
                # header is spelled differently on the table that carries PV-.
                aliases=["Item Number", "Item No", "Provet Code", "Code"],
                description="The order code — CV- for the Cvet range, PV- for ProVet's own.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="product_name",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Description",
                aliases=["Product", "Product Description"],
                description=(
                    "The product, its size and its pack in one string: 'Cvet 3-part "
                    "Syringes Luer centre 3ml 100s'."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="unit_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="List Price HK$",
                aliases=["Price", "List Price", "List Price (HK$)", "List Price HKD"],
                description="What one pack costs, in HKD. Printed '$54.00' or 'HKD$258'.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Description",
                aliases=["Product", "Product Description"],
                description=(
                    "The count at the end of the description — the '100s' of 'Luer centre "
                    "3ml 100s'. Only the count form is read."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="content_measure",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Pack Size",
                description=(
                    "How much is inside, on the one table that prints it: '20mL', '100mL'. "
                    "A measure, and never a count of things."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="catalogue_page",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Page Number",
                description=(
                    "Where the illustrated entry sits in the catalogue — sometimes a range "
                    "('17 & 19'). Kept so a reviewer can turn to the picture; never read as "
                    "a quantity."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="section_header",
                role=SourceFieldRole.CATEGORY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description=(
                    "The banner above the table — 'Price List — SYRINGES', 'Price List — "
                    "SURGICAL GLOVES'. The catalogue's own grouping."
                ),
                evidence=_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="unit_price",
            rrp_source_field=None,
            price_basis=UnitOfMeasure(code=UnitCode.PACK),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "One price per product line, in HKD, and the page states no basis. Read as "
                "the generic PACK on the 2026-09-03 ruling that a distributor price list "
                "prices the pack — $54.00 buys the box of a hundred syringes, not one "
                "syringe. No container is printed either, so PACK rather than a vessel "
                "nobody named. No RRP and no bulk column anywhere in the document."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack",
            purchase_uom=UnitOfMeasure(code=UnitCode.PACK),
            sellable_units_per_purchase_unit_source_field="pack",
            content_measure_source_field="content_measure",
            # "100s", "50s" — a number and the letter s as its own token. A
            # measure ("3ml", "20mL") cannot match, which is the point.
            packaging_text_pattern=r"\b(\d[\d,]*\s*s)\b",
            interpretation_rules=[
                "The count lives at the end of the description, not in a column.",
                "Only the 'Ns' form is a count; a measure is content and is left alone.",
                "A description stating no count is one pack of one thing.",
            ],
            unresolved_semantics=[
                "The container is not printed; the basis is the generic PACK.",
                "Break-pack permission is not stated by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=[],
            condition_patterns=[],
            benefit_patterns=[],
            requires_validation_issue_when=[],
            notes="No bulk column, no tier table and no quantity break anywhere in the catalogue.",
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="COVETRUS_CATALOGUE_IS_MOSTLY_UNPRICED",
                condition=(
                    "The 43 tables of this catalogue that list dimensions, gauges, colours "
                    "and quantities per box without a price."
                ),
                review_guidance=(
                    "Expected. This document is a picture catalogue with eleven price "
                    "tables in it, not a price list throughout. Those rows answer none of "
                    "the declared columns and never reach the desk; a product visible in "
                    "the catalogue and absent from the run is unpriced, not delisted."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="COVETRUS_TWO_CODE_FAMILIES_ONE_SUPPLIER",
                condition="Rows coded CV- (Cvet) alongside rows coded PV- (ProVet).",
                review_guidance=(
                    "One company sells both ranges and this catalogue prices them together. "
                    "The prefix is the brand, not a different supplier, and both belong to "
                    "supplier 62 like ProVet's own price list."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku", "product_name", "unit_price", "pack", "content_measure",
            "catalogue_page", "section_header",
        ),
        created_at=_CREATED_AT,
        created_by=_CREATED_BY,
    )
)

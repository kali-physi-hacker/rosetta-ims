"""ProVet Kruuse — the Hong Kong price list.

The plainest source we hold, and the only one with nothing clever about it: a
spreadsheet printed to PDF, three columns, one price per line.

    Product Code | Description | List Price (HKD$)

Sectioned into Dangerous Drugs and Psychotropics, Normal Drugs and Supplements,
which is the only categorisation the page offers and worth keeping — a schedule
drug is not stocked or handled like a supplement.

WHAT THE PAGE DOES NOT SAY, and what this contract does about it:

* IT NAMES NO BASIS. One price sits beside one product and nothing states what
  the money buys. Read as the generic PACK on the user's ruling of 2026-09-03:
  $90.00 buys the box of four Cerenia tablets, not one tablet. The golden sheet
  records "1 Tab" for that row, which would make the box $360 — but its basis
  column is visibly malformed on these rows ("1 1 Set", inconsistent case), so
  the page and a plain reading of a distributor's list win.
* IT NAMES NO CONTAINER either, so PACK is declared rather than a vessel
  nobody printed — the same ruling AVM's list stands on.
* THE PACK COUNT IS IN THE DESCRIPTION, at the end: "Tablets 4s", "Patch 5s",
  "Tablets 100s". Only the count form is read. "Injection 20ml" and "Sachet
  50g" are CONTENT, not a count of things, and reading twenty millilitres as
  twenty sellable units would divide a price by twenty.

THE DOCUMENT CONTRADICTS ITSELF ON ONE ROW. CERE60 (Cerenia 60mg Tablets 4s)
is printed twice on page 2, once at $174.00 and once at $198.00, with the same
description both times. Nothing on the page says which supersedes the other,
and a reader that takes the first or the last is choosing by accident. Both
reach the desk and a person decides.
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

_CREATED_AT = datetime(2026, 9, 3, tzinfo=timezone.utc)
_CREATED_BY = "provet-kruuse-hk-price-list"

_PROVET = SupplierSourceReference(
    supplier_id=62,
    supplier_name="ProVet Kruuse HK",
    supplier_code="PROVETKR",
    # The golden sheet files these rows under "Kruuse Hong Kong Ltd" and the
    # documents head themselves "Provet Hong Kong". One company, three ways of
    # writing it, and a page identity must match whichever it prints.
    also_trades_as=("Kruuse Hong Kong Ltd", "Provet Hong Kong", "Kruuse", "Provet"),
)

_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:Provet Hong Kong Price List - Year 2025 v2.pdf",
        (
            "The 2025 price list, revision 2, four pages printed from Excel on "
            "2025-08-13. 192 rows across three sections, every one carrying a product "
            "code, a description and a price. All ten of the coded rows on the BizOps "
            "golden sheet were found here and agree to the cent."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "golden:provet_kruuse",
        (
            "The header is written THREE ways across four pages, and which way "
            "depends on the reading: 'Product Code / Description / List Price (HKD$)', "
            "the shorter 'Code / Description / Price', and 'Code / Product / Price' — "
            "the last seen only on a live ingestion, where 71 rows lost their name "
            "before it was declared. CERE60 appears TWICE on page 2 "
            "at two different prices; the recorded envelope keeps both rows rather than "
            "collapsing them, which is what lets a person see the contradiction."
        ),
    ),
]

PROVET_KRUUSE_HK_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="provet_kruuse.hk_price_list.v1",
        contract_version="v1",
        supplier=_PROVET,
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="ProVet Kruuse Hong Kong price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="provet_price_table",
                    selector="The price table on every page.",
                    notes=(
                        "One row per product, under a section banner naming the drug "
                        "schedule. No page carries anything but the table and its header."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=[
                "Product Code", "Code", "Description", "Product",
                "List Price (HKD$)", "Price",
            ],
            row_eligibility_rules=[
                "The ingestion supplier must be ProVet Kruuse HK (supplier ID 62).",
                "A row needs a product code and a price; the section banner has neither.",
                "A code printed twice is two rows, not one. Where the prices differ the "
                "page is contradicting itself and both must reach a person.",
            ],
            source_location_expectations=["source document", "page", "table row"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Product Code",
                aliases=["Code", "Item Code", "產品編號"],
                description="ProVet's order code — on every row of the list.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="product_name",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Description",
                # THREE spellings of this heading have been seen across four
                # pages of one document — "Description" on two, "Product" on a
                # third, and the reading varies run to run. A live ingestion
                # headed page 2 "Code | Product | Price" and, with only
                # "Description" declared, all 71 rows on it lost their name and
                # blocked as a missing required field. Matching is on the exact
                # folded heading, so "Product" cannot capture "Product Code".
                aliases=["Product", "Product Description", "Item Description", "產品"],
                description=(
                    "The product, its strength and its pack, in one string: "
                    "'Cerenia 16mg Tablets 4s', 'Methone 10mg/ml Injection 20ml'."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="unit_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="List Price (HKD$)",
                aliases=["Price", "List Price", "List Price (HKD)"],
                description="What one pack costs, in HKD. The page states no other price.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Description",
                aliases=["Product", "Product Description", "Item Description"],
                description=(
                    "The count at the end of the description — the '4s' of 'Tablets 4s'. "
                    "Only the count form is read; a measure is content, not a count."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="section_header",
                role=SourceFieldRole.CATEGORY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description=(
                    "The banner above the row: Dangerous Drugs and Psychotropics, Normal "
                    "Drugs, Supplements. The only categorisation the page offers, and a "
                    "schedule drug is not stocked or handled like a supplement."
                ),
                evidence=_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="unit_price",
            rrp_source_field=None,
            price_basis=UnitOfMeasure(code=UnitCode.PACK),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            # "Please contact us" is a stated refusal to publish a price, not an
            # unreadable cell. Ten rows carry it, and every one is Zoetis —
            # Cytopoint, Solensia, Beransa — which is the range Queen's Pharma
            # price for us. ProVet list them and decline to quote them here.
            null_cost_markers=["Please contact us"],
            notes=(
                "One price per product line, in HKD, and the page states no basis at all. "
                "Read as the generic PACK on the user's ruling of 2026-09-03: $90.00 buys "
                "the box of four Cerenia tablets, not one tablet. The container is not "
                "printed either, so PACK rather than a vessel nobody named. No RRP is "
                "published and no second price column exists."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack",
            purchase_uom=UnitOfMeasure(code=UnitCode.PACK),
            sellable_units_per_purchase_unit_source_field="pack",
            # "Tablets 4s", "Patch 5s", "Tablets 100s" — a number and the letter s,
            # standing as its own token. "Injection 20ml" and "Sachet 50g" cannot
            # match, which is the point: twenty millilitres is not twenty things.
            packaging_text_pattern=r"\b(\d[\d,]*\s*s)\b",
            interpretation_rules=[
                "The count lives at the end of the description, not in a column.",
                "Only the 'Ns' form is a count. A measure is content and is left alone.",
                "A description that states no count is one pack of one thing.",
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
            notes=(
                "No bulk column and no tier table. One row hides a term in its "
                "description — TEMVET10 reads 'Injection 10ml ($162 for 12 or up)' — "
                "which is a real quantity break written in prose on a single line out of "
                "192. It is left in the product name where a reviewer will see it rather "
                "than parsed out of English on the strength of one example."
            ),
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="PROVET_DECLINES_TO_PRICE_THE_ZOETIS_RANGE",
                condition=(
                    "A row reading 'Please contact us' instead of a price — all ten are "
                    "Zoetis: Cytopoint, Solensia and Beransa."
                ),
                review_guidance=(
                    "A stated refusal, not a missing price. That range is priced for us by "
                    "Queen's Pharma, whose own contract carries it; ProVet list it here "
                    "without quoting. Nobody need chase ProVet for a number they have "
                    "declined to print."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="PROVET_CODE_PRINTED_TWICE_AT_DIFFERENT_PRICES",
                condition=(
                    "One product code printed on two rows with two different prices — "
                    "CERE60 (Cerenia 60mg Tablets 4s) at $174.00 and $198.00 on page 2."
                ),
                review_guidance=(
                    "The page contradicts itself and says nothing about which supersedes. "
                    "Both rows reach the desk; confirm the price with ProVet rather than "
                    "taking whichever the reader happened to see last. The golden sheet "
                    "records $198.00, which is a record of someone's decision and not a "
                    "reading of this page."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="PROVET_CODE_PRINTED_TWICE_AT_THE_SAME_PRICE",
                condition="One product code on two rows agreeing on price — ALUTAB600.",
                review_guidance=(
                    "A duplicated line, not a contradiction. One product; publish it once."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="PROVET_PRODUCT_LIST_IS_NOT_PRICED",
                condition=(
                    "A product ProVet sell that this price list does not carry — "
                    "Bactroban, Panacur and Doxycycline paste among them."
                ),
                review_guidance=(
                    "ProVet issue a separate 'Product list' — 765 names, no codes and no "
                    "prices — and a Covetrus catalogue whose 54 tables include 11 priced "
                    "ones this contract does not read. Between them they name products "
                    "this price list does not carry, so a product missing from it is a "
                    "price we have not been given, never evidence of a delisting."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku", "product_name", "unit_price", "pack", "section_header",
        ),
        created_at=_CREATED_AT,
        created_by=_CREATED_BY,
    )
)

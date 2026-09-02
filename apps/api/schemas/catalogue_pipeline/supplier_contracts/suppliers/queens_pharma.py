"""Queen's Pharma — the Zoetis price list and pre-order form.

Queen's distributes Zoetis and prices it on a form a clinic is meant to fill in
and send back: a header for the date and clinic, a product table, and an empty
Order column for the quantities. We read the table and nothing else.

The forms arrive as photographs, one per product range and each with its own
effective date, and their table is the same every time:

    [Form] | Brand | Ingredients | Pack Size | Unit | PriceHK$ | Order

What this source states, and what it does not:

* it prints NO ITEM CODE. There is no column for one, on any form. Rows conform
  without identity and take it at the match, on the 2026-08-26 ruling — the
  same footing Vetapet stands on. Every Queen's row therefore reaches a person.
* the price buys what the ORDER column names — a Box, or a Set. That is the
  purchase unit and the price basis.
* the UNIT column says how many sellable things that purchase holds: "2vials
  per box" is two, "1 set" and "1 box" are one.
* the PACK SIZE column is a CONTENT MEASURE and never a count. "1ml/bot" is how
  much is in a vial; "50's/box" is what is in the box. Reading 50 as a sellable
  count would turn a $350 box into a $7 strip, so the two columns are declared
  separately and the schema refuses to let one stand in for the other.

The sell side is deliberately absent from this contract. What a channel charges
for, and in what multiples, is per-channel operations data — it is not a fact
about Queen's price list, and inferring it from a supplier's packaging is how a
buying fact ends up deciding a selling price (user ruling 2026-09-02).
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

_CREATED_AT = datetime(2026, 9, 2, tzinfo=timezone.utc)
_CREATED_BY = "queens-pharma-zoetis-price-list"

_QUEENS = SupplierSourceReference(
    supplier_id=63,
    supplier_name="Queen's Pharma Limited",
    supplier_code="QUEENSPH",
    # Every Queen's form prints "Distributor for zoetis" beneath the logo, and
    # a vision pass sometimes returns only that fragment as the page's identity
    # — dropping the "QUEEN'S PHARMA" above it. Read literally that names a
    # company we do not buy from, so the identity check blocked the whole
    # Cytopoint page while the other two forms went through: the same document,
    # ingested twice, can pass once and fail once.
    #
    # Zoetis rather than the whole phrase, because "distributor" is not an
    # identity stopword and declaring it would let any page saying
    # "<anyone> Distributor" vouch for Queen's. Zoetis vouches for nothing else
    # we ingest, and Queen's is the only route we buy it by.
    also_trades_as=("Zoetis",),
)

_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:Queen's Pharma PRICE LIST AND PRE ORDER FORM (images)",
        (
            "Three real forms, read on 2026-09-02: Cytopoint (2024, from 1 October), "
            "Librela and Solensia (2025, from 1 January), and AlphaTRAK 3 (2024, from "
            "1 August). Ten products, every one carrying a brand, a pack, a unit and a "
            "price, and not one carrying an item code. Six of the ten are on the BizOps "
            "golden sheet and agree with the forms to the dollar."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "golden:queens_pharma",
        (
            "Recorded vision envelopes for all three forms. The Cytopoint and AlphaTRAK "
            "forms print a leading Form column of syringe icons; the Librela form does "
            "not, so the table is six columns there and seven elsewhere. Neither the "
            "column nor its position is required — the header names are."
        ),
    ),
]

QUEENS_PHARMA_ZOETIS_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="queens_pharma.zoetis_price_list.v1",
        contract_version="v1",
        supplier=_QUEENS,
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Queen's Pharma price list and pre-order form (Zoetis)",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="queens_zoetis_price_table",
                    selector="The single product table on each form.",
                    notes=(
                        "One row per product. The form's header (date, clinic, ordered-by) "
                        "and its footer (MOQ, delivery charge, contacts) are not products."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=["Form", "Brand", "Ingredients", "Pack Size", "Unit", "PriceHK$", "Order"],
            row_eligibility_rules=[
                "The ingestion supplier must be Queen's Pharma Limited (supplier ID 63).",
                "A row needs a brand and a price; the header and footer carry neither.",
                "The Form column holds a dosage-form icon and is absent from some forms. "
                "Neither its presence nor the column count decides whether a row is real.",
                "A quantity written into the Order column is a clinic's order, not "
                "catalogue data, and is never read as one.",
            ],
            source_location_expectations=["source document", "page", "table row"],
        ),
        fields=[
            SourceFieldContract(
                field_key="product_name",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Brand",
                description=(
                    "The product and its strength, as printed: 'CYTOPOINT 40mg', "
                    "'SOLENSIA 7MG'. With no item code this is what a reviewer matches on."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="unit_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="PriceHK$",
                description="What one purchase unit costs, in HKD. Printed with a thousands comma.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="purchase_unit",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Order",
                # SET is not a unit the system knows, and a starter kit is one
                # pack of components sold together — the same generic container
                # AVM's list resolves to when the page names no vessel. Mapped
                # rather than added to the shared vocabulary, so the day SET
                # earns its own code this is one line to retire.
                value_map={"Set": "PACK", "SET": "PACK", "set": "PACK"},
                description=(
                    "What the price buys and what a clinic orders — Box or Set. This is "
                    "the price basis; it is not a quantity."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="units_per_purchase_unit",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Unit",
                description=(
                    "How many sellable things one purchase holds: '2vials per box' is two, "
                    "'1 set' and '1 box' are one. Where the form does not say, it is one — "
                    "an unstated count is not a reason to hold a row."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="content_measure",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Pack Size",
                description=(
                    "How much is inside — '1ml/bot', '50's/box', '1 set/box'. A content "
                    "measure, never a sellable count."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="ingredients",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Ingredients",
                description=(
                    "The active substance and strength, or for a device what is in the box. "
                    "Kept as evidence for the reviewer matching a codeless row."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                requirement=SourceFieldRequirement.OPTIONAL,
                constant_value="Zoetis",
                description=(
                    "Queen's is the distributor; Zoetis is the manufacturer and the brand "
                    "on every product they have sent us. The forms say so in their header."
                ),
                evidence=_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="unit_price",
            rrp_source_field=None,
            price_basis=UnitOfMeasure(code=UnitCode.BOX),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "One price per row, in HKD, buying one of whatever the Order column names. "
                "That is BOX on every injectable row and on the test strips, and SET on the "
                "AlphaTRAK starter kit. The BizOps golden sheet records the same reading for "
                "all six of its Queen's rows: basis 1 BOX, two vials to the box. No form "
                "states a recommended retail price and none states a bulk or tiered term."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="units_per_purchase_unit",
            # The Order column names the purchase unit PER ROW — Box on the
            # injectables and the test strips, Set on the AlphaTRAK kit. Pinning
            # a single BOX here would price a $1,250 starter kit as a box.
            purchase_uom_source_field="purchase_unit",
            price_basis_follows_purchase_unit=True,
            sellable_units_per_purchase_unit_source_field="units_per_purchase_unit",
            content_measure_source_field="content_measure",
            order_increment_source_field=None,
            interpretation_rules=[
                "The Unit column states how many sellable things one purchase holds.",
                "'2vials per box' is two. '1 set' and '1 box' are one.",
                "Where the count is not stated it is one — never a reason to hold a row.",
                "Pack Size is a content measure and is never read as a count: '50's/box' "
                "describes what is in the box, and the box is still one purchase.",
                "An Order unit of 'Set' is read as one PACK: a starter kit is a pack of "
                "components bought together, and SET is not a unit this system knows.",
            ],
            unresolved_semantics=[
                "Break-pack permission is not stated by the source.",
                "The supplier's own order multiple is not stated; the form asks for a "
                "quantity of boxes and names no minimum per line.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=[],
            condition_patterns=[],
            benefit_patterns=[],
            requires_validation_issue_when=[],
            notes=(
                "No bulk or promotional pricing. The forms carry one order-level term — "
                "delivery is free over HKD1,000 and costs HKD50 below it — which is a "
                "shipping condition on the order, not a price break on a product."
            ),
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="QUEENS_ROW_HAS_NO_SUPPLIER_CODE",
                condition="Every row. No Queen's form prints an item code.",
                review_guidance=(
                    "Expected, not a defect. Match the row to a product by its brand and "
                    "strength; the entity's Rosetta SKU becomes the offering identity at "
                    "apply. Ten products across three forms, so this is a short job."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="QUEENS_FORM_IS_ALSO_AN_ORDER_FORM",
                condition=(
                    "A form that has been filled in and signed by a clinic before it "
                    "reached us, carrying handwritten quantities in the Order column."
                ),
                review_guidance=(
                    "Read the table, ignore the handwriting. The printed prices are the "
                    "list and are identical on the blank and filled copies we hold; the "
                    "quantities belong to whoever placed that order."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "product_name", "unit_price", "purchase_unit",
            "units_per_purchase_unit", "content_measure", "ingredients", "brand",
        ),
        created_at=_CREATED_AT,
        created_by=_CREATED_BY,
    )
)

"""Asia Vet Medical — the consumables list.

AVM's third document, and the one asked for first. Thirteen pages of tubes,
syringes, feeding catheters, anaesthesia circuits and autoclave bags, sectioned
forty-eight ways, in two columns and nothing else:

    Item Description | Unit Price (HK$)

It costs nothing to read — the text is machine-readable on every page — and it
is the plainest table we hold. What makes it interesting is what has been
folded into that first column, because the page has no room for anything else:

* THE MANUFACTURER'S CODE, in brackets at the end. "Mila Nasogastric Feeding
  Tube 6Fr x 55cm (22in) (NG622)" is item NG622. 264 of the 460 rows carry one
  and the rest genuinely do not. It is read out of the description rather than
  left buried there, because a code is the join a cost reconciliation needs and
  a name is not.
* THE PACK, as a count and the thing counted: "100 pcs/pack", "12 rolls/box".
  91 rows state one; the rest are a single item.

Only a code carrying a DIGIT is read as one. The same brackets hold colours and
dimensions — "(Blue)", "(Orange)", "(22in)" — and a tube whose supplier code
came out as "Blue" would be worse than one with no code at all.

The page states no price basis anywhere, so a price buys the pack, on the same
ruling ProVet's and Covetrus' lists stand on.
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
_CREATED_BY = "avm-consumables-price-list"

_AVM = SupplierSourceReference(
    supplier_id=3,
    supplier_name="Asia Vet Medical Limited",
    supplier_code="AVM",
)

#: A bracketed token carrying a digit, at the very end of the description.
#: The digit is what separates a code from the colours and dimensions printed
#: in the same brackets — "(Blue)", "(Orange)", "(22in)" are not item codes.
_CODE_IN_BRACKETS = r"\(([A-Za-z][\w.-]*\d[\w.-]*)\)\s*$"

#: "100 pcs/pack", "12 rolls/box" — a count and the thing counted, before the
#: slash. Captured with its noun so the count reads off the front of it.
_PACK_IN_DESCRIPTION = r"(\d[\d,]*\s*(?:pcs?|rolls?|pairs?|tests?|sheets?))\s*/\s*(?:pack|box|bag|pkt|case)"

_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "external-sample:Price List_(AVM) Consumable items list_20Oct2025.pdf",
        (
            "AVM's consumables list of 20 October 2025, thirteen pages, 460 rows across "
            "48 sections, every one carrying a description and a price. Machine-readable "
            "throughout, so it costs no vision provider to read — unlike AVM's "
            "VetriScience list, which is a photograph."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "golden:avm_consumables",
        (
            "One column shape on all thirteen pages: 'Item Description' and 'Unit Price "
            "(HK$)'. There is no code column and no pack column; both are folded into the "
            "description, and both are read out of it by declared pattern. 264 of the 460 "
            "rows yield an item code and 91 a pack count; the remainder state neither, "
            "which the page is entitled to do."
        ),
    ),
]

AVM_CONSUMABLES_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="asia_vet_medical.consumables_price_list.v1",
        contract_version="v1",
        supplier=_AVM,
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Asia Vet Medical consumables price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="avm_consumables_table",
                    selector="The two-column table running through all thirteen pages.",
                    notes=(
                        "One row per product under a section banner naming the clinical "
                        "area. The running header repeats on every page and is not a row."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=["Item Description", "Description", "Unit Price (HK$)", "Unit Price"],
            row_requires_any_field=("product_name",),
            row_eligibility_rules=[
                "The ingestion supplier must be Asia Vet Medical Limited (supplier ID 3).",
                "A row needs a description and a price; the section banner has neither.",
                "This list covers consumables only. AVM's VetriScience supplements are a "
                "separate document with its own contract, and the two never overlap.",
            ],
            source_location_expectations=["source document", "page", "table row"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                # OPTIONAL: 196 of the 460 rows print no code at all, and the
                # 2026-08-26 ruling says those are considered, not stranded.
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Item Description",
                aliases=["Description"],
                source_pattern=_CODE_IN_BRACKETS,
                description=(
                    "The manufacturer's code, printed in brackets at the end of the "
                    "description — NG622, E1030, J322a2. Only a bracketed token carrying a "
                    "digit qualifies: the same brackets hold colours and dimensions, and a "
                    "supplier code reading 'Blue' is worse than none."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="product_name",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Item Description",
                aliases=["Description"],
                description=(
                    "The product, its size, its pack and often its code, in one string: "
                    "'Lithium Heparin Tube (Orange), 1.3ml, 100 pcs/pack'."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="unit_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Unit Price (HK$)",
                aliases=["Unit Price", "Price (HK$)", "Price"],
                description="What one pack costs, in HKD. The page prints no other price.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Item Description",
                aliases=["Description"],
                description=(
                    "The count and the thing counted, before the slash: the '100 pcs' of "
                    "'100 pcs/pack'. A row stating none is one item."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="section_header",
                role=SourceFieldRole.CATEGORY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section_header",
                description=(
                    "The banner above the row, naming the clinical area: 'Clinical "
                    "Laboratory Consumables', 'Feeding Tubes Mila', 'Autoclave Bags'. "
                    "Forty-eight of them across the list."
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
                "One price per row, in HKD, and the page states no basis. Read as the "
                "generic PACK on the 2026-09-03 ruling — HK$270.00 buys the pack of a "
                "hundred heparin tubes, not one tube. The container is not printed either, "
                "so PACK rather than a vessel nobody named. No RRP, no second price and no "
                "bulk column anywhere in the thirteen pages."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack",
            purchase_uom=UnitOfMeasure(code=UnitCode.PACK),
            sellable_units_per_purchase_unit_source_field="pack",
            packaging_text_pattern=_PACK_IN_DESCRIPTION,
            interpretation_rules=[
                "The pack lives inside the description, before a slash: '100 pcs/pack'.",
                "The count is read from the front of that phrase and the noun is kept.",
                "A description stating no pack is one item, which is not a reason to hold it.",
                "A size printed in the same string ('1.3ml', '6Fr x 55cm') is the product's "
                "dimensions and is never a count of things.",
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
            notes="No bulk column, no tier table and no quantity break in the document.",
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="AVM_CONSUMABLES_ROW_HAS_NO_CODE",
                condition=(
                    "A row whose description carries no bracketed code — 196 of the 460, "
                    "the laboratory tubes and pipettes among them."
                ),
                review_guidance=(
                    "Expected. The page has no code column and only some products carry "
                    "the manufacturer's. Those rows match on their description, and take "
                    "their identity at the match like any other codeless row."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="AVM_CONSUMABLES_CODE_IS_THE_MANUFACTURERS",
                condition="Any row whose code was read out of the description.",
                review_guidance=(
                    "The bracketed code is the MANUFACTURER's — Mila's NG622, J-brand's "
                    "J322a2 — and not necessarily what AVM would put on an invoice. Good "
                    "enough to match on and worth confirming before it is relied on for "
                    "reconciliation."
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

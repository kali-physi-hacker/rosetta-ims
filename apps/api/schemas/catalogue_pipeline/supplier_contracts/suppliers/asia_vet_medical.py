"""Asia Vet Medical — the VetriScience supplement price list.

AVM sends more than one list; this one covers the VetriScience Laboratories
supplement range and nothing else. It is the best-conditioned source we hold: a
real five-column table, sectioned by health category, with an item code on
every row.

Two things the page states that most suppliers leave us to infer:

* the SPECIES, in its own words — "Canine", "Feline", "Canine + Feline". Every
  other contract derives species from a product name, and where that fails the
  system asks a model with web search. A declared column beats both.
* the PACKAGE, as a quantity and the thing counted — "180 Capsules",
  "60 Bite-Sized Chews" — or as a measure, "30ml Liquid", "16oz Powder".

What the page does NOT state is the container. It says "180 Capsules", never
"bottle" or "box", so the price basis is the generic PACK (user ruling
2026-09-01) rather than a vessel nobody printed.

A quiet corroboration worth keeping: the item code's suffix repeats the package
quantity — 580.180 holds 180, 322.100 holds 100, 988.016 is 16oz, 590.030 is
30ml — and it agrees on all 31 rows of the captured page. Two readings of one
fact, so a future disagreement is an extraction error announcing itself.
"""

from __future__ import annotations

from datetime import datetime, timezone

from schemas.catalogue_pipeline.enums import SourceFormat, UnitCode
from schemas.catalogue_pipeline.common import UnitOfMeasure
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

_CREATED_AT = datetime(2026, 9, 1, tzinfo=timezone.utc)
_CREATED_BY = "avm-vetriscience-price-list"

_AVM = SupplierSourceReference(
    supplier_id=3,
    supplier_name="Asia Vet Medical Limited",
    supplier_code="AVM",
)

_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "Downloads/VetriScience_AVM_catalogue_extraction.json",
        (
            "Captured page of the VetriScience Product Price List: six sections, 31 rows, "
            "columns Item ID / Description / Formula / Package / Price HK$. Every row carries "
            "an item code; 29 packages state a count and a countable, 2 state a measure "
            "(30ml Liquid, 16oz Powder). The page prints 'AVM' as the supplier and "
            "'VetriScience Laboratories' as the brand."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "schemas/catalogue_pipeline/supplier_contracts/suppliers/asia_vet_medical.py",
        (
            "The item code's numeric suffix repeats the package quantity on all 31 rows "
            "(580.180 -> 180 Capsules, 988.016 -> 16oz), so the two columns corroborate each "
            "other and a mismatch is an extraction error rather than a supplier change."
        ),
    ),
]

ASIA_VET_MEDICAL_VETRISCIENCE_PRICE_LIST_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="asia_vet_medical.vetriscience_price_list.v1",
        contract_version="v1",
        supplier=_AVM,
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="Asia Vet Medical — VetriScience product price list",
        source_format=SourceFormat.PDF_TABLE,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.PDF_TABLE,
            table_regions=[
                SourceTableRegion(
                    name="vetriscience_sections",
                    selector="Each health-category table: Joint Health, Behavioral Health, "
                             "Gastrointestinal Health, Immune Health, Specialty Products, Everyday Health.",
                    notes=(
                        "One row per product. The section heading is the product's category and "
                        "is not itself a row."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=["Item ID", "Description", "Formula", "Package", "Price HK$"],
            row_eligibility_rules=[
                "The ingestion supplier must be Asia Vet Medical Limited (supplier ID 3).",
                "Only the VetriScience range appears on this list; AVM's other lists are "
                "separate documents with their own contracts.",
                "A section heading names the category for the rows beneath it and is not a product.",
                "Rows require an Item ID; every row on the page observed carries one.",
            ],
            source_location_expectations=["source document", "page", "table section", "row"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Item ID",
                description=(
                    "VetriScience's own item code (580.180, 88A.060). The three characters "
                    "before the dot identify the product and the three after repeat the "
                    "package quantity."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.OPTIONAL,  # a name never blocks a row (ruling 2026-08-25)
                source_column="Description",
                description="The printed product name.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="species",
                role=SourceFieldRole.SPECIES,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Formula",
                value_map={"Canine": "dog", "Feline": "cat", "Canine + Feline": "both"},
                description=(
                    "Which animal the formula is for, stated per row. Mapped to the vocabulary "
                    "the product record already uses (dog / cat / both). A supplier that says "
                    "this outright spares the row a species lookup."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="package",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="Package",
                description=(
                    "What one package holds: a count and the thing counted (\"180 Capsules\"), "
                    "or a measure (\"30ml Liquid\", \"16oz Powder\"). A measure is content, "
                    "never a count of sellable units."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="unit_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="Price HK$",
                description="Trade price in HKD for one package.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="category",
                role=SourceFieldRole.CATEGORY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_path="section",
                description="The health category heading the row sits under.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                requirement=SourceFieldRequirement.OPTIONAL,
                constant_value="VetriScience",
                description=(
                    "The whole list is one brand, printed once in the page header rather than "
                    "per row. AVM is the supplier; VetriScience is what is on the bottle."
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
                "One price per row, in HKD, for one package. The page never names the vessel — "
                "it says '180 Capsules', not 'bottle' or 'box' — so the basis is the generic "
                "PACK (user ruling 2026-09-01) rather than a container nobody printed. "
                "VetriScience publishes no RRP and no bulk terms on this list, so none is claimed."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="package",
            purchase_uom=UnitOfMeasure(code=UnitCode.PACK),
            sellable_units_per_purchase_unit_source_field="package",
            sellable_count_excludes_measures=True,
            interpretation_rules=[
                "The Package column states what ONE package holds.",
                "A count and a countable ('180 Capsules') gives sellable units per package.",
                "A measure ('30ml Liquid', '16oz Powder') is content, never a unit count — "
                "a 30 ml bottle is one sellable thing, not thirty.",
                "The item code's suffix repeats the same quantity and corroborates it.",
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
            notes="This list prints one price per row and no bulk or promotional terms.",
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="AVM_VETRISCIENCE_PACKAGE_IS_A_MEASURE",
                condition="The Package column states a measure (30ml Liquid, 16oz Powder) rather than a count.",
                review_guidance=(
                    "Publish the cost at package basis and derive no per-unit price. The measure "
                    "describes what is inside one package, not how many things it holds."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="AVM_VETRISCIENCE_CODE_SUFFIX_DISAGREES_WITH_PACKAGE",
                condition="The item code's numeric suffix does not match the package quantity.",
                review_guidance=(
                    "Re-read the row before trusting either figure. The two agreed on every row "
                    "of the page observed, so a disagreement is far more likely an extraction "
                    "error than a supplier change."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku", "description", "species", "package", "unit_price", "category", "brand",
        ),
        created_at=_CREATED_AT,
        created_by=_CREATED_BY,
    )
)

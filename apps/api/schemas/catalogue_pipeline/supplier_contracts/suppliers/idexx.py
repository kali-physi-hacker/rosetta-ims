"""IDEXX HK — the ordering portal, read into a snapshot.

Asia Vet Medical invoices IDEXX products; IDEXX is the brand on the box and the
portal the catalogue lives in. There is no price file and no data API, so
`services.idexx_connector` signs in with a browser and writes a CSV snapshot
carrying IDEXX's own field names. From here this is an ordinary delimited
source, and nothing downstream needs a browser.

Three things this source states that shape the contract:

* the price is OUR price. The portal quotes what this practice pays, so a
  snapshot is account-specific and never a list price. IDEXX's list figure
  appears beside it on only 3 of 105 rows, far too sparse to declare, so it is
  not captured.
* one price buys one ITEM, and the page says what the item holds — "5 tests per
  item", "12 panels per item". The item is a pack; the page never names a
  vessel, so the basis is the generic PACK.
* about a third of the catalogue is marked "Free item" — consumables IDEXX
  supplies alongside an analyser contract. That is a price of ZERO, not a
  missing price, and is declared so the desk is never asked to chase a price
  IDEXX has already stated.
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
_CREATED_BY = "idexx-order-portal-connector"

_AVM = SupplierSourceReference(
    supplier_id=3,
    supplier_name="Asia Vet Medical Limited",
    supplier_code="AVM",
)

_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "connector:idexx_connector/order.idexx.com",
        (
            "Live read of the IDEXX HK ordering portal on 2026-09-02 under the clinic's own "
            "account (Ohana Animal Hospital, 426799): 20 category leaves, 105 distinct "
            "products, every one carrying a material number and a pack. 73 priced, 32 marked "
            "'Free item'. Six of the eight IDEXX rows on the BizOps golden sheet matched the "
            "captured price to the cent, and their unit counts agreed too."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "services/idexx_connector.py",
        (
            "The snapshot is generated, not transcribed: columns are fixed by SNAPSHOT_COLUMNS "
            "and rows are sorted by material, so an unchanged catalogue yields identical bytes "
            "and re-reading submits nothing. Our price is the figure carrying the asterisk; the "
            "unmarked figure beside it is IDEXX's list price and is deliberately not captured."
        ),
    ),
]

IDEXX_ORDER_PORTAL_SNAPSHOT_V1 = register_supplier_source_contract(
    SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id="idexx.order_portal_snapshot.v1",
        contract_version="v1",
        supplier=_AVM,
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name="IDEXX HK ordering portal snapshot (via Asia Vet Medical)",
        source_format=SourceFormat.CSV,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.CSV,
            table_regions=[
                SourceTableRegion(
                    name="idexx_portal_products",
                    selector="Every row of the connector's snapshot CSV.",
                    notes=(
                        "One row per orderable material. A product listed under two categories "
                        "is one row — the first category reached wins."
                    ),
                )
            ],
            required_headers=[],
            optional_headers=list((
                "material", "description", "category", "pack_text", "units_per_item",
                "pack_noun", "price_hkd", "is_free_item", "previously_ordered", "source_url",
            )),
            row_eligibility_rules=[
                "The ingestion supplier must be Asia Vet Medical Limited (supplier ID 3).",
                "Rows require IDEXX's material number; a row without one is not orderable.",
                "The header row repeats the declared column names and is not a product.",
                "A row marked is_free_item costs zero and is a product, not a priceless row.",
            ],
            source_location_expectations=["source document", "snapshot row", "source column"],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="material",
                description="IDEXX's material number — what you quote when ordering (99-0013506).",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.OPTIONAL,  # a name never blocks a row
                source_column="description",
                description="The product name as the portal prints it.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="unit_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="price_hkd",
                description=(
                    "What THIS account pays for one item, in HKD. Zero where IDEXX supplies the "
                    "item free with an analyser contract — a stated price, not a missing one."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="pack_text",
                aliases=["units_per_item", "pack_noun"],
                description="What one item holds, as printed: '5 tests per item', '12 panels per item'.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="units_per_item",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="units_per_item",
                description="The count from the pack text, carried on its own so it needs no re-parsing.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="free_item",
                role=SourceFieldRole.ROW_ELIGIBILITY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="is_free_item",
                description=(
                    "TRUE where IDEXX supplies the item at no charge. Recorded, never used to "
                    "drop a row: a free consumable is still stock we hold and count."
                ),
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="category",
                role=SourceFieldRole.CATEGORY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="category",
                description="The portal's own category path for the product.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="brand",
                role=SourceFieldRole.BRAND,
                requirement=SourceFieldRequirement.OPTIONAL,
                constant_value="IDEXX",
                description="AVM invoices it; IDEXX is the brand on the box and in the portal.",
                evidence=_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="source_url",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="source_url",
                description="The category page the row was read from, so a reviewer can see it.",
                evidence=_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="unit_price",
            rrp_source_field=None,
            price_basis=UnitOfMeasure(code=UnitCode.PACK),
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "One price per row, in HKD, for one orderable item. The item is a pack whose "
                "contents the page states ('5 tests per item'); it never names a vessel, so the "
                "basis is the generic PACK. IDEXX's list price is printed on only 3 of 105 rows "
                "and is not captured — what this account pays is the figure carrying the asterisk."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="pack",
            purchase_uom=UnitOfMeasure(code=UnitCode.PACK),
            sellable_units_per_purchase_unit_source_field="units_per_item",
            interpretation_rules=[
                "pack_text states what ONE item holds: a count and the thing counted.",
                "units_per_item carries the same count on its own, so nothing re-parses prose.",
                "Every pack noun observed is a countable (tests, cassettes, tubes, jars); this "
                "source states no measures.",
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
            notes="The portal shows this account one price per item and no bulk or promotional terms.",
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="IDEXX_FREE_ITEM_HAS_NO_COST",
                condition="A row IDEXX supplies at no charge, priced zero.",
                review_guidance=(
                    "Publish it at zero. It is a real product we hold and count; the price is "
                    "stated, not missing, and no one should be sent to chase it."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="IDEXX_PORTAL_DOES_NOT_COVER_EVERY_INVOICED_PRODUCT",
                condition=(
                    "A product AVM invoices that the ordering portal does not list — send-out "
                    "laboratory tests in particular."
                ),
                review_guidance=(
                    "The snapshot proves what the portal SELLS AS STOCK, not everything AVM "
                    "bills. Reference-laboratory-supplies holds 20 items and every one is a free "
                    "collection consumable — tubes, swabs, jars, specimen bags — with not a "
                    "single test among them: IDEXX gives you the tube, and the test it goes to "
                    "is a service invoiced separately. That is why two of the eight IDEXX rows "
                    "on the golden sheet (99-0018136 Pancreatic Lipase, 99-0004959 CRP) are "
                    "absent while their in-house equivalents, SNAP cPL and SNAP fPL, are "
                    "present. A product missing from a snapshot is therefore never evidence of "
                    "a delisting."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku", "description", "unit_price", "pack", "units_per_item",
            "free_item", "category", "brand", "source_url",
        ),
        created_at=_CREATED_AT,
        created_by=_CREATED_BY,
    )
)

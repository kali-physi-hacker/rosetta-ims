"""Royal Canin supplier-source contract declarations.

Royal Canin publishes no price list. Their HK B2B webshop is the catalogue, and
`services.royal_canin_connector` reads it into a CSV snapshot carrying the
shop's own field names — so from here Royal Canin is an ordinary delimited
source with an unusually well-behaved shape: every row has a code, a barcode, a
name, a weight and a price.

The one thing that is NOT ordinary: the price basis is stated per ROW. The shop
prints a `nav_uom` of UNIT (a single bag or tin) or INNER BOX (a case), and the
price is per whatever that says. The contract declares this with
`price_basis_source_field`, so no row is ever priced on an assumed unit.
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


_DECLARATION_CREATED_AT = datetime(2026, 8, 27, tzinfo=timezone.utc)
_DECLARATION_CREATED_BY = "royal-canin-webshop-connector"

# Royal Canin trades as TWO suppliers, and the difference is money: the
# veterinary account and the retail account carry different rebate ladders,
# credit terms and minimum orders. One webshop login shows both ranges, so the
# connector splits its read by the channel Royal Canin files each product
# under, and each half ingests against its own supplier (user ruling
# 2026-08-28).
_ROYAL_CANIN_VET = SupplierSourceReference(
    supplier_id=40,
    supplier_name="Royal Canin (Vet)",
    supplier_code="ROYALCAN",
)
_ROYAL_CANIN_NON_VET = SupplierSourceReference(
    supplier_id=39,
    supplier_name="Royal Canin (Non Vet)",
    supplier_code="28588096",
)

_ROYAL_CANIN_EVIDENCE = [
    evidence(
        SupplierSourceEvidenceType.REAL_SOURCE_CATALOGUE_SAMPLE,
        "connector:royal_canin_connector/webshop.royalcanin.com/hk/en",
        (
            "Live read of the Royal Canin HK webshop's product index on 2026-08-27 "
            "under the clinic's own account: 454 products carry a price for our "
            "customer group, each with original_sku, ean_code, name, navision_weight "
            "and nav_uom (UNIT 326, INNER BOX 127, BOX 1)."
        ),
    ),
    evidence(
        SupplierSourceEvidenceType.PARSER_BEHAVIOR,
        "services/royal_canin_connector.py",
        (
            "The snapshot is generated, not transcribed: column names are fixed by "
            "SNAPSHOT_COLUMNS and rows are sorted by supplier code, so the same "
            "catalogue always yields the same bytes and the same headings."
        ),
    ),
]


def _webshop_snapshot_contract(
    *, contract_id: str, supplier: SupplierSourceReference, format_name: str, range_note: str,
) -> SupplierSourceContractV1:
    """One supplier's half of the webshop snapshot.

    Both halves read the same shop through the same connector and share every
    field and semantic; only which products they carry differs, and that is
    decided by Royal Canin's own channel filing before the snapshot is built.
    """
    return SupplierSourceContractV1(
        schema_version=SUPPLIER_SOURCE_SCHEMA_VERSION,
        contract_id=contract_id,
        contract_version="v1",
        supplier=supplier,
        document_type=SupplierDocumentType.PRICE_LIST,
        format_name=format_name,
        source_format=SourceFormat.CSV,
        support_status=SupplierContractSupportStatus.SUPPORTED,
        evidence=_ROYAL_CANIN_EVIDENCE,
        source_structure=SourceStructure(
            source_format=SourceFormat.CSV,
            table_regions=[
                SourceTableRegion(
                    name="royal_canin_webshop_products",
                    selector="Every row of the connector's snapshot CSV.",
                    notes=(
                        "One row per product offered to our customer group. Products "
                        "belonging to other groups are excluded by the connector — their "
                        "own product pages 404 for this account, so they are not ours to sell."
                    ),
                )
            ],
            # The connector writes the headings, so they are guaranteed — but a
            # missing heading must hold the row rather than block the document:
            # the same discipline every other supplier gets.
            required_headers=[],
            optional_headers=list(
                (
                    "original_sku",
                    "sku",
                    "name",
                    "ean_code",
                    "price_hkd",
                    "nav_uom",
                    "navision_weight",
                    "gtm_category",
                    "nav_animal_type",
                    "category_path",
                    "in_stock",
                    "qty_in_stock",
                    "min_sale_qty",
                    "qty_increments",
                    "url",
                )
            ),
            row_eligibility_rules=[
                f"The ingestion supplier must be {supplier.supplier_name} (supplier ID {supplier.supplier_id}).",
                range_note,
                "Rows require the supplier's own product code; a row without one is not orderable.",
                "The header row repeats the declared column names and is not a product.",
            ],
            source_location_expectations=[
                "source document",
                "snapshot row",
                "source column",
            ],
        ),
        fields=[
            SourceFieldContract(
                field_key="supplier_sku",
                role=SourceFieldRole.SUPPLIER_SKU,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="original_sku",
                aliases=["nav_main_item_no"],
                description=(
                    "Royal Canin's own item number — what you quote when ordering. The "
                    "webshop's internal `sku` (HK_-prefixed) is carried separately and is "
                    "never the supplier code."
                ),
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="webshop_sku",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="sku",
                description="The webshop's own product key (HK_4200600), kept for traceability back to the page.",
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="description",
                role=SourceFieldRole.PRODUCT_NAME,
                requirement=SourceFieldRequirement.OPTIONAL,  # name never blocks a row (user ruling 2026-08-25)
                source_column="name",
                description=(
                    "Royal Canin's printed product name, which also encodes the pack for "
                    "case lines (CAN 410GX12, 200ML x3)."
                ),
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="barcode",
                role=SourceFieldRole.BARCODE,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="ean_code",
                description="EAN printed for the product; present on every row observed.",
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="trade_price",
                role=SourceFieldRole.SOURCE_PRICE,
                requirement=SourceFieldRequirement.REQUIRED,
                source_column="price_hkd",
                description=(
                    "Our account's trade price, in HKD, for one of whatever nav_uom names. "
                    "The connector writes only our customer group's tier — never the "
                    "no-account sentinel."
                ),
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="purchase_unit",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="nav_uom",
                description=(
                    "What one priced unit IS: UNIT for a single bag or tin, INNER BOX for a "
                    "case. This states the price basis for the row — see pricing semantics."
                ),
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="pack_weight_kg",
                role=SourceFieldRole.PACKAGING,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="navision_weight",
                description=(
                    "Royal Canin's own weight for the priced unit, in kilograms. It "
                    "corroborates a case's contents (12 x 410g = 4.92) but is a measure, "
                    "never a count of sellable units."
                ),
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="category_path",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="category_path",
                aliases=["gtm_category", "nav_animal_type"],
                description="The shop's own category placement, kept as evidence for review.",
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="availability",
                role=SourceFieldRole.ROW_ELIGIBILITY,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="in_stock",
                aliases=["qty_in_stock"],
                description=(
                    "Whether the shop currently shows stock. Recorded, never used to drop a "
                    "row: a product out of stock today is still a product with a price."
                ),
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="order_multiple",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="qty_increments",
                aliases=["min_sale_qty"],
                description=(
                    "Ordering constraints the shop states. Every row observed says 1 / 0 "
                    "(no multiple), but the column is declared so a future restriction is "
                    "read rather than silently ignored."
                ),
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
            SourceFieldContract(
                field_key="source_url",
                role=SourceFieldRole.OTHER,
                requirement=SourceFieldRequirement.OPTIONAL,
                source_column="url",
                description="The product's own page, so a reviewer can see what we read.",
                evidence=_ROYAL_CANIN_EVIDENCE,
            ),
        ],
        pricing=PricingSourceSemantics(
            cost_source_field="trade_price",
            rrp_source_field=None,
            price_basis=None,
            # The ROW says what its price buys, so the basis rides with the row.
            price_basis_source_field="purchase_unit",
            price_basis_value_map={
                "UNIT": UnitCode.UNIT.value,
                "INNER BOX": UnitCode.CASE.value,
                "BOX": UnitCode.BOX.value,
            },
            price_basis_status=SemanticResolutionStatus.VERIFIED,
            notes=(
                "One trade price per row, in HKD, for one nav_uom. UNIT is a single "
                "sellable item; INNER BOX is a case whose contents are printed in the "
                "product name (CAN 410GX12) and corroborated by navision_weight. Royal "
                "Canin publishes no RRP to this account, so none is claimed."
            ),
        ),
        packaging=PackagingSourceSemantics(
            packaging_source_field="description",
            content_measure_source_field="pack_weight_kg",
            break_pack_allowed=None,
            interpretation_rules=[
                "nav_uom names the purchase unit: UNIT buys one item, INNER BOX buys a case.",
                (
                    "A case's sellable count comes from the printed name (CAN 410GX12 -> 12, "
                    "200ML x3 -> 3) and must agree with navision_weight before it is used."
                ),
                "navision_weight is a weight, never a count of sellable units.",
            ],
            unresolved_semantics=[
                (
                    "Sellable units per INNER BOX are established only where the product name "
                    "prints the pack; where it does not, the cost stands at case basis and no "
                    "per-unit price is derived."
                ),
                "Break-pack permission is not stated by the source.",
            ],
        ),
        mbb=MbbSourceSemantics(
            source_fields=[],
            condition_patterns=[],
            benefit_patterns=[],
            requires_validation_issue_when=[],
            notes=(
                "The webshop shows this account no bulk terms or promotional pricing — the "
                "index carries an empty special_offers for every row read — so none is claimed."
            ),
        ),
        known_ambiguities=[
            AmbiguityRule(
                issue_code="ROYAL_CANIN_INNER_BOX_CONTENT_NOT_PRINTED",
                condition=(
                    "An INNER BOX row whose product name does not print its pack "
                    "(no X12 / x3 form), leaving the number of sellable units unstated."
                ),
                review_guidance=(
                    "Publish the cost at case basis and derive no per-unit price. Confirm the "
                    "case contents with Royal Canin, or read them from the product page, "
                    "before any per-unit figure is quoted."
                ),
                blocks_supported_status=False,
            ),
            AmbiguityRule(
                issue_code="ROYAL_CANIN_CUSTOMER_GROUP_PRICING",
                condition=(
                    "Prices are per customer group; the snapshot carries only the group the "
                    "connector is configured for."
                ),
                review_guidance=(
                    "If prices look wrong across the board, re-verify the account's customer "
                    "group from a logged-in page (royal_canin_connector.verify_credentials) "
                    "before treating any single row as a price change."
                ),
                blocks_supported_status=False,
            ),
        ],
        pipeline_mapping=pipeline_mapping(
            "supplier_sku",
            "webshop_sku",
            "description",
            "barcode",
            "trade_price",
            "purchase_unit",
            "pack_weight_kg",
            "category_path",
            "availability",
            "order_multiple",
            "source_url",
        ),
        created_at=_DECLARATION_CREATED_AT,
        created_by=_DECLARATION_CREATED_BY,
    )


ROYAL_CANIN_VET_WEBSHOP_SNAPSHOT_V1 = register_supplier_source_contract(
    _webshop_snapshot_contract(
        contract_id="royal_canin.vet_webshop_snapshot.v1",
        supplier=_ROYAL_CANIN_VET,
        format_name="Royal Canin HK webshop snapshot (veterinary)",
        range_note=(
            "Only products Royal Canin files under a veterinary channel (VET CAT / VET DOG) "
            "belong here — the veterinary diets, VHN and VD."
        ),
    )
)

ROYAL_CANIN_NON_VET_WEBSHOP_SNAPSHOT_V1 = register_supplier_source_contract(
    _webshop_snapshot_contract(
        contract_id="royal_canin.non_vet_webshop_snapshot.v1",
        supplier=_ROYAL_CANIN_NON_VET,
        format_name="Royal Canin HK webshop snapshot (retail)",
        range_note=(
            "Only products Royal Canin files under a retail channel (PET SHOP, BREED, CARE, "
            "PUPPY/KITTEN and the like) belong here — the FHN, SHN, FCN, BHN, FBN and CCN ranges."
        ),
    )
)

"""Exporting a run's published items in the golden-sample sheet's columns.

The sheet ("margin calculation", tab `gid=1535624888`) holds 122 hand-filled
SKUs across 24 suppliers — the only human-authored ground truth for how
packaging, price basis, sellable units and bulk terms should read. A regression
diff only works if our export lands in the same shape, so the header list is
pinned here: if someone tidies a column name, this fails rather than silently
producing a file that no longer lines up.
"""

from __future__ import annotations

import csv
import io
import os
import tempfile
from uuid import UUID

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/golden_export.db")

import database  # noqa: E402
import models  # noqa: E402
from services import catalogue_golden_export as export  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


# Copied from the sheet's header row, in its order. Do not sort or rename.
SHEET_HEADERS = [
    "supplier",
    "supplier_product_code",
    "product_name",
    "product name [Rosetta]",
    "weight",
    "brand",
    "package_configuration",
    "order_multiple",
    "catalogue_price_hkd",
    "catalogue_price_basis_qty",
    "catalogue_price_basis_uom",
    "sellable_qty",
    "sellable_uom",
    "sellable_units_per_price_basis",
    "rrp",
    "mbb_tier_1",
    "mbb_tier_2",
    "mbb_tier_3",
    "mbb_tier_4",
    "commercial_offer_summary",
]

RUN = UUID("aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa")


@pytest.fixture()
def db():
    session = database.SessionLocal()
    try:
        for model in (
            models.CatalogueServingPublication,
            models.CatalogueSupplierMbbTerm,
            models.CataloguePackagingConfiguration,
            models.SupplierOffering,
            models.CatalogueMasteringCandidate,
            models.ProductSupplier,
            models.ProductVariant,
            models.Supplier,
        ):
            session.query(model).delete()
        session.commit()
        yield session
        session.rollback()
    finally:
        session.close()


def test_headers_are_exactly_the_sheets(db):
    assert list(export.GOLDEN_COLUMNS) == SHEET_HEADERS


def test_csv_header_row_matches_even_when_the_run_published_nothing(db):
    body = export.golden_csv(db, RUN)
    assert body.splitlines()[0] == ",".join(SHEET_HEADERS)
    assert len(body.splitlines()) == 1, "header only when nothing is published"


def _publish_one(db, *, with_terms=True):
    supplier = models.Supplier(id=14, code="ALF", name="Alfamedic", created_at="2026-01-01T00:00:00")
    variant = models.ProductVariant(
        sku_code="50010319", name="Entyce - Oral Solution - 30mL", brand="Elanco",
        category="Medicine", status="ACTIVE", storage_rule="any", uom="ML",
        weight_g=1588.0, weight_unit="lb",
        created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
    )
    db.add_all([supplier, variant])
    db.flush()
    db.add(models.ProductSupplier(
        product_id=variant.id, supplier_id=14, supplier_sku="EN7502", rrp=1800.0,
        cost_source="manual", pack_source="manual", updated_at="2026-01-01T00:00:00",
    ))
    offering = models.SupplierOffering(
        supplier_product_key="supplier:14:offer:EN7502", supplier_id=14, supplier_sku="EN7502",
        product_variant_id=variant.id, status="active", created_at="2026-01-01T00:00:00",
    )
    db.add(offering)
    db.flush()
    db.add(models.CataloguePackagingConfiguration(
        supplier_product_id=offering.id,
        purchase_uom_code="BOTTLE", price_basis_uom_code="BOTTLE",
        sellable_unit_uom_code="ML", sellable_units_per_purchase_unit=30,
        content_amount=30, content_uom_code="ML",
        order_increment_amount=1, order_increment_uom_code="BOTTLE",
        created_at="2026-01-01T00:00:00",
    ))
    if with_terms:
        db.add(models.CatalogueSupplierMbbTerm(
            supplier_product_id=offering.id, scope="supplier_product",
            condition_type="minimum_quantity", condition_quantity_amount=6,
            condition_quantity_uom_code="BOTTLE",
            benefit_type="discounted_unit_price", discounted_price_amount=1200,
            discounted_price_currency="HKD", discounted_price_basis_uom_code="BOTTLE",
            is_active=1, created_at="2026-01-01T00:00:00",
        ))
        db.add(models.CatalogueSupplierMbbTerm(
            supplier_product_id=offering.id, scope="supplier_product",
            condition_type="minimum_quantity", condition_quantity_amount=5,
            condition_quantity_uom_code="BOTTLE",
            benefit_type="free_quantity", free_quantity_amount=1,
            free_quantity_uom_code="BOTTLE",
            is_active=1, created_at="2026-01-01T00:00:00",
        ))
    candidate_uuid = "cccccccc-1111-4111-8111-cccccccccccc"
    db.add(models.CatalogueMasteringCandidate(
        mastering_candidate_uuid=candidate_uuid, contract_version="catalogue.mastering_candidate.v1",
        ingestion_run_uuid=str(RUN), supplier_catalogue_uuid="s", source_file_uuid="f", catalogue_item_uuid="i",
        extraction_profile_id="p", extraction_profile_version="v1",
        raw_observation_ids_json="[]", lineage_json="{}",
        supplier_product_resolution_json="{}", product_variant_resolution_json="{}",
        packaging_resolution_json="{}", supplier_price_resolution_json="{}",
        mbb_resolution_json="{}", review_status="APPROVED",
        reviewed_by="reviewer@example.com", reviewed_at="2026-01-02T00:00:00+00:00",
        created_at="2026-01-01T00:00:00",
    ))
    db.add(models.CatalogueServingPublication(
        contract_version="catalogue.serving_item.v1",
        publication_key="k", publication_version="v2026-08-01",
        canonical_sku=variant.sku_code, product_variant_key="pv", product_variant_name=variant.name,
        product_id=variant.id, supplier_id=14, supplier_product_id=offering.id,
        supplier_product_key=offering.supplier_product_key, supplier_sku="EN7502",
        current_approved_cost_amount=1390, current_approved_cost_currency="HKD",
        current_approved_cost_basis_uom_code="BOTTLE",
        review_status="APPROVED", published_at="2026-01-02T00:00:00",
        mastering_candidate_uuid=candidate_uuid, catalogue_item_uuid="i",
        raw_observation_ids_json="[]", lineage_json="{}", snapshot_json="{}", is_current=1,
        created_at="2026-01-02T00:00:00",
    ))
    db.flush()
    return variant


def test_a_published_row_reads_like_the_sheet(db):
    """The Alfamedic Entyce row, which the sheet fills in by hand at line 65."""
    _publish_one(db)
    rows = export.golden_rows(db, RUN)
    assert len(rows) == 1
    row = rows[0]

    assert row["supplier"] == "Alfamedic"
    assert row["supplier_product_code"] == "EN7502"
    assert row["package_configuration"] == "30 ML / BOTTLE"   # sheet: "30 ML / BOTTLE"
    assert row["order_multiple"] == "1 BOTTLE"                # sheet: "1 BOTTLE"
    assert row["catalogue_price_hkd"] == "$1,390.00"          # sheet: "$1,390.00"
    assert row["catalogue_price_basis_qty"] == "1"
    assert row["catalogue_price_basis_uom"] == "BOTTLE"
    assert row["sellable_qty"] == "1"
    assert row["sellable_uom"] == "ML"
    assert row["sellable_units_per_price_basis"] == "30"
    assert row["rrp"] == "$1,800.00"
    assert row["brand"] == "Elanco"


def test_bulk_terms_are_one_canonical_phrasing_per_kind(db):
    """The sheet says this eight ways; a diff needs one."""
    _publish_one(db)
    row = export.golden_rows(db, RUN)[0]
    assert row["mbb_tier_1"] == "buy 6 BOTTLE at $1,200.00 per BOTTLE"
    assert row["mbb_tier_2"] == "buy 5 get 1 free"
    assert row["mbb_tier_3"] == ""
    assert row["commercial_offer_summary"] == "buy 6 BOTTLE at $1,200.00 per BOTTLE; buy 5 get 1 free"


def test_weight_is_converted_not_relabelled(db):
    """weight_g is canonical grams; weight_unit is only how the source showed it.

    Printing the gram figure beside that unit claims a 3.5 lb bag weighs 1588 lb.
    """
    _publish_one(db)
    assert export.golden_rows(db, RUN)[0]["weight"] == "3.501 lb"


def test_absent_values_are_empty_not_the_string_na(db):
    """The sheet writes "N/A" in 100 rows. That is not a value."""
    _publish_one(db, with_terms=False)
    row = export.golden_rows(db, RUN)[0]
    for column in ("mbb_tier_1", "mbb_tier_4", "commercial_offer_summary"):
        assert row[column] == "", f"{column} should be empty, got {row[column]!r}"


def test_csv_round_trips_through_a_reader(db):
    _publish_one(db)
    reader = csv.DictReader(io.StringIO(export.golden_csv(db, RUN)))
    assert reader.fieldnames == SHEET_HEADERS
    parsed = list(reader)
    assert len(parsed) == 1
    assert parsed[0]["supplier_product_code"] == "EN7502"


def test_only_published_items_are_exported(db):
    """Approved-but-unpublished is not live, so it is not in the regression set."""
    _publish_one(db)
    db.query(models.CatalogueServingPublication).update({"is_current": 0})
    db.flush()
    assert export.golden_rows(db, RUN) == []


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

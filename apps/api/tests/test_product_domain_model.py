from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models
from services.product_domain import backfill_explicit_product_domain


def _variant() -> models.ProductVariant:
    return models.ProductVariant(
        sku_code="SKU-001",
        name="Medicine 100 mg",
        brand="Example",
        category="Medicine",
        uom="tablet",
        storage_rule="clinic_only",
        status="ACTIVE",
        hero_sku=0,
        created_at="2026-07-29T00:00:00+00:00",
        updated_at="2026-07-29T00:00:00+00:00",
    )


def test_product_domain_types_are_explicit_and_legacy_product_type_is_removed():
    assert not hasattr(models, "Product")
    assert models.ProductFamily is not models.ProductVariant
    assert not hasattr(models, "CatalogueSupplierProduct")
    assert models.InventoryItem.__tablename__ == "inventory_items"
    assert models.SellingItem.__tablename__ == "selling_items"


def test_compatibility_backfill_separates_inventory_and_selling_concerns():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with Session(engine) as db:
        variant = _variant()
        db.add(variant)
        db.flush()
        db.add(
            models.ProductChannel(
                product_id=variant.id,
                channel="shopify",
                is_active=1,
                selling_price=125.0,
                units_per_listing=10,
                has_dispensing_fee=0,
                updated_at="2026-07-29T00:00:00+00:00",
            )
        )
        db.add(
            models.StockLevel(
                product_id=variant.id,
                location="warehouse",
                qty=12,
                source="import",
                updated_at="2026-07-29T00:00:00+00:00",
            )
        )
        db.commit()

        assert backfill_explicit_product_domain(db) == (1, 1)
        inventory = db.query(models.InventoryItem).one()
        selling = db.query(models.SellingItem).one()
        assert inventory.product_variant_id == variant.id
        assert inventory.valuation_uom == "tablet"
        assert inventory.storage_rule == "clinic_only"
        assert selling.product_variant_id == variant.id
        assert selling.inventory_item_id == inventory.id
        assert selling.channel == "shopify"
        assert selling.units_per_listing == 10
        assert selling.selling_price == 125.0

        assert backfill_explicit_product_domain(db) == (0, 0)
        assert db.query(models.InventoryItem).count() == 1
        assert db.query(models.SellingItem).count() == 1


def test_supplier_offering_is_distinct_from_inventory_and_selling_items():
    assert "supplier_id" in models.SupplierOffering.__table__.columns
    assert "supplier_sku" in models.SupplierOffering.__table__.columns
    assert "product_variant_id" in models.SupplierOffering.__table__.columns
    assert "supplier_id" not in models.InventoryItem.__table__.columns
    assert "supplier_id" not in models.SellingItem.__table__.columns

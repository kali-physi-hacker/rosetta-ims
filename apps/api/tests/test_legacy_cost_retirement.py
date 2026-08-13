"""Retiring basic_cost: migrate every remaining legacy cost into an offering
price, refuse to drop while anything would lose its cost, then drop."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import models
from services import legacy_cost_retirement


def _engine_with_legacy_column():
    """A database shaped like production before the cut: the ORM no longer
    declares basic_cost, so the migration's own raw SQL has to add it back
    for the fixture — exactly the column the real DBs still carry."""
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE product_suppliers ADD COLUMN basic_cost FLOAT"))
        conn.execute(text("ALTER TABLE products ADD COLUMN basic_cost_sheet FLOAT"))
        conn.execute(text("ALTER TABLE products ADD COLUMN units_per_pack_sheet INTEGER"))
    return engine


def _seed(engine, *, supplier_sku="10447", units_per_pack=12, cost=157.2):
    with Session(engine) as db:
        db.add(models.Supplier(id=14, code="HILLS", name="Hill's", created_at="2026-07-31T00:00:00+00:00"))
        variant = models.ProductVariant(
            sku_code="RIMS-RET-1", name="Retire Me", category="Food", uom="can",
            storage_rule="any", status="ACTIVE", hero_sku=0,
            created_at="2026-07-31T00:00:00+00:00", updated_at="2026-07-31T00:00:00+00:00")
        db.add(variant)
        db.flush()
        link = models.ProductSupplier(
            product_id=variant.id, supplier_id=14, supplier_sku=supplier_sku,
            units_per_pack=units_per_pack, cost_updated_at="2026-02-02T00:00:00+00:00",
            updated_at="2026-07-31T00:00:00+00:00")
        db.add(link)
        db.commit()
        db.execute(text("UPDATE product_suppliers SET basic_cost = :c WHERE id = :i"),
                   {"c": cost, "i": link.id})
        db.commit()
        return variant.id, link.id


def test_migrates_then_drops_all_three_columns():
    engine = _engine_with_legacy_column()
    variant_id, _ = _seed(engine)

    result = legacy_cost_retirement.retire_legacy_cost(engine)
    assert result["status"] == "retired"
    assert result["migrated"] == 1
    assert set(result["dropped"]) == {
        "product_suppliers.basic_cost", "products.basic_cost_sheet", "products.units_per_pack_sheet"}

    inspector = inspect(engine)
    assert "basic_cost" not in {c["name"] for c in inspector.get_columns("product_suppliers")}
    assert "basic_cost_sheet" not in {c["name"] for c in inspector.get_columns("products")}

    with Session(engine) as db:
        from services import offering_costs
        entry = offering_costs.variant_offerings(db, variant_id)[0]
        # 157.20 over 12 sellable units, carrying the legacy cost's own date
        assert entry["current"]["unit_cost"] == 13.1
        assert entry["current"]["since"] == "2026-02-02T00:00:00+00:00"
        assert entry["source"] == "manual"


def test_is_idempotent_and_safe_on_a_retired_database():
    engine = _engine_with_legacy_column()
    _seed(engine)
    legacy_cost_retirement.retire_legacy_cost(engine)
    again = legacy_cost_retirement.retire_legacy_cost(engine)
    assert again == {"status": "already_retired", "migrated": 0, "dropped": []}


def test_leaves_an_already_priced_link_untouched():
    engine = _engine_with_legacy_column()
    variant_id, link_id = _seed(engine)
    with Session(engine) as db:
        link = db.get(models.ProductSupplier, link_id)
        from services import offering_costs
        offering_costs.record_supplier_cost(db, link, pack_cost=120.0)
        db.commit()

    result = legacy_cost_retirement.retire_legacy_cost(engine)
    assert result["migrated"] == 0          # nothing to carry over
    with Session(engine) as db:
        from services import offering_costs
        entry = offering_costs.variant_offerings(db, variant_id)[0]
        assert entry["current"]["unit_cost"] == 10.0   # 120 / 12, the recorded cost


def test_duplicate_and_blank_supplier_skus_do_not_collide():
    engine = _engine_with_legacy_column()
    with Session(engine) as db:
        db.add(models.Supplier(id=20, code="DUP", name="Dup Co", created_at="2026-07-31T00:00:00+00:00"))
        ids = []
        for index in range(3):
            variant = models.ProductVariant(
                sku_code=f"RIMS-DUP-{index}", name=f"Dup {index}", category="Food", uom="can",
                storage_rule="any", status="ACTIVE", hero_sku=0,
                created_at="2026-07-31T00:00:00+00:00", updated_at="2026-07-31T00:00:00+00:00")
            db.add(variant)
            db.flush()
            # two share a supplier SKU, one is blank
            link = models.ProductSupplier(
                product_id=variant.id, supplier_id=20,
                supplier_sku="13150" if index < 2 else "",
                updated_at="2026-07-31T00:00:00+00:00")
            db.add(link)
            db.flush()
            ids.append(link.id)
        db.commit()
        for link_id in ids:
            db.execute(text("UPDATE product_suppliers SET basic_cost = 10 WHERE id = :i"), {"i": link_id})
        db.commit()

    result = legacy_cost_retirement.retire_legacy_cost(engine)
    assert result["status"] == "retired"
    assert result["migrated"] == 3
    with Session(engine) as db:
        skus = sorted(
            (o.supplier_sku for o in db.query(models.SupplierOffering).filter_by(supplier_id=20).all()),
            key=lambda value: (value is None, value))
        assert skus == ["13150", None, None]


def test_refuses_to_drop_when_a_cost_cannot_be_carried_over(monkeypatch):
    engine = _engine_with_legacy_column()
    _seed(engine)
    # simulate a migration that fails to place every cost
    monkeypatch.setattr(legacy_cost_retirement, "_migrate_remaining_costs", lambda db: 0)
    result = legacy_cost_retirement.retire_legacy_cost(engine)
    assert result["status"] == "blocked"
    assert result["unmigrated"] == 1
    # the column survives, so nothing is lost and the next boot retries
    assert "basic_cost" in {c["name"] for c in inspect(engine).get_columns("product_suppliers")}

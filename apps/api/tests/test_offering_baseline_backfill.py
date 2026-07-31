"""Offering baseline backfill: legacy-only links become MANUAL baselines with
the legacy date preserved; links that already have a current offering price
are untouched; the whole thing is idempotent."""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models
from services import offering_costs
from services.pricing_service import effective_cost_source, get_unit_cost


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def _seed(db: Session):
    db.add(models.Supplier(id=14, code="HILLS", name="Hill's", created_at="2026-07-29T00:00:00+00:00"))
    db.add(models.Supplier(id=15, code="MAXI", name="Maxipro", created_at="2026-07-29T00:00:00+00:00"))
    v1 = models.ProductVariant(
        sku_code="RIMS-BF-1", name="Backfill One", category="Food", uom="can",
        storage_rule="any", status="ACTIVE", hero_sku=0,
        created_at="2026-07-29T00:00:00+00:00", updated_at="2026-07-29T00:00:00+00:00")
    v2 = models.ProductVariant(
        sku_code="RIMS-BF-2", name="Backfill Two", category="Food", uom="can",
        storage_rule="any", status="ACTIVE", hero_sku=0,
        created_at="2026-07-29T00:00:00+00:00", updated_at="2026-07-29T00:00:00+00:00")
    db.add_all([v1, v2])
    db.flush()
    legacy = models.ProductSupplier(
        product_id=v1.id, supplier_id=14, supplier_sku="10447",
        basic_cost=157.2, units_per_pack=12, cost_updated_at="2026-02-02T00:00:00+00:00",
        updated_at="2026-07-29T00:00:00+00:00")
    already = models.ProductSupplier(
        product_id=v2.id, supplier_id=15, supplier_sku="MX-1",
        basic_cost=99.0, units_per_pack=None, updated_at="2026-07-29T00:00:00+00:00")
    db.add_all([legacy, already])
    db.commit()
    # the second link already has a live offering price — must not be touched
    offering_costs.record_supplier_cost(db, already, pack_cost=88.0)
    db.commit()
    return legacy, already


def test_backfill_creates_manual_baseline_with_legacy_date():
    with _session() as db:
        legacy, already = _seed(db)
        created_offerings, created_prices = offering_costs.backfill_offering_baselines(db)
        assert (created_offerings, created_prices) == (1, 1)

        entry = offering_costs.variant_offerings(db, legacy.product_id)[0]
        assert entry["source"] == "manual"
        assert entry["current"]["unit_cost"] == 13.1          # 157.2 / 12
        assert entry["current"]["since"] == "2026-02-02T00:00:00+00:00"
        assert entry["current"]["run_id"] is None
        assert get_unit_cost(legacy) == 13.1
        assert effective_cost_source(legacy) == "offering"

        # the pre-priced link kept its price, untouched by the backfill
        assert get_unit_cost(already) == 88.0


def test_backfill_is_idempotent():
    with _session() as db:
        _seed(db)
        offering_costs.backfill_offering_baselines(db)
        assert offering_costs.backfill_offering_baselines(db) == (0, 0)
        assert db.query(models.CatalogueSupplierPrice).filter_by(is_current=1).count() == 2


def test_backfill_fills_price_on_existing_priceless_offering():
    with _session() as db:
        legacy, _ = _seed(db)
        # an offering exists (e.g. pipeline-created) but has no price rows
        db.add(models.SupplierOffering(
            supplier_product_key="supplier:14:offer:10447",
            supplier_id=14, product_variant_id=legacy.product_id, supplier_sku="10447",
            status="active", created_at="2026-07-29T00:00:00+00:00", updated_at="2026-07-29T00:00:00+00:00"))
        db.commit()
        created_offerings, created_prices = offering_costs.backfill_offering_baselines(db)
        assert (created_offerings, created_prices) == (0, 1)
        assert db.query(models.SupplierOffering).filter_by(supplier_id=14).count() == 1


def test_backfill_survives_duplicate_supplier_sku_across_variants():
    with _session() as db:
        db.add(models.Supplier(id=20, code="DUP", name="Dup Co", created_at="2026-07-29T00:00:00+00:00"))
        va = models.ProductVariant(
            sku_code="RIMS-DUP-A", name="Dup A", category="Food", uom="can",
            storage_rule="any", status="ACTIVE", hero_sku=0,
            created_at="2026-07-29T00:00:00+00:00", updated_at="2026-07-29T00:00:00+00:00")
        vb = models.ProductVariant(
            sku_code="RIMS-DUP-B", name="Dup B", category="Food", uom="can",
            storage_rule="any", status="ACTIVE", hero_sku=0,
            created_at="2026-07-29T00:00:00+00:00", updated_at="2026-07-29T00:00:00+00:00")
        db.add_all([va, vb])
        db.flush()
        db.add_all([
            models.ProductSupplier(product_id=va.id, supplier_id=20, supplier_sku="13150",
                                   basic_cost=10.0, updated_at="2026-07-29T00:00:00+00:00"),
            models.ProductSupplier(product_id=vb.id, supplier_id=20, supplier_sku="13150",
                                   basic_cost=20.0, updated_at="2026-07-29T00:00:00+00:00"),
        ])
        db.commit()
        created_offerings, created_prices = offering_costs.backfill_offering_baselines(db)
        assert (created_offerings, created_prices) == (2, 2)
        skus = [o.supplier_sku for o in db.query(models.SupplierOffering).filter_by(supplier_id=20).all()]
        assert sorted(skus, key=lambda v: (v is None, v)) == ["13150", None]
        # both variants read their own baseline
        assert {e["current"]["unit_cost"] for e in offering_costs.variant_offerings(db, va.id)} == {10.0}
        assert {e["current"]["unit_cost"] for e in offering_costs.variant_offerings(db, vb.id)} == {20.0}


def test_backfill_normalizes_blank_supplier_skus_to_null():
    with _session() as db:
        db.add(models.Supplier(id=30, code="BLK", name="Blank Co", created_at="2026-07-29T00:00:00+00:00"))
        variants = []
        for idx in range(2):
            v = models.ProductVariant(
                sku_code=f"RIMS-BLK-{idx}", name=f"Blank {idx}", category="Food", uom="can",
                storage_rule="any", status="ACTIVE", hero_sku=0,
                created_at="2026-07-29T00:00:00+00:00", updated_at="2026-07-29T00:00:00+00:00")
            db.add(v)
            variants.append(v)
        db.flush()
        for v in variants:
            db.add(models.ProductSupplier(product_id=v.id, supplier_id=30, supplier_sku="",
                                          basic_cost=5.0, updated_at="2026-07-29T00:00:00+00:00"))
        db.commit()
        created_offerings, created_prices = offering_costs.backfill_offering_baselines(db)
        assert (created_offerings, created_prices) == (2, 2)
        assert all(o.supplier_sku is None for o in db.query(models.SupplierOffering).filter_by(supplier_id=30).all())

"""Offering-first cost reads: the explicit product domain is the read path.

The catalogue pipeline writes supplier cost to SupplierOffering price history;
read surfaces (get_unit_cost — the single cost all margin math runs on — and
the display serializers) must prefer that current offering price, basis-aware,
and fall back to the legacy whole-pack basic_cost only when no offering price
exists for the (supplier, variant) link.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models
from services import offering_costs
from services.pricing_service import (
    effective_cost_source,
    effective_pack_cost,
    get_primary_cost,
    get_unit_cost,
)


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def _seed_link(db: Session, *, basic_cost: float | None, units_per_pack: int | None) -> models.ProductSupplier:
    variant = models.ProductVariant(
        sku_code="RIMS-COST-1",
        name="Hill's Science Plan Adult Chicken 82g",
        brand="Hill's",
        category="Food",
        uom="can",
        storage_rule="any",
        status="ACTIVE",
        hero_sku=0,
        created_at="2026-07-29T00:00:00+00:00",
        updated_at="2026-07-29T00:00:00+00:00",
    )
    db.add(models.Supplier(id=14, code="HILLS", name="Hill's", created_at="2026-07-29T00:00:00+00:00"))
    db.add(variant)
    db.flush()
    link = models.ProductSupplier(
        product_id=variant.id,
        supplier_id=14,
        supplier_sku="10447",
        basic_cost=basic_cost,
        units_per_pack=units_per_pack,
        updated_at="2026-07-29T00:00:00+00:00",
    )
    db.add(link)
    db.commit()
    return link


def _seed_offering_price(
    db: Session,
    link: models.ProductSupplier,
    *,
    amount: float,
    basis_code: str,
    is_current: int = 1,
    packaging: tuple[str, str, float] | None = None,
) -> None:
    offering = db.query(models.SupplierOffering).filter_by(
        supplier_id=link.supplier_id, product_variant_id=link.product_id
    ).first()
    if offering is None:
        offering = models.SupplierOffering(
            supplier_product_key=f"supplier:{link.supplier_id}:offer:{link.supplier_sku}",
            supplier_id=link.supplier_id,
            product_variant_id=link.product_id,
            supplier_sku=link.supplier_sku,
            status="active",
            created_at="2026-07-29T00:00:00+00:00",
            updated_at="2026-07-29T00:00:00+00:00",
        )
        db.add(offering)
        db.flush()
    if packaging is not None:
        purchase, sellable, per_purchase = packaging
        db.add(
            models.CataloguePackagingConfiguration(
                supplier_product_id=offering.id,
                purchase_uom_code=purchase,
                sellable_unit_uom_code=sellable,
                sellable_units_per_purchase_unit=per_purchase,
                created_at="2026-07-29T00:00:00+00:00",
            )
        )
    db.add(
        models.CatalogueSupplierPrice(
            supplier_product_id=offering.id,
            amount=amount,
            currency="HKD",
            price_basis_uom_code=basis_code,
            is_current=is_current,
            created_at="2026-07-29T00:00:00+00:00",
        )
    )
    db.commit()
    offering_costs.invalidate(db)


def test_legacy_fallback_without_offering_price():
    with _session() as db:
        link = _seed_link(db, basic_cost=157.2, units_per_pack=12)
        assert get_unit_cost(link) == 13.1
        assert effective_pack_cost(link) == 157.2
        assert effective_cost_source(link) == "manual"


def test_detached_rows_keep_legacy_behaviour():
    assert get_unit_cost(models.ProductSupplier(basic_cost=100.0, units_per_pack=4)) == 25.0


def test_current_offering_price_wins_over_basic_cost():
    with _session() as db:
        link = _seed_link(db, basic_cost=157.2, units_per_pack=12)
        _seed_offering_price(db, link, amount=14.0, basis_code="UNIT")
        assert get_unit_cost(link) == 14.0
        # Whole-pack display equivalent keeps pack / units_per_pack = unit true.
        assert effective_pack_cost(link) == 168.0
        assert effective_cost_source(link) == "offering"
        assert get_primary_cost(link.product) == 168.0


def test_pack_basis_price_divides_by_offering_packaging():
    with _session() as db:
        link = _seed_link(db, basic_cost=None, units_per_pack=None)
        _seed_offering_price(db, link, amount=150.0, basis_code="CASE", packaging=("CASE", "UNIT", 12.0))
        assert get_unit_cost(link) == 12.5


def test_superseded_price_is_ignored():
    with _session() as db:
        link = _seed_link(db, basic_cost=157.2, units_per_pack=12)
        _seed_offering_price(db, link, amount=99.0, basis_code="UNIT", is_current=0)
        assert get_unit_cost(link) == 13.1
        assert effective_cost_source(link) == "manual"


def test_session_memo_is_one_query_and_invalidates(monkeypatch):
    with _session() as db:
        link = _seed_link(db, basic_cost=157.2, units_per_pack=12)
        assert get_unit_cost(link) == 13.1
        # Memo cached the empty map; a new offering price appears after invalidate.
        _seed_offering_price(db, link, amount=14.0, basis_code="UNIT")
        assert get_unit_cost(link) == 14.0


def test_record_supplier_cost_writes_current_offering_price():
    with _session() as db:
        link = _seed_link(db, basic_cost=157.2, units_per_pack=12)
        offering_costs.record_supplier_cost(db, link, pack_cost=157.2)
        db.commit()

        offering = db.query(models.SupplierOffering).one()
        assert offering.supplier_id == 14
        assert offering.product_variant_id == link.product_id
        assert offering.legacy_product_supplier_id == link.id
        price = db.query(models.CatalogueSupplierPrice).filter_by(is_current=1).one()
        # Whole-pack 157.2 over 12 sellable units → per-sell-unit price row.
        assert float(price.amount) == 13.1
        assert price.price_basis_uom_code == "UNIT"
        assert get_unit_cost(link) == 13.1
        assert effective_cost_source(link) == "offering"


def test_record_supplier_cost_supersedes_previous_price_and_reuses_offering():
    with _session() as db:
        link = _seed_link(db, basic_cost=157.2, units_per_pack=12)
        offering_costs.record_supplier_cost(db, link, pack_cost=157.2)
        offering_costs.record_supplier_cost(db, link, pack_cost=168.0)
        db.commit()

        assert db.query(models.SupplierOffering).count() == 1
        prices = db.query(models.CatalogueSupplierPrice).order_by(models.CatalogueSupplierPrice.id).all()
        assert [p.is_current for p in prices] == [0, 1]
        assert prices[0].superseded_at is not None
        assert float(prices[1].amount) == 14.0
        assert get_unit_cost(link) == 14.0


def test_record_supplier_cost_is_noop_without_supplier_or_cost():
    with _session() as db:
        link = _seed_link(db, basic_cost=157.2, units_per_pack=12)
        offering_costs.record_supplier_cost(db, link, pack_cost=None)
        link.supplier_id = None
        offering_costs.record_supplier_cost(db, link, pack_cost=100.0)
        db.commit()
        assert db.query(models.SupplierOffering).count() == 0

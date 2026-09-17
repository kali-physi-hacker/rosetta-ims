"""A channel that lists the pack, costed against a unit.

Cost is one number per (supplier, product), in the product's own unit. A
channel may price in something else — HKTV lists a box of twelve pouches at
$113.50 while the cost is $8.75 a pouch — and subtracting one from the other is
meaningless until they are in the same unit. `sell_uom_count` says how many
costing units the listed thing holds, and until now nothing read it: the sheet
recorded it, the export wrote it back, and every margin ignored it. HKTV read
92% where the real figure is 7.5%.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/chprice.db")

import database  # noqa: E402
import models  # noqa: E402
from services import channel_sale_terms, pricing_service  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

NOW = "2026-09-16T00:00:00"


@pytest.fixture()
def db():
    session = database.SessionLocal()
    for model in (models.SellingItem, models.ProductChannel, models.InventoryItem,
                  models.ProductVariant):
        session.query(model).delete()
    session.commit()
    yield session
    session.rollback()
    session.close()


def _product(db, uom="Pouch(es)"):
    p = models.ProductVariant(sku_code="10008340", name="RC Instinctive Pouch",
                              category="Food", uom=uom, storage_rule="any",
                              status="ACTIVE", hero_sku=0, created_at=NOW, updated_at=NOW)
    db.add(p); db.flush()
    return p


def _listing(db, product, channel, uom, count, price):
    db.add(models.ProductChannel(product_id=product.id, channel=channel, is_active=1,
                                 selling_price=price, updated_at=NOW))
    db.add(models.SellingItem(
        selling_item_key=f"variant:{product.sku_code}:channel:{channel}",
        product_variant_id=product.id, channel=channel, sell_uom=uom,
        sell_uom_count=count, selling_price=price, status="ACTIVE",
        created_at=NOW, updated_at=NOW))
    db.commit()


def test_a_box_price_is_divided_to_the_unit_we_cost_in(db):
    product = _product(db)
    _listing(db, product, "hktv", "Box(es)", 12, 113.50)
    priced = channel_sale_terms.price_in_costing_unit(db, product.id, "hktv", 113.50)
    assert round(priced, 4) == round(113.50 / 12, 4)      # 9.4583 a pouch, not 113.50


def test_a_channel_selling_the_unit_itself_is_untouched(db):
    product = _product(db)
    _listing(db, product, "shopify", "Pouch(es)", 1, 14.00)
    assert channel_sale_terms.price_in_costing_unit(db, product.id, "shopify", 14.00) == 14.00


def test_a_blank_count_changes_nothing(db):
    """Every one of the 11,919 listings recorded today has this blank, so the
    division must be inert until somebody says otherwise."""
    product = _product(db)
    _listing(db, product, "clinic", "Pouch(es)", None, 14.00)
    assert channel_sale_terms.price_in_costing_unit(db, product.id, "clinic", 14.00) == 14.00


def test_the_margin_uses_the_divided_price(db):
    """The whole point: one cost, two channels, both margins true."""
    product = _product(db)
    _listing(db, product, "shopify", "Pouch(es)", 1, 14.00)
    _listing(db, product, "hktv", "Box(es)", 12, 113.50)
    db.refresh(product)

    by_channel = {c.channel: pricing_service._priced_in_costing_unit(c, product)
                  for c in product.channels}
    assert by_channel["shopify"] == 14.00
    assert round(by_channel["hktv"], 4) == 9.4583

    cost = 8.75
    assert round(pricing_service.compute_gp(by_channel["shopify"], cost) * 100) == 38
    assert round(pricing_service.compute_gp(by_channel["hktv"], cost) * 100) == 7


def test_the_listed_price_is_still_reported_as_listed(db):
    """A person must see what the channel charges, not the restated figure."""
    product = _product(db)
    _listing(db, product, "hktv", "Box(es)", 12, 113.50)
    db.refresh(product)
    rng = pricing_service.margin_range(product, {})
    hktv = next(c for c in rng["channels"] if c["channel"] == "hktv")
    assert hktv["selling_price"] == 113.50        # not 9.4583

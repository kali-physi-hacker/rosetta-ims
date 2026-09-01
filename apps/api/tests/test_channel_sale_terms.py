"""How a channel sells a product, and what that does to delivery.

Three facts per channel, none of them derivable from how we BUY: what the
customer is charged in, how many of those make one sellable unit, and how many
units they must buy at once. The last is what decides a parcel — a case of
twelve tells you what one unit costs, not whether anyone may buy one.
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/terms.db")

import pytest  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
from services import channel_sale_terms  # noqa: E402
from services import pricing_service as ps  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


@pytest.fixture()
def db():
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _product(db, **kwargs):
    product = models.ProductVariant(
        sku_code=kwargs.pop("sku_code", "T-1"), name="Test", category="Test",
        weight_g=kwargs.pop("weight_g", 100.0),
        created_at="2026-09-01T00:00:00", updated_at="2026-09-01T00:00:00", **kwargs,
    )
    db.add(product)
    db.flush()
    return product


def _listing(db, product, channel, **kwargs):
    item = models.SellingItem(
        selling_item_key=f"{product.id}:{channel}", product_variant_id=product.id,
        channel=channel, status="ACTIVE",
        created_at="2026-09-01T00:00:00", updated_at="2026-09-01T00:00:00", **kwargs,
    )
    db.add(item)
    db.flush()
    channel_sale_terms.invalidate(db)
    return item


def test_a_blank_row_reads_as_selling_the_unit_whole(db):
    """Every row is blank today, so blank must mean the plain case."""
    product = _product(db, sku_code="T-blank")
    _listing(db, product, "shopify")

    terms = channel_sale_terms.terms_for(db, product.id, "shopify")
    assert terms.sell_uom is None
    assert terms.sell_uom_count == 1
    assert terms.order_multiple == 1
    assert terms.sells_by_measure is False
    # An unlisted channel, and a missing product, answer the same way.
    assert channel_sale_terms.terms_for(db, product.id, "hktv") is channel_sale_terms.DEFAULT
    assert channel_sale_terms.terms_for(db, None, "shopify") is channel_sale_terms.DEFAULT


def test_each_channel_sells_in_its_own_unit(db):
    """The clinic sells a 30 mL bottle by the mL; shopify sells the bottle."""
    product = _product(db, sku_code="T-bottle")
    _listing(db, product, "clinic", sell_uom="mL", sell_uom_count=Decimal(30))
    _listing(db, product, "shopify", sell_uom="Bottle(s)")

    clinic = channel_sale_terms.terms_for(db, product.id, "clinic")
    shopify = channel_sale_terms.terms_for(db, product.id, "shopify")

    assert (clinic.sell_uom, clinic.sell_uom_count) == ("mL", Decimal(30))
    assert clinic.sells_by_measure is True
    assert (shopify.sell_uom, shopify.sell_uom_count) == ("Bottle(s)", Decimal(1))
    assert shopify.sells_by_measure is False
    # $120/mL against a $1,390 bottle is a 61% margin, not a 91% loss.
    assert Decimal("120") * clinic.sell_uom_count == Decimal("3600")


def test_a_count_of_zero_is_not_a_denomination(db):
    """Nothing is priced per zero of anything; treat it as unstated."""
    product = _product(db, sku_code="T-zero")
    _listing(db, product, "shopify", sell_uom_count=Decimal(0))

    assert channel_sale_terms.terms_for(db, product.id, "shopify").sell_uom_count == 1


def test_the_channel_multiple_decides_the_parcel_not_the_supplier_pack(db):
    """What the CUSTOMER must buy is what travels together.

    A case of twelve says what one unit costs. It does not say a customer may
    not buy one — and charging delivery as though it did would spread a parcel
    over a quantity nobody can order.
    """
    product = _product(db, sku_code="T-ship", weight_g=48.0)
    db.add(models.ProductSupplier(product_id=product.id, supplier_id=1, units_per_pack=12,
                                  updated_at="2026-09-01T00:00:00"))
    channel = models.ProductChannel(product_id=product.id, channel="shopify",
                                    selling_price=100.0, updated_at="2026-09-01T00:00:00")
    db.add(channel)
    listing = _listing(db, product, "shopify")
    db.flush()

    alone = ps._shipped_together(None, product, channel)
    assert alone is None                      # the channel has not said

    listing.order_multiple = 12
    db.flush()
    channel_sale_terms.invalidate(db)
    assert ps._shipped_together(None, product, channel) == 12

    # And the parcel is weighed once for that quantity, then split.
    per_unit = ps._pack_sell_unit_delivery(product.weight_g, None, 12)
    assert round(per_unit, 4) == round(ps.shopify_logistics(48.0 * 12) / 12, 4)
    assert per_unit < ps.shopify_logistics(48.0)


def test_a_silent_channel_falls_back_to_the_supplier_floor(db):
    """Until the sell side is populated nothing moves, which is the point."""
    product = _product(db, sku_code="T-fallback", weight_g=85.0)
    link = models.ProductSupplier(product_id=product.id, supplier_id=1,
                                  units_per_pack=1, minimum_order_qty=24,
                                  updated_at="2026-09-01T00:00:00")
    db.add(link)
    channel = models.ProductChannel(product_id=product.id, channel="shopify",
                                    selling_price=20.0, updated_at="2026-09-01T00:00:00")
    db.add(channel)
    _listing(db, product, "shopify")
    db.flush()

    # The supplier's own floor still answers where the channel is blank.
    assert ps._shipped_together(link, product, channel) == 24

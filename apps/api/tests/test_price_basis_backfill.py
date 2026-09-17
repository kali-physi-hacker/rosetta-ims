"""Writing down what a price already means must not change what it costs.

The basis column records what one amount buys. The backfill states only what
the old word-matching would have concluded, so every cost in the system reads
the same before and after — that inertness is the property worth testing, and
it is what makes the column safe to ship ahead of anything that reads it.

What it cannot state without guessing it leaves unverified. A few thousand rows
are in that state and that is the honest answer, not a defect.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/basis.db")

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import models  # noqa: E402
from services import offering_costs  # noqa: E402
from services.price_basis_backfill import (  # noqa: E402
    PER_PURCHASE_UNIT,
    PER_SELLABLE_UNIT,
    backfill_price_basis,
)

NOW = "2026-09-17T00:00:00+00:00"


def _engine():
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return engine


def _price(db, *, sku, supplier_id, amount, basis, packaging=None):
    """One offering with one current price, and optionally its packaging."""
    variant = models.ProductVariant(
        sku_code=sku, name=f"Product {sku}", category="Food", uom="Can(s)",
        storage_rule="any", status="ACTIVE", hero_sku=0, created_at=NOW, updated_at=NOW)
    db.add(variant)
    db.add(models.Supplier(id=supplier_id, code=f"S{supplier_id}",
                           name=f"Supplier {supplier_id}", created_at=NOW))
    db.flush()
    link = models.ProductSupplier(product_id=variant.id, supplier_id=supplier_id,
                                  supplier_sku=f"SK-{sku}", updated_at=NOW)
    db.add(link)
    offering = models.SupplierOffering(
        supplier_product_key=f"supplier:{supplier_id}:offer:{sku}",
        supplier_id=supplier_id, product_variant_id=variant.id,
        supplier_sku=f"SK-{sku}", status="active", created_at=NOW, updated_at=NOW)
    db.add(offering)
    db.flush()
    if packaging is not None:
        purchase, sellable, per_purchase = packaging
        db.add(models.CataloguePackagingConfiguration(
            supplier_product_id=offering.id, purchase_uom_code=purchase,
            sellable_unit_uom_code=sellable,
            sellable_units_per_purchase_unit=per_purchase, created_at=NOW))
    db.add(models.CatalogueSupplierPrice(
        supplier_product_id=offering.id, amount=amount, currency="HKD",
        price_basis_uom_code=basis, is_current=1, created_at=NOW))
    db.commit()
    offering_costs.invalidate(db)
    return link


def _rows(engine):
    with engine.connect() as conn:
        return {
            r[0]: (r[1], r[2]) for r in conn.execute(text(
                "SELECT price_basis_uom_code, basis, basis_verified "
                "FROM catalogue_supplier_prices"))
        }


def test_the_column_defaults_to_what_the_old_reader_assumed():
    """Before anything runs: every row already says per-unit, unverified."""
    engine = _engine()
    with Session(engine) as db:
        _price(db, sku="A1", supplier_id=1, amount=130, basis="UNIT")
    assert _rows(engine)["UNIT"] == (PER_SELLABLE_UNIT, 0)


def test_a_basis_matching_the_purchase_unit_is_stated_as_a_pack_price():
    engine = _engine()
    with Session(engine) as db:
        _price(db, sku="B1", supplier_id=2, amount=130, basis="BOX",
               packaging=("BOX", "TABLET", 60))
    assert backfill_price_basis(engine)["pack"] == 1
    assert _rows(engine)["BOX"] == (PER_PURCHASE_UNIT, 1)


def test_a_basis_matching_the_sellable_unit_is_confirmed_per_unit():
    engine = _engine()
    with Session(engine) as db:
        _price(db, sku="C1", supplier_id=3, amount=42, basis="CAN",
               packaging=("CASE", "CAN", 12))
    assert backfill_price_basis(engine)["unit"] == 1
    assert _rows(engine)["CAN"] == (PER_SELLABLE_UNIT, 1)


def test_a_container_word_states_a_pack_price_without_vouching_for_it():
    """Royal Canin's shape: the price says CASE, the packaging says CAN(S).

    The words disagree about WHICH container, so the count cannot be trusted —
    but "CASE" is not ambiguous about the other question. It buys a pack. Saying
    so is what keeps the old reader's refusal intact: that reader returned no
    cost at all here, and a case price presented as a unit price is the
    direction that costs money. Left at the per-unit default, the refusal would
    silently become an undivided number the moment anything read the flag.

    Unverified, because nobody has confirmed the count.
    """
    engine = _engine()
    with Session(engine) as db:
        _price(db, sku="D1", supplier_id=4, amount=354, basis="CASE",
               packaging=("CAN(S)", "UNIT", 12))
    result = backfill_price_basis(engine)
    assert result["pack"] == 1          # stated as a pack price...
    assert result["unverified"] == 1    # ...but nobody has vouched for the count
    assert _rows(engine)["CASE"] == (PER_PURCHASE_UNIT, 0)


def test_a_word_that_names_nothing_keeps_the_per_unit_default():
    """`UNIT` on 94% of rows is the value meaning "no conversion needed". It
    stays that, unverified, because the question was never really answered.
    """
    engine = _engine()
    with Session(engine) as db:
        _price(db, sku="D2", supplier_id=5, amount=99, basis="UNIT")
    result = backfill_price_basis(engine)
    # Nothing to change: it already sits at the per-unit default, unverified.
    assert result["status"] == "already_backfilled"
    assert _rows(engine)["UNIT"] == (PER_SELLABLE_UNIT, 0)


def test_the_flag_reproduces_what_the_words_used_to_say():
    """The property the whole change rests on, stated as the four real shapes.

    Three of these read exactly as they did when the basis was inferred from
    the words. The fourth is the point of the exercise: a price whose word says
    CASE over a packaging row that says CAN(S) used to satisfy no comparison at
    all, so the reader refused and published nothing. The words disagree about
    which container; they do not disagree that it is one. With that written
    down, the cost is simply 354 over the twelve the packaging holds.
    """
    engine = _engine()
    with Session(engine) as db:
        priced_by_the_box = _price(db, sku="E1", supplier_id=11, amount=130,
                                   basis="BOX", packaging=("BOX", "TABLET", 60))
        priced_by_the_can = _price(db, sku="E2", supplier_id=12, amount=42,
                                   basis="CAN", packaging=("CASE", "CAN", 12))
        words_disagree = _price(db, sku="E3", supplier_id=13, amount=354,
                                basis="CASE", packaging=("CAN(S)", "UNIT", 12))
        no_packaging = _price(db, sku="E4", supplier_id=14, amount=99, basis="UNIT")
        ids = [link.id for link in
               (priced_by_the_box, priced_by_the_can, words_disagree, no_packaging)]

    backfill_price_basis(engine)

    with Session(engine) as db:
        offering_costs.invalidate(db)
        cost = [offering_costs.unit_cost_for_link(db.get(models.ProductSupplier, i))
                for i in ids]

    assert cost[0] == 130 / 60          # unchanged: the words agreed, and divided
    assert cost[1] == 42                # unchanged: the basis IS the sellable unit
    assert cost[2] == 354 / 12          # was None — a refusal nobody could act on
    assert cost[3] == 99                # unchanged: no packaging, nothing to divide


def test_a_pack_price_with_no_count_is_still_refused():
    """The refusal that protects: absent beats a case price wearing a unit's
    clothes. Stating PER_PURCHASE_UNIT is what keeps it — left at the per-unit
    default, 123.60 would read as the cost of one pouch.
    """
    engine = _engine()
    with Session(engine) as db:
        link = _price(db, sku="G1", supplier_id=31, amount=123.60, basis="CASE")
        link_id = link.id
    backfill_price_basis(engine)
    with Session(engine) as db:
        offering_costs.invalidate(db)
        assert offering_costs.unit_cost_for_link(
            db.get(models.ProductSupplier, link_id)) is None


def test_running_it_twice_does_nothing_the_second_time():
    engine = _engine()
    with Session(engine) as db:
        _price(db, sku="F1", supplier_id=21, amount=130, basis="BOX",
               packaging=("BOX", "TABLET", 60))
    first = backfill_price_basis(engine)
    snapshot = _rows(engine)
    second = backfill_price_basis(engine)
    assert first["status"] == "backfilled"
    assert second["status"] == "already_backfilled"
    assert _rows(engine) == snapshot


def test_a_database_without_the_column_is_left_alone():
    engine = create_engine("sqlite:///:memory:")
    assert backfill_price_basis(engine)["status"] == "no_table"


def test_two_placeholders_agreeing_is_not_confirmation():
    """`UNIT` is what set_offering_packaging writes when no sellable unit is
    known, and it is the price basis on 94% of rows. A basis of UNIT matching a
    sellable unit of UNIT says only that neither was filled in.

    Treating it as confirmation marked 485 production rows verified — among
    them VSRx 60004260, a $485 "tablet" that is really a bottle of a hundred,
    publishing a -16,067% margin under a flag that said someone had checked it.
    """
    engine = _engine()
    with Session(engine) as db:
        _price(db, sku="H1", supplier_id=41, amount=485, basis="UNIT",
               packaging=("BOTTLE", "UNIT", 100))
    backfill_price_basis(engine)
    basis, verified = _rows(engine)["UNIT"]
    assert basis == PER_SELLABLE_UNIT   # the number it publishes is unchanged
    assert verified == 0                # but nobody has vouched for it


def test_a_real_unit_still_counts_as_confirmation():
    """The guard must not swallow the genuine ones: BOTTLE naming BOTTLE is two
    facts agreeing, and 19 production rows are in that state.
    """
    engine = _engine()
    with Session(engine) as db:
        _price(db, sku="H2", supplier_id=42, amount=60, basis="BOTTLE",
               packaging=("BOX", "BOTTLE", 12))
    backfill_price_basis(engine)
    assert _rows(engine)["BOTTLE"] == (PER_SELLABLE_UNIT, 1)

"""Minting the internal SKU for products that never got one.

1,704 products on the live database carry something else in that column —
almost all of them the product NAME, from before the code was allocated. The
risk in fixing that is not the minting; it is everything else that stores the
same string. A rename that updates only `products` silently detaches sales
history and care tags, and a rename that updates too much rewrites what a
SUPPLIER calls its product.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/sku_backfill.db")

import pytest
from sqlalchemy import text

import database
import models
from scripts import backfill_missing_skus as backfill

NOW = "2026-08-03T00:00:00+00:00"


@pytest.fixture()
def db():
    models.Base.metadata.create_all(bind=database.engine)
    session = database.SessionLocal()
    try:
        for model in (models.AuditLog, models.CatalogueAuditEvent, models.CatalogueItem,
                      models.ProductSupplier, models.ProductVariant, models.CategoryRule):
            session.query(model).delete()
        session.commit()
        yield session
        session.rollback()
    finally:
        session.close()


def _product(db, sku, name, category="Medicine"):
    p = models.ProductVariant(sku_code=sku, name=name, category=category, uom="unit",
                              storage_rule="any", status="ACTIVE", hero_sku=0,
                              created_at=NOW, updated_at=NOW)
    db.add(p)
    db.flush()
    return p


def test_only_products_without_an_internal_code_are_touched(db):
    good = _product(db, "50010287", "Already has one")
    bare = _product(db, "Proheart Inj", "Proheart Inj")
    db.commit()

    missing = backfill.products_missing_a_sku(db)

    assert [p.id for p in missing] == [bare.id]
    assert good.sku_code == "50010287"


def test_the_minted_code_follows_the_category_scheme(db):
    _product(db, "50010287", "seed so the counter has a floor")
    food = _product(db, "Royal Canin - Adult 4kg", "Royal Canin", category="Food")
    med = _product(db, "Proheart Inj", "Proheart", category="Medicine")
    db.commit()

    mint = backfill._allocator(db)
    first, second = mint(backfill._digit_for(db, food.category)), mint(backfill._digit_for(db, med.category))

    assert first.startswith("1") and med and second.startswith("5"), "leading digit is the category's"
    assert len(first) == len(second) == 8
    assert int(first[1:]) == 10288 and int(second[1:]) == 10289, "one shared ascending counter"


def test_a_category_with_no_sku_digit_is_refused_rather_than_guessed(db):
    _product(db, "Blood/Skin/Tissue Smear", "Smear", category="Services")
    db.commit()

    assert backfill._digit_for(db, "Services") is None


def test_a_category_digit_added_in_the_ui_is_honoured(db):
    db.add(models.CategoryRule(category="Services", sku_digit="8", gp_floor=0.3, storage_rule="any"))
    db.commit()

    assert backfill._digit_for(db, "Services") == "8"


def test_the_denormalised_copies_follow_the_rename(db, monkeypatch):
    product = _product(db, "Proheart Inj", "Proheart Inj")
    db.add(models.CatalogueAuditEvent(sku_code="Proheart Inj", action="confirm_match", created_at=NOW))
    db.add(models.CatalogueItem(import_id=1, assigned_sku="Proheart Inj",
                                review_status="matched", skipped=0, created_at=NOW))
    db.commit()

    monkeypatch.setattr("sys.argv", ["backfill", "--apply"])
    backfill.main()
    db.expire_all()

    renamed = db.query(models.ProductVariant).filter_by(id=product.id).one().sku_code
    assert renamed != "Proheart Inj" and len(renamed) == 8
    assert db.query(models.CatalogueAuditEvent).one().sku_code == renamed
    assert db.query(models.CatalogueItem).one().assigned_sku == renamed
    change = db.query(models.AuditLog).filter_by(action="product.sku_change").one()
    assert "Proheart Inj" in change.details and renamed in change.details


def test_a_suppliers_own_code_is_never_rewritten(db, monkeypatch):
    """product_suppliers.supplier_sku matches only because both fields were
    seeded from the same name. It is what the SUPPLIER calls the product, and
    renaming ours does not change theirs."""
    product = _product(db, "Proheart Inj", "Proheart Inj")
    db.add(models.ProductSupplier(product_id=product.id, supplier_id=1,
                                  supplier_sku="Proheart Inj", updated_at=NOW))
    db.commit()

    monkeypatch.setattr("sys.argv", ["backfill", "--apply"])
    backfill.main()
    db.expire_all()

    assert db.query(models.ProductSupplier).one().supplier_sku == "Proheart Inj"


def test_a_dry_run_writes_nothing(db, monkeypatch):
    product = _product(db, "Proheart Inj", "Proheart Inj")
    db.commit()

    monkeypatch.setattr("sys.argv", ["backfill"])
    backfill.main()
    db.expire_all()

    assert db.query(models.ProductVariant).filter_by(id=product.id).one().sku_code == "Proheart Inj"
    assert db.query(models.AuditLog).count() == 0


def test_absent_client_tables_are_not_an_error(db):
    """They are loaded by a separate importer and missing on a fresh deploy."""
    assert backfill._count_sql(db, "clientssot_purchases", "sku", ["Proheart Inj"]) == 0
    backfill._update_sql(db, "clientssot_purchases", "sku", "Proheart Inj", "50010288")


def test_the_client_tables_follow_when_they_do_exist(db, monkeypatch):
    db.execute(text("create table clientssot_purchases (sku text, product text)"))
    db.execute(text("insert into clientssot_purchases values ('Proheart Inj', 'Proheart Inj')"))
    product = _product(db, "Proheart Inj", "Proheart Inj")
    db.commit()

    monkeypatch.setattr("sys.argv", ["backfill", "--apply"])
    backfill.main()
    db.expire_all()

    renamed = db.query(models.ProductVariant).filter_by(id=product.id).one().sku_code
    assert db.execute(text("select sku from clientssot_purchases")).scalar() == renamed

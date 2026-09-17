"""What the ops sheet is allowed to publish, and the second way a row earns it.

The sheet is read as fact by people costing orders, so it carries only rows
someone has agreed to. Until now the only way to agree was a serving
publication from the review desk — which left every SKU entered through the
upload workbook recorded, priced, and silently absent from the sheet.

A workbook import is the other kind of agreement: a person chose the file, read
the preview and pressed Apply. These pin that, and the two refusals around it —
a row with no agreed cost still stays out, and a pair the desk has published is
never doubled by a manual mark.
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/ops_manual.db")

import database  # noqa: E402
import models  # noqa: E402
from services import offering_costs, ops_db_export, workbook_import  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)

NOW = "2026-09-11T00:00:00"


class _Actor:
    id = 5
    username = "ops"
    display_name = "Ops Person"
    role = "admin"


@pytest.fixture()
def db():
    session = database.SessionLocal()
    for model in (models.CatalogueServingPublication, models.CatalogueSupplierPrice,
                  models.CataloguePackagingConfiguration, models.SupplierOffering,
                  models.MbbTerm, models.SellingItem, models.ProductChannel,
                  models.InventoryItem, models.ProductSupplier, models.ProductVariant,
                  models.Supplier):
        session.query(model).delete()
    session.commit()
    offering_costs.invalidate(session)
    yield session
    session.rollback()
    session.close()


def _seed(db, sku="50010302"):
    supplier = models.Supplier(code="ALFA", name="Alfamedic",
                               normalized_name="alfamedic", created_at=NOW)
    db.add(supplier); db.flush()
    product = models.ProductVariant(sku_code=sku, name="Metacoxx 1ML", category="Medicine",
                                    uom="Unit(s)", storage_rule="any", status="ACTIVE",
                                    hero_sku=0, created_at=NOW, updated_at=NOW)
    db.add(product); db.flush()
    db.commit()
    return supplier, product


def _import(db, **over):
    row = {"sku": "50010302", "buy.supplier": "Alfamedic", "buy.sku": "AL-1",
           "buy.pack": "Box(es)", "buy.per_pack": 60, "buy.cost": 130, "buy.cost_per": "pack"}
    row.update(over)
    return workbook_import.import_products(db, [row], actor=_Actor())


def test_applying_the_workbook_marks_the_link_reviewed(db):
    _seed(db)
    result = _import(db)
    assert result["summary"]["updated"] == 1, result["rows"][0]
    link = db.query(models.ProductSupplier).one()
    assert link.manual_review_status == "manual_workbook"
    assert link.manual_reviewed_by == "Ops Person"
    assert link.manual_reviewed_at


def test_a_manually_reviewed_row_reaches_the_ops_sheet(db):
    """The whole point: imported, priced, and now actually published."""
    _seed(db)
    _import(db)
    rows = ops_db_export.build_published_rows(db)
    assert len(rows) == 1
    row = rows[0]
    assert row["supplier"] == "Alfamedic"
    assert row["ims_sku"] == "50010302"
    assert row["supplier_product_code"] == "AL-1"
    # the cost the workbook stated, in the basis it stated it in
    assert str(row["cost_price_amount"]) == "130"
    assert row["sellable_units_per_purchase_unit"] == "60"


def test_a_reviewed_row_with_no_agreed_cost_stays_out(db):
    """Absent beats wrong: nobody can cost an order from a blank price.

    The same refusal this module already makes for a container whose contents
    nobody printed.
    """
    supplier, product = _seed(db)
    db.add(models.ProductSupplier(
        product_id=product.id, supplier_id=supplier.id, is_primary=1,
        cost_source="manual", pack_source="manual", updated_at=NOW,
        manual_review_status="manual_workbook", manual_reviewed_at=NOW,
        manual_reviewed_by="Ops Person"))
    db.commit()
    assert ops_db_export.build_published_rows(db) == []


def test_the_review_desk_wins_where_both_exist(db):
    """A published pair must not be doubled by a manual mark on the same link."""
    supplier, product = _seed(db)
    _import(db)
    link = db.query(models.ProductSupplier).one()
    offering = db.query(models.SupplierOffering).one()
    db.add(models.CatalogueServingPublication(
        serving_item_uuid="u" * 36, contract_version="v1", publication_key="k1",
        publication_version="v2026-09-11T00:00:00Z", canonical_sku=product.sku_code,
        product_variant_key="pv", product_variant_name="Metacoxx 1ML",
        product_id=product.id, supplier_id=supplier.id, supplier_product_id=offering.id,
        supplier_sku="AL-1", current_approved_cost_amount=130,
        current_approved_cost_currency="HKD", current_approved_cost_basis_uom_code="BOX(ES)",
        review_status="APPROVED", published_at=NOW, mastering_candidate_uuid="c" * 36,
        catalogue_item_uuid="i" * 36, raw_observation_ids_json="[]", lineage_json="{}",
        snapshot_json="{}", is_current=1, created_at=NOW))
    db.commit()

    rows = ops_db_export.build_published_rows(db)
    assert len(rows) == 1                      # one row, not two
    assert link.manual_review_status == "manual_workbook"   # the mark still stands


def test_a_superseded_publication_publishes_nothing(db):
    """It was replaced. Five of these were doubling rows in the live sheet."""
    supplier, product = _seed(db)
    common = dict(
        contract_version="v1", publication_key="k1",
        canonical_sku=product.sku_code, product_variant_key="pv",
        product_variant_name="Metacoxx 1ML", product_id=product.id,
        supplier_id=supplier.id, supplier_sku="AL-1",
        current_approved_cost_amount=130, current_approved_cost_currency="HKD",
        current_approved_cost_basis_uom_code="UNIT", review_status="APPROVED",
        published_at=NOW, mastering_candidate_uuid="c" * 36, catalogue_item_uuid="i" * 36,
        raw_observation_ids_json="[]", lineage_json="{}", snapshot_json="{}", created_at=NOW)
    # serving_item_uuid is unique per publication — a supersession is a new
    # row, not a rewrite of the old one.
    db.add(models.CatalogueServingPublication(
        serving_item_uuid="a" * 36, publication_version="v1",
        is_current=0, superseded_at=NOW, **common))
    db.add(models.CatalogueServingPublication(
        serving_item_uuid="b" * 36, publication_version="v2", is_current=1, **common))
    db.commit()
    assert len(ops_db_export.build_published_rows(db)) == 1


# ── marking by hand ─────────────────────────────────────────────────────────
# The workbook marks what it writes. This is the other way in: a person looking
# at a SKU that was never imported and never went through the review desk, who
# knows it is right and needs the sheet to carry it.

def test_the_button_marks_a_link_and_the_row_publishes(db):
    import main
    from dependencies import require_user
    from fastapi.testclient import TestClient

    supplier, product = _seed(db)
    _import(db)                                   # gives it a cost, then clear the mark
    link = db.query(models.ProductSupplier).one()
    link.manual_review_status = None
    link.manual_reviewed_at = None
    link.manual_reviewed_by = None
    db.commit()
    assert ops_db_export.build_published_rows(db) == []

    main.app.dependency_overrides[require_user] = lambda: _Actor()
    try:
        with TestClient(main.app) as client:
            r = client.patch(f"/products/{product.sku_code}/suppliers/{link.id}/ops-review",
                             json={"reviewed": True})
            assert r.status_code == 200, r.text
    finally:
        main.app.dependency_overrides.pop(require_user, None)

    db.expire_all()
    link = db.query(models.ProductSupplier).one()
    assert link.manual_review_status == "manual_review"     # not 'manual_workbook'
    assert link.manual_reviewed_by == "Ops Person"
    assert len(ops_db_export.build_published_rows(db)) == 1


def test_withdrawing_takes_the_row_back_off(db):
    """Never reviewed and withdrawn are different facts, which is why the
    column is a status: the same call with reviewed=False clears it."""
    import main
    from dependencies import require_user
    from fastapi.testclient import TestClient

    supplier, product = _seed(db)
    _import(db)
    link = db.query(models.ProductSupplier).one()
    assert len(ops_db_export.build_published_rows(db)) == 1

    main.app.dependency_overrides[require_user] = lambda: _Actor()
    try:
        with TestClient(main.app) as client:
            r = client.patch(f"/products/{product.sku_code}/suppliers/{link.id}/ops-review",
                             json={"reviewed": False})
            assert r.status_code == 200, r.text
    finally:
        main.app.dependency_overrides.pop(require_user, None)

    db.expire_all()
    assert db.query(models.ProductSupplier).one().manual_review_status is None
    assert ops_db_export.build_published_rows(db) == []


# ── one answer to "what does one unit cost" ─────────────────────────────────

def test_a_published_cost_already_per_unit_is_not_divided_again(db):
    """The real shape of the bug, which only ever hit PUBLICATION rows.

    The publication states its own basis — UNIT for a cost already per unit —
    and the export read that basis off the packaging row instead, where the
    column does not exist. Always None, so the pack count was divided in a
    second time: Hill's 10006311 published $0.7333 against a true $17.60, a
    96.6% margin where the real one is 18.3%.
    """
    supplier, product = _seed(db, sku="10006311")
    product.uom = "Can(s)"
    db.commit()
    link = models.ProductSupplier(
        product_id=product.id, supplier_id=supplier.id, is_primary=1,
        cost_source="manual", pack_source="manual", updated_at=NOW, supplier_sku="3394")
    db.add(link); db.flush()
    offering_costs.set_offering_packaging(db, link, purchase_uom="Capsule(s)", sellable_units=24)
    db.commit()
    offering = db.query(models.SupplierOffering).one()
    db.add(models.CatalogueServingPublication(
        serving_item_uuid="p" * 36, contract_version="v1", publication_key="k1",
        publication_version="v1", canonical_sku=product.sku_code,
        product_variant_key="pv", product_variant_name=product.name,
        product_id=product.id, supplier_id=supplier.id, supplier_product_id=offering.id,
        supplier_sku="3394",
        current_approved_cost_amount=17.60,          # ALREADY per can
        current_approved_cost_currency="HKD",
        current_approved_cost_basis_uom_code="UNIT",  # and it says so
        review_status="APPROVED", published_at=NOW, mastering_candidate_uuid="c" * 36,
        catalogue_item_uuid="i" * 36, raw_observation_ids_json="[]", lineage_json="{}",
        snapshot_json="{}", is_current=1, created_at=NOW))
    db.commit()

    rows = ops_db_export.build_published_rows(db)
    assert len(rows) == 1
    assert float(rows[0]["cost_per_unit"]) == 17.60      # not 17.60 / 24


def test_the_ops_sheet_costs_what_the_rest_of_the_system_costs(db):
    """This module used to derive its own per-unit cost from the packaging.

    Two implementations of one question drift. On production 59 of 443 rows
    disagreed with the app, and not subtly: a Can(s) product whose purchase uom
    read Capsule(s) divided a second time and published $0.7333 against a true
    $17.60 — a 96.6% margin where the real one is 18.3% — and most of the Royal
    Canin (Vet) range published margins near -650%.
    """
    supplier, product = _seed(db, sku="10006311")
    product.uom = "Can(s)"
    db.commit()
    link = models.ProductSupplier(
        product_id=product.id, supplier_id=supplier.id, is_primary=1,
        cost_source="manual", pack_source="manual", updated_at=NOW,
        manual_review_status="manual_review", manual_reviewed_at=NOW,
        manual_reviewed_by="Ops Person", supplier_sku="3394")
    db.add(link); db.flush()
    # the shape that made it divide twice: a purchase unit that is not the
    # sellable unit, and a price already stated per purchase unit
    offering_costs.set_offering_packaging(db, link, purchase_uom="Capsule(s)", sellable_units=24)
    offering_costs.record_catalogue_price(db, link, amount=422.40, per="pack",
                                          pack_uom="Capsule(s)")
    db.commit()
    offering_costs.invalidate(db)

    expected = offering_costs.unit_cost_for_link(link)
    assert round(expected, 4) == round(422.40 / 24, 4)          # 17.60 a can

    rows = ops_db_export.build_published_rows(db)
    assert len(rows) == 1
    assert float(rows[0]["cost_per_unit"]) == round(expected, 4)


def test_a_link_with_no_offering_still_falls_back_to_deriving(db):
    """Asking is preferred, not required: a row the offering cannot answer for
    keeps the packaging-derived figure rather than losing its cost."""
    supplier, product = _seed(db)
    _import(db)
    link = db.query(models.ProductSupplier).one()
    assert offering_costs.unit_cost_for_link(link) is not None
    rows = ops_db_export.build_published_rows(db)
    assert rows and rows[0]["cost_per_unit"]


def _publication(product, supplier, offering, *, code, amount, uuid, key):
    return models.CatalogueServingPublication(
        serving_item_uuid=uuid * 36, contract_version="v1", publication_key=key,
        publication_version="v2026-09-11T00:00:00Z", canonical_sku=product.sku_code,
        product_variant_key="pv", product_variant_name=product.name,
        product_id=product.id, supplier_id=supplier.id, supplier_product_id=offering.id,
        supplier_sku=code, current_approved_cost_amount=amount,
        current_approved_cost_currency="HKD", current_approved_cost_basis_uom_code="BOX(ES)",
        review_status="APPROVED", published_at=NOW, mastering_candidate_uuid=uuid * 36,
        catalogue_item_uuid=uuid * 36, raw_observation_ids_json="[]", lineage_json="{}",
        snapshot_json="{}", is_current=1, created_at=NOW)


def test_each_row_costs_the_purchase_it_prints(db):
    """A row's printed cost, over its pack count, must be its printed unit cost.

    The offering answers per (supplier, product); a row is a publication, and a
    publication only snapshots what the desk approved. Two publications on one
    product shared one link, so the link's answer went out beside both
    snapshots: Amoxyclav 50010310 published a $720 box of 240 next to a unit
    cost of $3.50, which is 840/240 — the offering's purchase, not the row's.
    Checked against Alfamedic's own document, that row's arithmetic does not
    work. Both columns now come from the one price.
    """
    supplier, product = _seed(db, sku="50010310")
    _import(db, sku="50010310", **{"buy.per_pack": 240, "buy.cost": 840})
    offering = db.query(models.SupplierOffering).one()
    db.add(_publication(product, supplier, offering,
                        code="AM5556-240s", amount=840, uuid="a", key="k-840"))
    db.add(_publication(product, supplier, offering,
                        code="AM5557-300's", amount=720, uuid="b", key="k-720"))
    db.commit()

    rows = {r["supplier_product_code"]: r for r in ops_db_export.build_published_rows(db)}
    assert len(rows) == 2
    for row in rows.values():
        amount = Decimal(str(row["cost_price_amount"]))
        per_pack = Decimal(str(row["sellable_units_per_purchase_unit"]))
        assert amount / per_pack == Decimal(str(row["cost_per_unit"]))

    # Both describe the same offering, so both carry its price — the stale
    # $720 snapshot no longer stands beside a unit cost it cannot produce.
    assert str(rows["AM5557-300's"]["cost_price_amount"]) == "840"
    assert str(rows["AM5557-300's"]["cost_per_unit"]) == "3.5"

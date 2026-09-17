"""Reading the upload workbook back in.

The load-bearing property is that the writer and the reader are inverses: a
workbook exported from the catalogue and uploaded again unchanged must report
every row unchanged. That is what `test_export_reimports_unchanged` asserts,
and it is the test that would have caught `buy.multiple` being a column the
sheet showed a unit for and never a number.

The other half is the deals sheet's add-only contract. Deals are commercial
terms; a spreadsheet may propose one, but it may not quietly edit or retire one
that is already live.
"""

from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/workbook_import.db")

import database  # noqa: E402
import models  # noqa: E402
from services import offering_costs, upload_workbook, workbook_import  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)

NOW = "2026-09-11T00:00:00"


class _Actor:
    id = 1
    username = "tester"
    display_name = "Tester"
    role = "admin"


@pytest.fixture()
def db():
    session = database.SessionLocal()
    _wipe(session)
    yield session
    session.rollback()
    _wipe(session)
    session.close()


def _wipe(session):
    for model in (models.MbbTerm, models.CatalogueSupplierPrice,
                  models.CataloguePackagingConfiguration, models.SupplierOffering,
                  models.SellingItem, models.ProductChannel, models.InventoryItem,
                  models.ProductSupplier, models.ProductVariant, models.Supplier):
        session.query(model).delete()
    session.commit()
    offering_costs.invalidate(session)


def _supplier(db, name="Alfamedic", code="ALFA"):
    row = models.Supplier(code=code, name=name, normalized_name=name.lower(), created_at=NOW)
    db.add(row); db.commit()
    return row


def _product(db, supplier=None, *, sku="50010302", name="Metacoxx 1ML",
             category="Medicine", uom="Unit(s)"):
    product = models.ProductVariant(
        sku_code=sku, name=name, category=category, uom=uom, storage_rule="any",
        status="ACTIVE", hero_sku=0, created_at=NOW, updated_at=NOW)
    db.add(product); db.flush()
    link = None
    if supplier is not None:
        link = models.ProductSupplier(
            product_id=product.id, supplier_id=supplier.id, is_primary=1,
            cost_source="manual", pack_source="manual", updated_at=NOW)
        db.add(link)
    db.commit()
    return product, link


def _rows(*dicts):
    return [dict(d) for d in dicts]


def _apply(db, rows, *, dry_run=False):
    return workbook_import.import_products(db, _rows(*rows), actor=_Actor(), dry_run=dry_run)


def _apply_deals(db, rows, *, dry_run=False):
    return workbook_import.import_deals(db, _rows(*rows), actor=_Actor(), dry_run=dry_run)


# ── products ────────────────────────────────────────────────────────────────

def test_blank_sku_mints_a_product(db):
    _supplier(db)
    result = _apply(db, [{
        "sku": "", "name": "New Thing 10ml", "category": "Medicine", "unit": "Unit(s)",
        "buy.supplier": "Alfamedic", "buy.sku": "X-1", "buy.pack": "Box(es)",
        "buy.per_pack": 60, "buy.cost": 130, "buy.cost_per": "pack",
    }])
    assert result["summary"] == {"total": 1, "created": 1, "updated": 0,
                                 "unchanged": 0, "not_found": 0, "errors": 0,
                                 "warnings": 0}
    sku = result["rows"][0]["sku"]
    # Minted by the same service the catalogue review flow uses: the category's
    # digit, then the global counter. Medicine is 5.
    assert sku and sku.startswith("5") and len(sku) == 8
    product = db.query(models.ProductVariant).filter_by(sku_code=sku).one()
    assert product.name == "New Thing 10ml"
    assert db.query(models.InventoryItem).filter_by(product_variant_id=product.id).count() == 1


def test_creation_refuses_a_row_that_cannot_be_bought(db):
    result = _apply(db, [{"sku": "", "name": "Nameless supplier", "category": "Medicine",
                          "unit": "Unit(s)"}])
    assert result["summary"]["errors"] == 1
    assert result["summary"]["created"] == 0
    # Every missing field is named at once — a person fixing a sheet should not
    # have to upload it six times to be told about six columns.
    assert any("buy.supplier" in e for e in result["rows"][0]["errors"])
    assert any("buy.cost" in e for e in result["rows"][0]["errors"])
    assert db.query(models.ProductVariant).count() == 0


def test_a_category_with_no_sku_digit_says_so(db):
    _supplier(db)
    result = _apply(db, [{
        "sku": "", "name": "A service", "category": "Services", "unit": "Unit(s)",
        "buy.supplier": "Alfamedic", "buy.sku": "S-1", "buy.pack": "Unit(s)",
        "buy.per_pack": 1, "buy.cost": 10, "buy.cost_per": "unit",
    }])
    assert result["summary"]["errors"] == 1
    detail = result["rows"][0]["errors"][0]
    assert "Services" in detail and "Categories" in detail


def test_filled_sku_updates_and_blank_cells_leave_alone(db):
    supplier = _supplier(db)
    product, _ = _product(db, supplier)
    product.subcategory = "analgesic"; db.commit()

    result = _apply(db, [{"sku": "50010302", "name": "Metacoxx renamed"}])
    assert result["summary"]["updated"] == 1
    db.refresh(product)
    assert product.name == "Metacoxx renamed"
    assert product.subcategory == "analgesic"      # untouched, not blanked


def test_a_dash_clears_and_a_blank_does_not(db):
    supplier = _supplier(db)
    product, link = _product(db, supplier)
    link.barcode = "40123456"; link.rrp = 18.0; db.commit()

    _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic", "buy.barcode": "-"}])
    db.refresh(link)
    assert link.barcode is None
    assert link.rrp == 18.0


def test_unchanged_rows_are_reported_as_unchanged(db):
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply(db, [{"sku": "50010302", "name": "Metacoxx 1ML"}])
    assert result["summary"]["unchanged"] == 1
    assert result["rows"][0]["changes"] == {}


def test_unknown_sku_and_unknown_supplier_are_reported_not_invented(db):
    _supplier(db)
    result = _apply(db, [
        {"sku": "99999999", "name": "Ghost"},
        {"sku": "", "name": "Orphan", "category": "Medicine", "unit": "Unit(s)",
         "buy.supplier": "Nobody Ltd", "buy.sku": "N-1", "buy.pack": "Unit(s)",
         "buy.per_pack": 1, "buy.cost": 5, "buy.cost_per": "unit"},
    ])
    assert result["summary"]["not_found"] == 1
    assert result["summary"]["errors"] == 1
    assert db.query(models.Supplier).count() == 1      # never created one


def test_dry_run_writes_nothing(db):
    _supplier(db)
    row = {"sku": "", "name": "Preview only", "category": "Medicine", "unit": "Unit(s)",
           "buy.supplier": "Alfamedic", "buy.sku": "P-1", "buy.pack": "Unit(s)",
           "buy.per_pack": 1, "buy.cost": 9, "buy.cost_per": "unit"}
    preview = _apply(db, [row], dry_run=True)
    assert preview["summary"]["created"] == 1
    assert preview["rows"][0]["sku"]               # the real SKU it would take
    assert db.query(models.ProductVariant).count() == 0


def test_a_pack_price_stays_a_pack_price(db):
    """$130 a box of 60 must read back as 130, not 2.1666.

    This is the column the whole cost_per pair exists for. Storing the divided
    figure loses the only number anyone can check against a supplier's PDF.
    """
    supplier = _supplier(db)
    _product(db, supplier)
    _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic", "buy.pack": "Box(es)",
                 "buy.per_pack": 60, "buy.cost": 130, "buy.cost_per": "pack"}])

    link = db.query(models.ProductSupplier).one()
    offering_costs.invalidate(db)
    price = offering_costs.catalogue_price_for_link(link)
    assert (price.amount, price.per, price.units_per_pack) == (130.0, "pack", 60.0)
    # and the figure every margin runs on is still the divided one
    assert round(offering_costs.unit_cost_for_link(link), 4) == round(130 / 60, 4)


def test_a_unit_price_stays_a_unit_price(db):
    supplier = _supplier(db)
    _product(db, supplier)
    _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic", "buy.pack": "Box(es)",
                 "buy.per_pack": 60, "buy.cost": 2.17, "buy.cost_per": "unit"}])
    link = db.query(models.ProductSupplier).one()
    offering_costs.invalidate(db)
    price = offering_costs.catalogue_price_for_link(link)
    assert (price.amount, price.per) == (2.17, "unit")
    assert offering_costs.unit_cost_for_link(link) == 2.17


def test_weight_is_converted_on_the_way_in(db):
    supplier = _supplier(db)
    product, _ = _product(db, supplier)
    _apply(db, [{"sku": "50010302", "weight": 1.5, "weight_unit": "kg"}])
    db.refresh(product)
    assert product.weight_g == 1500.0        # the sheet states 1.5 kg; grams is ours
    assert product.weight_unit == "kg"


def test_sell_columns_reach_both_rows_that_hold_them(db):
    supplier = _supplier(db)
    product, _ = _product(db, supplier)
    _apply(db, [{"sku": "50010302", "sell.clinic.uom": "mL", "sell.clinic.per_unit": 10,
                 "sell.clinic.multiple": 2, "sell.clinic.price": 48}])
    item = db.query(models.SellingItem).filter_by(
        product_variant_id=product.id, channel="clinic").one()
    channel = db.query(models.ProductChannel).filter_by(
        product_id=product.id, channel="clinic").one()
    assert (item.sell_uom, float(item.sell_uom_count)) == ("mL", 10.0)
    # selling_items is what the sheet exports; product_channels is what every
    # price read still uses. They agree on all 11,919 live rows and must keep to it.
    assert item.selling_price == channel.selling_price == 48
    assert item.order_multiple == channel.order_multiple == 2


def test_a_number_column_holding_text_is_refused(db):
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply(db, [{"sku": "50010302", "buy.cost": "one hundred"}])
    assert result["summary"]["errors"] == 1
    assert "not a number" in result["rows"][0]["errors"][0]


def test_a_fractional_pack_size_is_refused(db):
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic", "buy.per_pack": 2.5}])
    assert result["summary"]["errors"] == 1
    assert "whole number" in result["rows"][0]["errors"][0]


def test_export_reimports_unchanged(db, tmp_path):
    """The round trip. Every column the sheet writes, read back as no change.

    A column the exporter forgets, or a unit the importer reads differently,
    shows up here as an update nobody asked for.
    """
    supplier = _supplier(db)
    product, link = _product(db, supplier)
    product.brand = "Alfa"; product.subcategory = "analgesic"; product.species = "cat"
    product.segment = "vet"; product.notes = "fridge"; product.weight_g = 1500.0
    product.weight_unit = "kg"
    link.supplier_sku = "AL-1"; link.barcode = "40123456"; link.rrp = 18.0
    link.units_per_pack = 60
    link.minimum_order_qty = 6; link.minimum_order_uom = "Box(es)"
    link.order_increment_qty = 12; link.order_increment_uom = "Box(es)"
    link.pricing_note = "list price"
    db.commit()
    offering_costs.set_offering_packaging(db, link, purchase_uom="Box(es)", sellable_units=60)
    offering_costs.record_catalogue_price(db, link, amount=130, per="pack", pack_uom="Box(es)")
    # Every column group, or the round trip only proves the groups it touches.
    from services import product_domain
    inventory = product_domain.ensure_inventory_item(db, product)
    for channel, uom, count, multiple, price in (
        ("clinic", "mL", 10, 2, 48.0), ("shopify", "Unit(s)", 1, None, 62.0)):
        db.add(models.SellingItem(
            selling_item_key=f"variant:{product.sku_code}:channel:{channel}",
            product_variant_id=product.id, inventory_item_id=inventory.id,
            channel=channel, sell_uom=uom, sell_uom_count=count, order_multiple=multiple,
            selling_price=price, status="ACTIVE", created_at=NOW, updated_at=NOW))
        db.add(models.ProductChannel(
            product_id=product.id, channel=channel, is_active=1,
            order_multiple=multiple, selling_price=price, updated_at=NOW))
    db.commit()

    path = tmp_path / "round-trip.xlsx"
    upload_workbook.build(str(path), with_data=True)
    rows = workbook_import.read_rows(path.read_bytes(), "round-trip.xlsx", "products")
    assert len(rows) == 1

    result = workbook_import.import_products(db, rows, actor=_Actor())
    assert result["summary"]["unchanged"] == 1, result["rows"][0].get("changes")


# ── deals ───────────────────────────────────────────────────────────────────

def _deal(**over):
    row = {"sku": "50010302", "supplier": "Alfamedic", "kind": "tier",
           "min_qty": 6, "cost": 77, "cost_per": "unit"}
    row.update(over)
    return row


def test_a_deal_is_added(db):
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply_deals(db, [_deal()])
    assert result["summary"]["created"] == 1
    term = db.query(models.MbbTerm).one()
    assert (term.kind, term.min_qty, term.unit_cost, term.cost_basis) == ("tier", 6, 77.0, "unit")


def test_uploading_the_same_deals_twice_adds_nothing(db):
    supplier = _supplier(db)
    _product(db, supplier)
    _apply_deals(db, [_deal()])
    again = _apply_deals(db, [_deal()])
    assert again["summary"]["duplicate"] == 1
    assert again["summary"]["created"] == 0
    assert db.query(models.MbbTerm).count() == 1


def test_a_changed_price_at_a_recorded_rung_is_written_over(db):
    """The sheet is where deals are maintained, so its price is the newer one.

    Refusing meant a value entered wrongly could only be corrected by hand, one
    row at a time, in a second place — which is how seven production rows sat
    at 200%-1200% off with the file that would have fixed them in hand.
    """
    supplier = _supplier(db)
    _product(db, supplier)
    _apply_deals(db, [_deal()])
    result = _apply_deals(db, [_deal(cost=70)])
    assert result["summary"]["updated"] == 1
    assert result["summary"]["created"] == 0
    assert db.query(models.MbbTerm).count() == 1          # revised, not doubled
    assert db.query(models.MbbTerm).one().unit_cost == 70.0
    assert result["rows"][0]["changes"] == {"cost": {"from": 77.0, "to": 70.0}}


def test_a_wrong_discount_is_corrected_by_re_uploading_the_sheet(db):
    """The case this rule exists for.

    A discount written before the percent conversion was fixed sits at 2.0 —
    200% off, and a negative cost. Re-uploading the same sheet, which says 2,
    must put it right rather than report it and leave it.
    """
    supplier = _supplier(db, name="Royal Canin (Non Vet)", code="RCNV")
    product, link = _product(db, supplier)
    db.add(models.MbbTerm(product_supplier_id=link.id, kind="spend_discount",
                          min_spend=1000, discount_pct=2.0, created_at=NOW, sort_order=0))
    db.commit()

    result = _apply_deals(db, [{"sku": "50010302", "supplier": "Royal Canin (Non Vet)",
                                "kind": "spend_discount", "min_spend": 1000,
                                "discount_pct": 2}])
    assert result["summary"]["updated"] == 1
    assert db.query(models.MbbTerm).one().discount_pct == 0.02
    assert result["rows"][0]["changes"] == {"discount_pct": {"from": 2.0, "to": 0.02}}


def test_a_changed_cost_basis_reaches_its_own_column(db):
    """cost_per lives in cost_basis; writing the sheet's name changes nothing."""
    supplier = _supplier(db)
    _product(db, supplier)
    _apply_deals(db, [_deal(cost=77, cost_per="unit")])
    _apply_deals(db, [_deal(cost=77, cost_per="pack")])
    assert db.query(models.MbbTerm).one().cost_basis == "pack"


def test_a_buy_x_get_y_is_identified_by_its_quantity_and_its_free_qty_moves(db):
    """10+2 becoming 10+3 is the same deal on better terms.

    The quantity that unlocks it is what identifies it; what it hands over at
    that quantity is the part a supplier revises. So the new free_qty is
    written over the old one rather than refused.
    """
    supplier = _supplier(db, name="Kangaroo Pet Nutrition", code="KPN")
    _product(db, supplier)
    base = {"sku": "50010302", "supplier": "Kangaroo Pet Nutrition",
            "kind": "buy_x_get_y", "min_qty": 10}
    _apply_deals(db, [{**base, "free_qty": 2, "note": "10+2"}])
    result = _apply_deals(db, [{**base, "free_qty": 3, "note": "10+3"}])

    assert result["summary"]["updated"] == 1
    assert result["summary"]["created"] == 0          # not a rival rung
    assert result["rows"][0]["changes"] == {"free_qty": {"from": 2, "to": 3}}
    term = db.query(models.MbbTerm).one()             # still exactly one
    assert (term.min_qty, term.free_qty, term.note) == (10, 3, "10+3")


def test_a_different_quantity_is_a_different_rung_not_an_override(db):
    """Overriding is scoped to the benefit; the threshold still identifies."""
    supplier = _supplier(db, name="Kangaroo Pet Nutrition", code="KPN")
    _product(db, supplier)
    base = {"sku": "50010302", "supplier": "Kangaroo Pet Nutrition", "kind": "buy_x_get_y"}
    result = _apply_deals(db, [{**base, "min_qty": 10, "free_qty": 2},
                               {**base, "min_qty": 50, "free_qty": 3}])
    assert result["summary"]["created"] == 2
    assert result["summary"]["updated"] == 0
    assert db.query(models.MbbTerm).count() == 2


def test_two_rows_at_one_rung_in_one_file_leave_the_last_one_standing(db):
    """A file that revises the same rung twice keeps the row nearest the bottom.

    This is what your own deals tab does with "10+2 OR 10+3": both rows claim
    min_qty 10. Overriding means the second wins — which is worth knowing,
    because the first row's number is then nowhere.
    """
    supplier = _supplier(db, name="Kangaroo Pet Nutrition", code="KPN")
    _product(db, supplier)
    base = {"sku": "50010302", "supplier": "Kangaroo Pet Nutrition",
            "kind": "buy_x_get_y", "min_qty": 10}
    result = _apply_deals(db, [{**base, "free_qty": 2}, {**base, "free_qty": 3}])
    assert [r["status"] for r in result["rows"]] == ["created", "updated"]
    assert db.query(models.MbbTerm).one().free_qty == 3


def test_a_term_left_out_of_the_file_survives(db):
    """Add-only, explicitly: shortening the sheet does not shorten the ladder."""
    supplier = _supplier(db)
    _product(db, supplier)
    _apply_deals(db, [_deal(min_qty=1, cost=78), _deal(min_qty=6, cost=77),
                      _deal(min_qty=12, cost=76)])
    assert db.query(models.MbbTerm).count() == 3
    result = _apply_deals(db, [_deal(min_qty=1, cost=78)])
    assert result["summary"] == {"total": 1, "created": 0, "updated": 0,
                                 "duplicate": 1, "errors": 0}
    assert db.query(models.MbbTerm).count() == 3


def test_a_kind_without_its_fields_is_refused(db):
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply_deals(db, [{"sku": "50010302", "supplier": "Alfamedic",
                                "kind": "buy_x_get_y"}])
    assert result["summary"]["errors"] == 1
    assert {"a buy_x_get_y needs min_qty", "a buy_x_get_y needs free_qty"} <= set(
        result["rows"][0]["errors"])


def test_a_kind_carrying_a_field_it_ignores_is_refused(db):
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply_deals(db, [{"sku": "50010302", "supplier": "Alfamedic",
                                "kind": "spend_discount", "min_spend": 1200,
                                "discount_pct": 5, "cost": 70, "cost_per": "unit"}])
    assert result["summary"]["errors"] == 1
    assert any("does not use cost" in e for e in result["rows"][0]["errors"])


def test_a_cost_with_no_basis_is_refused(db):
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply_deals(db, [{"sku": "50010302", "supplier": "Alfamedic",
                                "kind": "flat_unit_cost", "cost": 78}])
    assert result["summary"]["errors"] == 1
    assert any("cost_per" in e for e in result["rows"][0]["errors"])


def test_an_unlinked_supplier_says_which_ones_are_linked(db):
    supplier = _supplier(db)
    _supplier(db, name="Royal Canin (Vet)", code="RCV")
    _product(db, supplier)
    result = _apply_deals(db, [_deal(supplier="Royal Canin (Vet)")])
    assert result["summary"]["errors"] == 1
    detail = result["rows"][0]["errors"][0]
    assert "not linked" in detail and "Alfamedic" in detail


def test_unknown_kind_is_refused(db):
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply_deals(db, [_deal(kind="mystery")])
    assert result["summary"]["errors"] == 1
    assert "not one of" in result["rows"][0]["errors"][0]


def test_deals_dry_run_writes_nothing(db):
    supplier = _supplier(db)
    _product(db, supplier)
    preview = _apply_deals(db, [_deal()], dry_run=True)
    assert preview["summary"]["created"] == 1
    assert db.query(models.MbbTerm).count() == 0


def test_a_workbook_uploaded_to_the_wrong_endpoint_is_rejected(db):
    import io

    from openpyxl import Workbook

    book = Workbook()
    book.active.title = "products"
    book.active.append(["sku", "name"])
    buffer = io.BytesIO(); book.save(buffer)
    with pytest.raises(workbook_import.WorkbookFormatError) as exc:
        workbook_import.read_rows(buffer.getvalue(), "book.xlsx", "deals")
    assert "no 'deals' sheet" in str(exc.value)


def test_two_identical_rows_in_one_file(db):
    """The second is a duplicate of the first, not a second deal.

    The relationship this reads was loaded before either row was written, so a
    stale collection would let one file create the same term twice.
    """
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply_deals(db, [_deal(), _deal()])
    assert result["summary"]["created"] == 1
    assert result["summary"]["duplicate"] == 1
    assert db.query(models.MbbTerm).count() == 1


def test_adding_only_a_supplier_link_is_not_unchanged(db):
    """Linking a supplier IS the change, even when no other cell is filled."""
    _supplier(db)
    product, _ = _product(db, supplier=None)
    result = _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic"}])
    assert result["summary"]["updated"] == 1, result["rows"][0]
    assert db.query(models.ProductSupplier).filter_by(product_id=product.id).count() == 1


def test_a_row_that_fails_after_validation_leaves_no_sku_behind(db):
    """A SKU is issued once, so an abandoned row must not consume one.

    The supplier is resolved before the product is minted for exactly this
    reason — a refused row that had already taken a number would leave a
    permanent gap in the sequence and a product nobody can buy.
    """
    from services import sku_service

    _supplier(db)
    before = sku_service.next_sku("Medicine", db)
    result = _apply(db, [{
        "sku": "", "name": "Doomed", "category": "Medicine", "unit": "Unit(s)",
        "buy.supplier": "Nobody Ltd", "buy.sku": "N-1", "buy.pack": "Unit(s)",
        "buy.per_pack": 1, "buy.cost": 5, "buy.cost_per": "unit",
    }])
    assert result["summary"]["errors"] == 1
    assert db.query(models.ProductVariant).count() == 0
    assert sku_service.next_sku("Medicine", db) == before     # the number is untaken


# ── what a real file actually contains ──────────────────────────────────────
# These come from smoke-testing an exported deals tab. Every one of them is a
# thing the file on someone's desktop does that a hand-written test row does
# not: a BOM from Sheets, columns in their own order, a read-only column, a
# note in two scripts, a cost someone typed with a dollar sign.

def test_a_note_in_any_script_survives(db):
    """Supplier notes are copied out of their own paperwork.

    A real one reads "10+2 OR 10+3 (單次購買 50或以上)" and another is a bulleted
    list with an en-dash arrow. Storing them mangled loses the only record of
    what the supplier actually offered.
    """
    supplier = _supplier(db, name="Kangaroo Pet Nutrition", code="KPN")
    _product(db, supplier)
    note = "10+2 OR 10+3 (單次購買 50或以上) (50 or more per order) • $2200 → 4%"
    _apply_deals(db, [{"sku": "50010302", "supplier": "Kangaroo Pet Nutrition",
                       "kind": "buy_x_get_y", "min_qty": 10, "free_qty": 2, "note": note}])
    assert db.query(models.MbbTerm).one().note == note


def test_the_read_only_column_is_ignored_not_written(db):
    """ref.name rides along in every export and is the product's own name."""
    supplier = _supplier(db)
    product, _ = _product(db, supplier)
    _apply_deals(db, [_deal(**{"ref.name": "SOMETHING ELSE ENTIRELY"})])
    db.refresh(product)
    assert product.name == "Metacoxx 1ML"
    assert db.query(models.MbbTerm).count() == 1


def test_columns_may_arrive_in_any_order_and_bring_strangers(db):
    """Read by header, never by position — people reorder and annotate sheets."""
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply_deals(db, [{
        "note": "reordered", "cost_per": "unit", "kind": "tier", "cost": 77,
        "supplier": "Alfamedic", "min_qty": 6, "sku": "50010302",
        "who added this": "someone", "": "",
    }])
    assert result["summary"]["created"] == 1
    assert db.query(models.MbbTerm).one().min_qty == 6


def test_a_cost_typed_with_a_currency_symbol_still_reads(db):
    """"$1,200" is what a person types. Refusing it teaches nothing useful."""
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply_deals(db, [{"sku": "50010302", "supplier": "Alfamedic",
                                "kind": "spend_discount", "min_spend": "$1,200",
                                "discount_pct": "2"}])
    assert result["summary"]["created"] == 1, result["rows"][0]
    assert db.query(models.MbbTerm).one().min_spend == 1200.0


def test_spreadsheet_debris_is_not_a_value(db):
    """`#N/A` is already recorded as a sell unit on 1,187 products.

    It got there because something wrote down what a spreadsheet displayed.
    Read as "nothing", it leaves the recorded value alone instead.
    """
    supplier = _supplier(db)
    product, link = _product(db, supplier)
    link.supplier_sku = "AL-1"
    product.subcategory = "analgesic"
    db.commit()
    _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic",
                 "buy.sku": "#N/A", "subcategory": "N/A"}])
    db.refresh(link); db.refresh(product)
    assert link.supplier_sku == "AL-1"
    assert product.subcategory == "analgesic"


def test_a_blank_row_is_spacing_and_does_not_shift_the_line_numbers(db):
    """Dropping a row must not renumber the ones after it.

    A report that says line 9 sends someone to row 9 of the sheet they are
    looking at. Counting kept rows instead would point at the wrong one for
    every row below a gap — and a wrong line number is worse than none.
    """
    supplier = _supplier(db)
    _product(db, supplier)
    csv_bytes = (
        "sku,supplier,kind,min_qty,cost,cost_per\n"
        "50010302,Alfamedic,tier,1,78,unit\n"      # row 2
        "\n"                                        # row 3 — spacing
        ",,,,,\n"                                   # row 4 — spacing
        "50010302,Alfamedic,tier,6,77,unit\n"      # row 5
    ).encode()
    rows = workbook_import.read_rows(csv_bytes, "deals.csv", "deals")
    assert len(rows) == 2                             # the gaps are not rows
    result = _apply_deals(db, rows)
    assert result["summary"]["created"] == 2
    assert [r["line"] for r in result["rows"]] == [2, 5]


def test_a_csv_exported_by_sheets_reads_despite_its_bom(db):
    supplier = _supplier(db)
    _product(db, supplier)
    csv_bytes = ("﻿" + "sku,supplier,kind,min_qty,cost,cost_per\n"
                 "50010302,Alfamedic,tier,6,77,unit\n").encode("utf-8")
    rows = workbook_import.read_rows(csv_bytes, "deals.csv", "deals")
    assert list(rows[0])[0] == "sku"            # not "﻿sku"
    assert _apply_deals(db, rows)["summary"]["created"] == 1


def test_an_order_gate_makes_a_second_rung_at_the_same_buy_quantity(db):
    """Kangaroo's real deal: 10+2, or 10+3 once the order reaches 50.

    Both rungs are "buy 10", so min_qty cannot tell them apart — without the
    order gate in the identity the second row overwrites the first and the
    plain 10+2 disappears. With it they are two deals, which is what they are.
    """
    supplier = _supplier(db, name="Kangaroo Pet Nutrition", code="KPN")
    _product(db, supplier)
    base = {"sku": "50010302", "supplier": "Kangaroo Pet Nutrition",
            "kind": "buy_x_get_y", "min_qty": 10}
    result = _apply_deals(db, [
        {**base, "free_qty": 2, "note": "10+2"},
        {**base, "free_qty": 3, "min_order_qty": 50, "note": "10+3 on orders of 50+"},
    ])
    assert result["summary"]["created"] == 2
    assert result["summary"]["updated"] == 0            # not a revision of each other
    terms = sorted(db.query(models.MbbTerm).all(), key=lambda t: t.min_order_qty or 0)
    assert [(t.min_qty, t.free_qty, t.min_order_qty) for t in terms] == [(10, 2, None), (10, 3, 50)]


def test_the_order_gate_is_part_of_what_a_re_upload_matches(db):
    """Re-uploading the gated rung finds the gated rung, not the plain one."""
    supplier = _supplier(db, name="Kangaroo Pet Nutrition", code="KPN")
    _product(db, supplier)
    base = {"sku": "50010302", "supplier": "Kangaroo Pet Nutrition",
            "kind": "buy_x_get_y", "min_qty": 10}
    _apply_deals(db, [{**base, "free_qty": 2},
                      {**base, "free_qty": 3, "min_order_qty": 50}])
    again = _apply_deals(db, [{**base, "free_qty": 2},
                              {**base, "free_qty": 4, "min_order_qty": 50}])
    assert again["summary"]["duplicate"] == 1           # the plain rung, untouched
    assert again["summary"]["updated"] == 1             # the gated rung, revised
    gated = db.query(models.MbbTerm).filter(models.MbbTerm.min_order_qty == 50).one()
    plain = db.query(models.MbbTerm).filter(models.MbbTerm.min_order_qty.is_(None)).one()
    assert (gated.free_qty, plain.free_qty) == (4, 2)


def test_any_kind_may_carry_an_order_gate(db):
    """A price or a percentage can be conditional on order size too."""
    supplier = _supplier(db)
    _product(db, supplier)
    result = _apply_deals(db, [
        {"sku": "50010302", "supplier": "Alfamedic", "kind": "tier",
         "min_qty": 6, "cost": 77, "cost_per": "unit", "min_order_qty": 50},
        {"sku": "50010302", "supplier": "Alfamedic", "kind": "spend_discount",
         "min_spend": 1200, "discount_pct": 5, "min_order_qty": 20},
    ])
    assert result["summary"]["created"] == 2, result["rows"]
    assert sorted(t.min_order_qty for t in db.query(models.MbbTerm).all()) == [20, 50]


def test_the_audit_trail_names_the_rung_it_changed(db):
    """"free_qty 2 -> 3" names no row when a SKU holds several rungs.

    50007255 holds a plain 10+2 and a 10+3 gated at 50. A trail that records
    only the field and its old value cannot be read back to the deal it
    changed, which is most of what a trail is for.
    """
    import json

    supplier = _supplier(db, name="Kangaroo Pet Nutrition", code="KPN")
    _product(db, supplier)
    base = {"sku": "50010302", "supplier": "Kangaroo Pet Nutrition",
            "kind": "buy_x_get_y", "min_qty": 10}
    _apply_deals(db, [{**base, "free_qty": 2},
                      {**base, "free_qty": 3, "min_order_qty": 50}])
    _apply_deals(db, [{**base, "free_qty": 4, "min_order_qty": 50}])

    entry = (db.query(models.AuditLog)
               .filter_by(action="product.mbb_term_update")
               .order_by(models.AuditLog.id.desc()).first())
    details = json.loads(entry.details)
    gated = db.query(models.MbbTerm).filter(models.MbbTerm.min_order_qty == 50).one()
    assert details["term_id"] == gated.id                  # which row
    assert details["term"]["min_order_qty"] == 50          # and which rung it was
    assert details["changes"] == {"free_qty": {"from": 3, "to": 4}}
    assert details["via"] == "workbook"
    assert entry.entity_label == "50010302"


def test_a_created_deal_records_the_row_it_became(db):
    import json

    supplier = _supplier(db)
    _product(db, supplier)
    _apply_deals(db, [_deal()])
    entry = (db.query(models.AuditLog)
               .filter_by(action="product.mbb_term_add")
               .order_by(models.AuditLog.id.desc()).first())
    details = json.loads(entry.details)
    assert details["term_id"] == db.query(models.MbbTerm).one().id
    assert details["supplier"] == "Alfamedic"


# ── units ───────────────────────────────────────────────────────────────────
# The round trip cannot catch a units bug. Export emitted the stored fraction
# where the column promised a percent, and the importer wrote the sheet's
# percent straight into the fraction column — wrong in opposite directions, so
# a file exported and re-imported came back unchanged and both halves were
# wrong. These pin each half against the DATABASE's convention instead of
# against the other half.

def test_a_percent_on_the_sheet_is_stored_as_a_fraction(db):
    """The column holds 0-1. `config.tsx` says so, pricing depends on it.

    `pricing_service` computes cost * (1 - discount_pct), so 2 written straight
    in makes a $8.75 item cost -$8.75 and every screen render it as 200% off.
    """
    supplier = _supplier(db, name="Royal Canin (Non Vet)", code="RCNV")
    _product(db, supplier)
    _apply_deals(db, [{"sku": "50010302", "supplier": "Royal Canin (Non Vet)",
                       "kind": "spend_discount", "min_spend": 1000, "discount_pct": 2}])
    assert db.query(models.MbbTerm).one().discount_pct == 0.02


def test_a_stored_fraction_leaves_as_the_percent_the_column_asks_for(db, tmp_path):
    """The header says "a number, 12.5 not 0.125", so the cell must hold 12.5."""
    from openpyxl import load_workbook

    supplier = _supplier(db, name="Royal Canin (Non Vet)", code="RCNV")
    product, link = _product(db, supplier)
    db.add(models.MbbTerm(product_supplier_id=link.id, kind="spend_discount",
                          min_spend=1000, discount_pct=0.125, created_at=NOW, sort_order=0))
    db.commit()

    path = tmp_path / "pct.xlsx"
    upload_workbook.build(str(path), with_data=True)
    ws = load_workbook(path)["deals"]
    headers = [c.value for c in ws[1]]
    row = dict(zip(headers, [c.value for c in ws[2]]))
    assert row["discount_pct"] == 12.5


def test_a_percent_survives_the_whole_round_trip_at_the_right_size(db, tmp_path):
    """Export it, read it back, and it must still be the same DEAL.

    Both halves now convert, so this passes for the right reason — and would
    fail if either half stopped converting, which the old round trip could not
    detect.
    """
    supplier = _supplier(db, name="Royal Canin (Non Vet)", code="RCNV")
    product, link = _product(db, supplier)
    db.add(models.MbbTerm(product_supplier_id=link.id, kind="spend_discount",
                          min_spend=1000, discount_pct=0.07, created_at=NOW, sort_order=0))
    db.commit()

    path = tmp_path / "trip.xlsx"
    upload_workbook.build(str(path), with_data=True)
    rows = workbook_import.read_rows(path.read_bytes(), "trip.xlsx", "deals")
    assert rows[0]["discount_pct"] == 7          # the sheet shows a percent

    result = workbook_import.import_deals(db, rows, actor=_Actor())
    assert result["summary"]["duplicate"] == 1   # recognised as the same deal
    assert db.query(models.MbbTerm).one().discount_pct == 0.07


# ── the three collisions a real 112-row upload hit ──────────────────────────
# Every one of these was an IntegrityError escaping as a 500, and each was
# hidden behind the one before it. They are all about the offering table's
# uniqueness rules meeting data that predates them.

def test_a_pack_cost_survives_packaging_with_no_purchase_code(db):
    """233 of 325 live packaging rows carry a NULL purchase_uom_code.

    The price row's basis is NOT NULL, so reading that column without a
    fallback put a NULL in it and failed the whole upload on the first row.
    """
    supplier = _supplier(db)
    product, link = _product(db, supplier)
    offering_costs.set_offering_packaging(db, link, purchase_uom=None, sellable_units=12)
    db.commit()
    packaging = db.query(models.CataloguePackagingConfiguration).one()
    packaging.purchase_uom_code = None          # the shape 233 rows are in
    db.commit()

    result = _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic",
                          "buy.pack": "Box(es)", "buy.per_pack": 12,
                          "buy.cost": 130, "buy.cost_per": "pack"}])
    assert result["summary"]["errors"] == 0, result["rows"][0]
    price = db.query(models.CatalogueSupplierPrice).filter_by(is_current=1).one()
    assert price.price_basis_uom_code                      # never NULL
    assert float(price.amount) == 130.0


def test_one_supplier_code_may_span_two_of_our_skus(db):
    """Their code is unique per supplier in our table and not unique in life.

    A supplier sells one thing we split into two SKUs, so the second offering
    cannot claim the code — it goes without one rather than failing the upload.
    """
    supplier = _supplier(db)
    first, first_link = _product(db, supplier, sku="10008143")
    first_link.supplier_sku = "P12558819"
    db.commit()
    offering_costs.record_catalogue_price(db, first_link, amount=10, per="unit")
    db.commit()

    second, _ = _product(db, supplier, sku="10010514", name="Same code, other SKU")
    result = _apply(db, [{"sku": "10010514", "buy.supplier": "Alfamedic",
                          "buy.sku": "P12558819", "buy.cost": 20, "buy.cost_per": "unit"}])
    assert result["summary"]["errors"] == 0, result["rows"][0]
    offerings = db.query(models.SupplierOffering).all()
    assert len(offerings) == 2
    assert sorted(o.supplier_sku or "" for o in offerings) == ["", "P12558819"]
    # the code itself is not lost — it still lives on the supplier link
    link = db.query(models.ProductSupplier).filter_by(product_id=second.id).one()
    assert link.supplier_sku == "P12558819"


def test_an_offering_whose_legacy_link_was_repointed_does_not_block_a_new_one(db):
    """Six offerings name a link that has since moved to another product.

    The back-reference is a convenience, not the identity, so a taken id is
    left unset rather than failing the row that needs a new offering.
    """
    supplier = _supplier(db)
    first, first_link = _product(db, supplier, sku="10008143")
    offering_costs.record_catalogue_price(db, first_link, amount=10, per="unit")
    db.commit()
    stale = db.query(models.SupplierOffering).one()

    second, second_link = _product(db, supplier, sku="10010514", name="Other")
    stale.legacy_product_supplier_id = second_link.id     # repointed, as six really are
    db.commit()

    result = _apply(db, [{"sku": "10010514", "buy.supplier": "Alfamedic",
                          "buy.cost": 20, "buy.cost_per": "unit"}])
    assert result["summary"]["errors"] == 0, result["rows"][0]
    made = (db.query(models.SupplierOffering)
              .filter_by(product_variant_id=second.id).one())
    assert made.legacy_product_supplier_id is None
    assert made.supplier_product_key != stale.supplier_product_key


# ── brand belongs to the supplier ───────────────────────────────────────────

def test_two_suppliers_may_call_one_product_different_things(db):
    """Kangaroo sell 10008140 as "Purina Pro Plan - Vet"; K.P.N. Trading sell
    the same product as "Purina Pro Plan". With one brand per product the
    second row silently discarded the first.
    """
    _supplier(db, name="Kangaroo Pet Nutrition", code="KPN")
    _supplier(db, name="K.P.N. Trading", code="KPNT")
    product, _ = _product(db, supplier=None, sku="10008140", name="Pro Plan EN")
    product.brand = "Purina Pro Plan"
    db.commit()

    result = _apply(db, [
        {"sku": "10008140", "buy.supplier": "Kangaroo Pet Nutrition",
         "buy.brand": "Purina Pro Plan - Vet"},
        {"sku": "10008140", "buy.supplier": "K.P.N. Trading",
         "buy.brand": "Purina Pro Plan"},
    ])
    assert result["summary"]["errors"] == 0, result["rows"]
    by_supplier = {l.supplier.name: l for l in db.query(models.ProductSupplier).all()}
    assert by_supplier["Kangaroo Pet Nutrition"].brand == "Purina Pro Plan - Vet"
    # agrees with the product's brand, so it holds none of its own
    assert by_supplier["K.P.N. Trading"].brand is None
    db.refresh(product)
    assert product.brand == "Purina Pro Plan"      # the default is untouched


def test_a_supplier_that_agrees_records_no_brand_of_its_own(db):
    """NULL means "the product's brand", so renaming the default still reaches
    every supplier that never disagreed — which a copy on each link would lose.
    """
    _supplier(db)
    product, _ = _product(db, supplier=None)
    product.brand = "Alfa"
    db.commit()
    _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic", "buy.brand": "Alfa"}])
    assert db.query(models.ProductSupplier).one().brand is None


def test_the_first_brand_seen_becomes_the_products_default(db):
    """A product with no brand yet has nothing to differ from."""
    _supplier(db)
    product, _ = _product(db, supplier=None)
    assert not product.brand
    _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic", "buy.brand": "Alfa"}])
    db.refresh(product)
    assert product.brand == "Alfa"
    assert db.query(models.ProductSupplier).one().brand is None


def test_the_sheet_shows_each_supplier_its_own_brand(db, tmp_path):
    """Export must hand back what the row said, or the round trip loses it."""
    from openpyxl import load_workbook

    supplier = _supplier(db)
    product, link = _product(db, supplier)
    product.brand = "Alfa"
    link.brand = "Alfa Vet"
    db.commit()

    path = tmp_path / "brand.xlsx"
    upload_workbook.build(str(path), with_data=True)
    ws = load_workbook(path)["products"]
    headers = [c.value for c in ws[1]]
    row = dict(zip(headers, [c.value for c in ws[2]]))
    assert row["buy.brand"] == "Alfa Vet"

    # and reading it back is no change at all
    rows = workbook_import.read_rows(path.read_bytes(), "brand.xlsx", "products")
    assert workbook_import.import_products(db, rows, actor=_Actor())["summary"]["unchanged"] == 1


def test_a_repacked_cost_is_stamped_with_the_NEW_purchase_unit(db):
    """Packaging is written, then the price is stamped with its purchase code.

    The session runs autoflush=False, so the packaging row added a moment
    earlier was invisible to the query that reads it back — and the price was
    stamped with the SUPERSEDED unit. A basis that lines up with neither the
    purchase unit nor the sellable unit cannot be divided, so the per-unit cost
    comes back None and every margin on the product goes blank. A $130 bottle
    of 60 stamped BOX is exactly that.
    """
    supplier = _supplier(db)
    product, link = _product(db, supplier)
    # what the product already holds: a different purchase unit
    offering_costs.set_offering_packaging(db, link, purchase_uom="Box(es)", sellable_units=10)
    offering_costs.record_catalogue_price(db, link, amount=50, per="pack", pack_uom="Box(es)")
    db.commit()

    # the sheet repacks it and prices the new pack in one row
    result = _apply(db, [{"sku": "50010302", "buy.supplier": "Alfamedic",
                          "buy.pack": "Bottle(s)", "buy.per_pack": 60,
                          "buy.cost": 130, "buy.cost_per": "pack"}])
    assert result["summary"]["errors"] == 0, result["rows"][0]

    offering_costs.invalidate(db)
    price = db.query(models.CatalogueSupplierPrice).filter_by(is_current=1).one()
    packaging = (db.query(models.CataloguePackagingConfiguration)
                   .filter_by(superseded_at=None).one())
    assert packaging.purchase_uom_code == "BOTTLE(S)"
    assert price.price_basis_uom_code == "BOTTLE(S)"      # not the superseded BOX
    # and the cost it implies is derivable, which is the whole point
    assert round(offering_costs.unit_cost_for_link(link), 4) == round(130 / 60, 4)

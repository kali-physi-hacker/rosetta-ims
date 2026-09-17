"""Reading the upload workbook back into the catalogue.

``upload_workbook`` writes the sheet; this reads it back. The two are inverses,
and the way inverses drift is by each keeping its own copy of the rules — so
nothing here is restated. Which columns exist, what a new product owes, what
each deal kind needs, which columns must hold numbers: all imported from the
writer, so a sheet can never tell someone to type what the importer refuses.

Two uploads, never one. Products and deals move independently because they are
different decisions — a product row describes something we sell, a deal row is
a commercial term, and the second is not a consequence of the first.

The three conventions the sheet already documents, honoured here:

    blank sku     mints a new product                 (products sheet only)
    blank cell    leaves whatever is recorded alone
    a dash  -     clears the value

**Deals are never deleted here.** A ladder someone shortens in a spreadsheet
must not quietly shorten in the system: a rung left out of a file stays as it
is, and retiring one is done in Rosetta.

Everything else the sheet says about a rung it already holds is written over.
A rung is identified by its thresholds; what it GIVES at those thresholds is
the part a supplier revises, so 10+2 becoming 10+3, or 2% becoming 3%, updates
the deal rather than sitting beside it. Re-uploading an unchanged file still
changes nothing.
"""

from __future__ import annotations

import csv
import io
from typing import Any

import models
from services import audit_log, offering_costs
from services import uom_vocabulary
from services.uom_vocabulary import canonical_for, weight_to_grams
from services.upload_workbook import (
    CHANNELS,
    CREATION_NEEDS,
    DEAL_COLUMNS,
    DEAL_KINDS,
    KIND_NEEDS,
    NUMERIC_COLUMNS,
    PRODUCT_COLUMNS,
    REQUIRED,
    _IGNORED_BY_KIND,
)

#: What the sheet means by a dash. Distinct from a blank, which means "leave
#: this alone" — the difference between the two is the only way a spreadsheet
#: can say "remove the barcode" rather than "I have nothing to say about it".
CLEAR = object()

#: Columns the database stores as whole numbers. A pack of 2.5 cans is a typo,
#: and rounding it silently is how a typo becomes a cost.
_INT_COLUMNS = {
    "buy.per_pack", "buy.min_qty", "buy.multiple", "min_qty", "min_order_qty", "free_qty",
} | {f"sell.{c}.multiple" for c in CHANNELS}

#: Of those, the ones where zero or less is not a smaller quantity but a
#: meaningless one. Deal thresholds are left out: a spend-based rung can
#: legitimately ask for no minimum quantity at all.
_POSITIVE_COLUMNS = {"buy.per_pack", "buy.multiple"} | {f"sell.{c}.multiple" for c in CHANNELS}

#: Read-only on the way back in. ref.name exists so a deal can be read without
#: looking the SKU up; it is the product's name, and the products sheet is
#: where a product's name is edited.
_IGNORED_ON_IMPORT = {"ref.name"}

#: Where the row was, in the file someone is looking at. Blank rows are dropped
#: on the way in, so counting the rows we kept would report line 9 for what the
#: spreadsheet shows on row 11 — and a line number that sends someone to the
#: wrong row is worse than none.
_LINE = "__line__"

#: Spreadsheet debris. These arrive from exports and re-imports of exports and
#: mean "nothing", not a value — `#N/A` in particular is already recorded as a
#: sell unit on 1,187 products because something once wrote it down.
#: The shared set, less the one character that means something here: a bare
#: "-" is how the sheet says CLEAR THIS FIELD, so treating it as debris would
#: silently turn every deliberate erasure into a no-op.
_PLACEHOLDERS = uom_vocabulary.PLACEHOLDERS - {"-"}

_SHEET_COLUMNS = {"products": PRODUCT_COLUMNS, "deals": DEAL_COLUMNS}


class WorkbookFormatError(ValueError):
    """The upload is not the sheet it was uploaded as."""


# ── reading the file ────────────────────────────────────────────────────────

def read_rows(content: bytes, filename: str, sheet: str) -> list[dict[str, Any]]:
    """The named sheet's rows, as {header: raw cell}.

    Takes the .xlsx itself or a CSV of one sheet. Both are real workflows: the
    workbook opens in Excel and in Google Sheets, and Sheets exports one tab at
    a time, which is exactly why the readme tells people to download twice.
    Uploading the same .xlsx to both endpoints reaches the same place.
    """
    if sheet not in _SHEET_COLUMNS:
        raise WorkbookFormatError(f"unknown sheet '{sheet}'")
    name = (filename or "").lower()
    rows = _read_xlsx(content, sheet) if name.endswith((".xlsx", ".xlsm")) else _read_csv(content)
    headers = {h for h, *_ in _SHEET_COLUMNS[sheet]}
    if rows and not (set(rows[0]) & headers):
        raise WorkbookFormatError(
            f"this file has no {sheet} columns. Expected some of: "
            f"{', '.join(sorted(headers)[:6])}…"
        )
    return rows


def _read_xlsx(content: bytes, sheet: str) -> list[dict[str, Any]]:
    from openpyxl import load_workbook

    # data_only: a formula's last cached result is what someone typed and saw.
    # Without it every computed cell arrives as "=B2*3" and reads as garbage.
    #
    # An .xlsx is a zip, and everything that can be wrong with one surfaces
    # here: a truncated upload, a file saved as .xlsx that is really a .xls or
    # a CSV, a password-protected book. Unhandled, each of those is a 500 and a
    # stack trace — which tells whoever picked the wrong file nothing about
    # which file to pick instead.
    try:
        book = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except WorkbookFormatError:
        raise
    except Exception as exc:                                   # noqa: BLE001
        raise WorkbookFormatError(
            "this file could not be opened as a workbook — check it is the .xlsx "
            "that Export produced, saved rather than renamed, and not password "
            f"protected ({type(exc).__name__})"
        ) from exc
    if sheet not in book.sheetnames:
        raise WorkbookFormatError(
            f"this workbook has no '{sheet}' sheet (found: {', '.join(book.sheetnames)})"
        )
    ws = book[sheet]
    rows = ws.iter_rows(values_only=True)
    header = [str(h).strip() if h is not None else "" for h in next(rows, ())]
    out = []
    for line, values in enumerate(rows, start=2):
        if not any(v is not None and str(v).strip() for v in values):
            continue      # a wholly blank row is spacing, not an instruction
        row = dict(zip(header, values))
        row[_LINE] = line
        out.append(row)
    return out


def _read_csv(content: bytes) -> list[dict[str, Any]]:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    out = []
    for raw in reader:
        if not any((v or "").strip() for v in raw.values() if isinstance(v, str)):
            continue
        row = {(k or "").strip(): v for k, v in raw.items()}
        row[_LINE] = reader.line_num
        out.append(row)
    return out


# ── reading one cell ────────────────────────────────────────────────────────

def _parse(raw: dict[str, Any], sheet: str) -> tuple[dict[str, Any], list[str]]:
    """One sheet row as {header: value | CLEAR}, plus what could not be read.

    Absent keys are absent on purpose: a column the file does not carry and a
    cell nobody filled in are the same instruction — leave it alone.
    """
    data: dict[str, Any] = {}
    errors: list[str] = []
    for header, *_rest in _SHEET_COLUMNS[sheet]:
        if header in _IGNORED_ON_IMPORT or header not in raw:
            continue
        value = raw[header]
        if value is None:
            continue
        if isinstance(value, str):
            value = value.strip()
            if value == "-":
                data[header] = CLEAR
                continue
            if value.lower() in _PLACEHOLDERS:
                continue
        elif value == "":
            continue
        if header in NUMERIC_COLUMNS:
            try:
                number = float(str(value).replace(",", "").replace("$", "").strip())
            except (TypeError, ValueError):
                errors.append(f"{header}: {value!r} is not a number")
                continue
            if header in _INT_COLUMNS:
                if number != int(number):
                    errors.append(f"{header}: {value!r} must be a whole number")
                    continue
                number = int(number)
                if header in _POSITIVE_COLUMNS and number <= 0:
                    # A pack of zero passes the whole-number check and then
                    # fails the `> 1` guard that divides a pack price, so the
                    # cost of a case is published as the cost of one unit.
                    errors.append(f"{header}: {value!r} must be more than zero")
                    continue
            data[header] = number
        else:
            data[header] = str(value).strip()
    return data, errors


def _value(data: dict, header: str, current=None):
    """What `header` says this field should become, given what it is now."""
    if header not in data:
        return current
    return None if data[header] is CLEAR else data[header]


# ── products ────────────────────────────────────────────────────────────────

def _snapshot(product, link, listings: dict) -> dict:
    """Everything on this row the sheet is able to change, for the diff.

    Cost is read through the same offering-first resolver the sheet exports
    from, so "what changed" is measured against what the sheet SHOWED, not
    against a column it never displayed.
    """
    price = offering_costs.catalogue_price_for_link(link) if link is not None else None
    out = {
        "name": product.name, "brand": product.brand, "category": product.category,
        "subcategory": product.subcategory, "species": product.species,
        "segment": product.segment, "uom": product.uom, "status": product.status,
        "storage_rule": product.storage_rule, "weight_g": product.weight_g,
        "weight_unit": product.weight_unit, "notes": product.notes,
    }
    if link is not None:
        out.update({
            "supplier_id": link.supplier_id, "supplier_sku": link.supplier_sku,
            "supplier_brand": link.brand,
            "barcode": link.barcode, "units_per_pack": link.units_per_pack,
            "cost": price.amount if price else None,
            "cost_per": price.per if price else None,
            "pack_uom": price.pack_uom if price else None,
            "rrp": link.rrp,
            "minimum_order_qty": link.minimum_order_qty,
            "minimum_order_uom": link.minimum_order_uom,
            "order_increment_qty": link.order_increment_qty,
            "order_increment_uom": link.order_increment_uom,
            "pricing_note": link.pricing_note,
        })
    for channel, item in listings.items():
        out.update({
            f"sell.{channel}.uom": item.sell_uom,
            f"sell.{channel}.per_unit": (float(item.sell_uom_count)
                                         if item.sell_uom_count is not None else None),
            f"sell.{channel}.multiple": item.order_multiple,
            f"sell.{channel}.price": item.selling_price,
        })
    return out


#: Fields stored on the PRODUCT, not on the supplier link. The products sheet
#: is one row per product PER SUPPLIER, so a SKU bought from two suppliers is
#: two rows — and both of them carry these. If they disagree, the lower row
#: wins and the other is discarded without a word, which is the quietest way
#: this file can lose something.
_PRODUCT_LEVEL = {
    "name", "category", "subcategory", "species", "segment", "unit",
    "status", "storage", "weight", "weight_unit", "notes",
}

#: Nothing sits here now. buy.brand used to: it reads as a supplier's label,
#: landed on `products.brand`, and the lower row of two suppliers won. Brand is
#: recorded on the supplier link instead, so the two no longer collide and the
#: warning that explained the collision has nothing to explain.
_ONE_PER_PRODUCT_BY_SCHEMA: set[str] = set()

#: What a size in a product's own name is worth in grams.
_NAME_UNITS = {"kg": 1000.0, "g": 1.0, "lb": 453.59237, "lbs": 453.59237, "oz": 28.349523}

#: How far a stated weight may sit from the size printed in the name before it
#: is worth a second look. Three-fold is wide on purpose: a bag named for its
#: contents can legitimately weigh more packaged, and a warning nobody trusts
#: is a warning nobody reads.
_WEIGHT_TOLERANCE = 3.0


def _weight_against_name(name: str, grams) -> str | None:
    """A complaint if the stated weight and the name disagree wildly.

    "Early Renal Dry Food for Cats - 1.5KG" recorded as 2.8 g is the case this
    exists for: a stale cell from another row, applied to a real bag, and
    delivery cost is computed from that number.
    """
    import re

    if grams in (None, "") or not name:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(KG|G|LBS?|OZ)\b", name, re.I)
    if not match:
        return None
    printed = float(match.group(1)) * _NAME_UNITS[match.group(2).lower()]
    grams = float(grams)
    if not printed or not grams:
        return None
    if max(printed / grams, grams / printed) < _WEIGHT_TOLERANCE:
        return None
    return (f"weight reads {grams:g}g but the name says {match.group(0)} "
            f"(~{printed:g}g) — delivery cost is computed from this")


def _resolve_supplier(db, name: str):
    """A supplier by the name the sheet offered. Never creates one.

    The sheet's supplier column is a dropdown built from this table, so a name
    that misses here came from a file older than the supplier list, or from
    someone typing past the validation. Both are worth reporting rather than
    guessing at: a supplier invented by an import is a supplier nobody agreed
    to buy from.
    """
    wanted = (name or "").strip().lower()
    if not wanted:
        return None
    for supplier in db.query(models.Supplier).all():
        if (supplier.name or "").strip().lower() == wanted:
            return supplier
    for supplier in db.query(models.Supplier).all():
        if (supplier.normalized_name or "").strip().lower() == wanted:
            return supplier
    return None


def _listings(db, product) -> dict:
    return {
        (item.channel or "").lower(): item
        for item in db.query(models.SellingItem).filter_by(product_variant_id=product.id).all()
        if (item.channel or "").lower() in CHANNELS
    }


def _apply_sell_block(db, product, data: dict, now: str) -> None:
    """The channel columns, written to both rows that hold them.

    ``selling_items`` is what the sheet exports from; ``product_channels`` is
    what every price read and margin still uses, and the two agree on all
    11,919 rows today. Writing one of them would end that on the first upload.
    ``sell_uom`` and ``sell_uom_count`` live only on the selling item — they
    are the channel sale terms, and nothing else records them.
    """
    from services import product_domain

    for channel in CHANNELS:
        keys = [f"sell.{channel}.{f}" for f in ("uom", "per_unit", "multiple", "price")]
        if not any(k in data for k in keys):
            continue
        uom_k, per_k, mult_k, price_k = keys
        item = (db.query(models.SellingItem)
                  .filter_by(product_variant_id=product.id, channel=channel).first())
        if item is None:
            inventory = product_domain.ensure_inventory_item(db, product)
            item = models.SellingItem(
                selling_item_key=f"variant:{product.sku_code}:channel:{channel}",
                product_variant_id=product.id, inventory_item_id=inventory.id,
                channel=channel, status="ACTIVE", created_at=now, updated_at=now,
            )
            db.add(item)
        item.sell_uom = _value(data, uom_k, item.sell_uom)
        item.sell_uom_count = _value(data, per_k, item.sell_uom_count)
        item.order_multiple = _value(data, mult_k, item.order_multiple)
        item.selling_price = _value(data, price_k, item.selling_price)
        item.updated_at = now

        channel_row = (db.query(models.ProductChannel)
                         .filter_by(product_id=product.id, channel=channel).first())
        if channel_row is None:
            channel_row = models.ProductChannel(
                product_id=product.id, channel=channel, is_active=1, updated_at=now)
            db.add(channel_row)
        channel_row.order_multiple = item.order_multiple
        channel_row.selling_price = item.selling_price
        channel_row.updated_at = now


def _apply_buy_block(db, product, link, data: dict, now: str, actor_name: str | None = None) -> None:
    """The supplier's own terms, written where each of them actually lives.

    Packaging and price go to the offering, in that order and never the other
    way round: the price row records WHICH unit its amount is in, by matching
    the packaging's purchase-unit code, so a price written before its packaging
    would be a number in a unit that does not exist yet.
    """
    # Brand belongs to the supplier, and is recorded on the link ONLY when it
    # differs from the product's. NULL means "whatever the product says", so a
    # later rename of the default still carries to every supplier that agreed —
    # which is the behaviour a copy on every link would quietly lose.
    if "buy.brand" in data:
        stated = _value(data, "buy.brand")
        default = (product.brand or "").strip()
        if not default:
            product.brand = stated          # nothing to differ from yet
            link.brand = None
        elif (stated or "") != default:
            link.brand = stated
        else:
            link.brand = None               # agrees with the default; inherit it

    link.supplier_sku = _value(data, "buy.sku", link.supplier_sku)
    link.barcode = _value(data, "buy.barcode", link.barcode)
    link.rrp = _value(data, "buy.rrp", link.rrp)
    link.minimum_order_qty = _value(data, "buy.min_qty", link.minimum_order_qty)
    link.minimum_order_uom = _value(data, "buy.min_qty_uom", link.minimum_order_uom)
    link.order_increment_qty = _value(data, "buy.multiple", link.order_increment_qty)
    link.order_increment_uom = _value(data, "buy.multiple_uom", link.order_increment_uom)
    link.pricing_note = _value(data, "buy.note", link.pricing_note)
    if "buy.min_qty" in data and link.minimum_order_qty is not None:
        link.minimum_order_source = "manual"
    if "buy.per_pack" in data:
        link.units_per_pack = _value(data, "buy.per_pack", link.units_per_pack)
        link.pack_source = "manual"
    #: Applying the workbook IS the review: a person chose the file, read the
    #: preview and pressed Apply. Without a mark here the row is recorded and
    #: priced and then never reaches the ops sheet, which publishes only what
    #: someone has agreed to — and nothing on screen would say so.
    link.manual_review_status = "manual_workbook"
    link.manual_reviewed_at = now
    link.manual_reviewed_by = actor_name
    link.updated_at = now

    price = offering_costs.catalogue_price_for_link(link)
    stated_pack = _value(data, "buy.pack", price.pack_uom if price else None)
    # One spelling, chosen on the way in. The export has always canonicalised
    # (upload_workbook writes "Box(es)"), the import never did, so a round trip
    # through a person's keyboard could return "box" or "BOXES" and quietly
    # become a different purchase unit from the one it left as.
    pack_uom = (canonical_for(stated_pack) or stated_pack) if stated_pack else stated_pack
    per_pack = link.units_per_pack
    if "buy.pack" in data or "buy.per_pack" in data:
        offering_costs.set_offering_packaging(
            db, link, purchase_uom=pack_uom, sellable_units=per_pack)

    if "buy.cost" in data:
        amount = _value(data, "buy.cost")
        basis = _value(data, "buy.cost_per", price.per if price else None)
        link.cost_source = "manual"
        link.cost_updated_at = now
        offering_costs.record_catalogue_price(
            db, link, amount=amount, per=basis, pack_uom=pack_uom)


def import_products(db, rows: list[dict], *, actor, dry_run: bool = False,
                    request=None) -> dict:
    """Apply the products sheet. Blank sku creates; a filled one updates.

    Runs in the caller's transaction and rolls it back for a dry run, so the
    preview is produced by doing the work rather than by predicting it —
    including the SKU a new product would be given, which is the one thing a
    prediction cannot honestly show.
    """
    from datetime import datetime

    from services import product_domain

    now = datetime.utcnow().isoformat()
    actor_name = getattr(actor, "display_name", None) or getattr(actor, "username", None)
    summary = {"total": 0, "created": 0, "updated": 0, "unchanged": 0,
               "not_found": 0, "errors": 0, "warnings": 0}
    report: list[dict] = []
    #: (sku, product-level field) -> (line, value) the sheet has already set.
    #: A second row setting the same field differently silently discards one of
    #: them, so it is worth saying which row and to what.
    claimed: dict[tuple, tuple] = {}

    for offset, raw in enumerate(rows, start=2):
        summary["total"] += 1
        entry: dict[str, Any] = {"line": raw.get(_LINE, offset), "sku": None,
                                  "status": "error"}
        data, errors = _parse(raw, "products")
        sku = data.get("sku")
        if sku is CLEAR:
            errors.append("sku: a dash cannot clear a SKU")
            sku = None
        entry["sku"] = sku
        if errors:
            entry["errors"] = errors
            report.append(entry); summary["errors"] += 1; continue

        product = None
        created = False
        new_link = False
        supplier = None
        if sku:
            product = db.query(models.ProductVariant).filter_by(sku_code=sku).first()
            if product is None:
                entry["status"] = "not_found"
                entry["errors"] = [f"no product with sku {sku}"]
                report.append(entry); summary["not_found"] += 1; continue

        # Everything that can refuse the row is checked BEFORE anything is
        # minted. A SKU is issued once: a row that names a supplier we do not
        # have, or leaves out a field creation needs, must not leave a numbered
        # product behind on its way to being reported as an error.
        supplier_name = data.get("buy.supplier")
        if supplier_name is CLEAR:
            errors.append("buy.supplier: a dash cannot unlink a supplier")
        elif supplier_name:
            supplier = _resolve_supplier(db, supplier_name)
            if supplier is None:
                errors.append(f"buy.supplier: no supplier named {supplier_name!r}")
        elif any(h.startswith("buy.") and h != "buy.brand" for h in data):
            # The sheet paints this red for the same reason: supplier terms
            # with no supplier cannot be attached to anything.
            errors.append("buy.supplier is required to record supplier terms")

        # A cost that buys a pack gets divided by the pack count, and a count
        # nobody recorded means the price of a case is published as the price of
        # one unit. Nothing downstream catches it: "Box(es)" is written to the
        # packaging as "BOX(ES)", which is not one of the container words the
        # cost reader refuses on, so the undivided amount passes as a unit cost.
        if data.get("buy.cost") not in (None, CLEAR) and data.get("buy.cost_per") == "pack":
            stated = data.get("buy.per_pack")
            recorded = None
            if product is not None and supplier is not None:
                existing = next((l for l in product.product_suppliers
                                 if l.supplier_id == supplier.id), None)
                recorded = getattr(existing, "units_per_pack", None)
            if (stated if stated not in (None, CLEAR) else recorded) is None:
                errors.append(
                    "buy.cost_per says pack, so buy.per_pack must give the number of "
                    "units in one — without it the pack price becomes the unit price")

        if not sku:
            missing = [h for h in sorted(REQUIRED["products"]) if not data.get(h)]
            missing += [h for h in CREATION_NEEDS if h not in data or data[h] is CLEAR]
            errors += [f"a new product needs {h}" for h in dict.fromkeys(missing)]

        if errors:
            entry["errors"] = errors
            report.append(entry); summary["errors"] += 1; continue

        if not sku:
            try:
                product = product_domain.create_variant_from_draft(
                    db,
                    {"name": data["name"], "category": data["category"],
                     "brand": data.get("buy.brand"), "uom": data.get("unit"),
                     "storage_rule": data.get("storage") or "any"},
                    provenance={"actor": actor_name},
                    # A dry run ends in a rollback, and on SQLite a savepoint
                    # opened after reads alone escapes one — so the one thing a
                    # preview must not do is open one.
                    nested=not dry_run,
                )
            except product_domain.VariantCreationError as exc:
                detail = str(exc)
                if "SKU digit" in detail:
                    detail += " — set one for this category under Categories."
                entry["errors"] = [detail]
                report.append(entry); summary["errors"] += 1; continue
            created = True
            entry["sku"] = sku = product.sku_code

        link = None
        if supplier is not None:
            link = next((l for l in product.product_suppliers
                         if l.supplier_id == supplier.id), None)
            new_link = link is None
        elif not supplier_name:
            link = next((l for l in product.product_suppliers if l.is_primary), None)

        # Snapshot BEFORE the link is created, or linking a supplier is the one
        # change that reports itself as no change: the new row's own empty
        # fields would stand in as what was there before it existed.
        before = _snapshot(product, link, _listings(db, product))
        if new_link:
            link = models.ProductSupplier(
                product_id=product.id, supplier_id=supplier.id,
                is_primary=1 if not product.product_suppliers else 0,
                cost_source="manual", pack_source="manual", updated_at=now)
            db.add(link); db.flush()
        product.name = _value(data, "name", product.name)
        if link is None and "buy.brand" in data:
            product.brand = _value(data, "buy.brand", product.brand)
        product.category = _value(data, "category", product.category)
        product.subcategory = _value(data, "subcategory", product.subcategory)
        product.species = _value(data, "species", product.species)
        product.segment = _value(data, "segment", product.segment)
        product.uom = _value(data, "unit", product.uom)
        product.notes = _value(data, "notes", product.notes)
        if "status" in data and data["status"] is not CLEAR:
            product.status = str(data["status"]).upper()
        if "storage" in data and data["storage"] is not CLEAR:
            product.storage_rule = data["storage"]
        if "weight" in data or "weight_unit" in data:
            unit = _value(data, "weight_unit", product.weight_unit)
            # The sheet carries the figure a supplier printed and the unit it is
            # in; grams is ours. Converting here is the whole reason the sheet
            # was allowed to stop converting.
            stated = _value(data, "weight")
            product.weight_g = (weight_to_grams(stated, unit) if "weight" in data
                                else product.weight_g)
            product.weight_unit = unit
        product.last_manual_edit_at = now
        product.last_manual_edit_by = actor_name
        product.updated_at = now

        if link is not None:
            _apply_buy_block(db, product, link, data, now, actor_name)
        _apply_sell_block(db, product, data, now)
        db.flush()
        offering_costs.invalidate(db)

        after = _snapshot(product, link, _listings(db, product))
        changes = audit_log.diff(before, after)
        entry["changes"] = changes

        # ── what in this row deserves a second look ─────────────────────────
        # A change that fills a blank and a change that replaces a recorded
        # value read identically in a list of "field: a -> b". They are not the
        # same thing: one adds what we did not know, the other overrules what
        # someone already decided.
        warnings: list[dict] = []
        for field, change in changes.items():
            if change["from"] not in (None, ""):
                warnings.append({
                    "field": field, "kind": "overwrite",
                    "message": f"replaces a recorded value ({change['from']!r})",
                })
        for header in _PRODUCT_LEVEL:
            if header not in data or data[header] is CLEAR:
                continue
            key = (sku, header)
            seen = claimed.get(key)
            if seen is not None and seen[1] != data[header]:
                if header in _ONE_PER_PRODUCT_BY_SCHEMA:
                    warnings.append({
                        "field": header, "kind": "one_per_product",
                        "message": (f"line {seen[0]} sets {header} to {seen[1]!r} for a "
                                    f"different supplier. {header} is stored once per "
                                    f"product, not per supplier, so only this row's value "
                                    f"survives — nothing here is wrong with the file"),
                    })
                else:
                    warnings.append({
                        "field": header, "kind": "contradicts",
                        "message": (f"line {seen[0]} of this file sets {header} to "
                                    f"{seen[1]!r} on the same SKU — only the lower row survives"),
                    })
            claimed[key] = (entry["line"], data[header])
        # Only when THIS ROW writes a weight. Sixteen products already hold a
        # weight that disagrees with their own name, and this file neither
        # causes nor changes them — flagging those would put a complaint on
        # rows whose Apply does nothing to the number being complained about.
        # They deserve fixing; they do not deserve to be this upload's noise.
        if "weight" in data or "weight_unit" in data:
            complaint = _weight_against_name(product.name, product.weight_g)
            if complaint:
                warnings.append({"field": "weight", "kind": "implausible",
                                 "message": complaint})
        if warnings:
            entry["warnings"] = warnings
            summary["warnings"] += 1
        if created:
            entry["status"] = "created"
            summary["created"] += 1
            if not dry_run:
                audit_log.record(db, action="product.create", actor=actor,
                                 entity_type="product", entity_id=product.id,
                                 entity_label=sku,
                                 details={"via": "workbook", "fields": after},
                                 request=request)
        elif changes:
            entry["status"] = "updated"
            summary["updated"] += 1
            if not dry_run:
                audit_log.record(db, action="product.update", actor=actor,
                                 entity_type="product", entity_id=product.id,
                                 entity_label=sku,
                                 details={"changes": changes, "via": "workbook"},
                                 request=request)
        else:
            entry["status"] = "unchanged"
            summary["unchanged"] += 1
        report.append(entry)

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {"dry_run": dry_run, "summary": summary, "rows": report}


# ── deals ───────────────────────────────────────────────────────────────────

#: What makes two terms the same DEAL rather than the same ROW. A ladder is
#: identified by its rungs: one kind, at every threshold that gates it.
#:
#: min_order_qty belongs here and not among the benefits, which is the whole
#: reason it exists. Kangaroo's two rungs are both "buy 10" — one plain, one on
#: orders of 50 or more — so identity without the order gate collapses them into
#: one deal, and the second row silently overwrites the first instead of sitting
#: beside it.
_DEAL_IDENTITY = ("kind", "min_qty", "min_spend", "min_order_qty")
_DEAL_TERMS = ("free_qty", "discount_pct", "cost", "cost_per")

#: The deals sheet is how these terms are maintained, so a row that names a
#: rung we already hold is the newer statement of what that rung gives. Every
#: benefit is written over: a supplier moving 10+2 to 10+3, or 2% to 3%, or a
#: tier from 78 to 77, changed the offer rather than opening a rival to it.
#:
#: This was once narrower — free quantities revised, prices refused — on the
#: reasoning that nothing in a row says whether a different price supersedes
#: the recorded one. In practice it does: the sheet is where deals are edited,
#: and refusing meant a value entered wrongly could only be corrected by hand,
#: one row at a time, in a second place.
#:
#: What still cannot happen from a sheet is REMOVAL. A rung left out of a file
#: stays exactly as it is, because a shortened spreadsheet must not silently
#: shorten a ladder.
#:
#: The consequence to know: an out-of-date file re-applied will put its own
#: numbers back over newer ones. The preview lists every change before it is
#: written, which is where that gets caught.
#: Sheet field -> the column it actually lives in.
_TERM_COLUMN = {"cost": "unit_cost", "cost_per": "cost_basis"}


def _round(value):
    return round(value, 6) if isinstance(value, (int, float)) else value


def _term_fields(term) -> dict:
    """A stored term in the sheet's own vocabulary, so the two can be compared.

    ``cost_basis`` is NULL on every row written before the column existed and
    NULL has always meant per unit — the same reading the sheet exports, so a
    file exported yesterday matches the rows it came from instead of looking
    like a change to all of them.
    """
    return {
        "kind": term.kind,
        "min_qty": term.min_qty,
        "min_spend": _round(term.min_spend),
        "min_order_qty": term.min_order_qty,
        "free_qty": term.free_qty,
        "discount_pct": _round(term.discount_pct),
        "cost": _round(term.unit_cost),
        "cost_per": (term.cost_basis or "unit") if term.unit_cost is not None else None,
    }


def _row_fields(data: dict) -> dict:
    """One sheet row in the units the database keeps, ready to compare.

    discount_pct is the one column whose number changes on the way in. The
    sheet asks for 12.5 and the column stores 0.125 — pricing computes
    `cost * (1 - discount_pct)`, so a percent written straight in makes the
    deal cost negative, which is what a whole catalogue of 2 stored as 2.0
    would have meant.
    """
    fields = {h: _round(_value(data, h)) for h in _DEAL_IDENTITY + _DEAL_TERMS}
    if fields["discount_pct"] is not None:
        fields["discount_pct"] = _round(fields["discount_pct"] / 100)
    if fields["cost"] is None:
        fields["cost_per"] = None
    elif fields["cost_per"] is None:
        fields["cost_per"] = "unit"
    return fields


def import_deals(db, rows: list[dict], *, actor, dry_run: bool = False,
                 request=None) -> dict:
    """Apply the deals sheet's terms, and never remove one.

    Every row lands in one of four states. `created` is a rung we did not have.
    `duplicate` is one we hold already, identically — so a file uploaded twice
    changes nothing the second time. `updated` is a rung we hold whose benefit
    the sheet now states differently, written over because the sheet is where
    these terms are maintained. `error` is a row that could not describe a term
    at all.

    Nothing here deletes. A rung left out of a file stays exactly as it is, and
    retiring one is done in Rosetta, in front of the term, where the thing
    being removed is visible.
    """
    from datetime import datetime

    now = datetime.utcnow().isoformat()
    summary = {"total": 0, "created": 0, "updated": 0, "duplicate": 0, "errors": 0}
    report: list[dict] = []

    for offset, raw in enumerate(rows, start=2):
        summary["total"] += 1
        entry: dict[str, Any] = {"line": raw.get(_LINE, offset), "sku": None,
                                  "status": "error"}
        data, errors = _parse(raw, "deals")
        entry["sku"] = data.get("sku") if isinstance(data.get("sku"), str) else None
        entry["supplier"] = data.get("supplier") if isinstance(data.get("supplier"), str) else None
        entry["kind"] = data.get("kind") if isinstance(data.get("kind"), str) else None

        for header in ("sku", "supplier", "kind"):
            if data.get(header) is CLEAR:
                errors.append(f"{header}: a dash cannot clear {header}")
                data.pop(header)
            elif not data.get(header):
                errors.append(f"{header} is required")
        kind = data.get("kind")
        if kind and kind not in DEAL_KINDS:
            errors.append(f"kind: {kind!r} is not one of {', '.join(DEAL_KINDS)}")
            kind = None
        if kind:
            for header in KIND_NEEDS[kind]:
                if data.get(header) in (None, CLEAR):
                    errors.append(f"a {kind} needs {header}")
            for header in sorted(_IGNORED_BY_KIND[kind]):
                if data.get(header) not in (None, CLEAR):
                    errors.append(f"a {kind} does not use {header}")
            if data.get("cost") not in (None, CLEAR) and not data.get("cost_per"):
                # The case-price bug, one column over: an amount whose basis
                # nobody stated is wrong by the pack size and reads as correct.
                errors.append("cost needs cost_per — say whether it buys the pack or one unit")

        link = None
        if not errors:
            product = db.query(models.ProductVariant).filter_by(sku_code=data["sku"]).first()
            if product is None:
                errors.append(f"no product with sku {data['sku']}")
            else:
                supplier = _resolve_supplier(db, data["supplier"])
                if supplier is None:
                    errors.append(f"no supplier named {data['supplier']!r}")
                else:
                    link = next((l for l in product.product_suppliers
                                 if l.supplier_id == supplier.id), None)
                    if link is None:
                        linked = sorted({(l.supplier.name or "") for l in product.product_suppliers
                                         if l.supplier}) or ["nobody"]
                        errors.append(
                            f"{data['sku']} is not linked to {data['supplier']} "
                            f"(linked to: {', '.join(linked)}) — add the supplier on the "
                            f"products sheet first")
        if errors:
            entry["errors"] = errors
            report.append(entry); summary["errors"] += 1; continue

        fields = _row_fields(data)
        entry["term"] = fields
        # Queried rather than read off link.mbb_term_list: that collection was
        # loaded before this file wrote anything, so two identical rows in one
        # upload would both look new and the ladder would gain the same rung
        # twice.
        recorded_terms = (db.query(models.MbbTerm)
                            .filter_by(product_supplier_id=link.id).all())
        existing = [t for t in recorded_terms
                    if all(_term_fields(t)[k] == fields[k] for k in _DEAL_IDENTITY)]
        if existing:
            match = existing[0]
            recorded = _term_fields(match)
            changed = [k for k in _DEAL_TERMS if recorded[k] != fields[k]]
            if not changed:
                entry["status"] = "duplicate"
                summary["duplicate"] += 1
            else:
                for field in changed:
                    # cost and cost_per are the sheet's names; the columns are
                    # unit_cost and cost_basis. Writing the sheet's name would
                    # set a stray attribute and change nothing in the database.
                    setattr(match, _TERM_COLUMN.get(field, field), fields[field])
                if "note" in data:
                    match.note = _value(data, "note")
                match.updated_at = now
                db.flush()
                entry["status"] = "updated"
                entry["changes"] = {k: {"from": recorded[k], "to": fields[k]} for k in changed}
                summary["updated"] += 1
                if not dry_run:
                    # Which rung, not just which field. A SKU can hold several
                    # terms of one kind — 10+2 beside 10+3-on-orders-of-50 — so
                    # "free_qty 3 -> 2" on its own names no row in particular,
                    # and the trail cannot be read back to the deal it changed.
                    audit_log.record(db, action="product.mbb_term_update", actor=actor,
                                     entity_type="product", entity_id=link.product_id,
                                     entity_label=data["sku"],
                                     details={"via": "workbook", "supplier": data["supplier"],
                                              "term_id": match.id,
                                              "term": {k: fields[k] for k in _DEAL_IDENTITY},
                                              "changes": entry["changes"]},
                                     request=request)
            report.append(entry); continue

        term = models.MbbTerm(
            product_supplier_id=link.id, kind=fields["kind"],
            min_qty=fields["min_qty"], min_spend=fields["min_spend"],
            min_order_qty=fields["min_order_qty"],
            free_qty=fields["free_qty"], discount_pct=fields["discount_pct"],
            unit_cost=fields["cost"],
            cost_basis=fields["cost_per"] if fields["cost"] is not None else None,
            note=_value(data, "note"),
            sort_order=len(recorded_terms), created_at=now,
        )
        db.add(term)
        db.flush()
        entry["status"] = "created"
        summary["created"] += 1
        if not dry_run:
            audit_log.record(db, action="product.mbb_term_add", actor=actor,
                             entity_type="product", entity_id=link.product_id,
                             entity_label=data["sku"],
                             details={"via": "workbook", "supplier": data["supplier"],
                                      "term_id": term.id, "term": fields},
                             request=request)
        report.append(entry)

    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {"dry_run": dry_run, "summary": summary, "rows": report}

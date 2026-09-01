"""Export and import how each channel sells a product, as one CSV.

Three facts per channel, none of them derivable from how we buy:

    sell_uom        what the customer is charged in       ("mL", "Can(s)")
    sell_uom_count  how many of those make ONE sellable   (30 for a 30 mL
                    unit — the thing cost is derived in    bottle sold by the mL)
    order_multiple  the customer buys in multiples of      (12 means 12, 24, 36)
                    this; the multiple IS the minimum

    python scripts/channel_sale_terms_csv.py --export terms.csv
    python scripts/channel_sale_terms_csv.py --import terms.csv           # dry run
    python scripts/channel_sale_terms_csv.py --import terms.csv --apply

One row per SKU with a column group per channel, because that is how a
spreadsheet is actually edited — not one row per SKU-and-channel.

A BLANK cell means "leave this alone". It does not mean "set to the default",
so a file covering twenty SKUs can never silently clear the other eleven
thousand. To clear a value back to the default, write ``-``.

Nothing is written until every row has been read and checked. A bad row is
reported with its line number and the file still applies the good ones; it is
never half-applied on one row.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from decimal import Decimal, InvalidOperation

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/terms.db")

import database  # noqa: E402
import models  # noqa: E402

CHANNELS = ("shopify", "hktv", "clinic")
FIELDS = ("uom", "uom_count", "order_multiple")
HEADER = ["ims_sku"] + [f"{c}_{f}" for c in CHANNELS for f in FIELDS]
CLEAR = "-"

#: The units we expect, folded for comparison. A channel may charge in a
#: measure ("mL") or in the item itself ("Can(s)") — both are legitimate, so
#: this only catches typos. Whatever is ALREADY recorded is added to this at
#: run time: an export must import back unchanged, and rejecting a value the
#: system itself wrote would make the round trip a lie.
BASE_UOMS = {
    "ml", "l", "g", "kg", "mg", "unit(s)", "units", "unit", "pcs", "pc",
    "bag(s)", "box(es)", "bottle(s)", "can(s)", "pouch(es)", "tablet(s)",
    "capsule(s)", "vial(s)", "tube(s)", "sachet(s)", "pack(s)", "syringe(s)",
    "collar(s)", "pipette(s)", "packet(s)", "pad(s)", "dose(s)", "strip(s)",
}


def _fold(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def known_uoms(db) -> set[str]:
    """What we accept: the expected vocabulary plus everything already in use."""
    in_use = {
        _fold(value)
        for (value,) in db.query(models.SellingItem.sell_uom).distinct()
        if value and _fold(value)
    }
    return BASE_UOMS | in_use


def export(db, path: str) -> int:
    products = {p.id: p for p in db.query(models.ProductVariant).all()}
    listings: dict[int, dict[str, models.SellingItem]] = {}
    for item in db.query(models.SellingItem).all():
        listings.setdefault(item.product_variant_id, {})[_fold(item.channel)] = item

    written = 0
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(HEADER)
        for product_id, by_channel in sorted(listings.items()):
            product = products.get(product_id)
            if product is None:
                continue
            row = [product.sku_code or ""]
            for channel in CHANNELS:
                item = by_channel.get(channel)
                row += [
                    (item.sell_uom or "") if item else "",
                    ("" if item is None or item.sell_uom_count is None
                     else _plain(item.sell_uom_count)),
                    ("" if item is None or not item.order_multiple else str(item.order_multiple)),
                ]
            writer.writerow(row)
            written += 1
    print(f"wrote {written} row(s) to {path}")
    print("Blank means unset. Fill only what you want to change and hand it back;")
    print("a blank cell on import leaves the current value alone.")
    return 0


def _plain(value) -> str:
    text = format(Decimal(str(value)).normalize(), "f")
    return text


def _read_change(raw: str, kind: str, line: int, column: str, vocabulary: set[str]) -> tuple[bool, object, str | None]:
    """(is_a_change, value, error). Blank leaves alone; '-' clears."""
    text = str(raw or "").strip()
    if text == "":
        return False, None, None
    if text == CLEAR:
        return True, None, None
    if kind == "uom":
        if _fold(text) not in vocabulary:
            return True, None, (
                f"line {line}: {column}={text!r} is not a unit we know. "
                f"Use one of: {', '.join(sorted(vocabulary))}"
            )
        return True, text, None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return True, None, f"line {line}: {column}={text!r} is not a number"
    if number <= 0:
        return True, None, f"line {line}: {column}={text!r} must be greater than zero"
    if kind == "order_multiple" and number != number.to_integral_value():
        return True, None, f"line {line}: {column}={text!r} must be a whole number of units"
    return True, (int(number) if kind == "order_multiple" else number), None


def _same(current, value) -> bool:
    """Is this cell already what the file asks for?

    Numbers are compared as numbers: the database hands back a NUMERIC as
    Decimal("30.000000") while the file says "30", and comparing those as text
    would report a change on every row of an untouched export.
    """
    if current is None or value is None:
        return current is None and value is None
    try:
        return Decimal(str(current)) == Decimal(str(value))
    except InvalidOperation:
        return str(current).strip() == str(value).strip()


def load(db, path: str, *, apply: bool) -> int:
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in HEADER if c not in (reader.fieldnames or ())]
        if missing:
            print(f"the file is missing column(s): {', '.join(missing)}", file=sys.stderr)
            return 1
        rows = list(reader)

    by_sku = {p.sku_code: p for p in db.query(models.ProductVariant).all() if p.sku_code}
    listings: dict[tuple[int, str], models.SellingItem] = {
        (item.product_variant_id, _fold(item.channel)): item
        for item in db.query(models.SellingItem).all()
    }

    vocabulary = known_uoms(db)
    changes, errors, unchanged = [], [], 0
    for line, row in enumerate(rows, start=2):
        sku = str(row.get("ims_sku") or "").strip()
        product = by_sku.get(sku)
        if product is None:
            if sku:
                errors.append(f"line {line}: no product with IMS SKU {sku!r}")
            continue
        for channel in CHANNELS:
            item = listings.get((product.id, channel))
            for field, attribute in (("uom", "sell_uom"),
                                     ("uom_count", "sell_uom_count"),
                                     ("order_multiple", "order_multiple")):
                column = f"{channel}_{field}"
                kind = "uom" if field == "uom" else field
                is_change, value, error = _read_change(row.get(column, ""), kind, line, column, vocabulary)
                if error:
                    errors.append(error)
                    continue
                if not is_change:
                    continue
                if item is None:
                    errors.append(f"line {line}: {sku} is not listed on {channel}")
                    continue
                current = getattr(item, attribute)
                if _same(current, value):
                    unchanged += 1
                    continue
                changes.append((item, attribute, current, value, sku, channel, field))

    for error in errors:
        print(f"  REJECTED {error}")
    print(f"\n{len(changes)} change(s), {unchanged} already correct, {len(errors)} rejected")
    for item, attribute, current, value, sku, channel, field in changes[:40]:
        print(f"   {sku:<12} {channel:<8} {field:<15} {str(current):>10} -> {value}")
    if len(changes) > 40:
        print(f"   … and {len(changes) - 40} more")

    if not apply:
        print("\nDry run. Re-run with --apply to write.")
        return 1 if errors else 0
    if not changes:
        print("nothing to write")
        return 1 if errors else 0
    for item, attribute, _current, value, *_ in changes:
        setattr(item, attribute, value)
    db.commit()
    print(f"\nwrote {len(changes)} change(s).")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--export", metavar="CSV", help="Write the current terms out.")
    group.add_argument("--import", dest="load", metavar="CSV", help="Read terms back in.")
    parser.add_argument("--apply", action="store_true", help="Write. Without it, only report.")
    args = parser.parse_args()

    db = database.SessionLocal()
    try:
        if args.export:
            return export(db, args.export)
        return load(db, args.load, apply=args.apply)
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

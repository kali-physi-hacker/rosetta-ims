"""One-off backfill: mint the internal IMS SKU for products that never got one.

A product's SKU is an 8-digit internal code (see services/sku_service.py). 1,704
products on the live database carry something else in that column — almost all
of them the product NAME, because they were created before the code was
allocated:

    sku_code = 'Proheart Inj'
    sku_code = 'Royal Canin - Sterilised 11+ Senior Cat Dry Food (4kg)'

This mints the real code for each, using the same allocator the application
uses, so the scheme and the single global counter are honoured rather than
re-invented here.

WHAT ELSE MOVES WITH IT

The SKU is denormalised into several tables, and a rename that updates only
`products` silently detaches them. Measured on the live database:

    catalogue_audit.sku_code                 1,469 rows   (onboarding history)
    clientssot_purchases.sku                   249 rows   (sales history)
    clientssot_product_caretags.sku_code       199 rows   (care tags)
    catalogue_items.assigned_sku                 0 rows

All four are carried across. Three other columns match by coincidence and are
deliberately NOT touched — `product_suppliers.supplier_sku`,
`catalogue_items.supplier_sku` and `catalogue_supplier_products.supplier_sku`
hold the SUPPLIER's own code, which happens to be the same text because both
fields were seeded from the same name. Renaming our SKU does not change what a
supplier calls its product.

Every rename is recorded as a `product.sku_change` audit row, so the SKU-history
view on the product page shows where the code came from.

Runs as a DRY RUN by default and prints what it would do. Pass --apply to write.

    python scripts/backfill_missing_skus.py                 # report only
    python scripts/backfill_missing_skus.py --apply         # write
    python scripts/backfill_missing_skus.py --apply --csv out.csv
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import database  # noqa: E402
import models  # noqa: E402
from services import audit_log, sku_service  # noqa: E402

# The shape the application treats as an internal SKU.
INTERNAL_SKU = re.compile(r"^\d{8}$")

# Denormalised copies that MUST follow the rename: (model, attribute).
CARRIED_WITH_RENAME = (
    (models.CatalogueAuditEvent, "sku_code"),
    (models.CatalogueItem, "assigned_sku"),
)
# The client SSOT tables have no ORM model in this codebase; they are updated by
# statement against the same session so they stay inside the transaction.
CARRIED_SQL = (
    ("clientssot_purchases", "sku"),
    ("clientssot_product_caretags", "sku_code"),
)


def products_missing_a_sku(db):
    """Products whose sku_code is absent or is not an internal 8-digit code."""
    return [
        product
        for product in db.query(models.ProductVariant).order_by(models.ProductVariant.id).all()
        if not product.sku_code or not INTERNAL_SKU.match(str(product.sku_code).strip())
    ]


def _digit_for(db, category: str) -> str | None:
    """The SKU leading digit for a category, contract-first then the static map.

    Mirrors sku_service.next_sku so a category added in the UI works here too.
    """
    rule = db.query(models.CategoryRule).filter(models.CategoryRule.category == category).first()
    return (rule.sku_digit if rule and rule.sku_digit else None) or sku_service.ITEM_CATEGORY_DIGIT.get(category)


def _allocator(db):
    """Yields the next free suffix, counting up from the current maximum once.

    sku_service.next_sku rescans every product on each call, which is right for
    minting one SKU and wrong for minting seventeen hundred. The sequence it
    produces is identical: the same global counter, the same sentinel floor,
    the same collision guard.
    """
    taken = {code for (code,) in db.query(models.ProductVariant.sku_code).all() if code}
    suffix = sku_service._max_real_suffix(db)

    def mint(digit: str) -> str:
        nonlocal suffix
        while True:
            suffix += 1
            if suffix > sku_service._MAX_SUFFIX:
                raise RuntimeError("SKU sequence exhausted (7-digit suffix overflow)")
            code = f"{digit}{suffix:07d}"
            if code not in taken:
                taken.add(code)
                return code

    return mint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write the changes (default is a dry run)")
    parser.add_argument("--csv", type=Path, help="write an old -> new record of every rename")
    args = parser.parse_args()

    db = database.SessionLocal()
    try:
        missing = products_missing_a_sku(db)
        print(f"products without an internal 8-digit SKU: {len(missing)}")
        if not missing:
            return 0

        mint = _allocator(db)
        planned: list[tuple[models.ProductVariant, str]] = []
        blocked: list[models.ProductVariant] = []
        for product in missing:
            digit = _digit_for(db, product.category)
            if not digit:
                blocked.append(product)
                continue
            planned.append((product, mint(digit)))

        by_category = Counter(p.category for p, _ in planned)
        print("\nwould mint, by category:")
        for category, count in by_category.most_common():
            print(f"   {str(category):24} {count}")
        if blocked:
            print(f"\nCANNOT MINT — the category has no SKU digit ({len(blocked)}):")
            for product in blocked[:10]:
                print(f"   id={product.id:<6} category={product.category!r:20} {str(product.name)[:44]!r}")
            print("   add a digit for these in Categories, then re-run.")

        print("\nreferences that will follow the rename:")
        old_codes = [str(p.sku_code) for p, _ in planned]
        for model, attribute in CARRIED_WITH_RENAME:
            n = db.query(model).filter(getattr(model, attribute).in_(old_codes)).count()
            print(f"   {model.__tablename__}.{attribute:<18} {n}")
        for table, column in CARRIED_SQL:
            n = _count_sql(db, table, column, old_codes)
            print(f"   {table}.{column:<18} {n}")

        print("\nfirst few renames:")
        for product, new in planned[:8]:
            print(f"   {str(product.sku_code)[:40]:42} -> {new}   {str(product.name)[:34]}")

        if args.csv:
            with args.csv.open("w", newline="") as handle:
                writer = csv.writer(handle)
                writer.writerow(["product_id", "old_sku", "new_sku", "name", "category"])
                for product, new in planned:
                    writer.writerow([product.id, product.sku_code, new, product.name, product.category])
            print(f"\nwrote {args.csv}")

        if not args.apply:
            print("\nDRY RUN — nothing written. Re-run with --apply to write.")
            return 0

        now = datetime.utcnow().isoformat()
        for product, new in planned:
            old = str(product.sku_code)
            product.sku_code = new
            product.updated_at = now
            for model, attribute in CARRIED_WITH_RENAME:
                db.query(model).filter(getattr(model, attribute) == old).update(
                    {attribute: new}, synchronize_session=False
                )
            for table, column in CARRIED_SQL:
                _update_sql(db, table, column, old, new)
            audit_log.record(
                db,
                action="product.sku_change",
                entity_type="product",
                entity_id=product.id,
                entity_label=new,
                details={"from": old, "to": new, "via": "backfill_missing_skus"},
            )
        db.commit()
        print(f"\napplied {len(planned)} renames.")
        if blocked:
            print(f"{len(blocked)} left untouched — no SKU digit for their category.")
        return 0
    finally:
        db.close()


def _table_exists(db, table: str) -> bool:
    """The client SSOT tables are loaded by a separate importer and are absent
    on a fresh deployment. Their absence is not an error here."""
    from sqlalchemy import inspect

    return table in inspect(db.get_bind()).get_table_names()


def _count_sql(db, table: str, column: str, codes: list[str]) -> int:
    from sqlalchemy import text

    if not codes or not _table_exists(db, table):
        return 0
    total = 0
    for chunk in (codes[i:i + 400] for i in range(0, len(codes), 400)):
        names = {f"c{i}": value for i, value in enumerate(chunk)}
        placeholders = ",".join(f":{key}" for key in names)
        total += db.execute(
            text(f"select count(*) from {table} where {column} in ({placeholders})"), names
        ).scalar() or 0
    return total


def _update_sql(db, table: str, column: str, old: str, new: str) -> None:
    from sqlalchemy import text

    if not _table_exists(db, table):
        return
    db.execute(
        text(f"update {table} set {column} = :new where {column} = :old"), {"new": new, "old": old}
    )


if __name__ == "__main__":
    raise SystemExit(main())

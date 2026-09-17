"""Repair deal discounts written as whole-number percents.

``mbb_terms.discount_pct`` holds a FRACTION: 0.02 is 2% off. `config.tsx`
documents it that way, every screen renders ``pct * 100``, and pricing computes
``cost * (1 - discount_pct)``.

Between the upload workbook's importer shipping and the conversion being fixed,
a deals sheet saying "2" wrote 2.0 into that column. The result is not
cosmetic: ``cost * (1 - 2)`` is NEGATIVE, so an $8.75 pouch priced at -$8.75
and rendered as 200% off, and any ops-sheet push in that window published the
margin that came with it.

    python scripts/repair_discount_pct.py                 # report only
    python scripts/repair_discount_pct.py --apply         # write the fix

The rule is ``discount_pct >= 1``, which is a fact rather than a guess: a
fraction below 1 is the only shape a real discount has, and the largest
genuine one recorded anywhere is 0.2. The one value it would misread is an
exact 1.0 meaning "100% off" — free goods, which this business records as
buy_x_get_y, not as a discount — so the script reports any 1.0 separately and
leaves it for a person rather than dividing it.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

if not os.environ.get("DATABASE_URL"):
    raise SystemExit(
        "DATABASE_URL is not set.\n"
        "  DATABASE_URL=sqlite:///data/ims.db python scripts/repair_discount_pct.py"
    )

import database  # noqa: E402
import models  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the repair (default: report only)")
    args = parser.parse_args()

    db = database.SessionLocal()
    suspect = (
        db.query(models.MbbTerm)
        .filter(models.MbbTerm.discount_pct >= 1)
        .order_by(models.MbbTerm.id)
        .all()
    )
    exact_one = [t for t in suspect if float(t.discount_pct) == 1.0]
    repairable = [t for t in suspect if float(t.discount_pct) != 1.0]

    if not suspect:
        total = db.query(models.MbbTerm).filter(models.MbbTerm.discount_pct.isnot(None)).count()
        print(f"nothing to repair — {total} discounts recorded, all below 1")
        return 0

    print(f"{len(repairable)} term(s) hold a whole-number percent:\n")
    for term in repairable:
        link = db.get(models.ProductSupplier, term.product_supplier_id)
        product = db.get(models.ProductVariant, link.product_id) if link else None
        supplier = db.get(models.Supplier, link.supplier_id) if link and link.supplier_id else None
        was = float(term.discount_pct)
        print(f"  term {term.id:<7} {(product.sku_code if product else '?'):<10} "
              f"{(supplier.name if supplier else '?'):<24} {term.kind:<16} "
              f"{was} -> {round(was / 100, 6)}   (renders {was * 100:.0f}% -> {was:.0f}%)")

    if exact_one:
        print(f"\n{len(exact_one)} term(s) hold exactly 1.0 and are NOT touched — 1.0 is either")
        print("100% off or a percent someone typed. A person has to say which:")
        for term in exact_one:
            print(f"  term {term.id}  product_supplier_id={term.product_supplier_id}  kind={term.kind}")

    if not args.apply:
        print(f"\nreport only — re-run with --apply to write {len(repairable)} change(s)")
        return 0

    for term in repairable:
        term.discount_pct = round(float(term.discount_pct) / 100, 6)
    db.commit()
    left = db.query(models.MbbTerm).filter(models.MbbTerm.discount_pct >= 1).count()
    print(f"\nrepaired {len(repairable)}; {left} row(s) at or above 1 remain "
          f"({len(exact_one)} of them the untouched 1.0s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

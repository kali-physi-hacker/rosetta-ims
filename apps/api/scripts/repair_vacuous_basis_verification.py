"""Demote price rows the backfill 'verified' on a match that said nothing.

`basis_verified` is meant to separate a fact from a reading. The first backfill
treated any agreement between the price's basis word and the packaging's
sellable unit as confirmation — including when both words were `UNIT`, which is
what set_offering_packaging writes when no sellable unit is known and what 94%
of price rows carry. Two placeholders agreeing is not agreement, and 485 rows
were marked confirmed on that basis. Among them VSRx 60004260: a $485 "tablet"
that is really a bottle of a hundred, publishing -16,067% under a flag saying
someone had checked it.

resolve_basis no longer makes that mistake, but it only ever examines rows that
are still unverified, so the already-marked ones need this one pass.

Nothing about what the sheet PUBLISHES changes: `basis` is untouched, so every
cost and every margin stays exactly where it is. What changes is which rows
admit that nobody has confirmed them.

Report-only by default. `--apply` writes.

    python scripts/repair_vacuous_basis_verification.py
    python scripts/repair_vacuous_basis_verification.py --apply
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from database import SessionLocal  # noqa: E402
from services.offering_costs import resolve_basis  # noqa: E402

_VERIFIED = """
    SELECT pr.id, pr.basis, pr.price_basis_uom_code, p.sku_code, pr.amount,
           pk.purchase_uom_code, pk.sellable_unit_uom_code,
           pk.sellable_units_per_purchase_unit
      FROM catalogue_supplier_prices pr
      LEFT JOIN catalogue_supplier_products o ON o.id = pr.supplier_product_id
      LEFT JOIN products p ON p.id = o.product_variant_id
      LEFT JOIN catalogue_packaging_configurations pk
        ON pk.supplier_product_id = pr.supplier_product_id
       AND pk.superseded_at IS NULL
     WHERE pr.basis_verified = 1
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the demotions")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        demote, kept, basis_would_move = [], 0, []
        for pid, basis, word, sku, amount, purchase, sellable, per in db.execute(text(_VERIFIED)):
            resolved, agreed = resolve_basis(
                word, (purchase, sellable, float(per) if per is not None else None)
            )
            if resolved != basis:
                # Must not happen: this pass is about confidence, not arithmetic.
                basis_would_move.append((sku, basis, resolved))
            if agreed:
                kept += 1
            else:
                demote.append((pid, sku, word, amount, sellable))

        print(f"verified rows examined : {kept + len(demote)}")
        print(f"  genuinely confirmed  : {kept}")
        print(f"  to demote            : {len(demote)}")
        if basis_would_move:
            print("\nREFUSING: this pass would change a basis, which it must never do:")
            for sku, was, now in basis_would_move[:10]:
                print(f"  {sku}: {was} -> {now}")
            return 1

        print("\n  sample of what gets demoted (the number each publishes is unchanged):")
        for _pid, sku, word, amount, sellable in demote[:10]:
            print(f"    {str(sku):<12} amount={str(amount):<10} basis word={word!r} "
                  f"sellable={sellable!r}")

        if not args.apply:
            print(f"\nreport only — {len(demote)} rows would move into the queue. "
                  f"re-run with --apply to write.")
            return 0

        ids = [row[0] for row in demote]
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            db.execute(text(
                "UPDATE catalogue_supplier_prices SET basis_verified = 0 "
                f"WHERE id IN ({','.join(str(int(i)) for i in chunk)})"
            ))
        db.commit()
        print(f"\ndemoted {len(ids)} rows. the queue is now honest.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

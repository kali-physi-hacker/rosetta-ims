"""One-off backfill: set Product.segment ('vet' | 'non_vet') from the supplier's
segment, for products that don't already have one.

A product is classified only when its supplier(s) point unambiguously one way; a
'both' / unknown / missing / conflicting supplier segment leaves it NULL. The
SKU MASTER LIST sheet has no vet/non-vet column, so the supplier segment (imported
from the dedicated vet / non-vet supplier sheets) is the signal.

Re-runnable: by default only fills NULLs, so it never clobbers a manual edit or a
later scrape value. Pass --force to re-derive everything.

    docker compose exec api python backfill_segment.py [--force]
"""

import sys
from collections import Counter

from database import SessionLocal
import models


def derive(product) -> str | None:
    segs = set()
    for ps in product.product_suppliers:
        seg = ps.supplier.segment if ps.supplier else None
        if seg in ("vet", "non_vet"):
            segs.add(seg)
    if segs == {"vet"}:
        return "vet"
    if segs == {"non_vet"}:
        return "non_vet"
    return None  # 'both' / unknown / none / conflicting -> leave NULL


def main(force: bool = False) -> None:
    db = SessionLocal()
    updated = 0
    final: Counter = Counter()
    try:
        for product in db.query(models.Product).all():
            if force or product.segment is None:
                derived = derive(product)
                if derived is not None and derived != product.segment:
                    product.segment = derived
                    updated += 1
            final[product.segment or "null"] += 1
        db.commit()
        print(f"Backfill complete: {updated} products updated.")
        print(f"Final segment distribution: {dict(final)}")
    finally:
        db.close()


if __name__ == "__main__":
    main(force="--force" in sys.argv)

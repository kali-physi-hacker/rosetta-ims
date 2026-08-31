"""Give already-published Royal Canin cases the count Royal Canin printed.

Royal Canin prices wet food by the case. Rows published before the connector
learned to read the printed pack ("CAN 410GX12" is twelve cans) carry a case
price with no idea what the case holds — so the case price stood in as the cost
of ONE pouch and every margin built on it read around -1000%.

The fix in the pipeline only helps rows captured after it. This gives the same
answer to rows already live, without re-capturing the catalogue and putting 454
products back on the review board: it reads the count from the shop for exactly
the affected SKUs and records it as the offering's packaging.

    python scripts/backfill_royal_canin_pack_counts.py            # dry run
    python scripts/backfill_royal_canin_pack_counts.py --apply

Append-only, like every other packaging write: the empty row is superseded and
a new one recorded, so the change is visible and reversible. Idempotent — a
second run finds nothing left to do. A SKU whose pack the shop does not spell
out is REPORTED, never guessed: the count comes from Royal Canin's own printed
name and from nowhere else, and in particular never from the legacy
units_per_pack column, which on other suppliers carries the order multiple.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/backfill.db")

import database  # noqa: E402
import models  # noqa: E402
from services import royal_canin_connector as connector  # noqa: E402

SUPPLIER_IDS = (39, 40)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _affected(db):
    """Catalogue-ingested Royal Canin offerings with no pack count recorded.

    Scoped by having reached PUBLICATION, not by what the current price says.
    A cost someone has since corrected by hand is still a catalogue-ingested
    product, and its packaging is a fact about the product rather than about
    whoever last touched the price — leave it blank and the next capture, which
    writes a case-basis price again, has nothing to divide by and the row breaks
    a second time.

    Recording it is safe whatever the price says: division only happens when
    the price basis IS the purchase unit, so a hand-set per-unit cost stays
    exactly as it is.
    """
    rows = (
        db.query(
            models.SupplierOffering,
            models.CatalogueSupplierPrice,
            models.CataloguePackagingConfiguration,
        )
        .join(
            models.CatalogueServingPublication,
            (models.CatalogueServingPublication.supplier_product_id == models.SupplierOffering.id)
            & (models.CatalogueServingPublication.is_current == 1),
        )
        .join(
            models.CatalogueSupplierPrice,
            (models.CatalogueSupplierPrice.supplier_product_id == models.SupplierOffering.id)
            & (models.CatalogueSupplierPrice.is_current == 1),
        )
        .outerjoin(
            models.CataloguePackagingConfiguration,
            (models.CataloguePackagingConfiguration.supplier_product_id == models.SupplierOffering.id)
            & (models.CataloguePackagingConfiguration.superseded_at.is_(None)),
        )
        .filter(models.SupplierOffering.supplier_id.in_(SUPPLIER_IDS))
        .all()
    )
    seen, out = set(), []
    for offering, price, packaging in rows:
        if offering.id in seen:
            continue
        seen.add(offering.id)
        if packaging is not None and packaging.sellable_units_per_purchase_unit is not None:
            continue
        out.append((offering, price, packaging))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Write. Without it, only report.")
    args = parser.parse_args()

    db = database.SessionLocal()
    try:
        affected = _affected(db)
        if not affected:
            print("Nothing to do: every container-priced Royal Canin offering has a count.")
            return 0
        print(f"{len(affected)} catalogue-ingested offering(s) with no pack count recorded.")

        # One read of the shop, for the whole set. The connector's own reader,
        # so the count here and the count a future capture writes are the same
        # function — they cannot drift apart.
        by_sku = {
            str(hit.get("original_sku")): hit
            for hit in connector.fetch_index()
        }
        print(f"read {len(by_sku)} products from Royal Canin\n")

        resolved, unresolved, single, missing = [], [], [], []
        for offering, price, packaging in affected:
            hit = by_sku.get(str(offering.supplier_sku or ""))
            if hit is None:
                missing.append(offering.supplier_sku)
                continue
            if str(hit.get("nav_uom") or "").strip().upper() not in connector.PACK_CONTAINER_BY_NAV_UOM:
                # A UNIT purchase buys one thing. There is no breakdown to
                # record and a count of 1 would only be noise.
                single.append(offering.supplier_sku)
                continue
            breakdown = connector.pack_breakdown(hit)
            if not breakdown:
                unresolved.append((offering.supplier_sku, str(hit.get("name") or "")))
                continue
            count, container = [part.strip() for part in breakdown.split("/")]
            resolved.append((offering, packaging, price, int(count), container))

        for sku, why in unresolved:
            print(f"  UNRESOLVED {sku}: Royal Canin does not print the pack — {why}")
        for sku in missing:
            print(f"  NOT IN THE SHOP {sku}: offered to us no longer?")
        print(
            f"\nresolved {len(resolved)}, unresolved {len(unresolved)}, "
            f"single-unit purchases skipped {len(single)}, absent {len(missing)}"
        )

        for offering, packaging, price, count, container in resolved:
            basis = (price.price_basis_uom_code or "").strip().upper()
            amount = float(price.amount)
            if basis == container:
                effect = f"{amount:>9.2f} / {count:<3} = {amount / count:>8.2f} per unit"
            else:
                # The price is already per unit — usually corrected by hand.
                # Recording the pack changes nothing today and keeps the next
                # capture from re-breaking it.
                effect = f"{amount:>9.2f} per unit already ({basis}); pack recorded for next capture"
            print(f"  {offering.supplier_sku:<12} {count:>3} / {container:<5} {effect}")

        if not args.apply:
            print("\nDry run. Re-run with --apply to write.")
            return 0

        now = _now()
        for offering, packaging, _price, count, container in resolved:
            if packaging is not None:
                packaging.superseded_at = now
            db.add(models.CataloguePackagingConfiguration(
                supplier_product_id=offering.id,
                purchase_uom_code=container,
                purchase_uom_label=container.title(),
                # What a case holds is a countable thing, but Royal Canin names
                # it inconsistently ("P", "P-J", "MOU CAN", "LOAF"), so no
                # pouch/can is claimed — UNIT says "one of whatever is inside".
                sellable_unit_uom_code="UNIT",
                sellable_units_per_purchase_unit=count,
                source_text=f"backfilled from Royal Canin's printed name: {count} / {container}",
                effective_from=now,
                created_at=now,
            ))
        db.commit()
        print(f"\nwrote packaging for {len(resolved)} offering(s).")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())

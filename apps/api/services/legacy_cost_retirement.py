"""Retire the legacy `basic_cost` column — migrate, verify, then drop.

Supplier cost lives in the explicit product domain: a SupplierOffering's
effective-dated price history. `product_suppliers.basic_cost` was the
pre-domain whole-pack column, and the Google Sheet's shadow columns
(`products.basic_cost_sheet`, `products.units_per_pack_sheet`) existed only
to diff against it. The sheet is no longer a source and every cost now has an
offering row, so all three columns go.

The order here is the safety property: any link still carrying only
`basic_cost` is migrated to an offering price FIRST, the result is verified,
and the drop happens only when nothing would lose its cost. A database that
has already been retired is detected by the missing column and skipped, so
this is safe to run on every boot.

Reads of the doomed column go through raw SQL on purpose — the ORM model no
longer declares it, and must not, or every query after the drop would break.
Writes reuse the domain models, so migrated rows are indistinguishable from
any other manually recorded cost.

This module is a one-shot migration: once every environment reports
`already_retired`, it and its call in main.py can be deleted.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

import models

_DOOMED = (
    ("product_suppliers", "basic_cost"),
    ("products", "basic_cost_sheet"),
    ("products", "units_per_pack_sheet"),
)


def retire_legacy_cost(engine) -> dict:
    """Migrate any remaining legacy costs, then drop the legacy columns."""

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    present = [
        (table, column)
        for table, column in _DOOMED
        if table in tables and column in {c["name"] for c in inspector.get_columns(table)}
    ]
    if not present:
        return {"status": "already_retired", "migrated": 0, "dropped": []}

    migrated = 0
    if ("product_suppliers", "basic_cost") in present:
        with Session(engine) as db:
            migrated = _migrate_remaining_costs(db)
            remaining = db.execute(text(_UNMIGRATED_SQL)).scalar() or 0
        if remaining:
            # Never drop data that has nowhere to live. Leaving the column in
            # place keeps the system readable; the next boot retries.
            return {"status": "blocked", "migrated": migrated, "unmigrated": int(remaining), "dropped": []}

    preparer = engine.dialect.identifier_preparer
    dropped = []
    with engine.begin() as connection:
        for table, column in present:
            connection.execute(
                text(f"ALTER TABLE {preparer.quote(table)} DROP COLUMN {preparer.quote(column)}")
            )
            dropped.append(f"{table}.{column}")
    return {"status": "retired", "migrated": migrated, "dropped": dropped}


# Links that still hold a legacy cost with no current offering price to carry it.
_UNMIGRATED_SQL = """
SELECT COUNT(*) FROM product_suppliers ps
WHERE ps.supplier_id IS NOT NULL
  AND ps.basic_cost IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM catalogue_supplier_products o
      JOIN catalogue_supplier_prices p
        ON p.supplier_product_id = o.id AND p.is_current = 1
      WHERE o.supplier_id = ps.supplier_id
        AND o.product_variant_id = ps.product_id
  )
"""

_TO_MIGRATE_SQL = """
SELECT ps.id, ps.supplier_id, ps.product_id, ps.supplier_sku, ps.barcode,
       ps.basic_cost, ps.units_per_pack, ps.cost_updated_at
FROM product_suppliers ps
WHERE ps.supplier_id IS NOT NULL
  AND ps.basic_cost IS NOT NULL
  AND NOT EXISTS (
      SELECT 1 FROM catalogue_supplier_products o
      JOIN catalogue_supplier_prices p
        ON p.supplier_product_id = o.id AND p.is_current = 1
      WHERE o.supplier_id = ps.supplier_id
        AND o.product_variant_id = ps.product_id
  )
"""


def _migrate_remaining_costs(db: Session) -> int:
    """Give every legacy-only link an offering + current price (the migration
    baseline). Mirrors a manually recorded cost: per-sell-unit amount, the
    legacy cost's own date, no ingestion run."""

    rows = db.execute(text(_TO_MIGRATE_SQL)).fetchall()
    if not rows:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    offerings = {
        (offering.supplier_id, offering.product_variant_id): offering
        for offering in (
            db.query(models.SupplierOffering)
            .filter(models.SupplierOffering.product_variant_id.isnot(None))
            .all()
        )
    }
    # (supplier, supplier_sku) is unique across offerings, but legacy data has
    # the same supplier SKU on more than one variant — the first keeps it, the
    # rest carry none (each link still shows its own SKU on the page).
    used_skus = {
        (offering.supplier_id, offering.supplier_sku)
        for offering in offerings.values()
        if offering.supplier_sku
    }
    uoms = dict(
        db.query(models.ProductVariant.id, models.ProductVariant.uom)
        .filter(models.ProductVariant.id.in_({row.product_id for row in rows}))
        .all()
    )

    for row in rows:
        key = (row.supplier_id, row.product_id)
        offering = offerings.get(key)
        if offering is None:
            sku = (row.supplier_sku or "").strip() or None
            if sku:
                if (row.supplier_id, sku) in used_skus:
                    sku = None
                else:
                    used_skus.add((row.supplier_id, sku))
            offering = models.SupplierOffering(
                supplier_product_key=f"supplier:{row.supplier_id}:offer:link:{row.id}",
                legacy_product_supplier_id=row.id,
                supplier_id=row.supplier_id,
                product_variant_id=row.product_id,
                supplier_sku=sku,
                barcode=row.barcode,
                status="active",
                created_at=now,
                updated_at=now,
            )
            db.add(offering)
            db.flush()
            offerings[key] = offering

        units = row.units_per_pack
        unit_cost = round(row.basic_cost / units, 4) if units and units > 1 else row.basic_cost
        db.add(
            models.CatalogueSupplierPrice(
                supplier_product_id=offering.id,
                amount=unit_cost,
                currency="HKD",
                price_basis_uom_code="UNIT",
                price_basis_uom_label=uoms.get(row.product_id),
                effective_from=row.cost_updated_at or now,
                is_current=1,
                created_at=now,
            )
        )
    db.commit()
    return len(rows)

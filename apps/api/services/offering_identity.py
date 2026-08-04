"""Finding the one SupplierOffering a supplier's SKU denotes.

`supplier_product_key` looks like an identity and is not one. Offerings reach
`catalogue_supplier_products` by several routes that mint different keys for the
same row:

    supplier:{sid}:offer:{sku}                        the catalogue pipeline
    supplier:{sid}:offer:link:{n}                     offering_costs / the baseline backfill
    supplier:{sid}:offer:legacy-product-supplier:{n}  earlier backfill generations

The real identity is what the database enforces: UNIQUE (supplier_id,
supplier_sku), plus barcode within a supplier. Every read that means "the
offering for this supplier's SKU" must resolve on that, or it silently misses a
row that exists — and then either inserts a duplicate and dies on the
constraint, or reports the state as missing.

That mistake has now been made three times in three checkpoints (apply, publish,
serving persistence), so the resolution lives here once.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

import models

LEGACY_LINK_PREFIX = "legacy-product-supplier:"
_LINK_KEY_SUFFIX = re.compile(r":offer:(?:link|legacy-product-supplier):(\d+)$")


def find_offering(
    db: Session,
    *,
    supplier_id: int | None,
    supplier_sku: str | None = None,
    barcode: str | None = None,
    key: str | None = None,
) -> models.SupplierOffering | None:
    """The offering this identity denotes, whatever key it happens to carry.

    Tries the key first (cheap, and exact when the row was minted by the same
    code path), then the business identity the unique index is built on.
    """
    if key:
        found = db.query(models.SupplierOffering).filter_by(supplier_product_key=key).first()
        if found is not None:
            return found
    if supplier_id is None:
        return None
    scoped = db.query(models.SupplierOffering).filter_by(supplier_id=supplier_id)
    if supplier_sku:
        found = scoped.filter(models.SupplierOffering.supplier_sku == supplier_sku).first()
        if found is not None:
            return found
    if barcode:
        return scoped.filter(models.SupplierOffering.barcode == barcode).first()
    return None


def offering_identities(
    row: models.SupplierOffering,
    *,
    supplier_id: int | None = None,
    supplier_sku: str | None = None,
) -> set[str]:
    """Every identity string that has ever denoted this offering.

    A mastering candidate freezes `supplier_product_id` when it is prepared, and
    which form it froze depends on what existed at that moment — so a candidate
    prepared before the offering baseline backfill holds the legacy-link form
    for a row that now carries an offering key. Comparing against the set keeps
    the check about the offering rather than about its current name.
    """
    identities = {row.supplier_product_key}
    if row.legacy_product_supplier_id is not None:
        identities.add(f"{LEGACY_LINK_PREFIX}{row.legacy_product_supplier_id}")
    # Rows predating the FK column still carry the link id inside the key.
    embedded = _LINK_KEY_SUFFIX.search(row.supplier_product_key or "")
    if embedded:
        identities.add(f"{LEGACY_LINK_PREFIX}{embedded.group(1)}")
    sid = supplier_id if supplier_id is not None else row.supplier_id
    sku = supplier_sku or row.supplier_sku
    if sid is not None and sku:
        identities.add(f"supplier:{sid}:offer:{sku}")
    return identities

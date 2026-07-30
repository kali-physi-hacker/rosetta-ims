"""Compatibility migration into the explicit product domain.

The operational ``products`` table is a Product Variant compatibility table.
These helpers materialize the inventory and channel-facing identities that
were historically folded into that row. They are intentionally idempotent.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

import models


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_inventory_item(db: Session, variant: models.ProductVariant) -> models.InventoryItem:
    row = db.query(models.InventoryItem).filter_by(product_variant_id=variant.id).first()
    if row is not None:
        return row
    timestamp = _now()
    row = models.InventoryItem(
        inventory_key=f"variant:{variant.sku_code}:inventory",
        product_variant_id=variant.id,
        legacy_product_id=variant.id,
        valuation_uom=variant.uom,
        storage_rule=variant.storage_rule,
        status=variant.status,
        created_at=timestamp,
        updated_at=timestamp,
    )
    db.add(row)
    db.flush()
    return row


def ensure_selling_item(
    db: Session,
    variant: models.ProductVariant,
    channel: models.ProductChannel,
    inventory_item: models.InventoryItem,
) -> models.SellingItem:
    row = (
        db.query(models.SellingItem)
        .filter_by(product_variant_id=variant.id, channel=channel.channel)
        .first()
    )
    status = "ACTIVE" if channel.is_active else "INACTIVE"
    timestamp = _now()
    if row is None:
        row = models.SellingItem(
            selling_item_key=f"variant:{variant.sku_code}:channel:{channel.channel}",
            product_variant_id=variant.id,
            inventory_item_id=inventory_item.id,
            channel=channel.channel,
            sell_uom=variant.uom,
            units_per_listing=channel.units_per_listing,
            order_multiple=channel.order_multiple,
            selling_price=channel.selling_price,
            status=status,
            created_at=timestamp,
            updated_at=timestamp,
        )
        db.add(row)
    else:
        row.inventory_item_id = inventory_item.id
        row.sell_uom = variant.uom
        row.units_per_listing = channel.units_per_listing
        row.order_multiple = channel.order_multiple
        row.selling_price = channel.selling_price
        row.status = status
        row.updated_at = timestamp
    return row


def backfill_explicit_product_domain(db: Session) -> tuple[int, int]:
    """Materialize missing InventoryItem and SellingItem compatibility rows."""

    inventory_created = 0
    selling_created = 0
    for variant in db.query(models.ProductVariant).all():
        existing_inventory = (
            db.query(models.InventoryItem.id)
            .filter_by(product_variant_id=variant.id)
            .first()
        )
        inventory = ensure_inventory_item(db, variant)
        if existing_inventory is None:
            inventory_created += 1
        for channel in variant.channels:
            existing_selling = (
                db.query(models.SellingItem.id)
                .filter_by(product_variant_id=variant.id, channel=channel.channel)
                .first()
            )
            ensure_selling_item(db, variant, channel, inventory)
            if existing_selling is None:
                selling_created += 1
    db.commit()
    return inventory_created, selling_created

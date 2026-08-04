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


# ── creating a variant from a catalogue review decision ───────────────────────

class VariantCreationError(ValueError):
    """The draft cannot become a product (unknown category, SKU space exhausted)."""


def create_variant_from_draft(
    db: Session,
    draft: dict,
    *,
    provenance: dict | None = None,
    attempts: int = 4,
) -> models.ProductVariant:
    """Mint a canonical Product Variant from a reviewer's create draft.

    Called from ``apply_approved_candidate`` inside the apply transaction, so a
    later failure in the same apply rolls the product back with everything else.
    That is the whole reason creation happens at apply and not at draft time —
    an abandoned or rejected draft must leave no SKU behind.

    ``next_sku`` reads max(suffix)+1 and checks for a collision, but two
    concurrent applies can still agree on a number. The unique index on
    ``sku_code`` is the real guard, so we retry a few times on the integrity
    error rather than trusting the read.
    """
    from sqlalchemy.exc import IntegrityError

    from services import sku_service

    name = (draft.get("name") or "").strip()
    category = (draft.get("category") or "").strip()
    if not name:
        raise VariantCreationError("create draft has no product name")
    if not category:
        raise VariantCreationError("create draft has no item category")

    timestamp = _now()
    actor = (provenance or {}).get("actor")
    for attempt in range(attempts):
        try:
            sku = sku_service.next_sku(category, db)
        except ValueError as exc:
            raise VariantCreationError(str(exc)) from exc
        variant = models.ProductVariant(
            sku_code=sku,
            name=name,
            category=category,
            brand=(draft.get("brand") or None),
            uom=(draft.get("uom") or None),
            pack_unit=(draft.get("pack_unit") or None),
            storage_rule=(draft.get("storage_rule") or "any"),
            status="ACTIVE",
            hero_sku=0,
            created_at=timestamp,
            updated_at=timestamp,
            last_manual_edit_at=timestamp,
            last_manual_edit_by=actor,
        )
        # A SAVEPOINT, not a bare flush: an IntegrityError leaves the session
        # needing a rollback, and rolling back the outer transaction would
        # discard the entire apply. The nested block confines the failure to
        # this one attempt. The flush itself is required — a cluster of creates
        # in one apply must each see the previous one's SKU, or they all
        # allocate the same number off the same stale max(suffix).
        try:
            with db.begin_nested():
                db.add(variant)
                db.flush()
        except IntegrityError:
            if attempt == attempts - 1:
                raise VariantCreationError(
                    f"could not allocate a SKU for '{name}' after {attempts} attempts"
                )
            continue
        ensure_inventory_item(db, variant)
        return variant
    raise VariantCreationError(f"could not allocate a SKU for '{name}'")

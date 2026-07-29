"""Offering-first supplier cost reads (explicit product domain).

The catalogue pipeline writes supplier cost to the SupplierOffering's
effective-dated CatalogueSupplierPrice history — never to the legacy
``ProductSupplier.basic_cost`` column. Read surfaces resolve cost through the
offering first; ``basic_cost`` remains only as the pre-domain fallback for
links that have no offering price yet (sheet seeds, manual edits). Legacy
write flows keep feeding that fallback until they migrate — no new code may
write ``basic_cost`` on behalf of the pipeline.

Price basis matters: ``basic_cost`` is a whole-pack cost, while an offering
price declares what one amount buys. A price based on the purchase unit
(case/box) divides by the offering's current sellable-units-per-purchase-unit
before it can stand in for the per-sell-unit cost every margin runs on; a
price already based on the sellable unit stands in directly.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

import models

_SESSION_CACHE_KEY = "offering_unit_costs"


def unit_cost_for_link(ps: models.ProductSupplier | None) -> float | None:
    """Per-sell-unit cost from the supplier's current offering price, if any.

    Bulk-safe: the first call in a session loads every current offering price
    once into ``session.info``; serializing thousands of products costs one
    query, not one per row.
    """

    # Callers may pass duck-typed stand-ins (reparse previews use
    # SimpleNamespace with just basic_cost/units_per_pack) — those have no
    # supplier link and therefore no offering.
    supplier_id = getattr(ps, "supplier_id", None)
    product_id = getattr(ps, "product_id", None)
    if ps is None or supplier_id is None or product_id is None:
        return None
    if not isinstance(ps, models.ProductSupplier):
        return None
    session = Session.object_session(ps)
    if session is None:
        return None
    return _session_map(session).get((supplier_id, product_id))


def invalidate(session: Session) -> None:
    """Drop the per-session memo — called after commercial application so a
    session that applies and then reads sees the price it just wrote."""

    session.info.pop(_SESSION_CACHE_KEY, None)


def record_supplier_cost(
    db: Session,
    link: models.ProductSupplier,
    *,
    pack_cost: float | None,
) -> None:
    """Record a human cost edit as the current offering price.

    Deliberate human actions (manual PATCH, CSV import, sheet-accept, invoice
    confirmation) write the domain's effective cost here — an effective-dated
    CatalogueSupplierPrice on the link's SupplierOffering, created if the link
    has none yet. The caller still dual-writes ``basic_cost`` so the sheet
    conflict/shadow machinery keeps working until it is retired; reads prefer
    the offering price either way. Bulk sheet re-seeds stay legacy-only on
    purpose: an offering price asserts a human decided this number.

    ``pack_cost`` follows basic_cost semantics (whole-pack); the per-sell-unit
    amount is stored on the price row with the variant's sell unit as basis.
    Does not commit — runs inside the caller's transaction.
    """

    if pack_cost is None or link.supplier_id is None or link.product_id is None:
        return
    units = link.units_per_pack
    unit_cost = round(pack_cost / units, 4) if units and units > 1 else pack_cost
    now = _utcnow_iso()

    offering = (
        db.query(models.SupplierOffering)
        .filter_by(supplier_id=link.supplier_id, product_variant_id=link.product_id)
        .first()
    )
    if offering is None:
        offering = models.SupplierOffering(
            supplier_product_key=f"supplier:{link.supplier_id}:offer:link:{link.id}",
            legacy_product_supplier_id=link.id,
            supplier_id=link.supplier_id,
            product_variant_id=link.product_id,
            supplier_sku=link.supplier_sku,
            barcode=link.barcode,
            status="active",
            created_at=now,
            updated_at=now,
        )
        db.add(offering)
        db.flush()

    variant = db.get(models.ProductVariant, link.product_id)
    db.query(models.CatalogueSupplierPrice).filter_by(
        supplier_product_id=offering.id, is_current=1
    ).update({"is_current": 0, "superseded_at": now}, synchronize_session=False)
    db.add(
        models.CatalogueSupplierPrice(
            supplier_product_id=offering.id,
            amount=unit_cost,
            currency="HKD",
            price_basis_uom_code="UNIT",
            price_basis_uom_label=(variant.uom if variant else None),
            effective_from=now,
            is_current=1,
            created_at=now,
        )
    )
    invalidate(db)


def _utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


def _session_map(session: Session) -> dict[tuple[int, int], float]:
    cached = session.info.get(_SESSION_CACHE_KEY)
    if cached is not None:
        return cached

    packaging: dict[int, tuple[str | None, str | None, float | None]] = {}
    packaging_rows = (
        session.query(
            models.CataloguePackagingConfiguration.supplier_product_id,
            models.CataloguePackagingConfiguration.purchase_uom_code,
            models.CataloguePackagingConfiguration.sellable_unit_uom_code,
            models.CataloguePackagingConfiguration.sellable_units_per_purchase_unit,
        )
        .filter(models.CataloguePackagingConfiguration.superseded_at.is_(None))
        .order_by(models.CataloguePackagingConfiguration.id)
        .all()
    )
    for offering_id, purchase, sellable, per_purchase in packaging_rows:
        packaging[offering_id] = (purchase, sellable, float(per_purchase) if per_purchase is not None else None)

    out: dict[tuple[int, int], float] = {}
    price_rows = (
        session.query(
            models.SupplierOffering.supplier_id,
            models.SupplierOffering.product_variant_id,
            models.SupplierOffering.id,
            models.CatalogueSupplierPrice.amount,
            models.CatalogueSupplierPrice.price_basis_uom_code,
        )
        .join(
            models.CatalogueSupplierPrice,
            models.CatalogueSupplierPrice.supplier_product_id == models.SupplierOffering.id,
        )
        .filter(
            models.CatalogueSupplierPrice.is_current == 1,
            models.SupplierOffering.product_variant_id.isnot(None),
        )
        .all()
    )
    for supplier_id, variant_id, offering_id, amount, basis_code in price_rows:
        out[(supplier_id, variant_id)] = _per_sell_unit(float(amount), basis_code, packaging.get(offering_id))

    session.info[_SESSION_CACHE_KEY] = out
    return out


def _per_sell_unit(
    amount: float,
    basis_code: str | None,
    pack: tuple[str | None, str | None, float | None] | None,
) -> float:
    if pack:
        purchase, sellable, per_purchase = pack
        if (
            per_purchase is not None
            and per_purchase > 1
            and basis_code
            and purchase
            and basis_code == purchase
            and basis_code != (sellable or "")
        ):
            return amount / per_purchase
    return amount

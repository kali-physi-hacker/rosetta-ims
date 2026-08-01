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

    offering = _ensure_offering(db, link, now)
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


def _ensure_offering(db: Session, link: models.ProductSupplier, now: str) -> models.SupplierOffering:
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
    return offering


def set_offering_packaging(
    db: Session,
    link: models.ProductSupplier,
    *,
    purchase_uom: str | None,
    sellable_units: float | None,
) -> None:
    """Record this supplier's packaging — what one purchase unit is called and
    how many sell units it contains — on the link's SupplierOffering, the
    domain home for packaging. Supersedes the current packaging row; whichever
    half isn't provided carries forward. No-op when nothing changes or the
    link has no supplier/variant. Does not commit.
    """

    if link.supplier_id is None or link.product_id is None:
        return
    label = purchase_uom.strip() if isinstance(purchase_uom, str) and purchase_uom.strip() else None
    if label is None and sellable_units is None:
        return
    now = _utcnow_iso()
    offering = _ensure_offering(db, link, now)
    current = (
        db.query(models.CataloguePackagingConfiguration)
        .filter_by(supplier_product_id=offering.id, superseded_at=None)
        .order_by(models.CataloguePackagingConfiguration.id.desc())
        .first()
    )
    if label is None and current is not None:
        label = current.purchase_uom_label or current.purchase_uom_code
    units = float(sellable_units) if sellable_units is not None else (
        float(current.sellable_units_per_purchase_unit)
        if current is not None and current.sellable_units_per_purchase_unit is not None
        else None
    )
    if current is not None:
        cur_label = current.purchase_uom_label or current.purchase_uom_code
        cur_units = (
            float(current.sellable_units_per_purchase_unit)
            if current.sellable_units_per_purchase_unit is not None
            else None
        )
        if cur_label == label and cur_units == units:
            return
        current.superseded_at = now
    variant = db.get(models.ProductVariant, link.product_id)
    db.add(
        models.CataloguePackagingConfiguration(
            supplier_product_id=offering.id,
            purchase_uom_code=(label or "PACK").upper().replace(" ", "_"),
            purchase_uom_label=label,
            sellable_unit_uom_code="UNIT",
            sellable_unit_uom_label=(variant.uom if variant else None),
            sellable_units_per_purchase_unit=units,
            created_at=now,
        )
    )
    invalidate(db)


def variant_offerings(db: Session, variant_id: int) -> list[dict]:
    """Read model for the SKU page's supplier-offerings lane.

    One entry per supplier that offers this variant — merged from the legacy
    ProductSupplier links and any SupplierOffering rows. Price history is the
    offering's effective-dated rows, each attributed in plain words:
    'catalogue' when a committed run wrote it, 'manual' otherwise. Lineage ids
    ride along ONLY to power audit links — the page never renders them.
    Every supplier link has an offering price (the domain owns cost), so an
    entry without one means no cost has been recorded for that supplier yet.
    """

    links = (
        db.query(models.ProductSupplier)
        .filter_by(product_id=variant_id)
        .order_by(models.ProductSupplier.is_primary.desc(), models.ProductSupplier.id)
        .all()
    )
    offerings = {
        offering.supplier_id: offering
        for offering in db.query(models.SupplierOffering).filter_by(product_variant_id=variant_id).all()
    }
    supplier_ids = {link.supplier_id for link in links if link.supplier_id} | set(offerings)
    names = {
        supplier.id: supplier
        for supplier in db.query(models.Supplier).filter(models.Supplier.id.in_(supplier_ids)).all()
    } if supplier_ids else {}

    out: list[dict] = []
    seen: set[int] = set()
    for link in links:
        if link.supplier_id is None:
            continue
        seen.add(link.supplier_id)
        out.append(_offering_entry(db, names.get(link.supplier_id), offerings.get(link.supplier_id), link))
    for supplier_id, offering in offerings.items():
        if supplier_id not in seen:
            out.append(_offering_entry(db, names.get(supplier_id), offering, None))
    return out


def _offering_entry(
    db: Session,
    supplier: models.Supplier | None,
    offering: models.SupplierOffering | None,
    link: models.ProductSupplier | None,
) -> dict:
    packaging = None
    history: list[dict] = []
    if offering is not None:
        pack_row = (
            db.query(models.CataloguePackagingConfiguration)
            .filter_by(supplier_product_id=offering.id, superseded_at=None)
            .order_by(models.CataloguePackagingConfiguration.id.desc())
            .first()
        )
        if pack_row is not None:
            packaging = {
                "purchase_uom": pack_row.purchase_uom_label or pack_row.purchase_uom_code,
                "sellable_uom": pack_row.sellable_unit_uom_label or pack_row.sellable_unit_uom_code,
                "sellable_units_per_purchase_unit": float(pack_row.sellable_units_per_purchase_unit) if pack_row.sellable_units_per_purchase_unit is not None else None,
                "content_amount": float(pack_row.content_amount) if pack_row.content_amount is not None else None,
                "content_uom": pack_row.content_uom_label or pack_row.content_uom_code,
            }
        pack_tuple = None
        if pack_row is not None:
            pack_tuple = (
                pack_row.purchase_uom_code,
                pack_row.sellable_unit_uom_code,
                float(pack_row.sellable_units_per_purchase_unit) if pack_row.sellable_units_per_purchase_unit is not None else None,
            )
        price_rows = (
            db.query(models.CatalogueSupplierPrice)
            .filter_by(supplier_product_id=offering.id)
            .order_by(models.CatalogueSupplierPrice.id.desc())
            .all()
        )
        # Which supplier catalogue this price was read out of. "catalogue" as a
        # source word does not say WHICH document, and by the time anyone asks
        # about a cost, that is usually the question.
        scanned = _scanned_files(db, {p.ingestion_run_uuid for p in price_rows if p.ingestion_run_uuid})
        history = [
            {
                "unit_cost": _per_sell_unit(float(price.amount), price.price_basis_uom_code, pack_tuple),
                "amount": float(price.amount),
                "basis": price.price_basis_uom_label or price.price_basis_uom_code,
                "since": price.effective_from or price.created_at,
                "until": price.superseded_at,
                "is_current": bool(price.is_current),
                "source": "catalogue" if price.ingestion_run_uuid else "manual",
                "run_id": price.ingestion_run_uuid,
                "source_file": scanned.get(price.ingestion_run_uuid or "", {}).get("filename"),
                "source_received_at": scanned.get(price.ingestion_run_uuid or "", {}).get("received_at"),
            }
            for price in price_rows
        ]

    current = next((row for row in history if row["is_current"]), None)
    return {
        "supplier_id": (link.supplier_id if link else None) or (offering.supplier_id if offering else None),
        "supplier_name": supplier.name if supplier else None,
        "supplier_code": supplier.code if supplier else None,
        "supplier_sku": (link.supplier_sku if link else None) or (offering.supplier_sku if offering else None),
        "barcode": (link.barcode if link else None) or (offering.barcode if offering else None),
        "source": current["source"] if current else "legacy",
        "current": current,
        "history": history,
        "packaging": packaging,
        "legacy": {
            "units_per_pack": link.units_per_pack if link else None,
            "cost_source": link.cost_source if link else None,
            "cost_updated_at": link.cost_updated_at if link else None,
        },
    }


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


def _scanned_files(db: Session, run_uuids: set[str]) -> dict[str, dict]:
    """run uuid -> the supplier catalogue file that run read.

    A re-parse reads the evidence of an earlier run rather than a file of its
    own, but it is the same document, and the source row is carried across —
    so this resolves for re-parsed runs too.
    """
    if not run_uuids:
        return {}
    rows = (
        db.query(models.IngestionRun.run_uuid, models.CatalogueSourceDocument.filename,
                 models.CatalogueSourceDocument.received_at)
        .join(models.CatalogueSourceDocument,
              models.CatalogueSourceDocument.id == models.IngestionRun.catalogue_source_document_id)
        .filter(models.IngestionRun.run_uuid.in_(run_uuids))
        .all()
    )
    return {run: {"filename": filename, "received_at": received} for run, filename, received in rows}


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

"""Reviewer-facing read models for the HITL surfaces (board / room / commit).

The full intermediate layer ships complete candidate contracts — megabytes per
run. The review UI needs a decision-ready row per candidate plus the sanity
context a reviewer reasons with (price delta vs the current supplier offering,
family evidence for pattern grouping, channel selling price for margin). This
module builds that summary in bulk queries, and powers the run-scoped product
variant picker with the same sanity fields.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID

import re

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

import models
from services import variant_similarity

_MATCHED_VARIANT_STATES = {"PROPOSED_MATCH", "CONFIRMED_MATCH"}
_LEGACY_OFFER_PREFIX = "legacy-product-supplier:"
_PREFERRED_SELLING_CHANNEL = "shopify"


_VALID_SKU = re.compile(r"^\d{6,}$")


def run_review_summary(db: Session, run_uuid: UUID) -> dict[str, Any]:
    run = str(run_uuid)
    candidates = (
        db.query(models.CatalogueMasteringCandidate)
        .filter_by(ingestion_run_uuid=run)
        .order_by(models.CatalogueMasteringCandidate.id)
        .all()
    )

    item_issues, run_issues = _issues_by_item(db, run)
    evidence = _first_evidence_by_uuid(db, run)
    published = _published_candidate_uuids(db, [c.mastering_candidate_uuid for c in candidates])

    parsed = [
        (
            candidate,
            _loads(candidate.supplier_product_resolution_json),
            _loads(candidate.product_variant_resolution_json),
            _loads(candidate.supplier_price_resolution_json),
        )
        for candidate in candidates
    ]

    offer_prices = _current_offer_prices(
        db,
        [offer.get("supplier_product_id") for _, offer, _, _ in parsed if offer.get("supplier_product_id")],
    )
    selling = _selling_prices_by_sku(
        db,
        [
            variant.get("canonical_sku") or variant.get("product_variant_id")
            for _, _, variant, _ in parsed
            if variant.get("state") in _MATCHED_VARIANT_STATES
        ],
    )

    items = []
    by_status: dict[str, int] = {}
    for candidate, offer, variant, price in parsed:
        by_status[candidate.review_status] = by_status.get(candidate.review_status, 0) + 1
        cost = _cost_amount(price)
        current = offer_prices.get(offer.get("supplier_product_id"))
        sku = variant.get("canonical_sku") or variant.get("product_variant_id")
        sell = selling.get(sku) if variant.get("state") in _MATCHED_VARIANT_STATES else None
        issues = item_issues.get(candidate.catalogue_item_uuid, (0, 0))
        page, family = evidence.get(_first_evidence_uuid(candidate), (None, None))
        items.append(
            {
                "mastering_candidate_id": candidate.mastering_candidate_uuid,
                "catalogue_item_id": candidate.catalogue_item_uuid,
                "created_at": candidate.created_at,
                "review_status": candidate.review_status,
                "superseded_by": candidate.superseded_by_uuid,
                "page": page,
                "supplier_sku": offer.get("supplier_sku"),
                "barcode": offer.get("barcode"),
                "name": variant.get("proposed_name") or variant.get("product_variant_name"),
                "cost_amount": cost,
                "cost_currency": (price.get("current_cost") or {}).get("currency"),
                "cost_basis": ((price.get("current_cost") or {}).get("price_basis") or {}).get("label"),
                "offering_state": offer.get("state"),
                "variant_state": variant.get("state"),
                "canonical_sku": variant.get("canonical_sku"),
                "variant_name": variant.get("product_variant_name"),
                # Drafted-to-create rows read very differently from matched
                # ones — they are about to mint a SKU — so the lane can mark
                # them without re-deriving intent from the state name.
                "will_create": variant.get("state") == "CONFIRMED_CREATE" and not variant.get("created_product_sku"),
                "created_product_sku": variant.get("created_product_sku"),
                "draft_name": (variant.get("proposed_variant") or {}).get("name"),
                "draft_category": (variant.get("proposed_variant") or {}).get("category"),
                "family_key": family,
                "open_issues": issues[0],
                "blocking_issues": issues[1],
                "current_cost": current,
                "price_delta_pct": _delta_pct(current, cost),
                "selling_price": sell[0] if sell else None,
                "selling_channel": sell[1] if sell else None,
                "published": candidate.mastering_candidate_uuid in published,
            }
        )

    return {
        "ingestion_run_id": run,
        "layer": "intermediate",
        "view": "summary",
        "counts": {"total": len(items), "by_review_status": by_status},
        "run_issues": run_issues,
        "items": items,
    }


def run_receipt(db: Session, run_uuid: UUID) -> dict[str, Any]:
    """What a run's commits actually wrote — the publish receipt.

    One entry per offering price row this run created: the SKU it landed on,
    the predecessor price it superseded (old -> new), and when. Empty until
    the run's first apply. Costs are per-sell-unit, packaging-aware, matching
    every other read surface.
    """

    from services import offering_costs

    run = str(run_uuid)
    rows = (
        db.query(models.CatalogueSupplierPrice, models.SupplierOffering, models.ProductVariant, models.Supplier)
        .join(models.SupplierOffering, models.CatalogueSupplierPrice.supplier_product_id == models.SupplierOffering.id)
        .outerjoin(models.ProductVariant, models.SupplierOffering.product_variant_id == models.ProductVariant.id)
        .outerjoin(models.Supplier, models.SupplierOffering.supplier_id == models.Supplier.id)
        .filter(models.CatalogueSupplierPrice.ingestion_run_uuid == run)
        .order_by(models.CatalogueSupplierPrice.id)
        .all()
    )
    created = _created_variants(db, run)
    if not rows:
        return {"ingestion_run_id": run, "count": 0, "changes": [], "created": created}

    offering_ids = {offering.id for _, offering, _, _ in rows}
    history: dict[int, list[models.CatalogueSupplierPrice]] = {oid: [] for oid in offering_ids}
    for price in (
        db.query(models.CatalogueSupplierPrice)
        .filter(models.CatalogueSupplierPrice.supplier_product_id.in_(offering_ids))
        .order_by(models.CatalogueSupplierPrice.id)
        .all()
    ):
        history[price.supplier_product_id].append(price)

    packaging: dict[int, tuple[str | None, str | None, float | None]] = {}
    for pack in (
        db.query(models.CataloguePackagingConfiguration)
        .filter(
            models.CataloguePackagingConfiguration.supplier_product_id.in_(offering_ids),
            models.CataloguePackagingConfiguration.superseded_at.is_(None),
        )
        .order_by(models.CataloguePackagingConfiguration.id)
        .all()
    ):
        packaging[pack.supplier_product_id] = (
            pack.purchase_uom_code,
            pack.sellable_unit_uom_code,
            float(pack.sellable_units_per_purchase_unit) if pack.sellable_units_per_purchase_unit is not None else None,
        )

    changes: list[dict[str, Any]] = []
    for price, offering, variant, supplier in rows:
        pack = packaging.get(offering.id)
        new_unit = offering_costs._per_sell_unit(float(price.amount), price.price_basis_uom_code, pack)
        prev = next(
            (p for p in reversed(history[offering.id]) if p.id < price.id),
            None,
        )
        old_unit = (
            offering_costs._per_sell_unit(float(prev.amount), prev.price_basis_uom_code, pack)
            if prev is not None else None
        )
        delta_pct = (
            round((new_unit - old_unit) / old_unit * 100, 1)
            if old_unit not in (None, 0) else None
        )
        changes.append({
            "sku_code": variant.sku_code if variant else None,
            "variant_name": variant.name if variant else None,
            "supplier_name": supplier.name if supplier else None,
            "supplier_sku": offering.supplier_sku,
            "old_unit_cost": old_unit,
            "new_unit_cost": new_unit,
            "delta_pct": delta_pct,
            "written_at": price.created_at,
            "is_current": bool(price.is_current),
        })
    return {
        "ingestion_run_id": run,
        "count": len(changes),
        "changes": changes,
        "created": created,
    }


def _created_variants(db: Session, run: str) -> list[dict[str, Any]]:
    """Products this run brought into existence, with why they were allowed to.

    The whole point of storing `checked_against` and `duplicate_ack` on the
    draft is that six months from now "why does this SKU exist?" has an answer
    in the UI rather than in a decision blob nobody opens.
    """
    out: list[dict[str, Any]] = []
    for candidate in (
        db.query(models.CatalogueMasteringCandidate)
        .filter(models.CatalogueMasteringCandidate.ingestion_run_uuid == run)
        .order_by(models.CatalogueMasteringCandidate.id)
        .all()
    ):
        variant = _loads(candidate.product_variant_resolution_json, {}) or {}
        sku = variant.get("created_product_sku")
        if not sku:
            continue
        draft = variant.get("proposed_variant") or {}
        product = db.query(models.ProductVariant).filter_by(sku_code=sku).first()
        out.append({
            "sku_code": sku,
            "name": product.name if product else draft.get("name"),
            "category": product.category if product else draft.get("category"),
            "brand": product.brand if product else draft.get("brand"),
            "drafted_by": candidate.reviewed_by,
            "decided_at": candidate.reviewed_at,
            "duplicate_ack": draft.get("duplicate_ack"),
            "checked_against": draft.get("checked_against") or [],
        })
    return out


def search_product_variants(db: Session, run_uuid: UUID, query: str, limit: int) -> dict[str, Any]:
    """Product-variant picker scoped to the run's supplier.

    Each result carries the sanity context the room renders: the supplier's
    current offering cost for the variant (SupplierOffering first, legacy
    ProductSupplier cost as fallback) and its channel selling price.
    """

    run = db.query(models.IngestionRun).filter_by(run_uuid=str(run_uuid)).first()
    supplier_id = run.supplier_id if run else None

    # A substring LIKE cannot answer the question the reviewer is actually
    # asking. "GI Biome Chicken" never appears contiguously inside "Hill's
    # Prescription Diet - Wet Cat Food - GI Biome Chicken & Vegetable Stew", so
    # the old search returned nothing and the row looked new. Score by shared
    # distinguishing words instead, and keep the literal LIKE as a floor so
    # typing a SKU or an exact fragment still works.
    scored = variant_similarity.rank_variants(db, query, limit=limit)
    seen = {variant.sku_code for variant, _ in scored}
    scores = {variant.sku_code: score for variant, score in scored}
    variants = [variant for variant, _ in scored]
    if len(variants) < limit:
        needle = f"%{query.lower()}%"
        literal = (
            db.query(models.ProductVariant)
            .filter(
                or_(
                    func.lower(models.ProductVariant.sku_code).like(needle),
                    func.lower(models.ProductVariant.name).like(needle),
                    func.lower(models.ProductVariant.brand).like(needle),
                )
            )
            .order_by(models.ProductVariant.status, models.ProductVariant.name)
            .limit(limit - len(variants))
            .all()
        )
        variants.extend(v for v in literal if v.sku_code not in seen)

    results = []
    for variant in variants:
        offering_cost, offering_source = _supplier_cost_for_variant(db, supplier_id, variant.id)
        sell = _selling_price_for_variant(db, variant.id)
        results.append(
            {
                "sku_code": variant.sku_code,
                "name": variant.name,
                "brand": variant.brand,
                "category": variant.category,
                "species": variant.species,
                "status": variant.status,
                "score": scores.get(variant.sku_code),
                # Some historical rows carry a product name in the SKU column.
                # They are still legitimate match targets, but the reviewer
                # should see what they are picking.
                "sku_valid": bool(_VALID_SKU.match(variant.sku_code or "")),
                "offering_cost": offering_cost,
                "offering_source": offering_source,
                "selling_price": sell[0] if sell else None,
                "selling_channel": sell[1] if sell else None,
            }
        )
    return {"ingestion_run_id": str(run_uuid), "query": query, "results": results}


def candidate_evidence(db: Session, candidate: models.CatalogueMasteringCandidate) -> list[dict[str, Any]]:
    """Verbatim evidence rows behind one candidate, for the review room's
    source panel — cells exactly as printed, with their page location."""

    evidence_uuids = _loads(candidate.raw_observation_ids_json, default=[])
    if not evidence_uuids:
        return []
    rows = (
        db.query(models.CatalogueExtractedEvidence)
        .filter(models.CatalogueExtractedEvidence.raw_observation_uuid.in_(evidence_uuids))
        .order_by(models.CatalogueExtractedEvidence.id)
        .all()
    )
    return [
        {
            "raw_observation_id": row.raw_observation_uuid,
            "page": row.page_number,
            "raw_text": row.raw_text,
            "cells": [
                {"column_name": cell.get("column_name"), "value": cell.get("raw_value")}
                for cell in _loads(row.raw_cells_json, default=[])
            ],
        }
        for row in rows
    ]


def _issues_by_item(db: Session, run: str) -> tuple[dict[str, tuple[int, int]], list[dict[str, Any]]]:
    per_item: dict[str, tuple[int, int]] = {}
    run_scoped: list[dict[str, Any]] = []
    rows = (
        db.query(models.CatalogueValidationIssue)
        .filter_by(ingestion_run_uuid=run)
        .order_by(models.CatalogueValidationIssue.id)
        .all()
    )
    for issue in rows:
        if issue.catalogue_item_uuid is None:
            run_scoped.append(
                {
                    "validation_issue_id": issue.validation_issue_uuid,
                    "issue_code": issue.issue_code,
                    "severity": issue.severity,
                    "message": issue.message,
                    "resolution_status": issue.resolution_status,
                    "publish_blocking": bool(issue.publish_blocking),
                    "review_guidance": issue.review_guidance,
                }
            )
            continue
        if issue.resolution_status != "OPEN":
            continue
        open_count, blocking = per_item.get(issue.catalogue_item_uuid, (0, 0))
        per_item[issue.catalogue_item_uuid] = (open_count + 1, blocking + (1 if issue.publish_blocking else 0))
    return per_item, run_scoped


def _first_evidence_by_uuid(db: Session, run: str) -> dict[str, tuple[int | None, str | None]]:
    """Map evidence UUID -> (page, family evidence text).

    The family key is the verbatim text of the first cell whose column reads
    as a range/family column; identical catalogue rows repeat the exact
    string, which is what pattern grouping clusters on.
    """

    out: dict[str, tuple[int | None, str | None]] = {}
    rows = (
        db.query(
            models.CatalogueExtractedEvidence.raw_observation_uuid,
            models.CatalogueExtractedEvidence.page_number,
            models.CatalogueExtractedEvidence.raw_cells_json,
        )
        .filter_by(ingestion_run_uuid=run)
        .all()
    )
    for uuid_value, page, cells_json in rows:
        family = None
        for cell in _loads(cells_json, default=[]):
            column = (cell.get("column_name") or "").lower()
            if "range" in column or "family" in column:
                value = cell.get("raw_value")
                family = str(value) if value not in (None, "") else None
                break
        out[uuid_value] = (page, family)
    return out


def _published_candidate_uuids(db: Session, candidate_uuids: list[str]) -> set[str]:
    if not candidate_uuids:
        return set()
    rows = (
        db.query(models.CatalogueServingPublication.mastering_candidate_uuid)
        .filter(
            models.CatalogueServingPublication.mastering_candidate_uuid.in_(candidate_uuids),
            models.CatalogueServingPublication.is_current == 1,
        )
        .all()
    )
    return {value for (value,) in rows}


def _current_offer_prices(db: Session, offer_keys: list[str]) -> dict[str, float]:
    """Current supplier cost per resolution supplier_product_id.

    Matched keys are SupplierOffering.supplier_product_key values. The
    resolver can also emit a legacy marker for a ProductSupplier row; those
    now always have an offering price, so they resolve through the link's
    (supplier, variant) pair rather than a cost column of their own.
    """

    prices: dict[str, float] = {}
    offering_keys = [key for key in set(offer_keys) if not key.startswith(_LEGACY_OFFER_PREFIX)]
    if offering_keys:
        rows = (
            db.query(models.SupplierOffering.supplier_product_key, models.CatalogueSupplierPrice.amount)
            .join(
                models.CatalogueSupplierPrice,
                models.CatalogueSupplierPrice.supplier_product_id == models.SupplierOffering.id,
            )
            .filter(
                models.SupplierOffering.supplier_product_key.in_(offering_keys),
                models.CatalogueSupplierPrice.is_current == 1,
            )
            .all()
        )
        prices.update({key: float(amount) for key, amount in rows})
    legacy_ids = {
        int(key[len(_LEGACY_OFFER_PREFIX):]): key
        for key in set(offer_keys)
        if key.startswith(_LEGACY_OFFER_PREFIX) and key[len(_LEGACY_OFFER_PREFIX):].isdigit()
    }
    if legacy_ids:
        rows = (
            db.query(models.ProductSupplier.id, models.CatalogueSupplierPrice.amount)
            .join(
                models.SupplierOffering,
                (models.SupplierOffering.supplier_id == models.ProductSupplier.supplier_id)
                & (models.SupplierOffering.product_variant_id == models.ProductSupplier.product_id),
            )
            .join(
                models.CatalogueSupplierPrice,
                (models.CatalogueSupplierPrice.supplier_product_id == models.SupplierOffering.id)
                & (models.CatalogueSupplierPrice.is_current == 1),
            )
            .filter(models.ProductSupplier.id.in_(legacy_ids.keys()))
            .all()
        )
        prices.update({legacy_ids[row_id]: float(cost) for row_id, cost in rows if cost is not None})
    return prices


def _selling_prices_by_sku(db: Session, sku_codes: list[str]) -> dict[str, tuple[float, str]]:
    skus = [sku for sku in set(sku_codes) if sku]
    if not skus:
        return {}
    rows = (
        db.query(
            models.ProductVariant.sku_code,
            models.SellingItem.channel,
            models.SellingItem.selling_price,
        )
        .join(models.SellingItem, models.SellingItem.product_variant_id == models.ProductVariant.id)
        .filter(models.ProductVariant.sku_code.in_(skus), models.SellingItem.selling_price.isnot(None))
        .all()
    )
    out: dict[str, tuple[float, str]] = {}
    for sku, channel, price in rows:
        if sku not in out or channel == _PREFERRED_SELLING_CHANNEL:
            out[sku] = (float(price), channel)
    return out


def _supplier_cost_for_variant(
    db: Session, supplier_id: int | None, variant_id: int
) -> tuple[float | None, str | None]:
    if supplier_id is None:
        return None, None
    offering = (
        db.query(models.SupplierOffering)
        .filter_by(supplier_id=supplier_id, product_variant_id=variant_id)
        .first()
    )
    if offering is not None:
        price = (
            db.query(models.CatalogueSupplierPrice.amount)
            .filter_by(supplier_product_id=offering.id, is_current=1)
            .first()
        )
        if price is not None:
            return float(price[0]), "offering"
    return None, None


def _selling_price_for_variant(db: Session, variant_id: int) -> tuple[float, str] | None:
    rows = (
        db.query(models.SellingItem.channel, models.SellingItem.selling_price)
        .filter(
            models.SellingItem.product_variant_id == variant_id,
            models.SellingItem.selling_price.isnot(None),
        )
        .all()
    )
    if not rows:
        return None
    for channel, price in rows:
        if channel == _PREFERRED_SELLING_CHANNEL:
            return float(price), channel
    channel, price = rows[0]
    return float(price), channel


def _first_evidence_uuid(candidate: models.CatalogueMasteringCandidate) -> str | None:
    ids = _loads(candidate.raw_observation_ids_json, default=[])
    return ids[0] if ids else None


def _loads(raw: str | None, default: Any | None = None) -> Any:
    try:
        value = json.loads(raw or "")
    except (TypeError, ValueError):
        return {} if default is None else default
    if value is None:
        return {} if default is None else default
    return value


def _cost_amount(price: dict[str, Any]) -> float | None:
    amount = (price.get("current_cost") or {}).get("amount")
    if amount is None:
        return None
    try:
        return float(Decimal(str(amount)))
    except (InvalidOperation, ValueError):
        return None


def _delta_pct(current: float | None, incoming: float | None) -> float | None:
    if current is None or incoming is None or current == 0:
        return None
    return round((incoming - current) / current * 100, 1)

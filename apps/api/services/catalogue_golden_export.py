"""Export a run's published items in the golden-sample column format.

The golden-sample sheet is 122 SKUs that people filled in by hand: for each one,
what the packaging, price basis, sellable unit and bulk terms actually are. It
is the only human-authored ground truth we have for the interpretation half of
the pipeline.

To compare our output against it, ours has to arrive in the same shape. This
emits exactly the sheet's 20 columns, in its order, from the SERVING layer —
the immutable published snapshot, not the working tables — so a regression diff
compares what a run actually put live.

Two deliberate differences from the sheet, both in our favour:

  * bulk terms are rendered from typed rows in ONE canonical phrasing per kind.
    The sheet has 28 distinct free-text shapes for the same four ideas
    ("10+2", "6 BOTTLES = $1200", ">5 BOX = $105 / BOX", "buy 5, get 1 free"),
    so the sheet side needs normalising before a diff, not this side.
  * an empty cell is empty. The sheet writes "N/A" as a string in 100 rows,
    which is not a value and should not read as one.
"""

from __future__ import annotations

import csv
import io
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

import models

# The sheet's headers, verbatim and in order. Changing these breaks the diff,
# so they are asserted against in the tests rather than tidied.
GOLDEN_COLUMNS: tuple[str, ...] = (
    "supplier",
    "supplier_product_code",
    "product_name",
    "product name [Rosetta]",
    "weight",
    "brand",
    "package_configuration",
    "order_multiple",
    "catalogue_price_hkd",
    "catalogue_price_basis_qty",
    "catalogue_price_basis_uom",
    "sellable_qty",
    "sellable_uom",
    "sellable_units_per_price_basis",
    "rrp",
    "mbb_tier_1",
    "mbb_tier_2",
    "mbb_tier_3",
    "mbb_tier_4",
    "commercial_offer_summary",
)

_MAX_MBB_TIERS = 4


def _num(value: Any) -> str:
    """A number without trailing-zero noise: 30.000000 -> 30, 58.6600 -> 58.66."""
    if value is None:
        return ""
    d = Decimal(str(value))
    d = d.quantize(Decimal(1)) if d == d.to_integral_value() else d.normalize()
    return format(d, "f")


def _money(value: Any, currency: str | None = None) -> str:
    """The sheet's money format: $1,390.00. Currency shown only when not HKD."""
    if value is None:
        return ""
    amount = f"${Decimal(str(value)):,.2f}"
    return amount if not currency or currency.upper() == "HKD" else f"{currency.upper()} {amount}"


def _uom(code: str | None, label: str | None) -> str:
    """Prefer the supplier's own word; fall back to the contract code."""
    for candidate in (label, code):
        text = (candidate or "").strip()
        if text and text.upper() not in {"#N/A", "N/A", "NA", "-"}:
            return text
    return ""


def _packaging_text(pack: models.CataloguePackagingConfiguration | None) -> str:
    """"30 ML / BOTTLE" — the sheet's smallest-packaging phrasing.

    Returns "" unless the pipeline actually resolved a PACK. A row that only
    carries the content measure answers "how big is one can", not "what do I
    buy" — emitting "2.9 OZ" where the human wrote "24 CANS / BAG" looks like
    a disagreement when it is really a missing value, and it hides the product
    identity that does know the answer.
    """
    if pack is None:
        return ""
    if pack.purchase_uom_code is None and pack.sellable_units_per_purchase_unit is None:
        return ""
    inner = _uom(pack.content_uom_code, pack.content_uom_label) or _uom(
        pack.sellable_unit_uom_code, pack.sellable_unit_uom_label
    )
    outer = _uom(pack.purchase_uom_code, pack.purchase_uom_label)
    qty = _num(pack.content_amount) or _num(pack.sellable_units_per_purchase_unit)
    if qty and inner and outer:
        return f"{qty} {inner} / {outer}"
    return " / ".join(part for part in (f"{qty} {inner}".strip(), outer) if part)


def _order_multiple_text(pack: models.CataloguePackagingConfiguration | None, variant=None) -> str:
    if pack is None or pack.order_increment_amount is None:
        return ""
    unit = _uom(pack.order_increment_uom_code, pack.order_increment_uom_label)
    # "UNIT" is the contract's placeholder for "a sellable one of these"; the
    # identity knows which noun that is.
    if unit.upper() == "UNIT":
        unit = _clean(variant.uom if variant else None) or unit
    return " ".join(part for part in (_num(pack.order_increment_amount), unit) if part)


def _basis_uom(pub, variant) -> str:
    """What the quoted price is per.

    The contract records the generic code UNIT when the price is per sellable
    unit; the sheet names the noun ("CAN", "BAG"). Both say the same thing, so
    resolve the placeholder against the product identity.
    """
    basis = _uom(pub.current_approved_cost_basis_uom_code, pub.current_approved_cost_basis_uom_label)
    if basis.upper() == "UNIT":
        return _clean(variant.uom if variant else None) or basis
    return basis


def _identity_order_multiple(variant, link) -> str:
    """The supplier link's ordering terms, which are kept separate from pack size."""
    # The order multiple IS the minimum: 24s means the smallest order is 24.
    if link is not None and link.order_increment_qty:
        unit = _clean(link.order_increment_uom) or _clean(variant.uom if variant else None)
        return " ".join(part for part in (_num(link.order_increment_qty), unit) if part)
    if link is not None and link.minimum_order_qty:
        unit = _clean(link.minimum_order_uom) or _clean(variant.uom if variant else None)
        return " ".join(part for part in (_num(link.minimum_order_qty), unit) if part)
    if variant is not None and variant.min_purchase_qty:
        return " ".join(part for part in (_num(variant.min_purchase_qty), _clean(variant.pack_unit)) if part)
    return ""


def _mbb_text(term: models.CatalogueSupplierMbbTerm) -> str:
    """One canonical phrasing per typed kind.

    The sheet says the same thing eight ways; a regression diff needs one. The
    typed columns are the authority, so the sentence is generated from them.
    """
    qty = _num(term.condition_quantity_amount)
    qty_uom = _uom(term.condition_quantity_uom_code, term.condition_quantity_uom_label)

    if term.benefit_type == "free_quantity":
        free = _num(term.free_quantity_amount)
        return f"buy {qty} get {free} free" if qty and free else (term.description or "")
    if term.benefit_type == "discounted_unit_price":
        price = _money(term.discounted_price_amount, term.discounted_price_currency)
        basis = _uom(term.discounted_price_basis_uom_code, term.discounted_price_basis_uom_label)
        if term.condition_type == "minimum_spend":
            return f"spend {_money(term.condition_spend_amount, term.condition_spend_currency)} at {price}"
        head = f"buy {qty} {qty_uom}".strip() if qty else "buy"
        return f"{head} at {price} per {basis}" if basis else f"{head} at {price}"
    if term.benefit_type == "percentage_discount":
        pct = _num(term.percentage_discount)
        if term.condition_type == "minimum_spend":
            return f"spend {_money(term.condition_spend_amount, term.condition_spend_currency)} for {pct}% off"
        return f"buy {qty} {qty_uom}".strip() + f" for {pct}% off" if qty else f"{pct}% off"
    if term.benefit_type == "fixed_discount":
        cut = _money(term.fixed_discount_amount, term.fixed_discount_currency)
        return f"buy {qty} {qty_uom}".strip() + f" for {cut} off" if qty else f"{cut} off"
    return term.description or ""


# weight_g is canonical grams; weight_unit is only how the SOURCE displayed it.
# Printing the gram figure beside that unit asserts "1588 lb" for a 3.5 lb bag.
_GRAMS_PER = {"g": Decimal(1), "kg": Decimal(1000), "lb": Decimal("453.59237"), "oz": Decimal("28.349523")}


def _weight_text(variant: models.ProductVariant | None) -> str:
    if variant is None or variant.weight_g is None:
        return ""
    unit = (variant.weight_unit or "g").strip().lower()
    per = _GRAMS_PER.get(unit)
    if per is None:
        return f"{_num(variant.weight_g)} g"
    converted = (Decimal(str(variant.weight_g)) / per).quantize(Decimal("0.001")).normalize()
    return f"{format(converted, 'f')} {unit}"


def _units_per_pack(variant, link) -> str:
    """How many sellable units one purchase covers.

    `units_per_pack` when the link records a real pack size. Otherwise the ORDER
    MULTIPLE: if you can only order in 24s then 24 is the starting quantity —
    the minimum order is a 24-unit pack whether or not anyone wrote "BAG" on it,
    so that is the number a per-unit cost has to divide by. This is exactly the
    Hill's GI Biome case: we hold order_increment_qty 24 and units_per_pack 1,
    and the human wrote 24.
    """
    if link is not None and link.units_per_pack and link.units_per_pack > 1:
        return _num(link.units_per_pack)
    for candidate in (
        getattr(link, "order_increment_qty", None) if link else None,
        getattr(link, "minimum_order_qty", None) if link else None,
        variant.min_purchase_qty if variant else None,
    ):
        if candidate and candidate > 1:
            return _num(candidate)
    return _num(link.units_per_pack) if link and link.units_per_pack else ""


def _identity_packaging(variant, link) -> str:
    """"24 Can(s) / Box" from the product identity itself.

    products.uom is the sell unit and products.pack_unit the buy unit — the two
    halves of the sheet's package_configuration — and the count comes from
    _units_per_pack. No contract change or re-extraction needed to read them.
    """
    inner = _clean(variant.uom if variant else None)
    outer = _clean(variant.pack_unit if variant else None)
    qty = _units_per_pack(variant, link)
    if qty and inner and outer:
        return f"{qty} {inner} / {outer}"
    if qty and inner:
        return f"{qty} {inner}"
    return " / ".join(part for part in (inner, outer) if part)


def _clean(raw: str | None) -> str:
    text = (raw or "").strip()
    return "" if text.upper() in {"#N/A", "N/A", "NA", "-", ""} else text


def _raw_supplier_names(db: Session, run: str) -> dict[str, str]:
    """The supplier's OWN description per catalogue item.

    The sheet's `product_name` is what the supplier called it; `product name
    [Rosetta]` is what we decided to call it. Keeping both is the point — a
    diff of the second is a diff of our naming, and it is meaningless if the
    first is a copy of it. The raw text is already on the normalized row.
    """
    import json as _json

    out: dict[str, str] = {}
    for row in (
        db.query(models.CatalogueNormalizedRow)
        .filter(models.CatalogueNormalizedRow.ingestion_run_uuid == run)
        .all()
    ):
        try:
            raw = _json.loads(row.raw_fields_json or "{}")
        except ValueError:
            continue
        for key in ("product_name", "description", "name", "product_description"):
            value = raw.get(key)
            if isinstance(value, str) and value.strip():
                out[row.catalogue_item_uuid] = value.strip()
                break
    return out


def golden_rows(db: Session, run_uuid: UUID) -> list[dict[str, str]]:
    """One row per item this run published, in the sheet's columns."""
    run = str(run_uuid)
    publications = (
        db.query(models.CatalogueServingPublication)
        .filter(
            models.CatalogueServingPublication.is_current == 1,
            models.CatalogueServingPublication.mastering_candidate_uuid.in_(
                db.query(models.CatalogueMasteringCandidate.mastering_candidate_uuid)
                .filter(models.CatalogueMasteringCandidate.ingestion_run_uuid == run)
            ),
        )
        .order_by(models.CatalogueServingPublication.id)
        .all()
    )
    if not publications:
        return []

    offering_ids = {p.supplier_product_id for p in publications if p.supplier_product_id}
    packaging: dict[int, models.CataloguePackagingConfiguration] = {}
    for pack in (
        db.query(models.CataloguePackagingConfiguration)
        .filter(
            models.CataloguePackagingConfiguration.supplier_product_id.in_(offering_ids),
            models.CataloguePackagingConfiguration.superseded_at.is_(None),
        )
        .order_by(models.CataloguePackagingConfiguration.id)
        .all()
    ):
        packaging[pack.supplier_product_id] = pack

    terms: dict[int, list[models.CatalogueSupplierMbbTerm]] = {}
    for term in (
        db.query(models.CatalogueSupplierMbbTerm)
        .filter(
            models.CatalogueSupplierMbbTerm.supplier_product_id.in_(offering_ids),
            models.CatalogueSupplierMbbTerm.is_active == 1,
        )
        .order_by(models.CatalogueSupplierMbbTerm.id)
        .all()
    ):
        terms.setdefault(term.supplier_product_id, []).append(term)

    suppliers = {s.id: s for s in db.query(models.Supplier).all()}
    variants = {
        v.id: v
        for v in db.query(models.ProductVariant)
        .filter(models.ProductVariant.id.in_({p.product_id for p in publications if p.product_id}))
        .all()
    }
    links = {
        (link.supplier_id, link.product_id): link
        for link in db.query(models.ProductSupplier).all()
    }
    raw_names = _raw_supplier_names(db, run)
    candidate_items = {
        c.mastering_candidate_uuid: c.catalogue_item_uuid
        for c in db.query(models.CatalogueMasteringCandidate)
        .filter(models.CatalogueMasteringCandidate.ingestion_run_uuid == run)
        .all()
    }

    rows: list[dict[str, str]] = []
    for pub in publications:
        pack = packaging.get(pub.supplier_product_id) if pub.supplier_product_id else None
        variant = variants.get(pub.product_id) if pub.product_id else None
        supplier = suppliers.get(pub.supplier_id)
        mbb = [_mbb_text(t) for t in terms.get(pub.supplier_product_id, [])] if pub.supplier_product_id else []
        mbb = [text for text in mbb if text][:_MAX_MBB_TIERS]

        weight = _weight_text(variant)

        link = links.get((pub.supplier_id, pub.product_id))
        item_uuid = candidate_items.get(pub.mastering_candidate_uuid)
        canonical_name = (variant.name if variant else pub.product_variant_name) or ""
        row = {
            "supplier": (supplier.name if supplier else "") or "",
            "supplier_product_code": pub.supplier_sku or "",
            # The supplier's own words, not ours — see _raw_supplier_names.
            "product_name": raw_names.get(item_uuid or "", "") or canonical_name,
            "product name [Rosetta]": canonical_name,
            "weight": weight,
            "brand": (variant.brand if variant else "") or "",
            # The pipeline's packaging when it resolved one, else the product
            # identity, which carries the same three facts.
            "package_configuration": _packaging_text(pack) or _identity_packaging(variant, link),
            "order_multiple": _order_multiple_text(pack, variant) or _identity_order_multiple(variant, link),
            "catalogue_price_hkd": _money(pub.current_approved_cost_amount, pub.current_approved_cost_currency),
            # Every price in this pipeline is quoted for one basis unit; the
            # sheet's column is 1 on all 120 filled rows for the same reason.
            "catalogue_price_basis_qty": "1",
            "catalogue_price_basis_uom": _basis_uom(pub, variant),
            "sellable_qty": "1",
            "sellable_uom": (
                _uom(pack.sellable_unit_uom_code, pack.sellable_unit_uom_label) if pack else ""
            ) or _clean(variant.uom if variant else None),
            "sellable_units_per_price_basis": (
                (_num(pack.sellable_units_per_purchase_unit) if pack else "")
                or _units_per_pack(variant, link)
            ),
            # products.rrp is the one that is actually populated; the per-supplier
            # column exists but is null on every published row of this run.
            "rrp": _money(
                (link.rrp if link and link.rrp is not None else None)
                if (link and link.rrp is not None) else (variant.rrp if variant else None)
            ),
            "commercial_offer_summary": "; ".join(mbb),
        }
        for index in range(_MAX_MBB_TIERS):
            row[f"mbb_tier_{index + 1}"] = mbb[index] if index < len(mbb) else ""
        rows.append({column: row.get(column, "") for column in GOLDEN_COLUMNS})
    return rows


def golden_csv(db: Session, run_uuid: UUID) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(GOLDEN_COLUMNS), lineterminator="\n")
    writer.writeheader()
    writer.writerows(golden_rows(db, run_uuid))
    return buffer.getvalue()

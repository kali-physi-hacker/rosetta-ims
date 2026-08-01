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
    """"30 ML / BOTTLE" — the sheet's smallest-packaging phrasing."""
    if pack is None:
        return ""
    inner = _uom(pack.content_uom_code, pack.content_uom_label) or _uom(
        pack.sellable_unit_uom_code, pack.sellable_unit_uom_label
    )
    outer = _uom(pack.purchase_uom_code, pack.purchase_uom_label)
    qty = _num(pack.content_amount) or _num(pack.sellable_units_per_purchase_unit)
    if qty and inner and outer:
        return f"{qty} {inner} / {outer}"
    return " / ".join(part for part in (f"{qty} {inner}".strip(), outer) if part)


def _order_multiple_text(pack: models.CataloguePackagingConfiguration | None) -> str:
    if pack is None or pack.order_increment_amount is None:
        return ""
    unit = _uom(pack.order_increment_uom_code, pack.order_increment_uom_label)
    return " ".join(part for part in (_num(pack.order_increment_amount), unit) if part)


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
    rrp_by_link = {
        (link.supplier_id, link.product_id): link.rrp
        for link in db.query(models.ProductSupplier).all()
        if link.rrp is not None
    }

    rows: list[dict[str, str]] = []
    for pub in publications:
        pack = packaging.get(pub.supplier_product_id) if pub.supplier_product_id else None
        variant = variants.get(pub.product_id) if pub.product_id else None
        supplier = suppliers.get(pub.supplier_id)
        mbb = [_mbb_text(t) for t in terms.get(pub.supplier_product_id, [])] if pub.supplier_product_id else []
        mbb = [text for text in mbb if text][:_MAX_MBB_TIERS]

        weight = _weight_text(variant)

        row = {
            "supplier": (supplier.name if supplier else "") or "",
            "supplier_product_code": pub.supplier_sku or "",
            # The supplier's own words are the extractor's input; the Rosetta
            # name is what we decided to call it. The sheet keeps both, and a
            # diff of the second is a diff of the naming decision.
            "product_name": (pub.product_variant_name or "") if variant is None else (variant.name or ""),
            "product name [Rosetta]": (variant.name if variant else pub.product_variant_name) or "",
            "weight": weight,
            "brand": (variant.brand if variant else "") or "",
            "package_configuration": _packaging_text(pack),
            "order_multiple": _order_multiple_text(pack),
            "catalogue_price_hkd": _money(pub.current_approved_cost_amount, pub.current_approved_cost_currency),
            # Every price in this pipeline is quoted for one basis unit; the
            # sheet's column is 1 on all 120 filled rows for the same reason.
            "catalogue_price_basis_qty": "1",
            "catalogue_price_basis_uom": _uom(
                pub.current_approved_cost_basis_uom_code, pub.current_approved_cost_basis_uom_label
            ),
            "sellable_qty": "1",
            "sellable_uom": (
                _uom(pack.sellable_unit_uom_code, pack.sellable_unit_uom_label) if pack else ""
            ) or ((variant.uom if variant else "") or ""),
            "sellable_units_per_price_basis": _num(pack.sellable_units_per_purchase_unit) if pack else "",
            "rrp": _money(rrp_by_link.get((pub.supplier_id, pub.product_id))),
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

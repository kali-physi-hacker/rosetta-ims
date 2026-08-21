"""The ops-DB row for every PUBLISHED catalogue product.

One row per serving publication, in the column contract agreed with BizOps: the
supplier's own facts, what one unit costs, every deal both stores hold, and the
selling price and margin per channel.

Two things this deliberately refuses rather than guesses. A cost per unit needs
a named countable unit to divide by, so a container whose contents nobody
printed stays blank instead of passing off a case price as a unit price. And a
deal price that cannot be reconciled to one unit — a basis that lines up with
nothing, or a hand-entered price that is really the base spread across a
minimum order — is left empty rather than published as a margin nobody can
achieve.
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

import sqlalchemy

import models
from services.catalogue_golden_export import (
    _identity_packaging,
    _mbb_text,
    _num,
    _order_multiple_text,
    _packaging_text,
    _uom,
)

BENEFIT_LABEL = {
    "discounted_unit_price": "BULK_PRICE",
    "percentage_discount": "PERCENT_OFF",
    "fixed_discount": "AMOUNT_OFF",
    "free_quantity": "FREE_ITEMS",
}
SCOPE_LABEL = {
    "SUPPLIER_SKU": "PRODUCT",
    "PRODUCT_GROUP": "PRODUCT_GROUP",
    "SUPPLIER_ORDER": "WHOLE_ORDER",
}
REDUCTION_LABEL = {
    "UNIT_PRICE": "unit price",
    "SUPPLIER_SKU_TOTAL": "product total",
    "SUPPLIER_ORDER_TOTAL": "order total",
}
SLOTS = 4
# Selling prices differ per sales channel, so margin is per channel too, and so
# are the fee inputs behind it — HKTVmall's commission is nothing like a clinic's.
CHANNELS = ("shopify", "hktvm", "daysmart")
# The ops column names the user asked for, mapped to the channel values the
# system actually stores.
CHANNEL_SOURCE = {"shopify": "shopify", "hktvm": "hktv", "daysmart": "clinic"}
SLOT_FIELDS = (("type", "applies_to", "min_qty", "min_qty_uom", "min_spend", "qty_to_qualify", "value",
                "value_uom", "text", "cost_per_unit")
               + tuple(f"{c}_{m}" for c in CHANNELS for m in ("gross_margin", "net_margin")))
# Margin needs selling prices and fees that ops owns, not the catalogue. Those
# columns ship empty until the inputs land rather than carrying a guessed number.
CHANNEL_INPUT_COLUMNS = tuple(
    f"{prefix}_{c}" for c in CHANNELS
    for prefix in ("selling_price", "selling_price_uom_SPLIT", "logistics_cost_per_unit", "platform_fee_percent")
)
BASE_COLUMNS = (
    "supplier", "supplier_product_code", "barcode", "product_name_supplier", "product_name_rosetta",
    "brand", "weight_value", "weight_unit", "weight_grams", "purchase_uom", "sellable_uom",
    "sellable_units_per_purchase_unit", "content_amount", "content_uom",
    "packaging_text", "order_increment_amount", "order_increment_uom", "minimum_order_amount",
    "minimum_order_uom", "order_multiple_text", "cost_price_amount", "cost_price_currency",
    "cost_price_basis_uom", "rrp_amount", "rrp_currency", "cost_per_unit",
) + tuple(
    column
    for channel in CHANNELS
    for column in (
        f"selling_price_{channel}",
        f"selling_price_{channel}_uom",
        f"logistics_cost_per_unit_{channel}",
        f"platform_fee_percent_{channel}",
        f"{channel}_gross_margin",
        f"{channel}_net_margin",
    )
) + (
    "number_of_deals", "has_order_level_promo", "commercial_offer_summary",
)
OPS_COLUMNS = list(BASE_COLUMNS) + [f"mbb_tier_{n}_{f}" for n in range(1, SLOTS + 1) for f in SLOT_FIELDS]

# A cost can only be divided by a COUNT of countable things. "15 ML / BOTTLE"
# carries 15 in the units-per-purchase field, but 15 is millilitres, so the
# division would state a per-millilitre rate under a per-item heading.
MEASURE_CODES = {"ML", "L", "G", "KG", "OZ", "LB"}
# Generic "each" words. A deal quoted "per PIECE" on a row packed 90 CAPSULE /
# BOTTLE prices the bottle you buy, not the capsule — $560 is plainly a bottle
# price beside a $7.22 capsule cost.
GENERIC_UNITS = {"UNIT", "PIECE", "EACH", "PCS", "PC"}

_GRAMS_PER = {"g": Decimal(1), "kg": Decimal(1000), "lb": Decimal("453.59237"), "oz": Decimal("28.349523")}


def _dec(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _bool(value):
    if value is None:
        return ""
    return "TRUE" if value else "FALSE"


def _pct(value):
    """A margin as a percentage, two decimals."""
    return _num((value * Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _load_channels(db):
    """Selling price, fee and sold-unit per product, per channel.

    product_channels carries the price and the platform's cut; selling_items
    carries the unit the listing is sold in. Keyed by product id, which is what
    both a publication and a resolved candidate hold.
    """
    prices: dict = {}
    for row in db.execute(
        sqlalchemy.text(
            "SELECT product_id, channel, selling_price, channel_fee_pct, units_per_listing FROM product_channels"
        )
    ):
        prices[(row[0], row[1])] = {
            "price": _dec(row[2]), "fee": _dec(row[3]), "units_per_listing": _dec(row[4])
        }
    for row in db.execute(
        sqlalchemy.text("SELECT product_variant_id, channel, sell_uom, selling_price FROM selling_items")
    ):
        entry = prices.setdefault((row[0], row[1]), {"price": _dec(row[3]), "fee": None, "units_per_listing": None})
        entry["uom"] = row[2]
        if entry.get("price") is None:
            entry["price"] = _dec(row[3])
    return prices


def _load_delivery_inputs(db):
    """What the courier tier table needs: shipping weight and pack size.

    A multi-unit pack ships as ONE parcel, so the fee is charged on the pack's
    weight and split across the units in it — charging a full parcel rate to
    each pouch in a box of twelve is what used to show impossible losses.
    """
    inputs: dict = {}
    for product_id, weight_g, units_per_pack in db.execute(sqlalchemy.text(
        "SELECT p.id, p.weight_g,"
        " (SELECT ps.units_per_pack FROM product_suppliers ps"
        "   WHERE ps.product_id = p.id ORDER BY ps.is_primary DESC, ps.id LIMIT 1)"
        " FROM products p WHERE p.weight_g IS NOT NULL"
    )):
        inputs[product_id] = (weight_g, units_per_pack)
    return inputs


def _logistics(channel_key, product_id, delivery_inputs):
    """Delivery attributed to one sellable unit, per the channel's own model.

    Shopify pays SF Express by weight. HKTVmall and the clinic do not pay per
    unit — that is a known zero, not a missing figure, so it is stated. A
    Shopify row with no weight on file stays blank: the courier rate cannot be
    read without one.
    """
    if channel_key != "shopify":
        return Decimal(0)
    weight_g, units_per_pack = (delivery_inputs or {}).get(product_id, (None, None))
    if not weight_g:
        return None
    from services import pricing_service

    return _dec(pricing_service._pack_sell_unit_delivery(float(weight_g), units_per_pack))


def _hktv_default_fee():
    """HKTVmall's standard commission, used where a listing states none.

    The app already falls back to it when costing, so a sheet that left the fee
    blank would quietly report a better net margin than the same product shows
    in Rosetta.
    """
    from services import transform_engine

    try:
        fee = transform_engine.get_param("hktv_fee")
    except Exception:
        return None
    return _dec(fee)


def _load_legacy_terms(db):
    """Hand-entered deals, keyed the same way channels are.

    The business keeps deals in two places: the pipeline's typed terms on the
    offering, and terms typed by hand onto the supplier link. `pricing_service.
    best_mbb` reads both on purpose — a "best bulk cost" that saw only half of
    them would contradict the table beside it — so this export reads both too.
    """
    rows = db.execute(sqlalchemy.text(
        "SELECT ps.supplier_id, ps.supplier_sku, m.kind, m.min_qty, m.min_spend,"
        "       m.free_qty, m.discount_pct, m.unit_cost, m.note"
        "  FROM mbb_terms m JOIN product_suppliers ps ON ps.id = m.product_supplier_id"
        " WHERE ps.supplier_sku IS NOT NULL"
    )).all()
    by_code: dict = {}
    for supplier_id, sku, *rest in rows:
        by_code.setdefault(str(sku).strip().upper(), []).append((supplier_id, rest))

    index: dict = {}
    for code, entries in by_code.items():
        suppliers = {supplier_id for supplier_id, _ in entries}
        for supplier_id, rest in entries:
            term = _legacy_term(*rest)
            if term is None:
                continue
            index.setdefault((supplier_id, code), []).append(term)
            # Same reasoning as the channel index: a link row with no supplier
            # still identifies the product when its code is unshared.
            if supplier_id is None and len(suppliers) == 1:
                index.setdefault((None, code), []).append(term)
    return index


def _legacy_term(kind, min_qty, min_spend, free_qty, discount_pct, unit_cost, note):
    """One hand-entered deal in the same shape a pipeline term arrives in."""
    term = {
        "scope": "SUPPLIER_SKU",
        "benefit_type": None,
        "min_qty": _dec(min_qty),
        "min_qty_uom": "",
        "min_spend": _dec(min_spend),
        "price_amount": None, "price_currency": "HKD", "price_basis": "",
        "percent": None, "fixed_amount": None, "fixed_currency": None, "reduction_basis": None,
        "free_qty": _dec(free_qty), "free_qty_uom": "",
        # tier and flat_unit_cost store a price that is ALREADY per sellable
        # unit, so it must not be divided by the pack size a second time.
        "unit_priced": False,
        "text": "",
    }
    if kind in ("tier", "flat_unit_cost"):
        if unit_cost is None:
            return None
        term.update(benefit_type="discounted_unit_price", price_amount=_dec(unit_cost), unit_priced=True)
        term["min_qty"] = term["min_qty"] or Decimal(1)
    elif kind == "buy_x_get_y":
        if not (min_qty and free_qty):
            return None
        term.update(benefit_type="free_quantity")
    elif kind == "spend_discount":
        if discount_pct is None:
            return None
        # Recorded as a fraction (0.1 = 10% off).
        term.update(benefit_type="percentage_discount", percent=_dec(discount_pct) * Decimal(100))
        # Classified the way the pipeline classifies the same business term:
        # "spend $X for Y% off" is an order-value promotion.
        term["scope"] = "SUPPLIER_ORDER"
    else:
        return None

    term["text"] = _mbb_text(models.CatalogueSupplierMbbTerm(
        scope=term["scope"],
        condition_type="minimum_spend" if term["min_spend"] is not None else "minimum_quantity",
        condition_quantity_amount=term["min_qty"],
        condition_spend_amount=term["min_spend"],
        condition_spend_currency="HKD" if term["min_spend"] is not None else None,
        benefit_type=term["benefit_type"],
        discounted_price_amount=term["price_amount"],
        discounted_price_currency="HKD" if term["price_amount"] is not None else None,
        percentage_discount=term["percent"],
        free_quantity_amount=term["free_qty"],
        description=note,
    ))
    return term


def _supplier_sku_index(db):
    """(supplier, their product code) -> our product.

    The business key. A supplier's own code is how "their item" and "our
    product" are tied together, and it holds whether the pipeline matched an
    existing product or minted a new one — which a golden replay always does,
    leaving the fresh record listed on no channel even though the real product
    is listed on several.
    """
    paired: dict = {}
    by_code: dict = {}
    for product_id, supplier_id, sku in db.execute(
        sqlalchemy.text(
            "SELECT product_id, supplier_id, supplier_sku FROM product_suppliers WHERE supplier_sku IS NOT NULL"
        )
    ):
        code = str(sku).strip().upper()
        if supplier_id is not None:
            paired[(supplier_id, code)] = product_id
        by_code.setdefault(code, set()).add(product_id)
    # Some link rows carry no supplier at all. Their code still identifies the
    # product when it points at exactly one — where a code is shared, it names
    # nothing in particular and is left alone.
    for code, product_ids in by_code.items():
        if len(product_ids) == 1:
            paired.setdefault((None, code), next(iter(product_ids)))
    return paired


def _sku_index(db):
    """products.sku_code -> products.id, the key a candidate actually carries."""
    return {
        str(code): pid
        for pid, code in db.execute(sqlalchemy.text("SELECT id, sku_code FROM products WHERE sku_code IS NOT NULL"))
    }


def _fill_channels(row, product_id, channel_data, delivery_inputs=None):
    """Fill every channel column, and the margins the inputs actually support."""
    slots = [("", "cost_per_unit")] + [(f"mbb_tier_{n}_", f"mbb_tier_{n}_cost_per_unit") for n in range(1, SLOTS + 1)]
    for key in CHANNELS:
        entry = channel_data.get((product_id, CHANNEL_SOURCE[key])) or {}
        price, fee, per_listing = entry.get("price"), entry.get("fee"), entry.get("units_per_listing")
        if fee is None:
            # Each channel's charge follows its own model, the same one the app
            # costs with: HKTVmall takes a commission (its own, else the
            # standard one), Shopify and the clinic take none. A known zero is
            # stated so the net margin can be computed at all.
            fee = _hktv_default_fee() if key == "hktvm" else Decimal(0)
        logistics = _logistics(key, product_id, delivery_inputs)
        # A price quoted per millilitre cannot be weighed against a cost per
        # bottle. Where the channel sells by a MEASURE and we do not cost by
        # that same measure, the two numbers count different things and any
        # margin drawn from them is arithmetic on unlike units. The price still
        # shows — it is a fact — but no margin is asserted.
        channel_uom = (entry.get("uom") or "").strip().upper()
        comparable = not (channel_uom in MEASURE_CODES
                          and channel_uom != (row.get("sellable_uom") or "").strip().upper())
        # A listing that states how many units it holds is divided down to one;
        # with nothing stated, one listing is one unit.
        unit_price = price / per_listing if price is not None and per_listing and per_listing > 0 else price

        row[f"selling_price_{key}"] = _num(price) if price is not None else ""
        row[f"selling_price_{key}_uom"] = entry.get("uom") or ""
        row[f"logistics_cost_per_unit_{key}"] = _num(logistics) if logistics is not None else ""
        row[f"platform_fee_percent_{key}"] = _pct(fee) if fee is not None else ""

        for prefix, cost_column in slots:
            cost = _dec(row.get(cost_column))
            gross = net = ""
            if unit_price and unit_price > 0 and cost is not None and comparable:
                gross = _pct((unit_price - cost) / unit_price)
                # Net needs BOTH charges known. A blank one is not treated as
                # zero — that would report a better margin than the product
                # earns, which is the direction that costs money.
                if fee is not None and logistics is not None:
                    net = _pct((unit_price - cost - unit_price * fee - logistics) / unit_price)
            row[f"{prefix}{key}_gross_margin"] = gross
            row[f"{prefix}{key}_net_margin"] = net
    return row


def _unit_divisor(sellable_uom, purchase_uom, pack_count, basis_uom):
    """How many sellable units one priced purchase covers.

    Costs are stated per single unit even when the catalogue prices per pack,
    so this is the number the pack price is divided by. A price already quoted
    per UNIT divides by one; buying the thing you sell divides by one; a box of
    100 tablets divides by 100.

    The count must be a genuine pack content. An ORDER MULTIPLE is not one —
    "min. 24 cans" means you buy 24 cans at the can price, not a 24-can pack for
    the price of one, and dividing by it understated the cost 24-fold.
    """
    if basis_uom and basis_uom.upper() == "UNIT":
        return Decimal(1)
    if not purchase_uom:
        # Nothing says the price buys a container, so it buys one of whatever
        # is sold. Dividing by one asserts nothing the catalogue did not.
        return Decimal(1)
    if sellable_uom and sellable_uom.upper() == purchase_uom.upper():
        return Decimal(1)
    count = _dec(pack_count)
    # A container whose contents nobody printed: the per-unit cost is genuinely
    # unknown and stays blank rather than passing off a case price as a unit one.
    return count if count and count > 0 else None


def _is_base_spread_over_a_minimum(term, base_unit_cost) -> bool:
    """Is this "deal" just the normal price divided by the minimum order?

    Some hand-entered rows hold the base price spread across the minimum order
    instead of a real per-unit deal price — the same defect as DEV-211, one
    store over. Hill's 3392 is the case: you must buy 24 cans at $16.80 each,
    and someone recorded $0.66, which is $16.80 spread over the 24.

    It shows as price x min_qty landing back on the base cost, which a genuine
    bulk price never does — a real deal is CHEAPER than the base, so its total
    across the same quantity comes out well below.
    """
    if not term.get("unit_priced"):
        return False
    price, qty = term["price_amount"], term["min_qty"]
    if price is None or not base_unit_cost or not qty or qty <= 1:
        return False
    return abs(price * qty - base_unit_cost) <= base_unit_cost * Decimal("0.2")


def _deal_unit_cost(term, base_unit_cost, sellable_uom, purchase_uom, divisor):
    """What one unit costs under this deal, always per single unit."""
    kind = BENEFIT_LABEL.get(term["benefit_type"])
    if kind == "BULK_PRICE":
        price, basis = term["price_amount"], (term["price_basis"] or "")
        if price is None:
            return None
        if term.get("unit_priced"):
            # Recorded per sellable unit already; dividing again would restate
            # a unit price as a fraction of itself.
            return price
        if sellable_uom and basis.upper() == sellable_uom.upper():
            return price
        buys_a_purchase_unit = (
            not basis
            or basis.upper() in GENERIC_UNITS
            # A row whose own unit is unnamed cannot contradict the deal: both
            # are naming the one thing this row sells.
            or (sellable_uom or "").upper() in GENERIC_UNITS
            or (purchase_uom and basis.upper() == purchase_uom.upper())
        )
        if buys_a_purchase_unit:
            converted = price / divisor if divisor else None
            # A term only exists because it beats the normal price. One that
            # comes out dearer means the basis was lined up wrongly.
            if converted is not None and base_unit_cost is not None and converted > base_unit_cost:
                return None
            return converted
        # A basis that lines up with neither would need a divisor nobody
        # printed, so the deal price is refused rather than guessed.
        return None
    if base_unit_cost is None:
        return None
    if kind == "PERCENT_OFF" and term["percent"] is not None:
        return base_unit_cost * (Decimal(100) - term["percent"]) / Decimal(100)
    if kind == "FREE_ITEMS" and term["min_qty"] and term["free_qty"]:
        paid, free = term["min_qty"], term["free_qty"]
        return base_unit_cost * paid / (paid + free)
    if kind == "AMOUNT_OFF" and term["fixed_amount"] is not None:
        if (term["reduction_basis"] or "") == "UNIT_PRICE":
            return base_unit_cost - term["fixed_amount"]
        return None
    return None


def _weight_parts(variant):
    """Display value in the source's own unit, plus canonical grams."""
    if variant is None or variant.weight_g is None:
        return "", "", ""
    grams = Decimal(str(variant.weight_g))
    unit = (variant.weight_unit or "g").strip().lower()
    per = _GRAMS_PER.get(unit)
    if per is None:
        return _num(grams), "g", _num(grams)
    shown = (grams / per).quantize(Decimal("0.001")).normalize()
    return format(shown, "f"), unit, _num(grams)


def _sort_key(term):
    """Product deals first, then by the threshold you must reach."""
    order_level = 1 if term["scope"] == "SUPPLIER_ORDER" else 0
    qty = term["min_qty"] if term["min_qty"] is not None else Decimal("0")
    spend = term["min_spend"] if term["min_spend"] is not None else Decimal("0")
    return (order_level, qty, spend)


def _slot_values(term, *, base_unit_cost=None, sellable_uom='', purchase_uom='', divisor=None):
    """Flatten one term into the eight per-slot columns."""
    kind = BENEFIT_LABEL.get(term["benefit_type"], term["benefit_type"])
    value, value_uom = "", ""
    if kind == "BULK_PRICE":
        value = _num(term["price_amount"])
        basis = term["price_basis"] or ""
        value_uom = f"{term['price_currency'] or 'HKD'} per {basis}".strip() if basis else (term["price_currency"] or "")
    elif kind == "PERCENT_OFF":
        value, value_uom = _num(term["percent"]), "%"
    elif kind == "AMOUNT_OFF":
        value = _num(term["fixed_amount"])
        target = REDUCTION_LABEL.get(term["reduction_basis"] or "", term["reduction_basis"] or "")
        value_uom = f"{term['fixed_currency'] or 'HKD'} off {target}".strip()
    elif kind == "FREE_ITEMS":
        value, value_uom = _num(term["free_qty"]), term["free_qty_uom"] or ""
    deal_cost = _deal_unit_cost(term, base_unit_cost, sellable_uom, purchase_uom, divisor)

    # Ruling 2026-08-19: an order-value deal is costed as if the whole order is
    # this one SKU, so the spend threshold becomes a quantity of this product.
    qty_to_qualify = ""
    if term["min_spend"] and base_unit_cost and base_unit_cost > 0:
        qty_to_qualify = _num((term["min_spend"] / base_unit_cost).to_integral_value(rounding=ROUND_CEILING))

    return {
        "type": kind,
        "applies_to": SCOPE_LABEL.get(term["scope"], term["scope"]),
        "min_qty": _num(term["min_qty"]) if term["min_qty"] is not None else "",
        "min_qty_uom": term["min_qty_uom"] or "",
        "min_spend": _num(term["min_spend"]) if term["min_spend"] is not None else "",
        "qty_to_qualify": qty_to_qualify,
        "value": value,
        "value_uom": value_uom,
        "text": term["text"],
        "cost_per_unit": _num(deal_cost.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)) if deal_cost is not None else "",
        **{f"{channel}_{metric}": "" for channel in CHANNELS for metric in ("gross_margin", "net_margin")},
    }


def _term_from_model(row):
    return {
        "scope": row.scope,
        "benefit_type": row.benefit_type,
        "min_qty": _dec(row.condition_quantity_amount),
        "min_qty_uom": _uom(row.condition_quantity_uom_code, row.condition_quantity_uom_label),
        "min_spend": _dec(row.condition_spend_amount),
        "price_amount": _dec(row.discounted_price_amount),
        "price_currency": row.discounted_price_currency,
        "price_basis": _uom(row.discounted_price_basis_uom_code, row.discounted_price_basis_uom_label),
        "percent": _dec(row.percentage_discount),
        "fixed_amount": _dec(row.fixed_discount_amount),
        "fixed_currency": row.fixed_discount_currency,
        "reduction_basis": row.fixed_discount_reduction_basis,
        "free_qty": _dec(row.free_quantity_amount),
        "free_qty_uom": _uom(row.free_quantity_uom_code, row.free_quantity_uom_label),
        "text": _mbb_text(row),
    }


def _term_from_json(payload):
    """Same term, read one stage earlier out of the candidate's resolution JSON."""
    condition, benefit = payload.get("condition") or {}, payload.get("benefit") or {}
    qty = condition.get("quantity") or {}
    spend = condition.get("spend") or {}
    price = benefit.get("discounted_price") or {}
    fixed = benefit.get("amount") or {}
    free = benefit.get("quantity") or {}
    term = {
        "scope": payload.get("scope"),
        "benefit_type": benefit.get("benefit_type"),
        "min_qty": _dec(qty.get("amount")),
        "min_qty_uom": _uom((qty.get("uom") or {}).get("code"), (qty.get("uom") or {}).get("label")),
        "min_spend": _dec(spend.get("amount")),
        "price_amount": _dec(price.get("amount")),
        "price_currency": price.get("currency"),
        "price_basis": _uom((price.get("price_basis") or {}).get("code"), (price.get("price_basis") or {}).get("label")),
        "percent": _dec(benefit.get("percentage")),
        "fixed_amount": _dec(fixed.get("amount")),
        "fixed_currency": fixed.get("currency"),
        "reduction_basis": benefit.get("reduction_basis"),
        "free_qty": _dec(free.get("amount")),
        "free_qty_uom": _uom((free.get("uom") or {}).get("code"), (free.get("uom") or {}).get("label")),
        "text": "",
    }
    # Render the sentence through the same helper the sheet uses, by handing it
    # an unsaved model object carrying these values.
    term["text"] = _mbb_text(
        models.CatalogueSupplierMbbTerm(
            scope=term["scope"],
            condition_type=(condition.get("condition_type") or ""),
            condition_quantity_amount=term["min_qty"],
            condition_quantity_uom_code=(qty.get("uom") or {}).get("code"),
            condition_spend_amount=term["min_spend"],
            condition_spend_currency=spend.get("currency"),
            benefit_type=term["benefit_type"],
            discounted_price_amount=term["price_amount"],
            discounted_price_currency=term["price_currency"],
            discounted_price_basis_uom_code=(price.get("price_basis") or {}).get("code"),
            percentage_discount=term["percent"],
            fixed_discount_amount=term["fixed_amount"],
            fixed_discount_currency=term["fixed_currency"],
            free_quantity_amount=term["free_qty"],
            free_quantity_uom_code=(free.get("uom") or {}).get("code"),
            description=payload.get("description"),
        )
    )
    return term


def _row(*, supplier_name, sku, barcode, name_supplier, name_rosetta, variant, pack, link,
         cost_amount, cost_currency, cost_basis, rrp_amount, rrp_currency, terms,
         product_id=None, channel_data=None, supplier_id=None, supplier_sku_index=None,
         legacy_terms=None, delivery_inputs=None):
    weight_value, weight_unit, weight_grams = _weight_parts(variant)
    # The pack's own content count only. Order multiples live in
    # order_increment_amount and must not stand in for a pack size here.
    per_purchase = ""
    if pack is not None and getattr(pack, "sellable_units_per_purchase_unit", None) is not None:
        per_purchase = _num(pack.sellable_units_per_purchase_unit)
    if not per_purchase and link is not None and getattr(link, "units_per_pack", None):
        if link.units_per_pack > 1:
            per_purchase = _num(link.units_per_pack)

    purchase_uom = _uom(getattr(pack, "purchase_uom_code", None), getattr(pack, "purchase_uom_label", None)) or (cost_basis or "")
    sellable_uom = _uom(getattr(pack, "sellable_unit_uom_code", None), getattr(pack, "sellable_unit_uom_label", None)) or (
        (variant.uom if variant else "") or ""
    )
    if not sellable_uom or sellable_uom.upper() in MEASURE_CODES:
        # Nothing named a unit, or the only noun is a measure ("30 ML / BOTTLE").
        # What you sell defaults to what you buy: you cannot sell a unit the
        # catalogue never mentions, so the purchase IS the unit. This divides by
        # one rather than by a count nobody printed, which is the same discipline
        # as refusing to split a case total.
        sellable_uom = purchase_uom

    cost = _dec(cost_amount)
    divisor = _unit_divisor(sellable_uom, purchase_uom, per_purchase, cost_basis)
    per_unit_cost = (cost / divisor).quantize(Decimal("0.0001")) if cost is not None and divisor else None
    per_unit = _num(per_unit_cost) if per_unit_cost is not None else ""

    code = str(sku or "").strip().upper()
    legacy = (legacy_terms or {})
    terms = list(terms) + list(legacy.get((supplier_id, code)) or legacy.get((None, code)) or [])
    # Dropped outright, not just left uncosted: a term we cannot trust would
    # otherwise still be counted in number_of_deals, printed in the offer
    # summary and shown in a tier slot, stating a discount that does not exist.
    terms = [t for t in terms if not _is_base_spread_over_a_minimum(t, per_unit_cost)]
    seen_terms, merged = set(), []
    for term in terms:
        # Both stores hold the same offer in different units — the pipeline
        # quotes "$560 per PIECE", the hand-entered row quotes "$6.22" for the
        # same capsule. Comparing raw values kept both and doubled the count,
        # so the fingerprint uses what the deal actually costs per unit.
        resolved = _deal_unit_cost(term, per_unit_cost, sellable_uom, purchase_uom, divisor)
        fingerprint = (
            term["benefit_type"], term["min_qty"], term["min_spend"],
            resolved.quantize(Decimal("0.01")) if resolved is not None else None,
            term["percent"], term["free_qty"],
        )
        if fingerprint in seen_terms:
            continue
        seen_terms.add(fingerprint)
        merged.append(term)
    terms = sorted(merged, key=_sort_key)
    row = {
        "supplier": supplier_name or "",
        "supplier_product_code": sku or "",
        "barcode": barcode or "",
        "product_name_supplier": name_supplier or "",
        "product_name_rosetta": name_rosetta or "",
        "brand": (variant.brand if variant else "") or "",
        "weight_value": weight_value,
        "weight_unit": weight_unit,
        "weight_grams": weight_grams,
        "purchase_uom": _uom(getattr(pack, "purchase_uom_code", None), getattr(pack, "purchase_uom_label", None)),
        "sellable_uom": sellable_uom,
        "sellable_units_per_purchase_unit": per_purchase,
        "content_amount": _num(getattr(pack, "content_amount", None)) if pack is not None else "",
        "content_uom": _uom(getattr(pack, "content_uom_code", None), getattr(pack, "content_uom_label", None)),
        "packaging_text": _packaging_text(pack) or _identity_packaging(variant, link),
        "order_increment_amount": _num(getattr(pack, "order_increment_amount", None)) if pack is not None else "",
        "order_increment_uom": _uom(getattr(pack, "order_increment_uom_code", None), getattr(pack, "order_increment_uom_label", None)),
        "minimum_order_amount": _num(getattr(pack, "minimum_order_amount", None)) if pack is not None else "",
        "minimum_order_uom": _uom(getattr(pack, "minimum_order_uom_code", None), getattr(pack, "minimum_order_uom_label", None)),
        "order_multiple_text": _order_multiple_text(pack, variant),
        "cost_price_amount": _num(cost),
        "cost_price_currency": cost_currency or "",
        "cost_price_basis_uom": cost_basis or "",
        "rrp_amount": _num(_dec(rrp_amount)),
        "rrp_currency": rrp_currency or "",
        "cost_per_unit": per_unit,
        # Every channel column below waits on ops inputs.
        **{column: "" for column in BASE_COLUMNS if column.startswith("selling_price_")
           or column.startswith("logistics_cost_per_unit_") or column.startswith("platform_fee_percent_")
           or column.endswith("_gross_margin") or column.endswith("_net_margin")},
        "number_of_deals": str(len(terms)),
        "has_order_level_promo": _bool(any(t["scope"] == "SUPPLIER_ORDER" for t in terms)) if terms else "FALSE",
        "commercial_offer_summary": "; ".join(t["text"] for t in terms if t["text"]),
    }
    for index in range(SLOTS):
        slot = (
            _slot_values(terms[index], base_unit_cost=per_unit_cost, sellable_uom=sellable_uom,
                         purchase_uom=purchase_uom, divisor=divisor)
            if index < len(terms) else {}
        )
        slot = {field: slot.get(field, "") for field in SLOT_FIELDS}
        for field, value in slot.items():
            row[f"mbb_tier_{index + 1}_{field}"] = value
    # Prefer the supplier's own code: it reaches the product the business
    # actually sells, not a record the pipeline happened to create.
    index = supplier_sku_index or {}
    linked = index.get((supplier_id, code)) or index.get((None, code))
    return _fill_channels(row, linked or product_id, channel_data or {}, delivery_inputs)


def build_published_rows(db) -> list[dict]:
    publications = db.query(models.CatalogueServingPublication).all()
    offering_ids = {p.supplier_product_id for p in publications if p.supplier_product_id}
    packaging = {}
    for pack in (
        db.query(models.CataloguePackagingConfiguration)
        .filter(models.CataloguePackagingConfiguration.superseded_at.is_(None))
        .all()
    ):
        packaging[pack.supplier_product_id] = pack
    terms = {}
    for term in db.query(models.CatalogueSupplierMbbTerm).filter(models.CatalogueSupplierMbbTerm.is_active == 1).all():
        terms.setdefault(term.supplier_product_id, []).append(_term_from_model(term))
    suppliers = {s.id: s for s in db.query(models.Supplier).all()}
    variants = {v.id: v for v in db.query(models.ProductVariant).all()}
    offerings = {o.id: o for o in db.query(models.SupplierOffering).filter(models.SupplierOffering.id.in_(offering_ids)).all()} if offering_ids else {}
    links = {(l.supplier_id, l.product_id): l for l in db.query(models.ProductSupplier).all()}
    channel_data = _load_channels(db)
    supplier_sku_index = _supplier_sku_index(db)
    legacy_terms = _load_legacy_terms(db)
    delivery_inputs = _load_delivery_inputs(db)

    rows = []
    for pub in publications:
        variant = variants.get(pub.product_id)
        offering = offerings.get(pub.supplier_product_id)
        rows.append(
            _row(
                supplier_name=(suppliers.get(pub.supplier_id).name if suppliers.get(pub.supplier_id) else ""),
                sku=pub.supplier_sku,
                barcode=getattr(offering, "barcode", None),
                name_supplier=pub.product_variant_name,
                name_rosetta=(variant.name if variant else ""),
                variant=variant,
                pack=packaging.get(pub.supplier_product_id),
                link=links.get((pub.supplier_id, pub.product_id)),
                cost_amount=pub.current_approved_cost_amount,
                cost_currency=pub.current_approved_cost_currency,
                cost_basis=_uom(
                    getattr(packaging.get(pub.supplier_product_id), "price_basis_uom_code", None),
                    getattr(packaging.get(pub.supplier_product_id), "price_basis_uom_label", None),
                ),
                rrp_amount=None,
                rrp_currency=None,
                terms=terms.get(pub.supplier_product_id, []),
                product_id=pub.product_id,
                channel_data=channel_data,
                supplier_id=pub.supplier_id,
                supplier_sku_index=supplier_sku_index,
                legacy_terms=legacy_terms,
                delivery_inputs=delivery_inputs,
            )
        )
    return rows

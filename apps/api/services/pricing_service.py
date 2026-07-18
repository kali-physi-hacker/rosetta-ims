"""GP computation, pricing recommendations, and margin range logic."""
import json
import re
from datetime import datetime, timezone
from models import Product, ProductChannel, ProductSupplier, StockLevel, SalesVelocity, CategoryRule
from services import transform_engine as engine

_STALE_DAYS = 90


def _is_cost_stale(ps: ProductSupplier | None) -> bool:
    if not ps:
        return True
    # manual with no ref is always stale
    if (ps.cost_source or 'manual') == 'manual' and not ps.cost_source_ref:
        return True
    if ps.cost_updated_at:
        try:
            updated = datetime.fromisoformat(ps.cost_updated_at.replace('Z', '+00:00'))
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - updated).days > engine.get_param("staleness_days")
        except ValueError:
            return True
    return True


def get_primary_supplier(product: Product) -> ProductSupplier | None:
    ps = next((p for p in product.product_suppliers if p.is_primary), None)
    return ps or (product.product_suppliers[0] if product.product_suppliers else None)


def get_primary_cost(product: Product) -> float | None:
    ps = get_primary_supplier(product)
    return ps.basic_cost if ps and ps.basic_cost else None


def _legacy_unit_cost(basic_cost, units_per_pack):
    """Pre-config formula — kept only as the shadow-equivalence reference in tests."""
    if basic_cost is None:
        return None
    if units_per_pack and units_per_pack > 1:
        return basic_cost / units_per_pack
    return basic_cost


def get_unit_cost(ps: ProductSupplier | None) -> float | None:
    """Cost of ONE SELL-UNIT = supplier WHOLESALE (whole-pack) basic_cost / units_per_pack.
    basic_cost is maintained as the whole-pack cost (Ops owns that data), so we ALWAYS divide
    by the pack size to get the per-sell-unit cost that every GP / margin calculation runs on.
    pack <= 1 (or unset) -> basic_cost is already the per-sell-unit cost.
    The formula now lives in config (transform_engine 'unit_cost')."""
    if not ps:
        return None
    return engine.evaluate("unit_cost", {"basic_cost": ps.basic_cost, "units_per_pack": ps.units_per_pack})


def _legacy_term_unit_cost(kind, base_unit_cost, min_qty, free_qty, discount_pct, unit_cost):
    """Pre-config formula — kept only as the shadow-equivalence reference in tests."""
    if kind == 'buy_x_get_y' and base_unit_cost and min_qty and free_qty:
        total = min_qty + free_qty
        return round(base_unit_cost * min_qty / total, 4) if total else None
    if kind == 'spend_discount' and base_unit_cost and discount_pct:
        return round(base_unit_cost * (1 - discount_pct), 4)
    if kind in ('tier', 'flat_unit_cost'):
        return unit_cost
    return None


def _term_unit_cost(term, base_unit_cost: float | None) -> float | None:
    """Effective per-sell-unit cost of ONE relational MBB term. The computed kinds
    (buy_x_get_y / spend_discount) run through config formulas; tier / flat_unit_cost is already
    per-unit and read directly."""
    k = term.kind
    if k == 'buy_x_get_y':
        return engine.evaluate("mbb_buy_x_get_y",
                               {"base": base_unit_cost, "min_qty": term.min_qty, "free_qty": term.free_qty})
    if k == 'spend_discount':
        return engine.evaluate("mbb_spend_discount",
                               {"base": base_unit_cost, "discount_pct": term.discount_pct})
    if k in ('tier', 'flat_unit_cost'):
        return term.unit_cost
    return None


def best_mbb(ps: ProductSupplier | None, base_unit_cost: float | None):
    """(cheapest achievable per-sell-unit cost, winning term) across this supplier's MBB terms.
    Falls back to the legacy flat scalars for any row not yet migrated to relational terms."""
    if not ps:
        return (None, None)
    best_cost, best_term = None, None
    for term in (getattr(ps, "mbb_term_list", None) or []):
        c = _term_unit_cost(term, base_unit_cost)
        if c is not None and (best_cost is None or c < best_cost):
            best_cost, best_term = c, term
    if best_cost is not None:
        return (best_cost, best_term)
    return (None, None)   # terms are authoritative — no scalar fallback (columns dropped)


def _cost_to_hit_mbb(term, base_unit_cost: float | None, achieved_unit_cost: float | None) -> float | None:
    """Cash outlay to UNLOCK an MBB term — what you actually pay up front, by term kind:
      • buy_x_get_y    → min_qty units at the BASIC price (the free units come after; you do NOT
                         pay the post-freebie effective price to reach the deal).
      • spend_discount → the spend threshold itself (min_spend).
      • tier / flat    → min_qty units at the achieved (discounted) unit cost.
    The old formula used the achieved cost for every kind, which under-counted buy_x_get_y
    (e.g. "buy 10 get 3 free" at $215 basic reads $2,150 to hit, not 10 × the $165 effective)."""
    if term is None:
        return None
    if term.kind == 'buy_x_get_y' and base_unit_cost and term.min_qty:
        return round(base_unit_cost * term.min_qty, 0)
    if term.kind == 'spend_discount' and term.min_spend:
        return round(term.min_spend, 0)
    if term.min_qty and achieved_unit_cost:
        return round(achieved_unit_cost * term.min_qty, 0)
    return None


def effective_mbb_unit_cost(ps: ProductSupplier | None, base_unit_cost: float | None) -> float | None:
    """Per-sell-unit cost at the best achievable Max-Bulk-Buy term — derived from the relational
    mbb_terms, so there is no stored per-box number to mis-divide (the old basis bug)."""
    return best_mbb(ps, base_unit_cost)[0]


# ── Channel fee model ─────────────────────────────────────────────────────────────────
# The only costs on top of the supplier unit cost are channel-specific:
#   Shopify -> logistics (SF Express, by shipping weight); no platform fee.
#   HKTV    -> platform fee (channel_fee_pct); no logistics.
#   Clinic  -> neither.
# SF Express Speedy Express — HK domestic, Limited-Time-Offer (effective 2024-06-01). Weight is
# billed rounded UP to 0.5kg (<=5kg) then 1kg; the tier tops below encode that, so a straight
# "weight <= limit" lookup gives the billed rate. Mirrors the transform_engine 'sf_logistics'
# default table — kept in lock-step for the shadow-equivalence test.
_SF_TIERS = [(500, 20.0), (1000, 30.0), (1500, 37.0), (2000, 44.0), (2500, 51.0), (5000, 58.0),
             (10000, 68.0), (15000, 78.0), (20000, 88.0), (21000, 294.0), (22000, 307.0),
             (23000, 320.0), (24000, 333.0), (25000, 346.0)]
_SF_OVER = 346.0       # > 25 kg (SF adds +13/kg beyond; flat cap here — very rare for us)
_SF_UNKNOWN = 58.0     # weight not recorded -> moderate default (<=5 kg tier)


def _legacy_sf(weight_g):
    """Pre-config table lookup — kept only as the shadow-equivalence reference in tests."""
    if not weight_g or weight_g <= 0:
        return _SF_UNKNOWN
    for limit, cost in _SF_TIERS:
        if weight_g <= limit:
            return cost
    return _SF_OVER


def shopify_logistics(weight_g: float | None) -> float:
    """SF Express delivery cost (HK$) for a parcel of the given shipping weight in grams.
    The tier table now lives in config (transform_engine 'sf_logistics')."""
    return engine.lookup_table("sf_logistics", weight_g)


def _pack_sell_unit_delivery(weight_g: float | None, units_per_pack: int | None) -> float:
    """SF Express cost attributed to ONE sell-unit.

    A multi-unit pack (e.g. a box of 12 pouches, min. sold as a box) ships as a SINGLE parcel,
    so the courier fee is charged once on the PACK's weight (unit weight × pack size) and split
    across the units. Charging the full parcel rate to each single pouch was the per-unit bug
    that made pack-sold items show impossible losses (e.g. −115% on an $18 pouch).

    A single-unit pack (units_per_pack ≤ 1) IS its own parcel, so this is unchanged from the old
    per-unit charge — every upp=1 SKU keeps its exact prior delivery cost."""
    upp = units_per_pack if (units_per_pack and units_per_pack > 1) else 1
    return shopify_logistics((weight_g or 0) * upp) / upp


_DEFAULT_HKTV_FEE = 0.18   # standard HKTV Mall commission; used when a SKU has no explicit fee
_ALL_CHANNELS = ("clinic", "shopify", "hktv")


def _fee_delivery(channel: ProductChannel, product: Product) -> tuple[float, float]:
    """(channel_fee_pct, delivery_cost) for a channel: Shopify -> SF logistics only,
    HKTV -> platform fee only (its own value, else the default commission), Clinic -> neither."""
    if channel.channel == "hktv":
        fee = channel.channel_fee_pct if channel.channel_fee_pct is not None else engine.get_param("hktv_fee")
        return fee, 0.0
    if channel.channel == "shopify":
        ps = get_primary_supplier(product)
        return 0.0, _pack_sell_unit_delivery(product.weight_g, ps.units_per_pack if ps else None)
    return 0.0, 0.0


class _MissingChannel:
    """Stand-in so a channel a SKU isn't listed on still appears in every margin view. Its price
    is None (blank margins) but the fee model still applies (e.g. HKTV's default commission)."""
    def __init__(self, name: str):
        self.channel = name
        self.selling_price = None
        self.channel_fee_pct = None
        self.is_active = 0
        self.units_per_listing = None


def _all_channels(product: Product) -> list:
    """The SKU's channels, always widened to Clinic + Shopify + HKTV (missing ones stubbed) so
    every margin view shows all three."""
    by_name = {c.channel: c for c in product.channels}
    return [by_name.get(name) or _MissingChannel(name) for name in _ALL_CHANNELS]


def get_stock(product: Product) -> tuple[float, float]:
    clinic    = next((s.qty for s in product.stock_levels if s.location == "clinic"),    0.0)
    warehouse = next((s.qty for s in product.stock_levels if s.location == "warehouse"), 0.0)
    return clinic, warehouse


def get_latest_velocity(product: Product):
    if not product.sales_velocity:
        return None
    return max(product.sales_velocity, key=lambda v: v.calculated_at)


def get_weekly_demand(product: Product) -> float:
    sv = get_latest_velocity(product)
    return sv.weekly_demand if sv else 0.0


def _legacy_gp(selling_price, cost_price):
    """Pre-config formula — kept only as the shadow-equivalence reference in tests."""
    if not selling_price or selling_price <= 0 or cost_price is None or cost_price <= 0:
        return None
    return round((selling_price - cost_price) / selling_price, 4)


def compute_gp(selling_price: float | None, cost_price: float | None) -> float | None:
    """Gross GP% — formula lives in config (transform_engine 'gross_gp')."""
    return engine.evaluate("gross_gp", {"price": selling_price, "cost": cost_price})


def channel_to_dict(channel: ProductChannel, cost: float | None, gp_floor: float) -> dict:
    gp_pct = compute_gp(channel.selling_price, cost)
    recommendation = None
    gap_pct = None

    if gp_pct is not None:
        if gp_pct >= gp_floor:
            recommendation = "Price is OK ✓"
        else:
            # Cost is trusted (whole-pack basic ÷ pack); below floor -> raise the price.
            recommendation = "Raise price ⚠"
            gap_pct = round(gp_floor - gp_pct, 4)

    return {
        "channel":            channel.channel,
        "is_active":          bool(channel.is_active),
        "selling_price":      channel.selling_price,
        "has_dispensing_fee": bool(channel.has_dispensing_fee),
        "channel_fee_pct":    channel.channel_fee_pct,
        "units_per_listing":  channel.units_per_listing,
        "gp_pct":             gp_pct,
        "recommendation":     recommendation,
        "gap_pct":            gap_pct,
    }


def _legacy_channel_margin(selling_price, unit_cost, channel_fee_pct, delivery_cost):
    """Pre-config formula — kept only as the shadow-equivalence reference in tests."""
    if not selling_price or selling_price <= 0 or unit_cost is None or unit_cost <= 0:
        return None
    fee = (channel_fee_pct or 0.0) * selling_price
    net = selling_price - unit_cost - fee - delivery_cost
    return round(net / selling_price, 4)


def _channel_margin(selling_price, unit_cost, channel_fee_pct, delivery_cost) -> float | None:
    """Effective margin after channel fee and delivery cost, as % of gross selling price.
    Formula lives in config (transform_engine 'net_margin')."""
    return engine.evaluate("net_margin", {"price": selling_price, "cost": unit_cost,
                                          "fee_pct": channel_fee_pct, "delivery": delivery_cost})


def _term_margin_dict(product: Product, term, base_unit: float | None,
                      weekly_demand: float) -> dict | None:
    """One MBB term's per-sell-unit cost + its per-channel margin (gross GP and net-of-fees).
    Same shape whether it's a primary-supplier term or a per-supplier one."""
    t_unit = _term_unit_cost(term, base_unit)
    if t_unit is None:
        return None
    t_landed = round(t_unit, 4)
    t_qty = term.min_qty
    return {
        "id":          term.id,
        "kind":        term.kind,
        "note":        term.note,
        "min_qty":     t_qty,
        "min_spend":   _cost_to_hit_mbb(term, base_unit, t_landed),
        "weeks_cover": round(t_qty / weekly_demand, 1) if (t_qty and weekly_demand > 0) else None,
        "unit_cost":   t_landed,
        "channels": [
            {"channel": ch.channel,
             "gp_pct":  compute_gp(ch.selling_price, t_landed),
             "margin":  _channel_margin(ch.selling_price, t_landed, *_fee_delivery(ch, product))}
            for ch in _all_channels(product)
        ],
    }


def margin_range(product: Product, cat_rules: dict) -> dict:
    """Basic vs MBB margin per channel, net of channel charges: Shopify subtracts SF logistics
    (by weight), HKTV subtracts channel_fee_pct, Clinic neither."""
    ps = get_primary_supplier(product)
    # Per-sell-unit supplier cost (pack-aware). Channel fees/logistics are applied PER CHANNEL
    # below, not folded into the cost — so each channel bears only its own charges.
    base_unit  = get_unit_cost(ps)
    basic_cost = base_unit
    mbb_unit, mbb_term = best_mbb(ps, base_unit)                    # cheapest achievable term
    mbb_cost   = mbb_unit
    mbb_min_qty  = mbb_term.min_qty if mbb_term else None
    mbb_terms    = mbb_term.note    if mbb_term else None

    mbb_min_spend   = _cost_to_hit_mbb(mbb_term, base_unit, mbb_cost)
    weekly_demand   = get_weekly_demand(product)
    mbb_weeks_cover = round(mbb_min_qty / weekly_demand, 1) if (mbb_min_qty and weekly_demand > 0) else None

    channel_ranges = []
    for ch in _all_channels(product):
        sp = ch.selling_price
        fee, delivery = _fee_delivery(ch, product)   # HKTV -> fee, Shopify -> logistics, Clinic -> neither

        channel_ranges.append({
            "channel":       ch.channel,
            "selling_price": sp,
            "gp_pct_mbb":    compute_gp(sp, mbb_cost) if mbb_cost else None,  # gross margin at MBB landed cost
            "basic_margin":  _channel_margin(sp, basic_cost, fee, delivery),
            "mbb_margin":    _channel_margin(sp, mbb_cost, fee, delivery) if mbb_cost else None,
            "channel_fee_pct": fee if fee > 0 else None,
            "delivery_cost":   delivery if delivery > 0 else None,
        })

    # Per-term MBB margins for the PRIMARY supplier (kept for the channel card / back-compat).
    mbb_term_margins = [d for d in (
        _term_margin_dict(product, term, base_unit, weekly_demand)
        for term in sorted((getattr(ps, "mbb_term_list", None) or []), key=lambda x: x.sort_order)
    ) if d]

    # Per-SUPPLIER margins: each linked supplier (cheapest basic_cost first = preferred), with its
    # own basic margin plus a margin for EACH of its MBB terms — so every MBB path is visible.
    supplier_blocks = []
    for i, sup in enumerate(sorted((s for s in product.product_suppliers if s.basic_cost),
                                   key=lambda s: s.basic_cost)):
        s_base = get_unit_cost(sup)
        if s_base is None:
            continue
        s_basic_landed = round(s_base, 4)
        supplier_blocks.append({
            "supplier_id":  sup.supplier_id,
            "name":         sup.supplier.name if sup.supplier else None,
            "code":         sup.supplier.code if sup.supplier else None,
            "is_primary":   bool(sup.is_primary),
            "is_preferred": i == 0,
            "basic_cost":   s_basic_landed,
            "basic_channels": [
                {"channel": ch.channel,
                 "gp_pct":  compute_gp(ch.selling_price, s_basic_landed),
                 "margin":  _channel_margin(ch.selling_price, s_basic_landed, *_fee_delivery(ch, product))}
                for ch in _all_channels(product)
            ],
            "term_margins": [d for d in (
                _term_margin_dict(product, term, s_base, weekly_demand)
                for term in sorted((getattr(sup, "mbb_term_list", None) or []), key=lambda x: x.sort_order)
            ) if d],
        })

    return {
        "basic_cost":      basic_cost,        # supplier per-sell-unit cost (channel charges applied per channel)
        "mbb_cost":        mbb_cost,
        "mbb_kind":        (mbb_term.kind if mbb_term else None),
        "mbb_min_qty":     mbb_min_qty,
        "mbb_min_spend":   mbb_min_spend,
        "mbb_weeks_cover": mbb_weeks_cover,
        "mbb_terms":       mbb_terms,
        "mbb_term_margins": mbb_term_margins,   # one entry per MBB term (primary supplier)
        "suppliers":       supplier_blocks,     # per-supplier basic + per-term margins
        "channels":        channel_ranges,
    }


_VALID_SKU_RE = re.compile(r'^\d{6,}$')


def is_valid_sku(sku_code: str) -> bool:
    return bool(_VALID_SKU_RE.match(sku_code.strip()))


def compute_data_grade(primary_cost, channels, supplier_name=None, sku_code=None) -> str:
    """Inventory data-quality signal. Invoice reconciliation / cost-source conflicts are a
    PROCUREMENT concern (handled in that flow), not part of the inventory SSOT:
      A — actionable: cost + selling price + supplier + valid SKU all present
      C — do not use: any of cost / selling price / supplier / valid SKU missing
    """
    has_cost      = primary_cost is not None
    has_any_price = any(c.selling_price is not None for c in channels)
    has_supplier  = bool(supplier_name)
    valid_sku     = is_valid_sku(sku_code) if sku_code else False
    return 'C' if (not has_cost or not has_any_price or not has_supplier or not valid_sku) else 'A'


def _oos_days(out_at: str | None, restock_at: str | None) -> int | None:
    """Length of an out-of-stock period in days: out_at → restock_at, or out_at → today if ongoing."""
    from datetime import date
    try:
        d0 = date.fromisoformat(out_at[:10])
        d1 = date.fromisoformat(restock_at[:10]) if restock_at else date.today()
        return (d1 - d0).days
    except Exception:
        return None


def product_to_dict(product: Product, cat_rules: dict[str, CategoryRule], include_margin_range: bool = False) -> dict:
    clinic_qty, warehouse_qty = get_stock(product)
    total_qty     = clinic_qty + warehouse_qty
    _vel          = get_latest_velocity(product)
    weekly_demand = _vel.weekly_demand if _vel else 0.0
    woc           = engine.evaluate("woc", {"total_qty": total_qty, "weekly_demand": weekly_demand})
    sales_120d    = engine.evaluate("sales_120d", {"weekly_demand": weekly_demand})
    # Per-channel weekly demand (algo multichannel sync); None if no velocity row yet.
    wd_by_channel = {
        "clinic":  getattr(_vel, "weekly_demand_clinic", None),
        "hktv":    getattr(_vel, "weekly_demand_hktv", None),
        "shopify": getattr(_vel, "weekly_demand_shopify", None),
    } if _vel else None
    # Monthly sales series (for the SKU-page spark), from the multichannel sync.
    sales_trend = None
    if _vel and getattr(_vel, "trend_json", None):
        try:
            sales_trend = [{"month": m, "units": u} for m, u in json.loads(_vel.trend_json)]
        except Exception:
            sales_trend = None
    primary_cost  = get_primary_cost(product)

    ps_temp = next((p for p in product.product_suppliers if p.is_primary), None) or \
              (product.product_suppliers[0] if product.product_suppliers else None)
    units_per_pack = ps_temp.units_per_pack if ps_temp else None
    # Per-sell-unit supplier cost (pack-aware once verified) + the extra costs we incur =
    # the LANDED unit cost that all GP / margin math runs on.
    unit_cost      = get_unit_cost(ps_temp)
    effective_cost = unit_cost   # landed = supplier unit cost; channel charges applied per channel

    rule     = cat_rules.get(product.category)
    gp_floor = rule.gp_floor if rule else 0.0

    channels = [channel_to_dict(c, effective_cost, gp_floor) for c in product.channels]

    gp_values = [c["gp_pct"] for c in channels if c["gp_pct"] is not None]
    cross_channel_flag = (max(gp_values) - min(gp_values)) > engine.get_param("cross_channel_threshold") if len(gp_values) >= 2 else False

    # Supplier info — primary + all linked suppliers
    ps = get_primary_supplier(product)
    supplier_name = None
    supplier_code = None
    if ps and ps.supplier:
        supplier_name = ps.supplier.name
        supplier_code = ps.supplier.code

    _sup_list = [
        {
            "id":            sup.id,
            "supplier_id":   sup.supplier_id,
            "name":          sup.supplier.name  if sup.supplier else None,
            "code":          sup.supplier.code  if sup.supplier else None,
            "supplier_sku":  sup.supplier_sku,
            "barcode":       sup.barcode,
            "basic_cost":    sup.basic_cost,
            "mbb_term_list": [
                {"id": t.id, "kind": t.kind, "min_qty": t.min_qty, "min_spend": t.min_spend,
                 "free_qty": t.free_qty, "discount_pct": t.discount_pct, "unit_cost": t.unit_cost,
                 "note": t.note, "sort_order": t.sort_order,
                 "effective_unit_cost": _term_unit_cost(t, get_unit_cost(sup))}
                for t in sorted(getattr(sup, "mbb_term_list", []) or [], key=lambda x: x.sort_order)
            ],
            "units_per_pack": sup.units_per_pack,
            "is_primary":    bool(sup.is_primary),
            "is_preferred":  False,  # set below
            "stock_status":        getattr(sup, "stock_status", None) or "in_stock",
            "reported_out_at":     getattr(sup, "reported_out_at", None),
            "expected_restock_at": getattr(sup, "expected_restock_at", None),
            "stock_confirmed_by":  getattr(sup, "stock_confirmed_by", None),
            "stock_note":          getattr(sup, "stock_note", None),
            "stock_events": [
                {"out_at": e.out_at, "restock_at": e.restock_at, "note": e.note,
                 "days": _oos_days(e.out_at, e.restock_at)}
                for e in sorted(getattr(sup, "stock_events", []) or [], key=lambda x: x.out_at, reverse=True)
            ],
        }
        for sup in product.product_suppliers
        if sup.supplier or sup.supplier_sku or sup.basic_cost
    ]
    # Sort by basic_cost ascending (nulls last); lowest cost = preferred
    _sup_list.sort(key=lambda s: (s["basic_cost"] is None, s["basic_cost"] or 0))
    if _sup_list:
        _sup_list[0]["is_preferred"] = True
    all_suppliers = _sup_list

    result = {
        "id":            product.id,
        "sku_code":      product.sku_code,
        "name":          product.name,
        "brand":         product.brand,
        "category":      product.category,
        "subcategory":   product.subcategory,
        "segment":       product.segment,
        "species":       product.species,
        "rrp":           product.rrp,
        "min_purchase_qty": product.min_purchase_qty,
        "min_sellable_qty": product.min_sellable_qty,
        "shopify_status":   product.shopify_status,
        "daysmart_status":  product.daysmart_status,
        "hktv_status":      product.hktv_status,
        "shopify_cost":     product.shopify_cost,
        "daysmart_avg_cost": product.daysmart_cost,   # platform avg unit cost (DaySmart balances API); distinct from the supplier-link daysmart_cost (last invoice)
        "hktv_cost":        product.hktv_cost,
        "uom":                product.uom,
        "pack_unit":          product.pack_unit,
        "last_manual_edit_at": product.last_manual_edit_at,
        "last_manual_edit_by": product.last_manual_edit_by,
        "storage_rule":       product.storage_rule,
        "status":        product.status,
        "hero_sku":      bool(product.hero_sku),
        "notes":         product.notes,
        "weight_g":      product.weight_g,
        "weight_unit":   product.weight_unit or 'kg',
        "clinic_qty":    clinic_qty,
        "warehouse_qty": warehouse_qty,
        "total_qty":     total_qty,
        "weekly_demand": weekly_demand,
        "weekly_demand_by_channel": wd_by_channel,   # {clinic, hktv, shopify} weekly demand, or None
        "sales_trend":   sales_trend,                # [{month:'YYYY-MM', units}] last ~5 months, or None
        "woc":           woc,
        "primary_cost":  primary_cost,
        "gp_floor":      gp_floor,
        "channels":      channels,
        "cross_channel_flag": cross_channel_flag,
        # Supplier / lineage
        "supplier_name":  supplier_name,
        "supplier_code":  supplier_code,
        "all_suppliers":  all_suppliers,
        "supplier_sku":   ps.supplier_sku      if ps else None,
        "mbb_unit_cost":    effective_mbb_unit_cost(ps, unit_cost),   # best achievable MBB cost (from terms)
        "cost_last_updated": ps.updated_at     if ps else None,
        "landed_unit_cost": effective_cost,        # = supplier per-sell-unit cost (no separate extra costs)
        # UOM / pack size
        "units_per_pack":       units_per_pack,
        "unit_cost":            unit_cost,         # supplier per-sell-unit cost (pre-extras)
        "uom_verified_at":      ps.uom_verified_at      if ps else None,
        "uom_verified_by":      ps.uom_verified_by      if ps else None,
        "pack_source":          ps.pack_source          if ps else 'sheet',
        # Shadow values — last value seen from Sheet sync
        "basic_cost_sheet":     ps.basic_cost_sheet     if ps else None,
        "units_per_pack_sheet": ps.units_per_pack_sheet if ps else None,
        # Discrepancy flags — Sheet value disagrees with IMS-locked value
        "cost_sheet_conflict": bool(
            ps and ps.basic_cost_sheet is not None and ps.basic_cost is not None
            and ps.cost_source in ('po_issued', 'invoice_matched', 'catalogue')
            and abs(ps.basic_cost_sheet - ps.basic_cost) / max(ps.basic_cost_sheet, ps.basic_cost) > 0.001
        ) if ps else False,
        "pack_sheet_conflict": bool(
            ps and ps.units_per_pack_sheet is not None and ps.units_per_pack is not None
            and (ps.uom_verified_at is not None or ps.pack_source == 'catalogue')
            and ps.units_per_pack_sheet != ps.units_per_pack
        ) if ps else False,
        # Sales velocity
        "sales_120d":      sales_120d,
        # Cost confidence (Story 1.5)
        "cost_source":        ps.cost_source     if ps else 'manual',
        "cost_source_ref":    ps.cost_source_ref if ps else None,
        "cost_updated_at":    ps.cost_updated_at if ps else None,
        "cost_is_stale":      _is_cost_stale(ps),
        # Ordering terms (order multiple / MOQ) — from the primary supplier link; NULL until set.
        # Read-only exposure for the UI / CSV export. Does not feed any margin math.
        "order_increment_qty":   ps.order_increment_qty   if ps else None,
        "order_increment_uom":   ps.order_increment_uom   if ps else None,
        "minimum_order_qty":     ps.minimum_order_qty     if ps else None,
        "minimum_order_uom":     ps.minimum_order_uom     if ps else None,
        "minimum_order_source":  ps.minimum_order_source  if ps else None,
        "pricing_note":          ps.pricing_note          if ps else None,
        # Data quality grade (inventory completeness — reconciliation lives in procurement)
        "data_grade": compute_data_grade(
            primary_cost, product.channels,
            supplier_name=supplier_name, sku_code=product.sku_code,
        ),
    }

    if include_margin_range:
        result["margin_range"] = margin_range(product, cat_rules)

    return result

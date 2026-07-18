"""Re-parse engine (RP-2.2 / RP-2.3 / RP-4): re-derive catalogue fields from RETAINED text, diff them
against the current value (the pending catalogue_item, or the live Product/ProductSupplier once matched),
and apply confirmed changes with a staleness re-verify + audit.

Re-parse RECAPTURES: for a committed SKU it re-applies the catalogue item's captured values onto the
live Product/ProductSupplier — cost, pack, identity and classification — so a corrected supplier SKU also
pulls in the right cost (not just the SKU). Deterministic: values come from the retained extraction (the
pack/cost guard corrects units_per_pack); no model call, no network.

Safety rules:
  · never null a live value from an empty catalogue capture — if the catalogue didn't capture a field,
    the live value is left alone (no change proposed);
  · never override a deliberate pack size — a per-unit price must not be divided by an order multiple
    (a case of 24 is `order_increment_qty`, NOT `units_per_pack`); re-parse defers to a manual/verified
    pack, or one whose catalogue count is already recorded as the SKU's order multiple / MOQ;
  · nothing writes to Product/ProductSupplier except `apply_change`, which runs only for a human-confirmed
    change and re-verifies the live value first.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import models
from services import audit_log
from services.pricing_service import get_unit_cost
from services import catalogue_pack as pack
from services import catalogue_contract

PARSER_VERSION = "v3-recapture"

# The fields the review card shows, grouped. For a committed SKU the CURRENT value is read from the live
# ProductSupplier (ps_attr) / Product (prod_attr); for a pending item it's the catalogue_item (item_attr).
# item-only fields (pack_size, variant, bulk_tiers) always read from the catalogue_item and never commit.
#   (group, field_key, prod_attr, ps_attr, item_attr)
DISPLAY_FIELDS = [
    ("Pricing", "cost_price", None, "basic_cost", "cost_price"),
    ("Pricing", "units_per_pack", None, "units_per_pack", "units_per_pack"),
    ("Pricing", "rrp", "rrp", None, "rrp"),
    ("Pricing", "bulk_tiers", None, None, "bulk_tiers"),
    ("Identity", "brand", "brand", None, "brand"),
    ("Identity", "supplier_sku", None, "supplier_sku", "supplier_sku"),
    ("Identity", "barcode", None, "barcode", "barcode"),
    ("Identity", "variant", None, None, "variant"),
    ("Identity", "species", "species", None, "species"),
    ("Pack & quantity", "uom", "uom", None, "uom"),
    ("Pack & quantity", "pack_size", None, None, "pack_size"),
    ("Pack & quantity", "min_sellable_qty", "min_sellable_qty", None, "min_sellable_qty"),
    ("Pack & quantity", "min_purchase_qty", "min_purchase_qty", None, "min_purchase_qty"),
    ("Pack & quantity", "order_increment_qty", None, "order_increment_qty", None),   # supplier order multiple (derived)
    ("Pack & quantity", "weight_grams", "weight_g", None, "weight_grams"),
    ("Classification", "category", "category", None, "ai_category"),
    ("Classification", "subcategory", "subcategory", None, "ai_subcategory"),
]

# Recapture map, derived from DISPLAY_FIELDS: every field that has a committed home (a ProductSupplier or
# Product attribute). item-only fields (no ps/prod attr) can't be recaptured onto a SKU and are skipped.
#   {field_key: (kind 'ps'|'product', target_attr, item_source_attr)}
_RECAPTURE = {}
for _g, _k, _prod_attr, _ps_attr, _item_attr in DISPLAY_FIELDS:
    if _ps_attr:
        _RECAPTURE[_k] = ("ps", _ps_attr, _item_attr)
    elif _prod_attr:
        _RECAPTURE[_k] = ("product", _prod_attr, _item_attr)

_COST_FIELDS = {"cost_price", "units_per_pack"}                    # effective unit cost = basic_cost / units_per_pack
_INT_FIELDS = {"units_per_pack", "weight_grams", "min_sellable_qty", "min_purchase_qty", "order_increment_qty"}
_FLOAT_FIELDS = {"cost_price", "rrp"}


def _now() -> str:
    # naive UTC ISO — matches the rest of the app and the /products/changes delta cursor (both use
    # datetime.utcnow()). An aware "+00:00" suffix makes that string-comparison cursor fragile.
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _eff(basic_cost, upp):
    """Effective unit cost via the canonical formula (get_unit_cost). None when an input is missing/zero."""
    if basic_cost is None or upp in (None, 0):
        return None
    return get_unit_cost(SimpleNamespace(basic_cost=basic_cost, units_per_pack=upp))


# ── Derivation ──────────────────────────────────────────────────────────────
def derive(item: models.CatalogueItem, ps=None) -> dict:
    """Candidate field values re-derived from the item's retained text. Deterministic. units_per_pack goes
    through the pack/cost guard; every other captured field is the value with string placeholders scrubbed.
    order_increment_qty is DERIVED: when we defer to a deliberate per-unit pack, the catalogue's pack count
    is a carton / order multiple (not the cost divisor), so it's captured as the supplier order-increment."""
    out = {}
    upp, _reason = pack.corrected_units_per_pack(item.raw_description, item.pack_size, item.uom, item.units_per_pack)
    out["units_per_pack"] = upp
    om = _as_int(upp)
    out["order_increment_qty"] = om if (om and om > 1 and _pack_protected(ps, upp)) else None
    for field, (kind, target_attr, item_attr) in _RECAPTURE.items():
        if field in ("units_per_pack", "order_increment_qty") or item_attr is None:
            continue
        raw = getattr(item, item_attr, None)
        out[field] = pack.clean_str(raw) if isinstance(raw, str) else raw
    # DC-3 backfill: when the item's supplier has a data contract, its invariants are AUTHORITATIVE over the
    # generic guard — re-parse re-applies the contract to already-onboarded rows (units_per_pack, order
    # multiple, consts). Uncontracted suppliers: load_contract → None → unchanged.
    contract = catalogue_contract.load_contract(getattr(item, "supplier_id", None))
    if contract is not None:
        out.setdefault("pack_size", item.pack_size)     # the enforce reads pack_size for the order-multiple parse
        contract._enforce(out)
        out.pop("pack_size", None)                       # pack_size is item-only, not a recapture target
    return out


def _candidate(cand: dict, field: str, committed: bool, current, ps=None, item=None, contracted=False):
    """The value re-parse proposes for `field`. Committed-SKU safeguards, so re-parse never corrupts a
    good live value from noisy catalogue data:
      · never overwrite a live value with an empty catalogue capture (keep the live value);
      · never recapture cost or rrp from a row whose cost > rrp — the columns are almost certainly
        swapped (see _cost_rrp_swapped). This holds even for a contracted supplier;
      · for an UNCONTRACTED supplier, defer to a deliberate pack size (see _pack_protected) and to a
        deliberately-set / verified cost (see _cost_protected) — the generic catalogue may be stale or
        mis-extracted. A CONTRACTED supplier's contract IS the source of truth for cost + pack (the
        catalogue is contract-guided and validated), so those two gates are bypassed and the contract's
        Gross-Wholesale cost / per-unit pack flow through even over a manual value."""
    val = cand[field]
    if committed and _norm(val) is None:
        return current
    if committed and field in ("cost_price", "rrp") and _cost_rrp_swapped(item):
        return current
    if not contracted:
        if committed and field == "units_per_pack" and _pack_protected(ps, val):
            return current
        if committed and field == "cost_price" and _cost_protected(ps):
            return current
    return val


def _current_value(db, item, field):
    """The value the candidate is diffed against: the live Product/ProductSupplier once the item is
    committed (matched_product_id set), else the catalogue_item's own field. Returns (value, target_obj)."""
    kind, target_attr, item_attr = _RECAPTURE[field]
    if item.matched_product_id:
        obj = db.get(models.Product, item.matched_product_id) if kind == "product" else _matched_ps(db, item)
        return (getattr(obj, target_attr, None) if obj else None), obj
    # pending — diff against the catalogue_item itself (derived fields have no catalogue attr → None)
    return (getattr(item, item_attr, None) if item_attr else None), item


def _matched_ps(db, item):
    """The ProductSupplier link for this item's product + supplier (None if unresolved)."""
    if not item.matched_product_id or not item.supplier_id:
        return None
    return (db.query(models.ProductSupplier)
            .filter(models.ProductSupplier.product_id == item.matched_product_id,
                    models.ProductSupplier.supplier_id == item.supplier_id)
            .first())


def _pack_protected(ps, candidate_upp=None) -> bool:
    """True when a units_per_pack change must NOT be proposed — the live pack size is a deliberate model
    that re-parse should defer to, so a per-unit price is never wrongly divided by an order multiple:
      · a human edit (pack_source == 'manual') or a verified pack size (uom_verified_at set); or
      · the catalogue's pack count is already recorded as this SKU's order multiple / MOQ (an ordering
        term that, by design, does not feed the unit cost)."""
    if ps is None:
        return False
    if ps.pack_source == "manual" or getattr(ps, "uom_verified_at", None):
        return True
    if candidate_upp is not None and _norm(candidate_upp) is not None:
        for term in (getattr(ps, "order_increment_qty", None), getattr(ps, "minimum_order_qty", None)):
            if term is not None and _norm(term) == _norm(candidate_upp):
                return True
    return False


def _cost_protected(ps) -> bool:
    """A cost the team deliberately set or procurement verified — re-parse defers to it rather than
    overriding with a re-derived catalogue cost (which may be stale from an older import, or mis-extracted).
    Catalogue/sheet-sourced costs stay correctable (that's the point of a re-parse)."""
    return ps is not None and ps.cost_source in ("manual", "po_issued", "invoice_matched")


def _cost_rrp_swapped(item) -> bool:
    """A catalogue row whose cost exceeds its RRP almost certainly has the cost/price columns swapped
    (you buy below RRP). Don't recapture cost OR rrp from such a row."""
    if item is None:
        return False
    c, r = _as_float(getattr(item, "cost_price", None)), _as_float(getattr(item, "rrp", None))
    return c is not None and r is not None and r > 0 and c > r


def _sku_and_name(db, item):
    if item.matched_product_id:
        p = db.get(models.Product, item.matched_product_id)
        if p:
            return p.sku_code, p.name
    return item.assigned_sku, (item.raw_description or "")


def _cost_preview(changed_cost: dict, live_basic, live_upp):
    """(eff_before, eff_after): effective unit cost now vs. after the item's cost recaptures apply.
    `changed_cost` holds the proposed new cost/upp for whichever cost field actually changed."""
    resolved_basic = changed_cost.get("cost_price", live_basic)
    resolved_upp = changed_cost.get("units_per_pack", live_upp)
    return _round(_eff(live_basic, live_upp)), _round(_eff(resolved_basic, resolved_upp))


def item_snapshot(db, item: models.CatalogueItem) -> list[dict]:
    """Every display field for one item: Current (live source) vs Re-parsed (recapture candidate). All
    fields, changed or not — the card's context. Cost-affecting rows carry a before/after effective cost."""
    committed = bool(item.matched_product_id)
    product = db.get(models.Product, item.matched_product_id) if committed else None
    ps = _matched_ps(db, item) if committed else None
    cand = derive(item, ps)
    contracted = catalogue_contract.load_contract(getattr(item, "supplier_id", None)) is not None
    live_basic = ps.basic_cost if ps else item.cost_price
    live_upp = ps.units_per_pack if ps else item.units_per_pack
    prelim, changed_cost = [], {}
    for group, key, prod_attr, ps_attr, item_attr in DISPLAY_FIELDS:
        if committed and ps_attr and ps is not None:
            cur = getattr(ps, ps_attr, None)
        elif committed and prod_attr and product is not None:
            cur = getattr(product, prod_attr, None)
        elif item_attr:
            cur = getattr(item, item_attr, None)
        else:                                     # derived field with no catalogue attr (order_increment_qty)
            cur = None
        rep = _candidate(cand, key, committed, cur, ps, item, contracted) if key in cand else cur   # item-only fields have no candidate
        changed = _norm(cur) != _norm(rep)
        if changed and key in _COST_FIELDS:
            changed_cost[key] = rep
        prelim.append((group, key, cur, rep, changed))
    eff_before, eff_after = _cost_preview(changed_cost, live_basic, live_upp)
    rows = []
    for group, key, cur, rep, changed in prelim:
        row = {"group": group, "field": key, "current": _s(cur), "reparsed": _s(rep),
               "changed": changed, "affects_cost": key in _COST_FIELDS,
               "editable": key in _RECAPTURE,      # recapturable → its value can be hand-edited before confirm
               "eff_cost_before": None, "eff_cost_after": None}
        if key in _COST_FIELDS and changed:
            row["eff_cost_before"], row["eff_cost_after"] = eff_before, eff_after
        rows.append(row)
    return rows


def compute_changes(db, item: models.CatalogueItem) -> list[dict]:
    """Per-field diff for one item: only fields whose candidate differs from the current value.
    Cost-affecting fields carry a before/after effective unit cost (reflecting all of the item's cost
    recaptures together, since cost and pack jointly set the effective cost)."""
    sku, name = _sku_and_name(db, item)
    committed = bool(item.matched_product_id)
    ps = _matched_ps(db, item) if committed else None
    cand = derive(item, ps)
    contracted = catalogue_contract.load_contract(getattr(item, "supplier_id", None)) is not None
    live_basic = ps.basic_cost if ps else item.cost_price
    live_upp = ps.units_per_pack if ps else item.units_per_pack
    staged, changed_cost = [], {}
    for field, (kind, target_attr, item_attr) in _RECAPTURE.items():
        # a committed cost/sku field with no resolvable supplier link can't be safely targeted → skip
        if committed and kind == "ps" and ps is None:
            continue
        old_val, _obj = _current_value(db, item, field)
        new_val = _candidate(cand, field, committed, old_val, ps, item, contracted)
        if _norm(old_val) == _norm(new_val):
            continue
        if field in _COST_FIELDS:
            changed_cost[field] = new_val
        staged.append((field, old_val, new_val))
    eff_before, eff_after = _cost_preview(changed_cost, live_basic, live_upp)
    changes = []
    for field, old_val, new_val in staged:
        row = {
            "catalogue_item_id": item.id, "product_id": item.matched_product_id, "committed": committed,
            "sku_code": sku, "product_name": name, "field": field,
            "old_value": _s(old_val), "new_value": _s(new_val),
            "affects_cost": field in _COST_FIELDS, "eff_cost_before": None, "eff_cost_after": None,
        }
        if field in _COST_FIELDS:
            row["eff_cost_before"], row["eff_cost_after"] = eff_before, eff_after
        changes.append(row)
    return changes


# ── Manual edit (adjust a value before confirm) ─────────────────────────────
def _refresh_cost_preview(db, batch, item):
    """Recompute this item's before/after effective unit cost across its PENDING cost-field changes and
    stamp it on those change rows — so the card's cost preview stays right after a hand-edit to cost/pack."""
    ps = _matched_ps(db, item) if item.matched_product_id else None
    live_basic = ps.basic_cost if ps else item.cost_price
    live_upp = ps.units_per_pack if ps else item.units_per_pack
    cost_changes = (db.query(models.ReparseChange)
                    .filter(models.ReparseChange.batch_id == batch.id,
                            models.ReparseChange.catalogue_item_id == item.id,
                            models.ReparseChange.field.in_(tuple(_COST_FIELDS)),
                            models.ReparseChange.status == "pending").all())
    changed_cost = {c.field: _coerce(c.field, c.new_value) for c in cost_changes}
    eff_before, eff_after = _cost_preview(changed_cost, live_basic, live_upp)
    for c in cost_changes:
        c.eff_cost_before, c.eff_cost_after = eff_before, eff_after


def set_field_value(db, batch, item, field: str, raw_value):
    """Hand-set the value re-parse will save for one field on one already-in-review item, before confirm.
    Upserts the PENDING ReparseChange for (batch, item, field); when the value equals the current live
    value the pending change is removed (nothing left to save). Refreshes the item's cost preview and
    returns the change (or None when it became a no-op). Raises ValueError for a non-editable field. A
    committed SKU with no supplier link is fine — confirming its supplier-level field onboards the link.
    Nothing writes to live cost here — confirm still applies + re-verifies."""
    if field not in _RECAPTURE:
        raise ValueError(f"'{field}' can't be edited")
    committed = bool(item.matched_product_id)
    kind, _target_attr, _item_attr = _RECAPTURE[field]
    current, _obj = _current_value(db, item, field)
    # a committed SKU with no link for this catalogue's supplier can still be edited: confirming a
    # supplier-level field ONBOARDS the missing link (apply_change creates it from the captured values).
    # Baseline the edit against the captured value so the diff reads sensibly (e.g. 98 → 100, not — → 100).
    if _obj is None and committed and kind == "ps" and _item_attr:
        current = getattr(item, _item_attr, None)
    typed = _coerce(field, raw_value)
    existing = (db.query(models.ReparseChange)
                .filter(models.ReparseChange.batch_id == batch.id,
                        models.ReparseChange.catalogue_item_id == item.id,
                        models.ReparseChange.field == field,
                        models.ReparseChange.status == "pending").first())
    if _norm(typed) == _norm(current):                     # edited back to the live value → no change to save
        if existing:
            db.delete(existing)
        db.flush()
        _refresh_cost_preview(db, batch, item)
        return None
    if existing:
        existing.new_value = _s(typed)
        ch = existing
    else:
        ch = models.ReparseChange(
            batch_id=batch.id, catalogue_item_id=item.id, product_id=item.matched_product_id,
            field=field, old_value=_s(current), new_value=_s(typed),
            affects_cost=1 if field in _COST_FIELDS else 0, status="pending")
        db.add(ch)
    db.flush()
    _refresh_cost_preview(db, batch, item)
    return ch


# ── Apply (confirm) ─────────────────────────────────────────────────────────
def _onboard_ps(db, item) -> models.ProductSupplier:
    """Create the missing ProductSupplier link for a committed item, seeded from its captured catalogue
    values (cost / pack / supplier_sku), so a re-parse can ONBOARD a matched SKU that never had this
    supplier's link. The confirmed field then writes over its own attribute on top."""
    ps = models.ProductSupplier(
        product_id=item.matched_product_id, supplier_id=item.supplier_id,
        supplier_sku=item.supplier_sku, basic_cost=item.cost_price, units_per_pack=item.units_per_pack,
        cost_source="catalogue", pack_source="catalogue", updated_at=_now())
    db.add(ps)
    db.flush()
    return ps


def apply_change(db, change: models.ReparseChange, operator: str | None) -> str:
    """Apply one confirmed change. Re-verifies the live value first; a drifted row is marked 'stale' and
    skipped (never overwrites a newer edit). A committed SKU missing this supplier's link has it onboarded
    from the captured values. Returns the new status ('confirmed' | 'stale' | 'rejected')."""
    item = db.get(models.CatalogueItem, change.catalogue_item_id)
    if item is None:
        change.status = "stale"
        return "stale"
    field = change.field
    if field not in _RECAPTURE:
        change.status = "rejected"
        return "rejected"
    live, target = _current_value(db, item, field)
    if target is None and item.matched_product_id and item.supplier_id and _RECAPTURE[field][0] == "ps":
        target = _onboard_ps(db, item)                     # matched SKU with no supplier link → create it
    elif _s(live) != change.old_value:                     # drifted since the diff was staged
        change.status = "stale"
        return "stale"
    if target is None:
        change.status = "stale"
        return "stale"
    _kind, target_attr, _item_attr = _RECAPTURE[field]
    new_typed = _coerce(field, change.new_value)
    setattr(target, target_attr, new_typed)
    now = _now()
    if isinstance(target, models.ProductSupplier):
        if field == "units_per_pack":
            target.pack_source = "manual"        # a human-confirmed pack — protect from Sheet re-sync
        if field == "cost_price":
            target.cost_source = "manual"        # a human-confirmed cost — protect from Sheet re-sync
            if hasattr(target, "cost_updated_at"):
                target.cost_updated_at = now
        if field == "order_increment_qty":
            # a captured order multiple needs a UOM + provenance (docs/product-vs-supplier-fields.md)
            if not target.order_increment_uom:
                prod = db.get(models.Product, item.matched_product_id)
                target.order_increment_uom = (prod.uom if prod and prod.uom else "sellable_unit")
            if not target.minimum_order_source:
                target.minimum_order_source = "inferred_from_order_multiple"
        target.updated_at = now
        # bump the parent Product's updated_at so the live inventory delta-feed surfaces this change
        # (/products/changes keys off Product.updated_at) — a supplier-only write (cost/pack/sku) is
        # otherwise invisible to the All-Inventory list until a hard reload.
        parent = db.get(models.Product, item.matched_product_id)
        if parent is not None:
            parent.updated_at = now
    elif isinstance(target, models.Product):
        target.updated_at = now
    else:  # catalogue_item (pending)
        item.updated_at = now if hasattr(item, "updated_at") else None
    item.parser_version = PARSER_VERSION
    item.reparsed_at = now
    item.reparse_source = "text"
    entity_type = ("product_supplier" if isinstance(target, models.ProductSupplier)
                   else "product" if isinstance(target, models.Product) else "catalogue_item")
    audit_log.record(db, action="catalogue.reparse_apply", actor=None,
                     entity_type=entity_type, entity_id=getattr(target, "id", None),
                     entity_label=_sku_and_name(db, item)[0],
                     details={"operator": operator, "field": field, "old": change.old_value,
                              "new": change.new_value, "catalogue_item_id": item.id,
                              "affects_cost": bool(change.affects_cost)})
    change.status = "confirmed"
    change.confirmed_by = operator
    change.confirmed_at = now
    return "confirmed"


# ── small value helpers ─────────────────────────────────────────────────────
def _s(v):
    return None if v is None else str(v)


def _norm(v):
    """Compare old/new leniently: None and '' are equal; numeric-looking values compare by number so
    75 == 75.0 (no spurious diff); other strings compare by trimmed text."""
    if v is None or (isinstance(v, str) and v.strip() == ""):
        return None
    s = str(v).strip()
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else repr(f)
    except (TypeError, ValueError):
        return s


def _as_int(v):
    try:
        return int(float(v)) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _as_float(v):
    try:
        return float(v) if v is not None and str(v).strip() != "" else None
    except (TypeError, ValueError):
        return None


def _coerce(field, v):
    if v is None or str(v).strip() == "":
        return None
    if field in _INT_FIELDS:
        return _as_int(v)
    if field in _FLOAT_FIELDS:
        return _as_float(v)
    return str(v)


def _round(x):
    return round(x, 4) if isinstance(x, (int, float)) else x

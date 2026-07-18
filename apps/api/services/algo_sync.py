"""Pull real sales + inventory/expiry from the algo-dashboard Postgres into IMS.

Read-only on algo-dashboard; upserts SalesVelocity (real multi-channel demand) and replaces
ExpiryTracking (real batch/expiry) for IMS's own SKUs. Configured with
ALGO_DASHBOARD_DATABASE_URL (unset -> sync is disabled and the endpoint 400s).

Sales come from commerce_order (clinic/DaySmart + HKTV + Shopify). weekly_demand is stored
combined (drives WOC) plus a per-channel split (weekly_demand_clinic/hktv/shopify).

Caveats:
  - We deliberately do NOT overwrite warehouse StockLevel — that has an existing source
    (sheet sync); picking commerce_inventory as the stock SoT is a separate decision.
"""
import json
import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

import models

_ALGO_URL = os.environ.get("ALGO_DASHBOARD_DATABASE_URL", "")


def is_configured() -> bool:
    return bool(_ALGO_URL)


def _engine():
    # pool_pre_ping recycles dead conns; short pool for an occasional batch job.
    return create_engine(_ALGO_URL, pool_pre_ping=True, pool_size=2, max_overflow=2)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sku_to_id(db) -> dict[str, int]:
    return {r.sku_code: r.id for r in db.query(models.Product.sku_code, models.Product.id).all()}


# ── Sales: 120d units per SKU per channel -> per-channel weekly demand ─────────────────
# commerce_order is the multi-channel table (clinic/DaySmart, HKTV, Shopify). What counts as a
# sale per channel: Shopify PAID/PARTIALLY_REFUNDED; DaySmart Paid + Open (placed = demand);
# HKTV has no financial_status (every row is a completed sale).
_PLATFORM_CHANNEL = {"daysmart": "clinic", "hktvmall": "hktv", "shopify": "shopify"}
_SALES_SQL = text("""
    SELECT sku_id AS sku, platform, to_char(order_date, 'YYYY-MM') AS ym, SUM(quantity) AS units
    FROM commerce_order
    WHERE order_date >= now() - interval '120 days'
      AND COALESCE(sku_id, '') <> ''
      AND quantity > 0
      AND platform IN ('daysmart', 'hktvmall', 'shopify')
      AND ( platform = 'hktvmall'
         OR (platform = 'daysmart' AND UPPER(COALESCE(financial_status, '')) IN ('PAID', 'OPEN'))
         OR (platform = 'shopify'  AND UPPER(COALESCE(financial_status, '')) IN ('PAID', 'PARTIALLY_REFUNDED')) )
    GROUP BY sku_id, platform, to_char(order_date, 'YYYY-MM')
""")


def _weekly(units: float) -> float:
    return round(units * 7.0 / 120.0, 3)


def _upsert_velocity(db, product_id: int, clinic: float, hktv: float, shopify: float, trend_json: str, now: str):
    combined = round(clinic + hktv + shopify, 3)
    sv = db.query(models.SalesVelocity).filter(models.SalesVelocity.product_id == product_id).first()
    if sv:
        sv.weekly_demand = combined
        sv.weekly_demand_clinic, sv.weekly_demand_hktv, sv.weekly_demand_shopify = clinic, hktv, shopify
        sv.trend_json = trend_json
        sv.period_days, sv.calculated_at, sv.source = 120, now, "algo_multichannel"
    else:
        db.add(models.SalesVelocity(
            product_id=product_id, weekly_demand=combined,
            weekly_demand_clinic=clinic, weekly_demand_hktv=hktv, weekly_demand_shopify=shopify,
            trend_json=trend_json, period_days=120, calculated_at=now, source="algo_multichannel"))


def sync_sales(db, engine) -> dict:
    id_by_sku = _sku_to_id(db)
    now = _now()
    with engine.connect() as c:
        rows = c.execute(_SALES_SQL).all()
    # Fold source rows (sku_id x platform x month) into per-SKU per-channel units + a monthly series.
    by_sku: dict[str, dict] = {}
    for row in rows:
        channel = _PLATFORM_CHANNEL.get(row.platform)
        if channel is None:
            continue
        units = float(row.units or 0)
        acc = by_sku.setdefault(str(row.sku), {"clinic": 0.0, "hktv": 0.0, "shopify": 0.0, "months": {}})
        acc[channel] += units
        if row.ym:
            acc["months"][row.ym] = acc["months"].get(row.ym, 0.0) + units
    matched = unmatched = 0
    channels_seen = {"clinic": 0, "hktv": 0, "shopify": 0}
    for sku, u in by_sku.items():
        pid = id_by_sku.get(sku)
        if pid is None:
            unmatched += 1
            continue
        clinic, hktv, shopify = _weekly(u["clinic"]), _weekly(u["hktv"]), _weekly(u["shopify"])
        trend = [[m, round(u["months"][m])] for m in sorted(u["months"])][-5:]   # last 5 months: [YYYY-MM, units]
        _upsert_velocity(db, pid, clinic, hktv, shopify, json.dumps(trend), now)
        matched += 1
        for ch, v in (("clinic", clinic), ("hktv", hktv), ("shopify", shopify)):
            if v > 0:
                channels_seen[ch] += 1
    db.commit()
    return {"sales_source_rows": len(rows), "sales_skus_matched": matched,
            "sales_skus_unmatched": unmatched, "sales_skus_by_channel": channels_seen}


# ── Inventory: commerce_inventory batches -> ExpiryTracking ───────────────────────────
_EXPIRY_SQL = text("""
    SELECT sku_id,
           COALESCE(lot_serial_number, '') AS lot,
           expiration_date,
           SUM(quantity)                    AS qty,
           COALESCE(merchant_name, location, '') AS loc
    FROM commerce_inventory
    WHERE quantity > 0 AND expiration_date IS NOT NULL
    GROUP BY sku_id, lot_serial_number, expiration_date, merchant_name, location
""")


def sync_inventory(db, engine) -> dict:
    id_by_sku = _sku_to_id(db)
    now = _now()
    with engine.connect() as c:
        rows = c.execute(_EXPIRY_SQL).all()

    to_add, synced_pids, unmatched = [], set(), 0
    for row in rows:
        pid = id_by_sku.get(str(row.sku_id))
        if pid is None:
            unmatched += 1
            continue
        synced_pids.add(pid)
        to_add.append(models.ExpiryTracking(
            product_id=pid,
            batch_ref=(row.lot or None),
            expiry_date=str(row.expiration_date),
            qty=float(row.qty or 0),
            location=(row.loc or None),
            created_at=now,
        ))

    # Snapshot semantics: clear the synced products' expiry rows, then insert fresh.
    if synced_pids:
        db.query(models.ExpiryTracking).filter(
            models.ExpiryTracking.product_id.in_(synced_pids)
        ).delete(synchronize_session=False)
    db.add_all(to_add)
    db.commit()
    return {"expiry_source_rows": len(rows), "expiry_batches_written": len(to_add),
            "expiry_skus_matched": len(synced_pids), "expiry_skus_unmatched": unmatched}


def run_algo_sync(db) -> dict:
    """Run both syncs. Raises RuntimeError if not configured."""
    if not is_configured():
        raise RuntimeError("ALGO_DASHBOARD_DATABASE_URL is not set — algo-dashboard sync is disabled.")
    engine = _engine()
    try:
        started = _now()
        out = {"started_at": started, **sync_sales(db, engine), **sync_inventory(db, engine),
               "finished_at": _now(),
               "note": "Sales are multi-channel (clinic + HKTV + Shopify) from commerce_order. "
                       "Warehouse stock intentionally not overwritten."}
        return out
    finally:
        engine.dispose()

"""
Google Sheet → IMS sync service.

Primary source: DATABASE [SSOT] → RECORD | SKU MASTER LIST (gid=8428031)
Secondary source: DATABASE [SSOT] → INVENTORY | HKTV (gid=TODO — provide tab GID)

Every field synced records its source file, tab, and column so the UI can
display clickable audit links back to the exact sheet location.
"""
import csv
import io
import re
import json
import os
import urllib.request
from datetime import datetime

from database import SessionLocal
import models

# ── Source registry ────────────────────────────────────────────────────────────
# These are displayed in the UI as clickable audit links.
# GIDs marked TODO: open the tab in your browser and read the gid= from the URL.

SHEET_ID = "18WUxJQZ9srms7S1oga6mrAdeH1QCA2pBsaJBUfwFRTQ"
SHEET_BASE = f"https://docs.google.com/spreadsheets/d/{SHEET_ID}"

SOURCES = {
    "sku_master": {
        "file":    "DATABASE [SSOT]",
        "tab":     "RECORD | SKU MASTER LIST",
        "gid":     "8428031",
        "url":     f"{SHEET_BASE}/edit#gid=8428031",
        "fields":  [
            "sku_code", "name", "brand", "supplier", "supplier_code", "supplier_barcode",
            "category", "status", "uom", "catalogue_cost", "daysmart_cost",
            "bulk_buy_cost", "mbb_terms", "cost_last_updated",
            "clinic_selling_price", "shopify_selling_price",
            "has_dispensing_fee", "hktv_channel_fee", "weight_g",
            "hero_sku", "clinic_qty", "warehouse_qty", "weekly_demand",
        ],
    },
    "hktv_inventory": {
        "file":   "DATABASE [SSOT]",
        "tab":    "INVENTORY | HKTV",
        "gid":    "1141197811",
        "url":    f"{SHEET_BASE}/edit#gid=1141197811",
        "fields": ["hktv_selling_price"],
    },
}

SKU_MASTER_CSV = f"{SHEET_BASE}/export?format=csv&gid=8428031"
HKTV_INV_CSV   = f"{SHEET_BASE}/export?format=csv&gid=1141197811"

LAST_SYNC_PATH = os.path.join(os.path.dirname(__file__), "..", "last_sync.json")

STATUS_MAP = {
    "ONLINE":  "ACTIVE",
    "OFFLINE": "INACTIVE",
    "":        "ACTIVE",
}

CATEGORY_MAP = {
    "Medicine":     "Medicine",
    "Preventative": "Preventative",
    "Supplement":   "Supplement",
    "Food":         "Food",
    "Pet Hygiene":  "Pet Hygiene",
    "Toys":         "Toys",
    "Cat Litter":   "Cat Litter",
    "Shampoo":      "Shampoo",
    "Not-For-Sale": "Not-For-Sale",
}


# ── Column detection ───────────────────────────────────────────────────────────

def _col(headers: list[str], *patterns: str) -> str | None:
    """Return first header matching any pattern (case-insensitive, newlines→spaces)."""
    for pat in patterns:
        for h in headers:
            normalised = h.lower().replace("\n", " ").replace("  ", " ").strip()
            if pat.lower() in normalised:
                return h
    return None


# ── Value cleaners ─────────────────────────────────────────────────────────────

def clean_price(val) -> float | None:
    if not val:
        return None
    cleaned = re.sub(r"[HKD$,\s]", "", str(val).strip())
    try:
        f = float(cleaned)
        return f if f > 0 else None
    except (ValueError, TypeError):
        return None


def clean_qty(val) -> float:
    if not val:
        return 0.0
    cleaned = re.sub(r"[,\s]", "", str(val).strip())
    try:
        return max(0.0, float(cleaned))
    except (ValueError, TypeError):
        return 0.0


def clean_pct(val) -> float | None:
    """Parse percentage: '8%' → 0.08, '0.08' → 0.08."""
    if not val:
        return None
    s = str(val).strip().replace("%", "")
    try:
        f = float(s)
        return f / 100 if f > 1 else f
    except (ValueError, TypeError):
        return None


# ── Fetch ──────────────────────────────────────────────────────────────────────

def _fetch_csv(url: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        raw = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(raw)))


# ── Core sync ──────────────────────────────────────────────────────────────────

def run_sync() -> dict:
    rows = _fetch_csv(SKU_MASTER_CSV)
    if not rows:
        return {"error": "No rows fetched from DATABASE [SSOT] → RECORD | SKU MASTER LIST"}

    headers = list(rows[0].keys())

    # ── Column detection against actual DATABASE [SSOT] column names ───────────
    C_SKU         = "SKU ID"
    C_NAME        = "SKU NAME"
    C_BRAND       = "Brand"
    C_SUPPLIER    = "Supplier"
    C_SUP_CODE    = "Supplier Code"
    C_BARCODE     = "Supplier Barcode"
    C_STATUS      = "status"
    C_HKTV_FLAG   = "HKTV?"
    C_HERO        = _col(headers, "hero sku")
    C_CATEGORY    = _col(headers, "item category")
    C_UOM         = _col(headers, "unit of measurement", "uom")
    C_DISP_FEE    = _col(headers, "dispensing fee")
    C_WEIGHT      = _col(headers, "weight (g)", "weight(g)", "weight (grams)")

    # Selling prices
    C_SELL_CLI    = _col(headers, "selling price from daysmart")
    C_SELL_SHOP   = _col(headers, "selling price from shopify")
    # HKTV selling price comes from INVENTORY | HKTV tab (separate fetch, GID pending)

    # Costs — two separate sources, both stored for reconciliation
    C_CAT_COST    = _col(headers, "wholesale cost (basic)", "wholesale cost \n(basic)")      # manually entered from catalogue
    C_DS_COST     = _col(headers, "last known cost per daysmart")                            # from DaySmart POS system
    C_QTY_BOX     = _col(headers, "qty / box", "qty/box", "units per box", "pack size")
    C_BULK_COST   = _col(headers, "wholesale cost (max bulk buy)", "max bulk buy")
    C_MBB_TERMS   = _col(headers, "mbb terms")
    C_COST_DATE   = _col(headers, "supplier cost last review date", "cost last review")
    C_HKTV_FEE   = _col(headers, "channel fee from hktv", "channel fee")

    # Stock (embedded in SKU master)
    C_STOCK_CLI   = _col(headers, "base quantity from daysmart")
    C_STOCK_SHOP  = _col(headers, "stp soh")

    # Sales
    C_SALES_120   = _col(headers, "past 120 day sales", "120 day sales")

    db = SessionLocal()
    now = datetime.utcnow().isoformat()
    today = datetime.utcnow().strftime("%Y-%m-%d")

    seeded = updated = skipped = 0
    missing_cost = 0        # products with no cost in either source
    cost_discrepancies = 0  # products where catalogue_cost ≠ daysmart_cost
    locked_cost_skips = 0   # seed skipped because IMS already holds a cost (any tier above 'sheet')
    locked_pack_skips = 0   # seed skipped because pack size is protected (OCR/manual or uom_verified_at)

    try:
        cat_rules_map = {r.category: r for r in db.query(models.CategoryRule).all()}

        for row in rows:
            sku_code = (row.get(C_SKU) or "").strip()
            name     = (row.get(C_NAME) or "").strip()

            if not sku_code:
                skipped += 1
                continue

            raw_cat  = (row.get(C_CATEGORY) or "").strip() if C_CATEGORY else ""
            # Case-insensitive lookup so "medicine" and "Medicine" both resolve correctly
            _cat_map_lower = {k.lower(): v for k, v in CATEGORY_MAP.items()}
            category = _cat_map_lower.get(raw_cat.lower(), raw_cat or "Uncategorized")
            storage_rule = "clinic_only" if category == "Medicine" else "any"

            raw_status = (row.get(C_STATUS) or "").strip().upper()
            status = STATUS_MAP.get(raw_status, "ACTIVE")

            brand    = (row.get(C_BRAND) or "").strip() or None
            hero_raw = (row.get(C_HERO) or "").strip().upper() if C_HERO else ""
            hero_sku = 1 if hero_raw in ("YES", "Y", "1", "TRUE") else 0
            uom_raw  = (row.get(C_UOM) or "").strip() or None if C_UOM else None
            weight_g = clean_qty(row.get(C_WEIGHT) or "") if C_WEIGHT else None
            weight_g = weight_g if weight_g and weight_g > 0 else None

            # Upsert product
            product = db.query(models.Product).filter(
                models.Product.sku_code == sku_code
            ).first()

            if not product:
                product = models.Product(
                    sku_code=sku_code,
                    name=name or f"SKU {sku_code}",
                    brand=brand,
                    category=category,
                    uom=uom_raw,
                    storage_rule=storage_rule,
                    status=status,
                    hero_sku=hero_sku,
                    weight_g=weight_g,
                    created_at=now,
                    updated_at=now,
                )
                db.add(product)
                db.flush()
                seeded += 1
            else:
                product.name         = name or product.name
                product.brand        = brand or product.brand
                product.category     = category
                product.storage_rule = storage_rule
                product.status       = status
                product.hero_sku     = hero_sku
                if uom_raw:    product.uom      = uom_raw
                if weight_g:   product.weight_g = weight_g
                product.updated_at   = now
                updated += 1

            pid = product.id

            # ── Supplier + costs ───────────────────────────────────────────────
            supplier_name = (row.get(C_SUPPLIER) or "").strip()
            supplier_code_raw = (row.get(C_SUP_CODE) or "").strip() or None
            supplier = None
            if supplier_name:
                supplier = db.query(models.Supplier).filter(
                    models.Supplier.name.ilike(f"%{supplier_name}%")
                ).first()
                if not supplier:
                    # Auto-create supplier from sheet data
                    code = supplier_code_raw or re.sub(r'[^A-Z0-9]', '', supplier_name.upper())[:8] or f"SUP{abs(hash(supplier_name)) % 10000}"
                    # Ensure code is unique
                    existing_codes = {s.code for s in db.query(models.Supplier.code).all()}
                    base_code = code
                    suffix = 2
                    while code in existing_codes:
                        code = f"{base_code[:6]}{suffix}"
                        suffix += 1
                    supplier = models.Supplier(
                        code=code,
                        name=supplier_name,
                        created_at=now,
                    )
                    db.add(supplier)
                    db.flush()

            supplier_sku = (row.get(C_SUP_CODE) or "").strip() or None
            barcode      = (row.get(C_BARCODE) or "").strip() or None

            # Both cost sources — stored separately for audit/reconciliation
            catalogue_cost = clean_price(row.get(C_CAT_COST) or "") if C_CAT_COST else None
            daysmart_cost  = clean_price(row.get(C_DS_COST) or "")  if C_DS_COST  else None

            # Effective cost: catalogue preferred, DaySmart as fallback
            basic_cost = catalogue_cost or daysmart_cost

            if basic_cost is None:
                missing_cost += 1

            bulk_cost  = clean_price(row.get(C_BULK_COST) or "") if C_BULK_COST else None
            mbb_terms  = (row.get(C_MBB_TERMS) or "").strip() or None if C_MBB_TERMS else None
            cost_date  = (row.get(C_COST_DATE) or "").strip() or None if C_COST_DATE else None

            units_per_pack = None
            if C_QTY_BOX:
                raw_qty = (row.get(C_QTY_BOX) or "").strip()
                try:
                    v = int(float(raw_qty)) if raw_qty else None
                    units_per_pack = v if v and v > 0 else None
                except (ValueError, TypeError):
                    units_per_pack = None

            if supplier or basic_cost or supplier_sku or catalogue_cost or daysmart_cost:
                ps = db.query(models.ProductSupplier).filter(
                    models.ProductSupplier.product_id == pid
                ).first()
                if ps:
                    # ── Shadow column: always record what the Sheet says ──────────
                    ps.basic_cost_sheet  = basic_cost   # shadow — never used in GP calculations

                    # ── Sheet sync is a one-time SEED ────────────────────────────
                    # It only writes cost into rows IMS has never touched
                    # (cost_source still 'sheet'). Every IMS-originated tier —
                    # manual, po_issued, invoice_matched, and the human-reviewed
                    # OCR catalogue flow — outranks the seed and is protected.
                    if (ps.cost_source or 'sheet') == 'sheet':
                        if basic_cost is not None:
                            ps.basic_cost  = basic_cost
                            ps.cost_source = 'sheet'
                    else:
                        locked_cost_skips += 1

                    # ── Pack size: Sheet sync is a one-time SEED ─────────────────
                    # Always record the shadow. Only seed the live value when IMS has
                    # never set it (pack_source still 'sheet') and no human has verified
                    # it. OCR catalogue ('catalogue') and manual edits ('manual') are protected.
                    if units_per_pack is not None:
                        ps.units_per_pack_sheet = units_per_pack  # shadow
                        if (ps.pack_source or 'sheet') == 'sheet' and ps.uom_verified_at is None:
                            ps.units_per_pack = units_per_pack
                            ps.pack_source    = 'sheet'
                        else:
                            locked_pack_skips += 1                # protected — keep IMS value

                    # MBB is relational now (mbb_terms table) — no longer imported as flat scalars.
                    if supplier_sku:           ps.supplier_sku  = supplier_sku
                    if barcode:                ps.barcode       = barcode
                    if supplier:               ps.supplier_id   = supplier.id
                    if cost_date:              ps.updated_at    = cost_date
                    ps.is_primary = 1
                else:
                    # First insert — shadow = live (no conflict possible yet)
                    db.add(models.ProductSupplier(
                        product_id=pid,
                        supplier_id=supplier.id if supplier else None,
                        supplier_sku=supplier_sku,
                        barcode=barcode,
                        basic_cost=basic_cost,
                        basic_cost_sheet=basic_cost,
                        units_per_pack=units_per_pack,
                        units_per_pack_sheet=units_per_pack,
                        cost_source='sheet',   # one-time seed tier — overridden by any IMS edit
                        pack_source='sheet',   # one-time seed tier — overridden by OCR / manual edit
                        is_primary=1,
                        updated_at=cost_date or now,
                    ))

            # ── Channels ───────────────────────────────────────────────────────
            clinic_price  = clean_price(row.get(C_SELL_CLI) or "")  if C_SELL_CLI  else None
            shopify_price = clean_price(row.get(C_SELL_SHOP) or "") if C_SELL_SHOP else None
            disp_raw      = (row.get(C_DISP_FEE) or "").strip().upper() if C_DISP_FEE else ""
            has_dispensing = 1 if disp_raw in ("YES", "Y", "1") else 0
            hktv_active    = (row.get(C_HKTV_FLAG) or "").strip().upper() in ("YES", "Y", "1")
            hktv_fee       = clean_pct(row.get(C_HKTV_FEE) or "") if C_HKTV_FEE else None

            if clinic_price is not None:
                _upsert_channel(db, pid, "clinic", clinic_price, has_dispensing, None, now)
            if shopify_price is not None:
                _upsert_channel(db, pid, "shopify", shopify_price, 0, None, now)
            if hktv_active:
                # Selling price populated in second pass from INVENTORY | HKTV tab
                _upsert_channel(db, pid, "hktv", None, 0, hktv_fee, now)

            # ── Stock levels ───────────────────────────────────────────────────
            clinic_qty    = clean_qty(row.get(C_STOCK_CLI) or "")  if C_STOCK_CLI  else 0.0
            warehouse_qty = clean_qty(row.get(C_STOCK_SHOP) or "") if C_STOCK_SHOP else 0.0
            _upsert_stock(db, pid, "clinic",    clinic_qty,    today, now)
            _upsert_stock(db, pid, "warehouse", warehouse_qty, today, now)

            # ── Sales velocity ─────────────────────────────────────────────────
            if C_SALES_120:
                sales_120 = clean_qty(row.get(C_SALES_120) or "")
                if sales_120 > 0:
                    weekly = round(sales_120 / 120 * 7, 2)
                    _upsert_velocity(db, pid, weekly, now)

        db.commit()

    except Exception as e:
        db.rollback()
        return {"error": str(e)}
    finally:
        db.close()

    # ── Second pass: HKTV selling prices from INVENTORY | HKTV tab ────────────
    hktv_updated = 0
    try:
        hktv_rows = _fetch_csv(HKTV_INV_CSV)
        if hktv_rows:
            hktv_headers = list(hktv_rows[0].keys())
            C_HKTV_SKU   = _col(hktv_headers, "sku id", "sku_id")
            C_HKTV_PRICE = _col(hktv_headers, "selling price")
            if C_HKTV_SKU and C_HKTV_PRICE:
                db2 = SessionLocal()
                try:
                    for row in hktv_rows:
                        sku_code    = (row.get(C_HKTV_SKU) or "").strip()
                        hktv_price  = clean_price(row.get(C_HKTV_PRICE) or "")
                        if not sku_code or hktv_price is None:
                            continue
                        product = db2.query(models.Product).filter(
                            models.Product.sku_code == sku_code
                        ).first()
                        if not product:
                            continue
                        pc = db2.query(models.ProductChannel).filter(
                            models.ProductChannel.product_id == product.id,
                            models.ProductChannel.channel == "hktv",
                        ).first()
                        if pc:
                            pc.selling_price = hktv_price
                            pc.updated_at    = now
                            hktv_updated += 1
                    db2.commit()
                except Exception:
                    db2.rollback()
                finally:
                    db2.close()
    except Exception:
        pass   # HKTV fetch failure doesn't abort the whole sync

    result = {
        "synced_at":           now,
        "rows_fetched":        len(rows),
        "seeded":              seeded,
        "updated":             updated,
        "skipped":             skipped,
        "missing_cost":        missing_cost,
        "cost_discrepancies":  cost_discrepancies,
        "locked_cost_skips":   locked_cost_skips,   # costs protected from Sheet overwrite
        "locked_pack_skips":   locked_pack_skips,   # pack sizes protected from Sheet overwrite
        "hktv_prices_updated": hktv_updated,
        "sources": {
            k: {
                "file": v["file"],
                "tab":  v["tab"],
                "url":  v["url"],
                "gid":  v["gid"],
            }
            for k, v in SOURCES.items()
        },
    }

    try:
        with open(LAST_SYNC_PATH, "w") as f:
            json.dump(result, f)
    except Exception:
        pass

    return result


def read_last_sync() -> dict | None:
    try:
        with open(LAST_SYNC_PATH) as f:
            return json.load(f)
    except Exception:
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _upsert_channel(db, product_id, channel, selling_price, has_dispensing, channel_fee_pct, now):
    pc = db.query(models.ProductChannel).filter(
        models.ProductChannel.product_id == product_id,
        models.ProductChannel.channel == channel,
    ).first()
    if pc:
        if selling_price is not None:   pc.selling_price    = selling_price
        pc.has_dispensing_fee = has_dispensing
        if channel_fee_pct is not None: pc.channel_fee_pct  = channel_fee_pct
        pc.updated_at = now
    else:
        db.add(models.ProductChannel(
            product_id=product_id,
            channel=channel,
            is_active=1,
            selling_price=selling_price,
            has_dispensing_fee=has_dispensing,
            channel_fee_pct=channel_fee_pct,
            updated_at=now,
        ))


def _upsert_stock(db, product_id, location, qty, as_of_date, now):
    sl = db.query(models.StockLevel).filter(
        models.StockLevel.product_id == product_id,
        models.StockLevel.location == location,
    ).first()
    if sl:
        sl.qty = qty
        sl.as_of_date = as_of_date
        sl.updated_at = now
    else:
        db.add(models.StockLevel(
            product_id=product_id,
            location=location,
            qty=qty,
            as_of_date=as_of_date,
            source="sheet_sync",
            updated_at=now,
        ))


def _upsert_velocity(db, product_id, weekly_demand, now):
    sv = db.query(models.SalesVelocity).filter(
        models.SalesVelocity.product_id == product_id,
    ).first()
    if sv:
        sv.weekly_demand = weekly_demand
        sv.calculated_at = now
        sv.source = "sheet_sync"
    else:
        db.add(models.SalesVelocity(
            product_id=product_id,
            weekly_demand=weekly_demand,
            period_days=120,
            calculated_at=now,
            source="sheet_sync",
        ))

"""[Run inside the algo-dashboard web container: docker exec -i algo-dashboard-web-1 python manage.py shell < this]

Extract a unified SKU list from the three sales platforms (runs in algo-dashboard).

Sources (all refreshed by this morning's daily downloads):
  shopify  core_product (full catalogue incl. ARCHIVED/DRAFT, variant-level from raw JSON)
  daysmart exports/daysmart_inventory.csv  (clinic items; Class=Service rows skipped)
  hktv     exports/hktv_inventory.xlsx     (SKU Status OFFLINE -> not available)

Writes /tmp/platform_items.json: [{source, sku, name, brand, category_hint, barcode,
price, weight_grams, available, status}]
"""
import csv
import json

items = []

# ── Shopify ──────────────────────────────────────────────────────────────────
from core.models import Product as CoreProduct

n_prod = 0
for p in CoreProduct.objects.all().iterator(chunk_size=500):
    raw = p.raw if isinstance(p.raw, dict) else {}
    status = (p.status or raw.get("status") or "").strip().lower()
    available = status == "active"
    variants = raw.get("variants") or [{}]
    n_prod += 1
    for v in variants:
        if not isinstance(v, dict):
            continue
        vt = (v.get("title") or "").strip()
        name = p.title if vt in ("", "Default Title") else f"{p.title} - {vt}"
        grams = v.get("grams")
        price = v.get("price")
        items.append({
            "source": "shopify",
            "sku": (v.get("sku") or "").strip(),
            "name": (name or "").strip(),
            "brand": (p.vendor or "").strip(),
            "category_hint": (p.product_type or "").strip(),
            "barcode": (v.get("barcode") or "").strip() or None,
            "price": float(price) if price not in (None, "") else None,
            "weight_grams": float(grams) if grams else None,
            "available": available,
            "status": status or "unknown",
        })
print(f"shopify: {n_prod} products -> {len(items)} variants")

# ── DaySmart ─────────────────────────────────────────────────────────────────
n0 = len(items)
skipped_services = 0
with open("/app/exports/daysmart_inventory.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if (r.get("Class") or "").strip().lower() == "service":
            skipped_services += 1
            continue
        name = (r.get("Item") or "").strip()
        if not name:
            continue
        price = (r.get("Unit Price") or "").strip()
        items.append({
            "source": "daysmart",
            "sku": (r.get("SKU") or "").strip(),
            "name": name,
            "brand": "",
            "category_hint": f"{(r.get('Category') or '').strip()}/{(r.get('Sub Category') or '').strip()}",
            "barcode": None,
            "price": float(price.replace(",", "")) if price else None,
            "weight_grams": None,
            "available": True,          # DaySmart export only lists current items
            "status": "active",
        })
print(f"daysmart: {len(items) - n0} items ({skipped_services} services skipped)")

# ── HKTV Mall ────────────────────────────────────────────────────────────────
import openpyxl

n0 = len(items)
wb = openpyxl.load_workbook("/app/exports/hktv_inventory.xlsx", read_only=True)
ws = wb.active
rows = ws.iter_rows(values_only=True)
hdr = [str(h or "").strip() for h in next(rows)]
ix = {h: i for i, h in enumerate(hdr)}

def col(r, name):
    i = ix.get(name)
    if i is None or i >= len(r) or r[i] is None:
        return ""
    return str(r[i]).strip()

for r in rows:
    sku = col(r, "Sku ID")
    name = col(r, "SKU Name") or col(r, "SKU Name Chi")
    if not sku and not name:
        continue
    status = col(r, "SKU Status").upper()
    items.append({
        "source": "hktv",
        "sku": sku,
        "name": name,
        "brand": col(r, "Brand"),
        "category_hint": col(r, "Primary Category Code"),
        "barcode": None,
        "price": None,
        "weight_grams": None,
        "available": status not in ("OFFLINE", "INACTIVE", "DELETED"),
        "status": status.lower() or "online",
    })
print(f"hktv: {len(items) - n0} rows")

json.dump(items, open("/tmp/platform_items.json", "w"))
print(f"TOTAL: {len(items)} platform items -> /tmp/platform_items.json")

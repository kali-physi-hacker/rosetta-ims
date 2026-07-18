"""[Runs in algo-dashboard container] Build platform_items_fresh.json from LIVE platform pulls:
/app/exports/shopify_bulk.jsonl (Shopify Admin API bulk export, fetched moments ago)
/app/exports/daysmart_inventory.csv + hktv_inventory.xlsx (fresh API/portal pulls)
"""
import csv
import json

items = []

# Shopify bulk JSONL: product lines, then variant lines carrying __parentId
prods = {}
with open("/app/exports/shopify_bulk.jsonl") as f:
    for line in f:
        o = json.loads(line)
        if o.get("id", "").startswith("gid://shopify/Product/"):
            prods[o["id"]] = o
        elif o.get("__parentId"):
            p = prods.get(o["__parentId"], {})
            status = (p.get("status") or "").strip().lower()
            vt = (o.get("title") or "").strip()
            name = p.get("title", "") if vt in ("", "Default Title") else f"{p.get('title','')} - {vt}"
            items.append({
                "source": "shopify",
                "sku": (o.get("sku") or "").strip(),
                "name": name.strip(),
                "brand": (p.get("vendor") or "").strip(),
                "barcode": (o.get("barcode") or "").strip() or None,
                "price": float(o["price"]) if o.get("price") not in (None, "") else None,
                "cost": (lambda uc: float(uc["amount"]) if uc and uc.get("amount") not in (None, "") else None)((o.get("inventoryItem") or {}).get("unitCost")),
                "weight_grams": None,
                "available": status == "active",
                "status": status or "unknown",
            })
print(f"shopify: {len(prods)} products -> {len(items)} variants")

n0 = len(items)
from commerce.models import InventorySnapshot
from django.db.models import Max
_lat = InventorySnapshot.objects.filter(source="daysmart").aggregate(m=Max("snapshot_date"))["m"]
ds_cost = {}
for r in InventorySnapshot.objects.filter(source="daysmart", snapshot_date=_lat).exclude(avg_unit_cost=None):
    k = ((r.raw or {}).get("SKU") or "").strip() if isinstance(r.raw, dict) else ""
    if k:
        ds_cost[k] = float(r.avg_unit_cost)
with open("/app/exports/daysmart_inventory.csv", encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        if (r.get("Class") or "").strip().lower() == "service":
            continue
        name = (r.get("Item") or "").strip()
        if not name:
            continue
        price = (r.get("Unit Price") or "").strip()
        items.append({"source": "daysmart", "sku": (r.get("SKU") or "").strip(), "name": name,
                      "brand": "", "barcode": None,
                      "price": float(price.replace(",", "")) if price else None,
                      "cost": ds_cost.get((r.get("SKU") or "").strip()),
                      "weight_grams": None, "available": True, "status": "active"})
print(f"daysmart: {len(items) - n0}")

n0 = len(items)
import openpyxl
wb = openpyxl.load_workbook("/app/exports/hktv_inventory.xlsx", read_only=True)
ws = wb.active
rows = ws.iter_rows(values_only=True)
hdr = [str(h or "").strip() for h in next(rows)]
ix = {h: i for i, h in enumerate(hdr)}
def col(r, name):
    i = ix.get(name)
    return str(r[i]).strip() if i is not None and i < len(r) and r[i] is not None else ""
for r in rows:
    sku, name = col(r, "Sku ID"), col(r, "SKU Name") or col(r, "SKU Name Chi")
    if not sku and not name:
        continue
    status = col(r, "SKU Status").upper()
    _c = col(r, "Cost")
    try: _cost = float(_c.replace(",", "")) if _c else None
    except ValueError: _cost = None
    items.append({"source": "hktv", "sku": sku, "name": name, "brand": col(r, "Brand"),
                  "barcode": None, "price": None, "cost": _cost, "weight_grams": None,
                  "available": status not in ("OFFLINE", "INACTIVE", "DELETED"),
                  "status": status.lower() or "online"})
print(f"hktv: {len(items) - n0}")

json.dump(items, open("/app/exports/platform_items_fresh.json", "w"))
print(f"TOTAL fresh: {len(items)}")

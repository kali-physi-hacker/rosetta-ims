"""[Run inside the rosetta api container with /tmp/platform_items.json present:
  docker exec -i [-e APPLY=1] backend-api-1 python < this]

Consolidate platform SKUs (shopify / daysmart / hktv) into the rosetta inventory.

Pure data operation — no schema changes. For every platform item not already present
(matched by SKU code, then barcode, then normalised/alnum name), create a Product row.
Items archived (Shopify) / OFFLINE (HKTV) on ALL their platforms are created as INACTIVE.
Existing products are never modified. Dry-run by default; APPLY=1 env writes.
"""
import json
import os
import re
from collections import Counter
from datetime import datetime

import database
import models
from services.tagging_service import _heuristic

APPLY = os.environ.get("APPLY") == "1"
NOW = datetime.utcnow().isoformat()
TODAY = NOW[:10]

nk = lambda s: " ".join((s or "").lower().split())
ak = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
nsku = lambda s: (s or "").strip().upper()

items = json.load(open("/tmp/platform_items.json"))

# ── 1. Dedupe platform items into consolidated entries ───────────────────────
SRC_ORDER = {"shopify": 0, "daysmart": 1, "hktv": 2}
entries: dict[str, dict] = {}
junk_skipped = 0
for it in sorted(items, key=lambda x: SRC_ORDER[x["source"]]):
    nm = (it["name"] or "").lstrip()
    if nm.startswith("#") or "test product" in nm.lower():   # store test artifacts
        junk_skipped += 1
        continue
    key = ("sku:" + nsku(it["sku"])) if nsku(it["sku"]) else ("name:" + ak(it["name"]))
    if key in ("sku:", "name:"):
        continue
    e = entries.setdefault(key, {"sku": nsku(it["sku"]), "name": "", "brand": "", "barcode": None,
                                 "price": None, "weight_grams": None, "sources": [],
                                 "available": False, "statuses": {}})
    e["sources"].append(it["source"])
    e["available"] = e["available"] or bool(it["available"])
    e["statuses"][it["source"]] = it["status"]
    if not e["name"] and it["name"]:
        e["name"] = it["name"]
    if not e["brand"] and it["brand"]:
        e["brand"] = it["brand"]
    if not e["barcode"] and it.get("barcode"):
        e["barcode"] = it["barcode"]
    if e["price"] is None and it.get("price") is not None:
        e["price"] = it["price"]
    if e["weight_grams"] is None and it.get("weight_grams"):
        e["weight_grams"] = it["weight_grams"]

print(f"platform items: {len(items)} -> consolidated entries: {len(entries)} (junk skipped: {junk_skipped})")

# ── 2. Index the current rosetta inventory ────────────────────────────────────
db = database.SessionLocal()
products = db.query(models.Product).all()
sku_set = {nsku(p.sku_code) for p in products}
nk_map = {nk(p.name): p for p in products}
ak_map = {ak(p.name): p for p in products}
bar_map = {}
for ps in db.query(models.ProductSupplier).filter(models.ProductSupplier.barcode.isnot(None)).all():
    b = (ps.barcode or "").strip()
    if b:
        bar_map.setdefault(b, ps.product_id)
print(f"rosetta: {len(products)} products, {len(bar_map)} barcodes")

# ── 3. Match ──────────────────────────────────────────────────────────────────
matched = Counter()
to_create = []
active_but_platform_dead = 0
for e in entries.values():
    p = None
    how = None
    if e["sku"] and e["sku"] in sku_set:
        how = "sku"
    elif e["barcode"] and e["barcode"] in bar_map:
        how = "barcode"
    elif nk(e["name"]) and nk(e["name"]) in nk_map:
        how, p = "name", nk_map[nk(e["name"])]
    elif ak(e["name"]) and ak(e["name"]) in ak_map:
        how, p = "name", ak_map[ak(e["name"])]
    if how:
        matched[how] += 1
        if p is not None and not e["available"] and p.status == "ACTIVE":
            active_but_platform_dead += 1
    else:
        to_create.append(e)

src_mix = Counter("+".join(sorted(set(e["sources"]))) for e in to_create)
status_mix = Counter("ACTIVE" if e["available"] else "INACTIVE" for e in to_create)
print(f"matched: {dict(matched)} (total {sum(matched.values())})")
print(f"existing ACTIVE products whose platform listing is archived/offline (untouched): {active_but_platform_dead}")
print(f"to create: {len(to_create)}  by status: {dict(status_mix)}")
print(f"  by source mix: {dict(src_mix)}")

# ── 4. Create (or preview) ────────────────────────────────────────────────────
PRF = {"shopify": "SP", "daysmart": "DS", "hktv": "HK"}
used = set(sku_set)
created = 0
samples = []
for e in to_create:
    sku = e["sku"]
    if not sku or sku in used:
        base = f"{PRF[e['sources'][0]]}-{(ak(e['name']) or 'item')[:30].upper()}"
        sku, n = base, 2
        while sku in used:
            sku = f"{base}-{n}"
            n += 1
    used.add(sku)
    name = e["name"] or f"[{e['sources'][0]}] {sku}"
    heur = _heuristic({"description": name, "brand": e["brand"]})
    status = "ACTIVE" if e["available"] else "INACTIVE"
    if len(samples) < 12:
        samples.append(f"  {status:8} {sku:24.24} {name[:52]:52} [{'+'.join(sorted(set(e['sources'])))}] cat={heur.get('category') or 'Others'}")
    if APPLY:
        db.add(models.Product(
            sku_code=sku,
            name=name[:300],
            brand=(e["brand"] or None),
            category=heur.get("category") or "Others",
            subcategory=heur.get("subcategory"),
            rrp=e["price"],
            weight_g=e["weight_grams"] if (e["weight_grams"] or 0) > 0 else None,
            storage_rule="any",
            status=status,
            hero_sku=0,
            notes=f"Imported via platform consolidation ({'+'.join(sorted(set(e['sources'])))}, "
                  f"platform status: {e['statuses']}) on {TODAY}",
            created_at=NOW,
            updated_at=NOW,
        ))
        created += 1
        if created % 500 == 0:
            db.flush()

print("sample creations:")
print("\n".join(samples))

if APPLY:
    from services import audit_log
    audit_log.record(db, action="product.bulk_import", entity_type="product",
                     entity_label="platform consolidation",
                     details={"created": created, "matched": dict(matched),
                              "by_status": dict(status_mix), "sources": dict(src_mix)})
    db.commit()
    print(f"\nAPPLIED: created {created} products")
else:
    print("\nDRY RUN — set APPLY=1 to write")

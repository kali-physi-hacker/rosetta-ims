"""[Runs in rosetta api container; needs /tmp/platform_items_fresh.json]

Reconcile inventory against FRESH platform pulls (Shopify Admin API, DaySmart API,
HKTV portal export) — replacing the earlier dashboard-copy-based consolidation:
  1. create platform items still missing from inventory
  2. rows created by the earlier consolidation that the live platforms do NOT have
     -> deleted if they are bare shells (no suppliers/channels/stock), else flagged
  3. status corrections on consolidation-created rows (live availability wins)
Hand-entered / pre-existing products are never touched. APPLY=1 writes.
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
nk = lambda s: " ".join((s or "").lower().split())
ak = lambda s: re.sub(r"[^a-z0-9]+", "", (s or "").lower())
nsku = lambda s: (s or "").strip().upper()

items = json.load(open("/tmp/platform_items_fresh.json"))

# consolidate fresh items (same rules as the original import)
SRC_ORDER = {"shopify": 0, "daysmart": 1, "hktv": 2}
entries, junk = {}, 0
for it in sorted(items, key=lambda x: SRC_ORDER[x["source"]]):
    nm = (it["name"] or "").lstrip()
    if nm.startswith("#") or "test product" in nm.lower():
        junk += 1
        continue
    key = ("sku:" + nsku(it["sku"])) if nsku(it["sku"]) else ("name:" + ak(it["name"]))
    if key in ("sku:", "name:"):
        continue
    e = entries.setdefault(key, {"sku": nsku(it["sku"]), "name": "", "brand": "", "barcode": None,
                                 "price": None, "available": False, "sources": [], "statuses": {}, "costs": {}})
    e["sources"].append(it["source"])
    e["available"] = e["available"] or bool(it["available"])
    e["statuses"][it["source"]] = it["status"]
    for k_src, k_dst in (("name", "name"), ("brand", "brand")):
        if not e[k_dst] and it[k_src]:
            e[k_dst] = it[k_src]
    if not e["barcode"] and it.get("barcode"):
        e["barcode"] = it["barcode"]
    if e["price"] is None and it.get("price") is not None:
        e["price"] = it["price"]
    if it.get("cost") is not None and it["source"] not in e["costs"]:
        e["costs"][it["source"]] = it["cost"]
print(f"fresh: {len(items)} items -> {len(entries)} entries (junk {junk})")

# fresh lookup maps
avail_by_sku, avail_by_name = {}, {}
for e in entries.values():
    if e["sku"]:
        avail_by_sku[e["sku"]] = avail_by_sku.get(e["sku"], False) or e["available"]
    if e["name"]:
        a = ak(e["name"])
        avail_by_name[a] = avail_by_name.get(a, False) or e["available"]

db = database.SessionLocal()
products = db.query(models.Product).all()
sku_set = {nsku(p.sku_code) for p in products}
sku_map = {nsku(p.sku_code): p for p in products}
by_id = {p.id: p for p in products}
nk_map = {nk(p.name): p for p in products}
ak_map = {ak(p.name): p for p in products}
bar_map = {}
for ps in db.query(models.ProductSupplier).filter(models.ProductSupplier.barcode.isnot(None)).all():
    b = (ps.barcode or "").strip()
    if b:
        bar_map.setdefault(b, ps.product_id)
imported = [p for p in products if (p.notes or "").startswith("Imported via platform consolidation")]
linked_pids = set()
for tbl, col in ((models.ProductSupplier, "product_id"), (models.ProductChannel, "product_id"),
                 (models.StockLevel, "product_id")):
    for (pid,) in db.query(getattr(tbl, col)).distinct().all():
        linked_pids.add(pid)
print(f"rosetta: {len(products)} products ({len(imported)} from earlier consolidation)")

# 1. fresh entries missing from inventory -> create; matched -> collect platform statuses
RANK = {"active": 3, "online": 3, "draft": 2}          # prefer the most-alive status per platform
matched = Counter()
to_create = []
plat: dict[int, dict] = {}                              # product_id -> {source: status}
for e in entries.values():
    p = None
    if e["sku"] and e["sku"] in sku_set:
        matched["sku"] += 1
        p = sku_map[e["sku"]]
    elif e["barcode"] and e["barcode"] in bar_map:
        matched["barcode"] += 1
        p = by_id.get(bar_map[e["barcode"]])
    elif nk(e["name"]) and nk(e["name"]) in nk_map:
        matched["name"] += 1
        p = nk_map[nk(e["name"])]
    elif ak(e["name"]) and ak(e["name"]) in ak_map:
        matched["name"] += 1
        p = ak_map[ak(e["name"])]
    else:
        to_create.append(e)
    if p is not None:
        d = plat.setdefault(p.id, {})
        for src, st in e["statuses"].items():
            if RANK.get(st, 1) >= RANK.get(d.get(src), 0):
                d[src] = st
        for src, c in e["costs"].items():
            d.setdefault("__cost_" + src, c)
print(f"matched: {dict(matched)}; to create from fresh: {len(to_create)}; products w/ platform status: {len(plat)}")

# 2+3. audit earlier-imported rows against the live platforms
stale, flips = [], []
for p in imported:
    s, a = nsku(p.sku_code), ak(p.name)
    in_fresh = (s in avail_by_sku) or (a in avail_by_name)
    if not in_fresh:
        stale.append(p)
        continue
    avail = avail_by_sku.get(s) if s in avail_by_sku else avail_by_name.get(a)
    want = "ACTIVE" if avail else "INACTIVE"
    if p.status != want and p.status != "DISCONTINUED":
        flips.append((p, want))
stale_shells = [p for p in stale if p.id not in linked_pids]
stale_linked = [p for p in stale if p.id in linked_pids]
print(f"earlier-imported rows NOT on live platforms: {len(stale)} "
      f"(shells to delete: {len(stale_shells)}, linked->flag only: {len(stale_linked)})")
print(f"status corrections on imported rows: {len(flips)}")
for p, w in flips[:8]:
    print(f"  flip {p.sku_code} {p.name[:44]!r} {p.status} -> {w}")
for p in stale_shells[:8]:
    print(f"  stale {p.sku_code} {p.name[:50]!r}")

created = 0
if APPLY:
    used = set(sku_set)
    PRF = {"shopify": "SP", "daysmart": "DS", "hktv": "HK"}
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
        db.add(models.Product(
            sku_code=sku, name=name[:300], brand=(e["brand"] or None),
            category=heur.get("category") or "Others", subcategory=heur.get("subcategory"),
            rrp=e["price"], storage_rule="any",
            status="ACTIVE" if e["available"] else "INACTIVE", hero_sku=0,
            shopify_status=e["statuses"].get("shopify"),
            shopify_cost=e["costs"].get("shopify"),
            daysmart_cost=e["costs"].get("daysmart"),
            hktv_cost=e["costs"].get("hktv"),
            daysmart_status=e["statuses"].get("daysmart"),
            hktv_status=e["statuses"].get("hktv"),
            notes=f"Imported via platform consolidation (LIVE {'+'.join(sorted(set(e['sources'])))}, "
                  f"status: {e['statuses']}) on {NOW[:10]}",
            created_at=NOW, updated_at=NOW))
        created += 1
    for p in stale_shells:
        db.delete(p)
    for p in stale_linked:
        p.notes = (p.notes or "") + f" | NOT FOUND on live platforms {NOW[:10]}"
        p.updated_at = NOW
    for p, want in flips:
        p.status = want
        p.updated_at = NOW
    # Per-platform listing status on every matched product (annotation only — no
    # updated_at bump, so the realtime delta feed isn't flooded by 3k rows).
    plat_set = 0
    for pid, d in plat.items():
        p = by_id[pid]
        changed = False
        for field, src in (("shopify_status", "shopify"), ("daysmart_status", "daysmart"),
                           ("hktv_status", "hktv"),
                           ("shopify_cost", "__cost_shopify"), ("daysmart_cost", "__cost_daysmart"),
                           ("hktv_cost", "__cost_hktv")):
            val = d.get(src)
            if val is not None and getattr(p, field) != val:
                setattr(p, field, val)
                changed = True
        plat_set += 1 if changed else 0
    print(f"platform-status annotations written: {plat_set}")
    from services import audit_log
    audit_log.record(db, action="product.bulk_reconcile", entity_type="product",
                     entity_label="live platform reconciliation",
                     details={"created": created, "stale_deleted": len(stale_shells),
                              "stale_flagged": len(stale_linked), "status_flips": len(flips),
                              "fresh_matched": dict(matched)})
    db.commit()
    print(f"\nAPPLIED: +{created} created, -{len(stale_shells)} stale shells deleted, "
          f"{len(flips)} statuses corrected, {len(stale_linked)} flagged")
else:
    print("\nDRY RUN — APPLY=1 to write")

# -*- coding: utf-8 -*-
"""Full Shopify ingest (last 12 months) via the Admin API — replaces the 22-row sample.
Aggregates orders -> customers; tags purchased products to care-types (customer-level, source=Shopify);
records LTV, order_count, tags, last order date; matches to existing SSOT customers by email/phone
(Shopify is the email<->phone bridge), else creates online-only customers."""
import sqlite3, io, sys, re, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.shopify_client import orders_last_12mo
from clientssot.taxonomy import classify
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

# aggregate orders -> per customer
custs = {}   # shopify_id -> {email, phone, first, last, ltv, orders, tags, products:set, last_order}
n_orders = 0
for o in orders_last_12mo():
    c = o["customer"]
    cgid = c.get("id")
    if not cgid:
        continue
    n_orders += 1
    e = custs.setdefault(cgid, {"email": (c.get("email") or "").strip(), "phone": (c.get("phone") or "").strip(),
        "first": c.get("firstName") or "", "last": c.get("lastName") or "",
        "ltv": float(c.get("amountSpent", {}).get("amount") or 0), "orders": int(c.get("numberOfOrders") or 0),
        "tags": ",".join(c.get("tags") or []), "products": set(), "last_order": ""})
    for t in o["products"]:
        e["products"].add(t)
    if o["created_at"] > e["last_order"]:
        e["last_order"] = o["created_at"]
    if n_orders % 300 == 0:
        print(f"  ...{n_orders} orders", flush=True)
print(f"orders={n_orders}  unique customers={len(custs)}", flush=True)

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_customer_caretags(
    customer_id TEXT, kind TEXT, main TEXT, sub TEXT, source TEXT, count INT,
    PRIMARY KEY(customer_id, kind, main, sub, source))""")
for coldef in ("ltv REAL", "order_count INT", "ext_tags TEXT", "shop_last_order TEXT"):
    try: cur.execute(f"ALTER TABLE clientssot_customers ADD COLUMN {coldef}")
    except sqlite3.OperationalError: pass
# clear prior Shopify data (sample + previous runs)
cur.execute("DELETE FROM clientssot_customer_caretags WHERE source='Shopify'")
cur.execute("DELETE FROM clientssot_customers WHERE id LIKE 'SHOP:%'")

phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}
email2cust = {r[1].lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}

matched = new = tagged = 0
for cgid, e in custs.items():
    cid = (email2cust.get(e["email"].lower()) if e["email"] else None)
    if not cid and e["phone"]:
        cid = phone2cust.get(norm_phone(e["phone"]))
    if cid:
        matched += 1
    else:
        cid = "SHOP:" + cgid.split("/")[-1]
        new += 1
        cur.execute("""INSERT INTO clientssot_customers (id, first_name, last_name, last_visit, visit_count, source, email, phone)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", (cid, e["first"], e["last"], "", 0, "Shopify", e["email"], e["phone"]))
    # backfill email/phone onto matched clinic customers (Shopify is the email<->phone bridge for CRM matching)
    cur.execute("""UPDATE clientssot_customers SET ltv=?, order_count=?, ext_tags=?, shop_last_order=?,
                   email=CASE WHEN (email IS NULL OR email='') THEN ? ELSE email END,
                   phone=CASE WHEN (phone IS NULL OR phone='') THEN ? ELSE phone END
                   WHERE id=?""",
                (e["ltv"], e["orders"], e["tags"], e["last_order"][:10], e["email"], e["phone"], cid))
    seen = set()
    for title in e["products"]:
        kind, main, sub = classify(title)
        if kind and (main, sub) not in seen:
            seen.add((main, sub))
            cur.execute("""INSERT INTO clientssot_customer_caretags VALUES (?,?,?,?,?,?)
                ON CONFLICT(customer_id,kind,main,sub,source) DO UPDATE SET count=count+1""", (cid, kind, main, sub, "Shopify", 1))
            tagged += 1
con.commit()
print(f"\nmatched to existing (clinic/CRM) {matched}  new online-only {new}  care-tags written {tagged}")
print("Shopify care tags by main:")
for r in cur.execute("SELECT main,COUNT(DISTINCT customer_id) n FROM clientssot_customer_caretags WHERE source='Shopify' AND kind='care' GROUP BY main ORDER BY n DESC"):
    print(f"  {r[1]:>4}  {r[0]}")
con.close()
print("SHOPIFY FULL INGEST DONE")

# -*- coding: utf-8 -*-
"""Care-tag the FULL lifetime Shopify buyer base (what each customer bought -> care-types).
Reads orders lifetime via the lean CARETAG query; maps each order's customer to the SSOT (email/phone or
SHOP: id); writes customer-level care tags (source Shopify). Does NOT touch the customer base (that's the
customers ingest). Slow (order-bound, throttled) — meant to run in the background / overnight."""
import sqlite3, io, sys, re, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.shopify_client import order_caretags
from clientssot.taxonomy import classify
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

con = sqlite3.connect(DB); cur = con.cursor()
phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}
email2cust = {r[1].lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}
existing = {r[0] for r in cur.execute("SELECT id FROM clientssot_customers")}
con.close()

# accumulate care (main,sub) per customer across all their orders
tags = collections.defaultdict(set)   # cid -> {(main,sub)}
n_orders = matched = 0
for o in order_caretags():
    n_orders += 1
    cid = (email2cust.get(o["email"].lower()) if o["email"] else None)
    if not cid and o["phone"]:
        cid = phone2cust.get(norm_phone(o["phone"]))
    if not cid and o["cid"]:
        sid = "SHOP:" + o["cid"].split("/")[-1]
        if sid in existing:
            cid = sid
    if not cid:
        continue
    matched += 1
    for title in o["products"]:
        kind, main, sub = classify(title)
        if kind == "care":
            tags[cid].add((main, sub))
    if n_orders % 1000 == 0:
        print(f"  ...{n_orders} orders, {len(tags)} customers tagged", flush=True)

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("DELETE FROM clientssot_customer_caretags WHERE source='Shopify'")
rows = [(cid, "care", m, s, "Shopify", 1) for cid, mss in tags.items() for (m, s) in mss]
cur.executemany("""INSERT INTO clientssot_customer_caretags VALUES (?,?,?,?,?,?)
    ON CONFLICT(customer_id,kind,main,sub,source) DO UPDATE SET count=count+1""", rows)
con.commit()
print(f"\norders scanned {n_orders} | orders matched {matched} | customers care-tagged {len(tags)} | tags written {len(rows)}")
con.close()
print("SHOPIFY CARETAGS INGEST DONE")

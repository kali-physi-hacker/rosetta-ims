# -*- coding: utf-8 -*-
"""OPS: paid-but-unfulfilled Shopify orders. Stores BOTH a per-customer rollup (for the Ops column) and the
individual orders (name + created date) so the War-Room alerts can list specific Shopify order IDs by age."""
import sqlite3, io, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.shopify_client import orders_unfulfilled
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

con = sqlite3.connect(DB); cur = con.cursor()
for coldef in ("shop_unfulfilled INT", "shop_unfulfilled_oldest TEXT"):
    try: cur.execute(f"ALTER TABLE clientssot_customers ADD COLUMN {coldef}")
    except sqlite3.OperationalError: pass
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_unfulfilled_orders(
    order_name TEXT PRIMARY KEY, created TEXT, customer_id TEXT, name TEXT, phone TEXT)""")
cur.execute("UPDATE clientssot_customers SET shop_unfulfilled=NULL, shop_unfulfilled_oldest=NULL")
cur.execute("DELETE FROM clientssot_unfulfilled_orders")
phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}
email2cust = {r[1].lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}
name_of = {r[0]: f'{r[1]} {r[2]}'.strip() for r in cur.execute("SELECT id,first_name,last_name FROM clientssot_customers")}

agg = {}; order_rows = []; n = 0
for o in orders_unfulfilled():
    n += 1
    cid = (email2cust.get(o["email"].lower()) if o["email"] else None)
    if not cid and o["phone"]:
        cid = phone2cust.get(norm_phone(o["phone"]))
    if not cid and o["cid"]:
        cid = "SHOP:" + o["cid"].split("/")[-1]
    order_rows.append((o["order"], o["created"], cid, name_of.get(cid, ""), o["phone"]))
    if cid:
        a = agg.setdefault(cid, [0, o["created"]])
        a[0] += 1
        if o["created"] and o["created"] < a[1]:
            a[1] = o["created"]

cur.executemany("INSERT OR REPLACE INTO clientssot_unfulfilled_orders VALUES (?,?,?,?,?)", order_rows)
for cid, (cnt, oldest) in agg.items():
    cur.execute("UPDATE clientssot_customers SET shop_unfulfilled=?, shop_unfulfilled_oldest=? WHERE id=?", (cnt, oldest, cid))
con.commit()
print(f"unfulfilled paid orders: {n} | individual orders stored: {len(order_rows)} | customers: {len(agg)}")
con.close()
print("UNFULFILLED INGEST DONE")

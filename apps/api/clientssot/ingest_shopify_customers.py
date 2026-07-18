# -*- coding: utf-8 -*-
"""FULL lifetime Shopify buyer base via the cheap customers endpoint (no nested order pull, so it doesn't
get throttled like the order-by-order pull). Gets EVERY customer back to the store's start with lifetime
LTV + order count + tags + last-order date. Matches to existing SSOT customers (email/phone bridge),
creates online-only ones. (Per-customer care tags still come from the order pull — this is the buyer base.)"""
import sqlite3, io, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.shopify_client import customers_all
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

con = sqlite3.connect(DB); cur = con.cursor()
for coldef in ("ltv REAL", "order_count INT", "ext_tags TEXT", "shop_last_order TEXT"):
    try: cur.execute(f"ALTER TABLE clientssot_customers ADD COLUMN {coldef}")
    except sqlite3.OperationalError: pass

phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}
email2cust = {r[1].lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}
print(f"match maps: {len(phone2cust)} phones, {len(email2cust)} emails", flush=True)

seen = matched = new = buyers = 0
for c in customers_all():
    if not c["id"]:
        continue
    seen += 1
    buyer = c["orders"] >= 1
    if buyer:
        buyers += 1
    cid = (email2cust.get(c["email"].lower()) if c["email"] else None)
    if not cid and c["phone"]:
        cid = phone2cust.get(norm_phone(c["phone"]))
    if cid:
        matched += 1
    else:
        cid = "SHOP:" + c["id"].split("/")[-1]
        new += 1
        cur.execute("""INSERT INTO clientssot_customers (id, first_name, last_name, last_visit, visit_count, source, email, phone)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", (cid, c["first"], c["last"], "", 0, "Shopify", c["email"], c["phone"]))
    # buyers get LTV; non-buyer leads still get tags (for CRM/consent) + email/phone bridge, but no LTV
    cur.execute(f"""UPDATE clientssot_customers SET order_count=?, ext_tags=?, shop_last_order=?,
                   {'ltv=?,' if buyer else ''}
                   email=CASE WHEN (email IS NULL OR email='') THEN ? ELSE email END,
                   phone=CASE WHEN (phone IS NULL OR phone='') THEN ? ELSE phone END
                   WHERE id=?""",
                ([c["orders"], c["tags"], c["last_order"]] + ([c["ltv"]] if buyer else []) + [c["email"], c["phone"], cid]))
    if seen % 2000 == 0:
        print(f"  ...{seen} customers ({buyers} buyers)", flush=True)
        con.commit()
con.commit()
print(f"\ntotal customers pulled {seen}  | buyers (>=1 order) {buyers}  | matched to clinic/CRM {matched}  | new online-only {new}")
n_online = cur.execute("SELECT COUNT(*) FROM clientssot_customers WHERE ltv IS NOT NULL").fetchone()[0]
print(f"online buyers in SSOT now (ltv not null): {n_online}")
con.close()
print("SHOPIFY CUSTOMERS INGEST DONE")

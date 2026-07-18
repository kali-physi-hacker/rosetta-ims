# -*- coding: utf-8 -*-
"""Shopify purchases into the unified timeline (source='Shopify', with SKU), back to 2019. Also flags the
PSG 2020-2021 prescription audience (products whose Shopify SKU contains 'PSG' — the old Rx-partnership era).
Then recomputes first/last purchase + bought_rx across ALL sources. Long-running (order pull) -> run overnight."""
import sqlite3, io, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.shopify_client import order_purchases
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def norm_phone(s):
    d = re.sub(r"\D", "", s or ""); return d[-8:] if len(d) >= 8 else ""

def category(n):
    t = (n or "").lower()
    if any(w in t for w in ['prescription diet','veterinary diet','renal','urinary s/o',' s/o','gastrointest','hypoallergenic','anallergenic','satiety','metabolic','k/d','i/d','z/d','c/d','hepatic','diabetic','sensitiv']):
        return 'Prescription Diet'
    if any(w in t for w in ['nexgard','revolution','frontline','bravecto','milbemax','drontal','proheart','advocate','simparica','selehold','selamectin','heartgard','nobivac','vaccin','rabies','dewormer','endogard','broadline','milpro']):
        return 'Preventative'
    if any(w in t for w in ['shampoo','wash','wipe','ear clean','otic','toothpaste','hexarinse','plaqueoff','deodor','spray']):
        return 'Pet Hygiene'
    if any(w in t for w in ['supplement','nutra','omega','probiotic','glucosamine','joint','cosequin','denamarin','vitamin','antinol']):
        return 'Supplement'
    if any(w in t for w in ['food','kibble','treat','pouch','biscuit','dry ','wet ']):
        return 'Food'
    if any(w in t for w in ['tablet','capsule','injection','cream','ointment','drops','syrup','suspension',' mg',' ml','amoxyclav','apoquel','metacam','doxy','prednisolone','gabapentin','insulin',' tab','antibiotic']):
        return 'Medicine'
    return 'Other'

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_purchases(
    customer_id TEXT, date TEXT, product TEXT, qty REAL, price REAL, source TEXT,
    patient TEXT, category TEXT, on_shopify INT)""")
try: cur.execute("ALTER TABLE clientssot_purchases ADD COLUMN sku TEXT")
except sqlite3.OperationalError: pass
try: cur.execute("ALTER TABLE clientssot_purchases ADD COLUMN autoship INT")
except sqlite3.OperationalError: pass
try: cur.execute("ALTER TABLE clientssot_customers ADD COLUMN bought_psg INT")
except sqlite3.OperationalError: pass
cur.execute("DELETE FROM clientssot_purchases WHERE source='Shopify'")
cur.execute("UPDATE clientssot_customers SET bought_psg=NULL")

phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}
email2cust = {r[1].lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}

n_ord = 0; rows = []; psg_custs = set()
for o in order_purchases():
    n_ord += 1
    cid = (email2cust.get(o["email"].lower()) if o["email"] else None)
    if not cid and o["phone"]:
        cid = phone2cust.get(norm_phone(o["phone"]))
    if not cid and o["cid"]:
        cid = "SHOP:" + o["cid"].split("/")[-1]
    for it in o["items"]:
        if not it["title"]:
            continue
        sku = it["sku"] or ""
        if "psg" in sku.lower() and cid:
            psg_custs.add(cid)
        rows.append((cid, o["created"], it["title"], it["qty"], it.get("price", 0), "Shopify", "", category(it["title"]), 1, sku, it.get("autoship", 0)))
    if n_ord % 1000 == 0:
        print(f"  ...{n_ord} orders, {len(rows)} line items, {len(psg_custs)} PSG customers", flush=True)
        cur.executemany("INSERT INTO clientssot_purchases (customer_id,date,product,qty,price,source,patient,category,on_shopify,sku,autoship) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows); con.commit(); rows = []
cur.executemany("INSERT INTO clientssot_purchases (customer_id,date,product,qty,price,source,patient,category,on_shopify,sku,autoship) VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
for cid in psg_custs:
    cur.execute("UPDATE clientssot_customers SET bought_psg=1 WHERE id=?", (cid,))
con.commit()
print(f"orders {n_ord} | PSG audience (bought a PSG-SKU product): {len(psg_custs)}")

# recompute first/last purchase + bought_rx across ALL sources (now incl Shopify)
cur.execute("""UPDATE clientssot_customers SET
    first_purchase=(SELECT MIN(date) FROM clientssot_purchases p WHERE p.customer_id=clientssot_customers.id AND date!=''),
    last_purchase =(SELECT MAX(date) FROM clientssot_purchases p WHERE p.customer_id=clientssot_customers.id AND date!='')
    WHERE id IN (SELECT DISTINCT customer_id FROM clientssot_purchases WHERE customer_id IS NOT NULL)""")
cur.execute("""UPDATE clientssot_customers SET bought_rx=1 WHERE id IN
    (SELECT DISTINCT customer_id FROM clientssot_purchases WHERE category IN ('Medicine','Prescription Diet') AND customer_id IS NOT NULL)""")
con.commit()
tot = cur.execute("SELECT COUNT(*) FROM clientssot_purchases WHERE source='Shopify'").fetchone()[0]
print(f"Shopify line items stored {tot} | PSG customers {len(psg_custs)}")
con.close()
print("SHOPIFY PURCHASES INGEST DONE")

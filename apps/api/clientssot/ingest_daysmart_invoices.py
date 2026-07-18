# -*- coding: utf-8 -*-
"""ACTUAL clinic purchases from DaySmart invoices -> clientssot_purchases (the real signal: what each
client bought, when, qty, price, which pet). Classifies each product into Rosetta IMS categories and flags
whether it's sold on Shopify (so we can say 'buy it online'). Source = 'Ohana' (clinic POS)."""
import sqlite3, io, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.daysmart_client import paginate
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def category(n):
    t = (n or "").lower()
    if any(w in t for w in ['prescription diet','veterinary diet','renal','urinary s/o',' s/o','gastrointest','hypoallergenic','anallergenic','satiety','metabolic','k/d','i/d','z/d','c/d','hepatic','diabetic','dental diet','mobility diet','sensitiv']):
        return 'Prescription Diet'
    if any(w in t for w in ['nexgard','revolution','frontline','bravecto','milbemax','drontal','proheart','advocate','simparica','selehold','selamectin','heartgard','nobivac','vaccin','rabies','dhppi','fvrcp','dewormer','de-wormer','endogard','broadline','milpro']):
        return 'Preventative'
    if any(w in t for w in ['shampoo','wash','wipe','ear clean','otic','toothpaste','hexarinse','plaqueoff','deodor','cologne','spray']):
        return 'Pet Hygiene'
    if any(w in t for w in ['supplement','nutra','omega','probiotic','glucosamine','joint','cosequin','denamarin','vitamin','calcium','prebiotic','antinol']):
        return 'Supplement'
    if any(w in t for w in ['food','kibble','treat','pouch','biscuit','dry ','wet ']):
        return 'Food'
    if any(w in t for w in ['tablet','capsule','injection','cream','ointment','drops','syrup','suspension',' mg',' ml','amoxyclav','apoquel','metacam','meloxicam','doxy','prednisolone','gabapentin','insulin',' tab',' inj','antibiotic','vetmedin','fortekor']):
        return 'Medicine'
    return 'Other'

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_purchases(
    customer_id TEXT, date TEXT, product TEXT, qty REAL, price REAL, source TEXT,
    patient TEXT, category TEXT, on_shopify INT)""")
cur.execute("DELETE FROM clientssot_purchases WHERE source='Ohana'")
existing = {r[0] for r in cur.execute("SELECT id FROM clientssot_customers")}
# catalog tokens (Shopify-sellable) for on_shopify flag
catalog_tokens = set()
for (nm,) in cur.execute("SELECT DISTINCT name FROM clientssot_product_caretags"):
    for w in re.findall(r"[a-z]{4,}", (nm or "").lower()):
        catalog_tokens.add(w)
STOP = {"tablets","tablet","chew","chews","for","dogs","cats","with","veterinary","diet","plus","care"}
def on_shopify(name):
    toks = [w for w in re.findall(r"[a-z]{4,}", (name or "").lower()) if w not in STOP]
    return 1 if any(w in catalog_tokens for w in toks[:3]) else 0

rows = []; n_inv = matched = 0
for inv in paginate("invoices", per_page=100):
    cid = (inv.get("client") or {}).get("id")
    date = (inv.get("date") or "")[:10]
    n_inv += 1
    in_ssot = cid in existing
    if in_ssot:
        matched += 1
    for it in (inv.get("items") or []):
        name = it.get("displayName") or it.get("name") or ""
        if not name:
            continue
        rows.append((cid if in_ssot else None, (it.get("date") or date)[:10], name,
                     it.get("quantity") or 0, it.get("price") or 0, "Ohana",
                     (it.get("patient") or {}).get("name", ""), category(name), on_shopify(name)))
    if n_inv % 500 == 0:
        print(f"  ...{n_inv} invoices, {len(rows)} line items", flush=True)
        cur.executemany("INSERT INTO clientssot_purchases VALUES (?,?,?,?,?,?,?,?,?)", rows); con.commit(); rows = []
cur.executemany("INSERT INTO clientssot_purchases VALUES (?,?,?,?,?,?,?,?,?)", rows)
con.commit()
tot = cur.execute("SELECT COUNT(*) FROM clientssot_purchases WHERE source='Ohana'").fetchone()[0]
print(f"\ninvoices {n_inv} (matched to SSOT {matched}) | line items stored {tot}")
print("by category:")
for r in cur.execute("SELECT category, COUNT(*) n FROM clientssot_purchases WHERE source='Ohana' GROUP BY category ORDER BY n DESC"):
    print(f"  {r[1]:>6}  {r[0]}")
con.close()
print("DAYSMART INVOICES INGEST DONE")

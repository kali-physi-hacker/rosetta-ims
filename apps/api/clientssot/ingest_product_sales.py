# -*- coding: utf-8 -*-
"""Real product-sales-by-care-type from Shopify order line items (last 12 months).
Works WITHOUT read_customers (line items only). Powers the Campaign Planner's "feature these products" —
the actual best-sellers per care theme, so you know exactly what to put on the Skin & Coat offer."""
import sqlite3, io, sys, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.shopify_client import order_lineitems_last_12mo
from clientssot.taxonomy import classify
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

units = collections.Counter()   # title -> total quantity sold
lines = collections.Counter()   # title -> number of order lines (popularity)
n = 0
for title, qty in order_lineitems_last_12mo():
    if not title:
        continue
    n += 1
    units[title] += qty
    lines[title] += 1
    if n % 2000 == 0:
        print(f"  ...{n} line items", flush=True)
print(f"line items={n}  distinct products={len(units)}", flush=True)

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("DROP TABLE IF EXISTS clientssot_product_sales")
cur.execute("""CREATE TABLE clientssot_product_sales(
    name TEXT PRIMARY KEY, units INT, lines INT, main TEXT, sub TEXT)""")
rows, tagged = [], 0
for title, u in units.items():
    kind, main, sub = classify(title)
    rows.append((title, u, lines[title], main if kind == "care" else None, sub if kind == "care" else None))
    if kind == "care":
        tagged += 1
cur.executemany("INSERT OR REPLACE INTO clientssot_product_sales VALUES (?,?,?,?,?)", rows)
con.commit()
print(f"products stored={len(rows)} (care-tagged {tagged})")
print("\n=== top-selling care products per theme (what to feature) ===")
for main in [r[0] for r in cur.execute("SELECT DISTINCT main FROM clientssot_product_sales WHERE main IS NOT NULL")]:
    top = cur.execute("SELECT name, units FROM clientssot_product_sales WHERE main=? ORDER BY units DESC LIMIT 3", (main,)).fetchall()
    print(f"  {main}: " + " | ".join(f"{t[0][:42]} ({t[1]})" for t in top))
con.close()
print("PRODUCT SALES INGEST DONE")

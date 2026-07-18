# -*- coding: utf-8 -*-
"""Shopify collection -> product membership, so customers can be filtered by collection in the Demand Record."""
import sqlite3, io, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.shopify_client import collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("DROP TABLE IF EXISTS clientssot_collections")
cur.execute("CREATE TABLE clientssot_collections(collection TEXT, product TEXT, PRIMARY KEY(collection, product))")
rows = []; n_coll = 0
for c in collections():
    if not c["collection"]:
        continue
    n_coll += 1
    for p in c["products"]:
        if p:
            rows.append((c["collection"], p))
cur.executemany("INSERT OR IGNORE INTO clientssot_collections VALUES (?,?)", rows)
con.commit()
print(f"collections {n_coll} | collection-product rows {len(rows)}")
print("top collections by product count:")
for r in cur.execute("SELECT collection, COUNT(*) n FROM clientssot_collections GROUP BY collection ORDER BY n DESC LIMIT 10"):
    print(f"  {r[1]:>4}  {r[0]}")
con.close()
print("COLLECTIONS INGEST DONE")

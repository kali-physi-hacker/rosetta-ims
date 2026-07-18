# -*- coding: utf-8 -*-
"""Populate clientssot_purchases.family (interim product roll-up for the Demand Breakdown) + backfill any
assigned SKU that's prepended in an Ohana product name. Fast, in-place. Re-run after ingests (in refresh_all)."""
import sqlite3, io, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
from clientssot.families import product_family, leading_sku
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DB = resolve_db()

con = sqlite3.connect(DB); cur = con.cursor()
try: cur.execute("ALTER TABLE clientssot_purchases ADD COLUMN family TEXT")
except sqlite3.OperationalError: pass

rows = cur.execute("SELECT rowid, product, source, sku FROM clientssot_purchases").fetchall()
fam_upd, sku_upd = [], []
for rid, product, source, sku in rows:
    fam_upd.append((product_family(product), rid))
    if source == "Ohana" and not (sku or "").strip():
        ls = leading_sku(product)
        if ls:
            sku_upd.append((ls, rid))
cur.executemany("UPDATE clientssot_purchases SET family=? WHERE rowid=?", fam_upd)
cur.executemany("UPDATE clientssot_purchases SET sku=? WHERE rowid=?", sku_upd)
cur.execute("CREATE INDEX IF NOT EXISTS idx_pur_family ON clientssot_purchases(family)")
con.commit()
print(f"families set: {len(fam_upd)} rows | Ohana SKUs lifted from titles: {len(sku_upd)}")
print("distinct families:", cur.execute("SELECT COUNT(DISTINCT family) FROM clientssot_purchases").fetchone()[0],
      "(was", cur.execute("SELECT COUNT(DISTINCT product) FROM clientssot_purchases").fetchone()[0], "raw products)")
print("Revolution roll-up check:")
for r in cur.execute("""SELECT family, COUNT(DISTINCT customer_id) cl, COUNT(DISTINCT product) names,
        SUM(CASE WHEN source='Shopify' THEN COALESCE(price,0)*COALESCE(qty,1) ELSE 0 END) onl
        FROM clientssot_purchases WHERE family IN ('Revolution','Cerenia','NexGard','Frontline')
        GROUP BY family ORDER BY cl DESC"""):
    print(f"  {r[0]:<12} {r[1]:>5} clients · {r[2]} raw names merged · online ${round(r[3]):,}")
con.close()
print("FAMILIES DONE")

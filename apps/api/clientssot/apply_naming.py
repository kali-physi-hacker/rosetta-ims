# -*- coding: utf-8 -*-
"""Apply the standardised naming convention (naming.py) to the SSOT's stored list & flow names.
Refreshes flow names from Klaviyo by flow_id first (to pick up Seph's in-progress renames), then
canonicalises both flows and Klaviyo lists. Safe, in-place, re-runnable. Read-only on Klaviyo."""
import sqlite3, io, sys
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
from clientssot.klaviyo_client import BASE, HEADERS
from clientssot.naming import canonical
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DB = resolve_db()

# current Klaviyo flow id -> name (fast; picks up live renames)
flow_names = {}
url = f"{BASE}/flows/?fields%5Bflow%5D=name&page%5Bsize%5D=50"
while url:
    j = requests.get(url, headers=HEADERS, timeout=40).json()
    for f in j.get("data", []):
        flow_names[f["id"]] = f["attributes"].get("name") or f["id"]
    url = (j.get("links") or {}).get("next")

con = sqlite3.connect(DB); cur = con.cursor()
# flows: refresh from Klaviyo by id, then canonicalise
fl = cur.execute("SELECT DISTINCT flow_id, flow_name FROM clientssot_crm_flows").fetchall()
fmap = {}
for fid, fname in fl:
    raw = flow_names.get(fid, fname)
    fmap[(fid, fname)] = canonical(raw, "flow")
for (fid, fname), newn in fmap.items():
    cur.execute("UPDATE clientssot_crm_flows SET flow_name=? WHERE flow_id=?", (newn, fid))
# lists: canonicalise in place
ll = [r[0] for r in cur.execute("SELECT DISTINCT list_name FROM clientssot_customer_crm WHERE source='Klaviyo'")]
for old in ll:
    new = canonical(old, "list")
    if new != old:
        cur.execute("UPDATE clientssot_customer_crm SET list_name=? WHERE list_name=? AND source='Klaviyo'", (new, old))
con.commit()
print("FLOWS now:")
for r in cur.execute("SELECT flow_name, COUNT(DISTINCT customer_id) n FROM clientssot_crm_flows GROUP BY flow_name ORDER BY n DESC LIMIT 20"):
    print(f"  {r[1]:>5}  {r[0]}")
print("LISTS now:")
for r in cur.execute("SELECT list_name, COUNT(DISTINCT customer_id) n FROM clientssot_customer_crm WHERE source='Klaviyo' GROUP BY list_name ORDER BY n DESC"):
    print(f"  {r[1]:>6}  {r[0]}")
con.close()
print("APPLY NAMING DONE")

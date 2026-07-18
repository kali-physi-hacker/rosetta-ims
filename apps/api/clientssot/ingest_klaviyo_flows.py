# -*- coding: utf-8 -*-
"""CRM depth: pull Klaviyo FLOW sends + DISCOUNT redemptions per customer (the Seph-call ask — know which
flow each customer got, WHEN, for frequency/spam control, and which discount code they claimed).

- Received Email events (metric VZfpg5) -> per (customer, flow): send count + first/last date.
- Placed Order events (metric RuSypr) -> per (customer, discount code): redemption count + last date.

Stores clientssot_crm_flows + clientssot_crm_discounts. Read-only on Klaviyo. Scoped to >= START (the
flows are post-takeover era) and page-capped so it can't run away. Run overnight / in background."""
import sqlite3, io, sys, json, time, re
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
from clientssot.klaviyo_client import BASE, HEADERS
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
DB = resolve_db()

RECEIVED_EMAIL = "VZfpg5"
PLACED_ORDER = "RuSypr"
START = "2026-01-01T00:00:00Z"   # the flows' active era; frequency/spam signal is about RECENT sends
MAX_PAGES = 4000

def norm_phone(s):
    d = re.sub(r"\D", "", s or ""); return d[-8:] if len(d) >= 8 else ""

def get_flows():
    flows = {}; url = f"{BASE}/flows/?fields%5Bflow%5D=name&page%5Bsize%5D=50"
    while url:
        r = requests.get(url, headers=HEADERS, timeout=60); j = r.json()
        for f in j.get("data", []):
            flows[f["id"]] = f["attributes"].get("name") or f["id"]
        url = (j.get("links") or {}).get("next"); time.sleep(0.2)
    return flows

def events(metric_id, fields):
    """Yield (email, datetime, props) for a metric since START, resolving email via include=profile."""
    url = (f"{BASE}/events/?filter=greater-than(datetime,{START}),equals(metric_id,\"{metric_id}\")"
           f"&include=profile&fields%5Bevent%5D={fields}&fields%5Bprofile%5D=email&sort=-datetime&page%5Bsize%5D=200")
    n = 0
    while url and n < MAX_PAGES:
        r = None
        for attempt in range(6):
            try:
                r = requests.get(url, headers=HEADERS, timeout=90)
                if r.status_code == 200:
                    break
            except Exception:
                r = None
            time.sleep(min(2 ** attempt, 20))
        if r is None or r.status_code != 200:
            print(f"  giving up at page {n}: HTTP {getattr(r,'status_code','—')}", flush=True); return
        j = r.json()
        prof = {d["id"]: (d.get("attributes", {}).get("email") or "").strip().lower() for d in j.get("included", [])}
        for e in j.get("data", []):
            pid = (((e.get("relationships") or {}).get("profile") or {}).get("data") or {}).get("id")
            yield prof.get(pid, ""), (e["attributes"].get("datetime") or "")[:10], (e["attributes"].get("event_properties") or {})
        url = (j.get("links") or {}).get("next"); n += 1
        if n % 25 == 0:
            print(f"  ...{n} pages ({metric_id})", flush=True)
        time.sleep(0.1)

con = sqlite3.connect(DB); cur = con.cursor()
email2cust = {r[1].strip().lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}
print("flows…", flush=True)
flows = get_flows(); print(f"  {len(flows)} flows")

from collections import defaultdict
flowagg = defaultdict(lambda: [0, "", ""])   # (cust, flow_id) -> [count, first, last]
print("received-email events…", flush=True)
seen = 0
for em, dt, p in events(RECEIVED_EMAIL, "datetime,event_properties"):
    cid = email2cust.get(em); fl = p.get("$flow")
    if not cid or not fl or not dt:
        continue
    seen += 1
    a = flowagg[(cid, fl)]
    a[0] += 1
    if not a[1] or dt < a[1]:
        a[1] = dt
    if dt > a[2]:
        a[2] = dt
print(f"  matched flow-sends: {seen} -> {len(flowagg)} (customer,flow) pairs")

discagg = defaultdict(lambda: [0, ""])        # (cust, code) -> [count, last]
print("placed-order discount events…", flush=True)
for em, dt, p in events(PLACED_ORDER, "datetime,event_properties"):
    cid = email2cust.get(em)
    if not cid:
        continue
    try:
        codes = json.loads(p.get("Discount Codes") or "[]")
    except Exception:
        codes = []
    for code in codes:
        code = (str(code) or "").strip().upper()
        if not code:
            continue
        d = discagg[(cid, code)]
        d[0] += 1
        if dt > d[1]:
            d[1] = dt
print(f"  discount redemptions: {len(discagg)} (customer,code) pairs")

cur.execute("DROP TABLE IF EXISTS clientssot_crm_flows")
cur.execute("""CREATE TABLE clientssot_crm_flows(customer_id TEXT, flow_id TEXT, flow_name TEXT,
    sends INT, first_date TEXT, last_date TEXT, PRIMARY KEY(customer_id, flow_id))""")
from clientssot.naming import canonical
cur.executemany("INSERT OR REPLACE INTO clientssot_crm_flows VALUES (?,?,?,?,?,?)",
                [(c, f, canonical(flows.get(f, f), "flow"), v[0], v[1], v[2]) for (c, f), v in flowagg.items()])
cur.execute("DROP TABLE IF EXISTS clientssot_crm_discounts")
cur.execute("""CREATE TABLE clientssot_crm_discounts(customer_id TEXT, code TEXT, redemptions INT,
    last_date TEXT, PRIMARY KEY(customer_id, code))""")
cur.executemany("INSERT OR REPLACE INTO clientssot_crm_discounts VALUES (?,?,?,?)",
                [(c, code, v[0], v[1]) for (c, code), v in discagg.items()])
cur.execute("CREATE INDEX IF NOT EXISTS idx_crmflow_cust ON clientssot_crm_flows(customer_id)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_crmflow_name ON clientssot_crm_flows(flow_name)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_crmdisc_cust ON clientssot_crm_discounts(customer_id)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_crmdisc_code ON clientssot_crm_discounts(code)")
con.commit()
print("\ntop flows by customers reached:")
for r in cur.execute("""SELECT flow_name, COUNT(DISTINCT customer_id) n FROM clientssot_crm_flows
        GROUP BY flow_name ORDER BY n DESC LIMIT 12"""):
    print(f"  {r[1]:>6}  {r[0]}")
print("discount codes claimed:")
for r in cur.execute("SELECT code, COUNT(DISTINCT customer_id) n FROM clientssot_crm_discounts GROUP BY code ORDER BY n DESC LIMIT 12"):
    print(f"  {r[1]:>6}  {r[0]}")
con.close()
print("KLAVIYO FLOWS INGEST DONE")

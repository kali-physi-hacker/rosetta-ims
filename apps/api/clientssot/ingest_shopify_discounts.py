# -*- coding: utf-8 -*-
"""Who claimed which discount — pulled from SHOPIFY orders (Klaviyo's Placed-Order codes came back empty).
Populates clientssot_crm_discounts(customer_id, code, redemptions, last_date) so the CRM 'Claimed' filter +
drill-in show real redemptions. Normalises per-customer unique codes (AL_xxxx -> 'ALOHA (Meta form)')."""
import sqlite3, io, sys, re, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
from clientssot.shopify_client import gql
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DB = resolve_db()
START = "2025-01-01"   # discount campaigns are recent; bounded pull

def norm_phone(s):
    d = re.sub(r"\D", "", s or ""); return d[-8:] if len(d) >= 8 else ""

def code_family(code):
    c = (code or "").strip()
    if not c or " " in c:           # skip notes like "Paw Points Payment" / reimbursement memos
        return ""
    u = c.upper()
    if u.startswith("AL_") or "ALOHA" in u:
        return "ALOHA (Meta form)"      # unique per-customer codes -> one campaign
    return u                            # GIFT4DOG, GIFT4CAT, PETPROJECT100, GET1FREE, DISCOUNT10, …

con = sqlite3.connect(DB); cur = con.cursor()
email2cust = {r[1].strip().lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}
phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}

Q = ('query($c:String){ orders(first:250, after:$c, query:"created_at:>=' + START + '", sortKey:CREATED_AT){'
     ' edges{ node{ createdAt discountCodes customer{ email phone } } } pageInfo{ hasNextPage endCursor } } }')
after = None; n_ord = 0
agg = {}  # (cust, fam) -> [count, last_date]
while True:
    j = gql(Q, {"c": after}); d = (j.get("data") or {}).get("orders")
    if not d:
        print("stop:", str(j)[:160]); break
    for e in d["edges"]:
        o = e["node"]; n_ord += 1
        codes = o.get("discountCodes") or []
        if not codes:
            continue
        c = o.get("customer") or {}
        cid = email2cust.get((c.get("email") or "").strip().lower())
        if not cid and c.get("phone"):
            cid = phone2cust.get(norm_phone(c.get("phone")))
        if not cid:
            continue
        dt = (o.get("createdAt") or "")[:10]
        for code in codes:
            fam = code_family(code)
            if not fam:
                continue
            k = (cid, fam); a = agg.setdefault(k, [0, ""])
            a[0] += 1
            if dt > a[1]:
                a[1] = dt
    if d["pageInfo"]["hasNextPage"]:
        after = d["pageInfo"]["endCursor"]; time.sleep(0.15)
    else:
        break

cur.execute("DROP TABLE IF EXISTS clientssot_crm_discounts")
cur.execute("""CREATE TABLE clientssot_crm_discounts(customer_id TEXT, code TEXT, redemptions INT,
    last_date TEXT, PRIMARY KEY(customer_id, code))""")
cur.executemany("INSERT OR REPLACE INTO clientssot_crm_discounts VALUES (?,?,?,?)",
                [(c, code, v[0], v[1]) for (c, code), v in agg.items()])
cur.execute("CREATE INDEX IF NOT EXISTS idx_crmdisc_cust ON clientssot_crm_discounts(customer_id)")
cur.execute("CREATE INDEX IF NOT EXISTS idx_crmdisc_code ON clientssot_crm_discounts(code)")
con.commit()
print(f"orders scanned {n_ord} | (customer,code) redemptions {len(agg)}")
print("by code:")
for r in cur.execute("SELECT code, COUNT(DISTINCT customer_id) n FROM clientssot_crm_discounts GROUP BY code ORDER BY n DESC LIMIT 15"):
    print(f"  {r[1]:>4}  {r[0]}")
con.close()
print("SHOPIFY DISCOUNTS INGEST DONE")

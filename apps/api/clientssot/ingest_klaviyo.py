# -*- coding: utf-8 -*-
"""Build the CRM list: pull Klaviyo list memberships, match to existing SSOT customers by email/phone,
and record which CRM campaigns/lists each customer has joined (clinic OR website). Marks a 'Klaviyo'
source so CRM membership is visible + filterable. Unmatched profiles are pure leads (counted, not added)."""
import sqlite3, io, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.klaviyo_client import list_profiles, BASE, HEADERS
from clientssot.naming import canonical
import requests
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

# Pull EVERY Klaviyo list dynamically (so a new list is never silently missed), skipping internal/test ones.
def _skip(low):  # internal / test / legacy junk — not real acquisition lists
    # "Email List" = legacy 99.8% duplicate of LIST - ALL - MASTER (pre-convention); skip to avoid a confusing twin.
    return ("internal" in low or "testing" in low or low in ("testing list", "petproject team recipient", "email list"))
def _channel(low):  # canonical names are "LIST - <CHANNEL> - …"
    if "whatsapp" in low or " wa " in low:
        return "whatsapp"
    if " sms " in low:
        return "sms"
    return "email"
def all_lists():
    out = {}
    url = f"{BASE}/lists/?fields%5Blist%5D=name"      # PAGINATE — Klaviyo returns 10/page; new lists live on page 2+
    while url:
        j = requests.get(url, headers=HEADERS, timeout=40).json()
        for l in j.get("data", []):
            name = (l.get("attributes", {}).get("name") or "").strip()
            low = name.lower()
            if name and not _skip(low):
                out[l["id"]] = (name, _channel(low))
        url = (j.get("links") or {}).get("next")
    return out

LISTS = all_lists()
print(f"Klaviyo lists to ingest: {len(LISTS)} — {[v[0] for v in LISTS.values()]}", flush=True)

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_customer_crm(
    customer_id TEXT, source TEXT, list_name TEXT, channel TEXT,
    PRIMARY KEY(customer_id, source, list_name))""")
cur.execute("DELETE FROM clientssot_customer_crm WHERE source='Klaviyo'")

phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}
email2cust = {r[1].lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}
print(f"match maps: {len(phone2cust)} phones, {len(email2cust)} emails", flush=True)

for lid, (name, channel) in LISTS.items():
    seen = matched = 0
    rows = []
    for p in list_profiles(lid):
        seen += 1
        cid = email2cust.get(p["email"].lower()) if p["email"] else None
        if not cid and p["phone"]:
            cid = phone2cust.get(norm_phone(p["phone"]))
        if cid:
            matched += 1
            rows.append((cid, "Klaviyo", canonical(name, "list"), channel))
        if seen % 4000 == 0:
            print(f"  {name}: {seen} profiles...", flush=True)
    cur.executemany("INSERT OR IGNORE INTO clientssot_customer_crm VALUES (?,?,?,?)", rows)
    con.commit()
    print(f"{name}: {seen} profiles -> {matched} matched to SSOT customers")

n_cust = cur.execute("SELECT COUNT(DISTINCT customer_id) FROM clientssot_customer_crm WHERE source='Klaviyo'").fetchone()[0]
print(f"\n>>> CRM members (existing customers on >=1 Klaviyo list): {n_cust}")
con.close()
print("KLAVIYO CRM INGEST DONE")

# -*- coding: utf-8 -*-
"""Real marketing CONSENT (Klaviyo SUBSCRIBED status) + ENGAGEMENT recency (last event date), in one pass.
Upgrades the consent proxy to the authoritative opt-in field, and adds 'last engaged' for an engagement filter."""
import sqlite3, io, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.klaviyo_client import all_profiles
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

con = sqlite3.connect(DB); cur = con.cursor()
for coldef in ("email_consent INT", "last_engagement TEXT"):
    try: cur.execute(f"ALTER TABLE clientssot_customers ADD COLUMN {coldef}")
    except sqlite3.OperationalError: pass
phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}
email2cust = {r[1].lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}

seen = matched = consented = 0
for p in all_profiles():
    seen += 1
    cid = (email2cust.get(p["email"].lower()) if p["email"] else None)
    if not cid and p["phone"]:
        cid = phone2cust.get(norm_phone(p["phone"]))
    if not cid:
        continue
    matched += 1
    if p["email_consent"]:
        consented += 1
    cur.execute("UPDATE clientssot_customers SET email_consent=?, last_engagement=? WHERE id=?",
                (1 if p["email_consent"] else 0, p["last_event"], cid))
    if seen % 4000 == 0:
        print(f"  ...{seen} profiles, {matched} matched", flush=True); con.commit()
con.commit()
print(f"\nprofiles {seen} | matched {matched} | real SUBSCRIBED consent {consented}")
eng = cur.execute("SELECT COUNT(*) FROM clientssot_customers WHERE last_engagement >= '2026-03-23'").fetchone()[0]
print(f"engaged in last 90d (last_engagement >= 2026-03-23): {eng}")
con.close()
print("KLAVIYO PROFILES INGEST DONE")

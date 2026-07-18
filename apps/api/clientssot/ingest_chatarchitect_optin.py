# -*- coding: utf-8 -*-
"""Pull the real WhatsApp opt-in list from ChatArchitect and mark matching customers as WhatsApp-reachable
(channel='whatsapp' in clientssot_customer_crm -> feeds WHATSAPP_REACH). Read-only on ChatArchitect's side."""
import sqlite3, io, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.chatarchitect_client import optin_phones
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def last8(s):
    d = re.sub(r"\D", "", s or ""); return d[-8:] if len(d) >= 8 else ""

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_customer_crm(customer_id TEXT, channel TEXT, source TEXT)""")
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_whatsapp_optin(phone TEXT PRIMARY KEY, matched INT)""")

phones = optin_phones()
print(f"opted-in phones pulled: {len(phones)}")
if not phones:
    print("NO PHONES — aborting (check creds/endpoint)"); con.close(); sys.exit(1)

# build last-8 -> customer_id map
p2c = {}
for cid, ph in cur.execute("SELECT id, phone FROM clientssot_customers WHERE phone IS NOT NULL AND phone!=''"):
    k = last8(ph)
    if k:
        p2c.setdefault(k, cid)

# clear prior chatarchitect whatsapp rows, re-insert
cur.execute("DELETE FROM clientssot_customer_crm WHERE channel='whatsapp' AND source='chatarchitect'")
cur.execute("DELETE FROM clientssot_whatsapp_optin")
matched = 0; rows = []
for ph in phones:
    cid = p2c.get(last8(ph))
    cur.execute("INSERT OR IGNORE INTO clientssot_whatsapp_optin VALUES (?,?)", (ph, 1 if cid else 0))
    if cid:
        rows.append((cid, "whatsapp", "chatarchitect")); matched += 1
cur.executemany("INSERT INTO clientssot_customer_crm (customer_id, channel, source) VALUES (?,?,?)", rows)
con.commit()
print(f"matched to customers: {matched} of {len(phones)} ({round(100*matched/len(phones))}%)")
print("total WhatsApp-reachable now:",
      cur.execute("SELECT COUNT(DISTINCT customer_id) FROM clientssot_customer_crm WHERE channel='whatsapp'").fetchone()[0])
con.close()
print("CHATARCHITECT OPT-IN INGEST DONE")

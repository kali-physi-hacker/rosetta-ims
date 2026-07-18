# -*- coding: utf-8 -*-
"""OPERATIONAL layer: customers who've actively reached out to CS via WhatsApp (mirrored into the Slack
channels #petproject-whatsapp and #ohana-whatsapp). Each inbound conversation is posted as 'Name(phone)'
with a reply thread; we match the phone to the SSOT. Lets you filter 'who is actively talking to us now'.

NOTE: this SAMPLE was read via the Slack MCP (which can't be called from a script). For a full/live sync,
add a Slack bot token (channels:history) to .env and point this at the Slack API — same table, same matching."""
import sqlite3, io, sys, re
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

# SAMPLE pulled from the two WhatsApp Slack channels (Name, phone, channel, last_contact, msg_count)
SAMPLE = [
    ("Flora", "85291233107", "PetProject", "2026-06-20", 5),
    ("Kong", "85263485478", "PetProject", "2026-06-19", 11),
    ("C", "85296988323", "PetProject", "2026-06-19", 3),
    ("Jinny", "85262955266", "PetProject", "2026-06-18", 9),
    ("Sam (client)", "85292328833", "PetProject", "2026-06-17", 10),
    ("Sharon Chan", "85263367883", "PetProject", "2026-06-19", 1),
    ("N", "85290907053", "PetProject", "2026-06-19", 1),
    ("Jack Cummins", "85298586168", "PetProject", "2026-06-18", 1),
    ("Maggie", "85293541545", "Ohana", "2026-06-18", 10),
    ("", "85291766988", "Ohana", "2026-06-18", 1),
    ("", "85290428818", "Ohana", "2026-06-18", 1),
    ("", "85290403196", "Ohana", "2026-06-18", 3),
    ("", "85290334466", "Ohana", "2026-06-18", 3),
    ("", "85296512618", "Ohana", "2026-06-18", 3),
    ("", "85291931101", "Ohana", "2026-06-18", 3),
    ("", "85292682207", "Ohana", "2026-06-18", 3),
    ("", "85251880300", "Ohana", "2026-06-18", 3),
    ("", "85261423659", "Ohana", "2026-06-18", 2),
    ("", "85266414326", "Ohana", "2026-06-18", 3),
    ("", "85266828897", "Ohana", "2026-06-18", 3),
    ("", "85260233703", "Ohana", "2026-06-18", 1),
    ("", "85293173711", "Ohana", "2026-06-18", 1),
]

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_cs_contacts(
    phone TEXT PRIMARY KEY, name TEXT, channel TEXT, last_contact TEXT, msg_count INT, customer_id TEXT)""")
cur.execute("DELETE FROM clientssot_cs_contacts")

phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}

matched = 0
for name, phone, channel, last, n in SAMPLE:
    cid = phone2cust.get(norm_phone(phone))
    if cid:
        matched += 1
    cur.execute("INSERT OR REPLACE INTO clientssot_cs_contacts VALUES (?,?,?,?,?,?)",
                (phone, name, channel, last, n, cid))
con.commit()
print(f"CS contacts: {len(SAMPLE)} | matched to SSOT customers: {matched}")
for r in cur.execute("""SELECT cs.name, cs.phone, cs.channel, cs.last_contact, c.first_name||' '||c.last_name
        FROM clientssot_cs_contacts cs LEFT JOIN clientssot_customers c ON c.id=cs.customer_id
        WHERE cs.customer_id IS NOT NULL ORDER BY cs.last_contact DESC LIMIT 8"""):
    print(f"  {r[2]:<11} {r[3]} | wa='{r[0]}' -> SSOT '{r[4]}'")
con.close()
print("CS CONTACTS INGEST DONE")

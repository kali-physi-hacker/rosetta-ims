# -*- coding: utf-8 -*-
"""Enrich Client SSOT with the FULL DaySmart client list (email + phone + all clients/pets).
The appointments feed only had names; the /clients endpoint has mobile + email = the MATCH KEYS
for meshing Shopify/Klaviyo. Upserts: adds email/phone, brings in clients who haven't booked,
and their nested patients. Preserves appointment-derived last_visit/visit_count."""
import sqlite3, io, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.daysmart_client import paginate
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()
con = sqlite3.connect(DB); cur = con.cursor()
for coldef in ("email TEXT", "phone TEXT"):
    try:
        cur.execute(f"ALTER TABLE clientssot_customers ADD COLUMN {coldef}")
    except sqlite3.OperationalError:
        pass  # already added

def phone_of(c):
    return (c.get("mobile") or c.get("home") or c.get("work") or "").strip()

def label_of(v):
    """DaySmart returns species/breed as {id,label} objects (or lists/null). Flatten to a string."""
    if v is None:
        return ""
    if isinstance(v, dict):
        return str(v.get("label") or v.get("name") or "")
    if isinstance(v, list):
        return ", ".join(label_of(x) for x in v if x)
    return str(v)

n = nc = npets = with_email = with_phone = 0
for c in paginate("clients", per_page=200):
    cid = c.get("id")
    if not cid:
        continue
    n += 1
    email = (c.get("email") or "").strip()
    phone = phone_of(c)
    if email: with_email += 1
    if phone: with_phone += 1
    cur.execute("""INSERT INTO clientssot_customers (id, first_name, last_name, last_visit, visit_count, source, email, phone)
        VALUES (?,?,?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET email=excluded.email, phone=excluded.phone,
            first_name=excluded.first_name, last_name=excluded.last_name""",
        (cid, c.get("firstName", ""), c.get("lastName", ""), "", 0, "DaySmart", email, phone))
    for p in (c.get("patients") or []):
        pid = p.get("id")
        if not pid:
            continue
        npets += 1
        cur.execute("""INSERT INTO clientssot_pets (id, customer_id, name, weight, species, breed, dob, last_visit, visit_count)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(id) DO UPDATE SET customer_id=excluded.customer_id, name=excluded.name,
                weight=excluded.weight, species=excluded.species, breed=excluded.breed, dob=excluded.dob""",
            (pid, cid, p.get("name", ""), label_of(p.get("weight")), label_of(p.get("species")),
             label_of(p.get("breeds") or p.get("breed")), (p.get("birthdate") or "")[:10], "", 0))
    if n % 2000 == 0:
        print(f"  ...{n} clients", flush=True); con.commit()

con.commit()
tot_c = cur.execute("SELECT COUNT(*) FROM clientssot_customers").fetchone()[0]
tot_p = cur.execute("SELECT COUNT(*) FROM clientssot_pets").fetchone()[0]
em = cur.execute("SELECT COUNT(*) FROM clientssot_customers WHERE email!=''").fetchone()[0]
ph = cur.execute("SELECT COUNT(*) FROM clientssot_customers WHERE phone!=''").fetchone()[0]
con.close()
print(f"\nclients pulled={n}  pets seen={npets}")
print(f"customers now={tot_c} (email {em}, phone {ph})   pets now={tot_p}")
print("ENRICH DONE")

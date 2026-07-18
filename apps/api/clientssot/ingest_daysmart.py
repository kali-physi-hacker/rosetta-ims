# -*- coding: utf-8 -*-
"""Ingest DaySmart appointments -> Client SSOT tables (additive, prefixed clientssot_* in ims.db).
Builds customer -> pet records, derives care tags (main/sub) from appointment reasons.
Idempotent: drops & rebuilds the clientssot_* tables each run. Touches no existing IMS tables."""
import sqlite3, io, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # backend/ on path
from clientssot.daysmart_client import paginate
from clientssot.taxonomy import classify
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def datepart(s):
    return (s or "")[:10]

def label_of(v):
    if v is None:
        return ""
    if isinstance(v, dict):
        return str(v.get("label") or v.get("name") or "")
    if isinstance(v, list):
        return ", ".join(label_of(x) for x in v if x)
    return str(v)

customers, pets, tags = {}, {}, {}
history = []
n = 0
for a in paginate("appointments", per_page=200):
    n += 1
    cl = a.get("client") or {}; pt = a.get("patient") or {}
    cid = cl.get("id"); pid = pt.get("id")
    d = datepart(a.get("start"))
    if cid:
        c = customers.setdefault(cid, {"first": cl.get("firstName", ""), "last": cl.get("lastName", ""),
                                       "last_visit": "", "visits": 0})
        c["visits"] += 1
        if d > c["last_visit"]:
            c["last_visit"] = d
    if pid:
        p = pets.setdefault(pid, {"cid": cid, "name": pt.get("name", ""), "weight": label_of(pt.get("weight")),
                                  "species": label_of(pt.get("species")), "breed": label_of(pt.get("breeds") or pt.get("breed")),
                                  "dob": (pt.get("birthdate") or "")[:10], "last_visit": "", "visits": 0})
        p["visits"] += 1
        if d > p["last_visit"]:
            p["last_visit"] = d
        reason = a.get("reason") or ""; note = a.get("note") or ""
        if reason or note:
            history.append((pid, "DaySmart", d, reason, note))
        kind, main, sub = classify(reason)
        if kind:
            key = (pid, kind, main, sub, "DaySmart")   # source dimension
            t = tags.setdefault(key, {"count": 0, "last": ""})
            t["count"] += 1
            if d > t["last"]:
                t["last"] = d
    if n % 1000 == 0:
        print(f"  ...{n} appts", flush=True)

print(f"\nappointments={n}  customers={len(customers)}  pets={len(pets)}  tag-rows={len(tags)}")

con = sqlite3.connect(DB); cur = con.cursor()
for t in ("clientssot_pet_caretags", "clientssot_pet_history", "clientssot_pets", "clientssot_customers"):
    cur.execute(f"DROP TABLE IF EXISTS {t}")
cur.execute("""CREATE TABLE clientssot_customers(
    id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, last_visit TEXT, visit_count INT, source TEXT)""")
cur.execute("""CREATE TABLE clientssot_pets(
    id TEXT PRIMARY KEY, customer_id TEXT, name TEXT, weight TEXT, species TEXT, breed TEXT, dob TEXT,
    last_visit TEXT, visit_count INT)""")
cur.execute("""CREATE TABLE clientssot_pet_caretags(
    pet_id TEXT, kind TEXT, main TEXT, sub TEXT, source TEXT, count INT, last_seen TEXT,
    PRIMARY KEY(pet_id, kind, main, sub, source))""")
cur.execute("""CREATE TABLE clientssot_pet_history(
    pet_id TEXT, source TEXT, date TEXT, dx TEXT, note TEXT)""")
cur.executemany("INSERT INTO clientssot_customers VALUES (?,?,?,?,?,?)",
    [(cid, c["first"], c["last"], c["last_visit"], c["visits"], "DaySmart") for cid, c in customers.items()])
cur.executemany("INSERT INTO clientssot_pets VALUES (?,?,?,?,?,?,?,?,?)",
    [(pid, p["cid"], p["name"], p["weight"], p["species"], p["breed"], p["dob"], p["last_visit"], p["visits"]) for pid, p in pets.items()])
cur.executemany("INSERT INTO clientssot_pet_caretags VALUES (?,?,?,?,?,?,?)",
    [(k[0], k[1], k[2], k[3], k[4], v["count"], v["last"]) for k, v in tags.items()])
cur.executemany("INSERT INTO clientssot_pet_history VALUES (?,?,?,?,?)", history)
con.commit()
print(f"history rows (DaySmart): {len(history)}")

# summary
print("\n=== CARE TAGS by MAIN (kind=care) ===")
for main, c in cur.execute("""SELECT main, COUNT(DISTINCT pet_id) FROM clientssot_pet_caretags
        WHERE kind='care' GROUP BY main ORDER BY 2 DESC"""):
    print(f"  {c:>4} pets  {main}")
print("\n=== top SUB (kind=care) ===")
for main, sub, c in cur.execute("""SELECT main, sub, COUNT(DISTINCT pet_id) FROM clientssot_pet_caretags
        WHERE kind='care' GROUP BY main, sub ORDER BY 3 DESC LIMIT 15"""):
    print(f"  {c:>4} pets  {main} -> {sub}")
con.close()
print("\nINGEST DONE")

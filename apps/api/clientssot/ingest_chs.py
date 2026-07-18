# -*- coding: utf-8 -*-
"""Ingest the CHS (Dr Hugh's legacy) export into the Client SSOT.
The file is headerless & denormalized: a client row (owner col0, phone col2, pet col3) followed by
visit rows (date col11, diagnosis col12, complaint col16) with blank identity cols. We forward-fill
identity, classify diagnoses/complaints into the SAME taxonomy (source='CHS'), and MATCH to existing
DaySmart customers by phone (+ pet by name) so overlap (D + C) lights up on the same record.
Idempotent for CHS rows: deletes prior source='CHS' tags before re-inserting; never touches DaySmart rows."""
import sqlite3, io, sys, re, csv, hashlib, collections
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.taxonomy import classify
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
csv.field_size_limit(10**9)

DB  = Path(__file__).resolve().parents[1] / "ims.db"
CHS = Path(__file__).resolve().parents[2] / "Client SSOT Context" / "customer list (22 July 2025).csv"

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

def norm_name(s):
    return re.sub(r"\s+", " ", (s or "").strip().upper())

# ---- load DaySmart match maps ----
con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; cur = con.cursor()
phone2cust = {}
for r in cur.execute("SELECT id, phone FROM clientssot_customers WHERE phone IS NOT NULL AND phone!=''"):
    p = norm_phone(r["phone"])
    if p:
        phone2cust.setdefault(p, r["id"])
custpet2id = {}
for r in cur.execute("SELECT id, customer_id, name FROM clientssot_pets"):
    custpet2id[(r["customer_id"], norm_name(r["name"]))] = r["id"]
print(f"DaySmart maps: {len(phone2cust)} phones, {len(custpet2id)} (cust,pet) keys", flush=True)

# ---- stream CHS, forward-fill, aggregate ----
clients = {}                       # ckey -> {owner, phone}
pets = {}                          # (ckey, petname) -> {dob, breed, species, last, visits}
tags = collections.Counter()      # (ckey, petname, kind, main, sub) -> count
tag_last = {}                      # same key -> last date
unmatched = collections.Counter() # diagnoses that didn't map (to grow taxonomy later)
history = []                       # (ckey, petname, date, dx, complaint) raw timeline
cur_ck = cur_owner = cur_phone = cur_pet = None
rows = 0

with open(CHS, encoding="utf-8", errors="replace", newline="") as f:
    for row in csv.reader(f):
        rows += 1
        g = lambda i: (row[i].strip() if i < len(row) and row[i] else "")
        owner, phone, pet = g(0), g(2), g(3)
        if owner:                                  # new client
            cur_owner = owner; cur_phone = phone
            p = norm_phone(phone)
            cur_ck = p if p else "name:" + norm_name(owner)
            clients.setdefault(cur_ck, {"owner": owner, "phone": phone})
            cur_pet = None
        if pet:                                    # new pet (for current client)
            cur_pet = norm_name(pet)
            if cur_ck:
                pets.setdefault((cur_ck, cur_pet), {"dob": g(4), "breed": g(8), "species": g(9), "last": "", "visits": 0})
        if not cur_ck or not cur_pet:
            continue
        date = g(11)
        pk = (cur_ck, cur_pet)
        if date:
            pr = pets.get(pk)
            if pr:
                pr["visits"] += 1
                if date > pr["last"]:
                    pr["last"] = date
        dx, comp = g(12), g(16)
        if dx or comp:
            history.append((cur_ck, cur_pet, date, dx, comp))
        for col in (12, 16):                       # diagnosis + complaint
            val = g(col)
            if not val:
                continue
            kind, main, sub = classify(val)
            if kind:
                key = (cur_ck, cur_pet, kind, main, sub)
                tags[key] += 1
                if date > tag_last.get(key, ""):
                    tag_last[key] = date
            elif col == 12 and len(val) <= 40:
                unmatched[val.upper()] += 1
        if rows % 50000 == 0:
            print(f"  ...{rows} rows", flush=True)

print(f"\nCHS parsed: rows={rows} clients={len(clients)} pets={len(pets)} tag-keys={len(tags)}", flush=True)

# ---- resolve to customer/pet ids (match to DaySmart) ----
ck2cust, matched_clients, new_clients = {}, 0, 0
for ck, c in clients.items():
    p = norm_phone(c["phone"])
    if p and p in phone2cust:
        ck2cust[ck] = phone2cust[p]; matched_clients += 1
    else:
        ck2cust[ck] = "CHS:" + ck; new_clients += 1

def pet_id_for(ck, petname):
    cust = ck2cust[ck]
    hit = custpet2id.get((cust, petname))
    if hit:
        return hit, True
    return "CHSP:" + hashlib.md5(f"{cust}|{petname}".encode()).hexdigest()[:14], False

new_cust_rows, new_pet_rows, tag_rows = [], [], []
matched_pets = new_pets = 0
seen_pets = set()
pet_resolved = {}
for (ck, petname), pr in pets.items():
    pid, matched = pet_id_for(ck, petname)
    pet_resolved[(ck, petname)] = pid
    if matched:
        matched_pets += 1
    else:
        new_pets += 1
        if pid not in seen_pets:
            new_pet_rows.append((pid, ck2cust[ck], petname.title(), "", pr.get("species", ""), pr.get("breed", ""),
                                 pr.get("dob", ""), pr["last"], pr["visits"]))
    seen_pets.add(pid)
for ck, c in clients.items():
    cust = ck2cust[ck]
    if cust.startswith("CHS:"):
        new_cust_rows.append((cust, c["owner"], "", "", 0, "CHS", "", c["phone"]))
for (ck, petname, kind, main, sub), cnt in tags.items():
    pid = pet_resolved.get((ck, petname))
    if pid:
        tag_rows.append((pid, kind, main, sub, "CHS", cnt, tag_last.get((ck, petname, kind, main, sub), "")))
hist_rows = []
for (ck, petname, date, dx, comp) in history:
    pid = pet_resolved.get((ck, petname))
    if pid:
        hist_rows.append((pid, "CHS", date, dx, comp))

# ---- write (CHS rows only; never touch DaySmart) ----
cur.execute("DELETE FROM clientssot_pet_caretags WHERE source='CHS'")
cur.execute("DELETE FROM clientssot_pet_history WHERE source='CHS'")
cur.executemany("""INSERT INTO clientssot_customers (id, first_name, last_name, last_visit, visit_count, source, email, phone)
    VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", new_cust_rows)
cur.executemany("""INSERT INTO clientssot_pets (id, customer_id, name, weight, species, breed, dob, last_visit, visit_count)
    VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", new_pet_rows)
cur.executemany("""INSERT INTO clientssot_pet_caretags (pet_id, kind, main, sub, source, count, last_seen)
    VALUES (?,?,?,?,?,?,?) ON CONFLICT(pet_id,kind,main,sub,source) DO UPDATE SET count=excluded.count""", tag_rows)
cur.executemany("INSERT INTO clientssot_pet_history VALUES (?,?,?,?,?)", hist_rows)
con.commit()
print(f"CHS history rows: {len(hist_rows)}")

# overlap: pets carrying BOTH a DaySmart and a CHS care tag
overlap = cur.execute("""SELECT COUNT(DISTINCT a.pet_id) FROM clientssot_pet_caretags a
    JOIN clientssot_pet_caretags b ON a.pet_id=b.pet_id
    WHERE a.kind='care' AND b.kind='care' AND a.source='DaySmart' AND b.source='CHS'""").fetchone()[0]
print(f"\nclients matched by phone: {matched_clients}  new (CHS-only): {new_clients}")
print(f"pets matched by name: {matched_pets}  new CHS pets: {new_pets}")
print(f"CHS care-tag rows written: {len(tag_rows)}")
print(f">>> PETS WITH DaySmart + CHS OVERLAP: {overlap}")
print("\n=== top 20 UNMATCHED CHS diagnoses (taxonomy gaps to review) ===")
for v, c in unmatched.most_common(20):
    print(f"  {c:>5}  {v}")
con.close()
print("CHS INGEST DONE")

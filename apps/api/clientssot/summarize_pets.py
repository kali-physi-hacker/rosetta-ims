# -*- coding: utf-8 -*-
"""Generate a short care summary per pet (the deck's 'proprietary care filing'), templated from
structured data: species/age + top care tags + notable meds (scanned from history) + sources + last visit.
Deterministic (no LLM key needed) so it runs across all ~23k pets. Richer LLM summaries are a later
enhancement once an Anthropic key / DaySmart SOAP export is available."""
import sqlite3, io, sys, re, collections
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()
CUR_YEAR = 2026
SPECIES = {"canine": "dog", "feline": "cat", "leporid": "rabbit", "lapine": "rabbit"}
MEDS = [("cytopoint", "Cytopoint"), ("apoquel", "Apoquel"), ("sc fluid", "SC fluids"),
        ("subcut", "SC fluids"), ("insulin", "insulin"), ("proheart", "Proheart"),
        ("librela", "Librela"), ("vetmedin", "Vetmedin")]

con = sqlite3.connect(DB); con.row_factory = sqlite3.Row; cur = con.cursor()

# care tags per pet (sub + count + sources)
caretags = collections.defaultdict(list)
for r in cur.execute("SELECT pet_id, main, sub, source, count FROM clientssot_pet_caretags WHERE kind='care'"):
    caretags[r["pet_id"]].append((r["sub"], r["source"], r["count"]))
sources_of = collections.defaultdict(set)
for r in cur.execute("SELECT DISTINCT pet_id, source FROM clientssot_pet_caretags"):
    sources_of[r["pet_id"]].add(r["source"])
# notable meds from history text
meds_of = collections.defaultdict(set)
for r in cur.execute("SELECT pet_id, dx, note FROM clientssot_pet_history"):
    blob = f"{r['dx']} {r['note']}".lower()
    for needle, label in MEDS:
        if needle in blob:
            meds_of[r["pet_id"]].add(label)

SRC_LABEL = {"DaySmart": "clinic", "CHS": "Dr Hugh's records", "Shopify": "online store", "Klaviyo": "marketing"}

def age_from(dob):
    m = re.search(r"(19|20)\d\d", dob or "")
    if not m:
        return None
    a = CUR_YEAR - int(m.group(0))
    return a if 0 <= a <= 35 else None

def lifestage(age, species):
    if age is None:
        return ""
    if age <= 1:
        return "young "
    if age >= 8:
        return "senior "
    return "adult "

def summarize(p):
    sp = SPECIES.get((p["species"] or "").strip().lower(), (p["species"] or "pet").strip().lower() or "pet")
    age = age_from(p["dob"])
    breed = (p["breed"] or "").strip()
    head = f"{lifestage(age, sp)}{sp}"
    if breed:
        head += f" ({breed})"
    if age is not None:
        head += f", ~{age}y"
    parts = [head[0].upper() + head[1:] + "."]
    # top care subs by count (dedup, keep order)
    tags = caretags.get(p["id"], [])
    agg = collections.Counter()
    for sub, _src, cnt in tags:
        agg[sub] += cnt
    top = [s for s, _ in agg.most_common(5)]
    if top:
        parts.append("Care history: " + ", ".join(top) + ".")
    meds = sorted(meds_of.get(p["id"], []))
    if meds:
        parts.append("On " + ", ".join(meds) + ".")
    srcs = sorted(sources_of.get(p["id"], []), key=lambda s: s != "DaySmart")
    if srcs:
        parts.append("Seen in " + " + ".join(SRC_LABEL.get(s, s) for s in srcs) + ".")
    lv = (p["last_visit"] or "")[:7]
    if lv:
        parts.append(f"Last {lv}, {p['visit_count']} visit(s).")
    return " ".join(parts), ", ".join(top)

cur.execute("DROP TABLE IF EXISTS clientssot_pet_summary")
cur.execute("CREATE TABLE clientssot_pet_summary(pet_id TEXT PRIMARY KEY, summary TEXT, care_csv TEXT)")
rows = []
n = 0
for p in cur.execute("SELECT id, name, species, breed, dob, last_visit, visit_count FROM clientssot_pets"):
    s, csv = summarize(p)
    rows.append((p["id"], s, csv))
    n += 1
    if len(rows) >= 5000:
        cur.executemany("INSERT INTO clientssot_pet_summary VALUES (?,?,?)", rows); rows = []
if rows:
    cur.executemany("INSERT INTO clientssot_pet_summary VALUES (?,?,?)", rows)
con.commit()
print(f"summaries written: {n}")
print("\n=== 6 sample summaries ===")
for r in cur.execute("""SELECT s.summary FROM clientssot_pet_summary s
        JOIN clientssot_pets p ON s.pet_id=p.id WHERE p.visit_count>3 LIMIT 6"""):
    print("  •", r["summary"])
con.close()
print("SUMMARIZE DONE")

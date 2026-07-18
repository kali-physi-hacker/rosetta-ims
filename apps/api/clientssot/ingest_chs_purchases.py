# -*- coding: utf-8 -*-
"""CHS (Dr Hugh) dispensed products -> clientssot_purchases (source='Dr Hugh'), with dates. Then recompute
each customer's FIRST and LAST purchase date across ALL sources (for pre-acquisition + lapsed filters).
Surfaces e.g. Ricard's Selehold (2024, Dr Hugh) — the pre-acquisition retarget signal."""
import sqlite3, io, sys, re
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

# regex -> (display product, Rosetta category)
PRODUCT_TERMS = [
    (r"selehold|selamectin|stronghold", "Selehold (selamectin)", "Preventative"),
    (r"revolution", "Revolution", "Preventative"),
    (r"nexgard", "NexGard", "Preventative"),
    (r"bravecto", "Bravecto", "Preventative"),
    (r"frontline", "Frontline", "Preventative"),
    (r"milbemax|milpro|interceptor", "Milbemax", "Preventative"),
    (r"drontal|endogard|panacur|dewormer|de-wormer", "Dewormer", "Preventative"),
    (r"advocate|advantage", "Advocate", "Preventative"),
    (r"proheart|heartgard", "Heartworm preventive", "Preventative"),
    (r"broadline", "Broadline", "Preventative"),
    (r"nobivac|fvrcp|dhppi|dhlppi|rabisin|rabies|vaccin", "Vaccine", "Preventative"),
    (r"apoquel", "Apoquel", "Medicine"),
    (r"cytopoint", "Cytopoint", "Medicine"),
    (r"malaseb", "Malaseb", "Pet Hygiene"),
    (r"miconazole|polymyxin|surolan|canaural|otomax|easotic|aurizon", "Ear drops (Miconazole/Polymyxin)", "Medicine"),
    (r"optimmune|chloramphenicol eye|tobramycin|exocin|eye drop", "Eye drops", "Medicine"),
    (r"amoxyclav|clavulox|amoxicillin|noroclav", "Amoxyclav", "Medicine"),
    (r"doxycycline|\bdoxy\b", "Doxycycline", "Medicine"),
    (r"metacam|meloxicam|\bmelox\b|onsior|carprofen|previcox|rimadyl", "NSAID (Metacam/Onsior)", "Medicine"),
    (r"gabapentin|tramadol", "Gabapentin", "Medicine"),
    (r"keppra|levetiracetam|phenobarb", "Anti-seizure", "Medicine"),
    (r"vetmedin|pimobendan|cardisure", "Vetmedin", "Medicine"),
    (r"fortekor|benazepril|semintra|telmisartan", "Fortekor/Semintra", "Medicine"),
    (r"propalin", "Propalin", "Medicine"),
    (r"insulin|caninsulin|prozinc", "Insulin", "Medicine"),
    (r"cerenia|maropitant", "Cerenia (maropitant)", "Medicine"),
    (r"metronidazole|flagyl", "Metronidazole", "Medicine"),
    (r"prednisolone|\bpred\b|steroid", "Prednisolone", "Medicine"),
    (r"royal canin renal|\bk/d\b|renal lp|nutraren", "Renal diet (Rx)", "Prescription Diet"),
    (r"urinary s/o|\bs/o\b|\bc/d\b|royal canin urinary", "Urinary diet (Rx)", "Prescription Diet"),
    (r"gastro|\bi/d\b|royal canin gastro|hypoallergenic|anallergenic|\bz/d\b|\bd/d\b", "GI/Hypoallergenic diet (Rx)", "Prescription Diet"),
    (r"antinol|nutramega|nutraquin|joint|glucosamine|denamarin|cosequin|probiotic", "Supplement", "Supplement"),
]
COMPILED = [(re.compile(p), disp, cat) for p, disp, cat in PRODUCT_TERMS]

def iso(d):
    """Normalize CHS dates (D/M/Y, HK format) -> YYYY-MM-DD so MIN/MAX + filtering work."""
    d = (d or "").strip()
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", d)
    if m:
        day, mon, yr = int(m.group(1)), int(m.group(2)), m.group(3)
        if 1 <= mon <= 12 and 1 <= day <= 31:
            return f"{yr}-{mon:02d}-{day:02d}"
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", d)
    return d[:10] if m else ""

con = sqlite3.connect(DB); cur = con.cursor()
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_purchases(
    customer_id TEXT, date TEXT, product TEXT, qty REAL, price REAL, source TEXT,
    patient TEXT, category TEXT, on_shopify INT)""")
cur.execute("DELETE FROM clientssot_purchases WHERE source='Dr Hugh'")
pet2cust = {r[0]: r[1] for r in cur.execute("SELECT id, customer_id FROM clientssot_pets")}
catalog_tokens = set()
for (nm,) in cur.execute("SELECT DISTINCT name FROM clientssot_product_caretags"):
    for w in re.findall(r"[a-z]{4,}", (nm or "").lower()):
        catalog_tokens.add(w)
def on_shopify(name):
    return 1 if any(w in catalog_tokens for w in re.findall(r"[a-z]{4,}", name.lower())[:2]) else 0

seen = set(); rows = []; n = 0
for pet_id, date, dx, note in cur.execute("SELECT pet_id, date, dx, note FROM clientssot_pet_history WHERE source='CHS'"):
    cid = pet2cust.get(pet_id)
    if not cid:
        continue
    blob = f"{dx or ''} {note or ''}".lower()
    d = iso(date)
    for rx, disp, cat in COMPILED:
        if rx.search(blob):
            key = (cid, d, disp)
            if key in seen:
                continue
            seen.add(key)
            rows.append((cid, d, disp, 1, 0, "Dr Hugh", "", cat, on_shopify(disp)))
    n += 1
    if n % 40000 == 0:
        print(f"  ...{n} history rows, {len(rows)} purchases", flush=True)
cur.executemany("INSERT INTO clientssot_purchases VALUES (?,?,?,?,?,?,?,?,?)", rows)
con.commit()
print(f"CHS purchases extracted: {len(rows)}")

# recompute first/last purchase date per customer across ALL sources
for coldef in ("first_purchase TEXT", "last_purchase TEXT", "bought_rx INT"):
    try: cur.execute(f"ALTER TABLE clientssot_customers ADD COLUMN {coldef}")
    except sqlite3.OperationalError: pass
cur.execute("UPDATE clientssot_customers SET first_purchase=NULL, last_purchase=NULL, bought_rx=NULL")
cur.execute("""UPDATE clientssot_customers SET
    first_purchase=(SELECT MIN(date) FROM clientssot_purchases p WHERE p.customer_id=clientssot_customers.id AND date!=''),
    last_purchase =(SELECT MAX(date) FROM clientssot_purchases p WHERE p.customer_id=clientssot_customers.id AND date!='')
    WHERE id IN (SELECT DISTINCT customer_id FROM clientssot_purchases WHERE customer_id IS NOT NULL)""")
# bought_rx = ever bought a Medicine or Prescription Diet (prescription products)
cur.execute("""UPDATE clientssot_customers SET bought_rx=1 WHERE id IN
    (SELECT DISTINCT customer_id FROM clientssot_purchases WHERE category IN ('Medicine','Prescription Diet') AND customer_id IS NOT NULL)""")
con.commit()
fp = cur.execute("SELECT COUNT(*) FROM clientssot_customers WHERE first_purchase IS NOT NULL").fetchone()[0]
rx = cur.execute("SELECT COUNT(*) FROM clientssot_customers WHERE bought_rx=1").fetchone()[0]
print(f"customers with purchase history: {fp} | bought Rx ever: {rx}")
con.close()
print("CHS PURCHASES INGEST DONE")

# -*- coding: utf-8 -*-
"""THE GOLDMINE: mine the CHS (Dr Hugh's) history notes for the actual products/meds dispensed,
grouped by care-type. So for the dormant cohort we can feature what they were ACTUALLY prescribed
(e.g. Skin → Apoquel / Cytopoint / Malaseb) — the precise thing to re-sell them."""
import sqlite3, io, sys, re, collections
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

# product/med term (lowercase, regex-safe) -> (display, main, sub). Curated from the CHS term analysis
# + common HK vet meds/diets. First-match per term; a note can mention several.
MED_TERMS = [
    ("apoquel", "Apoquel", "Skin & Coat", "Allergy / Atopy"),
    ("cytopoint", "Cytopoint", "Skin & Coat", "Allergy / Atopy"),
    ("malaseb", "Malaseb (med. shampoo)", "Skin & Coat", "Skin infection / Dermatitis"),
    ("fuciderm|isaderm", "Isaderm/Fuciderm", "Skin & Coat", "Skin infection / Dermatitis"),
    ("surolan|canaural|otoflush|epi-?otic|easotic", "Ear drops", "Eyes & Ears", "Ear"),
    ("optimmune|tear|exocin|chloramphenicol eye|eye drop", "Eye drops", "Eyes & Ears", "Eye"),
    ("doxy|doxycycline", "Doxycycline", "Respiratory", "Respiratory"),
    ("metacam|meloxicam|melox|onsior|previcox|carprofen|rimadyl", "NSAID (Metacam/Onsior)", "Mobility", "Joint / Arthritis"),
    ("gabapentin", "Gabapentin", "Neurological", "Seizure / Neuro"),
    ("keppra|levetiracetam|phenobarb", "Anti-seizure (Keppra)", "Neurological", "Seizure / Neuro"),
    ("vetmedin|pimobendan|cardisure", "Vetmedin (cardiac)", "Heart", "Cardiac"),
    ("fortekor|benazepril|semintra|telmisartan", "Fortekor/Semintra", "Heart", "Cardiac"),
    ("propalin", "Propalin", "Urinary & Renal", "Urinary / FLUTD"),
    ("\\bsc fluid|subcut", "SC fluids", "Urinary & Renal", "Renal / Kidney (CKD)"),
    ("insulin|caninsulin|prozinc", "Insulin", "Endocrine", "Diabetes"),
    ("frontline|revolution|nexgard|bravecto|milbemax|drontal|proheart|advocate|simparica", "Parasite preventive", "Preventative", "Parasite control"),
    ("nobivac|fvrcp|dhppi|dhlppi|rabisin|vaccin", "Vaccine", "Preventative", "Vaccination"),
    ("royal canin renal|\\bk/d\\b|renal lp|nutraren", "Renal diet", "Urinary & Renal", "Renal / Kidney (CKD)"),
    ("urinary s/o|\\bs/o\\b|\\bc/d\\b|royal canin urinary|urinary", "Urinary diet", "Urinary & Renal", "Urinary / FLUTD"),
    ("gastro|\\bi/d\\b|royal canin gastro|hypoallergenic|anallergenic", "GI/Hypoallergenic diet", "Digestive", "GI upset"),
    ("\\bz/d\\b|\\bd/d\\b|skintopic|sensitivity", "Skin/Allergy diet", "Skin & Coat", "Allergy / Atopy"),
    ("metabolic|satiety|obesity|\\br/d\\b|light", "Weight diet", "Weight & Nutrition", "Weight management"),
    ("dental|\\bt/d\\b|hexarinse|plaqueoff", "Dental care", "Dental", "Dental disease"),
]
COMPILED = [(re.compile(p), disp, m, s) for p, disp, m, s in MED_TERMS]

con = sqlite3.connect(DB); cur = con.cursor()
mentions = collections.Counter()   # (display, main, sub) -> count
n = 0
for dx, note in cur.execute("SELECT dx, note FROM clientssot_pet_history WHERE source='CHS'"):
    n += 1
    blob = f"{dx or ''} {note or ''}".lower()
    for rx, disp, m, s in COMPILED:
        if rx.search(blob):
            mentions[(disp, m, s)] += 1
    if n % 30000 == 0:
        print(f"  ...{n} history rows", flush=True)
print(f"scanned {n} CHS history rows", flush=True)

cur.execute("DROP TABLE IF EXISTS clientssot_chs_products")
cur.execute("CREATE TABLE clientssot_chs_products(name TEXT, main TEXT, sub TEXT, mentions INT)")
cur.executemany("INSERT INTO clientssot_chs_products VALUES (?,?,?,?)",
                [(d, m, s, c) for (d, m, s), c in mentions.items()])
con.commit()
print("\n=== what Dr Hugh's clients were actually on, per theme ===")
for main in [r[0] for r in cur.execute("SELECT DISTINCT main FROM clientssot_chs_products ORDER BY main")]:
    top = cur.execute("SELECT name, mentions FROM clientssot_chs_products WHERE main=? ORDER BY mentions DESC LIMIT 4", (main,)).fetchall()
    print(f"  {main}: " + " | ".join(f"{t[0]} ({t[1]})" for t in top))
con.close()
print("CHS PRODUCT MINING DONE")

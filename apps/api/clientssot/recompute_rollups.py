# -*- coding: utf-8 -*-
"""Recompute per-source rollups on customers (clinic vs online LTV + last purchase date per source) and fix
category misclassifications (drugs that landed in 'Other', e.g. Butorphanol -> Medicine). Fast, in-place."""
import sqlite3, io, sys
from pathlib import Path
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()
con = sqlite3.connect(DB); cur = con.cursor()

# --- 1. category fix: drugs miscategorised as 'Other' -> Medicine (incl controlled/vet-only) ---
DRUGS = ["butorphanol","buprenorphine","temgesic","methadone","fentanyl","morphine","pethidine","ketamine",
         "diazepam","midazolam","phenobarb","propofol","alfaxalone","medetomidine","dexdomitor","atipamezole",
         "furosemide","frusemide","spironolactone","enalapril","benazepril","amlodipine","pimobendan","digoxin",
         "clopidogrel","mirtazapine","ondansetron","omeprazole","ranitidine","sucralfate","lactulose","cisapride",
         "maropitant","metoclopramide","tramadol","gabapentin","amantadine","prednisolone","dexamethasone",
         "cyclosporin","atopica","ciclosporin","methimazole","carbimazole","levothyroxine","enrofloxacin","baytril",
         "marbofloxacin","metronidazole","clindamycin","cephalexin","cefovecin","convenia","doxycycline","amoxicillin",
         "clavulanic","terbinafine","ketoconazole","itraconazole","fluconazole","meloxicam","robenacoxib","firocoxib",
         "tolfenamic","famotidine","cerenia","apoquel","cytopoint","atropine","pilocarpine","dorzolamide","timolol",
         "latanoprost","insulin","vetmedin","fortekor","semintra","cardisure","propalin","phenylpropanolamine"]
like = " OR ".join(["lower(product) LIKE ?"] * len(DRUGS))
fixed = cur.execute(f"UPDATE clientssot_purchases SET category='Medicine' WHERE category='Other' AND ({like})",
                    [f"%{d}%" for d in DRUGS]).rowcount
con.commit()
print(f"reclassified Other->Medicine: {fixed} line items")

# --- 2. per-source rollups on customers ---
for coldef in ("clinic_ltv REAL", "shopify_ltv REAL", "last_clinic TEXT", "last_shopify TEXT"):
    try: cur.execute(f"ALTER TABLE clientssot_customers ADD COLUMN {coldef}")
    except sqlite3.OperationalError: pass
cur.execute("UPDATE clientssot_customers SET clinic_ltv=NULL, shopify_ltv=ltv, last_clinic=NULL, last_shopify=NULL")
# clinic LTV from Ohana invoice line totals (price*qty); Dr Hugh has no price captured
cur.execute("""UPDATE clientssot_customers SET clinic_ltv=(
    SELECT ROUND(SUM(COALESCE(price,0)*COALESCE(qty,1)),0) FROM clientssot_purchases p
    WHERE p.customer_id=clientssot_customers.id AND p.source='Ohana')
    WHERE id IN (SELECT DISTINCT customer_id FROM clientssot_purchases WHERE source='Ohana' AND customer_id IS NOT NULL)""")
cur.execute("""UPDATE clientssot_customers SET last_clinic=(
    SELECT MAX(date) FROM clientssot_purchases p WHERE p.customer_id=clientssot_customers.id
    AND p.source IN ('Ohana','Dr Hugh') AND date!='')
    WHERE id IN (SELECT DISTINCT customer_id FROM clientssot_purchases WHERE source IN ('Ohana','Dr Hugh') AND customer_id IS NOT NULL)""")
cur.execute("""UPDATE clientssot_customers SET last_shopify=(
    SELECT MAX(date) FROM clientssot_purchases p WHERE p.customer_id=clientssot_customers.id
    AND p.source='Shopify' AND date!='')
    WHERE id IN (SELECT DISTINCT customer_id FROM clientssot_purchases WHERE source='Shopify' AND customer_id IS NOT NULL)""")
con.commit()
print("clinic_ltv set:", cur.execute("SELECT COUNT(*) FROM clientssot_customers WHERE clinic_ltv>0").fetchone()[0])
print("last_clinic set:", cur.execute("SELECT COUNT(*) FROM clientssot_customers WHERE last_clinic IS NOT NULL").fetchone()[0])
print("last_shopify set:", cur.execute("SELECT COUNT(*) FROM clientssot_customers WHERE last_shopify IS NOT NULL").fetchone()[0])
con.close()
print("ROLLUPS DONE")

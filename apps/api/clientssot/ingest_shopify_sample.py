# -*- coding: utf-8 -*-
"""PROTOTYPE: plug Shopify (last-12-month sample) into the Client SSOT + tag the product catalog,
to power the recommendation engine — without waiting for Desmond's full-pull API.
Adds: customer-level care tags (Shopify/owner has no pet), LTV + ext tags on customers,
and product->care-type tags on the Rosetta catalog. Sample is ~22 real customers from the live MCP pull;
full ~1,473-buyer ingest comes via Desmond's API / Shopify Admin token."""
import sqlite3, io, sys, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.taxonomy import classify
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import sys as _sys; _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
DB = resolve_db()

def norm_phone(s):
    d = re.sub(r"\D", "", s or "")
    return d[-8:] if len(d) >= 8 else ""

# (email, phone, first, last, ltv, orders, tags_csv, [product titles])  — real, from the live pull
SAMPLE = [
 ("laucwing@gmail.com","",         "Cindy","Lau",26160.62,66,"appstle_active,active_subscriber,cats auto25",["Royal Canin Veterinary Diet Renal Pouches for Cats","Catty Munchies Cat Treats"]),
 ("rabbit_mica6a@icloud.com","",   "Gerard","Leung",3324.08,4,"appstle_active,dogs auto25",["Apoquel Tablet 3.6mg"]),
 ("mayahtang@gmail.com","+85293809077","Maya","Tang",18718.64,37,"active_subscriber,appstle_active,cats auto25",["Royal Canin Veterinary Diet Renal Select Cats","Royal Canin Urinary Pouches for Cats"]),
 ("kong94@gmail.com","+85263485478","Cheung","Kwokkong",140.0,1,"VIP",["Bova Omeprazole 5mg"]),
 ("superprettydudu@gmail.com","",  "Chantel","Li",3178.12,6,"appstle_active,newsletter,prospect",["Hill's Prescription Diet Canine i/d Low Fat Digestive Care"]),
 ("kingtakshum@gmail.com","+85261080018","King tak","Shum",8294.0,11,"appstle_active,Wrote Judge.me email review",["Royal Canin Veterinary Diet Gastrointestinal Low Fat Cans for Dogs"]),
 ("vickyy_wong@yahoo.com","+85298334880","Vicky","Wong",54325.16,53,"active_subscriber,appstle_active,cats auto25",["Royal Canin Veterinary Diet Renal Pouches for Cats"]),
 ("sammytclam@gmail.com","+85267640385","Sammy","Lam",43965.39,53,"active_subscriber,appstle_active,Has Active Subscription",["Royal Canin Veterinary Diet Urinary S/O Dry Food for Cats"]),
 ("kksharonchan@gmail.com","",     "Sharon","Chan",435.32,1,"newsletter,prospect,VIP",["MicrocynAH Eye Wash","Royal Canin Renal Pouches for Cats","Dechra Amoxyclav 50mg"]),
 ("miles.bennett257@gmail.com","+85256262745","Miles","Bennett",6030.0,4,"",["Apoquel Tablets 5.4mg for Dogs"]),
 ("grace507@netvigator.com","+85293651042","Grace","Wong",48326.64,52,"active_subscriber,appstle_active",["Royal Canin Veterinary Diet Diabetic Dry Food for Dogs"]),
 ("yyhui09@icloud.com","",         "Nancy","Hui",4810.3,6,"appstle_active,dogs auto25",["Vetmedin (Pimobendan) Chewable 1.25mg"]),
 ("kathy_dondon@hotmail.com","",   "Kathy","Ko",1132.0,2,"appstle_active,VIP",["KEPPRA Levetiracetam 250mg"]),
 ("lwlchan25@gmail.com","+85293805857","Louis","Chan",464.51,1,"VIP",["Hill's Prescription Diet Canine Metabolic Weight Loss"]),
 ("mswu.kyky@gmail.com","+85264117507","Ms","WU",450.0,1,"VIP",["Revolution Parasite Control for Cats"]),
 ("albertfong230@gmail.com","",    "Albert","Fong",970.55,2,"appstle_active,cats auto25,VIP",["MALASEB Medicated Shampoo","Olimega Omega-Rich Pet Oil"]),
 ("cwtrapness@gmail.com","",       "Helge","Weiner-Trapness",3561.4,3,"appstle_active,dogs auto25",["VetriScience Cardio Strength","Co Enzyme Q10"]),
 ("vcvincichan@gmail.com","",      "Vinci","Chan",432.62,1,"VIP,Wrote Judge.me email review",["Royal Canin Vet Health Skintopic Dry Food for Small Dogs"]),
 ("roger x@outlook.com","",        "Roger","Cheung",604.0,1,"VIP",["Hill's Prescription Diet Feline d/d Food Sensitivities","Royal Canin Hypoallergenic Dry Food for Cats"]),
 ("szcamama@gmail.com","+85295507602","Apoyu","",1620.0,1,"VIP",["NexGard Spectra Parasite Protection Chew"]),
 ("annlee13b@gmail.com","",        "Man Kwan","Li",2597.3,4,"appstle_active,dogs auto25",["Antinol Rapid for dogs"]),
 ("katylee_ym@hotmail.com","",     "Katy","Lee",948.24,2,"appstle_active,VIP",["Vetoquinol Propalin Syrup for Dogs"]),
]

con = sqlite3.connect(DB); cur = con.cursor()

# --- (1) tag the Rosetta product catalog by care-type (unblocked, powers recommendations) ---
cur.execute("DROP TABLE IF EXISTS clientssot_product_caretags")
cur.execute("""CREATE TABLE clientssot_product_caretags(
    sku_code TEXT, name TEXT, brand TEXT, category TEXT, hero_sku INT, main TEXT, sub TEXT,
    PRIMARY KEY(sku_code, main, sub))""")
prod_rows = 0
for sku, name, brand, cat, hero in cur.execute(
        "SELECT sku_code, name, brand, category, hero_sku FROM products WHERE name IS NOT NULL").fetchall():
    kind, main, sub = classify(name)
    if kind == "care":
        cur.execute("INSERT OR IGNORE INTO clientssot_product_caretags VALUES (?,?,?,?,?,?,?)",
                    (sku, name, brand, cat, hero or 0, main, sub))
        prod_rows += 1
print(f"product care-tags: {prod_rows}")

# --- (2) customer-level care tags (owner sources w/ no pet) + LTV/tags columns ---
cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_customer_caretags(
    customer_id TEXT, kind TEXT, main TEXT, sub TEXT, source TEXT, count INT,
    PRIMARY KEY(customer_id, kind, main, sub, source))""")
cur.execute("DELETE FROM clientssot_customer_caretags WHERE source='Shopify'")
for coldef in ("ltv REAL", "order_count INT", "ext_tags TEXT"):
    try: cur.execute(f"ALTER TABLE clientssot_customers ADD COLUMN {coldef}")
    except sqlite3.OperationalError: pass

# match maps
phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!=''") if norm_phone(r[1])}
email2cust = {r[1].lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}

matched = new = 0
for email, phone, first, last, ltv, orders, tags, products in SAMPLE:
    p = norm_phone(phone)
    cid = (email2cust.get(email.lower()) or (phone2cust.get(p) if p else None))
    if cid:
        matched += 1
    else:
        cid = "SHOP:" + email.lower()
        new += 1
        cur.execute("""INSERT INTO clientssot_customers (id, first_name, last_name, last_visit, visit_count, source, email, phone)
            VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING""", (cid, first, last, "", 0, "Shopify", email, phone))
    cur.execute("UPDATE clientssot_customers SET ltv=?, order_count=?, ext_tags=? WHERE id=?", (ltv, orders, tags, cid))
    for title in products:
        kind, main, sub = classify(title)
        if kind:
            cur.execute("""INSERT INTO clientssot_customer_caretags VALUES (?,?,?,?,?,?)
                ON CONFLICT(customer_id,kind,main,sub,source) DO UPDATE SET count=count+1""",
                (cid, kind, main, sub, "Shopify", 1))
con.commit()
print(f"Shopify sample: {len(SAMPLE)} customers — matched to clinic {matched}, new online-only {new}")
print("by care main (Shopify customer tags):")
for r in cur.execute("SELECT main,COUNT(*) n FROM clientssot_customer_caretags WHERE source='Shopify' AND kind='care' GROUP BY main ORDER BY n DESC"):
    print(f"   {r[1]:>3}  {r[0]}")
con.close()
print("SHOPIFY SAMPLE INGEST DONE")

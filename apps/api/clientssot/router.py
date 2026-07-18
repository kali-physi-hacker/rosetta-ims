# -*- coding: utf-8 -*-
"""Client SSOT API — read-only endpoints serving the merged, care-tagged clientbase.
Additive: reads only the clientssot_* tables. Mounted at /clients."""
import sqlite3, csv, io, re
from typing import List
from pathlib import Path
from fastapi import APIRouter, Depends, Query, Response
from dependencies import require_user
from clientssot.taxonomy import TAXONOMY, EVENTS
from clientssot.db_path import resolve_db

DB = resolve_db()   # honours DATABASE_URL on prod (/data/ims.db); backend/ims.db in dev
router = APIRouter(prefix="/clients", tags=["clients"])
# autoship = active subscription per Shopify/Appstle tags
AUTOSHIP = ("(c.ext_tags LIKE '%appstle_active%' OR c.ext_tags LIKE '%active_subscriber%' "
            "OR c.ext_tags LIKE '%Has Active Subscription%')")
# source families for overlap detection
HASPET = "EXISTS(SELECT 1 FROM clientssot_pets p JOIN clientssot_pet_caretags t ON t.pet_id=p.id WHERE p.customer_id=c.id)"
INCRM  = "EXISTS(SELECT 1 FROM clientssot_customer_crm m WHERE m.customer_id=c.id)"
CLINIC = f"({HASPET} OR c.visit_count>0)"
ONLINE = "(c.ltv IS NOT NULL)"
OVERLAP2 = f"(({CLINIC}) + {ONLINE} + ({INCRM})) >= 2"   # in 2+ channels: clinic / online / CRM
HASTAG = "SELECT p.customer_id FROM clientssot_pets p JOIN clientssot_pet_caretags t ON t.pet_id=p.id"
CUSTAG = "SELECT customer_id FROM clientssot_customer_caretags"
CHS_PRED = f"c.id IN ({HASTAG} WHERE t.source='CHS')"
CH_PREDS = {   # channel membership (for multi-select intersection)
    "DaySmart": f"(c.visit_count>0 OR c.id IN ({HASTAG} WHERE t.source='DaySmart'))",
    "CHS": CHS_PRED, "Shopify": "c.ltv IS NOT NULL", "Klaviyo": INCRM,
}
# marketing consent (derived proxy — opt-in signals). NOTE: a proper PDPO model needs explicit,
# timestamped opt-in records per channel; this approximates from Klaviyo membership + marketing tags.
# consent: REAL Klaviyo SUBSCRIBED status where known, else the membership/tag proxy
_CONSENT_PROXY = (f"({INCRM} OR COALESCE(c.ext_tags,'') LIKE '%newsletter%' "
                  f"OR COALESCE(c.ext_tags,'') LIKE '%accepts_marketing%' OR COALESCE(c.ext_tags,'') LIKE '%subscriber%')")
CONSENT = f"(c.email_consent IS 1 OR (c.email_consent IS NULL AND {_CONSENT_PROXY}))"
ENGAGED = "(c.last_engagement >= '2026-03-23')"   # opened/clicked something in last ~90 days
# operational: customer has actively reached out to CS via WhatsApp (Slack-mirrored)
CS_CONTACTED = "c.id IN (SELECT customer_id FROM clientssot_cs_contacts WHERE customer_id IS NOT NULL)"
# CHANNEL REACHABILITY — each drives a different action:
#   email_consent -> Klaviyo CRM email   |   whatsapp -> ChatArchitect WhatsApp blast   |   meta -> Meta ad audience
# (email = CONSENT proxy until Klaviyo real-consent pull; whatsapp = Klaviyo WA list OR has CS contact until
#  ChatArchitect /export opt-in list; meta = we simply have a contact to upload as a hashed custom audience.)
EMAIL_CONSENT = CONSENT
# WhatsApp-able = CONSENTED / opted-in to WhatsApp (ChatArchitect opt-in + Klaviyo WhatsApp lists).
# NOT the people who happen to be messaging us right now — that's an Operations/CS signal (CS_CONTACTED).
WHATSAPP_REACH = "c.id IN (SELECT customer_id FROM clientssot_customer_crm WHERE channel='whatsapp')"
META_REACH = "((c.email IS NOT NULL AND c.email!='') OR (c.phone IS NOT NULL AND c.phone!=''))"
META_ONLY = f"({META_REACH} AND NOT {EMAIL_CONSENT} AND NOT {WHATSAPP_REACH})"
_CS_SENT = "c.id IN (SELECT customer_id FROM clientssot_cs_contacts WHERE customer_id IS NOT NULL AND sentiment=?)"
OPS_PREDS = {   # the Ops dropdown (operational status)
    "cs": (CS_CONTACTED, None),
    "unfulfilled": ("c.shop_unfulfilled > 0", None),
    "happy": (_CS_SENT, "happy"),
    "fine": (_CS_SENT, "fine"),
    "poor": (_CS_SENT, "poor"),
}
SEG_PREDS = {  # provenance + commercial segments
    "new": f"c.visit_count > 0 AND NOT {CHS_PRED}",
    "legacy_active": f"c.visit_count > 0 AND {CHS_PRED}",
    "legacy_dormant": f"(c.visit_count = 0 OR c.visit_count IS NULL) AND {CHS_PRED}",
    "online": "c.ltv IS NOT NULL", "autoship": AUTOSHIP, "onetime": "c.order_count = 1",
    # lapsed = no PURCHASE in 12 months (visit data only goes back ~11mo + includes future bookings, so
    # visit-based lapsed was a coverage artifact = 0). Purchases span 2020-now -> the real lapsed signal.
    "lapsed": "c.last_purchase IS NOT NULL AND c.last_purchase < '2025-06-21'",
    "crm": INCRM, "overlap": OVERLAP2, "consented": CONSENT, "cs": CS_CONTACTED, "engaged": ENGAGED,
    "psg": "c.bought_psg = 1",   # bought a PSG-SKU Rx product (2020-21 partnership era) — high-value Rx audience
    "email_consent": EMAIL_CONSENT, "whatsapp": WHATSAPP_REACH, "meta_only": META_ONLY,
}
CH_PREDS["Consented"] = CONSENT   # marketing-reachable as a funnel channel

# ===== GROUPED MULTI-SELECT FILTER FAMILIES =====
# CUSTOMERS (AND within group) — 3 buckets + sub-categories
CUST_PREDS = {
    "chs": CHS_PRED,                                       # Dr Hugh (CHS)
    "ohana": "(c.visit_count>0 OR c.id IN (SELECT p.customer_id FROM clientssot_pets p JOIN clientssot_pet_caretags t ON t.pet_id=p.id WHERE t.source='DaySmart'))",
    "new_ohana": f"(c.visit_count > 0 AND NOT {CHS_PRED})",  # new to Ohana (no Dr Hugh history)
    "online": "c.ltv IS NOT NULL",                        # Online (Shopify) buyer
    "onetime": "c.order_count = 1",
    "autoship": AUTOSHIP,
    "rx_client": "(c.bought_rx = 1 AND c.ltv IS NOT NULL)",   # online buyers who've bought Rx (true Website subset)
    "psg": "c.bought_psg = 1",                            # PSG 2020-21 Rx audience
    # Prospect = on a list / in CRM but NEVER bought or visited (mailing-list non-buyers to convert)
    "prospect": ("(EXISTS(SELECT 1 FROM clientssot_customer_crm m WHERE m.customer_id=c.id) "
                 "AND c.first_purchase IS NULL AND COALESCE(c.visit_count,0)=0 AND COALESCE(c.ltv,0)=0)"),
}
# CONSENTS (OR within group) — reachability by type. Meta = has contact info (NOT a leftover of email/WA)
CONSENT_PREDS = {
    "no_contact": f"NOT {META_REACH}",
    "contact_no_consent": f"({META_REACH} AND NOT {EMAIL_CONSENT} AND NOT {WHATSAPP_REACH})",
    "email": EMAIL_CONSENT,
    "whatsapp": WHATSAPP_REACH,
}
# OPERATIONS (OR within group)
OPSGRP = {
    "unfulfilled": "c.shop_unfulfilled > 0",
    "fulfilled": "(c.order_count > 0 AND (c.shop_unfulfilled IS NULL OR c.shop_unfulfilled = 0))",
    "cs": CS_CONTACTED,
    "happy": "c.id IN (SELECT customer_id FROM clientssot_cs_contacts WHERE sentiment='happy')",
    "fine": "c.id IN (SELECT customer_id FROM clientssot_cs_contacts WHERE sentiment='fine')",
    "poor": "c.id IN (SELECT customer_id FROM clientssot_cs_contacts WHERE sentiment='poor')",
}

# a "cold lead" = an online-only contact (Shopify) who never bought (no LTV) — newsletter/lead signups.
# Hidden from the default view; an include_leads flag brings them back.
LEAD_ONLY = "(c.id LIKE 'SHOP:%' AND c.ltv IS NULL)"

# First-touch (marketing-relevant only): keep real campaign UTMs; drop system/app noise (Shopping feed, review
# apps, cart-reminders). And keep campaign-relevant landing pages; drop home/cart/search/account/checkout noise.
MKT_UTM = ("first_utm_campaign IS NOT NULL AND first_utm_campaign!='' "
           "AND COALESCE(first_utm_medium,'')!='product_sync' "
           "AND COALESCE(first_utm_source,'') NOT IN ('judgeme','abandoned_cart','shopify') "
           "AND first_utm_campaign NOT IN ('sag_organic','judgeme-review-request','shopify_cart_reminder')")
LANDING_OK = ("first_landing_path IS NOT NULL AND first_landing_path NOT IN "
              "('/ (home page)','/cart','/search','/account','/account/login','/account/register','/password') "
              "AND first_landing_path NOT LIKE '/account%' AND first_landing_path NOT LIKE '/checkout%' "
              "AND first_landing_path NOT LIKE '/challenge%' AND first_landing_path NOT LIKE '/tools%'")

def build_where(main=None, search=None, source=None, segment=None, channels=None, include_leads=False, ops=None,
                pbefore=None, pafter=None, preacq=None, rx=None, bought_cat=None, bought_product=None,
                cust=None, dcat=None, dprod=None, consents=None, opsl=None, dcoll=None, crm=None,
                xprod=None, xcat=None, pfrom=None, pto=None, flow=None, discount=None, dfam=None, landingsrc=None,
                utmcamp=None, landing=None, referral=None):
    """Build the WHERE clause + params shared by the list/funnel/campaign/export endpoints.
    Purchase filters: pbefore/pafter = last_purchase range (lapsed/active); preacq = first_purchase before a
    date (pre-acquisition); rx = ever bought Rx; bought_cat = ever bought a product in a Rosetta category."""
    where, params = [], []
    # prospects ARE leads (mailing-list non-buyers), so selecting them must not be hidden by the lead filter
    if not include_leads and not (cust and "prospect" in cust):
        where.append(f"NOT {LEAD_ONLY}")
    if pbefore:   # "no purchase since" — lapsed/reactivation (most-recent buy predates the date)
        where.append("c.last_purchase IS NOT NULL AND c.last_purchase < ?"); params.append(pbefore)
    if pafter:
        where.append("c.last_purchase >= ?"); params.append(pafter)
    if pfrom or pto:   # "purchased between X–Y": made >=1 purchase inside the window (any source)
        cond = ["date != ''"]
        if pfrom:
            cond.append("date >= ?")
        if pto:
            cond.append("date <= ?")
        where.append(f"c.id IN (SELECT customer_id FROM clientssot_purchases WHERE {' AND '.join(cond)})")
        if pfrom:
            params.append(pfrom)
        if pto:
            params.append(pto)
    if preacq:
        where.append("c.first_purchase IS NOT NULL AND c.first_purchase < ?"); params.append(preacq)
    if rx:
        where.append("c.bought_rx = 1")
    if bought_cat:
        where.append("c.id IN (SELECT customer_id FROM clientssot_purchases WHERE category=?)"); params.append(bought_cat)
    if bought_product:
        where.append("c.id IN (SELECT customer_id FROM clientssot_purchases WHERE product=?)"); params.append(bought_product)
    if search:
        where.append("((c.first_name || ' ' || c.last_name) LIKE ? OR c.email LIKE ? OR c.phone LIKE ?)")
        params += [f"%{search}%"] * 3
    if main:
        where.append(f"(c.id IN ({HASTAG} WHERE t.kind='care' AND t.main=?) OR c.id IN ({CUSTAG} WHERE kind='care' AND main=?))")
        params += [main, main]
    if source == "overlap":
        where.append(f"c.id IN ({HASTAG} WHERE t.source='DaySmart' INTERSECT {HASTAG} WHERE t.source='CHS')")
    elif source in ("DaySmart", "CHS", "Shopify", "Klaviyo"):
        where.append(f"(c.id IN ({HASTAG} WHERE t.source=?) OR c.id IN ({CUSTAG} WHERE source=?))")
        params += [source, source]
    if segment in SEG_PREDS:
        where.append(SEG_PREDS[segment])
    for ch in (channels or []):
        if ch in CH_PREDS:
            where.append(CH_PREDS[ch])
    if ops in OPS_PREDS:
        pred, p = OPS_PREDS[ops]
        where.append(pred)
        if p is not None:
            params.append(p)
    # --- grouped multi-select families ---
    for k in (cust or []):            # Customers: AND within group
        if k in CUST_PREDS:
            where.append(CUST_PREDS[k])
    for cat in (dcat or []):          # Demand Record categories: AND (bought in all)
        where.append("c.id IN (SELECT customer_id FROM clientssot_purchases WHERE category=?)"); params.append(cat)
    for prod in (dprod or []):        # Demand Record products: AND (bought all)
        where.append("c.id IN (SELECT customer_id FROM clientssot_purchases WHERE product=?)"); params.append(prod)
    for fam in (dfam or []):          # Demand Record product-family: AND (bought any product in the family)
        where.append("c.id IN (SELECT customer_id FROM clientssot_purchases WHERE COALESCE(NULLIF(family,''),product)=?)"); params.append(fam)
    for coll in (dcoll or []):        # Demand Record collections: AND (bought from each collection)
        where.append("c.id IN (SELECT customer_id FROM clientssot_purchases WHERE product IN "
                     "(SELECT product FROM clientssot_collections WHERE collection=?))"); params.append(coll)
    if consents:                      # Consents: OR within group
        ors = [CONSENT_PREDS[k] for k in consents if k in CONSENT_PREDS]
        if ors:
            where.append("(" + " OR ".join(ors) + ")")
    if opsl:                          # Operations: OR within group
        ors = [OPSGRP[k] for k in opsl if k in OPSGRP]
        if ors:
            where.append("(" + " OR ".join(ors) + ")")
    if crm:                           # CRM Marketing: in ANY of the selected Klaviyo lists (OR)
        ph = ",".join("?" * len(crm))
        where.append(f"c.id IN (SELECT customer_id FROM clientssot_customer_crm WHERE list_name IN ({ph}))")
        params.extend(crm)
    if flow:                          # CRM: received ANY of the selected Klaviyo flows (OR)
        ph = ",".join("?" * len(flow))
        where.append(f"c.id IN (SELECT customer_id FROM clientssot_crm_flows WHERE flow_name IN ({ph}))")
        params.extend(flow)
    if discount:                      # CRM: claimed ANY of the selected discount codes (OR)
        ph = ",".join("?" * len(discount))
        where.append(f"c.id IN (SELECT customer_id FROM clientssot_crm_discounts WHERE code IN ({ph}))")
        params.extend(discount)
    if landingsrc:                    # (legacy) first-touch coarse source — OR
        ph = ",".join("?" * len(landingsrc))
        where.append(f"c.first_source IN ({ph})")
        params.extend(landingsrc)
    if utmcamp:                       # FIRST TOUCH — marketing campaign / partner (UTM) — OR
        ph = ",".join("?" * len(utmcamp))
        where.append(f"c.first_utm_campaign IN ({ph})")
        params.extend(utmcamp)
    if landing:                       # FIRST TOUCH — specific landing page (path) — OR
        ph = ",".join("?" * len(landing))
        where.append(f"c.first_landing_path IN ({ph})")
        params.extend(landing)
    if referral:                      # FIRST TOUCH — external referral backlink — OR
        ph = ",".join("?" * len(referral))
        where.append(f"c.first_referral IN ({ph})")
        params.extend(referral)
    for prod in (xprod or []):        # EXCLUDE buyers of a product (e.g. NexGard but NOT Heartgard)
        where.append("c.id NOT IN (SELECT customer_id FROM clientssot_purchases WHERE product=?)"); params.append(prod)
    for cat in (xcat or []):          # EXCLUDE buyers of a category
        where.append("c.id NOT IN (SELECT customer_id FROM clientssot_purchases WHERE category=?)"); params.append(cat)
    return (("WHERE " + " AND ".join(where)) if where else ""), params

def top_products_by_main(cur):
    """The single best product to recommend per care-type: a 'winner' (prescribed at clinic AND selling
    online) if one exists, else the top online seller, else the top clinic-prescribed item."""
    clinic, online = {}, {}
    try:
        for r in cur.execute("SELECT main, name, mentions FROM clientssot_chs_products ORDER BY mentions DESC"):
            clinic.setdefault(r["main"], (r["name"], r["mentions"]))
    except sqlite3.OperationalError:
        pass
    try:
        for r in cur.execute("SELECT main, name, units FROM clientssot_product_sales WHERE main IS NOT NULL ORDER BY units DESC"):
            online.setdefault(r["main"], (r["name"], r["units"]))
    except sqlite3.OperationalError:
        pass
    out = {}
    for m in set(list(clinic) + list(online)):
        cn, on = clinic.get(m), online.get(m)
        name = None
        if cn and on:
            kw = cn[0].lower().replace("/", " ").split()[0]
            if kw and kw in on[0].lower():
                name = on[0]            # the sellable SKU that matches what they were prescribed = winner
        if not name:
            name = (on[0] if on else (cn[0] if cn else None))
        if name:
            out[m] = name
    return out

def _db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def _ensure_tables():
    con = sqlite3.connect(DB); cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_customer_caretags(
        customer_id TEXT, kind TEXT, main TEXT, sub TEXT, source TEXT, count INT,
        PRIMARY KEY(customer_id, kind, main, sub, source))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_customer_crm(
        customer_id TEXT, source TEXT, list_name TEXT, channel TEXT,
        PRIMARY KEY(customer_id, source, list_name))""")
    cur.execute("""CREATE TABLE IF NOT EXISTS clientssot_product_caretags(
        sku_code TEXT, name TEXT, brand TEXT, category TEXT, hero_sku INT, main TEXT, sub TEXT,
        PRIMARY KEY(sku_code, main, sub))""")
    con.commit(); con.close()
# NOTE: tables are created by the ingest scripts; we do NOT call _ensure_tables() at import
# (an import-time write would contend for the DB lock with a running ingest and hang startup).

@router.get("/products")
def products(q: str = Query(""), _=Depends(require_user)):
    """Search purchased products (for the Demand Record filter): name match -> distinct buyer count."""
    con = _db(); cur = con.cursor()
    try:
        rows = cur.execute("""SELECT product, category, GROUP_CONCAT(DISTINCT source) AS sources,
            COUNT(DISTINCT customer_id) n FROM clientssot_purchases
            WHERE product LIKE ? AND customer_id IS NOT NULL GROUP BY product ORDER BY n DESC LIMIT 20""",
            (f"%{q}%",)).fetchall()
        out = [{"product": r["product"], "category": r["category"],
                "sources": (r["sources"] or "").replace("Ohana", "Clinic").replace("Dr Hugh", "Clinic"), "n": r["n"]} for r in rows]
    except sqlite3.OperationalError:
        out = []
    con.close()
    return {"products": out}

@router.get("/collections")
def collections_list(q: str = Query(""), _=Depends(require_user)):
    """Search Shopify collections (for the Demand Record filter): collection -> distinct buyer count."""
    con = _db(); cur = con.cursor()
    try:
        rows = cur.execute("""SELECT col.collection, COUNT(DISTINCT p.customer_id) n
            FROM clientssot_collections col JOIN clientssot_purchases p ON p.product = col.product
            WHERE col.collection LIKE ? AND p.customer_id IS NOT NULL
              AND lower(col.collection) NOT LIKE '%do not delete%'
              AND lower(col.collection) NOT LIKE '%filter index%'
              AND col.collection NOT IN ('ALL','All Dogs','All Cats')
            GROUP BY col.collection ORDER BY n DESC LIMIT 25""", (f"%{q}%",)).fetchall()
        out = [{"collection": r["collection"], "n": r["n"]} for r in rows]
    except sqlite3.OperationalError:
        out = []
    con.close()
    return {"collections": out}

@router.get("/taxonomy")
def taxonomy(_=Depends(require_user)):
    """The care-type tree (main -> sub) + event types, for the filter UI."""
    return {"taxonomy": TAXONOMY, "events": EVENTS}

@router.get("/summary")
def summary(include_leads: bool = Query(False), _=Depends(require_user)):
    """Counts for the header: customers, pets, and pets per main care category."""
    con = _db(); cur = con.cursor()
    lead = "" if include_leads else f" AND NOT {LEAD_ONLY}"
    chs = ("c.id IN (SELECT p.customer_id FROM clientssot_pets p "
           "JOIN clientssot_pet_caretags t ON t.pet_id=p.id WHERE t.source='CHS')")
    def cnt(cond):
        return cur.execute(f"SELECT COUNT(*) FROM clientssot_customers c WHERE {cond}{lead}").fetchone()[0]
    total = cur.execute(f"SELECT COUNT(*) FROM clientssot_customers c WHERE 1=1{lead}").fetchone()[0]
    out = {
        "customers": total,
        "pets": cur.execute("SELECT COUNT(*) FROM clientssot_pets").fetchone()[0],
        "by_main": {r["main"]: r["n"] for r in cur.execute(
            "SELECT main, COUNT(DISTINCT pet_id) n FROM clientssot_pet_caretags "
            "WHERE kind='care' GROUP BY main ORDER BY n DESC")},
        "by_segment": {
            "all": total,
            "new": cnt(f"c.visit_count > 0 AND NOT {chs}"),
            "legacy_active": cnt(f"c.visit_count > 0 AND {chs}"),
            "legacy_dormant": cnt(f"(c.visit_count = 0 OR c.visit_count IS NULL) AND {chs}"),
            "online": cnt("c.ltv IS NOT NULL"),
            "autoship": cnt(AUTOSHIP),
            "onetime": cnt("c.order_count = 1"),
            "lapsed": cnt("c.last_purchase IS NOT NULL AND c.last_purchase < '2025-06-21'"),
            "crm": cnt(INCRM),
            "overlap": cnt(OVERLAP2),
            "consented": cnt(CONSENT),
            "cs": cnt(CS_CONTACTED),
            "engaged": cnt(ENGAGED),
            "psg": cnt("c.bought_psg = 1"),
            "email_consent": cnt(EMAIL_CONSENT), "whatsapp": cnt(WHATSAPP_REACH), "meta_only": cnt(META_ONLY),
        },
    }
    tpm = top_products_by_main(cur)
    out["reco_products"] = sorted(
        [{"main": m, "product": p, "n": out["by_main"].get(m, 0)} for m, p in tpm.items()],
        key=lambda x: -x["n"])
    sent = "c.id IN (SELECT customer_id FROM clientssot_cs_contacts WHERE sentiment=%r)"
    out["ops_counts"] = {
        "cs": cnt(CS_CONTACTED),
        "unfulfilled": cnt("c.shop_unfulfilled > 0"),
        "happy": cnt(sent % "happy"), "fine": cnt(sent % "fine"), "poor": cnt(sent % "poor"),
    }
    # % of customers who have ACTUALLY bought in each Rosetta category (shapes marketing direction)
    try:
        out["by_purchase_cat"] = {r["category"]: r["n"] for r in cur.execute(
            "SELECT category, COUNT(DISTINCT customer_id) n FROM clientssot_purchases "
            "WHERE category!='Other' AND category IS NOT NULL AND customer_id IS NOT NULL GROUP BY category ORDER BY n DESC")}
        # top vet-exclusive / Rx products that ARE sold online (the digitization-upsell targets)
        out["top_vet_products"] = [{"product": r["product"], "n": r["n"]} for r in cur.execute(
            "SELECT product, COUNT(DISTINCT customer_id) n FROM clientssot_purchases "
            "WHERE category IN ('Medicine','Preventative','Prescription Diet') AND on_shopify=1 "
            "AND customer_id IS NOT NULL GROUP BY product ORDER BY n DESC LIMIT 25")]
    except sqlite3.OperationalError:
        out["by_purchase_cat"] = {}; out["top_vet_products"] = []
    # counts for the grouped multi-select chips
    out["filters"] = {
        "cust": {k: cnt(v) for k, v in CUST_PREDS.items()},
        "consents": {k: cnt(v) for k, v in CONSENT_PREDS.items()},
        "ops": {k: cnt(v) for k, v in OPSGRP.items()},
    }
    # prospects ARE leads by definition, so count them WITHOUT the lead filter (matches the list view)
    out["filters"]["cust"]["prospect"] = cur.execute(
        f"SELECT COUNT(*) FROM clientssot_customers c WHERE {CUST_PREDS['prospect']}").fetchone()[0]
    # CRM Marketing: the Klaviyo lists/flows (+ promo claims) by name, for the CRM filter chips
    try:
        out["crm_lists"] = [{"name": r["list_name"], "n": r["n"]} for r in cur.execute(
            "SELECT list_name, COUNT(DISTINCT customer_id) n FROM clientssot_customer_crm "
            "WHERE list_name IS NOT NULL AND list_name NOT IN ('None','') "
            "GROUP BY list_name ORDER BY n DESC LIMIT 25")]
    except sqlite3.OperationalError:
        out["crm_lists"] = []
    try:   # Klaviyo flows received (frequency/spam signal) — name + distinct customers
        out["crm_flows"] = [{"name": r["flow_name"], "n": r["n"]} for r in cur.execute(
            "SELECT flow_name, COUNT(DISTINCT customer_id) n FROM clientssot_crm_flows "
            "WHERE flow_name IS NOT NULL GROUP BY flow_name ORDER BY n DESC LIMIT 30")]
        out["crm_discounts"] = [{"code": r["code"], "n": r["n"]} for r in cur.execute(
            "SELECT code, COUNT(DISTINCT customer_id) n FROM clientssot_crm_discounts "
            "GROUP BY code ORDER BY n DESC LIMIT 20")]
    except sqlite3.OperationalError:
        out["crm_flows"] = []; out["crm_discounts"] = []
    # FIRST TOUCH (marketing-relevant, recent online journeys only): campaign (UTM) / landing page / referral.
    try:
        out["utm_campaigns"] = [{"campaign": r["first_utm_campaign"], "source": r["first_utm_source"],
                                 "medium": r["first_utm_medium"], "n": r["n"]} for r in cur.execute(
            f"SELECT first_utm_campaign, first_utm_source, first_utm_medium, COUNT(*) n FROM clientssot_customers c "
            f"WHERE {MKT_UTM}{lead} GROUP BY first_utm_campaign ORDER BY n DESC LIMIT 30")]
    except sqlite3.OperationalError:
        out["utm_campaigns"] = []
    try:
        out["landing_pages"] = [{"path": r["first_landing_path"], "n": r["n"]} for r in cur.execute(
            f"SELECT first_landing_path, COUNT(*) n FROM clientssot_customers c WHERE {LANDING_OK}{lead} "
            f"GROUP BY first_landing_path ORDER BY n DESC LIMIT 30")]
    except sqlite3.OperationalError:
        out["landing_pages"] = []
    try:
        out["referrals"] = [{"domain": r["first_referral"], "n": r["n"]} for r in cur.execute(
            f"SELECT first_referral, COUNT(*) n FROM clientssot_customers c WHERE first_referral IS NOT NULL{lead} "
            f"GROUP BY first_referral ORDER BY n DESC LIMIT 20")]
    except sqlite3.OperationalError:
        out["referrals"] = []
    con.close()
    return out

@router.get("")
def list_clients(main: str = Query(None), search: str = Query(None), source: str = Query(None),
                 segment: str = Query(None), channels: List[str] = Query(None), include_leads: bool = Query(False),
                 ops: str = Query(None), pbefore: str = Query(None), pafter: str = Query(None),
                 preacq: str = Query(None), rx: bool = Query(False), bought_cat: str = Query(None),
                 bought_product: str = Query(None),
                 cust: List[str] = Query(None), dcat: List[str] = Query(None), dprod: List[str] = Query(None),
                 consents: List[str] = Query(None), opsl: List[str] = Query(None), dcoll: List[str] = Query(None), crm: List[str] = Query(None), xprod: List[str] = Query(None), xcat: List[str] = Query(None), pfrom: str = Query(None), pto: str = Query(None), flow: List[str] = Query(None), discount: List[str] = Query(None), dfam: List[str] = Query(None), landingsrc: List[str] = Query(None), utmcamp: List[str] = Query(None), landing: List[str] = Query(None), referral: List[str] = Query(None),
                 sort: str = Query(None), sortdir: str = Query("desc"),
                 limit: int = 200, offset: int = 0, _=Depends(require_user)):
    """List CUSTOMERS (every one — incl. those with no pet/appointment), with pets + care tags
    aggregated across their pets. Filters: main care category, name/email/phone search,
    source ('DaySmart'|'CHS'|'overlap'), and provenance segment
    ('new' = Ohana, no Dr Hugh's history; 'legacy_active' = Dr Hugh's + returned;
     'legacy_dormant' = Dr Hugh's history but never returned to Ohana — the reactivation goldmine)."""
    con = _db(); cur = con.cursor()
    wsql, params = build_where(main, search, source, segment, channels, include_leads, ops,
                              pbefore, pafter, preacq, rx, bought_cat, bought_product,
                              cust, dcat, dprod, consents, opsl, dcoll, crm, xprod, xcat, pfrom, pto, flow, discount, dfam, landingsrc, utmcamp, landing, referral)
    total = cur.execute(f"SELECT COUNT(*) FROM clientssot_customers c {wsql}", params).fetchone()[0]
    # explicit column sort (clickable headers) takes priority; else sensible per-view defaults
    SORT_COLS = {
        "owner": "(c.first_name || ' ' || c.last_name)",
        "last_buy": "c.last_purchase", "first_buy": "c.first_purchase",
        "ltv": "(COALESCE(c.clinic_ltv,0) + COALESCE(c.shopify_ltv,0))",
        "needs": "(SELECT COUNT(DISTINCT category) FROM clientssot_purchases p WHERE p.customer_id=c.id AND category!='Other')",
        "ops": "COALESCE(c.shop_unfulfilled,0)",
    }
    if sort in SORT_COLS:
        col = SORT_COLS[sort]; d = "ASC" if sortdir == "asc" else "DESC"
        order = f"ORDER BY ({col} IS NULL), {col} {d}"
    elif segment == "cs" or ops in ("cs", "happy", "fine", "poor"):
        order = "ORDER BY (SELECT last_contact FROM clientssot_cs_contacts WHERE customer_id=c.id) DESC, c.visit_count DESC"
    elif ops == "unfulfilled":
        order = "ORDER BY c.shop_unfulfilled_oldest ASC"
    else:
        order = "ORDER BY (c.last_visit IS NULL OR c.last_visit='') ASC, c.last_visit DESC, c.visit_count DESC"
    crows = cur.execute(f"""SELECT c.id cid, c.first_name, c.last_name, c.email, c.phone, c.last_visit, c.visit_count,
        c.ltv, c.order_count, c.ext_tags, c.shop_unfulfilled, c.shop_unfulfilled_oldest,
        c.first_purchase, c.last_purchase, c.bought_rx, c.last_engagement,
        c.clinic_ltv, c.shopify_ltv, c.last_clinic, c.last_shopify, c.first_landing, c.first_landing_path, c.first_source,
        c.first_referral, c.first_utm_campaign, c.first_utm_source, c.first_utm_medium
        FROM clientssot_customers c {wsql}
        {order}
        LIMIT ? OFFSET ?""", params + [limit, offset]).fetchall()
    cids = [r["cid"] for r in crows]
    pets_by, tags_by, crm_by, cs_by, purch_by, cat_by = {}, {}, {}, {}, {}, {}
    purch_clinic, purch_online, wa_optin = {}, {}, set()
    flows_by, disc_by = {}, {}
    if cids:
        q = ",".join("?" * len(cids))
        petrow = {}
        for p in cur.execute(f"""SELECT p.id pid, p.customer_id, p.name, p.species, p.breed, s.summary
                FROM clientssot_pets p LEFT JOIN clientssot_pet_summary s ON s.pet_id=p.id
                WHERE p.customer_id IN ({q})""", cids):
            pets_by.setdefault(p["customer_id"], []).append(
                {"pet_id": p["pid"], "name": p["name"], "species": p["species"], "breed": p["breed"], "summary": p["summary"]})
            petrow[p["pid"]] = p["customer_id"]
        agg = {}  # (cid, kind, main, sub) -> {count, sources} — unifies pet-level + customer-level (owner) tags
        if petrow:
            qp = ",".join("?" * len(petrow))
            for t in cur.execute(f"""SELECT pet_id, kind, main, sub, source, count FROM clientssot_pet_caretags
                    WHERE pet_id IN ({qp})""", list(petrow.keys())):
                key = (petrow[t["pet_id"]], t["kind"], t["main"], t["sub"])
                e = agg.setdefault(key, {"count": 0, "sources": set()})
                e["count"] += t["count"]
                if t["source"]:
                    e["sources"].add(t["source"])
        for t in cur.execute(f"""SELECT customer_id, kind, main, sub, source, count FROM clientssot_customer_caretags
                WHERE customer_id IN ({q})""", cids):
            key = (t["customer_id"], t["kind"], t["main"], t["sub"])
            e = agg.setdefault(key, {"count": 0, "sources": set()})
            e["count"] += t["count"]
            if t["source"]:
                e["sources"].add(t["source"])
        for (cidx, kind, mn, sub), e in agg.items():
            tags_by.setdefault(cidx, []).append({"kind": kind, "main": mn, "sub": sub,
                                                 "count": e["count"], "sources": sorted(e["sources"])})
        for v in tags_by.values():
            v.sort(key=lambda x: -x["count"])
        for r in cur.execute(f"SELECT customer_id, list_name, channel FROM clientssot_customer_crm WHERE customer_id IN ({q})", cids):
            if r["channel"] == "whatsapp":
                wa_optin.add(r["customer_id"])
            if r["list_name"] and r["list_name"] not in ("None", ""):
                crm_by.setdefault(r["customer_id"], []).append(r["list_name"])
        try:   # Klaviyo flows received (with last date + send count) per customer
            for r in cur.execute(f"SELECT customer_id, flow_name, sends, last_date FROM clientssot_crm_flows WHERE customer_id IN ({q}) ORDER BY last_date DESC", cids):
                flows_by.setdefault(r["customer_id"], []).append({"flow": r["flow_name"], "sends": r["sends"], "last": r["last_date"]})
            for r in cur.execute(f"SELECT customer_id, code, redemptions, last_date FROM clientssot_crm_discounts WHERE customer_id IN ({q}) ORDER BY last_date DESC", cids):
                disc_by.setdefault(r["customer_id"], []).append({"code": r["code"], "n": r["redemptions"], "last": r["last_date"]})
        except sqlite3.OperationalError:
            pass
        try:
            for r in cur.execute(f"SELECT customer_id, channel, last_contact, msg_count, sentiment, sentiment_quote FROM clientssot_cs_contacts WHERE customer_id IN ({q})", cids):
                cs_by[r["customer_id"]] = {"channel": r["channel"], "last_contact": r["last_contact"],
                                           "msg_count": r["msg_count"], "sentiment": r["sentiment"],
                                           "quote": r["sentiment_quote"]}
        except sqlite3.OperationalError:
            pass
        try:
            for r in cur.execute(f"""SELECT customer_id, date, product, source, category, on_shopify
                    FROM clientssot_purchases WHERE customer_id IN ({q}) AND date!=''
                    ORDER BY date DESC""", cids):
                item = {"date": r["date"], "product": r["product"], "source": r["source"],
                        "category": r["category"], "on_shopify": bool(r["on_shopify"])}
                lst = purch_by.setdefault(r["customer_id"], [])
                if len(lst) < 3:
                    lst.append(item)
                if r["source"] in ("Ohana", "Dr Hugh"):     # clinic side (Dr Hugh + Ohana)
                    cl = purch_clinic.setdefault(r["customer_id"], [])
                    if len(cl) < 3:
                        cl.append(item)
                elif r["source"] == "Shopify":              # online side
                    ol = purch_online.setdefault(r["customer_id"], [])
                    if len(ol) < 3:
                        ol.append(item)
                if r["category"] and r["category"] != "Other":   # demonstrated-need rollup
                    cat_by.setdefault(r["customer_id"], {})
                    cat_by[r["customer_id"]][r["category"]] = cat_by[r["customer_id"]].get(r["category"], 0) + 1
        except sqlite3.OperationalError:
            pass
    prod_by_main = {r["main"]: r["n"] for r in cur.execute(
        "SELECT main, COUNT(DISTINCT name) n FROM clientssot_product_caretags GROUP BY main")}
    tpm = top_products_by_main(cur)
    con.close()
    def care_mains(cid):
        return sorted({t["main"] for t in tags_by.get(cid, []) if t["kind"] == "care"})
    def reco_count(cid):
        return sum(prod_by_main.get(m, 0) for m in care_mains(cid))
    def top_reco(cid):
        # the ONE product to recommend. If a 'pitch product' (main) filter is active, show that product
        # for the whole cohort (they all matched it); otherwise the customer's dominant care need.
        if main and tpm.get(main):
            return {"name": tpm[main], "main": main}
        for t in tags_by.get(cid, []):          # tags_by is sorted by count desc
            if t["kind"] == "care" and tpm.get(t["main"]):
                return {"name": tpm[t["main"]], "main": t["main"]}
        return None
    def sources_of(cid, ltv):
        s = {x for t in tags_by.get(cid, []) for x in t["sources"]}
        if ltv is not None:
            s.add("Shopify")
        if crm_by.get(cid):
            s.add("Klaviyo")
        return sorted(s)
    def consent_of(cid, ext_tags):
        et = (ext_tags or "").lower()
        return bool(crm_by.get(cid)) or any(k in et for k in ("newsletter", "subscriber", "accepts_marketing"))
    def seg_of(cid, vc):
        srcs = {s for t in tags_by.get(cid, []) for s in t["sources"]}
        has_chs = "CHS" in srcs
        vc = vc or 0
        if vc > 0 and not has_chs:
            return "new"
        if vc > 0 and has_chs:
            return "legacy_active"
        if vc == 0 and has_chs:
            return "legacy_dormant"
        return "registered"
    return {"total": total, "rows": [{
        "customer_id": r["cid"], "owner": f'{r["first_name"]} {r["last_name"]}'.strip(),
        "email": r["email"], "phone": r["phone"],
        "last_visit": r["last_visit"], "visit_count": r["visit_count"],
        "ltv": r["ltv"], "order_count": r["order_count"], "ext_tags": (r["ext_tags"] or ""),
        "segment": seg_of(r["cid"], r["visit_count"]),
        "care_mains": care_mains(r["cid"]), "recommend_count": reco_count(r["cid"]),
        "top_reco": top_reco(r["cid"]),
        "sources": sources_of(r["cid"], r["ltv"]), "crm_lists": crm_by.get(r["cid"], []),
        "consent": consent_of(r["cid"], r["ext_tags"]), "cs_contact": cs_by.get(r["cid"]),
        "unfulfilled": ({"count": r["shop_unfulfilled"], "oldest": r["shop_unfulfilled_oldest"]} if r["shop_unfulfilled"] else None),
        "first_purchase": r["first_purchase"], "last_purchase": r["last_purchase"], "bought_rx": bool(r["bought_rx"]),
        "clinic_ltv": r["clinic_ltv"], "shopify_ltv": r["shopify_ltv"],
        "last_clinic": r["last_clinic"], "last_shopify": r["last_shopify"],
        "reach": {"email": consent_of(r["cid"], r["ext_tags"]),
                  "whatsapp": r["cid"] in wa_optin,
                  "meta": bool((r["email"] or "").strip() or (r["phone"] or "").strip())},
        "crm": {"last_email": r["last_engagement"],
                "last_whatsapp": (cs_by.get(r["cid"]) or {}).get("last_contact"),
                "lists": crm_by.get(r["cid"], []), "whatsapp_optin": r["cid"] in wa_optin,
                "flows": flows_by.get(r["cid"], []), "discounts": disc_by.get(r["cid"], []),
                "first_landing": r["first_landing"], "first_landing_path": r["first_landing_path"],
                "first_referral": r["first_referral"], "first_utm_campaign": r["first_utm_campaign"],
                "first_utm_source": r["first_utm_source"], "first_utm_medium": r["first_utm_medium"]},
        "recent_purchases": purch_by.get(r["cid"], []),
        "recent_clinic": purch_clinic.get(r["cid"], []), "recent_online": purch_online.get(r["cid"], []),
        "purchase_cats": [c for c, _ in sorted(cat_by.get(r["cid"], {}).items(), key=lambda x: -x[1])],
        "pets": pets_by.get(r["cid"], []), "pet_count": len(pets_by.get(r["cid"], [])),
        "care": [t for t in tags_by.get(r["cid"], []) if t["kind"] == "care"],
        "events": [t for t in tags_by.get(r["cid"], []) if t["kind"] == "event"],
        "engagement": [t for t in tags_by.get(r["cid"], []) if t["kind"] == "engagement"],
    } for r in crows]}


@router.get("/{pet_id}/history")
def pet_history(pet_id: str, _=Depends(require_user)):
    """A pet's raw medical timeline (DaySmart appointment reasons + CHS diagnoses/complaints)."""
    con = _db(); cur = con.cursor()
    rows = cur.execute("""SELECT source, date, dx, note FROM clientssot_pet_history
        WHERE pet_id=? ORDER BY date DESC LIMIT 300""", (pet_id,)).fetchall()
    con.close()
    return {"history": [{"source": r["source"], "date": r["date"], "dx": r["dx"], "note": r["note"]}
                        for r in rows if (r["dx"] or r["note"])]}


@router.get("/{customer_id}/recommend")
def recommend(customer_id: str, _=Depends(require_user)):
    """Recommendation engine v1: products from the Rosetta catalog whose care-type matches this
    customer's care profile (pet-level + owner-level). Hero SKUs first. (Gap-vs-purchased exclusion
    comes when full Shopify order history lands.)"""
    con = _db(); cur = con.cursor()
    mains = set()
    for r in cur.execute("""SELECT DISTINCT t.main FROM clientssot_pets p JOIN clientssot_pet_caretags t
            ON t.pet_id=p.id WHERE p.customer_id=? AND t.kind='care'""", (customer_id,)):
        mains.add(r["main"])
    for r in cur.execute("SELECT DISTINCT main FROM clientssot_customer_caretags WHERE customer_id=? AND kind='care'", (customer_id,)):
        mains.add(r["main"])
    if not mains:
        con.close(); return {"mains": [], "recommend": []}
    qm = ",".join("?" * len(mains))
    rows = cur.execute(f"""SELECT name, MAX(brand) brand, MAX(category) category, MAX(hero_sku) hero_sku, main, sub
        FROM clientssot_product_caretags WHERE main IN ({qm})
        GROUP BY name, main, sub ORDER BY hero_sku DESC, main, brand LIMIT 60""", list(mains)).fetchall()
    con.close()
    return {"mains": sorted(mains), "recommend": [
        {"name": r["name"], "brand": r["brand"], "category": r["category"],
         "main": r["main"], "sub": r["sub"], "hero": bool(r["hero_sku"])} for r in rows]}


@router.get("/funnel")
def funnel(main: str = Query(None), search: str = Query(None), source: str = Query(None),
           segment: str = Query(None), channels: List[str] = Query(None), include_leads: bool = Query(False),
           ops: str = Query(None), pbefore: str = Query(None), pafter: str = Query(None),
           preacq: str = Query(None), rx: bool = Query(False), bought_cat: str = Query(None),
           bought_product: str = Query(None),
           cust: List[str] = Query(None), dcat: List[str] = Query(None), dprod: List[str] = Query(None),
           consents: List[str] = Query(None), opsl: List[str] = Query(None), dcoll: List[str] = Query(None), crm: List[str] = Query(None), xprod: List[str] = Query(None), xcat: List[str] = Query(None), pfrom: str = Query(None), pto: str = Query(None), flow: List[str] = Query(None), discount: List[str] = Query(None), dfam: List[str] = Query(None), landingsrc: List[str] = Query(None), utmcamp: List[str] = Query(None), landing: List[str] = Query(None), referral: List[str] = Query(None), _=Depends(require_user)):
    """Campaign funnel for the current cohort: how many are reachable (consented/Klaviyo), bought online,
    and returned to clinic. Lets you track 'target Dr Hugh's skin cohort -> sign-up -> buy -> consult'
    with live numbers, all from the filters."""
    con = _db(); cur = con.cursor()
    wsql, params = build_where(main, search, source, segment, channels, include_leads, ops,
                              pbefore, pafter, preacq, rx, bought_cat, bought_product,
                              cust, dcat, dprod, consents, opsl, dcoll, crm, xprod, xcat, pfrom, pto, flow, discount, dfam, landingsrc, utmcamp, landing, referral)
    def c(extra=None):
        sql = f"SELECT COUNT(*) FROM clientssot_customers c {wsql}"
        if extra:
            sql += (" AND " if wsql else " WHERE ") + extra
        return cur.execute(sql, params).fetchone()[0]
    out = {
        "cohort": c(),
        # channel reachability — each is an extractable action list
        "email": c(EMAIL_CONSENT),
        "whatsapp": c(WHATSAPP_REACH),
        "meta": c(META_REACH),
        "meta_only": c(META_ONLY),
        # behaviour
        "bought_online": c("c.ltv IS NOT NULL"),
        "returned_clinic": c("c.visit_count > 0"),
    }
    con.close()
    return out

@router.get("/export")
def export_cohort(main: str = Query(None), search: str = Query(None), source: str = Query(None),
                  segment: str = Query(None), channels: List[str] = Query(None), include_leads: bool = Query(False),
                  ops: str = Query(None), pbefore: str = Query(None), pafter: str = Query(None),
                  preacq: str = Query(None), rx: bool = Query(False), bought_cat: str = Query(None),
                  bought_product: str = Query(None), channel: str = Query(None),
                  cust: List[str] = Query(None), dcat: List[str] = Query(None), dprod: List[str] = Query(None),
                  consents: List[str] = Query(None), opsl: List[str] = Query(None), dcoll: List[str] = Query(None), crm: List[str] = Query(None), xprod: List[str] = Query(None), xcat: List[str] = Query(None), pfrom: str = Query(None), pto: str = Query(None), flow: List[str] = Query(None), discount: List[str] = Query(None), dfam: List[str] = Query(None), landingsrc: List[str] = Query(None), utmcamp: List[str] = Query(None), landing: List[str] = Query(None), referral: List[str] = Query(None), _=Depends(require_user)):
    """Export the current cohort as CSV (email/phone/name + consent + recommended product) — ready to
    upload as a Meta Custom Audience or import into a Klaviyo list. Includes a consent column; filter to
    consented before sending."""
    con = _db(); cur = con.cursor()
    wsql, params = build_where(main, search, source, segment, channels, include_leads, ops,
                              pbefore, pafter, preacq, rx, bought_cat, bought_product,
                              cust, dcat, dprod, consents, opsl, dcoll, crm, xprod, xcat, pfrom, pto, flow, discount, dfam, landingsrc, utmcamp, landing, referral)
    # channel gate: email = Klaviyo list, whatsapp = ChatArchitect blast, meta = Meta/Google custom audience
    gate = {"email": EMAIL_CONSENT, "whatsapp": WHATSAPP_REACH, "meta": META_REACH}.get(channel or "")
    if gate:
        wsql = (wsql + " AND " + gate) if wsql else "WHERE " + gate
    tpm = top_products_by_main(cur)
    pitch = tpm.get(main, "") if main else ""
    rows = cur.execute(f"""
        SELECT c.first_name, c.last_name, c.email, c.phone, c.clinic_ltv, c.shopify_ltv,
            c.last_clinic, c.last_shopify, ({CONSENT}) AS email_consent, ({WHATSAPP_REACH}) AS wa,
            (SELECT GROUP_CONCAT(DISTINCT t.main) FROM clientssot_pets p JOIN clientssot_pet_caretags t
                ON t.pet_id=p.id WHERE p.customer_id=c.id AND t.kind='care') AS pet_mains,
            (SELECT GROUP_CONCAT(DISTINCT ct.main) FROM clientssot_customer_caretags ct
                WHERE ct.customer_id=c.id AND ct.kind='care') AS cust_mains
        FROM clientssot_customers c {wsql}
        ORDER BY (c.shopify_ltv IS NULL), c.shopify_ltv DESC""", params).fetchall()
    con.close()
    def intl(p):  # international phone for Meta/Google match (assume HK if 8 digits)
        d = re.sub(r"\D", "", p or "")
        return ("+" + d) if len(d) > 8 else ("+852" + d if len(d) == 8 else "")
    buf = io.StringIO(); w = csv.writer(buf)
    # email/phone/first/last/country first = Meta Custom Audience + Google Customer Match match keys
    w.writerow(["email", "phone", "first_name", "last_name", "country", "clinic_ltv", "online_ltv",
                "last_clinic", "last_online", "email_consent", "whatsapp_reachable", "recommend_product", "care_needs"])
    for r in rows:
        mains = (set((r["pet_mains"] or "").split(",")) | set((r["cust_mains"] or "").split(","))) - {""}
        w.writerow([r["email"] or "", intl(r["phone"]), r["first_name"], r["last_name"], "HK",
                    r["clinic_ltv"] or "", r["shopify_ltv"] or "", r["last_clinic"] or "", r["last_shopify"] or "",
                    "yes" if r["email_consent"] else "no", "yes" if r["wa"] else "no",
                    pitch, " | ".join(sorted(mains))])
    fname = f"cohort_{(channel or main or segment or 'all')}.csv".replace(" ", "_").replace("&", "and")
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{fname}"'})

@router.get("/initiatives")
def initiatives(_=Depends(require_user)):
    """MARKETING INITIATIVES: turn demand into ranked, ready-to-run campaigns. For each demand bucket,
    quantify the clinic->online conversion GAP (people who buy it at the clinic but not online = the prize
    Angelina's Phase-1 report was blind to) + who's reachable + the products to feature. This is the context
    that says 'don't give up on Dr Hugh's list'."""
    con = _db(); cur = con.cursor()
    cats = ["Preventative", "Prescription Diet", "Medicine", "Supplement", "Pet Hygiene", "Food"]
    bought = "(SELECT DISTINCT customer_id FROM clientssot_purchases WHERE category=? AND customer_id IS NOT NULL)"
    out = []
    for cat in cats:
        def one(sql, *p):
            return cur.execute(sql, p).fetchone()[0] or 0
        total = one("SELECT COUNT(DISTINCT customer_id) FROM clientssot_purchases WHERE category=? AND customer_id IS NOT NULL", cat)
        if not total:
            continue
        clinic = one("SELECT COUNT(DISTINCT customer_id) FROM clientssot_purchases WHERE category=? AND source IN ('Ohana','Dr Hugh') AND customer_id IS NOT NULL", cat)
        online = one("SELECT COUNT(DISTINCT customer_id) FROM clientssot_purchases WHERE category=? AND source='Shopify' AND customer_id IS NOT NULL", cat)
        gap = one("""SELECT COUNT(*) FROM (SELECT DISTINCT customer_id FROM clientssot_purchases
            WHERE category=? AND source IN ('Ohana','Dr Hugh') AND customer_id IS NOT NULL
            AND customer_id NOT IN (SELECT customer_id FROM clientssot_purchases WHERE category=? AND source='Shopify' AND customer_id IS NOT NULL))""", cat, cat)
        email = one(f"SELECT COUNT(*) FROM clientssot_customers c WHERE c.id IN {bought} AND {EMAIL_CONSENT}", cat)
        wa = one(f"SELECT COUNT(*) FROM clientssot_customers c WHERE c.id IN {bought} AND {WHATSAPP_REACH}", cat)
        meta = one(f"SELECT COUNT(*) FROM clientssot_customers c WHERE c.id IN {bought} AND {META_REACH}", cat)
        drhugh = one(f"SELECT COUNT(*) FROM clientssot_customers c WHERE c.id IN {bought} AND {CHS_PRED}", cat)
        clinic_ltv = one("SELECT ROUND(SUM(COALESCE(price,0)*COALESCE(qty,1))) FROM clientssot_purchases WHERE category=? AND source='Ohana'", cat)
        autoship = one("SELECT COUNT(DISTINCT customer_id) FROM clientssot_purchases WHERE category=? AND autoship=1", cat)
        tops = [{"family": r[0], "n": r[1]} for r in cur.execute(
            """SELECT COALESCE(NULLIF(family,''), product), COUNT(DISTINCT customer_id) n FROM clientssot_purchases
               WHERE category=? AND source IN ('Ohana','Dr Hugh') AND customer_id IS NOT NULL GROUP BY 1 ORDER BY n DESC LIMIT 4""", (cat,))]
        out.append({"category": cat, "total_clients": total, "clinic_clients": clinic, "online_clients": online,
                    "gap": gap, "reach": {"email": email, "whatsapp": wa, "meta": meta}, "dr_hugh_clients": drhugh,
                    "clinic_ltv": clinic_ltv, "autoship_clients": autoship, "top_products": tops})
    out.sort(key=lambda x: -x["gap"])
    drhugh_total = cur.execute(f"SELECT COUNT(*) FROM clientssot_customers c WHERE {CHS_PRED}").fetchone()[0]
    drhugh_online = cur.execute(f"SELECT COUNT(*) FROM clientssot_customers c WHERE {CHS_PRED} AND c.ltv IS NOT NULL").fetchone()[0]
    con.close()
    return {"buckets": out, "dr_hugh": {"total": drhugh_total, "online": drhugh_online, "never_online": drhugh_total - drhugh_online}}

@router.get("/performance")
def performance(_=Depends(require_user)):
    """CRM PERFORMANCE: measure which LISTS acquire best and which FLOWS convert best — the funnel per
    list/flow: members/reached -> reachable (consent) -> purchasers -> conversion% -> revenue -> claims.
    Directional (members who also purchased, not strict last-click attribution) but it ranks them."""
    con = _db(); cur = con.cursor()
    VAL = "(COALESCE(c.ltv,0)+COALESCE(c.clinic_ltv,0))"
    def stats(idsql, params):
        r = cur.execute(f"""SELECT COUNT(*) m,
            SUM(CASE WHEN {VAL}>0 THEN 1 ELSE 0 END) purch,
            ROUND(SUM({VAL})) rev,
            SUM(CASE WHEN c.id IN (SELECT customer_id FROM clientssot_crm_discounts) THEN 1 ELSE 0 END) claimed,
            SUM(CASE WHEN ({EMAIL_CONSENT}) OR ({WHATSAPP_REACH}) THEN 1 ELSE 0 END) reach
            FROM clientssot_customers c WHERE c.id IN ({idsql})""", params).fetchone()
        m = r[0] or 0
        return {"members": m, "purchasers": r[1] or 0, "revenue": r[2] or 0, "claimed": r[3] or 0,
                "reachable": r[4] or 0, "conv": round(100 * (r[1] or 0) / m, 1) if m else 0,
                "rev_per": round((r[2] or 0) / m) if m else 0}
    lists = []
    list_names = [r[0] for r in cur.execute("SELECT DISTINCT list_name FROM clientssot_customer_crm WHERE source='Klaviyo' AND list_name NOT IN ('','None')").fetchall()]
    for name in list_names:
        s = stats("SELECT customer_id FROM clientssot_customer_crm WHERE source='Klaviyo' AND list_name=?", (name,))
        s["name"] = name; lists.append(s)
    flows = []
    flow_names = [r[0] for r in cur.execute("SELECT DISTINCT flow_name FROM clientssot_crm_flows WHERE flow_name NOT IN ('','None')").fetchall()]
    for name in flow_names:
        s = stats("SELECT customer_id FROM clientssot_crm_flows WHERE flow_name=?", (name,))
        snd = cur.execute("SELECT ROUND(AVG(sends),1), SUM(sends) FROM clientssot_crm_flows WHERE flow_name=?", (name,)).fetchone()
        s["name"] = name; s["avg_sends"] = snd[0] or 0; s["total_sends"] = snd[1] or 0; flows.append(s)
    lists.sort(key=lambda x: -x["revenue"]); flows.sort(key=lambda x: -x["revenue"])
    con.close()
    return {"lists": lists, "flows": flows}

@router.get("/demand")
def demand(segment: str = Query(None), channels: List[str] = Query(None), main: str = Query(None),
           search: str = Query(None), source: str = Query(None), include_leads: bool = Query(False),
           ops: str = Query(None), pbefore: str = Query(None), pafter: str = Query(None),
           preacq: str = Query(None), rx: bool = Query(False), bought_cat: str = Query(None),
           bought_product: str = Query(None),
           cust: List[str] = Query(None), dcat: List[str] = Query(None), dprod: List[str] = Query(None),
           consents: List[str] = Query(None), opsl: List[str] = Query(None), dcoll: List[str] = Query(None), crm: List[str] = Query(None), xprod: List[str] = Query(None), xcat: List[str] = Query(None), pfrom: str = Query(None), pto: str = Query(None), flow: List[str] = Query(None), discount: List[str] = Query(None), dfam: List[str] = Query(None), landingsrc: List[str] = Query(None), utmcamp: List[str] = Query(None), landing: List[str] = Query(None), referral: List[str] = Query(None), _=Depends(require_user)):
    """DEMAND BREAKDOWN (marketing view): for the current cohort (same filters as the list), rank the
    products people have ACTUALLY purchased — by distinct customers — with the Rosetta category. Purchase
    data only (not recommendations). Profit margin per product is a TODO pending the IMS/OCR cost data."""
    con = _db(); cur = con.cursor()
    wsql, params = build_where(main, search, source, segment, channels, include_leads, ops,
                              pbefore, pafter, preacq, rx, bought_cat, bought_product,
                              cust, dcat, dprod, consents, opsl, dcoll, crm, xprod, xcat, pfrom, pto, flow, discount, dfam, landingsrc, utmcamp, landing, referral)
    cohort = f"SELECT id FROM clientssot_customers c {wsql}"
    cohort_size = cur.execute(f"SELECT COUNT(*) FROM clientssot_customers c {wsql}", params).fetchone()[0]
    by_cat, top = [], []
    try:
        by_cat = [{"category": r["category"], "n": r["n"]} for r in cur.execute(
            f"""SELECT category, COUNT(DISTINCT customer_id) n FROM clientssot_purchases
                WHERE customer_id IN ({cohort}) AND category IS NOT NULL AND category!='Other'
                GROUP BY category ORDER BY n DESC""", params)]
        # split EVERY metric by channel (clinic = Ohana+Dr Hugh, online = Shopify). Fixes the bogus
        # "sold online" + gives the clinic/online breakdown. Note: only Ohana carries price -> online $ is N/A
        # until Shopify line-item prices are ingested (price currently 0 for Shopify/Dr Hugh rows).
        top = [{"product": r["product"], "category": r["category"], "n": r["n"], "sku": r["sku"] or "",
                "clinic_clients": r["cc"], "online_clients": r["oc"],
                "clinic_units": int(r["cu"] or 0), "online_units": int(r["ou"] or 0),
                "clinic_ltv": round(r["cltv"] or 0), "online_ltv": round(r["oltv"] or 0),
                "autoship_clients": r["ac"] or 0, "names": r["names"]} for r in cur.execute(
            f"""SELECT COALESCE(NULLIF(family,''), product) AS product, MAX(category) category,
                   COUNT(DISTINCT customer_id) n, MAX(NULLIF(sku,'')) sku, COUNT(DISTINCT product) names,
                   COUNT(DISTINCT CASE WHEN source IN ('Ohana','Dr Hugh') THEN customer_id END) cc,
                   COUNT(DISTINCT CASE WHEN source='Shopify' THEN customer_id END) oc,
                   SUM(CASE WHEN source IN ('Ohana','Dr Hugh') THEN qty ELSE 0 END) cu,
                   SUM(CASE WHEN source='Shopify' THEN qty ELSE 0 END) ou,
                   SUM(CASE WHEN source='Ohana' THEN COALESCE(price,0)*COALESCE(qty,1) ELSE 0 END) cltv,
                   SUM(CASE WHEN source='Shopify' THEN COALESCE(price,0)*COALESCE(qty,1) ELSE 0 END) oltv,
                   COUNT(DISTINCT CASE WHEN autoship=1 THEN customer_id END) ac
                FROM clientssot_purchases WHERE customer_id IN ({cohort}) AND product IS NOT NULL AND product!=''
                GROUP BY COALESCE(NULLIF(family,''), product) ORDER BY n DESC LIMIT 60""", params)]
    except sqlite3.OperationalError:
        pass
    con.close()
    return {"cohort_size": cohort_size, "by_cat": by_cat, "top_products": top}

@router.get("/campaign")
def campaign(segment: str = Query(None), channels: List[str] = Query(None), main: str = Query(None),
             search: str = Query(None), source: str = Query(None), include_leads: bool = Query(False),
             ops: str = Query(None), pbefore: str = Query(None), pafter: str = Query(None),
             preacq: str = Query(None), rx: bool = Query(False), bought_cat: str = Query(None),
             bought_product: str = Query(None),
             cust: List[str] = Query(None), dcat: List[str] = Query(None), dprod: List[str] = Query(None),
             consents: List[str] = Query(None), opsl: List[str] = Query(None), dcoll: List[str] = Query(None), crm: List[str] = Query(None), xprod: List[str] = Query(None), xcat: List[str] = Query(None), pfrom: str = Query(None), pto: str = Query(None), flow: List[str] = Query(None), discount: List[str] = Query(None), dfam: List[str] = Query(None), landingsrc: List[str] = Query(None), utmcamp: List[str] = Query(None), landing: List[str] = Query(None), referral: List[str] = Query(None), _=Depends(require_user)):
    """Campaign Planner: for a cohort (same filters as the list), roll the long product tail UP into
    the few care THEMES that capture the most customers — ranked by GAP (need minus already-bought-online).
    Each theme carries top sub-categories + products to feature. Turns '960 products' into ~5-10 messages."""
    con = _db(); cur = con.cursor()
    wsql, params = build_where(main, search, source, segment, channels, include_leads, ops,
                              pbefore, pafter, preacq, rx, bought_cat, bought_product,
                              cust, dcat, dprod, consents, opsl, dcoll, crm, xprod, xcat, pfrom, pto, flow, discount, dfam, landingsrc, utmcamp, landing, referral)
    cohort = f"SELECT c.id cid FROM clientssot_customers c {wsql}"
    cohort_n = cur.execute(f"SELECT COUNT(*) FROM clientssot_customers c {wsql}", params).fetchone()[0]
    need_sql = f"""WITH cohort AS ({cohort}) SELECT main, COUNT(DISTINCT cid) n FROM (
        SELECT co.cid cid, t.main main FROM cohort co JOIN clientssot_pets p ON p.customer_id=co.cid
             JOIN clientssot_pet_caretags t ON t.pet_id=p.id WHERE t.kind='care'
        UNION SELECT co.cid, ct.main FROM cohort co JOIN clientssot_customer_caretags ct ON ct.customer_id=co.cid WHERE ct.kind='care'
        ) GROUP BY main"""
    need = {r["main"]: r["n"] for r in cur.execute(need_sql, params)}
    conv_sql = f"""WITH cohort AS ({cohort}) SELECT ct.main, COUNT(DISTINCT co.cid) n FROM cohort co
        JOIN clientssot_customer_caretags ct ON ct.customer_id=co.cid
        WHERE ct.kind='care' AND ct.source='Shopify' GROUP BY ct.main"""
    conv = {r["main"]: r["n"] for r in cur.execute(conv_sql, params)}
    subs_sql = f"""WITH cohort AS ({cohort}) SELECT main, sub, COUNT(DISTINCT cid) n FROM (
        SELECT co.cid cid, t.main, t.sub FROM cohort co JOIN clientssot_pets p ON p.customer_id=co.cid
             JOIN clientssot_pet_caretags t ON t.pet_id=p.id WHERE t.kind='care'
        UNION SELECT co.cid, ct.main, ct.sub FROM cohort co JOIN clientssot_customer_caretags ct ON ct.customer_id=co.cid WHERE ct.kind='care'
        ) GROUP BY main, sub"""
    subs_by = {}
    for r in cur.execute(subs_sql, params):
        subs_by.setdefault(r["main"], []).append({"sub": r["sub"], "n": r["n"]})
    # what to FEATURE per theme: (1) what Dr Hugh's clients were prescribed (CHS goldmine),
    # (2) what actually sells online now (Shopify line-item sales).
    clinic_by, online_by = {}, {}
    try:
        for r in cur.execute("SELECT main, name, mentions FROM clientssot_chs_products ORDER BY mentions DESC"):
            lst = clinic_by.setdefault(r["main"], [])
            if len(lst) < 3:
                lst.append({"name": r["name"], "n": r["mentions"]})
    except sqlite3.OperationalError:
        pass
    try:
        for r in cur.execute("SELECT main, name, units FROM clientssot_product_sales WHERE main IS NOT NULL ORDER BY units DESC"):
            lst = online_by.setdefault(r["main"], [])
            if len(lst) < 3:
                lst.append({"name": r["name"], "n": r["units"]})
    except sqlite3.OperationalError:
        pass
    con.close()
    def winners(cps, ops):
        # products that are BOTH prescribed at the clinic AND selling online = proven offers to feature
        out = []
        for cp in cps:
            kw = cp["name"].lower().replace("/", " ").split()[0] if cp["name"] else ""
            if len(kw) < 3:
                continue
            match = next((op for op in ops if kw in op["name"].lower()), None)
            if match:
                out.append({"name": cp["name"], "clinic_n": cp["n"], "online_n": match["n"]})
        return out
    themes = []
    for m, nn in need.items():
        c = conv.get(m, 0)
        cps, ops = clinic_by.get(m, []), online_by.get(m, [])
        themes.append({"main": m, "need": nn, "converted_online": c, "gap": nn - c,
                       "pct": round(100 * nn / cohort_n) if cohort_n else 0,
                       "subs": sorted(subs_by.get(m, []), key=lambda x: -x["n"])[:5],
                       "winners": winners(cps, ops),
                       "clinic_products": cps, "online_products": ops})
    themes.sort(key=lambda x: -x["gap"])
    return {"cohort_size": cohort_n, "themes": themes}

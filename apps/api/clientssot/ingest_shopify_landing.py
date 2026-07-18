# -*- coding: utf-8 -*-
"""First-landing attribution (top of the funnel, upstream of the list): each customer's FIRST landing page
+ traffic source + UTM params, from Shopify's customerJourneySummary.firstVisit. No GA4 needed.

The SPECIFIC landing PAGE is the signal we want (different lists / partners push to different pages),
not just the coarse source. Stores per customer:
  first_landing       full landing URL (for display)
  first_landing_path  clean path only, e.g. /collections/riplees-ranch  (the groupable/filterable dimension)
  first_source        coarse channel (Google/Facebook/Direct…)
  first_utm_source/medium/campaign/content  partner & campaign trackers (UTM)"""
import sqlite3, io, sys, re, time
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
from clientssot.shopify_client import gql
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
DB = resolve_db()

def norm_phone(s):
    d = re.sub(r"\D", "", s or ""); return d[-8:] if len(d) >= 8 else ""

def clean_path(url):
    """Path portion only, no domain/query/trailing slash. Bare home -> '/ (home page)'."""
    if not url:
        return ""
    try:
        p = urlsplit(url).path or "/"
    except Exception:
        p = url
    p = p.rstrip("/")
    return "/ (home page)" if p in ("", "/") else p

def norm_source(s):
    t = (s or "").strip().lower()
    if not t:
        return ""
    if "google" in t and "gmail" not in t and "mail" not in t:
        return "Google"
    if "facebook" in t or t == "fb" or "fb." in t:
        return "Facebook"
    if "instagram" in t or t == "ig":
        return "Instagram"
    if "gmail" in t or "mail.google" in t or t.startswith("android-app"):
        return "Email"
    if "klaviyo" in t or t == "email":
        return "Email"
    if t == "direct" or t == "(direct)":
        return "Direct"
    if "bing" in t:
        return "Bing"
    return s.strip()[:24]

# A genuine external REFERRAL / backlink (helpful) — excludes search engines, social, and our own/app domains.
_SEARCH = ("google", "bing", "yahoo", "duckduckgo", "ecosia", "perplexity", "baidu", "sogou", "qwant",
           "startpage", "yandex", "brave", "ask.com", "aol", "naver")
_INTERNAL = ("petproject", "shopify", "cashier", "wl.co", "paypal", "checkout", "accounts.", "myshopify", "klaviyo")
_SOCIAL = ("facebook", "instagram", "fb.", "t.co", "linkedin", "youtube", "tiktok", "pinterest", "ig.", "snapchat", "whatsapp", "lnstagram")

def referral_domain(s):
    """Clean external backlink domain (e.g. sassymamahk.com) or None for search/social/direct/internal."""
    t = (s or "").strip().lower()
    if not t or ("." not in t):          # bare tokens like 'direct'/'google'/'facebook' aren't backlinks
        return None
    try:
        host = urlsplit(t if "://" in t else "http://" + t).netloc or t
    except Exception:
        host = t
    host = host.split("/")[0].replace("www.", "").strip()
    if not host or "." not in host:
        return None
    if any(x in host for x in _SEARCH) or any(x in host for x in _INTERNAL) or any(x in host for x in _SOCIAL):
        return None
    return host[:48]

con = sqlite3.connect(DB); cur = con.cursor()
for col in ("first_landing TEXT", "first_landing_path TEXT", "first_source TEXT", "first_referral TEXT",
            "first_utm_source TEXT", "first_utm_medium TEXT", "first_utm_campaign TEXT", "first_utm_content TEXT"):
    try: cur.execute(f"ALTER TABLE clientssot_customers ADD COLUMN {col}")
    except sqlite3.OperationalError: pass

email2cust = {r[1].strip().lower(): r[0] for r in cur.execute("SELECT id,email FROM clientssot_customers WHERE email!='' AND email IS NOT NULL")}
phone2cust = {norm_phone(r[1]): r[0] for r in cur.execute("SELECT id,phone FROM clientssot_customers WHERE phone!='' AND phone IS NOT NULL") if norm_phone(r[1])}

# orders ASC so the FIRST order with a journey wins (= the customer's first landing)
Q = ('query($c:String){ orders(first:250, after:$c, query:"created_at:>=2019-01-01", sortKey:CREATED_AT){'
     ' edges{ node{ createdAt customerJourneySummary{ firstVisit{ landingPage source'
     '   utmParameters{ source medium campaign content } } } customer{ email phone } } }'
     ' pageInfo{ hasNextPage endCursor } } }')
after = None; n_ord = 0; first = {}   # cid -> dict (first seen = earliest order)
while True:
    j = gql(Q, {"c": after}); d = (j.get("data") or {}).get("orders")
    if not d:
        print("stop:", str(j)[:160]); break
    for e in d["edges"]:
        o = e["node"]; n_ord += 1
        fv = ((o.get("customerJourneySummary") or {}).get("firstVisit") or {})
        lp = fv.get("landingPage")
        if not lp:
            continue
        c = o.get("customer") or {}
        cid = email2cust.get((c.get("email") or "").strip().lower())
        if not cid and c.get("phone"):
            cid = phone2cust.get(norm_phone(c.get("phone")))
        if cid and cid not in first:
            u = fv.get("utmParameters") or {}
            raw_src = fv.get("source")
            first[cid] = {"landing": lp, "path": clean_path(lp), "source": norm_source(raw_src),
                          "ref": referral_domain(raw_src),
                          "us": (u.get("source") or "").strip(), "um": (u.get("medium") or "").strip(),
                          "uc": (u.get("campaign") or "").strip(), "uct": (u.get("content") or "").strip()}
    if n_ord % 4000 == 0:
        print(f"  ...{n_ord} orders, {len(first)} customers with a landing", flush=True)
    if d["pageInfo"]["hasNextPage"]:
        after = d["pageInfo"]["endCursor"]; time.sleep(0.15)
    else:
        break

cur.execute("""UPDATE clientssot_customers SET first_landing=NULL, first_landing_path=NULL, first_source=NULL, first_referral=NULL,
    first_utm_source=NULL, first_utm_medium=NULL, first_utm_campaign=NULL, first_utm_content=NULL""")
cur.executemany("""UPDATE clientssot_customers SET first_landing=?, first_landing_path=?, first_source=?, first_referral=?,
    first_utm_source=?, first_utm_medium=?, first_utm_campaign=?, first_utm_content=? WHERE id=?""",
    [(v["landing"], v["path"], v["source"], v["ref"], v["us"] or None, v["um"] or None, v["uc"] or None, v["uct"] or None, cid)
     for cid, v in first.items()])
con.commit()
print(f"orders scanned {n_ord} | customers with first-landing {len(first)}")
print("top landing pages:")
for r in cur.execute("SELECT first_landing_path, COUNT(*) n FROM clientssot_customers WHERE first_landing_path IS NOT NULL GROUP BY first_landing_path ORDER BY n DESC LIMIT 12"):
    print(f"  {r[1]:>5}  {r[0]}")
print("UTM campaigns:")
for r in cur.execute("SELECT first_utm_campaign, COUNT(*) n FROM clientssot_customers WHERE first_utm_campaign IS NOT NULL AND first_utm_campaign!='' GROUP BY first_utm_campaign ORDER BY n DESC LIMIT 12"):
    print(f"  {r[1]:>5}  {r[0]}")
print("referral backlinks:")
for r in cur.execute("SELECT first_referral, COUNT(*) n FROM clientssot_customers WHERE first_referral IS NOT NULL GROUP BY first_referral ORDER BY n DESC LIMIT 12"):
    print(f"  {r[1]:>5}  {r[0]}")
con.close()
print("SHOPIFY LANDING INGEST DONE")

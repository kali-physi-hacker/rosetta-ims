# -*- coding: utf-8 -*-
"""Client SSOT refresh pipeline — re-ingests ALL customer data sources in dependency order, then
rebuilds indexes. This is the "ingest new customer data" job: run it on a schedule (cron/Task Scheduler)
to pull the latest from DaySmart, Dr Hugh's export, Shopify, Klaviyo, Slack/WhatsApp + ChatArchitect.

  python -m clientssot.refresh_all                 # full rebuild
  python -m clientssot.refresh_all --skip-slow     # skip the rate-limited/flaky external pulls
  python -m clientssot.refresh_all --only ingest_shopify_purchases recompute_rollups

PRODUCTION MODEL (recommended): run this on a build box (or locally), then publish the result with
  python -m clientssot.pipeline dump  &&  python -m clientssot.pipeline load
so the live droplet DB is only ever swapped to a known-good snapshot — never left mid-rebuild while
teammates are querying it. (Step 1 below DROPS & rebuilds the base tables, so a direct prod run has a
visible empty window.) Needs every API key in .env (see .env.example).

Idempotent: each source re-pulls and replaces its own rows. Targets DATABASE_URL (see db_path.py)."""
import argparse, subprocess, sys, io, time, sqlite3
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from clientssot.db_path import resolve_db
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
BACKEND = Path(__file__).resolve().parents[1]

# (module, critical, slow, description) — order matters (later steps depend on earlier identity/purchases)
STEPS = [
    ("ingest_daysmart",          True,  False, "DaySmart appointments -> base customers/pets (DROPS & rebuilds)"),
    ("enrich_daysmart_clients",  True,  False, "DaySmart full client list -> email/phone match keys"),
    ("ingest_chs",               True,  False, "Dr Hugh (CHS) legacy customers/pets/history"),
    ("ingest_daysmart_invoices", True,  False, "Ohana clinic invoices -> purchases"),
    ("ingest_chs_purchases",     True,  False, "Dr Hugh dispensed products -> purchases (+ first/last dates)"),
    ("ingest_chs_products",      False, False, "Dr Hugh product goldmine per care-type"),
    ("ingest_shopify_customers", True,  False, "Shopify lifetime buyer base + LTV"),
    ("ingest_shopify_purchases", True,  False, "Shopify purchases + PSG audience + bought_rx"),
    ("ingest_shopify_caretags",  False, False, "Shopify care tags"),
    ("ingest_product_sales",     False, False, "Shopify product-sales-by-care-type (campaign planner)"),
    ("ingest_collections",       False, False, "Shopify collections -> Demand Record filter"),
    ("ingest_klaviyo",           False, False, "Klaviyo CRM list memberships"),
    ("ingest_klaviyo_profiles",  False, True,  "Klaviyo consent + engagement (RATE-LIMITED, slow)"),
    ("ingest_klaviyo_flows",     False, True,  "Klaviyo flow-sends per customer (slow)"),
    ("ingest_shopify_discounts", False, False, "Shopify discount-code redemptions -> who claimed which code"),
    ("ingest_shopify_landing",   False, False, "First-landing URL + source per customer (top of funnel)"),
    ("ingest_unfulfilled",       False, False, "Unfulfilled Shopify orders (Ops)"),
    ("ingest_slack",             False, True,  "Slack WhatsApp-mirror sync (token ~12h)"),
    ("ingest_cs_contacts",       False, False, "CS contacts from Slack mirror"),
    ("ingest_cs_sentiment",      False, False, "CS conversation sentiment"),
    ("ingest_chatarchitect_optin", False, False, "ChatArchitect WhatsApp opt-in list"),
    ("summarize_pets",           False, False, "Per-pet care summaries"),
    ("recompute_rollups",        True,  False, "Per-source LTV/last-date rollups + category fixes"),
    ("compute_families",         True,  False, "Product-family roll-up for Demand Breakdown (LAST)"),
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_pets_customer ON clientssot_pets(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_petct_pet ON clientssot_pet_caretags(pet_id)",
    "CREATE INDEX IF NOT EXISTS idx_custct_cust ON clientssot_customer_caretags(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_crm_cust ON clientssot_customer_crm(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_cust_ltv ON clientssot_customers(ltv)",
    "CREATE INDEX IF NOT EXISTS idx_pur_prod ON clientssot_purchases(product)",
    "CREATE INDEX IF NOT EXISTS idx_pur_cat ON clientssot_purchases(category)",
    "CREATE INDEX IF NOT EXISTS idx_pur_cust ON clientssot_purchases(customer_id)",
    "CREATE INDEX IF NOT EXISTS idx_coll_coll ON clientssot_collections(collection)",
    "CREATE INDEX IF NOT EXISTS idx_coll_prod ON clientssot_collections(product)",
]

def reindex():
    con = sqlite3.connect(resolve_db()); cur = con.cursor()
    for ddl in INDEXES:
        try:
            cur.execute(ddl)
        except sqlite3.OperationalError as e:
            print("  index skip:", e)
    con.commit(); con.close()
    print("  reindex done")

def run():
    ap = argparse.ArgumentParser(description="Client SSOT full refresh pipeline")
    ap.add_argument("--skip-slow", action="store_true", help="skip rate-limited/flaky external pulls")
    ap.add_argument("--only", nargs="*", help="run only these step modules (still reindexes)")
    a = ap.parse_args()
    print(f"REFRESH START -> {resolve_db()}\n")
    results = []
    for mod, critical, slow, desc in STEPS:
        if a.only and mod not in a.only:
            continue
        if a.skip_slow and slow:
            print(f"-- SKIP (slow): {mod}"); results.append((mod, "skipped")); continue
        print(f"== {mod} :: {desc}")
        t0 = time.time()
        r = subprocess.run([sys.executable, "-m", f"clientssot.{mod}"], cwd=str(BACKEND))
        dt = round(time.time() - t0)
        if r.returncode == 0:
            print(f"   OK ({dt}s)\n"); results.append((mod, "ok"))
        else:
            print(f"   FAILED rc={r.returncode} ({dt}s)\n"); results.append((mod, "FAILED"))
            if critical:
                print(f"ABORT: critical step {mod} failed."); _summary(results); sys.exit(1)
    print("== reindex"); reindex()
    _summary(results)

def _summary(results):
    print("\nREFRESH SUMMARY:")
    for mod, st in results:
        print(f"  {st:>8}  {mod}")

if __name__ == "__main__":
    run()

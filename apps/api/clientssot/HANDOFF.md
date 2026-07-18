# Client SSOT — Production Hand-off (for Desmond)

The Client SSOT ("Clientbase") is an **additive** feature: it only ever reads/writes tables prefixed
`clientssot_*`. It does not touch inventory/users/suppliers/etc. API is mounted at `/clients`, UI at
`/clients` on the frontend.

## ⚡ 2026-06-23 update — re-deploy after a full day of changes
Pull `feat/client-ssot` and **re-run the load** — that's the whole deploy. The snapshot now has **17 tables**
(added since first deploy: `clientssot_crm_flows`, `clientssot_crm_discounts`; new columns on `clientssot_purchases`:
`sku`, `price`, `autoship`, `family`; on `cs_contacts`: `sentiment_quote`). **`pipeline.py load` is schema-agnostic
— it recreates every table from the snapshot's own schema, so new tables/columns load automatically. No migration,
no loader changes needed.** Also new: `main.py` now mounts the clientssot router (was missing); new ingests
`ingest_klaviyo_flows`, `ingest_shopify_discounts`, `compute_families` (all in `refresh_all`).

## Re: "for any new data pull, dump it + ensure it loads on prod" (Desmond's ask #1)
Already handled by design — the cycle for ANY future source pull:
```
python -m clientssot.refresh_all      # (or a single ingest_*.py) — pulls new data into the local/build DB
python -m clientssot.pipeline dump    # -> clientssot/clientssot_data.db.gz  (captures ALL clientssot_* tables)
python -m clientssot.pipeline load    # on prod -> /data/ims.db  (recreates + loads, schema-agnostic)
```
No per-source loader is ever needed — `dump` reads the live schema from sqlite_master and `load` rebuilds it.

## What ships in this push
- `backend/clientssot/` — API router + ingest scripts + pipeline + families/discounts.
- `backend/clientssot/clientssot_data.db.gz` — **the data snapshot** (~37 MB, 17 tables incl. 43,281
  customers / 147k purchases / collections / Klaviyo flows / Shopify discount redemptions / WhatsApp opt-ins).
- `.env.example` — Client SSOT keys block (DAYSMART_*, SHOPIFY_*, KLAVIYO_API_KEY, CHATARCHITECT_*, SLACK_TOKEN)
  needed only to RE-PULL data (not to serve it).
- `backend/clientssot/db_path.py` — all clientssot code resolves the DB from `DATABASE_URL` (same as
  the main app), so on the droplet it uses the volume DB `/data/ims.db`. **No code change needed per-env.**

## Deploy steps
1. Deploy the branch as usual (Docker build + run on the droplet). The Clientbase router auto-mounts.
2. **Load the customer data into the production DB** (one command — only touches `clientssot_*` tables):
   ```
   # from the backend/ dir inside the container/droplet, with DATABASE_URL set (sqlite:////data/ims.db)
   python -m clientssot.pipeline load            # reads clientssot/clientssot_data.db.gz -> /data/ims.db
   ```
   It DROPs & recreates each `clientssot_*` table from the snapshot (schema + indexes), inserts the data,
   and prints row counts. Re-runnable. Inventory/users tables are never touched (asserted in code).
3. Verify: hit `GET /clients/summary` (auth required) — should report ~19k customers.

## Refreshing customer data later (the ingest pipeline)
`backend/clientssot/refresh_all.py` re-pulls every source (DaySmart, Dr Hugh export, Shopify, Klaviyo,
Slack/WhatsApp, ChatArchitect) in dependency order, then reindexes. Needs the API keys in `.env`
(see `.env.example` → "Client SSOT data pipeline" block).

**Recommended model — build then publish (no live downtime):**
```
python -m clientssot.refresh_all          # rebuild on a build box / locally (step 1 drops base tables)
python -m clientssot.pipeline dump        # -> clientssot_data.db.gz
python -m clientssot.pipeline load --db /data/ims.db   # swap snapshot into prod (fast)
```
Schedule that on cron (e.g. nightly) for "monitors/ingests new customer data". Running `refresh_all`
*directly* on prod also works but has a brief empty window during the rebuild, so prefer build-then-load.
`--skip-slow` skips the rate-limited Klaviyo-consent + Slack pulls.

## Frontend
The Vercel frontend already points at the droplet API (`NEXT_PUBLIC_API_URL`). The Clientbase page is at
`/clients`. Login gates all data endpoints (JWT). Set a strong shared password before sharing with the
marketing team — this is real customer PII.

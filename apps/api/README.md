# Rosetta IMS — Backend

FastAPI + SQLAlchemy + SQLite (dev) / Postgres (prod-ready) backend for the Rosetta Inventory Management System.

Lives at `backend/` in the repo. Frontend lives at `frontend/` and talks to this API over HTTP — see [`../frontend/src/lib/api.ts`](../frontend/src/lib/api.ts).

---

## Stack

| Layer | Choice | Notes |
|---|---|---|
| Web framework | [FastAPI](https://fastapi.tiangolo.com/) | Automatic OpenAPI/Swagger at `/docs` |
| ORM | [SQLAlchemy 2.x](https://www.sqlalchemy.org/) | Declarative models in `models.py` |
| DB (dev) | SQLite | File at `backend/ims.db` (gitignored) |
| DB (prod) | Postgres-ready | Set `DATABASE_URL` env var |
| Auth | JWT (HS256) + legacy API key gate | `auth.py` router, middleware in `main.py` |
| OCR / extraction | Claude Haiku via Anthropic SDK | `services/extraction_service.py` |
| Deployment | [Fly.io](https://fly.io) | `fly.toml` + `Dockerfile` |

---

## Local development

### Prerequisites
- Python 3.13+ (project tested on 3.14)
- Recommended: use the bundled venv at `backend/venv/`

### Setup

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1   # Windows PowerShell
# OR: source venv/bin/activate (macOS/Linux)
pip install -r requirements.txt
```

### Run

```powershell
.\venv\Scripts\python -m uvicorn main:app --reload --port 8001
```

API is now at `http://localhost:8001`. Swagger UI at `http://localhost:8001/docs`.

### Run both backend + frontend together

From the **project root** (not `backend/`):

```powershell
.\start.ps1
```

Starts the backend on `:8001` and the frontend on `:3001`.

---

## Environment variables

| Variable | Required? | Default | Purpose |
|---|---|---|---|
| `DATABASE_URL` | optional | `sqlite:///./ims.db` | SQLAlchemy connection string. Set to Postgres URL in prod. |
| `ALLOWED_ORIGINS` | optional | `http://localhost:3001,http://localhost:3000` | Comma-separated CORS allowlist |
| `IMS_API_KEY` | optional | (empty) | Legacy API key gate. If unset, only JWT auth is required. If set, requests must include either `X-API-Key: <key>` or a Bearer JWT. |
| `JWT_SECRET` | yes (prod) | `dev-only-secret-change-me` | HS256 signing key for JWT tokens (see `routers/auth.py`) |
| `ANTHROPIC_API_KEY` | yes for OCR | (empty) | Used by `services/extraction_service.py` to call Claude Haiku |
| `RESEND_API_KEY` | yes for access-request emails | (empty) | Used by `services/email_service.py` to send /tech-stack access-request emails. Free tier at [resend.com](https://resend.com) gives 100 emails/day. If unset, requests are still recorded in the DB; only the email is skipped. |
| `EMAIL_FROM` | optional | `Rosetta IMS <onboarding@resend.dev>` | Sender for transactional emails. Switch to a verified `algogroup.io` sender once DNS is configured on Resend. |
| `ADMIN_EMAIL` | optional | `chris@algogroup.io` | Who receives the /tech-stack access-request emails. Requestor is cc'd. |

Use `.env.local` (gitignored) for local secrets. In production, set via Fly.io secrets:

```bash
fly secrets set JWT_SECRET=<...> IMS_API_KEY=<...> ANTHROPIC_API_KEY=<...>
```

---

## Database

### Schema
See [`SCHEMA.md`](./SCHEMA.md) for the full ER diagram and table-by-table notes.

### Migrations
Migrations live in `database.py` → `run_migrations()`. They run automatically on every app start via `main.py`:

```python
models.Base.metadata.create_all(bind=database.engine)
database.run_migrations(database.engine)
```

The pattern is intentionally simple — idempotent `ALTER TABLE ADD COLUMN` statements wrapped in `try/except` (SQLite throws when a column already exists; we ignore). For new tables, use `CREATE TABLE IF NOT EXISTS`.

**To add a new column or table:**
1. Update the SQLAlchemy model in `models.py`
2. Add the corresponding `ALTER TABLE` / `CREATE TABLE` statement to `run_migrations()` in `database.py`
3. Restart the API — migration runs on next startup

For complex migrations (renames, data backfills), promote to [Alembic](https://alembic.sqlalchemy.org/). Not needed yet.

### Seeding
- `seed_default_users()` in `database.py` creates two default users on first run (`seph` / `team`) if the `users` table is empty
- `seed.py` and `seed_from_sheet.py` are one-off scripts for SKU data seeding from Google Sheets

---

## Project layout

```
backend/
├── main.py                  # FastAPI app, router wiring, CORS, auth middleware
├── database.py              # Engine, session, migrations, user seeding
├── models.py                # SQLAlchemy ORM — single source of truth for schema
├── dependencies.py          # FastAPI dependency injection (get_db, etc.)
├── seed.py                  # Initial SKU seed (legacy — kept for reference)
├── seed_from_sheet.py       # Pull SKUs from Google Sheet on demand
├── requirements.txt
├── Dockerfile               # For Fly.io deploy
├── fly.toml                 # Fly.io app config
├── ims.db                   # SQLite (gitignored)
│
├── routers/                 # HTTP routes — one file per domain
│   ├── auth.py              # /auth/login, /auth/me — JWT issuance
│   ├── products.py          # /products, /products/{sku}, /products/summary
│   ├── pricing.py           # /pricing matrix endpoints
│   ├── suppliers.py         # /suppliers CRUD
│   ├── sku.py               # SKU-level ops (cost edits, uom stamping)
│   ├── catalogues.py        # /catalogues OCR ingestion + review
│   ├── stock.py             # /stock CSV import + adjustments
│   └── sync.py              # /sync Google Sheet pull
│
├── services/                # Business logic — pure Python, no HTTP
│   ├── extraction_service.py    # OCR pipeline (Claude Haiku)
│   ├── pricing_service.py       # GP calculations, margin checks
│   ├── sheet_sync.py            # Google Sheet → IMS sync
│   └── sku_service.py           # SKU manipulation
│
└── scripts/                 # One-off jobs / utilities
    └── gen_ssot_spec_csv.py     # Generate ssot-spec.csv from spec source
```

**Pattern:** routers do HTTP. Services do logic. Models do schema. Don't mix.

---

## API contract

The frontend talks to the backend over HTTP via a single abstraction file: [`frontend/src/lib/api.ts`](../frontend/src/lib/api.ts).

Response shapes are typed in [`frontend/src/lib/types.ts`](../frontend/src/lib/types.ts). When you change a JSON response shape, update both:

1. The Pydantic model / dict structure in the FastAPI route handler
2. The matching TypeScript interface in `lib/types.ts`

### Auto-generating types

FastAPI exposes the full OpenAPI schema at `/openapi.json`. Currently 31KB / ~30 endpoint operations. The generated TypeScript file at [`frontend/src/lib/api-types.generated.ts`](../frontend/src/lib/api-types.generated.ts) is a fully-typed mirror — checked into the repo so audit-readers can browse without running anything.

To regenerate after backend changes, **three options**:

```powershell
# Option 1 — backend running locally on :8001
cd frontend
npm run types:generate

# Option 2 — hit Fly.io prod (after the next deploy makes /openapi.json public)
cd frontend
npm run types:generate:prod

# Option 3 — offline (no server, just Python + venv)
cd backend
.\venv\Scripts\python.exe -c "import json; from main import app; print(json.dumps(app.openapi()))" > openapi.json
cd ..\frontend
npx openapi-typescript ../backend/openapi.json -o src/lib/api-types.generated.ts
```

The hand-written [`lib/types.ts`](../frontend/src/lib/types.ts) (152 lines) and the auto-generated `api-types.generated.ts` (~2200 lines) coexist during the transition. Existing pages still import from `types.ts`; new code can import from `api-types.generated.ts` like:

```typescript
import type { components } from '@/lib/api-types.generated'
type Product = components['schemas']['Product']
```

Eventually `types.ts` can be deleted in favour of the generated file.

---

## Adding a new endpoint

1. Decide which router it belongs in (or create a new file in `routers/`)
2. Add the route handler — use `Depends(get_db)` for DB session and `Depends(get_current_user)` for auth
3. If the route returns a new shape, add a Pydantic response model in the same file (FastAPI uses it for OpenAPI)
4. If created a new router file, register it in `main.py` (`app.include_router(...)`)
5. Update `frontend/src/lib/api.ts` with a calling function and `frontend/src/lib/types.ts` with the response type — OR re-run `npm run types:generate` to refresh auto types

---

## Adding a new table (e.g., `purchase_orders`)

1. Add the SQLAlchemy model to `models.py`
2. Add `CREATE TABLE IF NOT EXISTS purchase_orders (...)` to `run_migrations()` in `database.py`
3. Create a router in `routers/purchase_orders.py` (or extend an existing one) with the relevant endpoints
4. Register the router in `main.py`
5. Restart the API — migration runs

For example: when migrating the Biz Ops tab into Rosetta IMS, this is the table that holds per-PO records. It would FK to `products.id` and `suppliers.id`.

---

## Deployment

### Fly.io (production)

**Auto-deploy** is wired up via GitHub Actions ([`.github/workflows/fly-deploy.yml`](../.github/workflows/fly-deploy.yml)) — every push to `main` that touches `backend/**` triggers `flyctl deploy --remote-only`. Mirrors what Vercel does for the frontend so neither side needs manual redeploys.

To enable the workflow you need a `FLY_API_TOKEN` GitHub secret:

```bash
# 1) Generate a deploy token (one-off; expires after 1 year by default)
fly tokens create deploy --name "github-actions-rosetta-ims"

# 2) Copy the token (starts with FlyV1 fm2_...) and add it to GitHub at
#    https://github.com/cswf86/rosetta-ims/settings/secrets/actions
#    Name: FLY_API_TOKEN
```

Once the secret is set, every backend push triggers a deploy automatically. Check progress at https://github.com/cswf86/rosetta-ims/actions or trigger manually from there.

**Manual deploy** (still useful for testing locally before pushing, or replaying a failed Action):

```bash
fly deploy                    # from backend/ dir
fly logs                      # tail logs
fly ssh console               # shell into the container
fly secrets list              # check env var names
fly secrets set KEY=value     # add or update a secret (triggers redeploy)
```

App URL: `https://rosetta-ims-api.fly.dev`
Swagger: `https://rosetta-ims-api.fly.dev/docs`

### Database in prod
- Currently SQLite on a Fly volume
- For higher concurrency, switch to Fly Postgres: `fly postgres create` + `fly postgres attach`, then `DATABASE_URL` is set automatically

### Vercel (frontend)
Frontend auto-deploys on every push to `main` via Vercel's GitHub integration. The frontend reads `NEXT_PUBLIC_API_URL` to know where the backend lives (set in Vercel project env vars).

---

## Auth

Two auth mechanisms run in parallel for transition reasons. **JWT is the path forward.**

### JWT (recommended)
- `POST /auth/login` with `{username, password}` returns `{access_token, user}`
- Subsequent requests include `Authorization: Bearer <token>`
- Token validated per-endpoint via `Depends(get_current_user)` in `dependencies.py`
- Default users seeded on first run: `seph` (admin), `team` (data_entry)

### Legacy API key (transitional)
- Gated globally in the `require_api_key` middleware in `main.py`
- Skipped entirely if `IMS_API_KEY` env var is unset (dev mode)
- Pass via `X-API-Key: <key>` header

`/health` and `/auth/login` are exempt from both gates.

---

## Frequently changed files

| When you want to... | Edit... |
|---|---|
| Add a new database column | `models.py` + `database.py` (`run_migrations`) |
| Add a new endpoint | `routers/*.py` (+ register in `main.py` if new file) |
| Change business logic | `services/*.py` |
| Change CORS or middleware | `main.py` |
| Add a new env var | `main.py` (read it with `os.environ.get`) + this README |

## What lives outside this directory

| Concern | Location |
|---|---|
| Frontend UI | `../frontend/` |
| API client | `../frontend/src/lib/api.ts` |
| TypeScript types | `../frontend/src/lib/types.ts` |
| Static page content (v7 spec, AM walkthrough data) | `../frontend/src/data/` |
| Project-wide CLAUDE.md (BMAD workflow) | `../CLAUDE.md` |
| Planning artifacts | `../_bmad-output/` |

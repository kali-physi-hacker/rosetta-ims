# Rosetta IMS

Inventory Management System for Algo Group (veterinary / pet-supply, Hong Kong).
Monorepo: a **React (Vite) SPA** talking to a **FastAPI** backend.

> This repository is a clean re-home of the original app. The **backend is copied
> verbatim** (unchanged logic); the **frontend is being rebuilt** from Next.js to a
> Vite SPA, screen by screen, with the UI preserved exactly. See
> [`MIGRATION.md`](./MIGRATION.md) for what is ported vs. pending.

## Layout

```
rosetta-ims/
├── apps/
│   ├── web/          # React 19 + Vite + TypeScript SPA (this is the rebuild)
│   └── api/          # FastAPI + SQLite backend — copied verbatim, do not refactor
├── pnpm-workspace.yaml
└── package.json      # workspace root (scripts delegate to apps/web)
```

### `apps/web` — the frontend

| Concern        | Choice                                             |
|----------------|----------------------------------------------------|
| Build / dev    | **Vite 6**                                          |
| UI             | **React 19** + TypeScript                           |
| Routing        | **TanStack Router** (file-based, `src/routes/`)     |
| Server state   | **TanStack Query**                                  |
| API client     | **openapi-fetch** + types generated from the backend OpenAPI schema |
| Styling        | Tailwind v4 (available) + the app's existing inline-style design system, ported verbatim |

## Prerequisites

- **Node ≥ 20** and **pnpm ≥ 10** (`corepack enable`)
- **Python ≥ 3.11** (for the backend)

## Quickstart

### 1. Frontend (`apps/web`)

```bash
pnpm install
cp apps/web/.env.example apps/web/.env   # optional; defaults work against the live backend
pnpm dev                                 # → http://localhost:3001
```

By default the dev server calls `/api/*` (one unversioned API surface) and proxies it to the deployed backend
(`https://178.128.127.5.nip.io`), so the frontend runs on its own with no local
backend needed. Point it elsewhere with `VITE_API_PROXY_TARGET` in `apps/web/.env`.

### 2. Backend (`apps/api`) — only if you want it running locally

```bash
cd apps/api
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --reload --port 8001
```

Then set `apps/web/.env` → `VITE_API_PROXY_TARGET=http://localhost:8001`.

### Regenerate the typed API client

The typed client (`apps/web/src/lib/api/generated.ts`) is generated from the
backend's live OpenAPI schema and committed. Refresh it after backend API changes:

```bash
pnpm types                                   # uses the deployed backend
VITE_API_URL=http://localhost:8001 pnpm types  # reads http://localhost:8001/openapi.json
```

## How auth works

The backend issues a JWT on `POST /auth/login`. The SPA stores it in the
`ims_token` cookie (and the user in `ims_user`), reads it client-side, and sends it
as a `Bearer` token on every request. The `/_authed` layout route guards every
authenticated screen and redirects to `/login` when the cookie is absent — the
backend remains the real authorization gate (`apps/api/permissions.py`).

## Conventions

- **`apps/api` is off-limits for refactors.** It holds pricing, SKU-matching, sync,
  auth and schema logic and was copied unchanged. Treat it as a black box the web app
  calls; change it only through its own review process.
- Frontend screens are ported to preserve the exact UI (colors, spacing, behavior).
  The design system is currently inline styles with literal hex values — see the
  palette section in `MIGRATION.md`.

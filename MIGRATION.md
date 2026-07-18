# Frontend migration — Next.js → Vite SPA

Status: **complete** — all 28 routes ported from the old Next.js app to the Vite +
TanStack Router SPA, verbatim (identical UI, colours, behaviour). Each screen was
verified with a whole-app `build` + `tsc` before commit.

## Foundation
Vite 8 · React 19 · TanStack Router (file-based) · TanStack Query · openapi-fetch typed
client (generated from the backend schema) · Tailwind v4 available. Auth = cookie
(`ims_token`/`ims_user`) + Bearer; `/_authed` layout route guards + provides the shell.
API base normalised to one place (`lib/config.ts`); dev calls proxy through `/api`.

## Screens (all ✅)
Foundation: theme, auth, app shell (AppShell + Sidebar), Toaster/ConfirmDialog/Spinner,
login, `/_authed` guard, client-side login (replaces the Next `/api/login` route).

- **Inventory/core:** `/` All Inventory (1.5k) · `/items/$` SKU detail (catch-all splat, 1.7k) · `/collections` · `/categories` · `/data-review` (0.9k)
- **Catalogue:** `/catalogues` (3.3k) · `/catalogues/reparse` · `/catalogues/reparse/$batchId`
- **Clients:** `/clients` CRM (1.2k) · `/clients/guide` (+ `GuideContent`)
- **Ops/admin:** `/suppliers` · `/stock` · `/config` · `/admin/users` · `/admin/audit` · `/admin/report`
- **Docs (static):** `/architecture` · `/playbook` · `/tech-stack` (NDA gate) · `/ssot-spec` · `/am-walkthrough` · `/logic`
- **Standalone:** `/onboard` (public invite) · `/pitch` (guarded iframe) · `/login`
- **Retired:** `/pricing` → redirect `/?view=margins`

## Infra dropped (replaced by SPA equivalents)
`middleware.ts` → `/_authed` guard · `app/api/login/route.ts` → client login ·
`next.config.ts` `/api` rewrite → Vite proxy · OpenNext/wrangler/Cloudflare config ·
`fly.toml` (backend, Fly disabled).

## Migration-time notes
- **Typed-route casts:** data-driven `<Link to={x as never}>` and `navigate({to: x as never})`
  appear where nav targets are computed strings. Tighten to typed routes as a follow-up.
- **`noUnusedLocals`/`noUnusedParameters`:** briefly relaxed while the verbatim ports
  landed (they inherited a handful of unused vars from the original). **Dead-code swept
  and the flags re-enabled** (see the closing commit).

## Cleanup TODO (quality, non-blocking)
- Centralise the palette — hex is still duplicated across screens (`lib` tokens module).
- Extract shared UI (category/status/cost-source chips, platform badges, filter bars, tables)
  currently redefined per screen.
- Migrate remaining raw `fetch` calls onto the typed client + TanStack Query, screen by screen.
- Add ESLint + Prettier (flat config).
- Replace the `as never` route casts with typed routes.

## Verify locally
```bash
pnpm install && pnpm dev     # http://localhost:3001 (proxies /api to the backend)
pnpm build && pnpm typecheck # both clean
```

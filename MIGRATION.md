# Frontend migration — Next.js → Vite SPA

Tracks the port of every screen so **nothing is silently dropped**. The UI (colors,
theme, layout, behavior) must stay identical; each screen is ported, then verified
against the original before it's marked done.

Legend: ✅ ported · ⏳ pending · ♻️ retired/redirect · 🧱 infra (replaced by SPA equivalent)

## Foundation

| Piece | Status | Notes |
|---|---|---|
| Repo scaffold (pnpm workspace, Vite, TS) | ✅ | |
| Theme / `globals.css` | ✅ | copied verbatim |
| Auth (`lib/auth.ts`, cookie + Bearer, capabilities) | ✅ | verbatim; `NEXT_PUBLIC_API_URL` → `API_BASE` |
| API base URL | ✅ | normalized to one source (`lib/config.ts`); the old code had 3 inconsistent fallbacks |
| Typed API client (openapi-fetch + generated types) | ✅ | `pnpm types` regenerates from the backend schema |
| TanStack Query provider | ✅ | `main.tsx` |
| App shell (`AppShell` + `Sidebar`) | ✅ | verbatim; hoisted into the `/_authed` layout route (was imported per-page) |
| Toaster / ConfirmDialog / Spinner | ✅ | verbatim |
| Auth route guard | ✅ | `/_authed` `beforeLoad` (replaces `middleware.ts`) |
| Client-side login (cookie set) | ✅ | replaces the Next `/api/login` route handler |

## Screens

Ordered roughly by size so the biggest, riskiest screens come last.

| Route | Lines | Status | Notes |
|---|---:|---|---|
| `/login` | 116 | ✅ | first slice |
| `/categories` | 160 | ✅ | **first verified data slice** (TanStack Query + typed client) |
| `/` — All Inventory | 1534 | ⏳ | master grid; NDJSON streaming, CSV export, margins view — dedicated port |
| `/items/$sku` — SKU detail | 1687 | ⏳ | catch-all (`[...sku]`); multi-channel pricing, MBB, competitors |
| `/catalogues` | 3293 | ⏳ | largest screen; matching/review/onboarding |
| `/catalogues/reparse` | 349 | ⏳ | re-parse inbox |
| `/catalogues/reparse/$batchId` | 736 | ⏳ | per-item diff review |
| `/clients` — Clientbase | 1169 | ⏳ | CRM |
| `/data-review` | 946 | ⏳ | streams products; client-side data-quality flags |
| `/collections` | 344 | ⏳ | smart-collection rule builder |
| `/stock` | 290 | ⏳ | CSV stock import |
| `/suppliers` | 261 | ⏳ | reference admin |
| `/config` | 463 | ⏳ | transformation-config editor |
| `/admin/users` | 292 | ⏳ | |
| `/admin/audit` | 184 | ⏳ | |
| `/admin/report` | 518 | ⏳ | onboarding report |
| `/onboard` | 162 | ⏳ | public invite acceptance (`?token`) |
| `/logic` | 305 | ⏳ | fetches `/category-rules` |
| `/architecture` | 882 | ⏳ | static doc |
| `/playbook` | 1081 | ⏳ | static doc (`src/data/inventory.ts`) |
| `/tech-stack` | 852 | ⏳ | static doc + NDA click-wrap gate (acknowledgement API) |
| `/ssot-spec` | 709 | ⏳ | static (`src/data/ssot-spec.ts`) |
| `/am-walkthrough` | 383 | ⏳ | static (`src/data/am-walkthrough.ts`) |
| `/clients/guide` | 12 | ⏳ | wraps `GuideContent` |
| `/pitch` | 24 | ⏳ | full-bleed iframe → reveal.js deck (`public/pitch/`) |
| `/pricing` | 8 | ♻️ | retired → redirect to `/?view=margins` |

Unported routes currently resolve to a clear "not migrated yet" page (`PendingRoute`).

## Infra intentionally NOT carried over (🧱 replaced)

- `middleware.ts` (edge auth guard) → `/_authed` `beforeLoad` guard
- `app/api/login/route.ts` (cookie-setting handler) → client-side login in `routes/login.tsx`
- `next.config.ts` `/api` rewrite → Vite dev-server proxy
- `open-next.config.ts`, `wrangler.jsonc`, `@opennextjs/cloudflare`, `wrangler` → dropped (static SPA)
- `fly.toml` (backend, Fly disabled) → removed from `apps/api`

## Cleanup TODO (do as screens are ported — don't rush ahead of verification)

- **Centralize the palette.** Colors are literal hex duplicated across ~10 screens.
  Extract to a `tokens` module (identical values) as each screen is ported, diffing
  the rendered result against the original.
- **Extract shared UI** (category/status chips, platform badges, filter bars, tables)
  that are currently redefined inside each page.
- **Migrate remaining raw `fetch` calls** onto the typed client + TanStack Query,
  screen by screen.
- **Add ESLint + Prettier** (flat config) once the surface stabilizes.
- Port `lib/streamProducts.ts` (NDJSON) and `lib/reparse.ts` with their screens.

## Design system reference (exact hex — do not change)

Chrome: sidebar `#0F172A`, hover `#1E293B`, active bg `#1E3A5F`, active text `#93C5FD`,
nav text `#94A3B8`, section labels `#475569`, brand accent `#818CF8`, logo gradient
`135deg #4F46E5→#7C74F0`. Content bg `#F6F7F9` (shell) / `#F9FAFB` (body). Cards `#FFF`,
border `#E2E8F0`. Text `#0F172A` / `#475569` / `#64748B` / `#94A3B8`. Primary indigo
`#6366F1` (`#4F46E5`, `#4338CA`, bg `#EEF2FF`). Role avatars: admin `#6366F1`, bizops
`#0891B2`, data_entry `#0D9488`. Font: `-apple-system, 'Inter', system-ui, sans-serif`;
mono `ui-monospace, "SF Mono", Menlo, monospace`. Category / status / cost-source chip
maps are defined per-screen — carry them verbatim.

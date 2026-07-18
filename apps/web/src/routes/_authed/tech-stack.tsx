import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { getUser, type IMSUser } from '@/lib/auth'
import { getMyAcknowledgement, createAcknowledgement, listAcknowledgements } from '@/lib/api'
import type { AccessAcknowledgement } from '@/lib/types'

const REPO = 'https://github.com/cswf86/rosetta-ims'
const API = 'https://178.128.127.5.nip.io'

export const Route = createFileRoute('/_authed/tech-stack')({ component: TechStackPage })

function TechStackPage() {
  return (
    <>
      <style>{`
        .ts-link:hover { background: #EEF2FF !important; border-color: #818CF8 !important; }
        .ts-link-inline:hover { text-decoration: underline; }
      `}</style>

      <div style={{ maxWidth: '1000px' }}>
        {/* Header */}
        <div style={{ marginBottom: '16px' }}>
          <p style={{ fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '4px' }}>
            For Tech Team
          </p>
          <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#0F172A', marginBottom: '6px' }}>
            Tech Stack & Audit Landing
          </h1>
          <p style={{ fontSize: '13px', color: '#475569', lineHeight: 1.55, maxWidth: '780px' }}>
            One page to brief any developer or auditor on how Rosetta IMS is built, where the code lives,
            and how to start contributing. This page is a pointer board — canonical docs live in the repo
            itself so they never drift.
          </p>
        </div>

        {/* OBJECTIVE — decouple UI from data */}
        <div style={{
          background: '#EEF2FF', border: '1px solid #C7D2FE', borderLeft: '4px solid #6366F1',
          borderRadius: '8px', padding: '16px 18px', marginBottom: '14px',
        }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: '#4338CA', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
            🎯 Architectural objective — decouple the UI from the data
          </p>
          <p style={{ fontSize: '12.5px', color: '#1E1B4B', lineHeight: 1.6, marginBottom: '8px' }}>
            The whole point of how this codebase is structured is so the <strong>tech team and the UI team can work
            in parallel without stepping on each other</strong>. Two separate apps, two separate deploy targets, one
            HTTP contract between them.
          </p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginTop: '10px', marginBottom: '10px' }}>
            <div style={{ background: 'white', border: '1px solid #C7D2FE', borderRadius: '6px', padding: '10px 12px' }}>
              <p style={{ fontSize: '11px', fontWeight: 700, color: '#4338CA', marginBottom: '4px' }}>Tech team (data side)</p>
              <p style={{ fontSize: '11px', color: '#1E1B4B', lineHeight: 1.55, margin: 0 }}>
                Owns <code style={{ background: '#F1F5F9', padding: '1px 4px', borderRadius: '2px', fontSize: '10.5px' }}>backend/</code> —
                builds ingestion pipelines, schemas, business logic, API endpoints. Can refactor freely as long as JSON response shapes stay stable.
              </p>
            </div>
            <div style={{ background: 'white', border: '1px solid #C7D2FE', borderRadius: '6px', padding: '10px 12px' }}>
              <p style={{ fontSize: '11px', fontWeight: 700, color: '#4338CA', marginBottom: '4px' }}>UI team (Chris)</p>
              <p style={{ fontSize: '11px', color: '#1E1B4B', lineHeight: 1.55, margin: 0 }}>
                Owns <code style={{ background: '#F1F5F9', padding: '1px 4px', borderRadius: '2px', fontSize: '10.5px' }}>frontend/</code> —
                builds pages, layouts, content, UX. Can rebuild any page completely without touching the backend.
              </p>
            </div>
          </div>
          <p style={{ fontSize: '12px', color: '#1E1B4B', lineHeight: 1.6, margin: 0 }}>
            <strong>The contract</strong> is the OpenAPI schema (auto-published from FastAPI) + the TypeScript types
            mirror in <code style={{ background: 'white', padding: '1px 4px', borderRadius: '2px', fontSize: '11px' }}>src/lib/api-types.generated.ts</code>.
            If the backend changes a response shape, TypeScript breaks loudly on the frontend until types are regenerated — drift is detectable.
            Cross-team conversations happen only when the contract changes.{' '}
            <a href="/architecture" style={{ color: '#6366F1', fontWeight: 600, textDecoration: 'underline' }}>
              See /architecture
            </a>{' '}for the full data-flow picture, including how Desmond&apos;s ingestion pipelines fit in.
          </p>
        </div>

        {/* Repo link — gated by NDA acknowledgement for non-admins */}
        <GitHubAccessGate />
        <p style={{ fontSize: '11px', color: '#94A3B8', marginBottom: '20px', lineHeight: 1.5, fontStyle: 'italic' }}>
          🔒 Private repo. If you get a 404 after clicking through, you don&apos;t have GitHub access yet —
          ping Chris to be added as a collaborator, or log into the GitHub account you were granted access on.
        </p>

        {/* Stack table */}
        <Section title="Stack">
          <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'hidden' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '120px 1fr 130px', gap: '0', padding: '8px 14px', background: '#F8FAFC', borderBottom: '1px solid #E2E8F0' }}>
              {['Layer', 'Choice', 'Hosted on'].map(h => (
                <span key={h} style={{ fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</span>
              ))}
            </div>
            {[
              { layer: 'Frontend',  choice: 'Next.js 16.2 (App Router) · React 19 · TypeScript 5 · Tailwind v4',          host: 'Vercel' },
              { layer: 'Backend',   choice: 'FastAPI · SQLAlchemy 2.x · Pydantic · Python 3.13+',                          host: 'Fly.io (Docker, auto-deploy via GitHub Actions)' },
              { layer: 'Database',  choice: 'SQLite (dev) — Postgres-ready via DATABASE_URL',                              host: 'Fly volume' },
              { layer: 'Auth',      choice: 'JWT (HS256) + legacy API-key gate (transitional)',                            host: '—' },
              { layer: 'OCR / AI',  choice: 'Anthropic SDK (Claude Haiku for catalogue extraction)',                       host: 'Anthropic API' },
              { layer: 'Schema',    choice: 'OpenAPI 3.1 auto-published; TypeScript types auto-generated',                 host: '/openapi.json' },
            ].map((r, i) => (
              <div key={r.layer} style={{
                display: 'grid', gridTemplateColumns: '120px 1fr 130px',
                padding: '10px 14px', borderBottom: i < 5 ? '1px solid #F1F5F9' : 'none',
                fontSize: '12px', alignItems: 'center',
              }}>
                <span style={{ fontWeight: 700, color: '#0F172A' }}>{r.layer}</span>
                <span style={{ color: '#475569' }}>{r.choice}</span>
                <span style={{ color: '#64748B', fontSize: '11px' }}>{r.host}</span>
              </div>
            ))}
          </div>
        </Section>

        {/* Where things live */}
        <Section title="Where things live">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
            <DirCard
              title="backend/"
              subtitle="Python · FastAPI · SQLAlchemy"
              items={[
                ['models.py',     'SQLAlchemy ORM — single source of truth for schema'],
                ['main.py',       'FastAPI app, CORS, auth middleware, router wiring'],
                ['database.py',   'Engine, sessions, migrations, user seeding'],
                ['routers/',      'HTTP routes — 1 file per domain'],
                ['services/',     'Business logic — pricing, OCR extraction, Sheet sync'],
                ['scripts/',      'One-off jobs'],
              ]}
            />
            <DirCard
              title="frontend/"
              subtitle="Next.js · TypeScript · Tailwind"
              items={[
                ['src/app/',                       'One folder per page (App Router)'],
                ['src/components/shell/',          'Shared chrome — Sidebar, AppShell, login'],
                ['src/data/',                      'Static page content (v7 spec, AM walkthrough)'],
                ['src/lib/api.ts',                 'Single abstraction for ALL HTTP calls'],
                ['src/lib/types.ts',               'Hand-written types (transitional)'],
                ['src/lib/api-types.generated.ts', 'Auto-generated from OpenAPI schema'],
              ]}
            />
          </div>

          <div style={{
            background: '#F0FDF4', border: '1px solid #BBF7D0',
            borderRadius: '8px', padding: '12px 16px', marginTop: '10px',
            fontSize: '12px', color: '#14532D', lineHeight: 1.6,
          }}>
            <strong>The contract:</strong> Pages never <code style={{ background: 'white', padding: '1px 4px', borderRadius: '2px', fontSize: '11px' }}>fetch()</code> directly —
            all backend calls go through <code style={{ background: 'white', padding: '1px 4px', borderRadius: '2px', fontSize: '11px' }}>src/lib/api.ts</code>.
            Response shapes are typed in <code style={{ background: 'white', padding: '1px 4px', borderRadius: '2px', fontSize: '11px' }}>src/lib/types.ts</code>
            (gradually being replaced by the auto-generated file). Backend devs can change router internals freely as long as JSON shapes match. Frontend devs
            can rebuild the UI without touching backend.
          </div>
        </Section>

        {/* Where data lives */}
        <Section title="Where data physically lives">
          <p style={{ fontSize: '12px', color: '#475569', lineHeight: 1.6, marginBottom: '10px' }}>
            A common audit question — "what's stored where, and what happens if I delete X?" Honest answer below,
            including known limitations.
          </p>
          <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'hidden' }}>
            <div style={{ display: 'grid', gridTemplateColumns: '230px 320px 1fr', gap: '8px', padding: '8px 14px', background: '#F8FAFC', borderBottom: '1px solid #E2E8F0' }}>
              {['What', 'Where today', 'Notes'].map(h => (
                <span key={h} style={{ fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</span>
              ))}
            </div>
            {[
              {
                what: 'Database (ims.db)',
                where: 'Local dev: backend/ims.db (gitignored). Prod: Fly.io volume mounted to the API container.',
                notes: 'SQLite file — moves with the Fly app. All SKUs, suppliers, stock, prices, extracted catalogue items, users, audit logs.',
              },
              {
                what: '⚠️ Raw catalogue PDFs / Excel',
                where: 'Nowhere on the server. Read into memory during upload, processed, then discarded.',
                notes: 'Originals must live somewhere YOU control — desktop, Google Drive, supplier folder. The IMS does not retain a copy.',
                warn: true,
              },
              {
                what: 'Extracted catalogue line items',
                where: 'Database (catalogue_items + catalogue_imports tables).',
                notes: 'Survives — you can re-review approvals without re-uploading. But you cannot re-extract from a different OCR pass without the original file.',
              },
              {
                what: 'User credentials',
                where: 'Database (users table). Passwords stored as bcrypt hashes.',
                notes: 'No plaintext passwords anywhere. JWT tokens are signed at request time, never stored.',
              },
              {
                what: 'OCR processing',
                where: 'In-memory on the API server, then Claude Haiku via Anthropic API.',
                notes: 'API call only — Anthropic does not retain data per their commercial terms. ANTHROPIC_API_KEY required.',
              },
              {
                what: 'Google Sheets (v7, Logic Layer)',
                where: 'Google\'s servers. IMS pulls from them on demand; never writes back.',
                notes: 'Read-only direction. Sheet IDs hardcoded in services/sheet_sync.py. The Sheets are the temporary SSOT; v7 is being migrated into the IMS database.',
              },
              {
                what: 'API key / JWT secret',
                where: 'Fly.io secrets (prod). .env.local (gitignored, never committed) for dev.',
                notes: 'Rotated via fly secrets set ... — never visible in repo or logs.',
              },
            ].map((r, i, arr) => (
              <div key={r.what} style={{
                display: 'grid', gridTemplateColumns: '230px 320px 1fr', gap: '8px',
                padding: '10px 14px', borderBottom: i < arr.length - 1 ? '1px solid #F1F5F9' : 'none',
                fontSize: '11.5px', alignItems: 'start',
                background: r.warn ? '#FFFBEB' : 'transparent',
              }}>
                <span style={{ color: r.warn ? '#92400E' : '#0F172A', fontWeight: 600 }}>{r.what}</span>
                <span style={{ color: r.warn ? '#7C2D12' : '#475569', lineHeight: 1.5 }}>{r.where}</span>
                <span style={{ color: r.warn ? '#7C2D12' : '#64748B', lineHeight: 1.5 }}>{r.notes}</span>
              </div>
            ))}
          </div>

          {/* Known limitation callout */}
          <div style={{
            background: '#FFFBEB', border: '1px solid #FDE68A', borderLeft: '4px solid #F59E0B',
            borderRadius: '8px', padding: '12px 16px', marginTop: '12px',
          }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: '#92400E', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>
              ⚠️ Known limitation — catalogue raw files
            </p>
            <p style={{ fontSize: '12px', color: '#451A03', lineHeight: 1.6, marginBottom: '6px' }}>
              <code style={{ background: 'white', padding: '1px 4px', borderRadius: '2px', fontSize: '11px' }}>routers/catalogues.py</code>
              reads the uploaded PDF into memory, passes it to Claude Haiku, then discards it. No <code>uploads/</code> folder.
              That means:
            </p>
            <ul style={{ margin: '0 0 6px 18px', padding: 0, fontSize: '11.5px', color: '#451A03', lineHeight: 1.6 }}>
              <li>You can re-review the extracted line items, but you can&apos;t re-OCR with a different prompt or a better model</li>
              <li>If a supplier sends a corrected catalogue, you have to upload it manually each time</li>
              <li>The originals depend on a human curating them somewhere outside the system</li>
            </ul>
            <p style={{ fontSize: '12px', color: '#451A03', lineHeight: 1.6, margin: 0 }}>
              <strong>Planned fix:</strong> Google Drive service account integration. Catalogues live in a Drive folder owned by Algo Technologies Pte Ltd;
              the IMS pulls from Drive on a schedule and re-extracts when a file is updated. That makes Drive the canonical store of originals
              and the IMS a derived view. Tech team owns this — it&apos;s a backend ingestion change, no UI impact.
            </p>
          </div>

          {/* Business vs tech reminder */}
          <p style={{ fontSize: '11px', color: '#94A3B8', marginTop: '10px', fontStyle: 'italic', lineHeight: 1.55 }}>
            Some of what you see in this app was vibe-coded fast to validate the business hypothesis. Now that the
            hypothesis is proven, tech team can harden the parts that need it (catalogue persistence, ingestion
            pipelines, Postgres migration). The contract between UI and data stays the same — only the implementation behind it changes.
          </p>
        </Section>

        {/* Canonical docs */}
        <Section title="Canonical docs in the repo">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <RepoLink
              href={`${REPO}/blob/main/backend/README.md`}
              title="backend/README.md"
              desc="Backend onboarding: stack, local dev, env vars, project layout, how to add endpoints / tables, deployment, auth"
            />
            <RepoLink
              href={`${REPO}/blob/main/backend/SCHEMA.md`}
              title="backend/SCHEMA.md"
              desc="Full ER diagram (renders natively in GitHub) + per-table notes + where future tables (purchase_orders, sf_express_rates, etc.) would slot in"
            />
            <RepoLink
              href={`${REPO}/blob/main/frontend/src/lib/api-types.generated.ts`}
              title="frontend/src/lib/api-types.generated.ts"
              desc="Auto-generated TypeScript types — every endpoint, every response shape. ~2200 lines. Regenerate via npm run types:generate."
            />
            <RepoLink
              href={`${REPO}/blob/main/frontend/src/lib/api.ts`}
              title="frontend/src/lib/api.ts"
              desc="The single HTTP abstraction. 80 lines. Every backend call in the frontend goes through this file."
            />
            <RepoLink
              href={`${REPO}/blob/main/backend/models.py`}
              title="backend/models.py"
              desc="SQLAlchemy ORM. 250 lines. 14 tables. The actual source of truth for the schema."
            />
          </div>
        </Section>

        {/* Live endpoints */}
        <Section title="Live endpoints">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <RepoLink
              href={`${API}/docs`}
              title="Swagger UI (interactive)"
              desc="Browse + try every API endpoint. Authentication: legacy API key or JWT (POST /auth/login to obtain a token)."
              monoUrl={`${API}/docs`}
            />
            <RepoLink
              href={`${API}/openapi.json`}
              title="OpenAPI 3.1 schema (raw)"
              desc="JSON spec — feed this to any code-generator (openapi-typescript, openapi-generator) to produce a typed client in any language."
              monoUrl={`${API}/openapi.json`}
            />
            <RepoLink
              href={`${API}/health`}
              title="Health check"
              desc="Liveness probe. No auth required."
              monoUrl={`${API}/health`}
            />
          </div>
          <p style={{ fontSize: '11px', color: '#94A3B8', marginTop: '8px', fontStyle: 'italic' }}>
            Note: <code>/docs</code> and <code>/openapi.json</code> are public after the next Fly.io deploy. Until then they require the API key —
            you can clone the repo and read <code>api-types.generated.ts</code> directly, which is generated from the same schema.
          </p>
        </Section>

        {/* If you want to audit X */}
        <Section title="If you want to audit X, look at Y">
          <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'hidden' }}>
            {[
              { q: 'The data model',                                 a: 'backend/SCHEMA.md (ER diagram)' },
              { q: 'The API surface',                                a: `${API}/docs (Swagger) — or api-types.generated.ts in the repo` },
              { q: 'How the frontend talks to the backend',          a: 'frontend/src/lib/api.ts (80 lines, all calls live here)' },
              { q: 'How auth works',                                 a: 'backend/main.py (middleware) + backend/routers/auth.py + frontend/src/lib/auth.ts' },
              { q: 'What the business logic is',                     a: 'backend/services/*.py — pricing_service, extraction_service (OCR), sheet_sync, sku_service' },
              { q: 'How to run locally',                             a: 'backend/README.md → Local development' },
              { q: 'How to add a new endpoint',                      a: 'backend/README.md → Adding a new endpoint' },
              { q: 'How to add a new database table',                a: 'backend/README.md → Adding a new table' },
              { q: 'How catalogues are ingested (OCR pipeline)',     a: 'backend/services/extraction_service.py + frontend/src/app/catalogues/' },
              { q: 'What\'s next on the architecture roadmap',       a: 'See in-app /architecture — current state, target state, 4-layer model, Desmond pipelines' },
              { q: 'How Biz Ops Sheet maps to v7 (the migration)',   a: 'See in-app /am-walkthrough — column-by-column gap analysis' },
              { q: 'What v7 actually is',                            a: 'See in-app /ssot-spec — the 65-column SKU master + suppliers spec' },
            ].map((r, i, arr) => (
              <div key={r.q} style={{
                display: 'grid', gridTemplateColumns: '350px 1fr',
                padding: '10px 14px', borderBottom: i < arr.length - 1 ? '1px solid #F1F5F9' : 'none',
                fontSize: '12px', alignItems: 'start', gap: '12px',
              }}>
                <span style={{ color: '#0F172A', fontWeight: 600 }}>{r.q}</span>
                <span style={{ color: '#475569', lineHeight: 1.5 }}>{r.a}</span>
              </div>
            ))}
          </div>
        </Section>

        {/* In-app docs */}
        <Section title="In-app docs (context for the build)">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px' }}>
            <InAppLink href="/architecture"   title="Architecture" desc="Current vs target data flow; 4-layer model; Desmond pipelines" />
            <InAppLink href="/am-walkthrough" title="AM Walkthrough" desc="All 85 Biz Ops cols mapped against v7 — what migrates where" />
            <InAppLink href="/ssot-spec"      title="SSOT Spec" desc="v7 = 65-col SKU master + suppliers spec; accuracy ladder; consumers" />
          </div>
        </Section>

        {/* Admin-only — paper trail of who acknowledged */}
        <AcknowledgementLog />

        <p style={{ fontSize: '11px', color: '#CBD5E1', marginTop: '20px', textAlign: 'center' }}>
          Edit this page at <code>frontend/src/app/tech-stack/page.tsx</code>. Canonical docs live in the repo.
        </p>
      </div>
    </>
  )
}

// ─── Access gate — modal click-wrap NDA before GitHub link ──────────────────

function GitHubAccessGate() {
  const [user, setUser] = useState<IMSUser | null>(null)
  const [latestAck, setLatestAck] = useState<AccessAcknowledgement | null>(null)
  const [justSubmitted, setJustSubmitted] = useState(false)  // brief success flash after a submission
  const [modalOpen, setModalOpen] = useState(false)

  useEffect(() => {
    const u = getUser()
    setUser(u)
    if (!u || u.role === 'admin') return
    getMyAcknowledgement()
      .then(r => {
        if (r.acknowledgement) setLatestAck(r.acknowledgement)
      })
      .catch(() => { /* ignore — endpoint may not exist yet */ })
  }, [])

  const isAdmin = user?.role === 'admin'

  // Admin → direct GitHub link, gate bypassed
  if (isAdmin) {
    return (
      <a
        href={REPO} target="_blank" rel="noreferrer" className="ts-link"
        style={{
          display: 'flex', alignItems: 'center', gap: '12px',
          background: '#0F172A', color: 'white', border: '1px solid #0F172A',
          borderRadius: '8px', padding: '14px 18px', textDecoration: 'none',
          marginBottom: '6px',
        }}
      >
        <span style={{ fontSize: '20px' }}>📦</span>
        <div style={{ flex: 1 }}>
          <p style={{ fontSize: '13px', fontWeight: 700, margin: 0 }}>GitHub repository ↗</p>
          <p style={{ fontSize: '11px', color: '#94A3B8', margin: '2px 0 0 0', fontFamily: 'ui-monospace, monospace' }}>
            cswf86/rosetta-ims
          </p>
        </div>
        <span style={{ fontSize: '11px', color: '#94A3B8' }}>admin · gate bypassed</span>
      </a>
    )
  }

  // Non-admin: ALWAYS show the request button. Anyone using this login (even multiple
  // people sharing it) gets their own chance to submit. Show a "you recently submitted"
  // note above the button when there's history on this account.
  const hasHistory = latestAck !== null

  return (
    <>
      {justSubmitted && latestAck && (
        <div style={{
          display: 'flex', alignItems: 'flex-start', gap: '10px',
          background: '#F0FDF4', border: '1px solid #BBF7D0', borderLeft: '4px solid #16A34A',
          borderRadius: '8px', padding: '12px 16px', marginBottom: '8px',
        }}>
          <span style={{ fontSize: '18px' }}>✓</span>
          <div style={{ flex: 1, fontSize: '11.5px', color: '#14532D', lineHeight: 1.55 }}>
            <strong>Request submitted.</strong> Email sent to chris@algogroup.io with{' '}
            <code style={{ background: 'white', padding: '1px 4px', borderRadius: '2px' }}>{latestAck.email_requestor}</code> as Reply-To.
            Chris will review and reply to you directly with the GitHub invitation.
            Another teammate sharing this login? They can submit their own below.
          </div>
        </div>
      )}

      {!justSubmitted && hasHistory && latestAck && (
        <div style={{
          fontSize: '11px', color: '#64748B',
          background: '#F8FAFC', border: '1px solid #E2E8F0',
          borderRadius: '6px', padding: '8px 12px', marginBottom: '8px',
          lineHeight: 1.55,
        }}>
          ℹ️ Most recent submission from this login: <strong>{latestAck.full_name_typed}</strong> (
          <code style={{ background: 'white', padding: '1px 4px', borderRadius: '2px', fontSize: '10.5px' }}>@{latestAck.github_username}</code>
          ) on {latestAck.accepted_at.slice(0, 10)}. If you&apos;re a different person sharing this
          login, submit your own request below.
        </div>
      )}

      <button
        onClick={() => setModalOpen(true)}
        className="ts-link"
        style={{
          display: 'flex', alignItems: 'center', gap: '12px', width: '100%',
          background: '#0F172A', color: 'white',
          border: '1px solid #0F172A', borderRadius: '8px',
          padding: '14px 18px', cursor: 'pointer',
          marginBottom: '6px', textAlign: 'left',
          fontFamily: 'inherit',
        }}
      >
        <span style={{ fontSize: '20px' }}>📝</span>
        <div style={{ flex: 1 }}>
          <p style={{ fontSize: '13px', fontWeight: 700, margin: 0 }}>
            {hasHistory ? 'Submit another access request' : 'Request access to GitHub repo'}
          </p>
          <p style={{ fontSize: '11px', color: '#94A3B8', margin: '2px 0 0 0' }}>
            Click-wrap NDA + emails Chris (your address goes in Reply-To, so his reply comes back to you)
          </p>
        </div>
        <span style={{ fontSize: '11px', color: '#FCD34D' }}>{hasHistory ? '' : 'required'}</span>
      </button>

      {modalOpen && user && (
        <AcknowledgementModal
          user={user}
          onClose={() => setModalOpen(false)}
          onSuccess={(ack) => {
            setLatestAck(ack)
            setJustSubmitted(true)
            setModalOpen(false)
          }}
        />
      )}
    </>
  )
}

function AcknowledgementModal({
  user, onClose, onSuccess,
}: {
  user: IMSUser
  onClose: () => void
  onSuccess: (ack: AccessAcknowledgement) => void
}) {
  const [c1, setC1] = useState(false)
  const [c2, setC2] = useState(false)
  const [c3, setC3] = useState(false)
  const [c4, setC4] = useState(false)
  const [github, setGithub] = useState('')
  const [name, setName] = useState(user.display_name)
  const [email, setEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const checkedCount = [c1, c2, c3, c4].filter(Boolean).length
  const allChecked = checkedCount === 4
  const emailValid = /\S+@\S+\.\S+/.test(email.trim())

  // Per-field validation flags (used to show what's missing under the submit button)
  const issues: string[] = []
  if (!allChecked) issues.push(`tick all 4 NDA boxes (${checkedCount}/4)`)
  if (github.trim().length < 1) issues.push('enter your GitHub username')
  if (name.trim().length < 2) issues.push('enter your full name')
  if (!emailValid) issues.push('enter a valid email')

  const canSubmit = issues.length === 0 && !submitting

  const submit = async () => {
    if (!canSubmit) return
    setSubmitting(true)
    setError(null)
    try {
      const res = await createAcknowledgement(github.trim(), name.trim(), email.trim())
      onSuccess(res.acknowledgement)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to submit request')
      setSubmitting(false)
    }
  }

  return (
    <div
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.5)',
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        zIndex: 100, padding: '20px',
      }}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'white', borderRadius: '12px',
          padding: '24px 28px', maxWidth: '600px', width: '100%',
          maxHeight: '92vh', overflowY: 'auto',
          boxShadow: '0 20px 50px rgba(15,23,42,0.3)',
        }}
      >
        <p style={{ fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '4px' }}>
          GitHub Access Request — NDA Acknowledgement
        </p>
        <h2 style={{ fontSize: '18px', fontWeight: 700, color: '#0F172A', marginBottom: '6px' }}>
          Algo Technologies Pte Ltd and its affiliates — proprietary source code
        </h2>
        <p style={{ fontSize: '12px', color: '#64748B', lineHeight: 1.6, marginBottom: '14px' }}>
          The Rosetta IMS repository contains the proprietary source code, schema, and business logic of
          Algo Technologies Pte Ltd and its affiliates. Submitting this form sends a request email to Chris
          (chris@algogroup.io) — your email is set as Reply-To so when Chris replies, it lands in your inbox.
          The submission is also stored in our audit log. Chris will then grant GitHub access manually — you&apos;ll
          receive a GitHub invitation by email when approved.
        </p>

        <p style={{ fontSize: '10px', fontWeight: 700, color: '#6366F1', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>
          Tick all 4 boxes — {checkedCount}/4 ticked
        </p>
        <div style={{ background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '14px 16px', marginBottom: '14px' }}>
          <Check checked={c1} onChange={setC1}>
            <strong>Confidentiality.</strong> The Rosetta IMS source code, schema, business logic, and any
            derivative work or output are confidential and proprietary to Algo Technologies Pte Ltd and its
            affiliates. I will not share, publish, or disclose them to any third party.
          </Check>
          <Check checked={c2} onChange={setC2}>
            <strong>Scope of use.</strong> I will use this code solely for work explicitly agreed with
            Algo Technologies Pte Ltd and its affiliates. I will not use, reuse, port, or adapt this code
            for any other company, client, project, or personal purpose without prior written consent from
            Algo Technologies Pte Ltd and its affiliates.
          </Check>
          <Check checked={c3} onChange={setC3}>
            <strong>Termination.</strong> Upon termination of my engagement with Algo Technologies Pte Ltd
            and its affiliates, I will delete all local copies of the code and revoke any access tokens I
            have been granted.
          </Check>
          <Check checked={c4} onChange={setC4}>
            <strong>Breach &amp; damages.</strong> I understand that any breach of the above may give rise
            to monetary damages, injunctive relief, and other remedies available to Algo Technologies Pte Ltd
            and its affiliates under applicable law.
          </Check>
        </div>

        <Label>Your GitHub username</Label>
        <input
          value={github}
          onChange={(e) => setGithub(e.target.value)}
          placeholder="e.g. desmondbrown"
          autoComplete="off"
          style={inputStyle}
        />

        <Label>Your full name (typed signature)</Label>
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Full name as on your contract"
          autoComplete="off"
          style={inputStyle}
        />

        <Label>Your email (Chris replies from here)</Label>
        <input
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          type="email"
          placeholder="name@company.com"
          autoComplete="email"
          style={inputStyle}
        />

        {error && (
          <p style={{ fontSize: '11px', color: '#991B1B', background: '#FEF2F2', border: '1px solid #FECACA', padding: '8px 12px', borderRadius: '6px', marginBottom: '12px' }}>
            {error}
          </p>
        )}

        {/* "what's still needed" helper — shown when submit is blocked */}
        {issues.length > 0 && !submitting && (
          <p style={{
            fontSize: '11.5px', color: '#92400E',
            background: '#FFFBEB', border: '1px solid #FDE68A',
            padding: '8px 12px', borderRadius: '6px', marginBottom: '12px',
            lineHeight: 1.5,
          }}>
            <strong>Still needed to enable submit:</strong> {issues.join(' · ')}
          </p>
        )}

        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end', marginTop: '16px' }}>
          <button
            onClick={onClose}
            disabled={submitting}
            style={{
              background: 'transparent', color: '#64748B',
              border: '1px solid #E2E8F0', borderRadius: '6px',
              padding: '8px 16px', fontSize: '12px', fontWeight: 600,
              cursor: submitting ? 'not-allowed' : 'pointer',
            }}
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={!canSubmit}
            title={issues.length > 0 ? `Still needed: ${issues.join('; ')}` : ''}
            style={{
              background: canSubmit ? '#0F172A' : '#CBD5E1', color: 'white',
              border: 'none', borderRadius: '6px',
              padding: '8px 16px', fontSize: '12px', fontWeight: 600,
              cursor: canSubmit ? 'pointer' : 'not-allowed',
            }}
          >
            {submitting ? 'Sending request…' :
             canSubmit ? 'I acknowledge — submit request' :
             `I acknowledge — submit request (${issues.length} thing${issues.length === 1 ? '' : 's'} left)`}
          </button>
        </div>

        <p style={{ fontSize: '10px', color: '#94A3B8', marginTop: '12px', lineHeight: 1.5, fontStyle: 'italic' }}>
          Click-wrap acceptance is legally binding under HK Electronic Transactions Ordinance and equivalent
          laws. The record is preserved in our audit log, in Chris&apos;s inbox, and in your inbox.
        </p>
      </div>
    </div>
  )
}

const inputStyle: React.CSSProperties = {
  width: '100%', fontSize: '12px', padding: '8px 12px',
  border: '1px solid #CBD5E1', borderRadius: '6px',
  marginBottom: '12px', fontFamily: 'inherit',
  boxSizing: 'border-box',
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <p style={{ fontSize: '10px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '4px' }}>
      {children}
    </p>
  )
}

function Check({ checked, onChange, children }: { checked: boolean; onChange: (v: boolean) => void; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', gap: '10px', alignItems: 'flex-start', padding: '6px 0', cursor: 'pointer' }}>
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        style={{ marginTop: '3px', flexShrink: 0, width: '14px', height: '14px', cursor: 'pointer' }}
      />
      <span style={{ fontSize: '12px', color: '#0F172A', lineHeight: 1.55 }}>{children}</span>
    </label>
  )
}

// ─── Admin-only paper-trail log ──────────────────────────────────────────────

function AcknowledgementLog() {
  const [user, setUser] = useState<IMSUser | null>(null)
  const [acks, setAcks] = useState<AccessAcknowledgement[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const u = getUser()
    setUser(u)
    if (u?.role !== 'admin') return
    listAcknowledgements()
      .then(r => setAcks(r.acknowledgements))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load log'))
  }, [])

  if (user?.role !== 'admin') return null

  return (
    <div style={{ marginTop: '24px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '16px 18px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
        <p style={{ fontSize: '11px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.08em', margin: 0 }}>
          🔐 Acknowledgement log (admin only)
        </p>
        <span style={{ fontSize: '10px', color: '#64748B' }}>
          {acks?.length ?? '—'} record{acks?.length === 1 ? '' : 's'}
        </span>
      </div>
      {error && (
        <p style={{ fontSize: '11px', color: '#991B1B', background: '#FEF2F2', border: '1px solid #FECACA', padding: '8px 10px', borderRadius: '6px' }}>{error}</p>
      )}
      {acks && acks.length === 0 && (
        <p style={{ fontSize: '12px', color: '#94A3B8', fontStyle: 'italic' }}>No acknowledgements recorded yet. As soon as a non-admin user clicks through, they appear here.</p>
      )}
      {acks && acks.length > 0 && (
        <div style={{ border: '1px solid #E2E8F0', borderRadius: '6px', overflow: 'hidden' }}>
          <div style={{ display: 'grid', gridTemplateColumns: '150px 140px 140px 180px 90px 1fr', gap: '8px', padding: '8px 12px', background: '#F8FAFC', fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.06em', borderBottom: '1px solid #E2E8F0' }}>
            <span>Name (typed)</span>
            <span>IMS user</span>
            <span>GitHub user</span>
            <span>Email (Reply-To)</span>
            <span>Email sent?</span>
            <span>Accepted at · IP</span>
          </div>
          {acks.map((a) => (
            <div key={a.id} style={{ borderBottom: '1px solid #F1F5F9' }}>
              <div style={{
                display: 'grid', gridTemplateColumns: '150px 140px 140px 180px 90px 1fr',
                gap: '8px', padding: '8px 12px',
                fontSize: '11px', color: '#0F172A', alignItems: 'center',
              }}>
                <span style={{ fontWeight: 600 }}>{a.full_name_typed}</span>
                <span style={{ color: '#475569' }}>{a.user_display ?? `user #${a.user_id}`}</span>
                <code style={{ color: '#4338CA', fontSize: '10.5px' }}>@{a.github_username}</code>
                <span style={{ color: '#475569', fontSize: '10.5px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{a.email_requestor ?? '—'}</span>
                <span style={{ fontSize: '10.5px', color: a.email_sent_at ? '#166534' : (a.email_send_error ? '#991B1B' : '#94A3B8') }}>
                  {a.email_sent_at ? '✓ sent' : (a.email_send_error ? '✗ failed' : '—')}
                </span>
                <span style={{ color: '#64748B', fontSize: '10.5px' }}>
                  {a.accepted_at.slice(0, 16).replace('T', ' ')} {a.ip_address ? `· ${a.ip_address}` : ''}
                </span>
              </div>
              {a.email_send_error && (
                <div style={{
                  margin: '0 12px 8px 162px',
                  padding: '6px 10px',
                  background: '#FEF2F2',
                  border: '1px solid #FECACA',
                  borderRadius: '4px',
                  fontSize: '10.5px',
                  color: '#991B1B',
                  fontFamily: 'ui-monospace, monospace',
                  lineHeight: 1.5,
                  wordBreak: 'break-word',
                }}>
                  <strong>Email error:</strong> {a.email_send_error}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <p style={{ fontSize: '10px', color: '#94A3B8', marginTop: '8px', lineHeight: 1.5, fontStyle: 'italic' }}>
        Records cannot be deleted from the UI (audit integrity). To purge, manually delete from <code>access_acknowledgements</code> in the database.
      </p>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '22px' }}>
      <p style={{ fontSize: '11px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '10px' }}>
        {title}
      </p>
      {children}
    </div>
  )
}

function DirCard({ title, subtitle, items }: { title: string; subtitle: string; items: [string, string][] }) {
  return (
    <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '14px' }}>
      <p style={{ fontSize: '13px', fontWeight: 700, color: '#0F172A', fontFamily: 'ui-monospace, monospace', marginBottom: '2px' }}>
        {title}
      </p>
      <p style={{ fontSize: '10.5px', color: '#94A3B8', marginBottom: '10px' }}>{subtitle}</p>
      {items.map(([path, desc]) => (
        <div key={path} style={{ display: 'grid', gridTemplateColumns: '190px 1fr', gap: '8px', padding: '4px 0', fontSize: '11px' }}>
          <code style={{ color: '#4338CA', fontFamily: 'ui-monospace, monospace', fontSize: '11px' }}>{path}</code>
          <span style={{ color: '#64748B', lineHeight: 1.5 }}>{desc}</span>
        </div>
      ))}
    </div>
  )
}

function RepoLink({ href, title, desc, monoUrl }: { href: string; title: string; desc: string; monoUrl?: string }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noreferrer"
      className="ts-link"
      style={{
        display: 'block', background: 'white', border: '1px solid #E2E8F0',
        borderRadius: '6px', padding: '10px 14px', textDecoration: 'none',
        transition: 'all 0.1s',
      }}
    >
      <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginBottom: '3px' }}>
        <span style={{ fontSize: '12px', fontWeight: 700, color: '#0F172A' }}>{title}</span>
        <span style={{ fontSize: '10px', color: '#6366F1' }}>↗</span>
      </div>
      <p style={{ fontSize: '11px', color: '#64748B', margin: 0, lineHeight: 1.5 }}>{desc}</p>
      {monoUrl && (
        <p style={{ fontSize: '10.5px', color: '#94A3B8', margin: '4px 0 0 0', fontFamily: 'ui-monospace, monospace' }}>{monoUrl}</p>
      )}
    </a>
  )
}

function InAppLink({ href, title, desc }: { href: string; title: string; desc: string }) {
  return (
    <a
      href={href}
      className="ts-link"
      style={{
        display: 'block', background: 'white', border: '1px solid #E2E8F0',
        borderRadius: '6px', padding: '10px 14px', textDecoration: 'none',
        transition: 'all 0.1s',
      }}
    >
      <p style={{ fontSize: '12px', fontWeight: 700, color: '#0F172A', marginBottom: '3px' }}>{title} →</p>
      <p style={{ fontSize: '11px', color: '#64748B', margin: 0, lineHeight: 1.45 }}>{desc}</p>
    </a>
  )
}

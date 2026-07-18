import { C } from '@/lib/tokens'
import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState, useCallback } from 'react'
import { authHeaders } from '@/lib/auth'
import { GuideContent } from '@/components/GuideContent'
import { API_BASE } from '@/lib/config'

export const Route = createFileRoute('/_authed/clients/')({ component: ClientbasePage })

const API = API_BASE

interface Tag { kind: string; main: string; sub: string; count: number; sources: string[] }
interface HistItem { source: string; date: string; dx: string; note: string }
interface Pet { pet_id: string; name: string; species?: string; breed?: string; summary?: string }
interface Row {
  customer_id: string; owner: string; email?: string; phone?: string; sources?: string[]; segment?: string
  last_visit: string; visit_count: number; pet_count: number; pets: Pet[]
  ltv?: number; order_count?: number; ext_tags?: string
  care_mains?: string[]; recommend_count?: number; crm_lists?: string[]; consent?: boolean
  top_reco?: { name: string; main: string } | null
  cs_contact?: { channel: string; last_contact: string; msg_count: number; sentiment?: string; quote?: string | null } | null
  unfulfilled?: { count: number; oldest: string } | null
  first_purchase?: string | null; last_purchase?: string | null; bought_rx?: boolean
  clinic_ltv?: number | null; shopify_ltv?: number | null; last_clinic?: string | null; last_shopify?: string | null
  reach?: { email: boolean; whatsapp: boolean; meta: boolean }
  crm?: { last_email?: string | null; last_whatsapp?: string | null; lists?: string[]; whatsapp_optin?: boolean; flows?: { flow: string; sends: number; last: string }[]; discounts?: { code: string; n: number; last: string }[]; first_landing?: string | null; first_landing_path?: string | null; first_referral?: string | null; first_utm_campaign?: string | null; first_utm_source?: string | null; first_utm_medium?: string | null }
  recent_purchases?: { date: string; product: string; source: string; category: string; on_shopify: boolean }[]
  recent_clinic?: { date: string; product: string; source: string; category: string; on_shopify: boolean }[]
  recent_online?: { date: string; product: string; source: string; category: string; on_shopify: boolean }[]
  purchase_cats?: string[]
  care: Tag[]; events: Tag[]; engagement: Tag[]
}
interface Rec { name: string; brand?: string; main: string; sub: string; hero: boolean }

function RecommendBlock({ customerId }: { customerId: string }) {
  const [rec, setRec] = useState<Rec[] | null>(null)
  useEffect(() => {
    fetch(`${API}/clients/${customerId}/recommend`, { headers: authHeaders() })
      .then(x => (x.ok ? x.json() : { recommend: [] })).then(d => setRec(d.recommend || [])).catch(() => setRec([]))
  }, [customerId])
  if (rec === null) return <p style={{ fontSize: '11px', color: C.knobOff, margin: '6px 0' }}>Loading recommendations…</p>
  if (!rec.length) return null
  const byMain: Record<string, Rec[]> = {}
  rec.forEach(p => { (byMain[p.main] = byMain[p.main] || []).push(p) })
  return (
    <div style={{ marginTop: '10px', background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: '6px', padding: '8px 10px' }}>
      <div style={{ fontSize: '9px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: '#16A34A', marginBottom: '5px' }}>
        🎯 Recommend / retarget — PetProject products matching this client&apos;s care profile
      </div>
      {Object.entries(byMain).map(([m, prods]) => {
        const [, fg] = colorFor(m)
        return (
          <div key={m} style={{ marginBottom: '4px', fontSize: '11px' }}>
            <span style={{ fontWeight: 700, color: fg }}>{m}: </span>
            <span style={{ color: C.ink }}>
              {prods.slice(0, 6).map((p, i) => <span key={i}>{i > 0 ? ' · ' : ''}{p.hero ? '★ ' : ''}{p.name.length > 46 ? p.name.slice(0, 46) + '…' : p.name}</span>)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

// provenance segment -> [label, text colour, bg colour]
const SEGMENTS: Record<string, [string, string, string]> = {
  'new': ['🆕 New', '#0D9488', '#CCFBF1'],
  'legacy_active': ['🔄 Returned', '#3730A3', '#E0E7FF'],
  'legacy_dormant': ["💎 Dr Hugh's · dormant", '#6B21A8', '#F3E8FF'],
  'registered': ['Registered', C.muted, C.monoBg],
}
function SegmentBadge({ seg }: { seg?: string }) {
  const [label, fg, bg] = SEGMENTS[seg || ''] ?? ['—', C.faint, C.monoBg]
  return <span style={{ fontSize: '9px', fontWeight: 700, color: fg, background: bg, padding: '1px 7px', borderRadius: '8px' }}>{label}</span>
}

// source -> [label, text colour, bg colour] for the clear per-row pills
const SRC_FULL: Record<string, [string, string, string]> = {
  'DaySmart': ['Clinic', '#0D9488', '#CCFBF1'],
  'CHS': ["Dr Hugh's", '#6B21A8', '#F3E8FF'],
  'Shopify': ['Online', C.green, C.greenBg],
  'Klaviyo': ['CRM', C.amberInk, C.warnBg],
}
function SourceTags({ sources }: { sources?: string[] }) {
  const s = sources || []
  if (!s.length) return <span style={{ fontSize: '10px', color: C.knobOff }}>—</span>
  const overlap = s.length >= 2   // present in 2+ channels
  return (
    <div style={{ display: 'flex', gap: '3px', flexWrap: 'wrap', marginTop: '3px', alignItems: 'center' }}>
      {overlap && <span style={{ fontSize: '9px', fontWeight: 700, color: 'white', background: C.indigo, padding: '1px 6px', borderRadius: '8px' }}>🔗 OVERLAP ×{s.length}</span>}
      {s.map(x => {
        const [label, fg, bg] = SRC_FULL[x] ?? [x, C.sub, C.monoBg]
        return <span key={x} style={{ fontSize: '9px', fontWeight: 700, color: fg, background: bg, padding: '1px 6px', borderRadius: '8px' }}>{label}</span>
      })}
    </div>
  )
}
interface Summary { customers: number; pets: number; by_main: Record<string, number>; by_segment?: Record<string, number>; reco_products?: { main: string; product: string; n: number }[]; ops_counts?: Record<string, number>; by_purchase_cat?: Record<string, number>; top_vet_products?: { product: string; n: number }[]; filters?: { cust?: Record<string, number>; consents?: Record<string, number>; ops?: Record<string, number> }; crm_lists?: { name: string; n: number }[]; crm_flows?: { name: string; n: number }[]; crm_discounts?: { code: string; n: number }[]; utm_campaigns?: { campaign: string; source: string; medium: string; n: number }[]; landing_pages?: { path: string; n: number }[]; referrals?: { domain: string; n: number }[] }

// source -> [letter, colour]
const SRC: Record<string, [string, string]> = {
  'DaySmart': ['D', '#0D9488'], 'Shopify': ['S', '#16A34A'], 'CHS': ['C', '#6B21A8'], 'Klaviyo': ['K', '#D97706'],
}
const MAIN_COLORS: Record<string, [string, string]> = {
  'Preventative': [C.greenBg, C.green], 'Skin & Coat': [C.redBg, C.redInk], 'Digestive': [C.warnBg, C.amberInk],
  'Eyes & Ears': ['#DBEAFE', '#1E40AF'], 'Dental': ['#F3E8FF', '#6B21A8'], 'Respiratory': ['#CFFAFE', '#155E75'],
  'Urinary & Renal': ['#FCE7F3', '#9D174D'], 'Mobility': ['#FEF9C3', '#854D0E'], 'Heart': ['#FFE4E6', '#9F1239'],
  'Endocrine': ['#FAE8FF', '#86198F'], 'Neurological': ['#E0E7FF', '#3730A3'],
  'Weight & Nutrition': ['#ECFCCB', '#3F6212'], 'Behaviour': ['#E0E7FF', '#3730A3'], 'Cat-specific': [C.monoBg, C.sub],
}
const colorFor = (m: string): [string, string] => MAIN_COLORS[m] ?? [C.monoBg, C.sub]

function Chip({ tag }: { tag: Tag }) {
  const [bg, fg] = colorFor(tag.main)
  const overlap = (tag.sources?.length ?? 0) > 1
  return (
    <span title={`${tag.main} · seen in: ${(tag.sources || []).join(', ') || '—'}`}
      style={{ background: bg, color: fg, fontSize: '10px', fontWeight: 600, padding: '2px 6px', borderRadius: '10px',
        whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: '4px',
        border: overlap ? `1.5px solid ${fg}` : '1.5px solid transparent' }}>
      {tag.sub}{tag.count > 1 ? ` ·${tag.count}` : ''}
      <span style={{ display: 'inline-flex', gap: '2px' }}>
        {(tag.sources || []).map(s => {
          const [ltr, col] = SRC[s] ?? ['?', C.faint]
          return <span key={s} title={s} style={{ fontSize: '8px', fontWeight: 700, color: 'white', background: col,
            borderRadius: '50%', width: '11px', height: '11px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', lineHeight: 1 }}>{ltr}</span>
        })}
      </span>
    </span>
  )
}

function PetBlock({ pet }: { pet: Pet }) {
  const [hist, setHist] = useState<HistItem[] | null>(null)
  useEffect(() => {
    fetch(`${API}/clients/${pet.pet_id}/history`, { headers: authHeaders() })
      .then(x => (x.ok ? x.json() : { history: [] })).then(d => setHist(d.history || [])).catch(() => setHist([]))
  }, [pet.pet_id])
  const sub = [pet.species, pet.breed].filter(Boolean).join(' · ')
  return (
    <div style={{ marginTop: '8px', paddingLeft: '10px', borderLeft: '2px solid #E2E8F0' }}>
      <div style={{ fontSize: '12px', fontWeight: 600, color: C.ink }}>
        🐾 {pet.name || '(unnamed)'}{sub ? <span style={{ color: C.faint, fontWeight: 400 }}> · {sub}</span> : null}
      </div>
      {pet.summary && <div style={{ fontSize: '11px', color: '#3730A3', background: C.primaryBg, borderRadius: '5px', padding: '5px 8px', marginTop: '4px' }}>{pet.summary}</div>}
      {hist === null ? <p style={{ fontSize: '11px', color: C.knobOff, margin: '4px 0' }}>Loading timeline…</p>
        : hist.length === 0 ? null
        : <div style={{ marginTop: '4px', maxHeight: '180px', overflowY: 'auto' }}>
            {hist.map((h, i) => {
              const [ltr, col] = SRC[h.source] ?? ['?', C.faint]
              return (
                <div key={i} style={{ fontSize: '11px', marginBottom: '3px', lineHeight: 1.4 }}>
                  <span style={{ fontVariantNumeric: 'tabular-nums', color: C.faint }}>{h.date || '—'}</span>{' '}
                  <span title={h.source} style={{ fontSize: '8px', fontWeight: 700, color: 'white', background: col, borderRadius: '3px', padding: '0 3px' }}>{ltr}</span>{' '}
                  <span style={{ color: C.ink, fontWeight: 600 }}>{h.dx}</span>
                  {h.note ? <span style={{ color: C.muted }}> — {h.note}</span> : null}
                </div>
              )
            })}
          </div>}
    </div>
  )
}

function Section({ label, tags }: { label: string; tags: Tag[] }) {
  if (!tags.length) return null
  return (
    <div style={{ marginTop: '8px' }}>
      <span style={{ fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{label}</span>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px', marginTop: '4px' }}>{tags.map((t, i) => <Chip key={i} tag={t} />)}</div>
    </div>
  )
}

function DetailPanel({ r }: { r: Row }) {
  const flags = (r.ext_tags || '').split(',').map(s => s.trim())
    .filter(t => /vip|appstle_active|active_subscriber|has active subscription|autoship/i.test(t))
  return (
    <div>
      <div style={{ fontSize: '12px', color: C.muted, display: 'flex', gap: '14px', flexWrap: 'wrap' }}>
        <span>{r.email ? `✉ ${r.email}` : ''} {r.phone ? `☎ ${r.phone}` : ''}</span>
        <span>{r.visit_count} clinic visits · last {r.last_visit || '—'}</span>
        <span style={{ color: '#0D9488', fontWeight: 600 }}>🏥 Clinic LTV HK${Math.round(r.clinic_ltv || 0).toLocaleString()}</span>
        <span style={{ color: C.green, fontWeight: 600 }}>🛒 Online LTV HK${Math.round(r.shopify_ltv || 0).toLocaleString()} · {r.order_count || 0} orders</span>
      </div>
      <div style={{ fontSize: '11px', color: C.muted, display: 'flex', gap: '12px', flexWrap: 'wrap', marginTop: '4px' }}>
        <span style={{ fontWeight: 600 }}>Reachable:</span>
        <span style={{ color: r.reach?.email ? '#16A34A' : C.knobOff }}>✉ Email {r.reach?.email ? '✓' : '✗'}</span>
        <span style={{ color: r.reach?.whatsapp ? '#16A34A' : C.knobOff }}>💬 WhatsApp {r.reach?.whatsapp ? '✓' : '✗'}</span>
        <span style={{ color: r.reach?.meta ? '#16A34A' : C.knobOff }}>📣 Meta {r.reach?.meta ? '✓' : '✗'}</span>
      </div>
      {flags.length ? <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', marginTop: '5px' }}>
        {flags.map((f, i) => <span key={i} style={{ fontSize: '9px', fontWeight: 700, color: C.amberInk, background: C.warnBg, padding: '1px 6px', borderRadius: '8px' }}>{f}</span>)}
      </div> : null}
      {r.crm_lists && r.crm_lists.length ? <div style={{ marginTop: '6px', fontSize: '11px', color: C.amberInk, background: C.warnBg, borderRadius: '5px', padding: '5px 8px' }}>
        📋 Joined CRM: {r.crm_lists.join(' · ')}
      </div> : null}
      {(r.crm?.last_email || r.crm?.last_whatsapp) ? <div style={{ marginTop: '5px', fontSize: '11px', color: '#0E7490' }}>
        ✉ last emailed {r.crm?.last_email || '—'} · 💬 last WhatsApp {r.crm?.last_whatsapp || '—'}
      </div> : null}
      {r.crm?.flows && r.crm.flows.length ? <div style={{ marginTop: '6px', background: '#F5F3FF', border: '1px solid #DDD6FE', borderRadius: '5px', padding: '5px 8px' }}>
        <div style={{ fontSize: '9px', fontWeight: 700, textTransform: 'uppercase', color: '#7C3AED', marginBottom: '3px' }}>🔁 Klaviyo flows received (frequency)</div>
        {r.crm.flows.map((f, i) => (
          <div key={i} style={{ fontSize: '11px', color: C.sub }}>
            <span style={{ color: C.faint, fontVariantNumeric: 'tabular-nums' }}>{f.last}</span> · {f.flow} <span style={{ color: C.faint }}>({f.sends}×)</span>
          </div>
        ))}
      </div> : null}
      {r.crm?.discounts && r.crm.discounts.length ? <div style={{ marginTop: '5px', fontSize: '11px', color: C.amber }}>
        🎟 Claimed: {r.crm.discounts.map(d => `${d.code} (${d.last})`).join(' · ')}
      </div> : null}
      {(r.crm?.first_utm_campaign || r.crm?.first_landing_path || r.crm?.first_referral) ? <div style={{ marginTop: '5px', fontSize: '11px', color: '#0F766E' }}>
        🔗 First touch:
        {r.crm?.first_utm_campaign ? <span> 🎯 <b>{r.crm.first_utm_campaign}</b>{r.crm.first_utm_source ? <span style={{ color: C.faint }}> ({r.crm.first_utm_source}/{r.crm.first_utm_medium})</span> : null}</span> : null}
        {r.crm?.first_landing_path ? <span style={{ color: C.sub }}> · landed {r.crm.first_landing_path}</span> : null}
        {r.crm?.first_referral ? <span> · via <b>{r.crm.first_referral}</b></span> : null}
      </div> : null}
      {r.cs_contact?.sentiment ? (() => {
        const s = r.cs_contact.sentiment
        const col = s === 'poor' ? '#DC2626' : s === 'happy' ? '#16A34A' : C.muted
        const lbl = s === 'poor' ? 'Poor' : s === 'happy' ? 'Happy' : 'Fine'
        return (
          <div style={{ marginTop: '6px', fontSize: '11px', background: s === 'poor' ? C.badBg : C.wash, border: `1px solid ${col}55`, borderRadius: '5px', padding: '5px 8px' }}>
            <span style={{ color: col, fontWeight: 700 }}>🎧 CS sentiment: {lbl}</span>
            <span style={{ color: C.faint }}> · last WhatsApp {r.cs_contact.last_contact || '—'}</span>
            {r.cs_contact.quote ? <div style={{ color: C.sub, fontStyle: 'italic', marginTop: '3px' }}>“{r.cs_contact.quote}”</div>
              : <div style={{ color: C.knobOff, marginTop: '3px' }}>(re-run sentiment ingest to capture the triggering line)</div>}
          </div>
        )
      })() : null}
      {(r.recent_clinic?.length || r.recent_online?.length) ? (
        <div style={{ marginTop: '10px' }}>
          <div style={{ fontSize: '9px', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.04em', color: C.muted, marginBottom: '5px' }}>
            🧾 Purchase history · first {r.first_purchase || '—'} → last {r.last_purchase || '—'}{r.bought_rx ? ' · has bought Rx' : ''}
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px' }}>
            {[{ t: '🏥 Clinic (Dr Hugh + Ohana)', list: r.recent_clinic || [], bg: '#F0FDFA', bd: '#99F6E4' },
              { t: '🛒 Online (Shopify)', list: r.recent_online || [], bg: '#F0FDF4', bd: '#BBF7D0' }].map((col, ci) => (
              <div key={ci} style={{ background: col.bg, border: `1px solid ${col.bd}`, borderRadius: '6px', padding: '8px 10px' }}>
                <div style={{ fontSize: '10px', fontWeight: 700, color: C.sub, marginBottom: '4px' }}>{col.t}</div>
                {col.list.length ? col.list.map((p, i) => (
                  <div key={i} style={{ fontSize: '11px', marginBottom: '2px' }}>
                    <span style={{ color: C.faint, fontVariantNumeric: 'tabular-nums' }}>{p.date}</span>{' · '}
                    <span style={{ color: C.ink }}>{p.product.length > 40 ? p.product.slice(0, 40) + '…' : p.product}</span>{' '}
                    <span style={{ color: C.faint }}>({p.category}{p.on_shopify ? ' · 🛒' : ''})</span>
                  </div>
                )) : <div style={{ fontSize: '11px', color: C.knobOff }}>— none —</div>}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      <Section label="Care types (all sources)" tags={r.care} />
      <RecommendBlock customerId={r.customer_id} />
      <Section label="Events" tags={r.events} />
      <Section label="Engagement / ops" tags={r.engagement} />
      {r.pets.length ? r.pets.map(p => <PetBlock key={p.pet_id} pet={p} />)
        : <p style={{ fontSize: '11px', color: C.faint, marginTop: '8px', fontStyle: 'italic' }}>No pet on file — online/transacted customer (still targetable for marketing).</p>}
    </div>
  )
}

const CATCOL: Record<string, [string, string]> = {
  'Medicine': [C.redBg, C.redInk], 'Preventative': [C.greenBg, C.green], 'Prescription Diet': [C.warnBg, C.amberInk],
  'Supplement': ['#EDE9FE', '#6D28D9'], 'Food': ['#E0F2FE', '#075985'], 'Pet Hygiene': ['#FCE7F3', '#9D174F'],
}
interface DemandProd { product: string; category: string; n: number; sku: string; clinic_clients: number; online_clients: number; clinic_units: number; online_units: number; clinic_ltv: number; online_ltv: number; autoship_clients: number; names: number }
interface DemandData { cohort_size: number; by_cat: { category: string; n: number }[]; top_products: DemandProd[] }
type DKey = 'product' | 'sku' | 'category' | 'n' | 'clinic_units' | 'clinic_ltv' | 'online_units' | 'online_ltv' | 'autoship_clients'
function DemandPanel({ onProduct }: { onProduct: (p: string) => void }) {
  const [data, setData] = useState<DemandData | null>(null)
  const [catF, setCatF] = useState<string>('')
  const [sk, setSk] = useState<DKey>('n')
  const [dir, setDir] = useState<'asc' | 'desc'>('desc')
  useEffect(() => {
    fetch(`${API}/clients/demand`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : null)).then(setData).catch(() => setData(null))
  }, [])
  if (!data) return <div style={{ padding: '40px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>Counting product demand…</div>
  const cc = (c: string): [string, string] => CATCOL[c] || [C.monoBg, C.sub]
  const CLINIC = '#0D9488', ONLINE = '#2563EB'
  const sort = (k: DKey) => { if (sk === k) setDir(d => d === 'desc' ? 'asc' : 'desc'); else { setSk(k); setDir('desc') } }
  const arrow = (k: DKey) => sk === k ? (dir === 'desc' ? ' ▼' : ' ▲') : ''
  let rows = data.top_products.filter(p => !catF || p.category === catF)
  rows = [...rows].sort((a, b) => {
    const av = a[sk], bv = b[sk]
    if (typeof av === 'string') return dir === 'asc' ? av.localeCompare(bv as string) : (bv as string).localeCompare(av)
    return dir === 'asc' ? (av as number) - (bv as number) : (bv as number) - (av as number)
  })
  const money = (units: number, ltv: number) => ltv ? '$' + ltv.toLocaleString() : (units ? '—' : '')
  const cols = '2.3fr 0.85fr 1fr 0.62fr 0.95fr 0.95fr 0.7fr 0.72fr'
  const th: React.CSSProperties = { fontSize: '10px', fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.03em', cursor: 'pointer', userSelect: 'none' }
  const ar = (k: DKey): React.CSSProperties => ({ ...th, textAlign: 'right', color: sk === k ? C.indigo : C.muted })
  return (
    <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '14px 16px' }}>
      <div style={{ fontSize: '14px', fontWeight: 800, color: C.ink, display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>📊 Demand breakdown — every product transacted ({data.cohort_size.toLocaleString()} customers)
        <span title="Products are rolled up by an interim name-matcher and clinic $ is indicative — the canonical SKU + cost + margin arrive with Rosetta IMS / OCR." style={{ fontSize: '10px', fontWeight: 700, color: C.amberInk, background: C.warnBg, border: '1px solid #FDE68A', borderRadius: '8px', padding: '2px 8px' }}>⏳ interim — awaiting Rosetta IMS / OCR</span>
      </div>
      <div style={{ fontSize: '11px', color: C.muted, margin: '2px 0 10px' }}>
        Products ranked by clients & revenue, split <b style={{ color: CLINIC }}>🏥 clinic</b> vs <b style={{ color: ONLINE }}>🛒 online</b>. Click a column header to sort; click a product to see its buyers.
        <span style={{ color: C.faint }}> “—” = price not captured (Dr Hugh legacy / pre-OCR). Margin lands with IMS/OCR cost data.</span>
      </div>
      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '10px' }}>
        <button onClick={() => setCatF('')} style={{ fontSize: '11px', fontWeight: 700, padding: '3px 10px', borderRadius: '13px', cursor: 'pointer', border: `1px solid ${!catF ? C.indigo : C.line}`, background: !catF ? C.indigo : 'white', color: !catF ? 'white' : C.sub }}>All</button>
        {data.by_cat.map(c => {
          const [bg, fg] = cc(c.category); const on = catF === c.category
          return <button key={c.category} onClick={() => setCatF(on ? '' : c.category)}
            style={{ fontSize: '11px', fontWeight: 700, padding: '3px 10px', borderRadius: '13px', cursor: 'pointer', border: `1px solid ${on ? fg : C.line}`, background: on ? fg : bg, color: on ? 'white' : fg }}>
            {c.category} · {c.n.toLocaleString()}{on ? ' ✓' : ''}
          </button>
        })}
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: cols, gap: '8px', padding: '6px 8px', borderBottom: '2px solid #E2E8F0' }}>
        <div style={{ ...th, color: sk === 'product' ? C.indigo : C.muted }} onClick={() => sort('product')}>Product{arrow('product')}</div>
        <div style={{ ...th, color: sk === 'sku' ? C.indigo : C.muted }} onClick={() => sort('sku')}>SKU{arrow('sku')}</div>
        <div style={{ ...th, color: sk === 'category' ? C.indigo : C.muted }} onClick={() => sort('category')}>Category{arrow('category')}</div>
        <div style={ar('n')} onClick={() => sort('n')}>Clients{arrow('n')}</div>
        <div style={ar('clinic_ltv')} onClick={() => sort('clinic_ltv')}>🏥 Clinic ${arrow('clinic_ltv')}</div>
        <div style={ar('online_ltv')} onClick={() => sort('online_ltv')}>🛒 Online ${arrow('online_ltv')}</div>
        <div style={ar('autoship_clients')} onClick={() => sort('autoship_clients')}>🔁 Autoship{arrow('autoship_clients')}</div>
        <div style={ar('online_ltv')} title="Profit margin — pending IMS/OCR cost data">Margin</div>
      </div>
      <div style={{ maxHeight: '64vh', overflowY: 'auto' }}>
        {rows.map(p => {
          const [bg, fg] = cc(p.category)
          return (
            <div key={p.product} onClick={() => onProduct(p.product)} title="click to see buyers in the client list"
              style={{ display: 'grid', gridTemplateColumns: cols, gap: '8px', padding: '7px 8px', borderBottom: '1px solid #F1F5F9', alignItems: 'center', cursor: 'pointer' }}>
              <div style={{ fontSize: '12px', fontWeight: 600, color: C.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={p.product}>{p.product}{p.names > 1 ? <span style={{ fontSize: '9px', color: C.faint, fontWeight: 400 }} title="variants/sources rolled up"> · {p.names} variants</span> : null}</div>
              <div style={{ fontSize: '11px', color: C.faint, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{p.sku || '—'}</div>
              <div><span style={{ fontSize: '10px', fontWeight: 700, color: fg, background: bg, padding: '1px 6px', borderRadius: '8px' }}>{p.category}</span></div>
              <div style={{ fontSize: '13px', fontWeight: 700, color: C.ink, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.n.toLocaleString()}</div>
              <div style={{ fontSize: '12px', color: p.clinic_ltv ? CLINIC : C.knobOff, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(p.clinic_units, p.clinic_ltv)}<div style={{ fontSize: '9px', color: C.faint }}>{p.clinic_units ? `${p.clinic_units.toLocaleString()}u · ${p.clinic_clients}cl` : ''}</div></div>
              <div style={{ fontSize: '12px', color: p.online_ltv ? ONLINE : C.knobOff, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{money(p.online_units, p.online_ltv)}<div style={{ fontSize: '9px', color: C.faint }}>{p.online_units ? `${p.online_units.toLocaleString()}u · ${p.online_clients}cl` : ''}</div></div>
              <div style={{ fontSize: '12px', fontWeight: 700, color: p.autoship_clients ? '#7C3AED' : C.knobOff, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{p.autoship_clients ? p.autoship_clients.toLocaleString() : '—'}</div>
              <div style={{ fontSize: '11px', color: C.knobOff, textAlign: 'right' }}>—</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

interface FunnelData { cohort: number; email: number; whatsapp: number; meta: number; meta_only: number; bought_online: number; returned_clinic: number }
function FunnelBar({ qs, label, onExport }: { qs: string; label: string; onExport: (ch: string) => void }) {
  const [d, setD] = useState<FunnelData | null>(null)
  useEffect(() => {
    setD(null)
    fetch(`${API}/clients/funnel?${qs}`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : null)).then(setD).catch(() => setD(null))
  }, [qs])
  if (!d) return <div style={{ padding: '12px', color: C.faint, fontSize: '13px' }}>Counting…</div>
  const pct = (n: number) => (d.cohort ? Math.round(100 * n / d.cohort) : 0)
  const cards = [
    { icon: '✉', label: 'Email-reachable', sub: 'Klaviyo CRM', val: d.email, ch: 'email', color: C.amberInk, bg: C.warnBg },
    { icon: '💬', label: 'WhatsApp-reachable', sub: 'ChatArchitect blast', val: d.whatsapp, ch: 'whatsapp', color: C.green, bg: C.greenBg },
    { icon: '📣', label: 'Meta / Google audience', sub: 'has contact — ad targeting', val: d.meta, ch: 'meta', color: '#6D28D9', bg: '#EDE9FE' },
  ]
  return (
    <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '12px 14px', marginBottom: '14px' }}>
      <div style={{ fontSize: '13px', color: C.ink, marginBottom: '10px' }}>
        👥 <b style={{ fontSize: '17px', fontVariantNumeric: 'tabular-nums' }}>{d.cohort.toLocaleString()}</b> customers match — <b>{label}</b>. Reach them via:
      </div>
      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
        {cards.map(c => (
          <div key={c.ch} style={{ flex: '1 1 180px', border: `1px solid ${c.color}33`, background: c.bg, borderRadius: '8px', padding: '8px 10px' }}>
            <div style={{ fontSize: '19px', fontWeight: 800, color: C.ink, fontVariantNumeric: 'tabular-nums' }}>{c.val.toLocaleString()} <span style={{ fontSize: '11px', fontWeight: 600, color: c.color }}>({pct(c.val)}%)</span></div>
            <div style={{ fontSize: '11px', fontWeight: 700, color: c.color }}>{c.icon} {c.label}</div>
            <div style={{ fontSize: '10px', color: C.muted, marginBottom: '5px' }}>{c.sub}</div>
            <button onClick={() => onExport(c.ch)} disabled={!c.val} style={{ fontSize: '10px', fontWeight: 700, color: 'white', background: c.color, border: 'none', borderRadius: '5px', padding: '3px 8px', cursor: c.val ? 'pointer' : 'default', opacity: c.val ? 1 : 0.4 }}>⬇ Export list</button>
          </div>
        ))}
      </div>
      <div style={{ fontSize: '11px', color: C.muted, marginTop: '8px' }}>
        Of these — behaviour: 🛒 <b style={{ color: C.ink }}>{d.bought_online.toLocaleString()}</b> bought online · 🏥 <b style={{ color: C.ink }}>{d.returned_clinic.toLocaleString()}</b> visited clinic
      </div>
    </div>
  )
}

interface DDGroup { header?: string; options: { value: string; label: string; n?: number }[] }
function FilterDropdown({ trigger, color, bg, groups, selected, onToggle }: {
  trigger: string; color: string; bg: string; groups: DDGroup[]; selected: string[]; onToggle: (v: string) => void
}) {
  const [open, setOpen] = useState(false)
  const all = groups.flatMap(g => g.options)
  const nSel = all.filter(o => selected.includes(o.value)).length
  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button onClick={() => setOpen(o => !o)}
        style={{ fontSize: '12px', fontWeight: 700, padding: '5px 12px', borderRadius: '13px', cursor: 'pointer',
          border: `${nSel ? 2 : 1}px solid ${nSel ? color : C.knobOff}`, background: nSel ? color : bg, color: nSel ? 'white' : color }}>
        {trigger}{nSel ? ` · ${nSel}` : ''} ▾
      </button>
      {open && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 30 }} />
          <div style={{ position: 'absolute', top: '30px', left: 0, zIndex: 31, background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', boxShadow: '0 8px 22px rgba(0,0,0,0.14)', padding: '5px', minWidth: '220px', maxHeight: '340px', overflowY: 'auto' }}>
            {groups.map((g, gi) => (
              <div key={gi}>
                {g.header && <div style={{ fontSize: '9px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.04em', padding: '6px 7px 2px' }}>{g.header}</div>}
                {g.options.map(o => {
                  const on = selected.includes(o.value)
                  return (
                    <div key={o.value} onClick={() => onToggle(o.value)}
                      style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '5px 7px', borderRadius: '5px', cursor: 'pointer', fontSize: '12px', background: on ? C.primaryBg : 'transparent' }}>
                      <span style={{ width: '13px', color: on ? color : C.knobOff }}>{on ? '☑' : '☐'}</span>
                      <span style={{ flex: 1, color: C.ink }}>{o.label}</span>
                      {o.n != null && <span style={{ color: C.faint, fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>{o.n.toLocaleString()}</span>}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function ChipGroup({ label, options, selected, onToggle, counts, mode, onClear }: {
  label: string; options: [string, string][]; selected: string[]; onToggle: (v: string) => void
  counts?: Record<string, number>; mode: 'all' | 'any'; onClear: () => void
}) {
  return (
    <div style={{ display: 'flex', alignItems: 'baseline', gap: '6px', flexWrap: 'wrap', marginBottom: '7px' }}>
      <span style={{ fontSize: '12px', fontWeight: 700, color: '#334155', width: '108px', flexShrink: 0 }}>{label}</span>
      {options.map(([v, disp]) => {
        const on = selected.includes(v)
        const n = counts?.[v]
        return (
          <button key={v} onClick={() => onToggle(v)}
            style={{ fontSize: '11px', fontWeight: 600, padding: '4px 10px', borderRadius: '13px', cursor: 'pointer',
              border: `1px solid ${on ? C.indigo : C.line}`, background: on ? C.indigo : 'white', color: on ? 'white' : C.sub }}>
            {disp}{n != null ? ` (${n.toLocaleString()})` : ''}{on ? ' ✓' : ''}
          </button>
        )
      })}
      {selected.length > 1 && <span style={{ fontSize: '10px', color: C.indigo, fontWeight: 600 }}>{mode === 'all' ? '= ALL of these' : '= ANY of these'}</span>}
      {selected.length > 0 && <button onClick={onClear} style={{ fontSize: '10px', color: C.faint, background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>clear</button>}
    </div>
  )
}

interface InitBucket { category: string; total_clients: number; clinic_clients: number; online_clients: number; gap: number; reach: { email: number; whatsapp: number; meta: number }; dr_hugh_clients: number; clinic_ltv: number; autoship_clients: number; top_products: { family: string; n: number }[] }
interface InitData { buckets: InitBucket[]; dr_hugh: { total: number; online: number; never_online: number } }
function InitiativesPanel({ onBuild }: { onBuild: (cat: string) => void }) {
  const [d, setD] = useState<InitData | null>(null)
  useEffect(() => { fetch(`${API}/clients/initiatives`, { headers: authHeaders() }).then(r => (r.ok ? r.json() : null)).then(setD).catch(() => setD(null)) }, [])
  if (!d) return <div style={{ padding: '40px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>Analysing demand → ranking campaign opportunities…</div>
  const cc = (c: string): [string, string] => CATCOL[c] || [C.monoBg, C.sub]
  const dh = d.dr_hugh
  const dhpct = dh.total ? Math.round(100 * dh.never_online / dh.total) : 0
  return (
    <div>
      {/* the reframe Angelina's report missed */}
      <div style={{ background: '#FFF7ED', border: '1px solid #FDBA74', borderRadius: '10px', padding: '14px 16px', marginBottom: '16px' }}>
        <div style={{ fontSize: '14px', fontWeight: 800, color: '#9A3412' }}>⚠️ Don’t give up on Dr Hugh’s list — it’s the #1 opportunity</div>
        <div style={{ fontSize: '12px', color: '#7C2D12', marginTop: '4px', lineHeight: 1.5 }}>
          Dr Hugh’s legacy base is <b>{dh.total.toLocaleString()} clients</b>, and <b>{dh.never_online.toLocaleString()} ({dhpct}%) have never bought online</b> — yet they carry massive demonstrated demand in
          <b> Medicines, Preventatives & Prescription Diets</b>. Phase 1 ranked this list as “revisit later” because it judged it on CPL alone, with no product context. The initiatives below rank every bucket by its
          <b> clinic→online conversion gap</b> — the customers who already buy it from us in person and just need to be moved online. Pair each with the proven <b>“Savings — HK$100 off”</b> angle (Phase 1’s clear winner).
        </div>
      </div>
      {d.buckets.map((b, i) => {
        const [bg, fg] = cc(b.category)
        const hooks = b.top_products.map(t => t.family).slice(0, 3)
        return (
          <div key={b.category} style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '14px 16px', marginBottom: '12px' }}>
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '14px' }}>
              <div style={{ fontSize: '22px', fontWeight: 800, color: C.knobOff, width: '28px' }}>{i + 1}</div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '15px', fontWeight: 800, color: C.ink }}>
                  Move <span style={{ color: fg, background: bg, padding: '1px 8px', borderRadius: '8px' }}>{b.category}</span> demand online
                </div>
                <div style={{ fontSize: '13px', color: '#334155', margin: '6px 0' }}>
                  <b style={{ fontSize: '20px', color: C.ink }}>{b.gap.toLocaleString()}</b> clients buy {b.category.toLowerCase()} <b>in-clinic but never online</b> — the target pool.
                </div>
                <div style={{ display: 'flex', gap: '14px', flexWrap: 'wrap', fontSize: '11px', color: C.muted, marginBottom: '8px' }}>
                  <span>🏥 {b.clinic_clients.toLocaleString()} clinic buyers</span>
                  <span>🛒 {b.online_clients.toLocaleString()} already online</span>
                  <span>🩺 {b.dr_hugh_clients.toLocaleString()} from Dr Hugh</span>
                  {b.autoship_clients > 0 && <span style={{ color: '#7C3AED' }}>🔁 {b.autoship_clients.toLocaleString()} on autoship</span>}
                  <span title="Ohana invoice revenue — indicative, pre-OCR cost/price unification">💰 ~${b.clinic_ltv.toLocaleString()} clinic ⏳</span>
                </div>
                <div style={{ fontSize: '11px', color: C.sub, marginBottom: '8px' }}>
                  <b>Feature:</b> {hooks.map((h, j) => <span key={j} style={{ fontWeight: 700, color: fg, background: bg, padding: '1px 7px', borderRadius: '8px', marginRight: '5px' }}>{h}</span>)}
                </div>
                <div style={{ fontSize: '11px', color: C.muted, marginBottom: '10px' }}>
                  <b>Reach the {b.total_clients.toLocaleString()}:</b> ✉ {b.reach.email.toLocaleString()} email · 💬 {b.reach.whatsapp.toLocaleString()} WhatsApp · 📣 {b.reach.meta.toLocaleString()} Meta/Google
                </div>
                <div style={{ fontSize: '11px', color: C.ok, background: '#F0FDF4', borderRadius: '6px', padding: '7px 10px', marginBottom: '10px' }}>
                  💡 <b>Play:</b> {hooks[0] ? `“Your ${hooks[0]} — now HK$100 off online”` : 'Savings — HK$100 off online'}. Email-first via Klaviyo, WhatsApp blast to opted-in, Meta custom audience (exclude recent online buyers) for the rest.
                </div>
                <button onClick={() => onBuild(b.category)}
                  style={{ fontSize: '12px', fontWeight: 700, color: 'white', background: C.indigo, border: 'none', borderRadius: '8px', padding: '8px 16px', cursor: 'pointer' }}>
                  Build this audience →
                </button>
              </div>
            </div>
          </div>
        )
      })}
      <div style={{ fontSize: '11px', color: C.faint, marginTop: '6px' }}>
        ⏳ = figure waiting on Rosetta IMS / OCR cost & product unification — counts are real; clinic $ is indicative.
      </div>
    </div>
  )
}

interface PerfRow { name: string; members: number; reachable: number; purchasers: number; conv: number; revenue: number; rev_per: number; claimed: number; avg_sends?: number; total_sends?: number }
interface PerfData { lists: PerfRow[]; flows: PerfRow[] }
function PerformancePanel({ onList, onFlow }: { onList: (n: string) => void; onFlow: (n: string) => void }) {
  const [d, setD] = useState<PerfData | null>(null)
  useEffect(() => { fetch(`${API}/clients/performance`, { headers: authHeaders() }).then(r => (r.ok ? r.json() : null)).then(setD).catch(() => setD(null)) }, [])
  if (!d) return <div style={{ padding: '40px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>Measuring list &amp; flow performance…</div>
  const th: React.CSSProperties = { fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.04em', textAlign: 'right', padding: '6px 10px', whiteSpace: 'nowrap' }
  const td: React.CSSProperties = { fontSize: '12px', color: '#334155', textAlign: 'right', padding: '8px 10px', fontVariantNumeric: 'tabular-nums', whiteSpace: 'nowrap' }
  const maxLConv = Math.max(1, ...d.lists.map(x => x.conv)), maxFConv = Math.max(1, ...d.flows.map(x => x.conv))
  const Table = ({ rows, kind, sizeLbl, onClick, maxConv }: { rows: PerfRow[]; kind: string; sizeLbl: string; onClick: (n: string) => void; maxConv: number }) => (
    <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', overflow: 'hidden', marginBottom: '18px' }}>
      <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead><tr style={{ borderBottom: '1px solid #E2E8F0', background: C.wash }}>
          <th style={{ ...th, textAlign: 'left' }}>{kind}</th>
          <th style={th}>{sizeLbl}</th><th style={th}>Reachable</th><th style={th}>Purchasers</th>
          <th style={th}>Conv.</th><th style={th}>Revenue</th>
          {kind === 'List' ? <th style={th}>$/member</th> : <th style={th} title="avg emails sent per person — frequency / spam guard">Avg sends</th>}
          <th style={th}>Claimed</th>
        </tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={r.name} style={{ borderBottom: i < rows.length - 1 ? '1px solid #F1F5F9' : 'none' }}>
              <td style={{ ...td, textAlign: 'left' }}>
                <button onClick={() => onClick(r.name)} style={{ fontSize: '12px', fontWeight: 700, color: C.indigo, background: 'none', border: 'none', cursor: 'pointer', padding: 0, textAlign: 'left' }}>{r.name}</button>
              </td>
              <td style={td}>{r.members.toLocaleString()}</td>
              <td style={{ ...td, color: C.muted }}>{r.reachable.toLocaleString()}</td>
              <td style={td}>{r.purchasers.toLocaleString()}</td>
              <td style={td}>
                <span style={{ display: 'inline-block', minWidth: '38px' }}>{r.conv}%</span>
                <span style={{ display: 'inline-block', width: '40px', height: '5px', background: C.primaryBg, borderRadius: '3px', verticalAlign: 'middle', marginLeft: '4px' }}>
                  <span style={{ display: 'block', width: `${Math.round(100 * r.conv / maxConv)}%`, height: '5px', background: C.indigo, borderRadius: '3px' }} /></span>
              </td>
              <td style={{ ...td, fontWeight: 700, color: C.ink }}>${r.revenue.toLocaleString()}</td>
              <td style={td}>{kind === 'List' ? `$${r.rev_per.toLocaleString()}` : (r.avg_sends ?? 0)}</td>
              <td style={{ ...td, color: r.claimed ? C.amber : C.knobOff }}>{r.claimed || '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
      </div>
    </div>
  )
  return (
    <div>
      <div style={{ background: '#F5F7FF', border: '1px solid #C7D2FE', borderRadius: '10px', padding: '13px 16px', marginBottom: '16px' }}>
        <div style={{ fontSize: '14px', fontWeight: 800, color: '#3730A3' }}>📈 Which lists acquire best, and which flows convert best</div>
        <div style={{ fontSize: '12px', color: C.sub, marginTop: '4px', lineHeight: 1.5 }}>
          The funnel per list/flow: <b>members → reachable → purchasers → conversion → revenue → claims</b>. A <b>list</b> is acquisition (top of funnel);
          a <b>flow</b> is conversion. They share a <i>CHANNEL-CAMPAIGN</i> token, so <code style={{ background: C.primaryBg, padding: '0 4px', borderRadius: '4px' }}>LIST - SITE - GIFT100</code> pairs with
          <code style={{ background: C.primaryBg, padding: '0 4px', borderRadius: '4px' }}>FLOW - SITE - GIFT100</code>. Click any name to see those customers. <span style={{ color: C.faint }}>Directional — “purchasers” = members who also bought (not strict last-click); autoship flows show ~100% because subscribers are buyers by definition. ⏳ revenue indicative pre-OCR.</span>
        </div>
      </div>
      <div style={{ fontSize: '12px', fontWeight: 700, color: C.ink, margin: '0 0 6px' }}>✉ Lists — acquisition ({d.lists.length})</div>
      <Table rows={d.lists} kind="List" sizeLbl="Members" onClick={onList} maxConv={maxLConv} />
      <div style={{ fontSize: '12px', fontWeight: 700, color: C.ink, margin: '0 0 6px' }}>🔁 Flows — conversion ({d.flows.length})</div>
      <Table rows={d.flows} kind="Flow" sizeLbl="Reached" onClick={onFlow} maxConv={maxFConv} />
    </div>
  )
}

function ClientbasePage() {
  const [summary, setSummary] = useState<Summary | null>(null)
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [main] = useState<string | null>(null)
  const [search, setSearch] = useState('')
  const [segment] = useState('')
  const [loading, setLoading] = useState(true)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [view, setView] = useState<'list' | 'demand' | 'campaign' | 'performance' | 'guide'>('list')
  const [filtersOpen, setFiltersOpen] = useState(true)
  const [includeLeads, setIncludeLeads] = useState(false)
  const [pbefore, setPbefore] = useState('')   // no purchase since (lapsed/reactivation)
  const [pfrom, setPfrom] = useState('')       // purchased between: from
  const [pto, setPto] = useState('')           // purchased between: to
  const [preacq, setPreacq] = useState(false)  // first purchase before acquisition (24 Jun 2026)
  const [rxOnly, setRxOnly] = useState(false)  // ever bought prescription
  // grouped multi-select filter families
  const [custSel, setCustSel] = useState<string[]>([])       // Customers (AND)
  const [dcatSel, setDcatSel] = useState<string[]>([])       // Demand categories (AND)
  const [dprodSel, setDprodSel] = useState<string[]>([])     // Demand products (AND)
  const [consentsSel, setConsentsSel] = useState<string[]>([])  // Consents (OR)
  const [opslSel, setOpslSel] = useState<string[]>([])       // Operations (OR)
  const [crmSel, setCrmSel] = useState<string[]>([])         // CRM Marketing lists (OR)
  const [flowSel, setFlowSel] = useState<string[]>([])       // received Klaviyo flows (OR)
  const [discountSel, setDiscountSel] = useState<string[]>([]) // claimed discount codes (OR)
  const [campSel, setCampSel] = useState<string[]>([])       // first-touch: UTM campaign/partner (OR)
  const [landSel, setLandSel] = useState<string[]>([])       // first-touch: landing page path (OR)
  const [refSel, setRefSel] = useState<string[]>([])         // first-touch: referral backlink (OR)
  const [prodQ, setProdQ] = useState('')
  const [prodResults, setProdResults] = useState<{ product: string; n: number; category?: string; sources?: string }[]>([])
  const [collQ, setCollQ] = useState('')
  const [collResults, setCollResults] = useState<{ collection: string; n: number }[]>([])
  const [dcollSel, setDcollSel] = useState<string[]>([])
  const [xprodQ, setXprodQ] = useState('')
  const [xprodResults, setXprodResults] = useState<{ product: string; n: number }[]>([])
  const [xprodSel, setXprodSel] = useState<string[]>([])   // EXCLUDE product buyers
  const [dfamSel, setDfamSel] = useState<string[]>([])     // Demand product-family (from Demand Breakdown)
  const [sort, setSort] = useState('')
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc')
  const tog = (arr: string[], set: (v: string[]) => void, v: string) => set(arr.includes(v) ? arr.filter(x => x !== v) : [...arr, v])
  const sortBy = (k: string) => { if (sort === k) setSortDir(d => d === 'desc' ? 'asc' : 'desc'); else { setSort(k); setSortDir('desc') } }
  const sortArrow = (k: string) => sort === k ? (sortDir === 'desc' ? ' ▼' : ' ▲') : ' ↕'
  const ACQ = '2025-06-24'   // Dr Hugh acquisition / Ohana takeover date
  const filterQs = (() => {
    const q = new URLSearchParams()
    if (main) q.set('main', main)
    if (search.trim()) q.set('search', search.trim())
    custSel.forEach(v => q.append('cust', v))
    dcatSel.forEach(v => q.append('dcat', v))
    dprodSel.forEach(v => q.append('dprod', v))
    dfamSel.forEach(v => q.append('dfam', v))
    dcollSel.forEach(v => q.append('dcoll', v))
    consentsSel.forEach(v => q.append('consents', v))
    opslSel.forEach(v => q.append('opsl', v))
    crmSel.forEach(v => q.append('crm', v))
    flowSel.forEach(v => q.append('flow', v))
    discountSel.forEach(v => q.append('discount', v))
    campSel.forEach(v => q.append('utmcamp', v))
    landSel.forEach(v => q.append('landing', v))
    refSel.forEach(v => q.append('referral', v))
    xprodSel.forEach(v => q.append('xprod', v))
    if (includeLeads) q.set('include_leads', 'true')
    if (pbefore) q.set('pbefore', pbefore)
    if (pfrom) q.set('pfrom', pfrom)
    if (pto) q.set('pto', pto)
    if (preacq) q.set('preacq', ACQ)
    if (rxOnly) q.set('rx', 'true')
    if (sort) { q.set('sort', sort); q.set('sortdir', sortDir) }
    return q.toString()
  })()
  const exportCohort = async (channel?: string) => {
    const res = await fetch(`${API}/clients/export?${filterQs}${channel ? `&channel=${channel}` : ''}`, { headers: authHeaders() })
    if (!res.ok) return
    const blob = await res.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url; a.download = `cohort_${channel || main || segment || 'all'}.csv`.replace(/[ &]/g, '_')
    document.body.appendChild(a); a.click(); a.remove(); URL.revokeObjectURL(url)
  }
  const cohortLabel = (() => {
    const CUST: Record<string, string> = { chs: 'Dr Hugh', ohana: 'Ohana', new_ohana: 'New to Ohana', online: 'Online', onetime: 'One-time', autoship: 'Autoship', rx_client: 'Rx clients', psg: 'PSG 2020-21' }
    const CONS: Record<string, string> = { no_contact: 'No contact', contact_no_consent: 'Contact·no-consent', email: 'Email-consented', whatsapp: 'WhatsApp-consented' }
    const OPSLAB: Record<string, string> = { unfulfilled: 'Unfulfilled', fulfilled: 'Fulfilled', cs: 'Reached CS', happy: 'Happy', fine: 'Fine', poor: 'Poor' }
    const grp = (items: string[], conn: string) => items.length > 1 ? '(' + items.join(` ${conn} `) + ')' : (items[0] || '')
    const p: string[] = []
    const c = grp(custSel.map(v => CUST[v] || v), 'AND'); if (c) p.push(c)
    const d = grp([...dcatSel, ...dfamSel, ...dprodSel.map(x => x.length > 16 ? x.slice(0, 16) + '…' : x), ...dcollSel.map(x => '⊞ ' + x)], 'AND'); if (d) p.push(d)
    const cn = grp(consentsSel.map(v => CONS[v] || v), 'OR'); if (cn) p.push(cn)
    const o = grp(opslSel.map(v => OPSLAB[v] || v), 'OR'); if (o) p.push(o)
    const cr = grp(crmSel.map(x => '✉ ' + (x.length > 18 ? x.slice(0, 18) + '…' : x)), 'OR'); if (cr) p.push(cr)
    const fl = grp(flowSel.map(x => '🔁 ' + (x.length > 18 ? x.slice(0, 18) + '…' : x)), 'OR'); if (fl) p.push(fl)
    const di = grp(discountSel.map(x => '🎟 ' + x), 'OR'); if (di) p.push(di)
    const ca = grp(campSel.map(x => '🎯 ' + x), 'OR'); if (ca) p.push(ca)
    const ld = grp(landSel.map(x => '🔗 landed ' + x), 'OR'); if (ld) p.push(ld)
    const rf = grp(refSel.map(x => '↗ via ' + x), 'OR'); if (rf) p.push(rf)
    xprodSel.forEach(x => p.push('NOT ' + (x.length > 16 ? x.slice(0, 16) + '…' : x)))
    if (preacq) p.push('pre-acquisition')
    if (rxOnly) p.push('bought Rx')
    if (pbefore) p.push(`no purchase since ${pbefore}`)
    if (pfrom || pto) p.push(`bought ${pfrom || '…'}→${pto || 'now'}`)
    if (search.trim()) p.push(`"${search.trim()}"`)
    return p.length ? p.join(' AND ') : 'all customers'
  })()

  useEffect(() => {
    fetch(`${API}/clients/summary${includeLeads ? '?include_leads=true' : ''}`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : null)).then(setSummary).catch(() => {})
  }, [includeLeads])

  useEffect(() => {
    if (!prodQ.trim()) { setProdResults([]); return }
    const t = setTimeout(() => {
      fetch(`${API}/clients/products?q=${encodeURIComponent(prodQ.trim())}`, { headers: authHeaders() })
        .then(r => (r.ok ? r.json() : { products: [] })).then(d => setProdResults(d.products || [])).catch(() => setProdResults([]))
    }, 250)
    return () => clearTimeout(t)
  }, [prodQ])

  useEffect(() => {
    if (!collQ.trim()) { setCollResults([]); return }
    const t = setTimeout(() => {
      fetch(`${API}/clients/collections?q=${encodeURIComponent(collQ.trim())}`, { headers: authHeaders() })
        .then(r => (r.ok ? r.json() : { collections: [] })).then(d => setCollResults(d.collections || [])).catch(() => setCollResults([]))
    }, 250)
    return () => clearTimeout(t)
  }, [collQ])

  useEffect(() => {
    if (!xprodQ.trim()) { setXprodResults([]); return }
    const t = setTimeout(() => {
      fetch(`${API}/clients/products?q=${encodeURIComponent(xprodQ.trim())}`, { headers: authHeaders() })
        .then(r => (r.ok ? r.json() : { products: [] })).then(d => setXprodResults(d.products || [])).catch(() => setXprodResults([]))
    }, 250)
    return () => clearTimeout(t)
  }, [xprodQ])

  const load = useCallback(() => {
    setLoading(true)
    fetch(`${API}/clients?${filterQs}&limit=200`, { headers: authHeaders() })
      .then(r => (r.ok ? r.json() : { total: 0, rows: [] }))
      .then(d => { setRows(d.rows || []); setTotal(d.total || 0) })
      .catch(() => { setRows([]); setTotal(0) })
      .finally(() => setLoading(false))
  }, [filterQs])

  useEffect(() => { const t = setTimeout(load, 200); return () => clearTimeout(t) }, [load])

  const cols = '1.4fr 0.95fr 0.7fr 1.5fr 0.9fr 0.95fr 1.0fr 28px'
  const th: React.CSSProperties = { fontSize: '10px', fontWeight: 700, color: C.faint, letterSpacing: '0.04em', textTransform: 'uppercase', padding: '0 4px' }

  return (
      <div style={{ padding: '24px 28px', maxWidth: '1320px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: '12px', marginBottom: '4px' }}>
          <h1 style={{ fontSize: '22px', fontWeight: 700, color: C.ink, margin: 0 }}>Clientbase</h1>
          <span style={{ fontSize: '12px', color: C.muted }}>Client SSOT · customer-first · clinic (DaySmart) + Dr Hugh’s (CHS)</span>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '14px', whiteSpace: 'nowrap' }}>
            {view === 'list' && <button onClick={() => exportCohort()}
              style={{ fontSize: '12px', fontWeight: 700, padding: '7px 13px', borderRadius: '8px', cursor: 'pointer',
                border: '1px solid #E2E8F0', background: 'white', color: C.sub }}>
              ⬇ Export cohort
            </button>}
            {view === 'list' && <div style={{ textAlign: 'right' }}>
              <span style={{ fontSize: '24px', fontWeight: 700, color: C.ink, fontVariantNumeric: 'tabular-nums' }}>{total.toLocaleString()}</span>
              <span style={{ fontSize: '12px', color: (search.trim() || custSel.length) ? C.indigo : C.muted, marginLeft: '6px', fontWeight: 600 }}>
                {(search.trim() || custSel.length) ? 'matching' : 'customers'}
              </span>
            </div>}
          </div>
        </div>
        {/* 3 view tabs */}
        <div style={{ display: 'flex', gap: '4px', marginBottom: '14px', borderBottom: '1px solid #E2E8F0' }}>
          {([['list', '👥 Client Database'], ['demand', '📊 Demand Breakdown'], ['campaign', '📣 Marketing Initiatives'], ['performance', '📈 CRM Performance']] as const).map(([v, label]) => (
            <button key={v} onClick={() => setView(v)}
              style={{ fontSize: '13px', fontWeight: 700, padding: '8px 16px', cursor: 'pointer', background: 'none', border: 'none',
                color: view === v ? C.indigo : C.muted, borderBottom: `2px solid ${view === v ? C.indigo : 'transparent'}`, marginBottom: '-1px' }}>
              {label}
            </button>
          ))}
          <button onClick={() => setView('guide')}
            style={{ fontSize: '13px', fontWeight: 700, padding: '8px 16px', cursor: 'pointer', background: 'none', border: 'none',
              color: view === 'guide' ? C.indigo : C.muted, borderBottom: `2px solid ${view === 'guide' ? C.indigo : 'transparent'}`, marginBottom: '-1px', marginLeft: 'auto' }}>
            📖 How to use
          </button>
        </div>

        {view === 'guide' ? (
          <GuideContent inline />
        ) : view === 'demand' ? (
          <DemandPanel onProduct={p => { setView('list'); if (!dfamSel.includes(p)) setDfamSel([...dfamSel, p]) }} />
        ) : view === 'campaign' ? (
          <InitiativesPanel onBuild={c => { setView('list'); setDcatSel([c]) }} />
        ) : view === 'performance' ? (
          <PerformancePanel onList={n => { setView('list'); setCrmSel([n]) }} onFlow={n => { setView('list'); setFlowSel([n]) }} />
        ) : (<>
        <p style={{ fontSize: '12px', color: C.faint, margin: '0 0 16px' }}>
          {summary ? `${summary.customers.toLocaleString()} customers · ${summary.pets.toLocaleString()} pets — every customer shown, pets nested` : 'Loading…'}
        </p>

        <div style={{ display: 'flex', gap: '10px', alignItems: 'center', marginBottom: '10px' }}>
          <input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search owner, email or phone…"
            style={{ border: '1px solid #E2E8F0', borderRadius: '6px', padding: '8px 11px', fontSize: '13px', width: '300px', background: 'white' }} />
          <label style={{ fontSize: '11px', color: C.muted, display: 'flex', alignItems: 'center', gap: '5px', cursor: 'pointer' }}>
            <input type="checkbox" checked={includeLeads} onChange={e => setIncludeLeads(e.target.checked)} /> Include newsletter leads (non-buyers)
          </label>
        </div>

        <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '10px 12px', marginBottom: '14px' }}>
          {(() => {
            const nActive = custSel.length + dcatSel.length + dprodSel.length + dfamSel.length + dcollSel.length + consentsSel.length + opslSel.length + crmSel.length + flowSel.length + discountSel.length + campSel.length + landSel.length + refSel.length + xprodSel.length + (pbefore ? 1 : 0) + (pfrom ? 1 : 0) + (pto ? 1 : 0) + (preacq ? 1 : 0) + (rxOnly ? 1 : 0) + (search.trim() ? 1 : 0)
            return (
              <div onClick={() => setFiltersOpen(o => !o)} style={{ display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', marginBottom: filtersOpen ? '10px' : 0 }}>
                <span style={{ fontSize: '13px', fontWeight: 700, color: '#334155' }}>🔍 Filters</span>
                {nActive > 0 && <span style={{ fontSize: '10px', fontWeight: 700, color: 'white', background: C.indigo, borderRadius: '10px', padding: '1px 8px' }}>{nActive} active — {cohortLabel}</span>}
                <span style={{ marginLeft: 'auto', fontSize: '12px', color: C.indigo, fontWeight: 700 }}>{filtersOpen ? '▾ hide' : '▸ show'}</span>
              </div>
            )
          })()}
          {filtersOpen && (<>
          {(() => {
            const cc = summary?.filters?.cust || {}
            const chip = (v: string, label: string, big: boolean) => {
              const on = custSel.includes(v); const n = cc[v]
              return (
                <button key={v} onClick={() => tog(custSel, setCustSel, v)}
                  style={{ fontSize: big ? '12px' : '11px', fontWeight: big ? 800 : 500, padding: big ? '5px 13px' : '3px 9px',
                    borderRadius: '13px', cursor: 'pointer', border: `${big ? 2 : 1}px solid ${on ? C.indigoInk : (big ? C.indigo : C.line)}`,
                    background: on ? C.indigo : (big ? C.primaryBg : 'white'), color: on ? 'white' : (big ? '#3730A3' : C.muted) }}>
                  {label}{n != null ? ` (${n.toLocaleString()})` : ''}{on ? ' ✓' : ''}
                </button>
              )
            }
            const onCust = (v: string) => tog(custSel, setCustSel, v)
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', marginBottom: '8px' }}>
                <span style={{ fontSize: '12px', fontWeight: 700, color: '#334155', width: '108px', flexShrink: 0 }}>Customers</span>
                {chip('chs', '🩺 Dr Hugh', true)}
                <FilterDropdown trigger={`🏥 Ohana (${(cc.ohana || 0).toLocaleString()})`} color="#0D9488" bg="#F0FDFA" selected={custSel} onToggle={onCust}
                  groups={[{ options: [{ value: 'ohana', label: 'All Ohana', n: cc.ohana }, { value: 'new_ohana', label: 'New to Ohana', n: cc.new_ohana }] }]} />
                <FilterDropdown trigger={`🛒 Website (${(cc.online || 0).toLocaleString()})`} color={C.green} bg="#F0FDF4" selected={custSel} onToggle={onCust}
                  groups={[{ options: [
                    { value: 'online', label: 'All Website buyers', n: cc.online },
                    { value: 'onetime', label: 'One-time buyers', n: cc.onetime },
                    { value: 'autoship', label: 'Autoship subscribers', n: cc.autoship },
                    { value: 'rx_client', label: 'Rx clients (online)', n: cc.rx_client },
                    { value: 'psg', label: 'PSG 2020-21 Rx audience', n: cc.psg },
                  ] }]} />
                {chip('prospect', '📋 Prospects', true)}
                {custSel.length > 1 && <span style={{ fontSize: '10px', color: C.indigo, fontWeight: 700 }}>= in ALL {custSel.length} (overlap)</span>}
                {custSel.length > 0 && <button onClick={() => setCustSel([])} style={{ fontSize: '10px', color: C.faint, background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>clear</button>}
              </div>
            )
          })()}
          <ChipGroup label="Demand Record" mode="all" selected={dcatSel} onClear={() => { setDcatSel([]); setDprodSel([]) }} onToggle={v => tog(dcatSel, setDcatSel, v)} counts={summary?.by_purchase_cat}
            options={[['Medicine', 'Meds'], ['Preventative', 'Preventatives'], ['Prescription Diet', 'Rx Diets'], ['Supplement', 'Supplements'], ['Food', 'Food'], ['Pet Hygiene', 'Hygiene']]} />
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '6px', flexWrap: 'wrap', marginBottom: '7px' }}>
            <span style={{ width: '108px', flexShrink: 0 }} />
            <div style={{ position: 'relative' }}>
              <input value={prodQ} onChange={e => setProdQ(e.target.value)} placeholder="+ add specific product (e.g. Apoquel)…"
                style={{ border: '1px solid #E2E8F0', borderRadius: '13px', padding: '4px 10px', fontSize: '11px', width: '230px' }} />
              {prodResults.length > 0 && (
                <div style={{ position: 'absolute', top: '28px', left: 0, zIndex: 20, background: 'white', border: '1px solid #E2E8F0', borderRadius: '6px', boxShadow: '0 6px 16px rgba(0,0,0,0.12)', width: '340px', maxHeight: '230px', overflowY: 'auto' }}>
                  {prodResults.map(p => (
                    <div key={p.product} onClick={() => { if (!dprodSel.includes(p.product)) setDprodSel([...dprodSel, p.product]); setProdQ(''); setProdResults([]) }}
                      style={{ padding: '5px 9px', fontSize: '11px', cursor: 'pointer', borderBottom: '1px solid #F1F5F9' }}>
                      <div>{p.product} <span style={{ color: C.faint }}>({p.n.toLocaleString()})</span></div>
                      <div style={{ fontSize: '9px', color: C.faint }}>{p.category}{p.sources ? ' · ' + p.sources : ''}</div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {dprodSel.map(p => (
              <button key={p} onClick={() => setDprodSel(dprodSel.filter(x => x !== p))}
                style={{ fontSize: '11px', fontWeight: 600, padding: '4px 10px', borderRadius: '13px', border: '1px solid #6366F1', background: C.indigo, color: 'white', cursor: 'pointer' }}>
                {p.length > 26 ? p.slice(0, 26) + '…' : p} ✕
              </button>
            ))}
            {dfamSel.map(f => (
              <button key={f} onClick={() => setDfamSel(dfamSel.filter(x => x !== f))} title="product family (from Demand Breakdown)"
                style={{ fontSize: '11px', fontWeight: 600, padding: '4px 10px', borderRadius: '13px', border: '1px solid #9333EA', background: '#9333EA', color: 'white', cursor: 'pointer' }}>
                📦 {f.length > 24 ? f.slice(0, 24) + '…' : f} ✕
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '6px', flexWrap: 'wrap', marginBottom: '7px' }}>
            <span style={{ width: '108px', flexShrink: 0 }} />
            <div style={{ position: 'relative' }}>
              <input value={collQ} onChange={e => setCollQ(e.target.value)} placeholder="+ add Shopify collection (e.g. Pharmacy)…"
                style={{ border: '1px solid #E2E8F0', borderRadius: '13px', padding: '4px 10px', fontSize: '11px', width: '240px' }} />
              {collResults.length > 0 && (
                <div style={{ position: 'absolute', top: '28px', left: 0, zIndex: 20, background: 'white', border: '1px solid #E2E8F0', borderRadius: '6px', boxShadow: '0 6px 16px rgba(0,0,0,0.12)', width: '300px', maxHeight: '230px', overflowY: 'auto' }}>
                  {collResults.map(c => (
                    <div key={c.collection} onClick={() => { if (!dcollSel.includes(c.collection)) setDcollSel([...dcollSel, c.collection]); setCollQ(''); setCollResults([]) }}
                      style={{ padding: '5px 9px', fontSize: '11px', cursor: 'pointer', borderBottom: '1px solid #F1F5F9' }}>
                      ⊞ {c.collection} <span style={{ color: C.faint }}>({c.n.toLocaleString()})</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {dcollSel.map(c => (
              <button key={c} onClick={() => setDcollSel(dcollSel.filter(x => x !== c))}
                style={{ fontSize: '11px', fontWeight: 600, padding: '4px 10px', borderRadius: '13px', border: '1px solid #6366F1', background: C.indigo, color: 'white', cursor: 'pointer' }}>
                ⊞ {c.length > 24 ? c.slice(0, 24) + '…' : c} ✕
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'flex-start', gap: '6px', flexWrap: 'wrap', marginBottom: '7px' }}>
            <span style={{ fontSize: '11px', fontWeight: 700, color: C.bad, width: '108px', flexShrink: 0 }}>↳ but NOT</span>
            <div style={{ position: 'relative' }}>
              <input value={xprodQ} onChange={e => setXprodQ(e.target.value)} placeholder="− exclude buyers of (e.g. Heartgard)…"
                style={{ border: '1px solid #FCA5A5', borderRadius: '13px', padding: '4px 10px', fontSize: '11px', width: '240px' }} />
              {xprodResults.length > 0 && (
                <div style={{ position: 'absolute', top: '28px', left: 0, zIndex: 20, background: 'white', border: '1px solid #E2E8F0', borderRadius: '6px', boxShadow: '0 6px 16px rgba(0,0,0,0.12)', width: '340px', maxHeight: '230px', overflowY: 'auto' }}>
                  {xprodResults.map(p => (
                    <div key={p.product} onClick={() => { if (!xprodSel.includes(p.product)) setXprodSel([...xprodSel, p.product]); setXprodQ(''); setXprodResults([]) }}
                      style={{ padding: '5px 9px', fontSize: '11px', cursor: 'pointer', borderBottom: '1px solid #F1F5F9' }}>
                      {p.product} <span style={{ color: C.faint }}>({p.n.toLocaleString()})</span>
                    </div>
                  ))}
                </div>
              )}
            </div>
            {xprodSel.map(p => (
              <button key={p} onClick={() => setXprodSel(xprodSel.filter(x => x !== p))}
                style={{ fontSize: '11px', fontWeight: 600, padding: '4px 10px', borderRadius: '13px', border: '1px solid #DC2626', background: C.redBg, color: C.bad, cursor: 'pointer' }}>
                ✕ {p.length > 26 ? p.slice(0, 26) + '…' : p}
              </button>
            ))}
          </div>
          <ChipGroup label="Consents" mode="any" selected={consentsSel} onClear={() => setConsentsSel([])} onToggle={v => tog(consentsSel, setConsentsSel, v)} counts={summary?.filters?.consents}
            options={[['no_contact', 'No contact info'], ['contact_no_consent', 'Contact · no consent'], ['email', '✉ Email-able'], ['whatsapp', '💬 WhatsApp-able']]} />
          <ChipGroup label="Operations" mode="any" selected={opslSel} onClear={() => setOpslSel([])} onToggle={v => tog(opslSel, setOpslSel, v)} counts={summary?.filters?.ops}
            options={[['unfulfilled', '📦 Unfulfilled'], ['fulfilled', 'Fulfilled'], ['cs', '🎧 Reached CS'], ['happy', '🟢 Happy'], ['fine', '⚪ Fine'], ['poor', '🔴 Poor']]} />
          {(() => {
            const lists = summary?.crm_lists || []
            const cflows = summary?.crm_flows || []
            const cdisc = summary?.crm_discounts || []
            // classify flows so "Got flow" is grouped, not a wall of chips
            const CAT = (name: string) => {
              const n = name.toLowerCase()
              if (n.includes('gift100')) return 'Welcome (GIFT100)'
              if (n.includes('site abandon')) return 'Welcome (GIFT100)'
              if (n.includes('abandon') || n.includes('browse')) return 'Abandonment'
              if (n.includes('winback') || n.includes('cross')) return 'Retention'
              if (n.includes('autoship')) return 'AutoShip'
              return 'Other'
            }
            const ORDER = ['Welcome (GIFT100)', 'Abandonment', 'Retention', 'AutoShip', 'Other']
            const byCat: Record<string, { value: string; label: string; n: number }[]> = {}
            cflows.forEach(f => { (byCat[CAT(f.name)] ||= []).push({ value: f.name, label: f.name, n: f.n }) })
            const flowGroups = ORDER.filter(c => byCat[c]).map(c => ({ header: c, options: byCat[c] }))
            return (
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', marginBottom: '7px', paddingTop: '7px', borderTop: '1px solid #F1F5F9' }}>
                <span style={{ fontSize: '12px', fontWeight: 700, color: '#334155', width: '108px', flexShrink: 0 }}>CRM Marketing</span>
                <FilterDropdown trigger="✉ On a list" color="#0E7490" bg="#ECFEFF" selected={crmSel} onToggle={v => tog(crmSel, setCrmSel, v)}
                  groups={[{ options: lists.map(l => ({ value: l.name, label: l.name, n: l.n })) }]} />
                <FilterDropdown trigger="🔁 Got a flow" color="#7C3AED" bg="#F3E8FF" selected={flowSel} onToggle={v => tog(flowSel, setFlowSel, v)}
                  groups={flowGroups.length ? flowGroups : [{ options: [{ value: '', label: 'pulling Klaviyo flows…' }] }]} />
                {cdisc.length > 0 && <FilterDropdown trigger="🎟 Claimed" color={C.amber} bg={C.warnBg} selected={discountSel} onToggle={v => tog(discountSel, setDiscountSel, v)}
                  groups={[{ options: cdisc.map(d => ({ value: d.code, label: d.code, n: d.n })) }]} />}
                {(crmSel.length + flowSel.length + discountSel.length > 0) && <button onClick={() => { setCrmSel([]); setFlowSel([]); setDiscountSel([]) }} style={{ fontSize: '10px', color: C.faint, background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>clear CRM</button>}
              </div>
            )
          })()}
          {/* FIRST TOUCH — marketing attribution (recent online journeys only) */}
          {((summary?.utm_campaigns || []).length + (summary?.landing_pages || []).length + (summary?.referrals || []).length > 0) && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap', marginBottom: '7px', paddingTop: '7px', borderTop: '1px solid #F1F5F9' }}>
              <span style={{ fontSize: '12px', fontWeight: 700, color: '#334155', width: '108px', flexShrink: 0 }} title="Where they first landed online — from Shopify. Recent online buyers only (~online cohort); clinic-only customers have none.">First touch ⓘ</span>
              {(summary?.utm_campaigns || []).length > 0 && <FilterDropdown trigger="🎯 Campaign / partner" color={C.indigo} bg={C.primaryBg} selected={campSel} onToggle={v => tog(campSel, setCampSel, v)}
                groups={[{ options: (summary?.utm_campaigns || []).map(c => ({ value: c.campaign, label: `${c.campaign} · ${c.source}/${c.medium}`, n: c.n })) }]} />}
              {(summary?.landing_pages || []).length > 0 && <FilterDropdown trigger="🔗 Landing page" color="#0F766E" bg="#F0FDFA" selected={landSel} onToggle={v => tog(landSel, setLandSel, v)}
                groups={[{ options: (summary?.landing_pages || []).map(l => ({ value: l.path, label: l.path, n: l.n })) }]} />}
              {(summary?.referrals || []).length > 0 && <FilterDropdown trigger="↗ Referral" color={C.amber} bg={C.warnBg} selected={refSel} onToggle={v => tog(refSel, setRefSel, v)}
                groups={[{ options: (summary?.referrals || []).map(r => ({ value: r.domain, label: r.domain, n: r.n })) }]} />}
              {(campSel.length + landSel.length + refSel.length > 0) && <button onClick={() => { setCampSel([]); setLandSel([]); setRefSel([]) }} style={{ fontSize: '10px', color: C.faint, background: 'none', border: 'none', cursor: 'pointer', textDecoration: 'underline' }}>clear</button>}
              <span style={{ fontSize: '10px', color: C.faint }}>recent online journeys</span>
            </div>
          )}
          <div style={{ display: 'flex', alignItems: 'center', gap: '14px', flexWrap: 'wrap', fontSize: '12px', color: C.muted, marginTop: '4px', paddingTop: '7px', borderTop: '1px solid #F1F5F9' }}>
            <span style={{ fontWeight: 700, color: '#334155', width: '108px', flexShrink: 0 }}>Dates / Rx</span>
            <label style={{ display: 'flex', alignItems: 'center', gap: '5px' }} title="Customers who made a purchase inside this window (any source)">Purchased
              <input type="date" value={pfrom} onChange={e => setPfrom(e.target.value)} style={{ border: '1px solid #E2E8F0', borderRadius: '6px', padding: '4px 8px', fontSize: '12px' }} />
              <span>→</span>
              <input type="date" value={pto} onChange={e => setPto(e.target.value)} style={{ border: '1px solid #E2E8F0', borderRadius: '6px', padding: '4px 8px', fontSize: '12px' }} />
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '5px' }} title="Lapsed / reactivation: most-recent purchase is before this date (haven't bought since)">No purchase since
              <input type="date" value={pbefore} onChange={e => setPbefore(e.target.value)} style={{ border: '1px solid #E2E8F0', borderRadius: '6px', padding: '4px 8px', fontSize: '12px' }} />
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '5px', cursor: 'pointer' }}>
              <input type="checkbox" checked={preacq} onChange={e => setPreacq(e.target.checked)} /> Pre-acquisition (first buy &lt; 24 Jun 2025)
            </label>
            <label style={{ display: 'flex', alignItems: 'center', gap: '5px', cursor: 'pointer' }}>
              <input type="checkbox" checked={rxOnly} onChange={e => setRxOnly(e.target.checked)} /> Bought Rx
            </label>
          </div>
          </>)}
        </div>

        <FunnelBar qs={filterQs} label={cohortLabel} onExport={exportCohort} />

        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '10px', fontSize: '11px', color: C.muted }}>
          <span style={{ fontWeight: 600 }}>Source:</span>
          {Object.entries(SRC).map(([name, [ltr, col]]) => (
            <span key={name} style={{ display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
              <span style={{ fontSize: '8px', fontWeight: 700, color: 'white', background: col, borderRadius: '50%', width: '13px', height: '13px', display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>{ltr}</span>{name}
            </span>
          ))}
          <span style={{ marginLeft: '4px', color: C.faint }}>· ring = same care-type in &gt;1 source</span>
        </div>

        <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'hidden' }}>
          <div style={{ display: 'grid', gridTemplateColumns: cols, gap: '8px', padding: '10px 14px', borderBottom: '1px solid #E2E8F0', background: C.wash }}>
            {(() => { const sh: React.CSSProperties = { ...th, cursor: 'pointer', userSelect: 'none' }; const act = (k: string): React.CSSProperties => sort === k ? { ...sh, color: C.indigo } : sh; return (<>
              <div style={act('owner')} onClick={() => sortBy('owner')}>Owner{sortArrow('owner')}</div>
              <div style={th}>CRM ✉/💬</div>
              <div style={act('last_buy')} onClick={() => sortBy('last_buy')}>Last buy{sortArrow('last_buy')}</div>
              <div style={th}>Last category (product)</div>
              <div style={act('needs')} onClick={() => sortBy('needs')} title="From ACTUAL purchases only — not recommendations">Demonstrated need (bought){sortArrow('needs')}</div>
              <div style={act('ltv')} onClick={() => sortBy('ltv')}>LTV 🏥/🛒{sortArrow('ltv')}</div>
              <div style={act('ops')} onClick={() => sortBy('ops')}>Ops{sortArrow('ops')}</div>
              <div></div>
            </>) })()}
          </div>
          {loading ? <div style={{ padding: '40px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>Loading…</div>
            : rows.length === 0 ? <div style={{ padding: '40px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>No matches.</div>
            : rows.map(r => {
              const open = expanded === r.customer_id
              return (
                <div key={r.customer_id}>
                  <div onClick={() => setExpanded(open ? null : r.customer_id)}
                    style={{ display: 'grid', gridTemplateColumns: cols, gap: '8px', padding: '10px 14px',
                      borderBottom: open ? 'none' : '1px solid #F1F5F9', alignItems: 'center', background: open ? C.wash : 'white', cursor: 'pointer' }}>
                    <div>
                      <div style={{ fontSize: '13px', fontWeight: 600, color: C.ink }}>{r.owner || '—'}</div>
                      <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', alignItems: 'center', marginTop: '3px' }}>
                        <SegmentBadge seg={r.segment} />
                        {r.bought_rx && <span title="Has bought prescription products" style={{ fontSize: '9px', fontWeight: 700, color: 'white', background: C.redInk, padding: '1px 6px', borderRadius: '8px' }}>Rx</span>}
                        {r.cs_contact && <span title={`Last CS contact ${r.cs_contact.last_contact}`} style={{ fontSize: '9px', fontWeight: 700, color: 'white', background: '#DB2777', padding: '1px 6px', borderRadius: '8px' }}>🎧 {r.cs_contact.last_contact?.slice(5)}</span>}
                        <SourceTags sources={r.sources} />
                      </div>
                    </div>
                    <div style={{ fontSize: '10px', display: 'flex', flexDirection: 'column', gap: '2px', fontVariantNumeric: 'tabular-nums' }}>
                      <span title="Last emailed (Klaviyo)" style={{ color: r.crm?.last_email ? C.amberInk : C.knobOff }}>✉ {r.crm?.last_email ? r.crm.last_email.slice(2) : '—'}</span>
                      <span title="Last WhatsApp contact" style={{ color: r.crm?.last_whatsapp ? C.green : C.knobOff }}>💬 {r.crm?.last_whatsapp ? r.crm.last_whatsapp.slice(2) : '—'}</span>
                      {r.crm?.lists?.length ? <span title={r.crm.lists.join(', ')} style={{ color: '#0E7490', fontWeight: 600 }}>📋 {r.crm.lists.length} list{r.crm.lists.length > 1 ? 's' : ''}</span> : null}
                      {r.crm?.flows?.length ? <span title={r.crm.flows.map(f => `${f.flow} (${f.last})`).join('\n')} style={{ color: '#7C3AED', fontWeight: 600 }}>🔁 {r.crm.flows.length} flow{r.crm.flows.length > 1 ? 's' : ''}</span> : null}
                    </div>
                    <div style={{ fontSize: '12px', color: C.ink, fontVariantNumeric: 'tabular-nums' }}>{r.last_purchase || <span style={{ color: C.knobOff }}>—</span>}</div>
                    <div style={{ fontSize: '12px' }}>
                      {r.recent_purchases && r.recent_purchases.length ? (() => {
                        const p = r.recent_purchases[0]
                        const sc = p.source === 'Shopify' ? C.green : p.source === 'Dr Hugh' ? '#6B21A8' : '#0D9488'
                        const cm: Record<string, string[]> = { 'Preventative': [C.greenBg, C.green], 'Prescription Diet': [C.warnBg, C.amberInk], 'Medicine': [C.redBg, C.redInk] }
                        const [cbg, cfg] = cm[p.category] || [C.monoBg, C.sub]
                        return (<>
                          <span style={{ fontSize: '11px', fontWeight: 700, color: cfg, background: cbg, padding: '1px 6px', borderRadius: '8px' }}>{p.category}</span>
                          {p.on_shopify ? <span style={{ fontSize: '10px', marginLeft: '4px' }}>🛒</span> : null}
                          <div style={{ fontSize: '10px', color: C.muted, marginTop: '3px' }}>
                            {p.product.length > 32 ? p.product.slice(0, 32) + '…' : p.product} <span style={{ color: sc, fontWeight: 700 }}>· {p.source}</span>
                          </div>
                        </>)
                      })() : <span style={{ color: C.knobOff }}>—</span>}
                    </div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', alignItems: 'flex-start' }}>
                      {(r.purchase_cats || []).slice(0, 3).map((c, i) => {
                        const m: Record<string, string[]> = { 'Preventative': [C.greenBg, C.green], 'Prescription Diet': [C.warnBg, C.amberInk], 'Medicine': [C.redBg, C.redInk] }
                        const [bg, fg] = m[c] || [C.monoBg, C.sub]
                        return <span key={i} style={{ fontSize: '9px', fontWeight: 700, color: fg, background: bg, padding: '1px 6px', borderRadius: '8px' }}>{c}</span>
                      })}
                      {!(r.purchase_cats || []).length ? <span style={{ color: C.knobOff, fontSize: '11px' }}>—</span> : null}
                    </div>
                    <div style={{ fontSize: '11px', fontVariantNumeric: 'tabular-nums' }}>
                      {(r.clinic_ltv || r.shopify_ltv) ? (<>
                        <div style={{ color: '#0D9488' }}>🏥 ${Math.round(r.clinic_ltv || 0).toLocaleString()}</div>
                        <div style={{ color: C.green }}>🛒 ${Math.round(r.shopify_ltv || 0).toLocaleString()}</div>
                      </>) : <span style={{ color: C.knobOff }}>—</span>}
                    </div>
                    <div style={{ fontSize: '11px', display: 'flex', flexDirection: 'column', gap: '2px' }}>
                      {r.cs_contact?.sentiment ? (() => {
                        const s = r.cs_contact.sentiment
                        const col = s === 'poor' ? '#DC2626' : s === 'happy' ? '#16A34A' : C.faint
                        const lbl = s === 'poor' ? 'Poor' : s === 'happy' ? 'Happy' : 'Fine'
                        return <span style={{ color: col, fontWeight: 700 }}>● {lbl}</span>
                      })() : null}
                      {r.unfulfilled ? (() => {
                        const days = Math.floor((Date.now() - Date.parse(r.unfulfilled.oldest)) / 86400000)
                        const urgent = days > 4
                        return <span style={{ color: urgent ? C.amber : C.muted, fontWeight: urgent ? 700 : 400 }}>📦 {r.unfulfilled.count} unfilled · {days}d{urgent ? ' ⚠' : ''}</span>
                      })() : null}
                      {r.cs_contact && !r.cs_contact.sentiment ? <span style={{ color: '#DB2777' }}>🎧 in CS</span> : null}
                      <span style={{ fontSize: '9px', color: C.faint }}>🏥 {r.last_clinic ? r.last_clinic.slice(2) : '—'} · 🛒 {r.last_shopify ? r.last_shopify.slice(2) : '—'}</span>
                    </div>
                    <div style={{ textAlign: 'center', color: C.faint, fontSize: '12px' }}>{open ? '▾' : '▸'}</div>
                  </div>
                  {open && <div style={{ padding: '4px 14px 14px', borderBottom: '1px solid #F1F5F9', background: C.wash }}><DetailPanel r={r} /></div>}
                </div>
              )
            })}
        </div>
        <p style={{ fontSize: '11px', color: C.faint, marginTop: '10px' }}>
          Showing {rows.length} of {total.toLocaleString()} customers{main ? ` with ${main}` : ''}. Care types are draft (v1) — refine as we go.
        </p>
        </>)}
      </div>
  )
}

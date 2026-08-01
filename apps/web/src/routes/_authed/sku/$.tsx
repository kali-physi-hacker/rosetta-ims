// SKU details — the rev-04 "instrument". One connected read from cost →
// margin → cover → next action: status ticks, a decision strip, an offering
// ledger with always-on cost sparklines, bullet-bar selling items with inline
// price editing, an offering-aware coverage simulator, issue cards pinned to
// the rows they describe, and an activity rail. Editing is native: record
// cost (append-only language), offering drawer, add supplier, availability,
// bulk-terms builder (add/edit/delete), stock adjust, identity modal.
// Provenance speaks plain words — run IDs and pipeline jargon never render.
// Sources are CATALOGUE / MANUAL only: links without an offering price yet
// (pre-backfill) read as the manual baseline.
import { createFileRoute, Link, useNavigate, useRouter } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useRef, useState } from 'react'
import { API_BASE } from '@/lib/config'
import { authHeaders, can } from '@/lib/auth'
import { skuToPath } from '@/lib/sku'
import { SKUD_CSS, SKUD2_CSS } from '@/lib/skudCss'
import { Spinner } from '@/components/Spinner'
import { EditSkuModal } from '@/components/EditSkuModal'
import { ChangeSkuModal } from '@/components/ChangeSkuModal'
import { toast } from '@/lib/toast'
import { openSourceFile } from '@/lib/review'
import { confirmDialog } from '@/lib/confirm'
import type { CompetitorPrice, MbbTerm, Product } from '@/lib/types'

export const Route = createFileRoute('/_authed/sku/$')({ component: SkuInstrumentRoute })

const API = API_BASE

// ── read-model types ─────────────────────────────────────────────────────
interface OfferingPrice {
  unit_cost: number
  amount: number
  basis: string | null
  since: string | null
  until: string | null
  is_current: boolean
  source: 'catalogue' | 'manual'
  run_id: string | null
  /** The supplier catalogue this price was read out of, when it came from a scan. */
  source_file?: string | null
  source_received_at?: string | null
}
interface OfferingEntry {
  supplier_id: number | null
  supplier_name: string | null
  supplier_code: string | null
  supplier_sku: string | null
  barcode: string | null
  source: 'catalogue' | 'manual' | 'legacy'
  current: OfferingPrice | null
  history: OfferingPrice[]
  packaging: {
    purchase_uom: string | null
    sellable_uom: string | null
    sellable_units_per_purchase_unit: number | null
    content_amount: number | null
    content_uom: string | null
  } | null
  legacy: { basic_cost: number | null; units_per_pack: number | null; cost_source: string | null; cost_updated_at: string | null }
}
interface SupFull {
  id: number
  effective_unit_cost: number | null
  order_increment_qty: number | null
  order_increment_uom: string | null
  minimum_order_qty: number | null
  minimum_order_uom: string | null
  minimum_order_source: string | null
  pricing_note: string | null
  cost_source: string | null
  cost_updated_at: string | null
}
interface AuditEvent {
  id: number; action: string; display_name: string | null
  details: Record<string, unknown>; created_at: string
}
type SupplierLinkRow = Product['all_suppliers'][number]

// ── helpers ──────────────────────────────────────────────────────────────
async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { cache: 'no-store', headers: authHeaders() })
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}))
    const detail = (payload as { detail?: unknown })?.detail
    throw new Error(typeof detail === 'string' ? detail : `HTTP ${res.status}`)
  }
  return res.json()
}
async function send(method: string, path: string, body?: unknown): Promise<Product | null> {
  const res = await fetch(`${API}${path}`, {
    method, headers: { 'Content-Type': 'application/json', ...authHeaders() },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (!res.ok) {
    const d = await res.json().catch(() => ({}))
    toast.error(typeof d.detail === 'string' ? d.detail : 'Request failed')
    return null
  }
  return res.json()
}

const fm = (v: number | null | undefined, d = 2) => (v == null ? '—' : v.toFixed(d))
const hk = (v: number | null | undefined) => (v == null ? '—' : `HK$${v >= 100 ? Math.round(v).toLocaleString() : v.toFixed(2)}`)
const fmtDay = (iso: string | null | undefined) => {
  if (!iso) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + (iso.length <= 10 ? 'T00:00:00Z' : 'Z'))
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}
const daysSince = (iso: string | null | undefined) => {
  if (!iso) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return isNaN(d.getTime()) ? null : Math.max(0, Math.round((Date.now() - d.getTime()) / 86400000))
}
const inDays = (days: number) => {
  const d = new Date(Date.now() + days * 86400000)
  return d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}
// Net margin selling one unit at `price`, after a channel's %-fee + delivery.
const netAt = (price: number | null | undefined, cost: number | null | undefined, fee = 0, delivery = 0) =>
  price != null && price > 0 && cost != null ? (price - cost - fee * price - delivery) / price : null

// Two sources only — anything without a catalogue provenance reads as manual.
const srcOf = (s: string | null | undefined): 'catalogue' | 'manual' => (s === 'catalogue' ? 'catalogue' : 'manual')
const SRC_WORDS: Record<'catalogue' | 'manual', string> = {
  catalogue: 'from a committed catalogue',
  manual: 'recorded by a person',
}
function SrcChip({ source }: { source: string | null | undefined }) {
  const s = srcOf(source)
  return <span className={`srcchip ${s}`}>{s.toUpperCase()}</span>
}

// Always-on cost history sparkline: silent line, accent "now" dot.
function Spark({ series, w = 64, h = 20 }: { series: number[]; w?: number; h?: number }) {
  if (series.length === 0) return null
  const pts = series.length === 1 ? [series[0], series[0]] : series
  const min = Math.min(...pts), max = Math.max(...pts)
  const y = (v: number) => (max === min ? h / 2 : 3 + (h - 6) * (1 - (v - min) / (max - min)))
  const x = (i: number) => 2 + (w - 6) * (i / (pts.length - 1))
  const line = pts.map((v, i) => `${x(i)},${y(v).toFixed(1)}`).join(' ')
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width={w} height={h} style={{ display: 'block' }} aria-hidden>
      <polyline points={line} fill="none" stroke="#A9AFC4" strokeWidth="1.5" />
      <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1])} r="2.2" fill="#4F46E5" />
    </svg>
  )
}

// Margin bullet: net fill vs a fixed 0–60% scale, gross ghost, floor tick,
// optional rival-if-matched marker. Numbers live beside the bar, never on it.
const SCALE = 0.6
const pctPos = (v: number) => `${Math.max(0, Math.min(100, (v / SCALE) * 100))}%`
function Bullet({ net, gross, floor, rival }: { net: number | null; gross?: number | null; floor: number; rival?: number | null }) {
  const cls = net == null ? 'a' : net >= floor ? 'g' : net > 0 ? 'a' : 'r'
  return (
    <span className="bull">
      {gross != null && gross > 0 && <i className="ghost" style={{ width: pctPos(gross) }} />}
      {net != null && net > 0 && <i className={`fill ${cls}`} style={{ width: pctPos(net) }} />}
      <b className="floor" style={{ left: pctPos(floor) }} />
      {rival != null && <s className="rival" style={{ left: pctPos(Math.max(rival, 0)) }} title="net margin if you matched the cheapest rival" />}
    </span>
  )
}

// Weeks-of-cover bar on a fixed 8-week scale with the 4-week target tick.
function CoverBar({ woc, proj }: { woc: number | null; proj?: number | null }) {
  const w = woc == null ? 0 : Math.min(100, (woc / 8) * 100)
  const pw = proj != null && woc != null ? Math.min(100 - w, ((proj - woc) / 8) * 100) : 0
  const cls = woc == null ? '' : woc < 2 ? 'crit' : woc < 4 ? 'low' : ''
  return (
    <span className="cover" style={{ display: 'block' }}>
      <i className={`fill ${cls}`} style={{ width: `${w}%` }} />
      {pw > 0 && <i className="proj" style={{ left: `${w}%`, width: `${pw}%` }} />}
      <b className="tgt" style={{ left: '50%' }} />
    </span>
  )
}

function Tick({ label, state, value }: { label: string; state: 'g' | 'a' | 'r'; value: string }) {
  return (
    <div className="tick">
      <span className="tl">{label}</span>
      <span className="tv"><span className={`ic ${state}`}>{state === 'g' ? '✓' : '!'}</span>{value}</span>
    </div>
  )
}

// ── route shell: queries + loading, then the instrument ─────────────────
function SkuInstrumentRoute() {
  const rawSplat = Route.useParams({ select: p => p._splat })
  const sku = (rawSplat ?? '').split('/').map(decodeURIComponent).join('/')
  const path = skuToPath(sku)
  const queryClient = useQueryClient()

  const product = useQuery({ queryKey: ['sku-v2', sku], queryFn: () => getJson<Product>(`/products/${path}`) })
  const offerings = useQuery({ queryKey: ['sku-v2-offerings', sku], queryFn: () => getJson<{ offerings: OfferingEntry[] }>(`/products/${path}/offerings`) })
  const supFull = useQuery({ queryKey: ['sku-v2-supfull', sku], queryFn: () => getJson<{ suppliers: SupFull[] }>(`/products/${path}/suppliers`) })
  const audit = useQuery({ queryKey: ['sku-v2-audit', sku], queryFn: () => getJson<{ events: AuditEvent[] }>(`/products/${path}/onboarding-audit?limit=30`) })
  const competitors = useQuery({ queryKey: ['sku-v2-comp', sku], queryFn: () => getJson<{ competitors: CompetitorPrice[]; cheapest: number | null }>(`/competitors/by-sku/${path}`) })

  if (product.isLoading) return <div style={{ padding: 40 }}><Spinner /></div>
  if (product.isError || !product.data) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: '#C0362C', fontSize: 13 }}>
        {String((product.error as Error)?.message ?? 'Product not found')}
        <br /><Link to={'/' as never} style={{ color: '#4F46E5', fontSize: 12, marginTop: 8, display: 'inline-block' }}>← Back to Inventory</Link>
      </div>
    )
  }

  const onProduct = (updated: Product) => {
    queryClient.setQueryData(['sku-v2', sku], updated)
    queryClient.invalidateQueries({ queryKey: ['sku-v2-offerings', sku] })
    queryClient.invalidateQueries({ queryKey: ['sku-v2-supfull', sku] })
    queryClient.invalidateQueries({ queryKey: ['sku-v2-audit', sku] })
  }

  return (
    <Instrument
      sku={sku}
      p={product.data}
      offeringRows={offerings.data?.offerings ?? []}
      offeringsLoading={offerings.isLoading}
      fullRows={supFull.data?.suppliers ?? []}
      events={audit.data?.events ?? []}
      compRows={competitors.data?.competitors ?? []}
      compCheapest={competitors.data?.cheapest ?? null}
      refreshCompetitors={() => queryClient.invalidateQueries({ queryKey: ['sku-v2-comp', sku] })}
      onProduct={onProduct}
    />
  )
}

// ── the instrument ───────────────────────────────────────────────────────
function Instrument({ sku, p, offeringRows, offeringsLoading, fullRows, events, compRows, compCheapest, refreshCompetitors, onProduct }: {
  sku: string
  p: Product
  offeringRows: OfferingEntry[]
  offeringsLoading: boolean
  fullRows: SupFull[]
  events: AuditEvent[]
  compRows: CompetitorPrice[]
  compCheapest: number | null
  refreshCompetitors: () => void
  onProduct: (p: Product) => void
}) {
  const router = useRouter()
  const navigate = useNavigate()
  const goBack = () => { if (typeof window !== 'undefined' && window.history.length > 1) router.history.back(); else navigate({ to: '/' as never }) }
  const path = skuToPath(sku)
  const editable = can('product_edit')

  // dialogs & panels
  const [openId, setOpenId] = useState<number | 'auto'>('auto')
  const [editing, setEditing] = useState(false)
  const [changingSku, setChangingSku] = useState(false)
  const [recordFor, setRecordFor] = useState<SupplierLinkRow | null>(null)
  const [drawerFor, setDrawerFor] = useState<SupplierLinkRow | null>(null)
  const [adding, setAdding] = useState(false)
  const [availFor, setAvailFor] = useState<SupplierLinkRow | null>(null)
  // Terms dialog tracks the link by id so it re-reads fresh deal lists after
  // every save (the dialog stays open for entering several deals in a row).
  const [termsFor, setTermsFor] = useState<number | null>(null)
  const [adjusting, setAdjusting] = useState(false)
  const [sellFor, setSellFor] = useState<string | null>(null)
  const [shortcuts, setShortcuts] = useState(false)
  const [compOpen, setCompOpen] = useState(false)
  const [actOpen, setActOpen] = useState(false)
  const anyModal = editing || changingSku || !!recordFor || !!drawerFor || adding || !!availFor || termsFor != null || adjusting || sellFor != null

  // ── domain derivations ──
  const uom = p.uom ?? 'unit'
  const packUnit = p.pack_unit ?? 'pack'
  const links = p.all_suppliers ?? []
  const defaultLink = links.find(s => s.is_primary) ?? links.find(s => s.is_preferred) ?? links[0] ?? null
  const fullById = new Map(fullRows.map(f => [f.id, f]))
  const entryFor = (l: SupplierLinkRow | null) => (l ? offeringRows.find(o => o.supplier_id === l.supplier_id) ?? null : null)

  const unitCostOf = (l: SupplierLinkRow | null): number | null => {
    if (!l) return null
    const e = entryFor(l)
    if (e?.current) return e.current.unit_cost
    if (l.basic_cost == null) return null
    const upl = l.units_per_pack && l.units_per_pack > 1 ? l.units_per_pack : 1
    return l.basic_cost / upl
  }
  const uppOf = (l: SupplierLinkRow | null): number => {
    const e = entryFor(l)
    return e?.packaging?.sellable_units_per_purchase_unit ?? (l?.units_per_pack && l.units_per_pack > 1 ? l.units_per_pack : p.units_per_pack ?? 1) ?? 1
  }
  const seriesFor = (e: OfferingEntry | null, l: SupplierLinkRow | null): number[] => {
    if (e && e.history.length > 0) {
      return [...e.history].sort((a, b) => (a.since ?? '').localeCompare(b.since ?? '')).map(h => h.unit_cost)
    }
    const c = unitCostOf(l)
    return c != null ? [c] : []
  }

  const effCost = p.unit_cost
  const mr = p.margin_range
  const marginRows = mr?.channels ?? []
  const chMap = new Map(p.channels.map(c => [c.channel, c]))
  const floor = p.gp_floor
  const defEntry = entryFor(defaultLink)
  const costSince = defEntry?.current?.since ?? defaultLink?.stock_confirmed_by ?? p.cost_updated_at
  const costSrc = srcOf(defEntry?.current?.source ?? (defaultLink ? undefined : p.cost_source))
  const costAge = daysSince(defEntry?.current?.since ?? p.cost_updated_at)

  const activeChans = p.channels.filter(c => c.is_active && c.selling_price != null)
  const belowFloor = activeChans.filter(c => {
    const m = marginRows.find(x => x.channel === c.channel)
    return m?.basic_margin != null && m.basic_margin < floor
  })
  const oos = defaultLink?.stock_status === 'out_of_stock'
  const backup = oos
    ? links.filter(s => s.id !== defaultLink?.id && s.stock_status !== 'out_of_stock' && unitCostOf(s) != null)
        .sort((a, b) => (unitCostOf(a) as number) - (unitCostOf(b) as number))[0] ?? null
    : null
  const issueCount = (oos ? 1 : 0) + belowFloor.length

  // ── next action (deterministic reorder math) ──
  const wd = p.weekly_demand
  const woc = p.woc
  const buyLink = (oos ? backup : defaultLink) ?? defaultLink
  const buyFull = buyLink ? fullById.get(buyLink.id) : null
  const buyUpp = uppOf(buyLink)
  const buyUnit = unitCostOf(buyLink)
  const inc = buyFull?.order_increment_qty && buyFull.order_increment_qty > 0 ? buyFull.order_increment_qty : 1
  const moq = buyFull?.minimum_order_qty && buyFull.minimum_order_qty > 0 ? buyFull.minimum_order_qty : 1
  // Bulk tiers: min_qty is in sell units (the label says "24+ cans"). Terms
  // with min_qty ≤ 1 are unconditional prices, not tiers — they lower the
  // landed cost but never gate a quantity decision.
  const allTerms = (buyLink?.mbb_term_list ?? [])
    .filter(t => t.effective_unit_cost != null)
    .sort((a, b) => ((a.min_qty ?? 0) as number) - ((b.min_qty ?? 0) as number))
  const tiers = allTerms.filter(t => t.min_qty != null && t.min_qty > 1)
  const landedAt = (units: number): { cost: number | null; tier: MbbTerm | null } => {
    const met = allTerms.filter(t => units >= ((t.min_qty ?? 0) as number))
    if (met.length === 0) return { cost: buyUnit, tier: null }
    const best = met.reduce((a, b) => ((a.effective_unit_cost as number) <= (b.effective_unit_cost as number) ? a : b))
    const cost = Math.min(best.effective_unit_cost as number, buyUnit ?? Infinity)
    // Only a real quantity gate counts as "hitting a tier" in the copy.
    return { cost, tier: best.min_qty != null && best.min_qty > 1 ? best : null }
  }
  const roundPacks = (n: number) => Math.max(moq, Math.ceil(n / inc) * inc)
  const runwayDays = woc != null && wd > 0 ? Math.max(0, (woc - 4) * 7) : null
  const suggestion = (() => {
    if (!buyLink || wd <= 0 || woc == null) return null
    const TARGET_W = 6
    if (woc >= TARGET_W) return null
    let packs = roundPacks(Math.ceil(((TARGET_W - woc) * wd) / buyUpp))
    // Bump to the next bulk tier when it's within reach (≤1.5× the base buy).
    const next = tiers.find(t => packs * buyUpp < (t.min_qty as number))
    if (next && (next.min_qty as number) <= packs * buyUpp * 1.5) packs = roundPacks((next.min_qty as number) / buyUpp)
    const units = packs * buyUpp
    const landed = landedAt(units)
    return { packs, units, landed: landed.cost, tier: landed.tier, spend: landed.cost != null ? landed.cost * units : null }
  })()

  // ── simulator ──
  const [simPacks, setSimPacks] = useState<number | 'auto'>('auto')
  const packs = simPacks === 'auto' ? (suggestion?.packs ?? moq) : simPacks
  const simUnits = packs * buyUpp
  const simLanded = landedAt(simUnits)
  const simProjW = woc != null && wd > 0 ? woc + simUnits / wd : null
  const simMax = Math.max(12, (suggestion?.packs ?? moq) * 2, ...tiers.map(t => Math.ceil((t.min_qty as number) / buyUpp) + inc * 2))
  const clinicRow = marginRows.find(m => m.channel === 'clinic') ?? marginRows[0]
  const clinicNetAt = (cost: number | null) =>
    netAt(clinicRow?.selling_price, cost, clinicRow?.channel_fee_pct ?? 0, clinicRow?.delivery_cost ?? 0)
  const sliderTicks: { at: number; label: string }[] = []
  if (moq > 0) sliderTicks.push({ at: moq, label: `MOQ ${moq}` })
  for (const t of tiers.slice(0, 2)) sliderTicks.push({ at: Math.ceil((t.min_qty as number) / buyUpp), label: `tier ${t.min_qty}${uom.slice(0, 1)}` })
  if (woc != null && wd > 0 && woc < 8) sliderTicks.push({ at: Math.ceil(((8 - woc) * wd) / buyUpp), label: '8w cover' })

  // ── status ticks ──
  const marginTick: ['g' | 'a' | 'r', string] = activeChans.length === 0
    ? ['a', 'no prices']
    : belowFloor.length === 0 ? ['g', 'all ≥ floor'] : [belowFloor.length === activeChans.length ? 'r' : 'a', `${belowFloor.length} low`]
  const costTick: ['g' | 'a' | 'r', string] = effCost == null
    ? ['r', 'none'] : costAge == null ? ['a', 'undated'] : costAge <= 90 ? ['g', `${costAge}d fresh`] : ['a', `${costAge}d old`]
  const coverTick: ['g' | 'a' | 'r', string] = woc == null
    ? ['a', 'no signal'] : woc < 2 ? ['r', `${woc.toFixed(1)}w`] : woc < 4 ? ['a', `${woc.toFixed(1)}w`] : ['g', `${woc.toFixed(1)}w`]
  const dataTick: ['g' | 'a' | 'r', string] = p.hitl_verified ? ['g', 'verified'] : ['a', 'unverified']

  // Strip ranks LISTED channels only — an unlisted channel's margin is noise.
  const listedMargins = marginRows.filter(m => m.selling_price != null && chMap.get(m.channel)?.is_active)
  const worstTwo = [...(listedMargins.length > 0 ? listedMargins : marginRows.filter(m => m.selling_price != null))]
    .sort((a, b) => (a.basic_margin ?? 1) - (b.basic_margin ?? 1)).slice(0, 2)

  // Best buy line: cheapest conditional deal + unconditional best.
  const bestBuy = (() => {
    let best: { unit: number; label: string } | null = null
    let flat: { unit: number; name: string } | null = null
    for (const l of links) {
      const u = unitCostOf(l)
      if (u != null && (!flat || u < flat.unit)) flat = { unit: u, name: l.name ?? '—' }
      for (const t of l.mbb_term_list ?? []) {
        if (t.effective_unit_cost != null && (!best || t.effective_unit_cost < best.unit)) {
          const req = t.min_qty ? `${t.min_qty}+ ${plu(uom)}` : t.min_spend != null ? `${hk(t.min_spend)} spend` : 'always'
          best = { unit: t.effective_unit_cost, label: `${req} from ${l.name ?? '—'}` }
        }
      }
    }
    return { best, flat }
  })()

  const rivalNetFor = (channel: string): number | null => {
    if (compCheapest == null) return null
    const m = marginRows.find(x => x.channel === channel)
    return netAt(compCheapest, effCost, m?.channel_fee_pct ?? 0, m?.delivery_cost ?? 0)
  }

  // ── keyboard layer ──
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (anyModal) { if (e.key === 'Escape') setShortcuts(false); return }
      const t = e.target as HTMLElement
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      if (e.key === '?') { setShortcuts(s => !s); return }
      if (e.key === 'Escape') { setShortcuts(false); return }
      if (!editable) return
      if (e.key === 'e' || e.key === 'E') { setEditing(true) }
      else if ((e.key === 'c' || e.key === 'C') && defaultLink) { setRecordFor(defaultLink) }
      else if ((e.key === 'b' || e.key === 'B') && defaultLink) { setTermsFor(defaultLink.id) }
    }
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [anyModal, editable, defaultLink?.id])   // eslint-disable-line react-hooks/exhaustive-deps

  const effectiveOpen = openId === 'auto' ? defaultLink?.id ?? null : openId
  const scrollToIssues = () => document.querySelector('.skud .issue')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
  const trend = p.sales_trend ?? []
  const trendMax = Math.max(...trend.map(t => t.units), 1)
  const wdbc = p.weekly_demand_by_channel

  return (
    <div className="skud" style={{ padding: '22px 28px 60px', maxWidth: 1280, margin: '0 auto' }}>
      <style>{SKUD_CSS + SKUD2_CSS}</style>

      {/* ── header: identity + status ticks ── */}
      <div className="hdr" style={{ marginBottom: 12 }}>
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow"><span className="lnk" onClick={goBack} style={{ fontWeight: 600 }}>← Inventory</span> · {p.category}{p.subcategory ? ` / ${p.subcategory}` : ''}</div>
          <h1 style={{ marginBottom: 5 }}>{p.name}</h1>
          <div className="idline">
            <span className="skucode" style={{ fontSize: 13.5 }}>{p.sku_code}</span>
            {p.brand && <><span className="sep">·</span><span>{p.brand}</span></>}
            {p.species && <><span className="sep">·</span><span>{p.species}</span></>}
            <span className={`bdg ${p.status === 'ACTIVE' ? 'ok' : 'warn'}`} style={{ marginLeft: 4 }}><span className="st" />{p.status === 'ACTIVE' ? 'Active' : p.status}</span>
            <span className="bdg neu">{p.units_per_pack && p.units_per_pack > 1 ? `${packUnit} of ${p.units_per_pack}` : `per ${uom}`}</span>
            <span className="bdg neu">Grade {p.data_grade}</span>
            {p.hero_sku && <span className="bdg acc">Hero</span>}
            {issueCount > 0 && <span className="bdg warn" style={{ cursor: 'pointer' }} onClick={scrollToIssues}>{issueCount} need{issueCount === 1 ? 's' : ''} attention ↓</span>}
          </div>
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8, alignItems: 'flex-end' }}>
          <div className="ticks">
            <Tick label="Margin" state={marginTick[0]} value={marginTick[1]} />
            <Tick label="Cost" state={costTick[0]} value={costTick[1]} />
            <Tick label="Cover" state={coverTick[0]} value={coverTick[1]} />
            <Tick label="Data" state={dataTick[0]} value={dataTick[1]} />
          </div>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span className="kbd" title="keyboard shortcuts" style={{ cursor: 'pointer' }} onClick={() => setShortcuts(true)}>?</span>
            {editable && <button className="btn" onClick={() => setEditing(true)}>Edit details</button>}
            {can('product_sensitive') && <StatusMenu p={p} onProduct={onProduct} sku={path} />}
            <MoreMenu p={p} onChangeSku={() => setChangingSku(true)} onProduct={onProduct} sku={path} />
          </div>
        </div>
      </div>

      {/* ── decision strip ── */}
      <div className="strip">
        <div className="blk">
          <span className="bl">Buy · effective cost</span>
          <span className="bv">{fm(effCost)} <span className="u">HK$/{uom}</span></span>
          <Spark series={seriesFor(defEntry, defaultLink)} w={96} h={22} />
          <span className="bs"><SrcChip source={defEntry?.current?.source ?? p.cost_source} />{costSince ? fmtDay(costSince) : ''}{defaultLink?.name ? ` · ${defaultLink.name}` : ''}</span>
        </div>
        <div className="conn">›</div>
        <div className="blk">
          <span className="bl">Sell · net vs {(floor * 100).toFixed(0)}% floor</span>
          {worstTwo.length === 0 && <span className="bs">no channel prices yet</span>}
          {worstTwo.map(m => (
            <div key={m.channel} style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 3 }}>
              <span style={{ fontSize: 10.5, width: 46, color: 'var(--muted)' }}>{m.channel}</span>
              <Bullet net={m.basic_margin} gross={chMap.get(m.channel)?.gp_pct} floor={floor} rival={m.channel === 'shopify' ? rivalNetFor(m.channel) : null} />
              <span style={{ fontSize: 11.5, fontWeight: 700, color: m.basic_margin == null ? 'var(--faint)' : m.basic_margin >= floor ? 'var(--good)' : 'var(--amber)', minWidth: 42, textAlign: 'right' }}>
                {m.basic_margin != null ? `${(m.basic_margin * 100).toFixed(1)}%` : '—'}
              </span>
            </div>
          ))}
          {compCheapest != null && <span className="bs" style={{ marginTop: 2 }}>▼ rival if matched · ghost = gross</span>}
        </div>
        <div className="conn">›</div>
        <div className="blk">
          <span className="bl">Cover · target 4w</span>
          <span className="bv">{woc != null ? woc.toFixed(1) : '—'} <span className="u">weeks</span></span>
          <CoverBar woc={woc} />
          <span className="bs">{wd > 0 ? `${Math.round(wd)} ${plu(uom)}/wk` : 'no demand signal'}{runwayDays != null ? ` · runs low ~${inDays(runwayDays)}` : ''}</span>
        </div>
        <div className="conn">›</div>
        <div className="blk act">
          <span className="bl">Next action</span>
          {suggestion ? (
            <>
              <span className="bv">
                Reorder{runwayDays != null ? <> by <b>{inDays(runwayDays)}</b></> : null} — {suggestion.packs} {packUnit}{suggestion.packs === 1 ? '' : 's'} from {buyLink?.name ?? '—'}
                {suggestion.tier ? <> hits the {suggestion.tier.min_qty}-{uom} tier → lands <b>{fm(suggestion.landed)}/{uom}</b></> : suggestion.landed != null ? <> at <b>{fm(suggestion.landed)}/{uom}</b></> : null}
                {suggestion.spend != null ? ` · ${hk(suggestion.spend)}` : ''}
              </span>
              <span style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 4 }}>
                <span className="btn soon" style={{ padding: '5px 12px', fontSize: 11.5 }} title="Purchase orders aren't wired up yet — when they land, this will prefill the suggested quantity and supplier">Create PO <i className="soonchip">SOON</i></span>
                <span className="lnk" style={{ fontSize: 11 }} onClick={() => { setSimPacks(suggestion.packs); document.getElementById('sim-instrument')?.scrollIntoView({ behavior: 'smooth', block: 'center' }) }}>adjust in simulator ↓</span>
              </span>
            </>
          ) : (
            <>
              <span className="bv">{woc == null || wd <= 0 ? 'No demand signal yet — nothing to schedule.' : <>Cover is healthy at <b>{woc.toFixed(1)}w</b>{runwayDays != null ? <> — next reorder check ~<b>{inDays(runwayDays)}</b></> : null}.</>}</span>
              <span style={{ marginTop: 4 }}><span className="lnk" style={{ fontSize: 11 }} onClick={() => document.getElementById('sim-instrument')?.scrollIntoView({ behavior: 'smooth', block: 'center' })}>plan a buy in the simulator ↓</span></span>
            </>
          )}
        </div>
      </div>

      {/* ── offerings ledger ── */}
      <div className="card" style={{ marginBottom: 16 }}>
        <div className="ch">
          <div>
            <div className="ct">Supplier offerings</div>
            <div className="hint">
              {bestBuy.best ? <>best buy: <b style={{ color: 'var(--accent-ink)' }}>{fm(bestBuy.best.unit)}/{uom}</b> — {bestBuy.best.label}{bestBuy.flat ? ` · unconditional: ${fm(bestBuy.flat.unit)} (${bestBuy.flat.name})` : ''}</>
                : 'what each supplier charges — cost, packaging, terms'}
            </div>
          </div>
          {editable && <span className="lnk" style={{ fontSize: 12 }} onClick={() => setAdding(true)}>+ Add supplier</span>}
        </div>
        {offeringsLoading && <div style={{ padding: 18 }}><Spinner /></div>}
        {links.map(l => {
          const e = entryFor(l)
          const isOpen = effectiveOpen === l.id
          const unit = unitCostOf(l)
          const upl = uppOf(l)
          const series = seriesFor(e, l)
          const hist = e ? [...e.history].sort((a, b) => (b.since ?? '').localeCompare(a.since ?? '')) : []
          const prev = hist.find(h => !h.is_current)
          const delta = prev && e?.current ? ((e.current.unit_cost - prev.unit_cost) / prev.unit_cost) * 100 : null
          const lOos = l.stock_status === 'out_of_stock'
          const full = fullById.get(l.id)
          const terms = l.mbb_term_list ?? []
          const isDefault = l.id === defaultLink?.id
          const packCost = unit != null && upl > 1 ? unit * upl : null
          return (
            <div key={l.id}>
              <div className={`lrow${isOpen ? ' open' : ''}`} onClick={() => setOpenId(isOpen ? -1 : l.id)}>
                <span className="star" title={isDefault ? 'default supplier' : ''}>{isDefault ? '★' : ''}</span>
                <span><span className="nm">{l.name ?? '—'}</span>{isDefault && <span style={{ fontSize: 10.5, color: 'var(--faint)' }}> default</span>}{!isDefault && l.is_preferred && <span style={{ fontSize: 10.5, color: 'var(--good)' }}> cheapest</span>}</span>
                <span className="skucode" style={{ fontSize: 11.5, color: 'var(--muted)', cursor: 'copy' }} title="copy supplier SKU"
                  onClick={ev => { ev.stopPropagation(); if (l.supplier_sku) { navigator.clipboard?.writeText(l.supplier_sku).catch(() => {}); toast.success('Supplier SKU copied') } }}>
                  {l.supplier_sku ? `${l.supplier_sku} ⧉` : '—'}
                </span>
                {lOos
                  ? <span className="bdg warn" style={{ fontSize: 10 }}><span className="st" />OOS{l.expected_restock_at ? ` · ~${fmtDay(l.expected_restock_at)}` : ''}</span>
                  : <span className="bdg ok" style={{ fontSize: 10 }}><span className="st" />in stock</span>}
                <span>{series.length > 1 ? <Spark series={series} /> : <span style={{ fontSize: 10.5, color: 'var(--faint)' }}>{series.length === 1 ? '1 record' : 'no cost yet'}</span>}</span>
                <span className="cost">
                  {fm(unit)} <span className="u">/{uom}</span>
                  {delta != null && <span style={{ display: 'block', fontSize: 10.5, fontWeight: 700, color: Math.abs(delta) > 20 ? 'var(--red)' : 'var(--amber)' }}>{delta > 0 ? '↑' : '↓'} {Math.abs(delta).toFixed(1)}%</span>}
                </span>
                <SrcChip source={e?.current?.source ?? (e ? e.source : 'manual')} />
                <span className="chev">{isOpen ? '▾' : '▸'}</span>
              </div>

              {isDefault && oos && (
                <div className="issue">
                  <span className="ad" />
                  <div>
                    <b>Out of stock{l.expected_restock_at ? ` until ~${fmtDay(l.expected_restock_at)}` : ''}.</b>{' '}
                    {backup ? <>{backup.name} has stock at {fm(unitCostOf(backup))}/{uom}{unit != null && unitCostOf(backup) != null ? ` (${(unitCostOf(backup) as number) >= unit ? '+' : '−'}${Math.abs((unitCostOf(backup) as number) - unit).toFixed(2)})` : ''}.</> : 'No in-stock backup supplier on record.'}
                    {woc != null && (woc >= 4 ? ` Cover ${woc.toFixed(1)}w — no urgent switch.` : ` Cover ${woc.toFixed(1)}w — watch closely.`)}{' '}
                    {editable && <span className="lnk" style={{ fontSize: 11.5 }} onClick={() => setAvailFor(l)}>Update availability</span>}
                  </div>
                </div>
              )}

              {isOpen && (
                <div className="lex">
                  <div className="cols">
                    <div>
                      <div className="kv2"><span className="k">Packaging</span>
                        <span>
                          {e?.packaging
                            ? <>{(e.packaging.purchase_uom ?? packUnit).toLowerCase()} of {e.packaging.sellable_units_per_purchase_unit ?? '?'} {plu((e.packaging.sellable_uom ?? uom).toLowerCase())}{e.packaging.content_amount ? <span style={{ color: 'var(--faint)' }}> · {e.packaging.content_amount} {e.packaging.content_uom ?? ''} each</span> : null}</>
                            : upl > 1 ? <>{packUnit} of {upl} {plu(uom)}</> : <>per {uom}</>}
                          {packCost != null && <span style={{ color: 'var(--faint)' }}> · {fm(packCost)}/{(e?.packaging?.purchase_uom ?? packUnit).toLowerCase()}</span>}
                        </span>
                      </div>
                      <div className="kv2"><span className="k">Ordering</span>
                        <span>
                          {full?.order_increment_qty ? `order in ${full.order_increment_qty}${full.order_increment_uom ? ` ${full.order_increment_uom}` : ''} steps` : 'any quantity'}
                          {full?.minimum_order_qty ? ` · min ${full.minimum_order_qty}${full.minimum_order_uom ? ` ${full.minimum_order_uom}` : ''}` : ''}
                          {full?.pricing_note && <span style={{ color: 'var(--faint)' }}> · “{full.pricing_note}”</span>}
                        </span>
                      </div>
                      {l.rrp != null && (
                        <div className="kv2"><span className="k">RRP</span>
                          <span>{fm(l.rrp)} <span style={{ color: 'var(--faint)' }}>— this supplier’s recommended retail</span></span>
                        </div>
                      )}
                      <div className="kv2" style={{ alignItems: 'flex-start' }}><span className="k">Bulk price</span>
                        <span style={{ flex: 1, minWidth: 0 }}>
                          {terms.length === 0 && <span style={{ color: 'var(--faint)' }}>no bulk terms · {editable && <span className="lnk" style={{ fontSize: 11.5 }} onClick={() => setTermsFor(l.id)}>Add terms</span>}</span>}
                          {terms.length > 0 && unit != null && (
                            <Ladder unit={unit} terms={terms} uom={uom} youUnits={l.id === buyLink?.id ? simUnits : null} />
                          )}
                          {terms.length > 0 && (
                            <table className="cpptab" style={{ marginTop: 6 }}>
                              <thead><tr><th style={{ textAlign: 'left' }}>Deal</th><th>Requires</th><th>Lands at</th><th>Saves</th><th>{clinicRow?.channel ?? 'clinic'} net</th></tr></thead>
                              <tbody>
                                {terms.map(t => {
                                  const saving = t.effective_unit_cost != null && unit != null && unit > 0 ? (1 - t.effective_unit_cost / unit) * 100 : null
                                  const n = clinicNetAt(t.effective_unit_cost ?? null)
                                  return (
                                    <tr key={t.id}>
                                      <td className="cpp-row">{termDeal(t, uom)}</td>
                                      <td>{termRequires(t, uom)}</td>
                                      <td style={{ fontWeight: 650 }}>{fm(t.effective_unit_cost)}</td>
                                      <td><SavesCell saving={saving} /></td>
                                      <td style={{ fontWeight: 650, color: n == null ? 'var(--faint)' : n >= floor ? 'var(--good)' : 'var(--amber)' }}>{n != null ? `${(n * 100).toFixed(1)}%` : '—'}</td>
                                    </tr>
                                  )
                                })}
                              </tbody>
                            </table>
                          )}
                        </span>
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: 9.5, fontWeight: 750, letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--faint)', marginBottom: 6 }}>Cost timeline</div>
                      <div className="tml">
                        {hist.length === 0 && (
                          <div className="tev cur">
                            <b>{fm(unit)}</b> · cost on record
                            <br /><span className="tw">{e?.legacy.cost_updated_at ? `${fmtDay(e.legacy.cost_updated_at)} · ` : ''}history starts with the next recorded cost or catalogue commit</span>
                          </div>
                        )}
                        {hist.map((h, i) => (
                          <div key={i} className={`tev${h.is_current ? ' cur' : ''}`}>
                            <b>{fm(h.unit_cost)}</b> · {SRC_WORDS[srcOf(h.source)]}
                            <br />
                            <span className="tw">
                              {h.is_current ? `since ${fmtDay(h.since) ?? '—'}` : `${fmtDay(h.since) ?? '—'} → ${fmtDay(h.until) ?? '—'}`}
                              {/* "catalogue" does not say WHICH catalogue, and by
                                  the time anyone questions a cost that is the
                                  question. */}
                              {h.source_file && h.run_id && <>
                                {' · '}
                                <button className="srcfile"
                                  title={`Open ${h.source_file}${h.source_received_at ? ` · received ${fmtDay(h.source_received_at)}` : ''}`}
                                  onClick={() => openSourceFile(h.run_id!, h.source_file).catch(err => toast.error(String(err?.message ?? err)))}>
                                  {h.source_file}
                                </button>
                              </>}
                              {' · '}
                              <Link className="lnk" style={{ fontSize: 10.5, textDecoration: 'none' }} to={(h.run_id ? `/catalogues/review/${h.run_id}` : '/admin/audit') as never}>audit →</Link>
                            </span>
                          </div>
                        ))}
                      </div>
                      {editable && (
                        <div style={{ display: 'flex', gap: 7, marginTop: 9, flexWrap: 'wrap' }}>
                          <button className="btn pri" style={{ padding: '5px 11px', fontSize: 11.5 }} onClick={() => setRecordFor(l)}>Record cost</button>
                          <button className="btn" style={{ padding: '5px 11px', fontSize: 11.5 }} onClick={() => setDrawerFor(l)}>Edit offering</button>
                          <button className="btn" style={{ padding: '5px 11px', fontSize: 11.5 }} onClick={() => setAvailFor(l)}>Availability</button>
                          <button className="btn" style={{ padding: '5px 11px', fontSize: 11.5 }} onClick={() => setTermsFor(l.id)}>Terms</button>
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          )
        })}
        {!offeringsLoading && links.length === 0 && (
          <div style={{ padding: 18, fontSize: 13, color: 'var(--faint)' }}>No suppliers linked yet{editable && <> — <span className="lnk" onClick={() => setAdding(true)}>add one</span></>}.</div>
        )}
      </div>

      {/* ── sell + stock lanes ── */}
      <div className="grid" style={{ gridTemplateColumns: '1.15fr 1fr' }}>
        <div className="col">
          <div className="card">
            <div className="ch">
              <div>
                <div className="ct">Selling items</div>
                <div className="hint">at effective {fm(effCost)}/{uom} · floor {(floor * 100).toFixed(0)}%</div>
              </div>
            </div>
            {p.channels.map(ch => {
              const m = marginRows.find(x => x.channel === ch.channel)
              const net = m?.basic_margin ?? null
              const isBad = ch.is_active && net != null && net < floor
              return (
                <div key={ch.channel}>
                  <div className="bmrow">
                    <span className="chn" style={{ cursor: editable ? 'pointer' : undefined }} title={editable ? 'Configure this selling item' : undefined} onClick={() => editable && setSellFor(ch.channel)}>
                      {ch.channel}
                      <span className="soldas">
                        {ch.is_active ? 'listed' : 'not listed'}
                        {ch.units_per_listing && ch.units_per_listing > 1 ? ` · ×${ch.units_per_listing}/listing` : ''}
                        {ch.order_multiple && ch.order_multiple > 1 ? ` · buy in ${ch.order_multiple}s` : ''}
                        {ch.channel === 'clinic' && (defaultLink?.rrp ?? p.rrp) != null ? ` · RRP ${fm(defaultLink?.rrp ?? p.rrp)}` : ''}
                        {editable && <> · <span className="lnk" style={{ fontSize: 10.5 }}>configure</span></>}
                      </span>
                    </span>
                    <PriceCell sku={path} channel={ch.channel} price={ch.selling_price} cost={effCost} upl={ch.units_per_listing ?? 1}
                      fee={m?.channel_fee_pct ?? 0} delivery={m?.delivery_cost ?? 0} floor={floor} editable={editable && ch.is_active} onProduct={onProduct} />
                    <Bullet net={net} gross={ch.gp_pct} floor={floor} rival={rivalNetFor(ch.channel)} />
                    <span style={{ textAlign: 'right', fontWeight: 700, color: net == null ? 'var(--faint)' : net >= floor ? 'var(--good)' : 'var(--amber)' }}>
                      {net != null ? `${(net * 100).toFixed(1)}%` : '—'}
                      <span className="soldas" style={{ textAlign: 'right' }}>gross {ch.gp_pct != null ? `${(ch.gp_pct * 100).toFixed(1)}%` : '—'}</span>
                    </span>
                    <span className={`bdg ${ch.is_active ? 'ok' : 'neu'}`} style={{ fontSize: 9.5, justifySelf: 'end' }}>{ch.is_active ? 'listed' : 'off'}</span>
                  </div>
                  {isBad && (
                    <div className="issue">
                      <span className="ad" />
                      <div>
                        <b>Below floor.</b> {(floor * 100).toFixed(0)}% needs ≥ {m?.selling_price != null && effCost != null ? fm((effCost + (m.delivery_cost ?? 0)) / (1 - floor - (m.channel_fee_pct ?? 0))) : '—'}
                        {p.mbb_unit_cost != null && clinicNetAt(p.mbb_unit_cost) != null ? <> — or hold and reprice once a bulk buy lands cost at {fm(p.mbb_unit_cost)} (→ {((netAt(m?.selling_price, p.mbb_unit_cost, m?.channel_fee_pct ?? 0, m?.delivery_cost ?? 0) ?? 0) * 100).toFixed(1)}%).</> : '.'}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
            {p.channels.length === 0 && <div style={{ padding: 18, fontSize: 12.5, color: 'var(--faint)', textAlign: 'center' }}>Not listed on any channel.</div>}
            {p.mbb_unit_cost != null && marginRows.some(m => m.mbb_margin != null) && (
              <div style={{ padding: '8px 15px', borderTop: '1px solid var(--line2)', fontSize: 11.5, color: 'var(--muted)' }}>
                At best bulk cost {fm(p.mbb_unit_cost)}: {marginRows.filter(m => m.mbb_margin != null).map(m => `${m.channel} ${((m.mbb_margin as number) * 100).toFixed(1)}%`).join(' · ')}
              </div>
            )}
            {editable && ['clinic', 'shopify', 'hktv'].some(c => !p.channels.some(ch => ch.channel === c)) && (
              <div style={{ padding: '7px 15px', borderTop: '1px solid var(--line2)', fontSize: 12, color: 'var(--muted)', display: 'flex', gap: 8, alignItems: 'baseline', flexWrap: 'wrap' }}>
                <span>Add selling item:</span>
                {['clinic', 'shopify', 'hktv'].filter(c => !p.channels.some(ch => ch.channel === c)).map(c => (
                  <span key={c} className="lnk" style={{ fontSize: 12 }} onClick={() => setSellFor(c)}>+ {c}</span>
                ))}
              </div>
            )}
            <div style={{ padding: '8px 15px', borderTop: '1px solid var(--line2)', fontSize: 12, color: 'var(--ink2)', display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
              <span><b>Competitors:</b> {compCheapest != null ? <>cheapest rival {fm(compCheapest)}</> : compRows.length > 0 ? 'no prices scraped yet' : 'none tracked'}</span>
              <span className="lnk" style={{ marginLeft: 'auto', fontSize: 12 }} onClick={() => setCompOpen(o => !o)}>{compRows.length} tracked {compOpen ? '▴' : '▾'}</span>
            </div>
            {compOpen && <CompetitorPanel sku={path} rows={compRows} effCost={effCost} mbbCost={p.mbb_unit_cost} marginRows={marginRows} floor={floor} editable={editable} refresh={refreshCompetitors} productId={p.id} />}
          </div>
        </div>

        <div className="col">
          <div className="card" id="sim-instrument">
            <div className="ch">
              <div>
                <div className="ct">Inventory</div>
                <div className="hint">{p.total_qty} on hand · whse {p.warehouse_qty} · clinic {p.clinic_qty}</div>
              </div>
              {editable && <span className="lnk" style={{ fontSize: 12 }} onClick={() => setAdjusting(true)}>Adjust</span>}
            </div>
            <div style={{ padding: '12px 15px' }}>
              <div style={{ display: 'flex', gap: 10, alignItems: 'center' }}>
                <span style={{ fontSize: 11.5, color: 'var(--muted)', flex: 'none' }}>Cover</span>
                <span style={{ flex: 1 }}><CoverBar woc={woc} proj={simProjW} /></span>
                <span style={{ fontSize: 11.5, flex: 'none' }}>
                  <b>{woc != null ? `${woc.toFixed(1)}w` : '—'}</b>
                  {simProjW != null && woc != null && simUnits > 0 && <span style={{ color: 'var(--good)', fontWeight: 700 }}> → {simProjW.toFixed(1)}w</span>}
                </span>
              </div>

              {buyLink && wd > 0 ? (
                <>
                  <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 14, flexWrap: 'wrap' }}>
                    <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>If I buy</span>
                    <span className="stepper">
                      <button onClick={() => setSimPacks(Math.max(0, packs - inc))}>−</button>
                      <b>{packs}</b>
                      <button onClick={() => setSimPacks(packs + inc)}>+</button>
                    </span>
                    <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>{plu(packUnit)} <span style={{ color: 'var(--faint)' }}>({simUnits} {plu(uom)})</span> from {buyLink.name}</span>
                  </div>
                  <div className="slider">
                    {(() => {
                      // Coincident ticks merge into one label; labels that would
                      // still crowd drop to a second row below the rail.
                      const shown = sliderTicks.filter(t => t.at <= simMax).sort((a, b) => a.at - b.at)
                      const merged: { pct: number; label: string }[] = []
                      for (const t of shown) {
                        const pct = (t.at / simMax) * 100
                        const last = merged[merged.length - 1]
                        if (last && pct - last.pct < 4) last.label += ` · ${t.label}`
                        else merged.push({ pct, label: t.label })
                      }
                      const rowEnd = [-100, -100]
                      return merged.map((t, i) => {
                        const width = t.label.length * 1.6
                        const row = t.pct - width / 2 > rowEnd[0] ? 0 : 1
                        rowEnd[row] = t.pct + width / 2
                        return (
                          <span key={i}>
                            <span className={`ticklab${row === 1 ? ' b2' : ''}`} style={{ left: `${t.pct}%` }}>{t.label}</span>
                            <i className="tickm" style={{ left: `${t.pct}%` }} />
                          </span>
                        )
                      })
                    })()}
                    <i className="rail" />
                    <i className="fillr" style={{ width: `${Math.min(100, (packs / simMax) * 100)}%` }} />
                    <i className="knob" style={{ left: `${Math.min(100, (packs / simMax) * 100)}%` }} />
                    <input type="range" min={0} max={simMax} step={inc} value={packs} onChange={e => setSimPacks(parseInt(e.target.value, 10))} aria-label="packs to buy" />
                  </div>
                  <div className="simbox">
                    {packs === 0 ? 'Drag to project the resulting weeks of cover.' : (
                      <>
                        {simUnits} {plu(uom)} · <b>{simLanded.cost != null ? hk(simLanded.cost * simUnits) : '—'}</b>
                        {simLanded.cost != null && <> at landed <b>{fm(simLanded.cost)}/{uom}</b></>}
                        {simLanded.tier ? <span style={{ color: 'var(--good)', fontWeight: 650 }}> ({simLanded.tier.min_qty}+ tier met ✓)</span>
                          : tiers.length > 0 ? <span style={{ color: 'var(--faint)' }}> (next tier at {tiers.find(t => (t.min_qty as number) > simUnits)?.min_qty ?? '—'} {plu(uom)})</span> : null}
                        {clinicNetAt(simLanded.cost) != null && <> · {clinicRow?.channel ?? 'clinic'} net at this cost <b style={{ color: (clinicNetAt(simLanded.cost) as number) >= floor ? 'var(--good)' : 'var(--amber)' }}>{((clinicNetAt(simLanded.cost) as number) * 100).toFixed(1)}%</b></>}
                      </>
                    )}
                  </div>
                </>
              ) : (
                <div style={{ marginTop: 12, fontSize: 12, color: 'var(--faint)' }}>Add a demand signal to plan a buy.</div>
              )}

              <div style={{ display: 'flex', gap: 10, alignItems: 'flex-end', marginTop: 14 }}>
                <span style={{ fontSize: 11.5, color: 'var(--muted)', flex: 'none' }}>120d</span>
                <span style={{ display: 'inline-flex', alignItems: 'flex-end', gap: 3, height: 30 }}>
                  {trend.map((t, i) => (
                    <i key={t.month} title={`${t.month}: ${t.units}`} style={{ width: 11, height: `${Math.max(8, (t.units / trendMax) * 100)}%`, background: i === trend.length - 1 ? 'var(--accent)' : '#D9DCF2', borderRadius: '2px 2px 0 0' }} />
                  ))}
                  {trend.length === 0 && <span style={{ fontSize: 11.5, color: 'var(--faint)' }}>no sales recorded</span>}
                </span>
                <span style={{ fontSize: 10.5, color: 'var(--faint)' }}>
                  {p.sales_120d} sold{wdbc ? ` · clinic ${Math.round(wdbc.clinic ?? 0)}/wk · shopify ${Math.round(wdbc.shopify ?? 0)} · hktv ${Math.round(wdbc.hktv ?? 0)}` : ''}
                </span>
              </div>
              <div style={{ marginTop: 10, fontSize: 11, color: 'var(--faint)' }}>Storage: {p.storage_rule === 'clinic_only' ? 'clinic only' : 'any location'}</div>
            </div>
          </div>
        </div>
      </div>

      {/* ── activity rail ── */}
      <div className="card" style={{ marginTop: 16 }}>
        <div className="ch" style={{ padding: '10px 15px' }}>
          <div className="ct" style={{ fontSize: 12.5 }}>Activity</div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center' }}>
            {!p.uom_verified_at && p.units_per_pack != null && <span className="bdg warn" style={{ fontSize: 9.5 }}>pack size unverified</span>}
            <Link className="lnk" style={{ fontSize: 12, textDecoration: 'none' }} to={'/admin/audit' as never}>full audit trail →</Link>
          </div>
        </div>
        <div className="activity">
          {events.length === 0 && <span style={{ color: 'var(--faint)' }}>No events recorded for this SKU yet.</span>}
          {(actOpen ? events : events.slice(0, 4)).map(e => (
            <span key={e.id}><b>{auditLabel(e.action)}</b>{auditSummary(e) ? ` — ${auditSummary(e)}` : ''} <span className="tw">{fmtDay(e.created_at)}{e.display_name ? ` · ${e.display_name}` : ''}</span></span>
          ))}
          {events.length > 4 && <span className="lnk" style={{ fontSize: 12 }} onClick={() => setActOpen(o => !o)}>{actOpen ? 'less ▴' : `all ${events.length} ▾`}</span>}
        </div>
        <div style={{ padding: '8px 15px', borderTop: '1px solid var(--line2)', fontSize: 11.5, color: 'var(--muted)', display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'baseline' }}>
          <span>Cost {SRC_WORDS[costSrc]}{costSince ? ` · ${fmtDay(costSince)}` : ''}</span>
          <span>· {p.uom_verified_at ? `pack verified ${fmtDay(p.uom_verified_at)}${p.uom_verified_by ? ` by ${p.uom_verified_by}` : ''}` : 'pack unverified'}</span>
          {(p.tags ?? []).slice(0, 4).map(t => <span key={t} className="bdg neu" style={{ fontSize: 9.5 }}>{t}</span>)}
        </div>
      </div>

      {/* ── footer ── */}
      <div className="footbar">
        <div className="l">
          <span className="btn soon" title="Purchase orders aren't wired up yet — coming with the procurement flow">Create PO <i className="soonchip">SOON</i></span>
          <Link className="btn" to={'/pricing' as never}>Pricing matrix</Link>
        </div>
        <div className="r" style={{ alignItems: 'center' }}>
          <span style={{ fontSize: 11, color: 'var(--faint)' }}>press <span className="kbd">?</span> for shortcuts</span>
          {editable && <UnverifyButton p={p} sku={path} onProduct={onProduct} />}
        </div>
      </div>

      {/* ── editors ── */}
      {editing && <EditSkuModal product={p} onSaved={onProduct} onClose={() => setEditing(false)} />}
      {changingSku && <ChangeSkuModal product={p} onClose={() => setChangingSku(false)} />}
      {recordFor && <RecordCostDialog sku={path} link={recordFor} unitNow={unitCostOf(recordFor)} clinicNetAt={clinicNetAt} floor={floor} uom={uom} packUnit={packUnit} onProduct={onProduct} onClose={() => setRecordFor(null)} />}
      {drawerFor && <OfferingDrawer sku={path} p={p} link={drawerFor} full={fullById.get(drawerFor.id) ?? null} packLabel={entryFor(drawerFor)?.packaging?.purchase_uom ?? p.pack_unit ?? null} linkCount={links.length} onProduct={onProduct} onClose={() => setDrawerFor(null)} />}
      {adding && <AddSupplierDrawer sku={path} p={p} onProduct={onProduct} onClose={() => setAdding(false)} />}
      {availFor && <AvailabilityDialog sku={path} link={availFor} onProduct={onProduct} onClose={() => setAvailFor(null)} />}
      {(() => {
        const termsLink = termsFor != null ? links.find(l => l.id === termsFor) ?? null : null
        return termsLink && (
          <TermsBuilder sku={path} link={termsLink} unitNow={unitCostOf(termsLink)} upp={uppOf(termsLink)}
            clinicNetAt={clinicNetAt} clinicLabel={clinicRow?.channel ?? 'clinic'} floor={floor}
            uom={uom} packUnit={packUnit} onProduct={onProduct} onClose={() => setTermsFor(null)} />
        )
      })()}
      {adjusting && <StockAdjustDialog sku={path} uom={uom} onProduct={onProduct} onClose={() => setAdjusting(false)} />}
      {(() => {
        if (sellFor == null) return null
        const existing = p.channels.find(c => c.channel === sellFor) ?? null
        const ch = existing ?? {
          channel: sellFor as Product['channels'][number]['channel'], is_active: true, selling_price: null,
          has_dispensing_fee: false, channel_fee_pct: null, units_per_listing: null, order_multiple: null,
          gp_pct: null, recommendation: null, gap_pct: null,
        }
        const m = marginRows.find(x => x.channel === sellFor) ?? null
        return (
          <SellingItemEditor sku={path} ch={ch} create={!existing} fee={m?.channel_fee_pct ?? 0} delivery={m?.delivery_cost ?? 0}
            effCost={effCost} floor={floor} uom={uom} onProduct={onProduct} onClose={() => setSellFor(null)} />
        )
      })()}
      {shortcuts && (
        <div className="shorto" onClick={() => setShortcuts(false)}>
          <div className="box" onClick={e => e.stopPropagation()}>
            <div style={{ fontSize: 15, fontWeight: 700, marginBottom: 10 }}>Keyboard shortcuts</div>
            {[['E', 'Edit SKU details'], ['C', 'Record cost (default supplier)'], ['B', 'Bulk terms (default supplier)'], ['?', 'This overlay'], ['Esc', 'Close']].map(([k, v]) => (
              <div className="row" key={k}><span>{v}</span><span className="kbd">{k}</span></div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

// Unit labels like "Can(s)" or "tablets" are already plural-safe.
const plu = (u: string) => (/(\(s\)|s)$/i.test(u.trim()) ? u : `${u}s`)

// ── term wording (plain words, no jargon) ────────────────────────────────
function termDeal(t: MbbTerm, uom: string): string {
  switch (t.kind) {
    case 'buy_x_get_y': return `Buy ${t.min_qty ?? '?'} get ${t.free_qty ?? '?'} free`
    case 'spend_discount': return t.discount_pct != null ? `${(t.discount_pct * 100).toFixed(0)}% off order` : 'Order discount'
    default: return t.min_qty != null && t.min_qty > 1 ? 'Volume price' : `Everyday ${uom} price`
  }
}
function termRequires(t: MbbTerm, uom: string): string {
  if (t.min_qty && t.min_qty > 1) return `${t.min_qty}+ ${plu(uom)}`
  if (t.min_spend != null) return `HK$${t.min_spend.toFixed(0)} spend`
  return 'always'
}
// Saves column: positive = cheaper than base; a stale deal dearer than base says so.
function SavesCell({ saving }: { saving: number | null }) {
  if (saving == null) return <>—</>
  if (saving >= 0.05) return <span style={{ color: 'var(--good)', fontWeight: 650 }}>{saving.toFixed(1)}%</span>
  if (saving <= -0.05) return <span style={{ color: 'var(--amber)', fontWeight: 650 }}>{Math.abs(saving).toFixed(1)}% dearer</span>
  return <span style={{ color: 'var(--faint)' }}>same as base</span>
}

// Price ladder: each break as a dot on a quantity rail; "you" = simulator qty.
// Unconditional terms (min_qty ≤ 1) fold into the base step so dots never overlap.
function Ladder({ unit, terms, uom, youUnits }: { unit: number; terms: MbbTerm[]; uom: string; youUnits: number | null }) {
  const priced = terms.filter(t => t.effective_unit_cost != null)
  const always = priced.filter(t => t.min_qty == null || t.min_qty <= 1).map(t => t.effective_unit_cost as number)
  const base = Math.min(unit, ...always)
  const steps = [{ qty: 1, price: base, label: '@1' }, ...priced
    .filter(t => t.min_qty != null && t.min_qty > 1)
    .sort((a, b) => (a.min_qty as number) - (b.min_qty as number))
    .map(t => ({ qty: t.min_qty as number, price: t.effective_unit_cost as number, label: `${t.min_qty}+ ${plu(uom)}` }))]
  if (steps.length < 2) return null
  // Ordinal rail: breaks sit equidistant (quantities live in the labels), so
  // step labels can never collide no matter how skewed the quantities are.
  // The "you" marker interpolates between its two neighbouring breaks.
  const pos = (i: number) => 8 + (i / (steps.length - 1)) * 76
  const youPos = (() => {
    if (youUnits == null || youUnits <= 0) return null
    const next = steps.findIndex(st => youUnits < st.qty)
    if (next === -1) return Math.min(95, pos(steps.length - 1) + 4)
    if (next === 0) return Math.max(2, pos(0) - 4)
    const a = steps[next - 1], b = steps[next]
    const f = (youUnits - a.qty) / (b.qty - a.qty)
    return pos(next - 1) + f * (pos(next) - pos(next - 1))
  })()
  return (
    <span className="ladder" style={{ display: 'block' }}>
      <i className="rail" />
      {steps.map((s, i) => (
        <span key={i} className={`step${youUnits != null && youUnits >= s.qty ? ' hit' : ''}`} style={{ left: `${pos(i)}%` }}>
          <span className="pv">{s.price.toFixed(2)}</span><span className="pq"> {s.label}</span><i />
        </span>
      ))}
      {youPos != null && <span className="you" style={{ left: `${youPos}%` }} />}
    </span>
  )
}

// Inline channel price editing — live net preview, floor-aware, Enter saves.
function PriceCell({ sku, channel, price, cost, upl, fee, delivery, floor, editable, onProduct }: {
  sku: string; channel: string; price: number | null; cost: number | null; upl: number
  fee: number; delivery: number; floor: number; editable: boolean; onProduct: (p: Product) => void
}) {
  const [editingPrice, setEditingPrice] = useState(false)
  const [val, setVal] = useState('')
  const [busy, setBusy] = useState(false)
  if (!editingPrice) {
    return (
      <span className="prc">
        {fm(price)}{' '}
        {editable && <span className="lnk" style={{ fontSize: 10.5 }} onClick={() => { setVal(price != null ? String(price) : ''); setEditingPrice(true) }}>✎</span>}
      </span>
    )
  }
  const parsed = parseFloat(val)
  const per = upl > 1 ? upl : 1
  const preview = Number.isFinite(parsed) ? netAt(parsed / per, cost, fee, delivery / per) : null
  const save = async () => {
    if (!Number.isFinite(parsed) || parsed <= 0) { toast.error('Enter a price'); return }
    setBusy(true)
    const updated = await send('PATCH', `/products/${sku}/channels/${channel}/price`, { selling_price: parsed })
    setBusy(false)
    if (updated) { onProduct(updated); toast.success(`${channel} price set to ${fm(parsed)}`); setEditingPrice(false) }
  }
  return (
    <span style={{ textAlign: 'right' }}>
      <input className="fin" autoFocus style={{ width: 78, textAlign: 'right', padding: '4px 7px', fontSize: 12 }} value={val}
        onChange={e => setVal(e.target.value)}
        onKeyDown={e => { if (e.key === 'Enter') save(); if (e.key === 'Escape') setEditingPrice(false) }} />
      <button className="btn pri" style={{ padding: '4px 9px', fontSize: 10.5, marginLeft: 5 }} disabled={busy} onClick={save}>{busy ? '…' : 'Save'}</button>
      <span className="soldas" style={{ textAlign: 'right' }}>
        {preview != null ? <>{upl > 1 ? '≈ ' : ''}net {((preview) * 100).toFixed(1)}% {preview >= floor ? '✓' : `· floor ${(floor * 100).toFixed(0)}% ✗`}</> : 'Enter saves · Esc cancels'}
      </span>
    </span>
  )
}

// Record cost — append-only language with a live landed-unit preview.
function RecordCostDialog({ sku, link, unitNow, clinicNetAt, floor, uom, packUnit, onProduct, onClose }: {
  sku: string; link: SupplierLinkRow; unitNow: number | null
  clinicNetAt: (c: number | null) => number | null; floor: number
  uom: string; packUnit: string; onProduct: (p: Product) => void; onClose: () => void
}) {
  const basis = link.units_per_pack && link.units_per_pack > 1 ? link.units_per_pack : 1
  const [amount, setAmount] = useState('')
  const [per, setPer] = useState<'pack' | 'unit'>(basis > 1 ? 'pack' : 'unit')
  const [busy, setBusy] = useState(false)
  const a = parseFloat(amount)
  const packCost = Number.isFinite(a) ? (per === 'pack' ? a : a * basis) : null
  const unitLands = packCost != null ? packCost / basis : null
  const delta = unitLands != null && unitNow != null && unitNow > 0 ? ((unitLands - unitNow) / unitNow) * 100 : null
  const net = clinicNetAt(unitLands)
  const save = async () => {
    if (packCost == null || packCost <= 0) { toast.error('Enter the new price'); return }
    setBusy(true)
    const updated = await send('PATCH', `/products/${sku}/suppliers/${link.id}`, { basic_cost: packCost })
    setBusy(false)
    if (updated) { onProduct(updated); toast.success(`Recorded ${fm(unitLands)}/${uom} for ${link.name ?? 'supplier'}`); onClose() }
  }
  return (
    <div className="ovl" onClick={onClose}>
      <div className="dlg skud" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Record cost — {link.name ?? 'supplier'}</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: 20, color: 'var(--faint)', cursor: 'pointer' }}>×</button>
        </div>
        <p style={{ fontSize: 11.5, color: 'var(--faint)', margin: '3px 0 14px' }}>The current price stays in history — costs are recorded, not overwritten.</p>
        <div style={{ display: 'flex', gap: 10 }}>
          <label style={{ flex: 1 }}><span className="flab">New price (HK$)</span>
            <input className="fin" autoFocus type="number" value={amount} onChange={e => setAmount(e.target.value)} onKeyDown={e => e.key === 'Enter' && save()} />
          </label>
          <label style={{ flex: 1 }}><span className="flab">Priced per</span>
            <select className="fin" value={per} onChange={e => setPer(e.target.value as 'pack' | 'unit')}>
              {basis > 1 && <option value="pack">{packUnit} of {basis} {plu(uom)}</option>}
              <option value="unit">{uom}</option>
            </select>
          </label>
        </div>
        <div className="preview" style={{ marginTop: 12 }}>
          {unitLands == null ? 'Enter a price to preview the landed unit cost.' : (
            <>
              Unit lands at <b>HK${fm(unitLands)}/{uom}</b>{basis > 1 && per === 'pack' ? ` (${fm(packCost)} ÷ ${basis})` : ''}
              {delta != null && <span style={{ fontWeight: 700, color: delta <= 0 ? 'var(--good)' : 'var(--amber)' }}> · {delta > 0 ? '↑' : '↓'} {Math.abs(delta).toFixed(1)}% vs current</span>}
              {net != null && <><br /><span style={{ color: 'var(--muted)' }}>clinic net becomes {(net * 100).toFixed(1)}% {net >= floor ? '✓' : `(floor ${(floor * 100).toFixed(0)}%)`} · current price moves to history</span></>}
            </>
          )}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9, marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn pri" disabled={busy || unitLands == null} onClick={save}>{busy ? 'Recording…' : unitLands != null ? `Record HK$${fm(unitLands)}/${uom}` : 'Record'}</button>
        </div>
      </div>
    </div>
  )
}

// Offering drawer — link identity, packaging basis, ordering terms. Cost is
// deliberately absent: Record cost owns that, so changes land in history.
const UOM_OPTIONS = ['unit', 'each', 'piece', 'pack', 'box', 'case', 'carton', 'inner', 'outer', 'dozen', 'set', 'can', 'pouch', 'sachet', 'bottle', 'jar', 'tub', 'bag', 'tablet', 'capsule', 'strip', 'blister', 'vial', 'ampoule', 'tube', 'syringe', 'roll', 'ml', 'L', 'g', 'kg']
function OfferingDrawer({ sku, p, link, full, packLabel, linkCount, onProduct, onClose }: {
  sku: string; p: Product; link: SupplierLinkRow; full: SupFull | null; packLabel: string | null; linkCount: number
  onProduct: (pr: Product) => void; onClose: () => void
}) {
  const [f, setF] = useState({
    supplier_sku: link.supplier_sku ?? '',
    barcode: link.barcode ?? '',
    rrp: link.rrp != null ? String(link.rrp) : '',
    pack_unit: packLabel ?? '',
    units_per_pack: link.units_per_pack != null ? String(link.units_per_pack) : '',
    order_increment_qty: full?.order_increment_qty != null ? String(full.order_increment_qty) : '',
    order_increment_uom: full?.order_increment_uom ?? '',
    minimum_order_qty: full?.minimum_order_qty != null ? String(full.minimum_order_qty) : '',
    minimum_order_uom: full?.minimum_order_uom ?? '',
    minimum_order_source: full?.minimum_order_source ?? '',
    pricing_note: full?.pricing_note ?? '',
  })
  const [busy, setBusy] = useState(false)
  const uoms = (() => {
    const seen = new Set<string>(); const out: string[] = []
    for (const u of [p.uom, p.pack_unit, ...UOM_OPTIONS]) { const t = (u ?? '').trim(); if (t && !seen.has(t.toLowerCase())) { seen.add(t.toLowerCase()); out.push(t) } }
    return out
  })()
  const uomSelect = (value: string, onChange: (v: string) => void) => (
    <select className="fin" value={value} onChange={e => onChange(e.target.value)}>
      <option value="">—</option>
      {uoms.map(u => <option key={u} value={u}>{u}</option>)}
      {value && !uoms.includes(value) && <option value={value}>{value}</option>}
    </select>
  )
  const intOr = (s: string) => { const n = parseInt(s, 10); return s.trim() !== '' && Number.isFinite(n) ? n : null }
  const strOr = (s: string) => (s.trim() === '' ? null : s.trim())
  const save = async () => {
    if (intOr(f.order_increment_qty) != null && !strOr(f.order_increment_uom)) { toast.error('Order increment UOM is required when a qty is set'); return }
    if (intOr(f.minimum_order_qty) != null && !strOr(f.minimum_order_uom)) { toast.error('Minimum order UOM is required when a qty is set'); return }
    setBusy(true)
    const body: Record<string, unknown> = {
      supplier_sku: strOr(f.supplier_sku), barcode: strOr(f.barcode),
      rrp: f.rrp.trim() === '' ? null : parseFloat(f.rrp),
      pack_unit: strOr(f.pack_unit),
      order_increment_qty: intOr(f.order_increment_qty), order_increment_uom: strOr(f.order_increment_uom),
      minimum_order_qty: intOr(f.minimum_order_qty), minimum_order_uom: strOr(f.minimum_order_uom),
      minimum_order_source: strOr(f.minimum_order_source), pricing_note: strOr(f.pricing_note),
    }
    if (f.units_per_pack.trim() !== '') body.units_per_pack = intOr(f.units_per_pack)
    const updated = await send('PATCH', `/products/${sku}/suppliers/${link.id}`, body)
    setBusy(false)
    if (updated) { onProduct(updated); toast.success('Offering saved'); onClose() }
  }
  const makeDefault = async () => {
    const updated = await send('PATCH', `/products/${sku}/suppliers/${link.id}`, { is_primary: true })
    if (updated) { onProduct(updated); toast.success(`${link.name ?? 'Supplier'} is now the default`) }
  }
  const remove = async () => {
    const ok = await confirmDialog({ title: 'Remove supplier?', message: `Unlink ${link.name ?? 'this supplier'} from ${p.sku_code}? Its cost history stays in the audit trail.`, confirmLabel: 'Remove', danger: true })
    if (!ok) return
    const updated = await send('DELETE', `/products/${sku}/suppliers/${link.id}`)
    if (updated) { onProduct(updated); toast.success('Supplier removed'); onClose() }
  }
  return (
    <>
      <div className="ovl" style={{ padding: 0 }} onClick={onClose} />
      <div className="skud-drawer skud" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Edit offering — {link.name ?? 'supplier'}</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: 20, color: 'var(--faint)', cursor: 'pointer' }}>×</button>
        </div>
        <p style={{ fontSize: 11.5, color: 'var(--faint)', margin: '3px 0 14px' }}>{p.sku_code} · {link.is_primary ? 'default supplier' : link.code ?? ''}</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 11 }}>
          <label><span className="flab">Supplier SKU</span><input className="fin" value={f.supplier_sku} onChange={e => setF({ ...f, supplier_sku: e.target.value })} /></label>
          <label><span className="flab">Barcode</span><input className="fin" value={f.barcode} onChange={e => setF({ ...f, barcode: e.target.value })} /></label>
          <label><span className="flab">RRP (HK$, this supplier’s)</span><input className="fin" type="number" value={f.rrp} onChange={e => setF({ ...f, rrp: e.target.value })} /></label>
        </div>
        <div style={{ margin: '14px 0 6px', fontSize: 10, fontWeight: 750, letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--faint)' }}>Packaging — this supplier’s</div>
        <div style={{ display: 'flex', gap: 9, alignItems: 'flex-end' }}>
          <label style={{ flex: 1 }}><span className="flab">Purchase unit</span><input className="fin" placeholder="e.g. tray, case, bottle" value={f.pack_unit} onChange={e => setF({ ...f, pack_unit: e.target.value })} /></label>
          <span style={{ fontSize: 11.5, color: 'var(--faint)', paddingBottom: 8 }}>contains</span>
          <label style={{ flex: 0.7 }}><span className="flab">{p.uom ? plu(p.uom) : 'sell units'}</span><input className="fin" type="number" value={f.units_per_pack} onChange={e => setF({ ...f, units_per_pack: e.target.value })} /></label>
        </div>
        <div style={{ margin: '14px 0 6px', fontSize: 10, fontWeight: 750, letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--faint)' }}>Ordering</div>
        <div style={{ display: 'grid', gridTemplateColumns: '0.8fr 1fr 0.8fr 1fr', gap: 9 }}>
          <label><span className="flab">Increment</span><input className="fin" type="number" value={f.order_increment_qty} onChange={e => setF({ ...f, order_increment_qty: e.target.value })} /></label>
          <label><span className="flab">Increment UOM</span>{uomSelect(f.order_increment_uom, v => setF({ ...f, order_increment_uom: v }))}</label>
          <label><span className="flab">Min order</span><input className="fin" type="number" value={f.minimum_order_qty} onChange={e => setF({ ...f, minimum_order_qty: e.target.value })} /></label>
          <label><span className="flab">Min order UOM</span>{uomSelect(f.minimum_order_uom, v => setF({ ...f, minimum_order_uom: v }))}</label>
        </div>
        <label style={{ display: 'block', marginTop: 10 }}><span className="flab">Source</span>
          <select className="fin" value={f.minimum_order_source} onChange={e => setF({ ...f, minimum_order_source: e.target.value })}>
            <option value="">—</option><option value="catalogue">catalogue</option><option value="inferred_from_order_multiple">inferred from multiple</option><option value="manual">manual</option>
          </select>
        </label>
        <label style={{ display: 'block', marginTop: 10 }}><span className="flab">Pricing note</span>
          <input className="fin" placeholder="e.g. price is per box of 8 tests" value={f.pricing_note} onChange={e => setF({ ...f, pricing_note: e.target.value })} />
        </label>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: 18, gap: 10, flexWrap: 'wrap' }}>
          <span style={{ display: 'flex', gap: 12 }}>
            {!link.is_primary && <span className="lnk" style={{ fontSize: 12 }} onClick={makeDefault}>★ Make default</span>}
            <span className="lnk" style={{ fontSize: 12, color: 'var(--red)' }} onClick={remove} title={linkCount <= 1 ? 'A SKU must keep at least one supplier' : ''}>Remove…</span>
          </span>
          <span style={{ display: 'flex', gap: 9 }}>
            <button className="btn" onClick={onClose}>Cancel</button>
            <button className="btn pri" disabled={busy} onClick={save}>{busy ? 'Saving…' : 'Save'}</button>
          </span>
        </div>
        <p style={{ fontSize: 10.5, color: 'var(--faint)', marginTop: 12 }}>Cost isn’t edited here — use <b>Record cost</b> so the change lands in price history. Availability has its own control on the row.</p>
      </div>
    </>
  )
}

function AddSupplierDrawer({ sku, p, onProduct, onClose }: { sku: string; p: Product; onProduct: (pr: Product) => void; onClose: () => void }) {
  const [opts, setOpts] = useState<{ id: number; code: string; name: string }[]>([])
  const [f, setF] = useState({ supplier_id: '', supplier_sku: '', cost: '', units: '', rrp: '', pack_unit: '' })
  const [busy, setBusy] = useState(false)
  useEffect(() => { fetch(`${API}/suppliers`, { headers: authHeaders() }).then(r => r.ok ? r.json() : []).then(setOpts).catch(() => {}) }, [])
  const linked = new Set(p.all_suppliers.map(s => s.supplier_id))
  const available = opts.filter(o => !linked.has(o.id))
  const save = async () => {
    if (!f.supplier_id) { toast.error('Pick a supplier'); return }
    setBusy(true)
    const body: Record<string, unknown> = { supplier_id: parseInt(f.supplier_id, 10), supplier_sku: f.supplier_sku.trim() }
    if (f.cost.trim() !== '') body.basic_cost = parseFloat(f.cost)
    if (f.units.trim() !== '') body.units_per_pack = parseInt(f.units, 10)
    if (f.rrp.trim() !== '') body.rrp = parseFloat(f.rrp)
    if (f.pack_unit.trim() !== '') body.pack_unit = f.pack_unit.trim()
    const updated = await send('POST', `/products/${sku}/suppliers`, body)
    setBusy(false)
    if (updated) { onProduct(updated); toast.success('Supplier added'); onClose() }
  }
  return (
    <>
      <div className="ovl" style={{ padding: 0 }} onClick={onClose} />
      <div className="skud-drawer skud">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Add supplier</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: 20, color: 'var(--faint)', cursor: 'pointer' }}>×</button>
        </div>
        <p style={{ fontSize: 11.5, color: 'var(--faint)', margin: '3px 0 14px' }}>{p.sku_code}</p>
        <label style={{ display: 'block' }}><span className="flab">Supplier</span>
          <select className="fin" value={f.supplier_id} onChange={e => setF({ ...f, supplier_id: e.target.value })}>
            <option value="">— pick —</option>
            {available.map(o => <option key={o.id} value={o.id}>{o.name} ({o.code})</option>)}
          </select>
        </label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 10, marginTop: 10 }}>
          <label><span className="flab">Supplier SKU</span><input className="fin" value={f.supplier_sku} onChange={e => setF({ ...f, supplier_sku: e.target.value })} /></label>
          <label><span className="flab">Purchase unit</span><input className="fin" placeholder="e.g. tray" value={f.pack_unit} onChange={e => setF({ ...f, pack_unit: e.target.value })} /></label>
          <label><span className="flab">Cost (HK$, whole {f.pack_unit.trim() || p.pack_unit || 'pack'})</span><input className="fin" type="number" value={f.cost} onChange={e => setF({ ...f, cost: e.target.value })} /></label>
          <label><span className="flab">Units in that price</span><input className="fin" type="number" value={f.units} onChange={e => setF({ ...f, units: e.target.value })} /></label>
          <label><span className="flab">RRP (optional)</span><input className="fin" type="number" value={f.rrp} onChange={e => setF({ ...f, rrp: e.target.value })} /></label>
        </div>
        {f.cost.trim() !== '' && (
          <div className="preview" style={{ marginTop: 11 }}>
            {(() => { const c = parseFloat(f.cost); const u = parseInt(f.units || '1', 10) || 1; return Number.isFinite(c) ? <>Unit lands at <b>HK${fm(c / (u > 1 ? u : 1))}/{p.uom ?? 'unit'}</b> — recorded as this supplier’s first price.</> : null })()}
          </div>
        )}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9, marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn pri" disabled={busy || !f.supplier_id} onClick={save}>{busy ? 'Adding…' : 'Add supplier'}</button>
        </div>
      </div>
    </>
  )
}

function AvailabilityDialog({ sku, link, onProduct, onClose }: { sku: string; link: SupplierLinkRow; onProduct: (p: Product) => void; onClose: () => void }) {
  const [restock, setRestock] = useState(link.expected_restock_at ?? '')
  const [note, setNote] = useState(link.stock_note ?? '')
  const [busy, setBusy] = useState(false)
  const oos = link.stock_status === 'out_of_stock'
  const set = async (status: 'in_stock' | 'out_of_stock') => {
    setBusy(true)
    const updated = await send('PATCH', `/products/${sku}/suppliers/${link.id}/stock`, { status, expected_restock_at: restock.trim() || null, note: note.trim() || null })
    setBusy(false)
    if (updated) { onProduct(updated); toast.success(status === 'out_of_stock' ? 'Marked out of stock' : 'Back in stock'); onClose() }
  }
  const closed = (link.stock_events ?? []).filter(e => e.restock_at != null)
  return (
    <div className="ovl" onClick={onClose}>
      <div className="dlg skud" onClick={e => e.stopPropagation()} style={{ width: 460 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Availability — {link.name ?? 'supplier'}</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: 20, color: 'var(--faint)', cursor: 'pointer' }}>×</button>
        </div>
        <p style={{ fontSize: 11.5, margin: '6px 0 12px' }}>
          Currently {oos ? <b style={{ color: 'var(--red)' }}>out of stock{link.reported_out_at ? ` since ${fmtDay(link.reported_out_at)}` : ''}</b> : <b style={{ color: 'var(--good)' }}>in stock</b>}
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1.4fr', gap: 10 }}>
          <label><span className="flab">Expected restock</span><input className="fin" placeholder="YYYY-MM-DD" value={restock} onChange={e => setRestock(e.target.value)} /></label>
          <label><span className="flab">Note</span><input className="fin" value={note} onChange={e => setNote(e.target.value)} /></label>
        </div>
        {closed.length > 0 && (
          <details style={{ marginTop: 10, fontSize: 11.5, color: 'var(--muted)' }}>
            <summary style={{ cursor: 'pointer', color: 'var(--accent)', fontWeight: 600 }}>Past out-of-stock periods ({closed.length})</summary>
            {closed.map((e, i) => <div key={i} style={{ padding: '3px 0' }}>{fmtDay(e.out_at)} → {fmtDay(e.restock_at)}{e.days != null ? ` · ${e.days}d` : ''}{e.note ? ` · ${e.note}` : ''}</div>)}
          </details>
        )}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9, marginTop: 16, flexWrap: 'wrap' }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          {oos ? (
            <>
              <button className="btn" disabled={busy} onClick={() => set('out_of_stock')}>Update details</button>
              <button className="btn pri" disabled={busy} onClick={() => set('in_stock')}>Back in stock</button>
            </>
          ) : (
            <button className="btn danger" disabled={busy} onClick={() => set('out_of_stock')}>Mark out of stock</button>
          )}
        </div>
      </div>
    </div>
  )
}

// Bulk deals — conditional prices on one supplier's offering, edited in the
// supplier's own language. Two families with different data-model behavior:
// fixed prices (tier / flat: an absolute unit price) and derived deals
// (free goods / order discount: computed FROM the base cost, so they move
// when a new cost is recorded). Quantities are quoted per sell unit or per
// pack and normalized to sell units — the basis every margin and simulator
// calculation runs on. A deal's shape is fixed after creation (the API
// PATCH can't clear fields across shapes); changing shape = replace.
type DealShape = 'volume' | 'everyday' | 'freegoods' | 'discount'
const SHAPES: { key: DealShape; kinds: string[]; name: string; desc: string; relative: boolean }[] = [
  { key: 'volume', kinds: ['tier'], name: 'Volume price', desc: 'Buying at least N drops the unit price', relative: false },
  { key: 'everyday', kinds: ['flat_unit_cost'], name: 'Everyday price', desc: 'A flat unit price, no minimum', relative: false },
  { key: 'freegoods', kinds: ['buy_x_get_y'], name: 'Free goods', desc: 'Buy N, get M free — derived from base cost', relative: true },
  { key: 'discount', kinds: ['spend_discount'], name: 'Order discount', desc: 'Spend HK$S → P% off — derived from base cost', relative: true },
]
const shapeOfKind = (kind: string, minQty: number | null): DealShape =>
  kind === 'buy_x_get_y' ? 'freegoods' : kind === 'spend_discount' ? 'discount'
  : kind === 'flat_unit_cost' && (minQty == null || minQty <= 1) ? 'everyday' : 'volume'

function TermsBuilder({ sku, link, unitNow, upp, clinicNetAt, clinicLabel, floor, uom, packUnit, onProduct, onClose }: {
  sku: string; link: SupplierLinkRow; unitNow: number | null; upp: number
  clinicNetAt: (c: number | null) => number | null; clinicLabel: string; floor: number
  uom: string; packUnit: string; onProduct: (p: Product) => void; onClose: () => void
}) {
  const blank = { qty: '', qtyBasis: 'unit' as 'unit' | 'pack', price: '', priceBasis: 'unit' as 'unit' | 'pack', buyN: '', freeM: '', spend: '', pct: '', note: '' }
  const [shape, setShape] = useState<DealShape>('volume')
  const [f, setF] = useState(blank)
  const [editId, setEditId] = useState<number | null>(null)
  const [editKind, setEditKind] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const terms = link.mbb_term_list ?? []
  const canPack = upp > 1
  const num = (s: string) => { const n = parseFloat(s); return s.trim() !== '' && Number.isFinite(n) ? n : null }
  const int = (s: string) => { const n = parseInt(s, 10); return s.trim() !== '' && Number.isFinite(n) ? n : null }

  // Normalization to sell units — what the server stores and margins consume.
  const unitsPer = (basis: 'unit' | 'pack') => (basis === 'pack' ? upp : 1)
  const normQty = (() => { const q = int(f.qty); return q != null ? q * unitsPer(f.qtyBasis) : null })()
  const normPrice = (() => { const v = num(f.price); return v != null ? v / unitsPer(f.priceBasis) : null })()

  const effPreview = (() => {
    if (shape === 'volume' || shape === 'everyday') return normPrice
    if (shape === 'freegoods') { const n = int(f.buyN), m = int(f.freeM); return n && m && unitNow != null ? unitNow * (n / (n + m)) : null }
    const pc = num(f.pct); return pc != null && unitNow != null ? unitNow * (1 - pc / 100) : null
  })()
  const saving = effPreview != null && unitNow != null && unitNow > 0 ? (1 - effPreview / unitNow) * 100 : null
  const previewNet = clinicNetAt(effPreview)
  const isRelative = shape === 'freegoods' || shape === 'discount'

  const reset = () => { setF(blank); setEditId(null); setEditKind(null) }
  const startEdit = (t: MbbTerm) => {
    const s = shapeOfKind(t.kind, t.min_qty)
    setShape(s); setEditId(t.id); setEditKind(t.kind)
    setF({
      ...blank,
      qty: t.min_qty != null ? String(t.min_qty) : '', qtyBasis: 'unit',
      price: t.unit_cost != null ? String(t.unit_cost) : '', priceBasis: 'unit',
      buyN: t.min_qty != null ? String(t.min_qty) : '', freeM: t.free_qty != null ? String(t.free_qty) : '',
      spend: t.min_spend != null ? String(t.min_spend) : '', pct: t.discount_pct != null ? String(+(t.discount_pct * 100).toFixed(2)) : '',
      note: t.note ?? '',
    })
  }

  const submit = async () => {
    const body: Record<string, unknown> = { note: f.note.trim() || undefined }
    if (shape === 'volume') {
      if (normQty == null || normQty <= 1) { toast.error('Volume price needs a minimum of 2+ — use Everyday price for no minimum'); return }
      if (normPrice == null || normPrice <= 0) { toast.error('Enter the unit price the volume unlocks'); return }
      body.kind = editKind ?? 'tier'; body.min_qty = normQty; body.unit_cost = normPrice
    } else if (shape === 'everyday') {
      if (normPrice == null || normPrice <= 0) { toast.error('Enter the flat unit price'); return }
      body.kind = editKind ?? 'flat_unit_cost'; body.unit_cost = normPrice
    } else if (shape === 'freegoods') {
      const n = int(f.buyN), m = int(f.freeM)
      if (!n || !m) { toast.error('Enter both quantities — buy N, get M free'); return }
      body.kind = 'buy_x_get_y'; body.min_qty = n; body.free_qty = m
    } else {
      const s = num(f.spend), pc = num(f.pct)
      if (s == null || pc == null) { toast.error('Enter the minimum spend and the discount %'); return }
      body.kind = 'spend_discount'; body.min_spend = s; body.discount_pct = pc / 100
    }
    setBusy(true)
    const updated = editId == null
      ? await send('POST', `/products/${sku}/suppliers/${link.id}/mbb-terms`, body)
      : await send('PATCH', `/products/${sku}/suppliers/${link.id}/mbb-terms/${editId}`, body)
    setBusy(false)
    if (updated) { onProduct(updated); toast.success(editId == null ? 'Deal added' : 'Deal updated'); reset() }
  }
  const del = async (tid: number) => {
    const ok = await confirmDialog({ title: 'Remove deal?', message: 'Remove this deal from the supplier?', confirmLabel: 'Remove', danger: true })
    if (!ok) return
    const updated = await send('DELETE', `/products/${sku}/suppliers/${link.id}/mbb-terms/${tid}`)
    if (updated) { onProduct(updated); toast.success('Deal removed'); if (editId === tid) reset() }
  }

  // Live ladder: saved deals, with the draft substituted for the one being edited.
  const ladderTerms: MbbTerm[] = (() => {
    const rest = terms.filter(t => t.id !== editId)
    if (effPreview == null) return rest
    const draft: MbbTerm = {
      id: -1, kind: shape === 'freegoods' ? 'buy_x_get_y' : shape === 'discount' ? 'spend_discount' : 'tier',
      min_qty: shape === 'volume' ? normQty : shape === 'freegoods' ? int(f.buyN) : null,
      min_spend: shape === 'discount' ? num(f.spend) : null, free_qty: shape === 'freegoods' ? int(f.freeM) : null,
      discount_pct: null, unit_cost: null, note: null, sort_order: 99, effective_unit_cost: effPreview,
    }
    return [...rest, draft]
  })()

  const basisSel = (v: 'unit' | 'pack', on: (x: 'unit' | 'pack') => void) => (
    <select className="si" value={v} onChange={e => on(e.target.value as 'unit' | 'pack')}>
      <option value="unit">{plu(uom)}</option>
      {canPack && <option value="pack">{plu(packUnit)} of {upp}</option>}
    </select>
  )
  const dealSentence = (t: MbbTerm): React.ReactNode => {
    const s = shapeOfKind(t.kind, t.min_qty)
    if (s === 'volume') return <>{t.min_qty}+ {plu(uom)} → <b>{fm(t.effective_unit_cost)}</b>/{uom}</>
    if (s === 'everyday') return <>Every {uom} at <b>{fm(t.effective_unit_cost)}</b> — no minimum</>
    if (s === 'freegoods') return <>Buy {t.min_qty ?? '?'} get {t.free_qty ?? '?'} free → lands <b>{fm(t.effective_unit_cost)}</b>/{uom}</>
    return <>Spend {hk(t.min_spend)}+ → {t.discount_pct != null ? (t.discount_pct * 100).toFixed(0) : '?'}% off → lands <b>{fm(t.effective_unit_cost)}</b>/{uom}</>
  }

  return (
    <div className="ovl" onClick={onClose}>
      <div className="dlg skud" onClick={e => e.stopPropagation()} style={{ width: 640 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Bulk deals — {link.name ?? 'supplier'}</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: 20, color: 'var(--faint)', cursor: 'pointer' }}>×</button>
        </div>
        <p style={{ fontSize: 11.5, color: 'var(--faint)', margin: '3px 0 12px' }}>
          Base cost {fm(unitNow)}/{uom}{canPack ? ` · ${packUnit} of ${upp} ${plu(uom)}` : ''} · deals belong to this supplier only
        </p>

        {unitNow != null && ladderTerms.length > 0 && <Ladder unit={unitNow} terms={ladderTerms} uom={uom} youUnits={null} />}

        {terms.length > 0 && (
          <div style={{ margin: '10px 0 14px' }}>
            {terms.map(t => {
              const s = shapeOfKind(t.kind, t.min_qty)
              const rel = s === 'freegoods' || s === 'discount'
              const sv = t.effective_unit_cost != null && unitNow != null && unitNow > 0 ? (1 - t.effective_unit_cost / unitNow) * 100 : null
              const n = clinicNetAt(t.effective_unit_cost ?? null)
              return (
                <div key={t.id} className={`dealcard${editId === t.id ? ' editing' : ''}`}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div className="ds">{dealSentence(t)}</div>
                    <div className="dm">
                      {sv != null && (sv >= 0.05 ? <span style={{ color: 'var(--good)', fontWeight: 650 }}>saves {sv.toFixed(1)}%</span> : sv <= -0.05 ? <span style={{ color: 'var(--amber)', fontWeight: 650 }}>{Math.abs(sv).toFixed(1)}% dearer than base</span> : <span style={{ color: 'var(--faint)' }}>same as base</span>)}
                      {n != null && <> · {clinicLabel} net <span style={{ fontWeight: 650, color: n >= floor ? 'var(--good)' : 'var(--amber)' }}>{(n * 100).toFixed(1)}%</span></>}
                      {t.note && <> · {t.note}</>}
                    </div>
                  </div>
                  <span className={`kindchip ${rel ? 'rel' : 'fx'}`} title={rel ? 'Recalculates whenever a new base cost is recorded' : 'An absolute price — unaffected by base-cost changes'}>{rel ? 'TRACKS BASE' : 'FIXED PRICE'}</span>
                  <span style={{ whiteSpace: 'nowrap' }}>
                    <span className="lnk" style={{ fontSize: 11 }} onClick={() => startEdit(t)}>edit</span>{' '}
                    <span className="lnk" style={{ fontSize: 11, color: 'var(--red)' }} onClick={() => del(t.id)}>remove</span>
                  </span>
                </div>
              )
            })}
          </div>
        )}

        <div style={{ borderTop: '1px dashed var(--line)', paddingTop: 12 }}>
          <div style={{ fontSize: 10, fontWeight: 750, letterSpacing: '.05em', textTransform: 'uppercase', color: 'var(--faint)', marginBottom: 7 }}>
            {editId == null ? 'Add a deal — what did the supplier offer?' : 'Edit deal values'}
          </div>
          {editId == null ? (
            <div className="shapegrid">
              {SHAPES.map(s => (
                <div key={s.key} className={`shape${shape === s.key ? ' on' : ''}`} onClick={() => { setShape(s.key); setF(blank) }}>
                  <div className="sn">{s.name}{s.relative && <span className="kindchip rel" style={{ marginLeft: 6 }}>TRACKS BASE</span>}</div>
                  <div className="sd">{s.desc}</div>
                </div>
              ))}
            </div>
          ) : (
            <div className="lockline">Shape is fixed after creation ({SHAPES.find(s => s.key === shape)?.name}). To change the deal's shape, remove it and add a new one.</div>
          )}

          <div className="sentence">
            {shape === 'volume' && (
              <>Buying <input className="si" style={{ width: 62 }} value={f.qty} onChange={e => setF({ ...f, qty: e.target.value })} placeholder="24" />
                {basisSel(f.qtyBasis, x => setF({ ...f, qtyBasis: x }))} or more drops the price to{' '}
                <input className="si" value={f.price} onChange={e => setF({ ...f, price: e.target.value })} placeholder="12.10" /> per {basisSel(f.priceBasis, x => setF({ ...f, priceBasis: x }))}
                {(f.qtyBasis === 'pack' || f.priceBasis === 'pack') && normQty != null && normPrice != null && (
                  <div className="normline">stored as {normQty}+ {plu(uom)} at {fm(normPrice)}/{uom} — the unit basis all margins use</div>
                )}
              </>
            )}
            {shape === 'everyday' && (
              <>Every {basisSel(f.priceBasis, x => setF({ ...f, priceBasis: x }))} costs <input className="si" value={f.price} onChange={e => setF({ ...f, price: e.target.value })} placeholder="0.61" /> — no minimum
                {f.priceBasis === 'pack' && normPrice != null && <div className="normline">stored as {fm(normPrice)}/{uom}</div>}
              </>
            )}
            {shape === 'freegoods' && (
              <>Buy <input className="si" style={{ width: 58 }} value={f.buyN} onChange={e => setF({ ...f, buyN: e.target.value })} placeholder="10" />, get{' '}
                <input className="si" style={{ width: 58 }} value={f.freeM} onChange={e => setF({ ...f, freeM: e.target.value })} placeholder="1" /> free
                <span style={{ fontSize: 11, color: 'var(--faint)' }}> (counted in the same unit — only the ratio matters)</span>
              </>
            )}
            {shape === 'discount' && (
              <>Spending HK$<input className="si" style={{ width: 84 }} value={f.spend} onChange={e => setF({ ...f, spend: e.target.value })} placeholder="2000" /> or more takes{' '}
                <input className="si" style={{ width: 58 }} value={f.pct} onChange={e => setF({ ...f, pct: e.target.value })} placeholder="5" />% off the order</>
            )}
          </div>
          <input className="fin" style={{ marginTop: 8 }} placeholder="Note (optional) — e.g. Q3 promo, confirmed by rep" value={f.note} onChange={e => setF({ ...f, note: e.target.value })} />

          <div className="preview" style={{ marginTop: 10 }}>
            {effPreview == null ? (isRelative && unitNow == null ? 'This deal derives from the base cost — record a cost first.' : 'Fill the sentence to preview where the unit lands.') : (
              <>Unit lands at <b>HK${fm(effPreview)}/{uom}</b>
                {saving != null && <span style={{ color: saving >= 0 ? 'var(--good)' : 'var(--amber)', fontWeight: 700 }}> · {saving >= 0 ? `saves ${saving.toFixed(1)}%` : `${Math.abs(saving).toFixed(1)}% dearer than base`}</span>}
                {previewNet != null && <> · {clinicLabel} net <b style={{ color: previewNet >= floor ? 'var(--good)' : 'var(--amber)' }}>{(previewNet * 100).toFixed(1)}%</b>{previewNet < floor ? ` (floor ${(floor * 100).toFixed(0)}%)` : ' ✓'}</>}
                {isRelative && <><br /><span style={{ color: 'var(--muted)' }}>derived from base {fm(unitNow)} — recalculates automatically when a new cost is recorded</span></>}
              </>
            )}
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9, marginTop: 12 }}>
            {editId != null && <button className="btn" onClick={reset}>Cancel edit</button>}
            <button className="btn" onClick={onClose}>Done</button>
            <button className="btn pri" disabled={busy} onClick={submit}>{busy ? 'Saving…' : editId == null ? 'Add deal' : 'Save deal'}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// Selling-item editor — one channel's own configuration: visibility, price,
// listing multiple (the selling order multiple) and dispensing fee. Prices
// are quoted per LISTING; the preview always restates the per-unit
// equivalent, since margins and comparisons run on sell units.
function SellingItemEditor({ sku, ch, create, fee, delivery, effCost, floor, uom, onProduct, onClose }: {
  sku: string; ch: Product['channels'][number]; create?: boolean; fee: number; delivery: number
  effCost: number | null; floor: number; uom: string; onProduct: (p: Product) => void; onClose: () => void
}) {
  const [f, setF] = useState({
    active: ch.is_active,
    price: ch.selling_price != null ? String(ch.selling_price) : '',
    upl: ch.units_per_listing != null && ch.units_per_listing > 1 ? String(ch.units_per_listing) : '',
    om: ch.order_multiple != null && ch.order_multiple > 1 ? String(ch.order_multiple) : '',
    disp: ch.has_dispensing_fee,
  })
  const [busy, setBusy] = useState(false)
  const price = (() => { const n = parseFloat(f.price); return f.price.trim() !== '' && Number.isFinite(n) ? n : null })()
  const upl = (() => { const n = parseInt(f.upl, 10); return f.upl.trim() !== '' && Number.isFinite(n) && n > 1 ? n : 1 })()
  const om = (() => { const n = parseInt(f.om, 10); return f.om.trim() !== '' && Number.isFinite(n) && n > 1 ? n : 1 })()
  const unitEq = price != null ? price / upl : null
  const net = netAt(unitEq, effCost, fee, delivery / upl)
  const save = async () => {
    const body: Record<string, unknown> = {}
    if (create) body.is_active = f.active
    else if (f.active !== ch.is_active) body.is_active = f.active
    if (price !== (ch.selling_price ?? null)) body.selling_price = price
    const oldUpl = ch.units_per_listing != null && ch.units_per_listing > 1 ? ch.units_per_listing : 1
    if (upl !== oldUpl) body.units_per_listing = upl
    const oldOm = ch.order_multiple != null && ch.order_multiple > 1 ? ch.order_multiple : 1
    if (om !== oldOm) body.order_multiple = om
    if (f.disp !== ch.has_dispensing_fee) body.has_dispensing_fee = f.disp
    if (create && price != null) body.selling_price = price
    if (Object.keys(body).length === 0) { onClose(); return }
    if (body.selling_price === null) { toast.error('Enter a selling price (or keep the current one)'); return }
    setBusy(true)
    const updated = await send('PATCH', `/products/${sku}/channels/${ch.channel}`, body)
    setBusy(false)
    if (updated) { onProduct(updated); toast.success(create ? `Listed on ${ch.channel}` : `${ch.channel} selling item updated`); onClose() }
  }
  return (
    <div className="ovl" onClick={onClose}>
      <div className="dlg skud" onClick={e => e.stopPropagation()} style={{ width: 470 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>{create ? 'Add selling item' : 'Selling item'} — {ch.channel}</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: 20, color: 'var(--faint)', cursor: 'pointer' }}>×</button>
        </div>
        <p style={{ fontSize: 11.5, color: 'var(--faint)', margin: '3px 0 14px' }}>
          This channel's own configuration — other channels are unaffected.
        </p>
        <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, cursor: 'pointer' }}>
          <input type="checkbox" checked={f.active} onChange={e => setF({ ...f, active: e.target.checked })} />
          <span style={{ fontSize: 13, fontWeight: 600 }}>Listed on {ch.channel}</span>
        </label>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 11 }}>
          <label><span className="flab">Selling price (HK$ {upl > 1 ? `per listing of ${upl}` : `per ${uom}`})</span>
            <input className="fin" type="number" value={f.price} onChange={e => setF({ ...f, price: e.target.value })} onKeyDown={e => e.key === 'Enter' && save()} />
          </label>
          <label><span className="flab">Units per listing</span>
            <input className="fin" type="number" placeholder="1" value={f.upl} onChange={e => setF({ ...f, upl: e.target.value })} />
            <span style={{ display: 'block', fontSize: 10.5, color: 'var(--faint)', marginTop: 3 }}>what one listing <b>contains</b> — e.g. a bundle of 6 {plu(uom)} sold as one item</span>
          </label>
          <label><span className="flab">Order multiple</span>
            <input className="fin" type="number" placeholder="1" value={f.om} onChange={e => setF({ ...f, om: e.target.value })} />
            <span style={{ display: 'block', fontSize: 10.5, color: 'var(--faint)', marginTop: 3 }}>how customers must <b>buy</b> — in multiples of N listings (e.g. 12)</span>
          </label>
        </div>
        {ch.channel === 'clinic' && (
          <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 11, cursor: 'pointer' }}>
            <input type="checkbox" checked={f.disp} onChange={e => setF({ ...f, disp: e.target.checked })} />
            <span style={{ fontSize: 12.5 }}>Dispensing fee applies</span>
          </label>
        )}
        <div className="preview" style={{ marginTop: 12 }}>
          {price == null ? 'Enter a price to preview the per-unit economics.' : (
            <>
              {upl > 1 ? <>One listing = {upl} {plu(uom)} · <b>{fm(unitEq)}/{uom}</b> equivalent<br /></> : null}
              {om > 1 ? <>Customers buy in {om}s → smallest order {om} listing{om === 1 ? '' : 's'} = {om * upl} {plu(uom)} · {price != null ? hk(price * om) : '—'}<br /></> : null}
              {net != null && effCost != null ? <>≈ net margin <b style={{ color: net >= floor ? 'var(--good)' : 'var(--amber)' }}>{(net * 100).toFixed(1)}%</b> at effective cost {fm(effCost)} {net >= floor ? '✓' : `(floor ${(floor * 100).toFixed(0)}%)`}{fee ? ` · after ${(fee * 100).toFixed(0)}% fee` : ''}{delivery ? ` · courier ${fm(delivery / upl)}/${uom}` : ''}</> : 'margin preview needs a cost on record'}
            </>
          )}
        </div>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9, marginTop: 15 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn pri" disabled={busy} onClick={save}>{busy ? 'Saving…' : create ? 'Create selling item' : 'Save selling item'}</button>
        </div>
      </div>
    </div>
  )
}

function StockAdjustDialog({ sku, uom, onProduct, onClose }: { sku: string; uom: string; onProduct: (p: Product) => void; onClose: () => void }) {
  const [location, setLocation] = useState<'warehouse' | 'clinic'>('warehouse')
  const [delta, setDelta] = useState('')
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState(false)
  const d = parseFloat(delta)
  const save = async () => {
    if (!Number.isFinite(d) || d === 0) { toast.error('Enter a non-zero adjustment') ; return }
    if (!reason.trim()) { toast.error('A reason is required — it lands in the audit trail'); return }
    setBusy(true)
    const updated = await send('PATCH', `/products/${sku}/stock/adjust`, { location, delta: d, reason: reason.trim() })
    setBusy(false)
    if (updated) { onProduct(updated); toast.success(`${location} stock ${d > 0 ? '+' : ''}${d} ${plu(uom)}`); onClose() }
  }
  return (
    <div className="ovl" onClick={onClose}>
      <div className="dlg skud" onClick={e => e.stopPropagation()} style={{ width: 440 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <h2 style={{ fontSize: 16, fontWeight: 700, margin: 0 }}>Adjust stock</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: 20, color: 'var(--faint)', cursor: 'pointer' }}>×</button>
        </div>
        <p style={{ fontSize: 11.5, color: 'var(--faint)', margin: '3px 0 14px' }}>Correction with a reason — recorded in the audit trail.</p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10 }}>
          <label><span className="flab">Location</span>
            <select className="fin" value={location} onChange={e => setLocation(e.target.value as 'warehouse' | 'clinic')}>
              <option value="warehouse">warehouse</option><option value="clinic">clinic</option>
            </select>
          </label>
          <label><span className="flab">Change ({plu(uom)}, ± allowed)</span><input className="fin" type="number" placeholder="e.g. -3" value={delta} onChange={e => setDelta(e.target.value)} /></label>
        </div>
        <label style={{ display: 'block', marginTop: 10 }}><span className="flab">Reason</span>
          <input className="fin" placeholder="e.g. damaged in storage / count correction" value={reason} onChange={e => setReason(e.target.value)} onKeyDown={e => e.key === 'Enter' && save()} />
        </label>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 9, marginTop: 16 }}>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn pri" disabled={busy} onClick={save}>{busy ? 'Adjusting…' : 'Adjust'}</button>
        </div>
      </div>
    </div>
  )
}

// Compact competitor expansion — list + margin-if-matched + manage tools.
function CompetitorPanel({ sku, rows, effCost, mbbCost, marginRows, floor, editable, refresh, productId }: {
  sku: string; rows: CompetitorPrice[]; effCost: number | null; mbbCost: number | null
  marginRows: { channel: string; channel_fee_pct: number | null; delivery_cost: number | null; selling_price: number | null }[]
  floor: number; editable: boolean; refresh: () => void; productId: number
}) {
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState<'' | 'add' | 'refresh'>('')
  const sorted = [...rows].sort((a, b) => (a.price == null ? 1 : 0) - (b.price == null ? 1 : 0) || (a.price ?? 0) - (b.price ?? 0))
  const add = async () => {
    if (!/^https?:\/\//i.test(url.trim())) { toast.error('Enter a full http(s) competitor URL'); return }
    setBusy('add')
    const r = await fetch(`${API}/competitors/by-sku/${sku}`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ url: url.trim(), competitor_name: name.trim() || null }) }).catch(() => null)
    setBusy('')
    if (r?.ok) { setUrl(''); setName(''); refresh() } else toast.error('Could not add competitor')
  }
  const doRefresh = async () => {
    setBusy('refresh')
    await fetch(`${API}/competitors/refresh`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ product_id: productId }) }).catch(() => {})
    setBusy(''); refresh()
  }
  const remove = async (id: number) => {
    if (!(await confirmDialog({ message: 'Remove this competitor link?', confirmLabel: 'Remove', danger: true }))) return
    await fetch(`${API}/competitors/${id}`, { method: 'DELETE', headers: authHeaders() }).catch(() => {})
    refresh()
  }
  return (
    <div style={{ borderTop: '1px solid var(--line2)', padding: '10px 15px', background: 'var(--panel)' }}>
      {sorted.map(c => {
        const margins = marginRows.filter(m => m.selling_price != null).map(m => {
          const n = netAt(c.price, effCost, m.channel_fee_pct ?? 0, m.delivery_cost ?? 0)
          const nb = netAt(c.price, mbbCost, m.channel_fee_pct ?? 0, m.delivery_cost ?? 0)
          return `${m.channel} ${n != null ? `${(n * 100).toFixed(0)}%` : '—'}${nb != null ? ` (bulk ${(nb * 100).toFixed(0)}%)` : ''}`
        }).join(' · ')
        return (
          <div key={c.id} style={{ display: 'flex', gap: 10, alignItems: 'baseline', padding: '5px 0', borderBottom: '1px solid var(--line2)', fontSize: 12, flexWrap: 'wrap' }}>
            {c.url ? <a href={c.url} target="_blank" rel="noreferrer" className="lnk" style={{ textDecoration: 'none' }}>{c.competitor_name}</a> : <b>{c.competitor_name}</b>}
            <b style={{ fontVariantNumeric: 'tabular-nums' }}>{fm(c.price)}</b>
            <span style={{ color: 'var(--faint)', fontSize: 11 }}>{c.last_checked ? `checked ${c.last_checked}` : 'not scraped yet'}{c.last_status && c.last_status !== 'ok' ? ` · ${c.last_status}` : ''}</span>
            {c.price != null && <span style={{ color: 'var(--muted)', fontSize: 11 }}>if matched: {margins}</span>}
            {editable && <span className="lnk" style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--red)' }} onClick={() => remove(c.id)}>remove</span>}
          </div>
        )
      })}
      {sorted.length === 0 && <div style={{ fontSize: 12, color: 'var(--faint)', padding: '4px 0' }}>No competitors linked yet — paste a product URL below.</div>}
      {editable && (
        <div style={{ display: 'flex', gap: 8, marginTop: 9, flexWrap: 'wrap' }}>
          <input className="fin" style={{ flex: 2, minWidth: 180 }} placeholder="Competitor product URL" value={url} onChange={e => setUrl(e.target.value)} onKeyDown={e => e.key === 'Enter' && add()} />
          <input className="fin" style={{ flex: 1, minWidth: 110 }} placeholder="Name (optional)" value={name} onChange={e => setName(e.target.value)} />
          <button className="btn pri" style={{ padding: '6px 12px', fontSize: 11.5 }} disabled={busy === 'add' || !url.trim()} onClick={add}>{busy === 'add' ? 'Adding…' : 'Add + fetch'}</button>
          {sorted.length > 0 && <button className="btn" style={{ padding: '6px 12px', fontSize: 11.5 }} disabled={busy === 'refresh'} onClick={doRefresh}>{busy === 'refresh' ? 'Refreshing…' : 'Refresh prices'}</button>}
        </div>
      )}
    </div>
  )
}

// ── small header controls ────────────────────────────────────────────────
function StatusMenu({ p, sku, onProduct }: { p: Product; sku: string; onProduct: (pr: Product) => void }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  const lbl = (s: string) => (s === 'ACTIVE' ? 'Active' : s === 'INACTIVE' ? 'Inactive' : 'Discontinued')
  const pick = async (s: string) => {
    setOpen(false)
    if (s === p.status) return
    setBusy(true)
    const updated = await send('PATCH', `/products/${sku}`, { status: s })
    setBusy(false)
    if (updated) { onProduct(updated); toast.success(`Status set to ${lbl(s)}`) }
  }
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button className="btn" onClick={() => setOpen(o => !o)} disabled={busy}>{busy ? 'Saving…' : `Status: ${lbl(p.status)}`} ▾</button>
      {open && (
        <div style={{ position: 'absolute', top: 'calc(100% + 4px)', right: 0, background: '#fff', border: '1px solid var(--line)', borderRadius: 8, boxShadow: '0 10px 30px rgba(15,23,42,.14)', zIndex: 40, minWidth: 160, padding: 4 }}>
          {['ACTIVE', 'INACTIVE', 'DISCONTINUED'].map(o => (
            <button key={o} onClick={() => pick(o)} style={{ display: 'block', width: '100%', textAlign: 'left', padding: '8px 10px', fontSize: 12.5, border: 'none', borderRadius: 6, background: o === p.status ? 'var(--accent-soft)' : 'transparent', color: o === p.status ? 'var(--accent-ink)' : 'var(--ink2)', fontWeight: o === p.status ? 600 : 400, cursor: 'pointer' }}>
              {lbl(o)}{o === p.status ? ' ✓' : ''}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function MoreMenu({ p, onChangeSku, sku, onProduct }: {
  p: Product; onChangeSku: () => void; sku: string; onProduct: (pr: Product) => void
}) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  const item: React.CSSProperties = { display: 'block', width: '100%', textAlign: 'left', padding: '8px 11px', fontSize: 12.5, border: 'none', borderRadius: 6, background: 'transparent', color: 'var(--ink2)', cursor: 'pointer', textDecoration: 'none' }
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button className="btn" onClick={() => setOpen(o => !o)} title="More actions">⋯</button>
      {open && (
        <div style={{ position: 'absolute', top: 'calc(100% + 4px)', right: 0, background: '#fff', border: '1px solid var(--line)', borderRadius: 8, boxShadow: '0 10px 30px rgba(15,23,42,.14)', zIndex: 40, minWidth: 200, padding: 4 }}>
          {can('product_edit') && <button style={item} onClick={() => { setOpen(false); onChangeSku() }}>Change SKU code…</button>}
          {p.shopify_status && p.shopify_status !== 'archived' && (
            <a style={item} href={`https://petproject.hk/search?q=${encodeURIComponent(p.name)}`} target="_blank" rel="noreferrer" onClick={() => setOpen(false)}>Open in Shopify ↗</a>
          )}
          <Link style={item} to={'/admin/audit' as never} onClick={() => setOpen(false)}>Full audit trail →</Link>
          {!p.uom_verified_at && p.units_per_pack != null && can('product_edit') && (
            <button style={item} onClick={async () => {
              setOpen(false)
              const updated = await send('PATCH', `/products/${sku}/uom`, { verified_by: null })
              if (updated) { onProduct(updated); toast.success('Pack size confirmed') }
            }}>Confirm pack size ✓</button>
          )}
        </div>
      )}
    </div>
  )
}

function UnverifyButton({ p, sku, onProduct }: { p: Product; sku: string; onProduct: (pr: Product) => void }) {
  const [busy, setBusy] = useState(false)
  if (!p.hitl_verified) return null
  const run = async () => {
    const ok = await confirmDialog({
      title: 'Unverify this SKU?',
      message: `${p.sku_code} will lose its verified status and stop being pushed to the sheet until it is re-verified through catalogue onboarding.`,
      confirmLabel: 'Unverify', danger: true,
    })
    if (!ok) return
    setBusy(true)
    const r = await fetch(`${API}/products/${sku}/hitl-unverify`, { method: 'POST', headers: authHeaders() })
    setBusy(false)
    if (r.ok) { onProduct({ ...p, hitl_verified: false }); toast.success(`${p.sku_code} unverified — ready to re-scan`) }
    else toast.error((await r.json().catch(() => ({}))).detail ?? 'Failed to unverify')
  }
  return <button className="btn danger" onClick={run} disabled={busy}>{busy ? '…' : 'Unverify (allow re-scan)'}</button>
}

// ── activity wording (shared shape with the classic page) ────────────────
function auditLabel(action: string): string {
  switch (action) {
    case 'assign_new': return 'New SKU assigned'
    case 'confirm_match': return 'Matched & verified'
    case 'edit': return 'Edited'
    case 'reject': return 'Rejected'
    case 'supplier_confirm': return 'Supplier confirmed'
    case 'product.supplier_stock': return 'Supplier stock updated'
    case 'product.supplier_add': return 'Supplier added'
    case 'product.supplier_update': return 'Supplier updated'
    case 'product.price_update': return 'Price changed'
    case 'product.channel_update': return 'Selling item updated'
    case 'product.stock_adjust': return 'Stock adjusted'
    case 'product.mbb_term_add': return 'Bulk term added'
    case 'product.mbb_term_update': return 'Bulk term updated'
    case 'product.mbb_term_delete': return 'Bulk term removed'
    default: return action.replace(/[._]/g, ' ')
  }
}
function auditSummary(e: AuditEvent): string {
  const d = e.details ?? {}
  const s = (k: string) => (d[k] == null ? '' : String(d[k]))
  switch (e.action) {
    case 'assign_new': return [s('product_name'), s('category')].filter(Boolean).join(' · ')
    case 'confirm_match': return s('product_name') || ''
    case 'reject': return s('reason') || s('description') || '—'
    case 'supplier_confirm': return [s('supplier_name'), s('filename')].filter(Boolean).join(' · ')
    case 'product.supplier_stock': return s('status') === 'out_of_stock' ? 'marked out of stock' : 'back in stock'
    case 'product.price_update': return [s('channel'), s('from') && s('to') ? `${s('from')} → ${s('to')}` : ''].filter(Boolean).join(' · ')
    case 'product.stock_adjust': return [s('location'), s('delta'), s('reason')].filter(Boolean).join(' · ')
    case 'edit': {
      const ch = (d.changes ?? {}) as Record<string, { from: unknown; to: unknown }>
      const keys = Object.keys(ch)
      return keys.length ? keys.slice(0, 3).map(k => `${k}: ${ch[k].from ?? '∅'}→${ch[k].to ?? '∅'}`).join(', ') : 'edited'
    }
    default: return ''
  }
}

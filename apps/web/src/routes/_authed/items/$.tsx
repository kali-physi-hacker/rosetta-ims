import { useState, useEffect, useRef } from 'react'
import { createFileRoute, Link, useNavigate, useRouter } from '@tanstack/react-router'
import type { Product, SyncStatus, MbbTermMargin, MbbTerm, CompetitorPrice } from '@/lib/types'
import { authHeaders, can } from '@/lib/auth'
import { API_BASE } from '@/lib/config'
import { skuToPath } from '@/lib/sku'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'
import { Spinner } from '@/components/Spinner'
import { ReparseButton } from '@/components/ReparseButton'

const API = API_BASE

export const Route = createFileRoute('/_authed/items/$')({ component: ItemDetailPage })

// Compact status picker (Active / Inactive / Discontinued) — sensitive-field gated.
function StatusMenu({ current, saving, onPick }: { current: string; saving: boolean; onPick: (s: string) => void }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener('mousedown', h)
    return () => document.removeEventListener('mousedown', h)
  }, [open])
  const opts = ['ACTIVE', 'INACTIVE', 'DISCONTINUED']
  const lbl = (s: string) => s === 'ACTIVE' ? 'Active' : s === 'INACTIVE' ? 'Inactive' : 'Discontinued'
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button className="btn" onClick={() => setOpen(o => !o)} disabled={saving}>
        {saving ? 'Saving…' : `Status: ${lbl(current)}`} <span style={{ color: '#94A3B8' }}>▾</span>
      </button>
      {open && (
        <div style={{ position: 'absolute', top: 'calc(100% + 4px)', right: 0, background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', boxShadow: '0 10px 30px rgba(15,23,42,0.14)', zIndex: 40, minWidth: '162px', overflow: 'hidden', padding: '4px' }}>
          {opts.map(o => (
            <button key={o} onClick={() => { setOpen(false); if (o !== current) onPick(o) }}
              style={{ display: 'block', width: '100%', textAlign: 'left', padding: '8px 10px', fontSize: '12.5px', border: 'none', borderRadius: '6px', background: o === current ? '#EEF2FF' : 'transparent', color: o === current ? '#4338CA' : '#334155', fontWeight: o === current ? 600 : 400, cursor: 'pointer' }}>
              {lbl(o)}{o === current ? ' ✓' : ''}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

// Weight is canonical in grams; kg/lb is just the display/source unit.
const LB_G = 453.592
const gToUnit = (g: number, u: string | null) => +(g / (u === 'lb' ? LB_G : 1000)).toFixed(3)
const unitToG = (v: number, u: string | null) => Math.round(v * (u === 'lb' ? LB_G : 1000))

const CAT_STYLE: Record<string, { bg: string; color: string; dot: string }> = {
  'Medicine':     { bg: '#FEE2E2', color: '#991B1B', dot: '#DC2626' },
  'Preventative': { bg: '#FEF3C7', color: '#92400E', dot: '#D97706' },
  'Supplement':   { bg: '#DBEAFE', color: '#1E40AF', dot: '#2563EB' },
  'Food':         { bg: '#DCFCE7', color: '#166534', dot: '#15803D' },
  'Pet Hygiene':  { bg: '#F1F5F9', color: '#475569', dot: '#64748B' },
  'Not-For-Sale': { bg: '#F1F5F9', color: '#94A3B8', dot: '#94A3B8' },
}

const CHANNEL_LABEL: Record<string, string> = {
  clinic: 'Clinic',
  shopify: 'Shopify',
  hktv: 'HKTV',
}
const CHANNEL_SUB: Record<string, string> = {
  clinic: 'DaySmart POS',
  shopify: 'Online store',
  hktv: 'HKTV Mall',
}

// Onboarding audit trail (shared shape with the catalogues page).
interface AuditEvent {
  id: number; action: string; sku_code: string | null
  display_name: string | null; details: Record<string, unknown>; created_at: string
}
function fmtWhen(iso: string): string {
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return isNaN(d.getTime()) ? iso : d.toLocaleString()
}
function auditLabel(action: string): string {
  switch (action) {
    case 'assign_new':          return 'New SKU assigned'
    case 'confirm_match':        return 'Matched & verified'
    case 'edit':                 return 'Edited'
    case 'reject':               return 'Rejected'
    case 'supplier_confirm':     return 'Supplier confirmed'
    case 'product.supplier_stock': return 'Supplier stock updated'
    default:                     return action.replace(/[._]/g, ' ')
  }
}
function auditSummary(e: AuditEvent): string {
  const d = e.details ?? {}
  const s = (k: string) => (d[k] == null ? '' : String(d[k]))
  switch (e.action) {
    case 'assign_new':    return [s('product_name'), s('category')].filter(Boolean).join(' · ')
    case 'confirm_match': return s('product_name') || ''
    case 'reject':        return s('reason') || s('description') || '—'
    case 'supplier_confirm': return [s('supplier_name'), s('filename')].filter(Boolean).join(' · ')
    case 'product.supplier_stock': return [s('status') === 'out_of_stock' ? 'marked out of stock' : 'back in stock'].filter(Boolean).join(' · ')
    case 'edit': {
      const ch = (d.changes ?? {}) as Record<string, { from: unknown; to: unknown }>
      const keys = Object.keys(ch)
      return keys.length ? keys.map(k => `${k}: ${ch[k].from ?? '∅'}→${ch[k].to ?? '∅'}`).join(', ') : 'edited'
    }
    default: return ''
  }
}

// Shows a value or a clearly visible missing-data flag — never silently blank.
function Val({ v, fmt }: { v: string | number | null | undefined; fmt?: (x: string | number) => string }) {
  if (v === null || v === undefined || v === '') {
    return <span style={{ fontSize: '11px', fontWeight: 600, color: '#B45309', background: '#FCF3E6', padding: '1px 6px', borderRadius: '4px' }}>Missing</span>
  }
  return <>{fmt ? fmt(v) : String(v)}</>
}

const money = (n: number | null | undefined) => n != null ? `HK$${n < 100 ? n.toFixed(2) : Math.round(n).toLocaleString()}` : '—'
const moneyU = (n: number | null | undefined) => n != null ? `HK$${n >= 1 ? n.toFixed(2) : n.toFixed(3)}` : '—'
const fmtDate = (iso: string | null | undefined) => {
  if (!iso) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + (iso.length <= 10 ? 'T00:00:00Z' : 'Z'))
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' })
}

// Competitor prices — link competitor product URLs to this SKU; a scrape fetches each one's
// current selling price (Shopify JSON where possible, HTML otherwise).
function CompetitorPrices({ product }: { product: Product }) {
  const [rows, setRows] = useState<CompetitorPrice[] | null>(null)
  const [cheapest, setCheapest] = useState<number | null>(null)
  const [selId, setSelId] = useState<number | null>(null)
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [busy, setBusy] = useState<'' | 'add' | 'refresh'>('')
  const editable = can('product_edit')
  const path = skuToPath(product.sku_code)

  const load = () => fetch(`${API}/competitors/by-sku/${path}`, { headers: authHeaders() })
    .then(r => (r.ok ? r.json() : null))
    .then(d => { if (d) { setRows(d.competitors); setCheapest(d.cheapest) } })
    .catch(() => {})
  useEffect(() => { load() }, [product.id])   // eslint-disable-line react-hooks/exhaustive-deps

  const add = async () => {
    const u = url.trim()
    if (!/^https?:\/\//i.test(u)) { toast.error('Enter a full http(s) competitor URL'); return }
    setBusy('add')
    const r = await fetch(`${API}/competitors/by-sku/${path}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ url: u, competitor_name: name.trim() || null }) }).catch(() => null)
    setBusy('')
    if (r && r.ok) { setUrl(''); setName(''); load() } else { toast.error('Could not add competitor') }
  }
  const refresh = async () => {
    setBusy('refresh')
    await fetch(`${API}/competitors/refresh`, { method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ product_id: product.id }) }).catch(() => {})
    setBusy(''); load()
  }
  const remove = async (id: number) => {
    if (!(await confirmDialog({ message: 'Remove this competitor link?', confirmLabel: 'Remove', danger: true }))) return
    await fetch(`${API}/competitors/${id}`, { method: 'DELETE', headers: authHeaders() }).catch(() => {})
    setSelId(null); load()
  }

  // Cheapest first (priced before unpriced); default-select the cheapest.
  const sorted = [...(rows ?? [])].sort((a, b) =>
    (a.price == null ? 1 : 0) - (b.price == null ? 1 : 0) || (a.price ?? 0) - (b.price ?? 0))
  useEffect(() => {
    if (sorted.length && (selId == null || !sorted.some(c => c.id === selId))) setSelId(sorted[0].id)
  }, [rows])   // eslint-disable-line react-hooks/exhaustive-deps
  const sel = sorted.find(c => c.id === selId) ?? sorted[0] ?? null

  const mr = product.margin_range
  const pct = (m: number | null) => m == null ? '—' : `${(m * 100).toFixed(0)}%`
  const mCls = (m: number | null) => m == null ? '' : m >= product.gp_floor ? 'good' : m > 0 ? 'warn' : 'bad'
  // Margin selling one unit at `price`, net of a channel's fee% + delivery.
  const margAt = (price: number | null | undefined, cost: number | null | undefined, fee = 0, delivery = 0) =>
    (price != null && price > 0 && cost != null) ? (price - cost - fee * price - delivery) / price : null

  return (
    <div className="card">
      <div className="ch">
        <div className="ct">Competitor prices</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {cheapest != null && <span className="hint">cheapest {money(cheapest)}</span>}
          {editable && rows && rows.length > 0 && (
            <button className="linkbtn" disabled={busy === 'refresh'} onClick={refresh}>{busy === 'refresh' ? 'Refreshing…' : 'Refresh prices'}</button>
          )}
        </div>
      </div>
      <div className="cb flush">
        {rows === null && <div className="cmpe">Loading…</div>}
        {rows && rows.length === 0 && <div className="cmpe">No competitors linked yet. Paste a competitor product URL below to track its price.</div>}
        {sel && (
          <div className="cmpwrap">
            {/* left: one vertical tab per competitor, cheapest first (default selected) */}
            <div className="cmptabs">
              {sorted.map(c => (
                <button key={c.id} type="button" className={`cmptab${c.id === sel.id ? ' on' : ''}`} onClick={() => setSelId(c.id)}>
                  <span className="tnm">{c.competitor_name}</span>
                  <span className={`tpr${cheapest != null && c.price === cheapest ? ' cheap' : ''}`}>{money(c.price)}</span>
                </button>
              ))}
            </div>
            {/* right: margin if we matched the selected competitor's price */}
            <div className="cmppanel">
              <div className="cpp-h">
                {sel.url ? <a className="cmp-nm" href={sel.url} target="_blank" rel="noreferrer">{sel.competitor_name}</a> : <span className="cmp-nm">{sel.competitor_name}</span>}
                {editable && <button className="cmp-x" title="Remove" onClick={() => remove(sel.id)}>×</button>}
              </div>
              {(() => { const m = [sel.platform, sel.in_stock === 0 ? 'out of stock' : sel.in_stock === 1 ? 'in stock' : null, sel.last_checked ? `checked ${sel.last_checked}` : null, (sel.last_status && sel.last_status !== 'ok') ? sel.last_status : null].filter(Boolean).join(' · '); return m ? <div className="cmp-meta">{m}</div> : null })()}
              {sel.price == null ? (
                <div className="cmpe" style={{ padding: '10px 0' }}>No price scraped yet — hit “Refresh prices”.</div>
              ) : (
                <>
                  <div className="cpp-lead">If you match <b>{money(sel.price)}</b>, your margin:</div>
                  <table className="cpptab"><thead><tr><th></th><th>Basic</th><th>MBB</th></tr></thead><tbody>
                    <tr><td className="cpp-row">Gross <span className="cpp-sub">before fees</span></td>
                      <td><span className={`gpv ${mCls(margAt(sel.price, mr?.basic_cost))}`}>{pct(margAt(sel.price, mr?.basic_cost))}</span></td>
                      <td><span className={`gpv ${mCls(margAt(sel.price, mr?.mbb_cost))}`}>{pct(margAt(sel.price, mr?.mbb_cost))}</span></td>
                    </tr>
                    {(mr?.channels ?? []).map(ch => {
                      const fee = ch.channel_fee_pct ?? 0, del = ch.delivery_cost ?? 0
                      const chg = fee ? `${(fee * 100).toFixed(0)}% fee` : del ? `${money(del)} SF` : 'no fee'
                      return (
                        <tr key={ch.channel}><td className="cpp-row">{CHANNEL_LABEL[ch.channel] ?? ch.channel} <span className="cpp-sub">{chg}</span></td>
                          <td><span className={`gpv ${mCls(margAt(sel.price, mr?.basic_cost, fee, del))}`}>{pct(margAt(sel.price, mr?.basic_cost, fee, del))}</span></td>
                          <td><span className={`gpv ${mCls(margAt(sel.price, mr?.mbb_cost, fee, del))}`}>{pct(margAt(sel.price, mr?.mbb_cost, fee, del))}</span></td>
                        </tr>
                      )
                    })}
                  </tbody></table>
                </>
              )}
            </div>
          </div>
        )}
      </div>
      {editable && (
        <div className="cmp-add">
          <input className="cmp-in url" placeholder="Paste competitor product URL" value={url}
                 onChange={e => setUrl(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') add() }} />
          <input className="cmp-in nm" placeholder="Name (optional)" value={name}
                 onChange={e => setName(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') add() }} />
          <button className="btn pri" disabled={busy === 'add' || !url.trim()} onClick={add}>{busy === 'add' ? 'Adding…' : 'Add + fetch'}</button>
        </div>
      )}
    </div>
  )
}

function ItemDetailPage() {
  // Catch-all route: a sku_code can contain '/' (e.g. "…7mg/ml"), so it arrives as path
  // segments. Next does NOT URL-decode catch-all segments, so "%20"/"%2F" arrive literally —
  // decode each segment, then rejoin on the real '/' separators that skuToPath preserved.
  const rawSplat = Route.useParams({ select: (p) => p._splat })
  const sku = (rawSplat ?? '').split('/').map(decodeURIComponent).join('/')
  // Return to wherever the user came from — the filtered/searched inventory list is
  // restored from the URL + the session cache with no refetch. Falls back to a fresh list.
  const router = useRouter()
  const navigate = useNavigate()
  const goBack = () => { if (typeof window !== 'undefined' && window.history.length > 1) router.history.back(); else navigate({ to: '/' as never }) }
  const [item, setItem]       = useState<Product | null>(null)
  const [, setSyncStatus]     = useState<SyncStatus | null>(null)
  const [loading, setLoading]       = useState(true)
  const [error, setError]           = useState<string | null>(null)
  const [verifiedBy, setVerifiedBy] = useState(() =>
    typeof window !== 'undefined' ? (localStorage.getItem('ims_verified_by') ?? '') : ''
  )
  const [uomSaving, setUomSaving]   = useState(false)
  const [uomError, setUomError]     = useState<string | null>(null)
  const [editing, setEditing] = useState(false)   // edit-details modal
  const [savingStatus, setSavingStatus] = useState(false)
  const [changingSku, setChangingSku] = useState(false)   // change-SKU modal
  const [copiedSku, setCopiedSku] = useState(false)   // supplier-SKU copy feedback
  const [manageSuppliers, setManageSuppliers] = useState(false)   // supplier-manager modal
  const [skuHistory, setSkuHistory] = useState<{ from: string; to: string; at: string; by: string | null }[]>([])
  const [simBuy, setSimBuy] = useState('')   // coverage simulator: packs to buy

  async function confirmUom(sku: string) {
    setUomSaving(true); setUomError(null)
    if (verifiedBy) localStorage.setItem('ims_verified_by', verifiedBy)
    try {
      const res = await fetch(`${API}/products/${skuToPath(sku)}/uom`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ verified_by: verifiedBy || null }),
      })
      if (!res.ok) throw new Error(await res.text())
      setItem(await res.json())
    } catch (e: unknown) {
      setUomError(e instanceof Error ? e.message : 'Error saving')
    } finally {
      setUomSaving(false)
    }
  }

  useEffect(() => {
    fetch(`${API}/sync/status`, { headers: authHeaders() }).then(r => r.ok ? r.json() : null).then(setSyncStatus).catch(() => {})
  }, [])

  useEffect(() => {
    fetch(`${API}/products/${skuToPath(sku)}`, { cache: 'no-store', headers: authHeaders() })
      .then(r => {
        if (r.status === 404) { setError('404'); return null }
        if (!r.ok) throw new Error(`API ${r.status}`)
        return r.json()
      })
      .then(data => { if (data) setItem(data) })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, [sku])

  // Onboarding audit trail for this SKU (who created/matched/edited it, and when)
  const [history, setHistory] = useState<AuditEvent[]>([])
  useEffect(() => {
    fetch(`${API}/catalogues/audit?sku=${encodeURIComponent(sku)}&limit=100`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setHistory(d.events ?? []) })
      .catch(() => {})
  }, [sku])

  // SKU-rename history — what this product's code was changed from.
  useEffect(() => {
    fetch(`${API}/products/${skuToPath(sku)}/sku-history`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setSkuHistory(d.history ?? []) })
      .catch(() => {})
  }, [sku])

  // Tags — view + manual edit
  const [tagDraft, setTagDraft] = useState<string[] | null>(null)   // non-null = editing
  const [tagInput, setTagInput] = useState('')
  const [savingTags, setSavingTags] = useState(false)
  async function setStatus(next: string) {
    if (!item || next === item.status || savingStatus) return
    setSavingStatus(true)
    try {
      const r = await fetch(`${API}/products/${skuToPath(sku)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ status: next }),
      })
      if (r.ok) {
        setItem(prev => prev ? { ...prev, status: next as Product['status'] } : prev)
        toast.success(`Status set to ${next === 'ACTIVE' ? 'Active' : next === 'INACTIVE' ? 'Inactive' : 'Discontinued'}`)
      } else {
        const e = await r.json().catch(() => ({}))
        toast.error(e.detail || 'Could not update status')
      }
    } catch { toast.error('Could not update status') }
    finally { setSavingStatus(false) }
  }

  async function saveTags(draft: string[]) {
    setSavingTags(true)
    try {
      const r = await fetch(`${API}/products/${skuToPath(sku)}/tags`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ tags: draft }),
      })
      if (r.ok) { const d = await r.json(); setItem(prev => prev ? { ...prev, tags: d.tags } : prev); setTagDraft(null); setTagInput('') }
      else { toast.error((await r.json().catch(() => ({}))).detail ?? 'Failed to save tags') }
    } finally { setSavingTags(false) }
  }

  // HITL unverify — drop the SKU's verified status so it can be re-scanned / re-onboarded
  const [unverifying, setUnverifying] = useState(false)
  async function hitlUnverify() {
    const ok = await confirmDialog({
      title: 'Unverify this SKU?',
      message: `${sku} will lose its HITL-verified status and stop being pushed to the sheet until it is re-verified through catalogue onboarding. Use this to re-scan / correct the item.`,
      confirmLabel: 'Unverify',
      danger: true,
    })
    if (!ok) return
    setUnverifying(true)
    try {
      const r = await fetch(`${API}/products/${skuToPath(sku)}/hitl-unverify`, { method: 'POST', headers: authHeaders() })
      if (r.ok) {
        setItem(prev => prev ? { ...prev, hitl_verified: false } : prev)
        toast.success(`${sku} unverified — ready to re-scan`)
      } else {
        toast.error((await r.json().catch(() => ({}))).detail ?? 'Failed to unverify')
      }
    } finally { setUnverifying(false) }
  }

  if (loading) {
    return (
      <div style={{ padding: '60px', textAlign: 'center', color: '#94A3B8', fontSize: '13px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '9px' }}><Spinner size={15} color="#94A3B8" /> Loading…</div>
    )
  }

  if (error === '404' || (!loading && !item)) {
    return (
      <div style={{ padding: '40px', textAlign: 'center' }}>
        <p style={{ fontSize: '16px', fontWeight: 600, color: '#0F172A' }}>Product not found</p>
        <p style={{ fontSize: '13px', color: '#94A3B8', marginTop: '8px' }}>SKU {sku} does not exist in IMS.</p>
        <Link to={'/' as never} style={{ display: 'inline-block', marginTop: '16px', fontSize: '13px', color: '#6366F1' }}>← Back to Inventory</Link>
      </div>
    )
  }

  if (error) {
    return (
      <div style={{ padding: '40px', textAlign: 'center', color: '#991B1B', fontSize: '13px' }}>
        Error loading product: {error}
        <br /><Link to={'/' as never} style={{ color: '#6366F1', fontSize: '12px', marginTop: '8px', display: 'inline-block' }}>← Back to Inventory</Link>
      </div>
    )
  }

  const p = item!
  const copySupplierSku = () => {
    if (!p.supplier_sku) return
    navigator.clipboard?.writeText(p.supplier_sku)?.catch(() => { /* clipboard blocked */ })
    setCopiedSku(true)
    window.setTimeout(() => setCopiedSku(false), 1500)
  }
  const cat = CAT_STYLE[p.category] ?? { bg: '#F1F5F9', color: '#64748B', dot: '#64748B' }
  const primaryCh = p.channels.find(c => c.channel === 'clinic') ?? p.channels[0]
  const gpFailing = !p.channels.every(c => c.recommendation !== 'Raise price ⚠')

  // Suppliers / OOS
  const suppliers = p.all_suppliers ?? []
  const preferred = suppliers.find(s => s.is_preferred) ?? suppliers.find(s => s.is_primary) ?? suppliers[0] ?? null
  const prefOut = preferred?.stock_status === 'out_of_stock'
  const backup = prefOut
    ? suppliers.filter(s => s.id !== preferred?.id && s.stock_status !== 'out_of_stock' && s.basic_cost != null)
        .sort((a, b) => (a.basic_cost as number) - (b.basic_cost as number))[0] ?? null
    : null
  const costDiff = (backup?.basic_cost != null && preferred?.basic_cost != null) ? backup.basic_cost - preferred.basic_cost : null

  // Coverage / demand
  const uomLabel = p.uom ?? 'unit'
  const packLabel = p.pack_unit ?? 'pack'
  const upp = p.units_per_pack ?? 1
  const wocColor = p.woc == null ? '#8A93A2' : p.woc < 2 ? '#C0362C' : p.woc < 4 ? '#B45309' : '#15803D'
  const wocPct = p.woc == null ? 0 : Math.max(6, Math.min(100, (p.woc / 8) * 100))
  const landed = p.landed_unit_cost ?? p.unit_cost
  const wdbc = p.weekly_demand_by_channel
  const trend = p.sales_trend ?? []
  const trendMax = Math.max(...trend.map(t => t.units), 1)
  const monthLabel = (ym: string) => ['', 'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][parseInt(ym.slice(5, 7), 10)] ?? ym

  // Margin worksheet by channel
  const mr = p.margin_range
  const chMap = new Map(p.channels.map(c => [c.channel as string, c]))
  const gpCls = (v: number | null | undefined) => v == null ? '' : v >= p.gp_floor ? 'good' : v > 0 ? 'warn' : 'bad'
  const supBlocks = mr?.suppliers ?? []
  const marginChans = mr?.channels ?? []
  // A margin table cell: net margin (after fees, coloured) with the gross margin as a sub-line.
  const marginCell = (key: string, o?: { gp_pct: number | null; margin: number | null }) => (
    <td key={key} style={{ textAlign: 'right' }}>
      <span className={`gpv ${gpCls(o?.margin)}`}>{o?.margin != null ? `${(o.margin * 100).toFixed(1)}%` : '—'}</span>
      <div className="soldas" style={{ textAlign: 'right' }}>gross {o?.gp_pct != null ? `${(o.gp_pct * 100).toFixed(1)}%` : '—'}</div>
    </td>
  )
  // Full raw term (min_spend / free_qty / discount_pct / unit_cost) keyed by term id, so each MBB
  // row labels itself with real amounts instead of a bare kind or a stray "N/A" free-text note.
  const termById = new Map<number, MbbTerm>()
  for (const s of suppliers) for (const t of (s.mbb_term_list ?? [])) termById.set(t.id, t)
  const termLabel = (tm: MbbTermMargin, t?: MbbTerm) => {
    const q = t?.min_qty ?? tm.min_qty
    const unit = t?.unit_cost ?? tm.unit_cost
    switch (tm.kind) {
      case 'buy_x_get_y':    return `Buy ${q ?? '?'} get ${t?.free_qty ?? '?'} free`
      case 'spend_discount': return `Spend ${t?.min_spend != null ? money(t.min_spend) : '—'} → ${t?.discount_pct != null ? `${(t.discount_pct * 100).toFixed(0)}% off` : '—'}`
      case 'tier':           return `${q ?? '?'}+ ${uomLabel} → ${moneyU(unit)}/${uomLabel}`
      case 'flat_unit_cost': return `Flat ${moneyU(unit)}/${uomLabel}${q && q > 1 ? ` · ${q}+` : ''}`
      default:               return tm.kind
    }
  }

  // Coverage simulator
  const simPacks = Math.max(0, parseInt(simBuy || '0', 10) || 0)
  const mbbTiers = (preferred?.mbb_term_list ?? [])
    .filter(t => (t.kind === 'tier' || t.kind === 'flat_unit_cost') && t.min_qty != null && t.effective_unit_cost != null)
    .sort((a, b) => (a.min_qty as number) - (b.min_qty as number))
  const simTier = mbbTiers.filter(t => simPacks >= (t.min_qty as number)).sort((a, b) => (a.effective_unit_cost as number) - (b.effective_unit_cost as number))[0] ?? null
  const simUnitCost = simTier?.effective_unit_cost ?? p.unit_cost ?? landed ?? 0
  const simUnits = simPacks * upp
  const simAddW = p.weekly_demand > 0 ? simUnits / p.weekly_demand : null
  const simTotalW = (p.woc ?? 0) + (simAddW ?? 0)
  const chipQtys = Array.from(new Set([p.min_purchase_qty && p.min_purchase_qty > 0 ? p.min_purchase_qty : 4, ...(mbbTiers[0]?.min_qty != null ? [mbbTiers[0].min_qty as number] : [])])).slice(0, 2)

  const listedOn: [string, boolean][] = [
    ['SP', !!p.shopify_status && p.shopify_status !== 'archived'],
    ['DS', !!p.daysmart_status],
    ['HK', !!p.hktv_status && p.hktv_status !== 'offline'],
  ]

  return (
    <>
      <style>{SKUD_CSS}</style>
      <div className="skud" style={{ maxWidth: '1240px', margin: '0 auto', padding: '4px 2px 20px' }}>

        {/* Breadcrumb — back to the (filtered) inventory list */}
        <div style={{ marginBottom: '14px', fontSize: '12.5px', color: '#5B6472', display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }}>
          <span onClick={goBack} style={{ color: '#4F46E5', fontWeight: 600, cursor: 'pointer' }}>← All Inventory</span>
          <span style={{ color: '#C2C8D2' }}>/</span>
          <span style={{ color: '#334155' }}>{p.name}</span>
        </div>

        {/* ── Header ─────────────────────────────────────────── */}
        <div className="hdr">
          <div>
            <div className="eyebrow"><span className="cd" style={{ background: cat.dot }} />{p.category}{p.subcategory ? ` · ${p.subcategory}` : ''}</div>
            <h1>{p.name}</h1>
            <div className="idgrid">
              <div className="idcol">
                <div className="idlabel">IMS SKU</div>
                <div className="idrow">
                  <span className="skucode">{p.sku_code}</span>
                  <span className="lnk" onClick={() => setChangingSku(true)}>change</span>
                </div>
              </div>
              <div className="idcol">
                <div className="idlabel">Supplier SKU</div>
                <div className="idrow">
                  {p.supplier_sku ? (
                    <button type="button" className={`supsku${copiedSku ? ' copied' : ''}`} onClick={copySupplierSku} title="Copy supplier SKU">
                      <span className="v">{p.supplier_sku}</span>
                      {copiedSku
                        ? <span className="cf">Copied ✓</span>
                        : <svg className="cpi" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><rect x="9" y="9" width="11" height="11" rx="2" /><path d="M5 15V5a2 2 0 0 1 2-2h10" /></svg>}
                    </button>
                  ) : (
                    <span className="idnone">Not set</span>
                  )}
                  {p.supplier_name && <span className="idsup">{p.supplier_name}{p.pack_unit ? ` · ${p.pack_unit}` : ''}</span>}
                </div>
              </div>
            </div>
            <div className="badges">
              <span className={`bdg ${p.status === 'ACTIVE' ? 'ok' : 'neu'}`}><span className="st" />{p.status === 'ACTIVE' ? 'Active' : p.status}</span>
              <span className={`bdg ${p.data_grade === 'A' ? 'ok' : 'neu'}`}>Grade {p.data_grade}</span>
              {p.hero_sku && <span className="bdg acc">Hero SKU</span>}
              <span className="bdg neu">{p.storage_rule === 'clinic_only' ? 'Clinic only' : 'Any location'}</span>
              <span className="bdg neu">WOC target 4w</span>
              {prefOut && <span className="bdg warn">Preferred supplier out</span>}
            </div>
          </div>
          <div className="hdr-act">
            {p.shopify_status && p.shopify_status !== 'archived' &&
              <a className="btn" href={`https://petproject.hk/search?q=${encodeURIComponent(p.name)}`} target="_blank" rel="noreferrer">Open in Shopify</a>}
            {can('product_sensitive') && <StatusMenu current={p.status} saving={savingStatus} onPick={setStatus} />}
            {can('catalogue_onboard') && <ReparseButton scope="item" refId={p.sku_code} label="↻ Re-parse from catalogue" className="btn" />}
            {can('product_edit') && <button className="btn" onClick={() => setEditing(true)}>Edit details</button>}
            {can('product_edit') && <button className="btn pri" onClick={() => setEditing(true)}>Verify data</button>}
          </div>
        </div>

        {/* ── Alerts ─────────────────────────────────────────── */}
        {prefOut && (
          <div className="alert warn"><span className="ad" /><div>
            <b>Preferred supplier out of stock.</b> {preferred?.name ?? 'The preferred supplier'} is out{preferred?.expected_restock_at ? ` until ~${fmtDate(preferred.expected_restock_at)}` : ''}.
            {backup
              ? ` ${backup.name} has stock at ${moneyU(backup.basic_cost)}/${uomLabel}${costDiff != null ? ` (${moneyU(Math.abs(costDiff))} ${costDiff >= 0 ? 'dearer' : 'cheaper'})` : ''}.`
              : ' No in-stock backup supplier on record.'}
            {p.woc != null && p.woc >= 4 ? ` Cover is healthy (${p.woc.toFixed(1)}w), so no urgent action.` : p.woc != null ? ` Cover is ${p.woc.toFixed(1)}w — watch closely.` : ''}
          </div></div>
        )}
        {gpFailing && (
          <div className="alert red"><span className="ad" /><div>
            <b>Price below GP floor.</b> One or more channels sell below the {(p.gp_floor * 100).toFixed(0)}% GP floor for {p.category}. Review pricing below.
          </div></div>
        )}

        {/* ── Metrics ────────────────────────────────────────── */}
        <div className="metrics">
          <div className="metric"><div className="ml">Landed unit cost</div><div className="mv">{moneyU(landed)}</div><div className="ms">per {uomLabel}{upp > 1 ? ` · ${upp}/${packLabel}` : ''}</div></div>
          <div className="metric"><div className="ml">Clinic price</div><div className="mv">{money(primaryCh?.selling_price)}</div><div className={`ms ${primaryCh?.gp_pct != null && primaryCh.gp_pct >= p.gp_floor ? 'good' : 'amber'}`}>{primaryCh?.gp_pct != null ? `${(primaryCh.gp_pct * 100).toFixed(1)}% gross GP` : 'no price'}</div></div>
          <div className="metric"><div className="ml">Total stock</div><div className="mv">{p.total_qty.toLocaleString()}</div><div className="ms">Clinic {p.clinic_qty} · Whse {p.warehouse_qty}</div></div>
          <div className="metric"><div className="ml">Weeks of cover</div><div className="mv" style={{ color: wocColor }}>{p.woc != null ? `${p.woc.toFixed(1)}w` : '—'}</div><div className="ms">target 4w · ~{Math.round(p.weekly_demand)}/wk</div></div>
          <div className="metric"><div className="ml">120d sales</div><div className="mv">{p.sales_120d.toLocaleString()}</div><div className="ms">{uomLabel}s · est.</div></div>
          <div className="metric"><div className="ml">Best MBB cost</div><div className="mv">{moneyU(p.mbb_unit_cost)}</div><div className="ms">{p.mbb_unit_cost != null && p.unit_cost ? `${((1 - p.mbb_unit_cost / p.unit_cost) * 100).toFixed(0)}% off basic` : 'no bulk tier'}</div></div>
        </div>

        {/* ── Two-column grid ────────────────────────────────── */}
        <div className="grid">
          {/* ============ LEFT COLUMN ============ */}
          <div className="col">

            {/* Pricing & margin by channel */}
            <div className="card">
              <div className="ch"><div className="ct">Pricing &amp; margin by channel</div><div className="hint">GP floor {(p.gp_floor * 100).toFixed(0)}%</div></div>
              {p.mbb_unit_cost != null && <div className="mbbline"><span>Max Bulk-Buy cost</span><span>{moneyU(p.mbb_unit_cost)} / {uomLabel}</span></div>}
              {mr?.mbb_min_spend != null && <div className="mbbline"><span>Cost to hit MBB</span><span>{money(mr.mbb_min_spend)}{mr.mbb_min_qty != null ? ` · min ${mr.mbb_min_qty}` : ''}{mr.mbb_weeks_cover != null ? ` · ${mr.mbb_weeks_cover}w cover` : ''}</span></div>}
              <div className="cb flush">
                <table className="mtab"><thead><tr>
                  <th>Channel</th><th>Selling</th><th>Gross basic</th><th>Gross MBB</th><th>Net basic</th><th>Net MBB</th><th>Status</th>
                </tr></thead><tbody>
                  {(mr?.channels ?? []).map(mc => {
                    const pc = chMap.get(mc.channel)
                    return (
                      <tr key={mc.channel}>
                        <td><b>{CHANNEL_LABEL[mc.channel] ?? mc.channel}</b><div className="soldas">{CHANNEL_SUB[mc.channel] ?? ''}{pc?.units_per_listing && pc.units_per_listing > 1 ? ` · ${pc.units_per_listing}/listing` : ''}{mc.channel_fee_pct ? ` · ${(mc.channel_fee_pct * 100).toFixed(0)}% fee` : ''}{mc.delivery_cost ? ` · ${money(mc.delivery_cost)} SF` : ''}</div></td>
                        <td>{money(mc.selling_price)}</td>
                        <td><span className={`gpv ${gpCls(pc?.gp_pct)}`}>{pc?.gp_pct != null ? `${(pc.gp_pct * 100).toFixed(1)}%` : '—'}</span></td>
                        <td><span className={`gpv ${gpCls(mc.gp_pct_mbb)}`}>{mc.gp_pct_mbb != null ? `${(mc.gp_pct_mbb * 100).toFixed(1)}%` : '—'}</span></td>
                        <td><span className={`gpv ${gpCls(mc.basic_margin)}`}>{mc.basic_margin != null ? `${(mc.basic_margin * 100).toFixed(1)}%` : '—'}</span>{mc.basic_margin != null && mc.selling_price != null && <div style={{ fontSize: '10.5px', color: 'var(--faint)', marginTop: '2px', fontWeight: 500 }}>{money(mc.basic_margin * mc.selling_price)}</div>}</td>
                        <td><span className={`gpv ${gpCls(mc.mbb_margin)}`}>{mc.mbb_margin != null ? `${(mc.mbb_margin * 100).toFixed(1)}%` : '—'}</span>{mc.mbb_margin != null && mc.selling_price != null && <div style={{ fontSize: '10.5px', color: 'var(--faint)', marginTop: '2px', fontWeight: 500 }}>{money(mc.mbb_margin * mc.selling_price)}</div>}</td>
                        <td><span className={`cstat ${pc?.is_active ? 'on' : 'off'}`}><span className="d" />{pc?.is_active ? 'Active' : 'Off'}</span></td>
                      </tr>
                    )
                  })}
                  {(mr?.channels ?? []).length === 0 && <tr><td colSpan={7} style={{ textAlign: 'center', color: '#8A93A2', padding: '18px' }}>No channel pricing configured.</td></tr>}
                </tbody></table>
              </div>
              <div className="legend"><b>Gross</b> = margin before fees (sell − cost); <b>Net</b> = after platform fee / SF courier, shown as <b>%</b> over <b>HK$</b>. <b>Basic</b> vs <b>MBB</b> = standard vs discounted bulk-buy cost; <b>Cost to hit MBB</b> = spend to reach the bulk tier.</div>
            </div>

            {/* MBB margins by supplier — a margin for every buying option, per supplier */}
            {supBlocks.some(s => s.term_margins.length > 0) && (
              <div className="card">
                <div className="ch"><div className="ct">MBB margins by supplier</div><div className="hint">net (after fees) + gross · floor {(p.gp_floor * 100).toFixed(0)}%</div></div>
                <div className="cb">
                  {supBlocks.map((s, si) => {
                    const basicByCh = new Map(s.basic_channels.map(c => [c.channel, c]))
                    return (
                      <div key={s.supplier_id ?? si} style={{ marginBottom: si < supBlocks.length - 1 ? '16px' : 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '7px', marginBottom: '6px' }}>
                          <b style={{ fontSize: '13px', color: '#1F2733' }}>{s.name ?? 'Unknown supplier'}</b>
                          {s.is_preferred && <span className="bdg ok">Preferred</span>}
                          {s.is_primary && !s.is_preferred && <span className="bdg acc">Primary</span>}
                          <span style={{ fontSize: '11.5px', color: '#8A93A2', marginLeft: 'auto' }}>basic {moneyU(s.basic_cost)} / {uomLabel}</span>
                        </div>
                        <table className="mtab"><thead><tr>
                          <th>Buying option</th>
                          {marginChans.map(ch => <th key={ch.channel} style={{ textAlign: 'right' }}>{CHANNEL_LABEL[ch.channel] ?? ch.channel}</th>)}
                        </tr></thead><tbody>
                          <tr>
                            <td><b>Basic</b><div className="soldas">standard order</div></td>
                            {marginChans.map(ch => marginCell(ch.channel, basicByCh.get(ch.channel)))}
                          </tr>
                          {s.term_margins.map(tm => {
                            const t = termById.get(tm.id)
                            const mByCh = new Map(tm.channels.map(c => [c.channel, c]))
                            const noteOk = t?.note && !/^(n\/?a|none|-)$/i.test(t.note.trim()) ? t.note : null
                            const sub = [tm.min_qty ? `min ${tm.min_qty}` : null, tm.min_spend ? `buy-in ${money(tm.min_spend)}` : null, tm.weeks_cover != null ? `${tm.weeks_cover}w cover` : null, noteOk].filter(Boolean).join(' · ')
                            return (
                              <tr key={tm.id}>
                                <td><b>{termLabel(tm, t)}</b>{sub && <div className="soldas">{sub}</div>}</td>
                                {marginChans.map(ch => marginCell(ch.channel, mByCh.get(ch.channel)))}
                              </tr>
                            )
                          })}
                          {s.term_margins.length === 0 && <tr><td colSpan={1 + marginChans.length} style={{ color: '#8A93A2', fontSize: '11.5px' }}>No MBB terms for this supplier.</td></tr>}
                        </tbody></table>
                      </div>
                    )
                  })}
                </div>
                <div className="legend">Every buying option — <b>Basic</b> (standard order) or an <b>MBB term</b> — showing each channel's <b>net</b> margin (after platform fee / SF courier) over the <b>gross</b> margin. Green ≥ the {(p.gp_floor * 100).toFixed(0)}% floor; cheapest supplier is <b>Preferred</b>.</div>
              </div>
            )}

            {/* Stock & coverage */}
            <div className="card">
              <div className="ch"><div className="ct">Stock &amp; coverage</div><div className="hint">stock ÷ demand · target 4w</div></div>
              <div className="cb">
                <div className="stockrow">
                  <div className="stbox"><div className="n">{p.clinic_qty}</div><div className="l">Clinic</div></div>
                  <div className="stbox"><div className="n">{p.warehouse_qty}</div><div className="l">Warehouse</div></div>
                  <div className="stbox"><div className="n" style={{ color: wocColor }}>{p.woc != null ? `${p.woc.toFixed(1)}w` : '—'}</div><div className="l">Weeks of cover</div></div>
                </div>
                <div className="wocbar"><b style={{ width: `${wocPct}%`, background: wocColor }} /></div>
                <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', color: '#5B6472' }}>
                  <span>{p.woc == null ? 'No demand signal yet' : p.woc >= 4 ? `Healthy — ${(p.woc / 4).toFixed(1)}× the 4-week target` : p.woc >= 2 ? 'Amber — below the 4-week target' : 'Low — under 2 weeks of cover'}</span>
                  <span>~{Math.round(p.weekly_demand)} {uomLabel}s/week demand</span>
                </div>
                <div className="sim">
                  <div className="sl">Coverage simulator</div>
                  {p.weekly_demand > 0 ? <>
                    <div className="simrow">
                      <span style={{ fontSize: '12.5px', color: '#334155' }}>If I buy</span>
                      <input type="number" min={0} placeholder="0" value={simBuy} onChange={e => setSimBuy(e.target.value)} />
                      <span style={{ fontSize: '12.5px', color: '#5B6472' }}>{packLabel}s{upp > 1 ? ` (${upp} ${uomLabel})` : ''}</span>
                      {chipQtys.map(q => <span key={q} className="chip" onClick={() => setSimBuy(String(q))}>{q} {packLabel}s{mbbTiers[0]?.min_qty === q ? ' (MBB)' : ''}</span>)}
                    </div>
                    <div className="simout">
                      {simPacks <= 0
                        ? 'Enter a quantity to project the resulting weeks of cover.'
                        : <>+{(simAddW ?? 0).toFixed(1)}w → <b>{simTotalW.toFixed(1)} weeks total</b> &nbsp;·&nbsp; {simUnits.toLocaleString()} {uomLabel}s &nbsp;·&nbsp; {money(simUnits * simUnitCost)}{simTier ? <span style={{ color: '#3730A3', fontWeight: 600 }}> (MBB {moneyU(simUnitCost)}/{uomLabel})</span> : ''}</>}
                    </div>
                  </> : <div style={{ fontSize: '12px', color: '#8A93A2' }}>Add a demand signal to simulate coverage.</div>}
                </div>
              </div>
            </div>

            {/* Cost, units & MBB (functional editor, restyled) */}
            <CostMbbEditor product={p} onSaved={setItem} />

            {/* Suppliers & costs */}
            <div className="card">
              <div className="ch"><div className="ct">Suppliers &amp; costs</div>
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span className="hint">cheapest = preferred</span>
                  {can('product_edit') && <button className="linkbtn" onClick={() => setManageSuppliers(true)}>Manage</button>}
                </div>
              </div>
              <div className="cb flush">
                {suppliers.length === 0 && <div style={{ padding: '16px 17px', fontSize: '12px', color: '#8A93A2' }}>No suppliers linked yet.</div>}
                {[...suppliers].sort((a, b) => (a.basic_cost ?? Infinity) - (b.basic_cost ?? Infinity)).map(s => {
                  const isPref = s.id === preferred?.id
                  const out = s.stock_status === 'out_of_stock'
                  const closed = (s.stock_events ?? []).filter(e => e.restock_at != null)
                  return (
                    <div className="sup" key={s.id}>
                      <div className="sup-h">
                        <div>
                          <div className="sup-nm">{s.name ?? 'Unnamed supplier'}{isPref && <span className="prefflag">PREFERRED</span>}{s.is_primary && !isPref && <span className="prefflag" style={{ color: '#3730A3', background: '#EEF0FE' }}>PRIMARY</span>}</div>
                          <div className="sup-meta">{[s.code, `ID #${s.supplier_id ?? '—'}`, s.supplier_sku && `SKU ${s.supplier_sku}`].filter(Boolean).join(' · ')}</div>
                        </div>
                        <div className="sup-cost">
                          <div className="c">{moneyU(s.basic_cost)}</div>
                          <div className="u">{(s.units_per_pack ?? 1) > 1 ? `whole ${packLabel}` : `per ${uomLabel}`}</div>
                          {(s.units_per_pack ?? 1) > 1 && s.basic_cost != null && (
                            <div className="u" style={{ marginTop: '2px', color: '#5B6472', fontWeight: 600 }}>= {moneyU(s.basic_cost / (s.units_per_pack as number))} / {uomLabel} · {s.units_per_pack}/{packLabel}</div>
                          )}
                        </div>
                      </div>
                      <span className={`sstat ${out ? 'oos' : 'ok'}`}><span className="d" />{out ? 'OUT OF STOCK' : 'IN STOCK'}</span>
                      {out && (
                        <div className="oosdetail">
                          {s.reported_out_at && <div className="kv"><span className="k">Reported out</span><span className="v">{fmtDate(s.reported_out_at)}</span></div>}
                          <div className="kv"><span className="k">Expected restock</span><span className="v">{fmtDate(s.expected_restock_at) ?? 'Unknown'}</span></div>
                          {s.stock_confirmed_by && <div className="kv"><span className="k">Confirmed by</span><span className="v">{s.stock_confirmed_by}</span></div>}
                          {s.stock_note && <div className="kv"><span className="k">Note</span><span className="v" style={{ fontWeight: 400 }}>{s.stock_note}</span></div>}
                          {closed.length > 0 && (
                            <details>
                              <summary>Out-of-stock history ({closed.length})</summary>
                              {closed.map((e, i) => <div className="histrow" key={i}><span className="hd">{fmtDate(e.out_at)} → {fmtDate(e.restock_at)}</span><span className="hv">{e.days != null ? `${e.days}d` : '—'}</span></div>)}
                            </details>
                          )}
                        </div>
                      )}
                      {!out && prefOut && backup?.id === s.id && (
                        <div style={{ fontSize: '11.5px', color: '#5B6472', marginTop: '9px' }}>Backup source{costDiff != null ? ` — ${moneyU(Math.abs(costDiff))}/${uomLabel} ${costDiff >= 0 ? 'dearer' : 'cheaper'}` : ''}, available now while {preferred?.name} restocks.</div>
                      )}
                      {(s.mbb_term_list ?? []).length > 0 && (
                        <div style={{ fontSize: '11.5px', color: '#5B6472', marginTop: '11px' }}>MBB: {(s.mbb_term_list ?? []).length} term{(s.mbb_term_list ?? []).length > 1 ? 's' : ''}{s.mbb_term_list.find(t => t.effective_unit_cost != null) ? ` · best ${moneyU(Math.min(...s.mbb_term_list.filter(t => t.effective_unit_cost != null).map(t => t.effective_unit_cost as number)))}/${uomLabel}` : ''}</div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>

            {/* Competitor prices — linked competitor URLs + scraped selling prices */}
            <CompetitorPrices product={p} />

            {/* Sales — last 120 days, per channel (algo-dashboard multichannel sync) */}
            <div className="card">
              <div className="ch"><div className="ct">Sales — last 120 days</div><div className="hint">{p.sales_120d.toLocaleString()} {uomLabel}s total</div></div>
              <div className="cb">
                {trend.length > 0 || wdbc ? (
                  <>
                    {trend.length > 0 && (
                      <div className="spark">
                        {trend.map(t => (
                          <div key={t.month} className="bcol">
                            <div className="bar" style={{ height: `${Math.max(6, Math.round(t.units / trendMax * 100))}%` }} title={`${t.units.toLocaleString()} ${uomLabel}s in ${monthLabel(t.month)}`}><b /></div>
                            <div className="bl">{monthLabel(t.month)}</div>
                          </div>
                        ))}
                      </div>
                    )}
                    {wdbc && (
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px', marginTop: trend.length > 0 ? '14px' : 0 }}>
                        {([['Clinic', wdbc.clinic], ['Shopify', wdbc.shopify], ['HKTV', wdbc.hktv]] as [string, number | null][]).map(([k, v]) => (
                          <div key={k} className="stbox"><div className="n" style={{ fontSize: '16px' }}>{v != null ? Math.round(v * 120 / 7).toLocaleString() : '—'}</div><div className="l">{k}</div></div>
                        ))}
                      </div>
                    )}
                  </>
                ) : (
                  <div style={{ border: '1px dashed #D5D8F7', borderRadius: '10px', background: '#FAFBFC', padding: '18px', textAlign: 'center' }}>
                    <div style={{ fontSize: '22px', fontWeight: 700, color: '#0F172A', fontVariantNumeric: 'tabular-nums' }}>{p.sales_120d.toLocaleString()}</div>
                    <div style={{ fontSize: '11px', color: '#8A93A2', marginTop: '2px' }}>demand-based estimate ({uomLabel}s / 120d)</div>
                    <div style={{ fontSize: '11.5px', color: '#5B6472', marginTop: '10px', lineHeight: 1.5 }}>No sales pulled for this SKU yet — it may not have sold on clinic, HKTV or Shopify in the last 120 days.</div>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* ============ RIGHT COLUMN ============ */}
          <div className="col">

            {/* Product */}
            <div className="card">
              <div className="ch"><div className="ct">Product</div></div>
              <div className="cb">
                <div className="kv"><span className="k">Brand</span><span className="v"><Val v={p.brand} /></span></div>
                <div className="kv"><span className="k">Category</span><span className="v">{p.category}</span></div>
                <div className="kv"><span className="k">Subcategory</span><span className="v acc"><Val v={p.subcategory} /></span></div>
                <div className="kv"><span className="k">Species</span><span className="v"><Val v={p.species} /></span></div>
                <div className="kv"><span className="k">Sell unit (UOM)</span><span className="v"><Val v={p.uom} /></span></div>
                <div className="kv"><span className="k">Min sellable</span><span className="v">{p.min_sellable_qty ?? 1} × {uomLabel}</span></div>
                <div className="kv"><span className="k">Pack size</span><span className="v">{upp > 1 ? `${upp} ${uomLabel} / ${packLabel}` : '—'}</span></div>
                <div className="kv"><span className="k">Supplier MOQ</span><span className="v">{p.min_purchase_qty != null ? `${p.min_purchase_qty} ${packLabel}${p.min_purchase_qty === 1 ? '' : 's'}` : '—'}</span></div>
                <div className="kv"><span className="k">Unit weight</span><span className="v">{p.weight_g != null ? `${gToUnit(p.weight_g, p.weight_unit)} ${p.weight_unit ?? 'kg'}` : '—'}</span></div>
                <div className="kv"><span className="k">Storage rule</span><span className="v">{p.storage_rule === 'clinic_only' ? 'Clinic only' : 'Any location'}</span></div>
                <div className="kv"><span className="k">Hero SKU</span><span className="v acc">{p.hero_sku ? 'Yes' : 'No'}</span></div>
                <div className="kv"><span className="k">Listed on</span><span className="v">{listedOn.map(([l, on]) => <span key={l} className={`plat ${on ? 'on' : 'off'}`}>{l}</span>)}</span></div>
              </div>
            </div>

            {/* Data quality & verification */}
            <div className="card">
              <div className="ch"><div className="ct">Data quality &amp; verification</div></div>
              <div className="cb">
                <div className="kv"><span className="k">Grade</span><span className="v" style={{ color: p.data_grade === 'A' ? '#15803D' : '#B45309' }}>{p.data_grade === 'A' ? 'A — complete' : 'C — incomplete'}</span></div>
                <div className="kv"><span className="k">HITL verified</span><span className="v">{p.hitl_verified ? `Yes${p.uom_verified_by ? ` · ${p.uom_verified_by}` : ''}` : 'No'}</span></div>
                <div className="kv"><span className="k">Verified on</span><span className="v">{fmtDate(p.uom_verified_at) ?? '—'}</span></div>
                <div className="kv"><span className="k">Pack size</span><span className="v" style={{ color: p.uom_verified_at ? '#15803D' : '#B45309' }}>{p.uom_verified_at ? `Locked · ${upp}/${packLabel}` : upp > 1 ? `${upp}/${packLabel} · unverified` : '—'}</span></div>
                <div className="kv"><span className="k">Cost source</span><span className="v">{p.cost_source?.replace(/_/g, ' ') ?? '—'}{p.cost_is_stale ? ' · stale' : ''}</span></div>
                {(p.cost_sheet_conflict || p.pack_sheet_conflict) && (
                  <div className="hitl" style={{ background: '#FBEBEA', borderColor: '#F1CDC9', color: '#7A2A24' }}>
                    <b>Sheet conflict.</b> {p.cost_sheet_conflict ? `Sheet cost ${moneyU(p.basic_cost_sheet)} disagrees with the IMS-locked cost. ` : ''}{p.pack_sheet_conflict ? `Sheet pack size (${p.units_per_pack_sheet}) disagrees with the verified value. ` : ''}Resolve in the Cost card.
                  </div>
                )}
                {p.units_per_pack != null && !p.uom_verified_at && (
                  <div className="hitl">
                    <b>Pack size unverified.</b> Confirm {upp} {uomLabel} / {packLabel} after physically checking, to lock it against sheet overwrites.
                    <div className="hr">
                      <input placeholder="Your name / initials" value={verifiedBy} onChange={e => setVerifiedBy(e.target.value)} />
                      <button onClick={() => confirmUom(p.sku_code)} disabled={uomSaving}>{uomSaving ? '…' : 'Confirm correct'}</button>
                    </div>
                    {uomError && <div style={{ marginTop: '6px', color: '#C0362C' }}>{uomError}</div>}
                  </div>
                )}
              </div>
            </div>

            {/* Tags */}
            <div className="card">
              <div className="ch"><div className="ct">Tags</div>
                {tagDraft == null
                  ? <button className="linkbtn" onClick={() => setTagDraft([...(p.tags ?? [])])}>Edit</button>
                  : <span style={{ display: 'flex', gap: '10px' }}>
                      <button className="linkbtn" onClick={() => { setTagDraft(null); setTagInput('') }}>Cancel</button>
                      <button className="linkbtn" style={{ color: '#15803D' }} onClick={() => saveTags(tagDraft)} disabled={savingTags}>{savingTags ? 'Saving…' : 'Save'}</button>
                    </span>}
              </div>
              <div className="cb">
                {tagDraft == null ? (
                  (p.tags ?? []).length > 0
                    ? (p.tags ?? []).map(t => <span key={t} className="tagchip">{t}</span>)
                    : <span style={{ fontSize: '12px', color: '#8A93A2' }}>No tags.</span>
                ) : <>
                  {tagDraft.map(t => <span key={t} className="tagchip">{t}<span style={{ marginLeft: '6px', color: '#C0362C', cursor: 'pointer', fontWeight: 700 }} onClick={() => setTagDraft(tagDraft.filter(x => x !== t))}>×</span></span>)}
                  <div className="miniform" style={{ marginTop: '8px' }}>
                    <input placeholder="Add tag + Enter" value={tagInput}
                      onChange={e => setTagInput(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter' && tagInput.trim()) { if (!tagDraft.includes(tagInput.trim())) setTagDraft([...tagDraft, tagInput.trim()]); setTagInput('') } }} />
                  </div>
                </>}
              </div>
            </div>

            {/* Onboarding history */}
            <div className="card">
              <div className="ch"><div className="ct">Onboarding history</div></div>
              <div className="cb">
                {history.length === 0 && <span style={{ fontSize: '12px', color: '#8A93A2' }}>No history recorded.</span>}
                {history.slice(0, 8).map(e => (
                  <div className="aud" key={e.id}>
                    <div className="ad" />
                    <div>
                      <div className="at"><b>{auditLabel(e.action)}</b>{auditSummary(e) ? ` — ${auditSummary(e)}` : ''}</div>
                      <div className="aw">{fmtWhen(e.created_at)}{e.display_name ? ` · ${e.display_name}` : ''}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ── Footer actions ─────────────────────────────────── */}
        <div className="footbar">
          <div className="l">
            <Link className="btn pri" to={'/catalogues' as never}>Create PO in Rosetta →</Link>
            <Link className="btn" to={'/pricing' as never}>View Pricing Matrix</Link>
          </div>
          <div className="r">
            <button className="btn" onClick={goBack}>Back to Inventory</button>
            {can('product_edit') && <button className="btn danger" onClick={hitlUnverify} disabled={unverifying}>{unverifying ? '…' : 'Unverify (allow re-scan)'}</button>}
          </div>
        </div>
      </div>

      {editing && <EditSkuModal product={p} onSaved={setItem} onClose={() => setEditing(false)} />}
      {changingSku && <ChangeSkuModal product={p} history={skuHistory} onClose={() => setChangingSku(false)} />}
      {manageSuppliers && <SupplierManagerModal product={p} onSaved={setItem} onClose={() => setManageSuppliers(false)} />}
    </>
  )
}

const num = (s: string) => { const n = parseFloat(s); return s.trim() !== '' && Number.isFinite(n) ? n : null }
const int = (s: string) => { const n = parseInt(s, 10); return s.trim() !== '' && Number.isFinite(n) ? n : null }

// Ordering-UOM vocabulary for the supplier terms dropdowns (sell units + pack/bulk units).
// The product's own sell-UOM and pack-unit are surfaced first at render time; any existing value
// not in this list is preserved as its own option.
const UOM_OPTIONS = [
  'unit', 'each', 'piece', 'pack', 'box', 'case', 'carton', 'inner', 'outer', 'dozen', 'set',
  'can', 'pouch', 'sachet', 'bottle', 'jar', 'tub', 'bag',
  'tablet', 'capsule', 'strip', 'blister', 'vial', 'ampoule', 'tube', 'syringe', 'roll',
  'ml', 'L', 'g', 'kg',
]

function SupplierManagerModal({ product, onSaved, onClose }: { product: Product; onSaved: (p: Product) => void; onClose: () => void }) {
  const [current, setCurrent] = useState(product)
  const [opts, setOpts] = useState<{ id: number; code: string; name: string }[]>([])
  const [busy, setBusy] = useState(false)
  type Draft = {
    supplier_id: string; supplier_sku: string; basic_cost: string; units_per_pack: string
    order_increment_qty: string; order_increment_uom: string
    minimum_order_qty: string; minimum_order_uom: string; minimum_order_source: string; pricing_note: string
  }
  const blankDraft: Draft = { supplier_id: '', supplier_sku: '', basic_cost: '', units_per_pack: '', order_increment_qty: '', order_increment_uom: '', minimum_order_qty: '', minimum_order_uom: '', minimum_order_source: '', pricing_note: '' }
  const [drafts, setDrafts] = useState<Record<number, Draft>>({})
  const [add, setAdd] = useState<Draft>(blankDraft)
  const [stockDraft, setStockDraft] = useState<Record<number, { restock: string; note: string }>>({})
  type SupFull = { id: number; effective_unit_cost: number | null; order_increment_qty: number | null; order_increment_uom: string | null; minimum_order_qty: number | null; minimum_order_uom: string | null; minimum_order_source: string | null; pricing_note: string | null; cost_source: string | null; cost_source_ref: string | null; pack_source: string | null; cost_updated_at: string | null }
  const [full, setFull] = useState<Record<number, SupFull>>({})
  const puom = current.uom ?? 'unit'

  useEffect(() => { fetch(`${API}/suppliers`, { headers: authHeaders() }).then(r => r.ok ? r.json() : []).then(setOpts).catch(() => {}) }, [])
  // Full per-supplier terms (ordering fields + effective cost + provenance) — the main product serializer omits these.
  useEffect(() => {
    fetch(`${API}/products/${skuToPath(product.sku_code)}/suppliers`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then((d: { suppliers?: SupFull[] } | null) => { if (d?.suppliers) setFull(Object.fromEntries(d.suppliers.map(s => [s.id, s]))) })
      .catch(() => {})
  }, [current, product.sku_code])
  useEffect(() => {
    const d: Record<number, Draft> = {}
    for (const s of current.all_suppliers) {
      const f = full[s.id]
      d[s.id] = {
        supplier_id: String(s.supplier_id ?? ''), supplier_sku: s.supplier_sku ?? '',
        basic_cost: s.basic_cost != null ? String(s.basic_cost) : '', units_per_pack: s.units_per_pack != null ? String(s.units_per_pack) : '',
        order_increment_qty: f?.order_increment_qty != null ? String(f.order_increment_qty) : '', order_increment_uom: f?.order_increment_uom ?? '',
        minimum_order_qty: f?.minimum_order_qty != null ? String(f.minimum_order_qty) : '', minimum_order_uom: f?.minimum_order_uom ?? '',
        minimum_order_source: f?.minimum_order_source ?? '', pricing_note: f?.pricing_note ?? '',
      }
    }
    setDrafts(d)
  }, [current, full])

  async function call(method: string, path: string, body?: unknown): Promise<boolean> {
    setBusy(true)
    try {
      const r = await fetch(`${API}/products/${skuToPath(product.sku_code)}/suppliers${path}`, {
        method, headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: body ? JSON.stringify(body) : undefined,
      })
      if (!r.ok) { toast.error((await r.json().catch(() => ({}))).detail ?? 'Failed'); return false }
      const full = await fetch(`${API}/products/${skuToPath(product.sku_code)}`, { headers: authHeaders() }).then(x => x.ok ? x.json() : null)
      if (full) { setCurrent(full); onSaved(full) }
      return true
    } catch { toast.error('Failed'); return false } finally { setBusy(false) }
  }

  const numOrNull = (s: string) => { const t = s.trim(); return t === '' || isNaN(parseInt(t, 10)) ? null : parseInt(t, 10) }
  const strOrNull = (s: string) => s.trim() === '' ? null : s.trim()
  async function saveRow(id: number) {
    const dr = drafts[id]; if (!dr) return
    if (numOrNull(dr.order_increment_qty) != null && !strOrNull(dr.order_increment_uom)) { toast.error('Order increment UOM is required when a qty is set'); return }
    if (numOrNull(dr.minimum_order_qty) != null && !strOrNull(dr.minimum_order_uom)) { toast.error('Minimum order UOM is required when a qty is set'); return }
    const body: Record<string, unknown> = {
      supplier_sku: strOrNull(dr.supplier_sku),
      order_increment_qty: numOrNull(dr.order_increment_qty), order_increment_uom: strOrNull(dr.order_increment_uom),
      minimum_order_qty: numOrNull(dr.minimum_order_qty), minimum_order_uom: strOrNull(dr.minimum_order_uom),
      minimum_order_source: strOrNull(dr.minimum_order_source), pricing_note: strOrNull(dr.pricing_note),
    }
    if (dr.supplier_id) body.supplier_id = parseInt(dr.supplier_id, 10)
    if (dr.basic_cost.trim() !== '') body.basic_cost = parseFloat(dr.basic_cost)
    if (dr.units_per_pack.trim() !== '') body.units_per_pack = parseInt(dr.units_per_pack, 10)
    // MBB is managed via the relational terms editor (Cost card), not per-supplier scalars here.
    if (await call('PATCH', `/${id}`, body)) toast.success('Supplier saved')
  }
  async function removeRow(id: number, name: string | null) {
    const ok = await confirmDialog({ title: 'Remove supplier?', message: `Unlink ${name ?? 'this supplier'} from ${product.sku_code}?`, confirmLabel: 'Remove', danger: true })
    if (ok && await call('DELETE', `/${id}`)) toast.success('Supplier removed')
  }
  async function makePrimary(id: number) { if (await call('PATCH', `/${id}`, { is_primary: true })) toast.success('Primary supplier set') }
  async function setStock(id: number, status: 'in_stock' | 'out_of_stock', cur: { expected_restock_at: string | null; stock_note: string | null }) {
    const sd = stockDraft[id] ?? { restock: cur.expected_restock_at ?? '', note: cur.stock_note ?? '' }
    if (await call('PATCH', `/${id}/stock`, { status, expected_restock_at: sd.restock.trim() || null, note: sd.note.trim() || null }))
      toast.success(status === 'out_of_stock' ? 'Marked out of stock' : 'Back in stock')
  }
  async function addSupplier() {
    if (!add.supplier_id) { toast.error('Pick a supplier'); return }
    const body: Record<string, unknown> = { supplier_id: parseInt(add.supplier_id, 10), supplier_sku: add.supplier_sku.trim() }
    if (add.basic_cost.trim() !== '') body.basic_cost = parseFloat(add.basic_cost)
    if (add.units_per_pack.trim() !== '') body.units_per_pack = parseInt(add.units_per_pack, 10)
    if (await call('POST', '', body)) { toast.success('Supplier added'); setAdd(blankDraft) }
  }

  const inp: React.CSSProperties = { border: '1px solid #E2E8F0', borderRadius: '6px', padding: '6px 8px', fontSize: '12px', background: 'white', width: '100%', boxSizing: 'border-box' }
  const lblS: React.CSSProperties = { fontSize: '10px', fontWeight: 600, color: '#94A3B8', display: 'flex', flexDirection: 'column', gap: '3px' }
  // Order-UOM options: this SKU's sell-UOM + pack-unit first (the likeliest picks), then the shared
  // vocabulary; deduped case-insensitively. Rendered via uomSelect, which also keeps any stored value.
  const orderUomOptions = (() => {
    const seen = new Set<string>(); const out: string[] = []
    for (const u of [current.uom, current.pack_unit, ...UOM_OPTIONS]) {
      const t = (u ?? '').trim()
      if (t && !seen.has(t.toLowerCase())) { seen.add(t.toLowerCase()); out.push(t) }
    }
    return out
  })()
  const uomSelect = (value: string, onChange: (v: string) => void) => (
    <select style={inp} value={value} onChange={e => onChange(e.target.value)}>
      <option value="">—</option>
      {orderUomOptions.map(u => <option key={u} value={u}>{u}</option>)}
      {value && !orderUomOptions.includes(value) && <option value={value}>{value}</option>}
    </select>
  )
  const rows = current.all_suppliers
  const linkedIds = new Set(rows.map(r => r.supplier_id))
  const available = opts.filter(o => !linkedIds.has(o.id))

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '48px 20px', overflowY: 'auto' }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'white', borderRadius: '14px', width: '720px', maxWidth: '100%', padding: '22px', boxShadow: '0 20px 50px rgba(0,0,0,0.25)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '17px', fontWeight: 700, color: '#0F172A' }}>Manage suppliers</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: '22px', color: '#94A3B8', cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
        <p style={{ fontSize: '12px', color: '#94A3B8', margin: '2px 0 16px' }}>{product.sku_code} · {rows.length} supplier{rows.length === 1 ? '' : 's'}</p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {rows.map(s => {
            const dr = drafts[s.id]
            return (
              <div key={s.id} style={{ border: `1px solid ${s.is_primary ? '#C7D2FE' : '#E2E8F0'}`, borderRadius: '10px', padding: '12px', background: s.is_primary ? '#F5F7FF' : 'white' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                  {s.is_primary
                    ? <span style={{ fontSize: '10.5px', fontWeight: 700, color: '#4338CA', background: '#E0E7FF', padding: '2px 8px', borderRadius: '999px' }}>★ PRIMARY</span>
                    : <button onClick={() => makePrimary(s.id)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: '#6366F1', background: 'white', border: '1px solid #C7D2FE', borderRadius: '6px', padding: '3px 9px', cursor: 'pointer' }}>Make primary</button>}
                  <span style={{ flex: 1 }} />
                  <button onClick={() => saveRow(s.id)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: 'white', background: '#6366F1', border: 'none', borderRadius: '6px', padding: '4px 12px', cursor: 'pointer' }}>Save</button>
                  <button onClick={() => removeRow(s.id, s.name)} disabled={busy || rows.length <= 1} title={rows.length <= 1 ? 'A SKU must keep at least one supplier' : 'Remove'} style={{ fontSize: '11px', fontWeight: 600, color: rows.length <= 1 ? '#CBD5E1' : '#DC2626', background: 'white', border: `1px solid ${rows.length <= 1 ? '#E2E8F0' : '#FCA5A5'}`, borderRadius: '6px', padding: '4px 10px', cursor: rows.length <= 1 ? 'not-allowed' : 'pointer' }}>Remove</button>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 0.8fr 0.8fr', gap: '8px' }}>
                  <label style={lblS}>Supplier
                    <select style={inp} value={dr?.supplier_id ?? ''} onChange={e => setDrafts({ ...drafts, [s.id]: { ...dr, supplier_id: e.target.value } })}>
                      {s.supplier_id == null && <option value="">— none —</option>}
                      {opts.map(o => <option key={o.id} value={o.id} disabled={o.id !== s.supplier_id && linkedIds.has(o.id)}>{o.name} ({o.code})</option>)}
                    </select>
                  </label>
                  <label style={lblS}>Supplier SKU<input style={inp} value={dr?.supplier_sku ?? ''} onChange={e => setDrafts({ ...drafts, [s.id]: { ...dr, supplier_sku: e.target.value } })} /></label>
                  <label style={lblS}>Cost (HK$)<input type="number" style={inp} value={dr?.basic_cost ?? ''} onChange={e => setDrafts({ ...drafts, [s.id]: { ...dr, basic_cost: e.target.value } })} /></label>
                  <label style={lblS}>Cost basis units<input type="number" style={inp} value={dr?.units_per_pack ?? ''} onChange={e => setDrafts({ ...drafts, [s.id]: { ...dr, units_per_pack: e.target.value } })} /></label>
                </div>
                {/* Cost-basis readout: effective unit cost + provenance */}
                {(() => {
                  const bc = parseFloat(dr?.basic_cost ?? ''); const up = parseInt(dr?.units_per_pack ?? '', 10)
                  const upv = up && up > 0 ? up : 1
                  const eff = !isNaN(bc) ? (upv > 1 ? bc / upv : bc) : null
                  const cs = full[s.id]?.cost_source
                  return (
                    <div style={{ marginTop: '7px', display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap', fontSize: '11px', color: '#64748B' }}>
                      {eff != null
                        ? <span>Effective unit cost <b style={{ color: '#0F172A' }}>HK${eff.toFixed(2)}</b> / {puom}{upv > 1 && <span style={{ color: '#94A3B8' }}> (HK${bc} ÷ {upv})</span>}</span>
                        : <span style={{ color: '#CBD5E1' }}>enter cost + basis units to see effective unit cost</span>}
                      {cs && <span style={{ padding: '1px 7px', borderRadius: '99px', background: '#F1F5F9', color: '#64748B', fontWeight: 600 }}>cost: {cs}</span>}
                    </div>
                  )
                })()}
                {parseInt(dr?.units_per_pack ?? '', 10) > 1 && !strOrNull(dr?.pricing_note ?? '') &&
                  <div style={{ marginTop: '5px', fontSize: '10.5px', color: '#B45309' }}>⚠ Cost basis units &gt; 1 — add a pricing note explaining what the price covers (e.g. per box of N).</div>}
                {['Can(s)', 'Pouch(es)'].includes((dr?.order_increment_uom ?? '').trim()) && /\b(dry|bag|kg|lbs?)\b/i.test(current.name) &&
                  <div style={{ marginTop: '5px', fontSize: '10.5px', color: '#B45309' }}>⚠ Dry/bag product with a can/pouch order UOM — likely wrong.</div>}
                {/* Supplier ordering terms */}
                <div style={{ marginTop: '10px', paddingTop: '10px', borderTop: '1px solid #F1F5F9' }}>
                  <p style={{ fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '.04em', marginBottom: '6px' }}>Supplier ordering</p>
                  <div style={{ display: 'grid', gridTemplateColumns: '0.7fr 1fr 0.7fr 1fr 1.1fr', gap: '8px' }}>
                    <label style={lblS}>Order increment<input type="number" style={inp} value={dr?.order_increment_qty ?? ''} onChange={e => setDrafts({ ...drafts, [s.id]: { ...dr, order_increment_qty: e.target.value } })} /></label>
                    <label style={lblS}>Increment UOM{uomSelect(dr?.order_increment_uom ?? '', v => setDrafts({ ...drafts, [s.id]: { ...dr, order_increment_uom: v } }))}</label>
                    <label style={lblS}>Min order<input type="number" style={inp} value={dr?.minimum_order_qty ?? ''} onChange={e => setDrafts({ ...drafts, [s.id]: { ...dr, minimum_order_qty: e.target.value } })} /></label>
                    <label style={lblS}>Min order UOM{uomSelect(dr?.minimum_order_uom ?? '', v => setDrafts({ ...drafts, [s.id]: { ...dr, minimum_order_uom: v } }))}</label>
                    <label style={lblS}>Source
                      <select style={inp} value={dr?.minimum_order_source ?? ''} onChange={e => setDrafts({ ...drafts, [s.id]: { ...dr, minimum_order_source: e.target.value } })}>
                        <option value="">—</option>
                        <option value="catalogue">catalogue</option>
                        <option value="inferred_from_order_multiple">inferred from multiple</option>
                        <option value="manual">manual</option>
                      </select>
                    </label>
                  </div>
                  <label style={{ ...lblS, marginTop: '8px' }}>Pricing note<input style={inp} placeholder="e.g. Price is per box of 8 tests; 8 sellable units per box" value={dr?.pricing_note ?? ''} onChange={e => setDrafts({ ...drafts, [s.id]: { ...dr, pricing_note: e.target.value } })} /></label>
                  {(full[s.id]?.cost_source === 'catalogue' || full[s.id]?.pack_source === 'catalogue') &&
                    <div style={{ marginTop: '8px', fontSize: '10.5px', color: '#64748B', background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: '6px', padding: '6px 8px' }}>
                      📄 Catalogue evidence{full[s.id]?.cost_source_ref ? ` · ${full[s.id]?.cost_source_ref}` : ''}{full[s.id]?.cost_updated_at ? ` · ${full[s.id]?.cost_updated_at?.slice(0, 10)}` : ''}
                    </div>}
                </div>
                <div style={{ marginTop: '10px', paddingTop: '10px', borderTop: '1px solid #F1F5F9', display: 'flex', gap: '7px', alignItems: 'center', flexWrap: 'wrap' }}>
                  {s.stock_status === 'out_of_stock'
                    ? <span style={{ fontSize: '10.5px', fontWeight: 700, color: '#DC2626', background: '#FEF2F2', border: '1px solid #FCA5A5', borderRadius: '6px', padding: '2px 8px' }}>● OUT OF STOCK{s.reported_out_at ? ` · since ${s.reported_out_at}` : ''}</span>
                    : <span style={{ fontSize: '10.5px', fontWeight: 700, color: '#166534', background: '#ECFDF5', border: '1px solid #A7F3D0', borderRadius: '6px', padding: '2px 8px' }}>● In stock</span>}
                  <input placeholder="Restock date" defaultValue={s.expected_restock_at ?? ''} onChange={e => setStockDraft({ ...stockDraft, [s.id]: { restock: e.target.value, note: stockDraft[s.id]?.note ?? s.stock_note ?? '' } })} style={{ ...inp, width: '118px', padding: '4px 8px', fontSize: '11px' }} />
                  <input placeholder="Note" defaultValue={s.stock_note ?? ''} onChange={e => setStockDraft({ ...stockDraft, [s.id]: { restock: stockDraft[s.id]?.restock ?? s.expected_restock_at ?? '', note: e.target.value } })} style={{ ...inp, flex: 1, minWidth: '90px', padding: '4px 8px', fontSize: '11px' }} />
                  {s.stock_status === 'out_of_stock' ? <>
                    <button onClick={() => setStock(s.id, 'out_of_stock', s)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: '#B45309', background: 'white', border: '1px solid #FCD34D', borderRadius: '6px', padding: '4px 10px', cursor: 'pointer' }}>Update</button>
                    <button onClick={() => setStock(s.id, 'in_stock', s)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: 'white', background: '#16A34A', border: 'none', borderRadius: '6px', padding: '4px 10px', cursor: 'pointer' }}>Back in stock</button>
                  </> : <button onClick={() => setStock(s.id, 'out_of_stock', s)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: '#DC2626', background: 'white', border: '1px solid #FCA5A5', borderRadius: '6px', padding: '4px 10px', cursor: 'pointer' }}>Mark out of stock</button>}
                  {(s.stock_events?.length ?? 0) > 0 && <span style={{ fontSize: '10.5px', color: '#94A3B8' }} title={s.stock_events.map(e => `${e.out_at} → ${e.restock_at ?? 'ongoing'} (${e.days ?? '?'}d)`).join('\n')}>{s.stock_events.length} OOS period{s.stock_events.length > 1 ? 's' : ''}</span>}
                </div>
              </div>
            )
          })}
          {rows.length === 0 && <p style={{ fontSize: '12px', color: '#94A3B8' }}>No suppliers linked yet — add one below.</p>}
        </div>

        <div style={{ marginTop: '14px', border: '1px dashed #CBD5E1', borderRadius: '10px', padding: '12px', background: '#FAFAFA' }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '8px' }}>Add supplier</p>
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr 0.8fr 0.8fr auto', gap: '8px', alignItems: 'end' }}>
            <label style={lblS}>Supplier
              <select style={inp} value={add.supplier_id} onChange={e => setAdd({ ...add, supplier_id: e.target.value })}>
                <option value="">— pick —</option>
                {available.map(o => <option key={o.id} value={o.id}>{o.name} ({o.code})</option>)}
              </select>
            </label>
            <label style={lblS}>Supplier SKU<input style={inp} value={add.supplier_sku} onChange={e => setAdd({ ...add, supplier_sku: e.target.value })} /></label>
            <label style={lblS}>Cost (HK$)<input type="number" style={inp} value={add.basic_cost} onChange={e => setAdd({ ...add, basic_cost: e.target.value })} /></label>
            <label style={lblS}>Cost basis units<input type="number" style={inp} value={add.units_per_pack} onChange={e => setAdd({ ...add, units_per_pack: e.target.value })} /></label>
            <button onClick={addSupplier} disabled={busy || !add.supplier_id} style={{ fontSize: '12px', fontWeight: 600, color: 'white', background: !add.supplier_id ? '#CBD5E1' : '#16A34A', border: 'none', borderRadius: '7px', padding: '7px 14px', cursor: !add.supplier_id ? 'default' : 'pointer', height: '34px' }}>Add</button>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '18px' }}>
          <button onClick={onClose} style={{ padding: '9px 18px', fontSize: '13px', fontWeight: 600, color: '#64748B', background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', cursor: 'pointer' }}>Done</button>
        </div>
      </div>
    </div>
  )
}

function ChangeSkuModal({ product, history, onClose }: { product: Product; history: { from: string; to: string; at: string; by: string | null }[]; onClose: () => void }) {
  const [val, setVal] = useState(product.sku_code)
  const [busy, setBusy] = useState(false)
  const [regen, setRegen] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  async function regenerate() {
    setRegen(true); setErr(null)
    try {
      const r = await fetch(`${API}/sku/next?category=${encodeURIComponent(product.category)}`, { headers: authHeaders() })
      const d = await r.json().catch(() => ({}))
      if (r.ok && d.next_sku) setVal(String(d.next_sku))
      else setErr(d.error ?? 'Could not generate a SKU for this category')
    } catch { setErr('Could not generate a SKU') } finally { setRegen(false) }
  }

  async function save() {
    const next = val.trim()
    setErr(null)
    if (!next) { setErr('Enter a SKU'); return }
    if (next === product.sku_code) { onClose(); return }
    setBusy(true)
    try {
      const r = await fetch(`${API}/products/${skuToPath(product.sku_code)}/sku-code`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ new_sku: next }),
      })
      if (r.ok) {
        toast.success(`SKU changed to ${next}`)
        window.location.href = `/items/${skuToPath(next)}`
      } else {
        const d = await r.json().catch(() => ({}))
        setErr(d.detail ?? 'Could not change SKU')   // 409 = duplicate, stays open
        setBusy(false)
      }
    } catch { setErr('Could not change SKU'); setBusy(false) }
  }

  const inp: React.CSSProperties = { border: `1px solid ${err ? '#FCA5A5' : '#E2E8F0'}`, borderRadius: '7px', padding: '9px 11px', fontSize: '14px', fontFamily: 'monospace', background: 'white', width: '100%', boxSizing: 'border-box' }

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '70px 20px', overflowY: 'auto' }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'white', borderRadius: '14px', width: '460px', maxWidth: '100%', padding: '22px', boxShadow: '0 20px 50px rgba(0,0,0,0.25)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '17px', fontWeight: 700, color: '#0F172A' }}>Change SKU code</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: '22px', color: '#94A3B8', cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
        <p style={{ fontSize: '12px', color: '#94A3B8', margin: '2px 0 16px' }}>Current: <span style={{ fontFamily: 'monospace', color: '#64748B' }}>{product.sku_code}</span></p>

        <label style={{ fontSize: '11px', fontWeight: 600, color: '#64748B', display: 'block', marginBottom: '5px' }}>New SKU</label>
        <div style={{ display: 'flex', gap: '8px' }}>
          <input style={inp} value={val} autoFocus onChange={e => { setVal(e.target.value); setErr(null) }}
            onKeyDown={e => e.key === 'Enter' && save()} placeholder="e.g. 40005811" />
          <button onClick={regenerate} disabled={regen} title="Generate a unique SKU for this category" style={{ flexShrink: 0, padding: '0 13px', fontSize: '12px', fontWeight: 600, color: '#6366F1', background: 'white', border: '1px solid #C7D2FE', borderRadius: '7px', cursor: regen ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>{regen ? '…' : '⟳ Regenerate'}</button>
        </div>
        {err && <p style={{ fontSize: '12px', color: '#DC2626', marginTop: '8px', fontWeight: 500 }}>{err}</p>}

        {history.length > 0 && (
          <div style={{ marginTop: '16px' }}>
            <div style={{ fontSize: '10.5px', fontWeight: 700, color: '#94A3B8', letterSpacing: '0.04em', marginBottom: '6px' }}>PREVIOUS CODES</div>
            {history.map((h, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: '7px', fontSize: '12px', padding: '4px 0', borderTop: i ? '1px solid #F1F5F9' : 'none' }}>
                <span style={{ fontFamily: 'monospace', color: '#0F172A', fontWeight: 600 }}>{h.from}</span>
                <span style={{ color: '#CBD5E1' }}>→</span>
                <span style={{ fontFamily: 'monospace', color: '#64748B' }}>{h.to}</span>
                <span style={{ marginLeft: 'auto', color: '#94A3B8', fontSize: '11px', whiteSpace: 'nowrap' }}>{new Date(h.at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' })}{h.by ? ` · ${h.by}` : ''}</span>
              </div>
            ))}
          </div>
        )}
        <div style={{ marginTop: '14px', padding: '9px 11px', background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '8px', fontSize: '11.5px', color: '#92400E', lineHeight: 1.5 }}>
          Must be unique. The onboarding history follows the rename; external systems (Google Sheet, Shopify, POS) keep the old code until re-synced.
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '20px' }}>
          <button onClick={onClose} style={{ padding: '9px 16px', fontSize: '13px', fontWeight: 600, color: '#64748B', background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', cursor: 'pointer' }}>Cancel</button>
          <button onClick={save} disabled={busy} style={{ padding: '9px 18px', fontSize: '13px', fontWeight: 600, color: 'white', background: '#6366F1', border: 'none', borderRadius: '8px', cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.7 : 1 }}>{busy ? 'Saving…' : 'Change SKU'}</button>
        </div>
      </div>
    </div>
  )
}

function EditSkuModal({ product, onSaved, onClose }: { product: Product; onSaved: (p: Product) => void; onClose: () => void }) {
  const sensitive = can('product_sensitive')
  const [f, setF] = useState({
    name:  product.name ?? '',
    brand: product.brand ?? '',
    category: product.category ?? '',
    status:   product.status ?? 'ACTIVE',
    hero_sku: !!product.hero_sku,
    uom:       product.uom ?? '',
    pack_unit: product.pack_unit ?? '',
    min_purchase_qty: product.min_purchase_qty != null ? String(product.min_purchase_qty) : '',
    min_sellable_qty: product.min_sellable_qty != null ? String(product.min_sellable_qty) : '',
    subcategory: product.subcategory ?? '',
    species: product.species ?? '',
    rrp: product.rrp != null ? String(product.rrp) : '',
    storage_rule: product.storage_rule ?? 'any',
    weight: product.weight_g != null ? String(gToUnit(product.weight_g, product.weight_unit)) : '',
    weight_unit: product.weight_unit ?? 'kg',
    notes: product.notes ?? '',
    mark_verified: true,
  })
  const [cats, setCats] = useState<string[]>([])
  const [opts, setOpts] = useState<{ brands: string[]; subcategories: string[]; uoms: string[]; pack_units: string[] }>({ brands: [], subcategories: [], uoms: [], pack_units: [] })
  const [saving, setSaving] = useState(false)
  useEffect(() => {
    fetch(`${API}/category-rules`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : [])
      .then((d: unknown) => setCats((Array.isArray(d) ? d : []).map((c: { category?: string }) => c.category ?? '').filter(Boolean)))
      .catch(() => {})
    fetch(`${API}/products/field-options`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setOpts({ brands: d.brands ?? [], subcategories: d.subcategories ?? [], uoms: d.uoms ?? [], pack_units: d.pack_units ?? [] }) })
      .catch(() => {})
  }, [])

  async function save() {
    setSaving(true)
    try {
      const body: Record<string, unknown> = {
        brand: f.brand.trim(), uom: f.uom.trim(), pack_unit: f.pack_unit.trim(), notes: f.notes.trim(),
      }
      if (f.min_purchase_qty.trim() !== '') body.min_purchase_qty = parseInt(f.min_purchase_qty, 10)
      if (f.min_sellable_qty.trim() !== '') body.min_sellable_qty = parseInt(f.min_sellable_qty, 10)
      body.subcategory = f.subcategory.trim(); body.species = f.species; body.storage_rule = f.storage_rule
      if (f.rrp.trim() !== '') body.rrp = parseFloat(f.rrp)
      if (f.weight.trim() !== '') { body.weight_g = unitToG(parseFloat(f.weight), f.weight_unit); body.weight_unit = f.weight_unit }
      if (sensitive) { body.name = f.name.trim(); body.category = f.category; body.status = f.status; body.hero_sku = f.hero_sku }
      body.mark_verified = f.mark_verified
      const r = await fetch(`${API}/products/${skuToPath(product.sku_code)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body),
      })
      if (r.ok) { onSaved(await r.json()); toast.success('SKU updated'); onClose() }
      else toast.error((await r.json().catch(() => ({}))).detail ?? 'Could not save')
    } catch { toast.error('Could not save') }
    finally { setSaving(false) }
  }

  const inp: React.CSSProperties = { border: '1px solid #E2E8F0', borderRadius: '7px', padding: '8px 10px', fontSize: '13px', background: 'white', width: '100%', boxSizing: 'border-box' }
  const inpDis: React.CSSProperties = { ...inp, background: '#F8FAFC', color: '#94A3B8', cursor: 'not-allowed' }
  const lbl: React.CSSProperties = { fontSize: '11px', fontWeight: 600, color: '#64748B', display: 'block', marginBottom: '4px' }
  const field = (label: string, node: React.ReactNode) => <label style={{ display: 'block' }}><span style={lbl}>{label}</span>{node}</label>
  const hlp: React.CSSProperties = { display: 'block', fontSize: '10.5px', color: '#94A3B8', marginTop: '3px', lineHeight: 1.35 }

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '56px 20px', overflowY: 'auto' }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'white', borderRadius: '14px', width: '560px', maxWidth: '100%', padding: '22px', boxShadow: '0 20px 50px rgba(0,0,0,0.25)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '17px', fontWeight: 700, color: '#0F172A' }}>Edit SKU details</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: '22px', color: '#94A3B8', cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
        <p style={{ fontSize: '12px', color: '#94A3B8', margin: '2px 0 16px' }}>{product.sku_code}</p>
        <datalist id="opt-brands">{opts.brands.map(o => <option key={o} value={o} />)}</datalist>
        <datalist id="opt-subcategories">{opts.subcategories.map(o => <option key={o} value={o} />)}</datalist>
        <datalist id="opt-uoms">{opts.uoms.map(o => <option key={o} value={o} />)}</datalist>
        <datalist id="opt-pack-units">{opts.pack_units.map(o => <option key={o} value={o} />)}</datalist>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
          <div style={{ gridColumn: '1 / -1' }}>{field('Name', <input style={sensitive ? inp : inpDis} value={f.name} disabled={!sensitive} onChange={e => setF({ ...f, name: e.target.value })} />)}</div>
          {field('Brand', <input list="opt-brands" style={inp} value={f.brand} onChange={e => setF({ ...f, brand: e.target.value })} />)}
          {field('Category', (
            <select style={sensitive ? inp : inpDis} value={f.category} disabled={!sensitive} onChange={e => setF({ ...f, category: e.target.value })}>
              {f.category && !cats.includes(f.category) && <option value={f.category}>{f.category}</option>}
              {cats.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          ))}
          {field('Status', (
            <select style={sensitive ? inp : inpDis} value={f.status} disabled={!sensitive} onChange={e => setF({ ...f, status: e.target.value as Product['status'] })}>
              {['ACTIVE', 'INACTIVE', 'DISCONTINUED'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          ))}
          {field('Sell UOM (e.g. tablet, ml)', <input list="opt-uoms" style={inp} value={f.uom} onChange={e => setF({ ...f, uom: e.target.value })} />)}
          {field('Pack unit (e.g. box, bottle)', <input list="opt-pack-units" style={inp} value={f.pack_unit} onChange={e => setF({ ...f, pack_unit: e.target.value })} />)}
          {field('Default product purchase minimum', <>
            <input type="number" style={inp} value={f.min_purchase_qty} onChange={e => setF({ ...f, min_purchase_qty: e.target.value })} />
            <span style={hlp}>Product-level fallback only. A supplier&rsquo;s real MOQ / order multiple is set under <b>Supplier ordering</b> in Manage suppliers.</span>
          </>)}
          {field('Min sellable qty', <>
            <input type="number" style={inp} value={f.min_sellable_qty} onChange={e => setF({ ...f, min_sellable_qty: e.target.value })} />
            <span style={hlp}>Smallest quantity a customer can buy (e.g. sold in min. 24 cans). Not a cost divisor.</span>
          </>)}
          {field('Subcategory', <input list="opt-subcategories" style={inp} value={f.subcategory} onChange={e => setF({ ...f, subcategory: e.target.value })} placeholder="e.g. antibiotic" />)}
          {field('Species', (
            <select style={inp} value={f.species} onChange={e => setF({ ...f, species: e.target.value })}>
              <option value="">—</option>
              {['dog', 'cat', 'both', 'other'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          ))}
          {field('RRP (HK$)', <input type="number" style={inp} value={f.rrp} onChange={e => setF({ ...f, rrp: e.target.value })} />)}
          {field('Storage rule', (
            <select style={inp} value={f.storage_rule} onChange={e => setF({ ...f, storage_rule: e.target.value as 'any' | 'clinic_only' })}>
              <option value="any">any (clinic + warehouse)</option>
              <option value="clinic_only">clinic_only</option>
            </select>
          ))}
          {field('Weight', (
            <div style={{ display: 'flex', gap: '6px' }}>
              <input type="number" style={{ ...inp, flex: 1 }} value={f.weight} onChange={e => setF({ ...f, weight: e.target.value })} />
              <select style={{ ...inp, width: '64px' }} value={f.weight_unit} onChange={e => setF({ ...f, weight_unit: e.target.value })}>
                <option value="kg">kg</option>
                <option value="lb">lb</option>
              </select>
            </div>
          ))}
          <div style={{ gridColumn: '1 / -1' }}>{field('Notes', <textarea style={{ ...inp, minHeight: '58px', resize: 'vertical' }} value={f.notes} onChange={e => setF({ ...f, notes: e.target.value })} />)}</div>
          <label style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: '8px', cursor: sensitive ? 'pointer' : 'not-allowed' }}>
            <input type="checkbox" checked={f.hero_sku} disabled={!sensitive} onChange={e => setF({ ...f, hero_sku: e.target.checked })} />
            <span style={{ fontSize: '13px', color: '#0F172A' }}>Hero SKU</span>
          </label>
          <label style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', padding: '8px 10px', background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: '8px' }}>
            <input type="checkbox" checked={f.mark_verified} onChange={e => setF({ ...f, mark_verified: e.target.checked })} />
            <span style={{ fontSize: '13px', fontWeight: 600, color: '#166534' }}>Mark as HITL&#8209;Verified on save</span>
          </label>
          <p style={{ gridColumn: '1 / -1', fontSize: '11px', color: '#94A3B8', margin: 0 }}>These are product-level fields. Per-supplier cost, cost-basis units, ordering terms (MOQ / order multiple), bulk-buy tiers and stock live under <b>Manage suppliers</b>.</p>
        </div>
        {!sensitive && <p style={{ fontSize: '11px', color: '#94A3B8', marginTop: '12px' }}>Name, category, status &amp; hero are locked for your role.</p>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '20px' }}>
          <button onClick={onClose} style={{ padding: '9px 16px', fontSize: '13px', fontWeight: 600, color: '#64748B', background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', cursor: 'pointer' }}>Cancel</button>
          <button onClick={save} disabled={saving} style={{ padding: '9px 18px', fontSize: '13px', fontWeight: 600, color: 'white', background: '#6366F1', border: 'none', borderRadius: '8px', cursor: saving ? 'default' : 'pointer', opacity: saving ? 0.7 : 1 }}>{saving ? 'Saving…' : 'Save changes'}</button>
        </div>
      </div>
    </div>
  )
}

// Cost, units & Max-Bulk-Buy — landed unit economics + extra costs + relational MBB terms.
// Restyled to the SKU-detail card system; all editing logic is unchanged.
function CostMbbEditor({ product, onSaved }: { product: Product; onSaved: (p: Product) => void }) {
  const editable = can('product_edit')
  const [busy, setBusy] = useState(false)
  const [nt, setNt] = useState({ kind: 'buy_x_get_y', min_qty: '', free_qty: '', min_spend: '', discount_pct: '', unit_cost: '', note: '' })
  const uomLabel = product.uom ?? 'unit'

  async function api(method: string, path: string, body?: unknown): Promise<boolean> {
    setBusy(true)
    try {
      const r = await fetch(`${API}/products/${skuToPath(product.sku_code)}${path}`, {
        method, headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: body ? JSON.stringify(body) : undefined,
      })
      if (r.ok) { onSaved(await r.json()); return true }
      toast.error((await r.json().catch(() => ({}))).detail ?? 'Request failed'); return false
    } finally { setBusy(false) }
  }

  async function addTerm(psId: number) {
    const body: Record<string, unknown> = { kind: nt.kind, note: nt.note || undefined }
    if (nt.kind === 'buy_x_get_y') { body.min_qty = int(nt.min_qty); body.free_qty = int(nt.free_qty) }
    else if (nt.kind === 'spend_discount') { body.min_spend = num(nt.min_spend); const pc = num(nt.discount_pct); body.discount_pct = pc != null ? pc / 100 : null }
    else { body.unit_cost = num(nt.unit_cost); body.min_qty = int(nt.min_qty) }
    if (await api('POST', `/suppliers/${psId}/mbb-terms`, body)) {
      toast.success('MBB term added'); setNt({ kind: 'buy_x_get_y', min_qty: '', free_qty: '', min_spend: '', discount_pct: '', unit_cost: '', note: '' })
    }
  }
  async function delTerm(psId: number, tid: number) {
    if (await api('DELETE', `/suppliers/${psId}/mbb-terms/${tid}`)) toast.success('MBB term removed')
  }

  const money = (n: number | null | undefined) => n != null ? `HK$${n >= 1 ? n.toFixed(2) : n.toFixed(3)}` : '—'
  const sup = product.all_suppliers.find(s => s.is_primary) ?? product.all_suppliers[0]

  return (
    <div className="card">
      <div className="ch"><div className="ct">Cost, units &amp; Max-Bulk-Buy{!editable && <span style={{ fontWeight: 500, color: '#8A93A2' }}> · view only</span>}</div><div className="hint">landed unit economics</div></div>
      <div className="cb">

        {/* Unit cost + pack size. Channel charges (HKTV fee / Shopify logistics) are applied
            per channel in the Margin worksheet, not folded into the cost here. */}
        <div className="costrow">
          <span className="pill land">Unit cost {money(product.unit_cost)} / {uomLabel}{(product.units_per_pack ?? 1) > 1 && product.primary_cost != null ? ` · whole ${money(product.primary_cost)}/${product.pack_unit ?? 'pack'}` : ''}</span>
          {product.units_per_pack && product.units_per_pack > 1 && (
            <span style={{ color: product.uom_verified_at ? '#15803D' : '#B45309', fontSize: '11.5px' }}>
              · {product.uom_verified_at ? '✓ ' : ''}{product.units_per_pack} {uomLabel} / {product.pack_unit ?? 'pack'}
            </span>
          )}
        </div>

        {/* MBB terms */}
        <div className="subh" style={{ marginTop: '16px' }}>Max-Bulk-Buy terms</div>
        {!sup && <div style={{ fontSize: '12px', color: '#8A93A2' }}>Link a supplier first to add MBB terms.</div>}
        {sup && <>
          {product.all_suppliers.length > 1 && <p style={{ fontSize: '11px', color: '#8A93A2', marginBottom: '6px' }}>Terms for <b style={{ color: '#0F172A' }}>{sup.name ?? 'primary supplier'}</b> (other suppliers: use Manage in the Suppliers card)</p>}
          {(sup.mbb_term_list ?? []).length === 0 && <div style={{ fontSize: '12px', color: '#8A93A2', marginBottom: '4px' }}>No MBB terms.</div>}
          {(sup.mbb_term_list ?? []).map(t => (
            <div className="term" key={t.id}>
              <div className="tl">
                <b>{
                  t.kind === 'buy_x_get_y' ? `Buy ${t.min_qty ?? '?'} get ${t.free_qty ?? '?'} free`
                  : t.kind === 'spend_discount' ? `Spend $${t.min_spend ?? '?'}`
                  : `${t.min_qty ? `${t.min_qty}+ ${uomLabel}` : 'Flat'}`
                }</b>{' '}
                <span>· {t.kind === 'spend_discount' ? `${t.discount_pct != null ? (t.discount_pct * 100).toFixed(0) : '?'}% off` : t.kind === 'buy_x_get_y' ? 'free goods' : t.kind === 'tier' ? 'bulk tier' : 'flat cost'}</span>
                {t.note && <span> · {t.note.length > 28 ? t.note.slice(0, 28) + '…' : t.note}</span>}
              </div>
              <div className="tr">
                {t.effective_unit_cost != null ? `${money(t.effective_unit_cost)} / ${uomLabel}` : '—'}
                {editable && <button onClick={() => delTerm(sup.id, t.id)} disabled={busy} title="Remove" style={{ marginLeft: '10px', background: 'none', border: 'none', color: '#C0362C', cursor: 'pointer', fontSize: '15px', lineHeight: 1 }}>×</button>}
              </div>
            </div>
          ))}
          {editable && (
            <div className="miniform">
              <select value={nt.kind} onChange={e => setNt({ ...nt, kind: e.target.value })}>
                <option value="buy_x_get_y">Buy X get Y free</option>
                <option value="spend_discount">Spend $ → % off</option>
                <option value="tier">Tier (cost/unit @ qty)</option>
                <option value="flat_unit_cost">Flat cost/unit</option>
              </select>
              {nt.kind === 'buy_x_get_y' && <>
                <input style={{ width: '80px' }} value={nt.min_qty} onChange={e => setNt({ ...nt, min_qty: e.target.value })} placeholder="Buy" />
                <input style={{ width: '80px' }} value={nt.free_qty} onChange={e => setNt({ ...nt, free_qty: e.target.value })} placeholder="Get free" />
              </>}
              {nt.kind === 'spend_discount' && <>
                <input style={{ width: '100px' }} value={nt.min_spend} onChange={e => setNt({ ...nt, min_spend: e.target.value })} placeholder="Min spend $" />
                <input style={{ width: '90px' }} value={nt.discount_pct} onChange={e => setNt({ ...nt, discount_pct: e.target.value })} placeholder="Discount %" />
              </>}
              {(nt.kind === 'tier' || nt.kind === 'flat_unit_cost') && <>
                <input style={{ width: '100px' }} value={nt.unit_cost} onChange={e => setNt({ ...nt, unit_cost: e.target.value })} placeholder="Cost / unit" />
                <input style={{ width: '90px' }} value={nt.min_qty} onChange={e => setNt({ ...nt, min_qty: e.target.value })} placeholder="Min units" />
              </>}
              <input style={{ flex: 1, minWidth: '120px' }} value={nt.note} onChange={e => setNt({ ...nt, note: e.target.value })} placeholder="Note (optional)" />
              <button className="btn pri" onClick={() => addTerm(sup.id)} disabled={busy}>Add term</button>
            </div>
          )}
        </>}
        {product.mbb_unit_cost != null && (
          <div className="best" style={{ marginTop: '12px' }}>
            Best achievable MBB cost / {uomLabel}: <b>{money(product.mbb_unit_cost)}</b>
          </div>
        )}
      </div>
    </div>
  )
}

const SKUD_CSS = `
.skud{--card:#FFFFFF;--panel:#FAFBFC;--line:#E7EAEF;--line2:#F1F3F6;--ink:#0F172A;--ink2:#334155;--muted:#5B6472;--faint:#8A93A2;--ghost:#C2C8D2;--accent:#4F46E5;--accent-ink:#3730A3;--accent-soft:#EEF0FE;--accent-line:#D5D8F7;--good:#15803D;--good-soft:#EAF6EE;--amber:#B45309;--amber-soft:#FCF3E6;--red:#C0362C;--red-soft:#FBEBEA;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13px;line-height:1.45;color:var(--ink)}
.skud *{box-sizing:border-box}
.skud .btn{font-family:inherit;font-size:12.5px;font-weight:600;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink2);cursor:pointer;transition:border-color .12s,background .12s;display:inline-flex;align-items:center;gap:7px;text-decoration:none}
.skud .btn:hover{border-color:var(--ghost);background:var(--panel)}
.skud .btn.pri{color:#fff;background:var(--accent);border-color:var(--accent)}
.skud .btn.pri:hover{background:var(--accent-ink)}
.skud .btn.danger{color:var(--red);border-color:#EAB4AF}
.skud .btn.danger:hover{background:var(--red-soft)}
.skud .hdr{display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap;align-items:flex-start;margin-bottom:16px}
.skud .eyebrow{display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--muted);font-weight:600;margin-bottom:7px}
.skud .eyebrow .cd{width:8px;height:8px;border-radius:50%}
.skud h1{font-size:23px;font-weight:680;letter-spacing:-0.015em;margin:0 0 8px;line-height:1.2;color:var(--ink)}
.skud .idline{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:12.5px;color:var(--muted)}
.skud .idline .sep{color:var(--ghost)}
.skud .skucode{font-family:var(--mono);color:var(--ink2)}
.skud .lnk{color:var(--accent);cursor:pointer;font-weight:600}
.skud .idgrid{display:flex;gap:30px;flex-wrap:wrap;align-items:flex-start}
.skud .idlabel{font-size:10px;font-weight:700;color:var(--faint);text-transform:uppercase;letter-spacing:.05em;margin-bottom:5px}
.skud .idrow{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.skud .idrow .skucode{font-size:14px}
.skud .idrow .lnk{font-size:11.5px}
.skud .supsku{font-family:var(--mono);font-size:14px;font-weight:600;color:var(--accent-ink);background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:7px;padding:5px 10px;cursor:pointer;display:inline-flex;align-items:center;gap:8px;line-height:1;transition:border-color .12s,background .12s}
.skud .supsku:hover{border-color:var(--accent)}
.skud .supsku .cpi{color:var(--accent);opacity:.65}
.skud .supsku.copied{color:var(--good);background:var(--good-soft);border-color:#CDE8D6}
.skud .supsku .cf{font-family:inherit;font-size:11px;font-weight:700}
.skud .idsup{font-size:12.5px;color:var(--muted)}
.skud .idnone{font-size:13px;color:var(--faint);font-style:italic}
.skud .cmp{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:11px 17px;border-bottom:1px solid var(--line2)}
.skud .cmp-nm{font-size:13px;font-weight:600;color:var(--accent);text-decoration:none}
.skud .cmp-nm:hover{text-decoration:underline}
.skud .cmp-meta{font-size:11px;color:var(--faint);margin-top:2px}
.skud .cmp-price{display:flex;align-items:center;gap:9px;flex-shrink:0}
.skud .cmp-p{font-size:14px;font-weight:700;color:var(--ink2);font-variant-numeric:tabular-nums}
.skud .cmp-p.cheap{color:var(--good)}
.skud .cmp-x{border:none;background:none;color:var(--ghost);cursor:pointer;font-size:16px;line-height:1;padding:2px 5px;border-radius:5px}
.skud .cmp-x:hover{color:var(--red);background:var(--red-soft)}
.skud .cmpe{padding:15px 17px;font-size:12px;color:var(--faint)}
.skud .cmp-add{display:flex;gap:8px;padding:12px 17px;border-top:1px solid var(--line2);background:var(--panel)}
.skud .cmp-in{font-family:inherit;font-size:12.5px;padding:7px 10px;border:1px solid var(--line);border-radius:7px;background:var(--card);color:var(--ink);outline:none}
.skud .cmp-in.url{flex:1;min-width:0}
.skud .cmp-in.nm{width:150px}
.skud .cmp-in:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.skud .cmpwrap{display:flex;align-items:stretch}
.skud .cmptabs{display:flex;flex-direction:column;flex-shrink:0;width:180px;border-right:1px solid var(--line);max-height:360px;overflow-y:auto}
.skud .cmptab{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:9px 13px;border:none;border-left:2px solid transparent;border-bottom:1px solid var(--line2);background:none;cursor:pointer;text-align:left;font-family:inherit}
.skud .cmptab:hover{background:var(--panel)}
.skud .cmptab.on{background:var(--accent-soft);border-left-color:var(--accent)}
.skud .cmptab .tnm{font-size:12px;font-weight:600;color:var(--ink2);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.skud .cmptab.on .tnm{color:var(--accent-ink)}
.skud .cmptab .tpr{font-size:12px;font-weight:700;color:var(--muted);font-variant-numeric:tabular-nums;flex-shrink:0}
.skud .cmptab .tpr.cheap{color:var(--good)}
.skud .cmppanel{flex:1;min-width:0;padding:13px 17px}
.skud .cpp-h{display:flex;align-items:center;justify-content:space-between;gap:8px}
.skud .cpp-lead{font-size:12.5px;color:var(--ink2);margin:11px 0 7px}
.skud .cpptab{width:100%;border-collapse:collapse}
.skud .cpptab th{text-align:right;font-size:10px;font-weight:650;color:var(--faint);text-transform:uppercase;letter-spacing:.02em;padding:3px 8px}
.skud .cpptab th:first-child{text-align:left}
.skud .cpptab td{padding:6px 8px;border-top:1px solid var(--line2);font-size:12.5px;text-align:right;font-variant-numeric:tabular-nums}
.skud .cpptab td.cpp-row{text-align:left;color:var(--ink2);font-weight:600}
.skud .cpptab .cpp-sub{font-weight:400;color:var(--faint);font-size:11px}
.skud .badges{display:flex;gap:7px;flex-wrap:wrap;margin-top:12px}
.skud .bdg{font-size:11px;font-weight:650;padding:3px 10px;border-radius:6px;display:inline-flex;align-items:center;gap:5px;border:1px solid}
.skud .bdg.ok{background:var(--good-soft);color:var(--good);border-color:#CDE8D6}
.skud .bdg.neu{background:var(--line2);color:var(--muted);border-color:var(--line)}
.skud .bdg.acc{background:var(--accent-soft);color:var(--accent-ink);border-color:var(--accent-line)}
.skud .bdg.warn{background:var(--amber-soft);color:var(--amber);border-color:#F3E0BE}
.skud .bdg .st{width:7px;height:7px;border-radius:50%;background:currentColor}
.skud .hdr-act{display:flex;gap:8px;flex-wrap:wrap}
.skud .alert{display:flex;align-items:center;gap:11px;padding:11px 15px;border-radius:10px;margin-bottom:14px;font-size:13px}
.skud .alert.warn{background:var(--amber-soft);border:1px solid #F3E0BE;color:#7A4A12}
.skud .alert.red{background:var(--red-soft);border:1px solid #F1CDC9;color:#7A2A24}
.skud .alert .ad{width:9px;height:9px;border-radius:50%;background:var(--amber);flex:none}
.skud .alert.red .ad{background:var(--red)}
.skud .alert b{color:var(--amber)}
.skud .alert.red b{color:var(--red)}
.skud .metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:12px;margin-bottom:22px}
.skud .metric{background:var(--card);border:1px solid var(--line);border-radius:11px;padding:13px 15px}
.skud .metric .ml{font-size:11px;color:var(--muted);font-weight:600;margin-bottom:7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.skud .metric .mv{font-size:19px;font-weight:680;letter-spacing:-0.01em;font-variant-numeric:tabular-nums}
.skud .metric .ms{font-size:11px;color:var(--faint);margin-top:4px}
.skud .metric .ms.good{color:var(--good)}
.skud .metric .ms.amber{color:var(--amber)}
@media(max-width:1080px){.skud .metrics{grid-template-columns:repeat(3,1fr)}}
.skud .grid{display:grid;grid-template-columns:1.62fr 1fr;gap:16px;align-items:start}
@media(max-width:980px){.skud .grid{grid-template-columns:1fr}}
.skud .col{display:flex;flex-direction:column;gap:16px;min-width:0}
.skud .card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
.skud .ch{padding:13px 17px;border-bottom:1px solid var(--line2);display:flex;align-items:center;justify-content:space-between;gap:10px}
.skud .ct{font-size:13px;font-weight:650}
.skud .ch .hint{font-size:11px;color:var(--faint)}
.skud .cb{padding:15px 17px}
.skud .cb.flush{padding:0}
.skud table{border-collapse:collapse;width:100%}
.skud .mtab th{text-align:right;font-size:10px;font-weight:650;color:#4A5462;text-transform:uppercase;letter-spacing:.02em;padding:9px 12px;border-bottom:1px solid var(--line2);white-space:nowrap}
.skud .mtab th:first-child{text-align:left}
.skud .mtab td{padding:11px 12px;border-bottom:1px solid var(--line2);font-size:12.5px;text-align:right;font-variant-numeric:tabular-nums}
.skud .mtab td:first-child{text-align:left}
.skud .mtab tr:last-child td{border-bottom:none}
.skud .cstat{display:inline-flex;align-items:center;gap:6px;font-weight:600;font-size:11px}
.skud .cstat .d{width:7px;height:7px;border-radius:50%}
.skud .cstat.on{color:var(--good)}
.skud .cstat.on .d{background:var(--good)}
.skud .cstat.off{color:var(--faint)}
.skud .cstat.off .d{background:var(--ghost)}
.skud .gpv{font-weight:700}
.skud .gpv.good{color:var(--good)}
.skud .gpv.warn{color:var(--amber)}
.skud .gpv.bad{color:var(--red)}
.skud .soldas{font-size:10.5px;color:var(--faint);margin-top:2px;text-align:left}
.skud .mbbline{padding:10px 17px;background:var(--accent-soft);border-bottom:1px solid var(--line2);font-size:12px;color:var(--accent-ink);font-weight:600;display:flex;justify-content:space-between}
.skud .legend{padding:11px 17px;font-size:11px;color:var(--muted);line-height:1.55;border-top:1px solid var(--line2);background:var(--panel)}
.skud .legend b{color:var(--ink2)}
.skud .kv{display:flex;justify-content:space-between;gap:14px;padding:9px 0;border-bottom:1px solid var(--line2);font-size:12.5px}
.skud .kv:last-child{border-bottom:none}
.skud .kv .k{color:var(--muted)}
.skud .kv .v{color:var(--ink);font-weight:600;text-align:right}
.skud .kv .v.acc{color:var(--accent-ink)}
.skud .stockrow{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin-bottom:15px}
.skud .stbox{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px;text-align:center}
.skud .stbox .n{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums}
.skud .stbox .l{font-size:11px;color:var(--muted);margin-top:3px}
.skud .spark{display:flex;align-items:flex-end;gap:8px;height:74px;padding:6px 0 2px}
.skud .spark .bcol{flex:1;display:flex;flex-direction:column;align-items:center;gap:6px}
.skud .spark .bar{width:100%;background:var(--accent-soft);border-radius:4px 4px 0 0;position:relative;min-height:6px}
.skud .spark .bar b{position:absolute;inset:0;background:var(--accent);border-radius:4px 4px 0 0;opacity:.9}
.skud .spark .bl{font-size:10.5px;color:var(--faint)}
.skud .wocbar{height:8px;border-radius:5px;background:var(--line2);overflow:hidden;margin:6px 0 4px}
.skud .wocbar b{display:block;height:100%;border-radius:5px}
.skud .sim{margin-top:15px;padding:14px;border:1px dashed var(--accent-line);border-radius:10px;background:var(--accent-soft)}
.skud .sim .sl{font-size:11px;font-weight:700;color:var(--accent-ink);text-transform:uppercase;letter-spacing:.04em;margin-bottom:9px}
.skud .simrow{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.skud .simrow input{width:84px;padding:7px 10px;border:1px solid var(--line);border-radius:8px;font-family:inherit;font-size:13px;outline:none}
.skud .simrow input:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(79,70,229,.14)}
.skud .chip{font-size:11.5px;font-weight:600;color:var(--ink2);background:#fff;border:1px solid var(--line);border-radius:99px;padding:6px 11px;cursor:pointer}
.skud .chip:hover{border-color:var(--accent-line);color:var(--accent-ink)}
.skud .simout{margin-top:11px;font-size:13px;color:var(--ink2)}
.skud .simout b{color:var(--good)}
.skud .costrow{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:13px;margin-bottom:14px}
.skud .costrow .pill{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:6px 11px;font-weight:600}
.skud .costrow .pill.land{background:var(--accent-soft);color:var(--accent-ink);border-color:var(--accent-line)}
.skud .costrow .op{color:var(--faint);font-weight:700}
.skud .subh{font-size:10.5px;font-weight:700;color:var(--faint);text-transform:uppercase;letter-spacing:.05em;margin:6px 0 8px}
.skud .miniform{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:8px}
.skud .miniform input,.skud .miniform select{font-family:inherit;font-size:12px;padding:6px 9px;border:1px solid var(--line);border-radius:7px;outline:none;background:#fff}
.skud .term{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:9px 11px;border:1px solid var(--line);border-radius:9px;margin-bottom:7px;font-size:12.5px}
.skud .term .tl b{color:var(--ink)}
.skud .term .tl span{color:var(--muted)}
.skud .term .tr{font-weight:700;font-variant-numeric:tabular-nums;display:flex;align-items:center}
.skud .best{font-size:12px;color:var(--ink2);margin-top:4px}
.skud .best b{color:var(--accent-ink)}
.skud .sup{padding:15px 17px;border-bottom:1px solid var(--line2)}
.skud .sup:last-child{border-bottom:none}
.skud .sup-h{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}
.skud .sup-nm{font-size:14px;font-weight:650;display:flex;align-items:center;gap:8px}
.skud .prefflag{font-size:9.5px;font-weight:700;color:var(--good);background:var(--good-soft);padding:1px 6px;border-radius:4px}
.skud .sup-meta{font-family:var(--mono);font-size:11px;color:var(--muted);margin-top:3px}
.skud .sup-cost{text-align:right;flex:none}
.skud .sup-cost .c{font-size:16px;font-weight:700;font-variant-numeric:tabular-nums}
.skud .sup-cost .u{font-size:10.5px;color:var(--faint)}
.skud .sstat{display:inline-flex;align-items:center;gap:5px;font-size:11px;font-weight:700;padding:2px 8px;border-radius:5px;margin-top:8px}
.skud .sstat.ok{background:var(--good-soft);color:var(--good)}
.skud .sstat.oos{background:var(--red-soft);color:var(--red)}
.skud .sstat .d{width:6px;height:6px;border-radius:50%;background:currentColor}
.skud .oosdetail{margin-top:11px;padding:11px 13px;background:var(--red-soft);border:1px solid #F1CDC9;border-radius:9px}
.skud .oosdetail .kv{border-color:#F1CDC9;padding:6px 0}
.skud .oosdetail .kv .k{color:#9A3B33}
.skud .oosdetail .kv .v{color:#7A2A24}
.skud .oosdetail summary{margin-top:8px;font-size:11.5px;font-weight:650;color:var(--accent);cursor:pointer}
.skud .histrow{display:flex;justify-content:space-between;padding:6px 0;border-top:1px solid #F1CDC9;font-size:11.5px}
.skud .histrow .hd{color:#7A2A24}
.skud .histrow .hv{font-weight:700}
.skud .linkbtn{font-family:inherit;font-size:11.5px;font-weight:650;color:var(--accent);background:none;border:none;cursor:pointer;padding:0;text-decoration:underline;text-underline-offset:2px}
.skud .plat{font-size:9px;font-weight:800;padding:2px 6px;border-radius:4px;border:1px solid;margin-left:4px}
.skud .plat.on{background:var(--good-soft);color:var(--good);border-color:#CDE8D6}
.skud .plat.off{background:var(--line2);color:var(--faint);border-color:var(--line)}
.skud .tagchip{display:inline-block;font-size:11px;color:var(--ink2);background:var(--line2);border:1px solid var(--line);border-radius:99px;padding:3px 10px;margin:0 5px 5px 0}
.skud .hitl{margin-top:12px;padding:11px 13px;background:var(--amber-soft);border:1px solid #F3E0BE;border-radius:9px;font-size:11.5px;color:#7A4A12}
.skud .hitl .hr{display:flex;gap:8px;margin-top:9px}
.skud .hitl input{flex:1;font-family:inherit;font-size:12px;padding:7px 9px;border:1px solid #E7CFA0;border-radius:7px;outline:none;min-width:0}
.skud .hitl button{font-family:inherit;font-size:12px;font-weight:650;color:#fff;background:var(--good);border:none;border-radius:7px;padding:7px 12px;cursor:pointer}
.skud .aud{display:flex;gap:11px;padding:10px 0;border-bottom:1px solid var(--line2)}
.skud .aud:last-child{border-bottom:none}
.skud .aud .ad{width:8px;height:8px;border-radius:50%;background:var(--ghost);margin-top:5px;flex:none}
.skud .aud .at{font-size:12.5px;color:var(--ink2)}
.skud .aud .at b{color:var(--ink);font-weight:650}
.skud .aud .aw{font-size:10.5px;color:var(--faint);margin-top:2px}
.skud .footbar{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-top:22px;padding-top:18px;border-top:1px solid var(--line)}
.skud .footbar .l,.skud .footbar .r{display:flex;gap:9px;flex-wrap:wrap}
`

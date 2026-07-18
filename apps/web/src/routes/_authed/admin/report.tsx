import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { authHeaders, can, ROLE_LABELS } from '@/lib/auth'
import { getUser } from '@/lib/auth'
import { API_BASE } from '@/lib/config'

const API = API_BASE

// ── palette ──────────────────────────────────────────────────────────────────
const C = {
  matched: '#22C55E', new_sku: '#6366F1', rejected: '#EF4444', pending: '#F59E0B',
  edit: '#8B5CF6', supplier_confirm: '#0891B2', confirm_match: '#22C55E',
  assign_new: '#6366F1', hitl_unverify: '#94A3B8', verified: '#16A34A', toverify: '#F59E0B',
}
const ACTION_LABEL: Record<string, string> = {
  confirm_match: 'Matched', assign_new: 'New SKU', reject: 'Rejected', edit: 'Edited',
  supplier_confirm: 'Supplier confirm', hitl_unverify: 'Unverified',
}

interface Report {
  generated_at: string
  range: { from: string | null; to: string | null; ranged: boolean }
  onboarding: {
    totals: { imports: number; extracted: number; matched: number; new_sku: number; rejected: number; pending: number; processed: number }
    left: { pending_review: number; to_verify: number; verified: number; active_products: number }
    by_action: Record<string, number>
    by_reviewer: { reviewer: string; matched: number; new_sku: number; rejected: number; total: number }[]
    by_supplier: { supplier: string; matched: number; new_sku: number; rejected: number; pending: number; total: number }[]
    by_import: { import_id: number; filename: string; supplier: string; imported_at: string; extracted: number; matched: number; new_sku: number; rejected: number; pending: number }[]
    timeline: { date: string; matched: number; new_sku: number; rejected: number }[]
  }
  usage: {
    by_user: { user: string; display: string; logins: number; failed: number; sessions: number; total_seconds: number; avg_seconds: number; last_login: string | null }[]
    recent_sessions: { user: string; display: string; login_at: string; logout_at: string; seconds: number }[]
    totals: { logins: number; failed_logins: number; total_seconds: number }
  }
}

const fmt = (n: number) => (n ?? 0).toLocaleString()
function dur(s: number): string {
  if (!s) return '—'
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
  return h ? `${h}h ${m}m` : (m ? `${m}m` : `${s}s`)
}
function when(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
}

// Build a multi-section CSV from the report and trigger a download.
function downloadCsv(r: Report) {
  const esc = (v: unknown) => { const s = String(v ?? ''); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s }
  const line = (...cells: unknown[]) => cells.map(esc).join(',')
  const o = r.onboarding, u = r.usage
  const rangeLabel = r.range.ranged ? `${r.range.from || '…'} to ${r.range.to || '…'}` : 'All time'
  const L: string[] = [
    'Catalogue Onboarding Report', line('Range', rangeLabel), line('Generated', r.generated_at), '',
    'SUMMARY (matched/new/rejected = activity in range; pending/to-verify = live now)', 'Metric,Value',
    line('Imports', o.totals.imports), line('Items extracted', o.totals.extracted),
    line('Matched', o.totals.matched), line('New SKU', o.totals.new_sku), line('Rejected', o.totals.rejected),
    line('Processed', o.totals.processed), line('Pending in queue (now)', o.left.pending_review),
    line('Active SKUs to verify (now)', o.left.to_verify), line('Verified SKUs (now)', o.left.verified), '',
    'BY REVIEWER', 'Reviewer,Matched,New SKU,Rejected,Total',
    ...o.by_reviewer.map(x => line(x.reviewer, x.matched, x.new_sku, x.rejected, x.total)), '',
    'BY SUPPLIER', 'Supplier,Matched,New SKU,Rejected,Pending,Total',
    ...o.by_supplier.map(x => line(x.supplier, x.matched, x.new_sku, x.rejected, x.pending, x.total)), '',
    'BY IMPORT', 'Import ID,File,Supplier,Imported at,Extracted,Matched,New SKU,Rejected,Pending',
    ...o.by_import.map(x => line(x.import_id, x.filename, x.supplier, x.imported_at, x.extracted, x.matched, x.new_sku, x.rejected, x.pending)), '',
    'PLATFORM USAGE — BY USER', 'User,Logins,Failed,Sessions,Total seconds,Avg seconds,Last login',
    ...u.by_user.map(x => line(x.display, x.logins, x.failed, x.sessions, x.total_seconds, x.avg_seconds, x.last_login || '')), '',
    'RECENT SESSIONS', 'User,Login,Logout,Seconds',
    ...u.recent_sessions.map(x => line(x.display, x.login_at, x.logout_at, x.seconds)),
  ]
  const blob = new Blob([L.join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `onboarding-report-${r.range.ranged ? `${r.range.from || 'start'}_${r.range.to || 'end'}` : 'all-time'}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

// ── charts (hand-rolled SVG, no dependency) ──────────────────────────────────
function Donut({ data, size = 168, thickness = 28, onPick }: { data: { label: string; value: number; color: string; key?: string }[]; size?: number; thickness?: number; onPick?: (key: string) => void }) {
  const total = data.reduce((s, d) => s + d.value, 0)
  const r = (size - thickness) / 2, circ = 2 * Math.PI * r
  let offset = 0
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '18px', flexWrap: 'wrap' }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <g transform={`rotate(-90 ${size / 2} ${size / 2})`}>
          <circle cx={size / 2} cy={size / 2} r={r} fill="none" stroke="#F1F5F9" strokeWidth={thickness} />
          {total > 0 && data.map((d, i) => {
            const dash = (d.value / total) * circ
            const el = <circle key={i} cx={size / 2} cy={size / 2} r={r} fill="none" stroke={d.color} strokeWidth={thickness} strokeDasharray={`${dash} ${circ - dash}`} strokeDashoffset={-offset} strokeLinecap="butt"
              style={{ cursor: onPick && d.key ? 'pointer' : 'default' }} onClick={() => onPick && d.key && onPick(d.key)} />
            offset += dash
            return el
          })}
        </g>
        <text x="50%" y="47%" textAnchor="middle" fontSize="24" fontWeight="700" fill="#0F172A">{fmt(total)}</text>
        <text x="50%" y="59%" textAnchor="middle" fontSize="10" fill="#94A3B8">total</text>
      </svg>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {data.map(d => {
          const clickable = !!(onPick && d.key)
          return (
            <div key={d.label} onClick={() => clickable && onPick!(d.key!)} className={clickable ? 'drill-row' : ''}
              style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '12.5px', cursor: clickable ? 'pointer' : 'default', padding: '2px 6px', borderRadius: '5px' }}>
              <span style={{ width: '11px', height: '11px', borderRadius: '3px', background: d.color, flexShrink: 0 }} />
              <span style={{ color: '#475569', minWidth: '90px' }}>{d.label}</span>
              <strong style={{ color: '#0F172A', fontVariantNumeric: 'tabular-nums' }}>{fmt(d.value)}</strong>
              <span style={{ color: '#94A3B8', fontSize: '11px' }}>{total ? `${Math.round(100 * d.value / total)}%` : '0%'}</span>
              {clickable && <span style={{ color: '#CBD5E1', fontSize: '11px' }}>›</span>}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function StackBars({ rows, keys, onRow }: { rows: { label: string; values: Record<string, number | string>; total: number }[]; keys: { key: string; color: string }[]; onRow?: (label: string) => void }) {
  const max = Math.max(1, ...rows.map(r => r.total))
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
      {rows.map(r => (
        <div key={r.label} onClick={() => onRow && onRow(r.label)} className={onRow ? 'drill-row' : ''}
          style={{ display: 'flex', alignItems: 'center', gap: '10px', cursor: onRow ? 'pointer' : 'default', padding: '2px 4px', borderRadius: '5px' }}>
          <span style={{ fontSize: '12px', color: '#334155', width: '150px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flexShrink: 0 }} title={r.label}>{r.label}</span>
          <div style={{ flex: 1, display: 'flex', height: '18px', borderRadius: '4px', overflow: 'hidden', background: '#F8FAFC' }}>
            {keys.map(k => {
              const v = Number(r.values[k.key]) || 0
              return v ? <div key={k.key} title={`${k.key}: ${v}`} style={{ width: `${100 * v / max}%`, background: k.color }} /> : null
            })}
          </div>
          <span style={{ fontSize: '12px', fontWeight: 700, color: '#0F172A', width: '44px', textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{fmt(r.total)}</span>
        </div>
      ))}
    </div>
  )
}

function TimelineBars({ data }: { data: { date: string; matched: number; new_sku: number; rejected: number }[] }) {
  const max = Math.max(1, ...data.map(d => d.matched + d.new_sku + d.rejected))
  const H = 150
  const segs: [('matched' | 'new_sku' | 'rejected'), string][] = [['matched', C.matched], ['new_sku', C.new_sku], ['rejected', C.rejected]]
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: data.length > 30 ? '2px' : '6px', height: `${H}px`, borderBottom: '1px solid #E2E8F0' }}>
        {data.map(d => {
          const total = d.matched + d.new_sku + d.rejected
          return (
            <div key={d.date} title={`${d.date}: ${total} decisions`} style={{ flex: 1, display: 'flex', flexDirection: 'column-reverse', height: `${(total / max) * H}px`, minWidth: '4px', borderRadius: '3px 3px 0 0', overflow: 'hidden' }}>
              {segs.map(([k, col]) => {
                const v = d[k] as number
                return v ? <div key={k} style={{ height: `${100 * v / total}%`, background: col }} /> : null
              })}
            </div>
          )
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', color: '#94A3B8', marginTop: '4px' }}>
        <span>{data[0]?.date}</span><span>{data[data.length - 1]?.date}</span>
      </div>
    </div>
  )
}

// ── building blocks ──────────────────────────────────────────────────────────
function Kpi({ label, value, sub, color, onClick }: { label: string; value: number | string; sub?: string; color?: string; onClick?: () => void }) {
  return (
    <div onClick={onClick} className={onClick ? 'drill-card' : ''}
      style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '14px 16px', minWidth: '120px', flex: 1, cursor: onClick ? 'pointer' : 'default', position: 'relative' }}>
      <p style={{ fontSize: '10px', fontWeight: 600, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>{label}</p>
      <p style={{ fontSize: '26px', fontWeight: 700, color: color ?? '#0F172A', lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>{typeof value === 'number' ? fmt(value) : value}</p>
      {sub && <p style={{ fontSize: '11px', color: '#94A3B8', marginTop: '4px' }}>{sub}</p>}
      {onClick && <span style={{ position: 'absolute', top: '12px', right: '12px', color: '#CBD5E1', fontSize: '13px' }}>›</span>}
    </div>
  )
}

interface DrillRow { label: string; sub: string | null; meta: string; sku: string | null; href: string | null }
function DrillPanel({ title, rows, loading, truncated, onClose }: { title: string; rows: DrillRow[]; loading: boolean; truncated: boolean; onClose: () => void }) {
  return (
    <div onClick={onClose} className="no-print" style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 60, display: 'flex', justifyContent: 'flex-end' }}>
      <div onClick={e => e.stopPropagation()} style={{ width: '480px', maxWidth: '94vw', height: '100%', background: 'white', boxShadow: '-8px 0 32px rgba(0,0,0,0.25)', display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '16px 18px', borderBottom: '1px solid #E2E8F0', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <h3 style={{ fontSize: '14px', fontWeight: 700, color: '#0F172A', margin: 0, textTransform: 'capitalize' }}>{title}</h3>
            <p style={{ fontSize: '11px', color: '#94A3B8', margin: '2px 0 0' }}>{loading ? 'Loading…' : `${rows.length}${truncated ? '+' : ''} item${rows.length === 1 ? '' : 's'}`}</p>
          </div>
          <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: '22px', color: '#94A3B8', cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
        <div style={{ flex: 1, overflowY: 'auto' }}>
          {loading && <p style={{ padding: '20px', color: '#94A3B8', fontSize: '13px' }}>Loading…</p>}
          {!loading && rows.length === 0 && <p style={{ padding: '20px', color: '#94A3B8', fontSize: '13px' }}>No items.</p>}
          {!loading && rows.map((row, i) => {
            const href = row.sku ? `/items/${encodeURIComponent(row.sku)}` : null
            const inner = (
              <>
                <div style={{ fontSize: '12.5px', fontWeight: 600, color: '#0F172A', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{row.label}</div>
                <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '2px' }}>
                  {row.sub && <span style={{ fontSize: '11px', color: '#64748B' }}>{row.sub}</span>}
                  {row.meta && <span style={{ fontSize: '11px', color: '#94A3B8' }}>{row.meta}</span>}
                </div>
              </>
            )
            return href
              ? <a key={i} href={href} style={{ display: 'block', padding: '9px 18px', borderBottom: '1px solid #F1F5F9', textDecoration: 'none' }} className="drill-row">{inner}<span style={{ float: 'right', color: '#6366F1', fontSize: '11px', fontFamily: 'monospace' }}>{row.sku} ›</span></a>
              : <div key={i} style={{ padding: '9px 18px', borderBottom: '1px solid #F1F5F9' }}>{inner}</div>
          })}
          {truncated && !loading && <p style={{ padding: '12px 18px', fontSize: '11px', color: '#94A3B8' }}>Showing the first {rows.length} — narrow the period to see fewer.</p>}
        </div>
      </div>
    </div>
  )
}
function Card({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '16px 18px' }}>
      <div style={{ marginBottom: '12px' }}>
        <h3 style={{ fontSize: '13px', fontWeight: 700, color: '#0F172A', margin: 0 }}>{title}</h3>
        {hint && <p style={{ fontSize: '11px', color: '#94A3B8', margin: '2px 0 0' }}>{hint}</p>}
      </div>
      {children}
    </div>
  )
}
const th: React.CSSProperties = { padding: '7px 10px', fontSize: '10px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: 'left', borderBottom: '1px solid #E2E8F0' }
const td: React.CSSProperties = { padding: '7px 10px', fontSize: '12px', color: '#334155', borderBottom: '1px solid #F1F5F9' }

const PRESETS: { key: string; label: string; days: number | null }[] = [
  { key: 'all', label: 'All time', days: null },
  { key: '7', label: 'Last 7 days', days: 7 },
  { key: '30', label: 'Last 30 days', days: 30 },
  { key: '90', label: 'Last 90 days', days: 90 },
]
const isoDate = (d: Date) => d.toISOString().slice(0, 10)
// Shift a YYYY-MM-DD string by whole days, staying on UTC boundaries (matches how the
// backend buckets created_at) so stepping days never drifts in non-UTC timezones.
const shiftDay = (ds: string, delta: number) => {
  const d = new Date(ds + 'T00:00:00Z'); d.setUTCDate(d.getUTCDate() + delta)
  return d.toISOString().slice(0, 10)
}

export const Route = createFileRoute('/_authed/admin/report')({ component: ReportPage })

function ReportPage() {
  const me = getUser()
  const [r, setR] = useState<Report | null>(null)
  const [loading, setLoading] = useState(true)
  const [preset, setPreset] = useState('all')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')

  async function load(f: string, t: string) {
    setLoading(true)
    const qs = new URLSearchParams()
    if (f) qs.set('from', f)
    if (t) qs.set('to', t)
    try {
      const res = await fetch(`${API}/audit/report?${qs}`, { headers: authHeaders() })
      setR(res.ok ? await res.json() : null)
    } catch { setR(null) } finally { setLoading(false) }
  }

  useEffect(() => { if (can('audit_view')) load('', ''); else setLoading(false) }, [])  // eslint-disable-line

  function applyPreset(key: string) {
    setPreset(key)
    const p = PRESETS.find(x => x.key === key)
    if (!p) return
    if (p.days == null) { setFrom(''); setTo(''); load('', '') }
    else {
      const t = new Date(), f = new Date(); f.setDate(f.getDate() - p.days)
      const fs = isoDate(f), ts = isoDate(t)
      setFrom(fs); setTo(ts); load(fs, ts)
    }
  }
  function applyCustom() { setPreset('custom'); load(from, to) }

  // Daily filter — scope the entire report to one specific day (from === to).
  function pickDay(ds: string) {
    if (!ds) return
    setPreset(`day:${ds}`); setFrom(ds); setTo(ds); load(ds, ds)
  }

  // ── drill-down ──────────────────────────────────────────────────────────────
  const [drill, setDrill] = useState<{ title: string; rows: DrillRow[]; loading: boolean; truncated: boolean } | null>(null)
  function openDrill(query: Record<string, string>, title: string) {
    setDrill({ title, rows: [], loading: true, truncated: false })
    const qs = new URLSearchParams(query)
    if (from) qs.set('from', from)
    if (to) qs.set('to', to)
    fetch(`${API}/audit/report/drill?${qs}`, { headers: authHeaders() })
      .then(res => res.ok ? res.json() : null)
      .then(d => setDrill({ title: d?.title || title, rows: d?.items ?? [], loading: false, truncated: !!d?.truncated }))
      .catch(() => setDrill({ title, rows: [], loading: false, truncated: false }))
  }

  if (!can('audit_view')) {
    return <div style={{ padding: '40px', maxWidth: '560px' }}>
      <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A' }}>Onboarding Report</h1>
      <div style={{ marginTop: '12px', padding: '14px 16px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: '8px', color: '#991B1B', fontSize: '13px' }}>
        <strong>Admin access required.</strong> Your role ({ROLE_LABELS[me?.role ?? 'bizops']}) cannot view reports.
      </div></div>
  }
  if (loading && !r) return <div style={{ padding: '40px', color: '#94A3B8' }}>Building report…</div>
  if (!r) return <div style={{ padding: '40px', color: '#991B1B' }}>Could not load the report.</div>

  const o = r.onboarding, u = r.usage
  const t = o.totals
  const today = isoDate(new Date())
  const yesterday = shiftDay(today, -1)
  const activeDay = from && from === to ? from : ''   // single-day filter is active when from===to
  const atToday = !!activeDay && activeDay >= today

  return (
    <>
      <div style={{ padding: '24px 28px', maxWidth: '1180px' }}>
        <style>{`
          @media print { aside, .no-print { display:none !important } }
          .drill-row:hover { background:#F5F3FF !important }
          .drill-card:hover { border-color:#C7D2FE !important; box-shadow:0 2px 8px rgba(99,102,241,0.12) }
        `}</style>
        <div className="no-print" style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', marginBottom: '12px' }}>
          <div>
            <h1 style={{ fontSize: '21px', fontWeight: 700, color: '#0F172A', margin: 0 }}>Catalogue Onboarding Report</h1>
            <p style={{ fontSize: '12px', color: '#94A3B8', marginTop: '3px' }}>
              Generated {when(r.generated_at)} · {r.range.ranged ? <strong style={{ color: '#4338CA' }}>{r.range.from && r.range.from === r.range.to ? `${r.range.from} (single day)` : `${r.range.from ?? '…'} → ${r.range.to ?? '…'}`}</strong> : 'all time'} · <a href="/admin/audit" style={{ color: '#6366F1' }}>← back to Audit Log</a>
            </p>
          </div>
          <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
            <button onClick={() => downloadCsv(r)} style={{ padding: '8px 14px', fontSize: '12px', fontWeight: 600, background: 'white', color: '#166534', border: '1px solid #BBF7D0', borderRadius: '7px', cursor: 'pointer' }}>⬇ Export CSV</button>
            <button onClick={() => window.print()} style={{ padding: '8px 14px', fontSize: '12px', fontWeight: 600, background: '#6366F1', color: 'white', border: 'none', borderRadius: '7px', cursor: 'pointer' }}>🖨 Print / PDF</button>
          </div>
        </div>

        {/* Date-range filter */}
        <div className="no-print" style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'center', marginBottom: '18px', padding: '10px 12px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '9px' }}>
          <span style={{ fontSize: '11px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Period</span>
          {PRESETS.map(p => (
            <button key={p.key} onClick={() => applyPreset(p.key)}
              style={{ padding: '5px 12px', fontSize: '12px', fontWeight: 600, borderRadius: '6px', cursor: 'pointer',
                background: preset === p.key ? '#0F172A' : '#F1F5F9', color: preset === p.key ? 'white' : '#475569', border: 'none' }}>
              {p.label}
            </button>
          ))}
          <span style={{ width: '1px', height: '20px', background: '#E2E8F0', margin: '0 4px' }} />
          <input type="date" value={from} onChange={e => setFrom(e.target.value)} style={{ border: '1px solid #E2E8F0', borderRadius: '6px', padding: '5px 8px', fontSize: '12px' }} />
          <span style={{ color: '#94A3B8', fontSize: '12px' }}>→</span>
          <input type="date" value={to} onChange={e => setTo(e.target.value)} style={{ border: '1px solid #E2E8F0', borderRadius: '6px', padding: '5px 8px', fontSize: '12px' }} />
          <button onClick={applyCustom} disabled={!from && !to} style={{ padding: '5px 14px', fontSize: '12px', fontWeight: 600, background: '#6366F1', color: 'white', border: 'none', borderRadius: '6px', cursor: (from || to) ? 'pointer' : 'default', opacity: (from || to) ? 1 : 0.5 }}>Apply</button>

          {/* Daily filter — jump to a single day's activity */}
          <span style={{ width: '1px', height: '20px', background: '#E2E8F0', margin: '0 4px' }} />
          <span style={{ fontSize: '11px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Day</span>
          {([['Today', today], ['Yesterday', yesterday]] as const).map(([lbl, ds]) => (
            <button key={lbl} onClick={() => pickDay(ds)}
              style={{ padding: '5px 12px', fontSize: '12px', fontWeight: 600, borderRadius: '6px', cursor: 'pointer',
                background: activeDay === ds ? '#0F172A' : '#F1F5F9', color: activeDay === ds ? 'white' : '#475569', border: 'none' }}>
              {lbl}
            </button>
          ))}
          <button onClick={() => pickDay(shiftDay(activeDay || today, -1))} title="Previous day"
            style={{ padding: '5px 9px', fontSize: '13px', fontWeight: 700, borderRadius: '6px', cursor: 'pointer', background: '#F1F5F9', color: '#475569', border: 'none' }}>‹</button>
          <input type="date" value={activeDay} max={today} onChange={e => pickDay(e.target.value)} title="Pick a specific day"
            style={{ border: '1px solid', borderColor: activeDay ? '#6366F1' : '#E2E8F0', borderRadius: '6px', padding: '5px 8px', fontSize: '12px', color: activeDay ? '#4338CA' : '#0F172A', fontWeight: activeDay ? 600 : 400 }} />
          <button onClick={() => pickDay(shiftDay(activeDay || today, 1))} disabled={atToday} title="Next day"
            style={{ padding: '5px 9px', fontSize: '13px', fontWeight: 700, borderRadius: '6px', cursor: atToday ? 'default' : 'pointer', background: '#F1F5F9', color: '#475569', border: 'none', opacity: atToday ? 0.4 : 1 }}>›</button>

          {loading && <span style={{ fontSize: '11px', color: '#94A3B8' }}>updating…</span>}
        </div>

        {/* KPIs */}
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <Kpi label="Imports" value={t.imports} sub="catalogue files scanned" />
          <Kpi label="Items extracted" value={t.extracted} sub="line items in queue" />
          <Kpi label="Matched" value={t.matched} sub={r.range.ranged ? 'in period' : 'all-time'} color="#16A34A" onClick={() => openDrill({ kind: 'items', status: 'matched' }, 'Matched items')} />
          <Kpi label="Pending review" value={o.left.pending_review} sub="left to action (now)" color="#F59E0B" onClick={() => openDrill({ kind: 'items', status: 'pending' }, 'Pending items')} />
          <Kpi label="Verified SKUs" value={o.left.verified} sub="HITL-confirmed (now)" color="#16A34A" onClick={() => openDrill({ kind: 'verified' }, 'Verified SKUs')} />
          <Kpi label="SKUs to verify" value={o.left.to_verify} sub={`of ${fmt(o.left.active_products)} active (now)`} color="#B45309" onClick={() => openDrill({ kind: 'to_verify' }, 'SKUs to verify')} />
        </div>

        {/* Amount left banner */}
        <div style={{ display: 'flex', gap: '12px', marginBottom: '18px', flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: '260px', background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '10px', padding: '16px 18px' }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: '#92400E', textTransform: 'uppercase', letterSpacing: '0.05em' }}>Amount left</p>
            <div style={{ display: 'flex', gap: '28px', marginTop: '8px' }}>
              <div onClick={() => openDrill({ kind: 'items', status: 'pending' }, 'Pending items')} style={{ cursor: 'pointer' }}>
                <p style={{ fontSize: '30px', fontWeight: 700, color: '#B45309', lineHeight: 1 }}>{fmt(o.left.pending_review)}</p>
                <p style={{ fontSize: '11px', color: '#92400E' }}>items pending in queue ›</p>
              </div>
              <div onClick={() => openDrill({ kind: 'to_verify' }, 'SKUs to verify')} style={{ cursor: 'pointer' }}>
                <p style={{ fontSize: '30px', fontWeight: 700, color: '#B45309', lineHeight: 1 }}>{fmt(o.left.to_verify)}</p>
                <p style={{ fontSize: '11px', color: '#92400E' }}>active SKUs to verify ›</p>
              </div>
            </div>
          </div>
        </div>

        {/* Charts row */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px', marginBottom: '14px' }}>
          <Card title="Queue status" hint="click a slice to see the list">
            <Donut onPick={k => openDrill({ kind: 'items', status: k }, `${k.replace('_', ' ')} items`)} data={[
              { label: 'Matched', value: t.matched, color: C.matched, key: 'matched' },
              { label: 'New SKU', value: t.new_sku, color: C.new_sku, key: 'new_sku' },
              { label: 'Rejected', value: t.rejected, color: C.rejected, key: 'rejected' },
              { label: 'Pending', value: o.left.pending_review, color: C.pending, key: 'pending' },
            ]} />
          </Card>
          <Card title="Decisions logged" hint="onboarding actions in the audit trail — click to list">
            <Donut onPick={k => openDrill({ kind: 'actions', action: k }, `${ACTION_LABEL[k] ?? k} decisions`)}
              data={Object.entries(o.by_action).map(([k, v]) => ({ label: ACTION_LABEL[k] ?? k, value: v, color: (C as Record<string, string>)[k] ?? '#94A3B8', key: k }))} />
          </Card>
        </div>

        {/* Timeline */}
        <div style={{ marginBottom: '14px' }}>
          <Card title="Onboarding activity over time" hint="decisions per day — matched (green) · new SKU (indigo) · rejected (red)">
            {o.timeline.length ? <TimelineBars data={o.timeline} /> : <p style={{ fontSize: '12px', color: '#94A3B8' }}>No dated activity yet.</p>}
          </Card>
        </div>

        {/* Reviewer + supplier */}
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px', marginBottom: '14px' }}>
          <Card title="By reviewer" hint="click a reviewer to see their items">
            {o.by_reviewer.length ? <StackBars onRow={l => openDrill({ kind: 'items', reviewer: l }, `Items reviewed by ${l}`)}
              rows={o.by_reviewer.map(x => ({ label: x.reviewer, values: x, total: x.total }))}
              keys={[{ key: 'matched', color: C.matched }, { key: 'new_sku', color: C.new_sku }, { key: 'rejected', color: C.rejected }]} />
              : <p style={{ fontSize: '12px', color: '#94A3B8' }}>No reviewer activity.</p>}
          </Card>
          <Card title="By supplier" hint="click a supplier to see its items (top 25)">
            <div style={{ maxHeight: '260px', overflowY: 'auto' }}>
              <StackBars onRow={l => openDrill({ kind: 'items', supplier: l }, `${l} items`)}
                rows={o.by_supplier.map(x => ({ label: x.supplier, values: x, total: x.total }))}
                keys={[{ key: 'matched', color: C.matched }, { key: 'new_sku', color: C.new_sku }, { key: 'rejected', color: C.rejected }, { key: 'pending', color: C.pending }]} />
            </div>
          </Card>
        </div>

        {/* By import */}
        <div style={{ marginBottom: '14px' }}>
          <Card title="By import" hint="most recent 30 catalogue files">
            <div style={{ overflowX: 'auto', maxHeight: '320px', overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>File</th><th style={th}>Supplier</th><th style={th}>When</th><th style={{ ...th, textAlign: 'right' }}>Extracted</th><th style={{ ...th, textAlign: 'right' }}>Matched</th><th style={{ ...th, textAlign: 'right' }}>New</th><th style={{ ...th, textAlign: 'right' }}>Rejected</th><th style={{ ...th, textAlign: 'right' }}>Pending</th></tr></thead>
                <tbody>
                  {o.by_import.map(i => (
                    <tr key={i.import_id} className="drill-row" style={{ cursor: 'pointer' }}
                      onClick={() => openDrill({ kind: 'items', import_id: String(i.import_id) }, i.filename)}>
                      <td style={{ ...td, maxWidth: '220px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: '#4338CA' }} title={i.filename}>{i.filename}</td>
                      <td style={td}>{i.supplier}</td>
                      <td style={{ ...td, color: '#94A3B8', whiteSpace: 'nowrap' }}>{when(i.imported_at)}</td>
                      <td style={{ ...td, textAlign: 'right', fontWeight: 600 }}>{fmt(i.extracted)}</td>
                      <td style={{ ...td, textAlign: 'right', color: C.matched }}>{i.matched || '—'}</td>
                      <td style={{ ...td, textAlign: 'right', color: C.new_sku }}>{i.new_sku || '—'}</td>
                      <td style={{ ...td, textAlign: 'right', color: C.rejected }}>{i.rejected || '—'}</td>
                      <td style={{ ...td, textAlign: 'right', color: '#B45309' }}>{i.pending || '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>
        </div>

        {/* Platform usage */}
        <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#0F172A', margin: '8px 0 12px' }}>Platform usage</h2>
        <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap', marginBottom: '14px' }}>
          <Kpi label="Total sign-ins" value={u.totals.logins} sub="successful logins" />
          <Kpi label="Failed logins" value={u.totals.failed_logins} sub="rejected attempts" color={u.totals.failed_logins ? '#B45309' : '#0F172A'} />
          <Kpi label="Time on platform" value={dur(u.totals.total_seconds)} sub="summed session time" color="#4338CA" />
          <Kpi label="Active users" value={u.by_user.filter(x => x.logins).length} sub="with sign-ins" />
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '14px' }}>
          <Card title="By user" hint="sign-ins, sessions & time spent">
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <thead><tr><th style={th}>User</th><th style={{ ...th, textAlign: 'right' }}>Logins</th><th style={{ ...th, textAlign: 'right' }}>Sessions</th><th style={{ ...th, textAlign: 'right' }}>Time</th><th style={{ ...th, textAlign: 'right' }}>Avg</th><th style={{ ...th, textAlign: 'right' }}>Last login</th></tr></thead>
              <tbody>
                {u.by_user.filter(x => x.logins || x.total_seconds).map(x => (
                  <tr key={x.user}>
                    <td style={td}><strong style={{ color: '#0F172A' }}>{x.display}</strong>{x.failed ? <span style={{ marginLeft: '6px', fontSize: '10px', color: '#B45309' }}>{x.failed} failed</span> : null}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{x.logins}</td>
                    <td style={{ ...td, textAlign: 'right' }}>{x.sessions}</td>
                    <td style={{ ...td, textAlign: 'right', fontWeight: 600 }}>{dur(x.total_seconds)}</td>
                    <td style={{ ...td, textAlign: 'right', color: '#64748B' }}>{dur(x.avg_seconds)}</td>
                    <td style={{ ...td, textAlign: 'right', color: '#94A3B8', whiteSpace: 'nowrap' }}>{when(x.last_login)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
          <Card title="Recent sessions" hint="login → logout, with time spent">
            <div style={{ maxHeight: '300px', overflowY: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                <thead><tr><th style={th}>User</th><th style={th}>Login</th><th style={th}>Logout</th><th style={{ ...th, textAlign: 'right' }}>Spent</th></tr></thead>
                <tbody>
                  {u.recent_sessions.map((s, i) => (
                    <tr key={i}>
                      <td style={td}>{s.display}</td>
                      <td style={{ ...td, color: '#64748B', whiteSpace: 'nowrap' }}>{when(s.login_at)}</td>
                      <td style={{ ...td, color: '#64748B', whiteSpace: 'nowrap' }}>{when(s.logout_at)}</td>
                      <td style={{ ...td, textAlign: 'right', fontWeight: 600, color: '#4338CA' }}>{dur(s.seconds)}</td>
                    </tr>
                  ))}
                  {u.recent_sessions.length === 0 && <tr><td style={td} colSpan={4}>No completed sessions yet.</td></tr>}
                </tbody>
              </table>
            </div>
          </Card>
        </div>
      </div>
      {drill && <DrillPanel title={drill.title} rows={drill.rows} loading={drill.loading} truncated={drill.truncated} onClose={() => setDrill(null)} />}
    </>
  )
}

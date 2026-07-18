import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'
import { authHeaders, getUser, can, ROLE_LABELS, type Role } from '@/lib/auth'
import { toast } from '@/lib/toast'
import { API_BASE } from '@/lib/config'

const API = API_BASE

export const Route = createFileRoute('/_authed/admin/audit')({ component: AuditPage })

interface AuditEvent {
  id: number | string
  created_at: string
  action: string
  actor_username: string | null
  actor_display_name: string | null
  actor_role: Role | null
  entity_type: string | null
  entity_id: string | null
  entity_label: string | null
  details: unknown
  ip: string | null
  user_agent: string | null
}

// action prefix → chip colour
function actionColor(action: string): { bg: string; fg: string } {
  if (action === 'catalogue.confirm_match' || action === 'catalogue.assign_new') return { bg: '#DBEAFE', fg: '#1E40AF' }  // OCR match
  if (action === 'catalogue.hitl_verify') return { bg: '#DCFCE7', fg: '#166534' }                                          // manual verify
  if (action === 'catalogue.hitl_unverify') return { bg: '#FEE2E2', fg: '#991B1B' }                                       // un-verify
  if (action.startsWith('login.fail')) return { bg: '#FEE2E2', fg: '#991B1B' }
  if (action.startsWith('login') || action === 'logout') return { bg: '#E0F2FE', fg: '#075985' }
  if (action.startsWith('user.')) return { bg: '#EDE9FE', fg: '#5B21B6' }
  if (action.startsWith('product.')) return { bg: '#DCFCE7', fg: '#166534' }
  if (action.startsWith('catalogue.')) return { bg: '#FEF3C7', fg: '#92400E' }
  if (action.startsWith('sheet.')) return { bg: '#FFE4E6', fg: '#9F1239' }
  return { bg: '#F1F5F9', fg: '#475569' }
}

function fmt(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso
    : d.toLocaleString('en-GB', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function summarise(e: AuditEvent): string {
  const d = e.details as Record<string, unknown> | null
  if (!d || typeof d !== 'object') return ''
  if (d.reason) return `reason: ${d.reason}`
  const changes = d.changes as Record<string, { from: unknown; to: unknown }> | undefined
  if (changes) return Object.entries(changes).map(([k, v]) => `${k}: ${v.from ?? '∅'} → ${v.to ?? '∅'}`).join(', ')
  return Object.entries(d).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ')
}

function AuditPage() {
  const me = getUser()
  const [events, setEvents] = useState<AuditEvent[]>([])
  const [actions, setActions] = useState<string[]>([])
  const [actors, setActors] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [fAction, setFAction] = useState('')
  const [fActor, setFActor] = useState('')
  const [fCategory, setFCategory] = useState('')   // '' | ocr_match | update | hitl
  const [q, setQ] = useState('')

  useEffect(() => {
    if (!can('audit_view')) { setLoading(false); return }
    fetch(`${API}/audit/facets`, { headers: authHeaders() }).then(r => r.ok ? r.json() : null)
      .then(d => { if (d) { setActions(d.actions ?? []); setActors(d.actors ?? []) } }).catch(() => {})
  }, [])

  useEffect(() => { if (can('audit_view')) load() }, [fAction, fActor, fCategory]) // eslint-disable-line

  async function load() {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ limit: '300' })
      if (fCategory) qs.set('category', fCategory)
      if (fAction) qs.set('action', fAction)
      if (fActor) qs.set('actor', fActor)
      if (q.trim()) qs.set('q', q.trim())
      const r = await fetch(`${API}/audit?${qs}`, { headers: authHeaders() })
      if (r.ok) setEvents((await r.json()).events ?? [])
      else if (r.status === 403) toast.error('Admin access required')
    } catch { toast.error('Could not load audit log') }
    finally { setLoading(false) }
  }

  if (!can('audit_view')) {
    return (
      <div style={{ padding: '40px', maxWidth: '560px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A' }}>Audit Log</h1>
        <div style={{ marginTop: '12px', padding: '14px 16px', background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: '8px', color: '#991B1B', fontSize: '13px' }}>
          <strong>Admin access required.</strong> Your role ({ROLE_LABELS[me?.role ?? 'bizops']}) cannot view the audit log.
        </div>
      </div>
    )
  }

  const th = { padding: '8px 12px', fontSize: '10px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase' as const, letterSpacing: '0.05em', textAlign: 'left' as const, borderBottom: '1px solid #E2E8F0', position: 'sticky' as const, top: 0, background: '#F8FAFC' }
  const cell = { padding: '7px 12px', fontSize: '12px', color: '#334155', borderBottom: '1px solid #F1F5F9', verticalAlign: 'top' as const }
  const input = { border: '1px solid #E2E8F0', borderRadius: '6px', padding: '6px 10px', fontSize: '12px', background: 'white' }

  return (
    <div style={{ padding: '28px 32px', maxWidth: '1280px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A', margin: 0 }}>Audit Log</h1>
          <p style={{ fontSize: '12.5px', color: '#64748B', marginTop: '4px' }}>Logins, edits, and admin actions — who did what, when, and from where.</p>
        </div>
        <a href="/admin/report" style={{ padding: '8px 16px', fontSize: '12px', fontWeight: 600, background: '#6366F1', color: 'white', borderRadius: '7px', textDecoration: 'none', whiteSpace: 'nowrap', flexShrink: 0 }}>
          📊 Onboarding Report
        </a>
      </div>

      <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '16px', alignItems: 'center' }}>
        {([['', 'All'], ['ocr_match', 'OCR matched'], ['update', 'Updates'], ['hitl', 'HITL-verified']] as const).map(([val, label]) => (
          <button key={val} onClick={() => setFCategory(val)} style={{
            padding: '6px 14px', fontSize: '12px', fontWeight: 600, borderRadius: '7px', cursor: 'pointer',
            border: fCategory === val ? '1px solid #6366F1' : '1px solid #E2E8F0',
            background: fCategory === val ? '#EEF2FF' : 'white',
            color: fCategory === val ? '#4338CA' : '#64748B',
          }}>{label}</button>
        ))}
        <span style={{ fontSize: '11px', color: '#94A3B8', alignSelf: 'center', marginLeft: '4px' }}>
          {fCategory === 'hitl' ? 'OCR match + update' : fCategory === 'ocr_match' ? 'confirm / assign-new' : fCategory === 'update' ? 'all product edits' : 'everything'}
        </span>
      </div>

      <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginTop: '10px', alignItems: 'center' }}>
        <select style={input} value={fAction} onChange={e => setFAction(e.target.value)}>
          <option value="">All actions</option>
          {actions.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <select style={input} value={fActor} onChange={e => setFActor(e.target.value)}>
          <option value="">All users</option>
          {actors.map(a => <option key={a} value={a}>{a}</option>)}
        </select>
        <input style={{ ...input, width: '220px' }} value={q} onChange={e => setQ(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && load()} placeholder="Search SKU / detail / user…" />
        <button onClick={load} style={{ padding: '6px 14px', fontSize: '12px', fontWeight: 600, background: '#6366F1', color: 'white', border: 'none', borderRadius: '6px', cursor: 'pointer' }}>Search</button>
        {(fAction || fActor || q || fCategory) && (
          <button onClick={() => { setFAction(''); setFActor(''); setQ(''); setFCategory('') }} style={{ padding: '6px 12px', fontSize: '12px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '6px', cursor: 'pointer', color: '#64748B' }}>Clear</button>
        )}
        <span style={{ marginLeft: 'auto', fontSize: '11px', color: '#94A3B8' }}>{events.length} event{events.length === 1 ? '' : 's'}</span>
      </div>

      <div style={{ marginTop: '14px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', overflow: 'auto', maxHeight: '72vh' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead><tr>
            <th style={th}>When</th><th style={th}>Action</th><th style={th}>Who</th>
            <th style={th}>Target</th><th style={th}>Detail</th><th style={th}>IP</th>
          </tr></thead>
          <tbody>
            {loading && <tr><td style={cell} colSpan={6}>Loading…</td></tr>}
            {!loading && events.map(e => {
              const c = actionColor(e.action)
              return (
                <tr key={e.id}>
                  <td style={{ ...cell, whiteSpace: 'nowrap', color: '#64748B' }}>{fmt(e.created_at)}</td>
                  <td style={cell}><span style={{ fontSize: '10.5px', fontWeight: 700, padding: '2px 7px', borderRadius: '5px', background: c.bg, color: c.fg, fontFamily: 'monospace' }}>{e.action}</span></td>
                  <td style={cell}>
                    <div style={{ fontWeight: 600, color: '#0F172A' }}>{e.actor_display_name ?? e.actor_username ?? '—'}</div>
                    {e.actor_role && <div style={{ fontSize: '10px', color: '#94A3B8' }}>{ROLE_LABELS[e.actor_role] ?? e.actor_role}</div>}
                  </td>
                  <td style={cell}>
                    {e.entity_label ? <span style={{ fontFamily: 'monospace', fontSize: '11px', color: '#4338CA' }}>{e.entity_label}</span> : '—'}
                    {e.entity_type && <div style={{ fontSize: '10px', color: '#94A3B8' }}>{e.entity_type}</div>}
                  </td>
                  <td style={{ ...cell, color: '#64748B', maxWidth: '360px' }}>{summarise(e)}</td>
                  <td style={{ ...cell, fontFamily: 'monospace', fontSize: '11px', color: '#94A3B8', whiteSpace: 'nowrap' }}>{e.ip ?? '—'}</td>
                </tr>
              )
            })}
            {!loading && events.length === 0 && <tr><td style={cell} colSpan={6}>No events.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  )
}

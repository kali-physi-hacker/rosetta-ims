import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useMemo, useState } from 'react'
import { authHeaders, can } from '@/lib/auth'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'
import { ReparseButton } from '@/components/ReparseButton'
import { API_BASE } from '@/lib/config'

const API = API_BASE

interface Sup {
  id: number; code: string; name: string; segment: string | null
  contact_name: string | null; contact_email: string | null
  lead_time_days: number | null; moq_value: string | null; credit_term: string | null
  order_days: string | null; cut_off_time: string | null; delivery_days: string | null
  brand_count: number; alias_count: number; is_active: number | boolean
}
interface BrandLink { id: number; supplier_id: number; supplier_name: string; supplier_code: string | null }
interface Brand { normalized: string; name: string; is_fmcg: boolean | null; links: BrandLink[] }

type Draft = Partial<Record<'code' | 'name' | 'segment' | 'contact_name' | 'contact_email' | 'contact_phone' | 'lead_time_days' | 'moq_value' | 'credit_term' | 'order_days' | 'cut_off_time' | 'delivery_days', string>>

const inp: React.CSSProperties = { border: '1px solid #E2E8F0', borderRadius: '6px', padding: '6px 9px', fontSize: '12px', background: 'white' }
const lbl: React.CSSProperties = { fontSize: '10px', fontWeight: 600, color: '#94A3B8' }
const btn = (bg: string, fg: string): React.CSSProperties => ({ padding: '6px 12px', fontSize: '12px', fontWeight: 600, background: bg, color: fg, border: 'none', borderRadius: '6px', cursor: 'pointer' })
const ghost: React.CSSProperties = { padding: '5px 10px', fontSize: '11px', fontWeight: 600, background: 'white', color: '#475569', border: '1px solid #E2E8F0', borderRadius: '5px', cursor: 'pointer' }

const FIELDS: Array<[keyof Draft, string, string]> = [
  ['name', 'Name', '220px'], ['segment', 'Segment (vet/non_vet)', '130px'],
  ['contact_name', 'Contact', '140px'], ['contact_email', 'Email', '170px'],
  ['contact_phone', 'Phone', '120px'], ['lead_time_days', 'Lead days', '80px'],
  ['moq_value', 'MOQ', '100px'], ['credit_term', 'Payment terms', '130px'],
  ['order_days', 'Order days', '120px'], ['cut_off_time', 'Cut-off', '90px'],
  ['delivery_days', 'Delivery days', '120px'],
]

function DraftForm({ d, set }: { d: Draft; set: (p: Draft) => void }) {
  return (
    <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'flex-end' }}>
      {FIELDS.map(([k, label, w]) => (
        <label key={k} style={{ display: 'flex', flexDirection: 'column', gap: '3px' }}><span style={lbl}>{label}{k === 'name' ? ' *' : ''}</span>
          <input style={{ ...inp, width: w }} value={d[k] ?? ''} onChange={e => set({ ...d, [k]: e.target.value })} /></label>
      ))}
    </div>
  )
}

function draftBody(d: Draft) {
  const b: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(d)) {
    if (v == null || v === '') continue
    b[k] = k === 'lead_time_days' ? (parseInt(v, 10) || null) : v
  }
  return b
}

export const Route = createFileRoute('/_authed/suppliers')({ component: SuppliersPage })

function SuppliersPage() {
  const isAdmin = can('reference_admin')
  const [suppliers, setSuppliers] = useState<Sup[]>([])
  const [brands, setBrands] = useState<Brand[]>([])
  const [loading, setLoading] = useState(true)
  const [adding, setAdding] = useState(false)
  const [draft, setDraft] = useState<Draft>({})
  const [editingId, setEditingId] = useState<number | null>(null)
  const [edit, setEdit] = useState<Draft>({})
  const [busy, setBusy] = useState(false)
  const [brandQ, setBrandQ] = useState('')
  const [newBrand, setNewBrand] = useState({ name: '', supplier_id: '' })

  async function load() {
    setLoading(true)
    try {
      const r = await fetch(`${API}/suppliers?include_inactive=true`, { headers: authHeaders() })
      if (r.ok) setSuppliers(await r.json())
      if (isAdmin) {
        const b = await fetch(`${API}/brands/detail`, { headers: authHeaders() })
        if (b.ok) setBrands((await b.json()).brands ?? [])
      }
    } finally { setLoading(false) }
  }
  useEffect(() => { load() }, [])  // eslint-disable-line

  async function call(method: string, path: string, body?: unknown): Promise<boolean> {
    setBusy(true)
    try {
      const r = await fetch(`${API}${path}`, {
        method, headers: authHeaders({ 'Content-Type': 'application/json' }),
        body: body ? JSON.stringify(body) : undefined,
      })
      if (r.ok) { await load(); return true }
      toast.error((await r.json().catch(() => ({}))).detail ?? 'Request failed')
      return false
    } finally { setBusy(false) }
  }

  async function createSupplier() {
    if (!draft.name?.trim()) { toast.error('Name is required'); return }
    if (await call('POST', '/suppliers', draftBody(draft))) { toast.success('Supplier created'); setDraft({}); setAdding(false) }
  }
  async function saveEdit(s: Sup) {
    if (await call('PATCH', `/suppliers/${s.id}`, draftBody(edit))) { toast.success(`${s.code} updated`); setEditingId(null) }
  }
  async function toggleActive(s: Sup) {
    const on = !!s.is_active
    const ok = await confirmDialog({
      title: on ? 'Deactivate supplier' : 'Reactivate supplier',
      message: `${on ? 'Deactivate' : 'Reactivate'} ${s.name} (${s.code})?`,
      confirmLabel: on ? 'Deactivate' : 'Reactivate', danger: on,
    })
    if (ok && await call('PATCH', `/suppliers/${s.id}`, { is_active: !on })) toast.success(`${s.code} ${on ? 'deactivated' : 'reactivated'}`)
  }

  async function renameBrand(b: Brand) {
    const to = window.prompt(`Rename brand "${b.name}" to:`, b.name)
    if (!to?.trim() || to.trim() === b.name) return
    if (await call('PATCH', '/brands/rename', { from_name: b.name, to_name: to.trim() })) toast.success(`Renamed to ${to.trim()}`)
  }
  async function unlink(b: Brand, l: BrandLink) {
    const ok = await confirmDialog({
      title: 'Remove brand link',
      message: `Remove "${b.name}" from supplier ${l.supplier_name}?`, confirmLabel: 'Remove', danger: true,
    })
    if (ok && await call('DELETE', `/brands/link/${l.id}`)) toast.success('Link removed')
  }
  async function addLink(b: Brand, supplierId: string) {
    if (!supplierId) return
    if (await call('POST', '/brands/link', { name: b.name, supplier_id: Number(supplierId) })) toast.success(`${b.name} linked`)
  }
  async function addBrand() {
    if (!newBrand.name.trim() || !newBrand.supplier_id) { toast.error('Brand name and supplier required'); return }
    if (await call('POST', '/brands/link', { name: newBrand.name.trim(), supplier_id: Number(newBrand.supplier_id) })) {
      toast.success(`Brand ${newBrand.name.trim()} added`); setNewBrand({ name: '', supplier_id: '' })
    }
  }

  const shownBrands = useMemo(() => {
    const q = brandQ.trim().toLowerCase()
    return q ? brands.filter(b => b.name.toLowerCase().includes(q) || b.links.some(l => l.supplier_name.toLowerCase().includes(q))) : brands
  }, [brands, brandQ])

  const th: React.CSSProperties = { padding: '8px 10px', fontSize: '10px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.05em', textAlign: 'left', borderBottom: '1px solid #E2E8F0' }
  const td: React.CSSProperties = { padding: '8px 10px', fontSize: '12px', color: '#334155', borderBottom: '1px solid #F1F5F9', verticalAlign: 'top' }

  return (
    <div style={{ maxWidth: '1180px' }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', marginBottom: '14px' }}>
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A' }}>Suppliers</h1>
          <p style={{ fontSize: '12px', color: '#94A3B8', marginTop: '3px' }}>Supplier master + brand links. {isAdmin ? 'Changes are audited.' : 'View only · Admin to edit.'}</p>
        </div>
        {isAdmin && !adding && <button style={btn('#6366F1', 'white')} onClick={() => setAdding(true)}>+ Add supplier</button>}
      </div>

      {isAdmin && adding && (
        <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '14px 16px', marginBottom: '16px' }}>
          <DraftForm d={draft} set={setDraft} />
          <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
            <button style={btn('#22C55E', 'white')} disabled={busy} onClick={createSupplier}>{busy ? 'Saving…' : 'Create supplier'}</button>
            <button style={ghost} onClick={() => { setAdding(false); setDraft({}) }}>Cancel</button>
          </div>
        </div>
      )}

      {loading ? <p style={{ fontSize: '13px', color: '#94A3B8' }}>Loading…</p> : (
        <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', overflow: 'auto', marginBottom: '24px' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead><tr>
              <th style={th}>Code</th><th style={th}>Name</th><th style={th}>Segment</th><th style={th}>Contact</th>
              <th style={th}>Lead</th><th style={th}>MOQ</th><th style={th}>Payment</th><th style={th}>Order days</th><th style={th}>Delivery days</th>
              <th style={th}>Brands</th>{isAdmin && <th style={th}>Actions</th>}
            </tr></thead>
            <tbody>
              {suppliers.map(s => (
                editingId === s.id ? (
                  <tr key={s.id}><td style={td} colSpan={isAdmin ? 11 : 10}>
                    <div style={{ fontSize: '11px', fontWeight: 700, color: '#4338CA', marginBottom: '8px' }}>Editing {s.code}</div>
                    <DraftForm d={edit} set={setEdit} />
                    <div style={{ display: 'flex', gap: '8px', marginTop: '10px' }}>
                      <button style={btn('#6366F1', 'white')} disabled={busy} onClick={() => saveEdit(s)}>{busy ? 'Saving…' : 'Save'}</button>
                      <button style={ghost} onClick={() => setEditingId(null)}>Cancel</button>
                    </div>
                  </td></tr>
                ) : (
                  <tr key={s.id} style={{ opacity: s.is_active ? 1 : 0.5 }}>
                    <td style={{ ...td, fontFamily: 'monospace', fontWeight: 600 }}>{s.code}</td>
                    <td style={{ ...td, fontWeight: 600, color: '#0F172A' }}>{s.name}{!s.is_active && <span style={{ marginLeft: '6px', fontSize: '9px', fontWeight: 700, color: '#64748B', background: '#F1F5F9', padding: '1px 6px', borderRadius: '99px' }}>INACTIVE</span>}</td>
                    <td style={td}>{s.segment ?? '—'}</td>
                    <td style={td}>{[s.contact_name, s.contact_email].filter(Boolean).join(' · ') || '—'}</td>
                    <td style={td}>{s.lead_time_days ?? '—'}</td>
                    <td style={td}>{s.moq_value ?? '—'}</td>
                    <td style={td}>{s.credit_term ?? '—'}</td>
                    <td style={td}>{s.order_days ?? '—'}{s.cut_off_time ? ` (${s.cut_off_time})` : ''}</td>
                    <td style={td}>{s.delivery_days ?? '—'}</td>
                    <td style={td}>{s.brand_count}</td>
                    {isAdmin && (
                      <td style={td}>
                        <div style={{ display: 'flex', gap: '6px' }}>
                          <button style={ghost} onClick={() => {
                            setEditingId(s.id)
                            setEdit({ name: s.name, segment: s.segment ?? '', contact_name: s.contact_name ?? '', contact_email: s.contact_email ?? '', lead_time_days: s.lead_time_days != null ? String(s.lead_time_days) : '', moq_value: s.moq_value ?? '', credit_term: s.credit_term ?? '', order_days: s.order_days ?? '', cut_off_time: s.cut_off_time ?? '', delivery_days: s.delivery_days ?? '' })
                          }}>Edit</button>
                          <button style={{ ...ghost, color: s.is_active ? '#991B1B' : '#166534', borderColor: s.is_active ? '#FECACA' : '#BBF7D0' }} onClick={() => toggleActive(s)}>
                            {s.is_active ? 'Deactivate' : 'Reactivate'}
                          </button>
                          <ReparseButton scope="supplier" refId={s.id} label="↻ Re-parse"
                            title={`Re-parse all catalogue-sourced fields for ${s.name} and review the diff`} style={ghost} />
                        </div>
                      </td>
                    )}
                  </tr>
                )
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Brand manager (admin) */}
      {isAdmin && (
        <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '14px 16px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', marginBottom: '12px' }}>
            <h2 style={{ fontSize: '14px', fontWeight: 700, color: '#0F172A', margin: 0 }}>Brands</h2>
            <span style={{ fontSize: '11px', color: '#94A3B8' }}>{brands.length} brands</span>
            <input style={{ ...inp, width: '200px' }} placeholder="Search brand or supplier…" value={brandQ} onChange={e => setBrandQ(e.target.value)} />
            <span style={{ marginLeft: 'auto', display: 'flex', gap: '6px', alignItems: 'center' }}>
              <input style={{ ...inp, width: '160px' }} placeholder="New brand name" value={newBrand.name} onChange={e => setNewBrand({ ...newBrand, name: e.target.value })} />
              <select style={{ ...inp, width: '180px' }} value={newBrand.supplier_id} onChange={e => setNewBrand({ ...newBrand, supplier_id: e.target.value })}>
                <option value="">— supplier —</option>
                {suppliers.filter(s => s.is_active).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
              <button style={btn('#0891B2', 'white')} disabled={busy} onClick={addBrand}>+ Add brand</button>
            </span>
          </div>
          <div style={{ maxHeight: '420px', overflow: 'auto' }}>
            {shownBrands.map(b => (
              <div key={b.normalized} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '6px 4px', borderBottom: '1px solid #F8FAFC', flexWrap: 'wrap' }}>
                <span style={{ fontSize: '12.5px', fontWeight: 600, color: '#0F172A', minWidth: '180px' }}>{b.name}</span>
                {b.links.map(l => (
                  <span key={l.id} style={{ fontSize: '10.5px', fontWeight: 600, background: '#EEF2FF', color: '#4338CA', padding: '2px 8px', borderRadius: '99px', display: 'inline-flex', gap: '5px', alignItems: 'center' }}>
                    {l.supplier_name}
                    <button title="Remove this supplier link" onClick={() => unlink(b, l)}
                      style={{ background: 'none', border: 'none', color: '#6366F1', cursor: 'pointer', padding: 0, fontSize: '11px', lineHeight: 1 }}>×</button>
                  </span>
                ))}
                <select style={{ ...inp, padding: '2px 6px', fontSize: '10.5px' }} value="" onChange={e => addLink(b, e.target.value)}>
                  <option value="">+ link supplier</option>
                  {suppliers.filter(s => s.is_active && !b.links.some(l => l.supplier_id === s.id)).map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
                <button style={{ ...ghost, marginLeft: 'auto' }} onClick={() => renameBrand(b)}>Rename</button>
              </div>
            ))}
            {shownBrands.length === 0 && <p style={{ fontSize: '12px', color: '#94A3B8', padding: '10px 4px' }}>No brands match.</p>}
          </div>
        </div>
      )}
    </div>
  )
}

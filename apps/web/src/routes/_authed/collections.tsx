import { C } from '@/lib/tokens'
import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { authHeaders, can } from '@/lib/auth'
import { API_BASE } from '@/lib/config'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'

const API = API_BASE

export const Route = createFileRoute('/_authed/collections')({ component: CollectionsPage })

interface Condition { field: string; op: string; value: string }
interface Rule { match: 'all' | 'any'; conditions: Condition[] }
interface Collection {
  id: number; name: string; slug: string; description: string | null
  rule: Rule; ai_generated: boolean; count?: number; created_by: string | null
}
interface Draft { name: string; description: string | null; rule: Rule; count: number; ai_generated: boolean }
interface FieldVocab { fields: string[]; numeric_fields: string[]; string_ops: string[]; numeric_ops: string[]; tag_ops: string[] }

const OP_LABEL: Record<string, string> = {
  has: 'has tag', not_has: 'lacks tag', equals: 'is', not_equals: 'is not',
  contains: 'contains', in: 'is any of', gt: '>', gte: '≥', lt: '<', lte: '≤',
}
const FIELD_LABEL: Record<string, string> = {
  tag: 'Tag', category: 'Category', brand: 'Brand', supplier: 'Supplier', name: 'Name',
  status: 'Status', data_grade: 'Data grade', cost: 'Cost', sales_120d: 'Sales 120d',
  stock: 'Stock qty', woc: 'Weeks cover',
}

function CollectionsPage() {
  const [collections, setCollections] = useState<Collection[]>([])
  const [tags, setTags] = useState<string[]>([])
  const [vocab, setVocab] = useState<FieldVocab | null>(null)
  const [loading, setLoading] = useState(true)
  const [editing, setEditing] = useState<Collection | 'new' | null>(null)
  const [drafts, setDrafts] = useState<Draft[] | null>(null)
  const [suggesting, setSuggesting] = useState(false)
  const [viewing, setViewing] = useState<{ col: Collection; products: any[] } | null>(null)

  const fetchAll = useCallback(async () => {
    try {
      const [cRes, tRes, fRes] = await Promise.all([
        fetch(`${API}/collections`, { headers: authHeaders() }),
        fetch(`${API}/tags`, { headers: authHeaders() }),
        fetch(`${API}/collections/fields`, { headers: authHeaders() }),
      ])
      if (cRes.ok) setCollections(await cRes.json())
      if (tRes.ok) setTags((await tRes.json()).map((t: any) => t.label))
      if (fRes.ok) setVocab(await fRes.json())
    } finally { setLoading(false) }
  }, [])
  useEffect(() => { fetchAll() }, [fetchAll])

  async function runSuggest() {
    setSuggesting(true)
    try {
      const r = await fetch(`${API}/collections/suggest`, { method: 'POST', headers: authHeaders() })
      if (r.ok) setDrafts((await r.json()).drafts ?? [])
      else toast.error('Suggest failed')
    } finally { setSuggesting(false) }
  }

  async function saveDraft(d: Draft) {
    const r = await fetch(`${API}/collections`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({ name: d.name, description: d.description, rule: d.rule, ai_generated: true }),
    })
    if (r.ok) { setDrafts(prev => prev?.filter(x => x !== d) ?? null); fetchAll() }
    else toast.error((await r.json().catch(() => ({}))).detail ?? 'Save failed')
  }

  async function del(id: number) {
    if (!(await confirmDialog({ title: 'Delete collection', message: 'Delete this smart collection? The rule is removed; products are unaffected.', confirmLabel: 'Delete', danger: true }))) return
    const r = await fetch(`${API}/collections/${id}`, { method: 'DELETE', headers: authHeaders() })
    if (r.ok) fetchAll()
  }

  async function viewMembers(col: Collection) {
    const r = await fetch(`${API}/collections/${col.id}/products`, { headers: authHeaders() })
    if (r.ok) { const d = await r.json(); setViewing({ col, products: d.products ?? [] }) }
  }

  const canEdit = can('reference_admin')   // only Admins can edit reference data

  return (
    <>
      <div style={{ maxWidth: '1000px' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', flexWrap: 'wrap', gap: '12px', marginBottom: '6px' }}>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: C.ink }}>Smart Collections</h1>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            {canEdit ? (
              <>
                <button onClick={runSuggest} disabled={suggesting}
                  style={{ background: C.primaryBg, color: C.indigoInk, border: '1px solid #C7D2FE', borderRadius: '8px', padding: '8px 16px', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>
                  {suggesting ? 'Thinking…' : '✨ AI suggest'}
                </button>
                <button onClick={() => setEditing('new')}
                  style={{ background: C.indigo, color: 'white', border: 'none', borderRadius: '8px', padding: '8px 18px', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>
                  + New collection
                </button>
              </>
            ) : <span style={{ fontSize: '11px', color: C.faint, background: C.monoBg, padding: '5px 10px', borderRadius: '6px' }}>View only · Admin to edit</span>}
          </div>
        </div>
        <p style={{ fontSize: '13px', color: C.muted, marginBottom: '20px' }}>
          Dynamic product groups defined by rules over tags, category, brand, cost and more. Membership updates automatically — like Shopify smart collections.
        </p>

        {loading ? (
          <p style={{ color: C.faint, fontSize: '13px' }}>Loading…</p>
        ) : collections.length === 0 ? (
          <div style={{ background: 'white', border: '1px dashed #CBD5E1', borderRadius: '10px', padding: '40px', textAlign: 'center' }}>
            <p style={{ fontSize: '14px', color: C.sub, marginBottom: '6px', fontWeight: 600 }}>No collections yet</p>
            <p style={{ fontSize: '13px', color: C.faint }}>Click <strong>AI suggest</strong> to generate a starter set from your tags, or build one from scratch.</p>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: '14px' }}>
            {collections.map(c => (
              <div key={c.id} style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '16px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
                  <span style={{ fontSize: '14px', fontWeight: 700, color: C.ink }}>{c.name}</span>
                  <span style={{ fontSize: '12px', fontWeight: 700, color: C.indigoInk, background: C.primaryBg, borderRadius: '99px', padding: '2px 10px', whiteSpace: 'nowrap' }}>{c.count ?? 0} items</span>
                </div>
                {c.description && <p style={{ fontSize: '12px', color: C.muted, margin: 0 }}>{c.description}</p>}
                <RuleSummary rule={c.rule} />
                <div style={{ display: 'flex', gap: '8px', marginTop: '4px' }}>
                  <button onClick={() => viewMembers(c)} style={btnGhost}>View</button>
                  {canEdit && <button onClick={() => setEditing(c)} style={btnGhost}>Edit</button>}
                  {canEdit && <button onClick={() => del(c.id)} style={{ ...btnGhost, color: C.redInk, marginLeft: 'auto' }}>Delete</button>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {editing && vocab && (
        <CollectionEditor
          initial={editing === 'new' ? null : editing}
          vocab={vocab} tags={tags}
          onClose={() => setEditing(null)}
          onSaved={() => { setEditing(null); fetchAll() }}
        />
      )}

      {drafts && (
        <Modal title="AI-suggested collections" onClose={() => setDrafts(null)}>
          {drafts.length === 0 ? (
            <p style={{ fontSize: '13px', color: C.faint, padding: '8px 0' }}>No suggestions — add more tags during onboarding first.</p>
          ) : drafts.map((d, i) => (
            <div key={i} style={{ borderBottom: '1px solid #F1F5F9', padding: '12px 0', display: 'flex', alignItems: 'center', gap: '12px' }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '13px', fontWeight: 700, color: C.ink }}>{d.name} <span style={{ fontWeight: 600, color: C.indigoInk }}>· {d.count}</span></div>
                {d.description && <div style={{ fontSize: '12px', color: C.muted }}>{d.description}</div>}
                <RuleSummary rule={d.rule} />
              </div>
              <button onClick={() => saveDraft(d)} style={{ background: '#22C55E', color: 'white', border: 'none', borderRadius: '6px', padding: '6px 14px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap' }}>Save</button>
            </div>
          ))}
        </Modal>
      )}

      {viewing && (
        <Modal title={`${viewing.col.name} · ${viewing.products.length} products`} onClose={() => setViewing(null)}>
          {viewing.products.length === 0 ? (
            <p style={{ fontSize: '13px', color: C.faint }}>No products match this rule yet.</p>
          ) : (
            <div style={{ maxHeight: '60vh', overflowY: 'auto' }}>
              {viewing.products.map((p: any) => (
                <Link key={p.sku_code} to={`/items/${p.sku_code}` as never} style={{ display: 'flex', gap: '10px', padding: '8px 4px', borderBottom: '1px solid #F8FAFC', textDecoration: 'none', alignItems: 'baseline' }}>
                  <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: '12px', color: C.indigoInk, width: '88px' }}>{p.sku_code}</span>
                  <span style={{ flex: 1, fontSize: '13px', color: C.ink }}>{p.name}</span>
                  <span style={{ fontSize: '11px', color: C.faint }}>{p.category}</span>
                </Link>
              ))}
            </div>
          )}
        </Modal>
      )}
    </>
  )
}

const btnGhost: React.CSSProperties = { background: 'none', border: '1px solid #E2E8F0', borderRadius: '6px', padding: '4px 12px', fontSize: '12px', fontWeight: 600, color: C.sub, cursor: 'pointer' }

function RuleSummary({ rule }: { rule: Rule }) {
  const join = rule.match === 'any' ? ' OR ' : ' AND '
  const parts = (rule.conditions ?? []).map(c =>
    c.field === 'tag' ? `${c.op === 'not_has' ? 'not ' : ''}#${c.value}`
      : `${FIELD_LABEL[c.field] ?? c.field} ${OP_LABEL[c.op] ?? c.op} ${Array.isArray(c.value) ? (c.value as any).join('/') : c.value}`)
  return <div style={{ fontSize: '11px', color: C.faint, fontFamily: 'ui-monospace, monospace' }}>{parts.join(join) || '—'}</div>
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, zIndex: 60, background: 'rgba(15,23,42,0.45)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'white', borderRadius: '12px', width: '100%', maxWidth: '600px', maxHeight: '86vh', overflow: 'hidden', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 50px rgba(0,0,0,0.3)' }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid #E2E8F0', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <span style={{ fontSize: '14px', fontWeight: 700, color: C.ink }}>{title}</span>
          <button onClick={onClose} style={{ background: 'none', border: '1px solid #E2E8F0', borderRadius: '6px', padding: '4px 10px', fontSize: '12px', cursor: 'pointer', color: C.muted }}>Close</button>
        </div>
        <div style={{ padding: '16px 20px', overflowY: 'auto' }}>{children}</div>
      </div>
    </div>
  )
}

function opsFor(field: string, v: FieldVocab): string[] {
  if (field === 'tag') return v.tag_ops
  if (v.numeric_fields.includes(field)) return v.numeric_ops
  return v.string_ops
}

function CollectionEditor({ initial, vocab, tags, onClose, onSaved }: {
  initial: Collection | null; vocab: FieldVocab; tags: string[]; onClose: () => void; onSaved: () => void
}) {
  const [name, setName] = useState(initial?.name ?? '')
  const [description, setDescription] = useState(initial?.description ?? '')
  const [match, setMatch] = useState<'all' | 'any'>(initial?.rule.match ?? 'all')
  const [conditions, setConditions] = useState<Condition[]>(
    initial?.rule.conditions?.length ? initial.rule.conditions.map(c => ({ ...c, value: Array.isArray(c.value) ? (c.value as any).join(', ') : String(c.value ?? '') }))
      : [{ field: 'tag', op: 'has', value: '' }])
  const [preview, setPreview] = useState<{ count: number; sample: any[] } | null>(null)
  const [saving, setSaving] = useState(false)

  const rule = useMemo<Rule>(() => ({
    match,
    conditions: conditions.filter(c => c.field && c.op).map(c => ({
      field: c.field, op: c.op,
      value: c.op === 'in' ? c.value.split(',').map(s => s.trim()).filter(Boolean) as any : c.value,
    })),
  }), [match, conditions])

  // live preview (debounced)
  const timer = useRef<any>(null)
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current)
    timer.current = setTimeout(async () => {
      try {
        const r = await fetch(`${API}/collections/preview`, {
          method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ rule }),
        })
        setPreview(r.ok ? await r.json() : null)
      } catch { setPreview(null) }
    }, 400)
    return () => { if (timer.current) clearTimeout(timer.current) }
  }, [rule])

  function setCond(i: number, patch: Partial<Condition>) {
    setConditions(prev => prev.map((c, j) => {
      if (j !== i) return c
      const next = { ...c, ...patch }
      if (patch.field) { const ops = opsFor(patch.field, vocab); if (!ops.includes(next.op)) next.op = ops[0] }
      return next
    }))
  }

  async function save() {
    if (!name.trim()) { toast.error('Name required'); return }
    setSaving(true)
    try {
      const url = initial ? `${API}/collections/${initial.id}` : `${API}/collections`
      const r = await fetch(url, {
        method: initial ? 'PATCH' : 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ name, description: description || undefined, rule }),
      })
      if (r.ok) onSaved()
      else toast.error((await r.json().catch(() => ({}))).detail ?? 'Save failed')
    } finally { setSaving(false) }
  }

  return (
    <Modal title={initial ? 'Edit collection' : 'New collection'} onClose={onClose}>
      <datalist id="tag-list">{tags.map(t => <option key={t} value={t} />)}</datalist>
      <label style={lbl}>Name</label>
      <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. Senior Cat Food" style={inp} />
      <label style={lbl}>Description</label>
      <input value={description} onChange={e => setDescription(e.target.value)} placeholder="optional" style={inp} />

      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '14px 0 8px' }}>
        <span style={{ fontSize: '12px', fontWeight: 600, color: C.sub }}>Match</span>
        {(['all', 'any'] as const).map(m => (
          <button key={m} onClick={() => setMatch(m)} style={{
            background: match === m ? C.ink : C.monoBg, color: match === m ? 'white' : C.sub,
            border: 'none', borderRadius: '6px', padding: '4px 12px', fontSize: '12px', fontWeight: 600, cursor: 'pointer',
          }}>{m === 'all' ? 'ALL conditions' : 'ANY condition'}</button>
        ))}
      </div>

      {conditions.map((c, i) => {
        const ops = opsFor(c.field, vocab)
        const isNum = vocab.numeric_fields.includes(c.field)
        return (
          <div key={i} style={{ display: 'flex', gap: '6px', marginBottom: '8px', alignItems: 'center' }}>
            <select value={c.field} onChange={e => setCond(i, { field: e.target.value })} style={sel}>
              {vocab.fields.map(f => <option key={f} value={f}>{FIELD_LABEL[f] ?? f}</option>)}
            </select>
            <select value={c.op} onChange={e => setCond(i, { op: e.target.value })} style={sel}>
              {ops.map(o => <option key={o} value={o}>{OP_LABEL[o] ?? o}</option>)}
            </select>
            <input
              value={c.value}
              onChange={e => setCond(i, { value: e.target.value })}
              list={c.field === 'tag' ? 'tag-list' : undefined}
              type={isNum ? 'number' : 'text'}
              placeholder={c.field === 'tag' ? 'tag' : c.op === 'in' ? 'a, b, c' : 'value'}
              style={{ ...inp, marginBottom: 0, flex: 1 }}
            />
            <button onClick={() => setConditions(prev => prev.filter((_, j) => j !== i))}
              style={{ border: '1px solid #E2E8F0', background: 'none', borderRadius: '6px', width: '30px', height: '30px', cursor: 'pointer', color: C.redInk, flex: '0 0 auto' }}>×</button>
          </div>
        )
      })}
      <button onClick={() => setConditions(prev => [...prev, { field: 'tag', op: 'has', value: '' }])}
        style={{ ...btnGhost, marginTop: '2px' }}>+ Add condition</button>

      <div style={{ marginTop: '16px', padding: '12px 14px', background: C.wash, borderRadius: '8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: '13px', color: C.sub }}>
          Matches <strong style={{ color: C.ink }}>{preview?.count ?? '…'}</strong> products
        </span>
        {preview?.sample?.length ? (
          <span style={{ fontSize: '11px', color: C.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '60%' }}>
            {preview.sample.map(s => s.name).filter(Boolean).slice(0, 3).join(', ')}{preview.count > 3 ? '…' : ''}
          </span>
        ) : null}
      </div>

      <div style={{ display: 'flex', gap: '8px', marginTop: '16px', justifyContent: 'flex-end' }}>
        <button onClick={onClose} style={btnGhost}>Cancel</button>
        <button onClick={save} disabled={saving}
          style={{ background: C.indigo, color: 'white', border: 'none', borderRadius: '8px', padding: '8px 20px', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>
          {saving ? 'Saving…' : initial ? 'Save changes' : 'Create collection'}
        </button>
      </div>
    </Modal>
  )
}

const lbl: React.CSSProperties = { display: 'block', fontSize: '11px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '4px', marginTop: '10px' }
const inp: React.CSSProperties = { width: '100%', border: '1px solid #E2E8F0', borderRadius: '6px', padding: '7px 10px', fontSize: '13px', marginBottom: '4px' }
const sel: React.CSSProperties = { border: '1px solid #E2E8F0', borderRadius: '6px', padding: '7px 6px', fontSize: '12px', background: 'white', flex: '0 0 auto' }

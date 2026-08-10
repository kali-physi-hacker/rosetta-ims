// Identity editor — the fields the ProductVariant itself owns, grouped by
// what they mean: what it is (naming/classification/tags), units, selling
// posture, notes. Shared by the classic /items page and the /sku instrument.
//
// Deliberately NOT here: status (the header menu owns it), per-supplier
// cost/packaging/ordering/MOQ (each supplier's offering owns those), and
// channel prices (the selling lane owns those).
import { useEffect, useState } from 'react'
import { API_BASE } from '@/lib/config'
import { authHeaders, can } from '@/lib/auth'
import { skuToPath } from '@/lib/sku'
import { toast } from '@/lib/toast'
import { C } from '@/lib/tokens'
import type { Product } from '@/lib/types'

const API = API_BASE

// Weight is canonical in grams; kg/lb is just the display/source unit.
const LB_G = 453.592
const gToUnit = (g: number, u: string | null) => +(g / (u === 'lb' ? LB_G : 1000)).toFixed(3)
const unitToG = (v: number, u: string | null) => Math.round(v * (u === 'lb' ? LB_G : 1000))

export function EditSkuModal({ product, onSaved, onClose }: { product: Product; onSaved: (p: Product) => void; onClose: () => void }) {
  const sensitive = can('product_sensitive')
  const [f, setF] = useState({
    name:  product.name ?? '',
    brand: product.brand ?? '',
    category: product.category ?? '',
    hero_sku: !!product.hero_sku,
    uom:       product.uom ?? '',
    subcategory: product.subcategory ?? '',
    segment: product.segment ?? '',
    species: product.species ?? '',
    storage_rule: product.storage_rule ?? 'any',
    weight: product.weight_g != null ? String(gToUnit(product.weight_g, product.weight_unit)) : '',
    weight_unit: product.weight_unit ?? 'kg',
    notes: product.notes ?? '',
    mark_verified: true,
  })
  const [tags, setTags] = useState<string[]>(product.tags ?? [])
  const [tagInput, setTagInput] = useState('')
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

  const addTag = () => {
    const t = tagInput.trim()
    if (t && !tags.includes(t)) setTags([...tags, t])
    setTagInput('')
  }
  const tagsDirty = JSON.stringify(tags) !== JSON.stringify(product.tags ?? [])

  async function save() {
    setSaving(true)
    try {
      const body: Record<string, unknown> = {
        brand: f.brand.trim(), uom: f.uom.trim(), notes: f.notes.trim(),
        subcategory: f.subcategory.trim(), segment: f.segment, species: f.species, storage_rule: f.storage_rule,
      }
      if (f.weight.trim() !== '') { body.weight_g = unitToG(parseFloat(f.weight), f.weight_unit); body.weight_unit = f.weight_unit }
      if (sensitive) { body.name = f.name.trim(); body.category = f.category; body.hero_sku = f.hero_sku }
      body.mark_verified = f.mark_verified
      const r = await fetch(`${API}/products/${skuToPath(product.sku_code)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body),
      })
      if (!r.ok) { toast.error((await r.json().catch(() => ({}))).detail ?? 'Could not save'); return }
      let updated: Product = await r.json()
      if (tagsDirty) {
        const tr = await fetch(`${API}/products/${skuToPath(product.sku_code)}/tags`, {
          method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ tags }),
        })
        if (tr.ok) { const td = await tr.json(); updated = { ...updated, tags: td.tags ?? tags } }
        else toast.error('Details saved, but tags failed — try tags again')
      }
      onSaved(updated); toast.success('SKU updated'); onClose()
    } catch { toast.error('Could not save') }
    finally { setSaving(false) }
  }

  const inp: React.CSSProperties = { border: '1px solid #E2E8F0', borderRadius: '7px', padding: '8px 10px', fontSize: '13px', background: 'white', width: '100%', boxSizing: 'border-box', fontFamily: 'inherit' }
  const inpDis: React.CSSProperties = { ...inp, background: C.wash, color: C.faint, cursor: 'not-allowed' }
  const lbl: React.CSSProperties = { fontSize: '11px', fontWeight: 600, color: C.muted, display: 'block', marginBottom: '4px' }
  const field = (label: string, node: React.ReactNode) => <label style={{ display: 'block' }}><span style={lbl}>{label}</span>{node}</label>
  const hlp: React.CSSProperties = { display: 'block', fontSize: '10.5px', color: C.faint, marginTop: '3px', lineHeight: 1.35 }
  const section = (title: string, hint?: string) => (
    <div style={{ gridColumn: '1 / -1', margin: '6px 0 -4px', display: 'flex', alignItems: 'baseline', gap: '8px' }}>
      <span style={{ fontSize: '10px', fontWeight: 750, letterSpacing: '.06em', textTransform: 'uppercase', color: C.indigo }}>{title}</span>
      {hint && <span style={{ fontSize: '10.5px', color: C.faint }}>{hint}</span>}
      <span style={{ flex: 1, borderTop: `1px solid ${C.line}`, alignSelf: 'center' }} />
    </div>
  )

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '46px 20px', overflowY: 'auto' }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'white', borderRadius: '14px', width: '580px', maxWidth: '100%', padding: '22px', boxShadow: '0 20px 50px rgba(0,0,0,0.25)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '17px', fontWeight: 700, color: C.ink, margin: 0 }}>Edit SKU details</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: '22px', color: C.faint, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
        <p style={{ fontSize: '12px', color: C.faint, margin: '2px 0 14px' }}>
          {product.sku_code} · what this product <i>is</i> — suppliers, costs, deals and prices are edited where they live
        </p>
        <datalist id="opt-brands">{opts.brands.map(o => <option key={o} value={o} />)}</datalist>
        <datalist id="opt-subcategories">{opts.subcategories.map(o => <option key={o} value={o} />)}</datalist>
        <datalist id="opt-uoms">{opts.uoms.map(o => <option key={o} value={o} />)}</datalist>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '13px' }}>
          {section('What it is')}
          <div style={{ gridColumn: '1 / -1' }}>{field('Name', <input style={sensitive ? inp : inpDis} value={f.name} disabled={!sensitive} onChange={e => setF({ ...f, name: e.target.value })} />)}</div>
          {field('Brand', <input list="opt-brands" style={inp} value={f.brand} onChange={e => setF({ ...f, brand: e.target.value })} />)}
          {field('Category', (
            <select style={sensitive ? inp : inpDis} value={f.category} disabled={!sensitive} onChange={e => setF({ ...f, category: e.target.value })}>
              {f.category && !cats.includes(f.category) && <option value={f.category}>{f.category}</option>}
              {cats.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
          ))}
          {field('Subcategory', <input list="opt-subcategories" style={inp} value={f.subcategory} onChange={e => setF({ ...f, subcategory: e.target.value })} placeholder="e.g. antibiotic" />)}
          {field('Segment', (
            <select style={inp} value={f.segment} onChange={e => setF({ ...f, segment: e.target.value })}>
              <option value="">—</option>
              <option value="vet">Veterinary</option>
              <option value="non_vet">Retail</option>
            </select>
          ))}
          {field('Species', (
            <select style={inp} value={f.species} onChange={e => setF({ ...f, species: e.target.value })}>
              <option value="">—</option>
              {['dog', 'cat', 'both', 'other'].map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          ))}
          <label style={{ display: 'block' }}>
            <span style={lbl}>Tags</span>
            <div style={{ ...inp, display: 'flex', flexWrap: 'wrap', gap: '5px', alignItems: 'center', minHeight: '37px', cursor: 'text' }}>
              {tags.map(t => (
                <span key={t} style={{ fontSize: '11px', color: C.ink, background: C.monoBg, border: `1px solid ${C.line}`, borderRadius: '99px', padding: '2px 9px', display: 'inline-flex', gap: '5px', alignItems: 'center' }}>
                  {t}<span style={{ color: C.redInk, cursor: 'pointer', fontWeight: 700 }} onClick={() => setTags(tags.filter(x => x !== t))}>×</span>
                </span>
              ))}
              <input value={tagInput} placeholder={tags.length === 0 ? 'add tag + Enter' : ''}
                onChange={e => setTagInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addTag() } if (e.key === 'Backspace' && !tagInput && tags.length) setTags(tags.slice(0, -1)) }}
                style={{ border: 'none', outline: 'none', flex: 1, minWidth: '70px', fontSize: '12px', background: 'transparent' }} />
            </div>
          </label>

          {section('Unit & weight', 'the sell unit everything counts in — a supplier’s packaging (tray, case…) lives on its offering')}
          {field('Sell unit (e.g. tablet, can, ml)', <input list="opt-uoms" style={inp} value={f.uom} onChange={e => setF({ ...f, uom: e.target.value })} />)}
          {field('Weight per sell unit', (
            <div style={{ display: 'flex', gap: '6px' }}>
              <input type="number" style={{ ...inp, flex: 1 }} value={f.weight} onChange={e => setF({ ...f, weight: e.target.value })} />
              <select style={{ ...inp, width: '64px' }} value={f.weight_unit} onChange={e => setF({ ...f, weight_unit: e.target.value })}>
                <option value="kg">kg</option>
                <option value="lb">lb</option>
              </select>
            </div>
          ))}
          <div />

          {section('Selling', 'channel prices & order multiples live on selling items; RRP lives on each supplier')}
          <label style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: '8px', cursor: sensitive ? 'pointer' : 'not-allowed' }}>
            <input type="checkbox" checked={f.hero_sku} disabled={!sensitive} onChange={e => setF({ ...f, hero_sku: e.target.checked })} />
            <span style={{ fontSize: '13px', color: C.ink }}>Hero SKU</span>
          </label>

          {section('Handling & notes')}
          {field('Storage rule', (
            <select style={inp} value={f.storage_rule} onChange={e => setF({ ...f, storage_rule: e.target.value as 'any' | 'clinic_only' })}>
              <option value="any">any (clinic + warehouse)</option>
              <option value="clinic_only">clinic only</option>
            </select>
          ))}
          <div />
          <div style={{ gridColumn: '1 / -1' }}>{field('Notes', <textarea style={{ ...inp, minHeight: '54px', resize: 'vertical' }} value={f.notes} onChange={e => setF({ ...f, notes: e.target.value })} />)}</div>

          <label style={{ gridColumn: '1 / -1', display: 'flex', alignItems: 'center', gap: '8px', cursor: 'pointer', padding: '8px 10px', background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: '8px' }}>
            <input type="checkbox" checked={f.mark_verified} onChange={e => setF({ ...f, mark_verified: e.target.checked })} />
            <span style={{ fontSize: '13px', fontWeight: 600, color: C.green }}>Mark as HITL&#8209;Verified on save</span>
          </label>
        </div>
        {!sensitive && <p style={{ fontSize: '11px', color: C.faint, marginTop: '12px' }}>Name, category &amp; hero are locked for your role.</p>}
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '18px' }}>
          <button onClick={onClose} style={{ padding: '9px 16px', fontSize: '13px', fontWeight: 600, color: C.muted, background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', cursor: 'pointer' }}>Cancel</button>
          <button onClick={save} disabled={saving} style={{ padding: '9px 18px', fontSize: '13px', fontWeight: 600, color: 'white', background: C.indigo, border: 'none', borderRadius: '8px', cursor: saving ? 'default' : 'pointer', opacity: saving ? 0.7 : 1 }}>{saving ? 'Saving…' : 'Save changes'}</button>
        </div>
      </div>
    </div>
  )
}

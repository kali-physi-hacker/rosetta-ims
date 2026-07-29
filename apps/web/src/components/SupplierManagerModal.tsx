// Supplier manager — link suppliers to a variant, edit cost/pack/ordering
// terms and stock status. Shared by the classic /items page and the /sku
// domain view. Extracted verbatim from the classic page.
import { useEffect, useState } from 'react'
import { API_BASE } from '@/lib/config'
import { authHeaders } from '@/lib/auth'
import { skuToPath } from '@/lib/sku'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'
import { C } from '@/lib/tokens'
import type { Product } from '@/lib/types'

const API = API_BASE
// Ordering-UOM vocabulary for the supplier terms dropdowns.
const UOM_OPTIONS = [
  'unit', 'each', 'piece', 'pack', 'box', 'case', 'carton', 'inner', 'outer', 'dozen', 'set',
  'can', 'pouch', 'sachet', 'bottle', 'jar', 'tub', 'bag',
  'tablet', 'capsule', 'strip', 'blister', 'vial', 'ampoule', 'tube', 'syringe', 'roll',
  'ml', 'L', 'g', 'kg',
]


export function SupplierManagerModal({ product, onSaved, onClose }: { product: Product; onSaved: (p: Product) => void; onClose: () => void }) {
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
  const lblS: React.CSSProperties = { fontSize: '10px', fontWeight: 600, color: C.faint, display: 'flex', flexDirection: 'column', gap: '3px' }
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
          <h2 style={{ fontSize: '17px', fontWeight: 700, color: C.ink }}>Manage suppliers</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: '22px', color: C.faint, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
        <p style={{ fontSize: '12px', color: C.faint, margin: '2px 0 16px' }}>{product.sku_code} · {rows.length} supplier{rows.length === 1 ? '' : 's'}</p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {rows.map(s => {
            const dr = drafts[s.id]
            return (
              <div key={s.id} style={{ border: `1px solid ${s.is_primary ? C.indigoLine : C.line}`, borderRadius: '10px', padding: '12px', background: s.is_primary ? '#F5F7FF' : 'white' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
                  {s.is_primary
                    ? <span style={{ fontSize: '10.5px', fontWeight: 700, color: C.indigoInk, background: '#E0E7FF', padding: '2px 8px', borderRadius: '999px' }}>★ PRIMARY</span>
                    : <button onClick={() => makePrimary(s.id)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: C.indigo, background: 'white', border: '1px solid #C7D2FE', borderRadius: '6px', padding: '3px 9px', cursor: 'pointer' }}>Make primary</button>}
                  <span style={{ flex: 1 }} />
                  <button onClick={() => saveRow(s.id)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: 'white', background: C.indigo, border: 'none', borderRadius: '6px', padding: '4px 12px', cursor: 'pointer' }}>Save</button>
                  <button onClick={() => removeRow(s.id, s.name)} disabled={busy || rows.length <= 1} title={rows.length <= 1 ? 'A SKU must keep at least one supplier' : 'Remove'} style={{ fontSize: '11px', fontWeight: 600, color: rows.length <= 1 ? C.knobOff : '#DC2626', background: 'white', border: `1px solid ${rows.length <= 1 ? C.line : '#FCA5A5'}`, borderRadius: '6px', padding: '4px 10px', cursor: rows.length <= 1 ? 'not-allowed' : 'pointer' }}>Remove</button>
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
                    <div style={{ marginTop: '7px', display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap', fontSize: '11px', color: C.muted }}>
                      {eff != null
                        ? <span>Effective unit cost <b style={{ color: C.ink }}>HK${eff.toFixed(2)}</b> / {puom}{upv > 1 && <span style={{ color: C.faint }}> (HK${bc} ÷ {upv})</span>}</span>
                        : <span style={{ color: C.knobOff }}>enter cost + basis units to see effective unit cost</span>}
                      {cs && <span style={{ padding: '1px 7px', borderRadius: '99px', background: C.monoBg, color: C.muted, fontWeight: 600 }}>cost: {cs}</span>}
                    </div>
                  )
                })()}
                {parseInt(dr?.units_per_pack ?? '', 10) > 1 && !strOrNull(dr?.pricing_note ?? '') &&
                  <div style={{ marginTop: '5px', fontSize: '10.5px', color: C.amber }}>⚠ Cost basis units &gt; 1 — add a pricing note explaining what the price covers (e.g. per box of N).</div>}
                {['Can(s)', 'Pouch(es)'].includes((dr?.order_increment_uom ?? '').trim()) && /\b(dry|bag|kg|lbs?)\b/i.test(current.name) &&
                  <div style={{ marginTop: '5px', fontSize: '10.5px', color: C.amber }}>⚠ Dry/bag product with a can/pouch order UOM — likely wrong.</div>}
                {/* Supplier ordering terms */}
                <div style={{ marginTop: '10px', paddingTop: '10px', borderTop: '1px solid #F1F5F9' }}>
                  <p style={{ fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '.04em', marginBottom: '6px' }}>Supplier ordering</p>
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
                    <div style={{ marginTop: '8px', fontSize: '10.5px', color: C.muted, background: C.wash, border: '1px solid #E2E8F0', borderRadius: '6px', padding: '6px 8px' }}>
                      📄 Catalogue evidence{full[s.id]?.cost_source_ref ? ` · ${full[s.id]?.cost_source_ref}` : ''}{full[s.id]?.cost_updated_at ? ` · ${full[s.id]?.cost_updated_at?.slice(0, 10)}` : ''}
                    </div>}
                </div>
                <div style={{ marginTop: '10px', paddingTop: '10px', borderTop: '1px solid #F1F5F9', display: 'flex', gap: '7px', alignItems: 'center', flexWrap: 'wrap' }}>
                  {s.stock_status === 'out_of_stock'
                    ? <span style={{ fontSize: '10.5px', fontWeight: 700, color: '#DC2626', background: C.badBg, border: '1px solid #FCA5A5', borderRadius: '6px', padding: '2px 8px' }}>● OUT OF STOCK{s.reported_out_at ? ` · since ${s.reported_out_at}` : ''}</span>
                    : <span style={{ fontSize: '10.5px', fontWeight: 700, color: C.green, background: C.okBg, border: '1px solid #A7F3D0', borderRadius: '6px', padding: '2px 8px' }}>● In stock</span>}
                  <input placeholder="Restock date" defaultValue={s.expected_restock_at ?? ''} onChange={e => setStockDraft({ ...stockDraft, [s.id]: { restock: e.target.value, note: stockDraft[s.id]?.note ?? s.stock_note ?? '' } })} style={{ ...inp, width: '118px', padding: '4px 8px', fontSize: '11px' }} />
                  <input placeholder="Note" defaultValue={s.stock_note ?? ''} onChange={e => setStockDraft({ ...stockDraft, [s.id]: { restock: stockDraft[s.id]?.restock ?? s.expected_restock_at ?? '', note: e.target.value } })} style={{ ...inp, flex: 1, minWidth: '90px', padding: '4px 8px', fontSize: '11px' }} />
                  {s.stock_status === 'out_of_stock' ? <>
                    <button onClick={() => setStock(s.id, 'out_of_stock', s)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: C.amber, background: 'white', border: '1px solid #FCD34D', borderRadius: '6px', padding: '4px 10px', cursor: 'pointer' }}>Update</button>
                    <button onClick={() => setStock(s.id, 'in_stock', s)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: 'white', background: '#16A34A', border: 'none', borderRadius: '6px', padding: '4px 10px', cursor: 'pointer' }}>Back in stock</button>
                  </> : <button onClick={() => setStock(s.id, 'out_of_stock', s)} disabled={busy} style={{ fontSize: '11px', fontWeight: 600, color: '#DC2626', background: 'white', border: '1px solid #FCA5A5', borderRadius: '6px', padding: '4px 10px', cursor: 'pointer' }}>Mark out of stock</button>}
                  {(s.stock_events?.length ?? 0) > 0 && <span style={{ fontSize: '10.5px', color: C.faint }} title={s.stock_events.map(e => `${e.out_at} → ${e.restock_at ?? 'ongoing'} (${e.days ?? '?'}d)`).join('\n')}>{s.stock_events.length} OOS period{s.stock_events.length > 1 ? 's' : ''}</span>}
                </div>
              </div>
            )
          })}
          {rows.length === 0 && <p style={{ fontSize: '12px', color: C.faint }}>No suppliers linked yet — add one below.</p>}
        </div>

        <div style={{ marginTop: '14px', border: '1px dashed #CBD5E1', borderRadius: '10px', padding: '12px', background: '#FAFAFA' }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.04em', marginBottom: '8px' }}>Add supplier</p>
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
            <button onClick={addSupplier} disabled={busy || !add.supplier_id} style={{ fontSize: '12px', fontWeight: 600, color: 'white', background: !add.supplier_id ? C.knobOff : '#16A34A', border: 'none', borderRadius: '7px', padding: '7px 14px', cursor: !add.supplier_id ? 'default' : 'pointer', height: '34px' }}>Add</button>
          </div>
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', marginTop: '18px' }}>
          <button onClick={onClose} style={{ padding: '9px 18px', fontSize: '13px', fontWeight: 600, color: C.muted, background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', cursor: 'pointer' }}>Done</button>
        </div>
      </div>
    </div>
  )
}


// Cost + MBB (max-bulk-buy) term editor for the primary supplier link.
// Shared by the classic /items page (inside Edit details) and the /sku domain
// view (as its own modal). Extracted verbatim from the classic page.
import { useState } from 'react'
import { API_BASE } from '@/lib/config'
import { authHeaders, can } from '@/lib/auth'
import { skuToPath } from '@/lib/sku'
import { toast } from '@/lib/toast'
import { C } from '@/lib/tokens'
import type { Product } from '@/lib/types'

const API = API_BASE
const num = (s: string) => { const n = parseFloat(s); return s.trim() !== '' && Number.isFinite(n) ? n : null }
const int = (s: string) => { const n = parseInt(s, 10); return s.trim() !== '' && Number.isFinite(n) ? n : null }

export function CostMbbEditor({ product, onSaved }: { product: Product; onSaved: (p: Product) => void }) {
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
            <span style={{ color: product.uom_verified_at ? C.ok : C.amber, fontSize: '11.5px' }}>
              · {product.uom_verified_at ? '✓ ' : ''}{product.units_per_pack} {uomLabel} / {product.pack_unit ?? 'pack'}
            </span>
          )}
        </div>

        {/* MBB terms */}
        <div className="subh" style={{ marginTop: '16px' }}>Max-Bulk-Buy terms</div>
        {!sup && <div style={{ fontSize: '12px', color: '#8A93A2' }}>Link a supplier first to add MBB terms.</div>}
        {sup && <>
          {product.all_suppliers.length > 1 && <p style={{ fontSize: '11px', color: '#8A93A2', marginBottom: '6px' }}>Terms for <b style={{ color: C.ink }}>{sup.name ?? 'primary supplier'}</b> (other suppliers: use Manage in the Suppliers card)</p>}
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


// SKU rename — new code (or category regenerate), rename history, and the
// external-sync warning. Shared by the classic /items page and the /sku
// instrument view. Fetches its own rename history so callers stay thin.
import { useEffect, useState } from 'react'
import { API_BASE } from '@/lib/config'
import { authHeaders } from '@/lib/auth'
import { skuToPath } from '@/lib/sku'
import { toast } from '@/lib/toast'
import { C } from '@/lib/tokens'
import type { Product } from '@/lib/types'

const API = API_BASE

export function ChangeSkuModal({ product, onClose }: { product: Product; onClose: () => void }) {
  const [val, setVal] = useState(product.sku_code)
  const [busy, setBusy] = useState(false)
  const [regen, setRegen] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  const [history, setHistory] = useState<{ from: string; to: string; at: string; by: string | null }[]>([])

  useEffect(() => {
    fetch(`${API}/products/${skuToPath(product.sku_code)}/sku-history`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setHistory(d.history ?? []) })
      .catch(() => {})
  }, [product.sku_code])

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
        window.location.href = `/sku/${skuToPath(next)}`
      } else {
        const d = await r.json().catch(() => ({}))
        setErr(d.detail ?? 'Could not change SKU')   // 409 = duplicate, stays open
        setBusy(false)
      }
    } catch { setErr('Could not change SKU'); setBusy(false) }
  }

  const inp: React.CSSProperties = { border: `1px solid ${err ? '#FCA5A5' : C.line}`, borderRadius: '7px', padding: '9px 11px', fontSize: '14px', fontFamily: 'monospace', background: 'white', width: '100%', boxSizing: 'border-box' }

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '70px 20px', overflowY: 'auto' }}>
      <div onClick={e => e.stopPropagation()} style={{ background: 'white', borderRadius: '14px', width: '460px', maxWidth: '100%', padding: '22px', boxShadow: '0 20px 50px rgba(0,0,0,0.25)' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <h2 style={{ fontSize: '17px', fontWeight: 700, color: C.ink }}>Change SKU code</h2>
          <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: '22px', color: C.faint, cursor: 'pointer', lineHeight: 1 }}>×</button>
        </div>
        <p style={{ fontSize: '12px', color: C.faint, margin: '2px 0 16px' }}>Current: <span style={{ fontFamily: 'monospace', color: C.muted }}>{product.sku_code}</span></p>

        <label style={{ fontSize: '11px', fontWeight: 600, color: C.muted, display: 'block', marginBottom: '5px' }}>New SKU</label>
        <div style={{ display: 'flex', gap: '8px' }}>
          <input style={inp} value={val} autoFocus onChange={e => { setVal(e.target.value); setErr(null) }}
            onKeyDown={e => e.key === 'Enter' && save()} placeholder="e.g. 40005811" />
          <button onClick={regenerate} disabled={regen} title="Generate a unique SKU for this category" style={{ flexShrink: 0, padding: '0 13px', fontSize: '12px', fontWeight: 600, color: C.indigo, background: 'white', border: '1px solid #C7D2FE', borderRadius: '7px', cursor: regen ? 'default' : 'pointer', whiteSpace: 'nowrap' }}>{regen ? '…' : '⟳ Regenerate'}</button>
        </div>
        {err && <p style={{ fontSize: '12px', color: '#DC2626', marginTop: '8px', fontWeight: 500 }}>{err}</p>}

        {history.length > 0 && (
          <div style={{ marginTop: '16px' }}>
            <div style={{ fontSize: '10.5px', fontWeight: 700, color: C.faint, letterSpacing: '0.04em', marginBottom: '6px' }}>PREVIOUS CODES</div>
            {history.map((h, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'baseline', gap: '7px', fontSize: '12px', padding: '4px 0', borderTop: i ? '1px solid #F1F5F9' : 'none' }}>
                <span style={{ fontFamily: 'monospace', color: C.ink, fontWeight: 600 }}>{h.from}</span>
                <span style={{ color: C.knobOff }}>→</span>
                <span style={{ fontFamily: 'monospace', color: C.muted }}>{h.to}</span>
                <span style={{ marginLeft: 'auto', color: C.faint, fontSize: '11px', whiteSpace: 'nowrap' }}>{new Date(h.at).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: '2-digit' })}{h.by ? ` · ${h.by}` : ''}</span>
              </div>
            ))}
          </div>
        )}
        <div style={{ marginTop: '14px', padding: '9px 11px', background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '8px', fontSize: '11.5px', color: C.amberInk, lineHeight: 1.5 }}>
          Must be unique. The onboarding history follows the rename; external systems (Google Sheet, Shopify, POS) keep the old code until re-synced.
        </div>

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '20px' }}>
          <button onClick={onClose} style={{ padding: '9px 16px', fontSize: '13px', fontWeight: 600, color: C.muted, background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', cursor: 'pointer' }}>Cancel</button>
          <button onClick={save} disabled={busy} style={{ padding: '9px 18px', fontSize: '13px', fontWeight: 600, color: 'white', background: C.indigo, border: 'none', borderRadius: '8px', cursor: busy ? 'default' : 'pointer', opacity: busy ? 0.7 : 1 }}>{busy ? 'Saving…' : 'Change SKU'}</button>
        </div>
      </div>
    </div>
  )
}

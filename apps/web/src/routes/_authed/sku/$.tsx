// SKU details v2 — the classic page's type scale and card system (shared
// SKUD_CSS), reorganized around what each domain entity owns: identity, then
// supplier offerings (buy side), selling items (sell side), inventory, and
// provenance. Supplier and MBB management run natively here through the
// shared SupplierManagerModal / CostMbbEditor components. Run IDs and
// pipeline jargon never render — provenance speaks plain words and links to
// the audit surfaces.
import { createFileRoute, Link, useNavigate, useRouter } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { API_BASE } from '@/lib/config'
import { authHeaders } from '@/lib/auth'
import { skuToPath } from '@/lib/sku'
import { SKUD_CSS } from '@/lib/skudCss'
import { Spinner } from '@/components/Spinner'
import { SupplierManagerModal } from '@/components/SupplierManagerModal'
import { CostMbbEditor } from '@/components/CostMbbEditor'
import type { MbbTerm, Product } from '@/lib/types'

export const Route = createFileRoute('/_authed/sku/$')({ component: SkuDetailsV2 })

interface OfferingPrice {
  unit_cost: number
  amount: number
  basis: string | null
  since: string | null
  until: string | null
  is_current: boolean
  source: 'catalogue' | 'manual'
  run_id: string | null
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

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, { cache: 'no-store', headers: authHeaders() })
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}))
    const detail = (payload as any)?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message ?? `HTTP ${res.status}`)
  }
  return res.json()
}

const fmtDay = (iso: string | null | undefined) => {
  if (!iso) return null
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short', year: 'numeric' })
}
const money = (v: number | null | undefined, digits = 2) => (v == null ? '—' : v.toFixed(digits))

// Bulk-term display: what the deal is and what it takes, in plain words.
function termDeal(t: MbbTerm): string {
  if (t.note) return t.note
  switch (t.kind) {
    case 'buy_x_get_y': return `Buy ${t.min_qty ?? '?'} get ${t.free_qty ?? '?'} free`
    case 'spend_discount': return t.discount_pct != null ? `${(t.discount_pct * 100).toFixed(0)}% off order` : 'Spend discount'
    case 'flat_unit_cost': return 'Bulk unit price'
    default: return t.kind.replace(/_/g, ' ')
  }
}
function termRequires(t: MbbTerm, uom: string): string {
  if (t.min_qty) return `${t.min_qty}+ ${uom}s`
  if (t.min_spend != null) return `HK$${t.min_spend.toFixed(0)} spend`
  return '—'
}

const SOURCE_CHIP: Record<string, { cls: string; label: string; words: string }> = {
  catalogue: { cls: 'ok', label: 'CATALOGUE', words: 'from a committed catalogue' },
  manual: { cls: 'acc', label: 'MANUAL', words: 'set by a person' },
  legacy: { cls: 'neu', label: 'LEGACY', words: 'pre-domain cost' },
}
function SourceBadge({ source }: { source: string }) {
  const s = SOURCE_CHIP[source] ?? SOURCE_CHIP.legacy
  return <span className={`bdg ${s.cls}`} style={{ fontSize: 10 }}>{s.label}</span>
}

// Definition row inside a card body — the classic page's reading rhythm.
function DefRow({ label, children, last }: { label: string; children: React.ReactNode; last?: boolean }) {
  return (
    <div style={{ display: 'flex', gap: 14, padding: '8px 17px', borderBottom: last ? 'none' : '1px solid var(--line2)', alignItems: 'baseline' }}>
      <span style={{ flex: '0 0 96px', fontSize: 11, fontWeight: 650, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.04em' }}>{label}</span>
      <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: 'var(--ink2)' }}>{children}</span>
    </div>
  )
}

function SkuDetailsV2() {
  const rawSplat = Route.useParams({ select: p => p._splat })
  const sku = (rawSplat ?? '').split('/').map(decodeURIComponent).join('/')
  const router = useRouter()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const goBack = () => { if (typeof window !== 'undefined' && window.history.length > 1) router.history.back(); else navigate({ to: '/' as never }) }
  const [manageOpen, setManageOpen] = useState(false)
  const [mbbOpen, setMbbOpen] = useState(false)

  const product = useQuery({ queryKey: ['sku-v2', sku], queryFn: () => getJson<Product>(`/products/${skuToPath(sku)}`) })
  const offerings = useQuery({
    queryKey: ['sku-v2-offerings', sku],
    queryFn: () => getJson<{ offerings: OfferingEntry[] }>(`/products/${skuToPath(sku)}/offerings`),
  })

  if (product.isLoading) return <div style={{ padding: 40 }}><Spinner /></div>
  if (product.isError) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: '#C0362C', fontSize: 13 }}>
        {String((product.error as Error)?.message ?? product.error)}
        <br /><Link to={'/' as never} style={{ color: '#4F46E5', fontSize: 12, marginTop: 8, display: 'inline-block' }}>← Back to Inventory</Link>
      </div>
    )
  }
  const p = product.data!
  const offeringRows = offerings.data?.offerings ?? []
  const byId = new Map(p.all_suppliers.map(s => [s.supplier_id, s]))
  const preferred = p.all_suppliers.find(s => s.is_preferred) ?? p.all_suppliers[0] ?? null
  const preferredOffering = offeringRows.find(o => o.supplier_id === preferred?.supplier_id) ?? offeringRows[0] ?? null
  const uom = p.uom ?? 'unit'
  const effectiveCost = p.unit_cost
  const mr = p.margin_range
  const marginRows = (mr?.channels ?? [])
  const bestMargin = marginRows.reduce<{ channel: string; margin: number } | null>(
    (best, ch) => (ch.basic_margin != null && (!best || ch.basic_margin > best.margin) ? { channel: ch.channel, margin: ch.basic_margin } : best),
    null,
  )
  const trend = p.sales_trend ?? []
  const trendMax = Math.max(...trend.map(t => t.units), 1)
  const wdbc = p.weekly_demand_by_channel
  const costSince = preferredOffering?.current?.since ?? p.cost_updated_at
  const costSource = preferredOffering?.source ?? 'legacy'
  const classicParams = { _splat: skuToPath(p.sku_code) } as never
  // Modal saves return the fresh product payload — update it in place and let
  // the offerings lane refetch (a cost edit writes a new offering price row).
  const onSaved = (updated: Product) => {
    queryClient.setQueryData(['sku-v2', sku], updated)
    queryClient.invalidateQueries({ queryKey: ['sku-v2-offerings', sku] })
  }

  return (
    <div className="skud" style={{ padding: '26px 30px 60px', maxWidth: 1280, margin: '0 auto' }}>
      <style>{SKUD_CSS}</style>

      {/* Identity */}
      <div className="hdr">
        <div style={{ minWidth: 0 }}>
          <div className="eyebrow"><span className="lnk" onClick={goBack} style={{ fontWeight: 600 }}>← Back</span> · Domain view (preview)</div>
          <h1>{p.name}</h1>
          <div className="idline">
            <span className="skucode" style={{ fontSize: 14 }}>{p.sku_code}</span>
            {p.brand && <><span className="sep">·</span><span>{p.brand}</span></>}
            <span className="sep">·</span><span>{p.category}{p.species ? ` · ${p.species}` : ''}</span>
          </div>
          <div className="badges">
            <span className={`bdg ${p.status === 'ACTIVE' ? 'ok' : 'warn'}`}><span className="st" />{p.status}</span>
            <span className="bdg neu">data grade {p.data_grade}</span>
            <span className="bdg neu">{p.units_per_pack && p.units_per_pack > 1 ? `${p.pack_unit ?? 'pack'} of ${p.units_per_pack} ${uom}s` : `per ${uom}`}</span>
            {p.hitl_verified && <span className="bdg ok">verified</span>}
          </div>
        </div>
        <div className="hdr-act">
          <button className="btn" onClick={() => setManageOpen(true)}>Manage suppliers</button>
          <button className="btn" onClick={() => setMbbOpen(true)}>MBB terms</button>
          <Link className="btn pri" to={'/items/$' as never} params={classicParams}>Classic view →</Link>
        </div>
      </div>

      {/* Metrics band */}
      <div className="metrics">
        <div className="metric">
          <div className="ml">Effective unit cost</div>
          <div className="mv">{money(effectiveCost)} <span style={{ fontSize: 12, color: 'var(--muted)', fontWeight: 500 }}>/{uom}</span></div>
          <div className="ms" style={{ display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            <SourceBadge source={costSource} />{costSince ? fmtDay(costSince) : ''}
          </div>
        </div>
        <div className="metric">
          <div className="ml">Best net margin</div>
          <div className="mv" style={{ color: bestMargin && bestMargin.margin >= p.gp_floor ? 'var(--good)' : 'var(--amber)' }}>
            {bestMargin ? `${(bestMargin.margin * 100).toFixed(1)}%` : '—'}
          </div>
          <div className="ms">{bestMargin ? `${bestMargin.channel} · ` : ''}floor {(p.gp_floor * 100).toFixed(0)}%</div>
        </div>
        <div className="metric">
          <div className="ml">MBB best cost</div>
          <div className="mv">{money(p.mbb_unit_cost)}</div>
          <div className="ms">{p.mbb_unit_cost != null && mr?.mbb_min_qty ? `at ${mr.mbb_min_qty}+ ${uom}s` : 'no bulk terms'}</div>
        </div>
        <div className="metric">
          <div className="ml">Stock on hand</div>
          <div className="mv">{p.total_qty}</div>
          <div className="ms">warehouse {p.warehouse_qty} · clinic {p.clinic_qty}</div>
        </div>
        <div className="metric">
          <div className="ml">Weeks of cover</div>
          <div className="mv" style={{ color: p.woc == null ? 'var(--faint)' : p.woc < 2 ? 'var(--red)' : p.woc < 4 ? 'var(--amber)' : 'var(--ink)' }}>
            {p.woc != null ? p.woc.toFixed(1) : '—'}
          </div>
          <div className="ms">target 4w · {p.weekly_demand.toFixed(1)} {uom}s/week</div>
        </div>
        <div className="metric">
          <div className="ml">Sales · 120 days</div>
          <div className="mv">{p.sales_120d}</div>
          <div className="ms">{trend.length ? `${trend[trend.length - 1].units} last month` : 'no sales recorded'}</div>
        </div>
      </div>

      <div className="grid">
        {/* Buy side */}
        <div className="col">
          <div className="card">
            <div className="ch">
              <div>
                <div className="ct">Supplier offerings</div>
                <div className="hint">what each supplier charges — cost, packaging, bulk terms</div>
              </div>
              <span className="lnk" style={{ fontSize: 12 }} onClick={() => setManageOpen(true)}>Manage</span>
            </div>
            {offerings.isLoading && <div style={{ padding: 18 }}><Spinner /></div>}
            {offerings.isError && <div style={{ padding: 16, fontSize: 12.5, color: 'var(--red)' }}>{String((offerings.error as Error)?.message)}</div>}
            {offeringRows.map((entry, index) => (
              <OfferingSection
                key={`${entry.supplier_id}-${entry.supplier_sku}`}
                entry={entry}
                link={byId.get(entry.supplier_id)}
                uom={uom}
                onManageTerms={() => setMbbOpen(true)}
                last={index === offeringRows.length - 1}
              />
            ))}
            {!offerings.isLoading && offeringRows.length === 0 && (
              <div style={{ padding: 18, fontSize: 13, color: 'var(--faint)' }}>No suppliers linked to this variant yet.</div>
            )}
          </div>
        </div>

        {/* Sell side + inventory */}
        <div className="col">
          <div className="card">
            <div className="ch">
              <div>
                <div className="ct">Selling items</div>
                <div className="hint">margins at effective cost {money(effectiveCost)}/{uom}</div>
              </div>
            </div>
            <table className="cpptab" style={{ margin: '4px 0 2px' }}>
              <thead>
                <tr><th style={{ paddingLeft: 17 }}>Channel</th><th>Price</th><th>Net margin</th><th style={{ paddingRight: 17 }}>Verdict</th></tr>
              </thead>
              <tbody>
                {p.channels.map(ch => {
                  const margin = marginRows.find(m => m.channel === ch.channel)
                  const net = margin?.basic_margin ?? null
                  const ok = net != null && net >= p.gp_floor
                  return (
                    <tr key={ch.channel}>
                      <td className="cpp-row" style={{ paddingLeft: 17 }}>
                        {ch.channel}
                        <div className="cpp-sub">{ch.is_active ? 'listed' : 'not listed'}{ch.units_per_listing && ch.units_per_listing > 1 ? ` · ×${ch.units_per_listing}/listing` : ''}</div>
                      </td>
                      <td>{money(ch.selling_price)}</td>
                      <td style={{ color: net == null ? 'var(--faint)' : ok ? 'var(--good)' : 'var(--amber)', fontWeight: 650 }}>
                        {net != null ? `${(net * 100).toFixed(1)}%` : '—'}
                        <div className="cpp-sub">gross {ch.gp_pct != null ? `${(ch.gp_pct * 100).toFixed(1)}%` : '—'}</div>
                      </td>
                      <td style={{ paddingRight: 17, color: ok ? 'var(--good)' : 'var(--amber)', fontSize: 11.5 }}>
                        {net == null ? '—' : ok ? 'OK ✓' : 'Raise ⚠'}
                      </td>
                    </tr>
                  )
                })}
                {p.channels.length === 0 && (
                  <tr><td colSpan={4} style={{ padding: 18, color: 'var(--faint)', textAlign: 'center' }}>Not listed on any channel.</td></tr>
                )}
              </tbody>
            </table>
            {p.mbb_unit_cost != null && marginRows.some(ch => ch.mbb_margin != null) && (
              <div style={{ padding: '9px 17px', borderTop: '1px solid var(--line2)', fontSize: 12, color: 'var(--muted)' }}>
                At MBB cost {money(p.mbb_unit_cost)}: {marginRows.filter(ch => ch.mbb_margin != null).map(ch => `${ch.channel} ${(ch.mbb_margin! * 100).toFixed(1)}%`).join(' · ')}
              </div>
            )}
          </div>

          <div className="card">
            <div className="ch">
              <div>
                <div className="ct">Inventory</div>
                <div className="hint">stock · coverage · storage</div>
              </div>
            </div>
            <DefRow label="Stock">{p.warehouse_qty} warehouse · {p.clinic_qty} clinic · <b>{p.total_qty} {uom}s total</b></DefRow>
            <DefRow label="Coverage">
              {p.woc != null ? `${p.woc.toFixed(1)} weeks` : '—'} at {p.weekly_demand.toFixed(1)} {uom}s/week
              {wdbc ? <span style={{ color: 'var(--faint)' }}> (shopify {wdbc.shopify ?? 0} · clinic {wdbc.clinic ?? 0} · hktv {wdbc.hktv ?? 0})</span> : null}
            </DefRow>
            <DefRow label="Storage">{p.storage_rule === 'clinic_only' ? 'clinic only' : 'any location'} · valuation in {uom}s</DefRow>
            <DefRow label="Trend" last>
              <span style={{ display: 'inline-flex', alignItems: 'flex-end', gap: 3, height: 34, verticalAlign: 'bottom' }}>
                {trend.map((t, index) => (
                  <i key={t.month} title={`${t.month}: ${t.units}`} style={{ width: 14, height: `${Math.max(8, (t.units / trendMax) * 100)}%`, background: index === trend.length - 1 ? 'var(--accent)' : 'var(--accent-line)', borderRadius: '2px 2px 0 0' }} />
                ))}
                {trend.length === 0 && <span style={{ color: 'var(--faint)', fontSize: 12.5 }}>No sales recorded.</span>}
              </span>
              <span style={{ color: 'var(--faint)', marginLeft: 10, fontSize: 12 }}>{p.sales_120d} sold /120d</span>
            </DefRow>
          </div>
        </div>
      </div>

      {/* Provenance */}
      <div className="card" style={{ marginTop: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 17px', fontSize: 12.5, color: 'var(--muted)', flexWrap: 'wrap' }}>
          <b style={{ color: 'var(--ink2)' }}>Provenance</b>
          <span>
            cost {SOURCE_CHIP[costSource]?.words ?? costSource}{costSince ? ` · ${fmtDay(costSince)}` : ''}
            {p.uom_verified_at ? ` · pack verified ${fmtDay(p.uom_verified_at)}${p.uom_verified_by ? ` by ${p.uom_verified_by}` : ''}` : ' · pack unverified'}
            {p.last_manual_edit_at ? ` · last manual edit ${fmtDay(p.last_manual_edit_at)}${p.last_manual_edit_by ? ` by ${p.last_manual_edit_by}` : ''}` : ''}
          </span>
          <Link className="lnk" style={{ marginLeft: 'auto', fontSize: 12.5, textDecoration: 'none' }} to={'/admin/audit' as never}>full audit trail →</Link>
        </div>
      </div>

      {manageOpen && <SupplierManagerModal product={p} onSaved={onSaved} onClose={() => setManageOpen(false)} />}
      {mbbOpen && (
        <div style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 60, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '60px 16px' }} onClick={() => setMbbOpen(false)}>
          <div className="skud" onClick={e => e.stopPropagation()} style={{ background: '#fff', borderRadius: 12, maxWidth: 680, width: '100%', maxHeight: '80vh', overflowY: 'auto', padding: '18px 20px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <h2 style={{ fontSize: 17, fontWeight: 700, margin: 0, color: 'var(--ink)' }}>Cost &amp; MBB terms</h2>
              <button className="btn" onClick={() => setMbbOpen(false)}>Close</button>
            </div>
            <CostMbbEditor product={p} onSaved={onSaved} />
          </div>
        </div>
      )}
    </div>
  )
}

function OfferingSection({ entry, link, uom, onManageTerms, last }: {
  entry: OfferingEntry
  link?: Product['all_suppliers'][number]
  uom: string
  onManageTerms: () => void
  last: boolean
}) {
  const [showHistory, setShowHistory] = useState(false)
  const current = entry.current
  const previous = entry.history.find(h => !h.is_current)
  const packSize = entry.packaging?.sellable_units_per_purchase_unit ?? (entry.legacy.units_per_pack && entry.legacy.units_per_pack > 1 ? entry.legacy.units_per_pack : null)
  const unitCost = current?.unit_cost ?? (entry.legacy.basic_cost != null
    ? (entry.legacy.units_per_pack && entry.legacy.units_per_pack > 1 ? entry.legacy.basic_cost / entry.legacy.units_per_pack : entry.legacy.basic_cost)
    : null)
  const packCost = unitCost != null && packSize ? unitCost * packSize : null
  const delta = previous && current ? ((current.unit_cost - previous.unit_cost) / previous.unit_cost) * 100 : null
  const oos = link?.stock_status === 'out_of_stock'
  const auditTo = (current?.run_id ? `/catalogues/review/${current.run_id}` : '/admin/audit') as never
  const terms: MbbTerm[] = link?.mbb_term_list ?? []

  return (
    <div style={{ borderBottom: last ? 'none' : '1px solid var(--line)' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '11px 17px 9px', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13.5, color: 'var(--ink)' }}>{entry.supplier_name ?? '—'}{link?.is_preferred ? ' ★' : ''}</b>
        {entry.supplier_sku && <span className="skucode" style={{ fontSize: 12.5, color: 'var(--muted)' }}>{entry.supplier_sku}</span>}
        <span style={{ marginLeft: 'auto' }}>
          {oos
            ? <span className="bdg warn"><span className="st" />OOS{link?.expected_restock_at ? ` · back ${fmtDay(link.expected_restock_at)}` : ''}</span>
            : <span className="bdg ok"><span className="st" />in stock</span>}
        </span>
      </div>

      <DefRow label="Cost">
        <b style={{ fontSize: 14, fontVariantNumeric: 'tabular-nums' }}>{money(unitCost)}</b> /{current?.basis?.toLowerCase() ?? uom}
        {packCost != null && <span style={{ color: 'var(--muted)' }}> · {money(packCost)} per {entry.packaging?.purchase_uom?.toLowerCase() ?? 'pack'}</span>}
        {' '}<SourceBadge source={entry.source} />
        {delta != null && (
          <span style={{ marginLeft: 6, fontWeight: 700, color: Math.abs(delta) > 20 ? 'var(--red)' : 'var(--amber)', fontSize: 12 }}>
            {delta > 0 ? '↑' : '↓'} {Math.abs(delta).toFixed(1)}% vs previous
          </span>
        )}
      </DefRow>
      <DefRow label="Since">
        {current ? (
          <>
            {fmtDay(current.since) ?? '—'} · {SOURCE_CHIP[current.source].words} ·{' '}
            <Link className="lnk" style={{ fontSize: 12.5, textDecoration: 'none' }} to={auditTo}>audit details →</Link>
          </>
        ) : (
          <span style={{ color: 'var(--muted)' }}>
            {entry.legacy.cost_updated_at ? `${fmtDay(entry.legacy.cost_updated_at)} · ` : ''}pre-domain cost ({entry.legacy.cost_source ?? 'unknown'}) — updates on the next catalogue commit or cost edit
          </span>
        )}
      </DefRow>
      {(entry.packaging || packSize) && (
        <DefRow label="Packaging">
          {entry.packaging
            ? <>{entry.packaging.purchase_uom?.toLowerCase() ?? 'pack'} of {entry.packaging.sellable_units_per_purchase_unit ?? '?'} {entry.packaging.sellable_uom?.toLowerCase() ?? uom}s
              {entry.packaging.content_amount ? <span style={{ color: 'var(--muted)' }}> · {entry.packaging.content_amount} {entry.packaging.content_uom ?? ''} each</span> : null}</>
            : <>pack of {packSize} {uom}s <span style={{ color: 'var(--faint)' }}>(legacy pack size)</span></>}
        </DefRow>
      )}

      {/* Bulk terms — a proper table: what the deal is, what it takes, what a unit lands at. */}
      <div style={{ padding: '9px 17px', borderBottom: '1px solid var(--line2)' }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: terms.length ? 7 : 0 }}>
          <span style={{ fontSize: 11, fontWeight: 650, color: 'var(--faint)', textTransform: 'uppercase', letterSpacing: '.04em' }}>Bulk terms</span>
          <span className="lnk" style={{ fontSize: 12, marginLeft: 'auto' }} onClick={onManageTerms}>{terms.length ? 'Manage' : 'Add terms'}</span>
        </div>
        {terms.length === 0 && <div style={{ fontSize: 12.5, color: 'var(--faint)' }}>No bulk-buy terms for this supplier.</div>}
        {terms.length > 0 && (
          <table className="cpptab">
            <thead>
              <tr><th style={{ textAlign: 'left' }}>Deal</th><th>Requires</th><th>Unit lands at</th><th>Saves</th></tr>
            </thead>
            <tbody>
              {terms.map(t => {
                const saving = t.effective_unit_cost != null && unitCost != null && unitCost > 0
                  ? (1 - t.effective_unit_cost / unitCost) * 100 : null
                return (
                  <tr key={t.id}>
                    <td className="cpp-row">{termDeal(t)}</td>
                    <td>{termRequires(t, uom)}</td>
                    <td style={{ fontWeight: 650 }}>{money(t.effective_unit_cost)}</td>
                    <td style={{ color: saving != null && saving > 0 ? 'var(--good)' : 'var(--faint)' }}>
                      {saving != null ? `−${saving.toFixed(1)}%` : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>

      <DefRow label="History" last={!showHistory}>
        {entry.history.length === 0 && <span style={{ color: 'var(--faint)' }}>no price changes recorded yet</span>}
        {entry.history.length > 0 && (
          <span className="lnk" style={{ fontSize: 12.5 }} onClick={() => setShowHistory(h => !h)}>
            {showHistory ? 'Hide price history ▴' : `Price history (${entry.history.length}) ▾`}
          </span>
        )}
      </DefRow>
      {showHistory && (
        <div style={{ padding: '4px 17px 13px 17px', background: 'var(--panel)' }}>
          {entry.history.map((row, index) => (
            <div key={index} style={{ display: 'flex', gap: 12, alignItems: 'baseline', padding: '6px 0', borderBottom: index === entry.history.length - 1 ? 'none' : '1px solid var(--line2)', fontSize: 12.5 }}>
              <b style={{ fontVariantNumeric: 'tabular-nums', color: row.is_current ? 'var(--good)' : 'var(--ink2)', flex: '0 0 76px' }}>{money(row.unit_cost)}</b>
              <span style={{ color: 'var(--muted)', flex: 1 }}>
                {row.is_current ? `since ${fmtDay(row.since) ?? '—'} · current` : `${fmtDay(row.since) ?? '—'} → ${fmtDay(row.until) ?? '—'}`}
                {' · '}{SOURCE_CHIP[row.source].words}
              </span>
              <Link className="lnk" style={{ fontSize: 12, textDecoration: 'none' }} to={(row.run_id ? `/catalogues/review/${row.run_id}` : '/admin/audit') as never}>audit →</Link>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

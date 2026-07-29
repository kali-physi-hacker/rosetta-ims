// SKU details v2 — the page reorganized around what each domain entity owns:
// variant identity on top, supplier offerings (buy side) beside selling items
// (sell side), inventory across the width, provenance as the footer. Read-only
// comparison route against /items/<sku>; edits stay in the classic view until
// this layout is confirmed. Run IDs and pipeline jargon never render here —
// provenance speaks plain words and links to the audit surfaces instead.
import { createFileRoute, Link, useNavigate, useRouter } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { type CSSProperties } from 'react'
import { C } from '@/lib/tokens'
import { API_BASE } from '@/lib/config'
import { authHeaders } from '@/lib/auth'
import { skuToPath } from '@/lib/sku'
import { Spinner } from '@/components/Spinner'
import type { Product } from '@/lib/types'

export const Route = createFileRoute('/_authed/sku/$')({ component: SkuDetailsV2 })

const MONO = 'ui-monospace, "SF Mono", Menlo, monospace'

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

const pill = (bg: string, color: string): CSSProperties =>
  ({ fontSize: 10, fontWeight: 700, borderRadius: 999, padding: '2px 8px', background: bg, color, whiteSpace: 'nowrap' })
const SOURCE_CHIP: Record<string, { bg: string; color: string; border: string; label: string }> = {
  catalogue: { bg: C.okBg, color: C.green, border: C.okLine, label: 'CATALOGUE' },
  manual: { bg: '#DBEAFE', color: '#1E40AF', border: '#BFDBFE', label: 'MANUAL' },
  legacy: { bg: C.monoBg, color: C.muted, border: C.line, label: 'LEGACY' },
}
function SourceChip({ source }: { source: string }) {
  const s = SOURCE_CHIP[source] ?? SOURCE_CHIP.legacy
  return <span style={{ fontFamily: MONO, fontSize: 10, borderRadius: 4, padding: '1px 6px', background: s.bg, color: s.color, border: `1px solid ${s.border}` }}>{s.label}</span>
}
function LaneHead({ color, children, right }: { color: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.muted, margin: '0 0 6px' }}>
      <i style={{ width: 8, height: 8, borderRadius: 2, background: color, display: 'inline-block' }} />
      {children}
      {right && <span style={{ marginLeft: 'auto', textTransform: 'none', letterSpacing: 0 }}>{right}</span>}
    </div>
  )
}
const card: CSSProperties = { background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }
const kvLabel: CSSProperties = { padding: '5px 12px', borderBottom: `1px solid ${C.monoBg}`, color: C.muted, fontSize: 12 }
const kvValue: CSSProperties = { padding: '5px 12px', borderBottom: `1px solid ${C.monoBg}`, color: C.ink, fontSize: 12 }

function SkuDetailsV2() {
  const rawSplat = Route.useParams({ select: p => p._splat })
  const sku = (rawSplat ?? '').split('/').map(decodeURIComponent).join('/')
  const router = useRouter()
  const navigate = useNavigate()
  const goBack = () => { if (typeof window !== 'undefined' && window.history.length > 1) router.history.back(); else navigate({ to: '/' as never }) }

  const product = useQuery({ queryKey: ['sku-v2', sku], queryFn: () => getJson<Product>(`/products/${skuToPath(sku)}`) })
  const offerings = useQuery({
    queryKey: ['sku-v2-offerings', sku],
    queryFn: () => getJson<{ offerings: OfferingEntry[] }>(`/products/${skuToPath(sku)}/offerings`),
  })

  if (product.isLoading) return <div style={{ padding: 40 }}><Spinner /></div>
  if (product.isError) {
    return (
      <div style={{ padding: 40, textAlign: 'center', color: C.redInk, fontSize: 13 }}>
        {String((product.error as Error)?.message ?? product.error)}
        <br /><Link to={'/' as never} style={{ color: C.indigo, fontSize: 12, marginTop: 8, display: 'inline-block' }}>← Back to Inventory</Link>
      </div>
    )
  }
  const p = product.data!
  const offeringRows = offerings.data?.offerings ?? []
  const byId = new Map(p.all_suppliers.map(s => [s.supplier_id, s]))
  const preferred = p.all_suppliers.find(s => s.is_preferred) ?? p.all_suppliers[0] ?? null
  const preferredOffering = offeringRows.find(o => o.supplier_id === preferred?.supplier_id) ?? offeringRows[0] ?? null
  const uom = p.uom ?? 'unit'
  const effectiveCost = p.unit_cost   // offering-first server-side since Phase 1
  const mr = p.margin_range
  const marginRows = (mr?.channels ?? []).filter(ch => p.channels.some(c => c.channel === ch.channel))
  const bestMargin = marginRows.reduce<{ channel: string; margin: number } | null>(
    (best, ch) => (ch.basic_margin != null && (!best || ch.basic_margin > best.margin) ? { channel: ch.channel, margin: ch.basic_margin } : best),
    null,
  )
  const mbbLine = mr && p.mbb_unit_cost != null
    ? marginRows.filter(ch => ch.mbb_margin != null).map(ch => `${ch.channel} ${(ch.mbb_margin! * 100).toFixed(1)}%`).join(' · ')
    : null
  const trend = p.sales_trend ?? []
  const trendMax = Math.max(...trend.map(t => t.units), 1)
  const wdbc = p.weekly_demand_by_channel
  const costSince = preferredOffering?.current?.since ?? p.cost_updated_at
  const costSource = preferredOffering?.source ?? (preferred?.cost_source_effective === 'offering' ? 'catalogue' : 'legacy')

  return (
    <div style={{ padding: 24, maxWidth: 1180 }}>
      {/* Identity */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
            <button onClick={goBack} style={{ border: 'none', background: 'none', color: C.muted, fontSize: 12, cursor: 'pointer', padding: 0 }}>←</button>
            <h1 style={{ fontSize: 16.5, fontWeight: 800, color: C.ink, margin: 0, letterSpacing: '-0.01em' }}>{p.name}</h1>
          </div>
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 6, alignItems: 'center' }}>
            <span style={{ fontFamily: MONO, fontSize: 11.5, color: C.muted }}>SKU {p.sku_code}</span>
            {p.brand && <span style={pill(C.monoBg, C.sub)}>{p.brand}</span>}
            <span style={pill(C.monoBg, C.sub)}>{p.category}{p.species ? ` · ${p.species}` : ''}</span>
            <span style={pill(p.status === 'ACTIVE' ? C.greenBg : C.redBg, p.status === 'ACTIVE' ? C.green : C.redInk)}>{p.status}</span>
            <span style={pill(C.monoBg, C.sub)}>data grade {p.data_grade}</span>
          </div>
        </div>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
          <Link
            to={'/items/$' as never}
            params={{ _splat: skuToPath(p.sku_code) } as never}
            style={{ border: `1px solid ${C.knobOff}`, background: C.panel, borderRadius: 6, padding: '6px 11px', fontSize: 11.5, fontWeight: 600, color: C.ink, textDecoration: 'none' }}
          >
            Classic view (edits) →
          </Link>
        </span>
      </div>

      {/* KPI band */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginTop: 14 }}>
        <div style={{ ...card, padding: '10px 12px' }}>
          <div style={{ fontSize: 17, fontWeight: 800, fontVariantNumeric: 'tabular-nums', color: C.ink }}>
            {money(effectiveCost)} <span style={{ fontSize: 10.5, color: C.muted, fontWeight: 400 }}>HKD / {uom}</span>
          </div>
          <div style={{ fontSize: 10.5, color: C.muted, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
            effective unit cost · <SourceChip source={costSource} />{costSince ? <span>{fmtDay(costSince)}</span> : null}
          </div>
        </div>
        <div style={{ ...card, padding: '10px 12px' }}>
          <div style={{ fontSize: 17, fontWeight: 800, fontVariantNumeric: 'tabular-nums', color: bestMargin && bestMargin.margin >= p.gp_floor ? C.ok : C.amber }}>
            {bestMargin ? `${(bestMargin.margin * 100).toFixed(1)}%` : '—'}
          </div>
          <div style={{ fontSize: 10.5, color: C.muted }}>best net margin{bestMargin ? ` (${bestMargin.channel})` : ''} · floor {(p.gp_floor * 100).toFixed(0)}%</div>
        </div>
        <div style={{ ...card, padding: '10px 12px' }}>
          <div style={{ fontSize: 17, fontWeight: 800, fontVariantNumeric: 'tabular-nums', color: p.woc == null ? C.muted : p.woc < 2 ? C.bad : p.woc < 4 ? C.amber : C.ink }}>
            {p.woc != null ? `${p.woc.toFixed(1)} w` : '—'}
          </div>
          <div style={{ fontSize: 10.5, color: C.muted }}>weeks of cover · target 4w</div>
        </div>
        <div style={{ ...card, padding: '10px 12px' }}>
          <div style={{ fontSize: 17, fontWeight: 800, fontVariantNumeric: 'tabular-nums', color: C.ink }}>{p.total_qty} {uom}s</div>
          <div style={{ fontSize: 10.5, color: C.muted }}>stock on hand · warehouse {p.warehouse_qty} · clinic {p.clinic_qty}</div>
        </div>
      </div>

      {/* Buy / Sell lanes */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12, marginTop: 16, alignItems: 'start' }}>
        <div>
          <LaneHead color={C.amber}>Supplier offerings</LaneHead>
          {offerings.isLoading && <div style={{ ...card, padding: 16 }}><Spinner /></div>}
          {offerings.isError && <div style={{ ...card, padding: 12, fontSize: 12, color: C.redInk }}>{String((offerings.error as Error)?.message)}</div>}
          {offeringRows.map(entry => (
            <OfferingCard key={`${entry.supplier_id}-${entry.supplier_sku}`} entry={entry} link={byId.get(entry.supplier_id)} uom={uom} />
          ))}
          {!offerings.isLoading && offeringRows.length === 0 && (
            <div style={{ ...card, padding: 16, fontSize: 12.5, color: C.muted }}>No suppliers linked to this variant.</div>
          )}
        </div>

        <div>
          <LaneHead color={C.ok} right={<span style={{ fontFamily: MONO, fontSize: 10, color: C.muted }}>margins at effective cost {money(effectiveCost)}</span>}>
            Selling items
          </LaneHead>
          <div style={card}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: 12 }}>
              <thead>
                <tr style={{ textAlign: 'left' }}>
                  {['Channel', 'Status', 'Price', 'Net margin', ''].map(h => (
                    <th key={h} style={{ padding: '6px 12px', borderBottom: `1px solid ${C.monoBg}`, fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: C.muted }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {p.channels.map(ch => {
                  const margin = marginRows.find(m => m.channel === ch.channel)
                  const net = margin?.basic_margin ?? null
                  const ok = net != null && net >= p.gp_floor
                  return (
                    <tr key={ch.channel}>
                      <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.monoBg}`, color: C.ink }}>{ch.channel}</td>
                      <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.monoBg}` }}>
                        <span style={pill(ch.is_active ? C.greenBg : C.monoBg, ch.is_active ? C.green : C.muted)}>{ch.is_active ? 'active' : 'off'}</span>
                        {ch.units_per_listing != null && ch.units_per_listing > 1 && (
                          <span style={{ fontFamily: MONO, fontSize: 10, color: C.muted, marginLeft: 6 }}>×{ch.units_per_listing}/listing</span>
                        )}
                      </td>
                      <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.monoBg}`, fontFamily: MONO, fontSize: 11.5, fontVariantNumeric: 'tabular-nums' }}>{money(ch.selling_price)}</td>
                      <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.monoBg}`, fontFamily: MONO, fontSize: 11.5, color: net == null ? C.muted : ok ? C.ok : C.amber }}>
                        {net != null ? `${(net * 100).toFixed(1)}%` : '—'}
                        {ch.gp_pct != null && <span style={{ color: C.faint }}> · gross {(ch.gp_pct * 100).toFixed(1)}%</span>}
                      </td>
                      <td style={{ padding: '6px 12px', borderBottom: `1px solid ${C.monoBg}`, fontFamily: MONO, fontSize: 11, color: ok ? C.green : C.amberInk }}>
                        {net == null ? '' : ok ? '✓' : `Raise price ⚠ floor ${(p.gp_floor * 100).toFixed(0)}%`}
                      </td>
                    </tr>
                  )
                })}
                {p.channels.length === 0 && (
                  <tr><td colSpan={5} style={{ padding: 16, color: C.muted, fontSize: 12.5, textAlign: 'center' }}>Not listed on any channel.</td></tr>
                )}
              </tbody>
            </table>
            {mbbLine && (
              <div style={{ padding: '8px 12px', borderTop: `1px solid ${C.monoBg}`, fontSize: 11, color: C.muted }}>
                At MBB cost {money(p.mbb_unit_cost)}: {mbbLine}
                {mr?.mbb_min_qty ? ` — reach it at ${mr.mbb_min_qty}+ ${uom}s${mr.mbb_weeks_cover ? ` (${mr.mbb_weeks_cover} weeks of demand)` : ''}` : ''}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Inventory lane */}
      <div style={{ marginTop: 16 }}>
        <LaneHead color="#2563EB">Inventory</LaneHead>
        <div style={{ display: 'grid', gridTemplateColumns: '1.3fr 1fr', gap: 12, alignItems: 'start' }}>
          <div style={card}>
            <div style={{ display: 'grid', gridTemplateColumns: '118px 1fr' }}>
              <div style={kvLabel}>Stock</div>
              <div style={{ ...kvValue, fontFamily: MONO, fontSize: 11.5 }}>warehouse {p.warehouse_qty} · clinic {p.clinic_qty} · total {p.total_qty} {uom}s</div>
              <div style={kvLabel}>Coverage</div>
              <div style={{ ...kvValue, fontFamily: MONO, fontSize: 11.5 }}>
                {p.woc != null ? `${p.woc.toFixed(1)} weeks` : '—'} at {p.weekly_demand.toFixed(1)} {uom}s/week
                {wdbc ? ` (shopify ${wdbc.shopify ?? 0} · clinic ${wdbc.clinic ?? 0} · hktv ${wdbc.hktv ?? 0})` : ''}
              </div>
              <div style={kvLabel}>Storage</div>
              <div style={{ ...kvValue, fontFamily: MONO, fontSize: 11.5 }}>{p.storage_rule === 'clinic_only' ? 'clinic only' : 'any location'} · valuation UOM: {uom}</div>
              <div style={{ ...kvLabel, borderBottom: 'none' }}>Pack</div>
              <div style={{ ...kvValue, borderBottom: 'none', fontFamily: MONO, fontSize: 11.5 }}>
                {p.units_per_pack && p.units_per_pack > 1 ? `${p.pack_unit ?? 'pack'} of ${p.units_per_pack} ${uom}s` : `sold per ${uom}`}
                {p.uom_verified_at ? ` · verified ${fmtDay(p.uom_verified_at)}${p.uom_verified_by ? ` by ${p.uom_verified_by}` : ''}` : ' · unverified'}
              </div>
            </div>
          </div>
          <div style={{ ...card, padding: '10px 12px' }}>
            <div style={{ fontSize: 10.5, color: C.muted, marginBottom: 6 }}>SALES TREND · {trend.length ? `${trend.length} mo` : 'no data'} · {p.sales_120d} sold /120d</div>
            <div style={{ display: 'flex', alignItems: 'flex-end', gap: 4, height: 48 }}>
              {trend.map((t, index) => (
                <i key={t.month} title={`${t.month}: ${t.units}`} style={{ flex: 1, height: `${Math.max(6, (t.units / trendMax) * 100)}%`, background: index === trend.length - 1 ? C.indigoStrong : C.indigoLine, borderRadius: '2px 2px 0 0' }} />
              ))}
              {trend.length === 0 && <span style={{ fontSize: 11.5, color: C.faint }}>No sales recorded.</span>}
            </div>
          </div>
        </div>
      </div>

      {/* Provenance footer */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 16, ...card, padding: '8px 12px', fontSize: 11.5, color: C.sub, flexWrap: 'wrap' }}>
        <i style={{ width: 8, height: 8, borderRadius: 2, background: C.muted, display: 'inline-block' }} />
        <b style={{ color: C.ink }}>Provenance</b>
        <span>
          cost {costSource === 'catalogue' ? 'from a committed catalogue' : costSource === 'manual' ? 'set manually' : `from legacy ${p.cost_source}`}{costSince ? ` · ${fmtDay(costSince)}` : ''}
          {p.uom_verified_at ? ` · pack verified ${fmtDay(p.uom_verified_at)}` : ''}
          {p.last_manual_edit_at ? ` · last manual edit ${fmtDay(p.last_manual_edit_at)}${p.last_manual_edit_by ? ` by ${p.last_manual_edit_by}` : ''}` : ''}
        </span>
        <Link to={'/admin/audit' as never} style={{ marginLeft: 'auto', fontSize: 11.5, fontWeight: 650, color: C.indigoStrong, textDecoration: 'none' }}>full audit trail →</Link>
      </div>
    </div>
  )
}

function OfferingCard({ entry, link, uom }: {
  entry: OfferingEntry
  link?: Product['all_suppliers'][number]
  uom: string
}) {
  const current = entry.current
  const previous = entry.history.find(h => !h.is_current)
  const packSize = entry.packaging?.sellable_units_per_purchase_unit ?? (entry.legacy.units_per_pack && entry.legacy.units_per_pack > 1 ? entry.legacy.units_per_pack : null)
  const unitCost = current?.unit_cost ?? (entry.legacy.basic_cost != null
    ? (entry.legacy.units_per_pack && entry.legacy.units_per_pack > 1 ? entry.legacy.basic_cost / entry.legacy.units_per_pack : entry.legacy.basic_cost)
    : null)
  const packCost = unitCost != null && packSize ? unitCost * packSize : null
  const delta = previous && current ? ((current.unit_cost - previous.unit_cost) / previous.unit_cost) * 100 : null
  const oos = link?.stock_status === 'out_of_stock'
  const auditTo = current?.run_id ? `/catalogues/review/${current.run_id}` : '/admin/audit'
  const mbbBest = (link?.mbb_term_list ?? []).reduce<number | null>(
    (best, t) => (t.effective_unit_cost != null && (best == null || t.effective_unit_cost < best) ? t.effective_unit_cost : best), null)

  return (
    <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, marginBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${C.monoBg}`, flexWrap: 'wrap' }}>
        <b style={{ fontSize: 12.5, color: C.ink }}>{entry.supplier_name ?? '—'}{link?.is_preferred ? ' ★' : ''}</b>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>
          {entry.supplier_sku ? `SKU ${entry.supplier_sku}` : ''}{entry.barcode ? ` · ${entry.barcode}` : ''}
        </span>
        <span style={{ marginLeft: 'auto' }}>
          {oos
            ? <span style={pill(C.amberBg, C.amberInk)}>OOS{link?.expected_restock_at ? ` · restock ${fmtDay(link.expected_restock_at)}` : ''}</span>
            : <span style={pill(C.greenBg, C.green)}>in stock</span>}
        </span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: '118px 1fr' }}>
        <div style={kvLabel}>Current cost</div>
        <div style={kvValue}>
          <b style={{ fontFamily: MONO, fontSize: 11.5 }}>{money(unitCost)} HKD / {current?.basis?.toLowerCase() ?? uom}</b>
          {packCost != null && <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}> · {money(packCost)} / {entry.packaging?.purchase_uom?.toLowerCase() ?? 'pack'}</span>}
          {' '}<SourceChip source={entry.source} />
        </div>
        <div style={kvLabel}>Since</div>
        <div style={{ ...kvValue, fontFamily: MONO, fontSize: 11 }}>
          {current ? <>
            {fmtDay(current.since) ?? '—'} · {current.source === 'catalogue' ? 'from a committed catalogue' : 'manual edit'} ·{' '}
            <Link to={auditTo as never} style={{ color: C.indigoInk, textDecoration: 'none' }}>audit details →</Link>
          </> : <>
            {entry.legacy.cost_updated_at ? `${fmtDay(entry.legacy.cost_updated_at)} · ` : ''}pre-domain ({entry.legacy.cost_source ?? 'unknown'}) — flips on first catalogue commit or cost edit
          </>}
        </div>
        {previous && (
          <>
            <div style={kvLabel}>Previous</div>
            <div style={{ ...kvValue, fontFamily: MONO, fontSize: 11, color: C.muted }}>
              {money(previous.unit_cost)} ({previous.source}{previous.until ? ` · until ${fmtDay(previous.until)}` : ''})
              {delta != null && <span style={{ color: Math.abs(delta) > 20 ? C.bad : C.amber, fontWeight: 700 }}> · {delta > 0 ? '↑' : '↓'} {Math.abs(delta).toFixed(1)}%</span>}
              {entry.history.length > 1 && <span style={{ color: C.indigoInk }}> · history ({entry.history.length})</span>}
            </div>
          </>
        )}
        {(entry.packaging || packSize) && (
          <>
            <div style={kvLabel}>Packaging</div>
            <div style={{ ...kvValue, fontFamily: MONO, fontSize: 11 }}>
              {entry.packaging
                ? `${entry.packaging.purchase_uom?.toLowerCase() ?? 'pack'} of ${entry.packaging.sellable_units_per_purchase_unit ?? '?'} ${entry.packaging.sellable_uom?.toLowerCase() ?? uom}s${entry.packaging.content_amount ? ` · ${entry.packaging.content_amount} ${entry.packaging.content_uom ?? ''} each` : ''}${current?.basis ? ` · price basis: ${current.basis.toLowerCase()}` : ''}`
                : `pack of ${packSize} ${uom}s (legacy pack size)`}
            </div>
          </>
        )}
        {(link?.mbb_term_list?.length ?? 0) > 0 && (
          <>
            <div style={kvLabel}>MBB</div>
            <div style={{ ...kvValue, fontFamily: MONO, fontSize: 11 }}>
              {(link!.mbb_term_list[0].note ?? link!.mbb_term_list[0].kind)}
              {mbbBest != null && <> → <b>{money(mbbBest)} / {uom}</b></>}
              {link!.mbb_term_list.length > 1 && ` · +${link!.mbb_term_list.length - 1} more`}
            </div>
          </>
        )}
        <div style={{ ...kvLabel, borderBottom: 'none' }}>Ordering</div>
        <div style={{ ...kvValue, borderBottom: 'none', fontFamily: MONO, fontSize: 11 }}>
          {[
            link?.units_per_pack && link.units_per_pack > 1 ? `pack of ${link.units_per_pack}` : null,
          ].filter(Boolean).join(' · ') || 'no ordering terms recorded'}
        </div>
      </div>
    </div>
  )
}

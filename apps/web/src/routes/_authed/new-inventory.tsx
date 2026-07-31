// Inventory rev-02 — scope first, attention second, table third.
//
// The catalogue is 11k variants of which a few hundred are operational, so the
// page opens on *stocked or moving* (holds stock, or sold recently) and
// keeps the archive one click away. Attention counts are computed inside the
// scope, which is what makes them mean anything. Three fitting views replace
// the 18-column table plus its special-case Margins mode, and every row
// carries one state verdict instead of asking you to read four columns and
// know the thresholds.
//
// Data still streams client-side (instant filtering + client CSV export); the
// only new API dependency is sales_120d / data_grade / cost_source, now in the
// stream serializer.
import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react'
import { API_BASE } from '@/lib/config'
import { authHeaders, can } from '@/lib/auth'
import { skuToPath } from '@/lib/sku'
import { INV_CSS } from '@/lib/invCss'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'
import type { Product, Supplier } from '@/lib/types'

export const Route = createFileRoute('/_authed/new-inventory')({ component: NewInventoryPage })

const API = API_BASE

// ── domain ────────────────────────────────────────────────────────────────
type ScopeId = 'working' | 'active' | 'all'
type ViewId = 'ops' | 'money' | 'dq'
type AttentionId = 'low_cover' | 'below_floor' | 'supplier_out' | 'no_cost' | 'expiring'

/** The three scopes nest: stocked-or-moving ⊂ active ⊂ everything. The labels
 *  name the rule rather than a category — "Working set" and "Active" both read
 *  as "the live stuff", which hid the fact that one sits inside the other. */
const SCOPES: {
  id: ScopeId; label: string; blurb: string
  rule: string; delta: (d: ScopeCounts) => string; use: string
}[] = [
  {
    id: 'working', label: 'Stocked or moving', blurb: 'active, and holding stock or selling',
    rule: 'Active SKUs that hold stock, sold in the last 120 days, or carry weekly demand.',
    delta: d => `Hides ${d.dormant.toLocaleString()} active-but-dormant and ${d.inactive.toLocaleString()} inactive rows.`,
    use: 'The daily queue. Every attention count is measured against this set.',
  },
  {
    id: 'active', label: 'All active', blurb: 'status ACTIVE, dormant rows included',
    rule: 'Every SKU with status ACTIVE, whether or not it moves.',
    delta: d => `Adds ${d.dormant.toLocaleString()} dormant SKUs — no stock, no sales, no demand.`,
    use: 'When a SKU you expect is missing. Usually one that has never been stocked.',
  },
  {
    id: 'all', label: 'Whole catalogue', blurb: 'every variant, archive included',
    rule: 'Every variant on record, including inactive and discontinued.',
    delta: d => `Adds ${d.inactive.toLocaleString()} inactive rows — the archive.`,
    use: 'Looking something up by name or SKU. Attention counts switch off here.',
  },
]
type ScopeCounts = { working: number; active: number; all: number; dormant: number; inactive: number }
const VIEWS: { id: ViewId; label: string }[] = [
  { id: 'ops', label: 'Operations' },
  { id: 'money', label: 'Money' },
  { id: 'dq', label: 'Data quality' },
]

const isListed = (p: Product) => (p.channels ?? []).some(c => c.is_active && c.selling_price != null)
/** The operational catalogue: an active SKU that holds stock or actually moves.
 *  Listing alone is not a signal here — 9,096 of 11,159 rows still carry a
 *  channel price, including the whole archive, so it would let everything in. */
const inWorkingSet = (p: Product) =>
  p.status === 'ACTIVE' && ((p.total_qty ?? 0) > 0 || (p.sales_120d ?? 0) > 0 || (p.weekly_demand ?? 0) > 0)

const oosSuppliers = (p: Product) => (p.all_suppliers ?? []).filter(s => s.stock_status === 'out_of_stock')

// Batch expiry. Expired stock is a write-off; expiring stock is still sellable
// if it moves now — never merge the two into one "under 90 days" number.
const EXPIRY_SOON_DAYS = 90
const expiryDays = (p: Product) => p.expiry_days ?? null
const hasExpired = (p: Product) => (p.expiry_days ?? 1) < 0
const isExpiring = (p: Product) => { const d = p.expiry_days; return d != null && d >= 0 && d < EXPIRY_SOON_DAYS }

// Which supplier the row speaks for: the preferred link, else the first named one.
// `+N` says there are alternates; the dot says the preferred one can't ship, and
// whether anyone else can.
function supplierSummary(p: Product) {
  const named = (p.all_suppliers ?? []).filter(s => s.name)
  const pref = (p.all_suppliers ?? []).find(s => s.is_preferred && s.name) ?? named[0] ?? null
  return {
    pref,
    extra: Math.max(0, named.length - 1),
    prefOos: pref?.stock_status === 'out_of_stock',
    hasAlt: (p.all_suppliers ?? []).some(s => s.stock_status !== 'out_of_stock'),
  }
}
const worstMargin = (p: Product): number | null => {
  const live = (p.channels ?? []).filter(c => c.is_active && c.gp_pct != null)
  return live.length ? Math.min(...live.map(c => c.gp_pct as number)) : null
}
const belowFloor = (p: Product) => {
  const m = worstMargin(p)
  return m != null && m < (p.gp_floor ?? 0)
}
const hasCost = (p: Product) => p.primary_cost != null || p.unit_cost != null

/** One verdict per row — first match wins, so a row never shows two problems. */
type RowState = { key: string; label: string; tone: 'bad' | 'warn' | 'neu' | 'ok'; rank: number }
function rowState(p: Product): RowState {
  const sells = isListed(p) || (p.sales_120d ?? 0) > 0
  if (sells && (p.total_qty ?? 0) <= 0) return { key: 'oos', label: 'out of stock', tone: 'bad', rank: 0 }
  if (hasExpired(p) && (p.total_qty ?? 0) > 0) return { key: 'expired', label: 'expired stock', tone: 'bad', rank: 1 }
  if (p.woc != null && p.woc < 2) return { key: 'low_cover', label: `${p.woc.toFixed(1)}w cover`, tone: 'bad', rank: 2 }
  // Expiring stock outranks a margin miss: a few points of GP is recoverable,
  // a batch that dates out is a total loss on a fixed deadline.
  if (isExpiring(p) && (p.total_qty ?? 0) > 0)
    return { key: 'expiring', label: `expires in ${p.expiry_days}d`, tone: 'warn', rank: 3 }
  if (belowFloor(p)) return { key: 'below_floor', label: 'below floor', tone: 'warn', rank: 4 }
  if (oosSuppliers(p).length > 0) {
    const back = oosSuppliers(p)[0]?.expected_restock_at
    return { key: 'supplier_out', label: back ? `supplier out · back ${fmtDay(back)}` : 'supplier out', tone: 'warn', rank: 5 }
  }
  if (sells && !hasCost(p)) return { key: 'no_cost', label: 'no cost', tone: 'warn', rank: 6 }
  if (p.data_grade === 'C' || (p.units_per_pack != null && !p.uom_verified_at && sells))
    return { key: 'check', label: 'check data', tone: 'neu', rank: 7 }
  return { key: 'ok', label: 'ok', tone: 'ok', rank: 8 }
}

const money = (v: number | null | undefined, d = 2) =>
  v == null ? '—' : v.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d })
const pct = (v: number | null | undefined) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`)
/** Backend timestamps come in three shapes: date-only, naive datetime, and
 *  zoned. Normalize to UTC without corrupting the ones that already have a
 *  time component. */
const parseStamp = (iso: string | null | undefined): Date | null => {
  if (!iso) return null
  const zoned = /[zZ]|[+-]\d\d:?\d\d$/.test(iso)
  const d = new Date(zoned ? iso : iso.includes('T') ? `${iso}Z` : `${iso}T00:00:00Z`)
  return isNaN(d.getTime()) ? null : d
}
const fmtDay = (iso: string | null | undefined) => {
  const d = parseStamp(iso)
  return d ? d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) : ''
}
const daysSince = (iso: string | null | undefined) => {
  const d = parseStamp(iso)
  return d ? Math.max(0, Math.round((Date.now() - d.getTime()) / 86400000)) : null
}

// ── selection (isolated store so row toggles don't re-render the table) ────
const selectionStore = (() => {
  let sel = new Set<string>()
  const listeners = new Set<() => void>()
  const emit = () => listeners.forEach(l => l())
  return {
    subscribe: (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } },
    has: (sku: string) => sel.has(sku),
    size: () => sel.size,
    list: () => [...sel],
    toggle: (sku: string) => { sel = new Set(sel); if (sel.has(sku)) sel.delete(sku); else sel.add(sku); emit() },
    setMany: (skus: string[], on: boolean) => { sel = new Set(sel); for (const s of skus) { if (on) sel.add(s); else sel.delete(s) } emit() },
    clear: () => { if (sel.size) { sel = new Set(); emit() } },
  }
})()
const useSelHas = (sku: string) => useSyncExternalStore(selectionStore.subscribe, () => selectionStore.has(sku), () => false)
const useSelSize = () => useSyncExternalStore(selectionStore.subscribe, () => selectionStore.size(), () => 0)

function SelCheckbox({ sku }: { sku: string }) {
  const on = useSelHas(sku)
  return <input type="checkbox" checked={on} onChange={() => selectionStore.toggle(sku)} onClick={e => e.stopPropagation()} aria-label={`Select ${sku}`} />
}


// ── hover cards ───────────────────────────────────────────────────────────
// A summary cell shows the headline number; hovering reveals what's behind it
// (bulk tiers, the supplier list, the stock split, per-channel margin). These
// carry the detail the old page kept in extra columns, without the width.
/** One row of GET /products/margins.json, keyed by sku on the response. */
type MarginRow = {
  basic_cost?: number | null
  mbb_cost?: number | null
  cost_to_hit?: number | null
  gp_floor?: number | null
  ch?: Record<string, { price?: number | null; nb?: number | null; nm?: number | null }>
}

type PopKind = 'suppliers' | 'channels' | 'cover' | 'bulk' | 'stock'
  | 'state' | 'demand' | 'unitcost' | 'costtohit' | 'grade' | 'costsrc' | 'expiry'
type PopState = { kind: PopKind; item: Product; margin?: MarginRow; x: number; y: number } | null

const FEE: Record<string, number> = { clinic: 0, shopify: 0.029, hktv: 0.15 }
const LOGI: Record<string, number> = { clinic: 0, shopify: 0.9, hktv: 0.6 }

// One entry per rowState verdict: the numbers that triggered it, and the move
// that clears it. Keep these in step with rowState() above.
const STATE_WHY: Record<string, { what: (p: Product) => string; fix: string }> = {
  oos: {
    what: p => `Nothing on hand${(p.sales_120d ?? 0) > 0 ? ` while it still moves ~${fmtRate(p.weekly_demand ?? 0)}/wk` : ' and it is listed for sale'}.`,
    fix: 'Raise a purchase order, or take it off the channels it can’t serve.',
  },
  low_cover: {
    what: p => `${(p.woc ?? 0).toFixed(1)} weeks of cover left at ~${fmtRate(p.weekly_demand ?? 0)}/wk — under the 2-week reorder line.`,
    fix: 'Hover the cover cell for how much to buy to reach 4 or 8 weeks.',
  },
  below_floor: {
    what: p => {
      const m = worstMargin(p)
      return `Its weakest live channel returns ${m == null ? '—' : (m * 100).toFixed(1)}%, under the ${((p.gp_floor ?? 0) * 100).toFixed(0)}% floor.`
    },
    fix: 'Either the cost comes down or the price goes up — the margin card shows which channels bleed.',
  },
  supplier_out: {
    what: p => {
      const out = oosSuppliers(p)
      return `${out.length} of ${(p.all_suppliers ?? []).length} suppliers can’t ship${out[0]?.expected_restock_at ? `; the first is back ${fmtDay(out[0].expected_restock_at)}` : ''}.`
    },
    fix: 'Buy from an alternate, or wait for restock if cover allows.',
  },
  expired: {
    what: p => {
      const b = (p.expiry_batches ?? []).filter(x => x.days < 0)
      const q = b.reduce((n, x) => n + (x.qty ?? 0), 0)
      return `${b.length} batch${b.length === 1 ? '' : 'es'}${q > 0 ? ` (${q.toLocaleString()} units)` : ''} lapsed, the oldest ${Math.abs(p.expiry_days ?? 0)} days ago, and stock is still on hand.`
    },
    fix: 'Write it off and pull it from sale — it cannot ship.',
  },
  expiring: {
    what: p => {
      const b = (p.expiry_batches ?? []).filter(x => x.days >= 0 && x.days < EXPIRY_SOON_DAYS)
      const q = b.reduce((n, x) => n + (x.qty ?? 0), 0)
      return `${q > 0 ? `${q.toLocaleString()} units date` : 'A batch dates'} out in ${p.expiry_days} days at ~${fmtRate(p.weekly_demand ?? 0)}/wk.`
    },
    fix: 'Discount, promote, or move it to a channel that will clear it in time.',
  },
  no_cost: {
    what: () => 'It sells, but no supplier price is on record — every margin on this row is unknown.',
    fix: 'Add a supplier cost, or run it through a catalogue ingestion.',
  },
  check: {
    what: p => p.data_grade === 'C'
      ? 'Grade C — something a trading decision depends on is missing.'
      : 'Pack size has never been verified, so the per-unit cost is unconfirmed.',
    fix: 'Open the SKU and fill the gap; the Data quality view lists what’s missing.',
  },
  ok: {
    what: () => 'Stocked, covered, priced above the floor, and its data checks out.',
    fix: 'No action needed.',
  },
}

const primarySupplier = (p: Product) =>
  (p.all_suppliers ?? []).find(sp => sp.is_primary) ?? (p.all_suppliers ?? [])[0]

/** The bulk term the headline mbb_unit_cost came from — the cheapest one. */
const bestTerm = (p: Product) => {
  const terms = (primarySupplier(p)?.mbb_term_list ?? []).filter(t => t.effective_unit_cost != null)
  if (!terms.length) return null
  return terms.reduce((a, b) => ((b.effective_unit_cost as number) < (a.effective_unit_cost as number) ? b : a))
}

const termLabel = (t: ReturnType<typeof bestTerm>, uom: string): string | null => {
  if (!t) return null
  if (t.kind === 'buy_x_get_y') return `Buy ${t.min_qty ?? '?'} get ${t.free_qty ?? '?'} free`
  if (t.kind === 'spend_discount') return `Spend ${t.min_spend != null ? money(t.min_spend) : '?'} → ${t.discount_pct != null ? (t.discount_pct * 100).toFixed(0) : '?'}% off`
  return t.min_qty && t.min_qty > 1 ? `${t.min_qty}+ ${plural(uom)}` : `Flat ${uom} price`
}

// A check the row either passes or fails, rendered as a ✓/✗ line.
function Check({ ok, label, detail }: { ok: boolean; label: string; detail?: string }) {
  return (
    <div className="prow">
      <span className="pk"><b>{label}</b>{detail && <div className="pmeta">{detail}</div>}</span>
      <span className="pv" style={{ color: ok ? 'var(--good)' : 'var(--red)' }}>{ok ? '✓' : '✗'}</span>
    </div>
  )
}

function PopBody({ kind, item, margin }: { kind: PopKind; item: Product; margin?: MarginRow }) {
  const uom = item.uom ?? 'unit'
  if (kind === 'suppliers') {
    const costs = (item.all_suppliers ?? []).map(s => s.basic_cost).filter((v): v is number => v != null)
    const lo = costs.length ? Math.min(...costs) : null
    const out = oosSuppliers(item)
    return <>
      <div className="ph">Suppliers · pack cost</div>
      <div className="pb">
        {(item.all_suppliers ?? []).length === 0 && <div className="sub">No supplier linked yet.</div>}
        {(item.all_suppliers ?? []).map(sp => (
          <div className="prow" key={sp.id}>
            <span className="pk">
              <b>{sp.name ?? 'Supplier record missing'}</b>
              {sp.is_preferred && <span className="low" style={{ background: 'var(--accent-soft)', color: 'var(--accent-ink)' }}>preferred</span>}
              <div className="pmeta">{[sp.code, sp.supplier_sku && `SKU ${sp.supplier_sku}`].filter(Boolean).join(' · ') || (sp.name ? '' : 'this link points at no supplier')}</div>
              <div style={{ marginTop: 3 }}>
                {sp.stock_status === 'out_of_stock'
                  ? <span className="chip bad">out{sp.expected_restock_at ? ` · back ${fmtDay(sp.expected_restock_at)}` : ''}</span>
                  : <span className="chip ok">in stock</span>}
              </div>
            </span>
            <span className="pv">{sp.basic_cost != null ? money(sp.basic_cost) : '—'}
              {lo != null && sp.basic_cost === lo && (item.all_suppliers ?? []).length > 1 && <span className="low">lowest</span>}</span>
          </div>
        ))}
      </div>
      <div className="pf">{out.length > 0
        ? <b style={{ color: 'var(--red)' }}>{out.length} of {(item.all_suppliers ?? []).length} out of stock.</b>
        : (item.all_suppliers ?? []).length > 1 ? 'Cheapest source is preferred.' : 'Single supplier on record.'}</div>
    </>
  }
  if (kind === 'channels') {
    const live = (item.channels ?? []).filter(c => c.selling_price != null || c.is_active)
    return <>
      <div className="ph">Margin by channel</div>
      <div className="pb">
        {live.length === 0 && <div className="sub">Not listed on any channel.</div>}
        {live.map(c => {
          const sell = c.selling_price
          const gross = c.gp_pct
          const fee = c.channel_fee_pct ?? FEE[c.channel] ?? 0
          const logi = LOGI[c.channel] ?? 0
          const net = gross == null || sell == null ? null : gross - fee - logi / sell
          return (
            <div className="prow" key={c.channel}>
              <span className="pk">
                <b>{c.channel}</b>{c.is_active ? '' : ' · off'}
                {sell != null && <div className="pmeta">
                  {money(sell)} sell{fee > 0 ? ` · −${(fee * 100).toFixed(1)}% fee` : ''}{logi > 0 ? ` · −${money(logi)} logi` : ''}
                  {gross != null ? ` · ${(gross * 100).toFixed(1)}% gross` : ''}
                </div>}
              </span>
              <span className={`pv ${net == null ? '' : net >= (item.gp_floor ?? 0) ? 'good' : net > 0 ? 'amber' : 'red'}`}>
                {net == null ? '—' : `${(net * 100).toFixed(1)}%`}
              </span>
            </div>
          )
        })}
      </div>
      <div className="pf">Net = sell − channel fee − logistics − cost (fees are standard estimates). Floor {((item.gp_floor ?? 0) * 100).toFixed(0)}% is gross GP.</div>
    </>
  }
  if (kind === 'cover') {
    const upp = item.units_per_pack ?? 1
    const rows = (item.weekly_demand ?? 0) > 0
      ? [4, 8].map(w => {
          const packs = Math.ceil(Math.max(0, (w - (item.woc ?? 0)) * item.weekly_demand) / upp)
          return packs > 0 ? { w, packs, units: packs * upp } : null
        }).filter(Boolean) as { w: number; packs: number; units: number }[]
      : []
    return <>
      <div className="ph">Weeks of cover · target 4w</div>
      <div className="pb">
        <div style={{ marginBottom: rows.length ? 8 : 0 }}>
          Currently {item.woc != null
            ? <b className={item.woc < 2 ? 'red' : item.woc < 4 ? 'amber' : 'good'}>{item.woc.toFixed(1)} weeks</b>
            : <span className="sub">no demand signal</span>} · ~{fmtRate(item.weekly_demand ?? 0)}/wk
        </div>
        {rows.map(r => (
          <div className="prow" key={r.w}>
            <span className="pk">Reach <b>{r.w} weeks</b></span>
            <span className="pv">{r.packs} {item.pack_unit ?? 'pack'}{r.packs > 1 ? 's' : ''}
              <div className="pmeta" style={{ textAlign: 'right' }}>{r.units.toLocaleString()} {plural(uom)}</div></span>
          </div>
        ))}
        {!rows.length && <div className="sub">Add pack size + demand to simulate top-ups.</div>}
      </div>
      <div className="pf">Clinic (DaySmart) vs warehouse cover; target is 4 weeks.</div>
    </>
  }
  if (kind === 'stock') {
    return <>
      <div className="ph">Stock on hand</div>
      <div className="pb">
        <div className="prow"><span className="pk">Clinic</span><span className="pv">{Math.round(item.clinic_qty ?? 0).toLocaleString()}</span></div>
        <div className="prow"><span className="pk">Warehouse</span><span className="pv">{Math.round(item.warehouse_qty ?? 0).toLocaleString()}</span></div>
        <div className="prow"><span className="pk"><b>Total</b></span><span className="pv">{Math.round(item.total_qty ?? 0).toLocaleString()} {plural(uom)}</span></div>
      </div>
      <div className="pf">Storage rule: {item.storage_rule === 'clinic_only' ? 'clinic only' : 'any location'}
        {item.min_purchase_qty ? ` · product MOQ ${item.min_purchase_qty}` : ''}</div>
    </>
  }
  if (kind === 'state') {
    const st = rowState(item)
    const why = STATE_WHY[st.key]
    return <>
      <div className="ph">Why this row is flagged</div>
      <div className="pb">
        <div style={{ marginBottom: 7 }}>
          {st.key === 'ok'
            ? <span className="chip ok">nothing to do</span>
            : <span className={`chip ${st.tone === 'bad' ? 'bad' : 'warn'}`}>{st.label}</span>}
        </div>
        <div style={{ lineHeight: 1.55 }}>{why.what(item)}</div>
      </div>
      <div className="pf">{why.fix}</div>
    </>
  }
  if (kind === 'demand') {
    const wk = item.weekly_demand ?? 0
    return <>
      <div className="ph">Demand</div>
      <div className="pb">
        <div className="prow"><span className="pk">Weekly rate</span><span className="pv">{fmtRate(wk)} {plural(uom)}/wk</span></div>
        <div className="prow"><span className="pk">Over 120 days</span><span className="pv">{(item.sales_120d ?? 0).toLocaleString()} {plural(uom)}</span></div>
        <div className="prow"><span className="pk">Covered by stock for</span><span className="pv">{item.woc != null ? `${item.woc.toFixed(1)} weeks` : '—'}</span></div>
      </div>
      <div className="pf">This is the weekly rate carried over 120 days, not a recorded sales count. The rate comes from the algo sales feed.</div>
    </>
  }
  if (kind === 'unitcost') {
    const upp = item.units_per_pack ?? null
    return <>
      <div className="ph">Cost per {uom}</div>
      <div className="pb">
        <div className="prow"><span className="pk">Pack cost<div className="pmeta">what the supplier charges</div></span>
          <span className="pv">{item.primary_cost != null ? money(item.primary_cost) : '—'}</span></div>
        <div className="prow"><span className="pk">Units per {item.pack_unit ?? 'pack'}</span>
          <span className="pv">{upp ?? <span style={{ color: 'var(--red)' }}>not set</span>}</span></div>
        <div className="prow"><span className="pk"><b>Cost per {uom}</b></span>
          <span className="pv">{item.unit_cost != null ? money(item.unit_cost) : '—'}</span></div>
        {item.mbb_unit_cost != null && item.unit_cost != null && item.mbb_unit_cost < item.unit_cost && (
          <div className="prow"><span className="pk">At bulk terms</span>
            <span className="pv" style={{ color: 'var(--good)' }}>{money(item.mbb_unit_cost)}</span></div>
        )}
      </div>
      <div className="pf">{upp == null
        ? 'Without a pack size the per-unit cost — and every margin built on it — is a guess.'
        : `Margins are struck against the cost per ${uom}, not the pack cost.`}</div>
    </>
  }
  if (kind === 'costtohit') {
    // cost_to_hit is the cash you lay out to UNLOCK the best bulk term — for
    // buy-x-get-y that's min_qty at the basic price, for spend_discount the
    // threshold, otherwise min_qty at the discounted price. Not a margin target.
    const outlay = margin?.cost_to_hit ?? null
    const best = bestTerm(item)
    const qty = best?.min_qty ?? null
    const saving = item.unit_cost != null && item.mbb_unit_cost != null ? item.unit_cost - item.mbb_unit_cost : null
    const wk = item.weekly_demand ?? 0
    const weeks = qty != null && wk > 0 ? qty / wk : null
    return <>
      <div className="ph">Buying into the bulk tier</div>
      <div className="pb">
        <div className="prow"><span className="pk">Best term<div className="pmeta">{termLabel(best, uom) ?? 'none on record'}</div></span>
          <span className="pv">{item.mbb_unit_cost != null ? `${money(item.mbb_unit_cost)}/${uom}` : '—'}</span></div>
        <div className="prow"><span className="pk"><b>Outlay to unlock</b><div className="pmeta">what you pay up front</div></span>
          <span className="pv">{outlay != null ? money(outlay, 0) : '—'}</span></div>
        {saving != null && saving > 0 && (
          <div className="prow"><span className="pk">Saves per {uom}</span>
            <span className="pv" style={{ color: 'var(--good)' }}>{money(saving)}</span></div>
        )}
        {weeks != null && (
          <div className="prow"><span className="pk">That quantity lasts</span>
            <span className="pv" style={{ color: weeks > 26 ? 'var(--amber)' : undefined }}>{weeks.toFixed(0)} weeks</span></div>
        )}
      </div>
      <div className="pf">{outlay == null ? 'No bulk term on this supplier, so there is nothing to buy into.'
        : weeks != null && weeks > 26 ? 'Over six months of stock — the discount may not be worth the cash and the shelf life.'
        : 'Weigh the discount against tying up cash and storage.'}</div>
    </>
  }
  if (kind === 'grade') {
    const hasCostV = item.primary_cost != null
    const hasPrice = (item.channels ?? []).some(c => c.selling_price != null)
    const hasSup = !!supplierSummary(item).pref
    const okSku = /^\d{6,}$/.test(item.sku_code ?? '')
    return <>
      <div className="ph">What sets the grade</div>
      <div className="pb">
        <Check ok={hasCostV} label="Cost on record" detail={hasCostV ? undefined : 'no supplier price'} />
        <Check ok={hasPrice} label="Priced on a channel" detail={hasPrice ? undefined : 'no selling price anywhere'} />
        <Check ok={hasSup} label="Supplier linked" />
        <Check ok={okSku} label="Valid SKU code" detail={okSku ? undefined : item.sku_code} />
      </div>
      <div className="pf">All four pass → <b>A</b>, actionable. Any one fails → <b>C</b>, don’t trade on it.</div>
    </>
  }
  if (kind === 'expiry') {
    const batches = item.expiry_batches ?? []
    const lapsed = batches.filter(b => b.days < 0)
    const soon = batches.filter(b => b.days >= 0 && b.days < EXPIRY_SOON_DAYS)
    const atRisk = [...lapsed, ...soon].reduce((n, b) => n + (b.qty ?? 0), 0)
    const wk = item.weekly_demand ?? 0
    return <>
      <div className="ph">Batch expiry</div>
      <div className="pb">
        {batches.length === 0 && <div className="sub">No batch dates tracked for this SKU.</div>}
        {batches.map((b, i) => (
          <div className="prow" key={`${b.batch_ref ?? 'batch'}-${i}`}>
            <span className="pk">
              <b>{fmtDay(b.expiry_date)}</b>
              <div className="pmeta">{[b.batch_ref, b.location].filter(Boolean).join(' · ') || 'no batch reference'}</div>
            </span>
            <span className="pv">
              {b.qty != null ? `${b.qty.toLocaleString()} ${plural(uom)}` : '—'}
              <div className="pmeta" style={{ textAlign: 'right', color: b.days < 0 ? 'var(--red)' : b.days < EXPIRY_SOON_DAYS ? 'var(--amber)' : undefined }}>
                {b.days < 0 ? `lapsed ${Math.abs(b.days)}d ago` : `${b.days}d left`}
              </div>
            </span>
          </div>
        ))}
      </div>
      {batches.length > 0 && (
        <div className="pf">{lapsed.length > 0
          ? <b style={{ color: 'var(--red)' }}>{lapsed.length} batch{lapsed.length === 1 ? '' : 'es'} already lapsed — write off.</b>
          : soon.length > 0
            ? <>{atRisk.toLocaleString()} {plural(uom)} at risk{wk > 0 ? `; at ~${fmtRate(wk)}/wk you would clear ${Math.round(wk * (soon[0].days / 7)).toLocaleString()} of it first.` : ' and no demand to clear it.'}</>
            : 'Nothing dates out inside 90 days.'}</div>
      )}
    </>
  }
  if (kind === 'costsrc') {
    const age = daysSince(item.cost_last_updated)
    return <>
      <div className="ph">Where the cost came from</div>
      <div className="pb">
        <div className="prow"><span className="pk">Source</span>
          <span className="pv">{item.cost_source ? item.cost_source.toUpperCase() : '—'}</span></div>
        <div className="prow"><span className="pk">Last updated</span>
          <span className="pv">{item.cost_last_updated ? fmtDay(item.cost_last_updated) : '—'}</span></div>
        <div className="prow"><span className="pk">Age</span>
          <span className="pv" style={{ color: age != null && age > 90 ? 'var(--amber)' : undefined }}>{age != null ? `${age} days` : '—'}</span></div>
        {item.uom_verified_at && (
          <div className="prow"><span className="pk">Pack size checked</span>
            <span className="pv">{fmtDay(item.uom_verified_at)}{item.uom_verified_by ? ` · ${item.uom_verified_by}` : ''}</span></div>
        )}
      </div>
      <div className="pf">{item.cost_source === 'catalogue'
        ? 'Read from a reviewed supplier catalogue.'
        : 'Entered by hand — a catalogue run would replace it.'}{age != null && age > 90 ? ' Over 90 days old; worth re-checking.' : ''}</div>
    </>
  }
  // bulk
  const sup = primarySupplier(item)
  const terms = sup?.mbb_term_list ?? []
  return <>
    <div className="ph">Bulk-buy tiers{sup?.name ? ` · ${sup.name}` : ''}</div>
    <div className="pb">
      {terms.length === 0 && <div className="sub">No bulk-buy terms on record.</div>}
      {terms.map(t => (
        <div className="prow" key={t.id}>
          <span className="pk">
            <b>{termLabel(t, uom)}</b>
            {t.note && <div className="pmeta">{t.note}</div>}
          </span>
          <span className="pv">{t.effective_unit_cost != null ? `${money(t.effective_unit_cost)}/${uom}` : '—'}</span>
        </div>
      ))}
    </div>
    {item.mbb_unit_cost != null && (
      <div className="pf">Best achievable: <b style={{ color: 'var(--accent-ink)' }}>{money(item.mbb_unit_cost)}</b> / {uom}
        {item.unit_cost != null && item.mbb_unit_cost < item.unit_cost
          ? ` · ${((1 - item.mbb_unit_cost / item.unit_cost) * 100).toFixed(1)}% under the base cost`
          : item.unit_cost != null ? ' · not cheaper than the base cost' : ''}</div>
    )}
  </>
}

const plural = (u: string) => (/(\(s\)|s)$/i.test(u.trim()) ? u : `${u}s`)
// A demand of 0.4/wk still justifies a top-up — don't round it away to "0/wk".
const fmtRate = (n: number) => (n > 0 && n < 10 ? n.toFixed(1) : Math.round(n).toLocaleString())

function HoverCard({ pop, onKeep, onLeave }: { pop: PopState; onKeep: () => void; onLeave: () => void }) {
  if (!pop) return null
  return (
    <div className="inv2-pop" style={{ left: pop.x, top: pop.y }} onMouseEnter={onKeep} onMouseLeave={onLeave}>
      <PopBody kind={pop.kind} item={pop.item} margin={pop.margin} />
    </div>
  )
}

// ── page ──────────────────────────────────────────────────────────────────
function NewInventoryPage() {
  const navigate = useNavigate()
  const editable = can('product_edit')

  // data
  const [items, setItems] = useState<Product[]>([])
  const [loaded, setLoaded] = useState(0)
  const [total, setTotal] = useState<number | null>(null)
  const [settled, setSettled] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [margins, setMargins] = useState<Record<string, MarginRow>>({})
  const [marginsLoading, setMarginsLoading] = useState(false)

  // view state (URL-backed so a link reproduces the list)
  const params = typeof window !== 'undefined' ? new URLSearchParams(window.location.search) : new URLSearchParams()
  const [scope, setScope] = useState<ScopeId>((params.get('scope') as ScopeId) || 'working')
  const [view, setView] = useState<ViewId>((params.get('view') as ViewId) || 'ops')
  const [search, setSearch] = useState(params.get('q') ?? '')
  const [searchInput, setSearchInput] = useState(params.get('q') ?? '')
  const [attention, setAttention] = useState<AttentionId | null>((params.get('att') as AttentionId) || null)
  const [category, setCategory] = useState<string[]>(params.get('cat') ? params.get('cat')!.split('~') : [])
  const [supplier, setSupplier] = useState(params.get('sup') ?? 'All')
  const [stockFilter, setStockFilter] = useState(params.get('stock') ?? 'any')
  const [gradeFilter, setGradeFilter] = useState(params.get('dq') ?? 'any')
  const [channelFilter, setChannelFilter] = useState(params.get('ch') ?? 'any')
  const [storageFilter, setStorageFilter] = useState(params.get('store') ?? 'any')
  const [heroFilter, setHeroFilter] = useState(params.get('hero') ?? 'any')
  const [gapFilter, setGapFilter] = useState(params.get('gap') ?? 'any')
  const [expiryFilter, setExpiryFilter] = useState(params.get('exp') ?? 'any')
  const [collectionId, setCollectionId] = useState<number | null>(params.get('col') ? Number(params.get('col')) : null)
  const [collections, setCollections] = useState<{ id: number; name: string; count: number }[]>([])
  const [collectionSkus, setCollectionSkus] = useState<Set<string> | null>(null)
  const [sortCol, setSortCol] = useState(params.get('sort') ?? 'state')
  // 'state' ranks worst-first, so ascending is the useful default there.
  const [sortAsc, setSortAsc] = useState(params.get('dir') ? params.get('dir') === 'asc' : true)

  // ui state
  const [filtersOpen, setFiltersOpen] = useState(false)
  const [tasksOpen, setTasksOpen] = useState(false)
  const [exportOpen, setExportOpen] = useState(false)
  const [batchOpen, setBatchOpen] = useState(false)
  const [shortcuts, setShortcuts] = useState(false)
  const [focusRow, setFocusRow] = useState(0)
  const [pop, setPop] = useState<PopState>(null)
  const popTimer = useRef<number | null>(null)
  const showPop = (kind: PopKind, item: Product, e: React.MouseEvent, margin?: MarginRow) => {
    if (popTimer.current) { window.clearTimeout(popTimer.current); popTimer.current = null }
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    const x = Math.max(8, Math.min(r.left, window.innerWidth - 334))
    const below = e.clientY + 14
    const y = below + 300 > window.innerHeight ? Math.max(8, e.clientY - 306) : below
    setPop({ kind, item, margin, x, y })
  }
  const hidePop = () => { if (popTimer.current) window.clearTimeout(popTimer.current); popTimer.current = window.setTimeout(() => setPop(null), 130) }
  const keepPop = () => { if (popTimer.current) { window.clearTimeout(popTimer.current); popTimer.current = null } }
  const searchRef = useRef<HTMLInputElement>(null)
  const anyDialog = exportOpen || batchOpen || shortcuts

  // debounce search
  useEffect(() => { const t = setTimeout(() => setSearch(searchInput), 200); return () => clearTimeout(t) }, [searchInput])

  // keep the URL in step
  useEffect(() => {
    const p = new URLSearchParams()
    if (scope !== 'working') p.set('scope', scope)
    if (view !== 'ops') p.set('view', view)
    if (search.trim()) p.set('q', search.trim())
    if (attention) p.set('att', attention)
    if (category.length) p.set('cat', category.join('~'))
    if (supplier !== 'All') p.set('sup', supplier)
    if (stockFilter !== 'any') p.set('stock', stockFilter)
    if (gradeFilter !== 'any') p.set('dq', gradeFilter)
    if (channelFilter !== 'any') p.set('ch', channelFilter)
    if (storageFilter !== 'any') p.set('store', storageFilter)
    if (heroFilter !== 'any') p.set('hero', heroFilter)
    if (gapFilter !== 'any') p.set('gap', gapFilter)
    if (expiryFilter !== 'any') p.set('exp', expiryFilter)
    if (collectionId != null) p.set('col', String(collectionId))
    if (sortCol !== 'state') p.set('sort', sortCol)
    p.set('dir', sortAsc ? 'asc' : 'desc')
    const qs = p.toString()
    window.history.replaceState(null, '', qs ? `?${qs}` : window.location.pathname)
  }, [scope, view, search, attention, category, supplier, stockFilter, gradeFilter,
      channelFilter, storageFilter, heroFilter, gapFilter, expiryFilter, collectionId, sortCol, sortAsc])

  // ── stream the catalogue ──
  const fetchData = useCallback(async () => {
    setError(null); setSettled(false); setLoaded(0)
    try {
      const res = await fetch(`${API}/products/stream`, { cache: 'no-store', headers: authHeaders() })
      if (!res.ok || !res.body) throw new Error(`API ${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let batch: Product[] = []
      const rows: Product[] = []
      const flush = () => {
        if (!batch.length) return
        const chunk = batch; batch = []
        rows.push(...chunk)
        setItems([...rows]); setLoaded(rows.length)
      }
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let nl: number
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl); buf = buf.slice(nl + 1)
          if (!line.trim()) continue
          const obj = JSON.parse(line)
          if (obj._meta) { setTotal(obj._meta.total); continue }
          batch.push(obj as Product)
        }
        if (batch.length >= 400) flush()
      }
      flush()
      setSettled(true)
    } catch (e: any) {
      setError(String(e?.message ?? e)); setSettled(true)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])
  useEffect(() => {
    fetch(`${API}/suppliers`, { headers: authHeaders() }).then(r => r.ok ? r.json() : []).then(setSuppliers).catch(() => {})
    fetch(`${API}/collections`, { headers: authHeaders() }).then(r => r.ok ? r.json() : []).then(setCollections).catch(() => {})
  }, [])
  // A collection is a stored list of SKUs — fetch its membership when picked.
  useEffect(() => {
    if (collectionId == null) { setCollectionSkus(null); return }
    let cancelled = false
    fetch(`${API}/collections/${collectionId}/products`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!cancelled && d) setCollectionSkus(new Set((d.items ?? d ?? []).map((x: any) => x.sku_code ?? x))) })
      .catch(() => {})
    return () => { cancelled = true }
  }, [collectionId])
  // Money view pulls the margin table once, on demand.
  useEffect(() => {
    if (view !== 'money' || Object.keys(margins).length || marginsLoading) return
    setMarginsLoading(true)
    fetch(`${API}/products/margins.json`, { cache: 'no-store', headers: authHeaders() })
      .then(r => r.ok ? r.json() : null)
      // Already keyed by sku_code: { basic_cost, mbb_cost, gp_floor, ch: { clinic: { price, nb, nm } } }
      .then(d => { if (d && typeof d === 'object') setMargins(d) })
      .catch(() => {})
      .finally(() => setMarginsLoading(false))
  }, [view])   // eslint-disable-line react-hooks/exhaustive-deps

  // ── scope → filters → sort ──
  const scoped = useMemo(() => {
    if (scope === 'working') return items.filter(inWorkingSet)
    if (scope === 'active') return items.filter(i => i.status === 'ACTIVE')
    return items
  }, [items, scope])

  const counts = useMemo(() => ({
    low_cover: scoped.filter(i => i.woc != null && i.woc < 2).length,
    below_floor: scoped.filter(belowFloor).length,
    supplier_out: scoped.filter(i => oosSuppliers(i).length > 0).length,
    no_cost: scoped.filter(i => (isListed(i) || (i.sales_120d ?? 0) > 0) && !hasCost(i)).length,
    expiring: scoped.filter(i => isExpiring(i) || hasExpired(i)).length,
    expired: scoped.filter(hasExpired).length,
  }), [scoped])

  const categories = useMemo(
    () => [...new Set(items.map(i => i.category).filter(Boolean))].sort(),
    [items],
  )

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase()
    return scoped.filter(i => {
      if (q) {
        const hay = `${i.name ?? ''} ${i.sku_code} ${i.supplier_sku ?? ''} ${i.brand ?? ''}`.toLowerCase()
        if (!hay.includes(q)) return false
      }
      if (category.length && !category.includes(i.category)) return false
      if (supplier !== 'All' && i.supplier_name !== supplier) return false
      if (stockFilter === 'in' && (i.total_qty ?? 0) <= 0) return false
      if (stockFilter === 'out' && (i.total_qty ?? 0) > 0) return false
      if (gradeFilter === 'a' && i.data_grade !== 'A') return false
      if (gradeFilter === 'c' && i.data_grade !== 'C') return false
      if (gradeFilter === 'unverified' && i.uom_verified_at) return false
      if (gradeFilter === 'verified' && !i.uom_verified_at) return false
      if (channelFilter !== 'any' && !(i.channels ?? []).some(c => c.channel === channelFilter && c.is_active)) return false
      if (storageFilter === 'clinic_only' && i.storage_rule !== 'clinic_only') return false
      if (storageFilter === 'any_loc' && i.storage_rule === 'clinic_only') return false
      if (heroFilter === 'hero' && !i.hero_sku) return false
      if (heroFilter === 'not_hero' && i.hero_sku) return false
      if (collectionSkus && !collectionSkus.has(i.sku_code)) return false
      if (gapFilter === 'no_sku' && /^\d{6,}$/.test(i.sku_code.trim())) return false
      if (gapFilter === 'no_supplier' && (i.all_suppliers ?? []).some(sp => sp.name)) return false
      if (gapFilter === 'no_pack' && i.units_per_pack != null) return false
      if (gapFilter === 'priority' && !((i.sales_120d ?? 0) > 0 && i.data_grade === 'C')) return false
      if (attention === 'low_cover' && !(i.woc != null && i.woc < 2)) return false
      if (attention === 'below_floor' && !belowFloor(i)) return false
      if (attention === 'supplier_out' && oosSuppliers(i).length === 0) return false
      if (attention === 'no_cost' && !((isListed(i) || (i.sales_120d ?? 0) > 0) && !hasCost(i))) return false
      if (attention === 'expiring' && !(isExpiring(i) || hasExpired(i))) return false
      if (expiryFilter === 'expired' && !hasExpired(i)) return false
      if (expiryFilter === 'd30' && !(expiryDays(i) != null && expiryDays(i)! < 30)) return false
      if (expiryFilter === 'd90' && !(expiryDays(i) != null && expiryDays(i)! < 90)) return false
      if (expiryFilter === 'tracked' && expiryDays(i) == null) return false
      return true
    })
  }, [scoped, search, category, supplier, stockFilter, gradeFilter, attention, expiryFilter,
      channelFilter, storageFilter, heroFilter, gapFilter, collectionSkus])

  const sorted = useMemo(() => {
    const val = (p: Product): string | number | null => {
      switch (sortCol) {
        case 'state': return rowState(p).rank
        case 'name': return (p.name ?? '').toLowerCase()
        case 'sku': return p.sku_code
        case 'cover': return p.woc
        case 'onhand': return p.total_qty
        case 'expiry': return p.expiry_days ?? null
        case 'sales': return p.sales_120d
        case 'cost': return p.primary_cost
        case 'sell': return ((p.channels ?? []).find(c => c.channel === 'clinic' && c.selling_price != null)
          ?? (p.channels ?? []).find(c => c.selling_price != null))?.selling_price ?? null
        case 'gp': return worstMargin(p)
        case 'packcost': return p.primary_cost
        case 'unitcost': return p.unit_cost
        case 'bulk': return p.mbb_unit_cost
        case 'grade': return p.data_grade
        case 'costage': return daysSince(p.cost_last_updated)
        default: return null
      }
    }
    return [...filtered].sort((a, b) => {
      const av = val(a), bv = val(b)
      if (av === null || av === undefined) return 1        // nulls always sink
      if (bv === null || bv === undefined) return -1
      if (typeof av === 'string' && typeof bv === 'string') return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av)
      return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number)
    })
  }, [filtered, sortCol, sortAsc])

  // "Everything" can be 11k rows — keep incremental rendering there only.
  const [renderLimit, setRenderLimit] = useState(300)
  useEffect(() => { setRenderLimit(300) }, [scope, view, search, attention, category, supplier, stockFilter, gradeFilter,
    channelFilter, storageFilter, heroFilter, gapFilter, expiryFilter, collectionSkus])
  const rows = useMemo(() => sorted.slice(0, renderLimit), [sorted, renderLimit])

  const activeFilters = [
    ...category.map(c => ({ label: `Category: ${c}`, clear: () => setCategory(prev => prev.filter(x => x !== c)) })),
    ...(supplier !== 'All' ? [{ label: `Supplier: ${supplier}`, clear: () => setSupplier('All') }] : []),
    ...(stockFilter !== 'any' ? [{ label: stockFilter === 'in' ? 'In stock' : 'Out of stock', clear: () => setStockFilter('any') }] : []),
    ...(gradeFilter !== 'any' ? [{ label: GRADE_LABEL[gradeFilter] ?? gradeFilter, clear: () => setGradeFilter('any') }] : []),
    ...(attention ? [{ label: ATT_LABEL[attention], clear: () => setAttention(null) }] : []),
    ...(channelFilter !== 'any' ? [{ label: `Channel: ${channelFilter}`, clear: () => setChannelFilter('any') }] : []),
    ...(storageFilter !== 'any' ? [{ label: storageFilter === 'clinic_only' ? 'Clinic only' : 'Warehouse OK', clear: () => setStorageFilter('any') }] : []),
    ...(heroFilter !== 'any' ? [{ label: heroFilter === 'hero' ? '★ Hero SKUs' : 'Not hero', clear: () => setHeroFilter('any') }] : []),
    ...(gapFilter !== 'any' ? [{ label: GAP_LABEL[gapFilter] ?? gapFilter, clear: () => setGapFilter('any') }] : []),
    ...(expiryFilter !== 'any' ? [{ label: EXPIRY_LABEL[expiryFilter] ?? expiryFilter, clear: () => setExpiryFilter('any') }] : []),
    ...(collectionId != null ? [{ label: `Collection: ${collections.find(c => c.id === collectionId)?.name ?? collectionId}`, clear: () => setCollectionId(null) }] : []),
  ]
  const clearAll = () => {
    setCategory([]); setSupplier('All'); setStockFilter('any'); setGradeFilter('any'); setAttention(null)
    setChannelFilter('any'); setStorageFilter('any'); setHeroFilter('any'); setGapFilter('any'); setExpiryFilter('any')
    setCollectionId(null); setSearchInput('')
  }

  // search that reaches outside the scope
  const outsideScope = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (!q || scope === 'all') return 0
    const inScope = new Set(scoped.map(i => i.sku_code))
    return items.filter(i => !inScope.has(i.sku_code)
      && `${i.name ?? ''} ${i.sku_code} ${i.supplier_sku ?? ''} ${i.brand ?? ''}`.toLowerCase().includes(q)).length
  }, [items, scoped, search, scope])

  const toggleSort = (col: string) => {
    if (sortCol === col) setSortAsc(a => !a)
    else { setSortCol(col); setSortAsc(col === 'state' || col === 'name' || col === 'sku' || col === 'grade') }
  }

  const openSku = (sku: string) => navigate({ to: '/sku/$' as never, params: { _splat: skuToPath(sku) } as never })

  // ── keyboard ──
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      const t = e.target as HTMLElement
      const typing = t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)
      if (e.key === 'Escape') {
        if (anyDialog) { setExportOpen(false); setBatchOpen(false); setShortcuts(false); return }
        if (filtersOpen || tasksOpen) { setFiltersOpen(false); setTasksOpen(false); return }
        if (typing) { (t as HTMLInputElement).blur(); return }
        if (searchInput) { setSearchInput(''); return }
        selectionStore.clear(); return
      }
      if (typing || anyDialog) return
      if (e.key === '/') { e.preventDefault(); searchRef.current?.focus(); return }
      if (e.key === '?') { setShortcuts(true); return }
      if (e.key === '1') setView('ops')
      if (e.key === '2') setView('money')
      if (e.key === '3') setView('dq')
      if (e.key === 'f' || e.key === 'F') { e.preventDefault(); setFiltersOpen(o => !o) }
      if (e.key === 'e' || e.key === 'E') setExportOpen(true)
      if (e.key === 'j' || e.key === 'J') setFocusRow(i => Math.min(i + 1, rows.length - 1))
      if (e.key === 'k' || e.key === 'K') setFocusRow(i => Math.max(i - 1, 0))
      if (e.key === 'x' || e.key === 'X') { const r = rows[focusRow]; if (r) selectionStore.toggle(r.sku_code) }
      if (e.key === 'Enter') { const r = rows[focusRow]; if (r) openSku(r.sku_code) }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  const scopeMeta = SCOPES.find(s => s.id === scope)!
  const scopeCounts = useMemo<ScopeCounts>(() => {
    const working = items.filter(inWorkingSet).length
    const active = items.filter(i => i.status === 'ACTIVE').length
    return { working, active, all: items.length, dormant: active - working, inactive: items.length - active }
  }, [items])

  return (
    <div className="inv2" style={{ padding: '18px 24px 40px', maxWidth: 1320, margin: '0 auto', position: 'relative' }}>
      <style>{INV_CSS}</style>

      {/* header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <h1>Inventory</h1>
          <div className="sub" style={{ marginTop: 3 }}>
            {settled
              ? <>{items.length.toLocaleString()} variants · live from the catalogue</>
              : <>loading {loaded.toLocaleString()}{total ? ` / ${total.toLocaleString()}` : ''}…</>}
          </div>
        </div>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap', position: 'relative' }}>
          <ScopeSwitch scope={scope} onPick={setScope} counts={scopeCounts} settled={settled} />
          <button className="btn" onClick={() => setExportOpen(true)}>↓ Export</button>
          {editable && <button className="btn" onClick={() => setBatchOpen(true)}>Batch update</button>}
          <div style={{ position: 'relative' }}>
            <button className="btn" onClick={() => setTasksOpen(o => !o)} title="Data tasks">⋯</button>
            {tasksOpen && <DataTasksMenu onClose={() => setTasksOpen(false)} onShortcuts={() => { setTasksOpen(false); setShortcuts(true) }} />}
          </div>
        </div>
      </div>

      {/* attention rail */}
      {scope === 'all' ? (
        <div className="att" style={{ gridTemplateColumns: '1fr' }}>
          <div className="acard quiet">
            <div className="al" style={{ fontSize: 12, color: 'var(--ink2)' }}>
              Attention counts are off in the whole catalogue — {scopeCounts.inactive.toLocaleString()} of these rows are
              archived and can’t need anything. Search and filters still work.{' '}
              <button className="lnk" onClick={() => setScope('working')}>Back to stocked or moving</button>
            </div>
          </div>
        </div>
      ) : (
        <div className="att">
          <AttCard id="low_cover" n={counts.low_cover} label="running out" hint="cover under 2 weeks" hot active={attention} onPick={setAttention} settled={settled} />
          <AttCard id="below_floor" n={counts.below_floor} label="below GP floor" hint="selling under target margin" hot active={attention} onPick={setAttention} settled={settled} />
          <AttCard id="supplier_out" n={counts.supplier_out} label="supplier out" hint="a supplier can’t ship" active={attention} onPick={setAttention} settled={settled} />
          <AttCard id="no_cost" n={counts.no_cost} label="no cost on record" hint="selling, but margin unknown" active={attention} onPick={setAttention} settled={settled} />
          <AttCard id="expiring" n={counts.expiring} label="stock expiring"
            hint={counts.expired > 0 ? `${counts.expired} already lapsed — write off` : 'a batch dates out within 90 days'}
            hot={counts.expired > 0} active={attention} onPick={setAttention} settled={settled} />
          <div className="acard quiet">
            <div className="an">
              {!settled ? <span className="skel" style={{ display: 'block', width: 110 }} />
                : counts.low_cover + counts.below_floor + counts.supplier_out + counts.no_cost + counts.expiring === 0
                  ? 'Nothing needs attention' : `${scoped.length.toLocaleString()} in scope`}
            </div>
            <div className="al">{scopeMeta.blurb}</div>
            <div className="ax">{settled ? scopeMeta.delta(scopeCounts) : 'counts are scoped to the rows above'}</div>
          </div>
        </div>
      )}

      {/* views */}
      <div className="views">
        {VIEWS.map(v => (
          <button key={v.id} className={view === v.id ? 'on' : ''} onClick={() => setView(v.id)}>{v.label}</button>
        ))}
      </div>

      {/* toolbar */}
      <div className="tools">
        <div className="search">
          <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6" style={{ color: 'var(--faint)', flex: 'none' }}>
            <circle cx="7" cy="7" r="4.3" /><path d="M10.5 10.5l3 3" strokeLinecap="round" />
          </svg>
          <input ref={searchRef} value={searchInput} onChange={e => setSearchInput(e.target.value)}
            placeholder="Search product, SKU, supplier SKU or brand" aria-label="Search inventory" />
        </div>
        <div style={{ position: 'relative' }}>
          <button className={`fbtn${activeFilters.length ? ' on' : ''}`} onClick={() => setFiltersOpen(o => !o)}>
            Filters{activeFilters.length > 0 && <span className="cnt">{activeFilters.length}</span>}
          </button>
          {filtersOpen && (
            <FiltersPanel
              categories={categories} suppliers={suppliers}
              category={category} setCategory={setCategory}
              supplier={supplier} setSupplier={setSupplier}
              stockFilter={stockFilter} setStockFilter={setStockFilter}
              gradeFilter={gradeFilter} setGradeFilter={setGradeFilter}
              attention={attention} setAttention={setAttention}
              channelFilter={channelFilter} setChannelFilter={setChannelFilter}
              storageFilter={storageFilter} setStorageFilter={setStorageFilter}
              heroFilter={heroFilter} setHeroFilter={setHeroFilter}
              gapFilter={gapFilter} setGapFilter={setGapFilter}
              expiryFilter={expiryFilter} setExpiryFilter={setExpiryFilter}
              collections={collections} collectionId={collectionId} setCollectionId={setCollectionId}
              resultCount={filtered.length}
              onClose={() => setFiltersOpen(false)} onReset={clearAll}
            />
          )}
        </div>
        {activeFilters.map((f, i) => (
          <span key={i} className="fchip" onClick={f.clear}>{f.label} <span className="x">✕</span></span>
        ))}
        {activeFilters.length > 0 && <button className="lnk" style={{ fontSize: 11.5 }} onClick={clearAll}>clear</button>}
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {settled && <span className="sub"><span style={{ color: '#22A55E' }}>●</span> Live</span>}
          <span className="sub">
            {settled
              ? <><b style={{ color: 'var(--ink)' }}>{filtered.length.toLocaleString()}</b> of {scoped.length.toLocaleString()}</>
              : <>loading {loaded.toLocaleString()}{total ? ` / ${total.toLocaleString()}` : ''}…</>}
          </span>
        </span>
      </div>

      {/* table */}
      <div className="tblcard">
        <SelectionBar total={filtered.length} allSkus={filtered.map(i => i.sku_code)} onApplied={fetchData} editable={editable} />
        {error ? (
          <div className="empty">
            <div className="et">Cannot reach the API</div>
            <div style={{ marginBottom: 12 }}>{error}</div>
            <button className="btn pri" onClick={fetchData}>Retry</button>
          </div>
        ) : !settled && items.length === 0 ? (
          <SkeletonTable view={view} />
        ) : filtered.length === 0 ? (
          <EmptyState
            search={search} scope={scope} outsideScope={outsideScope}
            hasFilters={activeFilters.length > 0}
            onClearFilters={clearAll} onWiden={() => setScope('all')}
          />
        ) : (
          <>
            <div style={{ overflowX: 'auto' }}>
              <table>
                <thead><tr>
                  <th style={{ width: 30 }}>
                    <SelectAllBox skus={rows.map(r => r.sku_code)} />
                  </th>
                  <SortTh id="name" label="Product" sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                  <SortTh id="state" label="State" sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                  {view === 'ops' && <>
                    <SortTh id="cover" label="Cover" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <SortTh id="onhand" label="On hand" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <SortTh id="expiry" label="Expiry" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    {/* Not recorded sales — the backend projects it from the weekly
                        rate (weekly_demand × 120/7), so call it demand. */}
                    <SortTh id="sales" label="120d demand" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <SortTh id="cost" label="Cost" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <SortTh id="sell" label="Sell" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <SortTh id="gp" label="GP%" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                  </>}
                  {view === 'money' && <>
                    <SortTh id="packcost" label="Pack cost" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <SortTh id="unitcost" label="Unit cost" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <SortTh id="bulk" label="Best bulk" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <th className="r" title="Cash you lay out to unlock the best bulk term">To unlock bulk</th>
                    <th className="r">Clinic net</th><th className="r">Shopify net</th><th className="r">HKTV net</th>
                  </>}
                  {view === 'dq' && <>
                    <SortTh id="grade" label="Grade" sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <th>Verified</th><th>Cost source</th>
                    <SortTh id="costage" label="Cost age" r sortCol={sortCol} sortAsc={sortAsc} onSort={toggleSort} />
                    <th>Storage</th><th>Missing</th>
                  </>}
                  <th style={{ width: 26 }}></th>
                </tr></thead>
                <tbody>
                  {rows.map((p, i) => (
                    <Row key={p.sku_code} p={p} view={view} margin={margins[p.sku_code]} marginsLoading={marginsLoading}
                      focused={i === focusRow} onOpen={() => openSku(p.sku_code)} showPop={showPop} hidePop={hidePop} />
                  ))}
                </tbody>
              </table>
            </div>
            <div className="tfoot">
              <span>
                Showing {rows.length.toLocaleString()} of {filtered.length.toLocaleString()} matching
                {scope !== 'all' && <> · {scoped.length.toLocaleString()} in {scopeMeta.label.toLowerCase()}</>}
              </span>
              {rows.length < filtered.length && (
                <button className="lnk" onClick={() => setRenderLimit(n => n + 500)}>Show 500 more</button>
              )}
              {scope !== 'all' && (
                <button className="lnk" style={{ marginLeft: 'auto' }} onClick={() => setScope('all')}>
                  Show the other {(items.length - scoped.length).toLocaleString()} catalogue rows →
                </button>
              )}
            </div>
          </>
        )}
      </div>

      {outsideScope > 0 && (
        <div className="sub" style={{ marginTop: 8 }}>
          {outsideScope.toLocaleString()} more match “{search.trim()}” outside this scope —{' '}
          <button className="lnk" style={{ fontSize: 11 }} onClick={() => setScope('all')}>search the full catalogue</button>
        </div>
      )}

      {exportOpen && (
        <ExportDialog rows={filtered} scopeRows={scoped} scopeLabel={scopeMeta.label} onClose={() => setExportOpen(false)} />
      )}
      {batchOpen && <BatchDialog onClose={() => setBatchOpen(false)} onApplied={fetchData} />}
      {shortcuts && <ShortcutsOverlay onClose={() => setShortcuts(false)} />}
      <HoverCard pop={pop} onKeep={keepPop} onLeave={hidePop} />
    </div>
  )
}

const GAP_LABEL: Record<string, string> = {
  no_sku: 'No valid SKU', no_supplier: 'No supplier', no_pack: 'No pack size',
  priority: 'Fix first — sells, Grade C',
}
const GRADE_LABEL: Record<string, string> = {
  a: 'Grade A', c: 'Grade C', verified: 'Pack size verified', unverified: 'Pack size unverified',
}
const ATT_LABEL: Record<AttentionId, string> = {
  low_cover: 'Cover < 2 weeks', below_floor: 'Below GP floor',
  supplier_out: 'Supplier out', no_cost: 'No cost on record', expiring: 'Expiring or expired',
}
const EXPIRY_LABEL: Record<string, string> = {
  expired: 'Already expired', d30: 'Expires within 30 days',
  d90: 'Expires within 90 days', tracked: 'Has batch dates',
}

function AttCard({ id, n, label, hint, hot, active, onPick, settled }: {
  id: AttentionId; n: number; label: string; hint: string; hot?: boolean
  active: AttentionId | null; onPick: (a: AttentionId | null) => void; settled: boolean
}) {
  return (
    <button className={`acard${hot && n > 0 ? ' hot' : ''}${active === id ? ' on' : ''}`}
      onClick={() => onPick(active === id ? null : id)} aria-pressed={active === id}>
      <div className="an">{settled ? n.toLocaleString() : <span className="skel" style={{ display: 'block', width: 34, height: 14 }} />}</div>
      <div className="al">{label}</div>
      <div className="ax">{hint}</div>
    </button>
  )
}

function SortTh({ id, label, r, sortCol, sortAsc, onSort }: {
  id: string; label: string; r?: boolean; sortCol: string; sortAsc: boolean; onSort: (c: string) => void
}) {
  const on = sortCol === id
  return (
    <th className={`sortable${r ? ' r' : ''}${on ? ' sorted' : ''}`} onClick={() => onSort(id)}>
      {label}<span className="arw">{on ? (sortAsc ? '↑' : '↓') : '↕'}</span>
    </th>
  )
}

function Row({ p, view, margin, marginsLoading, focused, onOpen, showPop, hidePop }: {
  p: Product; view: ViewId; margin?: MarginRow; marginsLoading: boolean; focused: boolean; onOpen: () => void
  showPop: (k: PopKind, item: Product, e: React.MouseEvent, margin?: MarginRow) => void; hidePop: () => void
}) {
  // No className here — spreading it would clobber the cell's alignment class.
  const hov = (kind: PopKind) => ({
    onMouseEnter: (e: React.MouseEvent) => showPop(kind, p, e, margin),
    onMouseLeave: hidePop,
  })
  const st = rowState(p)
  const sel = useSelHas(p.sku_code)
  const cover = p.woc
  const coverPct = cover == null ? 0 : Math.max(3, Math.min(100, (cover / 8) * 100))
  const coverTone = cover == null ? 'var(--ghost)' : cover < 2 ? '#E25A4E' : cover < 4 ? '#E9A23B' : '#34B36F'
  const gp = worstMargin(p)
  // Headline sell price: clinic first (the primary channel), else any listed one.
  const sellPrice = ((p.channels ?? []).find(c => c.channel === 'clinic' && c.selling_price != null)
    ?? (p.channels ?? []).find(c => c.selling_price != null))?.selling_price ?? null
  const sup = supplierSummary(p)
  const missing = [
    !hasCost(p) && 'cost',
    !(p.channels ?? []).some(c => c.selling_price != null) && 'selling price',
    !sup.pref && 'supplier',
    !/^\d{6,}$/.test(p.sku_code) && 'valid SKU',
    p.units_per_pack == null && 'pack size',
  ].filter(Boolean) as string[]

  return (
    <tr className={`${focused ? 'focus' : ''} ${sel ? 'sel' : ''}`}>
      <td onClick={e => e.stopPropagation()}><SelCheckbox sku={p.sku_code} /></td>
      <td>
        <span className="pname" onClick={onOpen}>{p.name ?? p.sku_code}</span>
        <span className="pmeta">
          <span className="sku">{p.sku_code}</span>
          {p.hero_sku && <span title="Hero SKU" style={{ color: '#D9A400' }}>★</span>}
          {p.brand && <>· {p.brand}</>}
          <span className="chip neu">{p.category}</span>
          {sup.pref
            ? <span className="hoverable" {...hov('suppliers')} style={{ color: 'var(--muted)' }}>
                {sup.pref.name}
                {sup.extra > 0 && <span className="plusn">+{sup.extra}</span>}
                {sup.prefOos && <span className={`oosdot ${sup.hasAlt ? 'part' : 'crit'}`}
                  title={`Preferred supplier out of stock${sup.hasAlt ? ' — an alternate can ship' : ''}`} />}
              </span>
            : <span className="chip warn">no supplier</span>}
          {p.shopify_status && p.shopify_status !== 'archived' && <span className="chip ok">SP</span>}
          {p.daysmart_status && <span className="chip ok">DS</span>}
          {p.hktv_status && p.hktv_status !== 'offline' && <span className="chip ok">HK</span>}
        </span>
      </td>
      <td className="hoverable" {...hov('state')}>
        {st.key === 'ok'
          ? <span className="sub" style={{ color: 'var(--good)' }}>● ok</span>
          : <span className={`chip ${st.tone === 'bad' ? 'bad' : st.tone === 'warn' ? 'warn' : 'neu'}`}>{st.label}</span>}
      </td>

      {view === 'ops' && <>
        <td className="r hoverable" {...hov('cover')}>
          <span className="cover"><i style={{ width: `${coverPct}%`, background: coverTone }} /><b style={{ left: '50%' }} /></span>
          <span className={cover == null ? 'zero' : cover < 2 ? 'red' : cover < 4 ? 'amber' : 'good'}>{cover == null ? '—' : `${cover.toFixed(1)}w`}</span>
        </td>
        <td className="r hoverable" {...hov('stock')}>{(p.total_qty ?? 0) > 0 ? Math.round(p.total_qty).toLocaleString() : <span className="zero">0</span>}</td>
        <td className="r hoverable" {...hov('expiry')}>{p.expiry_days == null
          ? <span className="zero">—</span>
          : p.expiry_days < 0 ? <span className="red">expired</span>
          : <span className={p.expiry_days < EXPIRY_SOON_DAYS ? 'amber' : ''}>{p.expiry_days}d</span>}</td>
        <td className="r hoverable" {...hov('demand')}>{(p.sales_120d ?? 0) > 0 ? p.sales_120d!.toLocaleString() : <span className="zero">—</span>}</td>
        <td className="r hoverable" {...hov('suppliers')}>{p.primary_cost != null ? money(p.primary_cost) : <span className="zero">—</span>}</td>
        <td className="r hoverable" {...hov('channels')}>{sellPrice != null ? money(sellPrice) : <span className="zero">—</span>}</td>
        <td className={`r hoverable ${gp == null ? '' : gp >= (p.gp_floor ?? 0) ? 'good' : 'amber'}`} {...hov('channels')}>{pct(gp)}</td>
      </>}

      {view === 'money' && <>
        <td className="r hoverable" {...hov('suppliers')}>{p.primary_cost != null ? money(p.primary_cost) : <span className="zero">—</span>}</td>
        <td className="r hoverable" {...hov('unitcost')}>{p.unit_cost != null ? money(p.unit_cost) : <span className="zero">—</span>}</td>
        <td className={`r hoverable ${p.mbb_unit_cost != null && p.unit_cost != null && p.mbb_unit_cost < p.unit_cost ? 'good' : ''}`} {...hov('bulk')}>
          {p.mbb_unit_cost != null ? money(p.mbb_unit_cost) : <span className="zero">—</span>}
        </td>
        <td className="r hoverable" {...hov('costtohit')}>{margin?.cost_to_hit != null ? money(margin.cost_to_hit, 0) : <span className="zero">—</span>}</td>
        {(['clinic', 'shopify', 'hktv'] as const).map(ch => {
          const cell = margin?.ch?.[ch]
          const net = cell?.nb ?? null   // net-after-fees at the basic (non-bulk) cost
          if (marginsLoading && !margin) return <td key={ch} className="r"><span className="skel" style={{ display: 'inline-block', width: 34, height: 9 }} /></td>
          if (cell?.price == null) return <td key={ch} className="r"><span className="zero">not listed</span></td>
          return <td key={ch} className={`r hoverable ${net == null ? '' : net >= (p.gp_floor ?? 0) ? 'good' : 'red'}`} {...hov('channels')}>{pct(net)}</td>
        })}
      </>}

      {view === 'dq' && <>
        <td className="hoverable" {...hov('grade')}><span className={`chip ${p.data_grade === 'A' ? 'ok' : 'warn'}`}>{p.data_grade ?? '—'}</span></td>
        <td>{p.uom_verified_at ? <span className="chip ok">✓ {p.uom_verified_by ?? 'verified'}</span> : <span className="zero">—</span>}</td>
        <td className="hoverable" {...hov('costsrc')}>{p.cost_source ? <span className={`chip ${p.cost_source === 'catalogue' ? 'ok' : 'acc'}`}>{p.cost_source.toUpperCase()}</span> : <span className="zero">—</span>}</td>
        <td className="r hoverable" {...hov('costsrc')}>{(() => { const d = daysSince(p.cost_last_updated); return d == null ? <span className="zero">—</span> : <span className={d > 90 ? 'amber' : ''}>{d}d</span> })()}</td>
        <td>{p.storage_rule === 'clinic_only' ? <span className="chip neu">clinic only</span> : <span className="sub">any location</span>}</td>
        <td>{missing.length ? missing.map(m => <span key={m} className="chip warn" style={{ marginRight: 4 }}>{m}</span>) : <span className="sub">nothing</span>}</td>
      </>}

      <td><button className="lnk" onClick={onOpen} title="Open SKU">›</button></td>
    </tr>
  )
}

function SelectAllBox({ skus }: { skus: string[] }) {
  const size = useSelSize()
  const all = skus.length > 0 && skus.every(s => selectionStore.has(s))
  return (
    <input type="checkbox" checked={all} aria-label="Select all rows"
      ref={el => { if (el) el.indeterminate = !all && size > 0 }}
      onChange={() => selectionStore.setMany(skus, !all)} />
  )
}

function SelectionBar({ total, allSkus, onApplied, editable }: {
  total: number; allSkus: string[]; onApplied: () => void; editable: boolean
}) {
  const n = useSelSize()
  const [busy, setBusy] = useState(false)
  if (n === 0 || !editable) return null

  async function setStatus(status: string) {
    const skus = selectionStore.list()
    if (status === 'DISCONTINUED') {
      const ok = await confirmDialog({
        title: `Discontinue ${skus.length} SKU${skus.length === 1 ? '' : 's'}?`,
        message: `They stop appearing in the stocked-or-moving scope and in channel pushes. This is reversible from here or the SKU page.\n\nIncludes: ${skus.slice(0, 5).join(' · ')}${skus.length > 5 ? ` + ${skus.length - 5} more` : ''}`,
        confirmLabel: `Discontinue ${skus.length}`, danger: true,
      })
      if (!ok) return
    }
    setBusy(true)
    let failed = 0
    for (const sku of skus) {
      const r = await fetch(`${API}/products/${skuToPath(sku)}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ status }),
      }).catch(() => null)
      if (!r?.ok) failed++
    }
    setBusy(false)
    selectionStore.clear()
    onApplied()
    if (failed) toast.error(`${failed} of ${skus.length} could not be updated`)
    else toast.success(`${skus.length} SKU${skus.length === 1 ? '' : 's'} set to ${status.toLowerCase()}`)
  }

  return (
    <div className="selbar">
      <b>{n} selected</b>
      <span className="sub" style={{ color: 'var(--accent-ink)' }}>of {total.toLocaleString()} matching</span>
      <button className="btn sm" disabled={busy} onClick={() => setStatus('ACTIVE')}>Set active</button>
      <button className="btn sm" disabled={busy} onClick={() => setStatus('INACTIVE')}>Set inactive</button>
      <button className="btn sm" disabled={busy} onClick={() => setStatus('DISCONTINUED')} style={{ color: 'var(--red)', borderColor: '#F1CDC9' }}>Discontinue…</button>
      <button className="lnk" onClick={() => selectionStore.setMany(allSkus, true)}>Select all {total.toLocaleString()}</button>
      {busy && <span className="sub" style={{ color: 'var(--accent-ink)' }}>applying…</span>}
      <button className="lnk" style={{ marginLeft: 'auto' }} onClick={() => selectionStore.clear()}>Clear</button>
    </div>
  )
}

function SkeletonTable({ view }: { view: ViewId }) {
  const cols = view === 'ops' ? 5 : view === 'money' ? 6 : 5
  return (
    <table>
      <tbody>
        {Array.from({ length: 8 }, (_, i) => (
          <tr key={i}>
            <td style={{ width: 30 }}><span className="skel" style={{ display: 'block', width: 13, height: 13 }} /></td>
            <td><span className="skel" style={{ display: 'block', width: `${55 + (i % 3) * 12}%` }} />
              <span className="skel" style={{ display: 'block', width: '30%', height: 7, marginTop: 5 }} /></td>
            <td><span className="skel" style={{ display: 'block', width: 62 }} /></td>
            {Array.from({ length: cols }, (_, c) => (
              <td key={c} className="r"><span className="skel" style={{ display: 'inline-block', width: 40 }} /></td>
            ))}
            <td />
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** Scope picker. Each segment carries its own size, so the cost of widening is
 *  visible before you click, and hovering explains the rule in place rather
 *  than in a tooltip you have to hunt for. */
function ScopeSwitch({ scope, onPick, counts, settled }: {
  scope: ScopeId; onPick: (s: ScopeId) => void; counts: ScopeCounts; settled: boolean
}) {
  const [hint, setHint] = useState<ScopeId | null>(null)
  const shown = SCOPES.find(s => s.id === (hint ?? scope))!
  const n = (id: ScopeId) => (id === 'working' ? counts.working : id === 'active' ? counts.active : counts.all)
  return (
    <div style={{ position: 'relative' }} onMouseLeave={() => setHint(null)}
      onBlur={e => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setHint(null) }}>
      <div className="scope2" role="group" aria-label="Catalogue scope">
        {SCOPES.map(s => (
          <button key={s.id} className={scope === s.id ? 'on' : ''} aria-pressed={scope === s.id}
            onClick={() => onPick(s.id)} onMouseEnter={() => setHint(s.id)} onFocus={() => setHint(s.id)}>
            <span className="sl">{s.label}</span>
            <span className="sn">{settled ? n(s.id).toLocaleString() : <span className="skel" style={{ display: 'inline-block', width: 30, height: 8 }} />}</span>
          </button>
        ))}
      </div>
      {hint && (
        <div className="inv2-pop scopehint">
          <div className="ph">{shown.label}{hint === scope ? ' · showing now' : ''}</div>
          <div className="pb">
            <div style={{ lineHeight: 1.55 }}>{shown.rule}</div>
            {settled && <div className="pmeta" style={{ marginTop: 6 }}>{shown.delta(counts)}</div>}
          </div>
          <div className="pf">{shown.use}</div>
        </div>
      )}
    </div>
  )
}

function EmptyState({ search, scope, outsideScope, hasFilters, onClearFilters, onWiden }: {
  search: string; scope: ScopeId; outsideScope: number; hasFilters: boolean
  onClearFilters: () => void; onWiden: () => void
}) {
  if (search.trim() && outsideScope > 0) {
    return (
      <div className="empty">
        <div className="et">No match in this scope</div>
        <div>“{search.trim()}” doesn’t match anything here, but {outsideScope.toLocaleString()} row{outsideScope === 1 ? '' : 's'} match in the full catalogue.</div>
        <button className="btn pri" style={{ marginTop: 12 }} onClick={onWiden}>Search everything</button>
      </div>
    )
  }
  if (hasFilters) {
    return (
      <div className="empty">
        <div className="et">No SKUs match these filters</div>
        <div>Try removing one, or widen the scope.</div>
        <button className="btn" style={{ marginTop: 12 }} onClick={onClearFilters}>Clear filters</button>
      </div>
    )
  }
  if (scope !== 'all') {
    return (
      <div className="empty">
        <div className="et">Nothing in this scope</div>
        <div>Nothing here is stocked or selling yet.</div>
        <button className="btn pri" style={{ marginTop: 12 }} onClick={onWiden}>Show the whole catalogue</button>
      </div>
    )
  }
  return <div className="empty"><div className="et">No products</div><div>The catalogue is empty.</div></div>
}

function FiltersPanel(props: {
  categories: string[]; suppliers: Supplier[]
  category: string[]; setCategory: (f: (prev: string[]) => string[]) => void
  supplier: string; setSupplier: (s: string) => void
  stockFilter: string; setStockFilter: (s: string) => void
  gradeFilter: string; setGradeFilter: (s: string) => void
  attention: AttentionId | null; setAttention: (a: AttentionId | null) => void
  channelFilter: string; setChannelFilter: (s: string) => void
  storageFilter: string; setStorageFilter: (s: string) => void
  heroFilter: string; setHeroFilter: (s: string) => void
  gapFilter: string; setGapFilter: (s: string) => void
  expiryFilter: string; setExpiryFilter: (s: string) => void
  collections: { id: number; name: string; count: number }[]
  collectionId: number | null; setCollectionId: (id: number | null) => void
  resultCount: number; onClose: () => void; onReset: () => void
}) {
  const seg = (value: string, current: string, set: (v: string) => void, label: string) => (
    <button className={current === value ? 'on' : ''} onClick={() => set(value)}>{label}</button>
  )
  // The panel hangs off a toolbar that sits ~270px down the page, so a fixed
  // vh cap still runs off the bottom. Measure the real room left instead.
  const panelRef = useRef<HTMLDivElement | null>(null)
  useLayoutEffect(() => {
    const fit = () => {
      const el = panelRef.current
      if (!el) return
      const room = window.innerHeight - el.getBoundingClientRect().top - 16
      el.style.setProperty('--favail', `${Math.max(260, room)}px`)
    }
    fit()
    window.addEventListener('resize', fit)
    window.addEventListener('scroll', fit, true)
    return () => { window.removeEventListener('resize', fit); window.removeEventListener('scroll', fit, true) }
  }, [])
  return (
    <div className="fpanel" ref={panelRef} onClick={e => e.stopPropagation()}>
      <div className="dh" style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
        <span className="dt" style={{ fontSize: 14 }}>Filters</span>
        <button className="lnk" style={{ marginLeft: 'auto', fontSize: 11.5 }} onClick={props.onReset}>reset</button>
      </div>
      <div className="db">
        <div className="fgrid">
          <div>
            <span className="flab">Category</span>
            <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap', maxHeight: 96, overflowY: 'auto' }}>
              {props.categories.map(c => (
                <span key={c} className={`chip ${props.category.includes(c) ? 'acc' : 'neu'}`} style={{ cursor: 'pointer' }}
                  onClick={() => props.setCategory(prev => prev.includes(c) ? prev.filter(x => x !== c) : [...prev, c])}>{c}</span>
              ))}
            </div>
          </div>
          <div>
            <span className="flab">Supplier</span>
            <select className="fin" value={props.supplier} onChange={e => props.setSupplier(e.target.value)}>
              <option value="All">All suppliers</option>
              {props.suppliers.map(s => <option key={s.id} value={s.name}>{s.name}</option>)}
            </select>
          </div>
        </div>
        <div className="fgrid" style={{ marginTop: 12 }}>
          <div>
            <span className="flab">Channel</span>
            <span className="seg">
              {seg('any', props.channelFilter, props.setChannelFilter, 'Any')}
              {seg('clinic', props.channelFilter, props.setChannelFilter, 'Clinic')}
              {seg('shopify', props.channelFilter, props.setChannelFilter, 'Shopify')}
              {seg('hktv', props.channelFilter, props.setChannelFilter, 'HKTV')}
            </span>
          </div>
          <div>
            <span className="flab">Collection</span>
            <select className="fin" value={props.collectionId ?? ''}
              onChange={e => props.setCollectionId(e.target.value ? Number(e.target.value) : null)}>
              <option value="">All collections</option>
              {props.collections.map(c => <option key={c.id} value={c.id}>{c.name} ({c.count})</option>)}
            </select>
          </div>
        </div>
        <div className="fgrid" style={{ marginTop: 12 }}>
          <div>
            <span className="flab">Stock</span>
            <span className="seg">
              {seg('any', props.stockFilter, props.setStockFilter, 'Any')}
              {seg('in', props.stockFilter, props.setStockFilter, 'In stock')}
              {seg('out', props.stockFilter, props.setStockFilter, 'Out')}
            </span>
          </div>
          <div>
            <span className="flab">Storage</span>
            <span className="seg">
              {seg('any', props.storageFilter, props.setStorageFilter, 'Any')}
              {seg('clinic_only', props.storageFilter, props.setStorageFilter, 'Clinic only')}
              {seg('any_loc', props.storageFilter, props.setStorageFilter, 'Warehouse OK')}
            </span>
          </div>
        </div>
        <div className="fgrid" style={{ marginTop: 12 }}>
          <div>
            <span className="flab">Hero</span>
            <span className="seg">
              {seg('any', props.heroFilter, props.setHeroFilter, 'Any')}
              {seg('hero', props.heroFilter, props.setHeroFilter, '★ Hero only')}
              {seg('not_hero', props.heroFilter, props.setHeroFilter, 'Not hero')}
            </span>
          </div>
          <div>
            <span className="flab">Data quality</span>
            <span className="seg">
              {seg('any', props.gradeFilter, props.setGradeFilter, 'Any')}
              {seg('a', props.gradeFilter, props.setGradeFilter, 'Grade A')}
              {seg('c', props.gradeFilter, props.setGradeFilter, 'Grade C')}
              {seg('verified', props.gradeFilter, props.setGradeFilter, 'Verified')}
              {seg('unverified', props.gradeFilter, props.setGradeFilter, 'Unverified')}
            </span>
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <span className="flab">Batch expiry</span>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            {Object.entries(EXPIRY_LABEL).map(([k, label]) => (
              <span key={k} className={`chip ${props.expiryFilter === k ? 'acc' : 'neu'}`} style={{ cursor: 'pointer' }}
                onClick={() => props.setExpiryFilter(props.expiryFilter === k ? 'any' : k)}>{label}</span>
            ))}
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <span className="flab">Missing data</span>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            {Object.entries(GAP_LABEL).map(([k, label]) => (
              <span key={k} className={`chip ${props.gapFilter === k ? 'acc' : 'neu'}`} style={{ cursor: 'pointer' }}
                onClick={() => props.setGapFilter(props.gapFilter === k ? 'any' : k)}>{label}</span>
            ))}
          </div>
        </div>
        <div style={{ marginTop: 12 }}>
          <span className="flab">Attention</span>
          <div style={{ display: 'flex', gap: 5, flexWrap: 'wrap' }}>
            {(Object.keys(ATT_LABEL) as AttentionId[]).map(a => (
              <span key={a} className={`chip ${props.attention === a ? 'acc' : 'neu'}`} style={{ cursor: 'pointer' }}
                onClick={() => props.setAttention(props.attention === a ? null : a)}>{ATT_LABEL[a]}</span>
            ))}
          </div>
        </div>
      </div>
      <div className="df">
        <span className="sub">Filters apply inside the current scope · they’re in the URL, so this list is shareable</span>
        <button className="btn pri" style={{ marginLeft: 'auto' }} onClick={props.onClose}>Show {props.resultCount.toLocaleString()} results</button>
      </div>
    </div>
  )
}

function DataTasksMenu({ onClose, onShortcuts }: { onClose: () => void; onShortcuts: () => void }) {
  const [busy, setBusy] = useState<string | null>(null)

  async function algoSync() {
    setBusy('algo')
    try {
      const r = await fetch(`${API}/sync/algo`, { method: 'POST', headers: authHeaders() })
      const d = await r.json().catch(() => ({}))
      if (r.ok) toast.success(`Live data synced — ${d.sales_skus_matched ?? 0} SKUs sales · ${d.expiry_batches_written ?? 0} expiry batches`)
      else toast.error(d.detail ?? 'Sync failed')
    } catch { toast.error('Sync failed') } finally { setBusy(null); onClose() }
  }
  async function competitors() {
    setBusy('comp')
    try {
      const r = await fetch(`${API}/competitors/refresh-all`, { method: 'POST', headers: authHeaders() })
      if (r.ok) { const d = await r.json(); toast.info(`Fetching ${d.count ?? 0} competitor price${d.count === 1 ? '' : 's'} in the background.`) }
      else toast.error('Could not start competitor price fetch')
    } catch { toast.error('Could not start competitor price fetch') } finally { setBusy(null); onClose() }
  }
  async function pushSheet() {
    setBusy('push')
    try {
      const dr = await fetch(`${API}/sync/push-sheet?dry_run=true`, { method: 'POST', headers: authHeaders() })
      const p = await dr.json().catch(() => ({}))
      if (!dr.ok) { toast.error(`Push preview failed: ${p.detail ?? dr.status}`); return }
      const ok = await confirmDialog({
        title: 'Push to the reporting sheet?',
        message: `Writes ${p.written_cells ?? p.cells ?? '?'} cells across ${p.written_rows ?? p.rows ?? '?'} rows to “${p.target?.tab ?? 'the sheet'}”.\n\nThis writes live cells.`,
        confirmLabel: 'Push live',
      })
      if (!ok) return
      const res = await fetch(`${API}/sync/push-sheet?dry_run=false`, { method: 'POST', headers: authHeaders() })
      const r = await res.json().catch(() => ({}))
      if (res.ok) toast.success(`Pushed ${r.written_rows} rows (${r.written_cells} cells) to “${r.target?.tab}”.`)
      else toast.error(`Push failed: ${r.detail ?? res.status}`)
    } catch { toast.error('Push error') } finally { setBusy(null); onClose() }
  }

  return (
    <>
      <div style={{ position: 'fixed', inset: 0, zIndex: 40 }} onClick={onClose} />
      <div className="menu" onClick={e => e.stopPropagation()}>
        <button className="mi" onClick={algoSync} disabled={!!busy}>
          <span>⟳</span><span><span className="mt">Sync live sales data</span>
            <span className="mx">{busy === 'algo' ? 'syncing…' : 'Real sales + expiry from algo-dashboard'}</span></span>
        </button>
        <button className="mi" onClick={competitors} disabled={!!busy}>
          <span>🏷</span><span><span className="mt">Fetch competitor prices</span>
            <span className="mx">{busy === 'comp' ? 'starting…' : 'Re-scrapes every tracked competitor URL'}</span></span>
        </button>
        <button className="mi" onClick={pushSheet} disabled={!!busy}>
          <span>↑</span><span><span className="mt">Push to reporting sheet</span>
            <span className="mx">{busy === 'push' ? 'previewing…' : 'Writes IMS-owned columns · previews first'}</span></span>
        </button>
        <div style={{ borderTop: '1px solid var(--line2)', margin: '4px 0' }} />
        <button className="mi" onClick={onShortcuts}>
          <span>⌘</span><span><span className="mt">Keyboard shortcuts</span><span className="mx">Press ?</span></span>
        </button>
      </div>
    </>
  )
}

// ── export ────────────────────────────────────────────────────────────────
type ExportCol = { key: string; label: string; value: (p: Product) => string | number | null | undefined }
const csvEsc = (v: unknown) => {
  const s = v == null ? '' : String(v)
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s
}
const STANDARD_COLS: ExportCol[] = [
  { key: 'sku_code', label: 'SKU', value: p => p.sku_code },
  { key: 'name', label: 'Name', value: p => p.name },
  { key: 'brand', label: 'Brand', value: p => p.brand },
  { key: 'category', label: 'Category', value: p => p.category },
  { key: 'status', label: 'Status', value: p => p.status },
  { key: 'supplier_name', label: 'Supplier', value: p => p.supplier_name },
  { key: 'supplier_sku', label: 'Supplier SKU', value: p => p.supplier_sku },
  { key: 'pack_cost', label: 'Pack cost', value: p => p.primary_cost },
  { key: 'unit_cost', label: 'Unit cost', value: p => p.unit_cost },
  { key: 'units_per_pack', label: 'Units per pack', value: p => p.units_per_pack },
  { key: 'total_qty', label: 'On hand', value: p => p.total_qty },
  { key: 'clinic_qty', label: 'Clinic qty', value: p => p.clinic_qty },
  { key: 'warehouse_qty', label: 'Warehouse qty', value: p => p.warehouse_qty },
  { key: 'woc', label: 'Cover (weeks)', value: p => p.woc },
  { key: 'sales_120d', label: 'Sold 120d', value: p => p.sales_120d },
  { key: 'data_grade', label: 'Data grade', value: p => p.data_grade },
]
const EXTRA_COLS: ExportCol[] = [
  { key: 'uom', label: 'Sell unit', value: p => p.uom },
  { key: 'pack_unit', label: 'Pack unit', value: p => p.pack_unit },
  { key: 'weight_g', label: 'Weight (g)', value: p => p.weight_g },
  { key: 'storage_rule', label: 'Storage rule', value: p => p.storage_rule },
  { key: 'hero_sku', label: 'Hero', value: p => (p.hero_sku ? 'yes' : 'no') },
  { key: 'rrp', label: 'RRP', value: p => p.rrp },
  { key: 'mbb_unit_cost', label: 'Best bulk cost', value: p => p.mbb_unit_cost },
  { key: 'gp_floor', label: 'GP floor', value: p => p.gp_floor },
  { key: 'weekly_demand', label: 'Weekly demand', value: p => p.weekly_demand },
  { key: 'cost_source', label: 'Cost source', value: p => p.cost_source },
  { key: 'cost_last_updated', label: 'Cost updated', value: p => (p.cost_last_updated ?? '').slice(0, 10) },
  { key: 'uom_verified_at', label: 'Pack verified', value: p => (p.uom_verified_at ?? '').slice(0, 10) },
  { key: 'expiry_date', label: 'Soonest expiry', value: p => (p.expiry_batches ?? [])[0]?.expiry_date ?? '' },
  { key: 'expiry_days', label: 'Days to expiry', value: p => p.expiry_days ?? '' },
  { key: 'notes', label: 'Notes', value: p => p.notes },
]
// The round-trip set: exactly what Batch update accepts back.
const ROUNDTRIP_COLS: ExportCol[] = [
  { key: 'sku_code', label: 'sku_code', value: p => p.sku_code },
  { key: 'name', label: 'name', value: p => p.name },
  { key: 'brand', label: 'brand', value: p => p.brand },
  { key: 'category', label: 'category', value: p => p.category },
  { key: 'status', label: 'status', value: p => p.status },
  { key: 'hero_sku', label: 'hero_sku', value: p => (p.hero_sku ? 'true' : 'false') },
  { key: 'uom', label: 'uom', value: p => p.uom },
  { key: 'pack_unit', label: 'pack_unit', value: p => p.pack_unit },
  { key: 'units_per_pack', label: 'units_per_pack', value: p => p.units_per_pack },
  { key: 'min_purchase_qty', label: 'min_purchase_qty', value: p => p.min_purchase_qty },
  { key: 'min_sellable_qty', label: 'min_sellable_qty', value: p => p.min_sellable_qty },
  { key: 'weight_g', label: 'weight_g', value: p => p.weight_g },
  { key: 'weight_unit', label: 'weight_unit', value: p => p.weight_unit },
  { key: 'supplier_name', label: 'supplier_name', value: p => p.supplier_name },
  { key: 'basic_cost', label: 'basic_cost', value: p => p.primary_cost },
  { key: 'rrp', label: 'rrp', value: p => p.rrp },
  { key: 'notes', label: 'notes', value: p => p.notes },
]

function ExportDialog({ rows, scopeRows, scopeLabel, onClose }: {
  rows: Product[]; scopeRows: Product[]; scopeLabel: string; onClose: () => void
}) {
  const [target, setTarget] = useState<'filtered' | 'scope'>('filtered')
  const [preset, setPreset] = useState<'standard' | 'everything' | 'roundtrip'>('standard')
  const [extra, setExtra] = useState<Set<string>>(new Set())
  const [detailsOpen, setDetailsOpen] = useState(false)

  const cols = preset === 'roundtrip' ? ROUNDTRIP_COLS
    : preset === 'everything' ? [...STANDARD_COLS, ...EXTRA_COLS]
    : [...STANDARD_COLS, ...EXTRA_COLS.filter(c => extra.has(c.key))]
  const data = target === 'filtered' ? rows : scopeRows

  function download() {
    const lines = [cols.map(c => csvEsc(c.label)).join(',')]
    for (const p of data) lines.push(cols.map(c => csvEsc(c.value(p))).join(','))
    const blob = new Blob([lines.join('\n')], { type: 'text/csv;charset=utf-8;' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `ims_${preset === 'roundtrip' ? 'roundtrip_' : ''}${new Date().toISOString().slice(0, 10)}.csv`
    a.click()
    URL.revokeObjectURL(url)
    toast.success(`Exported ${data.length.toLocaleString()} rows`)
    onClose()
  }

  return (
    <div className="inv2-ovl" onClick={onClose}>
      <div className="inv2" onClick={e => e.stopPropagation()} style={{ width: 600, maxWidth: '100%' }}>
        <div className="dlg">
          <div className="dh">
            <div className="dt">Export</div>
            <div className="dsub">{rows.length.toLocaleString()} rows match your current filters, inside {scopeLabel.toLowerCase()}.</div>
          </div>
          <div className="db">
            <span className="flab">What to export</span>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 14 }}>
              <div className={`pickcard${target === 'filtered' ? ' on' : ''}`} onClick={() => setTarget('filtered')}>
                <div className="pt">These {rows.length.toLocaleString()} rows</div><div className="sub">what you’re looking at</div>
              </div>
              <div className={`pickcard${target === 'scope' ? ' on' : ''}`} onClick={() => setTarget('scope')}>
                <div className="pt">All {scopeRows.length.toLocaleString()} in scope</div><div className="sub">ignores filters</div>
              </div>
            </div>
            <span className="flab">Columns</span>
            <span className="seg">
              <button className={preset === 'standard' ? 'on' : ''} onClick={() => setPreset('standard')}>Standard</button>
              <button className={preset === 'everything' ? 'on' : ''} onClick={() => setPreset('everything')}>Everything</button>
              <button className={preset === 'roundtrip' ? 'on' : ''} onClick={() => setPreset('roundtrip')}>Round-trip for Batch update</button>
            </span>
            {preset === 'roundtrip' && (
              <div className="sub" style={{ marginTop: 9, lineHeight: 1.5 }}>
                Exports the editable database fields exactly as stored, keyed by <span className="mono">sku_code</span> — edit and re-upload through Batch update.
              </div>
            )}
            {preset === 'standard' && (
              <div style={{ marginTop: 11 }}>
                <button className="lnk" style={{ fontSize: 11.5 }} onClick={() => setDetailsOpen(o => !o)}>
                  {detailsOpen ? 'Hide' : 'Add'} optional columns ({EXTRA_COLS.length})
                </button>
                {detailsOpen && (
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '3px 12px', marginTop: 8 }}>
                    {EXTRA_COLS.map(c => (
                      <label key={c.key} style={{ display: 'flex', gap: 6, alignItems: 'center', fontSize: 11.5, color: 'var(--ink2)' }}>
                        <input type="checkbox" checked={extra.has(c.key)}
                          onChange={() => setExtra(prev => { const n = new Set(prev); n.has(c.key) ? n.delete(c.key) : n.add(c.key); return n })} />
                        {c.label}
                      </label>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
          <div className="df">
            <span className="sub">{data.length.toLocaleString()} rows · {cols.length} columns · CSV</span>
            <button className="btn" style={{ marginLeft: 'auto' }} onClick={onClose}>Cancel</button>
            <button className="btn pri" onClick={download}>Download CSV</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── batch update ──────────────────────────────────────────────────────────
interface ImportRow { sku_code: string; status: string; changes?: Record<string, { from: unknown; to: unknown }>; error?: string; ignored?: string[] }
interface ImportResult { summary: Record<string, number>; rows: ImportRow[] }

function BatchDialog({ onClose, onApplied }: { onClose: () => void; onApplied: () => void }) {
  const [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false)
  const [preview, setPreview] = useState<ImportResult | null>(null)
  const [done, setDone] = useState<ImportResult | null>(null)
  const [markVerified, setMarkVerified] = useState(true)
  const [tab, setTab] = useState<'changes' | 'errors' | 'not_found'>('changes')
  const [dragOver, setDragOver] = useState(false)

  async function send(dry: boolean) {
    if (!file) return
    setBusy(true)
    try {
      const fd = new FormData(); fd.append('file', file)
      const r = await fetch(`${API}/products/import-csv?dry_run=${dry}&mark_verified=${markVerified}`, { method: 'POST', headers: authHeaders(), body: fd })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { toast.error(d.detail ?? 'Import failed'); return }
      if (dry) { setPreview(d); setDone(null) }
      else { setDone(d); toast.success(`Updated ${d.summary.updated} SKU(s)`); onApplied() }
    } catch { toast.error('Import failed') } finally { setBusy(false) }
  }

  const res = done ?? preview
  const changed = (res?.rows ?? []).filter(r => r.status === 'updated' && r.changes)
  const errors = (res?.rows ?? []).filter(r => r.status === 'error' || r.error)
  const notFound = (res?.rows ?? []).filter(r => r.status === 'not_found')
  // group changes by field so "214 cost changes" reads at a glance
  const byField = useMemo(() => {
    const map = new Map<string, { sku: string; from: unknown; to: unknown }[]>()
    for (const row of changed) {
      for (const [field, c] of Object.entries(row.changes ?? {})) {
        map.set(field, [...(map.get(field) ?? []), { sku: row.sku_code, from: c.from, to: c.to }])
      }
    }
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [changed])

  const fmtVal = (v: unknown) => (v == null || v === '' ? '∅' : String(v))
  const movePct = (from: unknown, to: unknown) => {
    const a = Number(from), b = Number(to)
    if (!Number.isFinite(a) || !Number.isFinite(b) || a === 0) return null
    return ((b - a) / a) * 100
  }
  const upd = preview?.summary.updated ?? 0

  return (
    <div className="inv2-ovl" onClick={onClose}>
      <div className="inv2" onClick={e => e.stopPropagation()} style={{ width: 680, maxWidth: '100%' }}>
        <div className="dlg">
          <div className="dh">
            <div className="dt">{res ? 'Review changes' : 'Batch update from CSV'}</div>
            <div className="dsub">
              {res
                ? <>{file?.name} · {res.summary.total} rows</>
                : <>Keyed by <span className="mono">sku_code</span>. Empty cells are left unchanged; read-only columns (quantities, prices, GP%, supplier, cover) are ignored.</>}
            </div>
          </div>

          <div className="db">
            {!res && (
              <>
                <div className={`drop${dragOver ? ' over' : ''}`}
                  onDragOver={e => { e.preventDefault(); setDragOver(true) }}
                  onDragLeave={() => setDragOver(false)}
                  onDrop={e => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files?.[0]; if (f) { setFile(f); setPreview(null); setDone(null) } }}>
                  {file ? <><b style={{ color: 'var(--ink)' }}>{file.name}</b><div className="sub" style={{ marginTop: 4 }}>{(file.size / 1024).toFixed(0)} KB · ready to preview</div></>
                    : <>Drop a CSV here, or{' '}
                      <label className="lnk" style={{ cursor: 'pointer' }}>browse
                        <input type="file" accept=".csv,text/csv" style={{ display: 'none' }}
                          onChange={e => { setFile(e.target.files?.[0] ?? null); setPreview(null); setDone(null) }} />
                      </label></>}
                  <div className="sub" style={{ marginTop: 7 }}>
                    Not sure of the format? Export with the <b>Round-trip</b> preset — that file re-uploads here unchanged.
                  </div>
                </div>
                <label style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 13, cursor: 'pointer' }}>
                  <input type="checkbox" checked={markVerified} onChange={e => setMarkVerified(e.target.checked)} />
                  <span style={{ fontSize: 12.5, fontWeight: 650, color: 'var(--good)' }}>Mark every processed SKU as HITL-verified</span>
                </label>
              </>
            )}

            {res && (
              <>
                <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', marginBottom: 11 }}>
                  <span className="chip ok">{res.summary.updated} will update</span>
                  <span className="chip neu">{res.summary.unchanged} unchanged</span>
                  {res.summary.not_found > 0 && <span className="chip warn">{res.summary.not_found} not found</span>}
                  {res.summary.errors > 0 && <span className="chip bad">{res.summary.errors} errors</span>}
                  {res.summary.verified > 0 && <span className="chip ok" style={{ marginLeft: 'auto' }}>{done ? 'verified' : 'will verify'} {res.summary.verified}</span>}
                </div>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 9 }}>
                  <span className="seg">
                    <button className={tab === 'changes' ? 'on' : ''} onClick={() => setTab('changes')}>Changes</button>
                    <button className={tab === 'errors' ? 'on' : ''} onClick={() => setTab('errors')}>Errors</button>
                    <button className={tab === 'not_found' ? 'on' : ''} onClick={() => setTab('not_found')}>Not found</button>
                  </span>
                  {tab === 'changes' && <span className="sub" style={{ marginLeft: 'auto' }}>grouped by field</span>}
                </div>
                <div className="diff">
                  {tab === 'changes' && byField.map(([field, list]) => (
                    <div key={field}>
                      <div className="diffhead">{field.replace(/_/g, ' ')} — {list.length} row{list.length === 1 ? '' : 's'}</div>
                      {list.slice(0, 25).map((c, i) => {
                        const mv = movePct(c.from, c.to)
                        return (
                          <div className="diffrow" key={i}>
                            <span className="mono">{c.sku}</span>
                            <span className="sub">{field.replace(/_/g, ' ')}</span>
                            <span>{fmtVal(c.from)} → <b>{fmtVal(c.to)}</b>
                              {mv != null && <span className={Math.abs(mv) > 20 ? 'red' : mv < 0 ? 'good' : 'amber'} style={{ marginLeft: 6 }}>
                                {mv > 0 ? '+' : ''}{mv.toFixed(1)}%
                              </span>}
                            </span>
                          </div>
                        )
                      })}
                      {list.length > 25 && <div className="diffrow"><span className="sub" style={{ gridColumn: '1 / -1' }}>+ {list.length - 25} more</span></div>}
                    </div>
                  ))}
                  {tab === 'changes' && byField.length === 0 && <div className="diffrow"><span className="sub" style={{ gridColumn: '1 / -1' }}>No field changes in this file.</span></div>}
                  {tab === 'errors' && (errors.length ? errors.map((r, i) => (
                    <div className="diffrow" key={i}><span className="mono">{r.sku_code}</span><span className="red">error</span><span className="red">{r.error}</span></div>
                  )) : <div className="diffrow"><span className="sub" style={{ gridColumn: '1 / -1' }}>No errors.</span></div>)}
                  {tab === 'not_found' && (notFound.length ? notFound.map((r, i) => (
                    <div className="diffrow" key={i}><span className="mono">{r.sku_code}</span><span className="sub">not found</span><span className="sub">no SKU with this code</span></div>
                  )) : <div className="diffrow"><span className="sub" style={{ gridColumn: '1 / -1' }}>Every row matched a SKU.</span></div>)}
                </div>
              </>
            )}
          </div>

          <div className="df">
            <span className="sub">
              {done ? `✓ Applied — ${done.summary.updated} updated${done.summary.verified ? `, ${done.summary.verified} verified` : ''}`
                : res ? `${upd} to update · errors are skipped, not blocking`
                : 'Nothing is written until you review the changes'}
            </span>
            <button className="btn" style={{ marginLeft: 'auto' }} onClick={res && !done ? () => { setPreview(null); setDone(null) } : onClose}>
              {done ? 'Close' : res ? 'Back' : 'Cancel'}
            </button>
            {!done && (
              res
                ? <button className="btn pri" disabled={busy || upd === 0} onClick={() => send(false)}>{busy ? 'Applying…' : `Apply ${upd} update${upd === 1 ? '' : 's'}`}</button>
                : <button className="btn pri" disabled={!file || busy} onClick={() => send(true)}>{busy ? 'Checking…' : 'Preview changes'}</button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function ShortcutsOverlay({ onClose }: { onClose: () => void }) {
  const keys: [string, string][] = [
    ['/', 'Focus search'], ['1 · 2 · 3', 'Operations · Money · Data quality'],
    ['F', 'Filters'], ['J / K', 'Move row focus'], ['Enter', 'Open the focused SKU'],
    ['X', 'Select the focused row'], ['E', 'Export'], ['Esc', 'Close · clear search · clear selection'],
  ]
  return (
    <div className="shorto" onClick={onClose}>
      <div className="box" onClick={e => e.stopPropagation()}>
        <div style={{ fontSize: 15, fontWeight: 750, marginBottom: 10 }}>Keyboard shortcuts</div>
        {keys.map(([k, v]) => (
          <div className="krow" key={k}><span>{v}</span><span className="kbd">{k}</span></div>
        ))}
      </div>
    </div>
  )
}

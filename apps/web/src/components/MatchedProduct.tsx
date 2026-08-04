/**
 * The product a catalogue row is being matched to — shown, not just named.
 *
 * The desk used to say "Matched → 50010319" and leave it there, which asks a
 * reviewer to confirm an identity they cannot see. Confirming meant opening the
 * SKU page in another tab, reading it, coming back, and remembering it. Several
 * hundred times per catalogue. So the honest options were to trust the matcher
 * blindly or to work very slowly, and neither is a review.
 *
 * WHAT A REVIEWER IS ACTUALLY DECIDING
 *
 * "This supplier line and our SKU are the same physical product." Four things
 * settle it, and they are ordered here by how often they settle it:
 *
 *   1. Is this supplier ALREADY linked to this product, under which code?
 *      A product already carrying `Alfamedic EN7502` and a row that says
 *      EN7502 is a confirmed match with nothing left to judge. A product
 *      linked to the same supplier under a DIFFERENT code is the sharpest
 *      warning the desk can give — usually a second pack size, occasionally
 *      the wrong product. This fact is invisible everywhere else in the desk
 *      and it is why the supplier block leads the card.
 *   2. Is it the same thing? Name, brand, category, species.
 *   3. Is it the same PACK? "100 tabs/box" against "box of 100 tablets".
 *      Pack mismatch is the classic silent error: the identity looks right
 *      and the per-unit cost comes out an order of magnitude wrong.
 *   4. What does it do to the money? Cost delta and the resulting margin.
 *
 * THE CARD FOLLOWS THE SELECTION
 *
 * It renders whichever product is currently in play — the existing match, or a
 * suggestion the reviewer just clicked. Picking suggestion 2 shows suggestion
 * 2's real entity immediately, which is what turns re-picking from a guess
 * into a comparison.
 */
import { useQuery } from '@tanstack/react-query'

import { fetchProduct } from '@/lib/review'
import type { Product } from '@/lib/types'

type SupplierLink = Product['all_suppliers'][number]

const money = (value: number | null | undefined, dp = 2) =>
  value == null ? null : value.toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp })

/** Cost per single sellable unit, so two differently-packed numbers compare. */
function perUnit(cost: number | null | undefined, unitsPerPack: number | null | undefined): number | null {
  if (cost == null) return null
  const per = unitsPerPack && unitsPerPack > 0 ? unitsPerPack : 1
  return cost / per
}

export interface MatchedProductProps {
  /** SKU to show: the confirmed match, or whichever suggestion is selected. */
  sku: string | null
  /** True when `sku` is a suggestion being previewed rather than the live match. */
  preview?: boolean
  /** The supplier this run belongs to — identifies the relevant link. */
  supplierId?: number | null
  /** The supplier's own code on the row under review. */
  rowSupplierSku?: string | null
  /** What the row says this costs, and per what. */
  rowCost?: number | null
  rowCostBasis?: string | null
}

export function MatchedProduct({
  sku, preview = false, supplierId, rowSupplierSku, rowCost, rowCostBasis,
}: MatchedProductProps) {
  const product = useQuery({
    queryKey: ['review-product', sku],
    queryFn: () => fetchProduct(sku!),
    enabled: !!sku,
    staleTime: 60_000,
  })

  if (!sku) return null
  if (product.isLoading) {
    return <div className="mprod"><div className="mprod-skel" /><div className="mprod-skel short" /></div>
  }
  if (product.isError || !product.data) {
    return (
      <div className="mprod">
        <div className="mprod-none">
          Couldn’t load <span className="mono">{sku}</span> — the match still stands, it just can’t be shown.
        </div>
      </div>
    )
  }

  const p = product.data
  const link: SupplierLink | undefined =
    (supplierId != null ? p.all_suppliers?.find(s => s.supplier_id === supplierId) : undefined)
    ?? (rowSupplierSku ? p.all_suppliers?.find(s => s.supplier_sku === rowSupplierSku) : undefined)

  // The three states the supplier block can be in, in order of how much they
  // tell you. `sameCode` is a confirmed identity; `otherCode` is the warning.
  const sameCode = !!link && !!rowSupplierSku && link.supplier_sku === rowSupplierSku
  const otherCode = !!link && !!rowSupplierSku && !!link.supplier_sku && link.supplier_sku !== rowSupplierSku

  const unitsPerPack = link?.units_per_pack ?? null
  const weNowPay = perUnit(link?.basic_cost ?? p.primary_cost, unitsPerPack)
  const rowPerUnit = perUnit(rowCost, unitsPerPack)
  const delta = weNowPay != null && rowPerUnit != null && weNowPay !== 0
    ? Math.round(((rowPerUnit - weNowPay) / weNowPay) * 1000) / 10
    : null

  // Clinic is the reference channel on this desk; fall back to any priced
  // channel, then to RRP.
  const sells = p.channels?.find(c => c.channel === 'clinic' && c.selling_price != null)?.selling_price
    ?? p.channels?.find(c => c.selling_price != null)?.selling_price
    ?? p.rrp
    ?? null
  // Margin at the NEW cost, which is the number the decision turns on — the
  // channel's own gp_pct is computed against the cost we pay today. Margins
  // are fractions throughout this codebase, gp_floor included.
  const margin = sells != null && sells !== 0 && rowPerUnit != null ? (sells - rowPerUnit) / sells : null
  const belowFloor = margin != null && p.gp_floor != null && margin < p.gp_floor

  const pack = [
    link?.units_per_pack ? `${link.units_per_pack} per ${p.pack_unit ?? 'pack'}` : p.pack_unit,
    p.uom ? `sold by ${p.uom}` : null,
  ].filter(Boolean).join(' · ')

  return (
    <div className="mprod">
      <div className="mprod-hd">
        <span>{preview ? 'If you pick this' : 'Our product'}</span>
        <a className="mono mprod-sku" href={`/sku/${encodeURIComponent(p.sku_code)}`} target="_blank" rel="noreferrer"
           title="Open the full SKU page in a new tab">{p.sku_code} ↗</a>
      </div>

      {/* 2 — is it the same thing? */}
      <div className="mprod-id">
        <b className="mprod-name">{p.name}</b>
        <div className="mprod-sub">
          {[p.brand, p.subcategory ?? p.category, p.species].filter(Boolean).join(' · ') || '—'}
        </div>
        <div className="mprod-pills">
          <span className={`bdg ${p.status === 'ACTIVE' ? 'ok' : p.status === 'DISCONTINUED' ? 'bad' : 'neu'}`}>
            {p.status.toLowerCase()}
          </span>
          {p.storage_rule === 'clinic_only' && <span className="bdg neu">clinic only</span>}
          {p.hero_sku && <span className="bdg acc">hero</span>}
          {pack && <span className="mprod-pack">{pack}</span>}
        </div>
      </div>

      {/* 1 — the supplier link: the fact that most often settles the match. */}
      <div className={`mprod-link${sameCode ? ' ok' : otherCode ? ' warn' : ''}`}>
        {sameCode && (
          <>
            <b>Already linked to this supplier</b>
            <div>
              as <span className="mono">{link!.supplier_sku}</span> — the same code this row carries.
              {link!.is_primary ? ' Primary supplier.' : ''}
            </div>
          </>
        )}
        {otherCode && (
          <>
            <b>Linked to this supplier under a different code</b>
            <div>
              we hold <span className="mono">{link!.supplier_sku}</span>, this row says{' '}
              <span className="mono">{rowSupplierSku}</span> — a second pack size, or the wrong product?
            </div>
          </>
        )}
        {!link && (
          <>
            <b>Not linked to this supplier yet</b>
            <div>approving this row creates the link{rowSupplierSku ? <> as <span className="mono">{rowSupplierSku}</span></> : null}.</div>
          </>
        )}
        {link && !rowSupplierSku && <div>linked as <span className="mono">{link.supplier_sku ?? '—'}</span></div>}
      </div>

      {/* 4 — what it does to the money. Per single unit on both sides, so a
          pack-priced row and a unit-priced product are actually comparable. */}
      <div className="mprod-rows">
        <div className="mprod-row">
          <span className="k">We pay now</span>
          <span className="v">
            {money(weNowPay) ?? '—'}{weNowPay != null && p.uom ? <span className="u"> /{p.uom}</span> : null}
            {link?.basic_cost == null && p.primary_cost != null && <span className="u"> (all suppliers)</span>}
          </span>
        </div>
        <div className="mprod-row">
          <span className="k">This row</span>
          <span className="v">
            {money(rowPerUnit) ?? '—'}{rowPerUnit != null && p.uom ? <span className="u"> /{p.uom}</span> : null}
            {rowCost != null && unitsPerPack && unitsPerPack > 1 && (
              <span className="u"> ({money(rowCost)}/{(rowCostBasis ?? 'pack').toLowerCase()})</span>
            )}
            {delta != null && (
              <b className={delta > 0 ? 'up' : delta < 0 ? 'down' : ''}>
                {'  '}{delta > 0 ? '+' : ''}{delta}%
              </b>
            )}
          </span>
        </div>
        <div className="mprod-row">
          <span className="k">Sells at</span>
          <span className="v">
            {money(sells) ?? <span className="u">not priced</span>}
            {margin != null && (
              <b className={belowFloor ? 'up' : 'down'}>{'  '}{(margin * 100).toFixed(1)}% GP</b>
            )}
            {belowFloor && <span className="u"> under the {(p.gp_floor * 100).toFixed(0)}% floor</span>}
          </span>
        </div>
        <div className="mprod-row">
          <span className="k">Stock · demand</span>
          <span className="v">
            {p.total_qty > 0
              ? `${p.clinic_qty} clinic · ${p.warehouse_qty} warehouse`
              : <span className="u">none on hand</span>}
            {p.weekly_demand > 0 && <span className="u">  ·  {p.weekly_demand.toFixed(1)}/wk</span>}
          </span>
        </div>
        {(link?.mbb_term_list?.length || link?.catalogue_term_list?.length) ? (
          <div className="mprod-row">
            <span className="k">Bulk deals</span>
            <span className="v">
              {(link?.mbb_term_list?.length ?? 0) + (link?.catalogue_term_list?.length ?? 0)} on this supplier
            </span>
          </div>
        ) : null}
      </div>
    </div>
  )
}

/** Scoped to .rdesk so it inherits the desk's tokens and type scale. */
export const MATCHED_PRODUCT_CSS = `
.rdesk .mprod{border:1px solid var(--line);border-radius:10px;background:var(--card);overflow:hidden}
.rdesk .mprod-hd{display:flex;align-items:center;gap:8px;padding:7px 11px;border-bottom:1px solid var(--line2);font-size:10px;font-weight:750;letter-spacing:.06em;text-transform:uppercase;color:var(--faint)}
.rdesk .mprod-sku{margin-left:auto;font-size:10.5px;color:var(--accent);text-decoration:none;letter-spacing:0;text-transform:none;font-weight:600}
.rdesk .mprod-sku:hover{text-decoration:underline}
.rdesk .mprod-id{padding:9px 11px 8px}
.rdesk .mprod-name{font-size:13px;color:var(--ink);line-height:1.32;display:block}
.rdesk .mprod-sub{font-size:11px;color:var(--muted);margin-top:2px}
.rdesk .mprod-pills{display:flex;flex-wrap:wrap;gap:5px;align-items:center;margin-top:7px}
.rdesk .mprod-pack{font-size:10.5px;color:var(--ink2);font-family:var(--mono)}
.rdesk .mprod-link{padding:8px 11px;border-top:1px solid var(--line2);background:var(--panel);font-size:11.5px;color:var(--ink2)}
.rdesk .mprod-link b{display:block;color:var(--ink);font-size:11.5px}
.rdesk .mprod-link div{margin-top:1px}
.rdesk .mprod-link.ok{background:var(--good-soft);color:#255E3A}
.rdesk .mprod-link.ok b{color:var(--good)}
.rdesk .mprod-link.warn{background:var(--amber-soft);color:#7A4A12}
.rdesk .mprod-link.warn b{color:var(--amber)}
.rdesk .mprod-rows{border-top:1px solid var(--line2)}
.rdesk .mprod-row{display:flex;gap:10px;padding:5px 11px;font-size:11.5px;border-bottom:1px solid var(--line2)}
.rdesk .mprod-row:last-child{border-bottom:none}
.rdesk .mprod-row .k{color:var(--faint);flex:0 0 96px}
.rdesk .mprod-row .v{color:var(--ink);font-weight:600;font-variant-numeric:tabular-nums;min-width:0}
.rdesk .mprod-row .v .u{color:var(--faint);font-weight:400}
.rdesk .mprod-row .v b.up{color:var(--amber)}
.rdesk .mprod-row .v b.down{color:var(--good)}
.rdesk .mprod-none{padding:10px 11px;font-size:11.5px;color:var(--muted)}
.rdesk .mprod-skel{height:12px;margin:11px;border-radius:4px;background:var(--line2)}
.rdesk .mprod-skel.short{width:55%}
.rdesk .mprod-more{display:flex;align-items:center;gap:7px;width:100%;background:none;border:none;padding:8px 11px;font:inherit;font-size:11.5px;color:var(--muted);cursor:pointer;text-align:left}
.rdesk .mprod-more:hover{color:var(--ink)}
`

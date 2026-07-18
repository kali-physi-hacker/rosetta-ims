import { C } from '@/lib/tokens'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useState, useEffect, useMemo } from 'react'
import type { Product, CategoryRule } from '@/lib/types'
import { authHeaders } from '@/lib/auth'
import { streamProducts } from '@/lib/streamProducts'
import { skuToPath } from '@/lib/sku'
import { API_BASE } from '@/lib/config'

const API = API_BASE

const GP_BAND = 0.10  // 10pp above floor = "comfortably compliant"

type Flag = 'below_floor' | 'borderline' | 'ok'

function getFlag(bestGp: number | null, gpFloor: number): Flag {
  if (bestGp === null) return 'below_floor'
  if (bestGp < gpFloor) return 'below_floor'
  if (bestGp < gpFloor + GP_BAND) return 'borderline'
  return 'ok'
}

const FLAG_STYLE: Record<Flag, { label: string; bg: string; color: string; border: string }> = {
  below_floor: { label: 'Below floor',  bg: C.badBg, color: C.redInk, border: '#FECACA' },
  borderline:  { label: 'Borderline',   bg: '#FFFBEB', color: C.amberInk, border: '#FCD34D' },
  ok:          { label: 'Approved',     bg: '#F0FDF4', color: C.green, border: '#BBF7D0' },
}

const CAT_ORDER = ['Medicine', 'Preventative', 'Supplement', 'Food', 'Pet Hygiene', 'Not-For-Sale']

export const Route = createFileRoute('/_authed/logic')({ component: LogicLayerPage })

function LogicLayerPage() {
  const [items, setItems]   = useState<Product[]>([])
  const [rules, setRules]   = useState<CategoryRule[]>([])
  const [loading, setLoading] = useState(true)
  const [catFilter, setCatFilter] = useState<string>('All')
  const [flagFilter, setFlagFilter] = useState<Flag | 'All'>('All')
  const [sortCol, setSortCol] = useState<'name' | 'category' | 'gp' | 'flag'>('flag')
  const [sortAsc, setSortAsc] = useState(true)

  useEffect(() => {
    // Category rules are tiny — fetch them alongside. Products stream in (fast first paint,
    // continuous fill) instead of one ~4s blocking fetch of all ~11k rows.
    const ctrl = new AbortController()
    fetch(`${API}/category-rules`, { headers: authHeaders() })
      .then(r => r.json()).then(setRules).catch(() => {})
    streamProducts(({ batch, isFirst }) => {
      setItems(prev => isFirst ? batch : [...prev, ...batch])
      if (isFirst) setLoading(false)
    }, { signal: ctrl.signal }).catch(() => {}).finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
    return () => ctrl.abort()
  }, [])

  const ruleMap = useMemo(() => {
    const m: Record<string, number> = {}
    rules.forEach(r => { m[r.category] = r.gp_floor })
    return m
  }, [rules])

  // Compute per-product flags — use best GP% across all active channels
  const flagged = useMemo(() => {
    return items
      .filter(p => p.status === 'ACTIVE')
      .map(p => {
        const floor = ruleMap[p.category] ?? 0
        const gpVals = p.channels
          .filter(c => c.is_active && c.gp_pct !== null)
          .map(c => c.gp_pct as number)
        const bestGp = gpVals.length > 0 ? Math.max(...gpVals) : null
        const worstGp = gpVals.length > 0 ? Math.min(...gpVals) : null
        const flag = getFlag(worstGp, floor)  // flag is driven by worst channel
        return { p, floor, bestGp, worstGp, flag }
      })
  }, [items, ruleMap])

  const categories = useMemo(() => {
    const cats = Array.from(new Set(flagged.map(r => r.p.category)))
    return ['All', ...CAT_ORDER.filter(c => cats.includes(c)), ...cats.filter(c => !CAT_ORDER.includes(c))]
  }, [flagged])

  const filtered = useMemo(() => {
    return flagged.filter(r => {
      if (catFilter !== 'All' && r.p.category !== catFilter) return false
      if (flagFilter !== 'All' && r.flag !== flagFilter) return false
      return true
    })
  }, [flagged, catFilter, flagFilter])

  const sorted = useMemo(() => {
    return [...filtered].sort((a, b) => {
      let cmp = 0
      if (sortCol === 'name')     cmp = a.p.name.localeCompare(b.p.name)
      if (sortCol === 'category') cmp = a.p.category.localeCompare(b.p.category)
      if (sortCol === 'gp')       cmp = (a.worstGp ?? -1) - (b.worstGp ?? -1)
      if (sortCol === 'flag') {
        const order: Record<Flag, number> = { below_floor: 0, borderline: 1, ok: 2 }
        cmp = order[a.flag] - order[b.flag]
      }
      return sortAsc ? cmp : -cmp
    })
  }, [filtered, sortCol, sortAsc])

  function handleSort(col: typeof sortCol) {
    if (sortCol === col) setSortAsc(a => !a)
    else { setSortCol(col); setSortAsc(true) }
  }

  function SortArrow({ col }: { col: typeof sortCol }) {
    if (sortCol !== col) return <span style={{ color: C.knobOff, marginLeft: '3px' }}>↕</span>
    return <span style={{ color: C.indigo, marginLeft: '3px' }}>{sortAsc ? '↑' : '↓'}</span>
  }

  // Summary counts
  const counts = useMemo(() => ({
    below:     flagged.filter(r => r.flag === 'below_floor').length,
    borderline: flagged.filter(r => r.flag === 'borderline').length,
    ok:        flagged.filter(r => r.flag === 'ok').length,
    no_price:  flagged.filter(r => r.worstGp === null).length,
  }), [flagged])

  // Per-category summary
  const catSummary = useMemo(() => {
    return CAT_ORDER.map(cat => {
      const inCat = flagged.filter(r => r.p.category === cat)
      if (inCat.length === 0) return null
      const floor = ruleMap[cat] ?? 0
      return {
        cat,
        floor,
        total: inCat.length,
        below: inCat.filter(r => r.flag === 'below_floor').length,
        borderline: inCat.filter(r => r.flag === 'borderline').length,
        ok: inCat.filter(r => r.flag === 'ok').length,
      }
    }).filter(Boolean)
  }, [flagged, ruleMap])

  if (loading) {
    return <div style={{ padding: '60px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>Loading…</div>
  }

  return (
    <div style={{ maxWidth: '1100px' }}>
      {/* Header */}
      <div style={{ marginBottom: '20px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: C.ink }}>Logic Layer</h1>
        <p style={{ fontSize: '13px', color: C.muted, marginTop: '4px' }}>
          GP% compliance check against category approval floors. Active products only.
          <span style={{ marginLeft: '8px', fontSize: '12px', color: C.faint }}>
            Borderline = within {GP_BAND * 100}pp of floor · Below floor = price alert
          </span>
        </p>
      </div>

      {/* Category bandwidth overview */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: '10px', marginBottom: '20px' }}>
        {catSummary.map(s => s && (
          <div key={s.cat} style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '12px 14px' }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: C.muted, marginBottom: '2px' }}>{s.cat}</p>
            <p style={{ fontSize: '18px', fontWeight: 700, color: C.ink, marginBottom: '6px' }}>
              {(s.floor * 100).toFixed(0)}% floor
            </p>
            <div style={{ display: 'flex', gap: '6px', fontSize: '11px', flexWrap: 'wrap' }}>
              {s.below > 0 && <span style={{ background: C.badBg, color: C.redInk, padding: '1px 6px', borderRadius: '4px', fontWeight: 600 }}>{s.below} below</span>}
              {s.borderline > 0 && <span style={{ background: '#FFFBEB', color: C.amberInk, padding: '1px 6px', borderRadius: '4px', fontWeight: 600 }}>{s.borderline} edge</span>}
              <span style={{ background: '#F0FDF4', color: C.green, padding: '1px 6px', borderRadius: '4px', fontWeight: 600 }}>{s.ok} ok</span>
            </div>
          </div>
        ))}
      </div>

      {/* Alert summary cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '12px', marginBottom: '20px' }}>
        {[
          { label: 'Below Floor',  value: counts.below,      bg: C.badBg, border: '#FECACA', color: C.redInk, flag: 'below_floor' as Flag | 'All' },
          { label: 'Borderline',   value: counts.borderline,  bg: '#FFFBEB', border: '#FCD34D', color: C.amberInk, flag: 'borderline' as Flag | 'All' },
          { label: 'Approved',     value: counts.ok,          bg: '#F0FDF4', border: '#BBF7D0', color: C.green, flag: 'ok' as Flag | 'All' },
          { label: 'No Price Data',value: counts.no_price,    bg: C.wash, border: C.line, color: C.muted, flag: 'All' as Flag | 'All' },
        ].map(card => (
          <button
            key={card.label}
            onClick={() => setFlagFilter(f => f === card.flag ? 'All' : card.flag)}
            style={{
              background: card.bg, border: `1px solid ${flagFilter === card.flag ? card.color : card.border}`,
              borderRadius: '8px', padding: '12px 14px', textAlign: 'left', cursor: 'pointer',
              outline: flagFilter === card.flag ? `2px solid ${card.color}` : 'none',
            }}
          >
            <p style={{ fontSize: '10px', fontWeight: 600, color: card.color, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '4px' }}>{card.label}</p>
            <p style={{ fontSize: '24px', fontWeight: 700, color: card.color, lineHeight: 1 }}>{card.value}</p>
          </button>
        ))}
      </div>

      {/* Filters */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '14px', alignItems: 'center', flexWrap: 'wrap' }}>
        <select
          value={catFilter}
          onChange={e => setCatFilter(e.target.value)}
          style={{ fontSize: '12px', border: '1px solid #E2E8F0', borderRadius: '6px', padding: '6px 10px', background: 'white', color: C.ink }}
        >
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>
        <span style={{ fontSize: '12px', color: C.faint }}>
          {sorted.length} product{sorted.length !== 1 ? 's' : ''}
          {(catFilter !== 'All' || flagFilter !== 'All') && ' (filtered)'}
        </span>
        {(catFilter !== 'All' || flagFilter !== 'All') && (
          <button
            onClick={() => { setCatFilter('All'); setFlagFilter('All') }}
            style={{ fontSize: '11px', color: C.indigo, background: 'none', border: 'none', cursor: 'pointer', padding: '0 4px' }}
          >
            Clear filters ×
          </button>
        )}
      </div>

      {/* Table */}
      <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'hidden' }}>
        {/* Header */}
        <div style={{ display: 'grid', gridTemplateColumns: '90px 1fr 120px 90px 90px 90px 80px', gap: '0', background: C.wash, borderBottom: '1px solid #E2E8F0' }}>
          {[
            { label: 'SKU',       col: null },
            { label: 'Product',   col: 'name'     as typeof sortCol },
            { label: 'Category',  col: 'category' as typeof sortCol },
            { label: 'GP Floor',  col: null },
            { label: 'Worst GP%', col: 'gp'       as typeof sortCol },
            { label: 'Best GP%',  col: null },
            { label: 'Status',    col: 'flag'     as typeof sortCol },
          ].map(({ label, col }) => (
            <div
              key={label}
              onClick={col ? () => handleSort(col) : undefined}
              style={{ padding: '9px 12px', fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.06em', cursor: col ? 'pointer' : 'default', userSelect: 'none' }}
            >
              {label}{col && <SortArrow col={col} />}
            </div>
          ))}
        </div>

        {/* Rows */}
        {sorted.length === 0 && (
          <div style={{ padding: '40px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>
            No products match the current filters.
          </div>
        )}
        {sorted.map(({ p, floor, bestGp, worstGp, flag }, i) => {
          const fs = FLAG_STYLE[flag]
          return (
            <div
              key={p.id}
              style={{
                display: 'grid', gridTemplateColumns: '90px 1fr 120px 90px 90px 90px 80px',
                borderBottom: i < sorted.length - 1 ? '1px solid #F1F5F9' : 'none',
                alignItems: 'center',
                borderLeft: flag === 'below_floor' ? '3px solid #EF4444' : flag === 'borderline' ? '3px solid #F59E0B' : '3px solid transparent',
              }}
            >
              <div style={{ padding: '10px 12px' }}>
                <Link to={`/items/${skuToPath(p.sku_code)}` as never} style={{ fontSize: '11px', color: C.indigo, textDecoration: 'none', fontWeight: 600, fontVariantNumeric: 'tabular-nums' }}>
                  {p.sku_code}
                </Link>
              </div>
              <div style={{ padding: '10px 12px' }}>
                <p style={{ fontSize: '12px', fontWeight: 500, color: C.ink, lineHeight: 1.3 }}>{p.name}</p>
                {p.brand && <p style={{ fontSize: '11px', color: C.faint, marginTop: '1px' }}>{p.brand}</p>}
              </div>
              <div style={{ padding: '10px 12px' }}>
                <span style={{ fontSize: '11px', color: C.muted }}>{p.category}</span>
              </div>
              <div style={{ padding: '10px 12px', fontVariantNumeric: 'tabular-nums' }}>
                <span style={{ fontSize: '12px', fontWeight: 600, color: C.muted }}>{(floor * 100).toFixed(0)}%</span>
              </div>
              <div style={{ padding: '10px 12px', fontVariantNumeric: 'tabular-nums' }}>
                {worstGp !== null
                  ? <span style={{ fontSize: '13px', fontWeight: 700, color: worstGp < floor ? '#EF4444' : worstGp < floor + GP_BAND ? '#F59E0B' : C.green }}>
                      {(worstGp * 100).toFixed(1)}%
                    </span>
                  : <span style={{ fontSize: '12px', color: C.knobOff }}>—</span>}
              </div>
              <div style={{ padding: '10px 12px', fontVariantNumeric: 'tabular-nums' }}>
                {bestGp !== null
                  ? <span style={{ fontSize: '12px', color: C.faint }}>{(bestGp * 100).toFixed(1)}%</span>
                  : <span style={{ fontSize: '12px', color: C.knobOff }}>—</span>}
              </div>
              <div style={{ padding: '10px 12px' }}>
                <span style={{ fontSize: '10px', fontWeight: 600, padding: '2px 8px', borderRadius: '4px', background: fs.bg, color: fs.color, border: `1px solid ${fs.border}`, whiteSpace: 'nowrap' }}>
                  {fs.label}
                </span>
              </div>
            </div>
          )
        })}
      </div>

      {/* Footer note */}
      <p style={{ fontSize: '11px', color: C.knobOff, marginTop: '12px' }}>
        GP% calculated from cost vs selling price per channel. Worst GP% = lowest across all active channels (drives the flag). Best GP% = highest.
        Approval band: ≥ floor + {GP_BAND * 100}pp = Approved · floor to floor+{GP_BAND * 100}pp = Borderline · &lt; floor = Below.
      </p>
    </div>
  )
}

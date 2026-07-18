import { C } from '@/lib/tokens'
import { useState, useEffect, useMemo, useCallback, Suspense } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import type { Product } from '@/lib/types'
import { authHeaders } from '@/lib/auth'
import { streamProducts } from '@/lib/streamProducts'
import { skuToPath } from '@/lib/sku'
import { toast } from '@/lib/toast'
import { API_BASE } from '@/lib/config'

export const Route = createFileRoute('/_authed/data-review')({ component: DataReviewPage })

const API = API_BASE

const CATEGORIES = [
  'Medicine', 'Preventative', 'Supplement', 'Food',
  'Pet Hygiene', 'Shampoo', 'Cat Litter', 'Toys', 'Not-For-Sale',
]
const STATUSES = ['ACTIVE', 'INACTIVE', 'DISCONTINUED']

const CAT_STYLE: Record<string, { bg: string; color: string }> = {
  'Medicine':     { bg: C.redBg, color: C.redInk },
  'Preventative': { bg: C.warnBg, color: C.amberInk },
  'Supplement':   { bg: '#DBEAFE', color: '#1E40AF' },
  'Food':         { bg: C.greenBg, color: C.green },
  'Pet Hygiene':  { bg: C.monoBg, color: C.sub },
  'Shampoo':      { bg: '#E0E7FF', color: '#3730A3' },
  'Cat Litter':   { bg: '#FFF7ED', color: '#9A3412' },
  'Toys':         { bg: '#FDF4FF', color: '#7E22CE' },
  'Not-For-Sale': { bg: C.monoBg, color: C.faint },
}
const STATUS_STYLE: Record<string, { bg: string; color: string }> = {
  ACTIVE:       { bg: C.greenBg, color: C.green },
  INACTIVE:     { bg: C.warnBg, color: C.amberInk },
  DISCONTINUED: { bg: C.redBg, color: C.redInk },
}
const COST_SOURCE_LABEL: Record<string, string> = {
  catalogue:       '★ Catalogue',
  invoice_matched: '✓ Invoice',
  po_issued:       '✓ PO',
  manual:          '? Manual',
  sheet:           '· Sheet seed',
}
const COST_SOURCE_STYLE: Record<string, { bg: string; color: string }> = {
  catalogue:       { bg: C.greenBg, color: C.green },
  invoice_matched: { bg: '#D1FAE5', color: '#065F46' },
  po_issued:       { bg: '#DBEAFE', color: '#1E40AF' },
  manual:          { bg: C.monoBg, color: C.muted },
  sheet:           { bg: C.wash, color: C.faint },
}
const GRADE_STYLE: Record<string, { bg: string; color: string }> = {
  A: { bg: C.greenBg, color: C.green },
  B: { bg: '#DBEAFE', color: '#1E40AF' },
  C: { bg: C.redBg, color: C.redInk },
}

function hasIssue(p: Product) {
  return (
    !!p.last_manual_edit_at ||
    p.cost_sheet_conflict || p.pack_sheet_conflict ||
    (p.units_per_pack != null && !p.uom_verified_at) ||
    p.channels.some(c => c.recommendation === 'Raise price ⚠' || c.recommendation === 'Check pack size ⚠') ||
    p.data_grade === 'C'
  )
}

// ── Tiny reusable components ────────────────────────────────────────────────

function Badge({ label, bg, color }: { label: string; bg: string; color: string }) {
  return (
    <span style={{ fontSize: '10px', fontWeight: 700, color, background: bg, padding: '1px 6px', borderRadius: '3px', whiteSpace: 'nowrap' }}>
      {label}
    </span>
  )
}

function SaveBtn({ saving, dirty, onClick, label = 'Save' }: { saving: boolean; dirty: boolean; onClick: () => void; label?: string }) {
  if (!dirty && !saving) return null
  return (
    <button onClick={onClick} disabled={saving}
      style={{ fontSize: '11px', fontWeight: 600, padding: '3px 10px', background: saving ? C.line : C.indigo, color: 'white', border: 'none', borderRadius: '4px', cursor: saving ? 'default' : 'pointer' }}>
      {saving ? 'Saving…' : label}
    </button>
  )
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return <span style={{ fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.05em', display: 'block', marginBottom: '3px' }}>{children}</span>
}

// ── Main content ────────────────────────────────────────────────────────────

function DataReviewContent() {
  const searchParams = new URLSearchParams(window.location.search)

  const [products, setProducts]   = useState<Product[]>([])
  const [loading, setLoading]     = useState(true)
  const [overrides, setOverrides] = useState<Record<string, Product>>({})
  const [expanded, setExpanded]   = useState<string | null>(null)
  const [verifiedBy, setVerifiedBy] = useState(() =>
    typeof window !== 'undefined' ? (localStorage.getItem('ims_verified_by') ?? '') : ''
  )
  const [supplierFilter, setSupplierFilter] = useState(searchParams.get('supplier') ?? 'All')
  const [categoryFilter, setCategoryFilter] = useState(searchParams.get('category') ?? 'All')
  const [statusFilter, setStatusFilter]     = useState('ACTIVE')
  const [issueFilter, setIssueFilter]       = useState('All')
  const [bulkMode, setBulkMode]             = useState(false)
  const [staged, setStaged]                 = useState<Record<string, Record<string, unknown>>>({})
  const [discardKey, setDiscardKey]         = useState(0)
  const [bulkSaving, setBulkSaving]         = useState(false)

  // per-row saving state: sku → field name (or 'all')
  const [saving, setSaving] = useState<Record<string, string>>({})

  useEffect(() => {
    // Stream the catalogue in — fast first paint, then fills in continuously (same path as the
    // inventory screen) instead of one ~4s blocking fetch of all ~11k rows.
    const ctrl = new AbortController()
    streamProducts(({ batch, isFirst }) => {
      setProducts(prev => isFirst ? batch : [...prev, ...batch])
      if (isFirst) setLoading(false)
    }, { signal: ctrl.signal }).catch(() => {}).finally(() => { if (!ctrl.signal.aborted) setLoading(false) })
    return () => ctrl.abort()
  }, [])

  const resolve = useCallback((p: Product) => overrides[p.sku_code] ?? p, [overrides])

  const afterSave = useCallback((sku: string, updated: Product, field: string) => {
    setOverrides(o => ({ ...o, [sku]: updated }))
    setSaving(s => { const n = { ...s }; delete n[`${sku}:${field}`]; return n })
  }, [])

  const isSaving = (sku: string, field: string) => !!saving[`${sku}:${field}`]
  const setSavingField = (sku: string, field: string) =>
    setSaving(s => ({ ...s, [`${sku}:${field}`]: '1' }))

  // Generic PATCH /{sku}
  async function patchProduct(sku: string, field: string, body: Record<string, unknown>) {
    setSavingField(sku, field)
    try {
      const res = await fetch(`${API}/products/${sku}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(body),
      })
      if (!res.ok) throw new Error(await res.text())
      afterSave(sku, await res.json(), field)
    } catch (e) {
      setSaving(s => { const n = { ...s }; delete n[`${sku}:${field}`]; return n })
      toast.error(e instanceof Error ? e.message : 'Error')
    }
  }

  // Generic POST/PATCH to any URL
  async function callUrl(sku: string, field: string, url: string, method = 'POST', body?: object) {
    setSavingField(sku, field)
    try {
      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: body ? JSON.stringify(body) : undefined,
      })
      if (!res.ok) throw new Error(await res.text())
      afterSave(sku, await res.json(), field)
    } catch (e) {
      setSaving(s => { const n = { ...s }; delete n[`${sku}:${field}`]; return n })
      toast.error(e instanceof Error ? e.message : 'Error')
    }
  }

  // ── Bulk edit helpers ───────────────────────────────────────────────────────
  const stagedSkus = Object.keys(staged).filter(sku => Object.keys(staged[sku]).length > 0)

  function stageChange(sku: string, field: string, value: unknown, original: unknown) {
    setStaged(prev => {
      const skuStaged = { ...(prev[sku] ?? {}) }
      const same = value === original || (value === '' && (original == null || original === ''))
      if (same) { delete skuStaged[field] } else { skuStaged[field] = value }
      if (Object.keys(skuStaged).length === 0) {
        const next = { ...prev }; delete next[sku]; return next
      }
      return { ...prev, [sku]: skuStaged }
    })
  }

  function discardAllStaged() { setStaged({}); setDiscardKey(k => k + 1) }

  async function saveAllStaged() {
    if (!stagedSkus.length) return
    setBulkSaving(true)
    const results = await Promise.allSettled(
      stagedSkus.map(async sku => {
        const res = await fetch(`${API}/products/${sku}`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify(staged[sku]),
        })
        if (!res.ok) throw new Error(`${sku}: ${await res.text()}`)
        return { sku, data: await res.json() as Product }
      })
    )
    const saved: string[] = []
    const errors: string[] = []
    const updates: Record<string, Product> = {}
    for (const r of results) {
      if (r.status === 'fulfilled') { updates[r.value.sku] = r.value.data; saved.push(r.value.sku) }
      else errors.push(r.reason?.message ?? 'Unknown error')
    }
    setOverrides(o => ({ ...o, ...updates }))
    setStaged(prev => { const n = { ...prev }; saved.forEach(s => delete n[s]); return n })
    setBulkSaving(false)
    if (errors.length) toast.error(`${errors.length} failed:\n${errors.join('\n')}`)
  }

  const suppliers = useMemo(() =>
    ['All', ...new Set(products.map(p => p.supplier_name).filter(Boolean) as string[])].sort(
      (a, b) => a === 'All' ? -1 : b === 'All' ? 1 : a.localeCompare(b)
    ), [products]
  )
  const categories = useMemo(() =>
    ['All', ...new Set(products.map(p => p.category))].sort(
      (a, b) => a === 'All' ? -1 : b === 'All' ? 1 : a.localeCompare(b)
    ), [products]
  )

  const filtered = useMemo(() => {
    return products.map(resolve).filter(p => {
      if (statusFilter !== 'All' && p.status !== statusFilter) return false
      if (supplierFilter !== 'All' && p.supplier_name !== supplierFilter) return false
      if (categoryFilter !== 'All' && p.category !== categoryFilter) return false
      if (issueFilter === 'Any issue'           && !hasIssue(p)) return false
      if (issueFilter === 'Manually edited'     && !p.last_manual_edit_at) return false
      if (issueFilter === 'Cost conflict'        && !p.cost_sheet_conflict) return false
      if (issueFilter === 'Pack conflict'        && !p.pack_sheet_conflict) return false
      if (issueFilter === 'Unverified pack size' && !(p.units_per_pack != null && !p.uom_verified_at)) return false
      if (issueFilter === 'Below margin'         && !p.channels.some(c => c.recommendation === 'Raise price ⚠')) return false
      if (issueFilter === 'Grade C'              && p.data_grade !== 'C') return false
      if (issueFilter === 'Missing cost'         && p.primary_cost != null) return false
      if (issueFilter === 'Missing pack size'    && p.units_per_pack != null) return false
      return true
    }).sort((a, b) => {
      const ai = hasIssue(a) ? 0 : 1; const bi = hasIssue(b) ? 0 : 1
      if (ai !== bi) return ai - bi
      return (b.sales_120d ?? 0) - (a.sales_120d ?? 0)
    })
  }, [products, overrides, statusFilter, supplierFilter, categoryFilter, issueFilter])

  const issueCounts = useMemo(() => {
    const all = products.map(resolve)
    return {
      manuallyEdited: all.filter(p => !!p.last_manual_edit_at).length,
      costConflict:   all.filter(p => p.cost_sheet_conflict).length,
      packConflict:   all.filter(p => p.pack_sheet_conflict).length,
      packUnverified: all.filter(p => p.units_per_pack != null && !p.uom_verified_at).length,
      belowMargin:    all.filter(p => p.channels.some(c => c.recommendation === 'Raise price ⚠')).length,
      gradeC:         all.filter(p => p.data_grade === 'C').length,
      missingCost:    all.filter(p => p.primary_cost == null).length,
      missingPack:    all.filter(p => p.units_per_pack == null).length,
    }
  }, [products, overrides])

  const exportUrl = useMemo(() => {
    const p = new URLSearchParams()
    if (supplierFilter !== 'All') p.set('supplier', supplierFilter)
    if (categoryFilter !== 'All') p.set('category', categoryFilter)
    return `${API}/products/export.csv?${p}`
  }, [supplierFilter, categoryFilter])

  if (loading) return (
    <div style={{ padding: '60px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>Loading…</div>
  )

  return (
      <div style={{ padding: '24px 32px', maxWidth: '1200px' }}>

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '16px', flexWrap: 'wrap', gap: '12px' }}>
          <div>
            <Link to={"/" as never} style={{ fontSize: '12px', color: C.faint, textDecoration: 'none' }}>← All Inventory</Link>
            <h1 style={{ fontSize: '20px', fontWeight: 700, color: C.ink, margin: '4px 0 2px' }}>Data Review</h1>
            <p style={{ fontSize: '12px', color: C.muted }}>
              {bulkMode
                ? 'Bulk Edit — tab between cells, changes are staged. Nothing saves until you click "Save".'
                : 'Click any row to open the edit panel. Switch to Bulk Edit for spreadsheet-style editing across multiple rows.'}
            </p>
          </div>
          <a href={exportUrl} style={{ padding: '8px 16px', fontSize: '12px', fontWeight: 600, background: C.ink, color: 'white', borderRadius: '6px', textDecoration: 'none', whiteSpace: 'nowrap' }}>
            ↓ Download CSV
          </a>
        </div>

        {/* Issue chips — click to filter */}
        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginBottom: '14px', alignItems: 'center' }}>
          <span style={{ fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.06em', marginRight: '2px' }}>Issues:</span>
          {([
            { key: 'Manually edited',      label: `✎ ${issueCounts.manuallyEdited} manually edited`,                                                      count: issueCounts.manuallyEdited, bg: '#EDE9FE', color: '#6D28D9', activeBg: '#7C3AED', activeColor: 'white' },
            { key: 'Cost conflict',        label: `⚡ ${issueCounts.costConflict} cost conflict${issueCounts.costConflict !== 1 ? 's' : ''}`,              count: issueCounts.costConflict,   bg: C.warnBg, color: C.amberInk, activeBg: '#F59E0B', activeColor: 'white' },
            { key: 'Pack conflict',        label: `⚡ ${issueCounts.packConflict} pack conflict${issueCounts.packConflict !== 1 ? 's' : ''}`,              count: issueCounts.packConflict,   bg: C.warnBg, color: C.amberInk, activeBg: '#F59E0B', activeColor: 'white' },
            { key: 'Unverified pack size', label: `📐 ${issueCounts.packUnverified} unverified pack size${issueCounts.packUnverified !== 1 ? 's' : ''}`,  count: issueCounts.packUnverified, bg: '#FFF7ED', color: '#C2410C', activeBg: '#EA580C', activeColor: 'white' },
            { key: 'Below margin',         label: `↑ ${issueCounts.belowMargin} below margin`,                                                            count: issueCounts.belowMargin,    bg: '#FFFBEB', color: C.amber, activeBg: '#D97706', activeColor: 'white' },
            { key: 'Grade C',              label: `C ${issueCounts.gradeC} grade C`,                                                                      count: issueCounts.gradeC,         bg: C.redBg, color: C.redInk, activeBg: '#DC2626', activeColor: 'white' },
            { key: 'Missing cost',         label: `$ ${issueCounts.missingCost} missing cost`,                                                                count: issueCounts.missingCost,    bg: '#F0FDF4', color: C.green, activeBg: '#16A34A', activeColor: 'white' },
            { key: 'Missing pack size',    label: `⬜ ${issueCounts.missingPack} missing pack size`,                                                           count: issueCounts.missingPack,    bg: '#F0FDF4', color: C.green, activeBg: '#16A34A', activeColor: 'white' },
          ] as const).filter(chip => chip.count > 0).map(chip => {
            const active = issueFilter === chip.key
            return (
              <button key={chip.key} onClick={() => setIssueFilter(active ? 'All' : chip.key)}
                style={{ fontSize: '11px', fontWeight: 600, background: active ? chip.activeBg : chip.bg, color: active ? chip.activeColor : chip.color, padding: '3px 10px', borderRadius: '99px', border: 'none', cursor: 'pointer', transition: 'all 0.1s' }}>
                {chip.label}
              </button>
            )
          })}
          {issueFilter !== 'All' && (
            <button onClick={() => setIssueFilter('All')}
              style={{ fontSize: '11px', fontWeight: 600, color: C.muted, background: C.monoBg, padding: '3px 10px', borderRadius: '99px', border: 'none', cursor: 'pointer' }}>
              × clear
            </button>
          )}
        </div>

        {/* Controls */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap', padding: '10px 14px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', marginBottom: '14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ fontSize: '11px', color: C.muted }}>Verified by</span>
            <input type="text" placeholder="Name / initials" value={verifiedBy}
              onChange={e => { setVerifiedBy(e.target.value); localStorage.setItem('ims_verified_by', e.target.value) }}
              style={{ padding: '4px 8px', fontSize: '12px', border: '1px solid #E2E8F0', borderRadius: '5px', width: '130px' }} />
          </div>
          <div style={{ width: '1px', height: '20px', background: C.line }} />
          {[
            { label: 'Status',   value: statusFilter,   set: setStatusFilter,   opts: ['All', ...STATUSES] },
            { label: 'Supplier', value: supplierFilter,  set: setSupplierFilter, opts: suppliers },
            { label: 'Category', value: categoryFilter,  set: setCategoryFilter, opts: categories },
          ].map(({ label, value, set, opts }) => (
            <div key={label} style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
              <span style={{ fontSize: '11px', color: C.muted }}>{label}</span>
              <select value={value} onChange={e => set(e.target.value)}
                style={{ padding: '4px 6px', fontSize: '11px', border: '1px solid #E2E8F0', borderRadius: '5px', background: 'white', color: C.ink }}>
                {opts.map(o => <option key={o}>{o}</option>)}
              </select>
            </div>
          ))}
          <div style={{ display: 'flex', alignItems: 'center', gap: '5px', marginLeft: 'auto' }}>
            <span style={{ fontSize: '11px', color: C.muted }}>Issue</span>
            <select value={issueFilter} onChange={e => setIssueFilter(e.target.value)}
              style={{ padding: '4px 6px', fontSize: '11px', border: '1px solid #E2E8F0', borderRadius: '5px', background: issueFilter !== 'All' ? C.warnBg : 'white', color: issueFilter !== 'All' ? C.amberInk : C.ink, fontWeight: issueFilter !== 'All' ? 700 : 400 }}>
              {['All', 'Any issue', 'Manually edited', 'Cost conflict', 'Pack conflict', 'Unverified pack size', 'Below margin', 'Grade C', 'Missing cost', 'Missing pack size'].map(o => (
                <option key={o}>{o}</option>
              ))}
            </select>
          </div>
          <span style={{ fontSize: '11px', color: C.faint }}>{filtered.length} rows</span>
          <div style={{ width: '1px', height: '20px', background: C.line }} />
          {/* Mode toggle */}
          <div style={{ display: 'flex', background: C.monoBg, borderRadius: '6px', padding: '2px', gap: '2px' }}>
            {(['Normal', 'Bulk Edit'] as const).map(mode => (
              <button key={mode} onClick={() => { setBulkMode(mode === 'Bulk Edit'); if (mode === 'Normal') discardAllStaged() }}
                style={{ fontSize: '11px', fontWeight: 600, padding: '4px 10px', borderRadius: '4px', border: 'none', cursor: 'pointer', background: (mode === 'Bulk Edit') === bulkMode ? C.ink : 'transparent', color: (mode === 'Bulk Edit') === bulkMode ? 'white' : C.muted }}>
                {mode}
              </button>
            ))}
          </div>
        </div>

        {/* Bulk Edit — staged save bar + spreadsheet table */}
        {bulkMode && (
          <div style={{ marginBottom: '0' }}>
            <div style={{
              position: 'sticky', top: 0, zIndex: 10, marginBottom: '8px',
              background: stagedSkus.length > 0 ? C.ink : C.wash,
              border: `1px solid ${stagedSkus.length > 0 ? '#1E293B' : C.line}`,
              borderRadius: '8px', padding: '10px 16px',
              display: 'flex', alignItems: 'center', gap: '12px',
            }}>
              {stagedSkus.length > 0 ? (
                <>
                  <span style={{ fontSize: '13px', fontWeight: 700, color: 'white' }}>
                    {stagedSkus.length} row{stagedSkus.length !== 1 ? 's' : ''} with unsaved changes
                  </span>
                  <span style={{ fontSize: '11px', color: C.faint, flex: 1 }}>
                    Amber cells are staged — tab between fields to move fast
                  </span>
                  <button onClick={discardAllStaged} disabled={bulkSaving}
                    style={{ fontSize: '12px', fontWeight: 600, padding: '6px 14px', background: 'transparent', color: C.faint, border: '1px solid #475569', borderRadius: '6px', cursor: 'pointer' }}>
                    Discard all
                  </button>
                  <button onClick={saveAllStaged} disabled={bulkSaving}
                    style={{ fontSize: '12px', fontWeight: 700, padding: '6px 18px', background: C.indigo, color: 'white', border: 'none', borderRadius: '6px', cursor: bulkSaving ? 'default' : 'pointer' }}>
                    {bulkSaving ? 'Saving…' : `Save ${stagedSkus.length} change${stagedSkus.length !== 1 ? 's' : ''}`}
                  </button>
                </>
              ) : (
                <span style={{ fontSize: '12px', color: C.muted }}>
                  Edit any cell — changes are staged here until you save. Tab to move between fields.
                </span>
              )}
            </div>

            <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'auto' }}>
              {/* Bulk edit header — Name removed (system-level only); UOM split into sell + buy */}
              <div style={{ display: 'grid', gridTemplateColumns: '100px 120px 120px 70px 70px 90px 75px 100px 1fr', padding: '8px 12px', background: C.wash, borderBottom: '1px solid #E2E8F0', gap: '8px', minWidth: '1000px' }}>
                {['SKU · Name', 'Category', 'Status', 'Sell UOM', 'Buy Unit', 'Cost (HKD)', 'Pack qty', 'Brand', 'Issues'].map(h => (
                  <span key={h} style={{ fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</span>
                ))}
              </div>

              {/* Bulk edit rows */}
              {filtered.map(p => {
                const changes = staged[p.sku_code] ?? {}
                const isDirty = Object.keys(changes).length > 0
                const cellBase: React.CSSProperties = { width: '100%', padding: '5px 7px', fontSize: '12px', border: '1px solid #E2E8F0', borderRadius: '4px', outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box' }
                const cell = (field: string): React.CSSProperties => ({ ...cellBase, background: field in changes ? '#FFFBEB' : 'white', borderColor: field in changes ? '#F59E0B' : C.line })

                const chips: { label: string; color: string; bg: string }[] = []
                if (p.last_manual_edit_at)                         chips.push({ label: p.last_manual_edit_by ? `✎ ${p.last_manual_edit_by}` : '✎ Edited', color: '#6D28D9', bg: '#EDE9FE' })
                if (p.cost_sheet_conflict)                          chips.push({ label: '⚡ Cost', color: C.amberInk, bg: C.warnBg })
                if (p.pack_sheet_conflict)                          chips.push({ label: '⚡ Pack', color: C.amberInk, bg: C.warnBg })
                if (p.units_per_pack != null && !p.uom_verified_at) chips.push({ label: '📐 UPP', color: '#C2410C', bg: '#FFF7ED' })
                if (p.channels.some(c => c.recommendation === 'Raise price ⚠')) chips.push({ label: '↑ Margin', color: C.amber, bg: '#FFFBEB' })
                if (p.data_grade === 'C')                           chips.push({ label: 'C', color: C.redInk, bg: C.redBg })

                return (
                  <div key={p.sku_code} style={{
                    display: 'grid', gridTemplateColumns: '100px 120px 120px 70px 70px 90px 75px 100px 1fr',
                    padding: '5px 12px', gap: '8px', alignItems: 'center',
                    borderBottom: '1px solid #F1F5F9', minWidth: '1000px',
                    borderLeft: `3px solid ${isDirty ? '#F59E0B' : p.last_manual_edit_at ? '#A78BFA' : 'transparent'}`,
                    background: isDirty ? '#FEFCE8' : 'white',
                  }}>
                    {/* SKU + name (read-only — name changes must go through system) */}
                    <div title="Name changes must be made at system level (DaySmart / Sheet)">
                      <div style={{ fontSize: '11px', fontWeight: 600, color: C.indigo, fontVariantNumeric: 'tabular-nums' }}>{p.sku_code}</div>
                      <div style={{ fontSize: '10px', color: C.muted, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</div>
                    </div>
                    {/* Category — dropdown only */}
                    <select value={(changes['category'] as string) ?? p.category}
                      onChange={e => stageChange(p.sku_code, 'category', e.target.value, p.category)}
                      style={{ ...cell('category'), cursor: 'pointer' }}>
                      {CATEGORIES.map(c => <option key={c}>{c}</option>)}
                    </select>
                    {/* Status — dropdown only */}
                    <select value={(changes['status'] as string) ?? p.status}
                      onChange={e => stageChange(p.sku_code, 'status', e.target.value, p.status)}
                      style={{ ...cell('status'), cursor: 'pointer' }}>
                      {STATUSES.map(s => <option key={s}>{s}</option>)}
                    </select>
                    {/* Sell UOM (tablet, ml, g) */}
                    <input key={`uom-${p.sku_code}-${discardKey}`} defaultValue={p.uom ?? ''}
                      onBlur={e => stageChange(p.sku_code, 'uom', e.target.value || null, p.uom ?? null)}
                      style={cell('uom')} placeholder="tablet" />
                    {/* Buy unit (box, bottle, strip) */}
                    <input key={`pu-${p.sku_code}-${discardKey}`} defaultValue={p.pack_unit ?? ''}
                      onBlur={e => stageChange(p.sku_code, 'pack_unit', e.target.value || null, p.pack_unit ?? null)}
                      style={cell('pack_unit')} placeholder="box" />
                    {/* Cost */}
                    <input key={`c-${p.sku_code}-${discardKey}`} type="number" step="0.01" min="0"
                      defaultValue={p.primary_cost ?? ''}
                      onBlur={e => stageChange(p.sku_code, 'basic_cost', e.target.value === '' ? null : Number(e.target.value), p.primary_cost ?? null)}
                      style={{ ...cell('basic_cost'), textAlign: 'right' }} placeholder="—" />
                    {/* Pack qty (units_per_pack) */}
                    <input key={`p-${p.sku_code}-${discardKey}`} type="number" step="1" min="1"
                      defaultValue={p.units_per_pack ?? ''}
                      onBlur={e => stageChange(p.sku_code, 'units_per_pack', e.target.value === '' ? null : Number(e.target.value), p.units_per_pack ?? null)}
                      style={{ ...cell('units_per_pack'), textAlign: 'right' }} placeholder="—" />
                    {/* Brand */}
                    <input key={`b-${p.sku_code}-${discardKey}`} defaultValue={p.brand ?? ''}
                      onBlur={e => stageChange(p.sku_code, 'brand', e.target.value || null, p.brand ?? null)}
                      style={cell('brand')} placeholder="—" />
                    {/* Issues */}
                    <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px' }}>
                      {chips.map(c => <Badge key={c.label} label={c.label} color={c.color} bg={c.bg} />)}
                      {!chips.length && <span style={{ fontSize: '10px', color: C.faint }}>✓</span>}
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Normal view — expandable rows table */}
        {!bulkMode && <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'hidden' }}>
          {/* Header */}
          <div style={{ display: 'grid', gridTemplateColumns: '100px 1fr 140px 50px 90px 24px', padding: '8px 16px', background: C.wash, borderBottom: '1px solid #E2E8F0', gap: '8px' }}>
            {['SKU', 'Name · Brand · Issues', 'Supplier', 'Gr.', 'Category', ''].map(h => (
              <span key={h} style={{ fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</span>
            ))}
          </div>

          {filtered.length === 0 ? (
            <div style={{ padding: '48px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>
              {issueFilter !== 'All' ? `No "${issueFilter}" issues in this filter ✓` : 'No products match.'}
            </div>
          ) : filtered.map(p => {
            const isOpen   = expanded === p.sku_code
            const issue    = hasIssue(p)
            const catStyle = CAT_STYLE[p.category] ?? { bg: C.monoBg, color: C.sub }
            const grStyle  = GRADE_STYLE[p.data_grade] ?? { bg: C.monoBg, color: C.faint }

            const issueChips: { label: string; color: string; bg: string }[] = []
            if (p.last_manual_edit_at)                          issueChips.push({ label: p.last_manual_edit_by ? `✎ ${p.last_manual_edit_by}` : '✎ Edited', color: '#6D28D9', bg: '#EDE9FE' })
            if (p.cost_sheet_conflict)                          issueChips.push({ label: '⚡ Cost conflict',     color: C.amberInk, bg: C.warnBg })
            if (p.pack_sheet_conflict)                          issueChips.push({ label: '⚡ Pack conflict',     color: C.amberInk, bg: C.warnBg })
            if (p.units_per_pack != null && !p.uom_verified_at) issueChips.push({ label: '📐 Unverified pack',  color: '#C2410C', bg: '#FFF7ED' })
            if (p.channels.some(c => c.recommendation === 'Raise price ⚠')) issueChips.push({ label: '↑ Below margin', color: C.amber, bg: '#FFFBEB' })
            if (p.data_grade === 'C')                           issueChips.push({ label: 'Grade C',              color: C.redInk, bg: C.redBg })

            return (
              <div key={p.sku_code} style={{ borderBottom: '1px solid #F1F5F9' }}>
                {/* Collapsed row — click to expand */}
                <div
                  onClick={() => setExpanded(isOpen ? null : p.sku_code)}
                  style={{
                    display: 'grid', gridTemplateColumns: '100px 1fr 140px 50px 90px 24px',
                    padding: '10px 16px', gap: '8px', alignItems: 'center', cursor: 'pointer',
                    background: isOpen ? C.primaryBg : issue ? '#FAFAFA' : 'white',
                    transition: 'background 0.1s',
                  }}
                >
                  <div>
                    <span style={{ fontSize: '12px', fontWeight: 600, color: isOpen ? C.indigoInk : C.indigo, fontVariantNumeric: 'tabular-nums' }}>{p.sku_code}</span>
                    <div style={{ fontSize: '10px', color: p.status === 'ACTIVE' ? C.faint : '#EF4444', marginTop: '1px' }}>{p.status}</div>
                  </div>
                  <div>
                    <p style={{ fontSize: '12px', fontWeight: 600, color: C.ink }}>{p.name}</p>
                    {p.brand && <p style={{ fontSize: '10px', color: C.faint }}>{p.brand}</p>}
                    {p.sales_120d > 0 && <p style={{ fontSize: '10px', color: C.indigo }}>{p.sales_120d} sold/120d</p>}
                    {issueChips.length > 0 && (
                      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '3px', marginTop: '4px' }}>
                        {issueChips.map(c => <Badge key={c.label} label={c.label} color={c.color} bg={c.bg} />)}
                      </div>
                    )}
                  </div>
                  <div>
                    <p style={{ fontSize: '11px', color: C.sub }}>{p.supplier_name ?? <span style={{ color: '#EF4444' }}>⚠ None</span>}</p>
                    {p.all_suppliers.length > 1 && <p style={{ fontSize: '10px', color: C.faint }}>+{p.all_suppliers.length - 1} more</p>}
                  </div>
                  <Badge label={p.data_grade} {...grStyle} />
                  <Badge label={p.category} {...catStyle} />
                  <span style={{ fontSize: '12px', color: C.faint, textAlign: 'center' }}>{isOpen ? '▲' : '▼'}</span>
                </div>

                {/* Expanded editing panel */}
                {isOpen && (
                  <ExpandedRow
                    p={p}
                    verifiedBy={verifiedBy}
                    isSaving={isSaving}
                    patchProduct={patchProduct}
                    callUrl={callUrl}
                  />
                )}
              </div>
            )
          })}
        </div>}

        <div style={{ marginTop: '12px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: '8px' }}>
          <p style={{ fontSize: '11px', color: C.faint }}>
            Edits save to IMS immediately. Export CSV → input into DaySmart / Shopify. Do not feed back to Google Sheet.
          </p>
          <a href={exportUrl} style={{ fontSize: '12px', fontWeight: 600, color: C.ink, background: C.monoBg, border: '1px solid #E2E8F0', padding: '6px 14px', borderRadius: '6px', textDecoration: 'none' }}>
            ↓ Download CSV
          </a>
        </div>
      </div>
  )
}

// ── Expanded row ────────────────────────────────────────────────────────────

function ExpandedRow({ p, verifiedBy, isSaving, patchProduct, callUrl }: {
  p: Product
  verifiedBy: string
  isSaving: (sku: string, field: string) => boolean
  patchProduct: (sku: string, field: string, body: Record<string, unknown>) => Promise<void>
  callUrl: (sku: string, field: string, url: string, method?: string, body?: object) => Promise<void>
}) {
  const sku = p.sku_code

  // Local field state — edits buffer before save
  const [name,     setName]     = useState(p.name)
  const [brand,    setBrand]    = useState(p.brand ?? '')
  const [category, setCategory] = useState(p.category)
  const [status,   setStatus]   = useState(p.status)
  const [uom,      setUom]      = useState(p.uom ?? '')
  const [notes,    setNotes]    = useState(p.notes ?? '')
  const [clinicP,  setClinicP]  = useState(String(p.channels.find(c => c.channel === 'clinic')?.selling_price ?? ''))
  const [shopifyP, setShopifyP] = useState(String(p.channels.find(c => c.channel === 'shopify')?.selling_price ?? ''))
  const [hktvP,    setHktvP]    = useState(String(p.channels.find(c => c.channel === 'hktv')?.selling_price ?? ''))

  const [pricesDirty, setPricesDirty] = useState(false)
  const [costInput,   setCostInput]   = useState(p.primary_cost != null ? String(p.primary_cost) : '')
  const [packInput,   setPackInput]   = useState(p.units_per_pack != null ? String(p.units_per_pack) : '')
  const [invoiceRef,  setInvoiceRef]  = useState('')
  const [invoiceCost, setInvoiceCost] = useState(p.primary_cost != null ? String(p.primary_cost) : '')
  const [showInvoice, setShowInvoice] = useState(false)

  const catStyle = CAT_STYLE[category] ?? { bg: C.monoBg, color: C.sub }

  async function saveField(field: string, value: string | boolean | null) {
    await patchProduct(sku, field, { [field]: value })
  }

  async function savePrices() {
    const channels = [
      { ch: 'clinic',  val: clinicP },
      { ch: 'shopify', val: shopifyP },
      { ch: 'hktv',    val: hktvP },
    ]
    for (const { ch, val } of channels) {
      const num = parseFloat(val)
      if (!isNaN(num) && num > 0) {
        await callUrl(sku, `price-${ch}`, `${API}/products/${sku}/channels/${ch}/price`, 'PATCH', { selling_price: num })
      }
    }
    setPricesDirty(false)
  }

  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '5px 8px', fontSize: '12px', border: '1px solid #E2E8F0',
    borderRadius: '5px', color: C.ink, background: 'white', boxSizing: 'border-box',
  }
  const sectionStyle: React.CSSProperties = {
    background: C.wash, borderRadius: '6px', padding: '12px 14px',
  }

  return (
    <div style={{ padding: '0 16px 16px', background: C.primaryBg, borderTop: '1px solid #C7D2FE' }}>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '12px', paddingTop: '12px' }}>

        {/* ── Identity ── */}
        <div style={sectionStyle}>
          <p style={{ fontSize: '10px', fontWeight: 700, color: C.indigoInk, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '10px' }}>Identity</p>

          <FieldLabel>Name</FieldLabel>
          <input style={inputStyle} value={name} onChange={e => setName(e.target.value)}
            onBlur={() => name !== p.name && saveField('name', name)} />

          <FieldLabel>Brand</FieldLabel>
          <input style={{ ...inputStyle, marginTop: '6px' }} value={brand} onChange={e => setBrand(e.target.value)}
            onBlur={() => brand !== (p.brand ?? '') && saveField('brand', brand || null)} />

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginTop: '6px' }}>
            <div>
              <FieldLabel>Category</FieldLabel>
              <select value={category} onChange={e => { setCategory(e.target.value); saveField('category', e.target.value) }}
                style={{ ...inputStyle, background: catStyle.bg, color: catStyle.color, fontWeight: 600 }}>
                {CATEGORIES.map(c => <option key={c} value={c}>{c}</option>)}
              </select>
            </div>
            <div>
              <FieldLabel>Status</FieldLabel>
              <select value={status} onChange={e => { const v = e.target.value as typeof status; setStatus(v); saveField('status', v) }}
                style={{ ...inputStyle, background: STATUS_STYLE[status]?.bg ?? C.monoBg, fontWeight: 600 }}>
                {STATUSES.map(s => <option key={s}>{s}</option>)}
              </select>
            </div>
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginTop: '6px' }}>
            <div>
              <FieldLabel>UOM</FieldLabel>
              <input style={inputStyle} value={uom} onChange={e => setUom(e.target.value)}
                onBlur={() => uom !== (p.uom ?? '') && saveField('uom', uom || null)}
                placeholder="e.g. tablet, ml, g" />
            </div>
            <div>
              <FieldLabel>Hero SKU</FieldLabel>
              <select value={p.hero_sku ? 'Yes' : 'No'}
                onChange={e => saveField('hero_sku', e.target.value === 'Yes')}
                style={inputStyle}>
                <option>No</option><option>Yes</option>
              </select>
            </div>
          </div>

          <div style={{ marginTop: '6px' }}>
            <FieldLabel>Notes</FieldLabel>
            <textarea style={{ ...inputStyle, resize: 'vertical', minHeight: '52px' }}
              value={notes} onChange={e => setNotes(e.target.value)}
              onBlur={() => notes !== (p.notes ?? '') && saveField('notes', notes || null)}
              placeholder="Internal notes…" />
          </div>

          <div style={{ marginTop: '4px', fontSize: '10px', color: C.faint }}>
            {isSaving(sku, 'name') || isSaving(sku, 'brand') || isSaving(sku, 'category') || isSaving(sku, 'status') || isSaving(sku, 'uom') || isSaving(sku, 'notes') ? 'Saving…' : ''}
          </div>
        </div>

        {/* ── Suppliers + Cost ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <div style={sectionStyle}>
            <p style={{ fontSize: '10px', fontWeight: 700, color: C.indigoInk, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Suppliers</p>
            {p.all_suppliers.length === 0 ? (
              <p style={{ fontSize: '11px', color: '#EF4444' }}>⚠ No supplier linked — fix in the Sheet and re-sync</p>
            ) : p.all_suppliers.map(s => (
              <div key={s.supplier_id ?? s.name} style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '5px 0', borderBottom: '1px solid #F1F5F9' }}>
                <div style={{ flex: 1 }}>
                  <p style={{ fontSize: '12px', fontWeight: 600, color: C.ink }}>{s.name ?? '—'}</p>
                  {s.code && <p style={{ fontSize: '10px', color: C.faint }}>{s.code}</p>}
                  {s.basic_cost != null && <p style={{ fontSize: '10px', color: C.sub }}>HK${s.basic_cost.toFixed(2)} basic</p>}
                </div>
                <div style={{ display: 'flex', gap: '4px', alignItems: 'center' }}>
                  {s.is_primary && <Badge label="Primary" bg="#DBEAFE" color="#1E40AF" />}
                  {s.is_preferred && !s.is_primary && <Badge label="Cheapest" bg={C.greenBg} color={C.green} />}
                  {!s.is_primary && s.supplier_id != null && (
                    <button
                      disabled={isSaving(sku, `primary-${s.supplier_id}`)}
                      onClick={() => callUrl(sku, `primary-${s.supplier_id}`, `${API}/products/${sku}/suppliers/${s.supplier_id}/primary`, 'PATCH')}
                      style={{ fontSize: '10px', padding: '2px 8px', background: C.monoBg, color: C.sub, border: '1px solid #CBD5E1', borderRadius: '3px', cursor: 'pointer' }}>
                      Set primary
                    </button>
                  )}
                </div>
              </div>
            ))}
            <p style={{ fontSize: '10px', color: C.faint, marginTop: '6px' }}>
              To link a new supplier, use the <Link to={`/items/${skuToPath(sku)}` as never} style={{ color: C.indigo }}>detail page</Link> or re-sync after updating the Sheet.
            </p>
          </div>

          <div style={sectionStyle}>
            <p style={{ fontSize: '10px', fontWeight: 700, color: C.indigoInk, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Cost</p>
            {p.cost_sheet_conflict ? (
              <div style={{ background: C.warnBg, border: '1px solid #FCD34D', borderRadius: '5px', padding: '8px' }}>
                <p style={{ fontSize: '10px', fontWeight: 700, color: C.amberInk, marginBottom: '4px' }}>⚡ Conflict — Sheet ≠ IMS</p>
                <p style={{ fontSize: '11px', color: '#78350F' }}>Sheet: <strong>HK${p.basic_cost_sheet?.toFixed(2)}</strong></p>
                <p style={{ fontSize: '11px', color: C.ink, marginBottom: '8px' }}>IMS: <strong>HK${p.primary_cost?.toFixed(2)}</strong> · {COST_SOURCE_LABEL[p.cost_source]}</p>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button disabled={isSaving(sku, 'accept-cost')} onClick={() => callUrl(sku, 'accept-cost', `${API}/products/${sku}/cost/accept-sheet`)}
                    style={{ fontSize: '11px', fontWeight: 600, padding: '4px 10px', background: C.amberInk, color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
                    {isSaving(sku, 'accept-cost') ? 'Saving…' : 'Use Sheet value'}
                  </button>
                  <button disabled={isSaving(sku, 'dismiss-cost')} onClick={() => callUrl(sku, 'dismiss-cost', `${API}/products/${sku}/cost/dismiss-conflict`)}
                    style={{ fontSize: '11px', padding: '4px 10px', background: C.monoBg, color: C.sub, border: '1px solid #CBD5E1', borderRadius: '4px', cursor: 'pointer' }}>
                    Keep IMS
                  </button>
                </div>
              </div>
            ) : (
              <div>
                {/* Current cost + source */}
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '2px' }}>
                  <span style={{ fontSize: '18px', fontWeight: 700, color: C.ink }}>
                    {p.primary_cost != null ? `HK$${p.primary_cost.toFixed(2)}` : <span style={{ fontSize: '13px', color: '#EF4444' }}>⚠ Missing</span>}
                  </span>
                  {p.primary_cost != null && (
                    <span style={{ fontSize: '10px', fontWeight: 700, ...COST_SOURCE_STYLE[p.cost_source], padding: '1px 6px', borderRadius: '3px' }}>
                      {COST_SOURCE_LABEL[p.cost_source]}
                    </span>
                  )}
                </div>
                {p.cost_is_stale && <p style={{ fontSize: '10px', color: '#F59E0B', marginTop: '2px' }}>⏱ Stale — not updated in 90+ days</p>}

                {/* Inline cost edit — for Philippines team direct entry */}
                {p.cost_source !== 'invoice_matched' && (
                  <div style={{ display: 'flex', gap: '6px', alignItems: 'center', marginTop: '8px' }}>
                    <span style={{ fontSize: '11px', color: C.faint }}>HK$</span>
                    <input
                      type="number" min={0} step={0.01}
                      value={costInput}
                      onChange={e => setCostInput(e.target.value)}
                      placeholder="Enter cost"
                      style={{ ...inputStyle, width: '90px' }}
                    />
                    <SaveBtn
                      saving={isSaving(sku, 'basic_cost')}
                      dirty={costInput !== '' && parseFloat(costInput) !== p.primary_cost}
                      onClick={() => {
                        const n = parseFloat(costInput)
                        if (!isNaN(n) && n >= 0) patchProduct(sku, 'basic_cost', { basic_cost: n })
                      }}
                    />
                  </div>
                )}

                {/* Invoice lock — for Desmond 3-way match */}
                {p.cost_source === 'invoice_matched' ? (
                  <div style={{ marginTop: '8px', fontSize: '10px', color: C.green, background: C.greenBg, padding: '6px 8px', borderRadius: '4px', display: 'flex', gap: '4px', alignItems: 'center' }}>
                    <span>🔒</span>
                    <span>Invoice locked · {p.cost_source_ref}</span>
                  </div>
                ) : (
                  <div style={{ marginTop: '8px' }}>
                    {!showInvoice ? (
                      <button onClick={() => setShowInvoice(true)}
                        style={{ fontSize: '10px', color: C.indigoInk, background: 'none', border: '1px solid #C7D2FE', borderRadius: '4px', padding: '3px 8px', cursor: 'pointer' }}>
                        🔒 Lock to invoice (3-way match)
                      </button>
                    ) : (
                      <div style={{ background: C.primaryBg, border: '1px solid #C7D2FE', borderRadius: '6px', padding: '10px' }}>
                        <p style={{ fontSize: '10px', fontWeight: 700, color: C.indigoInk, marginBottom: '8px' }}>Confirm against invoice</p>
                        <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', alignItems: 'center' }}>
                          <input
                            type="text" placeholder="Invoice ref e.g. INV-2026-042"
                            value={invoiceRef} onChange={e => setInvoiceRef(e.target.value)}
                            style={{ ...inputStyle, width: '180px', fontSize: '11px' }}
                          />
                          <span style={{ fontSize: '11px', color: C.faint }}>HK$</span>
                          <input
                            type="number" min={0} step={0.01}
                            placeholder="Cost"
                            value={invoiceCost} onChange={e => setInvoiceCost(e.target.value)}
                            style={{ ...inputStyle, width: '80px', fontSize: '11px' }}
                          />
                          <button
                            disabled={!invoiceRef.trim() || !invoiceCost || isSaving(sku, 'lock-invoice')}
                            onClick={() => callUrl(sku, 'lock-invoice', `${API}/products/${sku}/cost/lock-invoice`, 'POST', { invoice_ref: invoiceRef.trim(), confirmed_cost: parseFloat(invoiceCost) })}
                            style={{ fontSize: '11px', fontWeight: 700, padding: '4px 12px', background: C.indigoInk, color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
                            {isSaving(sku, 'lock-invoice') ? 'Locking…' : '🔒 Lock'}
                          </button>
                          <button onClick={() => setShowInvoice(false)}
                            style={{ fontSize: '11px', color: C.faint, background: 'none', border: 'none', cursor: 'pointer' }}>
                            Cancel
                          </button>
                        </div>
                        <p style={{ fontSize: '10px', color: C.indigo, marginTop: '6px' }}>
                          Once locked at invoice level, Sheet sync cannot overwrite this cost.
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>
        </div>

        {/* ── Pack Size + Prices ── */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          <div style={sectionStyle}>
            <p style={{ fontSize: '10px', fontWeight: 700, color: C.indigoInk, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>Pack Size</p>
            {p.pack_sheet_conflict ? (
              <div style={{ background: C.warnBg, border: '1px solid #FCD34D', borderRadius: '5px', padding: '8px' }}>
                <p style={{ fontSize: '10px', fontWeight: 700, color: C.amberInk, marginBottom: '4px' }}>⚡ Conflict — Sheet ≠ IMS</p>
                <p style={{ fontSize: '11px', color: '#78350F' }}>Sheet: <strong>{p.units_per_pack_sheet} units</strong></p>
                <p style={{ fontSize: '11px', color: C.ink, marginBottom: '8px' }}>IMS: <strong>{p.units_per_pack} units</strong> (verified)</p>
                <div style={{ display: 'flex', gap: '6px' }}>
                  <button disabled={isSaving(sku, 'accept-uom')} onClick={() => callUrl(sku, 'accept-uom', `${API}/products/${sku}/uom/accept-sheet`, 'POST', { verified_by: verifiedBy || null })}
                    style={{ fontSize: '11px', fontWeight: 600, padding: '4px 10px', background: C.amberInk, color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
                    Use Sheet
                  </button>
                  <button disabled={isSaving(sku, 'keep-uom')} onClick={() => callUrl(sku, 'keep-uom', `${API}/products/${sku}/uom`, 'PATCH', { verified_by: verifiedBy || null })}
                    style={{ fontSize: '11px', padding: '4px 10px', background: C.monoBg, color: C.sub, border: '1px solid #CBD5E1', borderRadius: '4px', cursor: 'pointer' }}>
                    Keep IMS
                  </button>
                </div>
              </div>
            ) : p.units_per_pack != null ? (
              <div>
                <p style={{ fontSize: '18px', fontWeight: 700, color: C.ink }}>{p.units_per_pack} <span style={{ fontSize: '12px', fontWeight: 400, color: C.muted }}>units/pack</span></p>
                {p.uom_verified_at ? (
                  <p style={{ fontSize: '10px', color: C.green, marginTop: '2px' }}>✓ Verified {p.uom_verified_at.slice(0, 10)}{p.uom_verified_by ? ` · ${p.uom_verified_by}` : ''}</p>
                ) : (
                  <button disabled={isSaving(sku, 'verify-uom')} onClick={() => callUrl(sku, 'verify-uom', `${API}/products/${sku}/uom`, 'PATCH', { verified_by: verifiedBy || null })}
                    style={{ marginTop: '6px', fontSize: '11px', fontWeight: 600, padding: '4px 12px', background: C.green, color: 'white', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
                    {isSaving(sku, 'verify-uom') ? 'Saving…' : '✓ Confirm correct'}
                  </button>
                )}
              </div>
            ) : (
              <div>
                <p style={{ fontSize: '11px', color: C.faint, marginBottom: '8px' }}>Pack size not set — enter it here:</p>
                <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
                  <input
                    type="number" min={1} step={1}
                    value={packInput}
                    onChange={e => setPackInput(e.target.value)}
                    placeholder="e.g. 100"
                    style={{ ...inputStyle, width: '80px' }}
                  />
                  <span style={{ fontSize: '11px', color: C.muted }}>units/pack</span>
                  <SaveBtn
                    saving={isSaving(sku, 'units_per_pack')}
                    dirty={packInput !== '' && parseInt(packInput) > 0}
                    onClick={() => {
                      const n = parseInt(packInput)
                      if (!isNaN(n) && n > 0) patchProduct(sku, 'units_per_pack', { units_per_pack: n })
                    }}
                  />
                </div>
              </div>
            )}
          </div>

          <div style={sectionStyle}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
              <p style={{ fontSize: '10px', fontWeight: 700, color: C.indigoInk, textTransform: 'uppercase', letterSpacing: '0.06em' }}>Selling Prices</p>
              <SaveBtn saving={isSaving(sku, 'price-clinic') || isSaving(sku, 'price-shopify') || isSaving(sku, 'price-hktv')} dirty={pricesDirty} onClick={savePrices} label="Save prices" />
            </div>
            {[
              { ch: 'clinic',  label: 'Clinic (DaySmart)', val: clinicP,  set: setClinicP,  rec: p.channels.find(c => c.channel === 'clinic')?.recommendation },
              { ch: 'shopify', label: 'Shopify',           val: shopifyP, set: setShopifyP, rec: p.channels.find(c => c.channel === 'shopify')?.recommendation },
              { ch: 'hktv',    label: 'HKTVMall',          val: hktvP,    set: setHktvP,    rec: p.channels.find(c => c.channel === 'hktv')?.recommendation },
            ].map(({ ch, label, val, set, rec }) => {
              if (!p.channels.find(c => c.channel === ch)) return null
              const alertColor = rec === 'Raise price ⚠' ? C.amberInk : rec === 'Check pack size ⚠' ? '#1E40AF' : undefined
              return (
                <div key={ch} style={{ display: 'grid', gridTemplateColumns: '100px 1fr auto', gap: '8px', alignItems: 'center', marginBottom: '6px' }}>
                  <span style={{ fontSize: '11px', color: C.muted }}>{label}</span>
                  <div style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                    <span style={{ fontSize: '11px', color: C.faint }}>HK$</span>
                    <input type="number" min={0} step={0.5} value={val}
                      onChange={e => { set(e.target.value); setPricesDirty(true) }}
                      style={{ width: '80px', padding: '4px 6px', fontSize: '12px', fontWeight: 600, border: `1px solid ${pricesDirty ? C.indigo : C.line}`, borderRadius: '4px', background: pricesDirty ? C.primaryBg : 'white', color: C.ink }} />
                  </div>
                  {rec && rec !== 'Price is OK ✓' && (
                    <span style={{ fontSize: '10px', color: alertColor, fontWeight: 600 }}>
                      {rec === 'Raise price ⚠' ? '↑' : '?'}
                    </span>
                  )}
                </div>
              )
            })}
            <p style={{ fontSize: '10px', color: C.faint, marginTop: '6px' }}>Saved to IMS → export CSV → input into DaySmart / Shopify</p>
          </div>
        </div>

      </div>

      {/* Bottom bar */}
      <div style={{ marginTop: '8px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Link to={`/items/${skuToPath(sku)}` as never} style={{ fontSize: '11px', color: C.indigo, textDecoration: 'none' }}>
          Full detail page →
        </Link>
        <button onClick={() => {}} style={{ fontSize: '11px', color: C.faint, background: 'none', border: 'none', cursor: 'pointer' }}>
          {/* placeholder for future actions */}
        </button>
      </div>
    </div>
  )
}

// ─── Fix JSX interpolation issue with status style ───────────────────────────
// Status select onChange needs current value — handled via state above.

function DataReviewPage() {
  return (
    <Suspense fallback={<div style={{ padding: '60px', textAlign: 'center', color: C.faint, fontSize: '13px' }}>Loading…</div>}>
      <DataReviewContent />
    </Suspense>
  )
}

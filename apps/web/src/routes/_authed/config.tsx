import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState, useCallback, useRef } from 'react'
import { can } from '@/lib/auth'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'
import { getTransformations, getConfigVersions, editParameter, editFormula, editTable, validateConfigEdit, restoreConfigVersion } from '@/lib/api'
import type { TransformationConfig, ConfigVersionInfo, ConfigTable } from '@/lib/types'

const CARD: React.CSSProperties = { background: '#FFFFFF', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '14px 16px' }
const CAT_LABEL: Record<string, string> = { cost: 'Cost', margin: 'Margins', inventory: 'Inventory / WOC', classification: 'Classification' }
const CAT_ORDER = ['cost', 'margin', 'inventory', 'classification']

// The ONLY variables a formula may reference — each maps to a real value the engine passes in at
// runtime. The backend rejects anything else (sandbox allow-list); the UI surfaces the same set
// and validates against it before you can save, so you can't reference a value that doesn't exist.
const VARIABLE_CATALOG: Record<string, string> = {
  basic_cost:     'Supplier whole-pack cost (ProductSupplier.basic_cost)',
  units_per_pack: 'Pack size — sell-units per buy-pack',
  price:          'Channel selling price',
  cost:           'Per-sell-unit cost fed into the margin',
  fee_pct:        'Channel fee, as a fraction (0–1)',
  delivery:       'Per-unit delivery cost (HK$)',
  total_qty:      'Total stock (clinic + warehouse)',
  weekly_demand:  'Weekly demand (latest sales velocity)',
  base:           'Base per-sell-unit cost',
  min_qty:        'Bulk-buy minimum quantity',
  free_qty:       'Bulk-buy free quantity',
  discount_pct:   'Bulk-buy discount, as a fraction (0–1)',
  weight_g:       'Shipping weight (grams)',
}
const ALLOWED_FUNCS = ['round', 'abs', 'min', 'max']
const FORMULA_WORDS = new Set<string>(['None', 'True', 'False', 'if', 'else', 'and', 'or', 'not', 'is', ...ALLOWED_FUNCS])

// Per-parameter constraints — mirror backend validate_param so the input is bounded, not free-form.
type ParamRule = { min?: number; max?: number; step?: number; integer?: boolean; pct?: boolean; hint: string }
const PARAM_RULES: Record<string, ParamRule> = {
  hktv_fee:                { min: 0, max: 1, step: 0.01, pct: true, hint: 'fraction 0–1 (0.18 = 18%)' },
  cross_channel_threshold: { min: 0, max: 1, step: 0.01, pct: true, hint: 'fraction 0–1 (0.05 = 5%)' },
  staleness_days:          { min: 1, step: 1, integer: true, hint: 'whole days, ≥ 1' },
}

const KIND_BADGE: Record<string, { label: string; bg: string; fg: string }> = {
  parameter: { label: 'value',   bg: '#EEF2FF', fg: '#3730A3' },
  table:     { label: 'table',   bg: '#ECFEFF', fg: '#0E7490' },
  formula:   { label: 'formula', bg: '#FEF3C7', fg: '#92400E' },
}

/** Identifiers in a formula that aren't an allowed variable / function / keyword. */
function unknownVars(formula: string, inputs: string[]): string[] {
  const ids = formula.match(/[A-Za-z_][A-Za-z0-9_]*/g) ?? []
  const allowed = new Set<string>([...inputs, ...FORMULA_WORDS])
  return Array.from(new Set(ids.filter(id => !allowed.has(id))))
}

/** Client-side parameter check (mirrors the backend); returns an error string, or null if ok. */
function paramError(key: string, raw: string): string | null {
  if (raw.trim() === '') return 'Enter a number'
  const n = Number(raw)
  if (Number.isNaN(n)) return 'Not a number'
  const r = PARAM_RULES[key]
  if (!r) return null
  if (r.integer && !Number.isInteger(n)) return 'Must be a whole number'
  if (r.min != null && n < r.min) return `Must be ≥ ${r.min}`
  if (r.max != null && n > r.max) return `Must be ≤ ${r.max}`
  return null
}

// A table edited as strings (so intermediate typing states are allowed), parsed + checked on save.
type TableDraft = { tiers: [string, string][]; over: string; unknown: string }

function tableToDraft(t: ConfigTable): TableDraft {
  return {
    tiers: t.tiers.map(([l, v]) => [String(l), String(v)] as [string, string]),
    over: String(t.over), unknown: String(t.unknown),
  }
}

/** Parse + validate a table draft (mirrors the backend validate_table). Returns the table or an error. */
function tableFromDraft(d: TableDraft): ConfigTable | string {
  const tiers: [number, number][] = []
  let prev: number | null = null
  for (const [ls, vs] of d.tiers) {
    if (ls.trim() === '' || vs.trim() === '') return 'Every tier needs a limit and a value'
    const l = Number(ls), v = Number(vs)
    if (Number.isNaN(l) || Number.isNaN(v)) return 'Tier limit and value must be numbers'
    if (l <= 0) return 'Tier limit must be greater than 0'
    if (v < 0) return 'Tier value must be 0 or more'
    if (prev != null && l <= prev) return 'Tier limits must strictly increase (top to bottom)'
    prev = l
    tiers.push([l, v])
  }
  if (!tiers.length) return 'Add at least one tier'
  const over = Number(d.over), unknown = Number(d.unknown)
  if (d.over.trim() === '' || Number.isNaN(over) || over < 0) return "'over' must be a number ≥ 0"
  if (d.unknown.trim() === '' || Number.isNaN(unknown) || unknown < 0) return "'unknown' must be a number ≥ 0"
  return { tiers, over, unknown }
}

export const Route = createFileRoute('/_authed/config')({ component: ConfigPage })

function ConfigPage() {
  const editable = can('config_admin')
  const [items, setItems] = useState<TransformationConfig[]>([])
  const [versions, setVersions] = useState<ConfigVersionInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [drafts, setDrafts] = useState<Record<string, string>>({})               // parameter drafts
  const [fDrafts, setFDrafts] = useState<Record<string, string>>({})             // formula drafts
  const [tDrafts, setTDrafts] = useState<Record<string, TableDraft>>({})         // table drafts
  const [vResult, setVResult] = useState<Record<string, { ok: boolean; error?: string }>>({})
  const [busy, setBusy] = useState<string | null>(null)
  const taRefs = useRef<Record<string, HTMLTextAreaElement | null>>({})

  const load = useCallback(async () => {
    setLoading(true); setError('')
    try {
      const [t, v] = await Promise.all([getTransformations(), getConfigVersions()])
      setItems(t); setVersions(v)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load config')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  // ── parameters ──
  const pCurrent = (t: TransformationConfig) => String(t.value ?? '')
  const pDraft = (t: TransformationConfig) => drafts[t.key] ?? pCurrent(t)
  const pDirty = (t: TransformationConfig) => pDraft(t) !== pCurrent(t)

  async function saveParam(t: TransformationConfig) {
    const raw = pDraft(t)
    const err = paramError(t.key, raw)
    if (err) { toast.error(err); return }
    setBusy(t.key)
    try {
      await editParameter(t.key, Number(raw))
      toast.success(`${t.name} updated`)
      setDrafts(d => { const n = { ...d }; delete n[t.key]; return n })
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy(null)
    }
  }

  // ── formulas ──
  const fCurrent = (t: TransformationConfig) => t.formula ?? ''
  const fDraft = (t: TransformationConfig) => fDrafts[t.key] ?? fCurrent(t)
  const fDirty = (t: TransformationConfig) => fDraft(t).trim() !== fCurrent(t).trim()
  const fUnknown = (t: TransformationConfig) => unknownVars(fDraft(t), t.inputs)

  function setFormula(t: TransformationConfig, v: string) {
    setFDrafts(d => ({ ...d, [t.key]: v }))
    setVResult(r => { const n = { ...r }; delete n[t.key]; return n })
  }

  function insertVar(t: TransformationConfig, v: string) {
    const ta = taRefs.current[t.key]
    const cur = fDraft(t)
    const s = ta?.selectionStart ?? cur.length
    const e = ta?.selectionEnd ?? cur.length
    setFormula(t, cur.slice(0, s) + v + cur.slice(e))
    requestAnimationFrame(() => {
      const el = taRefs.current[t.key]
      if (el) { el.focus(); const p = s + v.length; el.setSelectionRange(p, p) }
    })
  }

  async function validateFormula(t: TransformationConfig) {
    const unk = fUnknown(t)
    if (unk.length) { setVResult(r => ({ ...r, [t.key]: { ok: false, error: `unknown variable(s): ${unk.join(', ')}` } })); return }
    setBusy(`v:${t.key}`)
    try {
      const res = await validateConfigEdit(t.key, { formula: fDraft(t).trim() })
      setVResult(r => ({ ...r, [t.key]: res }))
    } catch (e) {
      setVResult(r => ({ ...r, [t.key]: { ok: false, error: e instanceof Error ? e.message : 'error' } }))
    } finally {
      setBusy(null)
    }
  }

  async function saveFormula(t: TransformationConfig) {
    const formula = fDraft(t).trim()
    if (!formula) { toast.error('Formula cannot be empty'); return }
    const unk = fUnknown(t)
    if (unk.length) { toast.error(`Unknown variable(s): ${unk.join(', ')}`); return }
    setBusy(t.key)
    try {
      await editFormula(t.key, formula)
      toast.success(`${t.name} formula updated`)
      setFDrafts(d => { const n = { ...d }; delete n[t.key]; return n })
      setVResult(r => { const n = { ...r }; delete n[t.key]; return n })
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy(null)
    }
  }

  // ── tables (e.g. sf_logistics tiers) ──
  const tDraft = (t: TransformationConfig): TableDraft => tDrafts[t.key] ?? tableToDraft(t.table!)
  const setTable = (key: string, d: TableDraft) => setTDrafts(s => ({ ...s, [key]: d }))
  const updTier = (t: TransformationConfig, i: number, j: 0 | 1, val: string) => {
    const d = tDraft(t); const tiers = d.tiers.map(r => [...r] as [string, string]); tiers[i][j] = val
    setTable(t.key, { ...d, tiers })
  }
  const addTier = (t: TransformationConfig) => { const d = tDraft(t); setTable(t.key, { ...d, tiers: [...d.tiers, ['', '']] }) }
  const rmTier = (t: TransformationConfig, i: number) => { const d = tDraft(t); setTable(t.key, { ...d, tiers: d.tiers.filter((_, k) => k !== i) }) }

  async function saveTable(t: TransformationConfig) {
    const parsed = tableFromDraft(tDraft(t))
    if (typeof parsed === 'string') { toast.error(parsed); return }
    setBusy(t.key)
    try {
      await editTable(t.key, parsed)
      toast.success(`${t.name} updated`)
      setTDrafts(s => { const n = { ...s }; delete n[t.key]; return n })
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Save failed')
    } finally {
      setBusy(null)
    }
  }

  async function restore(v: ConfigVersionInfo) {
    const ok = await confirmDialog({
      title: 'Restore config version',
      message: `Restore version ${v.id}? This creates a new active version with those values — every margin/cost/WOC recomputes immediately.`,
      confirmLabel: 'Restore',
    })
    if (!ok) return
    setBusy(`v${v.id}`)
    try {
      await restoreConfigVersion(v.id)
      toast.success(`Restored to version ${v.id}`)
      await load()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Restore failed')
    } finally {
      setBusy(null)
    }
  }

  const groups = CAT_ORDER
    .map(cat => ({ cat, rows: items.filter(i => i.category === cat) }))
    .filter(g => g.rows.length)

  return (
    <div style={{ padding: '24px 28px', maxWidth: '1160px' }}>
      <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A', margin: '0 0 2px' }}>Transformation Config</h1>
      <p style={{ fontSize: '13px', color: '#64748B', margin: '0 0 20px', maxWidth: '760px', lineHeight: 1.5 }}>
        {editable
          ? 'The formulas and values behind every margin, cost, and WOC number. Edit a value or formula and it takes effect immediately — every change is versioned, so you can roll back any time. Formulas may only use the predefined variables shown on each card.'
          : 'The formulas and values behind every margin, cost, and WOC number. Editing needs the config-admin role.'}
      </p>

      {error && <div style={{ ...CARD, borderColor: '#FCA5A5', color: '#B91C1C', marginBottom: '16px' }}>{error}</div>}

      {loading ? (
        <div style={{ color: '#94A3B8', fontSize: '13px' }}>Loading…</div>
      ) : (
        <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 300px', gap: '24px', alignItems: 'start' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
            {groups.map(({ cat, rows }) => (
              <div key={cat}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '8px', margin: '0 0 10px' }}>
                  <span style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase', color: '#64748B' }}>{CAT_LABEL[cat] ?? cat}</span>
                  <span style={{ fontSize: '11px', color: '#CBD5E1' }}>{rows.length}</span>
                  <span style={{ flex: 1, height: '1px', background: '#EEF1F5' }} />
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  {rows.map(t => {
                    const badge = KIND_BADGE[t.kind]
                    return (
                      <div key={t.key} style={CARD}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                          <span style={{ fontSize: '13.5px', fontWeight: 600, color: '#0F172A' }}>{t.name}</span>
                          {badge && <span style={{ fontSize: '9.5px', fontWeight: 700, letterSpacing: '.04em', textTransform: 'uppercase', color: badge.fg, background: badge.bg, padding: '1px 6px', borderRadius: '4px' }}>{badge.label}</span>}
                          <span style={{ flex: 1 }} />
                          {t.output_field && <span style={{ fontSize: '10.5px', color: '#94A3B8', fontFamily: 'ui-monospace, Menlo, monospace' }}>→ {t.output_field}</span>}
                        </div>
                        {t.description && <div style={{ fontSize: '12px', color: '#64748B', margin: '5px 0 11px', lineHeight: 1.45 }}>{t.description}</div>}

                        {/* ── parameter ── */}
                        {t.kind === 'parameter' && (() => {
                          const rule = PARAM_RULES[t.key]
                          const raw = pDraft(t)
                          const perr = editable && pDirty(t) ? paramError(t.key, raw) : null
                          const asPct = rule?.pct && !Number.isNaN(Number(raw)) && raw.trim() !== ''
                          return (
                            <div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexWrap: 'wrap' }}>
                                <input
                                  type="number"
                                  min={rule?.min} max={rule?.max} step={rule?.step ?? 'any'}
                                  value={raw}
                                  disabled={!editable || busy === t.key}
                                  onChange={e => setDrafts(d => ({ ...d, [t.key]: e.target.value }))}
                                  style={{ border: `1px solid ${perr ? '#FCA5A5' : '#E2E8F0'}`, borderRadius: '5px', padding: '6px 9px', fontSize: '13px', width: '130px' }}
                                />
                                {asPct && <span style={{ fontSize: '12px', color: '#64748B' }}>= {(Number(raw) * 100).toFixed(1)}%</span>}
                                {editable && (
                                  <button
                                    onClick={() => saveParam(t)}
                                    disabled={busy === t.key || !pDirty(t) || !!paramError(t.key, raw)}
                                    style={{ background: '#6366F1', color: '#fff', border: 'none', borderRadius: '5px', padding: '6px 12px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', opacity: (busy === t.key || !pDirty(t) || !!paramError(t.key, raw)) ? 0.5 : 1 }}
                                  >{busy === t.key ? 'Saving…' : 'Save'}</button>
                                )}
                              </div>
                              <div style={{ fontSize: '11px', marginTop: '6px', color: perr ? '#B91C1C' : '#94A3B8' }}>
                                {perr ? `✕ ${perr}` : (rule ? `Allowed: ${rule.hint}` : 'Any number')}
                              </div>
                            </div>
                          )
                        })()}

                        {/* ── table (tier editor) ── */}
                        {t.kind === 'table' && t.table && (() => {
                          if (!editable) {
                            return (
                              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', fontSize: '11.5px', color: '#334155', fontVariantNumeric: 'tabular-nums' }}>
                                {t.table!.tiers.map(([lim, val], i) => (
                                  <span key={i} style={{ background: '#F8FAFC', border: '1px solid #EEF1F5', borderRadius: '4px', padding: '2px 7px' }}>≤ {lim} → <b>{val}</b></span>
                                ))}
                                <span style={{ background: '#F8FAFC', border: '1px solid #EEF1F5', borderRadius: '4px', padding: '2px 7px' }}>over → <b>{t.table!.over}</b></span>
                                <span style={{ background: '#F8FAFC', border: '1px solid #EEF1F5', borderRadius: '4px', padding: '2px 7px' }}>unknown → <b>{t.table!.unknown}</b></span>
                              </div>
                            )
                          }
                          const d = tDraft(t)
                          const parsed = tableFromDraft(d)
                          const err = typeof parsed === 'string' ? parsed : null
                          const changed = !err && JSON.stringify(parsed) !== JSON.stringify(t.table)
                          const numIn: React.CSSProperties = { border: '1px solid #E2E8F0', borderRadius: '5px', padding: '5px 8px', fontSize: '12px', width: '108px' }
                          return (
                            <div>
                              <div style={{ display: 'flex', gap: '8px', fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '.04em', marginBottom: '5px' }}>
                                <span style={{ width: '108px' }}>≤ weight (g)</span><span style={{ width: '108px' }}>cost (HK$)</span>
                              </div>
                              <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                                {d.tiers.map((tier, i) => (
                                  <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                    <input type="number" value={tier[0]} disabled={busy === t.key} onChange={e => updTier(t, i, 0, e.target.value)} style={numIn} />
                                    <input type="number" value={tier[1]} disabled={busy === t.key} onChange={e => updTier(t, i, 1, e.target.value)} style={numIn} />
                                    <button type="button" onClick={() => rmTier(t, i)} title="Remove tier" style={{ background: 'none', border: 'none', color: '#94A3B8', cursor: 'pointer', fontSize: '16px', lineHeight: 1, padding: '0 4px' }}>×</button>
                                  </div>
                                ))}
                              </div>
                              <button type="button" onClick={() => addTier(t)} style={{ marginTop: '7px', background: 'none', border: '1px dashed #CBD5E1', borderRadius: '5px', padding: '4px 10px', fontSize: '11.5px', color: '#6366F1', cursor: 'pointer' }}>+ Add tier</button>
                              <div style={{ display: 'flex', gap: '16px', marginTop: '11px', flexWrap: 'wrap' }}>
                                <label style={{ fontSize: '12px', color: '#334155', display: 'flex', alignItems: 'center', gap: '6px' }}>over (&gt; last tier): <input type="number" value={d.over} disabled={busy === t.key} onChange={e => setTable(t.key, { ...d, over: e.target.value })} style={{ ...numIn, width: '86px' }} /></label>
                                <label style={{ fontSize: '12px', color: '#334155', display: 'flex', alignItems: 'center', gap: '6px' }}>no weight: <input type="number" value={d.unknown} disabled={busy === t.key} onChange={e => setTable(t.key, { ...d, unknown: e.target.value })} style={{ ...numIn, width: '86px' }} /></label>
                              </div>
                              <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '11px' }}>
                                <button onClick={() => saveTable(t)} disabled={busy === t.key || !!err || !changed}
                                  style={{ background: '#6366F1', color: '#fff', border: 'none', borderRadius: '5px', padding: '6px 12px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', opacity: (busy === t.key || !!err || !changed) ? 0.5 : 1 }}>
                                  {busy === t.key ? 'Saving…' : 'Save'}</button>
                                {err ? <span style={{ color: '#B91C1C', fontSize: '11.5px' }}>✕ {err}</span>
                                     : changed ? <span style={{ color: '#64748B', fontSize: '11.5px' }}>unsaved changes</span> : null}
                              </div>
                              <div style={{ fontSize: '11px', color: '#94A3B8', marginTop: '9px', lineHeight: 1.5 }}>Lookup returns the first tier the weight does not exceed; <b>over</b> applies beyond the last tier, <b>no&nbsp;weight</b> when a SKU has no weight. Limits must strictly increase.</div>
                            </div>
                          )
                        })()}

                        {/* ── formula ── */}
                        {t.kind === 'formula' && (() => {
                          const unk = editable ? fUnknown(t) : []
                          const vr = vResult[t.key]
                          return (
                            <div>
                              {editable ? (
                                <>
                                  <div style={{ fontSize: '10.5px', fontWeight: 600, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '.04em', marginBottom: '5px' }}>Variables — click to insert</div>
                                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px', marginBottom: '8px' }}>
                                    {t.inputs.map(v => (
                                      <button key={v} type="button" onClick={() => insertVar(t, v)} title={VARIABLE_CATALOG[v] ?? v}
                                        style={{ fontFamily: 'ui-monospace, Menlo, monospace', fontSize: '10.5px', color: '#3730A3', background: '#EEF2FF', border: '1px solid #E0E7FF', borderRadius: '4px', padding: '2px 7px', cursor: 'pointer' }}>{v}</button>
                                    ))}
                                    {!t.inputs.length && <span style={{ fontSize: '11px', color: '#94A3B8' }}>no variables</span>}
                                  </div>
                                  <textarea
                                    ref={el => { taRefs.current[t.key] = el }}
                                    value={fDraft(t)}
                                    disabled={busy === t.key}
                                    onChange={e => setFormula(t, e.target.value)}
                                    rows={3}
                                    spellCheck={false}
                                    style={{ display: 'block', width: '100%', boxSizing: 'border-box', fontFamily: 'ui-monospace, Menlo, monospace', fontSize: '11.5px', color: '#0F172A', background: '#F8FAFC', border: `1px solid ${unk.length ? '#FCA5A5' : '#E2E8F0'}`, borderRadius: '6px', padding: '8px 10px', resize: 'vertical' }}
                                  />
                                  {unk.length > 0 && (
                                    <div style={{ fontSize: '11px', color: '#B91C1C', marginTop: '5px' }}>✕ unknown variable(s): <b>{unk.join(', ')}</b> — use only the variables above</div>
                                  )}
                                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '7px' }}>
                                    <button onClick={() => validateFormula(t)} disabled={busy === `v:${t.key}` || !fDraft(t).trim()}
                                      style={{ background: 'none', border: '1px solid #E2E8F0', borderRadius: '5px', padding: '5px 11px', fontSize: '12px', color: '#334155', cursor: 'pointer' }}>
                                      {busy === `v:${t.key}` ? '…' : 'Validate'}</button>
                                    <button onClick={() => saveFormula(t)} disabled={busy === t.key || !fDirty(t) || unk.length > 0}
                                      style={{ background: '#6366F1', color: '#fff', border: 'none', borderRadius: '5px', padding: '5px 12px', fontSize: '12px', fontWeight: 600, cursor: 'pointer', opacity: (busy === t.key || !fDirty(t) || unk.length > 0) ? 0.5 : 1 }}>
                                      {busy === t.key ? 'Saving…' : 'Save'}</button>
                                    {vr && (vr.ok
                                      ? <span style={{ color: '#059669', fontSize: '11.5px' }}>✓ valid</span>
                                      : <span style={{ color: '#B91C1C', fontSize: '11.5px' }}>✕ {vr.error}</span>)}
                                  </div>
                                </>
                              ) : (
                                <code style={{ display: 'block', fontSize: '11.5px', color: '#0F172A', background: '#F8FAFC', border: '1px solid #EEF1F5', borderRadius: '6px', padding: '8px 10px', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{t.formula}</code>
                              )}
                              {/* variable reference — what each predefined variable corresponds to */}
                              {t.inputs.length > 0 && (
                                <div style={{ marginTop: '8px', fontSize: '11px', color: '#94A3B8', lineHeight: 1.55 }}>
                                  {t.inputs.map(v => (
                                    <div key={v}><code style={{ color: '#475569' }}>{v}</code> — {VARIABLE_CATALOG[v] ?? 'value'}</div>
                                  ))}
                                  <div style={{ marginTop: '2px' }}>functions: {ALLOWED_FUNCS.join(', ')}</div>
                                </div>
                              )}
                            </div>
                          )
                        })()}
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>

          <div style={{ ...CARD, position: 'sticky', top: '16px' }}>
            <div style={{ fontSize: '11px', fontWeight: 700, letterSpacing: '.06em', textTransform: 'uppercase', color: '#64748B', margin: '0 0 10px' }}>Version history</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '70vh', overflow: 'auto' }}>
              {versions.map(v => (
                <div key={v.id} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '8px', fontSize: '12px' }}>
                  <div>
                    <span style={{ fontWeight: 600, color: '#0F172A' }}>v{v.id}</span>
                    {v.is_active && <span style={{ marginLeft: '6px', fontSize: '10px', color: '#059669', background: '#ECFDF5', padding: '1px 6px', borderRadius: '4px' }}>active</span>}
                    <div style={{ color: '#94A3B8', fontSize: '11px' }}>{(v.created_at || '').slice(0, 10)} · {v.created_by || '—'}</div>
                    {v.note && <div style={{ color: '#64748B', fontSize: '11px' }}>{v.note}</div>}
                  </div>
                  {editable && !v.is_active && (
                    <button onClick={() => restore(v)} disabled={busy === `v${v.id}`}
                      style={{ background: 'none', border: '1px solid #E2E8F0', borderRadius: '5px', padding: '4px 9px', fontSize: '11px', color: '#6366F1', cursor: 'pointer', flex: 'none' }}>
                      {busy === `v${v.id}` ? '…' : 'Restore'}</button>
                  )}
                </div>
              ))}
              {!versions.length && <div style={{ color: '#94A3B8', fontSize: '12px' }}>No versions yet.</div>}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

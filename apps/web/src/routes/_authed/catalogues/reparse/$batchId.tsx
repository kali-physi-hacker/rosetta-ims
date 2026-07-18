// Catalogue re-parse — per-item review screen. Loads a batch of re-parsed catalogue items,
// each shown as an "old vs new" card (every captured field, Current vs Re-parsed, changes
// highlighted). The reviewer confirms/rejects per item or in bulk; nothing touches live cost
// data until a change is confirmed. The batch also carries a flat `changes` list (alt view),
// which this screen ignores in favour of `items`.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import { Spinner } from '@/components/Spinner'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'
import {
  getReparseBatch, confirmReparse, discardReparse, editReparseField,
  type ReparseBatch, type ReparseItem, type ReparseField,
} from '@/lib/reparse'

export const Route = createFileRoute('/_authed/catalogues/reparse/$batchId')({ component: ReparseReviewPage })

// Command-Centre palette (exact hex, matching the mock).
const C = {
  panel: '#FFFFFF', ink: '#0F172A', sub: '#475569', faint: '#94A3B8', line: '#E2E8F0',
  indigo: '#6366F1', indigoBg: '#EEF0FE', indigoInk: '#4338CA', indigoLine: '#C7D2FE',
  ok: '#15803D', okBg: '#ECFDF5', okLine: '#A7F3D0', bad: '#B91C1C', badBg: '#FEF2F2',
  amber: '#B45309', amberBg: '#FEF6E7', amberLine: '#FCD9A6', monoBg: '#F1F5F9', knobOff: '#CBD5E1',
}
const MONO = 'ui-monospace, "SF Mono", Menlo, monospace'

// A confirmed item's changed fields render as done and are not actionable again; a locally
// "skipped" item is excluded from Confirm-all until undone; "partial" = some fields went stale.
type ItemUiState = 'pending' | 'confirmed' | 'partial' | 'rejected' | 'none'

function chip(bg: string, color: string, border?: string): React.CSSProperties {
  return {
    display: 'inline-flex', alignItems: 'center', gap: '4px', fontSize: '10.5px', fontWeight: 700,
    padding: '2px 9px', borderRadius: '99px', letterSpacing: '0.02em', whiteSpace: 'nowrap',
    background: bg, color, border: `1px solid ${border ?? 'transparent'}`,
  }
}
const actBtn = (kind: 'ok' | 'ghost'): React.CSSProperties => ({
  fontSize: '11px', fontWeight: 650, fontFamily: 'inherit', borderRadius: '6px', padding: '4px 10px',
  cursor: 'pointer', whiteSpace: 'nowrap',
  ...(kind === 'ok'
    ? { color: '#fff', background: C.ok, border: '1px solid transparent' }
    : { color: C.sub, background: C.panel, border: `1px solid ${C.line}` }),
})
const bulkBtn = (primary: boolean): React.CSSProperties => ({
  fontSize: '12px', fontWeight: 650, fontFamily: 'inherit', borderRadius: '8px', padding: '7px 13px',
  cursor: 'pointer', whiteSpace: 'nowrap',
  ...(primary
    ? { color: '#fff', background: C.ok, border: '1px solid transparent' }
    : { color: C.sub, background: C.panel, border: `1px solid ${C.line}` }),
})

// Field-row grid: field label · Current · Re-parsed. Changed rows get the indigo tint + rule;
// unchanged rows keep a transparent 2px rule so the columns line up with changed rows.
const frow = (changed: boolean): React.CSSProperties => ({
  display: 'grid', gridTemplateColumns: '150px 1fr 1fr', gap: '10px', alignItems: 'baseline',
  padding: '5px 8px', borderRadius: '7px', fontSize: '12.5px',
  borderLeft: `2px solid ${changed ? C.indigo : 'transparent'}`,
  background: changed ? C.indigoBg : 'transparent',
})
const flStyle: React.CSSProperties = { color: C.sub, fontFamily: MONO, fontSize: '11.5px', overflowWrap: 'anywhere' }
const valStyle: React.CSSProperties = { fontVariantNumeric: 'tabular-nums', overflowWrap: 'anywhere', minWidth: 0 }

function tagStyle(kind: 'same' | 'fix' | 'ok' | 'stale' | 'bad'): React.CSSProperties {
  const base: React.CSSProperties = {
    fontSize: '9.5px', fontWeight: 700, marginLeft: '7px', padding: '1px 6px',
    borderRadius: '4px', whiteSpace: 'nowrap',
  }
  if (kind === 'same') return { ...base, color: C.faint, background: C.monoBg }
  if (kind === 'fix') return { ...base, color: C.indigoInk, background: C.indigoBg }
  if (kind === 'ok') return { ...base, color: C.ok, background: C.okBg }
  if (kind === 'stale') return { ...base, color: C.amber, background: C.amberBg }
  return { ...base, color: C.bad, background: C.badBg }
}

const rowStyle: React.CSSProperties = {
  background: C.panel, border: `1px solid ${C.line}`, borderRadius: '11px', padding: '11px 14px',
  display: 'flex', alignItems: 'center', gap: '11px',
}
const cardStyle: React.CSSProperties = {
  background: C.panel, border: `1px solid ${C.indigoLine}`, borderRadius: '12px', overflow: 'hidden',
  boxShadow: '0 8px 24px rgba(15,23,42,0.08)',
}
const chdStyle: React.CSSProperties = {
  padding: '13px 15px', borderBottom: `1px solid ${C.line}`, display: 'flex', alignItems: 'flex-start', gap: '10px',
}
const grpStyle: React.CSSProperties = {
  fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase',
  letterSpacing: '0.06em', margin: '12px 0 5px',
}
const cfootStyle: React.CSSProperties = {
  padding: '11px 15px', borderTop: `1px solid ${C.line}`, display: 'flex', alignItems: 'center',
  gap: '10px', background: C.monoBg, flexWrap: 'wrap',
}
const chevBtn: React.CSSProperties = {
  color: C.faint, fontSize: '12px', width: '16px', minWidth: '16px', height: '16px', lineHeight: '16px',
  padding: 0, border: 'none', background: 'transparent', cursor: 'pointer', fontFamily: 'inherit', textAlign: 'center',
}
const tglBtn: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '7px', fontSize: '12px', color: C.sub,
  border: `1px solid ${C.line}`, borderRadius: '8px', padding: '5px 10px', background: C.panel,
  cursor: 'pointer', fontFamily: 'inherit',
}
const swTrack = (on: boolean): React.CSSProperties => ({
  width: '30px', height: '17px', borderRadius: '99px', position: 'relative',
  background: on ? C.indigo : C.knobOff, display: 'inline-block', flexShrink: 0, transition: 'background .15s',
})
const swKnob = (on: boolean): React.CSSProperties => ({
  position: 'absolute', top: '2px', left: on ? '15px' : '2px', width: '13px', height: '13px',
  borderRadius: '50%', background: '#fff', transition: 'left .15s',
})
const doneText: React.CSSProperties = {
  fontSize: '11px', fontWeight: 700, color: C.ok, display: 'inline-flex', alignItems: 'center', gap: '4px', whiteSpace: 'nowrap',
}

const fmtVal = (v: string | number | null) => (v === null || v === '' ? '—' : String(v))
const fmtCost = (n: number | null) => (n === null ? '—' : `$${n.toFixed(2)}`)

// Inline field-value editor (edit the Re-parsed value before confirm).
const editInputStyle: React.CSSProperties = {
  fontFamily: MONO, fontSize: '12px', padding: '2px 6px', borderRadius: '5px',
  border: `1px solid ${C.indigo}`, outline: 'none', width: '116px', color: C.ink, minWidth: 0,
}
const editOkBtn: React.CSSProperties = {
  fontSize: '11px', fontWeight: 700, color: '#fff', background: C.ok, border: '1px solid transparent',
  borderRadius: '5px', padding: '2px 7px', cursor: 'pointer', fontFamily: 'inherit', lineHeight: 1.5,
}
const editCancelBtn: React.CSSProperties = {
  fontSize: '11px', color: C.sub, background: C.panel, border: `1px solid ${C.line}`,
  borderRadius: '5px', padding: '2px 7px', cursor: 'pointer', fontFamily: 'inherit', lineHeight: 1.5,
}
const editPencil: React.CSSProperties = {
  marginLeft: '8px', fontSize: '10.5px', fontWeight: 700, color: C.indigoInk, background: C.indigoBg,
  border: `1px solid ${C.indigoLine}`, borderRadius: '5px', padding: '1px 7px', cursor: 'pointer',
  fontFamily: 'inherit', whiteSpace: 'nowrap', verticalAlign: 'middle',
}

// Per-item edit controller passed down to each field row. activeField = the field currently being edited
// in this item (null = none). onStart pre-fills the draft with the field's current Re-parsed value.
interface ItemEdit {
  activeField: string | null
  draft: string
  saving: boolean
  onStart: (field: string, current: string | null) => void
  onChange: (val: string) => void
  onSave: () => void
  onCancel: () => void
}

function scopeTitle(b: ReparseBatch): string {
  const items = b.items ?? []
  if (b.scope_type === 'item') return items[0]?.product_name || b.scope_ref
  if (b.scope_type === 'import') return items[0]?.source_file || `Import #${b.scope_ref}`
  return b.supplier_name || `Supplier #${b.scope_ref}`
}
const SCOPE_KIND: Record<ReparseBatch['scope_type'], string> = { supplier: 'Supplier', import: 'Import', item: 'SKU' }

function computeItemState(item: ReparseItem, rejected: Set<number>): ItemUiState {
  if (rejected.has(item.catalogue_item_id)) return 'rejected'
  const chg = item.fields.filter(f => f.changed)
  if (chg.length === 0) return 'none'
  const live = chg.filter(f => f.status !== 'superseded')   // superseded = replaced by a newer re-parse
  if (live.length === 0) return 'none'
  if (live.some(f => f.status === 'pending' || f.status === null)) return 'pending'
  if (live.every(f => f.status === 'confirmed')) return 'confirmed'
  return 'partial'
}

function CenteredCard({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      background: C.panel, border: `1px solid ${C.line}`, borderRadius: '14px',
      padding: '40px 32px', textAlign: 'center', maxWidth: '520px', margin: '40px auto',
    }}>{children}</div>
  )
}

// One captured field: label · Current · Re-parsed. Changed rows highlight the diff and carry a
// status-aware tag; cost-affecting changes append an "eff unit cost" sub-line. Editable fields can be
// hand-corrected inline (the ✎) before confirm.
function FieldRow({ f, edit }: { f: ReparseField, edit?: ItemEdit }) {
  const applied = f.status === 'confirmed'
  const skipped = f.status === 'stale'
  const rejected = f.status === 'rejected'
  const superseded = f.status === 'superseded'
  const strikeCurrent = f.changed && !skipped && !rejected && !superseded
  const tagLabel = f.reparsed === null || f.reparsed === '' ? 'scrubbed' : 'fixed'
  const hasCostSub = f.changed && f.affects_cost && (f.eff_cost_before !== null || f.eff_cost_after !== null)

  const reparsedColor = applied ? C.ok : skipped ? C.amber : (rejected || superseded) ? C.faint : C.ok
  // editable only while still actionable (pending / unchanged) — a confirmed/stale/superseded value is locked
  const canEdit = !!edit && f.editable && (f.status === null || f.status === 'pending')
  const editing = !!edit && edit.activeField === f.field

  return (
    <>
      <div style={hasCostSub
        ? { ...frow(true), borderBottomLeftRadius: 0, borderBottomRightRadius: 0 }
        : frow(editing ? true : f.changed)}>
        <span style={flStyle}>{f.field}</span>
        <span style={{ ...valStyle, color: strikeCurrent ? C.bad : C.sub, textDecoration: strikeCurrent ? 'line-through' : 'none' }}>
          {fmtVal(f.current)}
        </span>
        <span style={valStyle}>
          {editing ? (
            <span style={{ display: 'inline-flex', gap: '5px', alignItems: 'center', flexWrap: 'wrap' }}>
              <input
                autoFocus value={edit!.draft} placeholder="—" aria-label={`New value for ${f.field}`}
                onChange={e => edit!.onChange(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter') { e.preventDefault(); edit!.onSave() }
                  else if (e.key === 'Escape') { e.preventDefault(); edit!.onCancel() }
                }}
                style={editInputStyle}
              />
              <button onClick={edit!.onSave} disabled={edit!.saving} style={{ ...editOkBtn, opacity: edit!.saving ? 0.6 : 1 }} aria-label="Save value">
                {edit!.saving ? <Spinner size={9} color="#fff" /> : '✓'}
              </button>
              <button onClick={edit!.onCancel} disabled={edit!.saving} style={editCancelBtn} aria-label="Cancel edit">✕</button>
            </span>
          ) : !f.changed ? (
            <>
              <span style={{ color: C.faint }}>{fmtVal(f.reparsed)}</span>
              <span style={tagStyle('same')}>same</span>
              {canEdit && <button onClick={() => edit!.onStart(f.field, f.reparsed)} title="Edit this value before confirming" style={editPencil} aria-label={`Edit ${f.field}`}>✎ Edit</button>}
            </>
          ) : (
            <>
              <span style={{ color: reparsedColor, fontWeight: applied || (!skipped && !rejected && !superseded) ? 750 : 600 }}>{fmtVal(f.reparsed)}</span>
              {applied ? <span style={tagStyle('ok')}>✓ confirmed</span>
                : skipped ? <span style={tagStyle('stale')}>stale</span>
                : rejected ? <span style={tagStyle('bad')}>rejected</span>
                : superseded ? <span style={tagStyle('same')}>superseded</span>
                : <span style={tagStyle('fix')}>{tagLabel}</span>}
              {canEdit && <button onClick={() => edit!.onStart(f.field, f.reparsed)} title="Edit this value before confirming" style={editPencil} aria-label={`Edit ${f.field}`}>✎ Edit</button>}
            </>
          )}
        </span>
      </div>
      {hasCostSub && (
        <div style={{ ...frow(true), paddingTop: 0, borderTopLeftRadius: 0, borderTopRightRadius: 0 }}>
          <span />
          <span style={{ gridColumn: '2 / -1', fontSize: '11px', color: C.sub, margin: '-2px 0 2px' }}>
            eff unit cost <span style={{ color: C.faint, textDecoration: 'line-through' }}>{fmtCost(f.eff_cost_before)}</span>
            {' → '}<b style={{ color: C.ok }}>{fmtCost(f.eff_cost_after)}</b>
          </span>
        </div>
      )}
    </>
  )
}

interface ItemBlockProps {
  item: ReparseItem
  state: ItemUiState
  expanded: boolean
  changesOnly: boolean
  busy: boolean
  rowBusy: boolean
  onToggle: () => void
  onConfirm: () => void
  onSkip: () => void
  onUndo: () => void
  selected: boolean
  onSelect: () => void
  edit: ItemEdit
}

const cbStyle: React.CSSProperties = { accentColor: '#6366F1', width: '15px', height: '15px', cursor: 'pointer', flexShrink: 0, margin: 0 }

function ItemBlock({ item, state, expanded, changesOnly, busy, rowBusy, onToggle, onConfirm, onSkip, onUndo, selected, onSelect, edit }: ItemBlockProps) {
  const changed = item.fields.filter(f => f.changed)
  const pendingCount = changed.filter(f => f.status === 'pending').length || item.changed_count
  const staleCount = changed.filter(f => f.status === 'stale').length

  const identity = (
    <div style={{ flex: 1, minWidth: 0, cursor: 'pointer' }} onClick={onToggle}>
      <div style={{ fontFamily: MONO, fontSize: '11px', color: C.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.sku_code ?? '—'}</div>
      <div style={{ fontWeight: 650, fontSize: '13px', color: C.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.product_name}</div>
      {item.source_file && (
        <div style={{ fontSize: '10.5px', color: C.faint, marginTop: '1px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={item.source_file}>📄 {item.source_file}</div>
      )}
    </div>
  )

  // ---- collapsed row ----
  if (!expanded) {
    const single = item.changed_count === 1 && changed.length >= 1
    const summary = single
      ? `1 change · ${changed[0].field} ${fmtVal(changed[0].current)} → ${fmtVal(changed[0].reparsed)}`
      : `${item.changed_count} change${item.changed_count === 1 ? '' : 's'}`

    return (
      <div style={rowStyle}>
        <input type="checkbox" checked={selected} onChange={onSelect} style={cbStyle} aria-label="Select SKU" />
        <button onClick={onToggle} style={chevBtn} aria-label="Expand">▸</button>
        {identity}
        <span style={{ ...chip(C.indigoBg, C.indigoInk, C.indigoLine), maxWidth: '360px', overflow: 'hidden', textOverflow: 'ellipsis', display: 'inline-block' }} title={summary}>{summary}</span>
        {state === 'pending' && (
          <>
            <button style={{ ...actBtn('ok'), opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={onConfirm}>
              {rowBusy ? <Spinner size={10} color="#fff" /> : 'Confirm'}
            </button>
            <button style={{ ...actBtn('ghost'), opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={onSkip}>Skip</button>
          </>
        )}
        {state === 'confirmed' && <span style={doneText}>✓ Confirmed</span>}
        {state === 'partial' && <span style={{ fontSize: '11px', color: C.amber, whiteSpace: 'nowrap' }}>Applied · {staleCount} skipped</span>}
        {state === 'rejected' && (
          <>
            <span style={{ fontSize: '11px', color: C.faint, whiteSpace: 'nowrap' }}>Skipped</span>
            <button style={{ ...actBtn('ghost'), opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={onUndo}>Undo</button>
          </>
        )}
        {state === 'none' && <span style={{ fontSize: '11px', color: C.faint }}>No changes</span>}
      </div>
    )
  }

  // ---- expanded card ----
  const visible = changesOnly ? item.fields.filter(f => f.changed) : item.fields
  const groups: string[] = []
  for (const f of visible) if (!groups.includes(f.group)) groups.push(f.group)

  return (
    <div style={cardStyle}>
      <div style={chdStyle}>
        <input type="checkbox" checked={selected} onChange={onSelect} style={{ ...cbStyle, marginTop: '3px' }} aria-label="Select SKU" />
        <button onClick={onToggle} style={{ ...chevBtn, marginTop: '3px' }} aria-label="Collapse">▾</button>
        {identity}
        {item.committed && <span style={chip(C.amberBg, C.amber, C.amberLine)}>● live SKU</span>}
        <span style={chip(C.indigoBg, C.indigoInk, C.indigoLine)}>{item.changed_count} change{item.changed_count === 1 ? '' : 's'}</span>
      </div>

      <div style={{ padding: '6px 15px 4px' }}>
        {groups.map(g => (
          <div key={g}>
            <div style={grpStyle}>{g}</div>
            {visible.filter(f => f.group === g).map(f => <FieldRow key={f.field} f={f} edit={edit} />)}
          </div>
        ))}
        {visible.length === 0 && <div style={{ fontSize: '12px', color: C.faint, padding: '10px 8px' }}>No changed fields.</div>}
      </div>

      <div style={cfootStyle}>
        <span style={{ fontSize: '11px', color: C.faint, flex: 1, minWidth: '220px', lineHeight: 1.4 }}>
<b style={{ color: C.sub }}>Every captured field</b> is re-derived from the retained catalogue text and compared to the live SKU. Click <b style={{ color: C.indigoInk }}>✎ Edit</b> on any field to correct its value before confirming.
        </span>
        {state === 'pending' && (
          <>
            <button style={{ ...actBtn('ghost'), opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={onSkip}>Reject item</button>
            <button style={{ ...actBtn('ok'), opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={onConfirm}>
              {rowBusy ? <><Spinner size={10} color="#fff" /> Confirming…</> : `Confirm ${pendingCount} change${pendingCount === 1 ? '' : 's'}`}
            </button>
          </>
        )}
        {state === 'confirmed' && <span style={doneText}>✓ Confirmed — applied to live cost</span>}
        {state === 'partial' && <span style={{ fontSize: '11px', fontWeight: 700, color: C.amber, whiteSpace: 'nowrap' }}>Applied · {staleCount} skipped (went stale)</span>}
        {state === 'rejected' && (
          <>
            <span style={{ fontSize: '11px', color: C.faint }}>Skipped — not applied</span>
            <button style={{ ...actBtn('ghost'), opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={onUndo}>Undo</button>
          </>
        )}
        {state === 'none' && <span style={{ fontSize: '11px', color: C.faint }}>No changes</span>}
      </div>
    </div>
  )
}

function ReparseReviewPage() {
  const { batchId } = Route.useParams()

  const [batch, setBatch] = useState<ReparseBatch | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [changesOnly, setChangesOnly] = useState(false)
  const [inInventoryOnly, setInInventoryOnly] = useState(true)   // hide catalogue rows not matched to a live SKU (default on)
  const [expanded, setExpanded] = useState<Set<number>>(new Set())
  const [rejected, setRejected] = useState<Set<number>>(new Set())      // locally-skipped catalogue_item_ids
  const [rowBusy, setRowBusy] = useState<number | null>(null)           // catalogue_item_id being confirmed
  const [bulkBusy, setBulkBusy] = useState<null | 'confirm' | 'reject'>(null)
  const [lastResult, setLastResult] = useState<{ applied: number; skipped: number } | null>(null)
  const [discarded, setDiscarded] = useState(false)
  const [editField, setEditField] = useState<{ itemId: number; field: string } | null>(null)  // field being hand-edited
  const [editDraft, setEditDraft] = useState('')
  const [editSaving, setEditSaving] = useState(false)

  useEffect(() => {
    let alive = true
    setLoading(true); setError(null)
    getReparseBatch(batchId)
      .then(b => {
        if (!alive) return
        setBatch(b)
        // Auto-expand a single-item batch; multi-item batches stay collapsed for scannability.
        if ((b.items ?? []).length === 1) setExpanded(new Set([b.items[0].catalogue_item_id]))
      })
      .catch(e => { if (alive) setError(e instanceof Error ? e.message : 'Failed to load batch') })
      .finally(() => { if (alive) setLoading(false) })
    return () => { alive = false }
  }, [batchId])

  const items = useMemo(() => batch?.items ?? [], [batch])
  const busy = rowBusy !== null || bulkBusy !== null
  const [selected, setSelected] = useState<Set<number>>(new Set())   // bulk selection (per-SKU + per-file group)

  // "In inventory only" hides parsed rows not matched to a live SKU (committed=false) — the default view,
  // so a supplier re-parse shows the SKUs you can actually update, not catalogue-only rows. Everything
  // below (list, groups, counts, confirm-all) works off this filtered set.
  const visibleItems = useMemo(() => inInventoryOnly ? items.filter(it => it.committed) : items, [items, inInventoryOnly])
  const hiddenCount = items.length - visibleItems.length

  // Confirm-all applies every actionable (pending, not locally-skipped) VISIBLE item's change ids.
  const actionable = useMemo(() => visibleItems.filter(it => computeItemState(it, rejected) === 'pending'), [visibleItems, rejected])
  const confirmAllIds = useMemo(() => [...new Set(actionable.flatMap(it => it.change_ids))], [actionable])
  const confirmedCount = useMemo(() => visibleItems.filter(it => computeItemState(it, rejected) === 'confirmed').length, [visibleItems, rejected])
  // Group the SKUs by their source upload file (a supplier re-parse spans several catalogue uploads).
  const groupedItems = useMemo(() => {
    const g = new Map<string, ReparseItem[]>()
    for (const it of visibleItems) {
      const key = it.source_file || (it.import_id != null ? `Import #${it.import_id}` : 'Uploaded file')
      const arr = g.get(key)
      if (arr) arr.push(it); else g.set(key, [it])
    }
    return [...g.entries()]
  }, [visibleItems])

  // Deep-link (?item=<catalogue_item_id>) from the hub search → expand + scroll + briefly highlight that SKU.
  const [focusId, setFocusId] = useState<number | null>(null)
  const [highlightId, setHighlightId] = useState<number | null>(null)
  useEffect(() => {
    const p = new URLSearchParams(window.location.search).get('item')
    setFocusId(p ? Number(p) || null : null)
  }, [])
  useEffect(() => {
    if (!batch || !focusId) return
    const target = (batch.items ?? []).find(it => it.catalogue_item_id === focusId)
    if (!target) return
    if (!target.committed) setInInventoryOnly(false)   // reveal a catalogue-only deep-link target (default filter hides it)
    setExpanded(prev => new Set(prev).add(focusId))
    setHighlightId(focusId)
    // brief defer so the scroll runs after any filter-reveal re-render commits the target to the DOM
    const t0 = setTimeout(() => document.getElementById(`reparse-item-${focusId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 60)
    const t = setTimeout(() => setHighlightId(null), 2600)
    return () => { clearTimeout(t0); clearTimeout(t) }
  }, [batch, focusId])

  const runConfirm = useCallback(async (ids: number[], busyKey: number | 'bulk') => {
    if (!batch) return
    const unique = [...new Set(ids)]
    if (unique.length === 0) { toast.info('Nothing to confirm'); return }
    if (busyKey === 'bulk') setBulkBusy('confirm'); else setRowBusy(busyKey)
    try {
      const res = await confirmReparse(batch.id, unique)
      const { applied, skipped, ...updated } = res
      setBatch(updated)
      setLastResult({ applied, skipped })
      toast.success(`Applied ${applied}${skipped ? ` · ${skipped} skipped (went stale)` : ''}`)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Confirm failed')
    } finally {
      setBulkBusy(null); setRowBusy(null)
    }
  }, [batch])

  // ── Inline field edit: correct a Re-parsed value before confirming it ──
  const startEdit = useCallback((itemId: number, field: string, current: string | null) => {
    setEditField({ itemId, field })
    setEditDraft(current === null || current === undefined ? '' : String(current))
  }, [])
  const cancelEdit = useCallback(() => { setEditField(null); setEditDraft('') }, [])
  const saveEdit = useCallback(async () => {
    if (!batch || !editField) return
    setEditSaving(true)
    try {
      const { item, changed_count } = await editReparseField(
        batch.id, editField.itemId, editField.field, editDraft.trim() === '' ? null : editDraft.trim())
      setBatch(prev => prev ? {
        ...prev, changed_count,
        items: (prev.items ?? []).map(it => it.catalogue_item_id === item.catalogue_item_id ? item : it),
      } : prev)
      setEditField(null); setEditDraft('')
      toast.success('Value updated')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Update failed')
    } finally { setEditSaving(false) }
  }, [batch, editField, editDraft])

  const toggleExpand = useCallback((id: number) => {
    setExpanded(prev => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id); else n.add(id)
      return n
    })
  }, [])
  const skipItem = useCallback((id: number) => setRejected(prev => new Set(prev).add(id)), [])
  const undoSkip = useCallback((id: number) => setRejected(prev => { const n = new Set(prev); n.delete(id); return n }), [])

  // ── Bulk selection: pick SKUs (or a whole file group) and act on them together ──
  const toggleSelect = useCallback((id: number) => setSelected(prev => {
    const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id); return n
  }), [])
  const toggleGroup = useCallback((groupItems: ReparseItem[]) => setSelected(prev => {
    const ids = groupItems.map(it => it.catalogue_item_id)
    const all = ids.every(id => prev.has(id))                 // all already selected → this click clears them
    const n = new Set(prev); for (const id of ids) { if (all) n.delete(id); else n.add(id) }
    return n
  }), [])
  const clearSelection = useCallback(() => setSelected(new Set()), [])
  const skipSelected = useCallback(() => {
    setRejected(prev => { const n = new Set(prev); for (const id of selected) n.add(id); return n })
    setSelected(new Set())
  }, [selected])
  const confirmSelected = useCallback(() => {
    const ids = items.filter(it => selected.has(it.catalogue_item_id) && computeItemState(it, rejected) === 'pending')
      .flatMap(it => it.change_ids)
    if (ids.length === 0) { toast.info('No pending changes in the selection'); return }
    runConfirm(ids, 'bulk'); setSelected(new Set())
  }, [items, selected, rejected, runConfirm])

  const rejectAll = useCallback(async () => {
    if (!batch) return
    const ok = await confirmDialog({
      title: 'Discard this re-parse?',
      message: 'This batch is thrown away and any remaining changes are not applied. You can re-run the re-parse at any time.',
      confirmLabel: 'Discard batch', cancelLabel: 'Keep reviewing', danger: true,
    })
    if (!ok) return
    setBulkBusy('reject')
    try {
      await discardReparse(batch.id)
      setDiscarded(true)
      toast.success('Batch discarded — nothing further was applied')
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Discard failed')
    } finally { setBulkBusy(null) }
  }, [batch])

  const renderItem = (item: ReparseItem) => (
    <div
      key={item.catalogue_item_id}
      id={`reparse-item-${item.catalogue_item_id}`}
      style={{ borderRadius: '12px', transition: 'box-shadow .4s',
        boxShadow: highlightId === item.catalogue_item_id ? `0 0 0 3px ${C.indigo}` : 'none' }}
    >
      <ItemBlock
        item={item}
        state={computeItemState(item, rejected)}
        expanded={expanded.has(item.catalogue_item_id)}
        changesOnly={changesOnly}
        busy={busy}
        rowBusy={rowBusy === item.catalogue_item_id}
        onToggle={() => toggleExpand(item.catalogue_item_id)}
        onConfirm={() => runConfirm(item.change_ids, item.catalogue_item_id)}
        onSkip={() => skipItem(item.catalogue_item_id)}
        onUndo={() => undoSkip(item.catalogue_item_id)}
        selected={selected.has(item.catalogue_item_id)}
        onSelect={() => toggleSelect(item.catalogue_item_id)}
        edit={{
          activeField: editField?.itemId === item.catalogue_item_id ? editField.field : null,
          draft: editDraft, saving: editSaving,
          onStart: (field, current) => startEdit(item.catalogue_item_id, field, current),
          onChange: setEditDraft, onSave: saveEdit, onCancel: cancelEdit,
        }}
      />
    </div>
  )

  const backLink = (
    <div style={{ marginBottom: '14px', fontSize: '12.5px', display: 'flex', alignItems: 'center', gap: '7px', flexWrap: 'wrap' }}>
      <Link to={'/catalogues' as never} style={{ color: C.indigoInk, fontWeight: 600, textDecoration: 'none' }}>← Catalogues</Link>
      <span style={{ color: '#C2C8D2' }}>/</span>
      <Link to={'/catalogues/reparse' as never} style={{ color: C.indigoInk, fontWeight: 600, textDecoration: 'none' }}>Re-parse</Link>
      <span style={{ color: '#C2C8D2' }}>/</span>
      <span style={{ color: '#334155' }}>review</span>
    </div>
  )

  return (
    <div style={{ maxWidth: '1040px', margin: '0 auto', padding: '4px 2px 24px' }}>
      {backLink}

      {loading && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: C.sub, fontSize: '13px', padding: '40px 4px' }}>
          <Spinner size={14} color={C.indigo} /> Loading re-parse…
        </div>
      )}

      {!loading && error && (
        <CenteredCard>
          <div style={{ fontSize: '15px', fontWeight: 700, color: C.ink, marginBottom: '6px' }}>Couldn’t load this re-parse</div>
          <p style={{ fontSize: '13px', color: C.sub, margin: '0 0 16px' }}>{error}</p>
          <Link to={'/catalogues' as never} style={{ ...bulkBtn(false), textDecoration: 'none' }}>Back to Catalogues</Link>
        </CenteredCard>
      )}

      {!loading && !error && discarded && (
        <CenteredCard>
          <div style={{ fontSize: '15px', fontWeight: 700, color: C.ink, marginBottom: '6px' }}>Batch discarded</div>
          <p style={{ fontSize: '13px', color: C.sub, margin: '0 0 16px' }}>No further changes were applied. You can re-run the re-parse whenever you like.</p>
          <Link to={'/catalogues' as never} style={{ ...bulkBtn(false), textDecoration: 'none' }}>Back to Catalogues</Link>
        </CenteredCard>
      )}

      {!loading && !error && !discarded && batch && items.length === 0 && (
        <CenteredCard>
          <div style={{ fontSize: '26px', marginBottom: '8px' }}>✓</div>
          <div style={{ fontSize: '15px', fontWeight: 700, color: C.ink, marginBottom: '6px' }}>No changes</div>
          <p style={{ fontSize: '13px', color: C.sub, margin: '0 0 4px' }}>
            Every item in scope already matches parser {batch.parser_version || 'v2'}. Nothing to review.
          </p>
          <p style={{ fontSize: '12px', color: C.faint, margin: '0 0 16px' }}>{batch.item_count} item{batch.item_count === 1 ? '' : 's'} checked.</p>
          <Link to={'/catalogues' as never} style={{ ...bulkBtn(false), textDecoration: 'none' }}>Back to Catalogues</Link>
        </CenteredCard>
      )}

      {!loading && !error && !discarded && batch && items.length > 0 && (
        <>
          {/* Batch bar */}
          <div style={{
            background: C.panel, border: `1px solid ${C.line}`, borderRadius: '12px', padding: '12px 15px',
            display: 'flex', alignItems: 'center', gap: '11px', flexWrap: 'wrap', marginBottom: '12px',
          }}>
            <span style={chip(C.indigoBg, C.indigoInk, C.indigoLine)}>{SCOPE_KIND[batch.scope_type]}</span>
            <span style={{ fontSize: '14px', fontWeight: 700, color: C.ink }}>{scopeTitle(batch)}</span>
            <span style={chip(C.monoBg, C.sub, C.line)}>parser {batch.parser_version || 'v1'}{batch.mode ? ` · ${batch.mode}` : ''}</span>
            <span style={{ flex: 1 }} />
            <span style={{ fontSize: '12px', color: C.sub }}>
              <b style={{ color: C.ink }}>{batch.changed_count}</b> of {batch.item_count} changed
              {confirmedCount > 0 && <> · <b style={{ color: C.ok }}>{confirmedCount}</b> confirmed</>}
              {inInventoryOnly && hiddenCount > 0 && <> · <span style={{ color: C.faint }}>{hiddenCount} not-in-inventory hidden</span></>}
            </span>
            <button onClick={() => setInInventoryOnly(v => !v)} style={tglBtn} aria-pressed={inInventoryOnly}
              title="Hide parsed rows that aren’t matched to a live SKU">
              <span style={swTrack(inInventoryOnly)}><span style={swKnob(inInventoryOnly)} /></span>
              In inventory only
            </button>
            <button onClick={() => setChangesOnly(v => !v)} style={tglBtn} aria-pressed={changesOnly}>
              <span style={swTrack(changesOnly)}><span style={swKnob(changesOnly)} /></span>
              Changes only
            </button>
            <button style={{ ...bulkBtn(false), opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={rejectAll}>
              {bulkBusy === 'reject' ? <><Spinner size={11} /> Discarding…</> : 'Discard re-parse'}
            </button>
            <button
              style={{ ...bulkBtn(true), opacity: busy || confirmAllIds.length === 0 ? 0.6 : 1, cursor: confirmAllIds.length === 0 ? 'not-allowed' : 'pointer' }}
              disabled={busy || confirmAllIds.length === 0}
              onClick={() => runConfirm(confirmAllIds, 'bulk')}
            >
              {bulkBusy === 'confirm' ? <><Spinner size={11} color="#fff" /> Confirming…</> : `Confirm all (${actionable.length})`}
            </button>
          </div>

          <div style={{ fontSize: '11.5px', color: C.sub, margin: '-4px 2px 12px', display: 'flex', alignItems: 'center', gap: '6px', flexWrap: 'wrap' }}>
            <span>Expand a SKU to see its fields, then</span>
            <span style={{ ...editPencil, cursor: 'default' }}>✎ Edit</span>
            <span>any value to correct it before confirming.</span>
          </div>

          {lastResult && (
            <div style={{ fontSize: '11.5px', color: C.faint, margin: '-4px 2px 10px' }}>
              Applied {lastResult.applied}{lastResult.skipped ? ` · ${lastResult.skipped} skipped (went stale)` : ''}.
            </div>
          )}

          {/* Bulk-selection action bar — tick SKUs (or a whole file group) and act on them together */}
          {selected.size > 0 && (
            <div style={{
              display: 'flex', alignItems: 'center', gap: '11px', flexWrap: 'wrap', marginBottom: '12px',
              background: C.indigoBg, border: `1px solid ${C.indigoLine}`, borderRadius: '10px', padding: '9px 14px',
            }}>
              <span style={{ fontSize: '12.5px', fontWeight: 700, color: C.indigoInk }}>{selected.size} SKU{selected.size === 1 ? '' : 's'} selected</span>
              <span style={{ flex: 1 }} />
              <button style={{ ...bulkBtn(false), opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={skipSelected}>Skip selected</button>
              <button style={{ ...bulkBtn(true), opacity: busy ? 0.6 : 1 }} disabled={busy} onClick={confirmSelected}>Confirm selected</button>
              <button onClick={clearSelection} style={{ fontSize: '12px', color: C.sub, background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit', textDecoration: 'underline', textUnderlineOffset: '2px' }}>Clear</button>
            </div>
          )}

          {/* Item list — grouped by the source upload file (helpful for a multi-upload supplier re-parse) */}
          {visibleItems.length === 0 ? (
            <div style={{
              background: C.panel, border: `1px solid ${C.line}`, borderRadius: '12px',
              padding: '26px 20px', textAlign: 'center',
            }}>
              <div style={{ fontSize: '13.5px', fontWeight: 700, color: C.ink, marginBottom: '5px' }}>Nothing in inventory to review</div>
              <p style={{ fontSize: '12.5px', color: C.sub, margin: '0 0 14px' }}>
                All {items.length} parsed row{items.length === 1 ? '' : 's'} in this re-parse {items.length === 1 ? 'isn’t' : 'aren’t'} matched to a live SKU.
              </p>
              <button style={bulkBtn(false)} onClick={() => setInInventoryOnly(false)}>Show catalogue-only rows</button>
            </div>
          ) : groupedItems.length > 1 ? (
            groupedItems.map(([file, groupItems]) => (
              <div key={file}>
                <div style={{
                  display: 'flex', alignItems: 'center', gap: '8px', margin: '18px 2px 9px',
                  paddingBottom: '7px', borderBottom: `1px solid ${C.line}`,
                }}>
                  <input type="checkbox"
                    ref={el => { if (el) { const s = groupItems.filter(it => selected.has(it.catalogue_item_id)).length; el.indeterminate = s > 0 && s < groupItems.length } }}
                    checked={groupItems.every(it => selected.has(it.catalogue_item_id))}
                    onChange={() => toggleGroup(groupItems)} style={cbStyle}
                    aria-label={`Select all ${groupItems.length} SKUs in ${file}`} />
                  <span style={{ fontSize: '13px' }}>📄</span>
                  <span style={{ fontSize: '12.5px', fontWeight: 700, color: C.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={file}>{file}</span>
                  <span style={{ fontSize: '10.5px', fontWeight: 700, color: C.faint, background: C.monoBg, borderRadius: '99px', padding: '2px 8px', whiteSpace: 'nowrap' }}>
                    {groupItems.length} SKU{groupItems.length === 1 ? '' : 's'}
                  </span>
                </div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                  {groupItems.map(renderItem)}
                </div>
              </div>
            ))
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {visibleItems.map(renderItem)}
            </div>
          )}

          {/* Footer reassurance */}
          <div style={{
            marginTop: '18px', fontSize: '11.5px', color: C.faint, background: C.panel,
            border: `1px solid ${C.line}`, borderRadius: '11px', padding: '12px 15px', lineHeight: 1.6,
          }}>
            Only confirmed changes write to live cost · a stale row is skipped, never overwritten · every captured field is re-derived from the retained catalogue text.
          </div>
        </>
      )}
    </div>
  )
}

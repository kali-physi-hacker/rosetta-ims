// Exception room — one candidate at a time: verbatim evidence beside the
// contract-normalized view, a variant picker with cost + margin sanity, and
// family-pattern corrections that collapse repeated fixes into one confirmed
// batch. Decisions are append-only; corrections create revisions.
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react'
import { Spinner } from '@/components/Spinner'
import { toast } from '@/lib/toast'
import { C } from '@/lib/tokens'
import {
  fetchSummary, fetchDetail, searchVariants, latest, groupOf, isPulled, isDecided, sampleIds,
  decideCandidate, correctVariantMatch, fanOut, fmtDelta, marginPct,
  PULL_THRESHOLD_PCT, type SummaryItem, type VariantHit,
} from '@/lib/review'

type RoomSearch = { g?: string; c?: string }

export const Route = createFileRoute('/_authed/catalogues/review/$runId/room')({
  component: ReviewRoomPage,
  validateSearch: (search: Record<string, unknown>): RoomSearch => ({
    g: typeof search.g === 'string' ? search.g : undefined,
    c: typeof search.c === 'string' ? search.c : undefined,
  }),
})

const MONO = 'ui-monospace, "SF Mono", Menlo, monospace'
const pill = (bg: string, color: string): CSSProperties =>
  ({ fontSize: 10.5, fontWeight: 700, borderRadius: 999, padding: '2px 9px', background: bg, color, whiteSpace: 'nowrap' })
const btn: CSSProperties = { border: `1px solid ${C.knobOff}`, borderRadius: 6, padding: '6px 11px', background: C.panel, cursor: 'pointer', fontSize: 11.5, fontWeight: 600, color: C.ink }
const btnPrimary: CSSProperties = { ...btn, background: C.indigoStrong, borderColor: C.indigoStrong, color: '#fff' }
const cardTitle: CSSProperties = { margin: 0, padding: '8px 12px', borderBottom: `1px solid ${C.line}`, fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.muted, display: 'flex', justifyContent: 'space-between' }

const TABS: { id: string; label: (counts: Record<string, number>) => string }[] = [
  { id: 'ambiguous', label: c => `Ambiguous ${c.ambiguous ?? 0}` },
  { id: 'unmatched', label: c => `No offering ${c.unmatched ?? 0}` },
  { id: 'sample', label: c => `Price review ${c.sample ?? 0}` },
]

function ReviewRoomPage() {
  const { runId } = Route.useParams()
  const { g, c } = Route.useSearch()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const group = g ?? 'ambiguous'

  const summary = useQuery({ queryKey: ['review-summary', runId], queryFn: () => fetchSummary(runId) })
  const items = useMemo(() => latest(summary.data?.items ?? []), [summary.data])
  const sample = useMemo(
    () => sampleIds(runId, items.filter(i => groupOf(i) === 'matched' && !isPulled(i) && i.blocking_issues === 0)),
    [runId, items],
  )

  // Queue per tab. "Price review" is everything a human must look at inside
  // the matched group: the deterministic sample plus material price moves.
  const queues: Record<string, SummaryItem[]> = useMemo(() => ({
    ambiguous: items.filter(i => groupOf(i) === 'ambiguous'),
    unmatched: items.filter(i => groupOf(i) === 'unmatched'),
    sample: items.filter(i => groupOf(i) === 'matched' && (sample.has(i.mastering_candidate_id) || isPulled(i))),
    matched: items.filter(i => groupOf(i) === 'matched'),
  }), [items, sample])
  const queue = queues[group] ?? queues.ambiguous
  const pendingCounts = Object.fromEntries(
    Object.entries(queues).map(([key, list]) => [key, list.filter(i => !isDecided(i)).length]),
  )

  const current = queue.find(i => i.mastering_candidate_id === c) ?? queue.find(i => !isDecided(i)) ?? queue[0]
  const currentId = current?.mastering_candidate_id
  const position = current ? queue.indexOf(current) : -1
  const nextPending = queue.find((i, index) => index > position && !isDecided(i)) ?? queue.find(i => !isDecided(i) && i !== current)

  const detail = useQuery({
    queryKey: ['review-detail', runId, currentId],
    queryFn: () => fetchDetail(runId, currentId!),
    enabled: !!currentId,
  })
  // Zero-wait queue flow: the next pending candidate's detail loads while this
  // one is being decided.
  useEffect(() => {
    if (nextPending) {
      queryClient.prefetchQuery({
        queryKey: ['review-detail', runId, nextPending.mastering_candidate_id],
        queryFn: () => fetchDetail(runId, nextPending.mastering_candidate_id),
      })
    }
  }, [runId, nextPending?.mastering_candidate_id, queryClient])

  const select = (candidate?: SummaryItem, nextGroup?: string) =>
    navigate({
      to: '/catalogues/review/$runId/room',
      params: { runId },
      search: { g: nextGroup ?? group, c: candidate?.mastering_candidate_id },
      replace: true,
    })

  // Session pace: mean decision interval over this sitting, shown after 3.
  const paceRef = useRef<number[]>([])
  const notePace = () => paceRef.current.push(Date.now())
  const paceLeft = useMemo(() => {
    const stamps = paceRef.current
    const remaining = queue.filter(i => !isDecided(i)).length
    if (stamps.length < 3 || remaining === 0) return null
    const interval = (stamps[stamps.length - 1] - stamps[0]) / (stamps.length - 1)
    return `~${Math.max(1, Math.round((interval * remaining) / 60000))} min left at current pace`
  }, [queue, detail.dataUpdatedAt])

  async function decide(status: SummaryItem['review_status'], reason: string) {
    if (!current) return
    try {
      await decideCandidate(runId, current.mastering_candidate_id, status, reason)
      notePace()
      // Optimistic queue advance; the refetch reconciles.
      queryClient.setQueryData(['review-summary', runId], (old: any) => old && ({
        ...old,
        items: old.items.map((i: SummaryItem) =>
          i.mastering_candidate_id === current.mastering_candidate_id ? { ...i, review_status: status } : i),
      }))
      queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
      toast.success(status === 'APPROVED'
        ? `Approved — ${current.supplier_sku}. Cost lands on the product at the Commit step.`
        : `${status === 'REJECTED' ? 'Rejected' : 'Sent for clarification'} — ${current.supplier_sku}`)
      select(nextPending)
    } catch (e: any) {
      toast.error(String(e?.message ?? e))
    }
  }

  async function correct(variant: VariantHit, reason: string) {
    if (!current) return
    try {
      const result = await correctVariantMatch(
        runId, current.mastering_candidate_id, reason,
        { sku_code: variant.sku_code, name: variant.name }, current.name,
      )
      notePace()
      await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
      const revision = (result as any)?.output_ids?.[0]
      toast.success(`Corrected to ${variant.sku_code} — new revision`)
      navigate({
        to: '/catalogues/review/$runId/room', params: { runId },
        search: { g: group, c: typeof revision === 'string' ? revision : undefined }, replace: true,
      })
    } catch (e: any) {
      toast.error(String(e?.message ?? e))
    }
  }

  // Keyboard: J/K move, A approves (single candidate only — bulk is never a
  // keystroke). Skipped while typing in an input.
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement
      if (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA') return
      if (event.key === 'j' || event.key === 'J') select(queue[Math.min(position + 1, queue.length - 1)])
      if (event.key === 'k' || event.key === 'K') select(queue[Math.max(position - 1, 0)])
      if ((event.key === 'a' || event.key === 'A') && current && canApprove(current)) {
        decide('APPROVED', 'Reviewed in the exception room')
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (summary.isLoading) return <div style={{ padding: 24 }}><Spinner /></div>
  if (summary.isError) return <div style={{ padding: 24, color: C.bad, fontSize: 13 }}>{String((summary.error as Error)?.message)}</div>

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '290px 1fr 300px', minHeight: 'calc(100vh - 56px)' }}>
      {/* Queue */}
      <div style={{ background: C.panel, borderRight: `1px solid ${C.line}`, overflowY: 'auto' }}>
        <div style={{ padding: '10px 12px', borderBottom: `1px solid ${C.line}` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <Link to="/catalogues/review/$runId" params={{ runId }} style={{ fontSize: 12, color: C.muted, textDecoration: 'none' }}>← Board</Link>
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>J/K move · A approve</span>
          </div>
          <div style={{ display: 'flex', gap: 4, marginTop: 8 }}>
            {TABS.map(tab => (
              <button
                key={tab.id}
                onClick={() => select(undefined, tab.id)}
                style={{
                  fontSize: 10.5, fontWeight: 700, borderRadius: 6, padding: '3px 8px', cursor: 'pointer', border: 'none',
                  background: group === tab.id ? C.indigoBg : C.monoBg, color: group === tab.id ? C.indigoInk : C.muted,
                }}
              >
                {tab.label(pendingCounts)}
              </button>
            ))}
          </div>
        </div>
        {queue.map(item => {
          const selected = item.mastering_candidate_id === currentId
          return (
            <div
              key={item.mastering_candidate_id}
              onClick={() => select(item)}
              style={{
                padding: '9px 12px', borderBottom: `1px solid ${C.monoBg}`, cursor: 'pointer',
                background: selected ? C.indigoBg : undefined,
                borderLeft: selected ? `3px solid ${C.indigoStrong}` : '3px solid transparent',
                opacity: isDecided(item) ? 0.55 : 1,
                contentVisibility: 'auto',
              } as CSSProperties}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, alignItems: 'center' }}>
                <span style={{ fontWeight: 650, fontSize: 12, color: C.ink, overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis' }}>{item.name ?? item.supplier_sku}</span>
                {isDecided(item)
                  ? <span style={pill(C.monoBg, C.sub)}>{item.review_status.replace('_', ' ').toLowerCase()}</span>
                  : isPulled(item)
                    ? <span style={pill(C.redBg, C.redInk)}>Δ {item.price_delta_pct}%</span>
                    : <span style={pill(groupOf(item) === 'ambiguous' ? C.redBg : groupOf(item) === 'unmatched' ? C.warnBg : C.greenBg, groupOf(item) === 'ambiguous' ? C.redInk : groupOf(item) === 'unmatched' ? C.amberInk : C.green)}>{groupOf(item) === 'ambiguous' ? 'AMBIGUOUS' : groupOf(item) === 'unmatched' ? 'NO OFFERING' : 'MATCHED'}</span>}
              </div>
              <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, marginTop: 2 }}>
                {item.supplier_sku} · {item.cost_amount != null ? `$${item.cost_amount.toFixed(2)}` : '—'}{item.page ? ` · p${item.page}` : ''}
              </div>
            </div>
          )
        })}
        {queue.length === 0 && <div style={{ padding: 20, color: C.muted, fontSize: 12.5 }}>This queue is empty.</div>}
      </div>

      {/* Workspace */}
      {current ? (
        <Workspace
          runId={runId}
          item={current}
          queue={queue}
          position={position}
          paceLeft={paceLeft}
          detail={detail}
          clusterRows={items.filter(i =>
            i.mastering_candidate_id !== currentId
            && groupOf(i) === 'unmatched' && !isDecided(i)
            && i.family_key != null && i.family_key === current.family_key,
          )}
          onDecide={decide}
          onCorrect={correct}
        />
      ) : (
        <div style={{ padding: 40, color: C.muted, fontSize: 13 }}>Nothing to review in this queue.</div>
      )}
    </div>
  )
}

function canApprove(item: SummaryItem): boolean {
  return (item.variant_state === 'PROPOSED_MATCH' || item.variant_state === 'CONFIRMED_MATCH')
    && item.blocking_issues === 0 && !isDecided(item)
}

function Workspace(props: {
  runId: string
  item: SummaryItem
  queue: SummaryItem[]
  position: number
  paceLeft: string | null
  detail: ReturnType<typeof useQuery<any>>
  clusterRows: SummaryItem[]
  onDecide: (status: SummaryItem['review_status'], reason: string) => void
  onCorrect: (variant: VariantHit, reason: string) => void
}) {
  const { runId, item, queue, position, paceLeft, detail, clusterRows } = props
  const [reason, setReason] = useState('Reviewed against supplier evidence')
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<VariantHit[]>([])
  const [picked, setPicked] = useState<VariantHit | null>(null)
  const [clusterOpen, setClusterOpen] = useState(false)

  // Reset per candidate; seed the picker from the supplier SKU, which is how
  // canonical SKUs are discovered in practice (RIMS-…-5351 contains 5351).
  useEffect(() => {
    setPicked(null)
    setQuery(item.supplier_sku ?? '')
    setClusterOpen(false)
  }, [item.mastering_candidate_id])

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) { setHits([]); return }
    const timer = setTimeout(() => {
      searchVariants(runId, q).then(r => setHits(r.results)).catch(() => setHits([]))
    }, 250)
    return () => clearTimeout(timer)
  }, [runId, query])

  const evidence = detail.data?.evidence?.[0]
  const candidate = detail.data?.candidate
  const packaging = candidate?.packaging_resolution?.packaging
  const stateLabel = groupOf(item) === 'ambiguous'
    ? { text: 'AMBIGUOUS · pick the right offering', bg: C.redBg, color: C.redInk }
    : groupOf(item) === 'unmatched'
      ? { text: 'NO OFFERING · match to a product variant', bg: C.warnBg, color: C.amberInk }
      : { text: `MATCHED → ${item.canonical_sku}`, bg: C.greenBg, color: C.green }

  return (
    <>
      <div style={{ padding: '14px 18px', minWidth: 0, overflowY: 'auto' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 10, flexWrap: 'wrap' }}>
          <div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <b style={{ fontSize: 14.5, color: C.ink }}>{item.name ?? item.supplier_sku}</b>
              <span style={pill(stateLabel.bg, stateLabel.color)}>{stateLabel.text}</span>
            </div>
            <div style={{ fontFamily: MONO, fontSize: 11, color: C.muted, marginTop: 2 }}>
              candidate {item.mastering_candidate_id.slice(0, 8)}{item.page ? ` · page ${item.page}` : ''}
            </div>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontFamily: MONO, fontSize: 11, color: C.muted }}>
            <span>{position + 1} / {queue.length}</span>
            <span style={{ width: 120, height: 5, borderRadius: 3, background: C.line, overflow: 'hidden' }}>
              <i style={{ display: 'block', height: '100%', width: `${queue.length ? ((position + 1) / queue.length) * 100 : 0}%`, background: C.indigoStrong }} />
            </span>
            {paceLeft && <span>{paceLeft}</span>}
          </div>
        </div>

        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginTop: 12 }}>
          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            <h5 style={cardTitle}>Source evidence <span style={{ fontFamily: MONO }}>{evidence?.page ? `page ${evidence.page} · verbatim` : 'verbatim'}</span></h5>
            {detail.isLoading ? <div style={{ padding: 16 }}><Spinner /></div> : (
              <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', fontSize: 12 }}>
                {(evidence?.cells ?? []).map((cell: any, index: number) => (
                  <FragmentRow key={index} label={cell.column_name ?? `col ${index + 1}`} value={cell.value == null ? '' : String(cell.value)} mono={index === 0 || /price|code|size/i.test(cell.column_name ?? '')} />
                ))}
                {evidence && !evidence.cells?.length && (
                  <div style={{ gridColumn: '1 / -1', padding: '8px 12px', fontFamily: MONO, fontSize: 11.5, color: C.sub, whiteSpace: 'pre-wrap' }}>{evidence.raw_text}</div>
                )}
              </div>
            )}
          </div>
          <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8 }}>
            <h5 style={cardTitle}>Contract normalized <span style={{ fontFamily: MONO }}>{candidate?.lineage?.supplier_source_contract_id ?? ''}</span></h5>
            <div style={{ display: 'grid', gridTemplateColumns: '140px 1fr', fontSize: 12 }}>
              <FragmentRow label="Supplier SKU" value={item.supplier_sku ?? '—'} mono />
              <FragmentRow label="Name" value={item.name ?? '—'} />
              <FragmentRow label="Cost" value={`${item.cost_amount?.toFixed(2) ?? '—'} ${item.cost_currency ?? ''}${item.cost_basis ? ` / ${item.cost_basis}` : ''}`} mono />
              {packaging?.content_amount != null && (
                <FragmentRow label="Content" value={`${packaging.content_amount} ${packaging.content_uom?.code ?? ''}`} mono />
              )}
              {item.barcode && <FragmentRow label="Barcode" value={item.barcode} mono />}
              <FragmentRow
                label="Supplier offering"
                value={groupOf(item) === 'matched' ? `${item.canonical_sku} · ${item.variant_name ?? ''}` : groupOf(item) === 'ambiguous' ? 'several plausible offerings — pick below' : 'none — variant creation is closed by design'}
                highlight={groupOf(item) !== 'matched'}
              />
            </div>
          </div>
        </div>

        {clusterRows.length > 0 && (
          <div style={{ marginTop: 10, background: C.indigoBg, border: `1px solid ${C.indigoLine}`, borderRadius: 8, padding: '8px 12px', fontSize: 11.5, color: C.indigoInk }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
              <span style={pill(C.indigoBg, C.indigoInk)}>FAMILY PATTERN</span>
              <span><b>{clusterRows.length} more rows share “{item.family_key}”</b> and have no offering. Match this one, then apply the same pattern — you confirm the list first.</span>
              <button style={{ ...btn, marginLeft: 'auto' }} onClick={() => setClusterOpen(open => !open)}>{clusterOpen ? 'Hide' : 'Preview'} cluster</button>
            </div>
            {clusterOpen && <ClusterPanel runId={runId} rows={clusterRows} />}
          </div>
        )}
      </div>

      {/* Decision rail */}
      <div style={{ background: C.panel, borderLeft: `1px solid ${C.line}`, padding: 12, display: 'flex', flexDirection: 'column', gap: 8, overflowY: 'auto' }}>
        <h5 style={{ margin: '0 0 2px', fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.muted }}>Match to a product variant</h5>
        <input
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search sku, name, brand…"
          style={{ border: `1px solid ${C.indigoStrong}`, borderRadius: 8, padding: '7px 10px', fontSize: 11.5 }}
          aria-label="Search product variants"
        />
        {hits.map(hit => {
          const delta = hit.offering_cost != null && item.cost_amount != null && hit.offering_cost !== 0
            ? Math.round(((item.cost_amount - hit.offering_cost) / hit.offering_cost) * 1000) / 10 : null
          const margin = marginPct(item.cost_amount, hit.selling_price)
          const suspicious = (delta != null && Math.abs(delta) > PULL_THRESHOLD_PCT) || (margin != null && margin < 10)
          const selected = picked?.sku_code === hit.sku_code
          return (
            <div
              key={hit.sku_code}
              onClick={() => setPicked(hit)}
              style={{
                border: `1px solid ${selected ? C.indigoStrong : C.line}`, background: selected ? C.indigoBg : C.panel,
                borderRadius: 8, padding: '8px 10px', fontSize: 11.5, cursor: 'pointer',
              }}
            >
              <b style={{ display: 'block', fontSize: 12, color: C.ink }}>{hit.sku_code} · {hit.name}</b>
              <span style={{ color: C.muted }}>{[hit.brand, hit.species, hit.status !== 'ACTIVE' ? hit.status : null].filter(Boolean).join(' · ')}</span>
              <div style={{ fontFamily: MONO, fontSize: 10.5, marginTop: 2, color: delta != null && Math.abs(delta) > PULL_THRESHOLD_PCT ? C.amber : C.green }}>
                {hit.offering_cost != null && item.cost_amount != null
                  ? `offering cost ${hit.offering_cost.toFixed(2)} → ${item.cost_amount.toFixed(2)} · ${fmtDelta(delta)} ${delta != null && Math.abs(delta) > PULL_THRESHOLD_PCT ? '⚠' : '✓'}`
                  : 'no current cost for this supplier'}
              </div>
              {hit.selling_price != null && (
                <div style={{ fontFamily: MONO, fontSize: 10.5, color: margin != null && margin < 10 ? C.bad : C.green }}>
                  sells at {hit.selling_price.toFixed(2)} ({hit.selling_channel}) → margin {margin ?? '—'}% {margin != null && margin < 10 ? '⚠ implausible' : '✓'}
                </div>
              )}
              {suspicious && selected && <div style={{ fontSize: 10.5, color: C.amberInk, marginTop: 2 }}>Both numbers say look twice before correcting to this.</div>}
            </div>
          )
        })}
        {query.trim().length >= 2 && hits.length === 0 && (
          <div style={{ fontSize: 11.5, color: C.muted }}>No variants match “{query.trim()}”.</div>
        )}

        <h5 style={{ margin: '6px 0 2px', fontSize: 10.5, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.muted }}>Reason</h5>
        <textarea
          value={reason}
          onChange={e => setReason(e.target.value)}
          rows={2}
          style={{ border: `1px solid ${C.line}`, borderRadius: 8, padding: '7px 10px', fontSize: 11.5, resize: 'vertical', fontFamily: 'inherit' }}
          aria-label="Decision reason"
        />
        <button
          style={{ ...btnPrimary, opacity: picked ? 1 : 0.45 }}
          disabled={!picked}
          onClick={() => picked && props.onCorrect(picked, reason)}
        >
          Correct → new revision
        </button>
        <button
          style={{ ...btn, opacity: canApprove(item) ? 1 : 0.45 }}
          disabled={!canApprove(item)}
          onClick={() => props.onDecide('APPROVED', reason)}
          title={canApprove(item) ? 'Approve (final)' : item.blocking_issues > 0 ? 'Blocked by validation issues' : 'Locked until matched to a variant'}
        >
          {canApprove(item) ? 'Approve (final)' : 'Approve (locked until matched)'}
        </button>
        {/* Clarify (NEEDS_CLARIFICATION) is parked until a clarification inbox exists —
            the backend transition stays available via the API. */}
        <button style={{ ...btn, color: C.bad, borderColor: '#FCA5A5' }} disabled={isDecided(item)} onClick={() => props.onDecide('REJECTED', reason)}>Reject</button>
        <div style={{ fontSize: 10.5, color: C.green, background: C.okBg, border: `1px solid ${C.okLine}`, borderRadius: 6, padding: '6px 8px' }}>
          On apply this becomes a permanent supplier-offering link — future runs auto-match SKU {item.supplier_sku}.
        </div>
        <div style={{ fontSize: 10.5, color: C.amberInk, background: C.amberBg, borderRadius: 6, padding: '6px 8px' }}>
          Decisions are append-only — approval is <b>final</b>. Corrections stay safe: each is a new revision; only the latest takes a decision.
        </div>
      </div>
    </>
  )
}

function FragmentRow({ label, value, mono, highlight }: { label: string; value: string; mono?: boolean; highlight?: boolean }) {
  const cell: CSSProperties = { padding: '6px 12px', borderBottom: `1px solid ${C.monoBg}`, background: highlight ? C.amberBg : undefined }
  return (
    <>
      <div style={{ ...cell, color: C.muted }}>{label}</div>
      <div style={{ ...cell, color: highlight ? C.amberInk : C.ink, fontWeight: highlight ? 650 : 400, fontFamily: mono ? MONO : undefined, fontSize: mono ? 11.5 : 12 }}>{value}</div>
    </>
  )
}

// Family-pattern batch: per sibling row, propose the top variant hit for its
// supplier SKU with the same sanity math; the reviewer confirms the list and
// corrections fire as individual immutable revisions.
function ClusterPanel({ runId, rows }: { runId: string; rows: SummaryItem[] }) {
  const queryClient = useQueryClient()
  const [proposals, setProposals] = useState<Record<string, VariantHit | null> | null>(null)
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [progress, setProgress] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setProposals(null)
    Promise.all(rows.map(async row => {
      if (!row.supplier_sku) return [row.mastering_candidate_id, null] as const
      const found = await searchVariants(runId, row.supplier_sku, 1).catch(() => ({ results: [] as VariantHit[] }))
      return [row.mastering_candidate_id, found.results[0] ?? null] as const
    })).then(entries => {
      if (cancelled) return
      setProposals(Object.fromEntries(entries))
      setChecked(Object.fromEntries(entries.map(([id, hit]) => [id, hit != null])))
    })
    return () => { cancelled = true }
  }, [runId, rows.map(r => r.mastering_candidate_id).join(',')])

  if (!proposals) return <div style={{ padding: 10 }}><Spinner /></div>
  const selected = rows.filter(row => checked[row.mastering_candidate_id] && proposals[row.mastering_candidate_id])

  async function applyAll() {
    setProgress(`0 / ${selected.length}`)
    const { failures } = await fanOut(
      selected,
      row => correctVariantMatch(
        runId, row.mastering_candidate_id, 'Family-pattern correction (reviewer-confirmed list)',
        { sku_code: proposals![row.mastering_candidate_id]!.sku_code, name: proposals![row.mastering_candidate_id]!.name },
        row.name,
      ),
      (done, total) => setProgress(`${done} / ${total}`),
    )
    setProgress(null)
    await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
    if (failures.length) toast.error(`${failures.length} corrections failed — first: ${failures[0].error}`)
    else toast.success(`Corrected ${selected.length} rows in the family`)
  }

  return (
    <div style={{ marginTop: 8 }}>
      {rows.map(row => {
        const hit = proposals[row.mastering_candidate_id]
        const delta = hit?.offering_cost != null && row.cost_amount != null && hit.offering_cost !== 0
          ? Math.round(((row.cost_amount - hit.offering_cost) / hit.offering_cost) * 1000) / 10 : null
        return (
          <label key={row.mastering_candidate_id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '5px 0', fontSize: 11.5, color: C.ink, cursor: hit ? 'pointer' : 'default' }}>
            <input
              type="checkbox"
              checked={!!checked[row.mastering_candidate_id]}
              disabled={!hit}
              onChange={e => setChecked(prev => ({ ...prev, [row.mastering_candidate_id]: e.target.checked }))}
            />
            <span style={{ fontFamily: MONO, fontSize: 11 }}>{row.supplier_sku}</span>
            <span style={{ overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis', maxWidth: 260 }}>{row.name}</span>
            {hit ? (
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: delta != null && Math.abs(delta) > PULL_THRESHOLD_PCT ? C.amber : C.green }}>
                → {hit.sku_code}{delta != null ? ` · ${fmtDelta(delta)}` : ''}
              </span>
            ) : (
              <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.muted }}>no confident match — handle individually</span>
            )}
          </label>
        )
      })}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6 }}>
        {progress
          ? <span style={{ fontFamily: MONO, fontSize: 11 }}>correcting {progress}…</span>
          : <button style={{ ...btnPrimary, opacity: selected.length ? 1 : 0.45 }} disabled={!selected.length} onClick={applyAll}>
              Correct {selected.length} confirmed rows
            </button>}
        <span style={{ fontSize: 10.5, color: C.indigoInk }}>Each correction is its own immutable revision — nothing is approved yet.</span>
      </div>
    </div>
  )
}

// Run board — the run's one-glance truth and its bulk lane. Exceptions go to
// the room; material price moves are pulled out of bulk; bulk approval unlocks
// only after the deterministic sample is reviewed (the sampling gate).
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type CSSProperties } from 'react'
import { Spinner } from '@/components/Spinner'
import { toast } from '@/lib/toast'
import { C } from '@/lib/tokens'
import {
  fetchSummary, latest, groupOf, isPulled, isBulkEligible, isDecided, sampleIds,
  approveCandidate, resolveRunIssue, fanOut, fmtDelta, reviewApi,
  SAMPLE_SIZE, PULL_THRESHOLD_PCT, type SummaryItem,
} from '@/lib/review'

export const Route = createFileRoute('/_authed/catalogues/review/$runId/')({ component: RunBoardPage })

const MONO = 'ui-monospace, "SF Mono", Menlo, monospace'
const pill = (bg: string, color: string): CSSProperties =>
  ({ fontSize: 10.5, fontWeight: 700, borderRadius: 999, padding: '2px 9px', background: bg, color, whiteSpace: 'nowrap' })
const btn: CSSProperties = { border: `1px solid ${C.knobOff}`, borderRadius: 6, padding: '6px 11px', background: C.panel, cursor: 'pointer', fontSize: 11.5, fontWeight: 600, color: C.ink }
const btnPrimary: CSSProperties = { ...btn, background: C.indigoStrong, borderColor: C.indigoStrong, color: '#fff' }

function RunBoardPage() {
  const { runId } = Route.useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [bulkTyped, setBulkTyped] = useState('')
  const [bulkProgress, setBulkProgress] = useState<string | null>(null)

  const status = useQuery({
    queryKey: ['review-run-status', runId],
    queryFn: () => reviewApi<{ status: string; contract_id: string | null; items_extracted: number | null; completed_at: string | null }>(`/catalogues/ingestions/${runId}`),
  })
  const summary = useQuery({ queryKey: ['review-summary', runId], queryFn: () => fetchSummary(runId) })

  const items = useMemo(() => latest(summary.data?.items ?? []), [summary.data])
  const ambiguous = items.filter(i => groupOf(i) === 'ambiguous')
  const unmatched = items.filter(i => groupOf(i) === 'unmatched')
  const matched = items.filter(i => groupOf(i) === 'matched')
  const pulled = matched.filter(isPulled)
  const eligible = matched.filter(isBulkEligible)
  const sample = useMemo(() => sampleIds(runId, matched.filter(i => !isPulled(i) && i.blocking_issues === 0)), [runId, items])
  const sampled = items.filter(i => sample.has(i.mastering_candidate_id))
  const sampledDone = sampled.filter(isDecided).length
  const gateOpen = sampled.length > 0 && sampledDone === sampled.length
  const bulkPool = eligible.filter(i => !sample.has(i.mastering_candidate_id))
  const openCount = items.filter(i => i.review_status === 'PENDING_REVIEW').length
  const approvedCount = items.filter(i => i.review_status === 'APPROVED' || i.review_status === 'APPROVED_WITH_OVERRIDE').length
  const publishedCount = items.filter(i => i.published).length
  const familyClusters = new Set(unmatched.map(i => i.family_key ?? `sku:${i.supplier_sku}`)).size
  const openRunIssues = (summary.data?.run_issues ?? []).filter(i => i.resolution_status === 'OPEN')

  const deltaChips = {
    unchanged: matched.filter(i => i.price_delta_pct != null && i.price_delta_pct === 0).length,
    small: matched.filter(i => i.price_delta_pct != null && i.price_delta_pct !== 0 && Math.abs(i.price_delta_pct) <= PULL_THRESHOLD_PCT).length,
    fresh: matched.filter(i => i.price_delta_pct == null).length,
  }

  async function onBulkApprove() {
    setBulkTyped('')
    setBulkProgress('0 / ' + bulkPool.length)
    const { failures } = await fanOut(
      bulkPool,
      item => approveCandidate(runId, item.mastering_candidate_id, 'Bulk-approved after sampling gate'),
      (done, total) => setBulkProgress(`${done} / ${total}`),
    )
    setBulkProgress(null)
    await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
    if (failures.length) toast.error(`${failures.length} approvals failed — first: ${failures[0].error}`)
    else toast.success(`Approved ${bulkPool.length} offering matches`)
  }

  async function onResolveIssue(issueId: string) {
    try {
      await resolveRunIssue(runId, issueId, 'Reviewed from the run board')
      await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
      toast.success('Run issue resolved')
    } catch (e: any) {
      toast.error(String(e?.message ?? e))
    }
  }

  const toRoom = (group: string, candidate?: string) =>
    navigate({ to: '/catalogues/review/$runId/room', params: { runId }, search: { g: group, c: candidate } as never })

  if (summary.isLoading || status.isLoading) return <div style={{ padding: 24 }}><Spinner /></div>
  if (summary.isError) return <div style={{ padding: 24, color: C.bad, fontSize: 13 }}>{String((summary.error as Error)?.message)}</div>

  return (
    <div style={{ padding: 24, maxWidth: 1200 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <Link to="/catalogues/review" style={{ fontSize: 12, color: C.muted, textDecoration: 'none' }}>← Runs</Link>
        <h1 style={{ fontSize: 17, fontWeight: 800, color: C.ink, margin: 0 }}>{status.data?.contract_id ?? 'Run'}</h1>
        <span style={pill('#DBEAFE', '#1E40AF')}>{status.data?.status}</span>
        <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>{runId.slice(0, 8)}</span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          {approvedCount > 0 && (
            <Link to="/catalogues/review/$runId/commit" params={{ runId }} style={{ ...btn, textDecoration: 'none', display: 'inline-block' }}>
              Commit · {approvedCount} approved
            </Link>
          )}
          <button style={btnPrimary} onClick={() => toRoom(ambiguous.length ? 'ambiguous' : 'unmatched')}>
            Start review · {openCount}
          </button>
        </span>
      </div>

      {/* Gate strip: the run's story in one row. */}
      <div style={{ display: 'flex', marginTop: 14, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, overflow: 'hidden' }}>
        {[
          { label: 'Raw', value: 'document ✓', color: C.muted },
          { label: 'Staging', value: `${status.data?.items_extracted ?? items.length} rows ✓`, color: '#2563EB' },
          { label: 'Intermediate', value: openCount ? `${openCount} open` : 'complete ✓', color: C.amber },
          { label: 'Serving', value: publishedCount ? `${publishedCount} published` : 'gate closed', color: C.ok },
        ].map((gate, index) => (
          <div key={gate.label} style={{ flex: 1, display: 'flex', gap: 10, alignItems: 'center', padding: '8px 12px', borderLeft: index ? `1px solid ${C.monoBg}` : 'none' }}>
            <span style={{ width: 4, alignSelf: 'stretch', borderRadius: 2, background: gate.color }} />
            <div>
              <div style={{ fontSize: 10, letterSpacing: '0.08em', textTransform: 'uppercase', color: C.muted }}>{gate.label}</div>
              <div style={{ fontSize: 13, fontWeight: 800, color: C.ink, fontVariantNumeric: 'tabular-nums' }}>{gate.value}</div>
            </div>
          </div>
        ))}
      </div>

      {openRunIssues.map(issue => (
        <div key={issue.validation_issue_id} style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10, background: C.amberBg, border: `1px solid ${C.amberLine}`, borderRadius: 8, padding: '7px 12px', fontSize: 11.5, color: C.amberInk }}>
          <span style={pill(C.warnBg, C.amberInk)}>RUN ISSUE</span>
          <span><b style={{ fontFamily: MONO, fontSize: 11 }}>{issue.issue_code}</b> — {issue.message}</span>
          <button style={{ ...btn, marginLeft: 'auto' }} onClick={() => onResolveIssue(issue.validation_issue_id)}>Resolve</button>
        </div>
      ))}

      <GroupTable
        title={<><span style={pill(C.redBg, C.redInk)}>AMBIGUOUS · {ambiguous.length}</span><span style={{ color: C.muted, fontWeight: 400 }}>several plausible supplier offerings — a human picks</span></>}
        items={ambiguous}
        onOpen={item => toRoom('ambiguous', item.mastering_candidate_id)}
        action={<button style={btnPrimary} onClick={() => toRoom('ambiguous')} disabled={!ambiguous.length}>Review in room →</button>}
        detail={() => <span style={{ fontFamily: MONO, fontSize: 11, color: C.redInk }}>offering collision</span>}
      />

      <GroupTable
        title={<><span style={pill(C.warnBg, C.amberInk)}>NO OFFERING YET · {unmatched.length}</span><span style={{ color: C.muted, fontWeight: 400 }}>match each to an existing product variant — creation stays closed</span></>}
        items={unmatched}
        onOpen={item => toRoom('unmatched', item.mastering_candidate_id)}
        action={<span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          <span style={{ fontFamily: MONO, fontSize: 11, color: C.muted }}>{familyClusters} family clusters</span>
          <button style={btnPrimary} onClick={() => toRoom('unmatched')} disabled={!unmatched.length}>Review in room →</button>
        </span>}
        detail={item => <span style={{ fontFamily: MONO, fontSize: 11, color: C.amberInk }}>{item.family_key ? `family: ${item.family_key}` : 'no mapping'}</span>}
      />

      <GroupTable
        title={<>
          <span style={pill(C.greenBg, C.green)}>OFFERING MATCHED · {matched.length}</span>
          <span style={{ marginLeft: 'auto', display: 'flex', gap: 6 }}>
            <span style={pill(C.monoBg, C.sub)}>{deltaChips.unchanged} price unchanged</span>
            {deltaChips.fresh > 0 && <span style={pill(C.monoBg, C.sub)}>{deltaChips.fresh} first price</span>}
            {deltaChips.small > 0 && <span style={pill(C.warnBg, C.amberInk)}>{deltaChips.small} moved ≤{PULL_THRESHOLD_PCT}%</span>}
            {pulled.length > 0 && <span style={pill(C.redBg, C.redInk)}>{pulled.length} &gt;{PULL_THRESHOLD_PCT}% — pulled to review</span>}
          </span>
        </>}
        items={matched}
        onOpen={item => toRoom('matched', item.mastering_candidate_id)}
        detail={item => {
          const delta = fmtDelta(item.price_delta_pct)
          const material = isPulled(item)
          return (
            <span style={{ fontFamily: MONO, fontSize: 11, color: material ? C.bad : C.green, fontWeight: material ? 700 : 400 }}>
              → {item.canonical_sku}{delta ? ` · ${delta}` : ''}{material ? ' — review required' : ''}
              {isDecided(item) ? ` · ${item.review_status.toLowerCase()}` : ''}
              {sample.has(item.mastering_candidate_id) && !isDecided(item) ? ' · in sample' : ''}
            </span>
          )
        }}
        footer={matched.length > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '8px 12px', background: C.indigoBg, borderTop: `1px solid ${C.indigoLine}`, fontSize: 11.5, color: C.indigoInk }}>
            <b>Sampling gate:</b> review {Math.min(SAMPLE_SIZE, sampled.length)} random before bulk approval unlocks
            <span style={{ flex: 1, maxWidth: 200, height: 6, borderRadius: 3, background: C.indigoLine, overflow: 'hidden' }}>
              <i style={{ display: 'block', height: '100%', width: `${sampled.length ? (sampledDone / sampled.length) * 100 : 0}%`, background: C.indigoStrong }} />
            </span>
            <span style={{ fontFamily: MONO, fontSize: 11 }}>{sampledDone} / {sampled.length} sampled</span>
            {!gateOpen && sampledDone < sampled.length && (
              <button style={btn} onClick={() => toRoom('sample')}>Review sample →</button>
            )}
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
              {bulkProgress ? (
                <span style={{ fontFamily: MONO, fontSize: 11 }}>approving {bulkProgress}…</span>
              ) : gateOpen && bulkPool.length > 0 ? (
                <>
                  <span style={{ fontFamily: MONO, fontSize: 10.5, color: C.muted }}>type {bulkPool.length} to confirm →</span>
                  <input
                    value={bulkTyped}
                    onChange={e => setBulkTyped(e.target.value)}
                    style={{ width: 56, border: `1px solid ${C.indigoLine}`, borderRadius: 6, padding: '4px 8px', fontFamily: MONO, fontSize: 11 }}
                    aria-label="Type the approval count to confirm"
                  />
                  <button style={{ ...btnPrimary, opacity: bulkTyped === String(bulkPool.length) ? 1 : 0.45 }} disabled={bulkTyped !== String(bulkPool.length)} onClick={onBulkApprove}>
                    Approve remaining {bulkPool.length} (final)
                  </button>
                </>
              ) : (
                <button style={{ ...btn, opacity: 0.45 }} disabled>
                  {bulkPool.length === 0 ? 'Nothing left to bulk-approve' : `Approve remaining ${bulkPool.length} (locked)`}
                </button>
              )}
            </span>
          </div>
        )}
      />
    </div>
  )
}

function GroupTable(props: {
  title: React.ReactNode
  items: SummaryItem[]
  detail: (item: SummaryItem) => React.ReactNode
  onOpen: (item: SummaryItem) => void
  action?: React.ReactNode
  footer?: React.ReactNode
}) {
  return (
    <div style={{ marginTop: 12, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', borderBottom: `1px solid ${C.line}`, background: C.wash, fontSize: 11.5 }}>
        {props.title}
        {props.action && <span style={{ marginLeft: 'auto' }}>{props.action}</span>}
      </div>
      <div style={{ maxHeight: 300, overflowY: 'auto' }}>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <tbody>
            {props.items.map(item => (
              <tr
                key={item.mastering_candidate_id}
                onClick={() => props.onOpen(item)}
                style={{ borderTop: `1px solid ${C.monoBg}`, cursor: 'pointer', contentVisibility: 'auto' } as CSSProperties}
              >
                <td style={{ padding: '7px 12px', fontFamily: MONO, fontSize: 11, color: C.sub, whiteSpace: 'nowrap' }}>{item.supplier_sku ?? '—'}</td>
                <td style={{ padding: '7px 12px', color: C.ink, maxWidth: 420, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{item.name ?? '—'}</td>
                <td style={{ padding: '7px 12px', fontFamily: MONO, fontSize: 11, color: C.sub, whiteSpace: 'nowrap', fontVariantNumeric: 'tabular-nums' }}>
                  {item.cost_amount != null ? `${item.cost_amount.toFixed(2)} ${item.cost_currency ?? ''}` : '—'}
                </td>
                <td style={{ padding: '7px 12px', whiteSpace: 'nowrap' }}>{props.detail(item)}</td>
              </tr>
            ))}
            {props.items.length === 0 && (
              <tr><td style={{ padding: 16, textAlign: 'center', color: C.muted, fontSize: 12.5 }}>Nothing in this group.</td></tr>
            )}
          </tbody>
        </table>
      </div>
      {props.footer}
    </div>
  )
}

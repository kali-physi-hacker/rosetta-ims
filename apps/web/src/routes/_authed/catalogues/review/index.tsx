// Catalogue review — a queue, not a log. Runs grouped by supplier contract,
// latest first; the newest run per contract shows its live review progress,
// older runs collapse to their receipts, failures explain themselves.
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Spinner } from '@/components/Spinner'
import { toast } from '@/lib/toast'
import { DESK_CSS } from '@/lib/deskCss'
import { fetchSummary, latest, isStaged, isApproved, isDecided, reviewApi, failureInfo, retryRun, type RunStatus } from '@/lib/review'

export const Route = createFileRoute('/_authed/catalogues/review/')({ component: ReviewRunsPage })

type RunRow = RunStatus

const FAILED = new Set(['failed'])
const WORKING = new Set(['queued', 'processing'])

function fmtWhen(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' }) + ', ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
}

function ReviewRunsPage() {
  const runs = useQuery({
    queryKey: ['review-runs'],
    queryFn: () => reviewApi<RunRow[]>('/catalogues/ingestions/run_ids'),
  })

  const groups = useMemo(() => {
    const sorted = [...(runs.data ?? [])].sort((a, b) => (b.submitted_at ?? '').localeCompare(a.submitted_at ?? ''))
    const map = new Map<string, RunRow[]>()
    for (const run of sorted) {
      const key = run.contract_id ?? 'unknown contract'
      map.set(key, [...(map.get(key) ?? []), run])
    }
    // groups whose latest run is newest come first
    return [...map.entries()].sort((a, b) => (b[1][0].submitted_at ?? '').localeCompare(a[1][0].submitted_at ?? ''))
  }, [runs.data])

  return (
    <div className="rdesk" style={{ padding: '18px 24px 40px', maxWidth: 980, margin: '0 auto' }}>
      <style>{DESK_CSS}</style>
      <h1>Catalogue review</h1>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 4 }}>
        Each run opens its desk — decide, stage, publish, receipt, all in one place.
      </div>

      {runs.isLoading && <div style={{ marginTop: 16 }}><Spinner /></div>}
      {runs.isError && <div style={{ marginTop: 16, color: 'var(--red)', fontSize: 13 }}>{String((runs.error as Error)?.message ?? runs.error)}</div>}

      {groups.map(([contract, rows]) => (
        <ContractGroup key={contract} contract={contract} rows={rows} />
      ))}
      {runs.data && groups.length === 0 && (
        <div className="panel" style={{ marginTop: 14, padding: 22, textAlign: 'center', color: 'var(--faint)', fontSize: 13 }}>
          No runs yet — submit a catalogue from the Catalogues page.
        </div>
      )}
    </div>
  )
}

function ContractGroup({ contract, rows }: { contract: string; rows: RunRow[] }) {
  const usable = rows.filter(r => !FAILED.has(r.status))
  const failures = rows.filter(r => FAILED.has(r.status))
  const [showFailures, setShowFailures] = useState(false)
  const newest = usable[0]

  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="laneh">
        <span className="ln">{contract}</span>
        <span className="lc">{rows.length} run{rows.length === 1 ? '' : 's'}</span>
        {failures.length > 0 && (
          <span className="lnk" style={{ marginLeft: 'auto', fontSize: 11 }} onClick={() => setShowFailures(open => !open)}>
            {showFailures ? 'hide' : 'show'} {failures.length} failed
          </span>
        )}
      </div>

      {usable.map((run, index) => (
        <div key={run.ingestion_run_id} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '9px 14px', borderTop: index ? '1px solid var(--line2)' : 'none', opacity: index === 0 ? 1 : 0.65, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11.5, color: 'var(--faint)', width: 110, flex: 'none' }}>{fmtWhen(run.submitted_at)}</span>
          <span style={{ fontSize: 12, color: 'var(--ink2)' }}>{run.items_extracted != null ? `${run.items_extracted} rows` : WORKING.has(run.status) ? 'processing…' : '—'}</span>
          {index === 0 && !WORKING.has(run.status) ? <RunProgress runId={run.ingestion_run_id} /> : <span style={{ flex: 1 }} />}
          {index === 0 ? (
            <Link className="btn pri sm" to="/catalogues/review/$runId" params={{ runId: run.ingestion_run_id }}>Open desk →</Link>
          ) : (
            <Link className="lnk" style={{ fontSize: 11.5 }} to="/catalogues/review/$runId/commit" params={{ runId: run.ingestion_run_id }}>receipt →</Link>
          )}
        </div>
      ))}
      {usable.length === 0 && (
        <div style={{ padding: '12px 14px', fontSize: 12, color: 'var(--faint)' }}>Every submission of this catalogue failed so far.</div>
      )}

      {showFailures && failures.map(run => <FailedRunRow key={run.ingestion_run_id} run={run} />)}
    </div>
  )
}

// Live progress chips for the newest run of a contract — one summary fetch.
function RunProgress({ runId }: { runId: string }) {
  const summary = useQuery({ queryKey: ['review-summary', runId], queryFn: () => fetchSummary(runId), staleTime: 30_000 })
  if (summary.isLoading) return <span style={{ flex: 1, fontSize: 11, color: 'var(--faint)' }}>…</span>
  if (summary.isError || !summary.data) return <span style={{ flex: 1 }} />
  const items = latest(summary.data.items)
  const needsYou = items.filter(i => !isDecided(i)).length
  const staged = items.filter(isStaged).length
  const live = items.filter(i => i.published).length
  const done = items.length > 0 && needsYou === 0 && staged === 0
  return (
    <span style={{ flex: 1, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
      {needsYou > 0 && <span className="bdg warn">{needsYou} to decide</span>}
      {staged > 0 && <span className="bdg acc">{staged} staged</span>}
      {live > 0 && <span className="bdg ok"><span className="st" />{live} live</span>}
      {done && live === 0 && <span className="bdg neu">reviewed · nothing published</span>}
      {items.length > 0 && items.every(i => isApproved(i) && i.published) && <span className="bdg ok">complete ✓</span>}
    </span>
  )
}


function FailedRunRow({ run }: { run: RunRow }) {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [busy, setBusy] = useState(false)
  const failure = failureInfo(run.error_summary)
  const superseded = run.superseded_by_run

  async function onRetry() {
    setBusy(true)
    try {
      const result = await retryRun(run.ingestion_run_id)
      toast.success('Retry queued — same file, new run')
      queryClient.invalidateQueries({ queryKey: ['review-runs'] })
      navigate({ to: '/catalogues/review/$runId', params: { runId: result.ingestion_run_id } })
    } catch (e: any) {
      toast.error(String(e?.message ?? e))
    } finally { setBusy(false) }
  }

  if (superseded) {
    return (
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '7px 14px', borderTop: '1px solid var(--line2)', opacity: 0.55 }}>
        <span style={{ fontSize: 11.5, color: 'var(--faint)', width: 110, flex: 'none' }}>{fmtWhen(run.submitted_at)}</span>
        <span style={{ fontSize: 11.5, color: 'var(--faint)' }}>↻ superseded — retried as a newer run</span>
        <Link className="lnk" style={{ fontSize: 11, marginLeft: 'auto' }} to="/catalogues/review/$runId" params={{ runId: superseded }}>open the retry →</Link>
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 14px', borderTop: '1px solid var(--line2)', flexWrap: 'wrap' }}>
      <span style={{ fontSize: 11.5, color: 'var(--faint)', width: 110, flex: 'none' }}>{fmtWhen(run.submitted_at)}</span>
      <span className="bdg bad" title={failure?.code}>failed — {failure ? failure.sentence.replace(/\.$/, '') : 'no details recorded'}</span>
      {failure && failure.attempts > 1 && <span style={{ fontSize: 10.5, color: 'var(--faint)' }}>{failure.attempts} attempts</span>}
      <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
        {failure?.retryable
          ? <button className="btn sm" disabled={busy} onClick={onRetry}>{busy ? 'Queuing…' : 'Retry'}</button>
          : <span style={{ fontSize: 10.5, color: 'var(--faint)' }}>fix the file &amp; re-upload</span>}
        <Link className="lnk" style={{ fontSize: 11 }} to="/catalogues/review/$runId" params={{ runId: run.ingestion_run_id }}>details</Link>
      </span>
    </div>
  )
}

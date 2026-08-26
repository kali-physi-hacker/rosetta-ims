// Catalogue review — a queue, not a log. Runs grouped by SUPPLIER, latest
// first; the newest run per supplier shows its live review progress, older
// runs collapse to their receipts, failures explain themselves. One group per
// supplier, not per contract: re-reads under a sibling or merged format are
// the same catalogue's story, and retired layout ids must not pin dead
// buckets in the list forever.
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Spinner } from '@/components/Spinner'
import { toast } from '@/lib/toast'
import { DESK_CSS } from '@/lib/deskCss'
import { fetchSummary, fetchDeadLetters, latest, isStaged, isApproved, isDecided, reviewApi, failureInfo, retryRun, type RunStatus } from '@/lib/review'

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
      const key = run.supplier_id != null ? `supplier:${run.supplier_id}` : (run.contract_id ?? 'unknown supplier')
      map.set(key, [...(map.get(key) ?? []), run])
    }
    // groups whose latest run is newest come first
    return [...map.entries()].sort((a, b) => (b[1][0].submitted_at ?? '').localeCompare(a[1][0].submitted_at ?? ''))
  }, [runs.data])

  return (
    <div className="rdesk" style={{ padding: '18px 24px 40px', maxWidth: 980, margin: '0 auto' }}>
      <style>{DESK_CSS}</style>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ margin: 0 }}>Catalogue review</h1>
        <Link className="btn sm" style={{ marginLeft: 'auto' }} to="/catalogues/review/held">
          Held across suppliers →
        </Link>
      </div>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 4 }}>
        Each run opens its desk — decide, stage, publish, receipt, all in one place.
      </div>

      {runs.isLoading && <div style={{ marginTop: 16 }}><Spinner /></div>}
      {runs.isError && <div style={{ marginTop: 16, color: 'var(--red)', fontSize: 13 }}>{String((runs.error as Error)?.message ?? runs.error)}</div>}

      {groups.map(([key, rows]) => (
        <SupplierGroup key={key} label={groupLabel(rows)} rows={rows} />
      ))}
      {runs.data && groups.length === 0 && (
        <div className="panel" style={{ marginTop: 14, padding: 22, textAlign: 'center', color: 'var(--faint)', fontSize: 13 }}>
          No runs yet — submit a catalogue from the Catalogues page.
        </div>
      )}
    </div>
  )
}

/** The supplier's family name, read off its contracts: "kangaroo_pet_nutrition",
 * never "kangaroo_pet_nutrition.unit_price_list.v1". */
function groupLabel(rows: RunRow[]): string {
  const contract = rows.find(r => r.contract_id)?.contract_id
  return contract ? contract.split('.')[0] : 'unknown supplier'
}

function SupplierGroup({ label, rows }: { label: string; rows: RunRow[] }) {
  const usable = rows.filter(r => !FAILED.has(r.status))
  const failures = rows.filter(r => FAILED.has(r.status))
  const [showFailures, setShowFailures] = useState(false)
  // The desk slot goes to the newest run with something TO REVIEW — a
  // re-drive whose rows all failed again has rows but zero candidates, and
  // letting it shadow the run holding the pending decisions made every desk
  // look empty. Falls back to the newest run when nothing has candidates.
  const primary = usable.find(r => (r.review_candidates ?? 0) > 0) ?? usable[0]

  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="laneh">
        <span className="ln">{label}</span>
        <span className="lc">{rows.length} run{rows.length === 1 ? '' : 's'}</span>
        {failures.length > 0 && (
          <span className="lnk" style={{ marginLeft: 'auto', fontSize: 11 }} onClick={() => setShowFailures(open => !open)}>
            {showFailures ? 'hide' : 'show'} {failures.length} failed
          </span>
        )}
      </div>

      {usable.map((run, index) => {
        const isPrimary = run.ingestion_run_id === primary?.ingestion_run_id
        const candidates = run.review_candidates ?? 0
        return (
          <div key={run.ingestion_run_id} style={{ display: 'flex', gap: 12, alignItems: 'center', padding: '9px 14px', borderTop: index ? '1px solid var(--line2)' : 'none', opacity: isPrimary ? 1 : 0.65, flexWrap: 'wrap' }}>
            <span style={{ fontSize: 11.5, color: 'var(--faint)', width: 110, flex: 'none' }}>{fmtWhen(run.submitted_at)}</span>
            {/* The primary line speaks for the DESK, whose universe is the
                folded family — RunProgress renders that rows figure so it
                always agrees with "to decide". Non-primary lines report their
                own run's product_rows (never items_extracted: BOs count
                products, and the raw figure includes page banners). */}
            {!isPrimary && (
              <span style={{ fontSize: 12, color: 'var(--ink2)' }}>{(run.product_rows ?? run.items_extracted) != null ? `${run.product_rows ?? run.items_extracted} rows` : WORKING.has(run.status) ? 'processing…' : '—'}</span>
            )}
            {isPrimary && WORKING.has(run.status) && (
              <span style={{ fontSize: 12, color: 'var(--ink2)' }}>processing…</span>
            )}
            {isPrimary && !WORKING.has(run.status) ? <RunProgress runId={run.ingestion_run_id} /> : <span style={{ flex: 1 }} />}
            {isPrimary ? (
              // The ONE desk: document-scoped, so it already holds every
              // pending SKU of the family — including these older runs'.
              <Link className="btn pri sm" to="/catalogues/review/$runId" params={{ runId: run.ingestion_run_id }}>Open desk →</Link>
            ) : (
              // Older runs are history: a summary line and the receipt, never
              // a second desk (user directive 2026-08-25).
              <span style={{ fontSize: 11.5, color: 'var(--faint)' }}>
                {candidates > 0 && <>{candidates} of its rows in the desk above · </>}
                <Link className="lnk" to="/catalogues/review/$runId/commit" params={{ runId: run.ingestion_run_id }}>receipt →</Link>
              </span>
            )}
          </div>
        )
      })}
      {usable.length === 0 && (
        <div style={{ padding: '12px 14px', fontSize: 12, color: 'var(--faint)' }}>Every submission of this catalogue failed so far.</div>
      )}

      {showFailures && failures.map(run => <FailedRunRow key={run.ingestion_run_id} run={run} />)}
    </div>
  )
}

// Live progress chips for the newest run of a contract — one summary fetch,
// plus the held-rows count so unreadable rows are visible from the list.
function RunProgress({ runId }: { runId: string }) {
  const summary = useQuery({ queryKey: ['review-summary', runId], queryFn: () => fetchSummary(runId), staleTime: 30_000 })
  const held = useQuery({ queryKey: ['dead-letters', runId], queryFn: () => fetchDeadLetters(runId), staleTime: 60_000 })
  const heldCount = held.data?.count ?? 0
  if (summary.isLoading) return <span style={{ flex: 1, fontSize: 11, color: 'var(--faint)' }}>…</span>
  if (summary.isError || !summary.data) return <span style={{ flex: 1 }} />
  const items = latest(summary.data.items)
  const needsYou = items.filter(i => !isDecided(i)).length
  const staged = items.filter(isStaged).length
  const live = items.filter(i => i.published).length
  const done = items.length > 0 && needsYou === 0 && staged === 0
  return (
    <span style={{ flex: 1, display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center' }}>
      {/* The desk's OWN universe (folded candidates + held), so the rows
          figure and the decision counts always agree. */}
      <span style={{ fontSize: 12, color: 'var(--ink2)' }}>{items.length + heldCount} rows</span>
      {needsYou > 0 && <span className="bdg warn">{needsYou} to decide</span>}
      {heldCount > 0 && (
        <Link className="bdg neu" style={{ textDecoration: 'none', cursor: 'pointer' }}
          to="/catalogues/review/$runId/held" params={{ runId }}
          title="Rows printed in the catalogue that could not be read into reviewable items — open to see why and re-run them">
          {heldCount} couldn&apos;t be read →
        </Link>
      )}
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

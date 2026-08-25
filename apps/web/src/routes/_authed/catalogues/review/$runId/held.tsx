// The held-rows lane — rows this run's catalogue printed that the machine
// could not read into reviewable items. Every one is listed with WHY it is
// held (in the guidance's words), its verbatim evidence, an inline
// fix-the-evidence panel, and a re-run action that re-drives exactly the held
// rows from stored evidence — a fresh attempt, no re-scan, no provider spend.
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'
import { Spinner } from '@/components/Spinner'
import { DESK_CSS } from '@/lib/deskCss'
import { FixEvidencePanel } from '@/components/FixEvidencePanel'
import { toast } from '@/lib/toast'
import {
  fetchDeadLetters, fetchObservationEvidence, fetchRunStatus, retriggerHeldRows,
  type DeadLetterEntry,
} from '@/lib/review'

export const Route = createFileRoute('/_authed/catalogues/review/$runId/held')({ component: HeldRowsPage })

const IN_FLIGHT = new Set(['queued', 'running'])

function HeldRowsPage() {
  const { runId } = Route.useParams()
  const queryClient = useQueryClient()
  const held = useQuery({
    queryKey: ['dead-letters', runId],
    queryFn: () => fetchDeadLetters(runId),
    // A fired re-run only moves the queue when its child run COMPLETES —
    // poll while one is in flight so cleared rows visibly leave, instead of
    // the re-run looking like it did nothing.
    refetchInterval: query =>
      (query.state.data?.retriggers ?? []).some(r => IN_FLIGHT.has(r.status)) ? 4000 : false,
  })
  const status = useQuery({ queryKey: ['run-status', runId], queryFn: () => fetchRunStatus(runId) })
  const [rerunning, setRerunning] = useState(false)
  const retriggers = held.data?.retriggers ?? []
  const inFlight = retriggers.find(r => IN_FLIGHT.has(r.status)) ?? null
  const latest = retriggers.length ? retriggers[retriggers.length - 1] : null
  const latestFailed = !inFlight && latest?.status === 'failed' ? latest : null
  // This run IS a fresh attempt for another run's queue — re-running it is
  // refused by design, so the desk points at the owning run instead of
  // offering buttons that always error.
  const retriggerOf = held.data?.retrigger_of ?? null
  const canRerun = !retriggerOf

  // Grouped by the row's primary hold reason; groups ordered by what one fix
  // would actually clear, so the most valuable fix reads first.
  const groups = useMemo(() => {
    const entries = held.data?.dead_letters ?? []
    const byCode = new Map<string, DeadLetterEntry[]>()
    for (const entry of entries) {
      const code = entry.issue_codes[0] ?? 'UNEXPLAINED'
      byCode.set(code, [...(byCode.get(code) ?? []), entry])
    }
    const cleared = new Map((held.data?.by_issue_code ?? []).map(t => [t.issue_code, t.rows_cleared_if_fixed]))
    return [...byCode.entries()].sort((a, b) => (cleared.get(b[0]) ?? 0) - (cleared.get(a[0]) ?? 0))
  }, [held.data])

  async function rerun(scope: { issue_code?: string; catalogue_item_ids?: string[] }, label: string) {
    setRerunning(true)
    try {
      const result = await retriggerHeldRows(runId, scope)
      toast.success(`Re-running ${result.rows_selected} ${result.rows_selected === 1 ? 'row' : 'rows'} — ${label}. Watch this page: rows leave the list when the attempt finishes.`)
      // The queue follows re-runs only once the child run COMPLETES. The
      // refetch below picks up the queued attempt, and the query then polls
      // until it finishes — cleared rows leave, survivors count one more try.
      await queryClient.invalidateQueries({ queryKey: ['dead-letters', runId] })
      await queryClient.invalidateQueries({ queryKey: ['review-runs'] })
    } catch (e: any) {
      toast.error(String(e?.message ?? e))
    } finally { setRerunning(false) }
  }

  const count = held.data?.count ?? 0

  return (
    <div className="rdesk" style={{ padding: '18px 22px', maxWidth: 1080, margin: '0 auto' }}>
      <style>{DESK_CSS}</style>

      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 17, fontWeight: 750, color: 'var(--ink)', margin: 0 }}>
          Rows we couldn&apos;t read{held.data ? ` — ${count}` : ''}
        </h1>
        <Link className="lnk" to="/catalogues/review/$runId" params={{ runId }}>← back to the desk</Link>
        {status.data?.source_filename && (
          <span style={{ fontSize: 11.5, color: 'var(--faint)' }}>{status.data.source_filename}</span>
        )}
        {count > 0 && canRerun && (
          <button className="btn pri sm" style={{ marginLeft: 'auto' }} disabled={rerunning || !!inFlight}
            onClick={() => rerun({}, 'every held row gets a fresh attempt')}>
            {inFlight ? 'Attempt in progress…' : rerunning ? 'Re-running…' : `Re-run all ${count} held rows`}
          </button>
        )}
      </div>

      {(held.data?.rereads ?? []).filter(read => read.candidates > 0).map(read => (
        <div key={read.ingestion_run_id} className="panel" style={{ padding: '10px 14px', margin: '10px 0 0', fontSize: 12.5, color: 'var(--accent-ink)', background: 'var(--accent-soft)' }}>
          This run&apos;s pages were re-read as <b>{read.format_name}</b> — {read.candidates}{' '}
          {read.candidates === 1 ? 'row' : 'rows'} became reviewable there, and any of them that were held
          here have left this list.{' '}
          <Link className="lnk" to="/catalogues/review/$runId" params={{ runId: read.ingestion_run_id }}>
            Review them on that run →
          </Link>
        </div>
      ))}

      {retriggerOf && (
        <div className="panel" style={{ padding: '10px 14px', margin: '10px 0 0', fontSize: 12.5, color: 'var(--accent-ink)', background: 'var(--accent-soft)' }}>
          This run is itself a fresh attempt for another run&apos;s held rows — what you see here is this
          attempt&apos;s own history. The live queue, evidence fixing, and Re-run all live on{' '}
          <Link className="lnk" to="/catalogues/review/$runId/held" params={{ runId: retriggerOf }}>
            the original run&apos;s held page →
          </Link>
        </div>
      )}
      <p style={{ fontSize: 12.5, color: 'var(--muted)', margin: '6px 0 16px', maxWidth: 720 }}>
        These rows are printed in the catalogue but could not be read into items you can review —
        usually because a price or layout needs a person&apos;s eyes. Fix the evidence where the scan
        misread the page, then re-run: the fixed rows get a fresh attempt from the stored pages,
        without re-scanning or extra cost.
      </p>

      {inFlight && (
        <div className="panel" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 12.5, color: 'var(--accent-ink)', background: 'var(--accent-soft)', display: 'flex', gap: 8, alignItems: 'center' }}>
          <Spinner />
          <span>
            A fresh attempt is running — attempt {inFlight.attempt}, {inFlight.observations}{' '}
            {inFlight.observations === 1 ? 'row' : 'rows'} re-read from stored evidence. This page follows it:
            cleared rows leave the list when it finishes, survivors come back counting one more try.
          </span>
        </div>
      )}
      {latestFailed && (
        <div className="panel" style={{ padding: '10px 14px', marginBottom: 14, fontSize: 12.5, color: '#C0362C' }}>
          The last attempt (attempt {latestFailed.attempt}) failed before processing its rows — the held
          rows are unchanged. Re-run to try again; if it keeps failing, the run itself needs an engineer&apos;s eyes.
        </div>
      )}

      {held.isLoading && <div style={{ padding: 24 }}><Spinner /></div>}
      {held.isError && <div style={{ padding: 24, color: '#C0362C', fontSize: 13 }}>{String((held.error as Error)?.message)}</div>}
      {held.data && count === 0 && (
        <div className="panel" style={{ padding: 20, fontSize: 13, color: 'var(--muted)' }}>
          Nothing is held — every printed row became something reviewable.
        </div>
      )}

      {groups.map(([code, entries]) => (
        <HeldGroup key={code} runId={runId} code={code} entries={entries}
          clearedIfFixed={(held.data?.by_issue_code ?? []).find(t => t.issue_code === code)?.rows_cleared_if_fixed ?? null}
          rerunning={rerunning || !!inFlight} canRerun={canRerun}
          onRerun={() => rerun({ issue_code: code }, 'the rows this fix clears get a fresh attempt')} />
      ))}
    </div>
  )
}

function HeldGroup({ runId, code, entries, clearedIfFixed, rerunning, canRerun, onRerun }: {
  runId: string; code: string; entries: DeadLetterEntry[]
  clearedIfFixed: number | null; rerunning: boolean; canRerun: boolean; onRerun: () => void
}) {
  // The guidance is written for the reviewer, so it leads; the code is the
  // pipeline's name for the condition and stays small for support handoffs.
  const guidance = entries.find(e => e.review_guidance)?.review_guidance
  return (
    <div className="panel" style={{ marginBottom: 14, background: 'var(--card)' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line2)', display: 'flex', gap: 10, alignItems: 'baseline', flexWrap: 'wrap' }}>
        <b style={{ fontSize: 13, color: 'var(--ink)' }}>
          {entries.length} {entries.length === 1 ? 'row' : 'rows'} held
        </b>
        {clearedIfFixed != null && clearedIfFixed !== entries.length && (
          <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>
            one fix frees {clearedIfFixed} of them — the rest are also held for another reason
          </span>
        )}
        <span className="mono" style={{ fontSize: 10, color: 'var(--faint)' }}>{code}</span>
        {canRerun && (
          <button className="btn sm" style={{ marginLeft: 'auto' }} disabled={rerunning} onClick={onRerun}>
            Re-run these rows
          </button>
        )}
      </div>
      {guidance && (
        <div style={{ padding: '8px 14px', fontSize: 12, color: 'var(--accent-ink)', background: 'var(--accent-soft)', borderBottom: '1px solid var(--line2)' }}>
          {guidance}
        </div>
      )}
      {entries.map(entry => <HeldRow key={entry.catalogue_item_id} runId={runId} entry={entry} />)}
    </div>
  )
}

function HeldRow({ runId, entry }: { runId: string; entry: DeadLetterEntry }) {
  const [open, setOpen] = useState(false)
  return (
    <div style={{ borderTop: '1px solid var(--line2)' }}>
      <button onClick={() => setOpen(o => !o)}
        style={{ display: 'flex', gap: 10, alignItems: 'baseline', width: '100%', textAlign: 'left', padding: '8px 14px', background: 'none', border: 'none', cursor: 'pointer', fontFamily: 'inherit' }}>
        <span className="mono" style={{ fontSize: 11.5, color: 'var(--ink)', flex: 'none' }}>{entry.supplier_sku ?? '—'}</span>
        <span style={{ fontSize: 12.5, color: 'var(--ink2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {entry.product_name ?? 'unnamed row'}
        </span>
        {entry.attempts > 1 && (
          <span className="bdg neu" style={{ flex: 'none' }}>tried {entry.attempts}×</span>
        )}
        <span style={{ marginLeft: 'auto', fontSize: 11, color: 'var(--faint)', flex: 'none' }}>{open ? 'close' : 'look at it'}</span>
      </button>
      {open && <HeldRowDetail runId={runId} entry={entry} />}
    </div>
  )
}

function HeldRowDetail({ runId, entry }: { runId: string; entry: DeadLetterEntry }) {
  const queryClient = useQueryClient()
  const [fixing, setFixing] = useState(false)
  const observationId = entry.raw_observation_id ?? null
  const evidence = useQuery({
    queryKey: ['held-evidence', runId, observationId],
    queryFn: () => fetchObservationEvidence(runId, observationId!),
    enabled: !!observationId,
  })
  if (!observationId) {
    return <div style={{ padding: '10px 14px', fontSize: 12, color: 'var(--muted)' }}>No stored evidence is linked to this row.</div>
  }
  if (evidence.isLoading) return <div style={{ padding: 14 }}><Spinner /></div>
  if (evidence.isError || !evidence.data) {
    return <div style={{ padding: '10px 14px', fontSize: 12, color: '#C0362C' }}>{String((evidence.error as Error)?.message ?? 'could not load the evidence')}</div>
  }
  const ev = evidence.data
  return (
    <div style={{ padding: '0 14px 12px', display: 'grid', gap: 10 }}>
      <div className="panel" style={{ background: 'var(--card)' }}>
        <div style={{ padding: '7px 12px', borderBottom: '1px solid var(--line2)', fontSize: 10, fontWeight: 750, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--faint)' }}>
          Supplier said — verbatim{ev.page ? ` · page ${ev.page}` : ''}
        </div>
        <table className="ev"><tbody>
          {(ev.cells ?? []).map((cell, index) => (
            <tr key={index}>
              <td className="k">{cell.column_name ?? `col ${index + 1}`}</td>
              <td className="v">{cell.value == null ? '' : String(cell.value)}</td>
            </tr>
          ))}
          {!ev.cells?.length && (
            <tr><td colSpan={2} className="v mono" style={{ fontSize: 11.5, whiteSpace: 'pre-wrap' }}>{ev.raw_text}</td></tr>
          )}
        </tbody></table>
        {(ev.page_brand_text || ev.page_promotion_text || ev.supplier_identity_text) && (
          <div className="readas" style={{ borderTopStyle: 'dashed' }}>
            {ev.page_brand_text && <>Page brand mark: <b>{ev.page_brand_text}</b></>}
            {ev.page_promotion_text && <>{ev.page_brand_text ? ' · ' : ''}Page promotion: <b>{ev.page_promotion_text}</b></>}
            {ev.supplier_identity_text && <>{(ev.page_brand_text || ev.page_promotion_text) ? ' · ' : ''}Supplier printed: <b>{ev.supplier_identity_text}</b></>}
          </div>
        )}
        {!fixing && !!ev.cells?.length && (
          <div className="readas">
            <button className="lnk" onClick={() => setFixing(true)}>page misread? fix the evidence</button>
          </div>
        )}
      </div>
      {fixing && (
        <FixEvidencePanel
          runId={runId}
          evidence={ev}
          savedHint="Evidence corrected — use Re-run when you finish fixing and these rows get a fresh attempt"
          onCancel={() => setFixing(false)}
          onSaved={async () => {
            setFixing(false)
            await queryClient.invalidateQueries({ queryKey: ['held-evidence', runId, observationId] })
          }}
        />
      )}
    </div>
  )
}

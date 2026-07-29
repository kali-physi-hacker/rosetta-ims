// Catalogue review — front door. Every ingestion run, newest first; a run opens
// its board (see the run board route for the review flow itself).
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Spinner } from '@/components/Spinner'
import { C } from '@/lib/tokens'
import { reviewApi } from '@/lib/review'

export const Route = createFileRoute('/_authed/catalogues/review/')({ component: ReviewRunsPage })

interface RunRow {
  ingestion_run_id: string
  supplier_id: number | null
  contract_id: string | null
  status: string
  submitted_at: string
  completed_at: string | null
  items_extracted: number | null
}

const STATUS_PILL: Record<string, { bg: string; color: string }> = {
  completed: { bg: C.greenBg, color: C.green },
  completed_with_warnings: { bg: '#DBEAFE', color: '#1E40AF' },
  failed: { bg: C.redBg, color: C.redInk },
  processing: { bg: C.warnBg, color: C.amberInk },
  queued: { bg: C.monoBg, color: C.sub },
}

const MONO = 'ui-monospace, "SF Mono", Menlo, monospace'

function fmtWhen(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function ReviewRunsPage() {
  const runs = useQuery({
    queryKey: ['review-runs'],
    queryFn: () => reviewApi<RunRow[]>('/catalogues/ingestions/run_ids'),
  })

  const rows = [...(runs.data ?? [])].sort((a, b) => (b.submitted_at ?? '').localeCompare(a.submitted_at ?? ''))

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <div style={{ marginBottom: 16 }}>
        <h1 style={{ fontSize: 18, fontWeight: 800, color: C.ink, margin: 0 }}>Catalogue review</h1>
        <div style={{ fontSize: 12.5, color: C.muted, marginTop: 4 }}>
          Pick a run to review its mastering candidates — board to see, room to decide, commit to change.
        </div>
      </div>

      {runs.isLoading && <Spinner />}
      {runs.isError && (
        <div style={{ color: C.bad, fontSize: 13 }}>{String((runs.error as Error)?.message ?? runs.error)}</div>
      )}

      {runs.data && (
        <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 10, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
            <thead>
              <tr style={{ background: C.wash, textAlign: 'left' }}>
                {['Run', 'Contract', 'Status', 'Rows', 'Submitted', ''].map(h => (
                  <th key={h} style={{ padding: '9px 14px', fontSize: 11, fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.03em', color: C.muted }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map(run => {
                const pill = STATUS_PILL[run.status] ?? STATUS_PILL.queued
                return (
                  <tr key={run.ingestion_run_id} style={{ borderTop: `1px solid ${C.monoBg}` }}>
                    <td style={{ padding: '10px 14px', fontFamily: MONO, fontSize: 11.5, color: C.sub }}>{run.ingestion_run_id.slice(0, 8)}</td>
                    <td style={{ padding: '10px 14px', color: C.ink }}>{run.contract_id ?? '—'}</td>
                    <td style={{ padding: '10px 14px' }}>
                      <span style={{ fontSize: 10.5, fontWeight: 700, borderRadius: 999, padding: '2px 9px', background: pill.bg, color: pill.color }}>{run.status}</span>
                    </td>
                    <td style={{ padding: '10px 14px', fontFamily: MONO, fontSize: 11.5, color: C.sub }}>{run.items_extracted ?? '—'}</td>
                    <td style={{ padding: '10px 14px', color: C.muted }}>{fmtWhen(run.submitted_at)}</td>
                    <td style={{ padding: '10px 14px', textAlign: 'right' }}>
                      <Link
                        to="/catalogues/review/$runId"
                        params={{ runId: run.ingestion_run_id }}
                        style={{ fontSize: 12, fontWeight: 650, color: C.indigoStrong, textDecoration: 'none' }}
                      >
                        Open board →
                      </Link>
                    </td>
                  </tr>
                )
              })}
              {rows.length === 0 && (
                <tr><td colSpan={6} style={{ padding: 24, textAlign: 'center', color: C.muted, fontSize: 13 }}>
                  No ingestion runs yet — submit a catalogue from the Catalogues page.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

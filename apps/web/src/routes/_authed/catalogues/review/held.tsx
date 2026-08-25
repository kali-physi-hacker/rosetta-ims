// Held across suppliers — the queue as an OPERATED system (DEV-303).
// One card per supplier, one row per current document reading; counts are the
// followed queues, so rescued rows and re-uploads never double-count. Answers
// "what is held, how long, and is it getting worse?" without opening runs.
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { Spinner } from '@/components/Spinner'
import { DESK_CSS } from '@/lib/deskCss'
import { fetchHeldOverview, type HeldOverviewSupplier } from '@/lib/review'

export const Route = createFileRoute('/_authed/catalogues/review/held')({ component: HeldOverviewPage })

function fmtWhen(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { day: 'numeric', month: 'short' })
}

function pct(share: number | null): string {
  return share == null ? '—' : `${Math.round(share * 100)}%`
}

function HeldOverviewPage() {
  const overview = useQuery({ queryKey: ['held-overview'], queryFn: fetchHeldOverview, staleTime: 30_000 })
  const suppliers = overview.data?.suppliers ?? []
  const anythingHeld = suppliers.some(s => s.held_total > 0)

  return (
    <div className="rdesk" style={{ padding: '18px 24px 40px', maxWidth: 980, margin: '0 auto' }}>
      <style>{DESK_CSS}</style>
      <h1>Held across suppliers</h1>
      <div style={{ fontSize: 12.5, color: 'var(--muted)', marginTop: 4, maxWidth: 720 }}>
        Every supplier&apos;s rows the machine still can&apos;t read, counted on the newest reading of each
        document — rows a re-run or re-read already rescued are not counted, and a re-upload replaces its
        predecessor. <Link className="lnk" to="/catalogues/review">← back to runs</Link>
      </div>

      {overview.isLoading && <div style={{ marginTop: 16 }}><Spinner /></div>}
      {overview.isError && <div style={{ marginTop: 16, color: 'var(--red)', fontSize: 13 }}>{String((overview.error as Error)?.message ?? overview.error)}</div>}

      {suppliers.map(supplier => <SupplierCard key={supplier.supplier_id} supplier={supplier} />)}
      {overview.data && !anythingHeld && (
        <div className="panel" style={{ marginTop: 14, padding: 22, textAlign: 'center', color: 'var(--faint)', fontSize: 13 }}>
          Nothing is held anywhere — every current document reads clean.
        </div>
      )}

      <div className="panel" style={{ marginTop: 18, padding: '12px 16px', fontSize: 12, color: 'var(--muted)' }}>
        <b style={{ color: 'var(--ink2)' }}>The routine:</b> check this page once a week. A supplier marked{' '}
        <b>worse than usual</b>, or an oldest-held age that keeps climbing week over week, means tell
        engineering — with the supplier name, nothing more. The flag rule is stated, not a black box: the
        newest reading holds a notably larger share of its rows than that supplier&apos;s usual pattern
        (more than 1.5× the baseline and at least 10 points above it).
      </div>
    </div>
  )
}

function SupplierCard({ supplier }: { supplier: HeldOverviewSupplier }) {
  return (
    <div className="panel" style={{ marginTop: 14 }}>
      <div className="laneh">
        <span className="ln">{supplier.supplier_name ?? `supplier ${supplier.supplier_id}`}</span>
        <span className="lc">
          {supplier.held_total > 0 ? `${supplier.held_total} held` : 'nothing held'}
          {supplier.oldest_age_days != null && supplier.held_total > 0 && ` · oldest ${supplier.oldest_age_days}d`}
        </span>
        {supplier.worse_than_usual && (
          <span className="bdg" style={{ background: 'var(--red-soft, #fdecea)', color: 'var(--red, #C0362C)', marginLeft: 'auto' }}>
            worse than usual — {pct(supplier.latest_share)} vs {pct(supplier.baseline_share)} typical
          </span>
        )}
      </div>
      {supplier.documents.map((doc, index) => (
        <div key={doc.ingestion_run_id} style={{ display: 'flex', gap: 12, alignItems: 'baseline', padding: '9px 14px', borderTop: index ? '1px solid var(--line2)' : 'none', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 11.5, color: 'var(--faint)', width: 70, flex: 'none' }}>{fmtWhen(doc.submitted_at)}</span>
          <span style={{ fontSize: 12, color: 'var(--ink2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 260 }}>
            {doc.filename ?? doc.ingestion_run_id.slice(0, 8)}
          </span>
          <span style={{ fontSize: 12, color: doc.held ? 'var(--ink)' : 'var(--faint)' }}>
            {doc.held ? `${doc.held} of ${doc.rows} held (${pct(doc.held_share)})` : `all ${doc.rows} readable`}
          </span>
          {doc.held > 0 && doc.oldest_age_days != null && (
            <span style={{ fontSize: 11.5, color: 'var(--muted)' }}>oldest {doc.oldest_age_days}d</span>
          )}
          {doc.held > 0 && doc.top_issue_code && (
            <span className="mono" style={{ fontSize: 10, color: 'var(--faint)' }}>
              {doc.top_issue_code} ×{doc.top_issue_rows}
            </span>
          )}
          {doc.held > 0 && (
            <Link className="lnk" style={{ fontSize: 11.5, marginLeft: 'auto' }} to="/catalogues/review/$runId/held" params={{ runId: doc.ingestion_run_id }}>
              open held rows →
            </Link>
          )}
        </div>
      ))}
    </div>
  )
}

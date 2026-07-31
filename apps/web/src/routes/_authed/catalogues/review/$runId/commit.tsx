// The receipt — what this run's publishes actually wrote, old → new per SKU,
// each row linking to the SKU page where the change now lives. Publishing
// itself happens in the run desk's dock; this route is the durable record
// (it kept the old /commit path so bookmarks still land somewhere useful).
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery } from '@tanstack/react-query'
import { useMemo } from 'react'
import { Spinner } from '@/components/Spinner'
import { skuToPath } from '@/lib/sku'
import { DESK_CSS } from '@/lib/deskCss'
import { fetchReceipt, fetchSummary, latest, isStaged, fmtDelta } from '@/lib/review'

export const Route = createFileRoute('/_authed/catalogues/review/$runId/commit')({ component: ReceiptPage })

const fm = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))

function fmtWhen(iso: string | null): string {
  if (!iso) return '—'
  const d = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + 'Z')
  return isNaN(d.getTime()) ? iso : d.toLocaleString()
}

function ReceiptPage() {
  const { runId } = Route.useParams()
  const receipt = useQuery({ queryKey: ['review-receipt', runId], queryFn: () => fetchReceipt(runId) })
  const summary = useQuery({ queryKey: ['review-summary', runId], queryFn: () => fetchSummary(runId) })
  const stagedCount = useMemo(
    () => latest(summary.data?.items ?? []).filter(isStaged).length,
    [summary.data],
  )

  if (receipt.isLoading) return <div style={{ padding: 24 }}><Spinner /></div>
  if (receipt.isError) return <div style={{ padding: 24, color: '#C0362C', fontSize: 13 }}>{String((receipt.error as Error)?.message)}</div>

  const changes = receipt.data?.changes ?? []

  return (
    <div className="rdesk" style={{ padding: '18px 24px 40px', maxWidth: 980, margin: '0 auto' }}>
      <style>{DESK_CSS}</style>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div>
          <div style={{ fontSize: 11, color: 'var(--faint)', marginBottom: 2 }}>
            <Link className="lnk" to="/catalogues/review/$runId" params={{ runId }}>← Run desk</Link>
          </div>
          <h1>Receipt <span style={{ fontWeight: 450, fontSize: 12.5, color: 'var(--faint)' }}>· {changes.length} change{changes.length === 1 ? '' : 's'} written by this run</span></h1>
        </div>
        {changes.length > 0 && <span className="bdg ok" style={{ marginLeft: 'auto' }}><span className="st" />live</span>}
      </div>

      {stagedCount > 0 && (
        <div className="issue">
          <span className="ad" />
          <span><b>{stagedCount} approved change{stagedCount === 1 ? ' is' : 's are'} still staged</b> — they become real when published from the run desk’s dock.</span>
          <Link className="btn sm" style={{ marginLeft: 'auto' }} to="/catalogues/review/$runId" params={{ runId }}>Open desk</Link>
        </div>
      )}

      {changes.length === 0 ? (
        <div className="panel" style={{ marginTop: 12, padding: 22, fontSize: 12.5, color: 'var(--faint)' }}>
          Nothing published from this run yet — decisions become real at publish, and land here as they do.
        </div>
      ) : (
        <div className="panel" style={{ marginTop: 12 }}>
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, minWidth: 640 }}>
              <thead>
                <tr style={{ textAlign: 'left', background: 'var(--panel)' }}>
                  {['SKU', 'Product variant', 'Old', 'New', 'Δ', 'Written', ''].map(h => (
                    <th key={h} style={{ padding: '8px 13px', fontSize: 10, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--faint)' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {changes.map((change, index) => (
                  <tr key={index} style={{ borderTop: '1px solid var(--line2)', opacity: change.is_current ? 1 : 0.6 }}>
                    <td className="mono" style={{ padding: '7px 13px', fontSize: 11 }}>{change.sku_code ?? change.supplier_sku ?? '—'}</td>
                    <td style={{ padding: '7px 13px', color: 'var(--ink)' }}>{change.variant_name ?? '—'}{!change.is_current && <span style={{ fontSize: 10, color: 'var(--faint)' }}> · superseded since</span>}</td>
                    <td className="mono" style={{ padding: '7px 13px', fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>{change.old_unit_cost != null ? fm(change.old_unit_cost) : 'first price'}</td>
                    <td className="mono" style={{ padding: '7px 13px', fontSize: 11, fontVariantNumeric: 'tabular-nums', fontWeight: 700 }}>{fm(change.new_unit_cost)}</td>
                    <td className="mono" style={{ padding: '7px 13px', fontSize: 11, fontWeight: 700, color: change.delta_pct != null && Math.abs(change.delta_pct) > 20 ? 'var(--red)' : 'var(--muted)' }}>{fmtDelta(change.delta_pct) ?? '—'}</td>
                    <td style={{ padding: '7px 13px', fontSize: 11, color: 'var(--faint)', whiteSpace: 'nowrap' }}>{fmtWhen(change.written_at)}</td>
                    <td style={{ padding: '7px 13px', textAlign: 'right' }}>
                      {change.sku_code && (
                        <Link className="lnk" style={{ fontSize: 11.5 }} to={'/sku/$' as never} params={{ _splat: skuToPath(change.sku_code) } as never}>view SKU →</Link>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div style={{ padding: '8px 13px', borderTop: '1px solid var(--line2)', fontSize: 11, color: 'var(--faint)' }}>
            Every row carries review-decision provenance — the SKU page’s cost timeline links straight back to this run.
          </div>
        </div>
      )}
    </div>
  )
}

// Commit step — approved work becomes commercial state, deliberately. A
// release-notes summary of exactly what applying writes (and what it can never
// touch), every price change listed, then apply + publish as one typed-count-
// confirmed action. Replay is material-checked server-side, so retry is safe.
import { createFileRoute, Link } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState, type CSSProperties } from 'react'
import { Spinner } from '@/components/Spinner'
import { toast } from '@/lib/toast'
import { C } from '@/lib/tokens'
import {
  fetchSummary, latest, applyCandidate, publishCandidate, fanOut, fmtDelta,
  type SummaryItem,
} from '@/lib/review'

export const Route = createFileRoute('/_authed/catalogues/review/$runId/commit')({ component: CommitPage })

const MONO = 'ui-monospace, "SF Mono", Menlo, monospace'
const btn: CSSProperties = { border: `1px solid ${C.knobOff}`, borderRadius: 6, padding: '6px 11px', background: C.panel, cursor: 'pointer', fontSize: 11.5, fontWeight: 600, color: C.ink }

const isApproved = (item: SummaryItem) =>
  item.review_status === 'APPROVED' || item.review_status === 'APPROVED_WITH_OVERRIDE'

function CommitPage() {
  const { runId } = Route.useParams()
  const queryClient = useQueryClient()
  const [typed, setTyped] = useState('')
  const [progress, setProgress] = useState<string | null>(null)
  const [failures, setFailures] = useState<{ sku: string | null; error: string }[]>([])

  const summary = useQuery({ queryKey: ['review-summary', runId], queryFn: () => fetchSummary(runId) })
  const items = useMemo(() => latest(summary.data?.items ?? []), [summary.data])

  const approved = items.filter(isApproved)
  const toCommit = approved.filter(i => !i.published)
  const alreadyPublished = approved.length - toCommit.length
  const rejected = items.filter(i => i.review_status === 'REJECTED').length
  const pending = items.filter(i => i.review_status === 'PENDING_REVIEW').length
  // Release notes for THIS commit only — rows already published stay in the
  // header count but not in the change list.
  const changes = toCommit
    .filter(i => i.price_delta_pct != null && i.price_delta_pct !== 0)
    .sort((a, b) => Math.abs(b.price_delta_pct!) - Math.abs(a.price_delta_pct!))
  const firstPrices = toCommit.filter(i => i.price_delta_pct == null).length
  const offeringUpdates = toCommit.filter(i => i.offering_state === 'PROPOSED_CREATE' || i.variant_state === 'CONFIRMED_MATCH').length
  const medianDelta = useMemo(() => {
    const moves = changes.map(i => i.price_delta_pct!).sort((a, b) => a - b)
    return moves.length ? moves[Math.floor(moves.length / 2)] : null
  }, [changes])
  const version = `v${new Date().toISOString().slice(0, 10)}`

  async function commitAll() {
    setTyped('')
    setFailures([])
    setProgress(`0 / ${toCommit.length}`)
    const result = await fanOut(
      toCommit,
      async item => {
        await applyCandidate(runId, item.mastering_candidate_id)
        await publishCandidate(runId, item.mastering_candidate_id, version)
      },
      (done, total) => setProgress(`${done} / ${total}`),
    )
    setProgress(null)
    await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
    if (result.failures.length) {
      setFailures(result.failures.map(f => ({ sku: f.item.supplier_sku, error: f.error })))
      toast.error(`${result.failures.length} of ${toCommit.length} failed — retry is safe, replay repairs incomplete work`)
    } else {
      toast.success(`Applied and published ${toCommit.length} candidates as ${version}`)
    }
  }

  if (summary.isLoading) return <div style={{ padding: 24 }}><Spinner /></div>
  if (summary.isError) return <div style={{ padding: 24, color: C.bad, fontSize: 13 }}>{String((summary.error as Error)?.message)}</div>

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <Link to="/catalogues/review/$runId" params={{ runId }} style={{ fontSize: 12, color: C.muted, textDecoration: 'none' }}>← Board</Link>
        <h1 style={{ fontSize: 17, fontWeight: 800, color: C.ink, margin: 0 }}>Commit approved work</h1>
        <span style={{ fontSize: 10.5, fontWeight: 700, borderRadius: 999, padding: '2px 9px', background: C.greenBg, color: C.green }}>{approved.length} approved</span>
        <span style={{ marginLeft: 'auto', fontFamily: MONO, fontSize: 11, color: C.muted }}>{rejected} rejected · {pending} pending</span>
      </div>

      {pending > 0 && (
        <div style={{ marginTop: 12, background: C.amberBg, border: `1px solid ${C.amberLine}`, borderRadius: 8, padding: '8px 12px', fontSize: 12, color: C.amberInk }}>
          {pending} candidates are still undecided — committing now publishes only the approved work; the rest can follow in a later commit.
        </div>
      )}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10, marginTop: 14 }}>
        {[
          { n: String(toCommit.length), label: `offering price rows will be written${alreadyPublished ? ` (${alreadyPublished} already published)` : ''}` },
          { n: String(changes.length), label: `offering price changes${medianDelta != null ? ` · median ${fmtDelta(medianDelta)}` : ''}`, color: C.amber },
          { n: String(firstPrices + offeringUpdates), label: 'supplier offerings created or updated (variant link / first price)' },
          { n: '0', label: 'new product variants (creation stays closed)' },
        ].map(card => (
          <div key={card.label} style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, padding: '10px 12px' }}>
            <div style={{ fontSize: 17, fontWeight: 800, color: card.color ?? C.ink, fontVariantNumeric: 'tabular-nums' }}>{card.n}</div>
            <div style={{ fontSize: 10.5, color: C.muted }}>{card.label}</div>
          </div>
        ))}
      </div>

      {changes.length > 0 && (
        <div style={{ marginTop: 10, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 8, overflow: 'hidden' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
            <thead>
              <tr style={{ textAlign: 'left', background: C.wash }}>
                {['SKU', 'Product variant', 'Current', 'New', 'Δ'].map(h => (
                  <th key={h} style={{ padding: '7px 12px', fontSize: 10, letterSpacing: '0.06em', textTransform: 'uppercase', color: C.muted }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {changes.map(item => (
                <tr key={item.mastering_candidate_id} style={{ borderTop: `1px solid ${C.monoBg}` }}>
                  <td style={{ padding: '7px 12px', fontFamily: MONO, fontSize: 11 }}>{item.supplier_sku}</td>
                  <td style={{ padding: '7px 12px', color: C.ink }}>{item.variant_name ?? item.name}</td>
                  <td style={{ padding: '7px 12px', fontFamily: MONO, fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>{item.current_cost?.toFixed(2)}</td>
                  <td style={{ padding: '7px 12px', fontFamily: MONO, fontSize: 11, fontVariantNumeric: 'tabular-nums' }}>{item.cost_amount?.toFixed(2)}</td>
                  <td style={{ padding: '7px 12px', fontFamily: MONO, fontSize: 11, fontWeight: 700, color: Math.abs(item.price_delta_pct!) > 20 ? C.bad : C.amber }}>{fmtDelta(item.price_delta_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div style={{ display: 'flex', gap: 10, marginTop: 10, fontSize: 11 }}>
        <div style={{ flex: 1, borderRadius: 8, padding: '8px 12px', background: C.okBg, border: `1px solid ${C.okLine}`, color: C.green }}>
          <b>Writes:</b> supplier offering links · offering cost rows · packaging configurations · MBB terms — all with review-decision provenance.
        </div>
        <div style={{ flex: 1, borderRadius: 8, padding: '8px 12px', background: C.wash, border: `1px solid ${C.line}`, color: C.muted }}>
          <b>Never touches:</b> product-variant fields · channel selling prices (SellingItem) · stock (InventoryItem).
        </div>
      </div>

      {failures.length > 0 && (
        <div style={{ marginTop: 10, background: C.badBg, border: `1px solid #FCA5A5`, borderRadius: 8, padding: '8px 12px', fontSize: 11.5, color: C.redInk }}>
          <b>Failed ({failures.length}) — server reasons verbatim; commit again to retry just these:</b>
          {failures.slice(0, 5).map((f, index) => <div key={index} style={{ fontFamily: MONO, fontSize: 10.5, marginTop: 2 }}>{f.sku}: {f.error}</div>)}
          {failures.length > 5 && <div style={{ fontFamily: MONO, fontSize: 10.5 }}>… and {failures.length - 5} more</div>}
        </div>
      )}

      <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 12, background: C.ink, color: C.line, borderRadius: 8, padding: '10px 14px', fontSize: 12 }}>
        <span>
          Apply commercial state, then publish snapshot <b style={{ fontFamily: MONO }}>{version}</b> — publication is immutable; history stays readable.
        </span>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          {progress ? (
            <span style={{ fontFamily: MONO, fontSize: 11 }}>committing {progress}…</span>
          ) : toCommit.length > 0 ? (
            <>
              <span style={{ fontFamily: MONO, fontSize: 11, fontWeight: 700, color: typed && typed.trim() !== String(toCommit.length) ? '#FCA5A5' : C.knobOff }}>
                {typed && typed.trim() !== String(toCommit.length) ? `must match ${toCommit.length} →` : `Type ${toCommit.length} here to unlock →`}
              </span>
              <input
                value={typed}
                onChange={e => setTyped(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && typed.trim() === String(toCommit.length)) commitAll() }}
                autoFocus
                placeholder={String(toCommit.length)}
                style={{ width: 64, background: '#1E293B', border: `1px solid ${typed.trim() === String(toCommit.length) ? C.ok : '#475569'}`, borderRadius: 6, padding: '4px 10px', fontFamily: MONO, fontSize: 12, color: C.line, textAlign: 'center' }}
                aria-label="Type the commit count to confirm"
              />
              <button
                style={{ ...btn, background: C.ok, borderColor: C.ok, color: '#fff', opacity: typed.trim() === String(toCommit.length) ? 1 : 0.75 }}
                onClick={() => {
                  if (typed.trim() === String(toCommit.length)) { commitAll(); return }
                  toast.error(`Type ${toCommit.length} in the box first — the count is the confirmation.`)
                }}
              >
                Apply &amp; publish {toCommit.length}
              </button>
            </>
          ) : (
            <span style={{ fontFamily: MONO, fontSize: 11, color: C.faint }}>
              {approved.length ? 'everything approved is already published' : 'nothing approved yet'}
            </span>
          )}
        </span>
      </div>
    </div>
  )
}

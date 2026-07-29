import { useCallback, useEffect, useState } from 'react'
import { API_BASE } from '@/lib/config'
import { authHeaders, can } from '@/lib/auth'
import { C } from '@/lib/tokens'

type Json = Record<string, any>

async function api(path: string, init?: RequestInit) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(init?.headers ?? {}) },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message ?? `HTTP ${response.status}`)
  }
  return payload
}

export function CataloguePipelineReview({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [intermediate, setIntermediate] = useState<Json | null>(null)
  const [serving, setServing] = useState<Json | null>(null)
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const [reason, setReason] = useState('Reviewed against supplier evidence')
  const [correctionFor, setCorrectionFor] = useState<string | null>(null)
  const [correction, setCorrection] = useState('{\n  "reason": "Corrected against supplier evidence"\n}')

  const refresh = useCallback(async () => {
    const [nextIntermediate, nextServing] = await Promise.all([
      api(`/catalogues/ingestions/${runId}/intermediate`),
      api(`/catalogues/ingestions/${runId}/serving`),
    ])
    setIntermediate(nextIntermediate)
    setServing(nextServing)
  }, [runId])

  useEffect(() => { refresh().catch(e => setError(String(e.message ?? e))) }, [refresh])

  async function action(key: string, path: string, body: Json) {
    setBusy(key); setError('')
    try {
      await api(path, { method: 'POST', body: JSON.stringify(body) })
      await refresh()
    } catch (e: any) {
      setError(String(e?.message ?? e))
    } finally {
      setBusy('')
    }
  }

  const issues = intermediate?.validation_issues ?? []
  const candidates = (intermediate?.mastering_candidates ?? []).filter((candidate: Json) => !candidate.superseded_by)
  const button = { border: '1px solid #CBD5E1', borderRadius: 6, padding: '5px 9px', background: 'white', cursor: 'pointer', fontSize: 11 } as const

  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,.42)', zIndex: 1000, display: 'flex', justifyContent: 'flex-end' }}>
      <section style={{ width: 'min(760px, 96vw)', height: '100%', overflow: 'auto', background: '#F8FAFC', padding: 20, boxShadow: '-8px 0 30px rgba(15,23,42,.18)' }}>
        <header style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 16 }}>
          <div style={{ flex: 1 }}>
            <h2 style={{ margin: 0, fontSize: 18, color: C.ink }}>Pipeline review</h2>
            <div style={{ color: C.muted, fontSize: 11, marginTop: 3 }}>{runId}</div>
          </div>
          <button onClick={() => refresh().catch(e => setError(String(e.message ?? e)))} style={button}>Refresh</button>
          <button onClick={onClose} style={button}>Close</button>
        </header>

        {error && <div style={{ background: C.redBg, color: C.redInk, padding: 10, borderRadius: 7, marginBottom: 12 }}>{error}</div>}

        <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: 10, padding: 14, marginBottom: 14 }}>
          <h3 style={{ margin: '0 0 10px', fontSize: 14 }}>Validation issues ({issues.length})</h3>
          {issues.length === 0 && <div style={{ color: C.muted, fontSize: 12 }}>No validation issues.</div>}
          {issues.map((issue: Json) => {
            const id = issue.validation_issue_id
            const open = issue.resolution_status === 'OPEN'
            return <div key={id} style={{ borderTop: '1px solid #F1F5F9', padding: '9px 0', display: 'flex', gap: 10 }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <strong style={{ fontSize: 12 }}>{issue.issue_code}</strong>
                <div style={{ fontSize: 12, color: C.sub }}>{issue.message}</div>
                <div style={{ fontSize: 10, color: C.faint }}>{issue.severity} · {issue.resolution_status}</div>
              </div>
              {open && <button disabled={!!busy} style={button} onClick={() => action(
                `issue:${id}`,
                `/catalogues/ingestions/${runId}/validation-issues/${id}/resolve`,
                { resolution_status: 'RESOLVED', resolution_note: reason },
              )}>{busy === `issue:${id}` ? 'Saving…' : 'Resolve'}</button>}
            </div>
          })}
        </div>

        <div style={{ marginBottom: 10 }}>
          <label style={{ fontSize: 11, color: C.muted }}>Decision note</label>
          <input value={reason} onChange={e => setReason(e.target.value)}
            style={{ width: '100%', boxSizing: 'border-box', border: '1px solid #CBD5E1', borderRadius: 6, padding: 8 }} />
        </div>

        {candidates.map((candidate: Json) => {
          const id = candidate.mastering_candidate_id
          const status = candidate.review_status
          const name = candidate.product_variant_resolution?.product_variant_name
            ?? candidate.product_variant_resolution?.proposed_name
            ?? candidate.metadata?.source_product_name
            ?? id
          const approved = status === 'APPROVED' || status === 'APPROVED_WITH_OVERRIDE'
          return <article key={id} style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: 10, padding: 14, marginBottom: 10 }}>
            <div style={{ display: 'flex', gap: 8 }}>
              <div style={{ flex: 1 }}>
                <strong style={{ color: C.ink }}>{name}</strong>
                <div style={{ fontSize: 11, color: C.muted }}>{status} · {candidate.product_variant_resolution?.state}</div>
                <div style={{ fontSize: 11, color: C.sub, marginTop: 4 }}>
                  Cost: {candidate.supplier_price_resolution?.current_cost?.amount ?? 'unresolved'} {candidate.supplier_price_resolution?.current_cost?.currency ?? ''}
                </div>
              </div>
              <button style={button} onClick={() => { setCorrectionFor(id); setCorrection(JSON.stringify({ reason: 'Corrected against supplier evidence' }, null, 2)) }}>Correct JSON</button>
            </div>
            <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 10 }}>
              {!approved && <>
                <button disabled={!!busy} style={button} onClick={() => action(`approve:${id}`, `/catalogues/ingestions/${runId}/mastering-candidates/${id}/review`, { review_status: 'APPROVED', reason })}>Approve</button>
                <button disabled={!!busy} style={button} onClick={() => action(`clarify:${id}`, `/catalogues/ingestions/${runId}/mastering-candidates/${id}/review`, { review_status: 'NEEDS_CLARIFICATION', reason })}>Needs clarification</button>
                <button disabled={!!busy} style={button} onClick={() => action(`reject:${id}`, `/catalogues/ingestions/${runId}/mastering-candidates/${id}/review`, { review_status: 'REJECTED', reason })}>Reject</button>
              </>}
              {approved && can('catalogue_publish') && <>
                <button disabled={!!busy} style={button} onClick={() => action(`apply:${id}`, `/catalogues/ingestions/${runId}/mastering-candidates/${id}/apply`, {})}>Apply commercial state</button>
                <button disabled={!!busy} style={button} onClick={() => action(`publish:${id}`, `/catalogues/ingestions/${runId}/mastering-candidates/${id}/publish`, { publication_version: new Date().toISOString() })}>Publish</button>
              </>}
            </div>
          </article>
        })}

        <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: 10, padding: 14 }}>
          <h3 style={{ margin: '0 0 8px', fontSize: 14 }}>Serving publications</h3>
          <div style={{ fontSize: 12, color: C.sub }}>{serving?.publication_count ?? 0} publication(s), {serving?.current_publications?.length ?? 0} current.</div>
        </div>

        {correctionFor && <div style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,.35)', display: 'grid', placeItems: 'center', zIndex: 1001 }}>
          <div style={{ width: 'min(620px, 92vw)', background: 'white', borderRadius: 10, padding: 16 }}>
            <h3 style={{ marginTop: 0 }}>Correct candidate resolution JSON</h3>
            <p style={{ fontSize: 12, color: C.muted }}>Include a reason and one or more resolution sections accepted by the candidate correction API.</p>
            <textarea value={correction} onChange={e => setCorrection(e.target.value)} rows={18}
              style={{ width: '100%', boxSizing: 'border-box', fontFamily: 'monospace', fontSize: 12 }} />
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 10 }}>
              <button style={button} onClick={() => setCorrectionFor(null)}>Cancel</button>
              <button style={button} onClick={() => {
                try {
                  const body = JSON.parse(correction)
                  const id = correctionFor
                  setCorrectionFor(null)
                  void action(`correct:${id}`, `/catalogues/ingestions/${runId}/mastering-candidates/${id}/correct`, body)
                } catch { setError('Correction must be valid JSON') }
              }}>Create revision</button>
            </div>
          </div>
        </div>}
      </section>
    </div>
  )
}

import { useRef, useState } from 'react'
import { toast } from '@/lib/toast'
import { correctEvidence } from '@/lib/review'

export function FixEvidencePanel({ runId, evidence, onCancel, onSaved, savedHint }: {
  runId: string
  evidence: { raw_observation_id: string; page?: number | null; cells: Array<{ column_name?: string | null; value?: any }> }
  onCancel: () => void
  onSaved: () => void | Promise<void>
  /** What happens next, in the surface's own words — the desk re-parses the
   * whole run; the held lane re-runs just the held rows. */
  savedHint?: string
}) {
  // This panel edits what the machine READ, not what it made of it. Values
  // are keyed by the observation's own column names and REPLACE the misread
  // cells; the originals stay stamped on the observation's audit trail.
  // Nothing re-runs on save — re-driving is its own visible action.
  const named = (evidence.cells ?? []).filter(c => c.column_name) as Array<{ column_name: string; value: any }>
  const initial = useRef<Record<string, string>>(
    Object.fromEntries(named.map(c => [c.column_name, c.value == null ? '' : String(c.value)]))).current
  const [values, setValues] = useState<Record<string, string>>({ ...initial })
  const [why, setWhy] = useState('')
  const [saving, setSaving] = useState(false)

  const changed: Record<string, string> = {}
  for (const [column, value] of Object.entries(values)) {
    if (value.trim() !== (initial[column] ?? '').trim()) changed[column] = value.trim()
  }
  const ready = Object.keys(changed).length > 0 && why.trim().length > 3

  async function save() {
    setSaving(true)
    try {
      await correctEvidence(runId, evidence.raw_observation_id, `Corrected the read evidence — ${why.trim()}`, changed)
      toast.success(savedHint ?? 'Evidence corrected — re-parse the run when you finish fixing and the pipeline re-reads it')
      await onSaved()
    } catch (e: any) {
      toast.error(String(e?.message ?? e))
    } finally { setSaving(false) }
  }

  return (
    <div className="panel" style={{ background: 'var(--card)', borderColor: 'var(--accent-line)' }}>
      <div className="cdh">
        <span>Fix the evidence — what the page actually prints</span>
        <span className="cdnote">replaces misread cells; the original values stay on the audit trail</span>
      </div>
      <div style={{ padding: '10px 12px', display: 'grid', gap: 8 }}>
        {named.map(cell => (
          <label key={cell.column_name} className="cdf"><span>{cell.column_name}</span>
            <input className="fin mono" value={values[cell.column_name] ?? ''}
              onChange={e => setValues(v => ({ ...v, [cell.column_name]: e.target.value }))} /></label>
        ))}
        <label className="cdf"><span>What does the page actually say? — required, this is the audit trail</span>
          <input className="fin" value={why} onChange={e => setWhy(e.target.value)}
            placeholder="e.g. the page prints HK$128.0 — the scan smudged the digit" /></label>
        <div style={{ fontSize: 11.5, color: 'var(--faint)' }}>
          Corrections apply on the next re-run — fix every misread row first, then re-run once.
        </div>
      </div>
      <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', padding: '10px 12px' }}>
        <button className="btn sm" onClick={onCancel} disabled={saving}>Cancel</button>
        <button className="btn pri sm" onClick={save} disabled={!ready || saving}>
          {saving ? 'Saving…' : 'Save the corrected evidence'}
        </button>
      </div>
    </div>
  )
}

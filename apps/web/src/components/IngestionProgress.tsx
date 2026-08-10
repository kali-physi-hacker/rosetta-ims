/**
 * What a catalogue is doing while you wait for it.
 *
 * A 56-page catalogue takes minutes, and the only thing the interface used to
 * say about that was the word "running". There was no way to tell a slow read
 * from a wedged one, so the honest reaction to a long wait was to reload the
 * page and hope.
 *
 * Two facts do almost all the work here, and both come from the run itself:
 * the stage it has reached, and — while it is reading — how many pages are
 * done. Everything else is restraint. In particular there is no synthetic
 * animation standing in for progress: a bar that moves on a timer rather than
 * on work says "something is happening" even when nothing is, which is the
 * exact reassurance this component exists to stop giving.
 */
import { useEffect, useState } from 'react'

import type { RunStatus } from '@/lib/review'

/* Carried by the component itself rather than a page stylesheet: this appears
   both on the upload page (inline styles, no design system) and on the run desk
   (scoped under .rdesk), and it should not depend on which one it landed in. */
const CSS = `
@keyframes ing-spin{to{transform:rotate(360deg)}}
.ing-spin{animation:ing-spin 900ms linear infinite}
@media(prefers-reduced-motion:reduce){.ing-spin{animation:none;opacity:.55}}
`

/** Stages advance in this order; used to place the run in the sequence. */
export const isWorking = (status: string | null | undefined) =>
  status === 'queued' || status === 'running'

/** "4m 12s" — elapsed, coarse enough not to twitch. */
function elapsed(sinceIso: string | null | undefined, now: number): string | null {
  if (!sinceIso) return null
  const started = new Date(/[zZ]|[+-]\d\d:?\d\d$/.test(sinceIso) ? sinceIso : sinceIso + 'Z').getTime()
  if (isNaN(started)) return null
  const seconds = Math.max(0, Math.round((now - started) / 1000))
  if (seconds < 60) return `${seconds}s`
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, '0')}s`
}

/** A tick purely so elapsed time advances between polls. */
function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (!active) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [active])
  return now
}

export interface IngestionProgressProps {
  run: Pick<
    RunStatus,
    'status' | 'stage' | 'stage_label' | 'stage_started_at' | 'stage_index' | 'stage_count'
    | 'units_done' | 'units_total' | 'started_at'
  > | null | undefined
  /** `bar` for a full-width block; `inline` for a single line inside a list row. */
  variant?: 'bar' | 'inline'
}

export function IngestionProgress({ run, variant = 'bar' }: IngestionProgressProps) {
  const working = isWorking(run?.status)
  const now = useNow(working)
  if (!run || !working) return null

  const total = run.units_total ?? 0
  const done = run.units_total ? Math.min(run.units_done ?? 0, run.units_total) : 0
  const hasUnits = total > 0
  const pct = hasUnits ? Math.round((done / total) * 100) : null

  // Queued is a real state with nothing to report yet: the file is accepted and
  // waiting for a worker. Saying so beats an empty stage label.
  const label = run.status === 'queued'
    ? 'Waiting for a worker'
    : run.stage_label ?? 'Working'

  const step = run.stage_index && run.stage_count ? `step ${run.stage_index} of ${run.stage_count}` : null
  const pages = hasUnits ? `page ${done} of ${total}` : null
  const since = elapsed(run.stage_started_at ?? run.started_at, now)

  if (variant === 'inline') {
    return (
      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
        <style>{CSS}</style>
        <Spinner />
        <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {label}
          {pages ? ` · ${pages}` : ''}
          {since ? ` · ${since}` : ''}
        </span>
      </span>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, minWidth: 0 }}>
      <style>{CSS}</style>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, minWidth: 0 }}>
        <Spinner />
        <b style={{ color: 'var(--ink)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {label}
        </b>
        <span style={{ color: 'var(--ink2)', whiteSpace: 'nowrap' }}>
          {[step, pages, since].filter(Boolean).join(' · ')}
        </span>
      </div>

      {/* Determinate only when pages are genuinely being counted. Every other
          stage gets a flat track: no bar is more honest than a fake one. */}
      <div
        role="progressbar"
        aria-label={label}
        aria-valuenow={pct ?? undefined}
        aria-valuemin={pct == null ? undefined : 0}
        aria-valuemax={pct == null ? undefined : 100}
        aria-valuetext={pages ?? label}
        style={{ height: 4, borderRadius: 99, background: 'var(--line, #E5E7EB)', overflow: 'hidden' }}
      >
        {pct != null && (
          <div
            style={{
              height: '100%',
              width: `${pct}%`,
              background: 'var(--accent, #4F46E5)',
              borderRadius: 99,
              transition: 'width 400ms ease',
            }}
          />
        )}
      </div>
    </div>
  )
}

function Spinner() {
  return (
    <span
      aria-hidden
      className="ing-spin"
      style={{
        flex: '0 0 auto',
        width: 9,
        height: 9,
        borderRadius: 99,
        border: '2px solid var(--accent, #4F46E5)',
        borderTopColor: 'transparent',
      }}
    />
  )
}

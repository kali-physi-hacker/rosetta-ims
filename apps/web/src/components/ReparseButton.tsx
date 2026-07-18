import { C } from '@/lib/tokens'
// Entry-point button for a catalogue re-parse. Owns the behaviour (POST to create a
// batch → route to the diff-review screen); appearance is supplied by the host page via
// `className` or `style` so it sits natively wherever it's dropped (SKU detail, imports
// list, supplier row). Additive only — it never restructures the page it lives on.
import { useState } from 'react'
import { useNavigate } from '@tanstack/react-router'
import { Spinner } from '@/components/Spinner'
import { toast } from '@/lib/toast'
import { startReparse, type ReparseScope } from '@/lib/reparse'

interface ReparseButtonProps {
  scope: ReparseScope
  refId: string | number
  label?: string
  title?: string
  className?: string
  style?: React.CSSProperties
}

// Mock default look (`.rbtn`) — used only when the host passes neither className nor style.
const DEFAULT_STYLE: React.CSSProperties = {
  display: 'inline-flex', alignItems: 'center', gap: '6px',
  fontSize: '12px', fontWeight: 650, fontFamily: 'inherit',
  color: '#fff', background: C.indigo, border: 'none',
  borderRadius: '8px', padding: '7px 12px', cursor: 'pointer',
}

export function ReparseButton({ scope, refId, label, title, className, style }: ReparseButtonProps) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)

  async function start() {
    if (busy) return
    setBusy(true)
    try {
      const batch = await startReparse(scope, refId)
      // Statically typed once the reparse route is ported (see MIGRATION.md); cast during migration.
      navigate({ to: `/catalogues/reparse/${batch.id}` } as never)
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Could not start re-parse')
      setBusy(false)   // keep the button usable on failure (success navigates away)
    }
  }

  const text = label ?? '↻ Re-parse from catalogue'
  return (
    <button
      type="button"
      onClick={start}
      disabled={busy}
      title={title ?? 'Re-derive catalogue-sourced fields and review the diff'}
      className={className}
      style={className ? undefined : { ...DEFAULT_STYLE, ...style, opacity: busy ? 0.7 : 1 }}
    >
      {busy ? <><Spinner size={11} /> Starting…</> : text}
    </button>
  )
}

import { C } from '@/lib/tokens'
import { useEffect, useState, useCallback } from 'react'
import { subscribeConfirm, ConfirmRequest } from '@/lib/confirm'

export function ConfirmDialog() {
  const [active, setActive] = useState<ConfirmRequest | null>(null)
  const [typed, setTyped] = useState('')

  useEffect(() => subscribeConfirm(r => { setTyped(''); setActive(r) }), [])

  const close = useCallback((v: boolean | string) => {
    setActive(a => { a?.resolve(v); return null })
  }, [])

  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(false)
      // A dialog that asks for a reason must not be confirmable by Enter on an
      // empty field — that is the click-OK path the reason exists to prevent.
      else if (e.key === 'Enter') {
        if (!active.options.prompt) close(true)
        else if (typed.trim()) close(typed)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, close, typed])

  if (!active) return null
  const o = active.options
  const danger = !!o.danger

  return (
    <>
      <style>{`@keyframes ims-dialog-in { from { opacity: 0; transform: translateY(10px) scale(.97); } to { opacity: 1; transform: none; } }`}</style>
      <div
        onClick={() => close(false)}
        style={{ position: 'fixed', inset: 0, zIndex: 1100, background: 'rgba(15,23,42,0.5)', backdropFilter: 'blur(1px)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}
      >
        <div
          onClick={e => e.stopPropagation()}
          role="dialog" aria-modal="true"
          style={{ background: 'white', borderRadius: '14px', width: '100%', maxWidth: '440px', boxShadow: '0 24px 60px rgba(15,23,42,0.32)', overflow: 'hidden', animation: 'ims-dialog-in .2s cubic-bezier(.2,.8,.2,1) both' }}
        >
          <div style={{ padding: '20px 22px 16px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: o.title ? '10px' : '0' }}>
              <span style={{
                flex: '0 0 auto', width: '32px', height: '32px', borderRadius: '50%',
                background: danger ? C.redBg : C.primaryBg, color: danger ? '#DC2626' : C.indigoStrong,
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', fontWeight: 800,
              }}>{danger ? '!' : '?'}</span>
              {o.title && <h3 style={{ fontSize: '15px', fontWeight: 700, color: C.ink, margin: 0 }}>{o.title}</h3>}
            </div>
            <p style={{ fontSize: '13px', color: C.sub, lineHeight: 1.5, margin: 0, whiteSpace: 'pre-wrap', fontFamily: o.message.includes('  ') ? 'ui-monospace, monospace' : 'inherit' }}>
              {o.message}
            </p>
            {o.prompt && (
              <input
                autoFocus value={typed} onChange={e => setTyped(e.target.value)}
                placeholder={o.prompt.placeholder ?? 'Reason'}
                aria-label={o.prompt.label ?? o.prompt.placeholder ?? 'Reason'}
                style={{
                  marginTop: '12px', width: '100%', boxSizing: 'border-box', padding: '9px 11px',
                  border: '1px solid #E2E8F0', borderRadius: '8px', fontSize: '13px', color: C.ink,
                }}
              />
            )}
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', padding: '14px 22px', background: C.wash, borderTop: '1px solid #EEF2F7' }}>
            <button onClick={() => close(false)} style={{ background: 'white', border: '1px solid #E2E8F0', color: C.sub, borderRadius: '8px', padding: '8px 16px', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>
              {o.cancelLabel ?? 'Cancel'}
            </button>
            <button
              onClick={() => close(o.prompt ? typed : true)}
              autoFocus={!o.prompt}
              disabled={!!o.prompt && !typed.trim()}
              style={{
                background: o.prompt && !typed.trim() ? '#CBD5E1' : danger ? '#DC2626' : C.indigo,
                border: 'none', color: 'white', borderRadius: '8px', padding: '8px 18px',
                fontSize: '13px', fontWeight: 700,
                cursor: o.prompt && !typed.trim() ? 'not-allowed' : 'pointer',
              }}
            >
              {o.confirmLabel ?? 'Confirm'}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

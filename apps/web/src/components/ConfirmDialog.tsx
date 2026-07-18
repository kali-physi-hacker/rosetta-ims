import { useEffect, useState, useCallback } from 'react'
import { subscribeConfirm, ConfirmRequest } from '@/lib/confirm'

export function ConfirmDialog() {
  const [active, setActive] = useState<ConfirmRequest | null>(null)

  useEffect(() => subscribeConfirm(setActive), [])

  const close = useCallback((v: boolean) => {
    setActive(a => { a?.resolve(v); return null })
  }, [])

  useEffect(() => {
    if (!active) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(false)
      else if (e.key === 'Enter') close(true)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [active, close])

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
                background: danger ? '#FEE2E2' : '#EEF2FF', color: danger ? '#DC2626' : '#4F46E5',
                display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '16px', fontWeight: 800,
              }}>{danger ? '!' : '?'}</span>
              {o.title && <h3 style={{ fontSize: '15px', fontWeight: 700, color: '#0F172A', margin: 0 }}>{o.title}</h3>}
            </div>
            <p style={{ fontSize: '13px', color: '#475569', lineHeight: 1.5, margin: 0, whiteSpace: 'pre-wrap', fontFamily: o.message.includes('  ') ? 'ui-monospace, monospace' : 'inherit' }}>
              {o.message}
            </p>
          </div>
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', padding: '14px 22px', background: '#F8FAFC', borderTop: '1px solid #EEF2F7' }}>
            <button onClick={() => close(false)} style={{ background: 'white', border: '1px solid #E2E8F0', color: '#475569', borderRadius: '8px', padding: '8px 16px', fontSize: '13px', fontWeight: 600, cursor: 'pointer' }}>
              {o.cancelLabel ?? 'Cancel'}
            </button>
            <button onClick={() => close(true)} autoFocus style={{ background: danger ? '#DC2626' : '#6366F1', border: 'none', color: 'white', borderRadius: '8px', padding: '8px 18px', fontSize: '13px', fontWeight: 700, cursor: 'pointer' }}>
              {o.confirmLabel ?? 'Confirm'}
            </button>
          </div>
        </div>
      </div>
    </>
  )
}

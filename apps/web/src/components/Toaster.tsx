import { C } from '@/lib/tokens'
import { useEffect, useState, useCallback } from 'react'
import { subscribeToasts, ToastItem, ToastType } from '@/lib/toast'

const STYLE: Record<ToastType, { bg: string; border: string; bar: string; icon: string; iconColor: string }> = {
  success: { bg: C.panel, border: '#BBF7D0', bar: '#22C55E', icon: '✓', iconColor: '#16A34A' },
  error:   { bg: C.panel, border: '#FECACA', bar: '#EF4444', icon: '!', iconColor: '#DC2626' },
  info:    { bg: C.panel, border: C.line, bar: C.indigo, icon: 'i', iconColor: C.indigoStrong },
}
const TTL: Record<ToastType, number> = { success: 3500, error: 6500, info: 4000 }

export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([])

  const dismiss = useCallback((id: number) => setItems(prev => prev.filter(t => t.id !== id)), [])

  useEffect(() => subscribeToasts(t => {
    setItems(prev => [...prev.slice(-4), t])   // cap the stack
    setTimeout(() => dismiss(t.id), TTL[t.type])
  }), [dismiss])

  if (items.length === 0) return null

  return (
    <>
      <style>{`
        @keyframes ims-toast-in { from { opacity: 0; transform: translateY(8px) scale(.98); } to { opacity: 1; transform: none; } }
      `}</style>
      <div style={{
        position: 'fixed', bottom: '20px', right: '20px', zIndex: 1000,
        display: 'flex', flexDirection: 'column', gap: '10px', alignItems: 'flex-end',
        maxWidth: 'min(380px, calc(100vw - 40px))', pointerEvents: 'none',
      }}>
        {items.map(t => {
          const s = STYLE[t.type]
          return (
            <div key={t.id} onClick={() => dismiss(t.id)} role="status" style={{
              pointerEvents: 'auto', cursor: 'pointer', width: '100%',
              display: 'flex', alignItems: 'flex-start', gap: '11px',
              background: s.bg, border: `1px solid ${s.border}`, borderLeft: `4px solid ${s.bar}`,
              borderRadius: '10px', padding: '12px 14px',
              boxShadow: '0 10px 30px rgba(15,23,42,0.14), 0 2px 6px rgba(15,23,42,0.08)',
              animation: 'ims-toast-in .22s cubic-bezier(.2,.8,.2,1) both',
            }}>
              <span style={{
                flex: '0 0 auto', width: '20px', height: '20px', borderRadius: '50%',
                background: s.bar + '22', color: s.iconColor, fontWeight: 800, fontSize: '12px',
                display: 'flex', alignItems: 'center', justifyContent: 'center', marginTop: '1px',
              }}>{s.icon}</span>
              <span style={{ flex: 1, fontSize: '13px', lineHeight: 1.4, color: C.ink, wordBreak: 'break-word' }}>{t.message}</span>
              <span style={{ flex: '0 0 auto', color: C.faint, fontSize: '15px', lineHeight: 1, marginTop: '1px' }}>×</span>
            </div>
          )
        })}
      </div>
    </>
  )
}

// Tiny global toast/notification bus — no provider needed. Call toast.error()/
// toast.success()/toast.info() from anywhere; <Toaster/> (mounted once in the
// AppShell) subscribes and renders the stack.
export type ToastType = 'success' | 'error' | 'info'
export interface ToastItem { id: number; type: ToastType; message: string }

type Listener = (t: ToastItem) => void
const listeners = new Set<Listener>()
let counter = 0

export function subscribeToasts(fn: Listener): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

function push(message: string, type: ToastType) {
  const item: ToastItem = { id: ++counter, type, message: String(message ?? '') }
  listeners.forEach(l => l(item))
}

export const toast = {
  success: (m: string) => push(m, 'success'),
  error:   (m: string) => push(m, 'error'),
  info:    (m: string) => push(m, 'info'),
}

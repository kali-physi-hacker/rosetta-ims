// Promise-based confirm dialog — async replacement for window.confirm().
// <ConfirmDialog/> (mounted once in AppShell) subscribes and renders the modal.
export interface ConfirmOptions {
  title?: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean   // red confirm button for destructive actions
}
export interface ConfirmRequest { id: number; options: ConfirmOptions; resolve: (v: boolean) => void }

type Listener = (r: ConfirmRequest) => void
const listeners = new Set<Listener>()
let counter = 0

export function subscribeConfirm(fn: Listener): () => void {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

export function confirmDialog(options: ConfirmOptions): Promise<boolean> {
  return new Promise(resolve => {
    // Fallback to the native dialog if no <ConfirmDialog/> is mounted.
    if (listeners.size === 0) {
      resolve(typeof window !== 'undefined' ? window.confirm(options.message) : false)
      return
    }
    const req: ConfirmRequest = { id: ++counter, options, resolve }
    listeners.forEach(l => l(req))
  })
}

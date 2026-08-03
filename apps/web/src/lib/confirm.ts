// Promise-based confirm dialog — async replacement for window.confirm().
// <ConfirmDialog/> (mounted once in AppShell) subscribes and renders the modal.
export interface ConfirmOptions {
  title?: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean   // red confirm button for destructive actions
  // When set, the dialog asks for a line of text and will not confirm without
  // one. Used where the answer is recorded and outlives the session — a
  // decision that needs a reason should not be reducible to clicking OK.
  prompt?: { placeholder?: string; label?: string }
}
export interface ConfirmRequest { id: number; options: ConfirmOptions; resolve: (v: boolean | string) => void }

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
    const req: ConfirmRequest = { id: ++counter, options, resolve: v => resolve(!!v) }
    listeners.forEach(l => l(req))
  })
}

/**
 * Ask for a reason. Resolves to the trimmed text, or null if cancelled or left
 * blank — a caller can treat "no reason" and "cancelled" the same, because
 * neither should record a decision.
 */
export function promptDialog(options: ConfirmOptions & { prompt?: ConfirmOptions['prompt'] }): Promise<string | null> {
  return new Promise(resolve => {
    if (listeners.size === 0) {
      const typed = typeof window !== 'undefined' ? window.prompt(options.message) : null
      resolve(typed && typed.trim() ? typed.trim() : null)
      return
    }
    const req: ConfirmRequest = {
      id: ++counter,
      options: { ...options, prompt: options.prompt ?? {} },
      resolve: v => resolve(typeof v === 'string' && v.trim() ? v.trim() : null),
    }
    listeners.forEach(l => l(req))
  })
}

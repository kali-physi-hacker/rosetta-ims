import type { Product } from './types'
import { authHeaders } from './auth'
import { API_BASE } from './config'

const API = API_BASE

/**
 * Stream the full inventory from /products/stream (NDJSON) and hand rows to `onBatch` as they
 * arrive — a small first batch for a fast first paint, then larger batches. The first call has
 * `isFirst: true` (replace your list); subsequent calls append. Resolves when the stream ends.
 * Pass an AbortSignal to cancel on unmount/refresh.
 *
 * Shared by the inventory-heavy screens (data-review, logic, …) so they all get the same
 * continuous, sub-second-first-paint load instead of a multi-second blocking fetch.
 */
export async function streamProducts(
  onBatch: (info: { batch: Product[]; isFirst: boolean; loaded: number; total: number }) => void,
  opts?: { signal?: AbortSignal },
): Promise<void> {
  const res = await fetch(`${API}/products/stream`, { cache: 'no-store', headers: authHeaders(), signal: opts?.signal })
  if (!res.ok || !res.body) throw new Error(`Products stream error ${res.status}`)
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  let batch: Product[] = []
  let total = 0
  let loaded = 0
  let isFirst = true
  const FIRST = 120, BATCH = 600
  const flush = () => {
    if (!batch.length) return
    const chunk = batch; batch = []
    loaded += chunk.length
    onBatch({ batch: chunk, isFirst, loaded, total })
    isFirst = false
  }
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let nl: number
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl); buf = buf.slice(nl + 1)
      if (!line) continue
      const obj = JSON.parse(line)
      if (obj._meta) { total = obj._meta.total ?? 0; continue }
      batch.push(obj as Product)
    }
    if (isFirst ? batch.length >= FIRST : batch.length >= BATCH) flush()
  }
  flush()
}

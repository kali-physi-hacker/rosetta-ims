// Catalogue HITL review — shared domain for the board / room / commit surfaces.
// Data comes from the summary view (`?view=summary`, one decision-ready row per
// candidate) and per-candidate detail; writes go through the pipeline action
// endpoints. Server rejection reasons are surfaced verbatim.
import { API_BASE } from '@/lib/config'
import { authHeaders } from '@/lib/auth'

export const SAMPLE_SIZE = 12          // required random sample before bulk approval unlocks
export const PULL_THRESHOLD_PCT = 20   // |price delta| above this is never bulk-approvable
export const ACTION_CONCURRENCY = 4

export async function reviewApi<T = any>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...authHeaders(), ...(init?.headers ?? {}) },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = payload?.detail
    throw new Error(typeof detail === 'string' ? detail : detail?.message ?? `HTTP ${response.status}`)
  }
  return payload as T
}

// ── Summary view (`/intermediate?view=summary`) ──────────────────────────────

export interface SummaryItem {
  mastering_candidate_id: string
  catalogue_item_id: string
  created_at: string
  review_status: 'PENDING_REVIEW' | 'APPROVED' | 'APPROVED_WITH_OVERRIDE' | 'REJECTED' | 'NEEDS_CLARIFICATION'
  superseded_by: string | null
  page: number | null
  supplier_sku: string | null
  barcode: string | null
  name: string | null
  cost_amount: number | null
  cost_currency: string | null
  cost_basis: string | null
  offering_state: string | null
  variant_state: string | null
  canonical_sku: string | null
  variant_name: string | null
  family_key: string | null
  open_issues: number
  blocking_issues: number
  current_cost: number | null
  price_delta_pct: number | null
  selling_price: number | null
  selling_channel: string | null
  published: boolean
}

export interface RunIssue {
  validation_issue_id: string
  issue_code: string
  severity: string
  message: string
  resolution_status: string
  publish_blocking: boolean
  review_guidance: string | null
}

export interface RunSummary {
  ingestion_run_id: string
  counts: { total: number; by_review_status: Record<string, number> }
  run_issues: RunIssue[]
  items: SummaryItem[]
}

export interface VariantHit {
  sku_code: string
  name: string
  brand: string | null
  category: string | null
  species: string | null
  status: string
  offering_cost: number | null
  offering_source: 'offering' | 'legacy' | null
  selling_price: number | null
  selling_channel: string | null
}

export interface EvidenceCell { column_name: string | null; value: unknown }
export interface CandidateEvidence { raw_observation_id: string; page: number | null; raw_text: string | null; cells: EvidenceCell[] }
export interface CandidateDetail {
  candidate: Record<string, any>
  evidence: CandidateEvidence[]
  decisions: { review_decision_id: string; decision_type: string; review_status: string | null; actor_id: string; decided_at: string; reason: string | null }[]
}

export const fetchSummary = (runId: string) => reviewApi<RunSummary>(`/catalogues/ingestions/${runId}/intermediate?view=summary`)
export const fetchDetail = (runId: string, candidateId: string) => reviewApi<CandidateDetail>(`/catalogues/ingestions/${runId}/mastering-candidates/${candidateId}`)
export const searchVariants = (runId: string, q: string, limit = 8) =>
  reviewApi<{ results: VariantHit[] }>(`/catalogues/ingestions/${runId}/variant-search?q=${encodeURIComponent(q)}&limit=${limit}`)

// ── Grouping (mirrors the design spec's board groups) ────────────────────────

export type Group = 'ambiguous' | 'unmatched' | 'matched'

/** Latest revision of every candidate row — superseded revisions stay in history. */
export const latest = (items: SummaryItem[]) => items.filter(i => !i.superseded_by)

export function groupOf(item: SummaryItem): Group {
  if (item.variant_state === 'AMBIGUOUS' || item.offering_state === 'AMBIGUOUS') return 'ambiguous'
  if (item.variant_state === 'PROPOSED_MATCH' || item.variant_state === 'CONFIRMED_MATCH') return 'matched'
  return 'unmatched'
}

/** Matched rows whose price move is material — always reviewed individually. */
export const isPulled = (item: SummaryItem) =>
  item.price_delta_pct != null && Math.abs(item.price_delta_pct) > PULL_THRESHOLD_PCT

/** Bulk-approvable pool: clean matches, nothing blocking, nothing material. */
export const isBulkEligible = (item: SummaryItem) =>
  groupOf(item) === 'matched' && !isPulled(item) && item.blocking_issues === 0 && item.review_status === 'PENDING_REVIEW'

export const isDecided = (item: SummaryItem) => item.review_status !== 'PENDING_REVIEW'

// Deterministic sample: stable across refreshes for a run (hash of run + candidate),
// so the gate can't be reshuffled by reloading.
function hash(text: string): number {
  let h = 2166136261
  for (let i = 0; i < text.length; i++) { h ^= text.charCodeAt(i); h = Math.imul(h, 16777619) }
  return h >>> 0
}

export function sampleIds(runId: string, pool: SummaryItem[], size = SAMPLE_SIZE): Set<string> {
  const ranked = [...pool].sort(
    (a, b) => hash(runId + a.mastering_candidate_id) - hash(runId + b.mastering_candidate_id),
  )
  return new Set(ranked.slice(0, size).map(i => i.mastering_candidate_id))
}

// ── Actions ──────────────────────────────────────────────────────────────────

export const approveCandidate = (runId: string, id: string, reason: string) =>
  reviewApi(`/catalogues/ingestions/${runId}/mastering-candidates/${id}/review`, {
    method: 'POST',
    body: JSON.stringify({ review_status: 'APPROVED', reason }),
  })

export const decideCandidate = (runId: string, id: string, review_status: SummaryItem['review_status'], reason: string) =>
  reviewApi(`/catalogues/ingestions/${runId}/mastering-candidates/${id}/review`, {
    method: 'POST',
    body: JSON.stringify({ review_status, reason }),
  })

/** Correct the variant match — creates an immutable revision that supersedes this candidate. */
export const correctVariantMatch = (
  runId: string, id: string, reason: string,
  variant: { sku_code: string; name: string }, proposedName: string | null,
) =>
  reviewApi(`/catalogues/ingestions/${runId}/mastering-candidates/${id}/correct`, {
    method: 'POST',
    body: JSON.stringify({
      reason,
      product_variant_resolution: {
        state: 'CONFIRMED_MATCH',
        canonical_sku: variant.sku_code,
        product_variant_id: variant.sku_code,
        product_variant_name: variant.name,
        proposed_name: proposedName,
        product_family_id: null,
      },
    }),
  })

export const applyCandidate = (runId: string, id: string) =>
  reviewApi(`/catalogues/ingestions/${runId}/mastering-candidates/${id}/apply`, { method: 'POST', body: '{}' })

export const publishCandidate = (runId: string, id: string, version: string) =>
  reviewApi(`/catalogues/ingestions/${runId}/mastering-candidates/${id}/publish`, {
    method: 'POST',
    body: JSON.stringify({ publication_version: version }),
  })

export const resolveRunIssue = (runId: string, issueId: string, note: string) =>
  reviewApi(`/catalogues/ingestions/${runId}/validation-issues/${issueId}/resolve`, {
    method: 'POST',
    body: JSON.stringify({ resolution_status: 'ACCEPTED_AS_IS', resolution_note: note }),
  })

/** Run one action per item with bounded concurrency; collect per-item failures. */
export async function fanOut<T>(
  items: T[],
  action: (item: T) => Promise<unknown>,
  onProgress?: (done: number, total: number) => void,
): Promise<{ done: number; failures: { item: T; error: string }[] }> {
  const queue = [...items]
  const failures: { item: T; error: string }[] = []
  let done = 0
  async function worker() {
    for (let next = queue.shift(); next !== undefined; next = queue.shift()) {
      try {
        await action(next)
      } catch (e: any) {
        failures.push({ item: next, error: String(e?.message ?? e) })
      }
      done += 1
      onProgress?.(done, items.length)
    }
  }
  await Promise.all(Array.from({ length: Math.min(ACTION_CONCURRENCY, items.length) }, worker))
  return { done, failures }
}

// ── Formatting ───────────────────────────────────────────────────────────────

export const fmtMoney = (amount: number | null | undefined, currency = 'HKD') =>
  amount == null ? '—' : `${amount.toFixed(2)} ${currency}`

export const fmtDelta = (pct: number | null | undefined) =>
  pct == null ? null : `${pct > 0 ? '↑' : pct < 0 ? '↓' : '±'} ${Math.abs(pct).toFixed(1)}%`

/** Margin a reviewer sanity-checks: what's left of the selling price after this cost. */
export const marginPct = (cost: number | null, sell: number | null) =>
  cost == null || sell == null || sell === 0 ? null : Math.round((1 - cost / sell) * 100)

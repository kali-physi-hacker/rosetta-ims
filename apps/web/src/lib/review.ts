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
  /** Drafted to become a new product — about to mint a SKU when the run applies. */
  will_create?: boolean
  /** The SKU apply actually minted, once it has. */
  created_product_sku?: string | null
  draft_name?: string | null
  draft_category?: string | null
  /** What the cost is per — "can", "bag", "unit". Null when the catalogue's
   *  uom for this variant is junk ("#N/A", empty) and would read worse than
   *  nothing. */
  uom?: string | null
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
  /** Name-similarity 0..1, present when the hit came from the scorer rather than a literal match. */
  score?: number | null
  /** False for historical rows carrying a product name in the SKU column. */
  sku_valid?: boolean
  offering_cost: number | null
  offering_source: 'offering' | 'legacy' | null
  selling_price: number | null
  selling_channel: string | null
}

export interface DuplicateBlocker {
  kind: 'barcode' | 'name'
  sku_code: string
  name: string | null
  detail: string
}

export interface DuplicateSimilar {
  sku_code: string
  name: string | null
  brand: string | null
  category: string | null
  status: string
  score: number
  /** False for historical rows carrying a product name in the SKU column. */
  sku_valid?: boolean
}

export interface DuplicateCheck {
  /** Facts, not judgement — a barcode or identical name already owned. No override. */
  blockers: DuplicateBlocker[]
  similar: DuplicateSimilar[]
  top_score: number
  /** Above the threshold the reviewer must say what makes their row different. */
  reason_required: boolean
  threshold: number
}

export interface VariantDraft {
  name: string
  category: string
  brand?: string | null
  uom?: string | null
  pack_unit?: string | null
  storage_rule?: string | null
  duplicate_ack?: string | null
  checked_against?: { sku_code: string; name: string | null; score: number }[]
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
  // A drafted create has been dealt with — it leaves the "new to us" queue and
  // joins the rows waiting to be approved, carrying a "will create" marker.
  if (item.variant_state === 'CONFIRMED_CREATE') return 'matched'
  return 'unmatched'
}

/** Matched rows whose price move is material — always reviewed individually. */
export const isPulled = (item: SummaryItem) =>
  item.price_delta_pct != null && Math.abs(item.price_delta_pct) > PULL_THRESHOLD_PCT

/** Rows that will mint a new SKU. Never swept in bulk — each one is a new
 *  product entering the catalogue and deserves its own look. */
export const willCreate = (item: SummaryItem) =>
  item.will_create === true || (item.variant_state === 'CONFIRMED_CREATE' && !item.created_product_sku)

/** Bulk-approvable pool: clean matches, nothing blocking, nothing material. */
export const isBulkEligible = (item: SummaryItem) =>
  groupOf(item) === 'matched' && !isPulled(item) && !willCreate(item)
  && item.blocking_issues === 0 && item.review_status === 'PENDING_REVIEW'

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

/** Categories that can actually mint a SKU — the picker offers nothing else,
 *  so "category has no SKU digit" can never surface at apply time. */
export const fetchSkuCategories = () =>
  reviewApi<{ category: string; sku_digit: string | null }[]>('/category-rules')
    .then(rows => rows.filter(r => !!r.sku_digit))

export const checkDuplicates = (runId: string, name: string, barcode?: string | null) =>
  reviewApi<DuplicateCheck>(
    `/catalogues/ingestions/${runId}/duplicate-check?name=${encodeURIComponent(name)}`
    + (barcode ? `&barcode=${encodeURIComponent(barcode)}` : ''),
  )

/**
 * Draft a brand-new canonical product for this row.
 *
 * Nothing is created here — this records the intent as an immutable revision,
 * exactly like a match correction. The SKU is minted when the run is applied,
 * so an abandoned or rejected draft leaves nothing behind.
 */
export const correctToNewProduct = (
  runId: string, id: string, reason: string,
  draft: VariantDraft, proposedName: string | null,
) =>
  reviewApi(`/catalogues/ingestions/${runId}/mastering-candidates/${id}/correct`, {
    method: 'POST',
    body: JSON.stringify({
      reason,
      product_variant_resolution: {
        state: 'CONFIRMED_CREATE',
        canonical_sku: null,
        product_variant_id: null,
        product_variant_name: draft.name,
        proposed_name: proposedName,
        product_family_id: null,
        proposed_variant: draft,
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

/** "13.10 / can" — the unit is dropped when the catalogue does not know it. */
export const per = (uom: string | null | undefined) => (uom ? ` / ${uom}` : '')

export const fmtDelta = (pct: number | null | undefined) =>
  pct == null ? null : `${pct > 0 ? '↑' : pct < 0 ? '↓' : '±'} ${Math.abs(pct).toFixed(1)}%`

/** Margin a reviewer sanity-checks: what's left of the selling price after this cost. */
export const marginPct = (cost: number | null, sell: number | null) =>
  cost == null || sell == null || sell === 0 ? null : Math.round((1 - cost / sell) * 100)

// ── Run desk additions ───────────────────────────────────────────────────────

/** Structured decision reasons — one tap, auditable, never boilerplate. */
export const REASON_CHIPS = [
  'Matches the evidence',
  'Picked the cheaper offering',
  'Parse was wrong',
  'Supplier confirmed directly',
] as const

export interface ReceiptChange {
  sku_code: string | null
  variant_name: string | null
  uom?: string | null
  brand?: string | null
  weight?: string | null
  supplier_name: string | null
  supplier_sku: string | null
  old_unit_cost: number | null
  new_unit_cost: number | null
  delta_pct: number | null
  written_at: string | null
  is_current: boolean
}
/** A product this run brought into existence, with the evidence that justified it. */
export interface ReceiptCreated {
  sku_code: string
  name: string | null
  uom?: string | null
  category: string | null
  brand: string | null
  drafted_by: string | null
  decided_at: string | null
  duplicate_ack: string | null
  checked_against: { sku_code: string; name: string | null; score: number }[]
}

/** The run's published items in the golden-sample sheet's exact columns, for a
 *  regression diff against the hand-filled ground truth. */
export async function downloadGoldenCsv(runId: string) {
  const response = await fetch(`${API_BASE}/catalogues/ingestions/${runId}/receipt/golden.csv`, {
    headers: { ...authHeaders() },
  })
  if (!response.ok) throw new Error(`Export failed — HTTP ${response.status}`)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = `rosetta-published-${runId}.csv`
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export const fetchReceipt = (runId: string) =>
  reviewApi<{
    ingestion_run_id: string; count: number
    changes: ReceiptChange[]; created?: ReceiptCreated[]
  }>(`/catalogues/ingestions/${runId}/receipt`)

/** Search terms for auto-suggestions: the family key when the pipeline found
 * one, else the name's first meaningful words — never the raw supplier SKU,
 * which is exactly the search that dead-ends. */
export function suggestTerms(item: SummaryItem): string {
  // The server matches the whole phrase, so: two distinctive words from the
  // parsed NAME first (family keys are often bilingual and phrase-miss);
  // fall back to the family key when the name has nothing usable.
  const words = (source: string | null) =>
    (source ?? '').replace(/[^\p{L}\p{N} ]/gu, ' ').trim().split(/\s+/).filter(w => w.length > 2)
  const name = words(item.name)
  if (name.length >= 1) return name.slice(0, 2).join(' ')
  return words(item.family_key).slice(0, 2).join(' ')
}

export const isApproved = (item: SummaryItem) =>
  item.review_status === 'APPROVED' || item.review_status === 'APPROVED_WITH_OVERRIDE'

/** Staged = decided-approved but not yet published; the dock's cart. */
export const isStaged = (item: SummaryItem) => isApproved(item) && !item.published

// ── Run failures: plain-words layer over the failure envelope ────────────────

export interface RunStatus {
  ingestion_run_id: string
  supplier_id: number | null
  contract_id: string | null
  contract_version?: string | null
  status: string
  submitted_at: string
  started_at?: string | null
  completed_at: string | null
  items_extracted: number | null
  error_summary?: Record<string, any> | string | null
  retry_of?: string | null
  superseded_by_run?: string | null
  /** The supplier catalogue this run read. */
  source_filename?: string | null
  source_received_at?: string | null
  /** Source run uuid when this run re-read stored evidence instead of a file. */
  reparse_of?: string | null
}
export const TERMINAL_RUN_STATUSES = new Set(['completed', 'completed_with_warnings', 'failed', 'cancelled'])
export const fetchRunStatus = (runId: string) => reviewApi<RunStatus>(`/catalogues/ingestions/${runId}`)
/** Re-run the interpretation over evidence the run already holds.
 *  No provider call, no re-scan — for seeing a contract change land. */
export const reparseRun = (runId: string) =>
  reviewApi<{ ingestion_run_id: string; status: string }>(
    `/catalogues/ingestions/${runId}/reparse`,
    { method: 'POST', body: JSON.stringify({ from_stage: 'conformance' }) },
  )

export const retryRun = (runId: string) =>
  reviewApi<{ ingestion_run_id: string }>(`/catalogues/ingestions/${runId}/retry`, { method: 'POST' })

export type FailureStage = 'received' | 'reading' | 'understanding' | 'recording'
export interface FailureView {
  code: string
  sentence: string
  action: string
  stage: FailureStage
  stageWords: string
  retryable: boolean
  attempts: number
  detail: string | null
  raw: string
}

const lowerFirst = (text: string) => (text ? text.charAt(0).toLowerCase() + text.slice(1) : text)

const FAILURE_COPY: Record<string, {
  sentence: (message: string, attempts: number) => string
  action: string
  stage: FailureStage
  retryable: boolean
}> = {
  SOURCE_VERIFICATION_ERROR: {
    sentence: m => `The file couldn’t be opened — ${lowerFirst(m)}.`,
    action: 'Fix the file (remove the password / re-export it) and upload it again — retrying the same file will fail the same way.',
    stage: 'reading', retryable: false,
  },
  SOURCE_PAGE_READ_ERROR: {
    sentence: m => `A page of the document couldn’t be read — ${lowerFirst(m)}.`,
    action: 'Retry once — if it repeats, re-export the PDF and upload again.',
    stage: 'reading', retryable: true,
  },
  SPREADSHEET_SHEET_READ_ERROR: {
    sentence: m => `The spreadsheet couldn’t be read — ${lowerFirst(m)}.`,
    action: 'Check the workbook matches the contract’s sheet layout, then re-upload.',
    stage: 'reading', retryable: false,
  },
  TRANSIENT_PROVIDER_ERROR: {
    sentence: () => 'The reading service had a temporary problem.',
    action: 'Retry now — nothing about the file needs to change.',
    stage: 'understanding', retryable: true,
  },
  PROVIDER_ERROR: {
    sentence: () => 'The reading service rejected this document.',
    action: 'Retry once — if it repeats, the document probably needs contract attention.',
    stage: 'understanding', retryable: true,
  },
  EXTRACTION_EVIDENCE_ERROR: {
    sentence: (_m, attempts) => `The reader couldn’t make sense of the pages${attempts > 1 ? ` after ${attempts} attempts` : ''}.`,
    action: 'Retry once — recurring failures usually mean the document layout changed and the supplier contract needs an update.',
    stage: 'understanding', retryable: true,
  },
  EXTRACTION_CONFIGURATION_ERROR: {
    sentence: () => 'This supplier’s reading instructions are broken.',
    action: 'Fix the extraction profile / supplier contract — retrying won’t help.',
    stage: 'understanding', retryable: false,
  },
  RECORDED_CONTRACT_ERROR: {
    sentence: () => 'The run’s contract records don’t line up.',
    action: 'Engineering territory — copy the technical details and file it.',
    stage: 'recording', retryable: false,
  },
  INVALID_RUN_TRANSITION: {
    sentence: () => 'The run hit an internal state conflict.',
    action: 'Retry creates a clean new run; if it recurs, involve engineering.',
    stage: 'recording', retryable: true,
  },
  DUPLICATE_RUN_CLAIM: {
    sentence: () => 'Two workers tried to process this run at once.',
    action: 'Retry creates a clean new run.',
    stage: 'recording', retryable: true,
  },
  TERMINAL_RUN_REPLAY: {
    sentence: () => 'The run was replayed after it already finished.',
    action: 'Retry creates a clean new run.',
    stage: 'recording', retryable: true,
  },
  RUN_CANCELLED: {
    sentence: m => `Cancelled — ${lowerFirst(m)}.`,
    action: 'Informational — re-submit from the Catalogues page if it should run again.',
    stage: 'recording', retryable: false,
  },
  CATALOGUE_ORCHESTRATION_ERROR: {
    sentence: () => 'The run failed unexpectedly.',
    action: 'Retry once — then copy the technical details for engineering.',
    stage: 'recording', retryable: true,
  },
}

const STAGE_WORDS: Record<FailureStage, string> = {
  received: 'receiving the file',
  reading: 'reading the file',
  understanding: 'reading the pages',
  recording: 'recording the run',
}

/** Normalize any failure envelope (v1 two-field or v2) into display truth.
 * Old envelopes carry the ×N semicolon stutter — dedupe it here so history
 * renders as cleanly as new failures. */
export function failureInfo(errorSummary: RunStatus['error_summary']): FailureView | null {
  if (errorSummary == null) return null
  let env: Record<string, any>
  if (typeof errorSummary === 'string') {
    try { env = JSON.parse(errorSummary) } catch { env = { message: errorSummary } }
  } else {
    env = errorSummary
  }
  const code = String(env.error_code ?? 'UNKNOWN')
  let message = String(env.message ?? '')
  let attempts = typeof env.attempts === 'number' ? env.attempts : 1
  let detail = env.detail != null ? String(env.detail) : null
  if (env.attempts == null && message.includes(';')) {
    const parts = message.split(';').map(p => p.trim()).filter(Boolean)
    const unique = [...new Set(parts)]
    message = unique[0] ?? message
    attempts = parts.length
    if (unique.length > 1) detail = unique.slice(1).join('; ')
  }
  const copy = FAILURE_COPY[code]
  const stage = (env.stage as FailureStage) ?? copy?.stage ?? 'recording'
  return {
    code,
    sentence: copy ? copy.sentence(message, attempts) : (message || 'The run failed unexpectedly.'),
    action: copy?.action ?? 'Retry once — then copy the technical details for engineering.',
    stage,
    stageWords: STAGE_WORDS[stage] ?? STAGE_WORDS.recording,
    retryable: typeof env.retryable === 'boolean' ? env.retryable : copy?.retryable ?? false,
    attempts,
    detail,
    raw: message,
  }
}

// Catalogue re-parse — client types + API helpers.
// Re-derives catalogue-sourced fields for already-onboarded items from the retained
// text (no source image needed), returns a reviewable batch of per-field diffs, and
// applies only what the reviewer confirms. Nothing touches live cost data until confirm.
//
// Backend contract (fixed):
//   POST /catalogues/reparse/{scope}/{ref}        → Batch  (scope ∈ item|import|supplier)
//   GET  /catalogues/reparse/{batch_id}           → Batch
//   POST /catalogues/reparse/{batch_id}/confirm   → Batch & { applied, skipped }   body { change_ids }
//   POST /catalogues/reparse/{batch_id}/discard   → { ok: true }
import { authHeaders } from './auth'
import { skuToPath } from './sku'
import { API_BASE } from './config'

const API = API_BASE

export type ReparseScope = 'item' | 'import' | 'supplier'
export type ChangeStatus = 'pending' | 'confirmed' | 'rejected' | 'stale' | 'superseded'
export type FieldGroup = 'Pricing' | 'Identity' | 'Pack & quantity' | 'Classification'

export interface ReparseChange {
  id: number
  catalogue_item_id: number
  product_id: number
  committed: boolean            // true → this SKU is live (writing its cost touches live data)
  sku_code: string
  product_name: string
  import_id: number | null
  source_file: string | null    // the source catalogue file this item was extracted from
  field: string
  old_value: string | number | null
  new_value: string | number | null
  affects_cost: boolean
  eff_cost_before: number | null
  eff_cost_after: number | null
  status: ChangeStatus
}

// Per-item view (the per-item "old vs new" review). One ReparseField per display field
// (changed or not) so a card can show every captured field grouped and in context; changed
// fields carry a change_id + status so they can be confirmed/reflected individually.
export interface ReparseField {
  group: FieldGroup
  field: string                     // e.g. "units_per_pack"
  current: string | null            // value on the live/committed item today
  reparsed: string | null           // value produced by the re-parse
  changed: boolean
  affects_cost: boolean
  editable: boolean                 // recapturable field → its Re-parsed value can be hand-edited before confirm
  eff_cost_before: number | null
  eff_cost_after: number | null
  change_id: number | null          // set on changed fields (null on unchanged rows)
  status: ChangeStatus | null       // set on changed fields (null on unchanged rows)
}

export interface ReparseItem {
  catalogue_item_id: number
  product_id: number | null
  committed: boolean                // true → live SKU (writing its cost touches live data)
  sku_code: string | null
  product_name: string
  import_id: number | null
  source_file: string | null        // source catalogue file this item was extracted from
  changed_count: number             // total changed fields for this item
  change_ids: number[]              // PENDING change ids — the set to confirm for this item
  fields: ReparseField[]            // ALL display fields (changed + unchanged), in group order
}

export interface ReparseBatch {
  id: number
  scope_type: ReparseScope
  scope_ref: string
  supplier_name: string | null   // named supplier for supplier-scoped batches (null for item/import)
  parser_version: string
  mode: string
  status: string
  item_count: number            // items in scope (changed + unchanged)
  changed_count: number         // items/fields that changed
  created_at: string
  changes: ReparseChange[]       // flat changed-field rows (kept for the alt "changes table" view)
  items: ReparseItem[]           // per-item old-vs-new view — the primary review surface
}

export interface ConfirmResult extends ReparseBatch {
  applied: number
  skipped: number               // skipped = went stale between preview and confirm
}

async function unwrap<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error((body && (body.detail as string)) || `Request failed (${res.status})`)
  }
  return res.json() as Promise<T>
}

/** Encode the ref for the URL path. sku_codes can contain '/', so item refs are kept as
 *  real path segments (matches the /products/{sku:path} convention); ids are plain-encoded. */
function encodeRef(scope: ReparseScope, ref: string | number): string {
  return scope === 'item' ? skuToPath(String(ref)) : encodeURIComponent(String(ref))
}

export async function startReparse(scope: ReparseScope, ref: string | number): Promise<ReparseBatch> {
  const res = await fetch(`${API}/catalogues/reparse/${scope}/${encodeRef(scope, ref)}`, {
    method: 'POST', headers: authHeaders(),
  })
  return unwrap<ReparseBatch>(res)
}

export async function getReparseBatch(batchId: string | number): Promise<ReparseBatch> {
  const res = await fetch(`${API}/catalogues/reparse/${batchId}`, {
    cache: 'no-store', headers: authHeaders(),
  })
  return unwrap<ReparseBatch>(res)
}

/** The current / most-recent re-parse batch — what the "Re-parse" nav entry resolves to. null = none yet. */
export async function getLatestReparse(): Promise<ReparseBatch | null> {
  const res = await fetch(`${API}/catalogues/reparse/latest`, {
    cache: 'no-store', headers: authHeaders(),
  })
  const { batch } = await unwrap<{ batch: ReparseBatch | null }>(res)
  return batch
}

// ── The re-parse inbox — every open (in-progress) re-parse, resumable, plus SKU search across them ──
export interface OpenReparse {
  id: number
  scope_type: ReparseScope
  scope_ref: string
  supplier_id: number | null
  supplier_name: string | null
  title: string | null            // best human label: supplier / product / source file
  parser_version: string
  created_at: string
  changed_count: number           // live PENDING changes (not the frozen stage-time count)
  pending_items: number           // distinct SKUs with a pending change
}
export interface ReparseHit {
  catalogue_item_id: number
  batch_id: number                // the open batch this SKU lives in — jump straight there
  sku_code: string | null
  product_name: string
  supplier_id: number | null
  supplier_name: string | null
  changed_count: number
}
export interface OpenReparses {
  batches: OpenReparse[]
  items: ReparseHit[]             // search hits — populated only when a `q` is passed
}

// ── A supplier's uploaded catalogue files (imports) — for the "re-parse one file" launcher ──
export interface CatalogueFile {
  id: number
  supplier_id: number | null
  filename: string
  imported_at: string
  item_count: number
}

/** The catalogue files (imports) uploaded for one supplier, newest first — re-parse can scope to one. */
export async function getSupplierImports(supplierId: number): Promise<CatalogueFile[]> {
  const res = await fetch(`${API}/catalogues`, { cache: 'no-store', headers: authHeaders() })
  const all = await unwrap<CatalogueFile[]>(res)
  return all.filter(f => f.supplier_id === supplierId)
}

/** The re-parse inbox: open re-parses (optionally narrowed to one supplier) + SKU search hits (when q set). */
export async function getOpenReparses(params?: { supplier?: number | null; q?: string }): Promise<OpenReparses> {
  const qs = new URLSearchParams()
  if (params?.supplier != null) qs.set('supplier', String(params.supplier))
  if (params?.q?.trim()) qs.set('q', params.q.trim())
  const suffix = qs.toString() ? `?${qs}` : ''
  const res = await fetch(`${API}/catalogues/reparse/open${suffix}`, { cache: 'no-store', headers: authHeaders() })
  return unwrap<OpenReparses>(res)
}

/** Confirm (apply) changes. An empty array means "all pending" — but callers that honour
 *  local rejects should pass explicit ids. Returns the updated batch plus apply counts. */
export async function confirmReparse(batchId: string | number, changeIds: number[]): Promise<ConfirmResult> {
  const res = await fetch(`${API}/catalogues/reparse/${batchId}/confirm`, {
    method: 'POST', headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ change_ids: changeIds }),
  })
  return unwrap<ConfirmResult>(res)
}

/** Hand-set the value re-parse will save for one field on one in-review SKU, before confirm. An empty
 *  string clears the field. Returns the refreshed item card + the batch's new change count. No live write
 *  happens here — the value is applied only when the change is later confirmed. */
export async function editReparseField(
  batchId: string | number, catalogueItemId: number, field: string, value: string | null,
): Promise<{ item: ReparseItem; changed_count: number }> {
  const res = await fetch(`${API}/catalogues/reparse/${batchId}/field`, {
    method: 'PUT', headers: authHeaders({ 'Content-Type': 'application/json' }),
    body: JSON.stringify({ catalogue_item_id: catalogueItemId, field, value }),
  })
  return unwrap<{ item: ReparseItem; changed_count: number }>(res)
}

export async function discardReparse(batchId: string | number): Promise<{ ok: boolean }> {
  const res = await fetch(`${API}/catalogues/reparse/${batchId}/discard`, {
    method: 'POST', headers: authHeaders(),
  })
  return unwrap<{ ok: boolean }>(res)
}

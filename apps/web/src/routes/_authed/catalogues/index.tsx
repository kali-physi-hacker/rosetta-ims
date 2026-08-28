// Catalogues — the ingestion funnel. Files enter the evidence-first queued
// pipeline here (single file or a whole Region/Supplier folder, extracted 3 at
// a time); review happens in /catalogues/review. The legacy synchronous
// import + per-item matching flow is gone — the exception room replaced it.
import { C } from '@/lib/tokens'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { authHeaders } from '@/lib/auth'
import { toast } from '@/lib/toast'
import { API_BASE } from '@/lib/config'
import { reviewApi } from '@/lib/review'
import { IngestionProgress } from '@/components/IngestionProgress'
import type { RunStatus } from '@/lib/review'

const API = API_BASE
const MONO = 'ui-monospace, "SF Mono", Menlo, monospace'

interface Supplier { id: number; code: string; name: string }

// ── Batch upload (whole-folder) ──────────────────────────────────────────────
const BATCH_EXT = new Set(['pdf', 'xlsx', 'xls', 'csv', 'jpg', 'jpeg', 'png'])
// 'processing' = accepted (202) and queued/running server-side; 'done' means the
// ingestion RUN reached a terminal machine status — never just the upload.
type BatchStatus = 'queued' | 'uploading' | 'processing' | 'done' | 'error'
interface BatchFile {
  key: string
  file: File
  name: string
  supplierFolder: string
  supplierId: number | null
  /** Which document format to read the file with — required only when the
   * supplier publishes more than one SUPPORTED format (resolution refuses
   * to guess between layouts). Null = resolve from the supplier alone. */
  contractId: string | null
  status: BatchStatus
  itemCount: number | null
  error: string | null
  fmt: string | null
  supplierStatus: string | null   // pipeline machine status while processing/terminal
  runId: string | null            // ingestion run id returned by the queued pipeline
  sizeMB: number
  startedAt: number | null
  /** Live progress from the run, refreshed by the poller while it works. */
  progress: RunProgressFields | null
}

/** The subset of the run payload the progress indicator reads. */
type RunProgressFields = Pick<
  RunStatus,
  'status' | 'stage' | 'stage_label' | 'stage_started_at' | 'stage_index' | 'stage_count'
  | 'units_done' | 'units_total' | 'started_at'
>

const progressFrom = (run: any): RunProgressFields => ({
  status: run?.status ?? null,
  stage: run?.stage ?? null,
  stage_label: run?.stage_label ?? null,
  stage_started_at: run?.stage_started_at ?? null,
  stage_index: run?.stage_index ?? null,
  stage_count: run?.stage_count ?? null,
  units_done: run?.units_done ?? null,
  units_total: run?.units_total ?? null,
  started_at: run?.started_at ?? null,
})

// Path is Root / Region / Supplier / [Brand] / file — supplier is the 3rd segment.
function inferSupplierFolder(relPath: string): string {
  const parts = relPath.split('/').filter(Boolean)
  if (parts.length >= 3) return parts[2]
  if (parts.length === 2) return parts[1]
  return parts[0] ?? ''
}

function matchSupplierId(folder: string, suppliers: Supplier[]): number | null {
  const fn = folder.toLowerCase().replace('(reseller)', '').replace(' ltd', '').trim()
  if (!fn) return null
  for (const s of suppliers) {
    const name = (s.name ?? '').toLowerCase().trim()
    if (name && (name.includes(fn) || fn.includes(name))) return s.id
  }
  return null
}

const BATCH_BADGE: Record<BatchStatus, { bg: string; color: string; label: string }> = {
  queued:     { bg: C.monoBg, color: C.muted, label: 'Queued' },
  uploading:  { bg: '#DBEAFE', color: '#1E40AF', label: 'Uploading…' },
  processing: { bg: '#DBEAFE', color: '#1E40AF', label: 'Processing…' },
  done:       { bg: C.greenBg, color: C.green, label: 'Done' },
  error:      { bg: C.redBg, color: C.redInk, label: 'Failed' },
}

// ── Batch resume across refresh ──────────────────────────────────────────────
// File bytes can't be persisted, so a metadata-only snapshot of the batch goes
// to localStorage. After a refresh a banner offers to resume; re-picking the
// same files reconciles by name+size so already-extracted files are skipped,
// and in-flight runs reconnect directly by their durable run id.
const BATCH_SNAPSHOT_KEY = 'ims_batch_snapshot'
type SerializedBatchFile = Omit<BatchFile, 'file'>
interface BatchSnapshot { savedAt: number; files: SerializedBatchFile[] }
const fileMatchId = (f: { name: string; sizeMB: number }) => `${f.name}|${f.sizeMB.toFixed(4)}`
function serializeBatch(files: BatchFile[]): SerializedBatchFile[] {
  return files.map(({ file: _file, ...meta }) => meta)
}

interface RunRow {
  ingestion_run_id: string
  contract_id: string | null
  status: string
  submitted_at: string
  items_extracted: number | null
  /** Catalogue product rows — the BO-facing figure; items_extracted counts
   * raw observations including page text lines. */
  product_rows?: number | null
}
const RUN_PILL: Record<string, { bg: string; color: string }> = {
  completed: { bg: C.greenBg, color: C.green },
  completed_with_warnings: { bg: '#DBEAFE', color: '#1E40AF' },
  failed: { bg: C.redBg, color: C.redInk },
  processing: { bg: C.warnBg, color: C.amberInk },
  queued: { bg: C.monoBg, color: C.sub },
}

export const Route = createFileRoute('/_authed/catalogues/')({ component: CataloguesPage })

/** Suppliers whose catalogue Rosetta reads directly, because they publish no file.
 *
 * Listed beside the upload box as the OTHER way a catalogue can arrive, so the
 * screen answers "where is this supplier's price list?" for both kinds. Adding
 * a connector later means adding a row here.
 */
const CONNECTORS = [
  {
    key: 'royal-canin',
    name: 'Royal Canin',
    where: 'Hong Kong webshop · your clinic account',
    why: 'Royal Canin issues no price list — Rosetta reads their catalogue and your trade prices straight from their system, and files each product under the account that invoices it: veterinary or retail.',
    endpoint: '/catalogues/connectors/royal-canin/capture',
  },
] as const

/** One per-product note, and what kind of problem it actually is. */
type ConnectorWarning = { code: string; message: string }

/** What one capture did for one supplier. */
type ConnectorResult = {
  supplier: string
  product_range: string
  status: 'submitted' | 'unchanged' | 'refused'
  rows: number
  message: string
  refusal?: string | null
  releasable?: boolean
  warnings?: ConnectorWarning[]
}

/** How each kind of per-product note reads, and what it asks of a person.
 *
 * The count used to be printed under a single sentence about dual-filing, so a
 * product with no price for our account was reported as a channel problem.
 * Three different problems want three different answers. */
const WARNING_KINDS: { code: string; say: (n: number) => string }[] = [
  {
    code: 'DUAL_LISTED',
    say: n => `${n} product(s) Royal Canin files under both a veterinary and a retail channel — review which account they belong to.`,
  },
  {
    code: 'NO_PRICE',
    say: n => `${n} product(s) carry no price for our account. They are still queued, and the review board holds each one until a price is confirmed.`,
  },
  {
    code: 'NO_SUPPLIER_CODE',
    say: n => `${n} product(s) had no Royal Canin item number and were left out — there is nothing to order against.`,
  },
]

/** One fetchable supplier: press it and the catalogue is read now.
 *
 * Four outcomes, all shown plainly, and per supplier — Royal Canin invoices
 * veterinary and retail separately, so one press can queue one and refuse the
 * other. A CHANGED catalogue becomes a run like any upload. An UNCHANGED one
 * queues nothing — re-reading is not new work, and a run per press would bury
 * the review board in identical rows. A read that came back SHORT refuses,
 * because a half-finished fetch looks exactly like the supplier discontinuing
 * half their range; releasing it is a deliberate second press by someone who
 * knows the products really are gone. A read that came back EMPTY refuses
 * outright and cannot be released: an account whose whole range vanished while
 * the other's stayed means the shop re-filed its products, not that it stopped
 * selling to us.
 */
function ConnectorSource({
  connector, onQueued,
}: { connector: (typeof CONNECTORS)[number]; onQueued: () => void }) {
  const [busy, setBusy] = useState(false)
  const [shortRead, setShortRead] = useState<string | null>(null)
  const [results, setResults] = useState<ConnectorResult[]>([])

  async function fetchNow(force: boolean) {
    setBusy(true)
    try {
      const res = await fetch(`${API}${connector.endpoint}`, {
        method: 'POST',
        headers: { ...authHeaders(), 'Content-Type': 'application/json' },
        body: JSON.stringify({ force_incomplete: force }),
      })
      const body = await res.json().catch(() => ({}))
      if (res.status === 409) {
        // Nothing at all was queued. The refused read's per-supplier outcomes
        // still come back, so a supplier that was merely unchanged is shown
        // rather than lost behind the banner.
        setShortRead(body?.detail?.message ?? `${connector.name} returned a short catalogue.`)
        setResults((body?.detail?.results ?? []) as ConnectorResult[])
        return
      }
      if (!res.ok) throw new Error(body?.detail?.message ?? body?.detail ?? `Fetch failed (${res.status})`)
      setShortRead(null)
      const outcomes = (body.results ?? []) as ConnectorResult[]
      setResults(outcomes)
      if (body.status === 'submitted') {
        toast.success(body.message ?? `Queued ${body.rows} products from ${connector.name}`)
        onQueued()
        // One supplier can be queued while the other is refused. Saying so
        // out loud, because the green toast otherwise reads as "all done".
        const refused = outcomes.filter(o => o.status === 'refused')
        if (refused.length) toast.info(refused.map(o => o.refusal ?? o.message).join(' '))
      } else {
        toast.info(body.message ?? `${connector.name}’s catalogue is unchanged`)
      }
    } catch (error) {
      toast.error(String((error as Error)?.message ?? error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div style={{ border: `1px solid ${C.line}`, borderRadius: 10, background: 'white', padding: '14px 16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 260 }}>
          <div style={{ fontSize: 13, fontWeight: 700, color: C.ink }}>{connector.name}</div>
          <div style={{ fontSize: 11.5, color: C.faint, marginTop: 1 }}>{connector.where}</div>
          <div style={{ fontSize: 12, color: C.sub, marginTop: 5, maxWidth: 620 }}>{connector.why}</div>
        </div>
        <button
          onClick={() => fetchNow(false)}
          disabled={busy}
          style={{
            background: busy ? C.line : C.indigo, color: busy ? C.knobOff : 'white',
            border: 'none', borderRadius: 7, padding: '8px 18px',
            fontSize: 12.5, fontWeight: 650, cursor: busy ? 'wait' : 'pointer', whiteSpace: 'nowrap',
          }}
        >
          {busy ? 'Reading…' : '⤓ Fetch catalogue'}
        </button>
      </div>

      {/* Royal Canin invoices veterinary and retail separately, so one read
          reports once per supplier — each with its own outcome. Shown beside a
          refusal too: one supplier can be queued while the other is held. */}
      {results.length > 0 && (
        <div style={{ marginTop: 10, display: 'grid', gap: 6 }}>
          {results.map(result => (
            <div key={result.product_range} style={{ display: 'flex', gap: 8, alignItems: 'baseline', fontSize: 11.5 }}>
              <span style={{
                fontSize: 10, fontWeight: 700, letterSpacing: 0.3, padding: '2px 7px', borderRadius: 20,
                background: result.status === 'submitted' ? C.primaryBg : result.status === 'refused' ? C.warnBg : C.monoBg,
                color: result.status === 'submitted' ? C.indigoInk : result.status === 'refused' ? C.amberInk : C.muted,
              }}>
                {result.status === 'submitted' ? `${result.rows} QUEUED`
                  : result.status === 'refused' ? 'HELD' : 'UNCHANGED'}
              </span>
              <span style={{ color: C.sub }}>{result.supplier}</span>
              <span style={{ color: C.faint }}>{result.message}</span>
            </div>
          ))}
          {WARNING_KINDS.map(kind => {
            const count = results.flatMap(r => r.warnings ?? []).filter(w => w.code === kind.code).length
            if (!count) return null
            return (
              <div key={kind.code} style={{ fontSize: 11, color: C.amberInk, background: C.warnBg, border: '1px solid #FDE68A', borderRadius: 7, padding: '6px 10px' }}>
                {kind.say(count)}
              </div>
            )
          })}
        </div>
      )}

      {shortRead && (
        <div style={{ background: C.warnBg, border: '1px solid #FDE68A', borderRadius: 9, padding: '10px 14px', marginTop: 10, fontSize: 12.5, color: C.amberInk }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Nothing was queued — this read looks incomplete</div>
          <div style={{ lineHeight: 1.5 }}>{shortRead}</div>
          <div style={{ display: 'flex', gap: 8, marginTop: 10, flexWrap: 'wrap' }}>
            <button
              onClick={() => fetchNow(false)}
              disabled={busy}
              style={{ background: 'white', color: C.amberInk, border: '1px solid #FDE68A', borderRadius: 6, padding: '5px 12px', fontSize: 12, fontWeight: 700, cursor: 'pointer' }}
            >
              ↻ Try the read again
            </button>
            {/* Offered only for a SHORT read, where the products really may be
                gone. An empty range is a filing change at the shop's end and
                no press should be able to publish it as a delisting. */}
            {results.some(r => r.releasable) && (
              <button
                onClick={() => fetchNow(true)}
                disabled={busy}
                style={{ background: 'none', color: C.muted, border: `1px solid ${C.line}`, borderRadius: 6, padding: '5px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}
              >
                These products really are gone — queue it anyway
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

/** One SUPPORTED document format a supplier publishes. */
interface SupplierContractOption { contract_id: string; contract_version: string; format_name: string; document_type: string }

function CataloguesPage() {
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  // SUPPORTED formats per supplier id. Suppliers with one entry resolve
  // automatically; with more, each file needs a format picked before upload.
  const [supplierContracts, setSupplierContracts] = useState<Record<string, SupplierContractOption[]>>({})
  const [batchFiles, setBatchFiles] = useState<BatchFile[]>([])
  const [batchRunning, setBatchRunning] = useState(false)
  const [batchSupplierId, setBatchSupplierId] = useState<number | null>(null)
  const [dragOver, setDragOver] = useState(false)
  const [resumeSnap, setResumeSnap] = useState<BatchSnapshot | null>(null)
  const batchInputRef = useRef<HTMLInputElement>(null)        // folder picker (webkitdirectory)
  const batchFilesInputRef = useRef<HTMLInputElement>(null)   // plain multi-file picker
  const batchCancelRef = useRef(false)

  const runs = useQuery({
    queryKey: ['review-runs'],
    queryFn: () => reviewApi<RunRow[]>('/catalogues/ingestions/run_ids'),
    refetchInterval: batchFiles.some(f => f.status === 'processing') ? 10_000 : false,
  })
  const recentRuns = useMemo(
    () => [...(runs.data ?? [])]
      .sort((a, b) => (b.submitted_at ?? '').localeCompare(a.submitted_at ?? ''))
      .slice(0, 8),
    [runs.data],
  )

  useEffect(() => {
    fetch(`${API}/suppliers`, { headers: authHeaders() })
      .then(r => r.json())
      .then(data => setSuppliers(Array.isArray(data) ? data : data.items ?? []))
      .catch(() => setSuppliers([]))
    fetch(`${API}/catalogues/supplier-contracts`, { headers: authHeaders() })
      .then(r => r.json())
      .then(data => setSupplierContracts(data.suppliers ?? {}))
      .catch(() => setSupplierContracts({}))
  }, [])

  // Enable folder selection on the hidden input (non-standard attrs, set via ref).
  useEffect(() => {
    const el = batchInputRef.current
    if (el) { el.setAttribute('webkitdirectory', ''); el.setAttribute('directory', '') }
  }, [])

  // Persist a metadata snapshot of the live batch so it survives a refresh.
  useEffect(() => {
    if (typeof window === 'undefined' || batchFiles.length === 0) return
    try {
      localStorage.setItem(BATCH_SNAPSHOT_KEY, JSON.stringify({ savedAt: Date.now(), files: serializeBatch(batchFiles) }))
    } catch { /* quota / private mode — non-fatal */ }
  }, [batchFiles])

  // On first load, recover any unfinished batch; in-flight runs reconnect by run id.
  useEffect(() => {
    if (typeof window === 'undefined') return
    try {
      const raw = localStorage.getItem(BATCH_SNAPSHOT_KEY)
      if (!raw) return
      const snap = JSON.parse(raw) as BatchSnapshot
      const remaining = (snap.files ?? []).filter(f => f.status !== 'done')
      if (remaining.length > 0) {
        setResumeSnap(snap)
        for (const file of remaining) {
          if (file.status === 'processing' && file.runId) void pollRecoveredRun(file.key, file.runId)
        }
      } else {
        localStorage.removeItem(BATCH_SNAPSHOT_KEY)
      }
    } catch { localStorage.removeItem(BATCH_SNAPSHOT_KEY) }
  }, [])

  // Warn before leaving while uploads or queued runs are in flight.
  useEffect(() => {
    if (typeof window === 'undefined') return
    const active = batchRunning || batchFiles.some(file => file.status === 'processing')
    if (!active) return
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); e.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [batchRunning, batchFiles])

  function discardResume() {
    setResumeSnap(null)
    if (typeof window !== 'undefined') localStorage.removeItem(BATCH_SNAPSHOT_KEY)
  }

  // Set the supplier for the whole batch (overrides folder-name inference).
  // A changed supplier invalidates any format choice made for the old one.
  function setBatchSupplier(id: number | null) {
    setBatchSupplierId(id)
    if (id != null) setBatchFiles(prev => prev.map(f => ({ ...f, supplierId: id, contractId: null })))
  }

  function handleBatchSelect(e: React.ChangeEvent<HTMLInputElement>) {
    ingestFiles(Array.from(e.target.files ?? []))
  }
  function handleDrop(e: React.DragEvent) {
    e.preventDefault(); setDragOver(false)
    if (!batchRunning) ingestFiles(Array.from(e.dataTransfer.files ?? []))
  }

  function ingestFiles(files: File[]) {
    let skipped = 0
    const picked: BatchFile[] = []
    for (const f of files) {
      const ext = (f.name.split('.').pop() ?? '').toLowerCase()
      if (f.name.startsWith('.') || !BATCH_EXT.has(ext)) { skipped++; continue }
      const relPath = (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name
      const folder = inferSupplierFolder(relPath)
      picked.push({
        key: `${relPath}:${f.size}`,
        file: f, name: f.name,
        supplierFolder: folder,
        supplierId: batchSupplierId ?? matchSupplierId(folder, suppliers),
        contractId: null,
        status: 'queued', itemCount: null, error: null,
        fmt: null, supplierStatus: null, runId: null,
        sizeMB: f.size / 1e6, startedAt: null, progress: null,
      })
    }
    picked.sort((a, b) =>
      a.supplierFolder.localeCompare(b.supplierFolder) || a.name.localeCompare(b.name))

    // Resuming an interrupted batch: files that already extracted last session
    // are marked done by name+size so they don't re-run as duplicate ingestions.
    if (resumeSnap) {
      const doneById = new Map<string, SerializedBatchFile>()
      for (const sf of resumeSnap.files) {
        if (sf.status === 'done') doneById.set(fileMatchId(sf), sf)
      }
      for (const p of picked) {
        const snap = doneById.get(fileMatchId(p))
        if (snap) {
          p.status = 'done'; p.error = null
          p.itemCount = snap.itemCount; p.fmt = snap.fmt
          p.supplierStatus = snap.supplierStatus; p.runId = snap.runId
        }
      }
      const remaining = picked.filter(p => p.status !== 'done').length
      setResumeSnap(null)
      toast.info(remaining > 0 ? `Resumed — ${remaining} file(s) left to scan` : 'All files already scanned')
    }

    setBatchFiles(picked)
    if (skipped > 0) toast.info(`${skipped} unsupported file(s) skipped`)
  }

  async function uploadOne(bf: BatchFile) {
    const startedAt = Date.now()
    setBatchFiles(prev => prev.map(x => x.key === bf.key ? { ...x, status: 'uploading', error: null, startedAt } : x))
    // The queued pipeline REQUIRES a supplier identity to resolve the
    // supplier-source contract.
    if (bf.supplierId == null) {
      setBatchFiles(prev => prev.map(x => x.key === bf.key
        ? { ...x, status: 'error', error: 'Supplier required — assign a supplier before ingesting' }
        : x))
      return
    }
    // A supplier with several supported formats needs the file's format named
    // up front — resolution refuses to guess between layouts.
    const formats = supplierContracts[String(bf.supplierId)] ?? []
    if (formats.length > 1 && !bf.contractId) {
      setBatchFiles(prev => prev.map(x => x.key === bf.key
        ? { ...x, status: 'error', error: 'This supplier publishes more than one catalogue format — pick which one this file is' }
        : x))
      return
    }
    try {
      const fd = new FormData()
      fd.append('file', bf.file)
      fd.append('supplier_id', String(bf.supplierId))
      if (bf.contractId) {
        fd.append('contract_id', bf.contractId)
        fd.append('contract_version', formats.find(f => f.contract_id === bf.contractId)?.contract_version ?? 'v1')
      }
      const res = await fetch(`${API}/catalogues/ingestions`, { method: 'POST', body: fd, headers: authHeaders() })
      const data = await res.json().catch(() => ({}))
      if (!res.ok) {
        setBatchFiles(prev => prev.map(x => x.key === bf.key
          ? { ...x, status: 'error', error: (typeof data.detail === 'string' ? data.detail : data.detail?.message) ?? `HTTP ${res.status}` }
          : x))
        return
      }
      // 202 means STORED AND QUEUED only — never a completed ingestion. Track
      // the run and poll its machine status to a terminal state.
      setBatchFiles(prev => prev.map(x => x.key === bf.key
        ? { ...x, status: 'processing', itemCount: null,
            runId: data.ingestion_run_id ?? null,
            fmt: data.document_type ?? null,
            supplierStatus: data.contract_id ? `queued (${data.contract_id})` : 'queued' }
        : x))
      if (data.ingestion_run_id) void pollIngestionRun(bf.key, data.ingestion_run_id)
    } catch {
      setBatchFiles(prev => prev.map(x => x.key === bf.key ? { ...x, status: 'error', error: 'Network error' } : x))
    }
  }

  // Poll one queued run (fire-and-forget; does not hold an upload-pool slot)
  // until the worker drives it to a terminal machine status.
  async function pollIngestionRun(key: string, runId: string) {
    const TERMINAL: Record<string, { status: BatchStatus; note: (d: any) => string | null }> = {
      completed:               { status: 'done',  note: () => null },
      completed_with_warnings: { status: 'done',  note: () => 'completed with warnings — review pipeline issues' },
      failed:                  { status: 'error', note: d => d?.error_summary?.message ?? d?.error_summary ?? 'ingestion failed' },
      cancelled:               { status: 'error', note: () => 'ingestion was cancelled' },
    }
    const DEADLINE = Date.now() + 45 * 60_000   // dense catalogues can take a while
    while (Date.now() < DEADLINE) {
      await new Promise(resolve => setTimeout(resolve, 2500))
      try {
        const res = await fetch(`${API}/catalogues/ingestions/${runId}`, { headers: authHeaders() })
        if (!res.ok) continue
        const run = await res.json()
        const terminal = TERMINAL[run.status]
        if (!terminal) {
          setBatchFiles(prev => prev.map(x => x.key === key
            ? { ...x, supplierStatus: run.status, progress: progressFrom(run) }
            : x))
          continue
        }
        setBatchFiles(prev => prev.map(x => x.key === key
          ? { ...x, status: terminal.status,
              itemCount: run.product_rows ?? run.items_extracted ?? null,
              supplierStatus: run.status,
              progress: null,
              error: terminal.status === 'error' ? String(terminal.note(run) ?? 'ingestion failed') : x.error }
          : x))
        return
      } catch { /* transient poll failure — keep polling until the deadline */ }
    }
    setBatchFiles(prev => prev.map(x => x.key === key
      ? { ...x, status: 'error', error: 'Timed out waiting for the ingestion run — check the run status manually' }
      : x))
  }

  // After a refresh, reconnect directly to the durable run ID saved in the
  // snapshot instead of asking for a re-upload (which would duplicate the run).
  async function pollRecoveredRun(key: string, runId: string) {
    const deadline = Date.now() + 45 * 60_000
    while (Date.now() < deadline) {
      try {
        const res = await fetch(`${API}/catalogues/ingestions/${runId}`, { headers: authHeaders() })
        if (res.ok) {
          const run = await res.json()
          const terminal = ['completed', 'completed_with_warnings', 'failed', 'cancelled'].includes(run.status)
          setResumeSnap(previous => {
            if (!previous) return previous
            const files = previous.files.map(file => file.key === key ? {
              ...file,
              status: run.status === 'completed' || run.status === 'completed_with_warnings' ? 'done' as BatchStatus : terminal ? 'error' as BatchStatus : 'processing' as BatchStatus,
              supplierStatus: run.status,
              itemCount: run.product_rows ?? run.items_extracted ?? file.itemCount,
              error: run.status === 'failed'
                ? String(run.error_summary?.message ?? run.error_summary ?? 'ingestion failed')
                : run.status === 'cancelled' ? 'ingestion was cancelled' : file.error,
            } : file)
            const snapshot = { ...previous, savedAt: Date.now(), files }
            localStorage.setItem(BATCH_SNAPSHOT_KEY, JSON.stringify(snapshot))
            return snapshot
          })
          if (terminal) return
        }
      } catch { /* transient reconnect failure */ }
      await new Promise(resolve => setTimeout(resolve, 2500))
    }
  }

  // Core runner — uploads the given files through a small concurrency pool.
  async function processFiles(todo: BatchFile[]) {
    if (batchRunning || todo.length === 0) return
    setBatchRunning(true)
    batchCancelRef.current = false
    const keys = new Set(todo.map(t => t.key))
    setBatchFiles(prev => prev.map(x => keys.has(x.key) ? { ...x, status: 'queued', error: null } : x))
    const CONCURRENCY = 3
    let idx = 0
    const worker = async () => {
      while (!batchCancelRef.current) {
        const i = idx++
        if (i >= todo.length) return
        await uploadOne(todo[i])
      }
    }
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, todo.length) }, worker))
    setBatchRunning(false)
    runs.refetch()
  }

  const runBatch = () => processFiles(batchFiles.filter(b => b.status === 'queued' || b.status === 'error'))
  const retryAllFailed = () => processFiles(batchFiles.filter(b => b.status === 'error'))
  const retrySupplier = (folder: string) =>
    processFiles(batchFiles.filter(b => b.supplierFolder === folder && b.status === 'error'))
  const redoFile = (key: string) => {
    const f = batchFiles.find(b => b.key === key)
    if (f) processFiles([f])
  }
  function cancelBatch() { batchCancelRef.current = true }
  function clearBatch() {
    if (batchRunning) return
    setBatchFiles([])
    if (typeof window !== 'undefined') localStorage.removeItem(BATCH_SNAPSHOT_KEY)
    if (batchInputRef.current) batchInputRef.current.value = ''
    if (batchFilesInputRef.current) batchFilesInputRef.current.value = ''
  }

  const batchStats = useMemo(() => ({
    total: batchFiles.length,
    queued: batchFiles.filter(b => b.status === 'queued').length,
    uploading: batchFiles.filter(b => b.status === 'uploading' || b.status === 'processing').length,
    done: batchFiles.filter(b => b.status === 'done').length,
    error: batchFiles.filter(b => b.status === 'error').length,
    items: batchFiles.reduce((sum, b) => sum + (b.itemCount ?? 0), 0),
  }), [batchFiles])

  const batchGroups = useMemo(() => {
    const groups = new Map<string, BatchFile[]>()
    for (const b of batchFiles) {
      const list = groups.get(b.supplierFolder) ?? []
      list.push(b)
      groups.set(b.supplierFolder, list)
    }
    return Array.from(groups.entries()).map(([folder, files]) => ({
      folder,
      files,
      matched: files.some(f => f.supplierId != null),
      total: files.length,
      done: files.filter(f => f.status === 'done').length,
      error: files.filter(f => f.status === 'error').length,
      items: files.reduce((sum, f) => sum + (f.itemCount ?? 0), 0),
    }))
  }, [batchFiles])

  return (
    <div style={{ padding: 24, maxWidth: 1100 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, marginBottom: 14, flexWrap: 'wrap' }}>
        <h1 style={{ fontSize: 18, fontWeight: 800, color: C.ink, margin: 0 }}>Catalogues</h1>
        <span style={{ fontSize: 12.5, color: C.muted }}>
          Upload supplier price lists into the evidence-first pipeline — review and commit in
        </span>
        <Link to="/catalogues/review" style={{ fontSize: 12.5, fontWeight: 650, color: C.indigoStrong, textDecoration: 'none' }}>Review →</Link>
      </div>

      {/* Resume an interrupted batch (recovered after a refresh) */}
      {resumeSnap && batchFiles.length === 0 && (() => {
        const done = resumeSnap.files.filter(f => f.status === 'done').length
        const remaining = resumeSnap.files.length - done
        return (
          <div style={{ background: '#FFFBEB', border: '1px solid #FCD34D', borderRadius: 12, padding: '14px 18px', marginBottom: 16, display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 240 }}>
              <div style={{ fontSize: 13, fontWeight: 700, color: C.amberInk }}>Unfinished batch from a previous session</div>
              <div style={{ fontSize: 12, color: C.sub, marginTop: 2 }}>
                {done} of {resumeSnap.files.length} files finished · {remaining} left. Re-pick the same files/folder — finished ones are skipped automatically; in-flight runs reconnect on their own.
              </div>
            </div>
            <label style={{ fontSize: 12, fontWeight: 600, color: 'white', background: C.indigo, borderRadius: 7, padding: '8px 16px', cursor: 'pointer' }}>
              Re-pick files
              <input type="file" multiple onChange={handleBatchSelect} style={{ display: 'none' }} />
            </label>
            <button onClick={discardResume} style={{ fontSize: 12, fontWeight: 600, color: C.muted, background: 'white', border: `1px solid ${C.line}`, borderRadius: 7, padding: '8px 14px', cursor: 'pointer' }}>Discard</button>
          </div>
        )
      })()}

      {/* Upload panel */}
      <div style={{ background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, padding: 18 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 12, flexWrap: 'wrap' }}>
          <b style={{ fontSize: 13.5, color: C.ink }}>Upload price lists</b>
          <select
            value={batchSupplierId ?? ''}
            onChange={e => setBatchSupplier(e.target.value ? Number(e.target.value) : null)}
            style={{ marginLeft: 'auto', border: `1px solid ${C.line}`, borderRadius: 8, padding: '7px 10px', fontSize: 12.5, background: 'white', color: C.ink }}
          >
            <option value="">Supplier: auto (by folder)</option>
            {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </div>

        <div
          onDragOver={e => { e.preventDefault(); if (!batchRunning) setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          style={{
            border: `2px dashed ${dragOver ? C.indigo : C.knobOff}`, borderRadius: 10,
            background: dragOver ? C.primaryBg : C.wash, padding: 22, textAlign: 'center',
            transition: 'all 0.12s',
          }}>
          <p style={{ fontSize: 13, color: C.sub, margin: '0 0 10px', fontWeight: 500 }}>
            {dragOver ? 'Drop to upload' : 'Drag files here, or'}
          </p>
          <div style={{ display: 'inline-flex', gap: 8, flexWrap: 'wrap', justifyContent: 'center' }}>
            <label style={{ fontSize: 12, fontWeight: 600, color: batchRunning ? C.knobOff : 'white', background: batchRunning ? C.line : C.indigo, borderRadius: 7, padding: '8px 16px', cursor: batchRunning ? 'default' : 'pointer' }}>
              📄 Choose files
              <input ref={batchFilesInputRef} type="file" multiple disabled={batchRunning} onChange={handleBatchSelect} style={{ display: 'none' }} />
            </label>
            <label style={{ fontSize: 12, fontWeight: 600, color: batchRunning ? C.knobOff : C.indigoInk, background: batchRunning ? C.monoBg : 'white', border: '1px solid #C7D2FE', borderRadius: 7, padding: '8px 16px', cursor: batchRunning ? 'default' : 'pointer' }}
              title="Pick a whole Region/Supplier folder. Your browser shows its own 'Upload N files?' prompt for folders.">
              📁 Choose folder
              <input ref={batchInputRef} type="file" multiple disabled={batchRunning} onChange={handleBatchSelect} style={{ display: 'none' }} />
            </label>
          </div>
          <p style={{ fontSize: 11, color: C.faint, marginTop: 10 }}>
            PDF · Excel · CSV · JPG · PNG. Handles one file or a whole batch — files extract 3 at a time; review continues in the Review board.
          </p>
        </div>

        {/* The other way a catalogue arrives: some suppliers publish no file at
            all, so Rosetta reads their system instead. Same pipeline from here
            on — the rows land in the Review board exactly like an upload. */}
        <div style={{ marginTop: 18, borderTop: `1px solid ${C.line}`, paddingTop: 16 }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, flexWrap: 'wrap', marginBottom: 10 }}>
            <b style={{ fontSize: 13.5, color: C.ink }}>Or fetch from a supplier system</b>
            <span style={{ fontSize: 12, color: C.muted }}>
              No file to upload — Rosetta reads the catalogue directly and queues what changed.
            </span>
          </div>
          <div style={{ display: 'grid', gap: 10 }}>
            {CONNECTORS.map(connector => (
              <ConnectorSource key={connector.key} connector={connector} onQueued={() => runs.refetch()} />
            ))}
          </div>
        </div>

        {batchFiles.length > 0 && (
          <div style={{ marginTop: 14 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 10 }}>
              {!batchRunning && batchStats.queued > 0 && (
                <button onClick={runBatch} style={{ background: C.indigo, color: 'white', border: 'none', borderRadius: 6, padding: '7px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
                  ▶ Start ({batchStats.queued}{batchStats.error > 0 ? ` + ${batchStats.error} retry` : ''})
                </button>
              )}
              {!batchRunning && batchStats.queued === 0 && batchStats.error > 0 && (
                <button onClick={retryAllFailed} style={{ background: '#F59E0B', color: 'white', border: 'none', borderRadius: 6, padding: '7px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>
                  ↻ Retry all failed ({batchStats.error})
                </button>
              )}
              {batchRunning && (
                <button onClick={cancelBatch} style={{ background: C.redBg, color: C.redInk, border: 'none', borderRadius: 6, padding: '7px 18px', fontSize: 13, fontWeight: 600, cursor: 'pointer' }}>■ Cancel</button>
              )}
              {!batchRunning && (
                <button onClick={clearBatch} style={{ background: 'white', color: C.muted, border: `1px solid ${C.line}`, borderRadius: 6, padding: '7px 14px', fontSize: 12.5, fontWeight: 600, cursor: 'pointer' }}>Clear</button>
              )}
              <span style={{ fontSize: 12, color: C.muted, marginLeft: 'auto', display: 'flex', gap: 12, flexWrap: 'wrap' }}>
                <span><strong>{batchStats.total}</strong> files</span>
                <span style={{ color: '#16A34A' }}>{batchStats.done} done</span>
                {batchStats.uploading > 0 && <span style={{ color: '#1E40AF' }}>{batchStats.uploading} extracting</span>}
                {batchStats.queued > 0 && <span>{batchStats.queued} queued</span>}
                {batchStats.error > 0 && <span style={{ color: C.redInk }}>{batchStats.error} failed</span>}
                <span style={{ color: C.ink }}><strong>{batchStats.items}</strong> rows</span>
              </span>
            </div>
            <div style={{ height: 6, background: C.monoBg, borderRadius: 99, overflow: 'hidden', marginBottom: 12 }}>
              <div style={{ height: '100%', width: `${batchStats.total ? Math.round((batchStats.done + batchStats.error) / batchStats.total * 100) : 0}%`, background: C.indigo, transition: 'width 0.3s' }} />
            </div>
            <div style={{ maxHeight: 360, overflowY: 'auto', border: `1px solid ${C.monoBg}`, borderRadius: 6 }}>
              {batchGroups.map(g => (
                <div key={g.folder}>
                  <div style={{ position: 'sticky', top: 0, zIndex: 1, display: 'flex', alignItems: 'center', gap: 10, padding: '7px 12px', background: C.wash, borderTop: `1px solid ${C.line}`, borderBottom: `1px solid ${C.monoBg}`, fontSize: 12, fontWeight: 600, color: C.ink }}>
                    <span style={{ flex: 1, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={g.folder}>
                      {g.folder}{!g.matched && <span style={{ color: C.knobOff, fontWeight: 400 }}> · no supplier match</span>}
                    </span>
                    <span style={{ fontWeight: 400, color: C.muted }}>
                      {g.done}/{g.total} done{g.items ? ` · ${g.items} rows` : ''}{g.error ? ' · ' : ''}
                      {g.error > 0 && <span style={{ color: C.redInk, fontWeight: 600 }}>{g.error} failed</span>}
                    </span>
                    {g.error > 0 && !batchRunning && (
                      <button onClick={() => retrySupplier(g.folder)} style={{ background: C.warnBg, color: C.amberInk, border: '1px solid #FDE68A', borderRadius: 5, padding: '3px 10px', fontSize: 11, fontWeight: 700, cursor: 'pointer', whiteSpace: 'nowrap' }}>↻ Retry {g.error}</button>
                    )}
                  </div>
                  {g.files.map(b => {
                    const badge = BATCH_BADGE[b.status]
                    const meta: string[] = []
                    if (b.sizeMB >= 0.1) meta.push(`${b.sizeMB.toFixed(1)} MB`)
                    if (b.status === 'uploading') meta.push('uploading…')
                    // The stage line replaces this while processing — see below.
                    if (b.status === 'done' && b.fmt) meta.push(b.fmt.toUpperCase())
                    return (
                      <div key={b.key} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '7px 12px', borderTop: `1px solid ${C.monoBg}`, fontSize: 12 }}>
                        <span style={{ flex: '0 0 84px', fontSize: 10, fontWeight: 700, textAlign: 'center', background: badge.bg, color: badge.color, padding: '2px 6px', borderRadius: 99, marginTop: 1 }}>
                          {b.status === 'uploading' ? '◷ uploading' : b.status === 'processing' ? '◷ processing' : badge.label}
                        </span>
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ color: C.ink, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={b.name}>{b.name}</div>
                          {/* Format picker: only when the file's supplier
                              publishes several supported formats and this
                              file has not started yet (or errored on the
                              missing choice). */}
                          {(b.status === 'queued' || b.status === 'error') && b.supplierId != null
                            && (supplierContracts[String(b.supplierId)] ?? []).length > 1 && (
                            <select
                              value={b.contractId ?? ''}
                              onChange={e => {
                                const chosen = e.target.value || null
                                setBatchFiles(prev => prev.map(x => x.key === b.key
                                  ? { ...x, contractId: chosen, ...(x.status === 'error' ? { status: 'queued' as BatchStatus, error: null } : {}) }
                                  : x))
                              }}
                              style={{ marginTop: 3, border: `1px solid ${b.contractId ? C.line : '#FDE68A'}`, borderRadius: 6, padding: '3px 7px', fontSize: 11, background: 'white', color: C.ink, maxWidth: 340 }}
                            >
                              <option value="">Which format is this file?</option>
                              {(supplierContracts[String(b.supplierId)] ?? []).map(f => (
                                <option key={f.contract_id} value={f.contract_id}>{f.format_name}</option>
                              ))}
                            </select>
                          )}
                          {meta.length > 0 && (
                            <div style={{ fontSize: 10.5, color: b.status === 'uploading' ? '#1E40AF' : C.faint, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }} title={meta.join(' · ')}>{meta.join(' · ')}</div>
                          )}
                          {b.status === 'processing' && (
                            <div style={{ fontSize: 10.5, color: C.faint, marginTop: 3 }}>
                              {b.progress
                                ? <IngestionProgress run={b.progress} />
                                : 'queued for processing'}
                            </div>
                          )}
                        </div>
                        <span style={{ flex: '0 0 auto', color: b.status === 'error' ? C.redInk : '#16A34A', fontWeight: 600, maxWidth: 160, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', marginTop: 1 }} title={b.error ?? ''}>
                          {b.status === 'done' ? `${b.itemCount ?? '—'} rows` : b.status === 'error' ? (b.error ?? 'error') : ''}
                        </span>
                        {b.runId && (b.status === 'done' || b.status === 'processing') && (
                          <Link
                            to="/catalogues/review/$runId"
                            params={{ runId: b.runId }}
                            style={{ flex: '0 0 auto', background: 'white', border: '1px solid #C7D2FE', borderRadius: 5, padding: '2px 8px', fontSize: 11, fontWeight: 600, color: C.indigoInk, textDecoration: 'none', marginTop: 1 }}
                          >
                            Open board →
                          </Link>
                        )}
                        {!batchRunning && (b.status === 'error' || b.status === 'done') && (
                          <button onClick={() => redoFile(b.key)} style={{ flex: '0 0 auto', background: 'none', border: `1px solid ${C.line}`, borderRadius: 5, padding: '2px 8px', fontSize: 11, fontWeight: 600, color: b.status === 'error' ? C.amberInk : C.muted, cursor: 'pointer', marginTop: 1 }} title={b.status === 'error' ? 'Retry this file' : 'Re-submit this file (creates a new run)'}>
                            ↻ {b.status === 'error' ? 'Retry' : 'Redo'}
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Recent runs */}
      <div style={{ marginTop: 16, background: C.panel, border: `1px solid ${C.line}`, borderRadius: 12, overflow: 'hidden' }}>
        <div style={{ display: 'flex', alignItems: 'center', padding: '10px 16px', borderBottom: `1px solid ${C.line}` }}>
          <b style={{ fontSize: 13.5, color: C.ink }}>Recent runs</b>
          <Link to="/catalogues/review" style={{ marginLeft: 'auto', fontSize: 12, fontWeight: 650, color: C.indigoStrong, textDecoration: 'none' }}>All runs →</Link>
        </div>
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5 }}>
          <tbody>
            {recentRuns.map(run => {
              const pill = RUN_PILL[run.status] ?? RUN_PILL.queued
              return (
                <tr key={run.ingestion_run_id} style={{ borderTop: `1px solid ${C.monoBg}` }}>
                  <td style={{ padding: '9px 16px', fontFamily: MONO, fontSize: 11.5, color: C.sub, whiteSpace: 'nowrap' }}>{run.ingestion_run_id.slice(0, 8)}</td>
                  <td style={{ padding: '9px 16px', color: C.ink }}>{run.contract_id ?? '—'}</td>
                  <td style={{ padding: '9px 16px' }}>
                    <span style={{ fontSize: 10.5, fontWeight: 700, borderRadius: 999, padding: '2px 9px', background: pill.bg, color: pill.color }}>{run.status}</span>
                  </td>
                  <td style={{ padding: '9px 16px', fontFamily: MONO, fontSize: 11.5, color: C.sub }}>{run.product_rows ?? run.items_extracted ?? '—'}</td>
                  <td style={{ padding: '9px 16px', textAlign: 'right' }}>
                    <Link to="/catalogues/review/$runId" params={{ runId: run.ingestion_run_id }} style={{ fontSize: 12, fontWeight: 650, color: C.indigoStrong, textDecoration: 'none' }}>
                      Open board →
                    </Link>
                  </td>
                </tr>
              )
            })}
            {recentRuns.length === 0 && (
              <tr><td style={{ padding: 20, textAlign: 'center', color: C.muted, fontSize: 12.5 }}>
                {runs.isLoading ? 'Loading…' : 'No runs yet — upload a price list above.'}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

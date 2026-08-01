// The run desk — one screen for a run's whole review lifecycle (rev-04).
// Plain-words lifecycle rail, decision lanes (needs-a-pick / new-to-us by
// family / check-by-hand / clean sweep), a persistent staged-changes dock
// (approve lands HERE, visibly — publishing lives in it), and a focus
// overlay for one-at-a-time decisions with ranked suggestions and reason
// chips. Decisions stay append-only; corrections stay immutable revisions;
// the sampling gate still guards the sweep.
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Spinner } from '@/components/Spinner'
import { toast } from '@/lib/toast'
import { skuToPath } from '@/lib/sku'
import { DESK_CSS } from '@/lib/deskCss'
import {
  fetchSummary, fetchDetail, fetchReceipt, searchVariants, latest, groupOf, isPulled,
  isDecided, isApproved, isStaged, sampleIds, suggestTerms,
  approveCandidate, decideCandidate, correctVariantMatch, applyCandidate, publishCandidate,
  correctToNewProduct, checkDuplicates, fetchSkuCategories, willCreate,
  resolveRunIssue, fanOut, fmtDelta, marginPct,
  fetchRunStatus, retryRun, failureInfo, TERMINAL_RUN_STATUSES,
  REASON_CHIPS, PULL_THRESHOLD_PCT,
  type SummaryItem, type VariantHit, type ReceiptChange, type RunStatus, type FailureView,
  type DuplicateCheck, type VariantDraft,
} from '@/lib/review'

export const Route = createFileRoute('/_authed/catalogues/review/$runId/')({ component: RunDeskPage })

type LaneId = 'pick' | 'new' | 'check' | 'clean'
const LANE_LABEL: Record<LaneId, string> = {
  pick: 'needs a pick', new: 'new to us', check: 'check by hand', clean: 'clean',
}

const fm = (v: number | null | undefined) => (v == null ? '—' : v.toFixed(2))

function canApprove(item: SummaryItem): boolean {
  // CONFIRMED_CREATE is approvable, PROPOSED_CREATE is not. The difference is a
  // human: PROPOSED_CREATE is only the matcher saying it found nothing, while
  // CONFIRMED_CREATE carries a draft somebody filled in and signed.
  const resolved = item.variant_state === 'PROPOSED_MATCH'
    || item.variant_state === 'CONFIRMED_MATCH'
    || item.variant_state === 'CONFIRMED_CREATE'
  return resolved && item.blocking_issues === 0 && !isDecided(item)
}


/**
 * Drafting a brand-new canonical product for an unmatched row.
 *
 * Nothing is created here. Confirming records an immutable revision carrying
 * the draft; the SKU is minted when the run is applied, so an abandoned or
 * rejected draft leaves nothing behind.
 *
 * The radar is the point of the panel. Matching only follows exact supplier
 * SKU/barcode, so most rows in the "new to us" lane are a missing *link*, not
 * a missing *product* — the reviewer has to see what already exists before
 * they can mint anything.
 */
function CreateDraftPanel({ runId, item, onCancel, onCreated }: {
  runId: string; item: SummaryItem; onCancel: () => void
  onCreated: (revisionId: string | null) => void | Promise<void>
}) {
  const [name, setName] = useState(item.name?.trim() ?? '')
  const [category, setCategory] = useState('')
  const [brand, setBrand] = useState('')
  const [uom, setUom] = useState('')
  const [ack, setAck] = useState('')
  const [dupes, setDupes] = useState<DuplicateCheck | null>(null)
  const [checking, setChecking] = useState(false)
  const [saving, setSaving] = useState(false)
  const [categories, setCategories] = useState<{ category: string; sku_digit: string | null }[]>([])

  useEffect(() => { fetchSkuCategories().then(setCategories).catch(() => setCategories([])) }, [])

  // The radar runs against the name as it is typed, debounced. It also carries
  // the row's barcode, which is checked regardless of what the name says.
  useEffect(() => {
    const probe = name.trim()
    if (probe.length < 3) { setDupes(null); return }
    setChecking(true)
    const timer = setTimeout(() => {
      checkDuplicates(runId, probe, item.barcode)
        .then(setDupes).catch(() => setDupes(null)).finally(() => setChecking(false))
    }, 300)
    return () => { clearTimeout(timer); setChecking(false) }
  }, [runId, name, item.barcode])

  const digit = categories.find(c => c.category === category)?.sku_digit ?? null
  const blocked = (dupes?.blockers.length ?? 0) > 0
  const needsReason = dupes?.reason_required === true
  const ready = name.trim().length > 1 && !!category && !blocked && (!needsReason || ack.trim().length > 3)

  async function confirm() {
    setSaving(true)
    try {
      const draft: VariantDraft = {
        name: name.trim(),
        category,
        brand: brand.trim() || null,
        uom: uom.trim() || null,
        duplicate_ack: needsReason ? ack.trim() : null,
        // Freeze what the radar showed at the moment of the decision — the
        // append-only design is worth nothing if the evidence moves later.
        checked_against: (dupes?.similar ?? []).slice(0, 3)
          .map(d => ({ sku_code: d.sku_code, name: d.name, score: d.score })),
      }
      const result = await correctToNewProduct(
        runId, item.mastering_candidate_id,
        needsReason ? `New to the catalogue — ${ack.trim()}` : 'New to the catalogue',
        draft, item.name,
      )
      const revision = (result as any)?.output_ids?.[0]
      toast.success('Drafted — approve it, and the SKU is minted when the run applies')
      onCreated(typeof revision === 'string' ? revision : null)
    } catch (e: any) {
      toast.error(String(e?.message ?? e))
    } finally { setSaving(false) }
  }

  return (
    <div className="panel" style={{ background: 'var(--card)', borderColor: 'var(--accent-line)' }}>
      <div className="cdh">
        <span>Draft a new product</span>
        <span className="cdnote">nothing is created until this run is applied</span>
      </div>

      <div className="cdgrid">
        <div>
          <div className="cdlab">What the supplier sent</div>
          <div className="cdev"><span>Name on the sheet</span><b>{item.name ?? '—'}</b></div>
          <div className="cdev"><span>Supplier SKU</span><b className="mono">{item.supplier_sku ?? '—'}</b></div>
          <div className="cdev"><span>Barcode</span><b className="mono">{item.barcode ?? '—'}</b></div>
          <div className="cdev"><span>Cost</span><b>{item.cost_amount != null
            ? `${item.cost_amount.toFixed(2)} ${item.cost_currency ?? ''}${item.cost_basis ? ` / ${item.cost_basis.toLowerCase()}` : ''}`
            : '—'}</b></div>
        </div>

        <div>
          <div className="cdlab">What Rosetta needs</div>
          <label className="cdf">
            <span>Item category — sets the SKU digit</span>
            <select className="fin" value={category} onChange={e => setCategory(e.target.value)}>
              <option value="">Choose one…</option>
              {categories.map(c => <option key={c.category} value={c.category}>{c.category}</option>)}
            </select>
          </label>
          <label className="cdf">
            <span>Product name</span>
            <input className="fin" value={name} onChange={e => setName(e.target.value)}
              placeholder="Brand - Form - Variant" />
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <label className="cdf" style={{ flex: 1 }}>
              <span>Brand</span>
              <input className="fin" value={brand} onChange={e => setBrand(e.target.value)} />
            </label>
            <label className="cdf" style={{ flex: 1 }}>
              <span>Sell unit</span>
              <input className="fin" value={uom} onChange={e => setUom(e.target.value)} placeholder="can, bag, kg…" />
            </label>
          </div>
          <div className="cdsku">
            SKU <b className="mono">{digit ? `${digit}·······` : '········'}</b>
            <span> — assigned from the {category || 'chosen category'} sequence when the run is applied</span>
          </div>
        </div>
      </div>

      {/* the radar */}
      <div className={`cdradar${blocked ? ' bad' : needsReason ? ' warn' : dupes ? ' ok' : ''}`}>
        {checking && <div className="cdrh"><Spinner size={13} /> <span>checking the catalogue…</span></div>}
        {!checking && blocked && dupes!.blockers.map(b => (
          <div key={`${b.kind}-${b.sku_code}`}>
            <div className="cdrh"><b>{b.detail}</b></div>
            <div className="cddup">
              <span className="mono" title={b.sku_code}>{b.sku_code}</span>
              <span className="dn" title={b.name ?? undefined}>{b.name}</span>
              <Link to={'/sku/$' as never} params={{ _splat: skuToPath(b.sku_code) } as never} className="lnk" target="_blank">open</Link>
            </div>
            <div className="cdrs">
              {b.kind === 'barcode'
                ? 'A barcode identifies a physical product. This row is that product from a different supplier — creating a second SKU would split its stock and its history.'
                : 'Match that product instead, or reject this row.'}
            </div>
          </div>
        ))}
        {!checking && !blocked && dupes && dupes.similar.length > 0 && (
          <>
            <div className="cdrh">
              <b>{dupes.similar.length} product{dupes.similar.length === 1 ? '' : 's'} look close to this name</b>
              <span className="cdnote">top match {Math.round(dupes.top_score * 100)}%</span>
            </div>
            {dupes.similar.slice(0, 3).map(d => (
              <div key={d.sku_code} className="cddup">
                <span className="cdbar"><i style={{
                  width: `${Math.round(d.score * 100)}%`,
                  background: d.score >= (dupes.threshold ?? 0.62) ? 'var(--red)' : 'var(--amber)',
                }} /></span>
                <span className="mono" title={d.sku_code}>{d.sku_code}</span>
                <span className="dn" title={d.name ?? undefined}>{d.name}</span>
                {d.sku_valid === false && <span className="badsku" title="This product's SKU code is a product name — matching to it inherits that">bad SKU</span>}
                <Link to={'/sku/$' as never} params={{ _splat: skuToPath(d.sku_code) } as never} className="lnk" target="_blank">open</Link>
              </div>
            ))}
            {needsReason && (
              <label className="cdf" style={{ marginTop: 8 }}>
                <span>What makes this different? — stored on the decision</span>
                <input className="fin" value={ack} onChange={e => setAck(e.target.value)}
                  placeholder="e.g. different pack format — 156 g can vs 370 g tin" />
              </label>
            )}
          </>
        )}
        {!checking && !blocked && dupes && dupes.similar.length === 0 && (
          <div className="cdrh"><b>Nothing close in the catalogue</b><span className="cdnote">clear to create</span></div>
        )}
        {!checking && !dupes && <div className="cdrh"><span className="cdnote">Type a name to check it against the catalogue.</span></div>}
      </div>

      <div className="cdact">
        <button className="btn pri" disabled={!ready || saving} onClick={confirm}>
          {saving ? 'Drafting…' : 'Create this product'}
        </button>
        <span className="cdnote">
          {blocked ? 'Blocked — that product already exists.'
            : needsReason && ack.trim().length <= 3 ? 'Say what makes this different to continue.'
            : !category ? 'Pick a category — it sets the SKU digit.'
            : name.trim().length < 2 ? 'Give it a name.'
            : 'Records the intent; the SKU is minted at apply.'}
        </span>
        <button className="btn" style={{ marginLeft: 'auto' }} onClick={onCancel} disabled={saving}>Cancel</button>
      </div>
    </div>
  )
}

function RunDeskPage() {
  const { runId } = Route.useParams()
  const queryClient = useQueryClient()

  const status = useQuery({
    queryKey: ['review-run-status', runId],
    queryFn: () => fetchRunStatus(runId),
    refetchInterval: query => (TERMINAL_RUN_STATUSES.has(query.state.data?.status ?? '') ? false : 4000),
  })
  const runState = status.data?.status ?? ''
  const reviewable = runState === 'completed' || runState === 'completed_with_warnings'
  const summary = useQuery({ queryKey: ['review-summary', runId], queryFn: () => fetchSummary(runId), enabled: reviewable })
  const items = useMemo(() => latest(summary.data?.items ?? []), [summary.data])

  // ── lanes ──
  const pick = items.filter(i => groupOf(i) === 'ambiguous')
  const newTo = items.filter(i => groupOf(i) === 'unmatched')
  const matched = items.filter(i => groupOf(i) === 'matched')
  const sample = useMemo(
    () => sampleIds(runId, matched.filter(i => !isPulled(i) && i.blocking_issues === 0)),
    [runId, items],
  )
  const check = matched.filter(i => isPulled(i) || sample.has(i.mastering_candidate_id))
  // Membership, NOT eligibility. isBulkEligible() also requires PENDING_REVIEW,
  // and using it here meant a matched row that was neither pulled nor sampled
  // simply vanished from the desk the moment it was decided — in no lane, so
  // the four lane counts summed to one less than the row count. `sweepable`
  // below re-filters to what can actually be swept.
  const sweepPool = matched.filter(i =>
    !isPulled(i) && !willCreate(i) && i.blocking_issues === 0 && !sample.has(i.mastering_candidate_id))
  const sampled = items.filter(i => sample.has(i.mastering_candidate_id))
  const sampledDone = sampled.filter(isDecided).length
  const gateOpen = sampled.length > 0 ? sampledDone === sampled.length : true

  const lanes: Record<LaneId, SummaryItem[]> = { pick, new: newTo, check, clean: sweepPool }
  const pendingIn = (list: SummaryItem[]) => list.filter(i => !isDecided(i))
  const needsYou = pendingIn(pick).length + pendingIn(newTo).length + pendingIn(check).length
  const staged = items.filter(isStaged)
  const published = items.filter(i => i.published)
  const rejected = items.filter(i => i.review_status === 'REJECTED')
  const cleanPending = pendingIn(sweepPool).length
  const total = items.length || 1
  const decidedCount = items.filter(isDecided).length

  // family clusters for the new-to-us lane
  const [clustersExpanded, setClustersExpanded] = useState(false)
  const [cleanExpanded, setCleanExpanded] = useState(false)
  const [search, setSearch] = useState('')
  const searchRef = useRef<HTMLInputElement>(null)
  const clusters = useMemo(() => {
    const map = new Map<string, SummaryItem[]>()
    for (const item of newTo) {
      const key = item.family_key ?? `— no family (${item.supplier_sku ?? item.mastering_candidate_id.slice(0, 6)})`
      map.set(key, [...(map.get(key) ?? []), item])
    }
    return [...map.entries()].sort((a, b) => b[1].length - a[1].length)
  }, [items])

  const openRunIssues = (summary.data?.run_issues ?? []).filter(i => i.resolution_status === 'OPEN')

  // ── focus overlay ──
  const [focus, setFocus] = useState<{ lane: LaneId; id: string } | null>(null)
  // Search spans every lane at once. Two of the four lanes are collapsed by
  // default (New to us groups by family, Clean hides its rows), so filtering
  // lanes in place would leave matches invisible inside a collapsed group. A
  // flat result list finds the row wherever it lives, and the lane chip says
  // what kind of decision it still needs.
  const laneOf = (item: SummaryItem): LaneId =>
    groupOf(item) === 'ambiguous' ? 'pick'
      : groupOf(item) === 'unmatched' ? 'new'
      : check.includes(item) ? 'check' : 'clean'
  const results = useMemo(() => {
    const q = search.trim().toLowerCase()
    if (q.length < 2) return null
    const hit = (v: string | null | undefined) => !!v && v.toLowerCase().includes(q)
    return items.filter(i =>
      hit(i.supplier_sku) || hit(i.canonical_sku) || hit(i.name) || hit(i.variant_name)
      || hit(i.barcode) || hit(i.family_key) || hit(i.draft_name))
  }, [items, search, check])

  // Where the focused row sits in its lane, remembered so a decision that moves
  // it out of the lane can hold it in place rather than teleporting it to the
  // end of the queue.
  const heldSlot = useRef(0)
  const focusQueue = useMemo(() => {
    if (!focus) return []
    const queue = lanes[focus.lane]
    const at = queue.findIndex(i => i.mastering_candidate_id === focus.id)
    if (at >= 0) {
      heldSlot.current = at
      return queue
    }
    const held = items.find(i => i.mastering_candidate_id === focus.id)
    if (!held) return queue
    const slot = Math.min(heldSlot.current, queue.length)
    return [...queue.slice(0, slot), held, ...queue.slice(slot)]
  }, [focus, items, pick, newTo, check, sweepPool])

  const openFocus = (lane: LaneId, id?: string) => {
    const queue = lanes[lane]
    const target = id ?? queue.find(i => !isDecided(i))?.mastering_candidate_id ?? queue[0]?.mastering_candidate_id
    if (target) setFocus({ lane, id: target })
  }

  // ── quick single stage from a lane row ──
  const [rowBusy, setRowBusy] = useState<string | null>(null)
  async function stageOne(item: SummaryItem) {
    setRowBusy(item.mastering_candidate_id)
    try {
      await approveCandidate(runId, item.mastering_candidate_id, 'Matches the evidence — staged from the desk')
      queryClient.setQueryData(['review-summary', runId], (old: any) => old && ({
        ...old,
        items: old.items.map((i: SummaryItem) =>
          i.mastering_candidate_id === item.mastering_candidate_id ? { ...i, review_status: 'APPROVED' } : i),
      }))
      queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
      toast.success(`${item.supplier_sku ?? 'Row'} staged — publish from the dock when ready`)
    } catch (e: any) {
      toast.error(String(e?.message ?? e))
    } finally {
      setRowBusy(null)
    }
  }

  // ── clean sweep (typed count; sampling gate still applies) ──
  const [sweepTyped, setSweepTyped] = useState('')
  const [sweepProgress, setSweepProgress] = useState<string | null>(null)
  const sweepable = sweepPool.filter(i => !isDecided(i))
  async function sweepClean() {
    setSweepTyped('')
    setSweepProgress(`0 / ${sweepable.length}`)
    const { failures } = await fanOut(
      sweepable,
      item => approveCandidate(runId, item.mastering_candidate_id, 'Clean sweep after sampling gate'),
      (done, totalN) => setSweepProgress(`${done} / ${totalN}`),
    )
    setSweepProgress(null)
    await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
    if (failures.length) toast.error(`${failures.length} failed — first: ${failures[0].error}`)
    else toast.success(`${sweepable.length} clean rows staged`)
  }

  async function onResolveIssue(issueId: string) {
    try {
      await resolveRunIssue(runId, issueId, 'Reviewed from the run desk')
      await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
      toast.success('Noted — issue marked handled')
    } catch (e: any) { toast.error(String(e?.message ?? e)) }
  }

  // ── keyboard: Enter opens focus on the first lane that needs a human ──
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (focus) return
      const target = event.target as HTMLElement
      const typing = target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)
      // Esc leaves the search box even while it has focus, so the keyboard
      // path back to the lanes never dead-ends.
      if (event.key === 'Escape' && typing && target === searchRef.current) {
        setSearch('')
        searchRef.current?.blur()
        return
      }
      if (typing) return
      if (event.key === '/') { event.preventDefault(); searchRef.current?.focus(); return }
      if (event.key === 'Enter') {
        const lane: LaneId | null = pendingIn(pick).length ? 'pick' : pendingIn(newTo).length ? 'new' : pendingIn(check).length ? 'check' : null
        if (lane) openFocus(lane)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (status.isLoading) return <div style={{ padding: 24 }}><Spinner /></div>
  if (status.isError) return <div style={{ padding: 24, color: '#C0362C', fontSize: 13 }}>{String((status.error as Error)?.message)}</div>
  if (status.data && (runState === 'failed' || runState === 'cancelled')) {
    return <FailedRunState run={status.data} />
  }
  if (status.data && !reviewable) {
    return (
      <div className="rdesk" style={{ padding: '18px 24px 40px', maxWidth: 1220, margin: '0 auto' }}>
        <style>{DESK_CSS}</style>
        <div style={{ fontSize: 11, color: 'var(--faint)', marginBottom: 2 }}>
          <Link to="/catalogues/review" className="lnk" style={{ fontWeight: 600 }}>← Runs</Link>
        </div>
        <h1>{status.data.contract_id ?? 'Catalogue run'}</h1>
        <div className="life">
          <span className="stage done"><span className="sdot">✓</span>Received</span>
          <span className="stage now"><span className="sdot">…</span>Reading <span className="scount">still processing — this page refreshes itself</span></span>
          <span className="stage"><span className="sdot">·</span>Decisions</span>
          <span className="stage"><span className="sdot">·</span>Publish</span>
          <span className="stage"><span className="sdot">·</span>Live</span>
        </div>
      </div>
    )
  }
  if (summary.isLoading) return <div style={{ padding: 24 }}><Spinner /></div>
  if (summary.isError) return <div style={{ padding: 24, color: '#C0362C', fontSize: 13 }}>{String((summary.error as Error)?.message)}</div>

  // lifecycle
  const rows = status.data?.items_extracted ?? items.length
  const decisionsDone = needsYou === 0 && cleanPending === 0
  const liveStage = published.length > 0

  // ring: live / staged / needs-you / clean-pending / rejected(rest)
  const pct = (n: number) => Math.round((n / total) * 100)
  const ringStops = (() => {
    let acc = 0
    const seg = (n: number, color: string) => {
      const from = acc; acc += (n / total) * 100
      return `${color} ${from}% ${acc}%`
    }
    return [
      seg(published.length, '#22A55E'),
      seg(staged.length, '#4F46E5'),
      seg(rejected.length, '#C6CAD6'),
      seg(cleanPending, '#B9C2F2'),
      seg(needsYou, '#E9A23B'),
      `#EFF0F5 ${acc}% 100%`,
    ].join(', ')
  })()

  return (
    <div className="rdesk" style={{ padding: '18px 24px 40px', maxWidth: 1220, margin: '0 auto' }}>
      <style>{DESK_CSS}</style>

      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 11, color: 'var(--faint)', marginBottom: 2 }}>
            <Link to="/catalogues/review" className="lnk" style={{ fontWeight: 600 }}>← Runs</Link>
          </div>
          <h1>{status.data?.contract_id ?? 'Catalogue run'} <span style={{ fontWeight: 450, fontSize: 12.5, color: 'var(--faint)' }}>· {rows} rows</span></h1>
          {status.data?.retry_of && (
            <div style={{ marginTop: 4, fontSize: 11.5, color: 'var(--muted)' }}>
              ↻ retry of an earlier failed attempt · <Link className="lnk" style={{ fontSize: 11.5 }} to="/catalogues/review/$runId" params={{ runId: status.data.retry_of }}>view attempt 1</Link>
            </div>
          )}
        </div>
        <span style={{ marginLeft: 'auto', display: 'flex', gap: 8, alignItems: 'center' }}>
          <span className="dsearch">
            <svg viewBox="0 0 16 16" width="13" height="13" fill="none" stroke="currentColor" strokeWidth="1.7" aria-hidden>
              <circle cx="7" cy="7" r="4.3" /><path d="M10.5 10.5l3 3" strokeLinecap="round" />
            </svg>
            <input ref={searchRef} value={search} onChange={e => setSearch(e.target.value)}
              placeholder="Find a row — supplier SKU, our SKU, name, barcode" aria-label="Search this run" />
            {search && <button className="x" onClick={() => { setSearch(''); searchRef.current?.focus() }} aria-label="Clear search">×</button>}
          </span>
          <span className="kbd" title="Press / to search, Enter to open the next decision">/</span>
          <span className="kbd" title="Enter opens the next decision">Enter</span>
          <Link className="btn sm" to="/catalogues/review/$runId/commit" params={{ runId }}>Receipt</Link>
        </span>
      </div>

      {/* lifecycle — plain words, counts per stage */}
      <div className="life">
        <span className="stage done"><span className="sdot">✓</span>Received</span>
        <span className="stage done"><span className="sdot">✓</span>Checked <span className="scount">{rows} rows</span></span>
        <span className={`stage ${!decisionsDone ? 'now' : 'done'}`}>
          <span className="sdot">{decisionsDone ? '✓' : needsYou}</span>Decisions
          {/* These three partition every row, so they sum to the row count —
              leaving the decided ones unnamed made 75 + 157 look short of 238. */}
          <span className="scount">{needsYou} need you · {cleanPending} clean · {decidedCount} done</span>
        </span>
        <span className={`stage ${decisionsDone && staged.length > 0 ? 'now' : staged.length > 0 || liveStage ? 'done' : ''}`}>
          <span className="sdot">{staged.length || '→'}</span>Publish
          {staged.length > 0 && <span className="scount">{staged.length} staged</span>}
        </span>
        <span className={`stage ${liveStage ? 'done' : ''}`}>
          <span className="sdot">{published.length || '·'}</span>Live
          {published.length > 0 && <span className="scount">{published.length} published</span>}
        </span>
      </div>

      {/* run issues — sentence first, code in the tooltip */}
      {openRunIssues.map(issue => (
        <div key={issue.validation_issue_id} className="issue">
          <span className="ad" />
          <span title={issue.issue_code}><b>{issue.message}</b>{issue.review_guidance ? ` ${issue.review_guidance}` : ''}</span>
          <button className="btn sm" style={{ marginLeft: 'auto', flex: 'none' }} onClick={() => onResolveIssue(issue.validation_issue_id)}>Mark handled</button>
        </div>
      ))}

      <div className="desk">
        {/* ── lanes, or search results across all of them ── */}
        <div className="panel">
          {results !== null ? (
            <div className="lane">
              <div className="laneh">
                <span className="ln">
                  {results.length} match{results.length === 1 ? '' : 'es'} · “{search.trim()}”
                </span>
                <span className="lc">across every lane · {results.filter(isDecided).length} decided</span>
                <span className="lnk" style={{ marginLeft: 'auto', fontSize: 11.5 }} onClick={() => setSearch('')}>
                  back to lanes
                </span>
              </div>
              <div className="lanebody">
                {results.map(item => {
                  const lane = laneOf(item)
                  return (
                    <LaneRow key={item.mastering_candidate_id} item={item}
                      why={<>
                        <span className={`lanetag ${lane}`}>{LANE_LABEL[lane]}</span>
                        {isDecided(item) ? decidedLabel(item)
                          : item.canonical_sku ? `→ ${item.canonical_sku}` : 'no match yet'}
                      </>}
                      action={!isDecided(item) && canApprove(item) && !isPulled(item)
                        ? <button className="btn sm" disabled={rowBusy === item.mastering_candidate_id} onClick={() => stageOne(item)}>✓ Stage</button>
                        : undefined}
                      onOpen={() => openFocus(lane, item.mastering_candidate_id)} />
                  )
                })}
                {results.length === 0 && (
                  <Empty label={`Nothing in this run matches “${search.trim()}”. Searches supplier SKU, our SKU, name, barcode and family.`} />
                )}
              </div>
            </div>
          ) : (<>
          {/* needs a pick */}
          <div className="lane">
            <div className="laneh">
              <span className="ln">Needs a pick · {pick.length}</span>
              <span className="lc">two or more plausible offerings — {pick.filter(isDecided).length} of {pick.length} decided</span>
              {pendingIn(pick).length > 0 && (
                <button className="btn pri sm" style={{ marginLeft: 'auto' }} onClick={() => openFocus('pick')}>Decide in focus</button>
              )}
            </div>
            <div className="lanebody">
              {pick.map(item => (
                <LaneRow key={item.mastering_candidate_id} item={item}
                  why={isDecided(item) ? decidedLabel(item) : 'offerings collide'}
                  onOpen={() => openFocus('pick', item.mastering_candidate_id)} />
              ))}
              {pick.length === 0 && <Empty label="No collisions in this run." />}
            </div>
          </div>

          {/* new to us — cluster-first */}
          <div className="lane">
            <div className="laneh">
              <span className="ln">New to us · {newTo.length}</span>
              <span className="lc">no offering on file — {clusters.length} famil{clusters.length === 1 ? 'y' : 'ies'} · {newTo.filter(isDecided).length} decided</span>
              <span className="lnk" style={{ marginLeft: 'auto', fontSize: 11.5 }} onClick={() => setClustersExpanded(open => !open)}>
                {clustersExpanded ? 'group by family' : 'show all items'}
              </span>
            </div>
            <div className="lanebody">
              {!clustersExpanded && clusters.map(([family, members]) => {
                const pending = members.filter(i => !isDecided(i))
                const prices = members.map(i => i.cost_amount).filter((v): v is number => v != null)
                return (
                  <div key={family} className="clusterrow">
                    <span style={{ minWidth: 0 }}>
                      <span style={{ fontWeight: 650, color: 'var(--ink)', fontSize: 12 }}>{family}</span>
                      <span style={{ color: 'var(--faint)', fontSize: 11 }}> · {members.length} item{members.length === 1 ? '' : 's'}{prices.length ? ` · ${Math.min(...prices).toFixed(2)}–${Math.max(...prices).toFixed(2)}` : ''}</span>
                    </span>
                    <span className="why" style={{ fontSize: 11, color: pending.length ? 'var(--muted)' : 'var(--good)' }}>
                      {pending.length ? `${pending.length} to match` : 'all matched ✓'}
                    </span>
                    {pending.length > 0 && (
                      <button className="btn sm" onClick={() => openFocus('new', pending[0].mastering_candidate_id)}>Match family…</button>
                    )}
                  </div>
                )
              })}
              {clustersExpanded && newTo.map(item => (
                <LaneRow key={item.mastering_candidate_id} item={item}
                  why={isDecided(item) ? decidedLabel(item) : item.family_key ? `family: ${item.family_key}` : 'no family'}
                  onOpen={() => openFocus('new', item.mastering_candidate_id)} />
              ))}
              {newTo.length === 0 && <Empty label="Every row already has an offering." />}
            </div>
          </div>

          {/* check by hand: material moves + spot-checks */}
          <div className="lane">
            <div className="laneh">
              <span className="ln">Check by hand · {check.length}</span>
              <span className="lc">price moved &gt;{PULL_THRESHOLD_PCT}% or in the {sampled.length}-row spot-check · {check.filter(isDecided).length} decided</span>
              {pendingIn(check).length > 0 && (
                <button className="btn pri sm" style={{ marginLeft: 'auto' }} onClick={() => openFocus('check')}>Check in focus</button>
              )}
            </div>
            <div className="lanebody">
              {check.map(item => (
                <LaneRow key={item.mastering_candidate_id} item={item}
                  why={isDecided(item) ? decidedLabel(item)
                    : `${item.canonical_sku ?? ''}${item.price_delta_pct != null ? ` · ${fmtDelta(item.price_delta_pct)}` : ''}${isPulled(item) ? ' — big move' : ' · spot-check'}`}
                  whyTone={isPulled(item) && !isDecided(item) ? 'var(--red)' : undefined}
                  action={!isDecided(item) && canApprove(item) && !isPulled(item)
                    ? <button className="btn sm" disabled={rowBusy === item.mastering_candidate_id} onClick={() => stageOne(item)}>✓ Stage</button>
                    : undefined}
                  onOpen={() => openFocus('check', item.mastering_candidate_id)} />
              ))}
              {check.length === 0 && <Empty label="No material moves, nothing sampled." />}
            </div>
          </div>

          {/* clean sweep — a bulk control, but you must be able to look at what
              you are about to approve in bulk. The 12-row spot-check gates the
              sweep; it is not a substitute for seeing the list. */}
          <div className="lane">
            <div className="laneh" style={{ borderBottom: cleanExpanded ? undefined : 'none' }}>
              <span className="ln">Clean · {sweepPool.length}</span>
              <span className="lc">
                matched, moves ≤{PULL_THRESHOLD_PCT}%, nothing blocking · {sweepPool.filter(isDecided).length} decided ·{' '}
                {/* "finish them to unlock" lived here AND on the button. */}
                {sampled.length > 0 ? `spot-checks ${sampledDone}/${sampled.length}${gateOpen ? ' ✓' : ''}` : 'no spot-checks needed'}
              </span>
              {sweepPool.length > 0 && (
                <span className="lnk" style={{ fontSize: 11.5, marginLeft: 'auto', whiteSpace: 'nowrap' }}
                  onClick={() => setCleanExpanded(open => !open)}>
                  {cleanExpanded ? 'hide items' : 'show all items'}
                </span>
              )}
              <span style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                {sweepProgress ? (
                  <span className="mono" style={{ fontSize: 11 }}>staging {sweepProgress}…</span>
                ) : sweepable.length === 0 ? (
                  <span style={{ fontSize: 11, color: 'var(--good)', fontWeight: 650 }}>all staged ✓</span>
                ) : gateOpen ? (
                  <>
                    <input className="fin" style={{ width: 62, textAlign: 'center' }} placeholder={String(sweepable.length)}
                      value={sweepTyped} onChange={e => setSweepTyped(e.target.value)}
                      onKeyDown={e => { if (e.key === 'Enter' && sweepTyped.trim() === String(sweepable.length)) sweepClean() }}
                      aria-label="Type the sweep count to confirm" />
                    <button className="btn pri sm" onClick={() => {
                      if (sweepTyped.trim() === String(sweepable.length)) sweepClean()
                      else toast.error(`Type ${sweepable.length} first — the count is the confirmation.`)
                    }}>Sweep {sweepable.length} → staged</button>
                  </>
                ) : (
                  <button className="btn sm" onClick={() => openFocus('check')}>Finish spot-checks first</button>
                )}
              </span>
            </div>
            {cleanExpanded && (
              <div className="lanebody">
                {sweepPool.map(item => (
                  <LaneRow key={item.mastering_candidate_id} item={item}
                    why={isDecided(item) ? decidedLabel(item)
                      : `${item.canonical_sku ?? ''}${item.price_delta_pct != null ? ` · ${fmtDelta(item.price_delta_pct)}` : ''}`}
                    action={!isDecided(item) && canApprove(item)
                      ? <button className="btn sm" disabled={rowBusy === item.mastering_candidate_id} onClick={() => stageOne(item)}>✓ Stage</button>
                      : undefined}
                    onOpen={() => openFocus('clean', item.mastering_candidate_id)} />
                ))}
                {sweepPool.length === 0 && <Empty label="Nothing clean yet." />}
              </div>
            )}
          </div>
          </>)}
        </div>

        {/* ── dock ── */}
        <Dock
          runId={runId}
          ringStops={ringStops}
          centerPct={pct(decidedCount)}
          stats={[
            { n: published.length, label: 'live', color: '#22A55E' },
            { n: staged.length, label: 'staged for publish', color: '#4F46E5' },
            { n: cleanPending, label: 'clean, awaiting sweep', color: '#B9C2F2' },
            { n: needsYou, label: 'need you', color: '#E9A23B' },
            { n: rejected.length, label: 'rejected', color: '#C6CAD6' },
          ]}
          staged={staged}
          onPublished={() => queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })}
        />
      </div>

      {focus && (
        <FocusOverlay
          runId={runId}
          lane={focus.lane}
          // The row you are working on stays in the queue even after your
          // decision moves it out of this lane. Correcting a match mints a
          // revision that is no longer ambiguous, so it drops out of the pick
          // lane immediately — and the overlay, finding nothing, fell back to
          // queue[0] and silently swapped a different row under you.
          queue={focusQueue}
          currentId={focus.id}
          allItems={items}
          onMove={id => setFocus(f => (f ? { ...f, id } : f))}
          onClose={() => setFocus(null)}
        />
      )}
    </div>
  )
}


// ── failed / cancelled run state: the rail shows where, the card shows what ──
const FAIL_STAGE_INDEX: Record<string, number> = { received: 0, reading: 1, understanding: 1, recording: 2 }

function FailedRunState({ run }: { run: RunStatus }) {
  const navigate = useNavigate()
  const [busy, setBusy] = useState(false)
  const [techOpen, setTechOpen] = useState(false)
  const failure: FailureView | null = failureInfo(run.error_summary) ?? {
    code: run.status === 'cancelled' ? 'RUN_CANCELLED' : 'UNKNOWN',
    sentence: run.status === 'cancelled' ? 'This run was cancelled.' : 'The run failed — no details were recorded.',
    action: 'Re-submit the file from the Catalogues page.',
    stage: 'recording', stageWords: 'recording the run', retryable: false, attempts: 1, detail: null, raw: '',
  }
  const failAt = FAIL_STAGE_INDEX[failure.stage] ?? 2
  const stages = ['Received', 'Reading', 'Decisions', 'Publish', 'Live']
  const superseded = run.superseded_by_run

  async function onRetry() {
    setBusy(true)
    try {
      const result = await retryRun(run.ingestion_run_id)
      toast.success('Retry queued — same file, new run')
      navigate({ to: '/catalogues/review/$runId', params: { runId: result.ingestion_run_id } })
    } catch (e: any) {
      toast.error(String(e?.message ?? e))
    } finally { setBusy(false) }
  }

  const copyTech = () => {
    const payload = [
      `run ${run.ingestion_run_id}`, `contract ${run.contract_id ?? '—'}@${run.contract_version ?? '—'}`,
      `code ${failure.code}`, `message ${failure.raw}`, failure.detail ? `detail ${failure.detail}` : null,
      `failed_at ${run.completed_at ?? '—'}`,
    ].filter(Boolean).join('\n')
    navigator.clipboard?.writeText(payload).then(() => toast.success('Technical details copied'), () => {})
  }

  return (
    <div className="rdesk" style={{ padding: '18px 24px 40px', maxWidth: 980, margin: '0 auto' }}>
      <style>{DESK_CSS}</style>
      <div style={{ fontSize: 11, color: 'var(--faint)', marginBottom: 2 }}>
        <Link to="/catalogues/review" className="lnk" style={{ fontWeight: 600 }}>← Runs</Link>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <h1>{run.contract_id ?? 'Catalogue run'}</h1>
        <span className="bdg bad"><span className="st" />{run.status}</span>
      </div>
      {run.retry_of && (
        <div style={{ marginTop: 4, fontSize: 11.5, color: 'var(--muted)' }}>
          ↻ this was already a retry · <Link className="lnk" style={{ fontSize: 11.5 }} to="/catalogues/review/$runId" params={{ runId: run.retry_of }}>view the earlier attempt</Link>
        </div>
      )}

      <div className="life">
        {stages.map((label, index) => (
          <span key={label} className={`stage ${index < failAt ? 'done' : index === failAt ? 'fail' : ''}`}
            style={index > failAt ? { opacity: 0.45 } : undefined}>
            <span className="sdot" style={index === failAt ? { background: '#DC2626' } : undefined}>
              {index < failAt ? '✓' : index === failAt ? '✗' : '·'}
            </span>
            {label}
          </span>
        ))}
      </div>

      <div style={{ border: '1px solid #F1CDC9', borderLeft: '4px solid #DC2626', borderRadius: 12, background: 'var(--card)', marginTop: 12, overflow: 'hidden' }}>
        <div style={{ padding: '13px 16px 4px' }}>
          <div style={{ fontSize: 15, fontWeight: 750, color: 'var(--ink)' }}>{failure.sentence}</div>
          <div style={{ fontSize: 12.5, color: 'var(--ink2)', marginTop: 4 }}>{superseded ? 'This attempt has already been retried — continue on the newer run.' : failure.action}</div>
        </div>
        <div style={{ display: 'flex', gap: 14, padding: '8px 16px', fontSize: 11, color: 'var(--faint)', flexWrap: 'wrap' }}>
          <span>failed {run.completed_at ? new Date(run.completed_at).toLocaleString() : '—'}</span>
          <span>· while {failure.stageWords}</span>
          {failure.attempts > 1 && <span>· {failure.attempts} attempts</span>}
          {failure.retryable && !superseded && <span className="bdg warn" style={{ fontSize: 9 }}>retryable</span>}
        </div>
        <div style={{ display: 'flex', gap: 8, padding: '10px 16px', borderTop: '1px solid var(--line2)', alignItems: 'center', flexWrap: 'wrap' }}>
          {superseded ? (
            <Link className="btn pri" to="/catalogues/review/$runId" params={{ runId: superseded }}>Open the retry →</Link>
          ) : failure.retryable ? (
            <>
              <button className="btn pri" disabled={busy} onClick={onRetry}>{busy ? 'Queuing retry…' : 'Retry run'}</button>
              <span style={{ fontSize: 10.5, color: 'var(--faint)' }}>same file, new run — no re-upload</span>
            </>
          ) : (
            <Link className="btn pri" to={'/catalogues' as never}>Upload a fixed file →</Link>
          )}
          <span className="lnk" style={{ fontSize: 11 }} onClick={copyTech}>copy technical details</span>
          <span className="lnk" style={{ fontSize: 11, marginLeft: 'auto' }} onClick={() => setTechOpen(open => !open)}>technical details {techOpen ? '▴' : '▾'}</span>
        </div>
        {techOpen && (
          <div className="mono" style={{ margin: '0 16px 13px', border: '1px solid var(--line2)', borderRadius: 8, background: 'var(--panel)', fontSize: 10.5, color: 'var(--muted)', padding: '8px 11px', whiteSpace: 'pre-wrap' }}>
            {failure.code} · run {run.ingestion_run_id}{'\n'}contract {run.contract_id ?? '—'}@{run.contract_version ?? '—'}{'\n'}message: {failure.raw || '—'}{failure.detail ? `\ndetail: ${failure.detail}` : ''}
          </div>
        )}
      </div>
    </div>
  )
}

const decidedLabel = (item: SummaryItem) =>
  item.review_status === 'REJECTED' ? 'rejected' : isApproved(item) ? (item.published ? 'live ✓' : 'staged ✓') : 'sent back'

function LaneRow({ item, why, whyTone, action, onOpen }: {
  item: SummaryItem; why: React.ReactNode; whyTone?: string; action?: React.ReactNode; onOpen: () => void
}) {
  return (
    <div className={`trow${isDecided(item) ? ' decided' : ''}`}>
      <span className="sku">{item.supplier_sku ?? '—'}</span>
      <span className="nm" onClick={onOpen} title={item.name ?? undefined}>
        {/* A row about to mint a SKU reads very differently from a matched one
            — mark it so a lane can be swept without losing track. */}
        {willCreate(item) && <span className="willcreate">will create</span>}
        {willCreate(item) && ' '}
        {item.draft_name ?? item.name ?? '—'}
      </span>
      <span className="prc">{item.cost_amount != null ? `${item.cost_amount.toFixed(2)} ${item.cost_currency ?? ''}` : '—'}</span>
      <span className="why" style={whyTone ? { color: whyTone, fontWeight: 650 } : undefined}>{why}</span>
      <span style={{ display: 'flex', gap: 6 }}>
        {action}
        <span className="lnk" style={{ fontSize: 11 }} onClick={onOpen}>open</span>
      </span>
    </div>
  )
}

const Empty = ({ label }: { label: string }) => (
  <div style={{ padding: '14px 16px', fontSize: 12, color: 'var(--faint)' }}>{label}</div>
)

// ── the dock: burn-down, staged cart, publish, receipt ─────────────────────
/** What publishing this staged row will actually write. */
type StagedKind = 'creates' | 'moves' | 'first' | 'holds'
const STAGED_KIND: Record<StagedKind, { label: string; tone: string }> = {
  creates: { label: 'creates a product', tone: 'var(--accent-ink)' },
  moves:   { label: 'price moves',       tone: 'var(--amber)' },
  first:   { label: 'first price',       tone: 'var(--good)' },
  holds:   { label: 'price unchanged',   tone: 'var(--muted)' },
}
function stagedKind(item: SummaryItem): StagedKind {
  if (willCreate(item)) return 'creates'
  if (item.current_cost == null) return 'first'
  if (item.price_delta_pct != null && item.price_delta_pct !== 0) return 'moves'
  return 'holds'
}

/**
 * Everything staged, in one list, before it becomes real.
 *
 * The dock could only ever show rows whose price MOVED, capped at five — on a
 * typical run that is 11 of 238, so a sweep of the clean lane collapsed 227
 * rows into "+227 more". You were asked to type a count to confirm a set you
 * could not read. This is that set, grouped by what publishing does to it.
 */
function StagedReview({ staged, version, typed, setTyped, onPublish, onClose }: {
  staged: SummaryItem[]
  version: string
  typed: string
  setTyped: (v: string) => void
  onPublish: () => void
  onClose: () => void
}) {
  const ORDER: StagedKind[] = ['creates', 'moves', 'first', 'holds']
  const groups = ORDER
    .map(kind => [kind, staged.filter(i => stagedKind(i) === kind)] as const)
    .filter(([, rows]) => rows.length > 0)
  // Biggest move first inside the group that has moves — the risky ones lead.
  const sorted = (kind: StagedKind, rows: SummaryItem[]) =>
    kind === 'moves'
      ? [...rows].sort((a, b) => Math.abs(b.price_delta_pct ?? 0) - Math.abs(a.price_delta_pct ?? 0))
      : rows

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="rdesk-ovl" onClick={onClose}>
      <div className="rdesk-focus" onClick={e => e.stopPropagation()} style={{ width: 900 }}>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 12, flexWrap: 'wrap' }}>
          <h1 style={{ fontSize: 17 }}>{staged.length} staged · review before publishing</h1>
          <span className="lnk" style={{ marginLeft: 'auto' }} onClick={onClose}>close <span className="kbd">Esc</span></span>
        </div>
        <div style={{ display: 'flex', gap: 7, flexWrap: 'wrap', margin: '9px 0 4px' }}>
          {groups.map(([kind, rows]) => (
            <span key={kind} className="sgroupchip" style={{ color: STAGED_KIND[kind].tone }}>
              <b>{rows.length}</b> {STAGED_KIND[kind].label}
            </span>
          ))}
        </div>

        <div className="panel" style={{ maxHeight: '54vh', overflowY: 'auto' }}>
          {groups.map(([kind, rows]) => (
            <div key={kind}>
              <div className="sgrouph">{STAGED_KIND[kind].label} · {rows.length}</div>
              {sorted(kind, rows).map(item => (
                <div key={item.mastering_candidate_id} className="srow">
                  <span className="mono sku">{item.canonical_sku ?? item.draft_name ?? '—'}</span>
                  <span className="nm" title={item.variant_name ?? item.name ?? undefined}>
                    {item.variant_name ?? item.draft_name ?? item.name ?? '—'}
                  </span>
                  <span className="ssup mono">{item.supplier_sku ?? '—'}</span>
                  <span className="sval">
                    {kind === 'creates' ? <>new SKU on publish</>
                      : kind === 'first' ? <>first price <b>{fm(item.cost_amount)}</b></>
                      : <>{fm(item.current_cost)} → <b>{fm(item.cost_amount)}</b></>}
                  </span>
                  <span className="sdelta" style={{
                    color: Math.abs(item.price_delta_pct ?? 0) > PULL_THRESHOLD_PCT ? 'var(--red)' : 'var(--faint)',
                  }}>{kind === 'moves' ? fmtDelta(item.price_delta_pct) : ''}</span>
                  {item.canonical_sku && (
                    <Link className="lnk" style={{ fontSize: 10.5 }} to={'/sku/$' as never}
                      params={{ _splat: skuToPath(item.canonical_sku) } as never} target="_blank">SKU →</Link>
                  )}
                </div>
              ))}
            </div>
          ))}
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 11, flexWrap: 'wrap' }}>
          <input className="fin" style={{ width: 72, textAlign: 'center' }} placeholder={String(staged.length)}
            value={typed} onChange={e => setTyped(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && typed.trim() === String(staged.length)) onPublish() }}
            aria-label="Type the publish count to confirm" />
          <button className="btn pri" onClick={() => {
            if (typed.trim() === String(staged.length)) onPublish()
            else toast.error(`Type ${staged.length} first — the count is the confirmation.`)
          }}>Publish {staged.length}</button>
          <span style={{ fontSize: 10.5, color: 'var(--faint)', lineHeight: 1.5, flex: 1, minWidth: 220 }}>
            Snapshot <b className="mono">{version}</b> · immutable · writes offering prices, links, packaging &amp; deals — never selling prices or stock.
          </span>
        </div>
      </div>
    </div>
  )
}

function Dock({ runId, ringStops, centerPct, stats, staged, onPublished }: {
  runId: string
  ringStops: string
  centerPct: number
  stats: { n: number; label: string; color: string }[]
  staged: SummaryItem[]
  onPublished: () => void
}) {
  const queryClient = useQueryClient()
  const [typed, setTyped] = useState('')
  const [progress, setProgress] = useState<string | null>(null)
  const [failures, setFailures] = useState<{ sku: string | null; error: string }[]>([])
  const [justPublished, setJustPublished] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const version = `v${new Date().toISOString().slice(0, 10)}`

  const receipt = useQuery({
    queryKey: ['review-receipt', runId],
    queryFn: () => fetchReceipt(runId),
    enabled: justPublished || stats[0].n > 0,
  })

  async function publishAll() {
    setTyped('')
    setFailures([])
    setProgress(`0 / ${staged.length}`)
    const result = await fanOut(
      staged,
      async item => {
        await applyCandidate(runId, item.mastering_candidate_id)
        await publishCandidate(runId, item.mastering_candidate_id, version)
      },
      (done, total) => setProgress(`${done} / ${total}`),
    )
    setProgress(null)
    setReviewOpen(false)
    onPublished()
    setJustPublished(true)
    // The LIVE section must move the moment publish lands — refetch the
    // receipt alongside the summary, no refresh required.
    queryClient.invalidateQueries({ queryKey: ['review-receipt', runId] })
    if (result.failures.length) {
      setFailures(result.failures.map(f => ({ sku: f.item.supplier_sku, error: f.error })))
      toast.error(`${result.failures.length} of ${staged.length} failed — publish again to retry just those`)
    } else {
      toast.success(`Published ${staged.length} changes as ${version}`)
    }
  }

  const deltas = staged
    .filter(i => i.price_delta_pct != null && i.price_delta_pct !== 0)
    .sort((a, b) => Math.abs(b.price_delta_pct!) - Math.abs(a.price_delta_pct!))
  const receiptRows = (receipt.data?.changes ?? []).slice(-4).reverse()

  return (
    <div className="dock">
      {reviewOpen && staged.length > 0 && (
        <StagedReview staged={staged} version={version} typed={typed} setTyped={setTyped}
          onPublish={publishAll} onClose={() => setReviewOpen(false)} />
      )}
      <div style={{ display: 'flex', gap: 12, padding: '13px 13px 10px', alignItems: 'center' }}>
        <span className="ring" style={{ background: `conic-gradient(${ringStops})` }}><b>{centerPct}%</b></span>
        <span style={{ minWidth: 0 }}>
          {stats.filter(s => s.n > 0 || s.label === 'need you').map(s => (
            <span key={s.label} className="dstat"><i style={{ background: s.color }} />{s.n} {s.label}</span>
          ))}
        </span>
      </div>

      <div className="dsec">
        <div className="dh">Staged — becomes real on publish</div>
        {staged.length === 0 && <div style={{ fontSize: 11.5, color: 'var(--faint)' }}>Nothing staged yet — approvals land here.</div>}
        {/* The preview leads with the price moves because they are the risky
            ones, but it must never imply that is all that is staged — the
            counts below name every row, and "Review all" shows them. */}
        {deltas.slice(0, 4).map(item => (
          <div key={item.mastering_candidate_id} className="sitem">
            <span className="sku">{item.canonical_sku ?? item.supplier_sku}</span>
            <span>{fm(item.current_cost)} → <b>{fm(item.cost_amount)}</b></span>
            <span style={{ marginLeft: 'auto', fontSize: 10.5, color: Math.abs(item.price_delta_pct ?? 0) > PULL_THRESHOLD_PCT ? 'var(--red)' : 'var(--muted)' }}>{fmtDelta(item.price_delta_pct)}</span>
          </div>
        ))}
        {staged.length > 0 && (
          <>
            <div style={{ fontSize: 10.5, color: 'var(--faint)', marginTop: 5, lineHeight: 1.6 }}>
              {(['creates', 'moves', 'first', 'holds'] as StagedKind[])
                .map(kind => [kind, staged.filter(i => stagedKind(i) === kind).length] as const)
                .filter(([, n]) => n > 0)
                .map(([kind, n]) => `${n} ${STAGED_KIND[kind].label}`)
                .join(' · ')}
            </div>
            <button className="btn sm" style={{ marginTop: 8, width: '100%', justifyContent: 'center' }}
              onClick={() => setReviewOpen(true)}>Review all {staged.length} →</button>
          </>
        )}
      </div>

      {failures.length > 0 && (
        <div className="dsec" style={{ background: 'var(--red-soft)' }}>
          <div className="dh" style={{ color: 'var(--red)' }}>Failed — publish again to retry just these</div>
          {failures.slice(0, 3).map((f, i) => (
            <div key={i} className="mono" style={{ fontSize: 10.5, color: 'var(--red)', padding: '2px 0' }}>{f.sku}: {f.error}</div>
          ))}
          {failures.length > 3 && <div className="mono" style={{ fontSize: 10.5, color: 'var(--red)' }}>… and {failures.length - 3} more</div>}
        </div>
      )}

      <div className="dsec">
        {progress ? (
          <div className="mono" style={{ fontSize: 11.5, color: 'var(--ink2)' }}>publishing {progress}…</div>
        ) : staged.length > 0 ? (
          <>
            <div style={{ display: 'flex', gap: 7 }}>
              <input className="fin" style={{ width: 64, textAlign: 'center' }} placeholder={String(staged.length)}
                value={typed} onChange={e => setTyped(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && typed.trim() === String(staged.length)) publishAll() }}
                aria-label="Type the publish count to confirm" />
              <button className="btn pri" style={{ flex: 1, justifyContent: 'center' }} onClick={() => {
                if (typed.trim() === String(staged.length)) publishAll()
                else toast.error(`Type ${staged.length} first — the count is the confirmation.`)
              }}>Publish {staged.length}</button>
            </div>
            <div style={{ fontSize: 10, color: 'var(--faint)', marginTop: 6, lineHeight: 1.5 }}>
              Snapshot <b className="mono">{version}</b> · immutable · writes offering prices, links, packaging &amp; deals — never variants, selling prices or stock.
            </div>
          </>
        ) : (
          <div style={{ fontSize: 11, color: 'var(--faint)' }}>Publish unlocks when something is staged.</div>
        )}
      </div>

      {(receipt.data?.count ?? 0) > 0 && (
        <div className="dsec" style={{ background: 'var(--good-soft)' }}>
          <div className="dh" style={{ color: 'var(--good)' }}>Live — this run has published</div>
          {receiptRows.map((change, index) => (
            <div key={index} className="sitem">
              <span className="sku">{change.sku_code ?? change.supplier_sku}</span>
              <span>{change.old_unit_cost != null ? `${fm(change.old_unit_cost)} → ` : 'first price '}<b>{fm(change.new_unit_cost)}</b></span>
              {change.sku_code && (
                <Link className="lnk" style={{ marginLeft: 'auto', fontSize: 10.5 }}
                  to={'/sku/$' as never} params={{ _splat: skuToPath(change.sku_code) } as never}>SKU →</Link>
              )}
            </div>
          ))}
          <Link className="lnk" style={{ fontSize: 11 }} to="/catalogues/review/$runId/commit" params={{ runId }}>full receipt →</Link>
        </div>
      )}

      {/* Products this run brought into existence. Called out separately from
          price changes — a new SKU in the catalogue is a bigger event than a
          cost moving, and it is the one thing here that cannot be undone by
          the next run. */}
      {(receipt.data?.created?.length ?? 0) > 0 && (
        <div className="dsec" style={{ background: 'var(--accent-soft)' }}>
          <div className="dh" style={{ color: 'var(--accent-ink)' }}>
            {receipt.data!.created!.length} product{receipt.data!.created!.length === 1 ? '' : 's'} created
          </div>
          {receipt.data!.created!.slice(0, 4).map(created => (
            <div key={created.sku_code} className="sitem">
              <span className="sku">{created.sku_code}</span>
              <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{created.name}</span>
              <Link className="lnk" style={{ marginLeft: 'auto', fontSize: 10.5 }}
                to={'/sku/$' as never} params={{ _splat: skuToPath(created.sku_code) } as never}>SKU →</Link>
            </div>
          ))}
          <div style={{ fontSize: 10.5, color: 'var(--muted)', marginTop: 4 }}>
            No selling price or channel yet — priced on the SKU page.
          </div>
        </div>
      )}
    </div>
  )
}

// ── focus overlay: one candidate, suggestions-first, reason chips ───────────
function FocusOverlay({ runId, lane, queue, currentId, allItems, onMove, onClose }: {
  runId: string
  lane: LaneId
  queue: SummaryItem[]
  currentId: string
  allItems: SummaryItem[]
  onMove: (id: string) => void
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const current = queue.find(i => i.mastering_candidate_id === currentId) ?? queue[0]
  const position = current ? queue.indexOf(current) : -1
  const nextPending = queue.find((i, index) => index > position && !isDecided(i)) ?? queue.find(i => !isDecided(i) && i !== current)

  const detail = useQuery({
    queryKey: ['review-detail', runId, current?.mastering_candidate_id],
    queryFn: () => fetchDetail(runId, current!.mastering_candidate_id),
    enabled: !!current,
  })
  useEffect(() => {
    if (nextPending) {
      queryClient.prefetchQuery({
        queryKey: ['review-detail', runId, nextPending.mastering_candidate_id],
        queryFn: () => fetchDetail(runId, nextPending.mastering_candidate_id),
      })
    }
  }, [runId, nextPending?.mastering_candidate_id, queryClient])

  // suggestions: auto-searched from what the run already knows — family/name,
  // never the raw supplier SKU (that search dead-ends).
  const [sugg, setSugg] = useState<VariantHit[] | null>(null)
  const [picked, setPicked] = useState<VariantHit | null>(null)
  const [searching, setSearching] = useState(false)
  const [query, setQuery] = useState('')
  const [hits, setHits] = useState<VariantHit[]>([])
  const [chip, setChip] = useState<string>(REASON_CHIPS[0])
  const [note, setNote] = useState('')
  const [noteOpen, setNoteOpen] = useState(false)
  const [drafting, setDrafting] = useState(false)
  const reason = note.trim() ? `${chip} — ${note.trim()}` : chip

  useEffect(() => {
    setPicked(null); setSugg(null); setQuery(''); setHits([]); setNote(''); setNoteOpen(false); setSearching(false)
    setDrafting(false)
    if (!current) return
    const terms = suggestTerms(current)
    if (terms.length < 2) { setSugg([]); return }
    searchVariants(runId, terms, 3).then(r => setSugg(r.results)).catch(() => setSugg([]))
  }, [current?.mastering_candidate_id])   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const q = query.trim()
    if (q.length < 2) { setHits([]); return }
    const timer = setTimeout(() => {
      searchVariants(runId, q, 6).then(r => setHits(r.results)).catch(() => setHits([]))
    }, 250)
    return () => clearTimeout(timer)
  }, [runId, query])

  const clusterRows = current
    ? allItems.filter(i =>
        i.mastering_candidate_id !== current.mastering_candidate_id
        && groupOf(i) === 'unmatched' && !isDecided(i)
        && i.family_key != null && i.family_key === current.family_key)
    : []

  async function decide(statusValue: SummaryItem['review_status']) {
    if (!current) return
    try {
      await decideCandidate(runId, current.mastering_candidate_id, statusValue, reason)
      queryClient.setQueryData(['review-summary', runId], (old: any) => old && ({
        ...old,
        items: old.items.map((i: SummaryItem) =>
          i.mastering_candidate_id === current.mastering_candidate_id ? { ...i, review_status: statusValue } : i),
      }))
      queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
      toast.success(statusValue === 'APPROVED' ? `${current.supplier_sku} staged — it publishes from the dock` : `${current.supplier_sku} rejected`)
      if (nextPending) onMove(nextPending.mastering_candidate_id)
      else onClose()
    } catch (e: any) { toast.error(String(e?.message ?? e)) }
  }

  async function correct(variant: VariantHit) {
    if (!current) return
    try {
      const result = await correctVariantMatch(
        runId, current.mastering_candidate_id, reason,
        { sku_code: variant.sku_code, name: variant.name }, current.name,
      )
      await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
      const revision = (result as any)?.output_ids?.[0]
      toast.success(`Matched to ${variant.sku_code} — approve to stage it`)
      if (typeof revision === 'string') onMove(revision)
    } catch (e: any) { toast.error(String(e?.message ?? e)) }
  }

  // keyboard: J/K move · 1-3 pick · A approve · R reject · Esc close
  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      const target = event.target as HTMLElement
      if (target && /^(INPUT|TEXTAREA|SELECT)$/.test(target.tagName)) {
        if (event.key === 'Escape') (target as HTMLInputElement).blur()
        return
      }
      if (event.key === 'Escape') { onClose(); return }
      if (!current) return
      if (event.key === 'j' || event.key === 'J') onMove(queue[Math.min(position + 1, queue.length - 1)].mastering_candidate_id)
      if (event.key === 'k' || event.key === 'K') onMove(queue[Math.max(position - 1, 0)].mastering_candidate_id)
      if (['1', '2', '3'].includes(event.key)) {
        const list = query.trim().length >= 2 ? hits : (sugg ?? [])
        const hit = list[Number(event.key) - 1]
        if (hit) setPicked(prev => (prev?.sku_code === hit.sku_code ? null : hit))
      }
      if ((event.key === 'a' || event.key === 'A')) {
        if (picked) correct(picked)
        else if (canApprove(current)) decide('APPROVED')
      }
      if ((event.key === 'r' || event.key === 'R') && !isDecided(current)) decide('REJECTED')
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  })

  if (!current) return null
  const evidence = detail.data?.evidence?.[0]
  const list = query.trim().length >= 2 ? hits : (sugg ?? [])
  const laneLabel = lane === 'pick' ? 'Needs a pick' : lane === 'new' ? 'New to us' : lane === 'clean' ? 'Clean' : 'Check by hand'
  const pendingLeft = queue.filter(i => !isDecided(i)).length

  return (
    <div className="rdesk-ovl" onClick={onClose}>
      <div className="rdesk-focus rdesk" onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <span className="lnk" onClick={onClose}>← Desk</span>
          <span className="bdg warn">{laneLabel} · {pendingLeft} left</span>
          <span className="prog" style={{ width: 140 }}><i style={{ width: `${queue.length ? ((position + 1) / queue.length) * 100 : 0}%` }} /></span>
          <span className="mono" style={{ fontSize: 11, color: 'var(--faint)' }}>{position + 1}/{queue.length}</span>
          <span style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--faint)' }}>
            <span className="kbd">J</span>/<span className="kbd">K</span> move · <span className="kbd">1</span>–<span className="kbd">3</span> pick · <span className="kbd">A</span> approve · <span className="kbd">R</span> reject · <span className="kbd">Esc</span>
          </span>
        </div>

        <div style={{ marginTop: 10 }}>
          <b style={{ fontSize: 15, color: 'var(--ink)' }}>{current.name ?? current.supplier_sku}</b>
          <span className="mono" style={{ fontSize: 11, color: 'var(--faint)', marginLeft: 8 }}>
            {current.supplier_sku}{current.page ? ` · page ${current.page}` : ''}
          </span>
          {isDecided(current) && <span className="bdg neu" style={{ marginLeft: 8 }}>{decidedLabel(current)}</span>}
        </div>

        <div className="fgrid">
          {/* unified evidence card */}
          <div className="panel" style={{ background: 'var(--card)' }}>
            <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--line2)', fontSize: 10, fontWeight: 750, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--faint)' }}>
              Supplier said — verbatim{evidence?.page ? ` · page ${evidence.page}` : ''}
            </div>
            {detail.isLoading ? <div style={{ padding: 16 }}><Spinner /></div> : (
              <>
                <table className="ev"><tbody>
                  {(evidence?.cells ?? []).map((cell: any, index: number) => (
                    <tr key={index}>
                      <td className="k">{cell.column_name ?? `col ${index + 1}`}</td>
                      <td className="v">{cell.value == null ? '' : String(cell.value)}</td>
                    </tr>
                  ))}
                  {evidence && !evidence.cells?.length && (
                    <tr><td colSpan={2} className="v mono" style={{ fontSize: 11.5, whiteSpace: 'pre-wrap' }}>{evidence.raw_text}</td></tr>
                  )}
                </tbody></table>
                <div className="readas">
                  Read as: <b>{current.name ?? '—'}</b> · {current.cost_amount != null ? `${current.cost_amount.toFixed(2)} ${current.cost_currency ?? ''}${current.cost_basis ? `/${current.cost_basis.toLowerCase()}` : ''}` : 'no cost'} · code {current.supplier_sku ?? '—'}
                  {current.barcode ? ` · barcode ${current.barcode}` : ''}
                </div>
              </>
            )}
          </div>

          {/* decision rail — replaced by the draft panel while creating, so the
              reviewer is doing one thing at a time */}
          {drafting ? (
            <CreateDraftPanel
              runId={runId}
              item={current}
              onCancel={() => setDrafting(false)}
              onCreated={async revision => {
                setDrafting(false)
                // Await the refetch: the revision is a NEW candidate, so moving
                // to it before the summary carries it lands on a stale row that
                // still reads as undecided.
                await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
                if (revision) onMove(revision)
              }}
            />
          ) : (
          <div className="panel" style={{ background: 'var(--card)' }}>
            <div style={{ padding: '8px 12px 6px', fontSize: 10, fontWeight: 750, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--faint)' }}>
              {groupOf(current) === 'matched' ? <>Matched → <span className="mono">{current.canonical_sku}</span> — confirm or re-pick</> : 'Best matches — pick one'}
            </div>
            <div style={{ padding: '0 11px 4px' }}>
              {sugg === null && query.trim().length < 2 && <div style={{ padding: 8 }}><Spinner size={14} /></div>}
              {list.map((hit, index) => {
                const delta = hit.offering_cost != null && current.cost_amount != null && hit.offering_cost !== 0
                  ? Math.round(((current.cost_amount - hit.offering_cost) / hit.offering_cost) * 1000) / 10 : null
                const margin = marginPct(current.cost_amount, hit.selling_price)
                const isOn = picked?.sku_code === hit.sku_code
                return (
                  <div key={hit.sku_code} className={`sugg${isOn ? ' on' : ''}`} onClick={() => setPicked(isOn ? null : hit)}>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'baseline' }}>
                      {index < 3 && <span className="kbd">{index + 1}</span>}
                      <span className="nm">{hit.name}</span>
                      <span className="mono" style={{ marginLeft: 'auto', fontSize: 10.5, color: 'var(--faint)' }}>{hit.sku_code}</span>
                    </div>
                    <div className="meta">
                      {hit.offering_cost != null && current.cost_amount != null
                        ? <>cost {hit.offering_cost.toFixed(2)} → {current.cost_amount.toFixed(2)} · <span style={{ color: delta != null && Math.abs(delta) > PULL_THRESHOLD_PCT ? 'var(--amber)' : 'var(--good)', fontWeight: 700 }}>{fmtDelta(delta)}</span></>
                        : 'no current cost for this supplier'}
                      {hit.selling_price != null && <> · sells {hit.selling_price.toFixed(2)} → margin <span style={{ color: margin != null && margin < 10 ? 'var(--red)' : 'var(--good)', fontWeight: 700 }}>{margin ?? '—'}%</span></>}
                    </div>
                  </div>
                )
              })}
              {sugg !== null && list.length === 0 && (
                <div style={{ fontSize: 11.5, color: 'var(--faint)', padding: '4px 2px 8px' }}>Nothing scored above the noise — search below{groupOf(current) === 'unmatched' ? ', or draft it as a new product' : ''}.</div>
              )}
              <input className="fin" placeholder="Search all variants — sku, name, brand…" value={query} onChange={e => setQuery(e.target.value)} />
            </div>

            <div style={{ padding: '8px 12px', borderTop: '1px solid var(--line2)' }}>
              <div style={{ fontSize: 10, fontWeight: 750, letterSpacing: '.06em', textTransform: 'uppercase', color: 'var(--faint)', marginBottom: 6 }}>Why</div>
              <div className="chiprow">
                {REASON_CHIPS.map(reasonChip => (
                  <span key={reasonChip} className={`rchip${chip === reasonChip ? ' on' : ''}`} onClick={() => setChip(reasonChip)}>{reasonChip}</span>
                ))}
                <span className={`rchip${noteOpen ? ' on' : ''}`} onClick={() => setNoteOpen(open => !open)}>+ note</span>
              </div>
              {noteOpen && (
                <input className="fin" style={{ marginTop: 7 }} placeholder="Anything unusual worth remembering…" value={note} onChange={e => setNote(e.target.value)} />
              )}
            </div>

            <div style={{ padding: '10px 12px', borderTop: '1px solid var(--line2)', display: 'flex', gap: 7, flexWrap: 'wrap', alignItems: 'center' }}>
              {picked ? (
                <button className="btn pri" onClick={() => correct(picked)}>Match → {picked.sku_code}</button>
              ) : (
                <button className="btn pri" disabled={!canApprove(current)}
                  title={canApprove(current) ? 'Approve — stages for publish' : current.blocking_issues > 0 ? 'Blocked by validation issues' : 'Pick a match, or draft a new product'}
                  onClick={() => decide('APPROVED')}>
                  {canApprove(current) ? 'Approve → staged' : 'Approve (decide this row first)'}
                </button>
              )}
              {/* Deliberately quiet, and worded as a claim the reviewer has to
                  be willing to make. Most rows in this lane are a missing link,
                  not a missing product. */}
              {groupOf(current) === 'unmatched' && !isDecided(current) && !picked && current.name && (
                <button className="btn" style={{ borderStyle: 'dashed', color: 'var(--muted)' }}
                  onClick={() => setDrafting(true)}>
                  Not in the catalogue…
                </button>
              )}
              <button className="btn" disabled={isDecided(current)} style={{ color: 'var(--red)', borderColor: '#F1CDC9' }} onClick={() => decide('REJECTED')}>Reject</button>
              <span style={{ fontSize: 10, color: 'var(--faint)', marginLeft: 'auto' }}>append-only · corrections = new revision</span>
            </div>
          </div>
          )}
        </div>

        {!drafting && clusterRows.length > 0 && (
          <div style={{ marginTop: 10, background: 'var(--accent-soft)', border: '1px solid var(--accent-line)', borderRadius: 10, padding: '8px 13px', fontSize: 11.5, color: 'var(--accent-ink)' }}>
            <ClusterBlock runId={runId} anchor={current} rows={clusterRows} />
          </div>
        )}
      </div>
    </div>
  )
}

// Family-pattern batch (ported from the room): propose the top variant per
// sibling, reviewer confirms the list, corrections fire as revisions.
function ClusterBlock({ runId, anchor, rows }: { runId: string; anchor: SummaryItem; rows: SummaryItem[] }) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [proposals, setProposals] = useState<Record<string, VariantHit | null> | null>(null)
  const [checked, setChecked] = useState<Record<string, boolean>>({})
  const [progress, setProgress] = useState<string | null>(null)

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setProposals(null)
    Promise.all(rows.map(async row => {
      const terms = suggestTerms(row)
      if (terms.length < 2) return [row.mastering_candidate_id, null] as const
      const found = await searchVariants(runId, terms, 1).catch(() => ({ results: [] as VariantHit[] }))
      return [row.mastering_candidate_id, found.results[0] ?? null] as const
    })).then(entries => {
      if (cancelled) return
      setProposals(Object.fromEntries(entries))
      setChecked(Object.fromEntries(entries.map(([id, hit]) => [id, hit != null])))
    })
    return () => { cancelled = true }
  }, [open, runId, rows.map(r => r.mastering_candidate_id).join(',')])   // eslint-disable-line react-hooks/exhaustive-deps

  const selected = rows.filter(row => checked[row.mastering_candidate_id] && proposals?.[row.mastering_candidate_id])

  async function applyAll() {
    setProgress(`0 / ${selected.length}`)
    const { failures } = await fanOut(
      selected,
      row => correctVariantMatch(
        runId, row.mastering_candidate_id, 'Family-pattern correction (reviewer-confirmed list)',
        { sku_code: proposals![row.mastering_candidate_id]!.sku_code, name: proposals![row.mastering_candidate_id]!.name },
        row.name,
      ),
      (done, total) => setProgress(`${done} / ${total}`),
    )
    setProgress(null)
    await queryClient.invalidateQueries({ queryKey: ['review-summary', runId] })
    if (failures.length) toast.error(`${failures.length} corrections failed — first: ${failures[0].error}`)
    else toast.success(`Matched ${selected.length} rows in the family — approve them to stage`)
  }

  return (
    <>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <b>{rows.length} more “{anchor.family_key}” rows have no offering</b> — match this one, then apply the pattern (you confirm the list).
        <button className="btn sm" style={{ marginLeft: 'auto' }} onClick={() => setOpen(o => !o)}>{open ? 'Hide' : 'Preview'} family</button>
      </div>
      {open && (proposals === null ? <div style={{ padding: 8 }}><Spinner size={14} /></div> : (
        <div style={{ marginTop: 7 }}>
          {rows.map(row => {
            const hit = proposals[row.mastering_candidate_id]
            const delta = hit?.offering_cost != null && row.cost_amount != null && hit.offering_cost !== 0
              ? Math.round(((row.cost_amount - hit.offering_cost) / hit.offering_cost) * 1000) / 10 : null
            return (
              <label key={row.mastering_candidate_id} style={{ display: 'flex', gap: 8, alignItems: 'center', padding: '3px 0', fontSize: 11.5, cursor: hit ? 'pointer' : 'default', color: 'var(--ink2)' }}>
                <input type="checkbox" checked={!!checked[row.mastering_candidate_id]} disabled={!hit}
                  onChange={e => setChecked(prev => ({ ...prev, [row.mastering_candidate_id]: e.target.checked }))} />
                <span className="mono" style={{ fontSize: 10.5 }}>{row.supplier_sku}</span>
                <span style={{ overflow: 'hidden', whiteSpace: 'nowrap', textOverflow: 'ellipsis', maxWidth: 300 }}>{row.name}</span>
                {hit
                  ? <span className="mono" style={{ fontSize: 10.5, color: delta != null && Math.abs(delta) > PULL_THRESHOLD_PCT ? 'var(--amber)' : 'var(--good)' }}>→ {hit.sku_code}{delta != null ? ` · ${fmtDelta(delta)}` : ''}</span>
                  : <span className="mono" style={{ fontSize: 10.5, color: 'var(--faint)' }}>no confident match</span>}
              </label>
            )
          })}
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 6 }}>
            {progress
              ? <span className="mono" style={{ fontSize: 11 }}>matching {progress}…</span>
              : <button className="btn pri sm" disabled={!selected.length} onClick={applyAll}>Match {selected.length} confirmed rows</button>}
            <span style={{ fontSize: 10.5 }}>Each is its own revision — nothing approved yet.</span>
          </div>
        </div>
      ))}
    </>
  )
}

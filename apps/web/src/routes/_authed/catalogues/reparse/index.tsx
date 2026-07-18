// Catalogue re-parse — inbox + launcher. The "↻ Re-parse" nav lands here.
//  · In progress: every OPEN re-parse (resumable, not just the latest), filterable by supplier,
//    with a search that finds a re-parsed SKU across all of them and jumps straight to it.
//  · Start a new re-parse: by supplier, by one of that supplier's uploaded files, or by a specific SKU.
// Starting a re-parse supersedes any prior re-parse of the SAME SKUs; other suppliers stay open.
import { useCallback, useEffect, useMemo, useState } from 'react'
import { createFileRoute, Link, useNavigate } from '@tanstack/react-router'
import { Spinner } from '@/components/Spinner'
import { toast } from '@/lib/toast'
import { getOpenReparses, startReparse, getSupplierImports,
  type OpenReparse, type ReparseHit, type ReparseScope, type CatalogueFile } from '@/lib/reparse'
import { getSuppliers, getProducts } from '@/lib/api'
import type { Supplier, Product } from '@/lib/types'

export const Route = createFileRoute('/_authed/catalogues/reparse/')({ component: ReparseHubPage })

const C = {
  panel: '#FFFFFF', ink: '#0F172A', sub: '#475569', faint: '#94A3B8', line: '#E2E8F0',
  indigo: '#6366F1', indigoBg: '#EEF0FE', indigoInk: '#4338CA', indigoLine: '#C7D2FE',
  amber: '#B45309', amberBg: '#FEF6E7', amberLine: '#FCD9A6', monoBg: '#F1F5F9',
}
const MONO = 'ui-monospace, "SF Mono", Menlo, monospace'
const SCOPE_KIND: Record<ReparseScope, string> = { supplier: 'Supplier', import: 'Import', item: 'SKU' }

function ago(iso: string): string {
  const t = new Date(iso).getTime()
  if (!t) return ''
  const s = Math.max(0, (Date.now() - t) / 1000)
  if (s < 90) return 'just now'
  const m = s / 60; if (m < 60) return `${Math.round(m)}m ago`
  const h = m / 60; if (h < 24) return `${Math.round(h)}h ago`
  return `${Math.round(h / 24)}d ago`
}

const sectionTitle: React.CSSProperties = { fontSize: '13px', fontWeight: 750, color: C.ink, letterSpacing: '0.01em' }
const label: React.CSSProperties = {
  fontSize: '10.5px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.06em',
}
const card: React.CSSProperties = { background: C.panel, border: `1px solid ${C.line}`, borderRadius: '12px', padding: '16px 18px' }
const primaryBtn = (disabled: boolean): React.CSSProperties => ({
  display: 'inline-flex', alignItems: 'center', gap: '6px', fontSize: '12.5px', fontWeight: 650, fontFamily: 'inherit',
  color: '#fff', background: C.indigo, border: '1px solid transparent', borderRadius: '8px', padding: '8px 14px',
  cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.55 : 1, whiteSpace: 'nowrap',
})
const field: React.CSSProperties = {
  fontFamily: 'inherit', fontSize: '13px', color: C.ink, background: C.panel,
  border: `1px solid ${C.line}`, borderRadius: '8px', padding: '8px 11px', width: '100%',
}
const miniSelect: React.CSSProperties = {
  fontFamily: 'inherit', fontSize: '12px', color: C.sub, background: C.panel,
  border: `1px solid ${C.line}`, borderRadius: '7px', padding: '4px 8px', cursor: 'pointer',
}
const kindChip: React.CSSProperties = {
  fontSize: '10.5px', fontWeight: 700, color: C.indigoInk, background: C.indigoBg,
  border: `1px solid ${C.indigoLine}`, borderRadius: '5px', padding: '1px 7px', whiteSpace: 'nowrap',
}
const rowBox: React.CSSProperties = {
  display: 'flex', alignItems: 'center', gap: '11px', background: C.panel,
  border: `1px solid ${C.line}`, borderRadius: '10px', padding: '11px 13px',
}

function ReparseHubPage() {
  const navigate = useNavigate()
  const [initLoading, setInitLoading] = useState(true)
  const [suppliers, setSuppliers] = useState<Supplier[]>([])          // all suppliers (start-new picker)
  const [openSuppliers, setOpenSuppliers] = useState<{ id: number; name: string }[]>([])  // only those with an open re-parse

  // inbox
  const [inbox, setInbox] = useState<{ batches: OpenReparse[]; items: ReparseHit[] }>({ batches: [], items: [] })
  const [inboxLoading, setInboxLoading] = useState(false)
  const [inboxSupplier, setInboxSupplier] = useState('')             // '' = all
  const [inboxQ, setInboxQ] = useState('')

  // start-new
  const [newSupplierId, setNewSupplierId] = useState('')
  const [files, setFiles] = useState<CatalogueFile[] | null>(null)   // selected supplier's uploaded files
  const [skuQ, setSkuQ] = useState('')
  const [skuResults, setSkuResults] = useState<Product[] | null>(null)
  const [skuSearching, setSkuSearching] = useState(false)

  const [busy, setBusy] = useState<string | null>(null)              // key of the action being started

  const rememberOpenSuppliers = useCallback((batches: OpenReparse[]) => {
    const m = new Map<number, string>()
    for (const b of batches) if (b.supplier_id != null) m.set(b.supplier_id, b.supplier_name || `#${b.supplier_id}`)
    setOpenSuppliers([...m].map(([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name)))
  }, [])

  // Initial: the inbox (unfiltered) + the supplier list.
  useEffect(() => {
    let alive = true
    Promise.allSettled([getOpenReparses(), getSuppliers()]).then(([ob, sup]) => {
      if (!alive) return
      if (ob.status === 'fulfilled') { setInbox(ob.value); rememberOpenSuppliers(ob.value.batches) }
      if (sup.status === 'fulfilled') setSuppliers([...sup.value].filter(s => s.name).sort((a, b) => a.name.localeCompare(b.name)))
      setInitLoading(false)
    })
    return () => { alive = false }
  }, [rememberOpenSuppliers])

  // Re-fetch the inbox when its filter or (debounced) search changes.
  useEffect(() => {
    if (initLoading) return
    const supplier = inboxSupplier ? Number(inboxSupplier) : null
    const q = inboxQ.trim()
    let alive = true
    setInboxLoading(true)
    const t = setTimeout(() => {
      getOpenReparses({ supplier, q })
        .then(r => { if (!alive) return; setInbox(r); if (!supplier && !q) rememberOpenSuppliers(r.batches) })
        .catch(() => { if (alive) toast.error('Could not load re-parses') })
        .finally(() => { if (alive) setInboxLoading(false) })
    }, q ? 300 : 0)                                                  // debounce typing; instant on filter change
    return () => { alive = false; clearTimeout(t) }
  }, [inboxSupplier, inboxQ, initLoading, rememberOpenSuppliers])

  // Start-new SKU search (searches ALL products, debounced).
  useEffect(() => {
    const term = skuQ.trim()
    if (term.length < 2) { setSkuResults(null); setSkuSearching(false); return }
    let alive = true
    setSkuSearching(true)
    const t = setTimeout(() => {
      getProducts({ search: term, limit: '15' })
        .then(r => { if (alive) setSkuResults(r.items) })
        .catch(() => { if (alive) setSkuResults([]) })
        .finally(() => { if (alive) setSkuSearching(false) })
    }, 300)
    return () => { alive = false; clearTimeout(t) }
  }, [skuQ])

  // When a supplier is picked for "start new", load its uploaded files so one can be re-parsed on its own.
  useEffect(() => {
    const sid = newSupplierId ? Number(newSupplierId) : null
    if (!sid) { setFiles(null); return }
    let alive = true
    setFiles(null)
    getSupplierImports(sid).then(fs => { if (alive) setFiles(fs) }).catch(() => { if (alive) setFiles([]) })
    return () => { alive = false }
  }, [newSupplierId])

  const launch = useCallback(async (scope: ReparseScope, ref: string | number, key: string) => {
    if (busy) return
    setBusy(key)
    try {
      const batch = await startReparse(scope, ref)
      navigate({ to: `/catalogues/reparse/${batch.id}` as never })    // navigate to the review (leave busy set)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Could not start re-parse'
      toast.error(/no catalogue items/i.test(msg) ? 'No catalogue items to re-parse in this scope.' : msg)
      setBusy(null)
    }
  }, [busy, navigate])

  const selectedSupplier = useMemo(() => suppliers.find(s => String(s.id) === newSupplierId) ?? null, [suppliers, newSupplierId])
  const searching = inboxQ.trim().length > 0
  const { batches, items } = inbox

  return (
    <div style={{ maxWidth: '760px', margin: '0 auto', padding: '4px 2px 28px' }}>
      <div style={{ marginBottom: '14px', fontSize: '12.5px', display: 'flex', gap: '7px', alignItems: 'center' }}>
        <Link to={'/catalogues' as never} style={{ color: C.indigoInk, fontWeight: 600, textDecoration: 'none' }}>← Catalogues</Link>
        <span style={{ color: '#C2C8D2' }}>/</span>
        <span style={{ color: '#334155' }}>Re-parse</span>
      </div>

      <h1 style={{ fontSize: '19px', fontWeight: 750, color: C.ink, margin: '0 0 3px' }}>Re-parse</h1>
      <p style={{ fontSize: '13px', color: C.sub, margin: '0 0 20px' }}>
        Re-derive catalogue fields from retained text, review the changes, then apply.
      </p>

      {initLoading ? (
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', color: C.sub, fontSize: '13px', padding: '32px 4px' }}>
          <Spinner size={14} color={C.indigo} /> Loading…
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '26px' }}>

          {/* ── In progress ─────────────────────────────────────────── */}
          <section>
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '10px' }}>
              <span style={sectionTitle}>In progress</span>
              {!searching && <span style={{ ...kindChip, color: C.sub, background: C.monoBg, borderColor: C.line }}>{batches.length}</span>}
              <span style={{ flex: 1 }} />
              {(openSuppliers.length > 0 || inboxSupplier) && (
                <select value={inboxSupplier} onChange={e => setInboxSupplier(e.target.value)} style={miniSelect} aria-label="Filter by supplier">
                  <option value="">All suppliers</option>
                  {openSuppliers.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
                </select>
              )}
            </div>

            <div style={{ position: 'relative', marginBottom: '11px' }}>
              <input
                value={inboxQ} onChange={e => setInboxQ(e.target.value)} spellCheck={false} autoComplete="off"
                placeholder="Find a re-parsed SKU — code, name, brand, category…" style={field}
              />
              {inboxLoading && searching && <span style={{ position: 'absolute', right: '11px', top: '9px' }}><Spinner size={13} color={C.faint} /></span>}
            </div>

            {/* Search hits */}
            {searching ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
                {items.length === 0 && !inboxLoading && (
                  <div style={{ fontSize: '12.5px', color: C.faint, padding: '8px 2px' }}>No re-parsed SKU matches “{inboxQ.trim()}”.</div>
                )}
                {items.map(h => (
                  <Link key={`${h.batch_id}:${h.catalogue_item_id}`} to={`/catalogues/reparse/${h.batch_id}?item=${h.catalogue_item_id}` as never} style={{ textDecoration: 'none' }}>
                    <div style={{ ...rowBox, cursor: 'pointer' }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontFamily: MONO, fontSize: '11px', color: C.faint, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.sku_code ?? '—'}</div>
                        <div style={{ fontSize: '13px', fontWeight: 650, color: C.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.product_name}</div>
                        <div style={{ fontSize: '11px', color: C.faint, marginTop: '1px' }}>
                          in {h.supplier_name || 'a re-parse'} · {h.changed_count} change{h.changed_count === 1 ? '' : 's'}
                        </div>
                      </div>
                      <span style={{ fontSize: '11.5px', fontWeight: 650, color: C.indigoInk, whiteSpace: 'nowrap' }}>Go to SKU →</span>
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              /* Open re-parse cards */
              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {batches.length === 0 ? (
                  <div style={{ ...card, textAlign: 'center', color: C.faint, fontSize: '12.5px', padding: '22px 18px' }}>
                    No re-parse in progress{inboxSupplier ? ' for this supplier' : ''}. Start one below.
                  </div>
                ) : batches.map(b => (
                  <Link key={b.id} to={`/catalogues/reparse/${b.id}` as never} style={{ textDecoration: 'none' }}>
                    <div style={{ ...rowBox, borderColor: C.amberLine, background: C.amberBg, cursor: 'pointer' }}>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', overflow: 'hidden' }}>
                          <span style={kindChip}>{SCOPE_KIND[b.scope_type]}</span>
                          <span style={{ fontSize: '14px', fontWeight: 700, color: C.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{b.title || b.supplier_name || `#${b.scope_ref}`}</span>
                        </div>
                        <div style={{ fontSize: '12px', color: C.sub, marginTop: '3px' }}>
                          <b style={{ color: C.ink }}>{b.changed_count}</b> change{b.changed_count === 1 ? '' : 's'} · {b.pending_items} SKU{b.pending_items === 1 ? '' : 's'}
                          {b.created_at ? <span style={{ color: C.faint }}> · started {ago(b.created_at)}</span> : null}
                        </div>
                      </div>
                      <span style={{ ...primaryBtn(false), background: C.amber }}>Resume →</span>
                    </div>
                  </Link>
                ))}
              </div>
            )}
          </section>

          {/* ── Start a new re-parse ────────────────────────────────── */}
          <section>
            <div style={{ ...sectionTitle, marginBottom: '10px' }}>Start a new re-parse</div>
            <div style={{ ...card, display: 'flex', flexDirection: 'column', gap: '14px' }}>
              {/* supplier */}
              <div>
                <div style={{ ...label, marginBottom: '7px' }}>By supplier</div>
                <div style={{ display: 'flex', gap: '9px', alignItems: 'center' }}>
                  <select value={newSupplierId} onChange={e => setNewSupplierId(e.target.value)} style={{ ...field, flex: 1, cursor: 'pointer' }}>
                    <option value="">Select a supplier…</option>
                    {suppliers.map(s => <option key={s.id} value={s.id}>{s.name}{s.code ? ` (${s.code})` : ''}</option>)}
                  </select>
                  <button
                    style={primaryBtn(!selectedSupplier || busy !== null)} disabled={!selectedSupplier || busy !== null}
                    onClick={() => selectedSupplier && launch('supplier', selectedSupplier.id, 'supplier')}
                  >
                    {busy === 'supplier' ? <><Spinner size={11} color="#fff" /> Starting…</> : 'Re-parse'}
                  </button>
                </div>

                {/* per-file re-parse (import scope) — the supplier's uploaded catalogue files */}
                {newSupplierId && files !== null && (files.length === 0 ? (
                  <div style={{ fontSize: '12px', color: C.faint, marginTop: '8px' }}>No uploaded files for this supplier.</div>
                ) : (
                  <div style={{ marginTop: '10px' }}>
                    <div style={{ fontSize: '11px', color: C.faint, marginBottom: '5px' }}>…or re-parse just one file:</div>
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '5px' }}>
                      {files.map(f => {
                        const key = `import:${f.id}`
                        const isBusy = busy === key
                        return (
                          <button key={f.id} disabled={busy !== null} onClick={() => launch('import', f.id, key)}
                            style={{ display: 'flex', alignItems: 'center', gap: '9px', textAlign: 'left', width: '100%',
                              background: isBusy ? C.indigoBg : C.panel, border: `1px solid ${C.line}`, borderRadius: '8px',
                              padding: '7px 11px', cursor: busy ? 'not-allowed' : 'pointer', fontFamily: 'inherit' }}>
                            <span style={{ fontSize: '13px' }}>📄</span>
                            <span style={{ flex: 1, fontSize: '12.5px', color: C.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={f.filename}>{f.filename}</span>
                            <span style={{ fontSize: '11px', color: C.faint, whiteSpace: 'nowrap' }}>{f.item_count} item{f.item_count === 1 ? '' : 's'} · {(f.imported_at || '').slice(0, 10)}</span>
                            {isBusy ? <Spinner size={12} color={C.indigo} /> : <span style={{ fontSize: '11.5px', fontWeight: 650, color: C.indigoInk, whiteSpace: 'nowrap' }}>Re-parse →</span>}
                          </button>
                        )
                      })}
                    </div>
                  </div>
                ))}
              </div>

              <div style={{ borderTop: `1px solid ${C.line}` }} />

              {/* SKU */}
              <div>
                <div style={{ ...label, marginBottom: '7px' }}>By a specific SKU</div>
                <div style={{ position: 'relative' }}>
                  <input
                    value={skuQ} onChange={e => setSkuQ(e.target.value)} spellCheck={false} autoComplete="off"
                    placeholder="Search all SKUs by code, name, or brand…" style={field}
                  />
                  {skuSearching && <span style={{ position: 'absolute', right: '11px', top: '9px' }}><Spinner size={13} color={C.faint} /></span>}
                </div>
                {skuResults !== null && (
                  <div style={{ marginTop: '9px', display: 'flex', flexDirection: 'column', gap: '5px' }}>
                    {skuResults.length === 0 && !skuSearching && (
                      <div style={{ fontSize: '12.5px', color: C.faint, padding: '6px 2px' }}>No SKUs match “{skuQ.trim()}”.</div>
                    )}
                    {skuResults.map(p => {
                      const key = `sku:${p.sku_code}`
                      const isBusy = busy === key
                      return (
                        <button
                          key={p.id} disabled={busy !== null} onClick={() => launch('item', p.sku_code, key)}
                          style={{
                            display: 'flex', alignItems: 'center', gap: '10px', textAlign: 'left', width: '100%',
                            background: isBusy ? C.indigoBg : C.panel, border: `1px solid ${C.line}`, borderRadius: '8px',
                            padding: '8px 11px', cursor: busy ? 'not-allowed' : 'pointer', fontFamily: 'inherit',
                          }}
                        >
                          <span style={{ fontFamily: MONO, fontSize: '11px', color: C.faint, minWidth: '110px', maxWidth: '160px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.sku_code}</span>
                          <span style={{ flex: 1, fontSize: '13px', color: C.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{p.name}</span>
                          {isBusy ? <Spinner size={12} color={C.indigo} /> : <span style={{ fontSize: '11.5px', fontWeight: 650, color: C.indigoInk, whiteSpace: 'nowrap' }}>Re-parse →</span>}
                        </button>
                      )
                    })}
                  </div>
                )}
              </div>
            </div>
          </section>

          <div style={{ fontSize: '11.5px', color: C.faint, lineHeight: 1.6, padding: '0 2px' }}>
            Re-parsing a SKU replaces any earlier re-parse of that same SKU — other suppliers’ re-parses stay open and resumable above. Nothing writes to live cost until you confirm a change.
          </div>
        </div>
      )}
    </div>
  )
}

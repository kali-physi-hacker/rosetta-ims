import { createFileRoute } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import { AM_ROWS, GAP_META, type GapStatus } from '@/data/am-walkthrough'

export const Route = createFileRoute('/_authed/am-walkthrough')({ component: AMWalkthroughPage })

function AMWalkthroughPage() {
  const [filter, setFilter] = useState<GapStatus | 'ALL'>('ALL')
  const [search, setSearch] = useState('')

  const counts = useMemo(() => {
    const c: Record<GapStatus, number> = {
      v7_reference: 0, v7_operational: 0, v7_config: 0, v7_proposed: 0,
      per_po: 0, am_formula: 0,
    }
    AM_ROWS.forEach(r => { c[r.gapStatus]++ })
    return c
  }, [])

  const filtered = useMemo(() => {
    return AM_ROWS.filter(r => {
      if (filter !== 'ALL' && r.gapStatus !== filter) return false
      if (search) {
        const s = search.toLowerCase()
        if (!r.amField.toLowerCase().includes(s) &&
            !r.amSection.toLowerCase().includes(s) &&
            !r.amSourceToday.toLowerCase().includes(s) &&
            !r.v7Column.toLowerCase().includes(s) &&
            !r.ultimateSource.toLowerCase().includes(s) &&
            !r.amCol.toLowerCase().includes(s)) return false
      }
      return true
    })
  }, [filter, search])

  const total = AM_ROWS.length
  const v7Total = counts.v7_reference + counts.v7_operational + counts.v7_config + counts.v7_proposed

  const downloadCSV = () => {
    const headers = ['AM_col','AM_section','AM_field','AM_source_today','v7_column','gap_status','ultimate_source','frequency_of_change','sam_notes']
    const escape = (s: string) => {
      const needs = s.includes(',') || s.includes('"') || s.includes('\n')
      const esc = s.replace(/"/g, '""')
      return needs ? `"${esc}"` : esc
    }
    const rows = AM_ROWS.map(r => [
      r.amCol, r.amSection, r.amField, r.amSourceToday, r.v7Column,
      `${GAP_META[r.gapStatus].emoji} ${GAP_META[r.gapStatus].label}`,
      r.ultimateSource, r.frequencyOfChange, r.samNotes,
    ].map(escape).join(','))
    const csv = [headers.join(','), ...rows].join('\n')
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' })
    const a = document.createElement('a')
    a.href = URL.createObjectURL(blob)
    a.download = 'sam-am-walkthrough.csv'
    a.click()
    URL.revokeObjectURL(a.href)
  }

  return (
    <>
      <style>{`
        .am-row:hover { background: #F8FAFC !important; }
        .am-chip { display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px; border-radius: 12px; font-size: 10px; font-weight: 700; white-space: nowrap; }
        .am-filter[data-active="true"] { box-shadow: 0 0 0 2px rgba(99,102,241,0.4); }
      `}</style>

      <div style={{ marginBottom: '14px' }}>
        <p style={{ fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '4px' }}>
          Sam's Column Migration Check
        </p>
        <h1 style={{ fontSize: '24px', fontWeight: 700, color: '#0F172A', marginBottom: '6px' }}>
          Biz Ops × v7 Column Walkthrough
        </h1>
        <p style={{ fontSize: '13px', color: '#475569', lineHeight: 1.55, maxWidth: '900px' }}>
          For each of the {total} Biz Ops columns, this page shows where the cell is populated today
          and where it would come from in the new architecture. <strong>{v7Total} of {total}</strong> columns
          map to Rosetta IMS (v7) — across reference / operational / config tables in the same database.
          {' '}The rest are per-PO entries or in-AM formulas.
        </p>
        <p style={{ fontSize: '11.5px', color: '#64748B', marginTop: '8px', maxWidth: '900px', fontStyle: 'italic' }}>
          Pre-filled by Claude. Sam: please override anything wrong and fill in the blank{' '}
          <code style={{ background: '#F1F5F9', padding: '1px 5px', borderRadius: '3px' }}>frequency_of_change</code> and
          <code style={{ background: '#F1F5F9', padding: '1px 5px', borderRadius: '3px', marginLeft: '4px' }}>sam_notes</code> cells.
          Edits go to <code style={{ background: '#F1F5F9', padding: '1px 5px', borderRadius: '3px' }}>frontend/src/data/am-walkthrough.ts</code> —
          ping Chris.
        </p>
      </div>

      {/* The architectural picture — corrected 2026-06-02 */}
      <div style={{
        background: '#F0FDF4', border: '1px solid #BBF7D0', borderLeft: '4px solid #16A34A',
        borderRadius: '8px', padding: '14px 18px', marginBottom: '14px', maxWidth: '900px',
      }}>
        <p style={{ fontSize: '11px', fontWeight: 700, color: '#166534', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
          ✓ Architectural framing (corrected 2026-06-02)
        </p>
        <p style={{ fontSize: '12.5px', color: '#14532D', lineHeight: 1.6, marginBottom: '8px' }}>
          The Logic Layer workbook has <strong>three distinct tabs that do different things</strong>:
        </p>
        <ul style={{ margin: '0 0 10px 18px', padding: 0 }}>
          <li style={{ fontSize: '12px', color: '#14532D', lineHeight: 1.7 }}>
            <a
              href="https://docs.google.com/spreadsheets/d/1PWcRMt0FIdUCxeFz9BxhBXpDyWsInidxAW753DNI2A4/edit?gid=1102115131"
              target="_blank"
              rel="noreferrer"
              style={{ color: '#14532D', textDecoration: 'underline', fontWeight: 700 }}
            >Approval Matrix tab ↗</a> — RULES text. Red/Amber/Green thresholds, exception logic.
          </li>
          <li style={{ fontSize: '12px', color: '#14532D', lineHeight: 1.7 }}>
            <strong>Biz Ops tab</strong> — the 85-col PO TRANSACTIONAL LOG. One row per PO. Pulls rules from the AM tab + SKU data from the old SSOT.
            This is the table mapped below.
          </li>
          <li style={{ fontSize: '12px', color: '#14532D', lineHeight: 1.7 }}>
            <strong>Data tab</strong> — the old SSOT cached/mirrored inside Logic Layer. Replaced by v7 in the new world.
          </li>
        </ul>
        <p style={{ fontSize: '12.5px', color: '#14532D', lineHeight: 1.6 }}>
          Formula-confirmed Biz Ops ← Data link (cell L3):{' '}
          <code style={{ background: 'white', padding: '1px 5px', borderRadius: '3px', fontSize: '11px' }}>
            =XLOOKUP(E3:E, Data!AU:AU, Data!BM:BM)
          </code>{' '}— so col L (Basic Unit Cost) pulls from Data!BM, col M (MBB Terms) from Data!BN.
        </p>
      </div>

      {/* The 4-layer model */}
      <div style={{
        background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px',
        padding: '14px 16px', marginBottom: '14px', maxWidth: '900px',
      }}>
        <p style={{ fontSize: '11px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
          The new architecture — one database, four layers
        </p>
        <p style={{ fontSize: '12px', color: '#0F172A', lineHeight: 1.6, marginBottom: '12px' }}>
          v7 = the Rosetta IMS database. It already has all the columns Biz Ops needs (SKU master, stock, demand,
          dispensing fees, expiration dates, hero SKU, channel fees, competitor prices). The right separation
          isn't "v7 vs another database" — it's <strong>different tables in v7 with different update cadences</strong>:
        </p>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginTop: '10px' }}>
          <div style={{ background: '#DCFCE7', borderRadius: '6px', padding: '10px 12px' }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: '#166534', marginBottom: '4px' }}>🟢 v7 — REFERENCE</p>
            <p style={{ fontSize: '11px', color: '#166534', lineHeight: 1.5 }}>
              Slow-changing SKU master: cost, MBB, weight, category, supplier, units-per-pack, hero SKU.
              Edited manually via <code style={{ background: 'rgba(255,255,255,0.5)', padding: '1px 4px', borderRadius: '2px' }}>/data-review</code>.
            </p>
          </div>
          <div style={{ background: '#DBEAFE', borderRadius: '6px', padding: '10px 12px' }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: '#1E40AF', marginBottom: '4px' }}>🔵 v7 — OPERATIONAL</p>
            <p style={{ fontSize: '11px', color: '#1E40AF', lineHeight: 1.5 }}>
              Daily-changing: stock, demand, JIT, autoship, expiration dates. <strong>Same database, faster cadence.</strong>{' '}
              Populated by Desmond's ingestion pipelines.
            </p>
          </div>
          <div style={{ background: '#EDE9FE', borderRadius: '6px', padding: '10px 12px' }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: '#5B21B6', marginBottom: '4px' }}>🟣 v7 — CONFIG</p>
            <p style={{ fontSize: '11px', color: '#5B21B6', lineHeight: 1.5 }}>
              SF Express logistics rate card. GP floors by category (proposed). Slow-changing, governed centrally.
            </p>
          </div>
          <div style={{ background: '#FED7AA', borderRadius: '6px', padding: '10px 12px' }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: '#7C2D12', marginBottom: '4px' }}>🟡 PER-PO (Biz Ops row)</p>
            <p style={{ fontSize: '11px', color: '#7C2D12', lineHeight: 1.5 }}>
              PO No., Requisition Date, Invoice No., Payment Date — typed by BizOps / Finance per PO. Today
              lives in Biz Ops Sheet. Tomorrow lives in a <code style={{ background: 'rgba(255,255,255,0.5)', padding: '1px 4px', borderRadius: '2px' }}>purchase_orders</code> table
              in Rosetta IMS — <em>same database as v7, different table</em>.
            </p>
          </div>
        </div>
        <p style={{ fontSize: '12px', color: '#0F172A', lineHeight: 1.6, marginTop: '12px' }}>
          Biz Ops queries one endpoint (Rosetta IMS API). One row per SKU comes back with everything joined
          server-side — cost AND current stock AND demand AND JIT. <strong>No "data from 2 places."</strong>
        </p>
      </div>

      {/* Desmond's scope reframed */}
      <div style={{
        background: '#EEF2FF', border: '1px solid #C7D2FE', borderRadius: '8px',
        padding: '14px 16px', marginBottom: '14px', maxWidth: '900px',
      }}>
        <p style={{ fontSize: '11px', fontWeight: 700, color: '#4338CA', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '8px' }}>
          Desmond's scope = ingestion pipelines into v7 (not a separate database)
        </p>
        <p style={{ fontSize: '12px', color: '#1E1B4B', lineHeight: 1.55, marginBottom: '10px' }}>
          His call-level concern ("SKU master isn't designed to hold daily-changing data") had merit — but the
          fix is separate <em>tables</em>, not separate <em>databases</em>. v7 spec already includes operational
          columns (stock_clinic, stock_warehouse, demand_120d_*, unfulfilled_jit, upcoming_14d_autoship,
          expiration_date). Desmond builds the pipelines that populate them:
        </p>
        <div style={{ background: 'white', border: '1px solid #C7D2FE', borderRadius: '6px', padding: '8px 12px', fontSize: '11.5px', color: '#1E1B4B', lineHeight: 1.7 }}>
          <div>📦 Shopify Admin API (warehouse stock) → <code>stock_warehouse</code></div>
          <div>📦 DaySmart Vet POS API → <code>stock_clinic</code></div>
          <div>📈 DaySmart + Shopify + HKTV daily sales → <code>demand_120d_*</code> (sum into total)</div>
          <div>⚡ Shopify webhooks (paid + unfulfilled) → <code>unfulfilled_jit</code></div>
          <div>🔁 Shopify subscriptions → <code>upcoming_14d_autoship</code></div>
          <div>📱 Supplier WhatsApp → BizOps manual entry → <code>expiration_date</code></div>
        </div>
        <p style={{ fontSize: '11.5px', color: '#1E1B4B', lineHeight: 1.55, marginTop: '10px' }}>
          Six pipelines, one destination. No second database to maintain.
        </p>
      </div>

      {/* Gaps surfaced — proposed v7 additions */}
      {counts.v7_proposed > 0 && (
        <div style={{
          background: '#FFFBEB', border: '1px solid #FDE68A', borderLeft: '4px solid #F59E0B',
          borderRadius: '8px', padding: '14px 16px', marginBottom: '14px', maxWidth: '900px',
        }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: '#92400E', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
            🆕 Gap analysis surfaced {counts.v7_proposed} column{counts.v7_proposed === 1 ? '' : 's'} that v7 doesn't have yet
          </p>
          <p style={{ fontSize: '12px', color: '#451A03', lineHeight: 1.55, marginBottom: '6px' }}>
            These are the only <em>real</em> gaps — Biz Ops uses them, but they're not in the current v7 SKU_MASTER spec.
            Filter the table below by <strong>🆕 v7 — PROPOSED</strong> to see them. Sam reviews, Chris decides whether
            to add to v7. Until then they remain documented gaps.
          </p>
          <p style={{ fontSize: '11.5px', color: '#7C2D12', lineHeight: 1.55, fontStyle: 'italic' }}>
            v7 the Google Sheet is NOT auto-edited. Any additions to v7 are made by Chris manually after review.
          </p>
        </div>
      )}

      {/* Stat strip */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '12px', flexWrap: 'wrap' }}>
        <Stat label="Total Biz Ops columns" value={String(total)} color="#0F172A" />
        {(Object.keys(GAP_META) as GapStatus[]).map(k => {
          const m = GAP_META[k]
          return (
            <button
              key={k}
              data-active={filter === k}
              onClick={() => setFilter(filter === k ? 'ALL' : k)}
              className="am-filter"
              style={{
                background: m.bg, color: m.fg, border: 'none', borderRadius: '8px',
                padding: '8px 12px', display: 'flex', flexDirection: 'column', alignItems: 'flex-start',
                cursor: 'pointer', textAlign: 'left', minWidth: '120px',
              }}
            >
              <span style={{ fontSize: '20px', fontWeight: 700 }}>{counts[k]}</span>
              <span style={{ fontSize: '10px', fontWeight: 700, marginTop: '2px' }}>
                {m.emoji} {m.label}
              </span>
            </button>
          )
        })}
      </div>

      {/* Search + reset */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px', flexWrap: 'wrap' }}>
        <input
          value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search by col, field, source…"
          style={{ fontSize: '11.5px', padding: '6px 10px', border: '1px solid #CBD5E1', borderRadius: '6px', width: '300px' }}
        />
        {(filter !== 'ALL' || search) && (
          <button onClick={() => { setFilter('ALL'); setSearch('') }}
                  style={{ fontSize: '11px', color: '#6366F1', background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600 }}>
            Clear filters
          </button>
        )}
        <span style={{ fontSize: '11px', color: '#64748B' }}>
          Showing {filtered.length} of {total}
        </span>
        <div style={{ flex: 1 }} />
        <button
          onClick={downloadCSV}
          style={{
            display: 'flex', alignItems: 'center', gap: '6px',
            background: '#0F172A', color: 'white', border: 'none',
            borderRadius: '6px', padding: '6px 12px', cursor: 'pointer',
            fontSize: '11.5px', fontWeight: 600,
          }}
        >
          <span>⤓</span><span>Download CSV</span>
        </button>
      </div>

      {/* Currently filtered status description */}
      {filter !== 'ALL' && (
        <div style={{
          background: GAP_META[filter].bg, color: GAP_META[filter].fg,
          border: `1px solid ${GAP_META[filter].fg}33`, borderRadius: '6px',
          padding: '8px 12px', marginBottom: '10px', fontSize: '11.5px',
        }}>
          <strong>{GAP_META[filter].emoji} {GAP_META[filter].label}:</strong> {GAP_META[filter].description}
        </div>
      )}

      {/* Table */}
      <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'hidden' }}>
        <div style={{
          display: 'grid',
          gridTemplateColumns: '50px 160px 200px 200px 220px 150px 1fr 120px',
          gap: '8px',
          fontSize: '9.5px', fontWeight: 700, color: '#94A3B8',
          textTransform: 'uppercase', letterSpacing: '0.06em',
          padding: '8px 10px', borderBottom: '1px solid #E2E8F0',
          background: '#F8FAFC', position: 'sticky', top: 0, zIndex: 1,
        }}>
          <span>Col</span>
          <span>Section</span>
          <span>Field</span>
          <span>Source today (Biz Ops)</span>
          <span>Maps to v7</span>
          <span>Status</span>
          <span>Ultimate source</span>
          <span>Freq + notes</span>
        </div>
        {filtered.map(r => {
          const m = GAP_META[r.gapStatus]
          return (
            <div
              key={r.amCol}
              className="am-row"
              style={{
                display: 'grid',
                gridTemplateColumns: '50px 160px 200px 200px 220px 150px 1fr 120px',
                gap: '8px',
                padding: '8px 10px',
                borderBottom: '1px solid #F1F5F9',
                fontSize: '11px', color: '#0F172A',
                alignItems: 'start',
              }}
            >
              <span style={{ fontFamily: 'ui-monospace, monospace', color: '#6366F1', fontWeight: 700 }}>
                {r.amCol}
              </span>
              <span style={{ color: '#475569', fontSize: '10px' }}>{r.amSection}</span>
              <span style={{ fontWeight: 600 }}>{r.amField}</span>
              <span style={{ color: '#64748B', fontSize: '10.5px' }}>{r.amSourceToday}</span>
              <span style={{ color: r.v7Column === '—' ? '#94A3B8' : '#1E40AF', fontFamily: r.v7Column === '—' ? 'inherit' : 'ui-monospace, monospace', fontSize: '10.5px' }}>
                {r.v7Column}
              </span>
              <span>
                <span className="am-chip" style={{ background: m.bg, color: m.fg }}>
                  {m.emoji} {m.label}
                </span>
              </span>
              <span style={{ color: '#0F172A', fontSize: '10.5px', lineHeight: 1.5 }}>{r.ultimateSource}</span>
              <span style={{ color: '#64748B', fontSize: '10px', lineHeight: 1.5 }}>
                {r.frequencyOfChange && <div style={{ fontWeight: 600 }}>↻ {r.frequencyOfChange}</div>}
                {r.samNotes && <div style={{ marginTop: '2px' }}>{r.samNotes}</div>}
                {!r.frequencyOfChange && !r.samNotes && <span style={{ color: '#CBD5E1' }}>—</span>}
              </span>
            </div>
          )
        })}
      </div>

      {/* Legend */}
      <div style={{ marginTop: '16px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '14px' }}>
        <p style={{ fontSize: '11px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
          What each status means
        </p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {(Object.keys(GAP_META) as GapStatus[]).map(k => {
            const m = GAP_META[k]
            return (
              <div key={k} style={{ display: 'flex', gap: '10px', alignItems: 'flex-start' }}>
                <span className="am-chip" style={{ background: m.bg, color: m.fg, flexShrink: 0 }}>
                  {m.emoji} {m.label}
                </span>
                <span style={{ fontSize: '11.5px', color: '#475569', lineHeight: 1.5 }}>{m.description}</span>
              </div>
            )
          })}
        </div>
      </div>
    </>
  )
}

function Stat({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={{
      background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px',
      padding: '8px 12px', display: 'flex', flexDirection: 'column', minWidth: '120px',
    }}>
      <span style={{ fontSize: '20px', fontWeight: 700, color, lineHeight: 1.2 }}>{value}</span>
      <span style={{ fontSize: '10px', color: '#64748B', marginTop: '2px' }}>{label}</span>
    </div>
  )
}

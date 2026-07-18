import { C } from '@/lib/tokens'
import { createFileRoute, Link } from '@tanstack/react-router'
import { useMemo, useState } from 'react'
import {
  SUPPLIERS_COLS, SKU_MASTER_COLS, ALL_COLS,
  SOURCES, REAL_SOURCES, LADDER_META, CONSUMERS, CONSUMER_INFO,
  type SsotColumn, type SsotSource, type SsotLadder, type SsotConsumer,
} from '@/data/ssot-spec'

export const Route = createFileRoute('/_authed/ssot-spec')({ component: SsotSpecPage })

const SHEET_URL = 'https://docs.google.com/spreadsheets/d/1mSUiOCeliDpHlw-xQSJI5Bd21g6DSJrc/edit'

// ─── Atoms ──────────────────────────────────────────────────────────────────

function StageHeader({ num, title, sub, tone = 'indigo' }: { num: number; title: string; sub?: string; tone?: 'indigo' | 'cyan' | 'amber' | 'emerald' | 'rose' }) {
  const TONES = {
    indigo:  { bg: C.primaryBg, color: C.indigoInk, border: C.indigoLine },
    cyan:    { bg: '#CFFAFE', color: '#155E75', border: '#A5F3FC' },
    amber:   { bg: C.warnBg, color: C.amberInk, border: '#FDE68A' },
    emerald: { bg: C.greenBg, color: C.green, border: '#BBF7D0' },
    rose:    { bg: '#FCE7F3', color: '#9D174D', border: '#FBCFE8' },
  }
  const t = TONES[tone]
  return (
    <div style={{ marginBottom: '10px' }}>
      <span style={{
        fontSize: '9px', fontWeight: 800, color: t.color, background: t.bg, border: `1px solid ${t.border}`,
        padding: '2px 8px', borderRadius: '10px', letterSpacing: '0.06em', textTransform: 'uppercase',
      }}>
        Stage {num}
      </span>
      <p style={{ fontSize: '13px', fontWeight: 700, color: C.ink, marginTop: '6px', lineHeight: 1.25 }}>{title}</p>
      {sub && <p style={{ fontSize: '10.5px', color: C.muted, marginTop: '2px', lineHeight: 1.4 }}>{sub}</p>}
    </div>
  )
}

function StageCard({ children, accent }: { children: React.ReactNode; accent?: string }) {
  return (
    <div style={{
      flex: '1 1 0', minWidth: 0, background: 'white', border: '1px solid #E2E8F0',
      borderTop: `3px solid ${accent ?? C.faint}`, borderRadius: '8px', padding: '14px',
      display: 'flex', flexDirection: 'column', gap: '8px', boxShadow: '0 1px 2px rgba(15,23,42,0.04)',
    }}>
      {children}
    </div>
  )
}

function FlowArrow() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', flex: '0 0 28px', padding: '60px 0 0 0' }}>
      <span style={{ fontSize: '24px', color: C.faint, fontWeight: 700 }}>→</span>
    </div>
  )
}

function SourcePill({ src, count, onClick }: { src: SsotSource; count: number; onClick: () => void }) {
  const m = SOURCES[src]
  return (
    <button
      onClick={onClick}
      className="ssot-src-pill"
      style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '6px',
        background: m.bg, border: `1px solid ${m.color}33`, color: m.color,
        borderRadius: '8px', padding: '8px 10px', cursor: 'pointer',
        fontSize: '11px', fontWeight: 600, width: '100%', textAlign: 'left',
        transition: 'all 0.15s', position: 'relative',
      }}
      title={`Click to see what ${m.label} is and who provides this data`}
    >
      <span style={{ display: 'flex', alignItems: 'center', gap: '6px', minWidth: 0 }}>
        <span style={{ fontSize: '15px' }}>{m.icon}</span>
        <span>{m.label}</span>
      </span>
      <span style={{ display: 'flex', alignItems: 'center', gap: '4px', flexShrink: 0 }}>
        <span style={{ background: 'rgba(255,255,255,0.7)', padding: '0 6px', borderRadius: '10px', fontSize: '10px', fontWeight: 700 }}>
          {count}
        </span>
        <span style={{ fontSize: '11px', opacity: 0.6 }}>↗</span>
      </span>
    </button>
  )
}

function LadderRung({
  ladder, count, target = false, link,
}: { ladder: SsotLadder; count: number; target?: boolean; link?: string }) {
  const m = LADDER_META[ladder]
  const content = (
    <div style={{
      background: target ? 'white' : m.bg, border: `1px solid ${target ? m.color + '44' : m.color + '66'}`,
      borderStyle: target ? 'dashed' : 'solid',
      borderRadius: '8px', padding: '8px 10px',
      display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
        <span style={{ fontSize: '18px' }}>{m.icon}</span>
        <div style={{ minWidth: 0 }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: m.color, lineHeight: 1.2 }}>{m.label}</p>
          <p style={{ fontSize: '9.5px', color: m.color, opacity: 0.8 }}>{m.pct} accuracy</p>
        </div>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', flexShrink: 0 }}>
        <span style={{ background: 'rgba(255,255,255,0.7)', color: m.color, fontSize: '10px', fontWeight: 700, padding: '2px 7px', borderRadius: '10px' }}>
          {count} {target ? 'target' : 'today'}
        </span>
        {link && <span style={{ fontSize: '11px', color: m.color, opacity: 0.6 }}>↗</span>}
      </div>
    </div>
  )
  if (link && link !== '#') {
    return (
      <Link to={link as never} className="ssot-rung" style={{ textDecoration: 'none' }}>
        {content}
      </Link>
    )
  }
  return <div className="ssot-rung">{content}</div>
}

function ConsumerCard({ id, sub }: { id: SsotConsumer; sub?: string }) {
  const info = CONSUMER_INFO.find(c => c.id === id)!
  return (
    <div style={{
      background: info.colorBg, border: `1px solid ${info.colorBorder}`, color: info.colorText,
      borderRadius: '8px', padding: '8px 10px',
    }}>
      <div style={{ fontSize: '11px', fontWeight: 700 }}>{info.label}</div>
      {sub && <div style={{ fontSize: '9.5px', opacity: 0.85, marginTop: '2px' }}>{sub}</div>}
    </div>
  )
}

function MiniTable({ title, subtitle, cols, accent, badge, onColClick, dimIds }: {
  title: string; subtitle: string; cols: SsotColumn[]; accent: string; badge: string;
  onColClick: (c: SsotColumn) => void; dimIds?: Set<string>;
}) {
  const groups = useMemo(() => {
    const m = new Map<string, SsotColumn[]>()
    cols.forEach(c => {
      if (!m.has(c.group)) m.set(c.group, [])
      m.get(c.group)!.push(c)
    })
    return Array.from(m.entries())
  }, [cols])

  return (
    <div style={{ background: 'white', border: `1.5px solid ${accent}`, borderRadius: '8px', overflow: 'hidden' }}>
      <div style={{ background: accent, padding: '6px 10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div>
          <p style={{ fontSize: '11px', fontWeight: 700, color: 'white', lineHeight: 1.2 }}>{title}</p>
          <p style={{ fontSize: '9px', color: 'rgba(255,255,255,0.85)' }}>{subtitle}</p>
        </div>
        <span style={{ fontSize: '9px', color: 'white', background: 'rgba(0,0,0,0.2)', padding: '2px 7px', borderRadius: '10px', fontWeight: 700 }}>{badge}</span>
      </div>
      <div style={{ padding: '4px' }}>
        {groups.map(([g, gcols]) => (
          <div key={g} style={{ marginBottom: '4px' }}>
            <p style={{ fontSize: '8.5px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.05em', padding: '2px 6px' }}>
              {g} <span style={{ opacity: 0.7 }}>· {gcols.length}</span>
            </p>
            {gcols.map(c => {
              const dim = dimIds ? !dimIds.has(c.id) : false
              const lm = LADDER_META[c.ladder]
              return (
                <button
                  key={c.id}
                  onClick={() => onColClick(c)}
                  className="ssot-col-row"
                  style={{
                    display: 'flex', alignItems: 'center', gap: '4px', width: '100%', textAlign: 'left',
                    padding: '3px 6px', borderRadius: '3px', border: 'none', background: 'transparent', cursor: 'pointer',
                    opacity: dim ? 0.3 : 1, transition: 'all 0.1s',
                  }}
                >
                  <span style={{ fontSize: '13px', flexShrink: 0 }}>{SOURCES[c.source].icon}</span>
                  <span style={{ fontSize: '10px', fontFamily: 'ui-monospace, monospace', color: C.ink, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {c.name}
                  </span>
                  <span style={{ background: lm.bg, color: lm.color, fontSize: '8.5px', fontWeight: 700, padding: '1px 4px', borderRadius: '3px', flexShrink: 0 }}>
                    {lm.icon}
                  </span>
                </button>
              )
            })}
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Source Drawer ──────────────────────────────────────────────────────────

function SourceDrawer({ src, onClose }: { src: SsotSource; onClose: () => void }) {
  const m = SOURCES[src]
  const cols = ALL_COLS.filter(c => c.source === src)
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 50, pointerEvents: 'none' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(15,23,42,0.4)', pointerEvents: 'auto' }} />
      <div style={{
        position: 'absolute', top: 0, right: 0, bottom: 0, width: '480px', background: 'white',
        boxShadow: '-4px 0 24px rgba(15,23,42,0.15)', padding: '20px', overflowY: 'auto', pointerEvents: 'auto',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '8px' }}>
          <span style={{ fontSize: '10px', fontWeight: 700, color: m.isRealSource ? C.indigo : C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {m.isRealSource ? 'Source system' : 'System-generated (not a source)'}
          </span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: '20px', color: C.muted, cursor: 'pointer', padding: 0, lineHeight: 1 }}>×</button>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '8px' }}>
          <span style={{ fontSize: '30px' }}>{m.icon}</span>
          <p style={{ fontSize: '22px', fontWeight: 700, color: m.color }}>{m.label}</p>
        </div>
        <p style={{ fontSize: '12.5px', color: C.sub, lineHeight: 1.6, marginBottom: '14px' }}>{m.brief}</p>

        <Section label="🧑 Who provides this data">
          <p style={{ fontSize: '12px', color: C.ink, background: m.bg, border: `1px solid ${m.color}33`, padding: '10px 12px', borderRadius: '6px', lineHeight: 1.55 }}>
            {m.providedBy}
          </p>
        </Section>

        <Section label="📥 How we pull from it">
          <p style={{ fontSize: '11.5px', color: C.ink, background: C.wash, border: '1px solid #E2E8F0', padding: '8px 10px', borderRadius: '6px', lineHeight: 1.55 }}>
            {m.pullMethod}
          </p>
        </Section>

        <Section label={`📊 Columns this ${m.isRealSource ? 'source' : 'category'} feeds (${cols.length})`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
            {cols.map(c => (
              <div key={c.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '4px 8px', background: C.wash, borderRadius: '4px' }}>
                <span style={{ fontSize: '11px', fontFamily: 'ui-monospace, monospace', color: C.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {c.table === 'SUPPLIERS' ? <span style={{ color: '#0891B2', fontWeight: 700, fontSize: '9px' }}>SUP </span> : <span style={{ color: C.indigo, fontWeight: 700, fontSize: '9px' }}>SKU </span>}
                  {c.name}
                </span>
                <span style={{ background: LADDER_META[c.ladder].bg, color: LADDER_META[c.ladder].color, fontSize: '9px', fontWeight: 700, padding: '1px 6px', borderRadius: '3px', flexShrink: 0, marginLeft: '6px' }}>
                  {LADDER_META[c.ladder].icon} {LADDER_META[c.ladder].label}
                </span>
              </div>
            ))}
          </div>
        </Section>
      </div>
    </div>
  )
}

// ─── Column Drawer ──────────────────────────────────────────────────────────

function ColumnDrawer({ col, onClose }: { col: SsotColumn; onClose: () => void }) {
  const src = SOURCES[col.source]
  const lm = LADDER_META[col.ladder]
  const tm = col.target ? LADDER_META[col.target] : null
  return (
    <div style={{ position: 'fixed', inset: 0, zIndex: 50, pointerEvents: 'none' }}>
      <div onClick={onClose} style={{ position: 'absolute', inset: 0, background: 'rgba(15,23,42,0.4)', pointerEvents: 'auto' }} />
      <div style={{
        position: 'absolute', top: 0, right: 0, bottom: 0, width: '460px', background: 'white',
        boxShadow: '-4px 0 24px rgba(15,23,42,0.15)', padding: '20px', overflowY: 'auto', pointerEvents: 'auto',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '4px' }}>
          <span style={{ fontSize: '10px', fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.06em' }}>
            {col.table === 'SUPPLIERS' ? 'SUPPLIERS table' : 'SKU Operational Database'} · {col.group}
          </span>
          <button onClick={onClose} style={{ background: 'none', border: 'none', fontSize: '20px', color: C.muted, cursor: 'pointer', padding: 0, lineHeight: 1 }}>×</button>
        </div>
        <p style={{ fontSize: '20px', fontFamily: 'ui-monospace, monospace', color: C.ink, fontWeight: 700, marginBottom: '6px' }}>{col.name}</p>
        <div style={{ display: 'flex', gap: '6px', marginBottom: '14px', flexWrap: 'wrap' }}>
          <span style={{ background: src.bg, color: src.color, fontSize: '10px', fontWeight: 700, padding: '3px 8px', borderRadius: '4px' }}>
            {src.icon} {src.label}
          </span>
          <span style={{ background: lm.bg, color: lm.color, fontSize: '10px', fontWeight: 700, padding: '3px 8px', borderRadius: '4px' }}>
            {lm.icon} {lm.label} · {lm.pct}
          </span>
        </div>

        <Section label="What it is">
          <p style={{ fontSize: '12px', color: C.ink, lineHeight: 1.6 }}>{col.description}</p>
        </Section>

        <Section label="Accuracy path">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            <div style={{ background: lm.bg, border: `1px solid ${lm.color}33`, padding: '6px 10px', borderRadius: '6px' }}>
              <p style={{ fontSize: '9px', fontWeight: 700, color: lm.color, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Today</p>
              <p style={{ fontSize: '11px', color: lm.color, marginTop: '2px' }}>{lm.icon} {lm.label} — {lm.brief}</p>
            </div>
            {tm && col.target !== col.ladder && (
              <>
                <span style={{ textAlign: 'center', color: C.knobOff, fontSize: '14px' }}>↓</span>
                <div style={{ background: 'white', border: `1px dashed ${tm.color}66`, padding: '6px 10px', borderRadius: '6px' }}>
                  <p style={{ fontSize: '9px', fontWeight: 700, color: tm.color, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Target</p>
                  <p style={{ fontSize: '11px', color: tm.color, marginTop: '2px' }}>{tm.icon} {tm.label} — {tm.brief}</p>
                </div>
              </>
            )}
          </div>
        </Section>

        <Section label="Used by">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
            {col.usedBy.map(c => {
              const m = CONSUMERS[c]
              return (
                <span key={c} style={{ background: m.bg, color: m.color, fontSize: '10px', fontWeight: 700, padding: '3px 8px', borderRadius: '4px' }}>
                  {m.label}
                </span>
              )
            })}
          </div>
        </Section>

        {col.amRef && (
          <Section label="Approval Matrix reference">
            <p style={{ fontSize: '11px', fontFamily: 'ui-monospace, monospace', color: '#7C2D12', background: '#FFEDD5', padding: '6px 10px', borderRadius: '4px' }}>
              {col.amRef}
            </p>
          </Section>
        )}
        {col.psRef && (
          <Section label="Product Selection reference">
            <p style={{ fontSize: '11px', fontFamily: 'ui-monospace, monospace', color: '#1E40AF', background: '#DBEAFE', padding: '6px 10px', borderRadius: '4px' }}>
              {col.psRef}
            </p>
          </Section>
        )}
        {col.notes && (
          <Section label="Notes">
            <p style={{ fontSize: '11px', color: C.sub, lineHeight: 1.6, background: C.wash, border: '1px solid #E2E8F0', padding: '8px 10px', borderRadius: '4px' }}>
              {col.notes}
            </p>
          </Section>
        )}
      </div>
    </div>
  )
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '14px' }}>
      <p style={{ fontSize: '9px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '4px' }}>{label}</p>
      {children}
    </div>
  )
}

// ─── Page ───────────────────────────────────────────────────────────────────

function SsotSpecPage() {
  const [openCol, setOpenCol] = useState<SsotColumn | null>(null)
  const [openSrc, setOpenSrc] = useState<SsotSource | null>(null)
  const [filterSource, setFilterSource] = useState<SsotSource | 'ALL'>('ALL')
  const [filterLadder, setFilterLadder] = useState<SsotLadder | 'ALL'>('ALL')
  const [filterConsumer, setFilterConsumer] = useState<SsotConsumer | 'ALL'>('ALL')
  const [search, setSearch] = useState('')

  // counts for source pills
  const srcCounts = useMemo(() => {
    const m = new Map<SsotSource, number>()
    ALL_COLS.forEach(c => m.set(c.source, (m.get(c.source) || 0) + 1))
    return m
  }, [])

  // accuracy ladder counts — today AND target
  const ladderToday = useMemo(() => {
    const m = new Map<SsotLadder, number>()
    ALL_COLS.forEach(c => m.set(c.ladder, (m.get(c.ladder) || 0) + 1))
    return m
  }, [])
  const ladderTarget = useMemo(() => {
    const m = new Map<SsotLadder, number>()
    ALL_COLS.forEach(c => {
      const t = c.target ?? c.ladder
      m.set(t, (m.get(t) || 0) + 1)
    })
    return m
  }, [])

  // highlight set for diagram dimming
  const highlightIds = useMemo(() => {
    if (filterSource === 'ALL' && filterLadder === 'ALL' && filterConsumer === 'ALL' && !search) return undefined
    const s = new Set<string>()
    ALL_COLS.forEach(c => {
      if (filterSource !== 'ALL' && c.source !== filterSource) return
      if (filterLadder !== 'ALL' && c.ladder !== filterLadder) return
      if (filterConsumer !== 'ALL' && !c.usedBy.includes(filterConsumer)) return
      if (search && !c.name.toLowerCase().includes(search.toLowerCase()) && !c.description.toLowerCase().includes(search.toLowerCase())) return
      s.add(c.id)
    })
    return s
  }, [filterSource, filterLadder, filterConsumer, search])

  const filteredTable = useMemo(() => ALL_COLS.filter(c => {
    if (filterSource !== 'ALL' && c.source !== filterSource) return false
    if (filterLadder !== 'ALL' && c.ladder !== filterLadder) return false
    if (filterConsumer !== 'ALL' && !c.usedBy.includes(filterConsumer)) return false
    if (search && !c.name.toLowerCase().includes(search.toLowerCase()) && !c.description.toLowerCase().includes(search.toLowerCase())) return false
    return true
  }), [filterSource, filterLadder, filterConsumer, search])

  return (
    <>
      <style>{`
        .ssot-col-row:hover { background: #F1F5F9; }
        .ssot-src-pill:hover { transform: translateY(-1px); box-shadow: 0 2px 6px rgba(15,23,42,0.08); }
        .ssot-rung:hover > div { transform: translateY(-1px); box-shadow: 0 2px 6px rgba(15,23,42,0.08); }
        .ssot-rung > div { transition: all 0.15s; }
        .ssot-tab-row:hover { background: #F8FAFC; }
      `}</style>

      {/* ─── Header ─── */}
      <div style={{ marginBottom: '14px', display: 'flex', alignItems: 'flex-end', gap: '14px', flexWrap: 'wrap' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <p style={{ fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '4px' }}>
            Operational Database Spec
          </p>
          <h1 style={{ fontSize: '24px', fontWeight: 700, color: C.ink, marginBottom: '6px' }}>SSOT Database — Column Specification</h1>
          <p style={{ fontSize: '13px', color: C.sub, lineHeight: 1.55, maxWidth: '820px' }}>
            This page is a <strong>teaching artifact</strong>, not an audit. Its job: show the team how data flows, where each column comes from,
            and how it gets to 100% accurate via two stages: <strong>OCR + human review (~80% combined)</strong>, then{' '}
            <strong>3-way matching against accounting (100%)</strong>.
          </p>
        </div>
        <a
          href={SHEET_URL} target="_blank" rel="noreferrer"
          style={{
            display: 'flex', alignItems: 'center', gap: '6px', padding: '8px 14px',
            background: C.ink, color: 'white', borderRadius: '6px', fontSize: '12px', fontWeight: 600,
            textDecoration: 'none', flexShrink: 0,
          }}
        >
          <span>📊</span><span>Open Live Google Sheet</span><span style={{ fontSize: '10px', opacity: 0.7 }}>↗</span>
        </a>
      </div>

      {/* ─── Filters strip ─── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '14px', flexWrap: 'wrap', background: 'white', padding: '10px 14px', border: '1px solid #E2E8F0', borderRadius: '8px' }}>
        <p style={{ fontSize: '11px', fontWeight: 700, color: C.muted }}>Highlight columns by</p>
        <input
          value={search} onChange={e => setSearch(e.target.value)}
          placeholder="Search name or description…"
          style={{ fontSize: '11px', padding: '5px 10px', border: '1px solid #CBD5E1', borderRadius: '6px', width: '200px' }}
        />
        <FilterSelect value={filterSource} onChange={setFilterSource as any} options={[
          { v: 'ALL', l: 'All sources' },
          ...REAL_SOURCES.map(k => ({ v: k, l: `${SOURCES[k].icon} ${SOURCES[k].label}` })),
          { v: 'internal', l: '📋 System (IMS)' },
          { v: 'lookup', l: '🔗 System (lookup)' },
        ]} />
        <FilterSelect value={filterLadder} onChange={setFilterLadder as any} options={[
          { v: 'ALL', l: 'All accuracy states' },
          ...Object.keys(LADDER_META).map(k => ({ v: k, l: `${LADDER_META[k as SsotLadder].icon} ${LADDER_META[k as SsotLadder].label}` })),
        ]} />
        <FilterSelect value={filterConsumer} onChange={setFilterConsumer as any} options={[
          { v: 'ALL', l: 'All consumers' },
          ...CONSUMER_INFO.map(c => ({ v: c.id, l: c.label })),
        ]} />
        {highlightIds && (
          <span style={{ fontSize: '11px', color: C.indigo, fontWeight: 600 }}>
            {highlightIds.size} of {ALL_COLS.length} matched
          </span>
        )}
        {(filterSource !== 'ALL' || filterLadder !== 'ALL' || filterConsumer !== 'ALL' || search) && (
          <button onClick={() => { setFilterSource('ALL'); setFilterLadder('ALL'); setFilterConsumer('ALL'); setSearch('') }}
                  style={{ fontSize: '11px', color: C.indigo, background: 'none', border: 'none', cursor: 'pointer', fontWeight: 600 }}>
            Clear
          </button>
        )}
      </div>

      {/* ─── 5-STAGE HORIZONTAL PIPELINE ─── */}
      <div style={{ marginBottom: '14px' }}>
        <div style={{ display: 'flex', alignItems: 'stretch', gap: '0', minWidth: 0 }}>
          {/* STAGE 1 — Sources */}
          <StageCard accent={C.indigo}>
            <StageHeader num={1} title="Source Systems" sub="raw upstream data — where every value originally lives" tone="indigo" />
            <p style={{ fontSize: '10px', color: C.indigo, background: C.primaryBg, border: '1px solid #C7D2FE', padding: '6px 8px', borderRadius: '6px', lineHeight: 1.4 }}>
              <strong>↗ Click any source</strong> to see what it is, how data is pulled, and which team is responsible.
            </p>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {REAL_SOURCES.map(src => (
                <SourcePill key={src} src={src} count={srcCounts.get(src) || 0} onClick={() => setOpenSrc(src)} />
              ))}
            </div>
            <div style={{ marginTop: '4px', paddingTop: '6px', borderTop: '1px dashed #E2E8F0' }}>
              <p style={{ fontSize: '9px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '3px' }}>
                Plus system-generated fields (not sources)
              </p>
              <button
                onClick={() => setOpenSrc('internal')}
                style={{ background: C.line, color: C.ink, fontSize: '10px', fontWeight: 600, padding: '4px 8px', borderRadius: '4px', border: 'none', cursor: 'pointer', marginRight: '4px' }}
              >
                📋 IMS-internal ({srcCounts.get('internal') || 0}) ↗
              </button>
              <button
                onClick={() => setOpenSrc('lookup')}
                style={{ background: '#CFFAFE', color: '#155E75', fontSize: '10px', fontWeight: 600, padding: '4px 8px', borderRadius: '4px', border: 'none', cursor: 'pointer' }}
              >
                🔗 Cached ({srcCounts.get('lookup') || 0}) ↗
              </button>
            </div>
          </StageCard>

          <FlowArrow />

          {/* STAGE 2 — Google Sheets SSOT */}
          <StageCard accent="#0891B2">
            <StageHeader num={2} title="Google Sheets SSOT" sub="temporary middle layer · phasing out · 2 tables · 65 cols" tone="cyan" />
            <MiniTable
              title="SUPPLIERS" subtitle="~46 rows" cols={SUPPLIERS_COLS} accent="#0891B2" badge="10"
              onColClick={setOpenCol} dimIds={highlightIds}
            />
            <div style={{ textAlign: 'center', fontSize: '9px', color: C.faint, fontWeight: 700, padding: '4px 0' }}>
              ↕ FK · supplier_id
            </div>
            <MiniTable
              title="SKU OPERATIONAL DATABASE" subtitle="~412 rows" cols={SKU_MASTER_COLS} accent={C.indigo} badge="55"
              onColClick={setOpenCol} dimIds={highlightIds}
            />
          </StageCard>

          <FlowArrow />

          {/* STAGE 3 — IMS All Inventory */}
          <StageCard accent="#7C3AED">
            <StageHeader num={3} title="Rosetta IMS — All Inventory" sub="bird's eye over every SKU · data quality grade · cost confidence" tone="indigo" />
            <Link to={"/" as never} style={{ textDecoration: 'none' }}>
              <div style={{ background: '#EDE9FE', border: '1px solid #DDD6FE', padding: '12px', borderRadius: '8px' }}>
                <p style={{ fontSize: '11px', fontWeight: 700, color: '#5B21B6', marginBottom: '4px' }}>📦 Inventory grid</p>
                <p style={{ fontSize: '10px', color: '#5B21B6', lineHeight: 1.5 }}>
                  All 412 SKUs in one view. Each row carries quality grade (A/B/C) and ladder state per cost column.
                </p>
                <p style={{ fontSize: '10px', color: '#5B21B6', marginTop: '6px', fontWeight: 600 }}>Open → /</p>
              </div>
            </Link>
            <div style={{ background: C.wash, border: '1px dashed #CBD5E1', padding: '10px', borderRadius: '6px' }}>
              <p style={{ fontSize: '9px', fontWeight: 700, color: C.muted, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '4px' }}>What you see here</p>
              <ul style={{ paddingLeft: '14px', fontSize: '10px', color: C.sub, lineHeight: 1.6 }}>
                <li>Every SKU + its current data quality</li>
                <li>Filter by data confidence (low cost trust = red flag)</li>
                <li>Edit attribution: who changed what + when</li>
              </ul>
            </div>
          </StageCard>

          <FlowArrow />

          {/* STAGE 4 — IMS Data Review (THE ACCURACY LADDER) */}
          <StageCard accent="#F59E0B">
            <StageHeader num={4} title="Rosetta IMS — Data Review" sub="the accuracy ladder · OCR + Review = ~80% · 3-way match = 100%" tone="amber" />
            <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
              {/* OCR + HITL form one combined milestone */}
              <div style={{ background: C.wash, border: '1px dashed #CBD5E1', padding: '6px 8px', borderRadius: '6px' }}>
                <p style={{ fontSize: '9px', fontWeight: 700, color: C.sub, textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '4px' }}>
                  Combined: ~80% accuracy
                </p>
                <LadderRung ladder="ocr"   count={ladderToday.get('ocr')   || 0} link={LADDER_META.ocr.link} />
                <span style={{ display: 'block', textAlign: 'center', color: C.knobOff, fontSize: '12px', lineHeight: 1, margin: '2px 0' }}>+</span>
                <LadderRung ladder="hitl"  count={ladderToday.get('hitl')  || 0} link={LADDER_META.hitl.link} />
              </div>
              <span style={{ textAlign: 'center', color: C.knobOff, fontSize: '14px', lineHeight: 1 }}>↓ remaining ~20% closed by</span>
              <LadderRung ladder="3way"  count={ladderToday.get('3way')  || 0} link={LADDER_META['3way'].link} />
            </div>
            <div style={{ background: '#FFFBEB', border: '1px solid #FDE68A', padding: '8px 10px', borderRadius: '6px', marginTop: '4px' }}>
              <p style={{ fontSize: '9.5px', color: C.amberInk, lineHeight: 1.5 }}>
                <strong>Target state →</strong> OCR: {ladderTarget.get('ocr') || 0} · HITL: {ladderTarget.get('hitl') || 0} · 3-way: {ladderTarget.get('3way') || 0}
              </p>
            </div>
            <p style={{ fontSize: '9.5px', color: C.faint, fontStyle: 'italic' }}>
              OCR alone is too noisy to trust. Combined with human review, ~80% — humans have their own error band. Only accounts get us to 100%.
            </p>
          </StageCard>

          <FlowArrow />

          {/* STAGE 5 — Export back */}
          <StageCard accent="#10B981">
            <StageHeader num={5} title="Export back to Source Systems" sub="trusted data closes the loop · IMS never writes to the Sheet" tone="emerald" />
            <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
              <ConsumerCard id="SHOPIFY_OUT" sub="min qty + multiples per MPQ × Risk Acceptance" />
              <ConsumerCard id="DAYSMART_OUT" sub="cost overrides from verified catalogue & invoice" />
              <div style={{ background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: '6px', padding: '8px 10px' }}>
                <p style={{ fontSize: '11px', fontWeight: 700, color: C.green }}>→ QuickBooks</p>
                <p style={{ fontSize: '9.5px', color: C.green, opacity: 0.85, marginTop: '2px' }}>3-way matched cost feeds accounting</p>
              </div>
            </div>
          </StageCard>
        </div>

        {/* Logic Layer branch */}
        <div style={{ display: 'flex', gap: '0', marginTop: '8px' }}>
          <div style={{ flex: '1 1 0' }} />
          <div style={{ flex: '0 0 28px' }} />
          <div style={{ flex: '1 1 0', maxWidth: '320px' }}>
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', marginBottom: '4px' }}>
              <span style={{ fontSize: '9px', fontWeight: 700, color: C.muted, letterSpacing: '0.04em' }}>also reads from Stage 2</span>
              <span style={{ color: C.faint, fontSize: '18px' }}>↓</span>
            </div>
            <div style={{ background: 'white', border: '1px solid #E2E8F0', borderTop: '3px solid #DC2626', borderRadius: '8px', padding: '12px' }}>
              <p style={{ fontSize: '9px', fontWeight: 800, color: C.redInk, background: C.redBg, border: '1px solid #FECACA', padding: '2px 8px', borderRadius: '10px', letterSpacing: '0.06em', textTransform: 'uppercase', display: 'inline-block' }}>
                Parallel consumer
              </p>
              <p style={{ fontSize: '13px', fontWeight: 700, color: C.ink, marginTop: '6px', lineHeight: 1.25 }}>Logic Layer (Sam's work)</p>
              <p style={{ fontSize: '10.5px', color: C.muted, marginTop: '2px', lineHeight: 1.4 }}>
                Decision layers that consume the SSOT to drive purchasing & sourcing.
              </p>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '8px' }}>
                <ConsumerCard id="AM" sub="Existing SKU PO approval · 85 cols" />
                <ConsumerCard id="PS" sub="New SKU sourcing · 18 cols · MOU/MPQ/MOQ/Risk" />
              </div>
            </div>
          </div>
          <div style={{ flex: '1 1 0' }} />
        </div>
      </div>

      {/* ─── Tie-back footer ─── */}
      <div style={{ background: C.wash, border: '1px solid #E2E8F0', borderRadius: '8px', padding: '12px 14px', marginBottom: '14px' }}>
        <p style={{ fontSize: '10px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>
          The accuracy ladder is real software — open each step
        </p>
        <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap' }}>
          <TieBackLink href="/catalogues" label="📄 OCR pipeline" desc="Machine extraction of supplier catalogues. Output needs human review before it counts." />
          <TieBackLink href="/data-review" label="👤 Data Review — gets us to ~80% combined" desc="Human-in-the-loop approval queue. OCR + Review together = the realistic ~80% milestone." />
          <TieBackLink href="#" label="⚖️ 3-way matching — gets us to 100%" desc="Accounts cross-check PO ↔ delivery note ↔ invoice. The only step that closes the remaining ~20%. Scoped (Desmond Chan)." disabled />
        </div>
      </div>

      {/* ─── Column detail table ─── */}
      <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '10px', padding: '14px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '10px', flexWrap: 'wrap' }}>
          <p style={{ fontSize: '13px', fontWeight: 700, color: C.ink }}>Column Detail</p>
          <span style={{ fontSize: '11px', color: C.muted }}>{filteredTable.length} of {ALL_COLS.length}</span>
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: '10.5px', color: C.faint }}>Use the filter strip above. Click any row for the full drawer.</span>
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '40px 200px 110px 60px 110px 1fr 150px', gap: '8px', fontSize: '9px', fontWeight: 700, color: C.faint, textTransform: 'uppercase', letterSpacing: '0.06em', padding: '6px 8px', borderBottom: '1px solid #E2E8F0' }}>
          <span>Table</span><span>Column</span><span>Group</span><span>Source</span><span>Accuracy</span><span>Description</span><span>Used by</span>
        </div>
        {filteredTable.map(c => {
          const lm = LADDER_META[c.ladder]
          return (
            <button
              key={c.id}
              onClick={() => setOpenCol(c)}
              className="ssot-tab-row"
              style={{
                display: 'grid', gridTemplateColumns: '40px 200px 110px 60px 110px 1fr 150px', gap: '8px',
                padding: '6px 8px', border: 'none', borderBottom: '1px solid #F1F5F9', background: 'transparent',
                cursor: 'pointer', textAlign: 'left', width: '100%', alignItems: 'center',
              }}
            >
              <span style={{ fontSize: '9px', color: c.table === 'SUPPLIERS' ? '#0891B2' : C.indigo, fontWeight: 700 }}>
                {c.table === 'SUPPLIERS' ? 'SUP' : 'SKU'}
              </span>
              <span style={{ fontSize: '11px', fontFamily: 'ui-monospace, monospace', color: C.ink, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.name}</span>
              <span style={{ fontSize: '10px', color: C.muted }}>{c.group}</span>
              <span style={{ fontSize: '14px' }}>{SOURCES[c.source].icon}</span>
              <span style={{ background: lm.bg, color: lm.color, fontSize: '9px', fontWeight: 700, padding: '2px 6px', borderRadius: '3px', whiteSpace: 'nowrap', width: 'fit-content' }}>
                {lm.icon} {lm.label}
              </span>
              <span style={{ fontSize: '10.5px', color: C.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.description}</span>
              <span style={{ display: 'flex', gap: '3px', flexWrap: 'wrap' }}>
                {c.usedBy.map(u => (
                  <span key={u} style={{ fontSize: '9px', fontWeight: 700, padding: '1px 5px', borderRadius: '3px', background: CONSUMERS[u].bg, color: CONSUMERS[u].color }}>
                    {CONSUMERS[u].short}
                  </span>
                ))}
              </span>
            </button>
          )
        })}
      </div>

      {openCol && <ColumnDrawer col={openCol} onClose={() => setOpenCol(null)} />}
      {openSrc && <SourceDrawer src={openSrc} onClose={() => setOpenSrc(null)} />}
    </>
  )
}

function TieBackLink({ href, label, desc, disabled }: { href: string; label: string; desc: string; disabled?: boolean }) {
  const content = (
    <div style={{
      background: 'white', border: '1px solid #E2E8F0', borderRadius: '6px', padding: '8px 12px',
      flex: '1 1 240px', opacity: disabled ? 0.5 : 1, cursor: disabled ? 'not-allowed' : 'pointer',
    }}>
      <p style={{ fontSize: '11px', fontWeight: 700, color: C.ink }}>{label}{!disabled && <span style={{ marginLeft: '6px', fontSize: '10px', color: C.faint }}>↗</span>}</p>
      <p style={{ fontSize: '10px', color: C.muted, marginTop: '2px', lineHeight: 1.4 }}>{desc}</p>
    </div>
  )
  if (disabled) return content
  return <Link to={href as never} style={{ textDecoration: 'none', display: 'flex', flex: '1 1 240px' }}>{content}</Link>
}

function FilterSelect<T extends string>({ value, onChange, options }: { value: T; onChange: (v: T) => void; options: { v: string; l: string }[] }) {
  return (
    <select
      value={value}
      onChange={e => onChange(e.target.value as T)}
      style={{ fontSize: '11px', padding: '5px 8px', border: '1px solid #CBD5E1', borderRadius: '6px', background: 'white', color: C.ink }}
    >
      {options.map(o => (<option key={o.v} value={o.v}>{o.l}</option>))}
    </select>
  )
}

import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'

export const Route = createFileRoute('/_authed/architecture')({ component: ArchitecturePage })

type Mode = 'current' | 'target' | 'split'

const PHASE = {
  1: { bg: '#EEF2FF', color: '#4338CA', border: '#C7D2FE', label: 'Phase 1 — Now' },
  2: { bg: '#DBEAFE', color: '#1E40AF', border: '#BFDBFE', label: 'Phase 2 — by end July (Desmond)' },
  3: { bg: '#FEF3C7', color: '#92400E', border: '#FDE68A', label: 'Phase 3 — by end July' },
  4: { bg: '#DCFCE7', color: '#166534', border: '#BBF7D0', label: 'Phase 4 — by end July (Desmond)' },
} as const

function Card({
  title,
  subtitle,
  items,
  tone = 'neutral',
  tag,
  phase,
  width,
}: {
  title: string
  subtitle?: string
  items?: string[]
  tone?: 'neutral' | 'system' | 'sheet' | 'human' | 'output' | 'risk'
  tag?: string
  phase?: 1 | 2 | 3 | 4
  width?: string
}) {
  const TONE: Record<string, { bg: string; border: string; title: string; sub: string }> = {
    neutral: { bg: 'white',   border: '#E2E8F0', title: '#0F172A', sub: '#64748B' },
    system:  { bg: '#F8FAFC', border: '#CBD5E1', title: '#0F172A', sub: '#475569' },
    sheet:   { bg: '#FFFBEB', border: '#FDE68A', title: '#78350F', sub: '#92400E' },
    human:   { bg: '#EFF6FF', border: '#BFDBFE', title: '#1E3A8A', sub: '#1E40AF' },
    output:  { bg: '#F0FDF4', border: '#BBF7D0', title: '#14532D', sub: '#166534' },
    risk:    { bg: '#FEF2F2', border: '#FECACA', title: '#991B1B', sub: '#B91C1C' },
  }
  const t = TONE[tone]
  const p = phase ? PHASE[phase] : null
  return (
    <div style={{
      background: t.bg, border: `1px solid ${p ? p.border : t.border}`, borderRadius: '8px',
      padding: '10px 12px', width: width ?? 'auto', flex: width ? 'none' : 1, minWidth: 0,
      boxShadow: '0 1px 2px rgba(15,23,42,0.04)',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px', flexWrap: 'wrap' }}>
        <p style={{ fontSize: '12px', fontWeight: 700, color: t.title, lineHeight: 1.3 }}>{title}</p>
        {tag && (
          <span style={{ fontSize: '9px', fontWeight: 700, color: '#64748B', background: '#F1F5F9', padding: '1px 6px', borderRadius: '3px', textTransform: 'uppercase', letterSpacing: '0.04em' }}>
            {tag}
          </span>
        )}
        {p && (
          <span style={{ fontSize: '9px', fontWeight: 700, color: p.color, background: p.bg, border: `1px solid ${p.border}`, padding: '1px 6px', borderRadius: '3px' }}>
            {p.label}
          </span>
        )}
      </div>
      {subtitle && <p style={{ fontSize: '10.5px', color: t.sub, marginBottom: items?.length ? '5px' : 0, lineHeight: 1.5 }}>{subtitle}</p>}
      {items && items.length > 0 && (
        <ul style={{ margin: '4px 0 0 0', paddingLeft: '14px', listStyleType: 'disc' }}>
          {items.map((it) => (
            <li key={it} style={{ fontSize: '10.5px', color: t.sub, lineHeight: 1.55, marginBottom: '1px' }}>{it}</li>
          ))}
        </ul>
      )}
    </div>
  )
}

function Arrow({ label, sublabel, direction = 'down' }: { label?: string; sublabel?: string; direction?: 'down' | 'right' }) {
  if (direction === 'right') {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '0 8px', flexShrink: 0 }}>
        <span style={{ color: '#94A3B8', fontSize: '16px', fontWeight: 700 }}>→</span>
        {label && <span style={{ fontSize: '10px', color: '#64748B', fontWeight: 600 }}>{label}</span>}
      </div>
    )
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '2px', padding: '4px 0' }}>
      {label && <span style={{ fontSize: '10px', color: '#475569', fontWeight: 600 }}>{label}</span>}
      {sublabel && <span style={{ fontSize: '9px', color: '#94A3B8' }}>{sublabel}</span>}
      <span style={{ color: '#94A3B8', fontSize: '18px', fontWeight: 700, lineHeight: 1 }}>↓</span>
    </div>
  )
}

function LayerLabel({ children, color = '#94A3B8' }: { children: React.ReactNode; color?: string }) {
  return (
    <p style={{ fontSize: '9px', fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px' }}>
      {children}
    </p>
  )
}

function Row({ children }: { children: React.ReactNode }) {
  return (
    <div style={{ display: 'flex', gap: '10px', alignItems: 'stretch' }}>
      {children}
    </div>
  )
}

function Pain({ children }: { children: React.ReactNode }) {
  return (
    <div style={{
      fontSize: '10.5px', color: '#991B1B', background: '#FEF2F2',
      border: '1px solid #FECACA', padding: '6px 10px', borderRadius: '6px',
      marginTop: '8px', lineHeight: 1.55,
    }}>
      <strong>⚠ Pain point:</strong> {children}
    </div>
  )
}

function DataFlowDiagram() {
  const StepLabel = ({ letter, text, color = '#94A3B8' }: { letter: string; text: string; color?: string }) => (
    <p style={{ fontSize: '9px', fontWeight: 700, color, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '6px', textAlign: 'center' }}>
      {letter} · {text}
    </p>
  )

  const FlowArrow = ({ label, sublabel, color = '#475569' }: { label: string; sublabel?: string; color?: string }) => (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', padding: '6px 0', gap: '2px' }}>
      <div style={{ width: '2px', height: '10px', background: '#CBD5E1' }} />
      <span style={{ fontSize: '9.5px', fontWeight: 600, color, background: color === '#475569' ? '#F1F5F9' : '#F0FDF4', padding: '2px 12px', borderRadius: '10px', border: `1px solid ${color === '#475569' ? '#E2E8F0' : '#BBF7D0'}` }}>{label}</span>
      {sublabel && <span style={{ fontSize: '9px', color: '#94A3B8' }}>{sublabel}</span>}
      <div style={{ width: '2px', height: '10px', background: '#CBD5E1' }} />
      <span style={{ fontSize: '13px', color: '#94A3B8', lineHeight: 1 }}>▼</span>
    </div>
  )

  return (
    <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '24px 28px', marginBottom: '24px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '4px' }}>
        <h2 style={{ fontSize: '15px', fontWeight: 700, color: '#0F172A', margin: 0 }}>End-to-end data flow</h2>
        <span style={{ fontSize: '10px', fontWeight: 700, background: '#FEF3C7', color: '#92400E', border: '1px solid #FDE68A', padding: '2px 8px', borderRadius: '4px' }}>CURRENT &rarr; TARGET</span>
      </div>
      <p style={{ fontSize: '11.5px', color: '#64748B', marginBottom: '20px', lineHeight: 1.5 }}>
        Today's path: source systems → Logic Layer workbook (3 tabs) → Rosetta IMS. Target: pipelines write straight
        into v7 / Rosetta IMS database; Biz Ops queries v7. Logic Layer Sheet is being replaced.{' '}
        <strong style={{ color: '#991B1B' }}>IMS never writes upstream to Google Sheet.</strong>
      </p>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 230px', gap: '24px', alignItems: 'start' }}>

        {/* Left: flow */}
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center' }}>

          {/* A: Source systems (input) */}
          <div style={{ width: '100%' }}>
            <StepLabel letter="A" text="Source Systems" />
            <div style={{ display: 'flex', gap: '6px' }}>
              {[
                { name: 'DaySmart', sub: 'Clinic POS / stock' },
                { name: 'Shopify', sub: 'E-com / stock / autoship' },
                { name: 'HKTV',    sub: 'Marketplace / fees' },
                { name: 'Catalogues', sub: 'OCR + HITL' },
                { name: 'WhatsApp', sub: 'Supplier expiry' },
                { name: 'SF Express', sub: 'Logistics rate card' },
              ].map(({ name, sub }) => (
                <div key={name} style={{ flex: 1, background: '#F8FAFC', border: '2px solid #CBD5E1', borderRadius: '8px', padding: '8px 6px', textAlign: 'center' }}>
                  <p style={{ fontSize: '11px', fontWeight: 700, color: '#0F172A', margin: 0 }}>{name}</p>
                  <p style={{ fontSize: '9px', color: '#64748B', margin: '2px 0 0 0', lineHeight: 1.3 }}>{sub}</p>
                </div>
              ))}
            </div>
          </div>

          <FlowArrow label="daily / hourly / per-batch — read-only" />

          {/* B: Logic Layer workbook with 3 tabs */}
          <div style={{ width: '100%' }}>
            <StepLabel letter="B" text="Logic Layer workbook (today — being replaced)" color="#92400E" />
            <div style={{ background: '#FFFBEB', border: '2px dashed #F59E0B', borderRadius: '8px', padding: '13px 14px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '8px', marginBottom: '10px' }}>
                <a
                  href="https://docs.google.com/spreadsheets/d/1PWcRMt0FIdUCxeFz9BxhBXpDyWsInidxAW753DNI2A4/edit"
                  target="_blank"
                  rel="noreferrer"
                  style={{ fontSize: '12px', fontWeight: 700, color: '#78350F', textDecoration: 'underline' }}
                >
                  Logic Layer Google Sheet ↗
                </a>
                <span style={{ fontSize: '9px', fontWeight: 700, background: '#FEF2F2', color: '#991B1B', border: '1px solid #FECACA', padding: '2px 7px', borderRadius: '3px' }}>TEMPORARY</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '8px', textAlign: 'left' }}>
                <div style={{ background: 'rgba(120,53,15,0.06)', borderRadius: '6px', padding: '8px 10px' }}>
                  <p style={{ fontSize: '10.5px', fontWeight: 700, color: '#78350F', marginBottom: '3px' }}>
                    📋 <a
                      href="https://docs.google.com/spreadsheets/d/1PWcRMt0FIdUCxeFz9BxhBXpDyWsInidxAW753DNI2A4/edit?gid=1102115131"
                      target="_blank"
                      rel="noreferrer"
                      style={{ color: '#78350F', textDecoration: 'underline' }}
                    >Approval Matrix tab ↗</a>
                  </p>
                  <p style={{ fontSize: '9.5px', color: '#92400E', margin: 0, lineHeight: 1.5 }}>
                    The RULES tab. Sam owns it. WOC + GP% thresholds + exception logic.
                    <em> Migrates to code/config in Rosetta.</em>
                  </p>
                </div>
                <div style={{ background: 'rgba(120,53,15,0.06)', borderRadius: '6px', padding: '8px 10px' }}>
                  <p style={{ fontSize: '10.5px', fontWeight: 700, color: '#78350F', marginBottom: '3px' }}>📦 Biz Ops tab</p>
                  <p style={{ fontSize: '9.5px', color: '#92400E', margin: 0, lineHeight: 1.5 }}>
                    The 85-col PO TRANSACTIONAL LOG. One row per PO line. Pulls AM rules + Data SKU master.
                    <em> Migrates to purchase_orders table in Rosetta IMS.</em>
                  </p>
                </div>
                <div style={{ background: 'rgba(120,53,15,0.06)', borderRadius: '6px', padding: '8px 10px' }}>
                  <p style={{ fontSize: '10.5px', fontWeight: 700, color: '#78350F', marginBottom: '3px' }}>📚 Data tab</p>
                  <p style={{ fontSize: '9.5px', color: '#92400E', margin: 0, lineHeight: 1.5 }}>
                    Old SSOT SKU master cached inside Logic Layer. XLOOKUP'd by Biz Ops (e.g. Data!BM = Basic Cost).
                    <em> Replaced by v7.</em>
                  </p>
                </div>
              </div>
            </div>
          </div>

          <FlowArrow label="pipelines (Desmond) + manual entry — one-way import" sublabel="6 ingestion pipelines populate v7 operational columns" />

          {/* C: v7 = Rosetta IMS database */}
          <div style={{ width: '100%' }}>
            <StepLabel letter="C" text="v7 = Rosetta IMS database (the new home)" color="#4338CA" />
            <div style={{ background: '#EEF2FF', border: '2px solid #818CF8', borderRadius: '8px', padding: '13px 16px' }}>
              <p style={{ fontSize: '12px', fontWeight: 700, color: '#3730A3', margin: '0 0 8px 0', textAlign: 'center' }}>Rosetta IMS — multi-table database</p>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr 1fr', gap: '6px' }}>
                <div style={{ background: 'white', border: '1px solid #C7D2FE', borderRadius: '5px', padding: '6px 8px', textAlign: 'center' }}>
                  <p style={{ fontSize: '10px', fontWeight: 700, color: '#166534', margin: 0 }}>🟢 SKU_MASTER</p>
                  <p style={{ fontSize: '9px', color: '#475569', margin: '2px 0 0 0', lineHeight: 1.4 }}>reference cols (cost, MBB, weight, category, hero, fees)</p>
                </div>
                <div style={{ background: 'white', border: '1px solid #C7D2FE', borderRadius: '5px', padding: '6px 8px', textAlign: 'center' }}>
                  <p style={{ fontSize: '10px', fontWeight: 700, color: '#1E40AF', margin: 0 }}>🔵 SKU_MASTER ops cols</p>
                  <p style={{ fontSize: '9px', color: '#475569', margin: '2px 0 0 0', lineHeight: 1.4 }}>stock_*, demand_120d_*, jit, autoship, expiry</p>
                </div>
                <div style={{ background: 'white', border: '1px solid #C7D2FE', borderRadius: '5px', padding: '6px 8px', textAlign: 'center' }}>
                  <p style={{ fontSize: '10px', fontWeight: 700, color: '#5B21B6', margin: 0 }}>🟣 Config tables</p>
                  <p style={{ fontSize: '9px', color: '#475569', margin: '2px 0 0 0', lineHeight: 1.4 }}>sf_express_rates · (proposed) category_gp_floors</p>
                </div>
                <div style={{ background: 'white', border: '1px solid #C7D2FE', borderRadius: '5px', padding: '6px 8px', textAlign: 'center' }}>
                  <p style={{ fontSize: '10px', fontWeight: 700, color: '#7C2D12', margin: 0 }}>🟡 purchase_orders</p>
                  <p style={{ fontSize: '9px', color: '#475569', margin: '2px 0 0 0', lineHeight: 1.4 }}>(future) per-PO log replacing Biz Ops tab</p>
                </div>
              </div>
              <p style={{ fontSize: '10px', color: '#4338CA', margin: '8px 0 0 0', textAlign: 'center', fontStyle: 'italic' }}>
                One database. Different tables. Different update cadences. Biz Ops / future-procurement queries one endpoint.
              </p>
            </div>
          </div>

          <FlowArrow label="human verification + 3-way matching" />

          {/* D: Data Review */}
          <div style={{ width: '100%' }}>
            <StepLabel letter="D" text="Human-in-the-loop" color="#1E40AF" />
            <div style={{ background: '#EFF6FF', border: '2px solid #93C5FD', borderRadius: '8px', padding: '13px 16px' }}>
              <p style={{ fontSize: '12px', fontWeight: 700, color: '#1E3A8A', textAlign: 'center', marginBottom: '10px' }}>/data-review</p>
              <div style={{ display: 'flex', gap: '8px' }}>
                <div style={{ flex: 1, background: 'white', border: '1px solid #BFDBFE', borderRadius: '6px', padding: '9px 12px', textAlign: 'center' }}>
                  <p style={{ fontSize: '11px', fontWeight: 700, color: '#1E40AF', margin: '0 0 3px 0' }}>👤 OCR + HITL</p>
                  <p style={{ fontSize: '9.5px', color: '#3B82F6', margin: 0, lineHeight: 1.5 }}>Cost, MBB, names, weights, category — promotes OCR to verified</p>
                </div>
                <div style={{ flex: 1, background: 'white', border: '1px solid #BFDBFE', borderRadius: '6px', padding: '9px 12px', textAlign: 'center' }}>
                  <p style={{ fontSize: '11px', fontWeight: 700, color: '#1E40AF', margin: '0 0 3px 0' }}>🔄 3-way match (Desmond)</p>
                  <p style={{ fontSize: '9.5px', color: '#3B82F6', margin: 0, lineHeight: 1.5 }}>PO ↔ receipt ↔ invoice → cost_source upgraded to 100%</p>
                </div>
              </div>
            </div>
          </div>

          <FlowArrow label="exports — direct to source systems" color="#166534" sublabel="Sheet is bypassed on return leg" />

          {/* E: Source systems (output) */}
          <div style={{ width: '100%' }}>
            <StepLabel letter="E" text="Feed corrected data back" color="#166534" />
            <div style={{ display: 'flex', gap: '8px' }}>
              {['DaySmart', 'Shopify', 'HKTV'].map((sys) => (
                <div key={sys} style={{ flex: 1, background: '#F0FDF4', border: '2px solid #86EFAC', borderRadius: '8px', padding: '10px', textAlign: 'center' }}>
                  <p style={{ fontSize: '12px', fontWeight: 700, color: '#14532D', margin: 0 }}>{sys}</p>
                  <p style={{ fontSize: '9.5px', color: '#166534', margin: '2px 0 0 0' }}>corrected costs / prices / stock</p>
                </div>
              ))}
            </div>
          </div>

        </div>

        {/* Right: key rules */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', paddingTop: '26px' }}>

          <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: '6px', padding: '10px 12px' }}>
            <p style={{ fontSize: '10px', fontWeight: 700, color: '#991B1B', margin: 0 }}>
              ⛔ IMS never writes back to Google Sheets
            </p>
          </div>

          <div style={{ background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: '6px', padding: '10px 12px' }}>
            <p style={{ fontSize: '10px', fontWeight: 700, color: '#166534', margin: 0 }}>
              ✅ One database, one query endpoint for Biz Ops
            </p>
          </div>

          <div style={{ background: '#EEF2FF', border: '1px solid #C7D2FE', borderRadius: '6px', padding: '10px 12px' }}>
            <p style={{ fontSize: '10px', fontWeight: 700, color: '#4338CA', margin: 0 }}>
              🔵 Operational columns update hourly/daily via pipelines (Desmond)
            </p>
          </div>

          <div style={{ background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '6px', padding: '10px 12px' }}>
            <p style={{ fontSize: '10px', fontWeight: 700, color: '#92400E', margin: 0 }}>
              📋 AM rules tab is the last piece staying in Sheets (Sam owns)
            </p>
          </div>

        </div>
      </div>
    </div>
  )
}

function CurrentState() {
  return (
    <div>
      <LayerLabel>1 · Source systems (HK)</LayerLabel>
      <Row>
        <Card tone="system" title="Shopify" subtitle="E-commerce sales + inventory" tag="PetProject" />
        <Card tone="system" title="HKTVMall" subtitle="Marketplace sales + clearance" tag="PetProject" />
        <Card tone="system" title="Warehouse" subtitle="E-commerce warehouse stock" tag="PetProject" />
        <Card tone="system" title="DaySmart" subtitle="Clinic POS + inventory + CRM" tag="Ohana" />
      </Row>

      <Arrow label="HK tech team — daily CSV/API pulls" sublabel="One-way: read only, never written back" />

      <LayerLabel>2 · Aggregation layer (Logic Layer workbook — 3 tabs, being replaced by v7)</LayerLabel>
      <Row>
        <Card
          tone="sheet"
          title="Google Sheets — Logic Layer workbook"
          subtitle="Three distinct tabs doing three different jobs."
          items={[
            '📋 Approval Matrix tab — RULES text. Red/Amber/Green thresholds + exception logic. Sam owns.',
            '📦 Biz Ops tab — 85-col per-PO transactional log. One row per PO line. Pulls AM rules + Data tab SKU master.',
            '📚 Data tab — old SSOT SKU master cached inside Logic Layer. XLOOKUP source for Biz Ops cost/MBB/weight/category. Replaced by v7.',
          ]}
        />
      </Row>
      <Pain>Cost prices are missing or inaccurate in the Data tab — without them the Approval Matrix rules silently break. v7 closes this gap by replacing the Data tab as the SKU master source.</Pain>

      <Arrow label="AM rules tab guard-rails consulted by Biz Ops row" sublabel="WOC thresholds + GP% floors + exception rules" />

      <LayerLabel>3 · Operations decision-making</LayerLabel>
      <Row>
        <Card
          tone="human"
          title="Philippines Ops Team"
          subtitle="Reads Logic Layer outputs and adjusts."
          items={[
            'Option A — Increase PO qty to unlock bulk-buy discount (lifts margin)',
            'Option B — Raise selling price to clear the margin floor',
            'Approval matrix in Sheet determines auto vs review',
          ]}
        />
      </Row>

      <Arrow label="Issue PO to supplier (outside system)" />

      <LayerLabel>4 · Receipt + 3-way match</LayerLabel>
      <Row>
        <Card
          tone="human"
          title="HK Warehouse / Frontline"
          subtitle="Receives goods. Scans receipts into Google Drive."
          tag="Manual"
        />
        <Arrow direction="right" label="receipts" />
        <Card
          tone="human"
          title="PH Ops — 3-way match"
          subtitle="Reconciles PO ↔ receipt ↔ goods received."
          items={[
            'Verifies actual cost paid against catalogue',
            'Inputs cost back into Biz Ops tab',
            'Inputs PO qty + expiry into DaySmart (writeback!)',
          ]}
        />
      </Row>
      <Pain>DaySmart is both a <em>source</em> (step 1) and a <em>destination</em> (step 4). This creates a circular dependency — the system reads from DaySmart, then writes back into DaySmart, then re-reads its own write on the next pull.</Pain>

      <Arrow label="Matched line items handed off" />

      <LayerLabel>5 · Accounting</LayerLabel>
      <Row>
        <Card
          tone="output"
          title="PH Accounts → QuickBooks"
          subtitle="Manual entry of matched items. Margin analysis runs here, not in the Logic Layer."
          tag="Truth lives downstream"
        />
      </Row>
      <Pain>Margin analysis happens in QuickBooks <em>after the fact</em>, but margin decisions are made in the Sheet <em>before the fact</em>. The feedback loop is days or weeks long.</Pain>
    </div>
  )
}

function TargetState() {
  return (
    <div>
      <LayerLabel color="#4338CA">Phase 1 — Now: IMS as cost SSOT</LayerLabel>
      <Row>
        <Card tone="system" title="Shopify" subtitle="API integration (eventual)" />
        <Card tone="system" title="HKTVMall" subtitle="API or feed (eventual)" />
        <Card tone="system" title="Warehouse" subtitle="CSV import → Stock Import page" tag="Live" />
        <Card tone="system" title="DaySmart" subtitle="CSV import → Stock Import page" tag="Live" />
      </Row>

      <Arrow label="Sheet sync + CSV stock imports (manual, daily)" sublabel="One-way only — IMS never writes upstream" />

      <Row>
        <Card
          phase={1}
          title="Rosetta IMS — Master Data Service"
          subtitle="Cost, SKU master, and stock truth — live now."
          items={[
            '✅ 400+ real SKUs synced from Google Sheets',
            '✅ cost_source tier: catalogue (OCR-reviewed) > invoice_matched > po_issued > manual > sheet (one-time seed)',
            '✅ GP Matrix: every SKU × channel with cost confidence badge',
            '✅ Stock per channel · weekly_demand · WOC computed',
            '✅ catalogue_cost_staging — safe write target for OCR (human approval required)',
            '✅ Sync protection — the Sheet sync is a one-time SEED. Once IMS holds a cost from any other tier (manual, po_issued, invoice_matched, or the human-reviewed OCR catalogue flow) re-syncs cannot overwrite it; the same applies to confirmed pack sizes (uom_verified_at). Shadow columns capture what the Sheet says; discrepancy flags surface when they disagree.',
            '✅ Pack Size Audit — bulk-verification page; one stamp per row; supplier-filtered URL for teammates.',
            'Extended data flow: source systems → Sheet → IMS → human verification in IMS → CSV export → back to DaySmart/Shopify. IMS never writes to the Sheet.',
          ]}
        />
      </Row>

      <Arrow label="Demand, cost, stock feed into procurement service" />

      <LayerLabel color="#1E40AF">Phase 2 — by end July (Desmond&apos;s mission): Logic Layer becomes a service + Procurement</LayerLabel>
      <Row>
        <Card
          phase={2}
          title="Rosetta Procurement — Logic Layer Engine"
          subtitle="Logic Layer migrates from Sheets into Rosetta — by tab."
          items={[
            '📋 Approval Matrix rules tab → code/config in Rosetta (or stays in Sheets, Sam-owned)',
            '📦 Biz Ops tab → purchase_orders table in Rosetta IMS database (joins to v7 SKU_MASTER via sku_id)',
            '📚 Data tab → REPLACED by v7 SKU_MASTER (no longer needed)',
            'PO entry flows through Rosetta Procurement UI; AM rules applied automatically',
            'Sheets kept running in parallel during cutover',
          ]}
        />
      </Row>

      <Arrow label="PH ops review queue — approve or override" sublabel="Their job shifts from rule-runner to algorithm reviewer" />

      <Row>
        <Card
          phase={2}
          title="Purchase Order"
          subtitle="Issued from Rosetta. Canonical PO record stored."
          items={['Linked to supplier, SKU lines, expected costs, expected receipt date']}
        />
      </Row>

      <Arrow label="Goods + receipt arrive in HK" />

      <LayerLabel color="#166534">OCR Catalogue Ingestion — LIVE + 3-way match (Desmond)</LayerLabel>
      <Row>
        <Card
          phase={1}
          title="OCR Catalogue Scanner — LIVE"
          subtitle="Supplier PDF/Excel → Claude Haiku extraction → review queue → matched to IMS SKUs."
          items={[
            '✅ 43 digital catalogues audited; extraction pipeline live (pypdf text + Claude Haiku chunked)',
            '✅ Xero-style reconciliation UI with side-by-side diff, field-level highlighting, bulk actions',
            '✅ Supplier SKU + cost + MBB captured on match approval; compounds year-over-year',
            '✅ Brand/supplier/confidence filters for task delegation (99%+, 95-98%, 85-94%, etc.)',
            'Next: scheduled yearly re-extraction; Google Drive service account for unattended runs',
          ]}
        />
        <Card
          phase={2}
          title="3-Way Match (Desmond — scoped 25 May)"
          subtitle="PO ↔ delivery note ↔ invoice → confirmed cost → QuickBooks entry."
          items={[
            'Division of labor: AlgoGroup fixes catalogues (80% confidence); Desmond automates 3-way match + QB',
            'Current manual flow: Vienna scans DNs → Drive → Janica keys into Rosetta Sheet → Carl enters QuickBooks',
            'Proposed: Vienna scans → Drive → Desmond software OCRs scanned DNs → 3-way match → QB output',
            'Human-in-the-loop: junior accountant validates before final QuickBooks entry',
            'Next: Desmond meets Sherni (finance) to scope QB integration + month-end closing',
          ]}
        />
      </Row>

      <Arrow label="OCR-extracted costs approved → cost_source upgraded" />

      <LayerLabel color="#166534">Phase 4 — by end July (Desmond&apos;s mission): Automated writebacks</LayerLabel>
      <Row>
        <Card
          phase={4}
          tone="risk"
          title="Channel Writeback"
          subtitle="Stock + expiry pushed to Shopify, Warehouse, DaySmart."
          items={[
            'Behind human approval queue (dangerous drugs need extra care)',
            'Dispensing fee differences preserved per channel',
          ]}
        />
        <Card
          phase={4}
          title="QuickBooks via API"
          subtitle="Matched items posted automatically."
          items={[
            'Behind review queue (accounts team retains veto)',
            'Margin analysis closes the loop back into IMS',
          ]}
        />
      </Row>
    </div>
  )
}

function LogicLayerInsights() {
  // Distilled from docs/logic-layer-snapshot.csv (Biz Ops tab, gid 1102115131)
  const rules = [
    { channel: 'Clinic',      woc: '4w / 12w',  gp: '70% / 40% / 35%', netGp: 'N/A · 15% · 15%', action: 'Propose price to doctor (med) / Check competitor (rest)' },
    { channel: 'STP-JIT',     woc: '0w / 6w',   gp: '70% / 40% / 35%', netGp: '15%',             action: 'Reduce purchase quantity' },
    { channel: 'STP-AS',      woc: '2w / 6w',   gp: '70% / 40% / 35%', netGp: '15%',             action: 'Propose price (med) / Check competitor (rest)' },
    { channel: 'STP-HKTVM',   woc: '4w / 4w',   gp: '— / 40% / 35%',   netGp: '15%',             action: 'Amend / Stop the PO · competitor price ceiling/floor active' },
  ]
  const terms = [
    { code: 'QGB / SGB → Hero SKU', def: 'Qualified / Speculative Generic Brand. In IMS this is the hero_sku flag. Drives Pounce Program pairing with EBs.' },
    { code: 'MBB (Max Bulk Buy)',   def: 'Bulk-discount cost in SKU Master col T. Used as approval threshold for Hero SKU POs — distinct from basic cost.' },
    { code: 'MPQ',                  def: 'Minimum Purchase Quantity (units, not dollars). A supplier with HK$1,000 MOQ may still force a 20-capsule box that exceeds it.' },
    { code: 'CCC',                  def: 'Cash Conversion Cycle = Inventory Days + Receivable Days − Payable Days. Core profitability KPI alongside GP.' },
    { code: 'Net-of-fees GP%',      def: 'Hidden second margin floor (15%) after channel + logistics fees. Must be checked alongside gross GP%.' },
  ]

  return (
    <div style={{
      background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px',
      padding: '20px 24px', marginTop: '20px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px', paddingBottom: '10px', borderBottom: '1px solid #F1F5F9' }}>
        <h2 style={{ fontSize: '14px', fontWeight: 700, color: '#0F172A', margin: 0 }}>
          Logic Layer — what the actual rules tell us
        </h2>
        <span style={{ fontSize: '10px', fontWeight: 600, color: '#64748B', background: '#F1F5F9', padding: '2px 8px', borderRadius: '4px' }}>
          Source: <code style={{ fontSize: '10px' }}>docs/logic-layer-snapshot.csv</code>
        </span>
      </div>

      <p style={{ fontSize: '12px', color: '#475569', lineHeight: 1.7, marginBottom: '14px' }}>
        Reading the actual Biz Ops tab changed three of my prior assumptions. Recording them here so we can argue against the corrected picture.
      </p>

      {/* The matrix */}
      <p style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
        Rules matrix — 4 channels × category-specific thresholds
      </p>
      <div style={{ border: '1px solid #E2E8F0', borderRadius: '6px', overflow: 'hidden', marginBottom: '16px' }}>
        <div style={{ display: 'grid', gridTemplateColumns: '110px 110px 160px 130px 1fr', background: '#F8FAFC', padding: '7px 10px', gap: '10px', borderBottom: '1px solid #E2E8F0' }}>
          {['Channel', 'WOC init / cap', 'GP% (Med/Mid/Food)', 'Net-of-fees GP%', 'Action if breached'].map(h => (
            <span key={h} style={{ fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{h}</span>
          ))}
        </div>
        {rules.map((r, i) => (
          <div key={r.channel} style={{
            display: 'grid', gridTemplateColumns: '110px 110px 160px 130px 1fr',
            padding: '8px 10px', gap: '10px', alignItems: 'center',
            background: i % 2 === 0 ? 'white' : '#FAFAFA',
            borderBottom: i < rules.length - 1 ? '1px solid #F1F5F9' : 'none',
          }}>
            <span style={{ fontSize: '11px', fontWeight: 700, color: '#0F172A', fontFamily: 'monospace' }}>{r.channel}</span>
            <span style={{ fontSize: '11px', color: '#475569', fontFamily: 'monospace' }}>{r.woc}</span>
            <span style={{ fontSize: '11px', color: '#475569', fontFamily: 'monospace' }}>{r.gp}</span>
            <span style={{ fontSize: '11px', color: '#475569', fontFamily: 'monospace' }}>{r.netGp}</span>
            <span style={{ fontSize: '11px', color: '#475569', lineHeight: 1.5 }}>{r.action}</span>
          </div>
        ))}
      </div>

      {/* Three corrections */}
      <p style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
        Three things I had wrong before reading it
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '10px', marginBottom: '16px' }}>
        <div style={{ background: '#FEF2F2', border: '1px solid #FECACA', borderRadius: '6px', padding: '12px 14px' }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: '#991B1B', marginBottom: '5px' }}>1 · Channels don&apos;t match</p>
          <p style={{ fontSize: '11px', color: '#B91C1C', lineHeight: 1.55, margin: 0 }}>
            Logic Layer uses 4 channels (Clinic, STP-JIT, STP-AS, STP-HKTVM). IMS uses 3. JIT and AS are fulfillment <em>sub-modes</em> of the same warehouse — different WOC rules apply.
          </p>
        </div>
        <div style={{ background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '6px', padding: '12px 14px' }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: '#78350F', marginBottom: '5px' }}>2 · It&apos;s a workflow router</p>
          <p style={{ fontSize: '11px', color: '#92400E', lineHeight: 1.55, margin: 0 }}>
            Every &ldquo;action&rdquo; column is a human instruction (&ldquo;Propose Price To Doctor&rdquo;, &ldquo;Amend / Stop The PO&rdquo;). It routes cases to roles — it does not auto-decide. Phase 2 needs to be a case-routing system, not a rules engine.
          </p>
        </div>
        <div style={{ background: '#EFF6FF', border: '1px solid #BFDBFE', borderRadius: '6px', padding: '12px 14px' }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: '#1E3A8A', marginBottom: '5px' }}>3 · Hero SKU = QGB/SGB</p>
          <p style={{ fontSize: '11px', color: '#1E40AF', lineHeight: 1.55, margin: 0 }}>
            Hero SKUs (QGBs and SGBs) switch the cost basis from basic cost to <strong>Max Bulk Buy (col T)</strong>. This is how the Pounce Program economics get baked into the Logic Layer.
          </p>
        </div>
      </div>

      {/* Terminology */}
      <p style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '8px' }}>
        Terminology check (from SOG-04)
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0 24px', marginBottom: '16px' }}>
        {terms.map(t => (
          <div key={t.code} style={{ padding: '7px 0', borderBottom: '1px solid #F8FAFC' }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: '#0F172A', marginBottom: '2px', fontFamily: 'monospace' }}>{t.code}</p>
            <p style={{ fontSize: '11px', color: '#64748B', lineHeight: 1.55 }}>{t.def}</p>
          </div>
        ))}
      </div>

      {/* Phase 1 gap — TRIMMED 2026-06-02 after Biz Ops × v7 walkthrough */}
      <div style={{ background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: '6px', padding: '12px 14px' }}>
        <p style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: '6px' }}>
          What v7 still needs before Phase 2 can ingest the full ruleset (updated 2026-06-02)
        </p>
        <p style={{ fontSize: '11.5px', color: '#64748B', lineHeight: 1.55, marginBottom: '8px', fontStyle: 'italic' }}>
          v7 already has hero_sku, mbb_cost_per_unit, competitor_lowest_price, channel_fee_pct_hktv, sf_express_fee.
          Only three real gaps remain:
        </p>
        <ul style={{ margin: 0, paddingLeft: '18px' }}>
          <li style={{ fontSize: '12px', color: '#475569', lineHeight: 1.7, marginBottom: '2px' }}>
            <strong style={{ color: '#0F172A' }}>fulfillment_mode</strong> on SKU × channel (JIT vs AS vs N/A) — currently lumped into one e-com channel
          </li>
          <li style={{ fontSize: '12px', color: '#475569', lineHeight: 1.7, marginBottom: '2px' }}>
            <strong style={{ color: '#0F172A' }}>doctor_advised_price</strong> — Biz Ops col BF reads this; not in current v7 SKU_MASTER spec
          </li>
          <li style={{ fontSize: '12px', color: '#475569', lineHeight: 1.7 }}>
            <strong style={{ color: '#0F172A' }}>category_gp_floor</strong> — Biz Ops col BD reads required GP%; needs a categories config table (Medicine 70%, Food 35%, Supp/Prev/PH 40%)
          </li>
        </ul>
        <p style={{ fontSize: '11px', color: '#64748B', marginTop: '8px', lineHeight: 1.55 }}>
          <a href="/am-walkthrough" style={{ color: '#6366F1', textDecoration: 'none', fontWeight: 600 }}>See the full Biz Ops × v7 walkthrough →</a>
        </p>
      </div>
    </div>
  )
}

function FourLayerModel() {
  // The corrected architecture after the 2026-06-01 Chris × Sam × Desmond call
  // and the Biz Ops × v7 column walkthrough.

  const layers = [
    {
      key: 'ref',
      title: 'v7 — REFERENCE',
      emoji: '🟢',
      color: '#166534', bg: '#DCFCE7', border: '#BBF7D0',
      cadence: 'rare / per-catalogue-refresh',
      examples: 'cost, MBB, weight, category, supplier link, units-per-pack, hero SKU, dispensing fees, channel fees',
      populated: 'OCR + HITL via /data-review; manual edits for human-judgment fields',
      tables: 'SKU_MASTER + SUPPLIERS',
    },
    {
      key: 'op',
      title: 'v7 — OPERATIONAL',
      emoji: '🔵',
      color: '#1E40AF', bg: '#DBEAFE', border: '#BFDBFE',
      cadence: 'hourly / daily',
      examples: 'stock_clinic, stock_warehouse, demand_120d_*, unfulfilled_jit, upcoming_14d_autoship, expiration_date',
      populated: "Desmond's ingestion pipelines: Shopify Admin API, DaySmart API, HKTV export, Shopify webhooks, supplier WhatsApp → manual entry",
      tables: 'SKU_MASTER (same table, fast columns)',
    },
    {
      key: 'cfg',
      title: 'v7 — CONFIG',
      emoji: '🟣',
      color: '#5B21B6', bg: '#EDE9FE', border: '#DDD6FE',
      cadence: 'annual',
      examples: 'SF Express weight-band rates, GP floors by category (proposed)',
      populated: 'Manual edits via admin UI; rarely changed',
      tables: 'sf_express_rates (lookup) → resolved to per-SKU sf_express_fee',
    },
    {
      key: 'po',
      title: 'BIZ OPS row (per-PO)',
      emoji: '🟡',
      color: '#7C2D12', bg: '#FED7AA', border: '#FDBA74',
      cadence: 'per PO event',
      examples: 'PO No., Requisition Date, Planned Qty, FOC Qty, Invoice No., Payment Date, Receiving Date',
      populated: 'BizOps and Finance team types into the Biz Ops tab row per PO',
      tables: 'NOT in v7 — lives in the Biz Ops log itself',
    },
  ]

  const pipelines = [
    { icon: '📦', src: 'Shopify Admin API (warehouse stock)',   dest: 'stock_warehouse',         cadence: 'hourly/daily' },
    { icon: '📦', src: 'DaySmart Vet POS inventory API',          dest: 'stock_clinic',            cadence: 'hourly/daily' },
    { icon: '📈', src: 'DaySmart + Shopify + HKTV sales',         dest: 'demand_120d_* (sum into total)', cadence: 'daily' },
    { icon: '⚡', src: 'Shopify webhooks (paid + unfulfilled)',   dest: 'unfulfilled_jit',         cadence: 'real-time' },
    { icon: '🔁', src: 'Shopify subscriptions module',            dest: 'upcoming_14d_autoship',   cadence: 'daily' },
    { icon: '📱', src: 'Supplier WhatsApp → BizOps manual entry', dest: 'expiration_date',         cadence: 'per-batch (irregular)' },
  ]

  return (
    <div style={{
      background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px',
      padding: '20px 24px', marginTop: '20px',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px', paddingBottom: '10px', borderBottom: '1px solid #F1F5F9' }}>
        <h2 style={{ fontSize: '14px', fontWeight: 700, color: '#0F172A', margin: 0 }}>
          Biz Ops × v7 — one database, four layers
        </h2>
        <a href="/am-walkthrough" style={{ fontSize: '11px', fontWeight: 600, color: '#6366F1', textDecoration: 'none', background: '#EEF2FF', padding: '4px 10px', borderRadius: '4px' }}>
          See the 85-col walkthrough →
        </a>
      </div>

      <p style={{ fontSize: '12px', color: '#475569', lineHeight: 1.65, marginBottom: '14px' }}>
        The 2026-06-01 call surfaced Desmond's concern that "SKU master isn't designed to hold daily-changing
        data." The 06-02 follow-up resolved it: v7 = the Rosetta IMS database, and within that database
        we have <strong>different tables (and different columns) with different update cadences</strong>.
        One source for Biz Ops to query. No separate operational database to maintain.
      </p>

      {/* 4-layer grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '10px', marginBottom: '18px' }}>
        {layers.map(l => (
          <div key={l.key} style={{ background: l.bg, border: `1px solid ${l.border}`, borderRadius: '6px', padding: '12px 14px' }}>
            <p style={{ fontSize: '11.5px', fontWeight: 700, color: l.color, marginBottom: '6px' }}>
              {l.emoji} {l.title}
            </p>
            <div style={{ fontSize: '10.5px', color: l.color, lineHeight: 1.6 }}>
              <div><strong>Cadence:</strong> {l.cadence}</div>
              <div style={{ marginTop: '3px' }}><strong>Tables:</strong> {l.tables}</div>
              <div style={{ marginTop: '3px' }}><strong>Examples:</strong> {l.examples}</div>
              <div style={{ marginTop: '3px' }}><strong>Populated by:</strong> {l.populated}</div>
            </div>
          </div>
        ))}
      </div>

      <p style={{ fontSize: '12px', color: '#0F172A', lineHeight: 1.6, marginBottom: '10px' }}>
        Plus <strong>⚪ AM FORMULA</strong> — derived in the Biz Ops row (WOC, GP%, approval decisions, rules lookups against
        the Approval Matrix tab). Lives nowhere upstream. ~37 of 85 cols.
      </p>

      {/* Pipelines */}
      <p style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: '16px', marginBottom: '8px' }}>
        Desmond's scope — ingestion pipelines into v7 (not a separate DB)
      </p>
      <div style={{ background: '#EEF2FF', border: '1px solid #C7D2FE', borderRadius: '6px', padding: '10px 14px' }}>
        {pipelines.map(p => (
          <div key={p.dest} style={{ display: 'grid', gridTemplateColumns: '24px 1fr 24px 240px 80px', gap: '8px', alignItems: 'center', padding: '4px 0', borderBottom: '1px solid rgba(199,210,254,0.5)', fontSize: '11px' }}>
            <span style={{ fontSize: '14px' }}>{p.icon}</span>
            <span style={{ color: '#1E1B4B' }}>{p.src}</span>
            <span style={{ color: '#94A3B8', textAlign: 'center' }}>→</span>
            <code style={{ fontSize: '10.5px', color: '#4338CA', background: 'white', padding: '1px 6px', borderRadius: '3px' }}>{p.dest}</code>
            <span style={{ fontSize: '10px', color: '#64748B', textAlign: 'right' }}>{p.cadence}</span>
          </div>
        ))}
        <p style={{ fontSize: '11px', color: '#1E1B4B', marginTop: '10px', lineHeight: 1.55, paddingTop: '8px', borderTop: '1px solid rgba(199,210,254,0.5)' }}>
          <strong>Six pipelines, one destination.</strong> All write into v7 SKU_MASTER's operational columns. Biz Ops queries
          one endpoint and gets a full row joined server-side. No "data from two places."
        </p>
      </div>

      {/* Gap surfaced */}
      <p style={{ fontSize: '11px', fontWeight: 700, color: '#475569', textTransform: 'uppercase', letterSpacing: '0.06em', marginTop: '16px', marginBottom: '8px' }}>
        Gaps surfaced by the walkthrough
      </p>
      <div style={{ background: '#FFFBEB', border: '1px solid #FDE68A', borderRadius: '6px', padding: '10px 14px', fontSize: '11.5px', color: '#451A03', lineHeight: 1.6 }}>
        v7 already has nearly every column Biz Ops needs. Only two genuine gaps:
        <ul style={{ margin: '6px 0 0 18px', padding: 0 }}>
          <li>
            <code>doctor_advised_price</code> — used in col BF (Price Advised By Doctor); not in current v7 SKU_MASTER spec
          </li>
          <li>
            <code>category_gp_floor</code> — used in col BD (Required GP%); needs a categories config table (Medicine 70%, Food 35%, Supp/Prev/PH 40%)
          </li>
        </ul>
        <p style={{ marginTop: '8px', fontStyle: 'italic', color: '#7C2D12' }}>
          v7 the Google Sheet is NOT auto-edited. Any additions are made by Chris manually after review.
        </p>
      </div>
    </div>
  )
}

// ArchitectureNotes removed — was internal scoping notes (Winston's read), not relevant to team-facing page

function ArchitecturePage() {
  const [mode, setMode] = useState<Mode>('split')

  return (
    <div style={{ maxWidth: '1200px' }}>
      {/* Header */}
      <div style={{ marginBottom: '16px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A' }}>Architecture — Data Flow</h1>
        <p style={{ fontSize: '12px', color: '#94A3B8', marginTop: '3px' }}>
          Current spreadsheet-driven workflow vs target cloud architecture. A shared visual to argue against.
        </p>
      </div>

      <DataFlowDiagram />

      {/* Mode toggle */}
      <div style={{ display: 'flex', gap: '6px', marginBottom: '20px', background: '#F1F5F9', padding: '4px', borderRadius: '8px', width: 'fit-content' }}>
        {[
          { id: 'current', label: 'Current State' },
          { id: 'target',  label: 'Target State' },
          { id: 'split',   label: 'Side-by-side' },
        ].map((m) => (
          <button
            key={m.id}
            onClick={() => setMode(m.id as Mode)}
            style={{
              fontSize: '12px', fontWeight: 600,
              color: mode === m.id ? 'white' : '#475569',
              background: mode === m.id ? '#0F172A' : 'transparent',
              padding: '6px 14px', borderRadius: '6px',
              border: 'none', cursor: 'pointer',
            }}
          >
            {m.label}
          </button>
        ))}
      </div>

      {/* Diagram */}
      {mode === 'current' && (
        <div style={{ background: '#FAFAFA', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '20px 24px' }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '14px' }}>
            Current — Spreadsheet as the brain
          </p>
          <CurrentState />
        </div>
      )}

      {mode === 'target' && (
        <div style={{ background: '#FAFAFA', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '20px 24px' }}>
          <p style={{ fontSize: '11px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '14px' }}>
            Target — IMS owns master data, Rosetta owns decisions
          </p>
          <TargetState />
        </div>
      )}

      {mode === 'split' && (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px' }}>
          <div style={{ background: '#FAFAFA', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '16px 18px' }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '14px' }}>
              Current — spreadsheet brain
            </p>
            <CurrentState />
          </div>
          <div style={{ background: '#FAFAFA', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '16px 18px' }}>
            <p style={{ fontSize: '11px', fontWeight: 700, color: '#64748B', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '14px' }}>
              Target — service-based
            </p>
            <TargetState />
          </div>
        </div>
      )}

      {/* ArchitectureNotes removed — was internal scoping notes */}

      <LogicLayerInsights />

      <FourLayerModel />

      <p style={{ fontSize: '11px', color: '#CBD5E1', marginTop: '12px', textAlign: 'center' }}>
        Edit this page at <code>frontend/src/app/architecture/page.tsx</code> as the architecture evolves.
      </p>
    </div>
  )
}

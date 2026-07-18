import { createFileRoute } from '@tanstack/react-router'
import { useState, useEffect, useRef } from 'react'
import { authHeaders } from '@/lib/auth'
import { API_BASE } from '@/lib/config'

const API = API_BASE

interface StockStatus {
  total_active_products: number
  clinic:    { count: number; latest_as_of: string | null }
  warehouse: { count: number; latest_as_of: string | null }
}
interface ImportResult {
  location: string
  as_of_date: string
  rows_parsed: number
  updated: number
  unmatched_count: number
  unmatched: { raw_id: string | null; raw_name: string | null; qty: number }[]
}

interface UploadState {
  file: File | null
  date: string
  uploading: boolean
  result: ImportResult | null
  error: string | null
}

function freshUpload(): UploadState {
  return { file: null, date: new Date().toISOString().slice(0, 10), uploading: false, result: null, error: null }
}

function CoverageBar({ count, total, label }: { count: number; total: number; label: string }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0
  const color = pct >= 80 ? '#22C55E' : pct >= 40 ? '#F59E0B' : '#EF4444'
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', marginBottom: '4px' }}>
        <span style={{ color: '#64748B', fontWeight: 600 }}>{label}</span>
        <span style={{ color, fontWeight: 700 }}>{count} / {total} ({pct}%)</span>
      </div>
      <div style={{ height: '6px', background: '#F1F5F9', borderRadius: '99px', overflow: 'hidden' }}>
        <div style={{ height: '100%', width: `${pct}%`, background: color, borderRadius: '99px', transition: 'width 0.4s' }} />
      </div>
    </div>
  )
}

interface UploadCardProps {
  title: string
  subtitle: string
  location: 'clinic' | 'warehouse'
  statusCount: number
  totalProducts: number
  latestAsOf: string | null
  onDone: () => void
}

function UploadCard({ title, subtitle, location, statusCount, totalProducts, latestAsOf, onDone }: UploadCardProps) {
  const [state, setState] = useState<UploadState>(freshUpload)
  const fileRef = useRef<HTMLInputElement>(null)

  function patch(p: Partial<UploadState>) { setState(prev => ({ ...prev, ...p })) }

  async function handleUpload() {
    if (!state.file || state.uploading) return
    patch({ uploading: true, result: null, error: null })
    try {
      const fd = new FormData()
      fd.append('file', state.file)
      fd.append('location', location)
      fd.append('as_of_date', state.date)
      const res = await fetch(`${API}/stock/import`, { method: 'POST', body: fd, headers: authHeaders() })
      const data = await res.json()
      if (res.ok) {
        patch({ result: data, file: null, uploading: false })
        if (fileRef.current) fileRef.current.value = ''
        onDone()
      } else {
        patch({ error: data.detail ?? 'Import failed', uploading: false })
      }
    } catch {
      patch({ error: 'Network error — is the backend running?', uploading: false })
    }
  }

  const accentColor = location === 'clinic' ? '#6366F1' : '#0EA5E9'
  const accentBg    = location === 'clinic' ? '#EEF2FF' : '#E0F2FE'

  return (
    <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '20px', flex: 1 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', marginBottom: '16px' }}>
        <div>
          <div style={{ fontSize: '14px', fontWeight: 700, color: '#0F172A' }}>{title}</div>
          <div style={{ fontSize: '11px', color: '#94A3B8', marginTop: '2px' }}>{subtitle}</div>
        </div>
        {latestAsOf && (
          <span style={{ fontSize: '11px', background: '#F1F5F9', color: '#64748B', padding: '3px 8px', borderRadius: '4px' }}>
            Last: {latestAsOf}
          </span>
        )}
      </div>

      {/* Coverage bar */}
      <div style={{ marginBottom: '18px' }}>
        <CoverageBar count={statusCount} total={totalProducts} label="Products with stock data" />
      </div>

      {/* Upload form */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
        <div>
          <p style={{ fontSize: '11px', color: '#64748B', fontWeight: 500, marginBottom: '5px' }}>
            File (CSV or Excel)
          </p>
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={e => patch({ file: e.target.files?.[0] ?? null, result: null, error: null })}
            style={{ fontSize: '12px', color: '#0F172A' }}
          />
        </div>

        <div>
          <p style={{ fontSize: '11px', color: '#64748B', fontWeight: 500, marginBottom: '5px' }}>
            Export date
          </p>
          <input
            type="date"
            value={state.date}
            onChange={e => patch({ date: e.target.value })}
            style={{ border: '1px solid #E2E8F0', borderRadius: '6px', padding: '5px 10px', fontSize: '12px', color: '#0F172A' }}
          />
        </div>

        <button
          onClick={handleUpload}
          disabled={!state.file || state.uploading}
          style={{
            background: state.file && !state.uploading ? accentColor : '#E2E8F0',
            color: state.file && !state.uploading ? 'white' : '#94A3B8',
            border: 'none', borderRadius: '6px', padding: '8px 0',
            fontSize: '13px', fontWeight: 600,
            cursor: state.file && !state.uploading ? 'pointer' : 'default',
            alignSelf: 'flex-start', minWidth: '160px',
          }}
        >
          {state.uploading ? 'Importing…' : `Import ${title}`}
        </button>
      </div>

      {/* Error */}
      {state.error && (
        <div style={{ marginTop: '12px', fontSize: '12px', background: '#FEE2E2', color: '#991B1B', borderRadius: '6px', padding: '8px 12px' }}>
          {state.error}
        </div>
      )}

      {/* Result */}
      {state.result && (
        <div style={{ marginTop: '14px' }}>
          <div style={{ fontSize: '12px', background: accentBg, color: accentColor === '#6366F1' ? '#4338CA' : '#0369A1', borderRadius: '6px', padding: '10px 12px', fontWeight: 500 }}>
            Updated <strong>{state.result.updated}</strong> products from {state.result.rows_parsed} rows
            {state.result.unmatched_count > 0 && (
              <> · <span style={{ color: '#F59E0B', fontWeight: 700 }}>{state.result.unmatched_count} unmatched</span></>
            )}
          </div>

          {state.result.unmatched.length > 0 && (
            <div style={{ marginTop: '10px' }}>
              <p style={{ fontSize: '11px', fontWeight: 600, color: '#64748B', marginBottom: '6px' }}>
                Unmatched rows (first {state.result.unmatched.length})
              </p>
              <div style={{ border: '1px solid #E2E8F0', borderRadius: '6px', overflow: 'hidden' }}>
                {state.result.unmatched.map((row, i) => (
                  <div key={i} style={{ padding: '6px 10px', borderBottom: i < state.result!.unmatched.length - 1 ? '1px solid #F1F5F9' : 'none', fontSize: '11px', display: 'flex', gap: '10px' }}>
                    {row.raw_id && <span style={{ fontFamily: 'monospace', color: '#94A3B8' }}>{row.raw_id}</span>}
                    <span style={{ color: '#475569', flex: 1 }}>{row.raw_name ?? '—'}</span>
                    <span style={{ color: '#0F172A', fontWeight: 600 }}>{row.qty}</span>
                  </div>
                ))}
              </div>
              <p style={{ fontSize: '11px', color: '#94A3B8', marginTop: '6px' }}>
                These items were not matched to any internal SKU. Add them via Catalogue Ingestion or check column names in your export.
              </p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export const Route = createFileRoute('/_authed/stock')({ component: StockImportPage })

function StockImportPage() {
  const [status, setStatus] = useState<StockStatus | null>(null)

  async function loadStatus() {
    try {
      const res = await fetch(`${API}/stock/status`, { headers: authHeaders() })
      if (res.ok) setStatus(await res.json())
    } catch { /* backend not running yet */ }
  }

  useEffect(() => { loadStatus() }, [])

  const total = status?.total_active_products ?? 0

  return (
    <div style={{ maxWidth: '960px' }}>

      {/* Header */}
      <div style={{ marginBottom: '24px' }}>
        <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A' }}>Stock Import</h1>
        <p style={{ fontSize: '12px', color: '#94A3B8', marginTop: '2px' }}>
          Upload daily exports from DaySmart (clinic) and Warehouse to update stock levels and WOC
        </p>
      </div>

      {/* Status overview */}
      {status && (
        <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '16px 20px', marginBottom: '24px', display: 'flex', gap: '32px' }}>
          <div>
            <p style={{ fontSize: '11px', color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>Active Products</p>
            <p style={{ fontSize: '24px', fontWeight: 700, color: '#0F172A', marginTop: '2px' }}>{total.toLocaleString()}</p>
          </div>
          <div>
            <p style={{ fontSize: '11px', color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>Clinic Coverage</p>
            <p style={{ fontSize: '24px', fontWeight: 700, color: '#6366F1', marginTop: '2px' }}>
              {total > 0 ? Math.round((status.clinic.count / total) * 100) : 0}%
            </p>
          </div>
          <div>
            <p style={{ fontSize: '11px', color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.06em', fontWeight: 600 }}>Warehouse Coverage</p>
            <p style={{ fontSize: '24px', fontWeight: 700, color: '#0EA5E9', marginTop: '2px' }}>
              {total > 0 ? Math.round((status.warehouse.count / total) * 100) : 0}%
            </p>
          </div>
        </div>
      )}

      {/* Two upload cards */}
      <div style={{ display: 'flex', gap: '16px', flexWrap: 'wrap' }}>
        <UploadCard
          title="Clinic Stock"
          subtitle="DaySmart export — Base Quantity on Hand"
          location="clinic"
          statusCount={status?.clinic.count ?? 0}
          totalProducts={total}
          latestAsOf={status?.clinic.latest_as_of ?? null}
          onDone={loadStatus}
        />
        <UploadCard
          title="Warehouse Stock"
          subtitle="Warehouse export — Inventory SOH"
          location="warehouse"
          statusCount={status?.warehouse.count ?? 0}
          totalProducts={total}
          latestAsOf={status?.warehouse.latest_as_of ?? null}
          onDone={loadStatus}
        />
      </div>

      {/* Format hints */}
      <div style={{ marginTop: '24px', background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', padding: '16px 20px' }}>
        <h3 style={{ fontSize: '12px', fontWeight: 600, color: '#0F172A', marginBottom: '10px' }}>How column auto-detection works</h3>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px', fontSize: '12px', color: '#64748B' }}>
          <div>
            <p style={{ fontWeight: 600, color: '#475569', marginBottom: '4px' }}>SKU / Item Code column</p>
            <p>Detected if header contains: <code>sku</code>, <code>code</code>, <code>item id</code>, <code>item no</code>, <code>ref</code></p>
            <p style={{ marginTop: '4px' }}>Matched first to internal SKU, then to supplier SKU in IMS.</p>
          </div>
          <div>
            <p style={{ fontWeight: 600, color: '#475569', marginBottom: '4px' }}>Quantity column</p>
            <p>Detected if header contains: <code>qty</code>, <code>quantity</code>, <code>on hand</code>, <code>available</code>, <code>soh</code>, <code>stock</code></p>
            <p style={{ marginTop: '4px' }}>Commas and spaces are stripped before parsing.</p>
          </div>
        </div>
        <p style={{ fontSize: '11px', color: '#CBD5E1', marginTop: '12px' }}>
          If your export has different column names, rename the headers in the file or let us know and we will add it to the auto-detection list.
        </p>
      </div>

    </div>
  )
}

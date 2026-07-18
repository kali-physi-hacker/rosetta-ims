import { createFileRoute } from '@tanstack/react-router'
import { useState } from 'react'
import { can } from '@/lib/auth'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'
import {
  useCategoryRules, useCreateCategoryRule, useUpdateCategoryRule, useDeleteCategoryRule,
  apiErrorDetail, type CategoryRule,
} from '@/lib/api/queries'

export const Route = createFileRoute('/_authed/categories')({ component: CategoriesPage })

const inp: React.CSSProperties = { border: '1px solid #E2E8F0', borderRadius: '5px', padding: '5px 8px', fontSize: '12px', width: '100%' }

function CategoriesPage() {
  const rulesQuery = useCategoryRules()
  const createM = useCreateCategoryRule()
  const updateM = useUpdateCategoryRule()
  const deleteM = useDeleteCategoryRule()

  const [editing, setEditing] = useState<string | null>(null)
  const [draft, setDraft] = useState<Partial<CategoryRule>>({})
  const [adding, setAdding] = useState(false)
  const [newRow, setNewRow] = useState<Partial<CategoryRule>>({ storage_rule: 'any', sku_digit: '7' })

  const rules = rulesQuery.data ?? []
  const loading = rulesQuery.isPending
  const error = rulesQuery.isError ? 'Could not load categories' : ''
  const busy = createM.isPending || updateM.isPending || deleteM.isPending

  async function saveEdit(cat: string) {
    try {
      await updateM.mutateAsync({
        category: cat,
        body: {
          gp_floor: draft.gp_floor, storage_rule: draft.storage_rule,
          channel_restriction: draft.channel_restriction || null, sku_digit: draft.sku_digit,
        },
      })
      setEditing(null)
    } catch (e) { toast.error(apiErrorDetail(e, 'Save failed')) }
  }

  async function addRow() {
    if (!newRow.category?.trim()) { toast.error('Category name required'); return }
    if (!newRow.sku_digit || !/^[1-9]$/.test(newRow.sku_digit)) { toast.error('SKU digit must be 1–9'); return }
    try {
      await createM.mutateAsync({
        category: newRow.category.trim(), gp_floor: Number(newRow.gp_floor ?? 0),
        storage_rule: newRow.storage_rule || 'any',
        channel_restriction: newRow.channel_restriction || null, sku_digit: newRow.sku_digit,
      })
      setAdding(false); setNewRow({ storage_rule: 'any', sku_digit: '7' })
    } catch (e) { toast.error(apiErrorDetail(e, 'Create failed')) }
  }

  async function del(cat: string) {
    const ok = await confirmDialog({ title: 'Delete category', message: `Delete category “${cat}”?\n\nExisting products keep the label but lose its GP floor.`, confirmLabel: 'Delete', danger: true })
    if (!ok) return
    try { await deleteM.mutateAsync(cat) } catch { toast.error('Delete failed') }
  }

  const cols = '150px 80px 110px 130px 70px 130px'
  const canEdit = can('reference_admin')   // only Admins can edit reference data

  return (
    <div style={{ maxWidth: '820px' }}>
      <div style={{ marginBottom: '18px', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px' }}>
        <div>
          <h1 style={{ fontSize: '20px', fontWeight: 700, color: '#0F172A' }}>Categories</h1>
          <p style={{ fontSize: '12px', color: '#94A3B8', marginTop: '3px' }}>
            The IMS item-category list — GP floor, storage rule, and the SKU leading digit used for new SKUs.
          </p>
        </div>
        {canEdit && !adding && <button onClick={() => setAdding(true)} style={{ background: '#6366F1', color: 'white', border: 'none', borderRadius: '7px', padding: '8px 16px', fontSize: '13px', fontWeight: 600, cursor: 'pointer', whiteSpace: 'nowrap' }}>+ Add category</button>}
        {!canEdit && <span style={{ fontSize: '11px', color: '#94A3B8', background: '#F1F5F9', padding: '5px 10px', borderRadius: '6px', whiteSpace: 'nowrap' }}>View only · Admin to edit</span>}
      </div>

      {loading && <p style={{ fontSize: '13px', color: '#94A3B8' }}>Loading…</p>}
      {error && <p style={{ fontSize: '13px', color: '#EF4444' }}>{error}</p>}

      {!loading && !error && (
        <div style={{ background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'hidden' }}>
          <div style={{ display: 'grid', gridTemplateColumns: cols, gap: '10px', background: '#F8FAFC', borderBottom: '1px solid #E2E8F0', padding: '10px 16px' }}>
            {['Category', 'GP Floor', 'Storage', 'Channel', 'SKU #', ''].map((h, i) => (
              <span key={i} style={{ fontSize: '10px', fontWeight: 700, color: '#94A3B8', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{h}</span>
            ))}
          </div>

          {adding && (
            <div style={{ display: 'grid', gridTemplateColumns: cols, gap: '10px', padding: '10px 16px', alignItems: 'center', background: '#EEF2FF', borderBottom: '1px solid #E2E8F0' }}>
              <input style={inp} placeholder="Name" value={newRow.category ?? ''} onChange={e => setNewRow({ ...newRow, category: e.target.value })} autoFocus />
              <input style={inp} type="number" placeholder="%" value={newRow.gp_floor != null ? Math.round(newRow.gp_floor * 100) : ''} onChange={e => setNewRow({ ...newRow, gp_floor: e.target.value === '' ? 0 : Number(e.target.value) / 100 })} />
              <select style={inp} value={newRow.storage_rule} onChange={e => setNewRow({ ...newRow, storage_rule: e.target.value })}><option value="any">any</option><option value="clinic_only">clinic_only</option></select>
              <select style={inp} value={newRow.channel_restriction ?? ''} onChange={e => setNewRow({ ...newRow, channel_restriction: e.target.value || null })}><option value="">All</option><option value="clinic">clinic</option></select>
              <input style={inp} maxLength={1} placeholder="1–9" value={newRow.sku_digit ?? ''} onChange={e => setNewRow({ ...newRow, sku_digit: e.target.value })} />
              <div style={{ display: 'flex', gap: '6px' }}>
                <button onClick={addRow} disabled={busy} style={{ background: '#22C55E', color: 'white', border: 'none', borderRadius: '5px', padding: '5px 10px', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}>Add</button>
                <button onClick={() => setAdding(false)} style={{ background: 'none', border: '1px solid #E2E8F0', borderRadius: '5px', padding: '5px 8px', fontSize: '12px', cursor: 'pointer', color: '#64748B' }}>✕</button>
              </div>
            </div>
          )}

          {rules.map((r, i) => {
            const isEdit = editing === r.category
            return (
              <div key={r.category} style={{ display: 'grid', gridTemplateColumns: cols, gap: '10px', padding: '11px 16px', alignItems: 'center', borderBottom: i < rules.length - 1 ? '1px solid #F1F5F9' : 'none', background: i % 2 === 0 ? 'white' : '#FAFAFA' }}>
                <span style={{ fontSize: '12px', fontWeight: 700, color: '#0F172A' }}>{r.category}</span>
                {isEdit ? (
                  <>
                    <input style={inp} type="number" value={draft.gp_floor != null ? Math.round(draft.gp_floor * 100) : ''} onChange={e => setDraft({ ...draft, gp_floor: e.target.value === '' ? 0 : Number(e.target.value) / 100 })} />
                    <select style={inp} value={draft.storage_rule} onChange={e => setDraft({ ...draft, storage_rule: e.target.value })}><option value="any">any</option><option value="clinic_only">clinic_only</option></select>
                    <select style={inp} value={draft.channel_restriction ?? ''} onChange={e => setDraft({ ...draft, channel_restriction: e.target.value || null })}><option value="">All</option><option value="clinic">clinic</option></select>
                    <input style={inp} maxLength={1} value={draft.sku_digit ?? ''} onChange={e => setDraft({ ...draft, sku_digit: e.target.value })} />
                    <div style={{ display: 'flex', gap: '6px' }}>
                      <button onClick={() => saveEdit(r.category)} disabled={busy} style={{ background: '#6366F1', color: 'white', border: 'none', borderRadius: '5px', padding: '5px 10px', fontSize: '12px', fontWeight: 600, cursor: 'pointer' }}>Save</button>
                      <button onClick={() => setEditing(null)} style={{ background: 'none', border: '1px solid #E2E8F0', borderRadius: '5px', padding: '5px 8px', fontSize: '12px', cursor: 'pointer', color: '#64748B' }}>✕</button>
                    </div>
                  </>
                ) : (
                  <>
                    <span style={{ fontSize: '15px', fontWeight: 700, color: '#0F172A' }}>{r.gp_floor > 0 ? `${Math.round(r.gp_floor * 100)}%` : '—'}</span>
                    <span style={{ fontSize: '12px', color: '#475569' }}>{r.storage_rule || '—'}</span>
                    <span style={{ fontSize: '12px', color: '#64748B' }}>{r.channel_restriction || 'All'}</span>
                    <span style={{ fontSize: '13px', fontWeight: 700, color: '#4338CA', fontFamily: 'ui-monospace, monospace' }}>{r.sku_digit ?? '—'}</span>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      {canEdit ? (
                        <>
                          <button onClick={() => { setEditing(r.category); setDraft({ ...r }) }} style={{ background: 'none', border: '1px solid #E2E8F0', borderRadius: '5px', padding: '4px 10px', fontSize: '11px', fontWeight: 600, color: '#475569', cursor: 'pointer' }}>Edit</button>
                          <button onClick={() => del(r.category)} style={{ background: 'none', border: 'none', fontSize: '11px', fontWeight: 600, color: '#991B1B', cursor: 'pointer' }}>Delete</button>
                        </>
                      ) : <span style={{ fontSize: '11px', color: '#CBD5E1' }}>—</span>}
                    </div>
                  </>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

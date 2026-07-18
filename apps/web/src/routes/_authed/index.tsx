import { useState, useMemo, useEffect, useCallback, useRef, useSyncExternalStore, Suspense, type CSSProperties } from 'react'
import { createFileRoute, Link } from '@tanstack/react-router'
import type { Product, SummaryResponse, Supplier, SyncStatus } from '@/lib/types'
import { authHeaders, can } from '@/lib/auth'
import { skuToPath } from '@/lib/sku'
import { toast } from '@/lib/toast'
import { confirmDialog } from '@/lib/confirm'
import { API_BASE } from '@/lib/config'

const API = API_BASE

// Session cache so navigating to a SKU detail and back does not re-stream the whole
// list. Lives at module scope (survives route changes, cleared on a hard reload).
let _invCache: { items: Product[]; cursor: string | null } | null = null


const CAT_STYLE: Record<string, { bg: string; color: string }> = {
  'Medicine':     { bg: '#FEE2E2', color: '#991B1B' },
  'Preventative': { bg: '#FEF3C7', color: '#92400E' },
  'Supplement':   { bg: '#DBEAFE', color: '#1E40AF' },
  'Food':         { bg: '#DCFCE7', color: '#166534' },
  'Pet Hygiene':  { bg: '#F1F5F9', color: '#475569' },
  'Shampoo':      { bg: '#E0E7FF', color: '#3730A3' },
  'Toys':         { bg: '#FDF4FF', color: '#7E22CE' },
  'Cat Litter':   { bg: '#FFF7ED', color: '#9A3412' },
  'Not-For-Sale': { bg: '#F1F5F9', color: '#94A3B8' },
}

// Where this SKU is listed: SP = Shopify, DS = DaySmart (clinic), HK = HKTV Mall.
// Green = available for purchase there; gray = listed but archived/offline; absent = not listed.
function PlatformBadges({ item }: { item: Product }) {
  const defs: Array<[string, string | null, string]> = [
    ['SP', item.shopify_status, 'Shopify'],
    ['DS', item.daysmart_status, 'DaySmart (clinic)'],
    ['HK', item.hktv_status, 'HKTV Mall'],
  ]
  const shown = defs.filter(([, s]) => s)
  if (shown.length === 0) return null
  return (
    <span style={{ marginLeft: '7px', display: 'inline-flex', gap: '3px', verticalAlign: 'middle' }}>
      {shown.map(([code, status, label]) => {
        const live = status === 'active' || status === 'online'
        return (
          <span key={code} title={`${label}: ${status}`}
            style={{ fontSize: '8.5px', fontWeight: 800, letterSpacing: '0.04em', padding: '1px 4px', borderRadius: '3px',
              background: live ? '#DCFCE7' : '#F1F5F9', color: live ? '#166534' : '#94A3B8',
              border: `1px solid ${live ? '#BBF7D0' : '#E2E8F0'}` }}>
            {code}
          </span>
        )
      })}
    </span>
  )
}

// ── Redesigned "All Inventory": column defs, palette helpers, scoped styles ──
type SortKey = 'sku' | 'name' | 'woc' | 'total_qty' | 'category' | 'gp' | 'data_grade' | 'sales_120d' | 'hero_sku' | 'cost' | 'sell' | 'clinic' | 'whse' | 'mbb' | 'supply' | 'supplier' | 'storage'
type ColDef = { id: string; label: string; sort?: SortKey; align?: 'r' | 'c' | 'l'; grp?: boolean; fixed?: boolean }
const CMAP: Record<string, string> = { clinic: 'Clinic · DaySmart', shopify: 'Shopify', hktv: 'HKTV' }
const CHANNEL_LABEL: Record<string, string> = { clinic: 'Clinic', shopify: 'Shopify', hktv: 'HKTV' }
const COLS: ColDef[] = [
  { id: 'pin',    label: '',           fixed: true },
  { id: 'dq',     label: 'DQ',         sort: 'data_grade', align: 'c', fixed: true },
  { id: 'sku',    label: 'SKU',        sort: 'sku',        fixed: true },
  { id: 'name',   label: 'Product',    sort: 'name',       fixed: true },
  { id: 'sup',    label: 'Supplier',   sort: 'supplier' },
  { id: 'cat',    label: 'Category',   sort: 'category' },
  { id: 'hero',   label: 'Hero',       sort: 'hero_sku',   align: 'c' },
  { id: 'cost',   label: 'Cost',       sort: 'cost',       align: 'r', grp: true },
  { id: 'gp',     label: 'GP%',        sort: 'gp',         align: 'r' },
  { id: 'sell',   label: 'Sell',       sort: 'sell',       align: 'r' },
  { id: 'clinic', label: 'Clinic',     sort: 'clinic',     align: 'r', grp: true },
  { id: 'whse',   label: 'Warehouse',  sort: 'whse',       align: 'r' },
  { id: 'woc',    label: 'WOC',        sort: 'woc',        align: 'r' },
  { id: 'woctgt', label: 'WOC Target',                     align: 'r' },
  { id: 'mbb',    label: 'MBB',        sort: 'mbb',        align: 'l', grp: true },
  { id: 'exp',    label: 'Exp Date' },
  { id: 'store',  label: 'Storage',    sort: 'storage' },
  { id: 'sales',  label: '120d Sales', sort: 'sales_120d', align: 'r' },
  { id: 'supply', label: 'Supply',     sort: 'supply',     align: 'l' },
  { id: 'go',     label: '' },
]
// Column set for the "Margins" view — a column swap over the same rows (net-after-fees etc.).
const MARGIN_COLS: ColDef[] = [
  { id: 'pin',       label: '',                fixed: true },
  { id: 'dq',        label: 'DQ', align: 'c',  fixed: true },
  { id: 'sku',       label: 'SKU',             fixed: true },
  { id: 'name',      label: 'Product',         fixed: true },
  { id: 'cat',       label: 'Category' },
  { id: 'sup',       label: 'Supplier' },
  { id: 'm_basic',   label: 'Basic cost',      align: 'r', grp: true },
  { id: 'm_mbb',     label: 'MBB cost',        align: 'r' },
  { id: 'm_hit',     label: 'Cost to hit MBB', align: 'r' },
  { id: 'm_clinic',  label: 'Clinic net',      align: 'r', grp: true },
  { id: 'm_shopify', label: 'Shopify net',     align: 'r' },
  { id: 'm_hktv',    label: 'HKTV net',        align: 'r' },
  { id: 'go',        label: '' },
]
type MarginCh  = { price: number | null; nb: number | null; nm: number | null }
type MarginRow = { basic_cost: number | null; mbb_cost: number | null; cost_to_hit: number | null; gp_floor: number; ch: Record<string, MarginCh> }
const imsMoney = (n: number | null | undefined) => n == null ? '—' : `HK$${n < 100 ? n.toFixed(2) : Math.round(n).toLocaleString()}`
const imsGpp   = (n: number) => `${(n * 100).toFixed(1)}%`
const imsGpCls = (gp: number, floor: number) => gp >= floor ? 'good' : gp > 0 ? 'warn' : 'bad'
const catColor = (cat: string) => CAT_STYLE[cat]?.color ?? '#94A3B8'

const IMS_CSS = `
@keyframes ims-bar { from { transform: translateX(-120%) } to { transform: translateX(320%) } }
.imslist{--bg:#F6F7F9;--card:#FFFFFF;--panel:#FAFBFC;--line:#E7EAEF;--line2:#F1F3F6;--ink:#0F172A;--ink2:#334155;--muted:#5B6472;--faint:#8A93A2;--ghost:#C2C8D2;--accent:#4F46E5;--accent-ink:#3730A3;--accent-soft:#EEF0FE;--accent-line:#D5D8F7;--good:#15803D;--good-soft:#EAF6EE;--amber:#B45309;--amber-soft:#FCF3E6;--red:#C0362C;--red-soft:#FBEBEA;--mono:ui-monospace,"SF Mono",Menlo,Consolas,monospace;font-size:13px;line-height:1.45;color:var(--ink)}
.imslist *{box-sizing:border-box}
.imslist .head{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap;margin-bottom:13px}
.imslist h1{font-size:19px;font-weight:650;letter-spacing:-0.01em;margin:0;color:var(--ink)}
.imslist .sub{font-size:12.5px;color:var(--muted);margin-top:2px}
.imslist .actions{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.imslist .live{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600;color:var(--good);margin-right:2px}
.imslist .live .d{width:7px;height:7px;border-radius:50%;background:var(--good);box-shadow:0 0 0 3px var(--good-soft)}
.imslist .warnbadge{font-size:11px;font-weight:600;color:var(--amber);background:var(--amber-soft);padding:3px 8px;border-radius:6px;border:1px solid #F3E0BE}
.imslist .errbadge{font-size:11px;font-weight:600;color:var(--red);background:var(--red-soft);padding:3px 8px;border-radius:6px;border:1px solid #F1CDC9}
.imslist .btn{font-family:inherit;font-size:12.5px;font-weight:600;padding:7px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--ink2);cursor:pointer;transition:border-color .12s,background .12s}
.imslist .btn:hover{border-color:var(--ghost);background:var(--panel)}
.imslist .btn:disabled{opacity:.55;cursor:default}
.imslist .btn.pri{color:#fff;background:var(--accent);border-color:var(--accent)}
.imslist .btn.pri:hover{background:var(--accent-ink)}
.imslist .toolbar{display:flex;gap:9px;align-items:center;flex-wrap:wrap;margin-bottom:11px}
.imslist .search{position:relative;display:flex;align-items:center}
.imslist .search svg{position:absolute;left:11px;color:var(--faint)}
.imslist .search input{font-family:inherit;font-size:13px;padding:8px 12px 8px 33px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink);width:256px;outline:none}
.imslist .search input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.imslist select.ctl{font-family:inherit;font-size:12.5px;font-weight:500;padding:8px 11px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink2);outline:none;cursor:pointer}
.imslist select.ctl:hover{border-color:var(--ghost)}
.imslist select.ctl.on{color:var(--accent-ink);background:var(--accent-soft);border-color:var(--accent-line);font-weight:600}
.imslist .dd{position:relative}
.imslist .dd-btn{font-family:inherit;font-size:12.5px;font-weight:600;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--ink2);cursor:pointer;display:inline-flex;align-items:center;gap:8px}
.imslist .dd-btn:hover{border-color:var(--ghost)}
.imslist .dd-btn.on{color:var(--accent-ink);background:var(--accent-soft);border-color:var(--accent-line)}
.imslist .dd-btn .car{font-size:9px;color:var(--faint)}
.imslist .dd-btn .cnt{font-size:10px;font-weight:700;background:var(--accent);color:#fff;border-radius:99px;padding:1px 6px;min-width:16px;text-align:center}
.imslist .dd-menu{position:absolute;top:calc(100% + 6px);left:0;z-index:40;background:var(--card);border:1px solid var(--line);border-radius:10px;box-shadow:0 14px 38px rgba(15,23,42,.14);padding:7px;min-width:214px;max-height:340px;overflow:auto}
.imslist .dd-item{display:flex;align-items:center;gap:9px;padding:7px 9px;border-radius:7px;font-size:12.5px;color:var(--ink2);cursor:pointer;user-select:none}
.imslist .dd-item:hover{background:var(--line2)}
.imslist .dd-item input{accent-color:var(--accent);width:14px;height:14px;cursor:pointer}
.imslist .dd-btn .dd-cur{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:158px;display:inline-block}
.imslist .dd-opt{display:flex;align-items:center;justify-content:space-between;gap:9px;padding:7px 9px;border-radius:7px;font-size:12.5px;color:var(--ink2);cursor:pointer;white-space:nowrap}
.imslist .dd-opt:hover{background:var(--line2)}
.imslist .dd-opt.sel{color:var(--accent-ink);font-weight:600}
.imslist .dd-check{color:var(--accent);font-size:11px}
.imslist .dd-note{font-size:10px;font-weight:700;color:var(--faint);text-transform:uppercase;letter-spacing:0.04em;padding:8px 9px 3px}
.imslist .right{margin-left:auto;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.imslist .count{font-size:12.5px;color:var(--muted)}
.imslist .count b{color:var(--ink);font-weight:650}
.imslist .chan-tag{font-size:11px;color:var(--accent-ink);background:var(--accent-soft);border:1px solid var(--accent-line);border-radius:6px;padding:3px 8px;font-weight:600}
.imslist .clear{font-family:inherit;font-size:12.5px;font-weight:600;color:var(--muted);background:none;border:none;cursor:pointer;padding:4px 2px;text-decoration:underline;text-underline-offset:2px}
.imslist .clear:hover{color:var(--ink)}
.imslist .more{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-bottom:16px;padding:12px 14px;background:var(--card);border:1px solid var(--line);border-radius:10px}
.imslist .fg{display:inline-flex;align-items:center;gap:7px}
.imslist .fg .fl{font-size:11px;font-weight:600;color:var(--faint);text-transform:uppercase;letter-spacing:0.04em}
.imslist .tiles{display:flex;gap:9px;flex-wrap:wrap;margin-bottom:13px}
.imslist .tile{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:9px 14px;min-width:110px;cursor:pointer;transition:border-color .12s,box-shadow .12s}
.imslist .tile:hover{border-color:var(--ghost);box-shadow:0 2px 8px rgba(15,23,42,.04)}
.imslist .tile.on{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.imslist .tile .lab{font-size:11px;letter-spacing:0.03em;color:var(--muted);margin-bottom:6px;font-weight:600}
.imslist .tile .val{font-size:18px;font-weight:680;line-height:1;letter-spacing:-0.01em;font-variant-numeric:tabular-nums}
.imslist .tile .vsub{font-size:10.5px;color:var(--ghost);margin-top:5px}
.imslist .tbl-wrap{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow-x:auto}
.imslist table{border-collapse:separate;border-spacing:0;width:100%;min-width:1200px}
.imslist thead th{position:sticky;top:0;z-index:6;background:var(--panel);text-align:left;font-size:10.5px;font-weight:650;color:#4A5462;padding:8px 12px;border-bottom:1px solid var(--line);white-space:nowrap;user-select:none}
.imslist thead th.sortable{cursor:pointer}
.imslist thead th.sortable:hover{color:var(--ink)}
.imslist th.r,.imslist td.r{text-align:right}
.imslist th.c,.imslist td.c{text-align:center}
.imslist th .arw{margin-left:5px;font-size:10px;color:var(--ghost);font-weight:400}
.imslist th.sorted{color:var(--accent-ink)}
.imslist th.sorted .arw{color:var(--accent)}
.imslist th.grp,.imslist td.grp{border-left:1px solid var(--line)}
.imslist tbody td{padding:7px 12px;border-bottom:1px solid var(--line2);vertical-align:middle;background:var(--card)}
.imslist tbody tr:hover td{background:#FAFBFC}
.imslist tbody tr.low td{background:#FDFAF4}
.imslist tbody tr.low:hover td{background:#FBF6EC}
.imslist tbody tr.pinned td{background:#F6F5FF}
.imslist th.col-pin,.imslist td.col-pin{position:sticky;left:0;width:38px;min-width:38px;text-align:center;padding-left:0;padding-right:0}
.imslist th.col-dq,.imslist td.col-dq{position:sticky;left:38px;width:42px;min-width:42px;text-align:center;padding-left:0;padding-right:0}
.imslist th.col-sku,.imslist td.col-sku{position:sticky;left:80px;width:94px;min-width:94px;padding-left:6px}
.imslist th.col-name,.imslist td.col-name{position:sticky;left:174px}
.imslist th.col-pin,.imslist th.col-dq,.imslist th.col-sku,.imslist th.col-name{z-index:8}
.imslist td.col-pin,.imslist td.col-dq,.imslist td.col-sku,.imslist td.col-name{z-index:3}
.imslist td.col-name{box-shadow:1px 0 0 var(--line2)}
.imslist tbody tr:hover td.col-pin,.imslist tbody tr:hover td.col-dq,.imslist tbody tr:hover td.col-sku,.imslist tbody tr:hover td.col-name{background:#FAFBFC}
.imslist tbody tr.pinned td.col-pin,.imslist tbody tr.pinned td.col-dq,.imslist tbody tr.pinned td.col-sku,.imslist tbody tr.pinned td.col-name{background:#F6F5FF}
.imslist tbody tr.pinned td.col-pin{box-shadow:inset 3px 0 0 var(--accent)}
.imslist .dqg{display:inline-flex;align-items:center;justify-content:center;width:20px;height:20px;border-radius:5px;font-size:10.5px;font-weight:700}
.imslist .dqg.A{color:var(--good);background:var(--good-soft)}
.imslist .dqg.C{color:var(--red);background:var(--red-soft)}
.imslist .pin{cursor:pointer;color:var(--ghost);display:inline-flex;padding:5px;border-radius:7px;font-size:12px}
.imslist .pin:hover{color:var(--muted);background:var(--line2)}
.imslist .pin.on{color:var(--accent)}
.imslist .sku{font-family:var(--mono);font-size:11.5px;color:var(--muted);letter-spacing:-0.02em}
.imslist .pname{font-size:12.5px;font-weight:600;color:var(--ink);max-width:240px;line-height:1.25;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;text-decoration:none;cursor:pointer}
.imslist .pname:hover{color:var(--accent-ink)}
.imslist .pmeta{font-size:10.5px;color:var(--faint);margin-top:2px;display:flex;gap:5px;flex-wrap:wrap;align-items:center;max-width:240px}
.imslist .pmeta .uom{color:var(--muted);font-weight:600}
.imslist .pmeta .dot{color:var(--ghost)}
.imslist .supplier{display:inline-flex;align-items:center;gap:5px}
.imslist .snm{font-size:12px;color:var(--ink2);font-weight:500;max-width:112px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.imslist .plus{font-size:10px;font-weight:700;color:var(--accent);background:var(--accent-soft);border:1px solid var(--accent-line);padding:0 4px;border-radius:4px}
.imslist .oosdot{width:8px;height:8px;border-radius:50%}
.imslist .oosdot.part{background:var(--amber)}
.imslist .oosdot.crit{background:var(--red)}
.imslist .catcell{display:inline-flex;align-items:center;gap:7px}
.imslist .catdot{width:7px;height:7px;border-radius:50%;flex:none}
.imslist .cattx{font-size:12px;color:var(--ink2);font-weight:500;white-space:nowrap}
.imslist .starbtn{color:var(--ghost);font-size:13px}
.imslist .starbtn.on{color:var(--accent)}
.imslist .num{font-size:12.5px;color:var(--ink);font-variant-numeric:tabular-nums}
.imslist .num.sub{color:var(--faint);font-size:10.5px;margin-top:2px}
.imslist .num.zero{color:var(--ghost)}
.imslist .cost b{font-weight:600}
.imslist .gp{font-size:12.5px;font-weight:650;font-variant-numeric:tabular-nums}
.imslist .gp.good{color:var(--good)}
.imslist .gp.warn{color:var(--amber)}
.imslist .gp.bad{color:var(--red)}
.imslist .miss{font-size:10px;font-weight:600;color:var(--amber);background:var(--amber-soft);padding:1px 6px;border-radius:4px;white-space:nowrap}
.imslist .woc{display:inline-flex;align-items:center;gap:6px;font-size:12.5px;font-weight:650;font-variant-numeric:tabular-nums}
.imslist .wdot{width:7px;height:7px;border-radius:50%;flex:none}
.imslist .mbb{font-size:12px;color:var(--accent-ink);font-weight:600;white-space:nowrap}
.imslist .mbb.none{color:var(--ghost)}
.imslist .store{font-size:11px;font-weight:600;padding:2px 9px;border-radius:5px;white-space:nowrap;border:1px solid}
.imslist .store.clinic{background:var(--accent-soft);color:var(--accent-ink);border-color:var(--accent-line)}
.imslist .store.any{background:var(--line2);color:var(--muted);border-color:var(--line)}
.imslist .supply{display:inline-flex;align-items:center;gap:6px;font-size:12px;font-weight:600}
.imslist .supply.ok{color:var(--good)}
.imslist .supply.part{color:var(--amber)}
.imslist .supply.crit{color:var(--red)}
.imslist .supply .sd{width:8px;height:8px;border-radius:50%}
.imslist .supply.ok .sd{background:var(--good)}
.imslist .supply.part .sd{background:var(--amber)}
.imslist .supply.crit .sd{background:var(--red)}
.imslist .open{color:var(--ghost);cursor:pointer;text-decoration:none;font-size:17px}
.imslist .open:hover{color:var(--accent)}
.imslist .plats{display:inline-flex;gap:3px;margin-left:5px;vertical-align:middle}
.imslist .empty{padding:46px;text-align:center;color:var(--faint);font-size:13px}
.imslist tr.pinhead td{background:var(--accent-soft);color:var(--accent-ink);font-size:11px;font-weight:700;padding:8px 14px}
.imslist tr.pinhead a{color:var(--accent);font-weight:700;cursor:pointer;text-decoration:underline;text-underline-offset:2px}
.imslist tr.pinsep td{padding:0;height:9px;background:var(--bg);border:none}
.imslist .showmore{text-align:center;padding:16px;border-top:1px solid var(--line2)}
.imslist .showmore button{background:var(--card);border:1px solid var(--line);border-radius:7px;padding:8px 18px;font-size:12px;font-weight:600;color:var(--accent);cursor:pointer;font-family:inherit}
.ims-pop{position:fixed;z-index:100;background:#FFFFFF;border:1px solid #E7EAEF;border-radius:12px;box-shadow:0 18px 46px rgba(15,23,42,.20);width:326px;font-size:12px;color:#0F172A}
.ims-pop .pop-h{font-size:10.5px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#8A93A2;padding:11px 14px 9px;border-bottom:1px solid #F1F3F6}
.ims-pop .pop-b{padding:10px 14px 12px;max-height:264px;overflow:auto}
.ims-pop .pop-f{padding:9px 14px;border-top:1px solid #F1F3F6;font-size:10.5px;color:#8A93A2;background:#FAFBFC;border-radius:0 0 12px 12px;line-height:1.5}
.ims-pop .srow{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;padding:9px 0;border-top:1px solid #F1F3F6}
.ims-pop .srow:first-child{border-top:none}
.ims-pop .s-nm{font-weight:650;color:#0F172A;font-size:12.5px}
.ims-pop .s-meta{color:#5B6472;font-size:10.5px;margin-top:2px;font-family:ui-monospace,Menlo,monospace}
.ims-pop .s-tags{margin-top:6px}
.ims-pop .s-stat{font-size:9.5px;font-weight:700;padding:1px 6px;border-radius:4px;display:inline-flex;align-items:center;gap:4px}
.ims-pop .s-stat.ok{background:#EAF6EE;color:#15803D}
.ims-pop .s-stat.oos{background:#FBEBEA;color:#C0362C}
.ims-pop .s-stat .b{width:6px;height:6px;border-radius:50%;background:currentColor}
.ims-pop .s-uc{font-weight:700;font-size:13px;font-variant-numeric:tabular-nums;text-align:right;flex:none}
.ims-pop .s-low{display:block;font-size:9px;font-weight:700;color:#15803D;margin-top:3px}
.ims-pop .ch-row{padding:9px 0;border-top:1px solid #F1F3F6}
.ims-pop .ch-row:first-child{border-top:none}
.ims-pop .ch-h{display:flex;align-items:center;justify-content:space-between;gap:10px}
.ims-pop .ch-nm{font-weight:650;color:#0F172A;display:flex;align-items:center;gap:6px}
.ims-pop .ch-st{width:7px;height:7px;border-radius:50%}
.ims-pop .ch-st.on{background:#15803D}
.ims-pop .ch-st.off{background:#C2C8D2}
.ims-pop .ch-net{font-weight:700;font-variant-numeric:tabular-nums;font-size:12.5px}
.ims-pop .ch-net.good{color:#15803D}
.ims-pop .ch-net.warn{color:#B45309}
.ims-pop .ch-net.bad{color:#C0362C}
.ims-pop .ch-break{font-size:10.5px;color:#5B6472;margin-top:4px;font-variant-numeric:tabular-nums}
.ims-pop .buy-row{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 10px;margin-top:7px;border:1px solid #F1F3F6;border-radius:8px}
.ims-pop .buy-l{font-size:11.5px;color:#334155}
.ims-pop .buy-l b{color:#0F172A}
.ims-pop .buy-r{text-align:right;flex:none;font-variant-numeric:tabular-nums}
.ims-pop .buy-w{font-weight:700;color:#15803D;font-size:12.5px}
.ims-pop .buy-c{font-size:10.5px;color:#5B6472;margin-top:1px}
.ims-pop .mbb-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px solid #F1F3F6;font-size:12px}
.ims-pop .mbb-row:first-child{border-top:none}
.ims-pop .mbb-k{color:#334155}
.ims-pop .mbb-v{font-weight:700;color:#3730A3;font-variant-numeric:tabular-nums}
.exp-wrap{position:relative;display:inline-block}
.exp-backdrop{position:fixed;inset:0;z-index:90}
.exp-pop{position:absolute;top:calc(100% + 6px);right:0;z-index:100;background:#FFFFFF;border:1px solid #E7EAEF;border-radius:12px;box-shadow:0 18px 46px rgba(15,23,42,.20);width:452px;max-width:92vw;padding:12px 14px;color:#0F172A;text-align:left}
.exp-head{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:4px}
.exp-head b{font-size:12.5px;color:#0F172A}
.exp-reset{border:none;background:none;color:#6366F1;font-size:11.5px;font-weight:600;cursor:pointer;padding:0}
.exp-reset:hover{text-decoration:underline}
.exp-body{max-height:52vh;overflow:auto;margin:0 -4px;padding:0 4px}
.exp-grp{font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:#94A3B8;margin:10px 0 4px}
.exp-grid{display:grid;grid-template-columns:1fr 1fr;gap:1px 14px}
.exp-opt{display:flex;align-items:center;gap:7px;font-size:12px;color:#334155;padding:3px 4px;border-radius:6px;cursor:pointer;user-select:none}
.exp-opt:hover{background:#F5F7FA}
.exp-opt span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.exp-opt input{accent-color:#6366F1;cursor:pointer;flex:none;margin:0}
.exp-foot{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px;padding-top:10px;border-top:1px solid #EEF1F5}
.exp-foot span{font-size:11px;color:#8A93A2;font-variant-numeric:tabular-nums}
.exp-foot .btn.primary{background:#6366F1;color:#fff;border-color:#6366F1}
.exp-foot .btn.primary:hover{background:#4F46E5}
.exp-foot .btn.primary:disabled{opacity:.5;cursor:not-allowed}
.exp-bulk{display:flex;align-items:center;gap:7px;font-size:12px;font-weight:600;color:#334155;padding:7px 9px;margin:4px 0 2px;background:#F5F7FA;border:1px solid #EEF1F5;border-radius:8px;cursor:pointer;user-select:none}
.exp-bulk input{accent-color:#6366F1;cursor:pointer;flex:none;margin:0}
.exp-note{font-size:11.5px;line-height:1.55;color:#64748B;padding:12px 2px 2px}
.exp-note code{font-family:ui-monospace,Menlo,monospace;font-size:10.5px;background:#EEF2FF;color:#3730A3;padding:1px 5px;border-radius:4px}
.exp-note b{color:#334155;font-weight:600}
`

// Custom dropdown matching the design (replaces native <select>). Only one open at a
// time via the shared openMenu; closes on outside click (see InventoryView effect).
type DDOption = { value: string; label: string; group?: boolean }
function DDField({ id, options, current, onPick, active, openMenu, setOpenMenu }: {
  id: string; options: DDOption[]; current: string; onPick: (v: string) => void; active: boolean
  openMenu: string | null; setOpenMenu: (v: string | null) => void
}) {
  const open = openMenu === id
  const sel = options.find(o => o.value === current && !o.group)
  return (
    <div className="dd">
      <button className={`dd-btn ${active ? 'on' : ''}`} onClick={() => setOpenMenu(open ? null : id)}>
        <span className="dd-cur">{sel?.label ?? options.find(o => !o.group)?.label}</span><span className="car">▼</span>
      </button>
      {open && (
        <div className="dd-menu">
          {options.map((o, i) => o.group
            ? <div key={`g${i}`} className="dd-note">{o.label}</div>
            : <div key={o.value} className={`dd-opt ${o.value === current ? 'sel' : ''}`} onClick={() => { onPick(o.value); setOpenMenu(null) }}><span>{o.label}</span>{o.value === current && <span className="dd-check">✓</span>}</div>)}
        </div>
      )}
    </div>
  )
}

// ── CSV export columns ───────────────────────────────────────────────────────
// `default` = the columns the Export button has always written (stay on by default).
// `extra`   = opt-in columns, off by default, chosen per-export via the column picker.
const csvEsc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
const csvHkd = (v: number | null | undefined) => (v != null ? v.toFixed(2) : '')
const csvPct = (v: number | null | undefined) => (v != null ? `${(v * 100).toFixed(1)}%` : '')
const csvYN  = (v: unknown) => (v ? 'yes' : 'no')
// Bulk-update export: emit the value EXACTLY as stored in the DB (blank for null) — no
// rounding, %, or HKD formatting, so the file re-imports without lossy coercion.
const csvRaw = (v: unknown) => (v == null ? '' : String(v))

type ExportRow = {
  item: Product
  ch: Record<string, { selling_price: number | null; gp_pct: number | null }>
  issues: string
}
type ExportCol = { key: string; label: string; group: 'default' | 'extra'; value: (r: ExportRow) => string | number }

const EXPORT_COLUMNS: ExportCol[] = [
  // ---- default: exactly what Export writes today, in the same order ----
  { key: 'sku_code',        label: 'SKU',             group: 'default', value: r => csvEsc(r.item.sku_code) },
  { key: 'name',            label: 'Name',            group: 'default', value: r => csvEsc(r.item.name) },
  { key: 'brand',           label: 'Brand',           group: 'default', value: r => csvEsc(r.item.brand ?? '') },
  { key: 'category',        label: 'Category',        group: 'default', value: r => csvEsc(r.item.category) },
  { key: 'status',          label: 'Status',          group: 'default', value: r => csvEsc(r.item.status) },
  { key: 'storage_rule',    label: 'Storage Rule',    group: 'default', value: r => csvEsc(r.item.storage_rule) },
  { key: 'supplier',        label: 'Supplier',        group: 'default', value: r => csvEsc(r.item.supplier_name ?? '') },
  { key: 'units_per_pack',  label: 'Units Per Pack',  group: 'default', value: r => r.item.units_per_pack ?? '' },
  { key: 'unit_cost',       label: 'Unit Cost (HKD)', group: 'default', value: r => csvHkd(r.item.unit_cost) },
  { key: 'clinic_price',    label: 'Clinic Price',    group: 'default', value: r => csvHkd(r.ch['clinic']?.selling_price) },
  { key: 'shopify_price',   label: 'Shopify Price',   group: 'default', value: r => csvHkd(r.ch['shopify']?.selling_price) },
  { key: 'hktv_price',      label: 'HKTV Price',      group: 'default', value: r => csvHkd(r.ch['hktv']?.selling_price) },
  { key: 'clinic_gp',       label: 'Clinic GP%',      group: 'default', value: r => csvPct(r.ch['clinic']?.gp_pct) },
  { key: 'shopify_gp',      label: 'Shopify GP%',     group: 'default', value: r => csvPct(r.ch['shopify']?.gp_pct) },
  { key: 'hktv_gp',         label: 'HKTV GP%',        group: 'default', value: r => csvPct(r.ch['hktv']?.gp_pct) },
  { key: 'gp_floor',        label: 'GP Floor',        group: 'default', value: r => csvPct(r.item.gp_floor) },
  { key: 'clinic_qty',      label: 'Clinic Qty',      group: 'default', value: r => r.item.clinic_qty },
  { key: 'warehouse_qty',   label: 'Warehouse Qty',   group: 'default', value: r => r.item.warehouse_qty },
  { key: 'total_qty',       label: 'Total Qty',       group: 'default', value: r => r.item.total_qty },
  { key: 'sales_120d',      label: '120d Sales',      group: 'default', value: r => r.item.sales_120d },
  { key: 'data_grade',      label: 'Data Grade',      group: 'default', value: r => r.item.data_grade },
  { key: 'issues',          label: 'Issues',          group: 'default', value: r => csvEsc(r.issues) },
  { key: 'rrp',             label: 'RRP',             group: 'default', value: r => csvHkd(r.item.rrp) },
  // ---- extra: opt-in ----
  { key: 'supplier_sku',    label: 'Supplier SKU',      group: 'extra', value: r => csvEsc(r.item.supplier_sku ?? '') },
  { key: 'supplier_code',   label: 'Supplier Code',     group: 'extra', value: r => csvEsc(r.item.supplier_code ?? '') },
  { key: 'subcategory',     label: 'Subcategory',       group: 'extra', value: r => csvEsc(r.item.subcategory ?? '') },
  { key: 'species',         label: 'Species',           group: 'extra', value: r => csvEsc(r.item.species ?? '') },
  { key: 'uom',             label: 'UOM',               group: 'extra', value: r => csvEsc(r.item.uom ?? '') },
  { key: 'pack_unit',       label: 'Pack Unit',         group: 'extra', value: r => csvEsc(r.item.pack_unit ?? '') },
  { key: 'min_purchase_qty', label: 'Min Purchase Qty', group: 'extra', value: r => r.item.min_purchase_qty ?? '' },
  { key: 'min_sellable_qty', label: 'Min Sellable Qty', group: 'extra', value: r => r.item.min_sellable_qty ?? '' },
  { key: 'weight_g',        label: 'Weight (g)',        group: 'extra', value: r => r.item.weight_g ?? '' },
  { key: 'weekly_demand',   label: 'Weekly Demand',     group: 'extra', value: r => (r.item.weekly_demand != null ? r.item.weekly_demand.toFixed(1) : '') },
  { key: 'woc',             label: 'WOC (weeks)',       group: 'extra', value: r => (r.item.woc != null ? r.item.woc.toFixed(1) : '') },
  { key: 'basic_cost',      label: 'Basic Cost',        group: 'extra', value: r => csvHkd(r.item.primary_cost) },
  { key: 'mbb_unit_cost',   label: 'MBB Unit Cost',     group: 'extra', value: r => csvHkd(r.item.mbb_unit_cost) },
  { key: 'landed_unit_cost', label: 'Landed Unit Cost', group: 'extra', value: r => csvHkd(r.item.landed_unit_cost) },
  { key: 'cost_source',     label: 'Cost Source',       group: 'extra', value: r => csvEsc(r.item.cost_source ?? '') },
  { key: 'cost_last_updated', label: 'Cost Last Updated', group: 'extra', value: r => csvEsc((r.item.cost_last_updated ?? '').slice(0, 10)) },
  { key: 'cost_is_stale',   label: 'Cost Stale?',       group: 'extra', value: r => csvYN(r.item.cost_is_stale) },
  { key: 'shopify_status',  label: 'Shopify Status',    group: 'extra', value: r => csvEsc(r.item.shopify_status ?? '') },
  { key: 'daysmart_status', label: 'DaySmart Status',   group: 'extra', value: r => csvEsc(r.item.daysmart_status ?? '') },
  { key: 'hktv_status',     label: 'HKTV Status',       group: 'extra', value: r => csvEsc(r.item.hktv_status ?? '') },
  { key: 'hero_sku',        label: 'Hero SKU',          group: 'extra', value: r => csvYN(r.item.hero_sku) },
  { key: 'cross_channel',   label: 'Cross-channel',     group: 'extra', value: r => csvYN(r.item.cross_channel_flag) },
  { key: 'notes',           label: 'Notes',             group: 'extra', value: r => csvEsc(r.item.notes ?? '') },
  { key: 'segment',         label: 'Segment',           group: 'extra', value: r => csvEsc(r.item.segment ?? '') },
  // Ordering terms (order multiple / MOQ) — the PR-A fields
  { key: 'order_increment_qty',   label: 'Order Increment Qty',   group: 'extra', value: r => r.item.order_increment_qty ?? '' },
  { key: 'order_increment_uom',   label: 'Order Increment UOM',   group: 'extra', value: r => csvEsc(r.item.order_increment_uom ?? '') },
  { key: 'minimum_order_qty',     label: 'Minimum Order Qty',     group: 'extra', value: r => r.item.minimum_order_qty ?? '' },
  { key: 'minimum_order_uom',     label: 'Minimum Order UOM',     group: 'extra', value: r => csvEsc(r.item.minimum_order_uom ?? '') },
  { key: 'minimum_order_source',  label: 'Minimum Order Source',  group: 'extra', value: r => csvEsc(r.item.minimum_order_source ?? '') },
  { key: 'pricing_note',          label: 'Pricing Note',          group: 'extra', value: r => csvEsc(r.item.pricing_note ?? '') },
  // Platform-recorded costs + provenance
  { key: 'weight_unit',     label: 'Weight Unit',       group: 'extra', value: r => csvEsc(r.item.weight_unit ?? '') },
  { key: 'shopify_cost',    label: 'Shopify Cost',      group: 'extra', value: r => csvHkd(r.item.shopify_cost) },
  { key: 'daysmart_avg_cost', label: 'DaySmart Avg Cost', group: 'extra', value: r => csvHkd(r.item.daysmart_avg_cost) },
  { key: 'hktv_cost',       label: 'HKTV Cost',         group: 'extra', value: r => csvHkd(r.item.hktv_cost) },
  { key: 'cost_source_ref', label: 'Cost Source Ref',   group: 'extra', value: r => csvEsc(r.item.cost_source_ref ?? '') },
  { key: 'cost_updated_at', label: 'Cost Updated At',   group: 'extra', value: r => csvEsc((r.item.cost_updated_at ?? '').slice(0, 10)) },
  // Pack-size verification + edit provenance
  { key: 'uom_verified_at', label: 'UOM Verified At',   group: 'extra', value: r => csvEsc((r.item.uom_verified_at ?? '').slice(0, 10)) },
  { key: 'uom_verified_by', label: 'UOM Verified By',   group: 'extra', value: r => csvEsc(r.item.uom_verified_by ?? '') },
  { key: 'hitl_verified',   label: 'HITL Verified',     group: 'extra', value: r => csvYN(r.item.hitl_verified) },
  { key: 'last_manual_edit_at', label: 'Last Manual Edit At', group: 'extra', value: r => csvEsc((r.item.last_manual_edit_at ?? '').slice(0, 10)) },
  { key: 'last_manual_edit_by', label: 'Last Manual Edit By', group: 'extra', value: r => csvEsc(r.item.last_manual_edit_by ?? '') },
  // Sheet-sync shadow values + conflict flags
  { key: 'basic_cost_sheet',      label: 'Basic Cost (Sheet)',    group: 'extra', value: r => csvHkd(r.item.basic_cost_sheet) },
  { key: 'units_per_pack_sheet',  label: 'Units/Pack (Sheet)',    group: 'extra', value: r => r.item.units_per_pack_sheet ?? '' },
  { key: 'cost_sheet_conflict',   label: 'Cost Sheet Conflict',   group: 'extra', value: r => csvYN(r.item.cost_sheet_conflict) },
  { key: 'pack_sheet_conflict',   label: 'Pack Sheet Conflict',   group: 'extra', value: r => csvYN(r.item.pack_sheet_conflict) },
]
const DEFAULT_EXPORT_KEYS = EXPORT_COLUMNS.filter(c => c.group === 'default').map(c => c.key)

// ── Bulk-update export ────────────────────────────────────────────────────────
// The FULL set of raw DB fields that round-trip through POST /products/import-csv (the
// "Batch update" flow) = backend `_CSV_EDITABLE` in backend/routers/products.py, the exact
// set the importer accepts. DB-field-name headers; each value is the raw stored value (no
// ÷pack, no %, no HKD formatting). basic_cost = raw whole-pack cost (item.primary_cost);
// barcode/supplier_sku come from the primary supplier link (is_primary → first, matching
// backend get_primary_supplier). The one _CSV_EDITABLE member deliberately omitted is
// `unit_cost_in` — the COMPUTED per-sell-unit alias (inverse of get_unit_cost), not a stored
// column; basic_cost already carries the raw cost. Keep this list in lock-step with
// _CSV_EDITABLE.
const _primarySupplier = (r: ExportRow) => r.item.all_suppliers.find(s => s.is_primary) ?? r.item.all_suppliers[0]
const BULK_UPDATE_COLUMNS: ExportCol[] = [
  { key: 'sku_code',         label: 'sku_code',         group: 'default', value: r => csvEsc(r.item.sku_code) },
  { key: 'name',             label: 'name',             group: 'default', value: r => csvEsc(r.item.name) },
  { key: 'brand',            label: 'brand',            group: 'default', value: r => csvEsc(r.item.brand ?? '') },
  { key: 'category',         label: 'category',         group: 'default', value: r => csvEsc(r.item.category) },
  { key: 'subcategory',      label: 'subcategory',      group: 'default', value: r => csvEsc(r.item.subcategory ?? '') },
  { key: 'segment',          label: 'segment',          group: 'default', value: r => csvEsc(r.item.segment ?? '') },
  { key: 'species',          label: 'species',          group: 'default', value: r => csvEsc(r.item.species ?? '') },
  { key: 'status',           label: 'status',           group: 'default', value: r => csvEsc(r.item.status) },
  { key: 'storage_rule',     label: 'storage_rule',     group: 'default', value: r => csvEsc(r.item.storage_rule) },
  { key: 'hero_sku',         label: 'hero_sku',         group: 'default', value: r => (r.item.hero_sku ? 1 : 0) },
  { key: 'uom',              label: 'uom',              group: 'default', value: r => csvEsc(r.item.uom ?? '') },
  { key: 'pack_unit',        label: 'pack_unit',        group: 'default', value: r => csvEsc(r.item.pack_unit ?? '') },
  { key: 'units_per_pack',   label: 'units_per_pack',   group: 'default', value: r => csvRaw(r.item.units_per_pack) },
  { key: 'min_purchase_qty', label: 'min_purchase_qty', group: 'default', value: r => csvRaw(r.item.min_purchase_qty) },
  { key: 'min_sellable_qty', label: 'min_sellable_qty', group: 'default', value: r => csvRaw(r.item.min_sellable_qty) },
  { key: 'weight_g',         label: 'weight_g',         group: 'default', value: r => csvRaw(r.item.weight_g) },
  { key: 'weight_unit',      label: 'weight_unit',      group: 'default', value: r => csvEsc(r.item.weight_unit ?? '') },
  { key: 'supplier_name',    label: 'supplier_name',    group: 'default', value: r => csvEsc(r.item.supplier_name ?? '') },
  { key: 'supplier_sku',     label: 'supplier_sku',     group: 'default', value: r => csvEsc(r.item.supplier_sku ?? '') },
  { key: 'barcode',          label: 'barcode',          group: 'default', value: r => csvEsc(_primarySupplier(r)?.barcode ?? '') },
  { key: 'basic_cost',       label: 'basic_cost',       group: 'default', value: r => csvRaw(r.item.primary_cost) },
  { key: 'order_increment_qty',  label: 'order_increment_qty',  group: 'default', value: r => csvRaw(r.item.order_increment_qty) },
  { key: 'order_increment_uom',  label: 'order_increment_uom',  group: 'default', value: r => csvEsc(r.item.order_increment_uom ?? '') },
  { key: 'minimum_order_qty',    label: 'minimum_order_qty',    group: 'default', value: r => csvRaw(r.item.minimum_order_qty) },
  { key: 'minimum_order_uom',    label: 'minimum_order_uom',    group: 'default', value: r => csvEsc(r.item.minimum_order_uom ?? '') },
  { key: 'minimum_order_source', label: 'minimum_order_source', group: 'default', value: r => csvEsc(r.item.minimum_order_source ?? '') },
  { key: 'pricing_note',         label: 'pricing_note',         group: 'default', value: r => csvEsc(r.item.pricing_note ?? '') },
  { key: 'rrp',              label: 'rrp',              group: 'default', value: r => csvRaw(r.item.rrp) },
  { key: 'notes',            label: 'notes',            group: 'default', value: r => csvEsc(r.item.notes ?? '') },
]

function InventoryPage() {
  return (
    <Suspense fallback={<div style={{ padding: '60px', textAlign: 'center', color: '#94A3B8', fontSize: '13px' }}>Loading…</div>}>
      <InventoryView />
    </Suspense>
  )
}

export const Route = createFileRoute('/_authed/')({ component: InventoryPage })

function InventoryView() {
  const searchParams = new URLSearchParams(window.location.search)
  const [items, setItems]         = useState<Product[]>(() => _invCache?.items ?? [])
  const [showBatch, setShowBatch] = useState(false)
  const [summary, setSummary]     = useState<SummaryResponse | null>(null)
  const [suppliers, setSuppliers] = useState<Supplier[]>([])
  const [syncStatus, setSyncStatus] = useState<SyncStatus | null>(null)
  const [syncing, setSyncing]     = useState(false)
  const [algoSyncing, setAlgoSyncing] = useState(false)
  const [pushing, setPushing]     = useState(false)
  const [fetchingComp, setFetchingComp] = useState(false)
  const [showExportCols, setShowExportCols] = useState(false)
  const [exportCols, setExportCols] = useState<Set<string>>(() => new Set(DEFAULT_EXPORT_KEYS))
  const [bulkExport, setBulkExport] = useState(false)   // export the raw DB-field set for Batch update (not remembered)
  const [loading, setLoading]     = useState(() => !(_invCache && _invCache.items.length))
  const [error, setError]         = useState<string | null>(null)
  // Filters seed from the URL query so a filtered/searched view is shareable and
  // survives back-navigation from a SKU detail page.
  const [search, setSearch]         = useState(() => searchParams.get('q') ?? '')
  const [searchInput, setSearchInput] = useState(search)   // immediate input; `search` (below) is debounced for filtering + URL
  useEffect(() => { const t = window.setTimeout(() => setSearch(searchInput), 200); return () => window.clearTimeout(t) }, [searchInput])
  const [selectedCats, setSelectedCats] = useState<string[]>(() => { const c = searchParams.get('cats'); return c ? c.split(',').filter(Boolean) : [] })
  const [supplier, setSupplier]     = useState(() => searchParams.get('sup') ?? 'All')
  const [sortCol, setSortCol]     = useState<SortKey>(() => (searchParams.get('sort') as SortKey) || 'sales_120d')
  const [sortAsc, setSortAsc]     = useState(() => searchParams.get('dir') === 'asc')
  const [quickFilter, setQuickFilter] = useState<'active' | 'inactive' | 'low_stock' | 'below_margin' | 'out_of_stock' | 'supplier_oos' | null>(() => (searchParams.get('quick') as 'active' | 'inactive' | 'low_stock' | 'below_margin' | 'out_of_stock' | 'supplier_oos') || null)
  const [locationFilter, setLocationFilter] = useState<'All' | 'clinic_only' | 'any'>(() => (searchParams.get('loc') as 'All' | 'clinic_only' | 'any') || 'All')
  const [stockFilter, setStockFilter] = useState<'All' | 'in_stock' | 'out_of_stock'>(() => (searchParams.get('stock') as 'All' | 'in_stock' | 'out_of_stock') || 'All')
  const [qualityFilter, setQualityFilter] = useState<'All' | 'grade_a' | 'grade_b' | 'grade_c' | 'no_sku' | 'no_supplier' | 'no_cost' | 'no_pack_size' | 'low_margin' | 'priority_fix' | 'unverified' | 'verified'>(() => (searchParams.get('quality') as 'All' | 'grade_a' | 'grade_b' | 'grade_c' | 'no_sku' | 'no_supplier' | 'no_cost' | 'no_pack_size' | 'low_margin' | 'priority_fix' | 'unverified' | 'verified') || 'All')
  const [collections, setCollections] = useState<{ id: number; name: string; count: number }[]>([])
  const [collectionId, setCollectionId] = useState<number | null>(() => { const c = searchParams.get('collection'); return c ? Number(c) : null })
  const [collectionSkus, setCollectionSkus] = useState<Set<string> | null>(null)
  const [channelFilter, setChannelFilter] = useState<'all' | 'clinic' | 'shopify' | 'hktv'>(() => (searchParams.get('ch') as 'all' | 'clinic' | 'shopify' | 'hktv') || 'all')
  const [pinnedSkus, setPinnedSkus] = useState<Set<string>>(new Set())
  const [pinnedOnly, setPinnedOnly] = useState(false)   // "review list" — show only the selected (pinned) SKUs
  const [hiddenCols, setHiddenCols] = useState<Set<string>>(new Set())
  const [showMore, setShowMore] = useState(false)
  // "Margins" column view — swaps columns to net-after-fees / MBB; data fetched in bulk on toggle.
  const [marginMode, setMarginMode]         = useState(() => searchParams.get('view') === 'margins')
  const [margins, setMargins]               = useState<Record<string, MarginRow>>({})
  const [marginsLoading, setMarginsLoading] = useState(false)
  const [openMenu, setOpenMenu] = useState<string | null>(null)   // which toolbar dropdown is open
  function togglePin(sku: string) {
    setPinnedSkus(prev => { const n = new Set(prev); if (n.has(sku)) n.delete(sku); else n.add(sku); return n })
  }
  function toggleCol(id: string) {
    setHiddenCols(prev => {
      const n = new Set(prev); if (n.has(id)) n.delete(id); else n.add(id)
      try { localStorage.setItem('ims_hidden_cols', JSON.stringify([...n])) } catch {}
      return n
    })
  }
  useEffect(() => { try { const s = localStorage.getItem('ims_hidden_cols'); if (s) setHiddenCols(new Set(JSON.parse(s) as string[])) } catch {} }, [])
  // Auth (can()) reads localStorage synchronously, so admin-gated controls must only
  // render after mount — otherwise SSR (no auth) ≠ client (auth) and React bails hydration.
  const [mounted, setMounted] = useState(false)
  useEffect(() => setMounted(true), [])
  // Close any open toolbar dropdown when clicking outside it (backdrop behaviour).
  useEffect(() => {
    if (!openMenu) return
    const onDown = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest('.dd')) setOpenMenu(null) }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [openMenu])
  // Mirror the active filter/sort/search into the URL (shallow replace — no refetch).
  useEffect(() => {
    const p = new URLSearchParams()
    const s = search.trim()
    if (s) p.set('q', s)
    if (selectedCats.length) p.set('cats', selectedCats.join(','))
    if (supplier !== 'All') p.set('sup', supplier)
    if (channelFilter !== 'all') p.set('ch', channelFilter)
    if (quickFilter) p.set('quick', quickFilter)
    if (locationFilter !== 'All') p.set('loc', locationFilter)
    if (stockFilter !== 'All') p.set('stock', stockFilter)
    if (qualityFilter !== 'All') p.set('quality', qualityFilter)
    if (collectionId != null) p.set('collection', String(collectionId))
    if (sortCol !== 'sales_120d') p.set('sort', sortCol)
    if (sortAsc) p.set('dir', 'asc')
    const qs = p.toString()
    window.history.replaceState(null, '', qs ? `/?${qs}` : '/')
  }, [search, selectedCats, supplier, channelFilter, quickFilter, locationFilter, stockFilter, qualityFilter, collectionId, sortCol, sortAsc])
  // A collection seeded from the URL needs its SKU set loaded once.
  useEffect(() => { if (collectionId != null) selectCollection(collectionId) }, [])   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    fetch(`${API}/collections`, { headers: authHeaders() })
      .then(r => r.ok ? r.json() : []).then(setCollections).catch(() => {})
  }, [])
  async function selectCollection(id: number | null) {
    setCollectionId(id)
    if (id === null) { setCollectionSkus(null); return }
    try {
      const r = await fetch(`${API}/collections/${id}/products`, { headers: authHeaders() })
      if (r.ok) { const d = await r.json(); setCollectionSkus(new Set((d.products ?? []).map((p: any) => p.sku_code))) }
    } catch { setCollectionSkus(null) }
  }

  function toggleQuickFilter(f: typeof quickFilter) {
    setQuickFilter(prev => prev === f ? null : f)
  }

  useEffect(() => {
    fetch(`${API}/suppliers`, { headers: authHeaders() }).then(r => r.ok ? r.json() : []).then(setSuppliers).catch(() => {})
    fetch(`${API}/sync/status`, { headers: authHeaders() }).then(r => r.ok ? r.json() : null).then(setSyncStatus).catch(() => {})
  }, [])

  // One full load; search + supplier then filter CLIENT-side over the in-memory list.
  // (Previously every search keystroke re-fetched the entire ~5 MB product list from the
  // server, which is what made searching/filtering feel so slow.)
  const lastSyncRef = useRef<string | null>(null)   // server-clock cursor for the delta poll
  const loadIdRef = useRef(0)                       // cancels a stale background load when a fresh one starts
  const [loadProgress, setLoadProgress] = useState<{ loaded: number; total: number } | null>(null)
  const [liveAt, setLiveAt] = useState<Date | null>(null)
  const fetchData = useCallback(async (force = false) => {
    // Warm cache from a prior mount → hydrate instantly and let the delta-poll catch up.
    if (!force && _invCache && _invCache.items.length) {
      setItems(_invCache.items)
      lastSyncRef.current = _invCache.cursor
      setLoading(false)
      return
    }
    const myId = ++loadIdRef.current
    try {
      setLoading(true)
      setError(null)
      // Summary is tiny — fire it in parallel, don't block the first paint on it.
      fetch(`${API}/products/summary`, { cache: 'no-store', headers: authHeaders() })
        .then(r => (r.ok ? r.json() : null)).then(d => { if (d) setSummary(d) }).catch(() => {})

      // Stream the inventory as NDJSON — rows arrive continuously, so the screen paints in a few
      // hundred ms and then fills in smoothly. Rows are flushed to React in modest batches (the
      // heavy re-sort runs a handful of times/sec, not per row); the progress bar's CSS
      // transition smooths the motion between flushes.
      const res = await fetch(`${API}/products/stream`, { cache: 'no-store', headers: authHeaders() })
      if (!res.ok || !res.body) throw new Error(`Products API error ${res.status}`)
      const reader = res.body.getReader()
      const decoder = new TextDecoder()
      let buf = ''
      let batch: Product[] = []
      let total = 0
      let fetched = 0
      let painted = false
      const FIRST = 120, BATCH = 400
      const flush = () => {
        if (!batch.length) return
        const chunk = batch; batch = []
        if (!painted) { painted = true; setItems(chunk); setLoading(false) }
        else setItems(prev => [...prev, ...chunk])
        setLoadProgress(total && fetched < total ? { loaded: fetched, total } : null)
      }
      for (;;) {
        const { done, value } = await reader.read()
        if (loadIdRef.current !== myId) { reader.cancel().catch(() => {}); return }   // newer load started
        if (done) break
        buf += decoder.decode(value, { stream: true })
        let nl: number
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl); buf = buf.slice(nl + 1)
          if (!line) continue
          const obj = JSON.parse(line)
          if (obj._meta) { total = obj._meta.total ?? 0; if (obj._meta.now) lastSyncRef.current = obj._meta.now; continue }
          batch.push(obj as Product); fetched++
        }
        if (!painted ? batch.length >= FIRST : batch.length >= BATCH) flush()
      }
      flush()
      // Settle the bar to 100%, then clear it a beat later so it finishes gracefully.
      if (loadIdRef.current === myId) {
        if (total) setLoadProgress({ loaded: total, total })
        window.setTimeout(() => { if (loadIdRef.current === myId) setLoadProgress(null) }, 500)
      }
    } catch (e) {
      if (loadIdRef.current !== myId) return
      setError(e instanceof Error ? e.message : 'Failed to load inventory')
      setLoading(false)
      setLoadProgress(null)
    }
  }, [])

  useEffect(() => { fetchData() }, [fetchData])
  // Keep the cross-navigation cache warm as rows stream in / deltas merge.
  useEffect(() => { if (items.length) _invCache = { items, cursor: lastSyncRef.current } }, [items])

  // Realtime inventory: poll the tiny /products/changes delta every 10s and merge — so
  // SKUs confirmed/created by OTHER reviewers in catalogue onboarding appear here without
  // a manual refresh (and without re-downloading the full list).
  useEffect(() => {
    let alive = true
    const poll = async () => {
      if (document.hidden || !lastSyncRef.current) return
      try {
        const r = await fetch(`${API}/products/changes?since=${encodeURIComponent(lastSyncRef.current)}`, { headers: authHeaders() })
        if (!r.ok) return
        const d = await r.json()
        if (!alive) return
        lastSyncRef.current = d.now
        setLiveAt(new Date())
        if (d.count > 0) {
          setItems(prev => {
            const by = new Map(prev.map(p => [p.sku_code, p]))
            for (const c of d.items as Product[]) {
              if (c.status === 'DISCONTINUED') by.delete(c.sku_code)
              else by.set(c.sku_code, c)
            }
            return Array.from(by.values())
          })
        }
      } catch { /* transient poll failure — next tick retries */ }
    }
    poll()                              // immediate: surface changes made elsewhere (e.g. a re-parse confirm) on return, not after a 10s wait
    const t = setInterval(poll, 10000)
    return () => { alive = false; clearInterval(t) }
  }, [])

  async function handleAlgoSync() {
    setAlgoSyncing(true)
    try {
      const r = await fetch(`${API}/sync/algo`, { method: 'POST', headers: authHeaders() })
      const d = await r.json().catch(() => ({}))
      if (r.ok) {
        toast.success(`Live data synced — ${d.sales_skus_matched ?? 0} SKUs sales · ${d.expiry_batches_written ?? 0} expiry batches`)
        fetchData(true)
      } else {
        toast.error(d.detail ?? 'Live-data sync failed')
      }
    } catch { toast.error('Live-data sync failed') }
    finally { setAlgoSyncing(false) }
  }

  async function handleFetchCompetitors() {
    setFetchingComp(true)
    try {
      const r = await fetch(`${API}/competitors/refresh-all`, { method: 'POST', headers: authHeaders() })
      if (r.ok) { const d = await r.json(); toast.info(`Fetching ${d.count ?? 0} competitor price${d.count === 1 ? '' : 's'} in the background — reopen a SKU shortly to see updates.`) }
      else toast.error('Could not start competitor price fetch')
    } catch { toast.error('Could not start competitor price fetch') }
    finally { setFetchingComp(false) }
  }

  async function handleSync() {
    setSyncing(true)
    try {
      const res = await fetch(`${API}/sync/sheet`, { method: 'POST', headers: authHeaders() })
      if (res.ok) {
        const data: SyncStatus = await res.json()
        setSyncStatus({ ...data, synced: true })
        fetchData(true)
      }
    } finally {
      setSyncing(false)
    }
  }

  // Push IMS → SSOT sheet. Dry-run first to preview, then confirm before writing.
  async function handlePush() {
    if (pushing) return
    setPushing(true)
    try {
      const dr = await fetch(`${API}/sync/push-sheet?dry_run=true`, { method: 'POST', headers: authHeaders() })
      const p = await dr.json().catch(() => ({}))
      if (!dr.ok) { toast.error(`Push preview failed: ${p.detail ?? dr.status}`); return }
      const techNote = p.tech_fetch?.error
        ? `TECH:    ⚠ fetch failed — TECH columns left untouched (${p.tech_fetch.error})\n`
        : p.tech_columns?.length
          ? `TECH:    ${p.tech_columns.length} columns fetched (${p.tech_fetch?.matched ?? 0} SKUs matched)\n`
          : `TECH:    not configured — TECH columns left untouched\n`
      const ok = await confirmDialog({
        title: `Push ${p.products} products to the SSOT sheet?`,
        message:
          `Tab:     ${p.target?.tab}\n` +
          `Rows:    from row ${p.target?.data_start_row}\n` +
          `Columns: ${p.ims_columns?.length ?? p.columns_written?.length} IMS-owned\n` +
          techNote +
          (p.columns_unmapped?.length ? `Unmapped (skipped): ${p.columns_unmapped.join(', ')}\n` : '') +
          `\nThis writes live cells.`,
        confirmLabel: 'Push live',
      })
      if (!ok) return
      const res = await fetch(`${API}/sync/push-sheet?dry_run=false`, { method: 'POST', headers: authHeaders() })
      const r = await res.json().catch(() => ({}))
      if (res.ok) toast.success(`Pushed ${r.written_rows} rows (${r.written_cells} cells) to "${r.target?.tab}" — rows ${r.row_range}.`)
      else toast.error(`Push failed: ${r.detail ?? res.status}`)
    } catch {
      toast.error('Push error — is the backend running with GOOGLE_SA_KEY_PATH set?')
    } finally {
      setPushing(false)
    }
  }

  const toggleExportCol = (key: string) => setExportCols(prev => {
    const next = new Set(prev)
    if (next.has(key)) next.delete(key); else next.add(key)
    return next
  })

  function handleExport() {
    const cols = bulkExport ? BULK_UPDATE_COLUMNS : EXPORT_COLUMNS.filter(c => exportCols.has(c.key))
    if (!cols.length) return

    const lines = [cols.map(c => csvEsc(c.label)).join(',')]
    for (const item of sorted) {
      const ch = Object.fromEntries(item.channels.map(c => [c.channel, c])) as ExportRow['ch']
      const issues: string[] = []
      if (!/^\d{6,}$/.test(item.sku_code.trim()))                          issues.push('No valid SKU')
      if (!item.all_suppliers.find(s => s.name))                           issues.push('No supplier')
      if (item.primary_cost == null)                                        issues.push('No cost')
      if (item.units_per_pack == null)                                      issues.push('No pack size')
      if (item.channels.some(c => c.recommendation === 'Raise price ⚠'))  issues.push('Low margin')

      const row: ExportRow = { item, ch, issues: issues.join(' | ') }
      lines.push(cols.map(c => c.value(row)).join(','))
    }

    const blob = new Blob([lines.join('\r\n')], { type: 'text/csv;charset=utf-8;' })
    const url  = URL.createObjectURL(blob)
    const date = new Date().toISOString().slice(0, 10)
    const name = bulkExport ? `ims_bulk_update_${date}.csv` : `ims_${date}.csv`
    const a    = Object.assign(document.createElement('a'), { href: url, download: name })
    a.click()
    URL.revokeObjectURL(url)
    setShowExportCols(false)
  }

  const ALL_CATEGORIES = ['Medicine', 'Preventative', 'Supplement', 'Food', 'Pet Hygiene', 'Shampoo', 'Toys', 'Cat Litter', 'Not-For-Sale']

  const clientCounts = useMemo(() => ({
    active:        items.filter(i => i.status === 'ACTIVE').length,
    inactive:      items.filter(i => i.status === 'INACTIVE').length,
    low_stock:     items.filter(i => i.woc !== null && i.woc < 2).length,
    below_margin:  items.filter(i => i.channels.some(c => c.recommendation === 'Raise price ⚠')).length,
    out_of_stock:  items.filter(i => i.total_qty === 0).length,
    supplier_oos:  items.filter(i => i.all_suppliers.some(s => s.stock_status === 'out_of_stock')).length,
    uom_unverified: items.filter(i => i.channels.some(c => c.recommendation === 'Check pack size ⚠')).length,
  }), [items])


  const quickFiltered = useMemo(() => {
    let result = items
    // Client-side search + supplier (instant — no server round-trip)
    const q = search.trim().toLowerCase()
    if (q) result = result.filter(i =>
      i.name.toLowerCase().includes(q)
      || i.sku_code.toLowerCase().includes(q)
      || (i.brand ?? '').toLowerCase().includes(q)
      || (i.supplier_sku ?? '').toLowerCase().includes(q)
      || i.all_suppliers.some(s => (s.supplier_sku ?? '').toLowerCase().includes(q)))
    if (supplier !== 'All') result = result.filter(i => i.all_suppliers.some(s => s.name === supplier))
    if (collectionSkus) result = result.filter(i => collectionSkus.has(i.sku_code))
    if (channelFilter !== 'all') result = result.filter(i => {
      const st = channelFilter === 'clinic' ? i.daysmart_status : channelFilter === 'shopify' ? i.shopify_status : i.hktv_status
      return !!st && st !== 'archived' && st !== 'offline'
    })
    if (selectedCats.length > 0) result = result.filter(i => selectedCats.includes(i.category || 'Uncategorized'))
    switch (quickFilter) {
      case 'active':        result = result.filter(i => i.status === 'ACTIVE'); break
      case 'inactive':      result = result.filter(i => i.status === 'INACTIVE'); break
      case 'low_stock':     result = result.filter(i => i.woc !== null && i.woc < 2); break
      case 'below_margin':  result = result.filter(i => i.channels.some(c => c.recommendation === 'Raise price ⚠')); break
      case 'out_of_stock':  result = result.filter(i => i.total_qty === 0); break
      case 'supplier_oos':  result = result.filter(i => i.all_suppliers.some(s => s.stock_status === 'out_of_stock')); break
    }
    if (locationFilter !== 'All') result = result.filter(i => i.storage_rule === locationFilter)
    if (stockFilter === 'in_stock')     result = result.filter(i => i.total_qty > 0)
    if (stockFilter === 'out_of_stock') result = result.filter(i => i.total_qty === 0)
    switch (qualityFilter) {
      case 'grade_a':      result = result.filter(i => i.data_grade === 'A'); break
      case 'grade_c':      result = result.filter(i => i.data_grade === 'C'); break
      case 'no_sku':       result = result.filter(i => !/^\d{6,}$/.test(i.sku_code.trim())); break
      case 'no_supplier':  result = result.filter(i => !i.all_suppliers.find(s => s.name)); break
      case 'no_cost':      result = result.filter(i => i.primary_cost === null); break
      case 'no_pack_size': result = result.filter(i => i.units_per_pack === null); break
      case 'low_margin':   result = result.filter(i => i.channels.some(c => c.recommendation === 'Raise price ⚠')); break
      case 'priority_fix': result = result.filter(i => i.sales_120d > 0 && i.data_grade === 'C'); break
      case 'unverified':   result = result.filter(i => !i.hitl_verified); break
      case 'verified':     result = result.filter(i => i.hitl_verified); break
    }
    return result
  }, [items, search, supplier, collectionSkus, channelFilter, selectedCats, quickFilter, locationFilter, stockFilter, qualityFilter])

  const categories = useMemo(() => {
    const extras = [...new Set(items.map(i => i.category))].filter(c => !ALL_CATEGORIES.includes(c)).sort()
    return ['All', ...ALL_CATEGORIES, ...extras]
  }, [items])

  function getClinicGp(item: Product): number | null {
    const ch = item.channels.find(c => c.channel === 'clinic')
    return ch?.gp_pct ?? null
  }
  const clinicSell = (item: Product): number | null => item.channels.find(c => c.channel === 'clinic')?.selling_price ?? null
  const oosCount = (item: Product): number => item.all_suppliers.filter(s => s.stock_status === 'out_of_stock').length

  function sortVal(item: Product, col: typeof sortCol): string | number | null {
    switch (col) {
      case 'gp':         return getClinicGp(item)
      case 'sku':        return item.sku_code
      case 'sales_120d': return item.sales_120d
      case 'hero_sku':   return item.hero_sku ? 1 : 0
      case 'cost':       return item.primary_cost
      case 'sell':       return clinicSell(item)
      case 'clinic':     return item.clinic_qty
      case 'whse':       return item.warehouse_qty
      case 'mbb':        return item.mbb_unit_cost
      case 'supply':     return oosCount(item)
      case 'supplier':   return item.supplier_name ?? null
      case 'storage':    return item.storage_rule
      case 'data_grade': return item.data_grade
      case 'name':       return item.name
      case 'category':   return item.category
      case 'woc':        return item.woc
      case 'total_qty':  return item.total_qty
      default:           return null
    }
  }

  const sorted = useMemo(() => {
    // "Review list": show only the selected SKUs — but an active search still finds/adds across the
    // whole catalogue (so you can pin more), overriding the review filter while you type.
    const base = (pinnedOnly && !search.trim()) ? items.filter(i => pinnedSkus.has(i.sku_code)) : quickFiltered
    return [...base].sort((a, b) => {
      const av = sortVal(a, sortCol), bv = sortVal(b, sortCol)
      // nulls always sink to the bottom regardless of direction
      if (av === null && bv === null) return 0
      if (av === null) return 1
      if (bv === null) return -1
      if (typeof av === 'string' && typeof bv === 'string')
        return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av)
      return sortAsc ? (av as number) - (bv as number) : (bv as number) - (av as number)
    })
  }, [quickFiltered, items, pinnedOnly, search, pinnedSkus, sortCol, sortAsc])

  // Mount rows incrementally — rendering all ~3.4k rows at once is what made the page heavy.
  const ROW_STEP = 100
  const [rowLimit, setRowLimit] = useState(ROW_STEP)
  useEffect(() => { setRowLimit(ROW_STEP) },
    [search, supplier, collectionSkus, selectedCats, quickFilter, locationFilter, stockFilter, qualityFilter, sortCol, sortAsc, pinnedOnly])
  useEffect(() => { if (pinnedSkus.size === 0 && pinnedOnly) setPinnedOnly(false) }, [pinnedSkus, pinnedOnly])
  // When the user has narrowed the list AT ALL (search or any filter), show results
  // generously (1,000 instantly, one click for the rest) — incremental pagination only
  // applies to the full unfiltered browse, where it keeps the ~11k-row page fast.
  const narrowed = !!(search.trim() || quickFilter || selectedCats.length > 0 || supplier !== 'All'
    || locationFilter !== 'All' || stockFilter !== 'All' || qualityFilter !== 'All' || collectionSkus || pinnedOnly)
  const baseLimit = narrowed ? Math.max(rowLimit, 1000) : rowLimit
  const shownRows = useMemo(() => sorted.slice(0, baseLimit), [sorted, baseLimit])

  function toggleSort(col: typeof sortCol) {
    if (sortCol === col) setSortAsc(v => !v)
    else { setSortCol(col); setSortAsc(col === 'gp' || col === 'sales_120d' ? false : true) }
    // GP defaults descending (highest margin first); others ascending
  }

  // Hover popovers — supplier detail, margin-by-channel, WOC buy-to-cover, MBB tiers.
  const [pop, setPop] = useState<{ type: string; item: Product; x: number; y: number } | null>(null)
  const popTimer = useRef<number | null>(null)
  const showPop = (type: string, item: Product, e: React.MouseEvent) => {
    if (popTimer.current) { window.clearTimeout(popTimer.current); popTimer.current = null }
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    // Anchor X to the cell (column-aligned) and Y just under the cursor, so the card
    // opens right beneath the field being hovered rather than the tall row's bottom.
    const x = Math.max(8, Math.min(r.left, window.innerWidth - 342))
    const below = e.clientY + 14
    const y = below + 300 > window.innerHeight ? Math.max(8, e.clientY - 306) : below
    setPop({ type, item, x, y })
  }
  const hidePop = () => { if (popTimer.current) window.clearTimeout(popTimer.current); popTimer.current = window.setTimeout(() => setPop(null), 130) }
  const keepPop = () => { if (popTimer.current) { window.clearTimeout(popTimer.current); popTimer.current = null } }
  // Standard channel fee / logistics estimates (real fees live on the detail endpoint).
  const FEE: Record<string, number> = { clinic: 0, shopify: 0.029, hktv: 0.15 }
  const LOGI: Record<string, number> = { clinic: 0, shopify: 0.9, hktv: 0.6 }

  const supPopBody = (item: Product) => {
    const costs = item.all_suppliers.filter(s => s.basic_cost != null).map(s => s.basic_cost as number)
    const lo = costs.length ? Math.min(...costs) : null
    return <>
      <div className="pop-h">Suppliers · unit cost</div>
      <div className="pop-b">
        {item.all_suppliers.map(s => {
          const oos = s.stock_status === 'out_of_stock'
          return <div className="srow" key={s.id}>
            <div style={{ minWidth: 0 }}>
              <div className="s-nm">{s.name ?? 'Unnamed supplier'}</div>
              <div className="s-meta">{[s.code, `ID #${s.supplier_id ?? '—'}`, s.supplier_sku && `SKU ${s.supplier_sku}`].filter(Boolean).join(' · ')}</div>
              <div className="s-tags">{oos
                ? <span className="s-stat oos"><span className="b" />Out{s.expected_restock_at ? ` · ETA ${s.expected_restock_at}` : ''}</span>
                : <span className="s-stat ok"><span className="b" />In stock</span>}</div>
            </div>
            <div className="s-uc">{s.basic_cost != null ? imsMoney(s.basic_cost) : '—'}{s.basic_cost === lo && lo != null && item.all_suppliers.length > 1 && <span className="s-low">lowest</span>}</div>
          </div>
        })}
      </div>
      <div className="pop-f">{oosCount(item) > 0 ? <b style={{ color: '#C0362C' }}>{oosCount(item)} of {item.all_suppliers.length} out of stock. </b> : ''}{item.all_suppliers.length > 1 ? 'Lowest cost is the preferred source.' : 'Single supplier on record.'}</div>
    </>
  }
  const chPopBody = (item: Product) => (
    <>
      <div className="pop-h">Margin by channel</div>
      <div className="pop-b">
        {item.channels.filter(c => c.selling_price != null || c.is_active).map(c => {
          const s = c.selling_price
          const gross = c.gp_pct
          const fee = c.channel_fee_pct ?? FEE[c.channel] ?? 0
          const logi = LOGI[c.channel] ?? 0
          const net = (gross == null || s == null) ? null : gross - fee - logi / s
          const cls = net == null ? '' : net >= item.gp_floor ? 'good' : net > 0 ? 'warn' : 'bad'
          return <div className="ch-row" key={c.channel}>
            <div className="ch-h"><span className="ch-nm"><span className={`ch-st ${c.is_active ? 'on' : 'off'}`} />{CHANNEL_LABEL[c.channel] ?? c.channel}{c.is_active ? '' : ' · off'}</span>{net == null ? <span className="ch-net">—</span> : <span className={`ch-net ${cls}`}>{(net * 100).toFixed(1)}% net</span>}</div>
            {gross != null && s != null && <div className="ch-break">HK${s.toFixed(2)} sell{fee > 0 ? ` · −${(fee * 100).toFixed(1)}% fee` : ''}{logi > 0 ? ` · −HK$${logi.toFixed(2)} logi` : ''} · {(gross * 100).toFixed(1)}% gross</div>}
          </div>
        })}
      </div>
      <div className="pop-f">Net = sell − channel fee − logistics − cost (fees are standard estimates). Floor {(item.gp_floor * 100).toFixed(0)}% is gross GP.</div>
    </>
  )
  const wocPopBody = (item: Product) => {
    const upp = item.units_per_pack ?? 1
    const rows = item.weekly_demand > 0 ? [4, 8].map(w => {
      const packs = Math.ceil(Math.max(0, (w - (item.woc ?? 0)) * item.weekly_demand) / upp)
      return packs > 0 ? <div className="buy-row" key={w}><div className="buy-l">Reach <b>{w} weeks</b></div><div className="buy-r"><div className="buy-w">{packs} {item.pack_unit ?? 'pack'}{packs > 1 ? 's' : ''}</div><div className="buy-c">{(packs * upp).toLocaleString()} {item.uom ?? 'units'}</div></div></div> : null
    }).filter(Boolean) : []
    return <>
      <div className="pop-h">Weeks of cover</div>
      <div className="pop-b">
        <div style={{ fontSize: '13px', marginBottom: rows.length ? '9px' : 0 }}>Currently {item.woc != null ? <b style={{ color: item.woc < 2 ? '#C0362C' : item.woc < 4 ? '#B45309' : '#15803D' }}>{item.woc.toFixed(1)} weeks</b> : <span style={{ color: '#8A93A2' }}>no demand signal</span>} · ~{Math.round(item.weekly_demand)}/wk</div>
        {rows.length ? rows : <div style={{ fontSize: '11.5px', color: '#8A93A2' }}>Add pack size + demand to simulate top-ups.</div>}
      </div>
      <div className="pop-f">Clinic (DaySmart) vs warehouse (ShopToPlus) cover; target 4 weeks.</div>
    </>
  }
  const mbbPopBody = (item: Product) => {
    const sup = item.all_suppliers.find(s => s.is_primary) ?? item.all_suppliers[0]
    const terms = sup?.mbb_term_list ?? []
    return <>
      <div className="pop-h">Bulk-buy (MBB) tiers</div>
      <div className="pop-b">
        {terms.length === 0 && <div style={{ fontSize: '12px', color: '#8A93A2' }}>No MBB terms on record.</div>}
        {terms.map(t => <div className="mbb-row" key={t.id}>
          <span className="mbb-k">{t.kind === 'buy_x_get_y' ? `Buy ${t.min_qty ?? '?'} get ${t.free_qty ?? '?'} free` : t.kind === 'spend_discount' ? `Spend $${t.min_spend ?? '?'} → ${t.discount_pct != null ? (t.discount_pct * 100).toFixed(0) : '?'}%` : `${t.min_qty ? `${t.min_qty}+ units` : 'Flat'}`}</span>
          <span className="mbb-v">{t.effective_unit_cost != null ? `${imsMoney(t.effective_unit_cost)}/${item.uom ?? 'unit'}` : '—'}</span>
        </div>)}
      </div>
      {item.mbb_unit_cost != null && <div className="pop-f">Best achievable: <b style={{ color: '#3730A3' }}>{imsMoney(item.mbb_unit_cost)}</b> / {item.uom ?? 'unit'}</div>}
    </>
  }
  const popBody = (type: string, item: Product) => type === 'ch' ? chPopBody(item) : type === 'woc' ? wocPopBody(item) : type === 'mbb' ? mbbPopBody(item) : supPopBody(item)
  const popFor = (id: string): string | null => (id === 'gp' || id === 'sell') ? 'ch' : (id === 'sup' || id === 'supply') ? 'sup' : id === 'woc' ? 'woc' : id === 'mbb' ? 'mbb' : null

  const visibleCols = useMemo(() => marginMode ? MARGIN_COLS : COLS.filter(c => c.fixed || !hiddenCols.has(c.id)), [hiddenCols, marginMode])

  // Load the margin summary once, the first time the Margins view is opened.
  useEffect(() => {
    if (!marginMode || Object.keys(margins).length > 0 || marginsLoading) return
    setMarginsLoading(true)
    fetch(`${API}/products/margins.json`, { cache: 'no-store', headers: authHeaders() })
      .then(r => r.ok ? r.json() : {})
      .then((data: Record<string, MarginRow>) => setMargins(data))
      .catch(() => {})
      .finally(() => setMarginsLoading(false))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [marginMode])
  const anyFilter = !!(search.trim() || selectedCats.length || supplier !== 'All' || channelFilter !== 'all' || quickFilter || locationFilter !== 'All' || stockFilter !== 'All' || qualityFilter !== 'All' || collectionSkus)
  const clearAll = () => { setSearch(''); setSearchInput(''); setSelectedCats([]); setSupplier('All'); setChannelFilter('all'); setQuickFilter(null); setLocationFilter('All'); setStockFilter('All'); setQualityFilter('All'); selectCollection(null) }
  const detailHref = (item: Product) => `/items/${skuToPath(item.sku_code)}`
  const validSkuCode = (s: string) => /^\d{6,}$/.test(s.trim())

  const canBulk = mounted && can('product_sensitive')
  const shownSkus = useMemo(() => shownRows.map(r => r.sku_code), [shownRows])
  const [bulkApplying, setBulkApplying] = useState(false)
  async function applyBulkStatus(status: string) {
    const skus = selectionStore.list()
    if (!skus.length) return
    const label = status === 'ACTIVE' ? 'Active' : status === 'INACTIVE' ? 'Inactive' : 'Discontinued'
    const ok = await confirmDialog({
      title: `Set ${skus.length} SKU${skus.length > 1 ? 's' : ''} to ${label}?`,
      message: status === 'DISCONTINUED'
        ? `Discontinues ${skus.length} SKU${skus.length > 1 ? 's' : ''} and removes ${skus.length > 1 ? 'them' : 'it'} from the active catalogue. Reversible per SKU.`
        : `Sets ${skus.length} selected SKU${skus.length > 1 ? 's' : ''} to ${label}.`,
      confirmLabel: `Set ${label}`,
      danger: status === 'DISCONTINUED',
    })
    if (!ok) return
    setBulkApplying(true)
    let done = 0, failed = 0
    const queue = [...skus]
    const worker = async () => {
      while (queue.length) {
        const s = queue.shift()!
        try {
          const r = await fetch(`${API}/products/${skuToPath(s)}`, {
            method: 'PATCH', headers: { 'Content-Type': 'application/json', ...authHeaders() },
            body: JSON.stringify({ status }),
          })
          if (r.ok) done++; else failed++
        } catch { failed++ }
      }
    }
    await Promise.all(Array.from({ length: Math.min(6, skus.length) }, worker))
    setBulkApplying(false)
    selectionStore.clear()
    if (failed === 0) toast.success(`Set ${done} SKU${done !== 1 ? 's' : ''} to ${label}`)
    else toast.error(`${done} updated · ${failed} failed`)
    fetchData(true)
  }
  const cell = (c: ColDef, item: Product): React.ReactNode => {
    switch (c.id) {
      case 'pin':    return canBulk
        ? <SelCheckbox sku={item.sku_code} />
        : <span className={`pin ${pinnedSkus.has(item.sku_code) ? 'on' : ''}`} title={pinnedSkus.has(item.sku_code) ? 'Remove from review list' : 'Add to review list'} onClick={() => togglePin(item.sku_code)}>{pinnedSkus.has(item.sku_code) ? '★' : '☆'}</span>
      case 'dq':     return <span className={`dqg ${item.data_grade}`} title="Data quality — A: complete · C: missing data">{item.data_grade}</span>
      case 'sku':    return validSkuCode(item.sku_code) ? <span className="sku">{item.sku_code}</span> : <span className="miss" title={`Bad SKU: ${item.sku_code}`}>No SKU</span>
      case 'name':   return <>
        <Link to={detailHref(item) as never} className="pname">{item.name}<PlatformBadges item={item} /></Link>
        <div className="pmeta">{item.brand && <span>{item.brand}</span>}{item.uom && <><span className="dot">·</span><span className="uom">per {item.uom}</span></>}{item.units_per_pack && item.units_per_pack > 1 ? <><span className="dot">·</span><span>{item.units_per_pack}/{item.pack_unit ?? 'pack'}</span></> : null}</div>
      </>
      case 'sup': {
        const named = item.all_suppliers.filter(s => s.name)
        if (!named.length) return <span className="miss">No supplier</span>
        const pref = item.all_suppliers.find(s => s.is_preferred && s.name) ?? named[0]
        const prefOos = pref.stock_status === 'out_of_stock'
        const hasAlt = item.all_suppliers.some(s => s.stock_status !== 'out_of_stock')
        return <span className="supplier"><span className="snm" title={named.map(s => s.name).join(', ')}>{pref.name}</span>{named.length > 1 && <span className="plus">+{named.length - 1}</span>}{prefOos && <span className={`oosdot ${hasAlt ? 'part' : 'crit'}`} title={`Preferred supplier out of stock${hasAlt ? ' — alternate available' : ''}`} />}</span>
      }
      case 'cat':    return <span className="catcell"><span className="catdot" style={{ background: catColor(item.category) }} /><span className="cattx">{item.category}</span></span>
      case 'hero':   return <span className={`starbtn ${item.hero_sku ? 'on' : ''}`} title={item.hero_sku ? 'Hero SKU' : 'Not a hero SKU'}>{item.hero_sku ? '★' : '☆'}</span>
      case 'cost':   return item.primary_cost == null ? <span className="miss">No cost</span> : <><div className="num cost"><b>{imsMoney(item.primary_cost)}</b></div><div className="num sub">per {item.uom ?? 'unit'}</div></>
      case 'gp': {    const gp = getClinicGp(item); return gp == null ? <span className="num zero">—</span> : <span className={`gp ${imsGpCls(gp, item.gp_floor)}`}>{imsGpp(gp)}</span> }
      case 'sell': {  const p = clinicSell(item); return p == null ? <span className="num zero">—</span> : <span className="num">{imsMoney(p)}</span> }
      case 'clinic': return <span className={`num ${item.clinic_qty === 0 ? 'zero' : ''}`}>{item.clinic_qty.toLocaleString()}</span>
      case 'whse':   return <span className={`num ${item.warehouse_qty === 0 ? 'zero' : ''}`}>{item.warehouse_qty.toLocaleString()}</span>
      case 'woc': {   if (item.woc == null) return <span className="num zero">—</span>; const col = item.woc < 2 ? '#C0362C' : item.woc < 4 ? '#B45309' : '#15803D'; return <span className="woc" style={{ color: col }}><span className="wdot" style={{ background: col }} />{item.woc.toFixed(1)}w</span> }
      case 'woctgt': return <span className="num" style={{ color: '#8A93A2' }} title="Category default healthy-cover target">4w</span>
      case 'mbb':    return item.mbb_unit_cost != null ? <span className="mbb">{imsMoney(item.mbb_unit_cost)}</span> : <span className="mbb none">—</span>
      case 'exp':    return <span className="num zero" title="Batch & expiry sync in Phase 3 (algo-dashboard commerce_inventory)">—</span>
      case 'store':  return <span className={`store ${item.storage_rule === 'clinic_only' ? 'clinic' : 'any'}`}>{item.storage_rule === 'clinic_only' ? 'Clinic only' : 'Any'}</span>
      case 'sales':  return item.sales_120d > 0 ? <span className="num">{item.sales_120d.toLocaleString()}</span> : <span className="num zero">—</span>
      case 'supply': { const oc = oosCount(item); const n = item.all_suppliers.length; return oc === 0 ? <span className="supply ok" title="All suppliers in stock"><span className="sd" /></span> : <span className={`supply ${oc >= n ? 'crit' : 'part'}`} title={`${oc} of ${n} suppliers out of stock`}><span className="sd" />{oc}/{n}</span> }
      case 'go':     return <Link to={detailHref(item) as never} className="open" title="Open SKU detail">›</Link>
      case 'm_basic': { const m = margins[item.sku_code]; return !m ? <span className="num zero">{marginsLoading ? '…' : '—'}</span> : <div className="num cost"><b>{imsMoney(m.basic_cost)}</b></div> }
      case 'm_mbb':   { const m = margins[item.sku_code]; return !m ? <span className="num zero">{marginsLoading ? '…' : '—'}</span> : m.mbb_cost != null ? <span className="num">{imsMoney(m.mbb_cost)}</span> : <span className="num zero">—</span> }
      case 'm_hit':   { const m = margins[item.sku_code]; return !m ? <span className="num zero">{marginsLoading ? '…' : '—'}</span> : m.cost_to_hit != null ? <span className="num">{imsMoney(m.cost_to_hit)}</span> : <span className="num zero">—</span> }
      case 'm_clinic': case 'm_shopify': case 'm_hktv': {
        const m = margins[item.sku_code]
        if (!m) return <span className="num zero">{marginsLoading ? '…' : '—'}</span>
        const cd = m.ch[c.id.slice(2)]
        if (!cd || cd.price == null) return <span className="num zero">—</span>
        const cls = (v: number | null) => v == null ? '' : v < 0 ? 'bad' : v >= m.gp_floor ? 'good' : 'warn'
        const ps  = (v: number | null) => v == null ? '—' : `${(v * 100).toFixed(1)}%`
        return <><span className={`gp ${cls(cd.nb)}`}>{ps(cd.nb)}</span><div className="num sub" title="net-after-fees at MBB cost">MBB {ps(cd.nm)}</div></>
      }
      default:       return null
    }
  }
  const renderRow = (item: Product) => {
    const low = item.woc !== null && item.woc < 2
    return (
      <tr key={item.sku_code} className={`${low ? 'low' : ''} ${pinnedSkus.has(item.sku_code) ? 'pinned' : ''}`}>
        {visibleCols.map(c => {
          const pt = popFor(c.id)
          return <td key={c.id} className={`col-${c.id} ${c.align === 'r' ? 'r' : c.align === 'c' ? 'c' : ''} ${c.grp ? 'grp' : ''}`}
            onMouseEnter={pt ? e => showPop(pt, item, e) : undefined}
            onMouseLeave={pt ? hidePop : undefined}
            style={pt ? { cursor: 'default' } : undefined}>{cell(c, item)}</td>
        })}
      </tr>
    )
  }
  const pinnedRows = useMemo(() => pinnedOnly ? [] : sorted.filter(i => pinnedSkus.has(i.sku_code)), [sorted, pinnedSkus, pinnedOnly])
  const bodyRows = useMemo(() => pinnedOnly ? shownRows : shownRows.filter(i => !pinnedSkus.has(i.sku_code)), [shownRows, pinnedSkus, pinnedOnly])
  const colspan = visibleCols.length
  // Memoise rendered rows so hovering a cell (popover state) doesn't re-render ~1000 rows;
  // only data / columns / pins rebuild the body. renderRow closes over stable setters/refs.
  const tableRows = useMemo(() => (
    <>
      {pinnedRows.length > 0 && <tr className="pinhead"><td colSpan={colspan}>Selected for review ({pinnedRows.length}) · <a onClick={() => setPinnedOnly(true)}>show only these</a> · <a onClick={() => setPinnedSkus(new Set())}>clear all</a></td></tr>}
      {pinnedRows.map(renderRow)}
      {pinnedRows.length > 0 && bodyRows.length > 0 && <tr className="pinsep"><td colSpan={colspan} /></tr>}
      {bodyRows.map(renderRow)}
    </>
  // eslint-disable-next-line react-hooks/exhaustive-deps
  ), [pinnedRows, bodyRows, colspan, visibleCols, pinnedSkus, canBulk, marginMode, margins, marginsLoading])

  return (
    <>
      <style>{IMS_CSS}</style>

      {/* Non-blocking top progress bar — inventory streams in the background while everything
          below stays fully usable. Determinate (fills to loaded/total) with an active sweep. */}
      {loadProgress && (
        <div aria-hidden
          style={{ position: 'fixed', top: 0, left: 0, right: 0, height: '3px', zIndex: 9999, background: 'rgba(99,102,241,0.12)', overflow: 'hidden', pointerEvents: 'none' }}>
          <div style={{ position: 'absolute', top: 0, bottom: 0, left: 0, width: `${Math.min(100, (loadProgress.loaded / loadProgress.total) * 100)}%`, background: 'linear-gradient(90deg, #6366F1, #818CF8)', borderRadius: '0 3px 3px 0', transition: 'width 0.45s cubic-bezier(0.22, 1, 0.36, 1)', boxShadow: '0 0 8px rgba(99,102,241,0.7)' }} />
          <div style={{ position: 'absolute', top: 0, bottom: 0, width: '40%', background: 'linear-gradient(90deg, transparent, rgba(255,255,255,0.6), transparent)', animation: 'ims-bar 1.4s linear infinite' }} />
        </div>
      )}

      <div className="imslist" style={{ maxWidth: '1560px' }}>

        {/* Header */}
        <div className="head">
          <div>
            <h1>All Inventory</h1>
            <div className="sub">Single source of truth for every SKU across the clinic and warehouse.</div>
          </div>
          <div className="actions">
            {liveAt && <span className="live" title={`Auto-refreshing — last check ${liveAt.toLocaleTimeString()}`}><span className="d" />Live</span>}
            {syncStatus?.synced_at
              ? <>
                  {(syncStatus.missing_cost ?? 0) > 0 && <span className="warnbadge">⚠ {syncStatus.missing_cost} missing costs</span>}
                  {(syncStatus.cost_discrepancies ?? 0) > 0 && <span className="errbadge">⚠ {syncStatus.cost_discrepancies} cost conflicts</span>}
                </>
              : <span className="warnbadge">⚠ Not synced</span>}
            <div style={{ display: 'inline-flex', border: '1px solid #E7EAEF', borderRadius: '8px', overflow: 'hidden', background: '#fff' }} role="tablist" aria-label="Table view">
              {([['Inventory', false], ['Margins', true]] as const).map(([lbl, m]) => (
                <button key={lbl} role="tab" aria-selected={marginMode === m} onClick={() => setMarginMode(m)}
                  title={m ? 'Show margin columns — net-after-fees per channel, MBB cost, cost-to-hit' : 'Show the standard inventory columns'}
                  style={{ border: 'none', padding: '7px 14px', fontSize: '12.5px', fontWeight: 600, cursor: 'pointer',
                    background: marginMode === m ? '#4F46E5' : 'transparent', color: marginMode === m ? '#fff' : '#5B6472' }}>{lbl}</button>
              ))}
            </div>
            {marginMode && <a className="btn" href={`${API}/products/export-margins.csv`} download title="Download every margin field as CSV (matches the verification sheet)">⤓ Margins CSV</a>}
            <div className="exp-wrap">
              <button className="btn" onClick={() => setShowExportCols(v => !v)} aria-expanded={showExportCols}>↓ Export {sorted.length.toLocaleString()}</button>
              {showExportCols && (
                <>
                  <div className="exp-backdrop" onClick={() => setShowExportCols(false)} />
                  <div className="exp-pop" role="dialog" aria-label="Choose export columns">
                    <div className="exp-head">
                      <b>Columns to export</b>
                      {!bulkExport && <button className="exp-reset" onClick={() => setExportCols(new Set(DEFAULT_EXPORT_KEYS))}>Reset to default</button>}
                    </div>
                    <label className="exp-bulk" title="Exact DB fields only — round-trips through Batch update with no computed values">
                      <input type="checkbox" checked={bulkExport} onChange={() => setBulkExport(v => !v)} />
                      <span>For bulk update — raw DB fields only</span>
                    </label>
                    <div className="exp-body">
                      {bulkExport ? (
                        <div className="exp-note">
                          Exports the {BULK_UPDATE_COLUMNS.length} editable database fields exactly as stored — raw <code>basic_cost</code> (whole-pack) and <code>units_per_pack</code>, no computed values. Keyed by <code>sku_code</code>; re-imports via <b>Batch update</b>.
                        </div>
                      ) : (
                        <>
                          <div className="exp-grp">Default columns</div>
                          <div className="exp-grid">
                            {EXPORT_COLUMNS.filter(c => c.group === 'default').map(c => (
                              <label key={c.key} className="exp-opt" title={c.label}>
                                <input type="checkbox" checked={exportCols.has(c.key)} onChange={() => toggleExportCol(c.key)} />
                                <span>{c.label}</span>
                              </label>
                            ))}
                          </div>
                          <div className="exp-grp">Additional columns</div>
                          <div className="exp-grid">
                            {EXPORT_COLUMNS.filter(c => c.group === 'extra').map(c => (
                              <label key={c.key} className="exp-opt" title={c.label}>
                                <input type="checkbox" checked={exportCols.has(c.key)} onChange={() => toggleExportCol(c.key)} />
                                <span>{c.label}</span>
                              </label>
                            ))}
                          </div>
                        </>
                      )}
                    </div>
                    <div className="exp-foot">
                      <span>{bulkExport ? `${BULK_UPDATE_COLUMNS.length} DB fields` : `${exportCols.size} column${exportCols.size === 1 ? '' : 's'}`} · {sorted.length.toLocaleString()} rows</span>
                      <button className="btn primary" onClick={handleExport} disabled={!bulkExport && exportCols.size === 0}>Download CSV</button>
                    </div>
                  </div>
                </>
              )}
            </div>
            {mounted && can('product_edit') && <button className="btn" onClick={() => setShowBatch(true)}>Batch update</button>}
            {mounted && can('product_edit') && <button className="btn" onClick={handleFetchCompetitors} disabled={fetchingComp} title="Scrape all linked competitor prices">{fetchingComp ? 'Fetching…' : '🏷 Fetch competitor prices'}</button>}
            {mounted && can('sheet') && <button className="btn" onClick={handlePush} disabled={pushing}>{pushing ? 'Pushing…' : '⤴ Push to Sheet'}</button>}
            {mounted && can('sheet') && <button className="btn" onClick={handleAlgoSync} disabled={algoSyncing} title="Pull real sales + inventory expiry from the algo-dashboard">{algoSyncing ? 'Syncing…' : '⟳ Sync live sales data'}</button>}
            {mounted && can('sheet') && <button className="btn pri" onClick={handleSync} disabled={syncing}>{syncing ? 'Syncing…' : '↻ Sync'}</button>}
            {showBatch && <BatchUpdateModal onClose={() => setShowBatch(false)} onApplied={fetchData} />}
          </div>
        </div>

        {/* Toolbar */}
        <div className="toolbar">
          <div className="search">
            <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="7" cy="7" r="4.3" /><path d="M10.5 10.5l3 3" strokeLinecap="round" /></svg>
            <input placeholder="Search product, SKU, supplier SKU or brand" value={searchInput} onChange={e => setSearchInput(e.target.value)} />
          </div>
          <DDField id="channel" active={channelFilter !== 'all'} current={channelFilter} onPick={v => setChannelFilter(v as typeof channelFilter)} openMenu={openMenu} setOpenMenu={setOpenMenu}
            options={[{ value: 'all', label: 'All channels' }, { value: 'clinic', label: 'Clinic · DaySmart' }, { value: 'shopify', label: 'Shopify' }, { value: 'hktv', label: 'HKTV' }]} />
          <div className="dd">
            <button className={`dd-btn ${selectedCats.length ? 'on' : ''}`} onClick={() => setOpenMenu(openMenu === 'cat' ? null : 'cat')}>Category{selectedCats.length > 0 && <span className="cnt">{selectedCats.length}</span>}<span className="car">▼</span></button>
            {openMenu === 'cat' && <div className="dd-menu">{categories.filter(c => c !== 'All').map(c => <label key={c} className="dd-item"><input type="checkbox" checked={selectedCats.includes(c)} onChange={() => setSelectedCats(prev => prev.includes(c) ? prev.filter(x => x !== c) : [...prev, c])} />{c}</label>)}</div>}
          </div>
          <DDField id="supplier" active={supplier !== 'All'} current={supplier} onPick={setSupplier} openMenu={openMenu} setOpenMenu={setOpenMenu}
            options={[{ value: 'All', label: 'All suppliers' }, ...suppliers.map(s => ({ value: s.name, label: s.name }))]} />
          {collections.length > 0 && <DDField id="collection" active={collectionId != null} current={collectionId != null ? String(collectionId) : ''} onPick={v => selectCollection(v ? Number(v) : null)} openMenu={openMenu} setOpenMenu={setOpenMenu}
            options={[{ value: '', label: 'All collections' }, ...collections.map(c => ({ value: String(c.id), label: `${c.name} (${c.count})` }))]} />}
          <button className={`dd-btn ${showMore ? 'on' : ''}`} onClick={() => setShowMore(v => !v)}>More filters<span className="car">▼</span></button>
          <div className="dd">
            <button className={`dd-btn ${hiddenCols.size ? 'on' : ''}`} onClick={() => setOpenMenu(openMenu === 'col' ? null : 'col')}>Columns<span className="car">▼</span></button>
            {openMenu === 'col' && <div className="dd-menu">{COLS.filter(c => !c.fixed && c.label).map(c => <label key={c.id} className="dd-item"><input type="checkbox" checked={!hiddenCols.has(c.id)} onChange={() => toggleCol(c.id)} />{c.label}</label>)}</div>}
          </div>
          <div className="right">
            {channelFilter !== 'all' && <span className="chan-tag">{CMAP[channelFilter]} · WOC uses {channelFilter === 'clinic' ? 'clinic' : 'warehouse'} stock</span>}
            <span className="count"><b>{sorted.length.toLocaleString()}</b> of {items.length.toLocaleString()} SKUs</span>
            {pinnedSkus.size > 0 && (
              <button onClick={() => setPinnedOnly(v => !v)} title={pinnedOnly ? 'Show all SKUs' : 'Show only the SKUs you selected'}
                style={{ border: '1px solid', borderColor: pinnedOnly ? '#4F46E5' : '#E7EAEF', borderRadius: '99px', padding: '4px 11px', fontSize: '11.5px', fontWeight: 700, cursor: 'pointer', whiteSpace: 'nowrap', background: pinnedOnly ? '#4F46E5' : '#fff', color: pinnedOnly ? '#fff' : '#5B6472' }}>
                ★ Review ({pinnedSkus.size})
              </button>
            )}
            {pinnedOnly && search.trim() && <span style={{ fontSize: '11px', color: '#B45309', whiteSpace: 'nowrap' }}>Searching all — pin ☆ to add</span>}
            {anyFilter && <button className="clear" onClick={clearAll}>Clear filters</button>}
          </div>
        </div>

        {/* More filters */}
        {showMore && (
          <div className="more">
            <div className="fg"><span className="fl">Location</span>
              <DDField id="loc" active={locationFilter !== 'All'} current={locationFilter} onPick={v => setLocationFilter(v as typeof locationFilter)} openMenu={openMenu} setOpenMenu={setOpenMenu}
                options={[{ value: 'All', label: 'Any' }, { value: 'clinic_only', label: 'Clinic only' }, { value: 'any', label: 'Warehouse OK' }]} />
            </div>
            <div className="fg"><span className="fl">Stock</span>
              <DDField id="stock" active={stockFilter !== 'All'} current={stockFilter} onPick={v => setStockFilter(v as typeof stockFilter)} openMenu={openMenu} setOpenMenu={setOpenMenu}
                options={[{ value: 'All', label: 'Any' }, { value: 'in_stock', label: 'In stock' }, { value: 'out_of_stock', label: 'Out of stock' }]} />
            </div>
            <div className="fg"><span className="fl">Data quality</span>
              <DDField id="quality" active={qualityFilter !== 'All'} current={qualityFilter} onPick={v => setQualityFilter(v as typeof qualityFilter)} openMenu={openMenu} setOpenMenu={setOpenMenu}
                options={[
                  { value: 'All', label: 'Any' },
                  { value: '_g1', label: 'Grade', group: true },
                  { value: 'grade_a', label: 'Grade A — complete' }, { value: 'grade_c', label: 'Grade C — missing data' },
                  { value: '_g2', label: 'Verification', group: true },
                  { value: 'unverified', label: 'Unverified' }, { value: 'verified', label: 'HITL-verified' },
                  { value: '_g3', label: 'Data gaps', group: true },
                  { value: 'no_sku', label: 'No SKU' }, { value: 'no_supplier', label: 'No supplier' }, { value: 'no_cost', label: 'No cost' }, { value: 'no_pack_size', label: 'No pack size' },
                  { value: '_g4', label: 'Priority', group: true },
                  { value: 'priority_fix', label: 'Fix first — sales + Grade C' },
                ]} />
            </div>
          </div>
        )}

        {/* Tiles */}
        {!loading && (
          <div className="tiles">
            <div className={`tile ${quickFilter === 'active' ? 'on' : ''}`} onClick={() => toggleQuickFilter('active')}><div className="lab">Active</div><div className="val">{clientCounts.active.toLocaleString()}</div></div>
            <div className={`tile ${quickFilter === 'inactive' ? 'on' : ''}`} onClick={() => toggleQuickFilter('inactive')}><div className="lab">Inactive</div><div className="val" style={{ color: clientCounts.inactive > 0 ? '#B45309' : undefined }}>{clientCounts.inactive.toLocaleString()}</div></div>
            <div className={`tile ${quickFilter === 'low_stock' ? 'on' : ''}`} onClick={() => toggleQuickFilter('low_stock')}><div className="lab">Low stock · WOC&lt;2</div><div className="val" style={{ color: clientCounts.low_stock > 0 ? '#C0362C' : undefined }}>{clientCounts.low_stock.toLocaleString()}</div></div>
            <div className={`tile ${quickFilter === 'below_margin' ? 'on' : ''}`} onClick={() => toggleQuickFilter('below_margin')}><div className="lab">Below margin</div><div className="val" style={{ color: clientCounts.below_margin > 0 ? '#B45309' : undefined }}>{clientCounts.below_margin.toLocaleString()}</div><div className="vsub">below GP floor</div></div>
            <div className={`tile ${quickFilter === 'out_of_stock' ? 'on' : ''}`} onClick={() => toggleQuickFilter('out_of_stock')}><div className="lab">Out of stock</div><div className="val" style={{ color: clientCounts.out_of_stock > 0 ? '#C0362C' : undefined }}>{clientCounts.out_of_stock.toLocaleString()}</div><div className="vsub">zero on hand</div></div>
            <div className={`tile ${quickFilter === 'supplier_oos' ? 'on' : ''}`} onClick={() => toggleQuickFilter('supplier_oos')}><div className="lab">Supplier OOS</div><div className="val" style={{ color: clientCounts.supplier_oos > 0 ? '#B45309' : undefined }}>{clientCounts.supplier_oos.toLocaleString()}</div><div className="vsub">a supplier is out</div></div>
            <div className="tile" style={{ cursor: 'default' }}><div className="lab">Expiring &lt;90d</div><div className="val" style={{ color: (summary?.expiring_soon ?? 0) > 0 ? '#B45309' : undefined }}>{summary?.expiring_soon ?? '—'}</div></div>
          </div>
        )}

        {/* Error state */}
        {error && (
          <div style={{ background: '#FBEBEA', border: '1px solid #F1CDC9', borderRadius: '10px', padding: '16px', marginBottom: '16px', color: '#7A2A24', fontSize: '13px' }}>
            <strong>Cannot reach API</strong> — {error}
          </div>
        )}

        {/* Bulk status actions — appears when rows are selected (admin/bizops only) */}
        {canBulk && <BulkStatusBar applying={bulkApplying} onApply={applyBulkStatus} />}

        {/* Table */}
        {!error && (
          <div className="tbl-wrap">
            <table>
              <thead><tr>
                {visibleCols.map(c => {
                  if (c.id === 'pin') return <th key="pin" className="col-pin">{canBulk ? <SelectAllBox skus={shownSkus} /> : ''}</th>
                  const active = !!c.sort && sortCol === c.sort
                  return <th key={c.id} className={`col-${c.id} ${c.align === 'r' ? 'r' : c.align === 'c' ? 'c' : ''} ${c.grp ? 'grp' : ''} ${c.sort ? 'sortable' : ''} ${active ? 'sorted' : ''}`} onClick={() => c.sort && toggleSort(c.sort)}>{c.label}{c.sort && <span className="arw">{active ? (sortAsc ? '↑' : '↓') : '↕'}</span>}</th>
                })}
              </tr></thead>
              <tbody>
                {loading && <tr><td colSpan={colspan} className="empty">Loading inventory…</td></tr>}
                {!loading && sorted.length === 0 && <tr><td colSpan={colspan} className="empty">No SKUs match your filters</td></tr>}
                {tableRows}
              </tbody>
            </table>
            {sorted.length > shownRows.length && (
              <div className="showmore">
                <button onClick={() => setRowLimit(narrowed ? sorted.length : (rowLimit + 200))}>
                  {narrowed
                    ? `Show all ${sorted.length.toLocaleString()} results (${(sorted.length - shownRows.length).toLocaleString()} more)`
                    : `Show 200 more (${(sorted.length - shownRows.length).toLocaleString()} remaining)`}
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {pop && (
        <div className="ims-pop" style={{ left: pop.x, top: pop.y }} onMouseEnter={keepPop} onMouseLeave={hidePop}>
          {popBody(pop.type, pop.item)}
        </div>
      )}
    </>
  )
}

// ── CSV batch update ─────────────────────────────────────────────────────────────
type ImportRow = { sku_code: string; status: 'updated' | 'unchanged' | 'not_found' | 'error'; changes?: Record<string, { from: unknown; to: unknown }>; error?: string; ignored?: string[] }
type ImportResult = { dry_run: boolean; summary: { total: number; updated: number; unchanged: number; not_found: number; errors: number; verified: number }; rows: ImportRow[] }

const fmtVal = (v: unknown): string => (v === null || v === undefined || v === '') ? '∅' : String(v)

function BatchUpdateModal({ onClose, onApplied }: { onClose: () => void; onApplied: () => void }) {
  const [file, setFile]       = useState<File | null>(null)
  const [busy, setBusy]       = useState(false)
  const [preview, setPreview] = useState<ImportResult | null>(null)
  const [done, setDone]       = useState<ImportResult | null>(null)
  const [markVerified, setMarkVerified] = useState(true)

  async function send(dry: boolean) {
    if (!file) return
    setBusy(true)
    try {
      const fd = new FormData(); fd.append('file', file)
      const r = await fetch(`${API}/products/import-csv?dry_run=${dry}&mark_verified=${markVerified}`, { method: 'POST', headers: authHeaders(), body: fd })
      const d = await r.json().catch(() => ({}))
      if (!r.ok) { toast.error(d.detail ?? 'Import failed'); return }
      if (dry) { setPreview(d); setDone(null) }
      else { setDone(d); toast.success(`Updated ${d.summary.updated} SKU(s)`); onApplied() }
    } catch { toast.error('Import failed') } finally { setBusy(false) }
  }

  const res = done ?? preview
  const COL: Record<string, string> = { updated: '#16A34A', unchanged: '#94A3B8', not_found: '#B45309', error: '#DC2626', errors: '#DC2626' }
  const upd = preview?.summary.updated ?? 0
  const ver = preview?.summary.verified ?? 0
  const applyDisabled = !preview || busy || !!done || (upd === 0 && ver === 0)
  const applyLabel = (busy && preview && !done) ? 'Applying…'
    : upd > 0 ? `Apply ${upd} update${upd === 1 ? '' : 's'}${ver > 0 ? ` + verify ${ver}` : ''}`
    : ver > 0 ? `Verify ${ver} SKU${ver === 1 ? '' : 's'}`
    : 'Apply'
  const card: CSSProperties = { background: 'white', borderRadius: '14px', width: '780px', maxWidth: '100%', maxHeight: '82vh', display: 'flex', flexDirection: 'column', boxShadow: '0 20px 50px rgba(0,0,0,0.25)' }

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(15,23,42,0.45)', zIndex: 1000, display: 'flex', alignItems: 'flex-start', justifyContent: 'center', padding: '48px 20px' }}>
      <div onClick={e => e.stopPropagation()} style={card}>
        <div style={{ padding: '20px 22px 14px', borderBottom: '1px solid #F1F5F9' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 style={{ fontSize: '17px', fontWeight: 700, color: '#0F172A' }}>Batch update from CSV</h2>
            <button onClick={onClose} style={{ border: 'none', background: 'none', fontSize: '22px', color: '#94A3B8', cursor: 'pointer', lineHeight: 1 }}>×</button>
          </div>
          <p style={{ fontSize: '12px', color: '#64748B', marginTop: '6px', lineHeight: 1.55 }}>
            Upload a CSV keyed by <code style={{ background: '#F1F5F9', padding: '1px 5px', borderRadius: '4px' }}>sku_code</code>. <b>Every editable column from Export round-trips here</b> — change any field and re-upload. Empty cells are left unchanged; read-only columns (quantities, GP%, prices, supplier, WOC) are ignored.
          </p>
        </div>

        <div style={{ padding: '16px 22px', overflowY: 'auto' }}>
          <div style={{ display: 'flex', gap: '10px', alignItems: 'center', flexWrap: 'wrap' }}>
            <input type="file" accept=".csv,text/csv" onChange={e => { setFile(e.target.files?.[0] ?? null); setPreview(null); setDone(null) }} style={{ fontSize: '13px' }} />
            <button onClick={() => send(true)} disabled={!file || busy} style={{ padding: '7px 14px', fontSize: '12px', fontWeight: 600, color: '#6366F1', background: 'white', border: '1px solid #C7D2FE', borderRadius: '7px', cursor: (!file || busy) ? 'default' : 'pointer', opacity: (!file || busy) ? 0.6 : 1 }}>{busy && !done ? 'Checking…' : 'Preview changes'}</button>
          </div>
          <label style={{ display: 'flex', alignItems: 'center', gap: '8px', marginTop: '12px', cursor: 'pointer', fontSize: '13px', fontWeight: 600, color: '#166534' }}>
            <input type="checkbox" checked={markVerified} onChange={e => { setMarkVerified(e.target.checked); setPreview(null); setDone(null) }} />
            Mark all processed SKUs as HITL&#8209;Verified
          </label>

          {res && (
            <div style={{ marginTop: '16px' }}>
              <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '12px' }}>
                {(['updated', 'unchanged', 'not_found', 'errors'] as const).map(k => (
                  <span key={k} style={{ fontSize: '12px', fontWeight: 600, color: COL[k], background: '#F8FAFC', border: '1px solid #E2E8F0', borderRadius: '7px', padding: '5px 10px' }}>
                    {({ updated: 'Will update', unchanged: 'No change', not_found: 'Not found', errors: 'Errors' } as const)[k]}: {res.summary[k]}
                  </span>
                ))}
                <span style={{ fontSize: '12px', fontWeight: 600, color: '#64748B', padding: '5px 4px' }}>· {res.summary.total} rows</span>
                {res.summary.verified > 0 && <span style={{ fontSize: '12px', fontWeight: 600, color: '#166534', background: '#F0FDF4', border: '1px solid #BBF7D0', borderRadius: '7px', padding: '5px 10px' }}>{done ? 'HITL-verified' : 'Will verify'}: {res.summary.verified}</span>}
              </div>
              <div style={{ border: '1px solid #E2E8F0', borderRadius: '8px', overflow: 'hidden' }}>
                {res.rows.slice(0, 200).map((row, i) => (
                  <div key={i} style={{ display: 'flex', gap: '10px', padding: '7px 12px', borderTop: i ? '1px solid #F1F5F9' : 'none', fontSize: '12px', alignItems: 'baseline' }}>
                    <span style={{ fontWeight: 600, color: '#0F172A', width: '130px', flexShrink: 0 }}>{row.sku_code}</span>
                    <span style={{ color: COL[row.status], fontWeight: 600, width: '76px', flexShrink: 0 }}>{row.status}</span>
                    <span style={{ color: '#64748B', flex: 1, wordBreak: 'break-word' }}>
                      {row.error ? row.error : row.changes ? Object.entries(row.changes).map(([fld, c]) => `${fld}: ${fmtVal(c.from)} → ${fmtVal(c.to)}`).join('   ·   ') : ''}
                      {row.ignored && row.ignored.length > 0 && <span style={{ color: '#B45309' }}> (locked fields ignored: {row.ignored.join(', ')})</span>}
                    </span>
                  </div>
                ))}
                {res.rows.length > 200 && <div style={{ padding: '7px 12px', fontSize: '11px', color: '#94A3B8', borderTop: '1px solid #F1F5F9' }}>+ {res.rows.length - 200} more…</div>}
              </div>
            </div>
          )}
        </div>

        <div style={{ padding: '14px 22px', borderTop: '1px solid #F1F5F9', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <span style={{ fontSize: '12px', color: done ? '#16A34A' : '#94A3B8' }}>{done ? `✓ Applied — ${done.summary.updated} updated${done.summary.verified ? `, ${done.summary.verified} verified` : ''}` : preview ? `${preview.summary.updated} to update${preview.summary.verified ? ` · ${preview.summary.verified} to verify` : ''}` : ''}</span>
          <div style={{ display: 'flex', gap: '10px' }}>
            <button onClick={onClose} style={{ padding: '9px 16px', fontSize: '13px', fontWeight: 600, color: '#64748B', background: 'white', border: '1px solid #E2E8F0', borderRadius: '8px', cursor: 'pointer' }}>{done ? 'Close' : 'Cancel'}</button>
            <button onClick={() => send(false)} disabled={applyDisabled} style={{ padding: '9px 18px', fontSize: '13px', fontWeight: 600, color: 'white', background: '#6366F1', border: 'none', borderRadius: '8px', cursor: applyDisabled ? 'default' : 'pointer', opacity: applyDisabled ? 0.5 : 1 }}>{applyLabel}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// ── Bulk selection ─────────────────────────────────────────────────────────────
// Isolated external store: toggling a checkbox notifies only the subscribed checkbox
// (its own boolean snapshot) — the memoised ~1000-row table body never re-renders.
const selectionStore = (() => {
  let sel = new Set<string>()
  const listeners = new Set<() => void>()
  const emit = () => listeners.forEach(l => l())
  return {
    subscribe: (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } },
    has: (sku: string) => sel.has(sku),
    size: () => sel.size,
    list: () => [...sel],
    toggle: (sku: string) => { sel = new Set(sel); if (sel.has(sku)) sel.delete(sku); else sel.add(sku); emit() },
    setMany: (skus: string[], on: boolean) => { sel = new Set(sel); for (const s of skus) { if (on) sel.add(s); else sel.delete(s) } emit() },
    clear: () => { if (sel.size) { sel = new Set(); emit() } },
  }
})()
function useSelHas(sku: string) { return useSyncExternalStore(selectionStore.subscribe, () => selectionStore.has(sku), () => false) }
function useSelSize() { return useSyncExternalStore(selectionStore.subscribe, () => selectionStore.size(), () => 0) }

function SelCheckbox({ sku }: { sku: string }) {
  const checked = useSelHas(sku)
  return <input type="checkbox" checked={checked} onChange={() => selectionStore.toggle(sku)} onClick={e => e.stopPropagation()}
    title="Select" style={{ cursor: 'pointer', accentColor: '#6366F1', width: '15px', height: '15px' }} />
}
function SelectAllBox({ skus }: { skus: string[] }) {
  useSelSize()  // re-render when selection changes
  const all = skus.length > 0 && skus.every(s => selectionStore.has(s))
  const some = !all && skus.some(s => selectionStore.has(s))
  return <input type="checkbox" checked={all} ref={el => { if (el) el.indeterminate = some }}
    onChange={() => selectionStore.setMany(skus, !all)} onClick={e => e.stopPropagation()}
    title={all ? 'Clear all shown' : 'Select all shown'} style={{ cursor: 'pointer', accentColor: '#6366F1', width: '15px', height: '15px' }} />
}
function BulkStatusBar({ applying, onApply }: { applying: boolean; onApply: (status: string) => void }) {
  const n = useSelSize()
  if (n === 0) return null
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '9px', flexWrap: 'wrap', margin: '0 0 11px', padding: '9px 13px', background: '#EEF2FF', border: '1px solid #C7D2FE', borderRadius: '10px' }}>
      <span style={{ fontSize: '12.5px', fontWeight: 700, color: '#3730A3' }}>{n} selected</span>
      <span style={{ fontSize: '12px', color: '#6366F1' }}>· Set status</span>
      <button className="btn" disabled={applying} onClick={() => onApply('ACTIVE')}>Active</button>
      <button className="btn" disabled={applying} onClick={() => onApply('INACTIVE')}>Inactive</button>
      <button className="btn" disabled={applying} onClick={() => onApply('DISCONTINUED')}>Discontinue</button>
      {applying && <span style={{ fontSize: '12px', color: '#6366F1' }}>Applying…</span>}
      <button className="btn" disabled={applying} onClick={() => selectionStore.clear()} style={{ marginLeft: 'auto' }}>Clear</button>
    </div>
  )
}

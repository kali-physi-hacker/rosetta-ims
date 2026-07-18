export interface ProductChannel {
  channel: 'clinic' | 'shopify' | 'hktv'
  is_active: boolean
  selling_price: number | null
  has_dispensing_fee: boolean
  channel_fee_pct: number | null
  units_per_listing: number | null
  gp_pct: number | null
  recommendation: 'Price is OK ✓' | 'Raise price ⚠' | 'Check pack size ⚠' | null
  gap_pct: number | null
}

export interface ChannelMarginRange {
  channel: string
  selling_price: number | null
  gp_pct_mbb: number | null      // gross margin at MBB cost (backend margin_range; null until computed)
  basic_margin: number | null
  mbb_margin: number | null
  channel_fee_pct: number | null
  delivery_cost: number | null
}

export interface MbbTerm {
  id: number
  kind: 'buy_x_get_y' | 'spend_discount' | 'tier' | 'flat_unit_cost'
  min_qty: number | null
  min_spend: number | null
  free_qty: number | null
  discount_pct: number | null
  unit_cost: number | null
  note: string | null
  sort_order: number
  effective_unit_cost: number | null    // derived per-sell-unit cost of this term
}

export interface MbbTermMargin {
  id: number
  kind: string
  note: string | null
  min_qty: number | null
  min_spend: number | null
  weeks_cover: number | null
  unit_cost: number | null          // landed per-sell-unit cost at this term
  channels: { channel: string; gp_pct: number | null; margin: number | null }[]
}

export interface SupplierMarginBlock {
  supplier_id: number | null
  name: string | null
  code: string | null
  is_primary: boolean
  is_preferred: boolean
  basic_cost: number | null
  basic_channels: { channel: string; gp_pct: number | null; margin: number | null }[]
  term_margins: MbbTermMargin[]
}

export interface MarginRange {
  basic_cost: number | null        // landed per-unit cost (supplier unit + per-unit extras)
  extra_unit_cost: number | null
  mbb_cost: number | null
  mbb_kind: string | null
  mbb_min_qty: number | null
  mbb_min_spend: number | null
  mbb_weeks_cover: number | null
  mbb_terms: string | null
  mbb_term_margins: MbbTermMargin[]   // one per MBB term — each with its own per-channel margin
  suppliers: SupplierMarginBlock[]    // per-supplier: basic margin + a margin for each MBB term
  channels: ChannelMarginRange[]
}

export interface CompetitorPrice {
  id: number
  product_id: number
  competitor_name: string
  url: string | null
  platform: string | null       // shopify | opencart | woocommerce | hktvmall | generic
  price: number | null          // last scraped selling price (HKD)
  in_stock: number | null       // 1 | 0 | null (unknown)
  title: string | null
  last_checked: string | null   // YYYY-MM-DD
  last_status: string | null    // 'ok' | 'no price found' | 'error: ...'
  notes: string | null
}

export type MbbType = 'unit_cost' | 'buy_x_get_y' | 'spend_discount'

export interface Product {
  id: number
  sku_code: string
  name: string
  brand: string | null
  category: string
  subcategory: string | null   // AI-detected functional/clinical class
  segment: string | null       // 'vet' | 'non_vet' | null — veterinary vs retail SKU
  species: string | null
  rrp: number | null
  min_purchase_qty: number | null   // supplier MOQ (number of packs)
  min_sellable_qty: number | null   // smallest sellable quantity in uom units (usually 1)
  shopify_status: string | null     // active | archived | draft  (null = not listed)
  daysmart_status: string | null    // active                     (null = not listed)
  hktv_status: string | null        // online | offline           (null = not listed)
  shopify_cost: number | null       // platform-recorded COGS (Shopify cost-per-item)
  daysmart_avg_cost: number | null  // DaySmart avg unit cost (balances API) — distinct from daysmart_cost (last invoice)
  hktv_cost: number | null          // HKTV template Cost column
  uom: string | null          // sell UOM: tablet, ml, g
  pack_unit: string | null    // buy UOM: box, bottle, strip
  last_manual_edit_at: string | null
  last_manual_edit_by: string | null
  storage_rule: 'clinic_only' | 'any'
  status: 'ACTIVE' | 'INACTIVE' | 'DISCONTINUED'
  hero_sku: boolean
  notes: string | null
  weight_g: number | null
  weight_unit: string | null   // display unit for weight: 'kg' | 'lb' (grams is canonical)
  clinic_qty: number
  warehouse_qty: number
  total_qty: number
  weekly_demand: number
  weekly_demand_by_channel: { clinic: number | null; hktv: number | null; shopify: number | null } | null
  sales_trend: { month: string; units: number }[] | null
  woc: number | null
  primary_cost: number | null
  gp_floor: number
  channels: ProductChannel[]
  cross_channel_flag: boolean
  // Supplier / lineage
  supplier_name: string | null
  supplier_code: string | null
  supplier_sku: string | null
  all_suppliers: { id: number; supplier_id: number | null; name: string | null; code: string | null; supplier_sku: string | null; barcode: string | null; basic_cost: number | null; mbb_term_list: MbbTerm[]; units_per_pack: number | null; is_primary: boolean; is_preferred: boolean; stock_status: string; reported_out_at: string | null; expected_restock_at: string | null; stock_confirmed_by: string | null; stock_note: string | null; stock_events: { out_at: string; restock_at: string | null; note: string | null; days: number | null }[] }[]
  mbb_unit_cost: number | null        // best achievable per-unit MBB cost (from mbb_terms)
  landed_unit_cost: number | null     // = supplier per-sell-unit cost (channel charges applied per channel)
  cost_last_updated: string | null
  // UOM / pack size
  units_per_pack: number | null      // IMS-live value (locked once uom_verified_at is set)
  unit_cost: number | null           // basic_cost ÷ units_per_pack — used for all GP calculations
  uom_verified_at: string | null     // IMS-stamped date pack size was manually confirmed
  uom_verified_by: string | null     // name/initials of person who confirmed
  hitl_verified?: boolean            // currently HITL-verified (latest onboarding event is a verify)
  // Sync protection — shadow values + conflict flags
  basic_cost_sheet: number | null        // last cost value seen from Sheet sync
  units_per_pack_sheet: number | null    // last pack size seen from Sheet sync
  cost_sheet_conflict: boolean           // Sheet cost disagrees with IMS-locked cost
  pack_sheet_conflict: boolean           // Sheet pack size disagrees with IMS-verified value
  // Sales velocity
  sales_120d: number                 // units sold in last 120 days
  data_grade: 'A' | 'C'              // inventory completeness (reconciliation lives in procurement)
  // Cost confidence (Story 1.5)
  cost_source: 'manual' | 'catalogue' | 'po_issued' | 'invoice_matched'
  cost_source_ref: string | null     // e.g. "catalogue_import:42"
  cost_updated_at: string | null     // ISO datetime of last cost change
  cost_is_stale: boolean             // true if >90 days old or manual with no ref
  // Ordering terms (order multiple / MOQ) — primary supplier link; null until set
  order_increment_qty: number | null
  order_increment_uom: string | null
  minimum_order_qty: number | null
  minimum_order_uom: string | null
  minimum_order_source: string | null   // inferred_from_order_multiple | explicit_supplier_moq | manual | unknown
  pricing_note: string | null
  // Only on detail endpoint
  margin_range?: MarginRange
  tags?: string[]                    // free-form product tags (detail endpoint only)
  tags_shopify?: string[]            // subset of tags pulled from the live Shopify store
}

export interface SyncSource {
  file: string
  tab: string
  url: string
  gid: string | null
}

export interface SyncSources {
  sku_master: SyncSource
  hktv_inventory: SyncSource
}

export interface ProductsResponse {
  total: number
  page: number
  limit: number
  items: Product[]
}

export interface SummaryResponse {
  total_active: number
  inactive_count: number
  discontinued_count: number
  low_stock_count: number
  expiring_soon: number
  price_alerts: number
}

export interface PricingResponse {
  price_alert_count: number
  total: number
  items: Product[]
}

export interface CategoryRule {
  category: string
  gp_floor: number
  storage_rule: string
  channel_restriction: string | null
}

export interface Supplier {
  id: number
  code: string
  name: string
  contact_name: string | null
  contact_email: string | null
  lead_time_days: number | null
}

export interface SyncStatus {
  synced: boolean
  synced_at: string | null
  rows_fetched?: number
  updated?: number
  seeded?: number
  skipped?: number
  missing_cost?: number
  cost_discrepancies?: number
  sources?: SyncSources
}

export interface AccessAcknowledgement {
  id: number
  user_id: number
  user_display: string | null
  github_username: string
  full_name_typed: string
  email_requestor: string | null
  terms_version: string
  ip_address: string | null
  accepted_at: string
  email_sent_at: string | null
  email_send_error: string | null
}

export interface MyAcknowledgementResponse {
  acknowledged: boolean
  current_terms_version: string
  acknowledgement?: AccessAcknowledgement
}

export interface AcknowledgementCreateResponse {
  acknowledgement: AccessAcknowledgement
  email_sent: boolean
  email_error: string | null
}

// ── Config-driven transformation engine (Phase B/B2) ──
export interface ConfigTable {
  tiers: [number, number][]   // [limit, value], strictly ascending limits
  over: number
  unknown: number
}

export interface TransformationConfig {
  key: string
  name: string
  description: string | null
  category: string            // cost | margin | inventory | classification
  output_field: string | null
  inputs: string[]
  kind: 'formula' | 'parameter' | 'table'
  editable: boolean           // true for parameter/table (formulas are read-only until Phase C)
  value: number | null        // parameter transformations
  formula: string | null      // formula transformations
  table: ConfigTable | null   // table transformations (e.g. sf_logistics)
}

export interface ConfigVersionInfo {
  id: number
  created_at: string
  created_by: string | null
  note: string | null
  is_active: boolean
  parent_version_id: number | null
}

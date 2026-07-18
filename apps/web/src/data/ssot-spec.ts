export type SsotSource =
  | 'daysmart'    // 📦 DaySmart POS
  | 'shopify'     // 🛒 Shopify
  | 'hktv'        // 🏪 HKTV Mall
  | 'ocr'         // 📄 Supplier Catalogue via Rosetta OCR
  | 'sfexpress'   // 🚚 SF Express weight-band lookup
  | 'manual'      // 🔧 Manual input (humans / Whatsapp)
  | 'internal'    // 📋 Internal config / primary keys
  | 'lookup'      // 🔗 Cached lookup from related table

// Accuracy ladder — where on the journey to 100% is this column TODAY?
// source     → pulled directly from upstream system; accuracy = source quality
// ocr        → machine-extracted from supplier catalogue, NOT yet human-reviewed (~80% accurate)
// hitl       → OCR + human-in-the-loop reviewed via /data-review (~95% accurate)
// 3way       → cost verified by 3-way matching (PO ↔ delivery note ↔ invoice) — 100%
// manual     → manually entered; accuracy depends on whoever typed it
// proposed   → column doesn't exist yet; the spec proposes adding it
export type SsotLadder = 'source' | 'ocr' | 'hitl' | '3way' | 'manual' | 'proposed'

export type SsotConsumer =
  | 'AM'             // Approval Matrix (existing-product PO decisions)
  | 'PS'             // Product Selection (new-product decisions)
  | 'IMS'            // Rosetta IMS reads
  | 'SHOPIFY_OUT'    // Pushed back to Shopify settings
  | 'DAYSMART_OUT'   // Pushed back to DaySmart

export type SsotColumn = {
  id: string
  name: string
  table: 'SUPPLIERS' | 'SKU_MASTER'
  group: string
  source: SsotSource
  ladder: SsotLadder       // accuracy state today
  target?: SsotLadder      // where this column aspires to be (omitted = stays where it is)
  usedBy: SsotConsumer[]
  description: string
  amRef?: string
  psRef?: string
  notes?: string
}

// `isRealSource` distinguishes the 6 actual sources of truth (where someone or
// something outside Rosetta IMS provides the value) from system fields (internal,
// lookup) which IMS generates by itself.
export const SOURCES: Record<SsotSource, {
  icon: string; label: string; color: string; bg: string;
  brief: string;
  providedBy: string;     // who actually populates this data (tech pipeline vs human team)
  pullMethod: string;
  isRealSource: boolean;  // shown as a Stage 1 pill / source filter option?
}> = {
  daysmart:  {
    icon: '📦', label: 'DaySmart', color: '#92400E', bg: '#FEF3C7',
    brief: 'DaySmart Vet — the clinic POS at Ohana Animal Hospital. System of record for clinic sales, in-clinic stock, dispensing fees, and clinic-side prices.',
    providedBy: 'Tech team (Desmond Brown / Austin) — build the DaySmart pull pipeline.',
    pullMethod: 'CSV export today (manual). Target: Admin API pull on a schedule.',
    isRealSource: true,
  },
  shopify:   {
    icon: '🛒', label: 'Shopify', color: '#166534', bg: '#DCFCE7',
    brief: 'Shopify — PetProject HK e-commerce storefront. System of record for online prices, warehouse stock, subscription/autoship orders.',
    providedBy: 'Tech team — build the Shopify Admin API pull. Some fields (min/multiples) get pushed BACK to Shopify from the SSOT.',
    pullMethod: 'Shopify Admin API (real-time).',
    isRealSource: true,
  },
  hktv:      {
    icon: '🏪', label: 'HKTV Mall', color: '#9D174D', bg: '#FCE7F3',
    brief: 'HKTV Mall — Hong Kong marketplace channel. System of record for HKTV listings, channel fees, and visible competitor pricing.',
    providedBy: 'Tech team — automate the HKTV merchant export. Manual export today.',
    pullMethod: 'HKTV merchant export (manual today) → automated scrape/API.',
    isRealSource: true,
  },
  ocr:       {
    icon: '📄', label: 'OCR / Catalogue', color: '#1E40AF', bg: '#DBEAFE',
    brief: 'Supplier catalogues (PDF/Excel) processed through Rosetta IMS OCR. Source of truth for wholesale costs, MBB terms, units-per-pack, supplier SKU codes, barcodes, weights, MPQ.',
    providedBy: 'Rosetta IMS does the extraction. BizOps team approves via /data-review (human-in-the-loop). 3-way matching against invoices finalises cost accuracy.',
    pullMethod: 'Already live. /catalogues uploads PDFs → Claude Haiku extracts → /data-review approves.',
    isRealSource: true,
  },
  sfexpress: {
    icon: '🚚', label: 'SF Express', color: '#5B21B6', bg: '#EDE9FE',
    brief: 'SF Express — negotiated weight-band shipping rates. Weight (kg or g) → discounted HKD rate lookup table.',
    providedBy: 'Operations maintain the rate table. Tech team automates the weight→fee lookup against weight_grams.',
    pullMethod: 'Weight-band rate table (Google Sheet maintained by Ops).',
    isRealSource: true,
  },
  manual:    {
    icon: '🔧', label: 'Manual', color: '#475569', bg: '#F1F5F9',
    brief: 'Manually entered data. Things no source system gives us: category & sub-category, hero SKU flag, risk acceptance, James clinic-need flag, expiry dates (from supplier Whatsapp), supplier contact details, MOQ, credit terms.',
    providedBy: 'BizOps team (Cloddie, Ae) for SKU-level fields. Finance for supplier finance fields. Dr James for clinic-need flag.',
    pullMethod: 'Human entry via Google Sheets today; via Rosetta IMS UI in the target state.',
    isRealSource: true,
  },
  internal:  {
    icon: '📋', label: 'System (IMS)', color: '#0F172A', bg: '#E2E8F0',
    brief: 'NOT a source. System fields auto-generated by Rosetta IMS — primary keys, audit timestamps, edit attribution, provenance flags.',
    providedBy: 'Rosetta IMS itself — no one provides this.',
    pullMethod: 'Auto-generated on write.',
    isRealSource: false,
  },
  lookup:    {
    icon: '🔗', label: 'System (lookup)', color: '#155E75', bg: '#CFFAFE',
    brief: 'NOT a source. Cached / derived value from a related table. e.g. supplier_moq_cached caches Suppliers.moq_hkd onto the SKU row for query speed.',
    providedBy: 'Rosetta IMS itself — computed from another column.',
    pullMethod: 'Computed at read time or refreshed on schedule.',
    isRealSource: false,
  },
}

// The 6 "real" sources — shown as Stage 1 pills and in source filters.
export const REAL_SOURCES: SsotSource[] = ['daysmart','shopify','hktv','ocr','sfexpress','manual']

export const LADDER_META: Record<SsotLadder, { icon: string; label: string; pct: string; color: string; bg: string; brief: string; link?: string }> = {
  source: {
    icon: '📡', label: 'Source Pull', pct: 'live', color: '#0891B2', bg: '#CFFAFE',
    brief: 'Pulled directly from a source system (DaySmart, Shopify, HKTV). Accuracy = source accuracy. No additional verification step needed.',
  },
  ocr: {
    icon: '📄', label: 'OCR (machine only)', pct: 'needs review', color: '#1E40AF', bg: '#DBEAFE',
    brief: 'Machine-extracted from a supplier catalogue. NOT yet human-reviewed. Cannot be trusted on its own — needs the HITL step before it counts.',
    link: '/catalogues',
  },
  hitl: {
    icon: '👤', label: 'OCR + Reviewed', pct: '~80% combined', color: '#7C2D12', bg: '#FED7AA',
    brief: 'OCR-extracted then approved by a human via /data-review. This is the ~80% milestone — humans introduce their own ~20% error band, so this stage is the realistic ceiling before accounting.',
    link: '/data-review',
  },
  '3way': {
    icon: '⚖️', label: '3-way Matched', pct: '100%', color: '#166534', bg: '#DCFCE7',
    brief: 'Cost reconciled across PO ↔ delivery note ↔ invoice via accounting. This is the only step that ground-truths cost — closes the remaining ~20% gap left by OCR+HITL.',
    link: '#',
  },
  manual: {
    icon: '🔧', label: 'Manual', pct: 'varies', color: '#475569', bg: '#F1F5F9',
    brief: 'Entered by a person. No automated verification. Accuracy depends on the typist.',
  },
  proposed: {
    icon: '🆕', label: 'Proposed', pct: 'not yet', color: '#5B21B6', bg: '#EDE9FE',
    brief: 'Column does not exist yet. Proposed by this spec.',
  },
}

export const CONSUMERS: Record<SsotConsumer, { label: string; short: string; color: string; bg: string }> = {
  AM:            { label: 'Approval Matrix',         short: 'AM',        color: '#7C2D12', bg: '#FED7AA' },
  PS:            { label: 'Product Selection',       short: 'PS',        color: '#1E40AF', bg: '#DBEAFE' },
  IMS:           { label: 'Rosetta IMS',             short: 'IMS',       color: '#5B21B6', bg: '#EDE9FE' },
  SHOPIFY_OUT:   { label: 'Shopify (min/multiples)', short: '→Shopify',  color: '#166534', bg: '#DCFCE7' },
  DAYSMART_OUT:  { label: 'DaySmart (cost)',         short: '→DaySmart', color: '#92400E', bg: '#FEF3C7' },
}

// ─── SUPPLIERS — 10 cols ────────────────────────────────────────────────────
export const SUPPLIERS_COLS: SsotColumn[] = [
  { id: 'sup_id',          name: 'supplier_id',                table: 'SUPPLIERS', group: 'Identity',    source: 'internal', ladder: 'source', usedBy: ['AM','PS','IMS'], description: 'Primary key. Internal supplier identifier.' },
  { id: 'sup_name',        name: 'supplier_name',              table: 'SUPPLIERS', group: 'Identity',    source: 'manual',   ladder: 'manual', usedBy: ['AM','PS','IMS'], description: 'Display name. e.g. Alfamedic, Maxipro.' },
  { id: 'sup_moq',         name: 'moq_hkd',                    table: 'SUPPLIERS', group: 'Order rules', source: 'manual',   ladder: 'manual', usedBy: ['AM','PS','IMS'], description: 'Minimum order value per PO (HKD across all line items).', notes: 'Lives in Supplier directory tab today. Today\'s coverage is patchy.' },
  { id: 'sup_credit',      name: 'credit_terms',               table: 'SUPPLIERS', group: 'Order rules', source: 'manual',   ladder: 'manual', usedBy: ['AM'], description: 'Payment terms. e.g. NET30, COD.', amRef: 'Finance group' },
  { id: 'sup_cutoff_day',  name: 'cutoff_day',                 table: 'SUPPLIERS', group: 'Order rules', source: 'manual',   ladder: 'manual', usedBy: ['IMS'], description: 'Weekday cutoff for next-delivery orders.' },
  { id: 'sup_cutoff_time', name: 'cutoff_time',                table: 'SUPPLIERS', group: 'Order rules', source: 'manual',   ladder: 'manual', usedBy: ['IMS'], description: 'Time-of-day cutoff.' },
  { id: 'sup_next_del',    name: 'next_delivery_day',          table: 'SUPPLIERS', group: 'Order rules', source: 'manual',   ladder: 'manual', usedBy: ['IMS'], description: 'Standard delivery day after cutoff.' },
  { id: 'sup_bulk_policy', name: 'bulk_discount_policy',       table: 'SUPPLIERS', group: 'Order rules', source: 'manual',   ladder: 'manual', usedBy: ['PS'], description: 'Free-text supplier bulk-buy policy. Structured tiers live on SKU.' },
  { id: 'sup_bank',        name: 'bank_account',               table: 'SUPPLIERS', group: 'Finance',     source: 'manual',   ladder: 'manual', usedBy: ['AM'], description: 'Bank account for payments.', amRef: 'Finance group' },
  { id: 'sup_contact',     name: 'contact_person_email_phone', table: 'SUPPLIERS', group: 'Finance',     source: 'manual',   ladder: 'manual', usedBy: ['IMS'], description: 'Primary contact details.' },
]

// ─── SKU MASTER — 55 cols ───────────────────────────────────────────────────
export const SKU_MASTER_COLS: SsotColumn[] = [
  // A. Identity & Classification (12)
  { id: 'sku_id',         name: 'sku_id',              table: 'SKU_MASTER', group: 'A. Identity',     source: 'internal', ladder: 'source', usedBy: ['AM','PS','IMS','SHOPIFY_OUT','DAYSMART_OUT'], description: '8-digit internal SKU identifier. Join key everywhere.', amRef: 'col F (OPS-LOGIC - G)' },
  { id: 'sku_name',       name: 'sku_name',            table: 'SKU_MASTER', group: 'A. Identity',     source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['AM','PS','IMS'], description: 'Standardized product name.', amRef: 'col G' },
  { id: 'brand',          name: 'brand',               table: 'SKU_MASTER', group: 'A. Identity',     source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['AM','PS','IMS'], description: 'Brand name extracted from supplier catalogue.' },
  { id: 'category',       name: 'category',            table: 'SKU_MASTER', group: 'A. Identity',     source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['AM','PS','IMS'], description: 'Top-level category: Medicine / Food / Supplement / Preventative / Pet Hygiene / Not-For-Sale. Per v7 review: OCR extracts from catalogue section + product context; BizOps verifies in /data-review.', amRef: 'col J (SKU Master - BH)' },
  { id: 'sub_category',   name: 'sub_category',        table: 'SKU_MASTER', group: 'A. Identity',     source: 'ocr',      ladder: 'proposed', target: 'hitl', usedBy: ['AM','PS','IMS'], description: 'Granular sub-category. Vaccine / Chemotherapy / Antibiotics / Wet Food / Dry Food / etc. Drives GP floor + James approval policy. Per v7 review: OCR + HITL, not manual.', notes: 'NEW per Chris/Seph 29 May call. Owner flipped from MANUAL to OCR+HUMAN on 2026-06-01.' },
  { id: 'species',        name: 'species',             table: 'SKU_MASTER', group: 'A. Identity',     source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['PS','IMS'], description: 'Dog / Cat / Both / Other. Per v7 review: extractable from catalogue product naming.' },
  { id: 'storage_rule',   name: 'storage_rule',        table: 'SKU_MASTER', group: 'A. Identity',     source: 'internal', ladder: 'manual', usedBy: ['IMS'], description: 'clinic_only / any — controls where stock can be held.' },
  { id: 'status',         name: 'status',              table: 'SKU_MASTER', group: 'A. Identity',     source: 'internal', ladder: 'manual', usedBy: ['IMS'], description: 'ACTIVE / INACTIVE / DISCONTINUED.', notes: 'Today only ~32% of SKUs have this set.' },
  { id: 'hero_sku',       name: 'hero_sku',            table: 'SKU_MASTER', group: 'A. Identity',     source: 'manual',   ladder: 'manual', usedBy: ['AM','PS','IMS'], description: 'QGB/SGB flag — e-commerce weeks-of-cover priority.', amRef: 'col AU/BB (SKU Master - CF)' },
  { id: 'clinic_needs',   name: 'clinic_needs_it',     table: 'SKU_MASTER', group: 'A. Identity',     source: 'manual',   ladder: 'proposed', target: 'manual', usedBy: ['PS','AM'], description: 'James-approved flag. If true, clinic absorbs leftover units when MPQ > MOU, reducing inventory risk to e-commerce.', notes: 'NEW per 29 May call.' },
  { id: 'listed_hktv',    name: 'listed_on_hktv',      table: 'SKU_MASTER', group: 'A. Identity',     source: 'hktv',     ladder: 'source', usedBy: ['PS','IMS'], description: 'Boolean. Replaces ambiguously-named "YES" column.' },
  { id: 'listed_shopify', name: 'listed_on_shopify',   table: 'SKU_MASTER', group: 'A. Identity',     source: 'shopify',  ladder: 'source', usedBy: ['PS','IMS'], description: 'Boolean.' },

  // B. Supplier link (5)
  { id: 'primary_sup',    name: 'primary_supplier_id',          table: 'SKU_MASTER', group: 'B. Supplier',    source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['AM','PS','IMS'], description: 'FK to Suppliers table. Single primary supplier. Per v7 review: derivable from which supplier catalogue the OCR scan came from.', amRef: 'col D' },
  { id: 'alt_sups',       name: 'alternative_supplier_ids',     table: 'SKU_MASTER', group: 'B. Supplier',    source: 'ocr',      ladder: 'proposed', target: 'hitl', usedBy: ['PS','IMS'], description: 'Comma-separated FKs. Used when primary out of stock. Per v7 review: OCR can flag when same SKU appears across multiple supplier catalogues.' },
  { id: 'sup_sku_code',   name: 'supplier_sku_code',            table: 'SKU_MASTER', group: 'B. Supplier',    source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['AM','IMS'], description: 'Supplier\'s own SKU code, captured via OCR catalogue match.', notes: 'Today ~0% filled in current master. Fixed by Rosetta OCR matching.' },
  { id: 'sup_barcode',    name: 'supplier_barcode',             table: 'SKU_MASTER', group: 'B. Supplier',    source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['IMS'], description: 'EAN/UPC barcode from catalogue.', notes: 'Today ~37% filled.' },
  { id: 'sup_moq_cached', name: 'supplier_moq_cached',          table: 'SKU_MASTER', group: 'B. Supplier',    source: 'lookup',   ladder: 'proposed', target: 'source', usedBy: ['AM','PS','IMS'], description: 'Cached copy of Suppliers.moq_hkd for query convenience.', notes: 'Refreshed from Suppliers table.' },

  // C. Cost — "THE GAP" (9)
  { id: 'basic_cost',         name: 'basic_unit_cost',             table: 'SKU_MASTER', group: 'C. Cost',         source: 'ocr',      ladder: 'ocr', target: '3way', usedBy: ['AM','PS','IMS'], description: 'Wholesale cost per unit at basic (non-bulk) tier.', amRef: 'col M (SKU Master - BM)', psRef: 'col 4' },
  { id: 'catalogue_cost',     name: 'catalogue_cost',              table: 'SKU_MASTER', group: 'C. Cost',         source: 'ocr',      ladder: 'proposed', target: 'ocr', usedBy: ['IMS'], description: 'Raw cost exactly as printed in supplier catalogue (audit trail).' },
  { id: 'daysmart_inv_cost',  name: 'daysmart_last_invoice_cost',  table: 'SKU_MASTER', group: 'C. Cost',         source: 'daysmart', ladder: 'proposed', target: '3way', usedBy: ['AM','IMS'], description: 'Most recent unit cost from DaySmart invoice (clinic side).', notes: 'Today 0% filled — THE rot. Needs DaySmart pull.' },
  { id: 'shopify_inv_cost',   name: 'shopify_last_invoice_cost',   table: 'SKU_MASTER', group: 'C. Cost',         source: 'shopify',  ladder: 'proposed', target: '3way', usedBy: ['AM','IMS'], description: 'Most recent unit cost from Shopify invoice (warehouse side).', notes: 'Today 0% filled — THE rot. Needs Shopify pull.' },
  { id: 'mbb_cost',           name: 'mbb_cost_per_unit',           table: 'SKU_MASTER', group: 'C. Cost',         source: 'ocr',      ladder: 'ocr', target: '3way', usedBy: ['AM','PS','IMS'], description: 'Per-unit cost at Max Bulk Buy tier.', amRef: 'col N/O/P' },
  { id: 'mbb_min_qty',        name: 'mbb_min_qty',                 table: 'SKU_MASTER', group: 'C. Cost',         source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['AM','PS','IMS'], description: 'Quantity threshold to trigger MBB pricing.' },
  { id: 'mbb_tiers',          name: 'mbb_tier_structure',          table: 'SKU_MASTER', group: 'C. Cost',         source: 'ocr',      ladder: 'proposed', target: 'hitl', usedBy: ['PS','IMS'], description: 'Structured JSON of bulk tiers (was free-text MBB Terms).', notes: 'Today free text like "1 box, 5+1 boxes, 8+4 boxes" — un-computable.' },
  { id: 'cost_source',        name: 'cost_source',                 table: 'SKU_MASTER', group: 'C. Cost',         source: 'internal', ladder: 'proposed', target: 'source', usedBy: ['IMS'], description: 'catalogue / manual / invoice_matched — provenance flag.' },
  { id: 'cost_reviewed_at',   name: 'cost_last_reviewed_at',       table: 'SKU_MASTER', group: 'C. Cost',         source: 'internal', ladder: 'source', usedBy: ['AM','IMS'], description: 'Timestamp. Replaces broken "Days Since Review" formula.', amRef: 'col W (SKU Master)' },

  // D. Pack / Unit structure (5)
  { id: 'mou',                name: 'minimum_operating_unit',      table: 'SKU_MASTER', group: 'D. Pack',         source: 'ocr',      ladder: 'proposed', target: 'hitl', usedBy: ['PS','SHOPIFY_OUT'], description: 'Smallest sellable unit to a customer. e.g. 1 tablet, 1 can, 1 ml. Per v7 review: derivable from catalogue UOM + pack context.', notes: 'NEW per 29 May call. Owner flipped from MANUAL to OCR+HUMAN on 2026-06-01.' },
  { id: 'uom',                name: 'uom_sell_unit',               table: 'SKU_MASTER', group: 'D. Pack',         source: 'daysmart', ladder: 'source', usedBy: ['IMS','SHOPIFY_OUT'], description: 'Unit-of-measure for selling: tablet, can, ml, pc.' },
  { id: 'mpq',                name: 'minimum_purchase_qty',        table: 'SKU_MASTER', group: 'D. Pack',         source: 'ocr',      ladder: 'proposed', target: 'hitl', usedBy: ['PS','AM','SHOPIFY_OUT'], description: 'Minimum quantity supplier requires per SKU per PO. e.g. case of 24 cans → 24.', notes: 'NEW per 29 May call.' },
  { id: 'units_per_pack',     name: 'units_per_pack',              table: 'SKU_MASTER', group: 'D. Pack',         source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['AM','PS','IMS'], description: 'Units per carton/box. e.g. 12 cans/case.', amRef: 'col AB (SKU Master - BP)' },
  { id: 'weight_g',           name: 'weight_grams',                table: 'SKU_MASTER', group: 'D. Pack',         source: 'ocr',      ladder: 'ocr', target: 'hitl', usedBy: ['AM','PS','IMS'], description: 'Weight in grams. Drives SF Express weight-band fee lookup.', amRef: 'col K (SKU Master - BV)' },

  // E. Risk & Shopify settings (3)
  { id: 'risk_accept',        name: 'risk_acceptance',             table: 'SKU_MASTER', group: 'E. Risk',         source: 'manual',   ladder: 'proposed', target: 'manual', usedBy: ['PS','SHOPIFY_OUT'], description: 'pass_to_customer / we_take_risk / clinic_absorbs. Business judgment: can we make customer buy MPQ?', notes: 'NEW per 29 May call. Drives Shopify min/multiples and inventory risk.' },
  { id: 'shopify_min',        name: 'shopify_minimum_qty',         table: 'SKU_MASTER', group: 'E. Risk',         source: 'manual',   ladder: 'proposed', target: 'manual', usedBy: ['SHOPIFY_OUT'], description: 'Shopify "minimum" setting — derived from MPQ + Risk Acceptance.' },
  { id: 'shopify_multi',      name: 'shopify_multiples',           table: 'SKU_MASTER', group: 'E. Risk',         source: 'manual',   ladder: 'proposed', target: 'manual', usedBy: ['SHOPIFY_OUT'], description: 'Shopify "multiples" setting — derived from MPQ + Risk Acceptance.' },

  // F. Sell prices (5)
  { id: 'sell_clinic',        name: 'sell_price_clinic',           table: 'SKU_MASTER', group: 'F. Sell prices',  source: 'daysmart', ladder: 'source', usedBy: ['AM','PS','IMS'], description: 'Clinic (DaySmart) selling price.', amRef: 'col L (SKU Master - BJ)' },
  { id: 'sell_shopify',       name: 'sell_price_shopify',          table: 'SKU_MASTER', group: 'F. Sell prices',  source: 'shopify',  ladder: 'source', usedBy: ['AM','PS','IMS'], description: 'Shopify selling price.' },
  { id: 'sell_hktv',          name: 'sell_price_hktv',             table: 'SKU_MASTER', group: 'F. Sell prices',  source: 'hktv',     ladder: 'source', usedBy: ['AM','PS','IMS'], description: 'HKTV Mall selling price.' },
  { id: 'dispensing_fee_clinic',    name: 'dispensing_fee_clinic',    table: 'SKU_MASTER', group: 'F. Sell prices',  source: 'daysmart', ladder: 'source', usedBy: ['AM','PS','IMS'], description: 'HKD ADDED ON TOP of clinic sell price. 0 = doesn\'t apply. Origin: human judgment → entered in DaySmart → pulled into SSOT. Lifts true clinic GP materially.', amRef: 'col Q (SKU Master - BU)' },
  { id: 'dispensing_fee_ecommerce', name: 'dispensing_fee_ecommerce', table: 'SKU_MASTER', group: 'F. Sell prices',  source: 'shopify',  ladder: 'source', usedBy: ['AM','PS','IMS'], description: 'HKD ADDED ON TOP of online sell price. 0 = doesn\'t apply. Origin: human judgment → entered in Shopify (or IMS config) → pulled into SSOT. Usually differs from clinic fee.', notes: 'NEW — split from previous dispensing_fee_applies Y/N per 31 May call.' },
  { id: 'channel_fee_hktv',   name: 'channel_fee_pct_hktv',        table: 'SKU_MASTER', group: 'F. Sell prices',  source: 'hktv',     ladder: 'source', usedBy: ['AM','PS'], description: 'HKTV Mall channel fee %.', amRef: 'col R (SKU Master - BC)' },

  // G. Stock & demand (7)
  { id: 'stock_clinic',       name: 'stock_clinic',                table: 'SKU_MASTER', group: 'G. Stock & demand', source: 'daysmart', ladder: 'source', usedBy: ['AM','IMS'], description: 'Clinic (DaySmart) on-hand quantity.', amRef: 'col V (OPS-LOGIC - K)' },
  { id: 'stock_warehouse',    name: 'stock_warehouse',             table: 'SKU_MASTER', group: 'G. Stock & demand', source: 'shopify',  ladder: 'source', usedBy: ['AM','IMS'], description: 'Warehouse on-hand quantity.', amRef: 'col U (OPS-LOGIC - J)' },
  { id: 'demand_120d_ds',     name: 'demand_120d_daysmart',        table: 'SKU_MASTER', group: 'G. Stock & demand', source: 'daysmart', ladder: 'source', usedBy: ['AM','IMS'], description: '120-day demand from DaySmart sales.', amRef: 'col X (BIZFIN DEMAND 2.0)' },
  { id: 'demand_120d_sh',     name: 'demand_120d_shopify',         table: 'SKU_MASTER', group: 'G. Stock & demand', source: 'shopify',  ladder: 'source', usedBy: ['AM','IMS'], description: '120-day demand from Shopify orders.' },
  { id: 'demand_120d_hk',     name: 'demand_120d_hktv',            table: 'SKU_MASTER', group: 'G. Stock & demand', source: 'hktv',     ladder: 'source', usedBy: ['AM','IMS'], description: '120-day demand from HKTV Mall.' },
  { id: 'unfulfilled_jit',    name: 'unfulfilled_jit',             table: 'SKU_MASTER', group: 'G. Stock & demand', source: 'shopify',  ladder: 'source', usedBy: ['AM','IMS'], description: 'Paid but unfulfilled Shopify orders.', amRef: 'col Z (OPS-LOGIC - T)' },
  { id: 'autoship_14d',       name: 'upcoming_14d_autoship',       table: 'SKU_MASTER', group: 'G. Stock & demand', source: 'shopify',  ladder: 'source', usedBy: ['AM','IMS'], description: 'Upcoming 14-day subscription deliveries.', amRef: 'col AA (OPS-LOGIC - U)' },

  // H. Market & logistics (5)
  { id: 'comp_lowest',        name: 'competitor_lowest_price',     table: 'SKU_MASTER', group: 'H. Market',       source: 'hktv',     ladder: 'source', usedBy: ['AM','PS','IMS'], description: 'Lowest competitor price observed.', amRef: 'col BH (SKU Master - CB)' },
  { id: 'comp_url',           name: 'competitor_source_url',       table: 'SKU_MASTER', group: 'H. Market',       source: 'manual',   ladder: 'manual', usedBy: ['IMS'], description: 'URL of competitor listing.' },
  { id: 'comp_reviewed',      name: 'competitor_last_reviewed',    table: 'SKU_MASTER', group: 'H. Market',       source: 'manual',   ladder: 'manual', usedBy: ['IMS'], description: 'Date competitor price was last verified.' },
  { id: 'rrp',                name: 'rrp',                         table: 'SKU_MASTER', group: 'H. Market',       source: 'ocr',      ladder: 'proposed', target: 'hitl', usedBy: ['PS','IMS'], description: 'Recommended retail price from supplier catalogue.' },
  { id: 'logistics_cost',     name: 'sf_express_fee',              table: 'SKU_MASTER', group: 'H. Market',       source: 'sfexpress', ladder: 'proposed', target: 'source', usedBy: ['AM','PS','IMS'], description: 'Per-unit SF Express shipping fee. Looked up from negotiated weight-band rate table using weight_grams.', amRef: 'col T (SSOT - Record | Logistic Cost)', notes: 'SF Express has tiered rates by weight (kg/g). Today the lookup is manual; should be automated.' },

  // I. Expiry & audit (4)
  { id: 'exp_date',           name: 'expiration_date',             table: 'SKU_MASTER', group: 'I. Expiry & audit', source: 'manual',  ladder: 'manual', usedBy: ['AM'], description: 'Stock-batch expiration date (from supplier Whatsapp).', amRef: 'col AR (OPS-LOGIC - V)' },
  { id: 'bpb',                name: 'bulk_purchase_benefit_available', table: 'SKU_MASTER', group: 'I. Expiry & audit', source: 'manual', ladder: 'manual', usedBy: ['AM'], description: 'Boolean. Whether supplier currently offers a bulk-purchase rebate.', amRef: 'col AV/BC' },
  { id: 'updated_by',         name: 'last_updated_by',             table: 'SKU_MASTER', group: 'I. Expiry & audit', source: 'internal', ladder: 'source', usedBy: ['IMS'], description: 'User who last edited this row.', notes: 'Today ~47% filled — most edits unattributed in current sheet.' },
  { id: 'updated_at',         name: 'last_updated_at',             table: 'SKU_MASTER', group: 'I. Expiry & audit', source: 'internal', ladder: 'proposed', target: 'source', usedBy: ['IMS'], description: 'Timestamp of last edit (any column).' },
]

export const ALL_COLS: SsotColumn[] = [...SUPPLIERS_COLS, ...SKU_MASTER_COLS]

export const CONSUMER_INFO = [
  { id: 'AM'           as const, label: 'Approval Matrix',     sub: 'Existing SKU PO approvals · 85 cols · reads ~15 from SSOT', colorBg: '#FFEDD5', colorBorder: '#FED7AA', colorText: '#7C2D12' },
  { id: 'PS'           as const, label: 'Product Selection',   sub: 'New SKU sourcing · 18 cols · reads ~15 from SSOT',          colorBg: '#DBEAFE', colorBorder: '#BFDBFE', colorText: '#1E40AF' },
  { id: 'IMS'          as const, label: 'Rosetta IMS',         sub: 'All 65 cols · the new SSOT',                                colorBg: '#EDE9FE', colorBorder: '#DDD6FE', colorText: '#5B21B6' },
  { id: 'SHOPIFY_OUT'  as const, label: '→ Shopify push',      sub: 'Min qty + multiples settings',                              colorBg: '#DCFCE7', colorBorder: '#BBF7D0', colorText: '#166534' },
  { id: 'DAYSMART_OUT' as const, label: '→ DaySmart push',     sub: 'Cost overrides',                                            colorBg: '#FEF3C7', colorBorder: '#FDE68A', colorText: '#92400E' },
]

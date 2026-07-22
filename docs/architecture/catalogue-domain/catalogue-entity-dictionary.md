# Catalogue Domain Entity Dictionary

## 1. Document metadata

| Property | Value |
|---|---|
| **Title** | Catalogue Domain Entity Dictionary |
| **Version** | 0.1 |
| **Status** | Draft — for review |
| **Owner** | Rosetta IMS — Catalogue & Data Architecture |
| **Domain** | Catalogue (Product Master + Supplier Commercial/Cost) |
| **Date** | 2026-07-21 |
| **Intended reviewers** | Austin (Ops / Product), Chris (Technical owner / CODEOWNERS), Ops team |
| **Related ClickUp task** | **CIS-102 — Define Canonical Catalogue Domain Entities** |
| **Repository** | `rosetta-ims-clean` (this repo). Backend evidence lives in `apps/api/`, which is byte-identical to the legacy `rosetta-ims/backend/`, so every `file:line` reference below is valid in this repo. |
| **Predecessor inputs** | The CIS-101.1/2/3 + support docs live in the **legacy `rosetta-ims/docs/`** (not ported to this repo per the CIS-102 scope): `catalogue-ingestion-flow.md` · `database-models-review.md` · `data-conflation-audit.md` · `product-vs-supplier-fields.md` · `mbb-per-supplier-margin.md`. Historical YAML-style supplier extraction mappings have been removed from this repo because they are not contracts. All inline `*.md` citations in this document refer to those legacy-repo predecessor deliverables. |

> **Nature of this document.** This is a **conceptual** dictionary — the *intended* canonical domain model, expressed in business terms. It is grounded in the current implementation (`apps/api/models.py`, ingestion code, and supplier-source contract work) as **evidence of the current state**, but it does **not** treat every existing column as canonical truth. Where the implementation conflates concepts or lacks an abstraction, that is recorded as a **modelling gap** in §7, not silently reproduced.
>
> **Constraints honoured.** No code, models, migrations, serializers, or APIs were changed to produce this document. It is documentation only. The next deliverable (the Canonical Catalogue ERD) consumes this dictionary.

---

## 2. Purpose and scope boundary

### What the catalogue domain covers

The catalogue domain answers three questions and nothing else:

1. **What did a supplier tell us?** — the preserved source evidence of a supplier's published catalogue (the file and the rows extracted from it).
2. **What is the canonical thing?** — the mastered, deduplicated identity of a sellable item: its Product, its stock-identifiable Variant / Canonical SKU, its Brand, its Category, its units of measure.
3. **How does a specific supplier offer it to us commercially?** — the supplier-specific purchasing offering: supplier SKU, packaging, published cost, and conditional bulk-buy terms.

Plus the two cross-cutting concerns that make the above trustworthy: **integration** (how a canonical item connects to external systems' identities) and **governance** (the human decisions and audit trail behind every canonical value).

### Explicitly OUT of scope (consume catalogue data, but are not modelled here)

| Excluded domain | Why it is not catalogue |
|---|---|
| **Inventory balances** (`stock_levels`) | On-hand quantity is a warehouse/clinic fact, not a catalogue fact. |
| **Warehouses & stock movements** (`stock_adjustments`) | Physical movement, not identity or cost. |
| **Demand calculations** (`sales_velocity`, WOC) | Derived analytics that *read* the catalogue. |
| **Procurement decisions** | What/when to buy is downstream of "what it is and what it costs". |
| **Purchase orders / Receiving / Supplier invoices** | Procurement lifecycle; catalogue is the reference they cite. Note: invoice-reconciliation was deliberately retired from IMS as "a procurement concern" (`models.py:208`). |
| **Three-way matching** | Procurement/finance control, not catalogue. |
| **Customer & order domains** (Client SSOT, channels' selling side) | Selling price per channel and customer data are adjacent domains that consume catalogue identity. |

> **Boundary note on price.** This domain owns **supplier cost** (what we pay). It does **not** own **selling price** (`product_channels.selling_price`) or **margin/GP** — those belong to the pricing/merchandising domain, which reads catalogue cost as an input. Cost is in; retail price and margin are out. (`Category.gp_floor` is included only as a *classification* attribute of Category, not as pricing logic.)

---

## 3. Entity inventory

Classification legend: **Source Evidence** = captured as-received, preserved, never overwritten by cleanup · **Canonical Master** = the mastered "what it is" · **Supplier Commercial** = supplier-specific "how they price/pack/sell it to us" · **Integration** = links to external systems · **Governance** = decisions & audit.

| Entity | Classification | Business purpose | System of record |
|---|---|---|---|
| **Supplier Catalogue** | Source Evidence | One received supplier catalogue file + its provenance | Rosetta IMS (ingestion) |
| **Catalogue Item** | Source Evidence | One extracted catalogue row, raw + normalized, awaiting/holding a review decision | Rosetta IMS (ingestion) |
| **Supplier** | Canonical Master | The distributor/vendor we buy from | Rosetta IMS (seeded from supplier sheets) |
| **Brand** | Canonical Master | The manufacturer/brand a product belongs to | ⚠ *Not yet mastered* — currently a string + supplier-carries link |
| **Category** | Canonical Master | Merchandising class + its handling/pricing/SKU rules | Rosetta IMS |
| **Product** | Canonical Master | The brand+line concept above individual sizes/variants | ⚠ *Not yet a distinct entity* — collapsed into the Variant row |
| **Product Variant / Canonical SKU** | Canonical Master | The stock-identifiable item; one per internal SKU | Rosetta IMS (the `products` table) |
| **Unit of Measure** | Canonical Master (reference) | Controlled vocabulary for sell-unit / buy-unit / weight-unit | ⚠ *Not a table* — free strings |
| **Supplier Product** | Supplier Commercial | A specific supplier's purchasing offering of a Variant | Rosetta IMS (`product_suppliers`) |
| **Packaging Configuration** | Supplier Commercial | Structured cost-basis pack (how many sell-units the price covers) | Rosetta IMS (embedded on Supplier Product) |
| **Supplier Price** | Supplier Commercial | A supplier's published cost, with currency, basis and effective period | ⚠ *Partial* — a single current scalar; no currency/basis/history |
| **MBB Term (Max-Bulk-Buy)** | Supplier Commercial | A conditional bulk-buy commercial term on a Supplier Product | Rosetta IMS (`mbb_terms`) |
| **Landed Cost Assessment** | Supplier Commercial (cost) | Supplier cost + freight/duty/import charges → true landed cost | ⚠ *Does not exist in Rosetta* — see §8 Q4 |
| **External Product Mapping** | Integration | Link from a Variant to an external system's product identity | ⚠ *Not a table* — scattered per-platform columns |
| **Review Decision** | Governance | A human curation decision (confirm/assign/edit/reject/confirm-supplier) | Rosetta IMS (`catalogue_audit` + re-parse confirm) |
| **Audit Event** | Governance | Append-only record of any material system/user action | Rosetta IMS (`audit_log` + `catalogue_audit`) |

**16 entities.** Five carry a ⚠ (Brand, Product, Unit of Measure, Supplier Price, External Product Mapping are under-modelled today; Landed Cost Assessment is absent). Those gaps are the substance of §7 and §8.

---

## 4. Detailed entity definitions

> Attribute tables list the **business-meaningful** attributes, not every physical column. "Source" cites current repository evidence (`file:line`) where it exists, or marks the attribute **[intended]** where the canonical model requires it but the implementation does not yet carry it.

---

### 4.1 Supplier Catalogue

*Classification: Source Evidence.*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Source Evidence |
| Business definition | One catalogue **document received from a supplier** (a price list / product list), together with the provenance of how it was ingested. It is the file-level unit of source truth. |
| Primary identifier | Import ID (surrogate) |
| Business identifier | Supplier + filename + received date (no formal natural key today) |
| System of record | Rosetta IMS ingestion (`catalogue_imports`, `models.py:395`) |
| Lifecycle/status | `pending → review → (contract_stale?)`; item-level review proceeds independently |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| import_id | Surrogate identity | Integer | Yes | `42` | `models.py:398` |
| supplier | The resolved supplier this catalogue is from | Ref → Supplier (0..1 until confirmed) | No (until confirmed) | Hill's (14) | `supplier_id`, `models.py:399` |
| filename | Original uploaded filename | Text | Yes | `Hill's Science Diet Price List.pdf` | `models.py:400` |
| format | Source file format | Enum: pdf \| xlsx \| jpeg \| gdoc | Yes | `pdf` | `models.py:401` |
| received_at | When the file was ingested | ISO datetime string | Yes | `2026-07-21T09:00:00` | `imported_at`, `models.py:402` |
| item_count | Rows extracted | Integer | No | `120` | `models.py:404` |
| detected_supplier_name | What the extractor read off the document | Text | No | `Hill's Pet Nutrition` | `models.py:407` |
| detected_brands | Brands detected in the document | Text (comma-joined) | No | `Hill's` | `models.py:408` |
| supplier_confidence | Resolver confidence for the matched supplier | Decimal 0–1 | No | `0.95` | `models.py:409` |
| supplier_status | Supplier-resolution state | Enum: confirmed \| needs_review | No | `confirmed` | `models.py:411` |
| **source_ref** | Storage key of the **persisted raw file** (enables re-OCR / re-parse) | Text (path) | No (best-effort) | `/data/catalogue_uploads/42.pdf` | `models.py:414`; `catalogues.py:38,210` |
| effective_date | Catalogue's own "effective from" date (printed on the document) | Date | No **[intended]** | `2024-04-01` | Printed on Hill's PDF; **not captured today** |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| is from | Supplier | Zero or one | No (until confirmed) | The vendor whose catalogue this is; unresolved at upload, set on confirm |
| contains | Catalogue Item | Zero or many | No | The extracted rows |
| governed by | Supplier Source Contract | Zero or one | No | A Pydantic supplier-format contract selected by explicit contract ID/version in future ingestion work |

#### Business rules

- The **raw uploaded file is preserved** (`source_ref`) so the catalogue can be re-parsed deterministically later; a storage failure never fails the import (best-effort, `catalogues.py:38`).
- If **>50% of rows fail optional legacy mapping validation**, the catalogue is flagged `contract_stale` for review (drift signal in `catalogues.py:167`).
- Current ingestion has no repository-shipped supplier YAML mappings and falls back to generic AI extraction unless a local operator-only mapping is supplied. Future contract selection should use the Pydantic supplier-source registry.
- Supplier resolution is **suggested, never forced**: an ambiguous match leaves `supplier_status = needs_review` with a best-guess pre-selection (§4.3 rules).

#### Example

`import 42`: `Hill's Science Diet Price List.pdf`, format `pdf`, received 2026-07-21, resolved to Supplier 14 (Hill's) at confidence 0.95 (`confirmed`), 120 rows, raw file persisted at `source_ref`. The document header states "Effective 1 APR 2024" — a business-meaningful **effective date** that today is *not* captured on the record (gap, §7).

> **Supplier Source Contract (governance artifact, not a stored entity).** A supplier-source contract is a Pydantic declaration under `apps/api/schemas/catalogue_pipeline/supplier_contracts/` keyed by supplier, document format, and version. Historical YAML-style files under `apps/api/catalogue_contracts/` were removed because they were extraction mappings, not contracts.

---

### 4.2 Catalogue Item

*Classification: Source Evidence.*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Source Evidence (the reviewable unit of work) |
| Business definition | **One row extracted from a Supplier Catalogue** — a supplier's line as printed, plus normalized/translated fields and extraction confidence. It is a *claim* awaiting a human decision; it is **not** canonical inventory. |
| Primary identifier | Item ID (surrogate) |
| Business identifier | (Import ID + supplier SKU) is the practical key; not formally unique |
| System of record | Rosetta IMS ingestion (`catalogue_items`, `models.py:435`) |
| Lifecycle/status | `pending → matched \| new_sku \| rejected`; may be `skipped` (set aside, still undecided) |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| item_id | Surrogate identity | Integer | Yes | `9001` | `models.py:438` |
| import | Owning Supplier Catalogue | Ref → Supplier Catalogue | Yes | `42` | `models.py:439` |
| supplier | Supplier (denormalized from the import) | Ref → Supplier | No | Hill's (14) | `models.py:440` |
| raw_description | Product name shown for review (English after translation) | Text | No | `Science Diet · Adult · Chicken` | `models.py:441` |
| original_description | Source text **as printed**, when translated | Text | No | `處方糧 · 成犬` | `models.py:442` — preserves un-translated evidence |
| supplier_sku | Supplier's own product code, **as printed** | Text | No | `10447` | `models.py:443` |
| barcode | Barcode as printed | Text | No | `9310022...` | `models.py:444` |
| cost_price | Extracted supplier cost, **as printed** (basis per the contract) | Decimal (HKD assumed) | No | `128.0` | `models.py:445` |
| rrp | Recommended retail price, as printed | Decimal | No | `205.0` | `models.py:475` |
| uom | Sell unit, as extracted | Text | No | `can` | `models.py:446` |
| units_per_pack | Sell-units per purchasable pack, as extracted | Integer | No | `1` | `models.py:447` |
| pack_size | **Raw pack-size string, exactly as printed** | Text | No | `2kg` / `100 tabs/box` | `models.py:451` — the un-parsed evidence |
| variant | Size/volume/flavour distinguishing sibling variants | Text | No | `15ml` | `models.py:450` |
| brand | Extracted brand | Text | No | `Hill's` | `models.py:449` |
| weight_grams | Net weight per sell-unit (canonical grams) | Decimal | No | `2000` | `models.py:473` |
| species | dog \| cat \| both \| other | Text | No | `dog` | `models.py:472` |
| min_purchase_qty | Supplier MOQ per SKU (packs) | Integer | No | `1` | `models.py:476` |
| bulk_tiers / bulk_buy_tiers | Extracted bulk-buy structure (raw) | JSON / Text | No | `[{min_qty:5, unit_cost:490}]` | `models.py:454,477` |
| confidence_score | **Extraction** confidence (queue sort key) | Decimal 0–1 | No | `0.82` | `models.py:455` — *not* match confidence |
| confidence_detail | Per-field confidence + contract flags | JSON | No | `{cost:0.9,...}` | `models.py:456` |
| review_status | Review lifecycle state | Enum: pending \| matched \| new_sku \| rejected | Yes | `pending` | `models.py:457` |
| skipped / skipped_at / skipped_by | Set-aside marker (undecided) | Int 0/1 + who/when | No | `0` | `models.py:460-462` |
| matched_product_id | Bridge to the Variant it resolved to (stamped **only on commit**) | Ref → Variant | No | `→ 10010385` | `models.py:463` |
| assigned_sku | The canonical SKU assigned/confirmed on commit | Text | No | `10010385` | `models.py:464` |
| parser_version / reparsed_at / reparse_source | Re-parse provenance | Text / date | No | `v3-recapture` | `models.py:480-482` |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| belongs to | Supplier Catalogue | Exactly one | Yes | The file it was extracted from |
| attributed to | Supplier | Zero or one | No | Denormalized supplier |
| resolves to | Product Variant / Canonical SKU | Zero or one | No | The canonical item it was matched to / minted as (only at commit) |
| becomes / updates | Supplier Product | Zero or one | No | On commit, its cost/pack/SKU write to the supplier link |
| decided by | Review Decision | Zero or many | No | Every human action on it is audited |

#### Business rules

- **Source evidence is preserved, never overwritten by cleanup.** `original_description` keeps the pre-translation text; `pack_size` keeps the raw printed string even after `units_per_pack` is derived; `cost_price` is the extracted number, distinct from the canonical `basic_cost` it may later set.
- **Nothing reaches live inventory without an explicit human commit.** `matched_product_id`/`assigned_sku` are stamped only at the commit action (`catalogues.py:1231,1367-1368`); the item table and the `products` table are joined *only* by these bridge fields (Catalogue Item ≠ Product, §5).
- **`confidence_score` is extraction confidence**, a triage/sort signal — it is NOT the live match confidence computed at review time (`_find_matches`, §4.7).
- A `rejected` item records a reason; a `skipped` item stays undecided and is hidden from the active queue.

#### Example

`item 9001` (from import 42): `supplier_sku=10447`, `raw_description="Science Diet · Adult · Chicken"`, `pack_size="2kg"` (raw), `units_per_pack=1` (derived), `cost_price=128.0`, `rrp=205.0`, `species=dog`, `weight_grams=2000`, `confidence_score=0.82`, `review_status=pending`. A reviewer later confirms it → `matched_product_id` and `assigned_sku` are stamped, and its cost/pack flow to a Supplier Product.

---

### 4.3 Supplier

*Classification: Canonical Master.*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Canonical Master |
| Business definition | A **distributor/vendor** Rosetta purchases from (e.g. Hill's, Alfamedic, Vetapet). Distinct from Brand — a supplier may carry many brands, and a brand may come from many suppliers. |
| Primary identifier | Supplier ID (surrogate) |
| Business identifier | `code` (unique, e.g. `ALF`) |
| System of record | Rosetta IMS (`suppliers`, seeded/imported from supplier sheets) |
| Lifecycle/status | `is_active` 1/0; `source` = sheet_import \| manual |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| supplier_id | Surrogate identity | Integer | Yes | `14` | `models.py:77` |
| code | Short unique code | Text (unique) | Yes | `ALF` | `models.py:78` |
| name | Display name | Text | Yes | `Alfamedic` | `models.py:79` |
| normalized_name | Casefolded matching key (suffixes stripped) | Text | No | `alfamedic` | `models.py:86`; `supplier_import.py:37` |
| segment | vet \| non_vet \| unknown | Text | No | `vet` | `models.py:87` |
| commercial terms | MOQ, credit term, monthly rebate, bulk-buy structure | Text (each) | No | `credit_term="30 days"` | `models.py:90-94` |
| logistics | order/delivery days, cut-off, delivery charges, pickup | Text (each) | No | `order_days="Mon,Wed"` | `models.py:96-102` |
| is_active | Active flag | Int 0/1 | Yes | `1` | `models.py:111` |
| source | Provenance | Enum: sheet_import \| manual | No | `sheet_import` | `models.py:112` |
| raw_json | The imported row, verbatim (audit) | JSON | No | `{...}` | `models.py:114` — source evidence for the master row |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| known as | Supplier Alias | Zero or many | No | Alternate names/spellings/codes that resolve to this supplier (`supplier_aliases`, `models.py:121`) |
| carries | Brand (via Supplier-Brand link) | Zero or many | No | Brands this supplier distributes — the strongest catalogue→supplier corroborating signal (`supplier_brands`, `models.py:136`) |
| offers | Supplier Product | Zero or many | No | Its purchasing offerings of our Variants |
| supplies | Supplier Catalogue | Zero or many | No | Catalogues received from it |

#### Business rules

- **Resolution is multi-signal and refuses to guess when ambiguous.** `supplier_resolver.resolve()` scores: exact **code** 0.99 > exact **name/alias** 0.95 > **brand** 0.85 > **fuzzy name** (difflib × 0.80, ≥0.72). Name+brand agreement adds +0.10 synergy (`supplier_resolver.py:53-84`). If the top two are within 0.08, or the best < 0.70, it returns **no resolution** + a best-guess for a forced manual pick (`supplier_resolver.py:101-111`).
- **Supplier is the only entity that is ever *merged*.** Legacy/duplicate supplier rows are reconciled into a master: `ProductSupplier` links are reassigned, duplicate links dropped, and the legacy row **deactivated (kept for audit)**, never hard-deleted (`supplier_reconcile.py:66-83`). Products/SKUs are never merged (§4.7).
- Aliases and brand-links are unique per `(supplier, normalized value)` (`models.py:133,148`).

#### Example

Supplier `14` Hill's, code `HPI`?—(actual seed codes include `ALF, ARR, AVM, CVP, BPC, BGB, CAE, ETT, HPI`, `seed.py:24-34`); segment inferred; carries brand "Hill's"; alias "Hill's Pet Nutrition" → resolves to it; offers N Supplier Products.

---

### 4.4 Brand ⚠

*Classification: Canonical Master — **currently under-modelled** (no canonical table).*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Canonical Master (reference) |
| Business definition | The **manufacturer / product brand** a Product belongs to (e.g. "Hill's", "Zoetis", "Royal Canin"). A grouping above Product, independent of who supplies it. |
| Primary identifier | Brand ID **[intended]** |
| Business identifier | Normalized brand name |
| System of record | ⚠ **None today** — brand is a denormalized string (`Product.brand`, `CatalogueItem.brand`) plus the supplier-carries link (`SupplierBrand`) |
| Lifecycle/status | n/a (no entity) |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| brand_id | Surrogate identity | Integer | Yes **[intended]** | `31` | Does not exist |
| name | Display name | Text | Yes | `Hill's` | `Product.brand` string, `models.py:157` |
| normalized_name | Matching key | Text | Yes | `hills` | `SupplierBrand.normalized_brand`, `models.py:143` |
| is_fmcg | Fast-moving consumer good flag | Int 0/1 | No | `0` | `SupplierBrand.is_fmcg`, `models.py:144` |
| segment_hint | Typical vet/non-vet leaning | Text | No | `non_vet` | Inferable, not stored |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| groups | Product | Zero or many | No | Products under this brand |
| carried by | Supplier | Zero or many | No | Suppliers that distribute the brand (`supplier_brands` join) |

#### Business rules

- **Today brand is a string, not a reference.** A brand rename must fan out across every `Product.brand` and `SupplierBrand` row (`brands.py:111-119`) — there is no single record to edit. This is a structural gap (`data-conflation-audit.md` finding 3), not a supplier/brand *conflation* (that boundary is clean).
- Brand is a **supplier-resolution** signal (§4.3) but explicitly **not** a product-match signal (§4.7).

#### Example

"Hill's" appears as `Product.brand="Hill's"` on many Variants, as `SupplierBrand(supplier=14, brand="Hill's")`, and as `CatalogueItem.brand="Hill's"` — three denormalized copies with no canonical anchor. **Recommended:** promote to a `Brand` reference table (§8 relates).

---

### 4.5 Category

*Classification: Canonical Master (reference).*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Canonical Master (reference / rule-bearing) |
| Business definition | The **merchandising class** of a Product, which also carries the handling, channel, GP-floor and SKU-digit rules for that class. |
| Primary identifier | `category` name (natural primary key) |
| Business identifier | `category` name |
| System of record | Rosetta IMS (`category_rules`, `models.py:64`; seeded `seed.py:12-22`) |
| Lifecycle/status | Static reference; upserted by seed |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| category | The class name (PK) | Text | Yes | `Medicine` | `models.py:67` |
| gp_floor | Minimum acceptable gross-profit fraction | Decimal | Yes | `0.70` | `models.py:68` — a *classification* attribute; pricing logic is out of scope |
| storage_rule | clinic_only \| any | Text | Yes | `clinic_only` | `models.py:69` |
| channel_restriction | NULL \| clinic | Text | No | `clinic` | `models.py:70` |
| sku_digit | Leading digit for generated SKUs of this class | Text (1 char) | No | `5` | `models.py:71` |

**Seeded values** (`seed.py:12-22`): Medicine `0.70` clinic_only/clinic · Food `0.35` · Cat Litter `0.35` · Preventative / Supplement / Shampoo / Pet Hygiene / Others `0.40` · Not-For-Sale `0.00`. SKU digits (`sku_service.py:22-32`): Food `1`, Shampoo/Pet Hygiene/Cat Litter `4`, Medicine/Preventative/Supplement `5`, Not-For-Sale `6`, Others `7`.

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| classifies | Product | Zero or many | No | Products in this class (one category per Variant today) |
| drives | Canonical SKU minting | — | — | `sku_digit` is the leading digit of a new SKU (§4.7) |

#### Business rules

- `category` is **required on every Variant** (`Product.category` NOT NULL, `models.py:158`).
- The picker offers exactly the seeded list (`seed.py:11`).
- A category's `sku_digit` (data-driven) is preferred over the static fallback map when minting SKUs (`sku_service.py:64-69`).
- **Open question (§8 Q3):** whether one Product may belong to multiple Categories. Today: **single** category per Variant.

#### Example

`Medicine`: gp_floor 0.70, storage `clinic_only`, channel_restriction `clinic`, sku_digit `5` → medicine SKUs look like `5#######`.

---

### 4.6 Product ⚠

*Classification: Canonical Master — **currently collapsed into the Variant** (no distinct entity).*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Canonical Master (the family/parent concept) |
| Business definition | The **brand + product line** concept that groups its sellable sizes/flavours (e.g. "Hill's Science Diet Adult Chicken" as a family, with 2kg / 12kg variants beneath it). The stable "same product, different size" grouping. |
| Primary identifier | Product ID **[intended]** |
| Business identifier | Brand + normalized product name **[intended]** |
| System of record | ⚠ **None distinct** — today the `products` table stores one row per **Variant**, with the variant flattened into the name; there is no family row above it |
| Lifecycle/status | n/a (no entity) — see the interim `clientssot/families.py` name-normaliser |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| product_id | Surrogate family identity | Integer | Yes **[intended]** | `700` | Does not exist |
| name | Family name (size-independent) | Text | Yes | `Science Diet Adult Chicken` | Approximated by `families.py` normaliser |
| brand | Ref → Brand | Ref | Yes | Hill's | via string today |
| category | Ref → Category | Ref | Yes | Food | via string today |
| segment | vet \| non_vet | Text | No | `non_vet` | `Product.segment`, `models.py:160` |
| species | dog \| cat \| both \| other | Text | No | `dog` | `Product.species`, `models.py:161` |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| has variants | Product Variant / Canonical SKU | One or many | Yes **[intended]** | The sellable sizes/flavours |
| belongs to | Brand | Exactly one | Yes | Its brand |
| classified as | Category | One or many | Yes (one today) | Its merchandising class |

#### Business rules

- **Today Product and Variant are 1:1 and collapsed** — each catalogue row becomes exactly one `products` row, with the variant text flattened into the name (`catalogues.py:1319`; `data-conflation-audit.md` finding 1). This is a *deliberate, clean 1:1*, not a conflation — but it means **there is no family abstraction above the SKU**. Cross-variant roll-ups (e.g. combined demand) are approximated by a heuristic name-normaliser explicitly marked interim (`clientssot/families.py`).
- **Recommendation (§8):** introduce `Product` as the parent and make `Product Variant` its child, so "same product, different size" is a first-class relationship rather than a string heuristic.

#### Example

Conceptually, Product "Science Diet Adult Chicken" → Variants {2kg SKU `10010385`, 12kg SKU `10010386`}. Today those are two independent `products` rows with no shared parent; only the name and a normaliser tie them together.

---

### 4.7 Product Variant / Canonical SKU

*Classification: Canonical Master. **This is the `products` table** — the single most important canonical record.*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Canonical Master |
| Business definition | The **stock-identifiable item**: one specific size/flavour we hold, sell, cost and count. Identified by an internal **Canonical SKU**. This is what the rest of Rosetta (stock, demand, channels) points at. |
| Primary identifier | `sku_code` (unique, indexed) — the **Canonical SKU** |
| Business identifier | `sku_code` |
| System of record | Rosetta IMS (`products`, `models.py:151`) |
| Lifecycle/status | `status` = ACTIVE \| INACTIVE \| DISCONTINUED |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| **sku_code** | Canonical internal SKU | Text (8-digit), unique | Yes | `10010385` | `models.py:155` |
| name | Full item name (variant flattened in) | Text | Yes | `Science Diet Adult Chicken 2kg` | `models.py:156` |
| brand | Brand label (string today) | Text | No | `Hill's` | `models.py:157` |
| category | Merchandising class | Ref → Category | Yes | `Food` | `models.py:158` |
| subcategory | AI-detected functional/clinical class | Text | No | `antibiotic` | `models.py:159` |
| segment | vet \| non_vet (derived from supplier) | Text | No | `non_vet` | `models.py:160` |
| species | dog \| cat \| both \| other | Text | No | `dog` | `models.py:161` |
| uom | **Sell unit** (the unit you sell one of) | Text → Unit of Measure | No | `can` | `models.py:174` |
| pack_unit | **Buy unit** label (packaging unit) | Text → Unit of Measure | No | `bag` | `models.py:175` |
| min_sellable_qty | Smallest quantity a customer can buy (NOT a cost basis) | Integer | No | `24` | `models.py:164` |
| min_purchase_qty | Default purchase minimum (fallback MOQ) | Integer | No | `1` | `models.py:163` |
| weight_g | Net weight per sell-unit (**grams canonical**) | Decimal | No | `2000` | `models.py:180` |
| weight_unit | Display/source unit (kg default, lb) | Text | No | `kg` | `models.py:181` |
| rrp | Recommended retail price (reference) | Decimal | No | `205.0` | `models.py:162` |
| storage_rule | clinic_only \| any | Text | Yes | `any` | `models.py:176` |
| status | Lifecycle | Enum | Yes | `ACTIVE` | `models.py:177` |
| hero_sku | Merchandising flag | Int 0/1 | Yes | `0` | `models.py:178` |
| shopify/daysmart/hktv_status | Per-platform listing status (mirror) | Text | No | `active` | `models.py:167-169` — see §4.14 |
| shopify/daysmart/hktv_cost | Per-platform recorded COGS (mirror) | Decimal | No | `18.5` | `models.py:171-173` — mirror, not the cost source |
| last_manual_edit_at/by | Human-edit provenance (never set by sync) | date / name | No | `Desmond` | `models.py:182-183` |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| variant of | Product | Exactly one | Yes **[intended]** | Its family (collapsed today) |
| classified as | Category | Exactly one | Yes | Its class |
| of brand | Brand | Zero or one | No | Its brand |
| offered by | Supplier Product | Zero or many | No | Every supplier link that prices/packs it |
| mapped to | External Product Mapping | Zero or many | No | Its identity on Shopify / HKTV / DaySmart / Sheets |
| sourced from | Catalogue Item | Zero or many | No | The catalogue rows that created/updated it |

#### Business rules — Canonical SKU generation & immutability

- **Format:** 8-digit numeric string `= [1 category digit][7-digit zero-padded global sequence]` — `code = f"{digit}{suffix:07d}"` (`sku_service.py:73`). Example: Medicine (digit 5) + suffix 0010385 → `50010385`.
- **Leading digit:** `CategoryRule.sku_digit` if set (data-driven), else a static map (`sku_service.py:64-69`); unknown category with no digit → error (never a silent default).
- **Sequence:** a **single global ascending counter** shared by all categories — `max(existing suffix) + 1` (`_max_real_suffix`, `sku_service.py:42-51`), **not** per-category.
- **Reserved block:** suffixes ≥ 9,000,000 are a reserved sentinel range, excluded from the counter (`sku_service.py:38,49`); collision-guarded loop re-checks uniqueness (`:72-77`).
- **Minted once, at commit** (`assign_new_sku`, `catalogues.py:1314`); the match/OCR path never regenerates it.
- **Effectively immutable, but a privileged rename exists:** `PATCH /{sku}/sku-code` (`change_sku_code`, `products.py:833+`) is gated on capability `product_sensitive`, **409s on clash** (never merges), and cascades the change to denormalized copies (`CatalogueAuditEvent.sku_code`, `CatalogueItem.assigned_sku`), audit-logged as `product.sku_change`. No automated flow ever mutates a SKU.
- **Products/SKUs are never merged** (unlike suppliers). Canonical identity is enforced up-front by the unique `sku_code` + global counter; duplicate-avoidance happens *before* creation (already-verified item clearing, `catalogues.py:749-844`).

#### Business rules — Catalogue → Variant matching

`_find_matches` (`catalogues.py:952-1058`) proposes the top-3 matches for a Catalogue Item, in priority order:

1. **Exact barcode** → confidence **0.99**.
2. **Exact supplier SKU** (scoped to `(supplier_id, supplier_sku)`) → **0.95**.
3. **Fuzzy name** — overlap of significant tokens (words > 3 chars), threshold **≥ 0.65**; tie-breakers **+0.10** if `units_per_pack` matches and **+0.10** if `cost_price` is within ±15% of the known supplier cost.

**Brand and variant are NOT match signals** (brand is a *supplier*-resolution signal; variant is used only to build the name at assign time). Live **match** confidence is distinct from the item's **extraction** `confidence_score`.

#### Example

Variant `10010385` (Food → digit 1): name "Science Diet Adult Chicken 2kg", brand Hill's, category Food, segment non_vet, species dog, uom `can`? (here `bag`), weight_g 2000, status ACTIVE. Offered by Supplier Product (Hill's). Platform mirror columns populated by reconciliation once listed.

---

### 4.8 Unit of Measure ⚠

*Classification: Canonical Master (reference) — **currently free strings, no table**.*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Canonical Master (controlled vocabulary) |
| Business definition | The measurement units the domain distinguishes: **sell unit** (what a customer buys one of), **buy/pack unit** (supplier packaging), and **weight unit** (display). Correctly separating these is the crux of correct costing. |
| Primary identifier | UoM code **[intended]** |
| Business identifier | Unit symbol/name |
| System of record | ⚠ **None** — `Product.uom`, `Product.pack_unit`, `weight_unit` are free strings |
| Lifecycle/status | n/a |

#### Attributes / roles

| Role | Business meaning | Example values | Source |
|---|---|---|---|
| sell unit (`uom`) | The unit one of which is sold | tablet, ml, g, can, sachet | `models.py:174` |
| buy/pack unit (`pack_unit`) | The supplier packaging unit | box, bottle, strip, bag | `models.py:175` |
| weight unit (`weight_unit`) | Display unit for weight (**grams canonical**) | kg (default), lb | `models.py:181` |
| dimension | Count / volume / weight family | count, volume, weight | Implied by `catalogue_pack.py` grammar |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| measures | Product Variant (sell/pack/weight roles) | Zero or many | No | The units a Variant is sold/packed/weighed in |
| counts | Packaging Configuration | — | — | `units_per_pack` counts sell units |

#### Business rules

- **Weight is canonically grams**; `kg`/`lb` are display/source only, converted at ingest (`catalogue_pack._to_grams`).
- Sell unit ≠ buy unit ≠ weight unit, and only sell-unit counts (`units_per_pack`) may feed the cost divisor (§5, §4.10).
- Currently uncontrolled: nothing enforces a fixed vocabulary, so `tab`/`tablet`/`Tablet` can all appear. **Recommendation (§8-adjacent):** a small controlled UoM reference with a dimension, to make pack/weight math and validation robust.

#### Example

Variant `10010385`: sell unit `bag`? no — sold per `can` with `pack_unit=case`; weight_unit `kg` (display) with canonical `weight_g=2000`. A medicine: `uom=tablet`, `pack_unit=box`, `weight_g` per tablet.

---

### 4.9 Supplier Product

*Classification: Supplier Commercial. **The `product_suppliers` join — the pivot of the whole cost model.***

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Supplier Commercial |
| Business definition | **A specific supplier's purchasing offering of a specific Variant** — this supplier's own SKU/barcode for it, what they charge, how they pack it, how they require us to order it, and whether they currently have it. A Variant has 0..N of these; one is `is_primary`. |
| Primary identifier | Product-Supplier ID (surrogate) |
| Business identifier | `(product_id, supplier_id)` (unique) |
| System of record | Rosetta IMS (`product_suppliers`, `models.py:198`) |
| Lifecycle/status | `stock_status` = in_stock \| out_of_stock; `is_primary` 1/0 |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| id | Surrogate identity | Integer | Yes | `5501` | `models.py:202` |
| product | The Variant offered | Ref → Variant | Yes | `10010385` | `models.py:203` |
| supplier | The offering supplier | Ref → Supplier | No | Hill's (14) | `models.py:204` |
| supplier_sku | Supplier's own code for it | Text | No | `10447` | `models.py:205` |
| barcode | Supplier barcode (also a platform-match key) | Text | No | `9310022...` | `models.py:206` |
| **basic_cost** | The supplier's **whole-pack** wholesale cost — the single number all margin math divides | Decimal (HKD assumed) | No | `240.0` | `models.py:207` — see §4.11 |
| units_per_pack | **Cost-basis** sell-units the price covers (see §4.10) | Integer | No | `24` | `models.py:210` |
| is_primary | The link margin math uses by default | Int 0/1 | Yes | `1` | `models.py:221` |
| cost_source | Provenance/priority of the cost | Enum: catalogue > invoice_matched > po_issued > manual > sheet | Yes | `catalogue` | `models.py:224` |
| cost_source_ref | Pointer to the evidence | Text | No | `catalogue_import:42` | `models.py:225` |
| cost_updated_at | When the cost last changed | ISO datetime | No | `2026-07-21T...` | `models.py:226` |
| basic_cost_sheet | Shadow: last cost seen from Sheet sync (drift detect) | Decimal | No | `238.0` | `models.py:217` |
| pack_source | Provenance of the pack size | Enum: sheet \| manual \| catalogue | Yes | `catalogue` | `models.py:214` |
| uom_verified_at/by | Pack size human-confirmed → locked from sync | date / name | No | `Desmond` | `models.py:215-216` |
| order_increment_qty/uom | Must order in multiples of N (ordering only) | Integer + Text | No | `24 can` | `models.py:240-241` |
| minimum_order_qty/uom | Supplier's real MOQ (ordering only) | Integer + Text | No | `48 can` | `models.py:242-243` |
| minimum_order_source | Provenance of the MOQ | Enum (app-level) | No | `catalogue` | `models.py:244` |
| pricing_note | Free-text: what the price covers | Text | No | `Price per case of 24 cans` | `models.py:245` |
| stock_status / reported_out_at / expected_restock_at | Supplier OOS tracking | Enum + dates | No | `in_stock` | `models.py:229-234` |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| offers | Product Variant / Canonical SKU | Exactly one | Yes | The item being offered |
| offered by | Supplier | Zero or one | No | The offering supplier |
| has packaging | Packaging Configuration | Exactly one (embedded) | Yes | Its cost-basis pack (§4.10) |
| has price | Supplier Price | Exactly one (current) | Yes | Its published cost (§4.11) |
| has bulk terms | MBB Term | Zero or many | No | Conditional bulk-buy terms (§4.12) |
| has OOS history | Supplier Stock Event | Zero or many | No | Out-of-stock periods (`supplier_stock_events`, `models.py:255`) |

#### Business rules

- **Cost lives here, not on the Variant.** There is no single `Product.cost`; "the cost" means the `is_primary` link's `basic_cost`. A Variant bought from two suppliers can have two different costs and two different pack sizes.
- **Cost basis is `units_per_pack` and only that** (§5). `min_sellable_qty`, `minimum_order_qty`, and `order_increment_qty` **must never** divide `basic_cost` (`product-vs-supplier-fields.md`).
- **Provenance gates overwrites:** only `cost_source='sheet'` / `pack_source='sheet'` values are overwritable by Sheet sync; catalogue(OCR)/manual values are protected (`sheet_sync.py:315`). Priority: catalogue > invoice_matched > po_issued > manual > sheet.
- **A quantity requires its UoM:** setting `order_increment_qty`/`minimum_order_qty` requires the matching `_uom` (400 otherwise, `products.py::_validate_supplier_terms`).
- Unique per `(product_id, supplier_id)` (`models.py:200`).

#### Example

Supplier Product: Variant `10010385` × Supplier 14, `supplier_sku=10447`, `basic_cost=240`, `units_per_pack=24` (a case of 24 cans) → effective unit cost **HK$10.00/can**; `minimum_order_qty=48 can`, `order_increment_qty=24 can`, `pricing_note="Price per case of 24 cans"`, `cost_source=catalogue`, `cost_source_ref=catalogue_import:42`, `is_primary=1`.

---

### 4.10 Packaging Configuration

*Classification: Supplier Commercial — **currently embedded on Supplier Product** (not a separate table).*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Supplier Commercial (offering-specific) |
| Business definition | The **structured** answer to "how many sellable units does this supplier's price cover, and in what pack unit?" — derived from, but distinct from, the raw pack-size text. It is the cost-basis divisor and is **offering-specific** (each Supplier Product may pack differently). |
| Primary identifier | (embedded on Supplier Product) |
| Business identifier | Supplier Product + units_per_pack |
| System of record | Rosetta IMS — `ProductSupplier.units_per_pack` (structured) + `CatalogueItem.pack_size` (raw evidence) |
| Lifecycle/status | `pack_source` provenance; locked once `uom_verified_at` set |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| units_per_pack | Sell-units the price covers (the cost divisor) | Integer | Yes (for multipacks) | `24` | `models.py:210` |
| pack_unit | The buy-unit label (on the Variant) | Text → UoM | No | `case` | `models.py:175` |
| raw_pack_text | The **printed** pack-size string (source evidence) | Text | No | `24/2.9 oz` | `CatalogueItem.pack_size`, `models.py:451` |
| pack_source | Provenance | Enum: sheet \| manual \| catalogue | Yes | `catalogue` | `models.py:214` |
| verified_at/by | Human confirmation → locked from sync | date / name | No | `Desmond` | `models.py:215-216` |
| net_weight_g | Net weight per sell-unit, parsed from pack text | Decimal | No | `82` | `models.py:473`; `catalogue_pack.py` |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| configures | Supplier Product | Exactly one | Yes | The offering it packs |
| derived from | Catalogue Item (raw pack text) | Zero or one | No | The printed string it was parsed from |
| counts in | Unit of Measure | Exactly one | Yes | Counts sell-units |

#### Business rules

- **Structured, not text-driven, for calculation.** `units_per_pack` is the *only* value that feeds the cost divisor; the raw `pack_size` string is preserved as evidence but never math'd directly. A deterministic guard (`catalogue_pack.corrected_units_per_pack`, `catalogue_pack.py:104`) detects when `units_per_pack` is actually a **mis-read weight/volume** (e.g. "4000" from "4kg") and proposes `1`, but **holds count-unit ambiguities** (e.g. "1.06oz" == 30 sachets) for human review rather than auto-fixing.
- **Never assume every pack holds the same count.** Pack size is per-offering; two suppliers of the same Variant may have different `units_per_pack`.
- **A per-unit contract sets `units_per_pack = 1`** at ingest (`basis: per_unit` → const 1), so a per-unit price is never divided (`catalogue_contract.py:141`).
- ⚠ **NULL vs 1 is not distinguished** — a genuine multipack with a missed pack size looks like a single unit and computes GP on a pack-as-unit cost (`data-conflation-audit.md` finding 6). Modelling should treat "unknown pack" as distinct from "pack of 1".

#### Example

Supplier Product above: `raw_pack_text="24 cans/case"` → `units_per_pack=24`, `pack_unit=case`, `pack_source=catalogue`; net weight parsed per can. Effective unit cost = `basic_cost / 24`.

---

### 4.11 Supplier Price ⚠

*Classification: Supplier Commercial — **currently a single scalar; missing currency, explicit basis, effective period and history.***

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Supplier Commercial |
| Business definition | A **supplier's published cost** for a Supplier Product, with the three facts every price needs: **currency**, **price basis** (per sell-unit vs per pack), and an **effective period** (from when, until when). Price changes should append history, not overwrite. |
| Primary identifier | Supplier Price ID **[intended]** |
| Business identifier | Supplier Product + effective_from **[intended]** |
| System of record | ⚠ Rosetta IMS stores only the **current** value: `ProductSupplier.basic_cost` + `cost_source`/`cost_updated_at` + one `basic_cost_sheet` shadow |
| Lifecycle/status | Current-only today; **no timeline** |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| amount | The published cost | Decimal | Yes | `240.0` | `ProductSupplier.basic_cost`, `models.py:207` |
| **currency** | Currency of the amount | ISO code | Yes **[intended]** | `HKD` | ⚠ **Not stored** — HKD assumed, symbols stripped at ingest (`extraction_service.py:282`) |
| **price_basis** | per_sell_unit \| per_pack | Enum | Yes **[intended]** | `per_pack` | ⚠ **Implicit** — encoded only via `units_per_pack`; explicit only transiently in the contract (`catalogue_contract.py:130`) |
| pack_ref | The Packaging Configuration the basis refers to | Ref | Yes | 24/case | via `units_per_pack` |
| **effective_from** | When this price takes effect | Date | Yes **[intended]** | `2024-04-01` | ⚠ **Not stored** — printed on the catalogue, dropped |
| **effective_to** | When it was superseded | Date | No **[intended]** | `2026-06-30` | ⚠ **Not stored** |
| source | Provenance/priority | Enum: catalogue > invoice_matched > po_issued > manual > sheet | Yes | `catalogue` | `cost_source`, `models.py:224` |
| source_ref | Evidence pointer | Text | No | `catalogue_import:42` | `models.py:225` |
| changed_at | When the current value was set | ISO datetime | No | `2026-07-21T...` | `cost_updated_at`, `models.py:226` |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| prices | Supplier Product | Exactly one | Yes | The offering priced |
| basis of | Packaging Configuration | Exactly one | Yes | What the amount is "per" |
| evidenced by | Catalogue Item / Review Decision | Zero or one | No | Where the value came from |
| superseded by | Supplier Price (next) | Zero or one | No **[intended]** | The next price in the timeline |

#### Business rules

- **`basic_cost` is always a whole-pack cost** by convention; the effective per-sell-unit cost is **derived at read time** as `basic_cost / units_per_pack` (only when `> 1`) via `get_unit_cost` — never stored (`pricing_service.py:47-55`, `transform_engine.py:79`). This is deliberate, to avoid a stored per-box figure being mis-divided.
- **Provenance protects the value** from being clobbered by a lower-priority sync (§4.9).
- ⚠ **Gaps against "every price has currency, basis, effective period; history preserved":**
  - **No currency column** anywhere — HKD is assumed and symbols are stripped at ingest (confirmed across cost/price fields).
  - **No explicit price basis** — per-unit vs per-pack is inferred from `units_per_pack`; the same key `basic_cost` even means *pack* cost in some endpoints and *unit* cost in others (label footgun A3, `data-conflation-audit.md`).
  - **No effective-dating / history** — only the current value + a single shadow + a change timestamp are kept. The catalogue's printed effective date is discarded. Price history exists only incidentally in `audit_log` and per-reparse `eff_cost_before/after` diffs, not as a queryable timeline.
- **Recommendation:** model Supplier Price as an effective-dated, currency-bearing, basis-explicit record with append-on-change history (see §8 and §9).

#### Example

Today: `basic_cost=240`, `cost_source=catalogue`, `cost_updated_at=2026-07-21`. Intended: `{amount:240, currency:HKD, price_basis:per_pack(24), effective_from:2024-04-01, source:catalogue, source_ref:catalogue_import:42}`, with the previous HK$238 row retained as history.

---

### 4.12 MBB Term (Max-Bulk-Buy)

*Classification: Supplier Commercial. **MBB = "Max-Bulk-Buy"** — confirmed from the repository (`models.py:218,271`, `mbb-per-supplier-margin.md`); not invented here.*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Supplier Commercial |
| Business definition | **A conditional bulk-buy commercial term** on a Supplier Product: buy/spend enough to unlock a better effective per-unit cost. A Supplier Product has 0..N typed terms; the domain compares each term's outcome. |
| Primary identifier | MBB Term ID (surrogate) |
| Business identifier | Supplier Product + kind + threshold |
| System of record | Rosetta IMS (`mbb_terms`, `models.py:271`) |
| Lifecycle/status | Ordered by `sort_order`; each term independently valid |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| id | Surrogate identity | Integer | Yes | `88` | `models.py:278` |
| kind | The term type | Enum: buy_x_get_y \| spend_discount \| tier \| flat_unit_cost | Yes | `tier` | `models.py:280` |
| min_qty | Units to unlock ("buy X") | Integer | No | `10` | `models.py:282` |
| min_spend | HK$ to unlock | Decimal | No | `5000` | `models.py:283` |
| free_qty | buy_x_get_y: units free ("get Y") | Integer | No | `1` | `models.py:285` |
| discount_pct | spend_discount: fraction off | Decimal | No | `0.10` | `models.py:286` |
| unit_cost | tier / flat_unit_cost: explicit per-sell-unit cost | Decimal | No | `9.20` | `models.py:287` |
| note | Human label | Text | No | `10+ cases @ 9.20` | `models.py:288` |
| sort_order | Ordering | Integer | Yes | `0` | `models.py:289` |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| term of | Supplier Product | Exactly one | Yes | The offering the term applies to |

#### Business rules

- **Thresholds and outcomes are explicit quantities/units, never free-text.** Unlock via `min_qty` (units) and/or `min_spend` (HK$); benefit is exactly one of `free_qty` / `discount_pct` / `unit_cost` matching `kind`.
- **Effective per-unit cost is derived from the base unit cost**, per kind (`pricing_service._term_unit_cost`, `transform_engine.py`):
  - `buy_x_get_y` → `base × min_qty / (min_qty + free_qty)`
  - `spend_discount` → `base × (1 − discount_pct)`
  - `tier` / `flat_unit_cost` → the explicit `unit_cost`
- **Standard cost and MBB-qualified cost are distinct outcomes.** `margin_range` emits both a basic margin (from `basic_cost`) and an MBB margin (cheapest achievable term) per channel; `best_mbb` picks the cheapest term (`pricing_service.py:86-98`).
- Replaces the old flat `mbb_*` scalars (dropped), which could hold only one term — the relational model holds many and stores no mis-dividable per-box number (`models.py:218-220,271-275`).

#### Example

Supplier Product (24-can case, base unit HK$10.00) has two terms: `tier` (min_qty 10 cases, unit_cost 9.20) and `buy_x_get_y` (min_qty 10, free_qty 1 → effective 10 × 10/(10+1) = HK$9.09). `best_mbb` → HK$9.09.

> **Note — Hill's settlement discounts are NOT MBB.** Hill's "Net Invoice @0/4/6%" columns are prompt-payment/settlement discounts, retained as catalogue evidence but explicitly **not** mapped to supplier cost or to MBB terms in the Pydantic supplier-source contract. Only genuine quantity/spend-conditional bulk deals become MBB terms.

---

### 4.13 Landed Cost Assessment ⚠

*Classification: Supplier Commercial (cost) — **does NOT exist in Rosetta today**; documented as a deferred/excluded entity with a recommendation.*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Supplier Commercial (cost) — **candidate / out-of-scope today** |
| Business definition | The **true cost to land goods**: supplier cost **plus** freight, duty, import/customs and handling charges, apportioned to a sell-unit. Distinct from both supplier price and channel selling fees. |
| Primary identifier | n/a |
| Business identifier | n/a |
| System of record | ⚠ **None** — no freight/duty/customs/import-charge fields exist in the backend |
| Lifecycle/status | n/a |

#### Repository evidence

- A backend search for `landed \| freight \| duty \| shipping \| customs \| tariff \| clearance \| import_charge` finds **no such cost fields**. The word "landed" appears but is explicitly redefined to mean *just the supplier unit cost* (`pricing_service.py:442,545`: "landed = supplier unit cost").
- The only add-ons above supplier cost are **channel-specific selling fees applied per channel in the margin** — Shopify SF-Express logistics by shipping weight, HKTV `channel_fee_pct` — and these enter `net_margin` only, **never** the cost itself (`pricing_service.py:176-185`).

#### Recommendation (decision required — §8 Q4)

Rosetta's catalogue/cost domain currently owns **supplier cost**, not landed cost. **Default recommendation:** keep Landed Cost Assessment **out** of the canonical catalogue domain for now — it is a downstream costing concern (like the retired invoice reconciliation) — and add it later as a distinct cost layer *on top of* Supplier Price if/when import charges must feed margin. Do **not** overload `basic_cost` with freight/duty. Documented here so the ERD explicitly shows it as a deliberate exclusion, not an oversight.

---

### 4.14 External Product Mapping ⚠

*Classification: Integration — **currently scattered per-platform columns; no mapping table and no external IDs stored.***

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Integration |
| Business definition | The **link between a canonical Variant and its identity in an external system** (Shopify, HKTV Mall, DaySmart POS, ShopToPlus, Google Sheets), so IMS can reconcile status/cost and push/pull the right record. |
| Primary identifier | Mapping ID **[intended]** |
| Business identifier | (Variant + external_system + external_id) **[intended]** |
| System of record | ⚠ **None** — external identity is scattered as per-platform status/cost columns on `products`; the universal join key is `sku_code` |
| Lifecycle/status | Per-platform status columns act as a partial proxy |

#### Attributes (intended) vs current reality

| Attribute | Business meaning | Intended | Current reality | Source |
|---|---|---|---|---|
| external_system | Which platform | Enum: shopify \| hktv \| daysmart \| shoptoplus \| sheets | Encoded as column prefixes / channel enum | `models.py:167-173,302` |
| external_id | The platform's own product/variant identity (GID, handle, listing id) | Text | ⚠ **Not stored** — Shopify GIDs/variant-ids read transiently then discarded | agent-confirmed; `scripts/extract_platform_items_live.py:15-33` |
| join_key | The key IMS actually joins on | `sku_code` | `sku_code` ↔ platform SKU / sheet "SKU ID" | `sheet_sync.py:142,215` |
| listing_status | Per-platform listing state | active/online/… | `shopify_status`/`daysmart_status`/`hktv_status` | `models.py:167-169` |
| platform_cost | Platform-recorded COGS (mirror) | Decimal | `shopify_cost`/`daysmart_cost`/`hktv_cost` | `models.py:171-173` |
| selling_price | Per-channel price | Decimal | `product_channels.selling_price` (channel ∈ clinic/shopify/hktv) | `models.py:304` |
| units_per_listing | Sell-units per platform listing | Integer | `product_channels.units_per_listing` | `models.py:307` |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| maps | Product Variant / Canonical SKU | Exactly one | Yes | The canonical item mapped |
| to system | (external system) | Exactly one | Yes | Shopify / HKTV / DaySmart / ShopToPlus / Sheets |

#### Business rules / current mechanics

- **`sku_code` is the universal join key** to every external system: Shopify variant `sku`, HKTV "SKU ID", Google Sheet "SKU ID" (`sheet_sync.py:142,400-422`). It is **`sku_code`, not `supplier_sku`**.
- **No external IDs are persisted** — no Shopify `gid://`/handle/variant-id, no HKTV/DaySmart listing id. They are read only transiently for matching and discarded. This makes re-matching SKU-string-dependent and fragile.
- **Platform quirks:** DaySmart has **no channel of its own** — it feeds the **clinic** channel (price + a cost fallback). **ShopToPlus is not a product identity at all** — it is a Shopify app, so it rides Shopify's identity and contributes only **warehouse stock** ("STP SOH" → `stock_levels` warehouse). There is no `stp_*` column.
- **Sole writer of the platform status/cost mirror:** `scripts/reconcile_platform_skus.py` (live pulls) populates the six `*_status`/`*_cost` columns; the Google-Sheet sync does not touch them. `consolidate_platform_skus.py` only *creates* shell products for unmatched platform items.
- **Recommendation:** introduce a real `External Product Mapping` table `(variant, external_system, external_id, join_key, status, last_synced)` so external identity is durable and not SKU-string-fragile.

#### Example

Variant `10010385` intended mappings: `{shopify: gid://shopify/ProductVariant/…}`, `{hktv: <listing id>}`, `{daysmart: <item id> → clinic}`, `{sheets: "SKU ID"=10010385}`. Today only the *effects* are stored (`shopify_status=active`, `shopify_cost=18.5`, clinic/shopify/hktv channel prices), joined by `sku_code`; the external IDs themselves are absent.

---

### 4.15 Review Decision

*Classification: Governance.*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Governance |
| Business definition | **A human curation decision** during catalogue onboarding or re-parse — confirm a match, assign a new SKU, edit a field, reject a row, confirm the supplier, or confirm a re-parse diff. It is the gate between source evidence and canonical data. |
| Primary identifier | Audit event ID (surrogate) |
| Business identifier | (Item + action + timestamp) |
| System of record | Rosetta IMS (`catalogue_audit`, `models.py:488`; re-parse confirms in `reparse_change`) |
| Lifecycle/status | Append-only (never updated) |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| id | Surrogate identity | Integer | Yes | `4400` | `models.py:495` |
| action | The decision | Enum: confirm_match \| assign_new \| edit \| reject \| supplier_confirm | Yes | `confirm_match` | `models.py:500` |
| item | The Catalogue Item decided | Ref → Catalogue Item | No | `9001` | `models.py:496` |
| import | The owning catalogue | Ref → Supplier Catalogue | No | `42` | `models.py:497` |
| product / sku_code | The Variant affected (once it exists) | Ref / Text | No | `10010385` | `models.py:498-499` |
| actor | Who decided (snapshotted) | user_id + username + display_name | Yes | `Desmond` | `models.py:501-503` — survives rename/delete |
| details | before/after, reason, match target | JSON | No | `{cost:{old:238,new:240}}` | `models.py:504` |
| created_at | When | ISO datetime | Yes | `2026-07-21T...` | `models.py:505` |

For re-parse, the equivalent decision is a **confirmed `ReparseChange`** (one field old→new, with `affects_cost` + `eff_cost_before/after`, status pending→confirmed/rejected/stale, `models.py:527-546`).

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| decides | Catalogue Item | Zero or one | No | The evidence acted on |
| affects | Product Variant / Supplier Product | Zero or one | No | The canonical record created/changed |
| made by | (User) | Exactly one | Yes | The accountable human (snapshotted) |
| recorded as | Audit Event | Exactly one | Yes | Every decision is also an audit event |

#### Business rules

- **Every commit is a decision, attributed and audited** (`catalogues.py` review actions → `audit.log_event`).
- **Actor identity is snapshotted** so the trail survives user rename/deactivation.
- **Cost-affecting decisions are guarded**: re-parse re-verifies the live value hasn't drifted before writing (else `stale`, skipped); a confirmed re-parse edit sets provenance to `manual`.
- ⚠ **Bulk auto-approve is a weak point:** `match_confident` can promote OCR matches across the whole queue with an unclamped `min_confidence`, stamping the clicker (not the vetter) as reviewer, and overwriting (not gap-filling) cost (`data-conflation-audit.md` finding 7). The documented `catalogue_cost_staging` approval step is **dead code**.

#### Example

`confirm_match` by Desmond on item 9001 → Variant `10010385`, details `{supplier_sku:{old:null,new:"10447"}, cost:{old:238,new:240}}`, at 2026-07-21T09:05. Simultaneously written as an Audit Event.

---

### 4.16 Audit Event

*Classification: Governance.*

#### Entity overview

| Property | Definition |
|---|---|
| Classification | Governance |
| Business definition | **An append-only record of any material system or user action** — logins, user-management, product/catalogue/reference edits, sheet syncs, scans, re-parses. The system-wide accountability log. |
| Primary identifier | Event ID (surrogate) |
| Business identifier | (action + entity + timestamp) |
| System of record | Rosetta IMS — **two parallel logs**: general `audit_log` (`models.py:26`) and catalogue-specific `catalogue_audit` (`models.py:488`) |
| Lifecycle/status | Append-only (never updated) |

#### Attributes

| Attribute | Business meaning | Type/format | Required? | Example | Rules/source |
|---|---|---|---|---|---|
| id | Surrogate identity | Integer | Yes | `77001` | `models.py:32` |
| created_at | When | ISO datetime | Yes | `2026-07-21T...` | `models.py:33` |
| action | Event type | Text | Yes | `product.update` / `login.success` | `models.py:34` |
| actor | Who (snapshotted) | user_id/username/display_name/role | No | `Desmond` | `models.py:35-38` |
| entity_type / entity_id / entity_label | What was affected | Text | No | `product` / `10010385` | `models.py:39-41` |
| details | before/after diff, reason | JSON | No | `{...}` | `models.py:42` |
| ip / user_agent | Request context | Text | No | `…` | `models.py:43-44` |

#### Relationships

| Relationship | Target entity | Cardinality | Required? | Business meaning |
|---|---|---|---|---|
| about | any catalogue entity | Zero or one | No | The affected record (loose ref by type+id) |
| generalizes | Review Decision | — | — | A Review Decision is a catalogue-specific audit event |

#### Business rules

- **Append-only; actor snapshotted** — one row per event, never updated.
- ⚠ **Two audit systems run in parallel** (`catalogue_audit` for review actions via `services/audit.py`; `audit_log` for scans/re-parse/general via `services/audit_log.py`). The dictionary treats them as one conceptual **Audit Event** with two physical stores — a consolidation candidate (§7).

#### Example

`audit_log`: `{action:"product.sku_change", entity_type:"product", entity_id:"10010385", actor:"Desmond", details:{old:"…",new:"10010385"}}`.

---

## 5. Critical distinctions

| # | Distinction | The line that must not blur |
|---|---|---|
| 1 | **Supplier Catalogue vs Catalogue Item** | The *file* (one upload, provenance, `source_ref`) vs one *row* extracted from it. One catalogue → many items. |
| 2 | **Catalogue Item vs Supplier Product** | A Catalogue Item is *preserved source evidence* (a claim, awaiting review). A Supplier Product is the *canonical commercial offering* it may become on commit. The item's `cost_price` is evidence; the Supplier Product's `basic_cost` is canonical. |
| 3 | **Product vs Product Variant** | Product = the family (brand+line). Variant = one stock-identifiable size/flavour with a Canonical SKU. **Today they are collapsed 1:1** — there is no family entity; this is the headline modelling gap. |
| 4 | **Product Variant vs Supplier Product** | The Variant is *what it is* (identity, one record). The Supplier Product is *how one supplier offers it* (cost/pack/SKU, 0..N per Variant). Cost lives on the Supplier Product, never on the Variant. |
| 5 | **Canonical SKU vs supplier SKU vs external SKU** | Canonical SKU = internal `sku_code` (8-digit, IMS-minted, unique). Supplier SKU = the vendor's code (`supplier_sku`, scoped per supplier). External SKU = a platform's identity (Shopify/HKTV/DaySmart) — today only joined *by* `sku_code`, never stored as its own id. Three different namespaces; never cross them. |
| 6 | **Raw pack text vs structured Packaging Configuration** | `pack_size` ("24/2.9 oz") is preserved evidence. `units_per_pack` (24) is the structured cost-basis divisor. Only the structured value does math; the text is never divided directly. |
| 7 | **Supplier Price vs normalized unit cost vs landed cost** | `basic_cost` = whole-pack supplier price. Normalized **unit cost** = `basic_cost / units_per_pack` (derived at read time). **Landed cost** (supplier + freight/duty) **does not exist** in Rosetta — "landed" in code means just the unit cost. Three different numbers; two exist, one is absent by design. |
| 8 | **Standard price vs MBB-qualified outcome** | The standard cost (`basic_cost` → unit cost) is unconditional. An MBB-qualified cost applies **only when a quantity/spend threshold is met** and is a *derived* per-unit outcome of a typed term. Both are surfaced side by side; neither overwrites the other. |
| 9 | **Source evidence vs canonical mastered data** | `catalogue_imports` / `catalogue_items` (evidence, preserved, never cleaned in place) vs `products` / `product_suppliers` (canonical, human-committed). Joined only by the commit-time bridge (`matched_product_id` / `assigned_sku`). |

---

## 6. End-to-end example

One Hill's catalogue row, all the way through the conceptual model.

1. **Source file → Supplier Catalogue.** `Hill's Science Diet Price List.pdf` (printed "Effective 1 APR 2024") is uploaded → `CatalogueImport 42`, format `pdf`, raw bytes persisted at `source_ref`, supplier resolved to **Hill's (14)** at 0.95 (`confirmed`). *(Its effective date is printed but not captured — gap.)*
2. **Extracted row → Catalogue Item.** Row "Science Diet · Adult · Chicken · 2kg" → `CatalogueItem 9001`: `supplier_sku=10447`, `raw_description` (range·lifestage·desc joined), `pack_size="2kg"` (raw), `cost_price=<Gross Wholesale>`, `rrp`, `species=dog` (from the "Canine" section banner), `segment=non_vet` (Science Diet), `category=Food` (const), `brand="Hill's"` (const), `units_per_pack=1` (contract `basis: per_unit`), `weight_grams=2000` (parsed "2kg"), `confidence_score=0.82`, `review_status=pending`.
3. **Resolve to Supplier Product.** On human confirm, a `ProductSupplier` link (Hill's × the Variant) is written: `supplier_sku=10447`, `basic_cost=<Gross Wholesale>`, `cost_source=catalogue`, `cost_source_ref=catalogue_import:42`, `pack_source=catalogue`, `is_primary=1`.
4. **Link to canonical Product Variant.** Matched to an existing Variant (barcode/supplier-SKU/fuzzy-name) **or** `assign_new_sku` mints one: Food → digit `1` → e.g. **`10010385`**. `CatalogueItem.matched_product_id`/`assigned_sku` stamped now (and only now).
5. **Raw pack text → Packaging Configuration.** `pack_size="2kg"` (weight, not a count) → `units_per_pack=1`, `pack_unit=bag`, net `weight_g=2000`. The 2kg is a per-unit weight, **not** a cost divisor.
6. **Published cost → Supplier Price.** `basic_cost=<Gross Wholesale>` becomes the current price. *Intended:* `{amount, currency:HKD, price_basis:per_unit, effective_from:2024-04-01, source:catalogue}` with history. *Today:* just the scalar + `cost_updated_at`.
7. **Conditional bulk info → MBB Term.** Hill's has bulk deals only where quantity/spend-conditional; those become typed `MbbTerm` rows. The "Net Invoice @0/4/6%" settlement-discount columns are **retained as evidence but not** mapped to cost or MBB.
8. **External identities → External Product Mapping.** Once live, `sku_code=10010385` joins to the Shopify variant `sku`, the HKTV "SKU ID", the DaySmart item (feeding the **clinic** channel), and the Google Sheet "SKU ID". `reconcile_platform_skus.py` fills `shopify_status/…_cost` etc. Warehouse stock arrives via ShopToPlus "STP SOH". *(The external IDs themselves are not stored — join is by SKU string.)*
9. **Decisions & changes → Review Decisions + Audit Events.** The confirm is a `confirm_match` **Review Decision** (attributed to the reviewer, with a before/after `details` diff) and simultaneously an **Audit Event**. A later cost change via re-parse is a confirmed `ReparseChange` (re-verified against the live value, `affects_cost=1`).

---

## 7. Repository alignment findings

Only gaps supported by repository evidence are listed. File references are indicative.

| Concept | Intended domain meaning | Current repository representation | Gap or conflict | Recommended decision |
|---|---|---|---|---|
| **Product vs Variant** | Family (Product) has many Variants (SKUs) | One `products` row per SKU; variant flattened into name (`catalogues.py:1319`) | **No family entity above the SKU**; cross-variant roll-ups are a name heuristic (`clientssot/families.py`) | Introduce `Product` parent; make Variant its child. (Clean 1:1 today, so additive.) |
| **Brand** | Canonical reference | String `Product.brand` + `SupplierBrand` join (`models.py:157,136`) | **No canonical Brand table**; rename fans out across strings (`brands.py:111-119`) | Promote Brand to a reference table; keep `SupplierBrand` as the carries-link. |
| **Unit of Measure** | Controlled vocabulary w/ dimension | Free strings `uom`/`pack_unit`/`weight_unit` | No enforced vocabulary; `tab`/`tablet` variants possible | Add a small UoM reference (symbol + dimension). |
| **Supplier Price — currency** | Every price has a currency | Bare `Float` cost/price fields; HKD assumed, symbols stripped (`extraction_service.py:282`) | **No currency column** anywhere | Add `currency` (default HKD) to every priced record. |
| **Supplier Price — basis** | Explicit per_unit vs per_pack | Implicit via `units_per_pack`; `basic_cost` means pack in some endpoints, unit in others (`products.py:473` vs `:302`) | **No explicit basis**; a naming footgun (A3) | Store explicit `price_basis`; disambiguate the `basic_cost` label. |
| **Supplier Price — history/effective period** | Effective-dated, append-on-change | Current value + one `basic_cost_sheet` shadow + `cost_updated_at` (`models.py:217,226`) | **No price timeline**; catalogue effective date discarded | Add effective-dated Supplier Price history. |
| **Packaging — unknown vs 1** | "unknown pack" ≠ "pack of 1" | `units_per_pack` NULL/≤1 both return pack cost as unit cost (`transform_engine.py:79`) | **NULL≡1 ambiguity**; data-grade ignores pack presence (`pricing_service.py:390-400`) | Treat NULL distinctly; flag multipacks with no pack size. |
| **Landed Cost** | Supplier cost + freight/duty | Absent; "landed" == supplier unit cost (`pricing_service.py:442`) | **No landed-cost concept** | Keep out of catalogue domain for now (§8 Q4); add as a separate layer later if needed. |
| **External Product Mapping** | Durable per-system identity | Per-platform status/cost columns on `products`; join by `sku_code`; external IDs discarded | **No mapping table; no external IDs stored**; SKU-string-fragile | Add `External Product Mapping` table with real external IDs. |
| **Cost promotion review** | OCR cost reviewed before canonical | `match_confident` bulk-promotes with unclamped confidence; `catalogue_cost_staging` unused (`database.py:312`) | **Auto-approve edge**; documented staging is dead code | Clamp confidence; gap-fill not overwrite; or wire a real staging step. |
| **Audit Event** | One accountability log | Two stores: `audit_log` + `catalogue_audit` | **Parallel audit systems** | Model as one Audit Event; consider consolidating stores. |
| **Invoice/landed reconciliation** | (procurement, out of scope) | Retired from IMS (`models.py:208`) | None — correctly excluded | Confirm it stays in procurement, not catalogue. |

**Clean boundaries confirmed (no action):** Product≠SKU (deliberate 1:1), SKU≠Supplier SKU (distinct, scoped), Supplier≠Brand (real relationship), Catalogue Item≠Product (commit-gated bridge), core Cost≠Unit Cost (routes through `get_unit_cost`) — per `data-conflation-audit.md`.

---

## 8. Open questions and decisions required

Each question has a **recommended default** and the **consequence** of adopting it.

| # | Question | Recommended default | Consequence |
|---|---|---|---|
| **Q1** | **MBB — official meaning & behaviour?** | **MBB = "Max-Bulk-Buy"** (confirmed from `models.py:218,271` + `mbb-per-supplier-margin.md` — not invented). Keep the four typed kinds (`buy_x_get_y`, `spend_discount`, `tier`, `flat_unit_cost`) with derived per-unit outcomes. | ERD models MBB Term as a typed 0..N child of Supplier Product; no expansion is guessed. **Confirm the expansion wording with Ops/Austin.** |
| **Q2** | **Supplier SKU: attribute or historical entity?** | Keep `supplier_sku` as an **attribute of Supplier Product** (no separate identifier-history entity yet). | Simpler model; matches current use. If suppliers re-use/rotate codes and history matters, revisit — the privileged SKU-rename already cascades, suggesting low churn. |
| **Q3** | **Can one Product belong to multiple Categories?** | **No — single category per Product/Variant** (current behaviour; category is NOT NULL and drives the SKU digit). | Keeps SKU minting deterministic and GP-floor unambiguous. Multi-category would break the leading-digit scheme; use tags/collections for secondary grouping instead. |
| **Q4** | **Does Landed Cost Assessment belong in Rosetta?** | **No — out of the catalogue domain for now**; it is a downstream costing concern. Model supplier cost only. | Avoids overloading `basic_cost` with freight/duty. Add a separate landed-cost layer later if import charges must feed margin. **Confirm with Austin/Chris.** |
| **Q5** | **Is Packaging Configuration canonical, supplier-specific, or both?** | **Supplier-specific** (per Supplier Product) — each supplier packs differently; `units_per_pack` already lives on the link. | Correct cost math per supplier. A "canonical/default pack" on the Variant could be added as a hint, but the *authoritative* pack is per offering. |
| **Q6** | **Canonical SKU generation & immutability rules?** | Keep 8-digit `[category digit][7-digit global sequence]`, minted at commit, effectively immutable; **restrict** rename to the existing permissioned `change_sku_code` (409-on-clash, cascades). | Stable external join key (everything joins by `sku_code`). **Decision needed:** is the current single-global-counter + reserved 9M sentinel block the intended long-term scheme, and should rename be further restricted/audited given SKU is the universal external key? |
| **Q7** *(added)* | **Currency, price basis, and price history on Supplier Price?** | Add **currency** (default HKD), **explicit basis**, and **effective-dated history** to meet the DoD. | Larger than a doc change (Red-Zone schema). Needed for multi-currency suppliers, auditability, and to stop the `basic_cost` label ambiguity. **Confirm priority with Chris.** |
| **Q8** *(added)* | **Consolidate the two audit stores?** | Model as one **Audit Event**; consider merging `audit_log` + `catalogue_audit` physically later. | Cleaner governance model; physical merge is optional and non-urgent. |

---

## 9. Definition of done — self-check

| DoD criterion | Met by this dictionary? | Where |
|---|---|---|
| Source evidence separated from canonical master data | ✅ | Classification (§3); §5 distinction 9; Supplier Catalogue / Catalogue Item vs Variant / Supplier Product |
| Product, Product Variant, Supplier Product not interchangeable | ✅ | §4.6 / §4.7 / §4.9; §5 distinctions 3–4 |
| Canonical, supplier and external SKUs unambiguous | ✅ | §5 distinction 5; §4.7 (canonical), §4.9 (supplier), §4.14 (external) |
| Catalogue Items can resolve to Supplier Products | ✅ | §4.2 relationships; §6 steps 2–3 |
| Packaging calculations not solely text-dependent | ✅ | §4.10 (structured `units_per_pack` vs raw `pack_size`); §5 distinction 6 |
| Every price has currency, basis, effective period | ⚠ **Specified, not yet implemented** | §4.11 marks currency/basis/effective_from as **[intended]**; gaps in §7; decision Q7 |
| Price history and source provenance preserved | ⚠ **Provenance ✅; history specified** | Provenance (`cost_source`/`ref`) ✅ §4.11; history is **[intended]** (§7, Q7) |
| MBB thresholds/outcomes use explicit quantities & units | ✅ | §4.12 (min_qty/min_spend + typed outcomes) |
| External-system mappings supported | ⚠ **Modelled as intended; today scattered** | §4.14; gap + recommendation in §7 |
| Relationships and cardinalities explicit | ✅ | Every entity's Relationships table (Exactly one / Zero or one / One or many / Zero or many) |
| Terminology precise enough to produce the ERD next | ✅ | 16 named entities with identifiers, attributes, relationships, and current-vs-intended clearly flagged |

**Overall:** the dictionary is **ready to drive the Canonical Catalogue ERD**. The ⚠ items (currency, explicit basis, price history, external-mapping table, and the Product-family abstraction) are **deliberately surfaced as intended-state modelling decisions with recommended defaults**, so the ERD can render them as target entities/attributes while §7 keeps the current implementation honest. No criterion is unaddressed; three are explicitly "specified as target, not yet built," which is the correct outcome for a conceptual dictionary that must not silently reproduce the current gaps.

---

_Ends. Next deliverable: Canonical Catalogue ERD (consumes this dictionary)._

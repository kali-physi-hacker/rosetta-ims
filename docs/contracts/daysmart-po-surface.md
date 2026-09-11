# Daysmart Purchase-Order API Surface — as observed

DEV-305 AC2. Every entry states its **evidence tier**:

- **wire** — seen in a real capture (HAR response bodies / the pasted list payload). Strongest.
- **source** — reconstructed from Daysmart's own frontend code (webpack chunks read 2026-08-29:
  `4525.*.js`, `4839.*.js`, `index.*.js`). Requests at this tier are near-wire-grade (the code
  *builds* them); responses are consumption maps (only what the UI reads is visible — exact
  types/nullability unproven until first live use).
- **unobserved** — known to exist, shape unknown.

The frontend source files themselves are proprietary and gitignored (`Sources/`); this doc is
the extraction. Browser flows commonly send both a `vettersession` cookie and `vetter-token`
header, but Rosetta's live probes prove that `/barramundi` supplier, inventory-search,
order-list, and order-item reads work token-only. Rosetta therefore treats `/barramundi` as
the token-authenticated API boundary and reserves web login for legacy `/apps/index.php`
routes. The former post-login business-context switch (`do_switch`) was removed on
2026-09-02: no legacy route is called any more (item search moved to `/barramundi`), so the web login
is kept minimal (validateLogin + warm-up) for the day a legacy route — `do_close_order` — is needed. Mutating calls retain one-attempt/no-blind-retry semantics; they have not been
production-written merely to test authentication.

## Reads

| Endpoint | Evidence | Notes |
|---|---|---|
| `GET /barramundi/inventory/order?type=&page=` | **wire** | List dialect: ISO `+08:00` dates, `{id,label}` status, `""` empties. Contracted (`DaysmartOrderV1`). Meta lies tolerated (`count`, `prevPageUrl`). |
| `GET /barramundi/inventory/order/{id}` | **wire** | Create/detail dialect: PHP `{date,timezone:"UTC"}` dates, bare-int status, `null` empties, embedded ship/bill addresses + full supplier. |
| `GET /barramundi/inventory/order/item/list/{id}?page=` | **wire** (non-empty rows: `daysmart_order_item_list_793009.json`) | Laravel paginator. A LINE row carries: `id` (int — the Daysmart line id, `purchase_order_item_id` for updates/receipts), **`item_id` as a "DIT-…" STRING** (inventory-item ids are DIT-strings on order lines too — corrected our int assumption), `quantity_ordered`/`quantity_received`/`back_ordered`, **`price` as a FLOAT** (2.5083333333333 — their UI's `line_total/qty` division lands in storage; `line_total` bare number), `notes`, `container_type`, PHP-UTC dates, and a full embedded `item` object (cost, current_balance, instruction…). |
| *(lines inbox note)* | **wire, both** | Rosetta mirrors PO lines from the item list above (single reader `fetch_order_lines` → contract `DaysmartOrderItemV1`, one field map). The order DETAIL also embeds the same rows under `purchase_order_items` (identical ids/values; lacks `container_type`, the embedded `item` object with catalogue `cost`, and the receipt-items link) — documented as an equivalent, deliberately not built on: one source, one parser. **Lines ride along with every pull, unconditionally** (2026-09-02, Moses's correction): `POST /purchase-orders/sync` pulls every order's lines after its header page; `POST /purchase-orders/{po_uuid}/refresh` pulls ONE order (header from `order/{id}` + lines — both reads first, then apply). There is no trigger — an edited line changes nothing on the header, not even `item_count` (kept as a display value only) — and no deferred retry: a line pull that fails is reported per order in the sync response (`line_failures`) or fails the refresh (502/504), for a person to re-run. |
| `GET /barramundi/inventory/search?query=&page=&per_page=` | **wire + live probe** | Token-only item lookup now used by Rosetta. Unusual response: numeric string keys beside `message`/`total`; `raw_id` is the stable `DIT-…` inventory id, while `id` is only an encoded navigation token. |
| `GET /barramundi/inventory/order/item/shipping/{orderId}` | **source** | Receipts/receiving lines for an order → `response.resources`. |
| `GET /barramundi/inventory/supplier/list`, `address/list`, `template/list` | **wire** | Reference data (dicts / list). |
| `GET /barramundi/account/find-distibutor/{supplier_id}` | **wire** | Supplier account details. Typo is load-bearing. |
| `GET /barramundi/inventory/order-supplier/list`, `inventory/distibutor-by-valid-order/list` | **source** | Supplier pickers. |
| `GET /barramundi/reference/master-uom?module=inventory` | **wire** | UOM reference list. |

## Writes

| Endpoint | Evidence | Request shape |
|---|---|---|
| `POST /barramundi/inventory/order/add` | **wire** | Header only (and Rosetta's create body REFUSES `lines` — `PO_LINES_NOT_ACCEPTED`; lines go through `POST /purchase-orders/{po_uuid}/lines`): `{supplier_id, supplier_name, account_no, order_date:<epoch int>, ship_to_id:<int>, bill_to_id:<int>, notes:<str\|null>, template_id:<str\|null>}`. Response: created order in the create dialect (contracted, `DaysmartCreatedOrderV1`). |
| `POST /barramundi/inventory/order/item/save` | **source** (add: wire, see PO-end-to-end.har; update: source) | **Add/update lines. Body is an ARRAY** of line objects — batch-capable. Add: `[{purchase_order_id, item_id, item_name, quantity_ordered, price, line_total, notes}]`. Update: same **plus `purchase_order_item_id`** (save = upsert) — used by `PUT /purchase-orders/{po_uuid}/lines/{line_uuid}` (the line's inventory item is never changed by an edit; delete + add instead). Integrated-catalog suppliers substitute `source_catalog` + `source_product_id` for `item_id`. Their UI computes `price = line_total / quantity` as a FLOAT — we will send exact decimal strings/numbers and verify acceptance on first live use. Success check used by their UI: `response.message.code === 200`, then a re-fetch of the item list (no created-line id is read from the save response — re-fetch is the canonical way to learn line ids). |
| `PUT /barramundi/inventory/order/{orderId}` | **wire** (PO-update.har, 2026-09-01) | Header update: `{account_no, order_date:<epoch int>, ship_to_id, bill_to_id, notes}` — confirmed byte-for-byte. Response: the updated order in the create/detail dialect (fixture `daysmart_order_update_response.json`) — parseable by `DaysmartCreatedOrderV1`. **Built (2026-09-02):** `PUT /purchase-orders/{po_uuid}` → `services/po_header_flow.update_purchase_order` (adapter `update_order`). Write-THROUGH for a PO in Daysmart: Daysmart first, then the echo is mirrored (header columns are Daysmart-owned mirrors once synced — a local edit Daysmart never took would only be overwritten by the next pull, so a failed write changes nothing locally and reports why). A draft is edited locally. Submitted/closed orders refused locally (`PO_NOT_OPEN`). |
| `GET /apps/index.php/inventory/purchase_order/do_close_order?id=&notes=` | **source** | **CLOSE is a LEGACY endpoint** — outside `/barramundi/`, a GET with query params (`id`, `notes`), same web session. Sets status → 4 server-side. |
| `DELETE /barramundi/inventory/order/{orderId}` | **wire** | **SOFT delete**: the response echoes the order with `is_active: false` and notes REWRITTEN to `"[removed]"` (fixture `daysmart_order_delete_response.json`) — validating the "absence from the list ≠ deletion" rule. Success = `message.code 200` + `messages: "Purchase order deleted"`. **Built (2026-09-02):** `DELETE /purchase-orders/{po_uuid}` → `delete_purchase_order` (adapter `delete_order`): the echo is mirrored and the local row kept as `sync_status='deleted'`; a never-sent draft is CANCELLED instead (`PO_NOT_SYNCED`); a timeout → needs_review (refresh to see). |
| `DELETE /barramundi/inventory/order/item/{itemId}` | **source** | Delete one line. **Now used** by `DELETE /purchase-orders/{po_uuid}/lines/{line_uuid}` (adapter `delete_order_item`, one attempt, ack checked as `message.code == 200`); the response has not been captured on the wire yet — first live use confirms. The local row stays as `deleted`. |
| `POST /barramundi/inventory/order/submit-order` | **source** | `{purchase_order_id}` — the GENERAL submission step: their UI shows "Submit Order" whenever the user has the `inventory.purchase.order.submit` permission, the order HAS ITEMS, and status is Open. Canonical flow: create → add items → submit. Likely moves status 1 → 2 (the button hides once status ≠ 1) — unconfirmed on the wire. MWI/integrated catalogs appear only in the ERROR branch (`response.catalog === "MWI"` carries extra error detail), not as a gate. |
| `POST /barramundi/inventory/order/send-email` | **source** | `{purchase_order_id, to:[contact ids], subject, message}`. |
| `POST /barramundi/inventory/order/print-pdf` | **source** | `{purchase_order_id}` → `response.resources.path` (PDF URL). |
| `POST /barramundi/inventory/order/catalog/status` | **source** | `{source_catalog, source_product_id:[...]}` — integrated-catalog availability check (`halt`, `warning_message` per resource). |

## Receipts (receiving goods against a PO)

The receiving lifecycle is its own resource family. A receipt links to one or more purchase
orders through `order_id`; each generated receipt row links back to its PO line through
`purchase_order_item_id`. The crucial wire finding is that a PO-linked receipt is
**header-create, server-generated rows, then row updates**. It is not header-create followed
by item POSTs.

### Canonical PO-linked flow — **wire** tier

Observed in `PO-end-to-end.har` (2026-09-01) and pinned by
`daysmart_receipt_{create_request,create_response,detail_822801,items_autocreated,item_update_request,item_update_response}.json`:

| Step | Endpoint | Contract |
|---|---|---|
| 1. Load suppliers | `GET /barramundi/inventory/supplier/list` | Receipt supplier picker. |
| 2. Load valid orders | `GET /barramundi/inventory/distibutor-by-valid-order/list?distributor_id={supplierId}` | Returns the supplier's selectable orders. The UI permits multiple selections. Typo `distibutor` is load-bearing. |
| 3. Create receipt header | `POST /barramundi/inventory/receipt` | `{supplier_id:<string>, supplier_name:<string>, date:<epoch int>, order_id:[<order-id string>, ...], invoice_number:<string>, amount:null, shipping:<number\|string\|null>, notes:<string>, tax:<number\|string\|null>, tax_type:<int>}` → `response.resources.id`. Captured example used `order_id:["795607"]`, empty invoice number, and `tax_type:0`. |
| 4. Read generated rows | `GET /barramundi/inventory/receipt/{receiptId}` | Read `response.resources.receipt_items`. This embedded list is canonical because it carries both generated row `id` and `purchase_order_item_id`. DaySmart has already created one row per selected PO line. |
| 5. Reconcile identities | local operation | Match each requested adjustment one-to-one by integer `purchase_order_item_id`; verify `purchase_order_id` when present; persist the generated receipt-row `id`. Missing, duplicate, foreign, or extra rows must stop the flow before any update—never match by array position. |
| 6. Update generated row | `PUT /barramundi/inventory/receipt/items/{generatedReceiptItemId}` | Captured bodies: `{quantity_received, quantity_in_stock, amount, expiration_date, lot_number, ndc_code, tax}` (PO-end-to-end.har) and the FULL form `{quantity_received:1, quantity_in_stock:1, amount:1, expiration_date:1790179200, lot_number:"3", manufacturer:"none", ndc_code:"2345676", tax:2}` (receipt-edit.har — `manufacturer` and an epoch expiry now wire). Optional `item_id:<DIT-string>` (source) when changing the Post To item. The echo is the full row in the detail dialect. Used by `PUT /purchase-orders/{po_uuid}/receipts/{receipt_uuid}/items/{item_uuid}` and by create-time adjustments. Fixtures `daysmart_receipt_item_update_full_{request,response}.json`. |
| 7. **Post receipt** | `POST /barramundi/inventory/post-receipt` | `{purchase_order_receipt_id:<receipt id>}`. Explicit stock commitment and the point of no return; never an automatic side effect of receipt synchronization. |

`POST /barramundi/inventory/receipt/items/{receiptId}` still exists for genuinely manual or
standalone receipt additions. It must **not** be used for PO lines after step 3: those rows
already exist, and POSTing them duplicates the receipt items.

Before posting, DaySmart's UI re-reads the receipt items and blocks while any item lacks a
Post To target. Rosetta mirrors that safety rule: it re-fetches the embedded rows, requires a
valid `item_id` (`DIT-…`) on every remote row, and requires every local adjustment to be
synced before calling `post-receipt`.

### Other receipt operations

| Endpoint | Evidence | Shape / behavior |
|---|---|---|
| `GET /barramundi/inventory/receipt?status=all&page=` | **wire** | Same meta envelope as orders (467 receipts, 10 pages; page 10 captured). `purchase_order_id=` filtering exists (wire, receipt-edit.har) — an EMPTY result comes back as `{"meta": [], "resources": []}` (meta is a list, not an object). Resource mixes dialects: `date`/`post_at` are PHP-UTC objects while `create_at`/`update_at` are ISO `+08:00`; `amount` is a string beside `total_amount` as a number. **An UNPOSTED receipt carries NO order link on its header**: the list says `purchase_order_id: ""` and the detail says `null`; the link appears on the header only once posted. Its ROWS name the order (`purchase_order_id` per row). Whether the `purchase_order_id=` filter finds unposted receipts is UNOBSERVED (the two captured filtered reads found nothing) — Rosetta's single-PO refresh therefore also reads its own receipts by detail. |
| `GET /barramundi/inventory/receipt/{id}` | **wire** | Detail uses PHP-UTC date objects, nullable empties, a string `index` such as `"Receipt #493"`, embedded `receipt_items`, and full supplier. This is the identity-bearing read used by Rosetta. |
| `GET /barramundi/inventory/receipt/items/{id}` | **wire** | Display-only flat list with labels (`item_name`, `post_to`, `reference`, UOM, cost). It does **not** expose the `purchase_order_item_id` required for safe reconciliation. |
| `GET /barramundi/inventory/order/item/shipping/{orderId}` | **source** | Receipts/receiving lines associated with an order. |
| `PUT /barramundi/inventory/receipt/{receiptId}` | **wire** (receipt-edit.har, 2026-09-02) | Header update: `{date:<epoch int>, tax, shipping, invoice_number, notes}` — captured twice, tax/shipping accepted as numbers (`0`, `2`) and as a string (`"1"`). Echo = the create dialect (bare int status, PHP dates, `purchase_order_id: null` even when linked — never infer un-linking from an echo). Used by `PUT /purchase-orders/{po_uuid}/receipts/{receipt_uuid}`. Fixtures `daysmart_receipt_header_update_{request,response}.json`. |
| `POST /barramundi/inventory/receipt/items/{receiptId}` | **source** | Manual/standalone item addition only: `{post_to_item_id, purchase_order_item_id, quantity_received, quantity_in_stock, amount, expiration_date, lot_number, manufacturer, ndc_code, [tax]}`. |
| `DELETE /barramundi/inventory/receipt/items/{itemId}` | **source** | Delete one receipt row. |
| `DELETE /barramundi/inventory/receipt/{receiptId}` | **wire** | Soft delete: `is_active:false` and `purchase_order_id:null`. |
| `GET .../receipt/add-item/list/{...}`, `GET .../receipt/post-to-item/list` | **source** | Manual-add and Post To pickers. |

### Receipt wire facts

- Receipt status has its own vocabulary: `0` = `{"id": 0, "label": "In Process"}` (wire,
  receipt-edit.har) and `1` = Posted. Receipt status 1 must never be interpreted using the
  order status map, where 1 means Open. Row-level `status` is separate (0 seen on an unposted
  receipt, 2 on a posted one; vocabulary unconfirmed).
- Inventory identities (`item_id` / Post To) are `DIT-…` strings. Receipt-row ids and
  `purchase_order_item_id` values are integers.
- `quantity_received` and `quantity_in_stock` are separate; short-shipping and stock
  acceptance differences must not be collapsed.
- `expiration_date` reads as an epoch integer (for example `1817481600`). The captured update
  sent `null`; Rosetta serializes a supplied ISO date/time to an epoch integer.
- Item rows also expose rejection fields (`quantity_rejected`, `reject_type_id`,
  `reject_action`, `reject_notes`), item-level status, `in_stock_uom_id`, and `purchase_id`.
  A rejection workflow exists but is not implemented in Rosetta's current receipt command.
- The list's `nextPageUrl` can say `/barramundi/receipt` without `inventory/`; use the called
  endpoint rather than trusting meta URLs.
- Receipts carry `invoice_number`, consistent with the observed BizOps practice of waiting
  for the invoice before closing a PO.

### Receipt PULL (inbox) — 2026-09-02

Receipts ride along with the PO pull (Moses's rule: everything attached to a PO comes with
the PO). Contract `DaysmartReceiptV1` / `DaysmartReceiptRowV1`
(`schemas/purchase_orders/daysmart_receipt_v1.py`) reads the list row's mixed dialect and the
detail's `receipt_items`; its own vocabulary `RECEIPT_STATUS_VOCABULARY = {0: in_process
(label unobserved → never a drift issue), 1: posted}`; the supplier identity is `supplier_id`
(detail/echoes) or `supplier.id` (the list row carries only the object). Adapter:
`fetch_receipts(page, purchase_order_id=)` → `GET /barramundi/inventory/receipt?status=all&page=`
(the `purchase_order_id` filter is used by the single-PO refresh), `fetch_receipt_rows(id)` →
`GET receipt/{id}` (`receipt_items`); `fetch_receipt_items` (the write flow's identity read)
is now derived from it — one parser. Service `services/po_receipts_inbound.py`: upsert on
`daysmart_receipt_id`, linked to OUR order through `purchase_order_id`; a receipt whose order
is not local is counted `unlinked`, never stored blind; rows upsert on the Daysmart row id and
link to our PO lines through `purchase_order_item_id`; ownership split (what we sent is
seeded once for an imported receipt; `daysmart_*` columns are the mirror); `post_status`
follows Daysmart's known status (posted is posted whoever pressed the button; in-process means
not posted). `POST /purchase-orders/sync` pulls every receipt of every local order (rows for
each — no trigger) after the order pages and reports `receipts.{created, unlinked, rows_synced,
rows_failed, failures[], error}`; `POST /purchase-orders/{po_uuid}/refresh` pulls that order's
receipts and rows with every other read before applying anything; `GET /purchase-orders/{po_uuid}`
lists the PO's receipts with their rows. Cost: one detail call per linked receipt per sync.
Not yet built: mirror-prefill create, adjust, receipt retry/resolve, delete (next steps).

### Rosetta implementation status (receipts) — 2026-09-02

Create now matches Daysmart's own screen (`services/po_receipt_flow.py`): stage the header
(and any adjustments) locally → `POST inventory/receipt` with the PO's id → pull the rows
Daysmart generated through the receipt inbox (`refresh_receipt_rows`) and keep them as they
are → PUT each adjustment onto its generated row (`apply_receipt_adjustment`, shared with the
adjust step) → pull once more so the mirror shows what Daysmart stored. A header-only request
is complete on its own (`POST /purchase-orders/{po_uuid}/receipts` with no `items`). Optional
`items` are ADJUSTMENTS naming one of this PO's lines by `line_uuid` (older callers may still
pass `daysmart_po_item_id`); Daysmart ids are looked up locally. A staged adjustment is adopted
by its generated row through the exact PO-line identity (one row per line; two adjustments for
one line are refused before staging; a line never sent to Daysmart is refused). Rows the caller
did not mention are received as ordered — never a review case. An adjustment whose line got no
generated row → that item `needs_review`, the rest proceed. Dates validated before staging
(`RECEIPT_VALIDATION_FAILED`, nothing created locally or remotely). The created Daysmart id is
kept even if the rows pull fails (no blind second create). Timeout on create → `needs_review`,
never resent. Posting is a separate action with the Post To/sync preflight before the
irreversible call. Everything fixture-backed; no production receipt is created or posted by
the tests.

EDITS (receipt-edit.har, 2026-09-02): `routers/purchase_order_receipts.py` now carries all receipt
writes under the PO — `PUT …/receipts/{receipt_uuid}` (header: invoice number, date, shipping, tax,
notes → `update_receipt`) and `PUT …/receipts/{receipt_uuid}/items/{item_uuid}` (one row: quantities,
amount, expiry, lot, manufacturer, ndc, tax, post-to → `update_receipt_item` via the shared
`apply_receipt_adjustment`). Both are full replacements of the fields Rosetta owns; the local edit
stands whatever Daysmart answers (outcome + error code in the response); after a successful write the
receipt is pulled from the detail (`fetch_receipt` → header + rows, `refresh_receipt`) so the mirror
shows what Daysmart stored. A posted receipt is refused locally (`RECEIPT_POSTED`); a row Daysmart has
not generated is refused (`RECEIPT_ITEM_NOT_GENERATED`). The pull links UNPOSTED receipts through their
rows (`link_receipt_from_rows`) since their header names no order.

RETRY (2026-09-02): `POST …/receipts/{receipt_uuid}/retry` → `retry_receipt` — the receipt lifecycle
engine. Attempts now carry `receipt_id`, so a retry reads ITS OWN last create outcome: provably
never-landed (daysmart_down / auth_failure) → the same staged receipt is re-driven (one local row,
identical payload); may-have-landed (timeout, refusal, contract mismatch) → `needs_review`, never a
second create; a landed header → finish what is left (rows, adjustments). The create's
Idempotency-Key replay is outcome-aware the same way (never-landed → re-drive; otherwise the stored
answer). Still to build: resolve (link a timed-out create to the receipt Daysmart already has),
delete receipt.

## End-to-end wire session (PO-end-to-end.har, 2026-09-01) — final upgrades

The full UI flow (create → search items → add/edit lines → submit → receipt
→ delete receipt) captured live. Everything below is **wire** tier; fixtures
`daysmart_{item_search,line_add,line_update,submit_order,receipt_*}*.json`.

- **Item search** was originally captured on the legacy
  `GET /apps/index.php/ajax/inventory/filter_all_catalogs?search_key=…` route.
  Rosetta now uses the token-authenticated `/barramundi/inventory/search`
  route above because it returns the same stable `DIT-…` identity without
  requiring a PHP web login.
- **Add line confirmed**: array body; `purchase_order_id` is a STRING in
  their payload ("795607"); money as bare numbers (their floats); ack =
  `message.code 200` + misspelled "sucess". Update variant with int
  `purchase_order_item_id` confirmed.
- **Submit confirmed**: `{purchase_order_id: "<id as string>"}` →
  full order echoed in the create dialect with **`status: 2`**; the list
  then shows **`{"id": 2, "label": "Submitted"}`** — status 2 is now IN
  our vocabulary (wire-proven). Their UI blocks all edits after submit
  (submit gate: has items + status Open).
- **Receipt create corrections** (source-tier guesses fixed):
  `date` is an **EPOCH INT** (not a datetime string) and `order_id` is an
  **ARRAY of string order ids** (`["795607"]` — a receipt can span
  orders); `invoice_number` may be empty at create; `tax_type: 0` default.
- **RECEIPT ITEMS ARE AUTO-CREATED**: creating a receipt with order_id
  (`is_with_order: 1`) makes Daysmart pre-populate one receipt item per PO
  line (quantity_received prefilled = ordered, cost prefilled, real
  `purchase_order_item_id`). Their UI then EDITS via
  `PUT /inventory/receipt/items/{itemId}` with a slim body
  `{quantity_received, quantity_in_stock, amount, expiration_date,
  lot_number, ndc_code, tax}`. ⚠ Blindly POSTing items after create would
  DUPLICATE the auto-created rows — the receiving flow must be
  create → fetch items → PUT adjustments.
- **Receipt status vocabulary**: `0` = draft/unposted (seen on the fresh
  receipt), `1` = Posted. Receipt DELETE is soft (`is_active: false`) and
  **nulls `purchase_order_id`** (unlinks from the order).
- Receipt list supports `?purchase_order_id=` filtering.

## Cross-cutting observations from the source

- **Success convention**: their UI treats `data.response.message.code === 200` as success and
  anything else as failure — matches our envelope handling.
- **Status vocabulary**: 1 = Open, 2 = Submitted, and 4 = Closed. Status 2 is
  wire-confirmed by the end-to-end submit capture.
- **`encoded_id`** is their frontend router's URL token (`/inventory/item/{encoded_id}`) —
  navigation, not identity. Never store or compare it.
- **Field-name inconsistency is systemic** in this API (their own item form sends
  `cost_type_id` on create but `cost_type` on update) — vindicates the two-dialect contract
  approach; never assume a naming convention, match observed fields exactly.

## Refusals / not available

- No public/OAuth surface for POs found: the OAuth client (`clientssot/daysmart_client.py`)
  serves other resources; the PO surface lives on the browser API only (probe listed below).
- No webhook/event mechanism found anywhere in the frontend — pull only.

## Remaining probes (first live use settles them)

1. OAuth API `order` resource — worth one probe; if present, reads move to the stable transport.
2. `order/item/save`: does it accept decimal-string prices (vs their floats)? Response body shape?
3. `do_close_order` response body (legacy endpoint — likely not the barramundi envelope).
4. Whether the list endpoint filters/sorts on `update_at` (sync efficiency, not correctness).

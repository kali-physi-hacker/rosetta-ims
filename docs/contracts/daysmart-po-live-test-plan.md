# Daysmart PO sync — live test plan (2026-09-04)

Everything below runs against the REAL Daysmart. Use the test supplier
**Alfamedic** and mark every note with `TEST` so the team can ignore it.
Order matters: edits and deletes of lines must happen BEFORE submit
(Daysmart locks a submitted order); post a receipt only if you accept
that stock is really committed (it cannot be undone).

## 0. Start the server

```bash
cd apps/api
source .env                      # DAYSMART_USERNAME, DAYSMART_PASSWORD, DAYSMART_API_TOKEN
export DATABASE_URL=sqlite:///./ims-dev.db
/Users/moseszeggey/work/talentcoop/virtualenv/rosetta/bin/python -m uvicorn main:app --reload --port 8001
```

Swagger: http://localhost:8001/docs (Authorize with the token below).

```bash
BASE=http://localhost:8001
TOKEN=$(curl -s $BASE/auth/login -H 'Content-Type: application/json' \
  -d '{"username":"seph","password":"<your password>"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')
AUTH="Authorization: Bearer $TOKEN"
```

## 1. Reference data (reads only)

```bash
curl -s $BASE/purchase-orders/suppliers -H "$AUTH"
curl -s "$BASE/purchase-orders/item-search?q=hills" -H "$AUTH"
```
Expect: Alfamedic with id `3cd1ace0-ddd6-4a`; the search returns `DIT-…` ids
(e.g. `DIT-ed47392c-6e6` "10009870 - Hills F9 c/d 6kg").

## 2. Create a PO (header only)

```bash
curl -s $BASE/purchase-orders -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "supplier_daysmart_id": "3cd1ace0-ddd6-4a",
  "supplier_name": "Alfamedic",
  "order_date": "2026-09-04T11:00:00+08:00",
  "account_no": "",
  "notes": "TEST - Rosetta API trial (ignore)"
}'
```
Expect: 201, `outcome: synced`, `po.daysmart_id` set, `po.lines: []`.
Save `po.po_uuid` as `PO`. A body with `lines` must answer 422 `PO_LINES_NOT_ACCEPTED`.

## 3. Add a line

```bash
curl -s $BASE/purchase-orders/$PO/lines -H "$AUTH" -H 'Content-Type: application/json' -d '[{
  "description": "10009870 - Hills F9 c/d 6kg",
  "quantity": 2, "quantity_uom": "BAG", "unit_price": "1.00",
  "daysmart_inventory_item_id": "DIT-ed47392c-6e6"
}]'
```
Expect: 201, `results[0].outcome: synced`, `line.daysmart_item_id` (an int).
Save `line.line_uuid` as `LINE`. UNPROVEN on the wire: the price as a decimal
string — a refusal here is a finding, not a bug in the test.

## 4. Refresh the PO (pull header + lines + receipts)

```bash
curl -s -X POST $BASE/purchase-orders/$PO/refresh -H "$AUTH"
```
Expect: 200, `lines.unchanged: 1` (or `updated`), `po.item_count: 1`,
`po.lines[0].daysmart_quantity_ordered: 2`, `receipts.completed: true`.

## 5. Edit the line (UNPROVEN: update by line id)

```bash
curl -s -X PUT $BASE/purchase-orders/$PO/lines/$LINE -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "description": "10009870 - Hills F9 c/d 6kg",
  "quantity": 3, "quantity_uom": "BAG", "unit_price": "1.00",
  "daysmart_inventory_item_id": "DIT-ed47392c-6e6", "notes": "TEST edit"
}'
```
Expect: 200, `outcome: synced`, `line.quantity: "3"`, `line.daysmart_quantity_ordered: 3`,
`mirror_error: null`. Check Daysmart's screen shows 3.

## 6. Update the PO header

```bash
curl -s -X PUT $BASE/purchase-orders/$PO -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "account_no": "-", "order_date": "2026-09-04T11:00:00+08:00",
  "ship_to_id": 861040, "bill_to_id": 861040, "notes": "TEST - Rosetta API trial (edited)"
}'
```
Expect: 200, `outcome: synced`, `po.notes` = the edited text (that is Daysmart's echo).

## 7. Add a second line, then delete it (UNPROVEN: delete-line response)

Repeat step 3 with `"quantity": 1`, save its `line_uuid` as `LINE2`, then:
```bash
curl -s -X DELETE $BASE/purchase-orders/$PO/lines/$LINE2 -H "$AUTH"
```
Expect: 200, `outcome: deleted`, `line.sync_status: deleted`. Refresh (step 4)
and check the deleted line shows `daysmart_is_active: false` or is absent.

## 8. Create a receipt (header only — Daysmart generates the rows)

```bash
curl -s $BASE/purchase-orders/$PO/receipts -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "invoice_number": "TEST-0001", "receipt_date": "2026-09-04T11:00:00+08:00",
  "notes": "TEST receipt (ignore)"
}'
```
Expect: 201, `outcome: synced`, `generated_rows: 1`, `adjustments_applied: 0`,
`receipt.items[0].daysmart_receipt_item_id` set, `quantity_received: "3"`.
Save `receipt.receipt_uuid` as `RCPT`, `items[0].item_uuid` as `ROW`.

## 9. Edit the received row, then the receipt header

```bash
curl -s -X PUT $BASE/purchase-orders/$PO/receipts/$RCPT/items/$ROW -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "quantity_received": 2, "quantity_in_stock": 2, "amount": "1",
  "lot_number": "TEST-LOT", "expiration_date": 1790179200, "manufacturer": "none"
}'
curl -s -X PUT $BASE/purchase-orders/$PO/receipts/$RCPT -H "$AUTH" -H 'Content-Type: application/json' -d '{
  "invoice_number": "TEST-0001A", "receipt_date": "2026-09-04T11:00:00+08:00",
  "shipping": "0", "tax": "0", "notes": "TEST receipt (edited)"
}'
```
Expect: 200 each, `outcome: synced`, `mirror_error: null`; the PO detail
(`GET /purchase-orders/$PO`) lists the receipt with `daysmart_status: in_process`.

## 10. Submit the PO (locks it in Daysmart)

```bash
curl -s -X POST $BASE/purchase-orders/$PO/submit -H "$AUTH"
curl -s -X POST $BASE/purchase-orders/$PO/refresh -H "$AUTH"
```
Expect: submit 200; after the refresh `po.daysmart_status: submitted`.
Now `PUT /purchase-orders/$PO` must answer 409 `PO_NOT_OPEN`.

## 11. Post the receipt — ONLY if committing stock is acceptable

```bash
curl -s -X POST $BASE/purchase-orders/$PO/receipts/$RCPT/post -H "$AUTH"
```
Expect: 200, `status: posted`. Afterwards a row edit must answer 409 `RECEIPT_POSTED`.

## 12. Delete the PO

```bash
curl -s -X DELETE $BASE/purchase-orders/$PO -H "$AUTH"
```
Expect: 200 `outcome: deleted` with `po.notes: "[removed]"` — OR, if Daysmart
refuses deleting an order that has a receipt / was submitted, `outcome:
needs_review` with `error_code: DAYSMART_REFUSED`. Both are findings to record.

## 13. Full sync (the initial import) — slow

```bash
time curl -s -X POST $BASE/purchase-orders/sync -H "$AUTH"
```
This makes roughly one call per order and one per receipt (a thousand or so).
Expect: `completed: true`, `created` ≈ the number of orders in Daysmart,
`line_failures: []`, `receipts.completed: true`, `receipts.unlinked` small,
`receipts.failures: []`. Note how long it took. Then:
```bash
curl -s "$BASE/purchase-orders?sync_status=synced&per_page=5" -H "$AUTH"
curl -s $BASE/purchase-orders/review -H "$AUTH"
```

## What to record

For every step: the HTTP status, `outcome`, `error_code` / `message` when present,
and what Daysmart's own screen shows afterwards. The three UNPROVEN items
(decimal-string price, line update by id, delete-line response) and whether
the receipt-list filter returns unposted receipts (visible in step 4's
`receipts` block before step 11) are the findings we are after.

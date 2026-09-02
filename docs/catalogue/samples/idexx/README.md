# IDEXX — no source document, by nature

Supplier **3** (`AVM`) invoices IDEXX products; IDEXX is the brand. Read by
`idexx.order_portal_snapshot.v1`.

**There is no file here, and there never will be.** IDEXX send no price list.
Their catalogue lives behind a login on `order.idexx.com`, and a browser fetches
it on demand when someone presses *Fetch catalogue* — the same shape as Royal
Canin. Two things were established before that design was settled, so nobody
need re-derive them:

* there is **no data API**;
* **plain HTTP login is captcha-blocked** — the risk check fails the request
  before any password is read — while a real browser passes. Chromium is
  genuinely required, which is why the production image carries it.

## What stands in for a source document

`apps/api/tests/fixtures/catalogue_pipeline/idexx/idexx_hk_snapshot.csv` — a real
capture of the live portal on 2026-09-02 under the clinic's own account: 105
products across 20 category leaves, every one carrying a material number and a
pack. It is generated, not transcribed: columns fixed by `SNAPSHOT_COLUMNS` and
rows sorted by material, so an unchanged catalogue yields identical bytes and
re-reading submits nothing.

Regenerate it with `services.idexx_connector.capture`; do not hand-edit it.

## Two things the snapshot will not tell you

* **The price carrying the asterisk is OURS.** The unmarked figure beside it is
  IDEXX's list price, appears on only 3 of 105 rows, and is deliberately not
  captured. A snapshot is account-specific and is never a list price.
* **The portal is not everything AVM bills.** `Reference-laboratory-supplies`
  holds 20 items and every one is a free collection consumable — tubes, swabs,
  jars, specimen bags — with no test among them. Send-out tests are a service
  invoiced separately, which is why two rows on the BizOps golden sheet
  (`99-0018136` Pancreatic Lipase, `99-0004959` CRP) are absent while their
  in-house equivalents SNAP cPL and SNAP fPL are present. **A product missing
  from a snapshot is never evidence of a delisting.**

## What reads it

* Connector — `apps/api/services/idexx_connector.py`
* Ingestion — `apps/api/services/idexx_ingestion.py`
* Contract — `apps/api/schemas/catalogue_pipeline/supplier_contracts/suppliers/idexx.py`
* Tests — `apps/api/tests/test_idexx_connector.py`, `…_ingestion`, `…_capture_api`, `…_end_to_end`

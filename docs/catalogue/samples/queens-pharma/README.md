# Queen's Pharma — Zoetis price lists

Supplier **63** (`QUEENSPH`). Read by `queens_pharma.zoetis_price_list.v1`.

Queen's distribute Zoetis and price it on a form a clinic is meant to fill in and
send back. They arrive as photographs over WhatsApp, one per product range, each
with its own effective date. The original filenames were WhatsApp's
(`WhatsApp Image 2026-03-31 at 18.44.36 (1).jpeg` and friends); they are renamed
here for what they are, and the mapping is below.

| File | Form | Products | Original |
|---|---|---|---|
| `cytopoint-2024-10-01.jpeg` | Price list 2024, from 1 October | Cytopoint 10/20/30/40mg | `…18.44.36 (1).jpeg` |
| `librela-solensia-2025-01-01.jpeg` | Price list 2025, from 1 January | Librela 5/10/15mg, Solensia 7mg | `…18.44.36.jpeg` |
| `alphatrak3-2024-08-01.jpeg` | Price list 2024, from 1 August | AlphaTRAK 3 starter kit, test strips | `…18.44.36 (2).jpeg` |
| `in-stock-banner-2025.jpeg` | Marketing banner | **none** — no table, no prices | `WhatsApp Image 2025-10-03…` |

The banner is kept deliberately. It is not a catalogue, and the pipeline must
yield no products from it rather than fail; that behaviour is pinned by
`test_the_marketing_banner_is_not_a_catalogue`.

## Not committed here

A fifth document exists: a copy of the Cytopoint form **filled in and signed by
another veterinary practice** before it reached us, carrying that practice's
name, a named individual, their signature and their stamp, plus handwritten
order quantities down the Order column.

It is held outside the repository pending a decision, because committing a third
party's signature and identity into shared history is not a thing to do by
default. Nothing depends on the file itself: what it demonstrates — that none of
the handwriting reaches the extracted envelope — is already pinned by
`cytopoint_second_read.json` in the test fixtures and by
`test_a_form_filled_in_by_a_clinic_yields_none_of_the_handwriting`.

## What reads these

* Contract — `apps/api/schemas/catalogue_pipeline/supplier_contracts/suppliers/queens_pharma.py`
* Recorded envelopes — `apps/api/tests/fixtures/catalogue_pipeline/queens_pharma/`
* Tests — `apps/api/tests/test_queens_pharma_contract.py`, `…_end_to_end.py`

These images are the source of those envelopes. Re-record with
`scripts/record_golden_envelopes.py`; the images themselves are evidence and
should not be edited, cropped or recompressed.

Ten products across the three price lists, and **not one item code** on any of
them — which is why every Queen's row reaches a person at the match.

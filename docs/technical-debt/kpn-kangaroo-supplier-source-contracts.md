# KPN / Kangaroo Supplier-Source Contract Debt

Status: draft PR later

The KPN/Kangaroo supplier-source contracts remain intentionally non-selectable at runtime. Do not promote them to `SUPPORTED` until supplier ownership and row evidence are confirmed.

## Supplier ID Candidates

Production supplier rows found during CIS-103C follow-up:

| Supplier ID | Code | Name | Segment |
|---:|---|---|---|
| `15` | `KPNTRADI` | K.P.N. Trading | `non_vet` |
| `81` | `KANGAR` | Kangaroo Pet Nutrition | `non_vet` |

## Deferred Decisions

Confirm ownership for each source sample:

| Source sample | Current contract | Decision needed |
|---|---|---|
| `KPN_Kangaroo.pdf` | `kangaroo.mixed_price_catalogue.v1` | Confirm whether supplier ID `15`, supplier ID `81`, or split ownership applies. |
| `✔ Proplan PPVD & PPSD Product List 202412 New packing.pdf` | `kangaroo.purina_proplan_veterinary_diets.v1` | Confirm whether supplier ID `15` or `81` owns this document. |
| `(Kangaroo) Earthz Pet.pdf` | `kangaroo.earthz_pet_price_sheet.v1` | Confirm supplier ownership and capture OCR/vision row evidence. |

## Evidence Required Before Promotion

- Representative row fixtures for every distinct table layout.
- Explicit confirmation of supplier ID per document.
- Price basis for wholesale/supply price fields.
- Distinction between case configuration and order constraint.
- OCR/vision text, page number, bounding boxes, and confidence for the image-only Earthz sheet.
- Promotion/MBB interpretation for Earthz buy/free notation.

Tracked fixtures:

```text
apps/api/tests/fixtures/catalogue_pipeline/supplier_source/v1/row_examples/kangaroo.mixed_price_catalogue.v1.rows.json
apps/api/tests/fixtures/catalogue_pipeline/supplier_source/v1/row_examples/kangaroo.purina_proplan_veterinary_diets.v1.rows.json
apps/api/tests/fixtures/catalogue_pipeline/supplier_source/v1/row_examples/kangaroo.earthz_pet_price_sheet.v1.rows.json
```

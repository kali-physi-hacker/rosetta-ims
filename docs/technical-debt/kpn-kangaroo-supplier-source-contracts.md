# KPN / Kangaroo Supplier-Source Contract Debt

Status: three contracts still non-selectable; two retired 2026-09-07.

## What changed on 2026-09-07

**The two bundle contracts were retired**, not promoted:

| Contract | Was | Now |
|---|---|---|
| `kpn_trading.catalogue_bundle.v1` | PARTIALLY_VERIFIED | DEPRECATED |
| `kangaroo_pet_nutrition.catalogue_bundle.v1` | PARTIALLY_VERIFIED | DEPRECATED |

Neither could ever have been promoted as written. `SUPPORTED` requires a VERIFIED
price basis, and a bundle spanning unit, pack and case prices has no single basis
to verify. The 2026-08-13 layout split already replaced them — both carried
`superseded_by_layout_specific_contracts` in their own metadata — and three of that
split's siblings were removed as redundant once `price_basis_per_column` let one
contract carry several bases (`pack_and_case_bulk_list` on 08-17,
`case_only_price_list` and `vet_clinic_price_list` on 08-25). The bundles are the
last survivors of a design that has been replaced. They remain **declared** so the
`contract_id` recorded on historical runs still resolves.

The mixed document is already handled without them, by supplier: `KPN_Kangaroo _1_.pdf`
ingested under supplier 15 through `kpn_trading.pack_price_list.v1`, and
`Kangaroo _ KPN.pdf` under supplier 81 through `kangaroo_pet_nutrition.unit_price_list.v1`.
Both are SUPPORTED and both have completed runs.

## Supplier IDs

| Supplier ID | Code | Name | Segment |
|---:|---|---|---|
| `15` | `KPNTRADI` | K.P.N. Trading | `non_vet` |
| `81` | `KANGAR` | Kangaroo Pet Nutrition | `non_vet` |

They are **distinct suppliers**, not one company under two names: 18 brands against
9, overlapping on only three parasiticides (Frontline, Heartgard, Nexgard).

## What our own data now answers

`supplier_brands` records which supplier each brand is bought from. Against the three
deferred ownership decisions:

| Source sample | Brands it contains | What the brand links say |
|---|---|---|
| `KPN_Kangaroo.pdf` | Stella & Chewy's, Canidae, Ziwipeak | **Genuinely both.** The first two are supplier 15's; Ziwipeak is supplier 81's. "Split ownership" is the answer. |
| `(Kangaroo) Earthz Pet.pdf` | Earthz Pet | **Supplier 81.** Earthz Pet is linked to Kangaroo and to no one else. The declaration is already correct. |
| `Proplan PPVD & PPSD…pdf` | Purina Pro Plan Vet | **Says supplier 15**, not the 81 the contract declares. See below. |

## Still open — needs a human

1. **Purina Pro Plan Veterinary Diets: 15 or 81?** `kangaroo.purina_proplan_veterinary_diets.v1`
   declares supplier 81, and a code comment in `test_supplier_source_contracts.py`
   justifies that as "the legacy layouts sit inside the Kangaroo half of the combined
   document". But `supplier_brands` links Purina Pro Plan Vet to supplier 15 (and to
   Maxipro, 18) — never to 81. One of the two is wrong and only BizOps can say which.
   This blocks promoting that contract, because promoting it would attribute the
   document to a supplier our own records disagree with.

2. **The `supplier_code="KPN"` on all three `kangaroo.*` contracts** matches neither
   `KPNTRADI` nor `KANGAR`. The collision is recorded as a deliberate decision, so it
   is left alone — but it should be revisited alongside (1), since it is why the
   registry reads as though there were two Kangaroo suppliers.

3. **`KPN_Kangaroo.pdf` exists in more than one version**: 53 pages in the working
   folder, 57 pages as ingested for supplier 15. Confirm which is current before
   building row fixtures against either.

## Evidence still required before promoting the remaining three

- Representative row fixtures for every distinct table layout.
- Price basis for wholesale/supply price fields.
- Distinction between case configuration and order constraint.
- Promotion/MBB interpretation for the Earthz buy/free notation.

`KANGAROO_EARTHZ_IMAGE_ONLY` is **no longer a blocker in itself**. It records that the
Earthz sheet has no text layer and needs OCR/vision — but every PDF page has gone to
vision since `_classify_pdf_page` lost its callers, so an image-only sheet is now the
ordinary case. What survives is confirming its price basis.

The three `*_SUPPLIER_IDENTITY_REQUIRED` ambiguities are likewise mechanised:
`CONTRACT_SUPPLIER_IDENTITY_MISMATCH` checks identity from captured evidence once a
source is re-extracted with the prompt that records `supplier_identity_text`, which is
live. They need a re-extraction to demonstrate, not a decision.

Tracked fixtures:

```text
apps/api/tests/fixtures/catalogue_pipeline/supplier_source/v1/row_examples/kangaroo.mixed_price_catalogue.v1.rows.json
apps/api/tests/fixtures/catalogue_pipeline/supplier_source/v1/row_examples/kangaroo.purina_proplan_veterinary_diets.v1.rows.json
apps/api/tests/fixtures/catalogue_pipeline/supplier_source/v1/row_examples/kangaroo.earthz_pet_price_sheet.v1.rows.json
```

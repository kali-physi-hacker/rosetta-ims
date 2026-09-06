# Asia Vet Medical — price lists

Supplier **3** (`AVM`). AVM send more than one list, and they are different
documents with different shapes. Only the first is contracted.

| File | Covered by | Shape |
|---|---|---|
| `vetriscience-price-list.jpg` | `asia_vet_medical.vetriscience_price_list.v1` | A photographed five-column table, sectioned by health category, with an item code on every row |
| `consumables-list-2025-10-20.pdf` | `asia_vet_medical.consumables_price_list.v1` | 13 text-extractable pages, 460 rows, 48 sections — the code and the pack both folded into the description |

The VetriScience list is the best-conditioned source we hold: it states the
SPECIES in its own words — "Canine", "Feline", "Canine + Feline" — where every
other contract has to infer it from a product name. What it does not state is
the container. It says "180 Capsules", never bottle or box, so the price basis
is the generic `PACK` (user ruling 2026-09-01).

The original filename was `___AVM.jpg`, which is why the row-evidence fixture
refers to it that way; it is renamed here for what it is.

## The consumables list, now contracted

Contracted on 2026-09-06. It is the plainest table we hold — `Item Description`
and `Unit Price (HK$)`, and nothing else — which puts all the difficulty in that
first column, because the page has no room for anything more:

* **The manufacturer's code, in brackets at the end.** `Mila Nasogastric Feeding
  Tube 6Fr x 55cm (22in) (NG622)` is item `NG622`. 267 of the 460 rows carry one.
  Only a bracketed token containing a **digit** is read as a code — the same
  brackets hold `(Blue)`, `(Orange)`, `(22in)`, and a tube whose supplier code
  read "Blue" would match confidently against the wrong thing.
* **The pack, as a count and the thing counted**: `100 pcs/pack`, `12 rolls/box`.
  99 rows state one; the rest are a single item. A product's own dimensions
  (`1.3ml`, `6Fr x 55cm`) sit in the same string and are never read as a count.

It costs no vision provider — the text is machine-readable on all thirteen pages,
unlike the VetriScience list, which is a photograph.

**No golden coverage**: the sample sheet's eighteen AVM rows are all VetriScience
supplements, so not one of these 460 is independently corroborated. And the code
is the **manufacturer's**, not necessarily what AVM put on an invoice — worth
confirming before a three-way match relies on it.

## What reads these

* Contract — `apps/api/schemas/catalogue_pipeline/supplier_contracts/suppliers/asia_vet_medical.py`
* Recorded envelope — `apps/api/tests/fixtures/catalogue_pipeline/asia_vet_medical/vetriscience_page_1.json`
* Tests — `apps/api/tests/test_asia_vet_medical_contract.py`

# Asia Vet Medical — price lists

Supplier **3** (`AVM`). AVM send more than one list, and they are different
documents with different shapes. Only the first is contracted.

| File | Covered by | Shape |
|---|---|---|
| `vetriscience-price-list.jpg` | `asia_vet_medical.vetriscience_price_list.v1` | A photographed five-column table, sectioned by health category, with an item code on every row |
| `consumables-list-2025-10-20.pdf` | **nothing yet** | 13 text-extractable pages, 455 rows, 43% of them carrying no item code at all |

The VetriScience list is the best-conditioned source we hold: it states the
SPECIES in its own words — "Canine", "Feline", "Canine + Feline" — where every
other contract has to infer it from a product name. What it does not state is
the container. It says "180 Capsules", never bottle or box, so the price basis
is the generic `PACK` (user ruling 2026-09-01).

The original filename was `___AVM.jpg`, which is why the row-evidence fixture
refers to it that way; it is renamed here for what it is.

## The consumables list is here on purpose

It is not contracted, and this is the file to reach for when it is. Two things
already known about it, from investigating it on 2026-09-01:

* it is **text-extractable on all 13 pages**, so a contract for it would need no
  vision provider at all — unlike the VetriScience list, which is an image and
  therefore costs a vision call on every single ingestion;
* **43% of its rows carry no item code**, so it would stand on the same codeless
  footing as Queen's: rows conform without identity and take it at the match.

A PDF of the VetriScience list — which AVM presumably have, since they produced
the consumables list as one — would make that source free and exact too. That
ask is still open with them.

## What reads these

* Contract — `apps/api/schemas/catalogue_pipeline/supplier_contracts/suppliers/asia_vet_medical.py`
* Recorded envelope — `apps/api/tests/fixtures/catalogue_pipeline/asia_vet_medical/vetriscience_page_1.json`
* Tests — `apps/api/tests/test_asia_vet_medical_contract.py`

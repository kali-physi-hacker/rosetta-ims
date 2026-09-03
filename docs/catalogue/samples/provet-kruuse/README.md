# ProVet Kruuse — Hong Kong price list

Supplier **62** (`PROVETKR`). Read by `provet_kruuse.hk_price_list.v1`. The
golden sheet files these rows under "Kruuse Hong Kong Ltd" and the documents
head themselves "Provet Hong Kong" — one company, three ways of writing it.

| File | Covered by | Shape |
|---|---|---|
| `hk-price-list-2025-v2.pdf` | `provet_kruuse.hk_price_list.v1` | 4 pages from Excel, **192 rows**: `Product Code \| Description \| List Price (HKD$)` |
| `hk-product-list-2025.pdf` | **nothing** | 7 pages of product NAMES — no codes, no prices |

## The page contradicts itself, once

**`CERE60` (Cerenia 60mg Tablets 4s) is printed twice on page 2 — at $174.00
and at $198.00**, same description both times, with nothing saying which
supersedes the other.

One supplier code is one product, so the two rows fold into a single offering
and the surviving price is **whichever was read last**. That is not a decision
anybody made. What stops it being silent is the contract's declared ambiguity
`PROVET_CODE_PRINTED_TWICE_AT_DIFFERENT_PRICES`, which fires on every run and is
pinned by a test. **Confirm the price with ProVet.**

`ALUTAB600` is also printed twice, but at one price — a duplicated line, and
reported separately so the two cases stay distinguishable.

## Three more things this list does not say

**It names no basis.** One price beside one product, and nothing about what the
money buys. Read as the generic `PACK` (user ruling 2026-09-03): $90.00 buys the
box of four Cerenia tablets, not one tablet. The golden sheet records "1 Tab"
for that row — which would make the box $360 — but its basis column is malformed
on these rows ("1 1 Set", inconsistent case) and is not used.

**The pack count is in the description**, at the end: "Tablets 4s", "Patch 5s".
Only that form is read. "Injection 20ml" is what a bottle HOLDS, and reading
twenty millilitres as twenty sellable things would value a bottle of a
controlled drug at a twentieth of its price.

**Ten rows say "Please contact us" instead of a price** — and every one is
Zoetis: Cytopoint, Solensia, Beransa. That range is priced for us by Queen's
Pharma, whose own contract carries it. Nobody need chase ProVet for a number
they have declined to print.

## The product list is here, and is not contractable

Seven pages of names with **no codes and no prices**, laid out in newspaper
columns. There is nothing to price, so nothing reads it. It is committed because
it names products the price list does not carry — Atropt, Bactroban, Doxycycline
paste, Panacur — which is why a product missing from the price list is a price
we have not been given rather than a delisting.

## Not committed

A `Covetrus Catalogue 2025.pdf` arrived in the same folder: 32 pages of product
copy with three dollar signs in the whole document. Marketing collateral, not a
price list, for a different brand, and 4.7MB. Held outside the repo; say so if
it should come in.

## What reads these

* Contract — `apps/api/schemas/catalogue_pipeline/supplier_contracts/suppliers/provet_kruuse.py`
* Recorded envelopes — `apps/api/tests/fixtures/catalogue_pipeline/provet_kruuse/` (all 4 pages)
* Tests — `apps/api/tests/test_provet_kruuse_contract.py`, `…_end_to_end.py`

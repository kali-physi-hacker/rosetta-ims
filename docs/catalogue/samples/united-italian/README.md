# United Italian — general-practice price list

永義(香港)有限公司, supplier **46** (`UNITEDIT`). Read by
`united_italian.gp_price_list.v1`.

`gp-price-list-2025.pdf` — the 2025 list, effective from its cover date, born
digital from Word on 2025-06-26. 40 pages, **657 priced lines**, 26 categories,
and 43 distributed brands (Abbott, B.Braun, BD, Baxter, Smith & Nephew, Pentax
among them). Bilingual throughout. Minimum order HK$500.

The largest source we read, and the one that states the most per row.

## Three things to know before changing anything here

**The price carries its own basis** — `$46.00 / bag`, `$205.00 / box`,
`$6.00 / pc` — in twenty-three different units, abbreviated inconsistently. The
contract declares every spelling this list prints; a unit they invent next year
holds the row rather than being priced on a guess.

**Some rows print two prices.** `$46.00 / bag` beside `$828.00 / box`. The first
is the cost, the second a bulk term. On some pages the vision pass returns them
as two columns and on others as one merged cell, so both readings take the
FIRST amount-and-unit pair. Taking the last prices a bag as a box.

**106 lines price by quantity and name no container** — `$78.00 / 100's`. Those
resolve to a PACK of that many. The golden sheet records BOX for them, which is
a vessel this page never prints; the prices agree to the cent.

## Two open questions for BizOps

Recorded here as well as in the row-evidence fixture, because they are about the
sheet rather than the code and will outlive this file being read:

* **`89471`** — the sheet records the basis as CASE, so $52.00 buys fifty
  drapes. The page prints `(50's / case) $52.00 / pc`. **A fiftyfold difference
  in the cost of every drape.** The contract reads the page.
* **`3549232` Propofol** — sheet $135.00, this list $320.00, same product.

And one likely slip: **`AHB1323HK` appears twice on the sheet**, once as "LRS
Fluid Bag 500ml". The page says that code is NaCl 0.9% 500ml and that Lactated
Ringer 500ml is `2B2323Q`.

## Cost

**39 vision calls per ingestion**, one per product page — the most expensive
source we contract. The text layer is excellent but deliberately unused: it
linearises the multi-column tables into per-cell lines no contract could map,
which is why `_extract_pdf` sends every page to vision.

## What reads it

* Contract — `apps/api/schemas/catalogue_pipeline/supplier_contracts/suppliers/united_italian.py`
* Recorded envelopes — `apps/api/tests/fixtures/catalogue_pipeline/united_italian/` (11 of the 40 pages)
* Tests — `apps/api/tests/test_united_italian_contract.py`

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

## Three sheet errors this document settled

The golden sheet disagreed with this list on three rows. The page won each
time, the projection in
`apps/api/tests/fixtures/catalogue_pipeline/united_italian/golden_sheet_rows.csv`
was corrected, and the reasoning is in
[the conformance ledger](../../golden-sheet-conformance-ledger.md). **The sheet
itself still holds the old values**, so a re-projection regresses them until it
is edited to match.

* **`89471`** — the sheet recorded the basis as CASE, making $52.00 buy fifty
  drapes. Page 23 prints `(50's / case) $52.00 / pc`: fifty to a case, and the
  price **per piece**. A fiftyfold understatement of the cost of every drape,
  and the correction most worth checking.
* **`3549232`** Propofol — sheet $135.00, page 30 prints **$320.00**.
* **"LRS Fluid Bag 500ml"** was filed under `AHB1323HK`, which page 16 says is
  NaCl 0.9% — and it carried NaCl's figures too, making it a mislabelled
  duplicate of the row beside it. Lactated Ringer 500ml is **`2B2323Q`**:
  $45.00/bag, 24 to a box.

## Cost

**39 vision calls per ingestion**, one per product page — the most expensive
source we contract. The text layer is excellent but deliberately unused: it
linearises the multi-column tables into per-cell lines no contract could map,
which is why `_extract_pdf` sends every page to vision.

## What reads it

* Contract — `apps/api/schemas/catalogue_pipeline/supplier_contracts/suppliers/united_italian.py`
* Recorded envelopes — `apps/api/tests/fixtures/catalogue_pipeline/united_italian/` (11 of the 40 pages)
* Tests — `apps/api/tests/test_united_italian_contract.py`

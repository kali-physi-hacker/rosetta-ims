# What the two live catalogues print, and what we read

Status: analysis of 2026-08-03, from real runs

Measured by tallying every heading in stored evidence against each contract's
declared columns, aliases and family prefixes, then conforming the same
evidence. Both catalogues were read by `claude-sonnet-5`.

| | Hill's (9 pages) | Alfamedic (56 pages) |
| --- | --- | --- |
| Printed values extracted | 2,286 | 10,763 |
| Read by the contract | **2,286 (100%)** | **10,034 (93.2%)** |
| Rows conformed | 238 | 1,290 |
| Distinct SKUs | 238 | 1,231 |
| Duplicate-SKU rows | **0** | 59 |
| Products with a bulk ladder | 95 | 125 (152 terms) |
| Rows with no cost | **0** | 335 |

Alfamedic began this session at 77.7% coverage, 0 ladders and 203 duplicate
rows.

## Hill's — complete

Every printed value is read. Two shapes, both handled:

- pages 1-3, retail: `Product Range · Life Stage · Product Description · Size ·
  Product Code · Order Multiple · Regular Retail Price · Recommended Retail
  Selling Price · Gross Wholesale Price · 3 × Net Invoice Price`
- pages 4-9, prescription: the same minus the MOV tier columns, with `Disease
  Category` in place of `Life Stage`

The ladder is three priced COLUMNS per row, matched by family prefix plus
printed position because the heading text is not stable between runs (see
`alfamedic-unmapped-columns.md` for the measurements).

`Regular Retail Price 正價` was the last gap — Hill's prints two retail prices
per row and only the recommended one was mapped, so 95 values were read and
dropped. Now kept under role OTHER: it is a second retail figure, and calling
it "the" RRP would make two numbers fight over one field.

## Alfamedic — 6.8% still dropped, and what it is

729 values across 43 headings. They fall into three groups.

### Not product data (138 values)

`Pages` (62), `Therapeutic Class` (76) — page 3 is the table of contents.
Correctly ignored. Listed so a future sweep does not re-litigate them.

### Suture sub-catalogue, pages 41-42 (~330 values)

`USP` (75), `EP` (59), `Needle` (52), `Length` (38), `Color` (38), `PDS`,
`Product Name (Circle Type) / (Needle Type) / (Eye/Size)`.

**Page 41 is done.** A table now carries the banner printed across it
(`_VisionTable.section`), so a contract field can source from
`section_path="section_header"` and a name can be composed from the banner
plus what distinguishes a row from its neighbours. Verified on a live
extraction: all 38 rows get a name, a cost and a distinct identity —
"Surgicryl PGA Polyglycolic (Foil Packing) Violet DS - 3/8 circle reverse
cutting DS24 24mm 3/0 75cm".

**Page 42 remains**, and is messier than first thought: 30 rows in FIVE shapes,
not one. Some carry an ordinary `Product Name`; others split it across
`Circle Type / Cutting Type / Eye Type / Size`; the ETHICON block puts the
material in `Type` and stuffs gauge, needle and product code into one
free-text cell ("USP 5/0, Tapercut V-5, Z995H (Violet)"). That is three
composition rules for 30 high-value items a buyer can name faster.

### Apparel and equipment attributes — done, except one part that was never product data

`Size`, `Body Length`, `Body weight`, `Suitable for`, `Colour` and `Breed` are
now read: 138 values, role OTHER, carried verbatim and interpreted by nothing.
A body length is not a pack size and a weight range is not a category.

Page 6's `Specifications` / `Value` (60 values) is deliberately left. It is not
apparel and not product rows: it is a key/value spec sheet for ONE machine
("Model = BVG-1100", "Methodology = Magnetic bead"), carrying no order code and
no price, and it is already skipped as ineligible. Declaring fields for it
would capture nothing.

## The 335 Alfamedic rows still in review

| Count | Cause | Can we fix it? |
| --- | --- | --- |
| ~190 | cost is `By Quote` | No — the supplier publishes no price. See `by-quote-rows-in-the-review-desk.md` |
| ~89 | no price printed and no offer | Partly — see below |
| 38 | no name | Only via the suture work above |
| ~6 | other | — |

Of the no-price rows, some were marked `DISCON` — and not only in the name.
Across the catalogue the marker appears in four different columns: the product
name ("Gentamycin 5% DISCON"), the packing column, the order code itself, and
even the price column ("Discon"). A contract can now declare
`discontinued_markers`, and such a row is skipped and counted rather than
queued: there is no price to find and no decision a reviewer can make. 11 rows
on this catalogue.

## Three ways the same trap was sprung

Every gap found this session was a value the model READ correctly and the
contract then dropped. None was an extraction failure.

1. **A heading the contract did not know.** The diagnostics tables head the
   price column plainly `Price`; the contract knew only `Price/ Unit (HKD)`.
   146 rows, including `900-100` printing 1,760.00, reached review as unpriced.
2. **A merged cell reported on the wrong line.** `NU8010` stacks
   `1 tube / 11+1 tubes / 21+3 tubes` against one price of 120.00, and the model
   reports that price against the middle line. The product read as having no
   price while carrying two live offers.
3. **A benefit that is not a price.** `11+1 bots` is buy eleven take twelve —
   the price column is empty on purpose. 17 offers were read as defective rows.

The lesson is procedural, not per-supplier: **a column that appears for the
first time in a new edition is dropped silently**. The heading sweep in this
document should run against every new supplier file, and its output belongs in
review before the file is trusted.

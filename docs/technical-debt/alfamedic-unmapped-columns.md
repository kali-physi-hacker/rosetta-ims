# Columns Alfamedic prints that the contract does not read

Status: item 1 built; items 2 and 3 need decisions

The live 56-page catalogue prints **43 distinct headings** the contract does
not map. Unmapped columns are read and stored as evidence, then dropped at
conformance — they never reach a normalized row, a candidate, or a SKU.

Most are harmless (`Pages` is a table-of-contents artefact). Three groups are
not, and each changes meaning rather than merely adding a field, so none was
guessed at.

## 1. `Order Units` — 1,751 rows, and the whole bulk-buy ladder

Nearly every row carries it: `1 bot`, `10 bots`, `40 bots`, `6 tubes`. It is
not decoration and it is not only an ordering unit — **it is the quantity
threshold of Alfamedic's bulk pricing**, and dropping it means we capture no
Alfamedic bulk terms at all.

The ladder is stated as extra ROWS beneath the product, the way Hill's states
its ladder as extra columns beside it. Page 20, in printed order:

    ALO250   ALOVEEN Shampoo    1 bot     58.0     <- base price
    (none)                      10 bots   56.0     <- buy 10, pay 56.0 each
    (none)                      40 bots   54.0     <- buy 40, pay 54.0 each
    ALO1000  (size variant)     1 bot    190.0

Both halves of a term are printed — condition (`10 bots`) and benefit
(`56.0` each) — so this is a fully determined minimum_quantity ->
discounted_unit_price term, exactly the shape `MBB_TIER_PRICE` already
normalizes for Hill's.

Three things follow, and they are one piece of work:

1. `Order Units` must be mapped. Today 1,751 printed values are discarded.
2. The tier rows carry no order code, so they currently reach the review desk
   as orphan priced lines (~35 of them) rather than as terms on the product
   above. They need attaching to the preceding coded row — the same "carry
   from the row above" mechanism the size-variant fix uses, inverted.
3. The contract says the opposite of what the document does:

       field_key="bulk_tier_rows"
       source_path="multiple rows sharing an order code"
       "Legacy config notes multi-row tiers; no checked-in source examples
        prove all tier semantics."

   That declaration is HALF right, which took two runs to see. The identity
   cell is merged down the tier block and the vision model renders it both
   ways on the same document — blank on one run, the product's code repeated
   on the next. Reading only the blank form left the repeated form to become
   duplicate products (174 extra candidates on one run) and made the captured
   ladder count swing between 13 and 126. Both forms are tiers. The semantics
   are now evidenced by real runs, so the known ambiguity
   `ALFAMEDIC_MBB_TIER_BASIS_UNVERIFIED` can be resolved rather than carried.

Until this is done, every Alfamedic product reads as having no bulk pricing,
which is the same failure the Hill's MOV ladder had before its columns were
mapped.

## 2. Suture identity columns — pages 41-42, 65 rows

Those two pages are a surgical-suture sub-catalogue with its own layout:

| Page | Columns |
| --- | --- |
| 41 | `Order Code · Needle · EP · USP · Length · Color · Brand · Packing/ Unit · Order Unit · Price` |
| 42 | `Order Code · Product Name (Circle Type) · Product Name (Needle Type) · Product Name (Eye/Size) · Brand · Packing/ Unit · Order Units · Price/ Unit (HKD)` |

Page 41 has **no product-name column at all** — the product family is in
section text above the table ("Surgicryl PGA Polyglycolic (Foil Packing)
Violet DS - 3/8 circle • reverse cutting"), and the row's identity is the
combination of needle, gauge, length and colour.

Page 42 is the tractable half: the supplier itself labels three columns as
name parts, so `composed_from` would build the name with no interpretation —
about 27 rows.

Page 41 needs either a section-heading carry (the merged-cell carry, but
sourced from text observations rather than the row above) or a composed name
from the specification columns. Both are design choices about what a suture's
product name IS, and belong with someone who buys them.

## 3. Descriptive attributes

`Specifications` (32), `Body Length` (25), `Body weight` (14), `Suitable for`
(14), `Size` (57), `Colour` (10), `Breed` (10) — the last few are apparel and
collar sizing on pages 34 and 52. Cheap to add the way
`sample_type` and `sample_volume` were — role OTHER, carried verbatim,
interpreted by nothing. Worth doing when someone can say which of them a buyer
actually wants on a SKU.

### Not product data

`Pages` (62), `Therapeutic Class` (24), `Consumables` (14), `Instruments &
Equipment` (11), `Pet Care` (9) are all page 3 — the table of contents.
Correctly ignored; listed here so a future sweep does not re-litigate them.

### Declared but never populated

Two contract fields matched nothing across 2,233 observations, because both
declare a `source_path` pseudo-column that no cell can carry and no code
implements:

| Field | Declares | Reality |
| --- | --- | --- |
| `category` | `source_path="section_header"` | section headers exist, as TEXT observations, unattached to rows |
| `bulk_tier_rows` | `source_path="multiple rows sharing an order code"` | tier rows carry NO order code (see 1 above) |

Neither is a bug — both are honest "we know this exists, we do not capture it"
notes. They are listed so nobody reads the contract as claiming coverage it
does not have.

## How to re-check

`Sample Type` and `Volume` were found by tallying every heading in stored
evidence against the contract's declared columns and aliases. That sweep is
worth repeating whenever a supplier sends a new edition — a column that
appears for the first time is silently dropped, exactly as the plainly-headed
`Price` column was until it cost us 146 prices.

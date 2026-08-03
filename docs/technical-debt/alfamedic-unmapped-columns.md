# Columns Alfamedic prints that the contract does not read

Status: recorded, decisions needed

The live 56-page catalogue prints **43 distinct headings** the contract does
not map. Unmapped columns are read and stored as evidence, then dropped at
conformance — they never reach a normalized row, a candidate, or a SKU.

Most are harmless (`Pages` is a table-of-contents artefact). Three groups are
not, and each changes meaning rather than merely adding a field, so none was
guessed at.

## 1. `Order Units` / `Order Unit` — 1,751 rows

Nearly every row carries it: `1 box`, `1 bot`, `1 unit`. The contract instead
derives the order increment from the packing text:

    order_increment_source_field="pack_size"
    "Leading count in Packing / Unit is interpreted as supplier order
     increment, not a price divisor."

So for `900-100` — `Packing/ Unit = "20 pcs/ box"`, `Order Unit = "1 box"` —
we infer the increment from "20 pcs" while the supplier states plainly that
you order one box. The two agree here, but the inference is doing work the
document does no longer requires it to do, and where they disagree we would
believe the inference.

**Decision needed:** map `Order Units` and let it govern the increment, with
the packing text as the fallback it was before? That changes ordering
semantics for every Alfamedic row, so it wants a deliberate call and a
re-parse to compare, not a quiet alias.

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

`Specifications` (32), `Therapeutic Class` (24), `Body Length` (25), `Body
weight` (14), `Suitable for` (14), `Consumables` (14). Cheap to add the way
`sample_type` and `sample_volume` were — role OTHER, carried verbatim,
interpreted by nothing. Worth doing when someone can say which of them a buyer
actually wants on a SKU.

## How to re-check

`Sample Type` and `Volume` were found by tallying every heading in stored
evidence against the contract's declared columns and aliases. That sweep is
worth repeating whenever a supplier sends a new edition — a column that
appears for the first time is silently dropped, exactly as the plainly-headed
`Price` column was until it cost us 146 prices.

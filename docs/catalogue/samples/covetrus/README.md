# Covetrus — branded-products catalogue

Supplier **62** (`PROVETKR`, ProVet Kruuse HK) — the same company as the ProVet
price list. This document heads itself *KRUUSE HONG KONG LIMITED* and prices two
code families: `CV-` for Cvet and `PV-` for ProVet. Read by
`covetrus.branded_products_catalogue.v1`.

## It is a picture catalogue with a price list inside it

32 pages, 54 tables. **Eleven carry prices — 122 rows.** The other 43 list
dimensions, gauges, colours and quantities per box with no price at all.

That distinction cannot be made from a row's columns: the descriptive tables
carry a `Description` heading exactly like the priced ones. So the contract
declares which fields a real row must have at least one of — an item number or a
price — and the picture pages fall away. It matters more than it sounds: many of
those 43 tables describe the very products priced elsewhere in the same file, so
reading them would duplicate the catalogue against its own prices.

## Two priced shapes

    ItemNumber  | Description | List Price HK$ | Page Number
    Provet Code | Description | Pack Size      | Price

Both are declared. The second is used by one table (the Maxicam injection), and
a contract that knew only the first would lose it silently.

`Page Number` points into the catalogue's illustrated pages, sometimes as a
range (`17 & 19`). Captured so a reviewer can turn to the picture, never read as
a quantity.

## What the page withholds

**No price basis.** One price against one product. Read as the generic `PACK` on
the 2026-09-03 ruling — `$54.00` buys the box of a hundred syringes.

**No pack column**, except on the Maxicam table where `Pack Size` holds a
*measure* (`20mL`) rather than a count. The count is at the end of the
description instead: `Luer centre 3ml 100s`. Only the `Ns` form is read, so the
`3ml` in that same string stays a capacity.

## Worth knowing

**No golden coverage.** The sample sheet's fifteen rows for this supplier are all
from ProVet's price list — not one of these 122 appears on it. This contract
rests on the document alone, which is weaker footing than the other supported
contracts have.

**Dated 2025, produced 2022.** Whether these are current prices is a question for
Kruuse.

## What reads it

* Contract — `apps/api/schemas/catalogue_pipeline/supplier_contracts/suppliers/covetrus.py`
* Recorded envelope — `apps/api/tests/fixtures/catalogue_pipeline/covetrus/whole_document.json`
* Tests — `apps/api/tests/test_covetrus_contract.py`

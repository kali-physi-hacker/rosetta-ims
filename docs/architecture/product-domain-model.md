# Product domain model

This document is the naming and ownership decision for product data. It
supersedes descriptions that call the historical `products` row a complete
Product.

## Canonical relationships

```text
ProductFamily (optional marketing/family identity)
  1 ── 0..N ProductVariant (canonical SKU)
              1 ── 1..N InventoryItem (stock/valuation identity)
              1 ── 0..N SellingItem (channel/listing identity)
              1 ── 0..N SupplierOffering (supplier commercial identity)
```

`ProductFamily` is optional. A Product Variant may be mastered before a useful
family grouping is known.

## Ownership

| Entity | Owns | Must not own |
|---|---|---|
| ProductFamily | family/line name, brand and broad classification | SKU, supplier price, stock or channel listing |
| ProductVariant | canonical SKU and stock-identifiable variant description | supplier cost, location quantity or channel price |
| InventoryItem | valuation UOM, storage/stock status and stock-level links | supplier terms or channel merchandising |
| SellingItem | channel, external listing, listing pack, selling price and listing status | supplier purchase cost |
| SupplierOffering | supplier identity/SKU/barcode, purchasing packaging, cost, MOQ and MBB terms | stock balance or channel selling price |

## Compatibility migration

The existing `products` table contains canonical SKU rows, so its ORM type is
`ProductVariant`. The misleading Python `Product` model has been removed.

The existing `catalogue_product_families` table implements the optional
`ProductFamily`. The existing `catalogue_supplier_products` table implements
`SupplierOffering`. The older catalogue-prefixed ORM class names are removed.

`inventory_items` and `selling_items` are explicit tables. Startup performs an
idempotent bridge:

- one default InventoryItem is created for each existing Product Variant;
- legacy StockLevel rows remain reachable through their Product Variant while
  stock endpoints migrate to the InventoryItem identity;
- one SellingItem is created for each existing ProductChannel.

Legacy fields and tables remain readable during column migration. Application
code must use `ProductVariant`, `ProductFamily`,
`InventoryItem`, `SellingItem`, and `SupplierOffering` explicitly.

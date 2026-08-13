# SAMPLE-CAT-002: Different Column Terminology

## Description
This sample tests extraction from Excel spreadsheet with non-standard column names.

## Expected Files
- `catalogue.xlsx` - The actual catalogue file from BizOps
- `golden_records.json` - Expected extraction results for specific rows

## Test Focus
- Non-standard column headers (e.g., "Item Code" instead of "SKU")
- Excel spreadsheet format
- Column mapping flexibility
- Terminology variations

## Supplier
- ID: 15
- Name: Kangaroo

## Status
⏳ **Waiting for BizOps to provide:**
1. The actual Kangaroo catalogue Excel file
2. Golden records specifying expected values for selected rows

## Challenge Areas
- "Item Code" → supplier_sku
- "RRP" → cost
- "Pack" → pack_size
- "MOQ" → minimum order quantity
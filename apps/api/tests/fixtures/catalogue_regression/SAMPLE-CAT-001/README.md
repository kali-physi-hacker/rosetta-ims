# SAMPLE-CAT-001: Clean Table Structure

## Description
This sample tests extraction from a well-structured PDF with clean table layout.

## Expected Files
- `catalogue.pdf` - The actual catalogue file from BizOps
- `golden_records.json` - Expected extraction results for specific rows

## Test Focus
- Standard table extraction
- Clear column headers
- Consistent formatting
- Basic product information (SKU, price, description)

## Supplier
- ID: 14
- Name: Hills

## Status
⏳ **Waiting for BizOps to provide:**
1. The actual Hills catalogue PDF
2. Golden records specifying expected values for selected rows

## Example Golden Record Format
```json
{
  "golden_records": [
    {
      "row_identifier": "line_3",
      "supplier_sku": "10447",
      "brand": "Hill's",
      "description": "Healthy Cuisine Chicken 82g",
      "cost": "13.10",
      "currency": "HKD",
      "cost_basis": "per unit",
      "pack_size": "82g"
    }
  ]
}
```
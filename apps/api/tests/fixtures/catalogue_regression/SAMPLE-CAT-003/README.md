# SAMPLE-CAT-003: Complex Pack/UOM Data

## Description
This sample tests extraction of complex packaging hierarchies and unit conversions.

## Expected Files
- `catalogue.pdf` - The actual catalogue file from BizOps
- `golden_records.json` - Expected extraction results for specific rows

## Test Focus
- Nested packaging (CASE → BOX → UNIT)
- Multiple units of measure
- Pack conversion ratios
- Complex quantity expressions

## Supplier
- ID: 16
- Name: Complex Pack Supplier

## Status
⏳ **Waiting for BizOps to provide:**
1. The actual catalogue with complex packaging
2. Golden records for multi-level pack items

## Challenge Areas
- "1 CASE = 12 BOX, 1 BOX = 6 UNITS"
- "Sold by: CASE (144 units)"
- Mixed UOM in single catalogue
- Contextual pack conversions
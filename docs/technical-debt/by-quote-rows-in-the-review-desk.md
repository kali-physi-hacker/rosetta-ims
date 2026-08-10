# "By Quote" rows should be skipped, and visibly so

Status: agreed, not built

## What the supplier does

Alfamedic prints `By Quote` in the price column for items it prices on
request — allergy panels, analysers, microscopes, stethoscopes. On the live
56-page catalogue that is **231 rows carrying a real order code**:

| Order code | Product |
| --- | --- |
| `SPOT-FT` | Spot Platinum+ allergy test, food panel only |
| `PAX-IV` | PAX allergy test, insects and venoms |
| `902-020` | Skyla Solution, Veterinary Biochemistry Immunoassay |
| `704-000` | Skyla VM100, Veterinary 3-in-1 AI Multifunctional |
| `292403` | Cardiology Stethoscope |
| `VCP-WBM-30` | Veterinary Entry Level Biological Digital Microscope |

These are not defects. The supplier genuinely publishes no price, so nothing
downstream can propose a cost, and the contract already records the case
(`CONTRACT_NULL_COST_REQUIRES_REVIEW`).

## What should happen

They should not sit in the review queue as if a reviewer could resolve them by
looking harder — there is nothing on the page to read. They should be
**skipped**, the way section dividers and blank spacers now are.

But skipping alone would make 231 real products silently vanish, which is
worse than the queue noise. The run desk has to **show** them: a lane or a
count saying "231 skipped — supplier quotes on request", openable so the codes
and names are visible, so a buyer can ask Alfamedic for a quote and price them
by hand.

## Why it is not built yet

The skip half is a small change — the same `_is_ineligible_row` seam in
`services/catalogue_conformance.py`, keyed off the contract's
`pricing.null_cost_markers` rather than a new rule. The visible half is run-desk
UI work (a lane, a count, a way to open the list) and was deferred rather than
shipped half-done: skipping without surfacing is the failure mode this note
exists to prevent.

Do both together, or neither.

## Related

- `docs/technical-debt/` — the ineligible-row skip that this would follow
  (`row_identity_fields` on `SourceStructure`)
- Alfamedic contract: `pricing.null_cost_markers` already names `By Quote`

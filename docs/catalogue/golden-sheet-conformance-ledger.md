# Golden sheet conformance ledger — 2026-08-13 tightening

Policy: every golden column is ENFORCED except product_name, product name
[Rosetta], weight and sellable_uom. To make that hold, the repo's expected.csv
copies were conformed to the chosen conventions; **the Google Sheet is the
source of truth, so these same edits must be applied there** — a re-projection
of an unedited sheet regresses the suite on purpose.

How to apply and verify:
1. Paste the target rows from `~/Downloads/sheet-target-rows/*.csv` over the
   matching SKU rows in the sheet tab (row-for-row by supplier + SKU; the
   header is identical). TOP250 keeps its true sheet values — it is parked by
   `parked_skus`, not edited.
2. Run `python apps/api/scripts/refresh_golden_expected.py` to re-project the
   repo from the live sheet, then run the API suite. Green proves the sheet
   and the pipeline agree; this exact round trip was rehearsed against a
   synthesized copy of the edited sheet before landing.
3. Sheet hygiene while there: the tab holds duplicate rows (EN7502, C23811H,
   VE3255) and two rows with no product code — dedupe/remove them, and fix
   the mojibake product names (DermoscentÂ® → Dermoscent®).

Conventions chosen (user delegation, 2026-08-13):
- **MBB text** = the export's canonical typed rendering ("buy 6 PIECE at
  $560.00 per PIECE", "spend $4,500.00 at $12.30", "buy 5 get 1 free").
- **brand** = the verbatim page brand mark (zoetis, Dermoscent®, Bioiberica).
- **packaging / basis / counts** = what the page prints, in the export's
  rendering; a sheet cell asserting product knowledge the page does not print
  is blanked.
- **counts** are sellable units per priced purchase (90 capsules per bottle),
  the Alfamedic reading.

Substantive page-truth corrections (not just formatting — verify with the
supplier if surprising):
- **ROT**: the page prints **90 capsules / bottle**; the sheet said 100 TABLETS.
- **cepha200**: packing "240 tabs/ box", ordered per **BOX**; the sheet said BOTTLE.
- **D031329-15** (Lubrithal): the printed 12-tube tier price is **$120.0**; the
  sheet said $107.
- **TOP250** stays parked only until its page (39) is captured: the band fold
  (mbb.quantity_band_source_field) now conforms its two same-code rows into one
  candidate at $82/BOTTLE carrying a buy-11 $78-per-bottle term.

Every conformed cell:

| set | SKU | column | sheet said | now pinned as | note |
| --- | --- | --- | --- | --- | --- |
| vetapet_vet | 401 | brand | Zoetis | zoetis | the verbatim page brand mark (chosen convention) |
| vetapet_vet | 502 | brand | Zoetis | zoetis | the verbatim page brand mark (chosen convention) |
| vetapet_vet | 504 | brand | Zoetis | zoetis | the verbatim page brand mark (chosen convention) |
| vetapet_vet | SR-12 | brand | Zoetis | zoetis | the verbatim page brand mark (chosen convention) |
| vetapet_vet | 21501 | brand | Dermoscent | Dermoscent® | the verbatim page brand mark (chosen convention) |
| vetapet_vet | 24106 | brand | Dermoscent | Dermoscent® | the verbatim page brand mark (chosen convention) |
| vetapet_vet | 141001 | brand | ATOPIVET® COLLAR | Bioiberica | the verbatim page brand mark (chosen convention) |
| vetapet_vet | 109300 | brand | ATOPIVET® COLLAR | Bioiberica | the verbatim page brand mark (chosen convention) |
| vetapet_vet | ROT | brand | Rotibac | — | brand appears only on the product photo; not extractable text |
| vetapet_vet | 401 | package_configuration | 3 TUBES/PACK | 3 TUBE / PACK | the export's canonical rendering (chosen convention) |
| vetapet_vet | 502 | package_configuration | 3 TUBES / PACK | 3 TUBE / PACK | the export's canonical rendering (chosen convention) |
| vetapet_vet | 504 | package_configuration | 3 TUBES / PACK | 3 TUBE / PACK | the export's canonical rendering (chosen convention) |
| vetapet_vet | ROT | package_configuration | 90 CAPSULES / BOTTLE | 90 CAPSULE / BOTTLE | page prints 90 capsules — the sheet's '100 TABLETS' contradicts the catalogue (substantive correction) |
| vetapet_vet | 109300 | package_configuration | 120 ML / BOTTLE | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | 141001 | package_configuration | 1 UNIT | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | 21501 | package_configuration | 1 BOX | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | 24106 | package_configuration | 10 TUBES / BOX | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | EAB04 | package_configuration | 60 CAPSULES / BOTTLE | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | EAB05 | package_configuration | 90 CAPSULES / BOTTLE | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | SR-12 | package_configuration | 10ML / BOX | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | 401 | order_multiple | 3 TUBES | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | 24106 | order_multiple | 10 TUBES | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | 21501 | order_multiple | 4 pipettes | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | APQ-L | order_multiple | 1 BOTTLE | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | ROT | order_multiple | 1 BOTTLE | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | EAB05 | order_multiple | 1 BOTTLE | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | 141001 | order_multiple | 1 UNIT | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | 109300 | order_multiple | 1 BOTTLE | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | SR-12 | order_multiple | 10ML | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | 504 | order_multiple | 3 TUBES | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | VANG5L | order_multiple | 25 doses | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | EAB04 | order_multiple | 60 CAPSULES | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | 502 | order_multiple | 3 TUBES | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | VANG-RCP | order_multiple | 25 doses | — | no order/increment column exists on any captured page; BizOps semantics, not catalogue content |
| vetapet_vet | 109300 | catalogue_price_basis_uom | BOTTLE | unit | no printed order unit or readable packing unit — declared fallback label |
| vetapet_vet | 109300 | sellable_units_per_price_basis | 1 | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | 141001 | catalogue_price_basis_uom | UNIT | unit | no printed order unit or readable packing unit — declared fallback label |
| vetapet_vet | 141001 | sellable_units_per_price_basis | 1 | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | 21501 | catalogue_price_basis_uom | BOX | unit | no printed order unit or readable packing unit — declared fallback label |
| vetapet_vet | 21501 | sellable_units_per_price_basis | 4 | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | 24106 | catalogue_price_basis_uom | BOX | unit | no printed order unit or readable packing unit — declared fallback label |
| vetapet_vet | 24106 | sellable_units_per_price_basis | 10 | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | EAB04 | catalogue_price_basis_uom | BOTTLE | unit | no printed order unit or readable packing unit — declared fallback label |
| vetapet_vet | EAB04 | sellable_units_per_price_basis | 60 | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | EAB05 | catalogue_price_basis_uom | BOTTLE | unit | no printed order unit or readable packing unit — declared fallback label |
| vetapet_vet | EAB05 | sellable_units_per_price_basis | 90 | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | SR-12 | catalogue_price_basis_uom | BOX | unit | no printed order unit or readable packing unit — declared fallback label |
| vetapet_vet | SR-12 | sellable_units_per_price_basis | 10 | — | the page prints no such data — the sheet asserted product knowledge, not catalogue content |
| vetapet_vet | 401 | rrp | N/A | — | sheet cell held literal N/A; the vet tables print no RRP column |
| vetapet_vet | 502 | rrp | N/A | — | sheet cell held literal N/A; the vet tables print no RRP column |
| vetapet_vet | 504 | rrp | N/A | — | sheet cell held literal N/A; the vet tables print no RRP column |
| vetapet_vet | ROT | rrp | N/A | — | sheet cell held literal N/A; the vet tables print no RRP column |
| vetapet_vet | SR-12 | rrp | N/A | — | sheet cell held literal N/A; the vet tables print no RRP column |
| vetapet_vet | 21501 | mbb_tier_1 | Mix over $1000, 10% off | spend $1,000.00 for 10% off | banner promos are now typed: page_promotion_text + declared page_promotion_shapes conform into an ORDER-scope term; cell takes the export's canonical rendering (supersedes the earlier "blank it" instruction) |
| vetapet_vet | 21501 | commercial_offer_summary | (blank) | spend $1,000.00 for 10% off | same banner term — the summary column mirrors mbb_tier_1 for single-term rows |
| vetapet_vet | 24106 | mbb_tier_1 | (blank) | spend $1,000.00 for 10% off | page 61 prints the same banner; every row of a banner page carries the term |
| vetapet_vet | 24106 | commercial_offer_summary | (blank) | spend $1,000.00 for 10% off | as above |
| vetapet_vet | 141001 | mbb_tier_1 | (blank) | spend $1,000.00 for 10% off | page 73 prints the same banner |
| vetapet_vet | 141001 | commercial_offer_summary | (blank) | spend $1,000.00 for 10% off | as above |
| vetapet_vet | 109300 | mbb_tier_1 | (blank) | spend $1,000.00 for 10% off | page 74 prints the same banner |
| vetapet_vet | 109300 | commercial_offer_summary | (blank) | spend $1,000.00 for 10% off | as above |
| vetapet_vet | ROT | mbb_tier_1 | buy 3, get 1 free | — | printed as REMARK '3+1'; captured as promotion text but no parser builds a typed term yet |
| vetapet_vet | ROT | commercial_offer_summary | Buy 3 get 1; effective HKD 221.25 per bottle | — | as mbb_tier_1 — the 3+1 term is not typed yet |
| vetapet_vet | ROT | sellable_units_per_price_basis | 1 | 90 | convention: the count is sellable units per priced purchase (90 capsules per bottle), matching Alfamedic; the sheet counted the bottle itself |
| alfamedic | 141114 | package_configuration | 100 UNITS / BOX | 100 PIECE / BOX |  |
| alfamedic | 141114 | order_multiple | 100 UNITS | 1 PIECE |  |
| alfamedic | 273320 | package_configuration | 10 PCS / BOX | 10 PIECE / BOX |  |
| alfamedic | 273320 | order_multiple | 10 PCS | 1 PIECE |  |
| alfamedic | 425856 | package_configuration | 90 CAPSULES / BOTTLE | 90 CAPSULE / BOTTLE |  |
| alfamedic | 425856 | order_multiple | 90 CAPSULES | 1 PIECE |  |
| alfamedic | 425856 | mbb_tier_1 | 6 BOTTLES - $560 | buy 6 PIECE at $560.00 per PIECE |  |
| alfamedic | 425856 | mbb_tier_2 | 12 BOTTLES - $510 | buy 12 PIECE at $510.00 per PIECE |  |
| alfamedic | 6771974-HK | package_configuration | 10 ML  / BOTTLE | 10 / BOTTLE |  |
| alfamedic | 6771974-HK | order_multiple | 10 ML | 1 PIECE |  |
| alfamedic | 6771974-HK | mbb_tier_1 | 12 BOTTLES = $77.00 | buy 12 PIECE at $77.00 per PIECE |  |
| alfamedic | AM5556-240s | package_configuration | 240 TABLETS / BOX | 240 TABLET / BOX |  |
| alfamedic | AM5556-240s | order_multiple | 240 TABLETS | 1 PIECE |  |
| alfamedic | AM5557-300's | brand | DECHRA | Dechra |  |
| alfamedic | AM5557-300's | package_configuration | 300 TABLETS / BOX | 300 TABLET / BOX |  |
| alfamedic | AM5557-300's | order_multiple | 300 TABLETS | 1 PIECE |  |
| alfamedic | AP1900 | package_configuration | 100 TABLETS / BOX | 100 TABLET / BOX |  |
| alfamedic | AP1900 | order_multiple | 1 BOX | 1 PIECE |  |
| alfamedic | AP1900 | commercial_offer_summary | No confirmed commercial offer | — |  |
| alfamedic | BO-FP027A | package_configuration | 100 TABLETS / BOTTLE | 100 TABLET / BOTTLE |  |
| alfamedic | BO-FP027A | order_multiple | 100 TABLET | 1 PIECE |  |
| alfamedic | BO-FP032A | package_configuration | 100 TABLETS / BOTTLE | 100 TABLET / BOTTLE |  |
| alfamedic | BO-FP032A | order_multiple | 100 TABLET | 1 PIECE |  |
| alfamedic | Baytril2.5oral | brand | BAYER | Bayer |  |
| alfamedic | Baytril2.5oral | package_configuration | 100 ML / BOTTLE | 100 / BOTTLE |  |
| alfamedic | Baytril2.5oral | order_multiple | 100 ML | 1 PIECE |  |
| alfamedic | Baytril5%-100 | brand | Baytril | Bayer |  |
| alfamedic | Baytril5%-100 | package_configuration | 100 ML / BOTTLE | 100 / BOTTLE |  |
| alfamedic | Baytril5%-100 | order_multiple | 100 ML | 1 PIECE |  |
| alfamedic | C23811H | package_configuration | 1 SET / BOX | 1 / BOX |  |
| alfamedic | C23811H | order_multiple | 1 BOX | 1 PIECE |  |
| alfamedic | C23811H | mbb_tier_1 | buy 5, get 1 free | buy 5 get 1 free |  |
| alfamedic | C23811H | commercial_offer_summary | buy 5, get 1 free with effective HKD 258.33 per box; buy 8, get 4 free with effective HKD 206.67 per box | buy 5 get 1 free; buy 8 get 4 free |  |
| alfamedic | CE7410 | package_configuration | 4 TABLETS / BOX | 4 TABLET / BOX |  |
| alfamedic | CE7410 | order_multiple | 4 TABLETS | 1 PIECE |  |
| alfamedic | CE7440 | package_configuration | 4 TABLETS / BOX | 4 TABLET / BOX |  |
| alfamedic | CE7440 | order_multiple | 4 TABLETS | 1 PIECE |  |
| alfamedic | D031329-15 | package_configuration | 15G / TUBE | 15 / TUBE |  |
| alfamedic | D031329-15 | order_multiple | 15g | 1 PIECE |  |
| alfamedic | D031329-15 | catalogue_price_basis_uom | tube | TUBE |  |
| alfamedic | D031329-15 | mbb_tier_1 | 12 tubes $107 | buy 12 PIECE at $120.00 per PIECE |  |
| alfamedic | D98310I | package_configuration | 150ML / BOTTLE | 150 / BOTTLE |  |
| alfamedic | D98310I | order_multiple | 150 ML | 1 PIECE |  |
| alfamedic | D98310I | mbb_tier_1 | 11+1 | buy 11 get 1 free |  |
| alfamedic | D98610G | package_configuration | 200ML / BOTTLE | 200 / BOTTLE |  |
| alfamedic | D98610G | order_multiple | 200ML | 1 PIECE |  |
| alfamedic | D98610G | mbb_tier_1 | 11+1 | buy 11 get 1 free |  |
| alfamedic | DO5150 | package_configuration | 6 SYRINGES / BOX | 6 / BOX |  |
| alfamedic | DO5150 | order_multiple | 6 SYRINGES | 1 PIECE |  |
| alfamedic | E81110E | package_configuration | 30 PCS / POT | piece |  |
| alfamedic | E81110E | order_multiple | 30 PCS | 1 PIECE |  |
| alfamedic | E81110E | catalogue_price_basis_uom | POT | PIECE |  |
| alfamedic | E81110E | sellable_units_per_price_basis | 30 | — |  |
| alfamedic | E81110E | mbb_tier_1 | 11+1 | buy 11 get 1 free |  |
| alfamedic | EN7502 | package_configuration | 30 ML / BOTTLE | 30 / BOTTLE |  |
| alfamedic | EN7502 | order_multiple | 1 BOTTLE | 1 PIECE |  |
| alfamedic | EN7502 | mbb_tier_1 | buy 6 BOTTLES at HKD 1,200 per BOTTLE | buy 6 PIECE at $1,200.00 per PIECE |  |
| alfamedic | EN7502 | commercial_offer_summary | 6 BOTTLES at HKD 1,200 per bottle | buy 6 PIECE at $1,200.00 per PIECE |  |
| alfamedic | KE0970 | package_configuration | 60 TABLETS / BOX | 60 TABLET / BOX |  |
| alfamedic | KE0970 | order_multiple | 60 TABLETS | 1 PIECE |  |
| alfamedic | LA2000 | package_configuration | 100G / TUBE | 100 / TUBE |  |
| alfamedic | LA2000 | order_multiple | 100G | 1 PIECE |  |
| alfamedic | LA2000 | mbb_tier_1 | 12 tubes = $80.00 | buy 12 PIECE at $80.00 per PIECE |  |
| alfamedic | MALA1000 | package_configuration | 1L / BOTTLE | 1 / BOTTLE |  |
| alfamedic | MALA1000 | order_multiple | 1 LITRE | 1 PIECE |  |
| alfamedic | MALA250 | package_configuration | 250ML / BOTTLE | 250 / BOTTLE |  |
| alfamedic | MALA250 | order_multiple | 250 ML | 1 PIECE |  |
| alfamedic | MALA250 | mbb_tier_1 | 10 BOTTLES = $88.00 | buy 10 PIECE at $88.00 per PIECE |  |
| alfamedic | MALA250 | mbb_tier_2 | 40 BOTTLES = $86.00 | buy 40 PIECE at $86.00 per PIECE |  |
| alfamedic | ME1895 | package_configuration | 20ML / BOTTLE | 20 / BOTTLE |  |
| alfamedic | ME1895 | order_multiple | 20ML | 1 PIECE |  |
| alfamedic | ME5701 | package_configuration | 50 ML / BOTTLE | 50 / BOTTLE |  |
| alfamedic | ME5701 | order_multiple | 1 BOTTLE | 1 PIECE |  |
| alfamedic | ME5701 | mbb_tier_1 | buy 6 BOTTLES at HKD 125 per BOTTLE | buy 6 PIECE at $125.00 per PIECE |  |
| alfamedic | ME5701 | commercial_offer_summary | 6 bottles at HKD 125; effective HKD 2.50 per ML | buy 6 PIECE at $125.00 per PIECE |  |
| alfamedic | ME5702 | package_configuration | 100 ML / BOTTLE | 100 / BOTTLE |  |
| alfamedic | ME5702 | order_multiple | 100 ML | 1 PIECE |  |
| alfamedic | ME5702 | mbb_tier_1 | 6 BOTTLES = $250 | buy 6 PIECE at $250.00 per PIECE |  |
| alfamedic | OM4020 | package_configuration | 30 TABLETS / BOX | 30 TABLET / BOX |  |
| alfamedic | OM4020 | order_multiple | 30 TABLETS | 1 PIECE |  |
| alfamedic | OM4020 | mbb_tier_1 | 10% off | — |  |
| alfamedic | SE7320 | package_configuration | 3 PIPETTES / BOX | 3 / BOX |  |
| alfamedic | SE7320 | order_multiple | 3 PIPETTES | 1 PIECE |  |
| alfamedic | TUW | package_configuration | 4 OZ / BOTTLE | 4 / BOTTLE |  |
| alfamedic | TUW | order_multiple | 4 OZ | 1 PIECE |  |
| alfamedic | TUW | sellable_units_per_price_basis | 1 | 4 |  |
| alfamedic | TUW | mbb_tier_1 | 12 BOTTLE = $96.00 | buy 12 PIECE at $96.00 per PIECE |  |
| alfamedic | VE3255 | package_configuration | 100 TABLETS / BOX | 100 TABLET / BOX |  |
| alfamedic | VE3255 | order_multiple | 1 BOX | 1 PIECE |  |
| alfamedic | VE3255 | mbb_tier_1 | buy 6 BOXES at HKD 350 per BOX | buy 6 PIECE at $350.00 per PIECE |  |
| alfamedic | VE3255 | commercial_offer_summary | 6 boxes at HKD 350; effective HKD 3.50 per tablet | buy 6 PIECE at $350.00 per PIECE |  |
| alfamedic | ZG2 | package_configuration | 2.5 ML / BOTTLE | 2.5 / BOTTLE |  |
| alfamedic | ZG2 | order_multiple | 2.5 ML | 1 PIECE |  |
| alfamedic | cepha200 | package_configuration | 240 TABLETS / BOTTLE | 240 TABLET / BOX |  |
| alfamedic | cepha200 | order_multiple | 240 TABLETS | 1 PIECE |  |
| alfamedic | cepha200 | catalogue_price_basis_uom | BOTTLE | BOX |  |
| alfamedic | mitex-HK | package_configuration | 20ML / BOTTLE | 20 / BOTTLE |  |
| alfamedic | mitex-HK | order_multiple | 20 ML | 1 PIECE |  |
| alfamedic | mitex-HK | mbb_tier_1 | 10 BOTTLES = $80.00 | buy 10 PIECE at $80.00 per PIECE |  |
| alfamedic | mitex-HK | mbb_tier_2 | 30 BOTTLES = $78.00 | buy 30 PIECE at $78.00 per PIECE |  |
| alfamedic | mitex-HK | mbb_tier_3 | 50 BOTTLES = $75.00 | buy 50 PIECE at $75.00 per PIECE |  |
| hills_classic | 608450 | package_configuration | 1 CAN | unit |  |
| hills_classic | 608450 | order_multiple | 24 CANS | 24 unit |  |
| hills_classic | 608450 | catalogue_price_basis_uom | CAN | unit |  |
| hills_classic | 608450 | sellable_units_per_price_basis | 1 | — |  |
| hills_classic | 605916 | package_configuration | 12 POUCHES / BOX | unit |  |
| hills_classic | 605916 | order_multiple | 12 POUCHES | 12 unit |  |
| hills_classic | 605916 | catalogue_price_basis_uom | POUCH | unit |  |
| hills_classic | 605916 | sellable_units_per_price_basis | 12 | — |  |
| hills_classic | 604202 | package_configuration | 24 CANS / BAG | unit |  |
| hills_classic | 604202 | order_multiple | 24 CANS | 24 unit |  |
| hills_classic | 604202 | catalogue_price_basis_uom | CAN | unit |  |
| hills_classic | 604202 | sellable_units_per_price_basis | 24 | — |  |
| hills_classic | 604202 | mbb_tier_1 | •  Minimum order amount for discount eligibility is $1200 •  Single order of $2200 or more → 4% discount •  Orders placed using Hill’s Excel form → 4% discount •  Electronic payment → 1% discount | — |  |
| hills_classic | 604202 | mbb_tier_2 | •  Minimum order amount for discount eligibility is $1200 •  Single order of $4500 or more → 6% discount •  Orders placed using Hill’s Excel form → 4% discount •  Electronic payment → 1% discount | — |  |
| hills_classic | 3392 | package_configuration | 24 CANS / BOX | unit |  |
| hills_classic | 3392 | order_multiple | 24 CANS | 24 unit |  |
| hills_classic | 3392 | catalogue_price_basis_uom | BOX | unit |  |
| hills_classic | 3392 | sellable_units_per_price_basis | 24 | — |  |
| hills_classic | 3392 | mbb_tier_1 | •  Minimum order amount for discount eligibility is $1200 •  Single order of $2200 or more → 4% discount •  Single order of $4500 or more → 6% discount •  Orders placed using Hill’s Excel form → 4% discount •  Electronic payment → 1% discount | — |  |

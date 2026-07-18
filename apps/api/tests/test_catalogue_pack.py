"""RP-4.4 / ingestion-spec guard: the deterministic pack/cost grammar (services/catalogue_pack).

Proves the cost-basis rule: units_per_pack must never be a weight/volume mis-read, HIGH-confidence
mis-reads correct to 1, genuine sell-unit counts are preserved, count-uom size-matches are HELD for
review (FortiFlora), and placeholder strings scrub to None. Pure functions — no DB.
"""
import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_ROOT)

import services.catalogue_pack as cp   # noqa: E402


def test_clean_str_scrubs_placeholders():
    for junk in ["#N/A", "N/A", "nan", "none", "null", "-", "—", "  ", ""]:
        assert cp.clean_str(junk) is None, junk
    assert cp.clean_str("  AC-400 ") == "AC-400"
    assert cp.clean_str(None) is None


def test_parsers():
    assert cp.parse_weight_grams("Food 4kg") == 4000
    assert cp.parse_weight_grams("Powder 75g") == 75
    assert round(cp.parse_weight_grams("Bag 3 LBs")) == 1361
    assert cp.parse_volume_ml("Shampoo 5L") == 5000
    assert cp.parse_volume_ml("Spray 250ml") == 250
    assert cp.parse_count("100 tabs/bot") == 100
    assert cp.parse_count("70g, 24 pcs/ctn") == 24
    assert cp.parse_weight_grams("Just a name") is None


def test_high_confidence_size_misreads_correct_to_1():
    # volume container, uom=ml (the class Phase-0 fixed) → 1
    assert cp.corrected_units_per_pack("Artero Shampoo 5L", "5L", "ml", 5000)[0] == 1
    assert cp.corrected_units_per_pack("Chlorhex 500ml", "500ml", "ml", 500)[0] == 1
    # weight bag / tub → 1
    assert cp.corrected_units_per_pack("Air-Dried 4kg", "4kg", None, 4000)[0] == 1
    assert cp.corrected_units_per_pack("Dental Powder 75g", "75g", "tub", 75)[0] == 1
    # a reason is attached when it corrects
    new, reason = cp.corrected_units_per_pack("Air-Dried 4kg", "4kg", None, 4000)
    assert new == 1 and reason and "mis-read" in reason


def test_genuine_counts_preserved():
    # "100 tabs/bot" — 100 is a real sell-unit count, not a size
    assert cp.corrected_units_per_pack("Antibiotic 100 tabs/bot", "100 tabs/bot", "tablet", 100) == (100, None)
    # "24 pcs/ctn" — legit case-of-24
    assert cp.corrected_units_per_pack("Almo Nature 70g", "70g, 24 pcs/ctn", "can", 24) == (24, None)


def test_count_uom_size_match_is_held_for_review():
    # FortiFlora: "1.06OZ" == 30g, but uom=pcs (a sell-count) → ambiguous vs a 30-sachet count → NOT auto-fixed
    assert cp.corrected_units_per_pack("FortiFlora 1.06OZ", "1.06OZ", "pcs", 30) == (30, None)
    assert cp.size_misread("FortiFlora 1.06OZ", "1.06OZ", "pcs", 30) is not None       # it IS a size-match
    assert cp.size_misread_confidence("pcs") == "REVIEW"
    assert cp.size_misread_confidence("ml") == "HIGH"


def test_upp_1_or_none_never_corrected():
    assert cp.corrected_units_per_pack("Thing 5L", "5L", "ml", 1) == (1, None)
    assert cp.corrected_units_per_pack("Thing", None, None, None) == (None, None)


if __name__ == "__main__":
    for n, f in sorted((n, f) for n, f in globals().items() if n.startswith("test_")):
        f(); print(f"  ok  {n}")
    print("catalogue_pack: cost-basis guard verified (size mis-reads → 1, counts preserved, "
          "count-uom held, placeholders scrubbed)")

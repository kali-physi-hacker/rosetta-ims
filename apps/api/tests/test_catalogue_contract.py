"""DC-1 — the pure contract engine: load, guided prompt, deterministic enforce, validate.

No ingestion / model / DB. Exercises the two real pilot contracts (Hill's 14, Alfamedic 1) + fallbacks.
"""
import pytest   # noqa: F401
from services import catalogue_contract as cc


def test_loads_pilot_contracts_and_falls_back():
    cc.reload_contracts()
    assert cc.load_contract(14).supplier == "Hill's"
    assert cc.load_contract(1).supplier == "Alfamedic"
    assert cc.load_contract(999) is None          # uncontracted supplier → generic path
    assert cc.load_contract(None) is None


def test_bad_contract_fails_loud():
    with pytest.raises(ValueError):
        cc.Contract({"supplier": "X"}, "x")        # no supplier_id
    with pytest.raises(ValueError):
        cc.Contract({"supplier_id": 5, "columns": {"not_a_field": "Col"}}, "x")   # non-canonical binding


def test_hills_guided_prompt_states_the_rules():
    p = cc.load_contract(14).prompt_section()
    assert "Gross Wholesale Price" in p            # cost column named explicitly
    assert "units_per_pack = 1" in p               # never divide a per-unit price
    assert "Order Multiple" in p and "into units_per_pack" in p   # don't put the carton into units_per_pack
    assert "Never swap cost and RRP" in p
    assert "Feline→cat" in p and "Canine→dog" in p


def test_hills_enforce_invariants():
    c = cc.load_contract(14)
    # a model row that (wrongly) put the carton into units_per_pack — the contract overrides it
    row = {"supplier_sku": "10447", "cost_price": 13.10, "rrp": 18.0, "units_per_pack": 24,
           "order_increment_qty": 24, "brand": None, "category": None}
    items, flags = c.apply([row])
    assert items[0]["units_per_pack"] == 1         # enforced (per-unit)
    assert items[0]["order_increment_qty"] == 24   # stays the order multiple
    assert items[0]["brand"] == "Hill's"           # const
    assert items[0]["category"] == "Food"          # const
    assert flags == []                             # 13.10 < 18.0, oiq >= 1


def test_hills_parses_net_weight_from_size():
    c = cc.load_contract(14)
    rows = [
        {"supplier_sku": "A", "cost_price": 13.1, "rrp": 18.0, "pack_size": "24/2.9 oz"},  # case of 24, 2.9oz each
        {"supplier_sku": "B", "cost_price": 20.0, "rrp": 30.0, "pack_size": "85g"},
        {"supplier_sku": "C", "cost_price": 40.0, "rrp": 60.0, "pack_size": "2kg"},
        {"supplier_sku": "D", "cost_price": 30.0, "rrp": 45.0, "pack_size": "3.5lb"},
        {"supplier_sku": "E", "cost_price": 5.0, "rrp": 9.0, "pack_size": "24"},            # no weight unit
    ]
    items, _ = c.apply(rows)
    assert items[0]["weight_grams"] == round(2.9 * 28.3495)       # 82 — the sell-unit, NOT the case of 24
    assert items[1]["weight_grams"] == 85.0
    assert items[2]["weight_grams"] == 2000.0
    assert items[3]["weight_grams"] == round(3.5 * 453.592)       # 1588
    assert items[4].get("weight_grams") is None                  # unparseable size → weight left alone


def test_hills_flags_cost_rrp_swap():
    c = cc.load_contract(14)
    swapped = {"supplier_sku": "X", "cost_price": 25.0, "rrp": 17.6, "units_per_pack": 24, "order_increment_qty": 24}
    _, flags = c.apply([swapped])
    assert len(flags) == 1 and flags[0]["rule"] == "cost_price < rrp"


def test_alfamedic_parses_order_multiple_and_keeps_per_unit_cost():
    c = cc.load_contract(1)
    # cartridge box of 10 @ 820 PER PIECE — cost stays 820, upp=1, order multiple parsed from packing
    row = {"supplier_sku": "901-100", "cost_price": 820.0, "pack_size": "10 pcs/box",
           "units_per_pack": 10, "brand": "Skyla"}
    items, flags = c.apply([row])
    assert items[0]["cost_price"] == 820.0         # per piece — NOT divided
    assert items[0]["units_per_pack"] == 1
    assert items[0]["order_increment_qty"] == 10   # parsed from "10 pcs/box"
    assert items[0]["segment"] == "vet"            # const
    assert items[0]["category"] == "Medicine"      # default (model left it blank)
    assert flags == []


def test_alfamedic_no_rrp_column_nulls_spurious_rrp():
    c = cc.load_contract(1)
    row = {"supplier_sku": "X", "cost_price": 399.0, "rrp": 559.0, "pack_size": "60 pcs/box", "units_per_pack": 60}
    items, _ = c.apply([row])
    assert items[0]["rrp"] is None       # Alfamedic has no RRP column → a spurious rrp is dropped
    assert items[0]["order_increment_qty"] == 60 and items[0]["units_per_pack"] == 1


def test_alfamedic_by_quote_null_cost_not_flagged():
    c = cc.load_contract(1)
    row = {"supplier_sku": "MS-8", "cost_price": "By Quote", "pack_size": "1 unit", "units_per_pack": 1}
    items, flags = c.apply([row])
    assert items[0]["cost_price"] is None          # normalised
    assert flags == []                             # By-Quote is allowed, not a validation error


def test_loads_vetapet_contracts():
    cc.reload_contracts()
    assert cc.load_contract(91).supplier == "Vetapet Vet"
    assert cc.load_contract(90).supplier == "Vetapet (Non-Vet)"


def test_vetapet_autoswaps_wholesale_below_rrp():
    # the model sometimes flips the two price columns on VetaPet's dense pages — the contract's
    # autoswap_cost_rrp deterministically corrects it (wholesale is always the lower number).
    c = cc.load_contract(91)
    swapped = {"supplier_sku": "FP10027", "cost_price": 126.0, "rrp": 70.0, "pack_size": "15mL", "units_per_pack": 1}
    items, flags = c.apply([swapped])
    assert items[0]["cost_price"] == 70.0 and items[0]["rrp"] == 126.0   # auto-corrected
    assert items[0]["units_per_pack"] == 1 and items[0]["segment"] == "vet"
    assert flags == []                                                    # no swap remains
    # a size in kg still parses to grams; an already-correct row is left alone
    ok = {"supplier_sku": "LS02", "cost_price": 128.0, "rrp": 205.0, "pack_size": "1.5 KG"}
    items2, _ = c.apply([ok])
    assert items2[0]["cost_price"] == 128.0 and items2[0]["weight_grams"] == 1500.0


def test_hills_does_not_autoswap_a_real_swap():
    # opt-in: Hill's has NO autoswap flag → a cost>rrp row is FLAGGED (not silently swapped)
    c = cc.load_contract(14)
    _, flags = c.apply([{"supplier_sku": "X", "cost_price": 25.0, "rrp": 17.6, "units_per_pack": 24}])
    assert len(flags) == 1 and flags[0]["rule"] == "cost_price < rrp"


def test_expected_columns_for_drift():
    cols = cc.load_contract(14).expected_columns()
    assert "Gross Wholesale Price / 每箱·罐" in cols or any("Gross Wholesale" in c for c in cols)


if __name__ == "__main__":
    for n, f in sorted((n, f) for n, f in globals().items() if n.startswith("test_")):
        f(); print("  ok ", n)
    print("catalogue_contract engine verified")

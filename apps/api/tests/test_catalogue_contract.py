"""Legacy extraction mapping engine tests.

The repository no longer ships YAML mapping files. These tests keep coverage on
the old deterministic mapping engine using inline mappings, and verify the
runtime loader falls back to generic extraction when no local mappings exist.
"""

import pytest

from services import catalogue_contract as cc


def _hills_contract():
    return cc.Contract(
        {
            "supplier": "Hill's",
            "supplier_id": 14,
            "format": "pdf_table",
            "document": {
                "species": {"from": "section_header", "map": {"Feline": "cat", "Canine": "dog"}},
                "segment": {
                    "from": "product_range",
                    "map": {"Prescription Diet": "vet", "Science Diet": "non_vet"},
                },
                "category": {"const": "Food"},
            },
            "columns": {
                "supplier_sku": "Product Code / 產品編號",
                "description": {
                    "join": [
                        "Product Range / 產品系列",
                        "Life Stage / 生命階段",
                        "Product Description / 產品名稱",
                    ],
                    "sep": " · ",
                },
                "pack_size": "Size / 重量",
                "brand": {"const": "Hill's"},
            },
            "pricing": {
                "basis": "per_unit",
                "units_per_pack": {"const": 1},
                "basic_cost": {"from": "Gross Wholesale Price / 每箱·罐"},
                "rrp": "Recommended Retail Selling Price / 建議零售價",
            },
            "weight": {"parse_from": "pack_size"},
            "ordering": {
                "order_increment_qty": "Order Multiple / 訂貨單位",
                "order_increment_uom": {"from": "sell_unit"},
            },
            "validate": ["cost_price < rrp", "order_increment_qty >= 1"],
        },
        "inline_hills",
    )


def _alfamedic_contract():
    return cc.Contract(
        {
            "supplier": "Alfamedic",
            "supplier_id": 1,
            "format": "pdf_table",
            "document": {
                "species": {"from": "product_name", "default": "both"},
                "segment": {"const": "vet"},
                "category": {"from": "section_header", "default": "Medicine"},
            },
            "columns": {
                "supplier_sku": "Order Code",
                "description": "Product Name",
                "brand": "Brand",
                "pack_size": "Packing / Unit",
            },
            "pricing": {
                "basis": "per_unit",
                "basic_cost": "Price/ Unit (HKD)",
                "units_per_pack": {"const": 1},
                "rrp": "none",
            },
            "ordering": {
                "order_increment_qty": {"parse": "Packing / Unit"},
                "order_increment_uom": {"from": "sell_unit"},
                "bulk_tiers": "multi_row",
            },
            "normalize": {"by_quote": {"basic_cost": None}},
            "validate": ["cost_price > 0", "order_increment_qty >= 1"],
        },
        "inline_alfamedic",
    )


def _vetapet_contract():
    return cc.Contract(
        {
            "supplier": "Vetapet Vet",
            "supplier_id": 91,
            "format": "pdf_table",
            "document": {
                "species": {"from": "product_name", "default": "both"},
                "segment": {"const": "vet"},
                "category": {"from": "section_header"},
            },
            "columns": {
                "supplier_sku": "CODE NO / 編號",
                "description": "PRODUCT NAME / 產品名稱",
                "pack_size": "SIZE / PACK / 重量",
            },
            "pricing": {
                "basis": "per_unit",
                "units_per_pack": {"const": 1},
                "autoswap_cost_rrp": True,
                "basic_cost": {"from": "WHOLESALE PRICE / 批發價"},
                "rrp": "SUGGESTED RETAIL PRICE / RETAIL PRICE / 零售價",
            },
            "weight": {"parse_from": "pack_size"},
            "validate": ["cost_price < rrp", "cost_price > 0"],
        },
        "inline_vetapet_vet",
    )


def test_no_shipped_yaml_mappings_load_and_generic_fallback_remains():
    cc.reload_contracts()

    assert cc.load_contract(14) is None
    assert cc.load_contract(1) is None
    assert cc.load_contract(91) is None
    assert cc.load_contract(999) is None
    assert cc.load_contract(None) is None


def test_bad_legacy_mapping_fails_loud():
    with pytest.raises(ValueError):
        cc.Contract({"supplier": "X"}, "x")
    with pytest.raises(ValueError):
        cc.Contract({"supplier_id": 5, "columns": {"not_a_field": "Col"}}, "x")


def test_hills_guided_prompt_states_the_rules():
    p = _hills_contract().prompt_section()
    assert "LEGACY SUPPLIER EXTRACTION MAPPING" in p
    assert "Gross Wholesale Price" in p
    assert "units_per_pack = 1" in p
    assert "Order Multiple" in p and "into units_per_pack" in p
    assert "Never swap cost and RRP" in p
    assert "Feline→cat" in p and "Canine→dog" in p


def test_hills_enforce_invariants():
    c = _hills_contract()
    row = {
        "supplier_sku": "10447",
        "cost_price": 13.10,
        "rrp": 18.0,
        "units_per_pack": 24,
        "order_increment_qty": 24,
        "brand": None,
        "category": None,
    }

    items, flags = c.apply([row])

    assert items[0]["units_per_pack"] == 1
    assert items[0]["order_increment_qty"] == 24
    assert items[0]["brand"] == "Hill's"
    assert items[0]["category"] == "Food"
    assert flags == []


def test_hills_parses_net_weight_from_size():
    rows = [
        {"supplier_sku": "A", "cost_price": 13.1, "rrp": 18.0, "pack_size": "24/2.9 oz"},
        {"supplier_sku": "B", "cost_price": 20.0, "rrp": 30.0, "pack_size": "85g"},
        {"supplier_sku": "C", "cost_price": 40.0, "rrp": 60.0, "pack_size": "2kg"},
        {"supplier_sku": "D", "cost_price": 30.0, "rrp": 45.0, "pack_size": "3.5lb"},
        {"supplier_sku": "E", "cost_price": 5.0, "rrp": 9.0, "pack_size": "24"},
    ]

    items, _ = _hills_contract().apply(rows)

    assert items[0]["weight_grams"] == round(2.9 * 28.3495)
    assert items[1]["weight_grams"] == 85.0
    assert items[2]["weight_grams"] == 2000.0
    assert items[3]["weight_grams"] == round(3.5 * 453.592)
    assert items[4].get("weight_grams") is None


def test_hills_flags_cost_rrp_swap():
    swapped = {
        "supplier_sku": "X",
        "cost_price": 25.0,
        "rrp": 17.6,
        "units_per_pack": 24,
        "order_increment_qty": 24,
    }
    _, flags = _hills_contract().apply([swapped])

    assert len(flags) == 1 and flags[0]["rule"] == "cost_price < rrp"


def test_alfamedic_parses_order_multiple_and_keeps_per_unit_cost():
    row = {
        "supplier_sku": "901-100",
        "cost_price": 820.0,
        "pack_size": "10 pcs/box",
        "units_per_pack": 10,
        "brand": "Skyla",
    }

    items, flags = _alfamedic_contract().apply([row])

    assert items[0]["cost_price"] == 820.0
    assert items[0]["units_per_pack"] == 1
    assert items[0]["order_increment_qty"] == 10
    assert items[0]["segment"] == "vet"
    assert items[0]["category"] == "Medicine"
    assert flags == []


def test_alfamedic_no_rrp_column_nulls_spurious_rrp():
    row = {
        "supplier_sku": "X",
        "cost_price": 399.0,
        "rrp": 559.0,
        "pack_size": "60 pcs/box",
        "units_per_pack": 60,
    }

    items, _ = _alfamedic_contract().apply([row])

    assert items[0]["rrp"] is None
    assert items[0]["order_increment_qty"] == 60
    assert items[0]["units_per_pack"] == 1


def test_alfamedic_by_quote_null_cost_not_flagged():
    row = {"supplier_sku": "MS-8", "cost_price": "By Quote", "pack_size": "1 unit", "units_per_pack": 1}

    items, flags = _alfamedic_contract().apply([row])

    assert items[0]["cost_price"] is None
    assert flags == []


def test_vetapet_autoswaps_wholesale_below_rrp():
    c = _vetapet_contract()
    swapped = {"supplier_sku": "FP10027", "cost_price": 126.0, "rrp": 70.0, "pack_size": "15mL", "units_per_pack": 1}

    items, flags = c.apply([swapped])

    assert items[0]["cost_price"] == 70.0
    assert items[0]["rrp"] == 126.0
    assert items[0]["units_per_pack"] == 1
    assert items[0]["segment"] == "vet"
    assert flags == []

    ok = {"supplier_sku": "LS02", "cost_price": 128.0, "rrp": 205.0, "pack_size": "1.5 KG"}
    items2, _ = c.apply([ok])
    assert items2[0]["cost_price"] == 128.0
    assert items2[0]["weight_grams"] == 1500.0


def test_hills_does_not_autoswap_a_real_swap():
    _, flags = _hills_contract().apply(
        [{"supplier_sku": "X", "cost_price": 25.0, "rrp": 17.6, "units_per_pack": 24}]
    )

    assert len(flags) == 1 and flags[0]["rule"] == "cost_price < rrp"


def test_expected_columns_for_drift():
    cols = _hills_contract().expected_columns()

    assert "Gross Wholesale Price / 每箱·罐" in cols or any("Gross Wholesale" in c for c in cols)

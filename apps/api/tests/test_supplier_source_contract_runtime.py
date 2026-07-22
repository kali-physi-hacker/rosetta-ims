"""Runtime adapter tests for Pydantic supplier-source contracts."""

from services import supplier_source_contract_runtime as runtime


def test_selects_only_supported_supplier_source_contracts():
    hills = runtime.load_contract(14)
    alfamedic = runtime.load_contract(1)

    assert hills is not None
    assert hills.slug == "hills.price_list.v1"
    assert alfamedic is not None
    assert alfamedic.slug == "alfamedic.price_list.v1"
    assert runtime.load_contract(91) is None
    assert runtime.load_contract(90) is None
    assert runtime.load_contract(999) is None
    assert runtime.load_contract(None) is None


def test_hills_prompt_is_derived_from_pydantic_source_contract():
    prompt = runtime.load_contract(14).prompt_section()

    assert "SUPPLIER SOURCE CONTRACT: hills.price_list.v1@v1" in prompt
    assert "Gross Wholesale Price" in prompt
    assert "Order Multiple" in prompt
    assert "Packaging rule" in prompt
    assert "legacy" not in prompt.lower()


def test_hills_runtime_applies_supported_contract_semantics():
    contract = runtime.load_contract(14)
    row = {
        "supplier_sku": "10447",
        "description": "Healthy Cuisine",
        "cost_price": 13.10,
        "rrp": 18.0,
        "units_per_pack": 24,
        "order_increment_qty": 24,
        "brand": None,
        "category": None,
        "pack_size": "24/2.9 oz",
    }

    items, flags = contract.apply([row])

    assert flags == []
    assert items[0]["units_per_pack"] == 1
    assert items[0]["order_increment_qty"] == 24
    assert items[0]["brand"] == "Hill's"
    assert items[0]["category"] == "Food"
    assert items[0]["weight_grams"] == round(2.9 * 28.3495)


def test_hills_runtime_flags_cost_rrp_swap_without_autoswap():
    row = {
        "supplier_sku": "SWAP1",
        "description": "Swapped row",
        "cost_price": 25.0,
        "rrp": 17.6,
        "units_per_pack": 24,
        "order_increment_qty": 24,
    }

    _, flags = runtime.load_contract(14).apply([row])

    assert len(flags) == 1
    assert flags[0]["rule"] == "HILLS_COST_NOT_BELOW_RRP"
    assert "not below" in flags[0]["detail"]


def test_alfamedic_runtime_applies_per_piece_price_and_order_increment():
    row = {
        "supplier_sku": "901-100",
        "description": "Skyla cartridge",
        "cost_price": 820.0,
        "rrp": 999.0,
        "pack_size": "10 pcs/box",
        "units_per_pack": 10,
        "brand": "Skyla",
    }

    items, flags = runtime.load_contract(1).apply([row])

    assert flags == []
    assert items[0]["cost_price"] == 820.0
    assert items[0]["units_per_pack"] == 1
    assert items[0]["order_increment_qty"] == 10
    assert items[0]["segment"] == "vet"
    assert items[0]["rrp"] is None


def test_alfamedic_by_quote_cost_is_null_and_not_flagged():
    row = {
        "supplier_sku": "MS-8",
        "description": "Manual quote product",
        "cost_price": "By Quote",
        "pack_size": "1 unit",
        "units_per_pack": 1,
    }

    items, flags = runtime.load_contract(1).apply([row])

    assert items[0]["cost_price"] is None
    assert items[0]["rrp"] is None
    assert flags == []


def test_expected_columns_come_from_source_declaration():
    columns = runtime.load_contract(14).expected_columns()

    assert "Product Code / 產品編號" in columns
    assert "Gross Wholesale Price / 每箱·罐" in columns

"""Runtime adapter tests for Pydantic supplier-source contracts."""

import pytest

from services import supplier_source_contract_runtime as runtime
from schemas.catalogue_pipeline.supplier_contracts import (
    SupplierDocumentType,
    SupplierSourceContractRegistration,
    SupplierSourceContractV1,
    get_supplier_source_contract,
)


def _registration(declaration: SupplierSourceContractV1) -> SupplierSourceContractRegistration:
    supplier_code_or_id = declaration.supplier.supplier_code or str(declaration.supplier.supplier_id)
    return SupplierSourceContractRegistration(
        contract_id=declaration.contract_id,
        contract_version=declaration.contract_version,
        supplier_code_or_id=supplier_code_or_id,
        document_type=declaration.document_type,
        support_status=declaration.support_status,
        declaration=declaration,
    )


def _synthetic_contract(*, contract_id: str, supplier_id: int, document_type: SupplierDocumentType) -> SupplierSourceContractV1:
    payload = get_supplier_source_contract("hills.price_list.v1", "v1").declaration.model_dump(mode="json")
    payload["contract_id"] = contract_id
    payload["supplier"] = {
        "supplier_id": supplier_id,
        "supplier_name": f"Synthetic Supplier {supplier_id}",
        "supplier_code": f"SYN{supplier_id}",
    }
    payload["document_type"] = document_type.value
    payload["format_name"] = f"Synthetic {document_type.value.lower()} {supplier_id}"
    return SupplierSourceContractV1.model_validate(payload)


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


def test_resolves_exact_supported_contract_and_rejects_mismatch_or_unknown_version():
    hills = runtime.resolve_supplier_contract(
        supplier_id=14,
        contract_id="hills.price_list.v1",
        contract_version="v1",
    )

    assert hills.slug == "hills.price_list.v1"

    with pytest.raises(runtime.SupplierContractIdentityError, match="belongs to supplier=14"):
        runtime.resolve_supplier_contract(
            supplier_id=1,
            contract_id="hills.price_list.v1",
            contract_version="v1",
        )

    with pytest.raises(runtime.SupplierContractNotFoundError, match="hills.price_list.v1@v2"):
        runtime.resolve_supplier_contract(
            supplier_id=14,
            contract_id="hills.price_list.v1",
            contract_version="v2",
        )

    with pytest.raises(runtime.SupplierContractIdentityError, match="contract_version cannot be supplied without contract_id"):
        runtime.resolve_supplier_contract(supplier_id=14, contract_version="v1")


def test_exact_resolution_rejects_unverified_or_partial_contracts_without_fallback():
    with pytest.raises(runtime.SupplierContractUnsupportedError, match="not SUPPORTED"):
        runtime.resolve_supplier_contract(
            supplier_id=91,
            contract_id="vetapet.vet_price_list.v1",
            contract_version="v1",
        )

    with pytest.raises(runtime.SupplierContractIdentityError, match="belongs to supplier=KPN"):
        runtime.resolve_supplier_contract(
            supplier_id=777,
            contract_id="kangaroo.earthz_pet_price_sheet.v1",
            contract_version="v1",
        )


def test_supplier_only_resolution_errors_are_specific(monkeypatch):
    partial = _registration(get_supplier_source_contract("vetapet.vet_price_list.v1", "v1").declaration)
    monkeypatch.setattr(runtime, "iter_supplier_source_contracts", lambda: (partial,))

    with pytest.raises(runtime.SupplierContractUnsupportedError, match="no SUPPORTED"):
        runtime.resolve_supplier_contract(supplier_id=91)

    assert runtime.load_contract(91) is None

    monkeypatch.setattr(runtime, "iter_supplier_source_contracts", lambda: ())

    with pytest.raises(runtime.SupplierContractUnsupportedError, match="no registered"):
        runtime.resolve_supplier_contract(supplier_id=91)


def test_supplier_only_resolution_rejects_multiple_supported_formats_without_ordering_fallback(monkeypatch):
    price_list = _registration(
        _synthetic_contract(
            contract_id="synthetic_supplier.price_list.v1",
            supplier_id=777,
            document_type=SupplierDocumentType.PRICE_LIST,
        )
    )
    promotion_sheet = _registration(
        _synthetic_contract(
            contract_id="synthetic_supplier.promotion_sheet.v1",
            supplier_id=777,
            document_type=SupplierDocumentType.PROMOTION_SHEET,
        )
    )

    monkeypatch.setattr(runtime, "iter_supplier_source_contracts", lambda: (promotion_sheet, price_list))

    with pytest.raises(runtime.SupplierContractAmbiguousError, match="multiple supported"):
        runtime.resolve_supplier_contract(supplier_id=777)


def test_supplier_only_resolution_is_independent_of_registry_order(monkeypatch):
    supported = _registration(
        _synthetic_contract(
            contract_id="synthetic_supplier.price_list.v1",
            supplier_id=778,
            document_type=SupplierDocumentType.PRICE_LIST,
        )
    )
    other_supplier = _registration(
        _synthetic_contract(
            contract_id="other_supplier.price_list.v1",
            supplier_id=779,
            document_type=SupplierDocumentType.PRICE_LIST,
        )
    )
    partial = _registration(get_supplier_source_contract("vetapet.vet_price_list.v1", "v1").declaration)
    monkeypatch.setattr(runtime, "iter_supplier_source_contracts", lambda: (other_supplier, partial, supported))

    resolved = runtime.resolve_supplier_contract(supplier_id=778)

    assert resolved.slug == "synthetic_supplier.price_list.v1"


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

import copy
import json
from pathlib import Path

from services import supplier_source_contract_runtime as runtime
from schemas.catalogue_pipeline.supplier_contracts import (
    SupplierContractSupportStatus,
    iter_supplier_source_contracts,
)


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "supplier_source" / "v1"
ROW_EXAMPLE_FIXTURES = sorted((FIXTURE_ROOT / "row_examples").glob("*.rows.json"))
VALID_ROW_EVIDENCE_STATUSES = {"CONFIRMED", "NEEDS_CONFIRMATION", "TECHNICAL_DEBT"}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _fixtures_by_contract_id() -> dict[str, dict]:
    return {_load(path)["contract_id"]: _load(path) for path in ROW_EXAMPLE_FIXTURES}


def test_row_evidence_fixtures_cover_every_registered_supplier_contract():
    registered = {item.contract_id for item in iter_supplier_source_contracts()}
    fixture_ids = set(_fixtures_by_contract_id())

    assert fixture_ids == registered


def test_row_evidence_status_matches_runtime_support_status():
    fixtures = _fixtures_by_contract_id()

    for registration in iter_supplier_source_contracts():
        fixture = fixtures[registration.contract_id]
        assert fixture["contract_version"] == registration.contract_version
        assert fixture["row_evidence_status"] in VALID_ROW_EVIDENCE_STATUSES
        assert isinstance(fixture["examples"], list)
        assert isinstance(fixture["technical_debt"], list)

        if registration.support_status == SupplierContractSupportStatus.SUPPORTED:
            assert fixture["row_evidence_status"] == "CONFIRMED"
            assert fixture["supplier_id"] == registration.declaration.supplier.supplier_id
            assert fixture["examples"]
            assert not fixture["technical_debt"]
        else:
            assert fixture["row_evidence_status"] in {"NEEDS_CONFIRMATION", "TECHNICAL_DEBT"}
            assert fixture["technical_debt"]


def test_confirmed_row_examples_are_source_located_and_match_runtime_semantics():
    for fixture in _fixtures_by_contract_id().values():
        if fixture["row_evidence_status"] != "CONFIRMED":
            continue

        contract = runtime.load_contract(
            fixture["supplier_id"],
            contract_id=fixture["contract_id"],
            contract_version=fixture["contract_version"],
        )
        assert contract is not None

        for example in fixture["examples"]:
            assert example["source"]["sample_reference"].startswith("external-sample:")
            assert example["source"]["page_number"] > 0
            assert example["source"]["text_excerpt"].strip()
            assert example["raw_row"]
            assert example["expected_semantics"]["price_basis"]

            item = copy.deepcopy(example["runtime_input"])
            items, flags = contract.apply([item])

            assert flags == []
            for key, expected in example["expected_runtime_item"].items():
                assert items[0].get(key) == expected


def test_kpn_kangaroo_row_evidence_is_deferred_as_technical_debt():
    fixtures = _fixtures_by_contract_id()
    kpn_contract_ids = {
        "kangaroo.mixed_price_catalogue.v1",
        "kangaroo.purina_proplan_veterinary_diets.v1",
        "kangaroo.earthz_pet_price_sheet.v1",
    }

    for contract_id in kpn_contract_ids:
        fixture = fixtures[contract_id]
        assert fixture["row_evidence_status"] == "TECHNICAL_DEBT"
        assert fixture["examples"] == []
        assert {item["supplier_id"] for item in fixture["supplier_id_candidates"]} == {15, 81}
        assert fixture["technical_debt"][0]["status"] == "DRAFT_PR_LATER"


def test_vetapet_row_evidence_requires_confirmation_before_runtime_support():
    fixtures = _fixtures_by_contract_id()

    for contract_id in {"vetapet.vet_price_list.v1", "vetapet.non_vet_price_list.v1"}:
        fixture = fixtures[contract_id]
        assert fixture["row_evidence_status"] == "NEEDS_CONFIRMATION"
        assert fixture["examples"] == []
        assert fixture["technical_debt"][0]["status"] == "OPEN"

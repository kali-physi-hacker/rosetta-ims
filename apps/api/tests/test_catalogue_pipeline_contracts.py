import json
import subprocess
import sys
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from schemas.catalogue_pipeline import (
    Cost,
    DiscountedUnitPriceBenefit,
    ExtractionProfileV1,
    FreeQuantityBenefit,
    MasteringCandidateV1,
    MbbTerm,
    MinimumQuantityCondition,
    MinimumSpendCondition,
    Money,
    PackagingConfiguration,
    PercentageDiscountBenefit,
    Quantity,
    RawObservationV1,
    ServingItemV1,
    StagingCatalogueItemV1,
    UnitOfMeasure,
    ValidationIssueV1,
    get_contract_model,
    registry_snapshot,
)
from schemas.catalogue_pipeline.enums import IssueSeverity, UnitCode


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "v1"
VALID_FIXTURES = sorted((FIXTURE_ROOT / "valid").glob("*.json"))
INVALID_FIXTURES = sorted((FIXTURE_ROOT / "invalid").glob("*.json"))
BACKEND_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]

MODEL_BY_NAME = {
    "ExtractionProfileV1": ExtractionProfileV1,
    "RawObservationV1": RawObservationV1,
    "StagingCatalogueItemV1": StagingCatalogueItemV1,
    "MasteringCandidateV1": MasteringCandidateV1,
    "ValidationIssueV1": ValidationIssueV1,
    "ServingItemV1": ServingItemV1,
}

EXPECTED_INVALIDS = {
    "confidence_above_1.json": (("extraction_confidence",), "less than or equal to 1"),
    "content_amount_without_uom.json": (("proposed_fields", "packaging"), "content_amount and content_uom"),
    "forbidden_extra_field.json": (("review_status",), "Extra inputs are not permitted"),
    "mastering_approved_no_lineage.json": (("lineage",), "Field required"),
    "other_uom_without_label.json": (("proposed_fields", "packaging", "purchase_uom"), "OTHER UOM requires a label"),
    "percentage_discount_above_100.json": (
        ("proposed_fields", "mbb_terms", 0, "benefit", "percentage_discount", "percentage"),
        "less than or equal to 100",
    ),
    "raw_observation_no_evidence.json": ((), "requires raw_text or at least one raw cell"),
    "serving_pending_review.json": ((), "Serving Item requires APPROVED"),
    "unsupported_currency.json": (("proposed_fields", "cost", "currency"), "Input should be 'HKD'"),
    "wrong_contract_version.json": (("contract_version",), "catalogue.raw_observation.v1"),
    "zero_quantity.json": (
        ("proposed_fields", "mbb_terms", 0, "condition", "minimum_quantity", "quantity", "amount"),
        "greater than 0",
    ),
}


def _load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("path", VALID_FIXTURES, ids=lambda p: p.name)
def test_valid_fixtures_validate(path):
    payload = _load(path)
    model = get_contract_model(payload["contract_version"])

    instance = model.model_validate(payload)

    assert instance.contract_version == payload["contract_version"]


@pytest.mark.parametrize("path", INVALID_FIXTURES, ids=lambda p: p.name)
def test_invalid_fixtures_fail_for_expected_reason(path):
    wrapper = _load(path)
    model = MODEL_BY_NAME[wrapper["model"]]
    expected_loc, expected_msg = EXPECTED_INVALIDS[path.name]

    with pytest.raises(ValidationError) as excinfo:
        model.model_validate(wrapper["payload"])

    errors = excinfo.value.errors()
    assert any(tuple(error["loc"]) == expected_loc and expected_msg in error["msg"] for error in errors)


def test_public_registry_and_contract_version_rejection():
    registry = registry_snapshot()

    assert set(registry) == {
        "catalogue.extraction_profile.v1",
        "catalogue.raw_observation.v1",
        "catalogue.staging_item.v1",
        "catalogue.mastering_candidate.v1",
        "catalogue.validation_issue.v1",
        "catalogue.serving_item.v1",
    }
    with pytest.raises(ValueError, match="Unknown catalogue pipeline contract id"):
        get_contract_model("catalogue.raw_observation.v999")


def test_raw_observation_uuid_and_timezone_serialization():
    payload = _load(FIXTURE_ROOT / "valid" / "raw_observation_pdf.json")
    obj = RawObservationV1.model_validate(payload)

    assert isinstance(obj.raw_observation_id, UUID)
    assert obj.captured_at.tzinfo is not None
    dumped = json.loads(obj.model_dump_json())
    assert dumped["raw_observation_id"] == str(obj.raw_observation_id)

    payload["captured_at"] = "2026-07-22T00:10:00"
    with pytest.raises(ValidationError, match="timezone-aware"):
        RawObservationV1.model_validate(payload)


def test_decimal_money_quantity_and_cost_serialize_as_strings():
    cost = Cost(amount="13.10", currency="HKD", price_basis={"code": "CAN"})
    money = Money(amount="13.10", currency="HKD")
    quantity = Quantity(amount="24", uom={"code": "CAN"})

    assert json.loads(cost.model_dump_json())["amount"] == "13.10"
    assert json.loads(money.model_dump_json())["amount"] == "13.10"
    assert json.loads(quantity.model_dump_json())["amount"] == "24"
    with pytest.raises(ValidationError, match="floats are not accepted"):
        Cost(amount=13.10, currency="HKD", price_basis={"code": "CAN"})


def test_packaging_keeps_unknowns_null_and_validates_uom_rules():
    packaging = PackagingConfiguration(
        purchase_uom=None,
        price_basis=None,
        sellable_unit_uom=None,
        sellable_units_per_purchase_unit=None,
        content_amount=None,
        content_uom=None,
        order_increment=None,
        minimum_order_quantity=None,
        break_pack_allowed=None,
        source_text="ambiguous pack text",
    )
    assert packaging.purchase_uom is None
    assert packaging.sellable_units_per_purchase_unit is None

    with pytest.raises(ValidationError, match="OTHER UOM requires a label"):
        UnitOfMeasure(code=UnitCode.OTHER)
    with pytest.raises(ValidationError, match="only allowed when code is OTHER"):
        UnitOfMeasure(code=UnitCode.CAN, label="tin")
    with pytest.raises(ValidationError, match="content_amount and content_uom"):
        PackagingConfiguration(content_amount="30", content_uom=None)
    with pytest.raises(ValidationError, match="greater than 0"):
        PackagingConfiguration(sellable_units_per_purchase_unit="0")
    with pytest.raises(ValidationError, match="greater than 0"):
        PackagingConfiguration(order_increment={"amount": "-1", "uom": {"code": "CAN"}})
    with pytest.raises(ValidationError, match="greater than 0"):
        PackagingConfiguration(minimum_order_quantity={"amount": "0", "uom": {"code": "CAN"}})


def test_mbb_discriminated_condition_and_benefit_parsing():
    staging = StagingCatalogueItemV1.model_validate(_load(FIXTURE_ROOT / "valid" / "staging_item_with_mbb.json"))
    terms = staging.proposed_fields.mbb_terms

    assert isinstance(terms[0].condition, MinimumQuantityCondition)
    assert isinstance(terms[0].benefit, DiscountedUnitPriceBenefit)
    assert isinstance(terms[1].benefit, PercentageDiscountBenefit)
    assert isinstance(terms[2].benefit, FreeQuantityBenefit)
    assert isinstance(terms[3].condition, MinimumSpendCondition)
    assert terms[3].scope == "SUPPLIER_ORDER"

    with pytest.raises(ValidationError, match="greater than 0"):
        MbbTerm.model_validate(
            {
                "mbb_term_id": "99999999-9999-4999-8999-999999999995",
                "scope": "SUPPLIER_SKU",
                "condition": {"condition_type": "minimum_quantity", "quantity": {"amount": "1", "uom": {"code": "CAN"}}},
                "benefit": {"benefit_type": "free_quantity", "quantity": {"amount": "0", "uom": {"code": "CAN"}}},
            }
        )


def test_validation_issue_resolution_and_publish_blocking_rules():
    issue = ValidationIssueV1.model_validate(_load(FIXTURE_ROOT / "valid" / "validation_issue_ambiguous_cost_basis.json"))
    assert issue.publish_blocking is False

    blocking = issue.model_copy(update={"severity": IssueSeverity.BLOCKING})
    assert blocking.publish_blocking is True

    data = _load(FIXTURE_ROOT / "valid" / "validation_issue_ambiguous_cost_basis.json")
    data["resolver_id"] = "bizops@example.com"
    with pytest.raises(ValidationError, match="unresolved issue cannot contain"):
        ValidationIssueV1.model_validate(data)

    data = _load(FIXTURE_ROOT / "valid" / "validation_issue_ambiguous_cost_basis.json")
    data["resolution_status"] = "CORRECTED"
    with pytest.raises(ValidationError, match="resolved issue requires resolved_at"):
        ValidationIssueV1.model_validate(data)


def test_product_variant_validates_without_product_family():
    candidate = MasteringCandidateV1.model_validate(_load(FIXTURE_ROOT / "valid" / "mastering_candidate_no_family.json"))

    assert candidate.product_variant_resolution.product_family_id is None
    assert candidate.product_family_resolution is None
    assert candidate.review_status == "APPROVED_WITH_OVERRIDE"


def test_serving_publication_guard_and_required_cost_per_sellable_unit():
    serving = ServingItemV1.model_validate(_load(FIXTURE_ROOT / "valid" / "serving_item_inventory.json"))
    assert serving.product_family_id is None
    assert serving.cost_per_sellable_unit.amount == Decimal("13.10")

    data = _load(FIXTURE_ROOT / "valid" / "serving_item_inventory.json")
    data["cost_per_sellable_unit"] = None
    with pytest.raises(ValidationError, match="cost_per_sellable_unit is required"):
        ServingItemV1.model_validate(data)


def test_schema_artifacts_are_current_and_have_contract_shape():
    result = subprocess.run(
        [sys.executable, "scripts/export_catalogue_pipeline_schemas.py", "--check"],
        cwd=BACKEND_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr

    schema_path = REPO_ROOT / "docs/contracts/catalogue-pipeline/v1/catalogue.staging_item.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    assert schema["properties"]["contract_version"]["const"] == "catalogue.staging_item.v1"
    assert schema["additionalProperties"] is False
    encoded = json.dumps(schema)
    assert "discriminator" in encoded and "condition_type" in encoded and "benefit_type" in encoded


def test_contract_import_does_not_import_fastapi_app_or_database():
    code = (
        "import sys; "
        "import schemas.catalogue_pipeline as cp; "
        "assert 'main' not in sys.modules; "
        "assert 'database' not in sys.modules; "
        "print(sorted(cp.registry_snapshot()))"
    )
    result = subprocess.run([sys.executable, "-c", code], cwd=BACKEND_ROOT, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr


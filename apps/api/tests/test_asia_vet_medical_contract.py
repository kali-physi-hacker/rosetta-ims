"""AVM's VetriScience list, read end to end from the page we captured.

The best-conditioned source we hold: a real five-column table with an item code
on every row, and — unusually — the SPECIES stated outright instead of being
guessed from a product name. What it does not state is the container: it says
"180 Capsules", never bottle or box.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/avm.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")

import pytest  # noqa: E402

from schemas.catalogue_pipeline.supplier_contracts import get_supplier_source_contract  # noqa: E402
from services.catalogue_conformance import conform_observations  # noqa: E402
from services.catalogue_evidence_extraction import extract_evidence  # noqa: E402
from services.supplier_source_contract_runtime import SupplierSourceRuntimeContract  # noqa: E402
from test_catalogue_golden_suppliers import _blank_pdf, _install_golden_replay  # noqa: E402

ENVELOPE = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "asia_vet_medical" / "vetriscience_page_1.json"
CONTRACT = "asia_vet_medical.vetriscience_price_list.v1"


@pytest.fixture(scope="module")
def conformed():
    """The captured page, replayed through extraction and conformance once."""
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        calls = _install_golden_replay(patch, [ENVELOPE])
        result = extract_evidence(_blank_pdf(1), "avm-vetriscience.pdf", "application/pdf")
        assert calls["n"] == 1, "replayed from the recorded envelope — no provider was called"
        runtime = SupplierSourceRuntimeContract(
            declaration=get_supplier_source_contract(CONTRACT, "v1").declaration
        )
        yield conform_observations(
            result.observations, tuple(uuid4() for _ in result.observations), runtime
        )
    finally:
        patch.undo()


def _by_sku(outcome):
    return {row.raw_fields.get("supplier_sku"): row for row in outcome.items}


def test_every_row_on_the_page_conforms(conformed):
    """31 rows, nothing held. A row this well printed should not need a person."""
    assert len(conformed.items) == 31
    blocking = [i.issue_code for row in conformed.items for i in row.issues if i.severity == "BLOCKING"]
    assert blocking == []


def test_the_price_buys_one_package_and_the_page_never_says_which(conformed):
    """The basis is the generic PACK, not a bottle nobody printed."""
    rows = _by_sku(conformed)
    disc = rows["580.180"]

    cost = disc.normalized_fields["cost"]
    assert cost["amount"] in ("225", "225.0", "225.00")
    assert cost["currency"] == "HKD"
    assert cost["price_basis"]["code"] == "PACK"
    # Every row prices a package; none of them names the vessel.
    assert {row.normalized_fields["cost"]["price_basis"]["code"] for row in conformed.items} == {"PACK"}


def test_a_count_of_countables_becomes_a_unit_count(conformed):
    """"180 Capsules" is what one package holds, and the code suffix agrees."""
    rows = _by_sku(conformed)
    for sku, expected in (("580.180", "180"), ("322.100", "100"), ("631.090", "90"), ("725.060", "60")):
        packaging = rows[sku].normalized_fields["packaging"]
        assert packaging["sellable_units_per_purchase_unit"] == expected, sku
        # The item code's own suffix repeats the quantity — two readings, one fact.
        assert sku.split(".")[1].lstrip("0") == expected.lstrip("0")


def test_a_measure_is_content_and_never_a_unit_count(conformed):
    """A 30 mL bottle is ONE sellable thing, not thirty.

    The same column states a count on 29 rows and a measure on 2. Reading the
    measure as a count would divide the price by thirty and invent a per-mL cost
    nobody sells at.
    """
    rows = _by_sku(conformed)
    for sku, amount, uom in (("590.030", "30", "ML"), ("988.016", "16", "OZ")):
        packaging = rows[sku].normalized_fields["packaging"]
        assert packaging.get("sellable_units_per_purchase_unit") is None, sku
        assert packaging["content_amount"] == amount
        assert packaging["content_uom"]["code"] == uom

    counted = [r for r in conformed.items
               if (r.normalized_fields["packaging"] or {}).get("sellable_units_per_purchase_unit")]
    measured = [r for r in conformed.items
                if (r.normalized_fields["packaging"] or {}).get("content_amount")]
    assert (len(counted), len(measured)) == (29, 2)


def test_the_page_states_the_species_so_nothing_has_to_guess(conformed):
    """Formula is an enumerated column, mapped to the vocabulary already in use.

    Every other contract infers species from a product name, and where that
    fails the system asks a model with web search. A declared column beats both.
    """
    rows = _by_sku(conformed)

    def species(sku):
        value = rows[sku].normalized_fields.get("species")
        return value.get("value") if isinstance(value, dict) else value

    assert species("580.180") == "dog"        # Canine
    assert species("822.060") == "cat"        # Feline
    assert species("23A.060") == "both"       # Canine + Feline — one value, not two

    counts = {"dog": 0, "cat": 0, "both": 0}
    for row in conformed.items:
        value = row.normalized_fields.get("species")
        counts[value.get("value") if isinstance(value, dict) else value] += 1
    assert counts == {"dog": 13, "cat": 9, "both": 9}


def test_the_brand_is_the_bottle_not_the_supplier(conformed):
    """AVM sells it; VetriScience makes it. The page prints both, once each."""
    assert {row.raw_fields.get("brand") for row in conformed.items} == {"VetriScience"}
    envelope = json.loads(ENVELOPE.read_text(encoding="utf-8"))
    assert envelope["supplier_identity_text"] == "AVM"
    assert envelope["page_brand_text"] == "VetriScience Laboratories"


def test_the_measure_rule_is_opt_in_so_no_other_supplier_moves():
    """Alfamedic's '30ml/ bot' deliberately keeps its 30 as a count.

    A supplier may sell BY the measure, so reading every leading measure as
    content would silently restate their costs. AVM declares the behaviour it
    needs; the default leaves every existing contract exactly as it was.
    """
    from schemas.catalogue_pipeline.supplier_contracts import iter_supplier_source_contracts

    opted_in = {
        c.contract_id
        for c in iter_supplier_source_contracts()
        if c.declaration.packaging.sellable_count_excludes_measures
    }
    assert opted_in == {CONTRACT}


def test_an_undeclared_species_survives_rather_than_vanishing():
    """A value map translates what it knows and carries the rest verbatim.

    An unrecognised species is still evidence a reviewer can read — unlike a
    price basis, where an unmapped unit has to hold the row instead.
    """
    from services.catalogue_conformance import _mapped_value

    field = next(
        f for f in get_supplier_source_contract(CONTRACT, "v1").declaration.fields
        if f.field_key == "species"
    )
    assert _mapped_value(field, "Canine") == "dog"
    assert _mapped_value(field, "  canine + feline  ") == "both"
    assert _mapped_value(field, "Equine") == "Equine"
    assert _mapped_value(field, None) is None

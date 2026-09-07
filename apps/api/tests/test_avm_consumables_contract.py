"""AVM's consumables list — two columns, and everything folded into one of them.

Thirteen pages, 460 rows, 48 sections, and the plainest table we hold:

    Item Description | Unit Price (HK$)

There is no code column and no pack column. Both facts are printed inside the
description, and both are read out of it by declared pattern — which is the
whole of what these tests guard, because reading either one loosely turns a
colour into a supplier code or a tube's diameter into a quantity of tubes.
"""

from __future__ import annotations

import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/avmc.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")

import pytest  # noqa: E402

from schemas.catalogue_pipeline.supplier_contracts import (  # noqa: E402
    get_supplier_source_contract,
)
from services.catalogue_conformance import conform_observations  # noqa: E402
from services.catalogue_evidence_extraction import extract_evidence  # noqa: E402
from services.supplier_source_contract_runtime import SupplierSourceRuntimeContract  # noqa: E402
from test_catalogue_golden_suppliers import _blank_pdf, _install_golden_replay  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "avm_consumables"
PAGES = [FIXTURES / f"page_{n}.json" for n in range(1, 14)]
CONTRACT = "asia_vet_medical.consumables_price_list.v1"


@pytest.fixture(scope="module")
def conformed():
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        calls = _install_golden_replay(patch, PAGES)
        result = extract_evidence(_blank_pdf(len(PAGES)), "avm-consumables.pdf", "application/pdf")
        assert calls["n"] == len(PAGES), "replayed from the recorded pages — no provider call"
        runtime = SupplierSourceRuntimeContract(
            declaration=get_supplier_source_contract(CONTRACT, "v1").declaration
        )
        yield conform_observations(
            result.observations, tuple(uuid4() for _ in result.observations), runtime
        )
    finally:
        patch.undo()


def _products(outcome):
    return [r for r in outcome.items if (r.raw_fields.get("product_name") or "").strip()]


def _named(outcome, needle):
    return [r for r in _products(outcome) if needle in (r.raw_fields.get("product_name") or "")]


def _cost(row):
    return row.normalized_fields.get("cost") or {}


def _pack(row):
    return row.normalized_fields.get("packaging") or {}


def _money(text):
    cleaned = re.sub(r"[^\d.]", "", str(text or ""))
    return Decimal(cleaned) if cleaned else None


def test_every_row_conforms_with_nothing_held(conformed):
    """460 rows over thirteen pages, one column shape throughout."""
    assert len(_products(conformed)) == 460

    blocking = [
        i.issue_code for r in conformed.items for i in r.issues if i.severity == "BLOCKING"
    ]
    assert blocking == []


def test_the_manufacturers_code_is_read_out_of_the_description(conformed):
    """The page has no code column; the code is in brackets at the end. A code
    is the join a cost reconciliation needs, and a product name is not."""
    tube = _named(conformed, "Mila Nasogastric Feeding Tube 6Fr x 55cm")[0]

    assert (tube.raw_fields.get("supplier_sku") or "") == "NG622"
    assert _money(_cost(tube).get("amount")) == Decimal("270.00")


def test_a_colour_in_brackets_is_never_read_as_a_code(conformed):
    """The same brackets hold colours and dimensions. A heparin tube whose
    supplier code came out as 'Orange' would be worse than one with no code —
    it would match confidently against nothing."""
    tube = _named(conformed, "Lithium Heparin Tube (Orange)")[0]

    assert not (tube.raw_fields.get("supplier_sku") or "")
    assert "(Orange)" in tube.raw_fields["product_name"]


def test_about_half_the_rows_carry_a_code_and_the_rest_genuinely_do_not(conformed):
    """Neither number is a target — they are what the page prints. Pinned so a
    pattern that quietly stops matching shows up as a change, not a silence."""
    coded = [r for r in _products(conformed) if (r.raw_fields.get("supplier_sku") or "").strip()]

    assert len(coded) == 267
    assert all(any(ch.isdigit() for ch in r.raw_fields["supplier_sku"]) for r in coded)


def test_the_pack_count_is_read_from_inside_the_description(conformed):
    """'100 pcs/pack' is a hundred tubes for HK$270.00. There is no pack column
    to read it from."""
    tube = _named(conformed, "Lithium Heparin Tube (Orange)")[0]

    assert str(_pack(tube).get("sellable_units_per_purchase_unit")) == "100"
    assert _money(_cost(tube).get("amount")) == Decimal("270.00")
    assert (_cost(tube).get("price_basis") or {}).get("code") == "PACK"


def test_a_products_dimensions_are_never_read_as_a_pack(conformed):
    """'1.3ml' and '6Fr x 55cm' sit in the same string as the pack. Read as a
    count, a feeding tube's length would become a quantity of feeding tubes."""
    tube = _named(conformed, "Mila Nasogastric Feeding Tube 6Fr x 55cm")[0]

    assert _pack(tube).get("sellable_units_per_purchase_unit") is None


def test_the_clinical_section_survives_from_the_banner(conformed):
    """Forty-eight sections, and they are the only grouping the page offers."""
    sections = {(r.raw_fields.get("category") or "").strip() for r in _products(conformed)}

    assert "Clinical Laboratory Consumables" in sections
    assert "Feeding Tubes Mila" in sections
    assert len(sections) > 30


def test_this_list_and_the_vetriscience_list_are_separate_contracts():
    """AVM send three documents and this contract reads one of them. A
    supplement priced on the VetriScience list must never be read by the
    consumables contract, or the same product arrives twice at two prices."""
    from schemas.catalogue_pipeline.supplier_contracts import iter_supplier_source_contracts

    avm = {
        c.contract_id for c in iter_supplier_source_contracts()
        if c.declaration.supplier.supplier_id == 3
    }
    assert {
        "asia_vet_medical.consumables_price_list.v1",
        "asia_vet_medical.vetriscience_price_list.v1",
    } <= avm


# --- the same list, read a second time and a different way -------------------
#
# `whole_document.json` is a BizOps extraction of all thirteen pages in one
# envelope, against the thirteen per-page envelopes recorded here from the
# production path. They agree on every product and disagree about one thing the
# recordings never exercised: theirs STATES the page identity.


def _conform_whole_document():
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        _install_golden_replay(patch, [FIXTURES / "whole_document.json"])
        result = extract_evidence(_blank_pdf(1), "avm-consumables.pdf", "application/pdf")
        runtime = SupplierSourceRuntimeContract(
            declaration=get_supplier_source_contract(CONTRACT, "v1").declaration
        )
        return conform_observations(
            result.observations, tuple(uuid4() for _ in result.observations), runtime
        )
    finally:
        patch.undo()


@pytest.fixture(scope="module")
def whole_document():
    return _conform_whole_document()


#: The two readings render the SAME description differently — a newline where
#: the other has a space before "(Sizes available: …)", a curly quote where the
#: other has a straight one in '2" x 16"'. That is typography, not disagreement,
#: and comparing raw strings would report it as one.
_TYPOGRAPHY = str.maketrans({"\u201c": '"', "\u201d": '"', "\u2018": "'", "\u2019": "'"})


def _same_text(value: str) -> str:
    return " ".join(str(value or "").translate(_TYPOGRAPHY).split()).casefold()


def test_both_readings_agree_on_every_product(whole_document, conformed):
    """Thirteen per-page envelopes against one whole-document extraction.

    The codes are pulled out of prose by pattern and the packs likewise, so how
    the page was read is exactly where either could drift. It does not: both
    yield 460 products, the same code on the same product and the same price.
    They differ only in whitespace and quote characters inside the description,
    which is why the comparison normalises those and nothing else.
    """
    def facts(outcome):
        return {
            _same_text(row.raw_fields.get("product_name")): (
                str(row.raw_fields.get("cost")),
                (row.raw_fields.get("supplier_sku") or ""),
                str((row.normalized_fields.get("packaging") or {}).get("sellable_units_per_purchase_unit")),
            )
            for row in _products(outcome)
        }

    mine, theirs = facts(conformed), facts(whole_document)

    assert len(mine) == len(theirs) == 460
    assert set(mine) == set(theirs)
    assert mine == theirs


def test_the_two_readings_really_are_different_envelopes():
    """Guards the test above from becoming a tautology. These are independent
    readings of one document, and they do differ — in typography, and in
    whether the page identity is stated at all."""
    import json

    mine = json.loads((FIXTURES / "page_1.json").read_text(encoding="utf-8"))
    theirs = json.loads((FIXTURES / "whole_document.json").read_text(encoding="utf-8"))

    assert mine.get("supplier_identity_text") != theirs.get("supplier_identity_text")
    assert len(theirs["tables"]) > len(mine["tables"])


def test_the_page_identity_is_read_when_the_source_states_it(whole_document):
    """This reading states 'Asia Vet Medical (AVM) Consumable items list' where
    the recorded pages leave identity null — so the identity check had never
    run against this contract at all.

    It is not a formality: 'medical' and 'limited' are both identity stopwords,
    so the match rests on 'Asia' alone, and the supplier code AVM answers for
    it too. Worth pinning, because a contract that only ever saw a null
    identity would discover this the first time a live read stated one.
    """
    import json

    from services.catalogue_conformance import _identity_names_overlap

    stated = json.loads((FIXTURES / "whole_document.json").read_text(encoding="utf-8"))
    identity = stated.get("supplier_identity_text")
    assert identity, "the fixture no longer states an identity"

    supplier = get_supplier_source_contract(CONTRACT, "v1").declaration.supplier
    assert _identity_names_overlap(identity, supplier.supplier_name)
    assert _identity_names_overlap(identity, supplier.supplier_code)

    blocking = [
        i.issue_code for r in whole_document.items for i in r.issues if i.severity == "BLOCKING"
    ]
    assert blocking == []

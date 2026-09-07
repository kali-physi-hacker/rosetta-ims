"""The Covetrus catalogue — eleven price tables inside a picture book.

Supplier 62 sends two documents. ProVet's price list is a price list all the
way through; this one is a 32-page illustrated catalogue that happens to carry
eleven price tables among its fifty-four.

What makes it worth a careful contract is that the other forty-three tables
share a Description column with the priced ones, and many of them describe the
very products priced elsewhere in the same file. Read them and the catalogue
duplicates itself against its own prices.
"""

from __future__ import annotations

import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/cov.db")
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

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "covetrus"
CONTRACT = "covetrus.branded_products_catalogue.v1"


@pytest.fixture(scope="module")
def conformed():
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        calls = _install_golden_replay(patch, [FIXTURES / "whole_document.json"])
        result = extract_evidence(_blank_pdf(1), "covetrus.pdf", "application/pdf")
        assert calls["n"] == 1, "replayed from the recorded document — no provider call"
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


def _by_code(outcome):
    return {(r.raw_fields.get("supplier_sku") or "").strip(): r for r in _products(outcome)}


def _cost(row):
    return row.normalized_fields.get("cost") or {}


def _money(text):
    cleaned = re.sub(r"[^\d.]", "", str(text or ""))
    return Decimal(cleaned) if cleaned else None


def test_only_the_priced_tables_are_read(conformed):
    """122 rows out of a 32-page catalogue, and nothing held.

    The other 43 tables list dimensions, gauges and quantities with no price.
    They cannot be told apart by their columns — they share Description with
    the priced ones — so the contract names the fields a real row must carry
    at least one of. Read them and the catalogue would price itself twice.
    """
    products = _products(conformed)

    assert len(products) == 122
    assert conformed.skipped_count >= 140
    blocking = [
        i.issue_code for r in conformed.items for i in r.issues if i.severity == "BLOCKING"
    ]
    assert blocking == []


def test_the_two_priced_table_shapes_both_read(conformed):
    """Most rows sit under 'ItemNumber / Description / List Price HK$ / Page
    Number'; the Maxicam table uses 'Provet Code / Description / Pack Size /
    Price'. A contract declaring only the first would lose the injection."""
    by_code = _by_code(conformed)

    maxicam = by_code.get("PV-MAXINJ20")
    syringe = by_code.get("CV-2800540")
    assert maxicam is not None and syringe is not None
    assert _money(_cost(maxicam).get("amount")) == Decimal("258")
    assert _money(_cost(syringe).get("amount")) == Decimal("54.00")


def test_a_price_buys_the_pack_and_the_count_comes_from_the_description(conformed):
    """$54.00 buys the box of a hundred syringes. The count is at the end of
    the description — '100s' — because the page has no column for it."""
    syringe = _by_code(conformed)["CV-2800540"]
    packaging = syringe.normalized_fields.get("packaging") or {}

    assert (_cost(syringe).get("price_basis") or {}).get("code") == "PACK"
    assert str(packaging.get("sellable_units_per_purchase_unit")) == "100"


def test_a_millilitre_is_never_read_as_a_count(conformed):
    """The Maxicam table prints a Pack Size of '20mL'. That is what the bottle
    holds, not twenty things — read as a count it would price the injection at
    a twentieth of its cost."""
    maxicam = _by_code(conformed)["PV-MAXINJ20"]
    packaging = maxicam.normalized_fields.get("packaging") or {}

    assert packaging.get("sellable_units_per_purchase_unit") is None
    assert _money(_cost(maxicam).get("amount")) == Decimal("258")


def test_both_code_families_belong_to_one_supplier(conformed):
    """CV- is Cvet and PV- is ProVet. One company sells both ranges and this
    catalogue prices them together; the prefix is a brand, not a supplier."""
    codes = {c for c in _by_code(conformed) if c}

    assert any(c.startswith("CV-") for c in codes)
    assert any(c.startswith("PV-") for c in codes)
    declaration = get_supplier_source_contract(CONTRACT, "v1").declaration
    assert declaration.supplier.supplier_id == 62


def test_the_catalogue_page_reference_is_kept_but_never_counted(conformed):
    """The Page Number column points into the illustrated pages, sometimes as a
    range ('17 & 19'). Useful to a reviewer, and not a quantity of anything."""
    rows = [
        r for r in _products(conformed)
        if (r.raw_fields.get("additional_fields") or {}).get("catalogue_page")
    ]

    assert rows, "the page references are gone — re-check the fixture"
    for row in rows:
        packaging = row.normalized_fields.get("packaging") or {}
        page = str((row.raw_fields["additional_fields"])["catalogue_page"])
        assert str(packaging.get("sellable_units_per_purchase_unit") or "") != page

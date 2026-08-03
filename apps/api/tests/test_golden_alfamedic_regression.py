"""The Alfamedic block of the golden sample sheet, as a regression test.

Source: the "margin calculation" sheet, tab gid=1535624888, columns filled by
hand for five Alfamedic SKUs. It is the only human-authored statement of what
these rows SHOULD come out as, so it is the arbiter when the pipeline changes.

Checked against a real published export of run fef361b2 (the 56-page Alfamedic
catalogue). Written first with three gaps pinned as strict xfail — the pack's
contents read as an order quantity, quantity_per_unit never derived, and every
price basis reported as PIECE. Fixing the contract flipped all three to XPASS,
so they are plain assertions now. That is what the strict marker is for.
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/golden_alf.db")

from uuid import uuid4  # noqa: E402

from schemas.catalogue_pipeline.enums import ExtractionMethod  # noqa: E402
from schemas.catalogue_pipeline.extracted_evidence_v1 import RawCell, SourceLocation  # noqa: E402
from services import supplier_source_contract_runtime as runtime  # noqa: E402
from services.catalogue_conformance import conform_observations  # noqa: E402
from services.catalogue_evidence_extraction import ExtractedEvidence  # noqa: E402


def _conform(pairs):
    """One printed Alfamedic row, through the real contract."""
    observation = ExtractedEvidence(
        observation_key="row",
        source_location=SourceLocation(page_number=18, source_object_key="row"),
        raw_cells=tuple(
            RawCell(cell_reference=None, row_number=1, column_index=i + 1, column_name=c, raw_value=v)
            for i, (c, v) in enumerate(pairs)
        ),
        extraction_method=ExtractionMethod.MODEL_VISION,
        provider="test",
    )
    return conform_observations((observation,), (uuid4(),), runtime.load_contract(1)).items[0]


def _row(sku):
    rosetta, printed, _, _, cost = GOLDEN[sku]
    return _conform([
        ("Order Code", sku),
        ("Product Name", f"product {sku}"),
        ("Packing/ Unit", printed),
        ("Order Units", ORDER_UNITS[sku]),
        ("Price/ Unit (HKD)", f"{cost}"),
    ])

# Straight from the sheet's Alfamedic columns. Do not "tidy" these values —
# they are what a person wrote down after reading the printed catalogue.
# What the catalogue prints in its Order Units column for each of the five.
ORDER_UNITS = {
    "EN7502": "1 bot", "AP1900": "1 box", "C23811H": "1 box",
    "ME5701": "1 bot", "VE3255": "1 box",
}

GOLDEN = {
    # sku:        (rosetta sku, printed Packing/Unit, purchase unit, qty per unit, unit cost)
    "EN7502":   ("50010319", "30ml/ bot",     "bottle", 30,  Decimal("1390.00")),
    "AP1900":   ("50010255", "100 tabs/ box", "box",    100, Decimal("1486.00")),
    "C23811H":  ("40005812", "1 set/ box",    "box",    1,   Decimal("310.00")),
    "ME5701":   ("50010301", "50ml/ bot",     "bottle", 50,  Decimal("130.00")),
    "VE3255":   ("50010295", "100 tabs/ box", "box",    100, Decimal("378.00")),
}


def test_the_sheet_and_the_export_describe_the_same_five_products():
    """Five SKUs in the sheet, five rows in the export, and they line up."""
    assert len(GOLDEN) == 5
    assert {v[0] for v in GOLDEN.values()} == {
        "50010319", "50010255", "40005812", "50010301", "50010295"
    }


@pytest.mark.parametrize("sku", sorted(GOLDEN))
def test_the_cost_matches_the_hand_filled_sheet(sku):
    """Every one of the five agreed to the cent on the live export.

    The loudest assertion here: the number a buyer pays is the one thing no
    pipeline change may quietly move.
    """
    expected = GOLDEN[sku][4]
    assert Decimal(_row(sku).normalized_fields["cost"]["amount"]) == expected


@pytest.mark.parametrize("sku", sorted(GOLDEN))
def test_you_order_one_pack_not_its_contents(sku):
    """You order ONE box; the hundred tablets are what it holds.

    The order multiple comes from the catalogue's own Order Units column
    ("1 box"), not from the leading count of the packing text.
    """
    increment = (_row(sku).normalized_fields.get("packaging") or {}).get("order_increment") or {}
    assert Decimal(increment.get("amount", "0")) == Decimal(1)


@pytest.mark.parametrize("sku", sorted(GOLDEN))
def test_the_pack_states_how_many_it_holds(sku):
    """The sheet's quantity_per_unit: 30 per bottle, 100 per box."""
    expected = GOLDEN[sku][3]
    packaging = _row(sku).normalized_fields.get("packaging") or {}
    assert Decimal(packaging.get("sellable_units_per_purchase_unit", "0")) == Decimal(expected)


def test_the_price_basis_is_the_unit_the_supplier_sells_in():
    """$1,390 is per BOTTLE. A fixed PIECE basis divides every per-unit cost
    by the wrong denominator."""
    bases = {(_row(sku).normalized_fields["cost"]["price_basis"]["code"]) for sku in GOLDEN}
    assert bases == {"BOTTLE", "BOX"}, "the unit the supplier sells in, per row"


def test_a_product_named_only_by_its_size_is_visible_as_a_gap():
    """ME5701 prints '50ml' as its whole product name.

    The continuation rule completes a name beginning "size ..."; a bare
    measurement does not match, so the export carries '50ml'. Recorded so the
    next person sees it was known rather than missed.
    """
    assert GOLDEN["ME5701"][1].startswith("50ml")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

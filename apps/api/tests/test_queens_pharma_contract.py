"""Queen's Pharma's Zoetis forms, read from the pages we captured.

Three forms, one table shape, ten products, and not one item code anywhere —
which is why this set is verified here rather than as a golden set. The golden
harness joins the sheet to the published export on `supplier_product_code`.
Queen's rows have none, and the code they eventually carry is the Rosetta SKU
adopted at the match, which the sheet has never seen. The two sides cannot be
joined, so the comparison happens where the facts are: the recorded pages
against the sheet's own rows, matched by product.

`golden_sheet_rows.csv` is a PROJECTION of the Google Sheet, filtered to
Queen's with N/A markers blanked. Refresh it from the sheet; never edit it to
make this file pass. The sheet is the truth and a disagreement is a real one.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/queens.db")
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

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "queens_pharma"
PAGES = [FIXTURES / f"page_{n}.json" for n in (1, 2, 3)]
#: The same three forms read a SECOND time, independently. The two readings
#: disagree about which optional fields to populate — supplier_identity_text and
#: page_brand_text appear in one and not the other — so replaying both is what
#: stops the contract being tuned to a single vision run.
ALT_PAGES = [FIXTURES / f"alt_page_{n}.json" for n in (1, 2, 3)]
CONTRACT = "queens_pharma.zoetis_price_list.v1"

KIT = "AlphaTRAK 3 (Blood Glucose Monitoring System ) Starter Kit"

#: Every product on the three forms, as the page prints it:
#: price, Unit column, Order column, Pack Size column.
#: p1 Cytopoint (2024), p2 Librela + Solensia (2025), p3 AlphaTRAK (2024).
PRINTED = {
    "CYTOPOINT 10mg": ("650", "2vials per box", "Box", "1ml/bot"),
    "CYTOPOINT 20mg": ("950", "2vials per box", "Box", "1ml/bot"),
    "CYTOPOINT 30mg": ("1,150", "2vials per box", "Box", "1ml/bot"),
    "CYTOPOINT 40mg": ("1,300", "2vials per box", "Box", "1ml/bot"),
    "LIBRELA 5mg": ("600", "2vials per box", "Box", "1ml/bot"),
    "LIBRELA 10mg": ("630", "2vials per box", "Box", "1ml/bot"),
    "LIBRELA 15mg": ("680", "2vials per box", "Box", "1ml/bot"),
    "SOLENSIA 7MG": ("650", "2vials per box", "Box", "1ml/bot"),
    # The kit's Order cell prints "Set"; the contract's value_map rewrites it
    # to PACK, so that is what a conformed row carries. What the PAGE printed is
    # pinned separately, in test_the_page_really_prints_set_for_the_kit.
    KIT: ("1250", "1 set", "PACK", "1 set/box"),
    "AlphaTRAK 3": ("350", "1 box", "Box", "50's/box"),
}

#: What each row must RESOLVE to: price basis, sellable units per purchase.
#: The injectables are the six the golden sheet also rules on.
RESOLVED = {
    "CYTOPOINT 10mg": ("BOX", "2"), "CYTOPOINT 20mg": ("BOX", "2"),
    "CYTOPOINT 30mg": ("BOX", "2"), "CYTOPOINT 40mg": ("BOX", "2"),
    "LIBRELA 5mg": ("BOX", "2"), "LIBRELA 10mg": ("BOX", "2"),
    "LIBRELA 15mg": ("BOX", "2"), "SOLENSIA 7MG": ("BOX", "2"),
    KIT: ("PACK", "1"),
    "AlphaTRAK 3": ("BOX", "1"),
}


def _conform(pages):
    """Replay a set of recorded pages through extraction and conformance."""
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        calls = _install_golden_replay(patch, pages)
        result = extract_evidence(_blank_pdf(len(pages)), "queens-zoetis.pdf", "application/pdf")
        assert calls["n"] == len(pages), "replayed from the recorded pages — no provider was called"
        runtime = SupplierSourceRuntimeContract(
            declaration=get_supplier_source_contract(CONTRACT, "v1").declaration
        )
        return conform_observations(
            result.observations, tuple(uuid4() for _ in result.observations), runtime
        )
    finally:
        patch.undo()


@pytest.fixture(scope="module")
def conformed():
    """The three captured forms, replayed through extraction and conformance once."""
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        calls = _install_golden_replay(patch, PAGES)
        result = extract_evidence(_blank_pdf(len(PAGES)), "queens-zoetis.pdf", "application/pdf")
        assert calls["n"] == len(PAGES), "replayed from the recorded pages — no provider was called"
        runtime = SupplierSourceRuntimeContract(
            declaration=get_supplier_source_contract(CONTRACT, "v1").declaration
        )
        yield conform_observations(
            result.observations, tuple(uuid4() for _ in result.observations), runtime
        )
    finally:
        patch.undo()


def _products(outcome) -> dict[str, object]:
    """The rows that are products. The forms' header and footer lines conform
    too, carrying nothing — the staging stage drops them as non-catalogue."""
    return {
        (row.raw_fields.get("product_name") or "").strip(): row
        for row in outcome.items
        if (row.raw_fields.get("product_name") or "").strip()
    }


def _sheet_rows() -> list[dict[str, str]]:
    with (FIXTURES / "golden_sheet_rows.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _money(text) -> Decimal:
    return Decimal(re.sub(r"[^\d.]", "", str(text or "0")) or "0")


def _packaging(row) -> dict:
    return row.normalized_fields.get("packaging") or {}


def _cost(row) -> dict:
    return row.normalized_fields.get("cost") or {}


# --- what the pages say ------------------------------------------------------


def test_every_product_on_all_three_forms_conforms(conformed):
    """Ten products across three layouts, none held.

    The forms differ: Cytopoint and AlphaTRAK print a leading Form column of
    dosage icons, the Librela form does not — six columns there, seven
    elsewhere. A contract keyed on column position rather than header name
    would silently lose a whole page here.
    """
    assert set(_products(conformed)) == set(PRINTED)

    blocking = [
        i.issue_code for row in conformed.items for i in row.issues if i.severity == "BLOCKING"
    ]
    assert blocking == []


def test_the_header_and_footer_never_reach_the_desk(conformed):
    """A form is also an order form: it carries a date, a clinic name, an MOQ
    note and phone numbers, and the vision pass reads all of it. Under a
    TABULAR contract those cells-less lines are page furniture and are skipped
    outright — declared PDF (not PDF_TABLE) they would each arrive BLOCKING and
    bury the desk in six non-issues per ingestion."""
    assert conformed.items, "nothing conformed at all"
    assert all((r.raw_fields.get("product_name") or "").strip() for r in conformed.items)
    assert conformed.skipped_count >= 6, (
        f"expected the forms' surrounding text to be skipped as furniture, "
        f"skipped_count={conformed.skipped_count}"
    )


def test_the_page_really_prints_set_for_the_kit():
    """Page-truth for the one value the contract rewrites. If Queen's stops
    printing 'Set' the value_map is dead code and should go, so the printed
    word is pinned where the mapping cannot hide it."""
    import json

    page = json.loads((FIXTURES / "page_3.json").read_text(encoding="utf-8"))
    orders = [row["cells"][-1] for table in page["tables"] for row in table["rows"]]

    assert orders == ["Set", "Box"]


def test_the_price_and_pack_survive_from_the_page(conformed):
    """Price, order unit and pack, exactly as printed. This is the whole read."""
    rows = _products(conformed)

    for name, (price, unit, order, pack) in PRINTED.items():
        raw = rows[name].raw_fields
        assert _money(raw.get("cost")) == _money(price), name
        assert (raw["additional_fields"].get("units_per_purchase_unit") or "") == unit, name
        assert (raw["additional_fields"].get("purchase_unit") or "") == order, name
        assert (raw["additional_fields"].get("content_measure") or "") == pack, name


def test_each_row_resolves_the_basis_its_own_order_column_names(conformed):
    """A box of two vials, a box of fifty strips, and a kit sold as one set.
    Pinning a single basis would price the $1,250 starter kit as a box."""
    rows = _products(conformed)

    for name, (basis, units) in RESOLVED.items():
        pack = _packaging(rows[name])
        assert (pack.get("price_basis") or {}).get("code") == basis, name
        assert str(pack.get("sellable_units_per_purchase_unit")) == units, name
        assert (_cost(rows[name]).get("price_basis") or {}).get("code") == basis, name


def test_a_pack_size_of_fifty_does_not_become_fifty_sellable_units(conformed):
    """AlphaTRAK test strips: "50's/box" is what is IN the box, and the box is
    still one purchase at $350. Read as a count it would price a strip at $7 and
    understate every box we buy by fiftyfold."""
    strips = _products(conformed)["AlphaTRAK 3"]

    assert strips.raw_fields["additional_fields"]["content_measure"] == "50's/box"
    assert str(_packaging(strips).get("sellable_units_per_purchase_unit")) == "1"


def test_no_row_carries_a_supplier_code_and_that_is_not_a_failure(conformed):
    """No Queen's form prints one. Rows conform without identity and take it at
    the match — the 2026-08-26 ruling. A code appearing here means the source
    changed and the contract should be told."""
    assert all(not (r.raw_fields.get("supplier_sku") or "") for r in _products(conformed).values())


def test_the_brand_is_zoetis_even_though_queens_invoices_it(conformed):
    """Queen's is the distributor. A row filed under the distributor's name is
    a row a clinician cannot find."""
    assert {r.raw_fields.get("brand") for r in _products(conformed).values()} == {"Zoetis"}


# --- what BizOps recorded ----------------------------------------------------


def _sheet_to_form(sheet_name: str, captured: dict) -> str:
    """Join the sheet's name to the form's. The sheet writes 'Cytopoint Inj
    40mg'; the form prints 'CYTOPOINT 40mg'. Brand stem plus strength is the
    only thing both sides state — there is no code to join on."""
    lowered = sheet_name.lower()
    stem = re.split(r"[\s(]", lowered)[0]
    if "solensia" in lowered:
        stem = "solensia"
    strength = re.search(r"(\d+)\s*mg", lowered)
    for name in captured:
        low = name.lower()
        if stem in low and (not strength or strength.group(1) in low):
            return name
    raise AssertionError(f"no captured row matches sheet row {sheet_name!r}")


def test_the_sheet_and_the_forms_agree_on_every_price(conformed):
    """Six of the ten are on the golden sheet, priced by hand from real
    invoices. Each must match the form to the dollar — a supplier price that
    drifts on its way to the desk is the one defect that reaches a PO."""
    rows = _products(conformed)
    sheet = _sheet_rows()
    assert len(sheet) == 6, "the sheet projection changed — refresh it and re-read this test"

    for row in sheet:
        captured = rows[_sheet_to_form(row["product_name"], rows)]
        assert _money(captured.raw_fields.get("cost")) == _money(row["catalogue_price_hkd"]), (
            f"{row['product_name']}: sheet {row['catalogue_price_hkd']}, "
            f"form {captured.raw_fields.get('cost')}"
        )


def test_the_pipeline_derives_the_pack_bizops_recorded_by_hand(conformed):
    """The one genuinely ambiguous thing on the page. BizOps ruled that $650
    buys a BOX of two — $325 a vial. The pipeline reads the same from the Unit
    column, independently. Two readings of one fact, so a future disagreement
    announces itself instead of quietly doubling a cost."""
    rows = _products(conformed)

    for row in _sheet_rows():
        pack = _packaging(rows[_sheet_to_form(row["product_name"], rows)])
        assert row["catalogue_price_basis_uom"] == "BOX", row["product_name"]
        assert row["catalogue_price_basis_qty"] == "1", row["product_name"]
        assert row["package_configuration"] == "2 VIALS / BOX", row["product_name"]

        assert (pack.get("price_basis") or {}).get("code") == row["catalogue_price_basis_uom"]
        assert str(pack.get("sellable_units_per_purchase_unit")) == row["sellable_units_per_price_basis"]
        assert (pack.get("sellable_unit_uom") or {}).get("code") == "VIAL", row["product_name"]


def test_the_sheet_records_no_code_for_any_queens_row():
    """Why this set is verified here and not as a golden set: the harness joins
    sheet to export on the supplier code, and neither side has one."""
    assert all(not row["supplier_product_code"] for row in _sheet_rows())


# --- robustness against the model that did the reading ------------------------


def test_a_second_independent_reading_of_the_same_forms_agrees(conformed):
    """The same three forms, read twice by the production vision path, produce
    envelopes that DIFFER: one populates supplier_identity_text and
    page_brand_text where the other leaves them null. None of that may change
    what the contract reads, or the catalogue depends on which run happened to
    read it — and the next run is always a different one.
    """
    alt = _conform(ALT_PAGES)

    def facts(outcome):
        return {
            (row.raw_fields.get("product_name") or "").strip(): (
                str(row.raw_fields.get("cost")),
                str((row.normalized_fields.get("packaging") or {}).get("sellable_units_per_purchase_unit")),
                ((row.normalized_fields.get("packaging") or {}).get("price_basis") or {}).get("code"),
                row.raw_fields.get("brand"),
            )
            for row in outcome.items
            if (row.raw_fields.get("product_name") or "").strip()
        }

    assert facts(alt) == facts(conformed)


def test_the_two_readings_really_are_different_envelopes():
    """Guards the test above from becoming a tautology: if someone copies one
    reading over the other, the agreement assertion still passes and proves
    nothing. These are the fields that actually differ."""
    import json

    a = json.loads((FIXTURES / "page_3.json").read_text(encoding="utf-8"))
    b = json.loads((FIXTURES / "alt_page_3.json").read_text(encoding="utf-8"))

    assert a.get("page_brand_text") != b.get("page_brand_text")
    assert a.get("supplier_identity_text") != b.get("supplier_identity_text")


def test_a_form_filled_in_by_a_clinic_yields_none_of_the_handwriting():
    """One of the two Cytopoint documents we hold was filled in and signed by a
    clinic before it reached us — a date, a clinic name, an order-by signature
    and handwritten quantities down the Order column.

    Neither reading carries any of it. The Order cells hold the printed unit,
    and no clinic name, date or quantity appears anywhere in the envelope. That
    is what makes QUEENS_FORM_IS_ALSO_AN_ORDER_FORM a note for a reviewer
    rather than a way for one clinic's order to become our catalogue.
    """
    import json
    import re

    for name in ("page_1.json", "cytopoint_second_read.json"):
        page = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        orders = [row["cells"][-1] for table in page["tables"] for row in table["rows"]]
        assert orders == ["Box", "Box", "Box", "Box"], name

        blob = json.dumps(page, ensure_ascii=False)
        assert not re.search(r"Hugh|Stanley|046\b|28\s*/\s*7", blob), (
            f"{name} carries handwriting from the clinic that filled the form in"
        )


def test_the_two_readings_of_the_cytopoint_form_price_it_identically():
    """Prices are the one thing two readings must never disagree on."""
    import json

    def priced(name):
        page = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
        return {r["cells"][1]: r["cells"][5] for t in page["tables"] for r in t["rows"]}

    assert priced("page_1.json") == priced("cytopoint_second_read.json")


def test_the_marketing_banner_is_not_a_catalogue():
    """Queen's send a stock banner alongside the forms. It has no table and no
    prices, and must yield no products rather than a run that fails — the
    pipeline classes it as furniture and moves on."""
    banner = _conform([FIXTURES / "stock_banner.json"])

    assert [r for r in banner.items if (r.raw_fields.get("product_name") or "").strip()] == []

"""United Italian's general-practice list, read from the pages we captured.

The largest source we read — 657 priced lines over 40 pages, 26 categories, 43
distributed brands — and the one that states the most per row. Eleven pages are
recorded here: every page carrying a golden-sheet row, plus one of each layout.

What these tests are really guarding is the PRICE BASIS. This supplier prints
it inside the price cell, twenty-three different ways, and on some rows prints
two prices in one cell. Read the wrong half and a bag is priced as a box.

`golden_sheet_rows.csv` is a PROJECTION of the Google Sheet filtered to this
supplier. Refresh it from the sheet; never edit it to make this file pass.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/ui.db")
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

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "united_italian"
PAGES = [FIXTURES / f"page_{n}.json" for n in range(1, 12)]
CONTRACT = "united_italian.gp_price_list.v1"

#: Which page of the real PDF each fixture came from, so a finding here can be
#: taken back to the document.
DOC_PAGES = {1: 4, 2: 5, 3: 12, 4: 15, 5: 16, 6: 17, 7: 22, 8: 23, 9: 28, 10: 30, 11: 31}


@pytest.fixture(scope="module")
def conformed():
    """The eleven captured pages, replayed through extraction and conformance."""
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        calls = _install_golden_replay(patch, PAGES)
        result = extract_evidence(_blank_pdf(len(PAGES)), "united-italian.pdf", "application/pdf")
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


def _by_code(outcome):
    out = {}
    for row in _products(outcome):
        code = (row.raw_fields.get("supplier_sku") or "").strip()
        if code:
            out.setdefault(code, row)
    return out


def _cost(row):
    return row.normalized_fields.get("cost") or {}


def _money(text):
    cleaned = re.sub(r"[^\d.]", "", str(text or ""))
    return Decimal(cleaned) if cleaned else None


def _sheet_rows():
    with (FIXTURES / "golden_sheet_rows.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --- the read ----------------------------------------------------------------


def test_every_row_on_the_recorded_pages_conforms(conformed):
    """282 products across four different column shapes, none held.

    The shapes differ page to page: Code/Product/Price, the same plus a Pack
    column, the same with that column UNLABELLED, and the intravenous pages
    that print 'Price (HK$)' twice. A contract keyed on position rather than
    heading — or unable to address a repeat — would lose whole sections.
    """
    products = _products(conformed)

    assert len(products) == 282
    blocking = [
        i.issue_code for row in conformed.items for i in row.issues if i.severity == "BLOCKING"
    ]
    assert blocking == []


def test_the_only_flagged_rows_are_the_ones_the_supplier_would_not_price(conformed):
    """'*****' is how this list prints a product quoted by the sales desk
    instead. A stated refusal, not an unreadable cell — a warning for a person,
    never a blocked row."""
    flagged = [
        (row.raw_fields.get("product_name"), i.issue_code)
        for row in conformed.items
        for i in row.issues
    ]

    assert {code for _, code in flagged} == {"CONTRACT_NULL_COST_REQUIRES_REVIEW"}
    assert {name for name, _ in flagged} == {"Eolis Air Manager", "Hygeolis"}


def test_every_priced_row_resolves_a_basis(conformed):
    """A price with no basis is a number nobody can buy anything with. It is
    also the one failure this source can produce silently, because the basis is
    printed inside the price rather than in a column of its own."""
    for row in _products(conformed):
        cost = _cost(row)
        if cost.get("amount") is None:
            continue
        assert (cost.get("price_basis") or {}).get("code"), row.raw_fields.get("product_name")


# --- the basis, which is where this source is dangerous ----------------------


def test_the_basis_is_read_from_the_price_cell_row_by_row(conformed):
    """Twenty-three spellings across the list, and the row decides — not the
    contract. A single declared basis would price a roll as a box."""
    seen = {
        (_cost(r).get("price_basis") or {}).get("code")
        for r in _products(conformed)
        if _cost(r).get("amount") is not None
    }

    assert {"BOX", "BAG", "PIECE", "BOTTLE", "PACK", "CASE", "TUBE", "VIAL"} <= seen


def test_two_prices_in_one_cell_take_the_first_pair(conformed):
    """Some intravenous pages render both prices as two columns and others as
    one cell — '$100.00 / bag $1,200.00 / box'. The first pair is the per-unit
    price on every page and on the golden sheet. Taking the last would price a
    single bag at the cost of a box of twelve, and which one you get would
    depend on how the vision pass happened to segment that table.
    """
    merged = [
        r for r in _products(conformed)
        if "Plasma-Lyte" in (r.raw_fields.get("product_name") or "")
    ]
    assert merged, "the merged-cell rows are gone — re-check the fixtures"

    cost = _cost(merged[0])
    assert _money(cost.get("amount")) == Decimal("100.00")
    assert (cost.get("price_basis") or {}).get("code") == "BAG"


def test_a_price_that_names_a_count_becomes_a_pack_of_that_many(conformed):
    """'$52.00 / 100's' — a hundred of something, in nothing named. 106 of the
    list's priced lines read this way. PACK rather than BOX, on the 2026-09-01
    ruling: the supplier stated the quantity and withheld the vessel, and
    naming one would be our invention."""
    gloves = _by_code(conformed).get("1208A – D")

    assert gloves is not None
    assert _money(_cost(gloves).get("amount")) == Decimal("46.00")
    assert (_cost(gloves).get("price_basis") or {}).get("code") == "PACK"


def test_a_suture_gauge_is_never_mistaken_for_a_quantity():
    """The count rule requires the possessive form the source actually uses.
    A suture size and a genuine fraction sit in the same position as a count
    and must not resolve to a pack of anything."""
    from services.catalogue_conformance import _BARE_COUNT

    for count in ("100’s", "50's", "1,000’s"):
        assert _BARE_COUNT.fullmatch(count), count
    for other in ("8", "3", '2"', "0 (1.5) 45cm DS19", "box"):
        assert not _BARE_COUNT.fullmatch(other), other


def test_an_undeclared_unit_holds_the_row_rather_than_guessing(conformed):
    """The value map governs. A unit United Italian invent next year resolves
    to nothing and the row is held — the whole point of declaring spellings."""
    declaration = get_supplier_source_contract(CONTRACT, "v1").declaration
    spellings = {s.casefold() for s in declaration.pricing.price_basis_value_map}

    assert "box" in spellings and "bag" in spellings and "pc" in spellings
    assert "flagon" not in spellings


# --- against what BizOps recorded --------------------------------------------


def _sheet_to_page(code, by_code):
    stem = code.split()[0].strip("–-")
    return next((k for k in by_code if k.split()[0].strip("–-") == stem), None)


def test_every_sheet_code_is_found_on_the_recorded_pages(conformed):
    """The pages were chosen to carry them. A code going missing means the
    fixture set drifted away from what the sheet describes."""
    by_code = _by_code(conformed)
    coded = [r for r in _sheet_rows() if (r["supplier_product_code"] or "").strip()]

    assert len(coded) == 13
    for row in coded:
        assert _sheet_to_page(row["supplier_product_code"], by_code), row["supplier_product_code"]


def test_the_price_matches_the_sheet_wherever_the_sheet_is_right(conformed):
    """Eleven of thirteen traceable codes agree to the cent.

    The twelfth is a known sheet defect (3549232, below). The thirteenth,
    89471, agrees on the amount and disagrees on the basis — also below.
    """
    by_code = _by_code(conformed)
    KNOWN_PRICE_DISPUTE = {"3549232"}

    for row in _sheet_rows():
        code = (row["supplier_product_code"] or "").strip()
        if not code or code in KNOWN_PRICE_DISPUTE:
            continue
        key = _sheet_to_page(code, by_code)
        if key is None:
            continue
        got = _money(_cost(by_code[key]).get("amount"))
        want = _money(row["catalogue_price_hkd"])
        if want is None:
            continue
        assert got == want, f"{code}: sheet {want}, page {got}"


def test_the_sheet_and_the_page_disagree_about_89471_and_the_page_wins(conformed):
    """The sheet says $52.00 buys a CASE of fifty drapes. The page prints
    '(50's / case) $52.00 / pc' — fifty to a case, and the price is PER PIECE.

    A fiftyfold difference in the cost of every drape we buy, and the kind that
    reaches a purchase order. The contract reads what the supplier printed.
    Pinned rather than quietly resolved: if BizOps correct the sheet this test
    tells us, and if they confirm CASE instead then the page has been
    misunderstood and this contract needs revisiting.
    """
    drape = _by_code(conformed).get("89471")
    sheet = next(r for r in _sheet_rows() if r["supplier_product_code"] == "89471")

    assert _money(_cost(drape).get("amount")) == Decimal("52.00") == _money(sheet["catalogue_price_hkd"])
    assert (_cost(drape).get("price_basis") or {}).get("code") == "PIECE"
    assert sheet["catalogue_price_basis_uom"] == "CASE"


def test_the_sheet_and_the_page_disagree_about_the_propofol_price(conformed):
    """Sheet $135.00, 2025 list $320.00, same product. The sheet predates this
    price list or is wrong; either way a person has to say which."""
    propofol = _by_code(conformed).get("3549232")
    sheet = next(r for r in _sheet_rows() if r["supplier_product_code"] == "3549232")

    assert _money(_cost(propofol).get("amount")) == Decimal("320.00")
    assert _money(sheet["catalogue_price_hkd"]) == Decimal("135.00")


def test_the_sheet_names_a_container_the_page_never_prints(conformed):
    """On the '/100's' rows the sheet records BOX. The page names no vessel at
    all, so the contract resolves PACK. A deliberate divergence, pinned so it
    stays a decision rather than becoming a surprise."""
    by_code = _by_code(conformed)

    for code in ("302032", "301805", "A4019", "1208A – D"):
        key = _sheet_to_page(code, by_code)
        sheet = next(r for r in _sheet_rows() if r["supplier_product_code"].strip() == code)
        assert (_cost(by_code[key]).get("price_basis") or {}).get("code") == "PACK", code
        assert sheet["catalogue_price_basis_uom"] == "BOX", code

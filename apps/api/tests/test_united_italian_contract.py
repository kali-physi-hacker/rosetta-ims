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


def test_every_sheet_price_matches_the_page_to_the_cent(conformed):
    """No exceptions any more.

    Three rows disagreed when this contract was written and all three were the
    sheet's error, corrected in the projection and written up in
    docs/catalogue/golden-sheet-conformance-ledger.md. The sheet itself still
    holds the old values, so re-projecting an unedited sheet regresses this on
    purpose — that is the ledger's whole point.
    """
    by_code = _by_code(conformed)

    for row in _sheet_rows():
        code = (row["supplier_product_code"] or "").strip()
        key = _sheet_to_page(code, by_code) if code else None
        want = _money(row["catalogue_price_hkd"])
        if key is None or want is None:
            continue
        got = _money(_cost(by_code[key]).get("amount"))
        assert got == want, f"{code}: sheet {want}, page {got}"


#: The one basis where the SHEET is right and our vocabulary is the lossy half:
#: the page prints "/ sleeve", there is no SLEEVE unit code, so the pipeline
#: resolves OTHER and keeps the supplier's own word as the label. Adding a
#: SLEEVE code would retire this.
_NO_UNIT_CODE_FOR = {"SLEEVE": "OTHER"}


def test_every_sheet_basis_matches_the_page(conformed):
    """The basis is the half of a price that can be wrong by fiftyfold while
    the number looks perfect — 89471 was exactly that, and is corrected."""
    by_code = _by_code(conformed)

    for row in _sheet_rows():
        code = (row["supplier_product_code"] or "").strip()
        want = (row["catalogue_price_basis_uom"] or "").strip().upper()
        key = _sheet_to_page(code, by_code) if code else None
        if key is None or not want:
            continue
        got = (_cost(by_code[key]).get("price_basis") or {}).get("code")
        assert got == _NO_UNIT_CODE_FOR.get(want, want), f"{code}: sheet {want}, page {got}"


def test_the_drape_is_priced_per_piece(conformed):
    """The correction most worth a second pair of eyes. The page prints
    '(50's / case) $52.00 / pc': fifty to a case, and the price is per piece.
    The sheet had recorded the basis as CASE, which made $52.00 buy all fifty
    — a fiftyfold understatement of the cost of every drape."""
    drape = _by_code(conformed)["89471"]
    sheet = next(r for r in _sheet_rows() if r["supplier_product_code"] == "89471")

    assert (_cost(drape).get("price_basis") or {}).get("code") == "PIECE"
    assert sheet["catalogue_price_basis_uom"] == "PIECE"
    assert sheet["package_configuration"] == "50 PIECES / CASE"


def test_lactated_ringer_is_no_longer_filed_under_the_saline_code(conformed):
    """The sheet listed 'LRS Fluid Bag 500ml' under AHB1323HK — which the page
    says is NaCl 0.9% — and gave it NaCl's figures too, making it a mislabelled
    duplicate of the row beside it. Lactated Ringer 500ml is 2B2323Q."""
    by_code = _by_code(conformed)
    rows = {r["supplier_product_code"]: r for r in _sheet_rows()}

    assert "2B2323Q" in rows and _money(rows["2B2323Q"]["catalogue_price_hkd"]) == Decimal("45.00")
    assert _money(_cost(by_code["2B2323Q"]).get("amount")) == Decimal("45.00")
    # The saline keeps its own code, its own price and its own pack.
    assert _money(_cost(by_code["AHB1323HK"]).get("amount")) == Decimal("46.00")
    assert [c for c in rows].count("AHB1323HK") == 1


def test_a_price_that_names_a_count_agrees_with_the_sheet_as_a_pack(conformed):
    """The sheet had recorded BOX for the '/100's' rows — a container the page
    never prints. Corrected to the generic PACK, so page and sheet now agree
    rather than diverging by convention."""
    by_code = _by_code(conformed)

    for code in ("302032", "301805", "A4019", "1208A – D"):
        key = _sheet_to_page(code, by_code)
        sheet = next(r for r in _sheet_rows() if r["supplier_product_code"].strip() == code)
        assert (_cost(by_code[key]).get("price_basis") or {}).get("code") == "PACK", code
        assert sheet["catalogue_price_basis_uom"] == "PACK", code


# --- the whole document, read a second time and a different way ---------------
#
# `whole_document.json` is a BizOps extraction of all 40 pages in one envelope,
# against the eleven per-page envelopes recorded here from the production path.
# The two disagree about things a contract can easily be over-fitted to:
#
#   * the two price columns are named DISTINCTLY there ("Price (HK$) per unit",
#     "Price (HK$) per box") and IDENTICALLY here — the shape the contract's
#     occurrence lookup was built for;
#   * there is no separate Pack column there at all;
#   * the page identity is stated there and null on every page here.
#
# Reading both is the only way to know the contract answers the same either way.


def _conform_whole_document():
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        _install_golden_replay(patch, [FIXTURES / "whole_document.json"])
        result = extract_evidence(_blank_pdf(1), "united-italian.pdf", "application/pdf")
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


def test_the_whole_document_reads_with_one_row_held(whole_document):
    """801 products across all 40 pages. The single held row is a price cell
    that came back holding pack text — a mis-segmentation a person should see,
    not something to guess past."""
    products = [r for r in whole_document.items if (r.raw_fields.get("product_name") or "").strip()]
    blocking = [
        i.issue_code for r in whole_document.items for i in r.issues if i.severity == "BLOCKING"
    ]

    assert len(products) == 801
    assert blocking == ["CONTRACT_COST_UNPARSEABLE"]


def test_a_case_price_is_never_the_unit_price_wearing_a_hat(whole_document, conformed):
    """The bug this second reading found.

    case_price declared the same exact heading as unit_price, so an exact match
    won outright and handed it the FIRST price column. Every single-price row in
    the catalogue — all 282 of the recorded pages, and 746 of the whole
    document — came out carrying a case price equal to its unit price: a bulk
    term claiming a whole box costs what one piece does.

    A case price must exist only where the page prints a second one.
    """
    for outcome in (whole_document, conformed):
        leaked = [
            r for r in outcome.items
            if (fields := r.raw_fields.get("additional_fields") or {})
            and fields.get("case_price")
            and str(fields["case_price"]).strip() == str(fields.get("unit_price") or "").strip()
        ]
        assert leaked == []


def test_both_readings_agree_about_every_price_they_share(whole_document, conformed):
    """The contract must not depend on how the vision pass happened to name or
    split the columns. Same codes, same money, same basis, either way."""
    def by_code(outcome):
        out = {}
        for row in outcome.items:
            code = (row.raw_fields.get("supplier_sku") or "").strip()
            if code and (row.normalized_fields.get("cost") or {}).get("amount") is not None:
                cost = row.normalized_fields["cost"]
                out.setdefault(code, (_money(cost["amount"]), (cost.get("price_basis") or {}).get("code")))
        return out

    mine, theirs = by_code(conformed), by_code(whole_document)
    shared = set(mine) & set(theirs)

    assert len(shared) > 150, f"only {len(shared)} codes in common — the fixtures drifted apart"
    disagreed = {c: (mine[c], theirs[c]) for c in shared if mine[c] != theirs[c]}
    assert disagreed == {}


def test_a_price_naming_a_count_of_something_is_still_a_pack(whole_document):
    """'$115.00 / 24 rolls' — twenty-four of something bought together for one
    price, which is the same statement as '/ 100's' with the countable named.
    The trailing word must already be a declared spelling, so this accepts
    nothing the contract had not accepted anyway."""
    tapes = [
        r for r in whole_document.items
        if (r.raw_fields.get("supplier_sku") or "").startswith("MEDI-T3")
    ]

    assert tapes, "the Medi-Tape rows are gone — re-check the fixture"
    for row in tapes:
        cost = row.normalized_fields.get("cost") or {}
        assert _money(cost.get("amount")) == Decimal("115.00")
        assert (cost.get("price_basis") or {}).get("code") == "PACK"


def test_a_product_listed_without_a_price_is_still_a_product(whole_document):
    """Page 7 lists the Vacutainer tube range by name alone. Requiring a price
    blocked 26 real products with a message about a missing field, which is not
    what the page is saying."""
    tubes = [
        r for r in whole_document.items
        if "Vacutainer" in (r.raw_fields.get("product_name") or "")
    ]

    assert len(tubes) >= 6
    for row in tubes:
        assert not [i for i in row.issues if i.severity == "BLOCKING"]


def test_the_second_refusal_to_publish_a_price_is_recognised(whole_document):
    """'*****' is not the only way this list declines. Three products say 'For
    details, please contact the Sales Department' instead — a stated refusal,
    and a warning for a person rather than a blocked row."""
    flagged = [
        r for r in whole_document.items
        for i in r.issues if i.issue_code == "CONTRACT_NULL_COST_REQUIRES_REVIEW"
    ]

    assert len(flagged) == 6

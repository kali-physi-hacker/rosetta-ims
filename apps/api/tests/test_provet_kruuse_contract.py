"""ProVet Kruuse's Hong Kong price list, read from the pages we captured.

The plainest source we hold — three columns, one price per line — which makes
it the one where the interesting cases are all about what the page does NOT
say, and about the one place it contradicts itself.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/provet.db")
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

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "provet_kruuse"
PAGES = [FIXTURES / f"page_{n}.json" for n in (1, 2, 3, 4)]
CONTRACT = "provet_kruuse.hk_price_list.v1"


@pytest.fixture(scope="module")
def conformed():
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        calls = _install_golden_replay(patch, PAGES)
        result = extract_evidence(_blank_pdf(len(PAGES)), "provet.pdf", "application/pdf")
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


def _rows_for(outcome, code):
    return [r for r in _products(outcome) if (r.raw_fields.get("supplier_sku") or "").strip() == code]


def _cost(row):
    return row.normalized_fields.get("cost") or {}


def _pack(row):
    return row.normalized_fields.get("packaging") or {}


def _money(text):
    cleaned = re.sub(r"[^\d.]", "", str(text or ""))
    return Decimal(cleaned) if cleaned else None


def _sheet_rows():
    with (FIXTURES / "golden_sheet_rows.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


# --- the read ----------------------------------------------------------------


def test_every_row_conforms_with_nothing_held(conformed):
    """192 rows over four pages, and the header is written two ways across
    them — 'Product Code / Description / List Price (HKD$)' on some pages and
    the shorter 'Code / Description / Price' on others. Both are declared, so
    neither spelling loses a page."""
    assert len(_products(conformed)) == 192

    blocking = [
        i.issue_code for row in conformed.items for i in row.issues if i.severity == "BLOCKING"
    ]
    assert blocking == []


def test_the_only_flagged_rows_are_the_ones_provet_will_not_price(conformed):
    """'Please contact us' is a stated refusal, not an unreadable cell — a
    warning for a person, never a blocked row."""
    flagged = [
        (row.raw_fields.get("supplier_sku"), i.issue_code)
        for row in conformed.items
        for i in row.issues
    ]

    assert {code for _, code in flagged} == {"CONTRACT_NULL_COST_REQUIRES_REVIEW"}
    assert len(flagged) == 10


def test_the_unpriced_rows_are_all_the_range_queens_price(conformed):
    """Every one of them is Zoetis — Cytopoint, Solensia, Beransa — which is
    the range Queen's Pharma quote for us. ProVet list it and decline to price
    it, so nobody need chase them for a number they have withheld."""
    unpriced = {
        (row.raw_fields.get("supplier_sku") or "")
        for row in conformed.items
        for i in row.issues
        if i.issue_code == "CONTRACT_NULL_COST_REQUIRES_REVIEW"
    }

    assert all(
        code.startswith(("CYTO", "SOLEC", "BERA")) for code in unpriced
    ), sorted(unpriced)


# --- what the page does not say ----------------------------------------------


def test_a_price_buys_the_pack_and_not_one_tablet(conformed):
    """The page names no basis at all. $90.00 buys the box of four Cerenia
    tablets (user ruling 2026-09-03) — read as per-tablet it would make that
    box $360 and every tablet cost four times what it does."""
    cerenia = _rows_for(conformed, "CERE16")[0]

    assert _money(_cost(cerenia).get("amount")) == Decimal("90.00")
    assert (_cost(cerenia).get("price_basis") or {}).get("code") == "PACK"
    assert str(_pack(cerenia).get("sellable_units_per_purchase_unit")) == "4"


def test_a_measure_in_the_description_is_never_read_as_a_count(conformed):
    """'Injection 20ml' is one bottle holding twenty millilitres, not twenty
    sellable things. Reading it as a count would divide the price by twenty."""
    methone = _rows_for(conformed, "METHONE")[0]

    assert "20ml" in (methone.raw_fields.get("product_name") or "")
    assert _pack(methone).get("sellable_units_per_purchase_unit") is None
    assert _money(_cost(methone).get("amount")) == Decimal("493.00")


def test_the_drug_schedule_survives_from_the_banner(conformed):
    """The only categorisation the page offers, and it matters: a dangerous
    drug is not stocked or handled like a supplement."""
    sections = {
        (row.raw_fields.get("category") or "").strip()
        for row in _products(conformed)
    }

    assert {"Dangerous Drugs and Psychotropics", "Normal Drugs", "Supplements"} <= sections


# --- where the page contradicts itself ---------------------------------------


def test_a_code_printed_twice_at_two_prices_reaches_the_desk_twice(conformed):
    """The find that named this folder.

    CERE60 (Cerenia 60mg Tablets 4s) is printed twice on page 2 — once at
    $174.00 and once at $198.00, same description both times. Nothing on the
    page says which supersedes the other.

    A reader that keeps the first publishes $174; one that keeps the last
    publishes $198; and neither is reading, both are guessing. So both rows
    survive to the desk and a person decides. The golden sheet's $198.00 is a
    record of someone's decision, not a reading of this page.
    """
    rows = _rows_for(conformed, "CERE60")

    assert len(rows) == 2
    assert {_money(_cost(r).get("amount")) for r in rows} == {
        Decimal("174.00"), Decimal("198.00"),
    }
    assert {(r.raw_fields.get("product_name") or "").strip() for r in rows} == {
        "Cerenia 60mg Tablets 4s"
    }


def test_a_code_printed_twice_at_one_price_is_not_a_contradiction(conformed):
    """ALUTAB600 is also printed twice, but both lines agree. A duplicated
    line, not a disagreement — pinned so the two cases stay distinguishable."""
    rows = _rows_for(conformed, "ALUTAB600")

    assert len(rows) == 2
    assert {_money(_cost(r).get("amount")) for r in rows} == {Decimal("263.00")}


def test_a_bulk_term_hidden_in_a_description_is_left_where_it_can_be_seen(conformed):
    """One row out of 192 hides a quantity break in prose: TEMVET10 reads
    'Injection 10ml ($162 for 12 or up)'. Parsing terms out of English on the
    strength of a single example is how a discount gets invented, so it stays
    in the name where a reviewer will read it."""
    temvet = _rows_for(conformed, "TEMVET10")[0]

    assert "$162 for 12" in (temvet.raw_fields.get("product_name") or "")
    assert _money(_cost(temvet).get("amount")) == Decimal("187.00")


# --- against what BizOps recorded --------------------------------------------


def test_every_coded_sheet_row_matches_the_page_to_the_cent(conformed):
    """Ten of the sheet's fifteen rows carry a code. All ten are on these pages
    and all ten agree — including CERE60, where the sheet's $198.00 is one of
    the two prices the page prints."""
    by_code = {}
    for row in _products(conformed):
        code = (row.raw_fields.get("supplier_sku") or "").strip()
        if code:
            by_code.setdefault(code, []).append(_money(_cost(row).get("amount")))

    checked = 0
    for row in _sheet_rows():
        code = (row["supplier_product_code"] or "").strip()
        want = _money(row["catalogue_price_hkd"])
        if not code or want is None or code not in by_code:
            continue
        assert want in by_code[code], f"{code}: sheet {want}, page {by_code[code]}"
        checked += 1

    assert checked == 11


def test_the_sheet_carries_products_this_price_list_never_prices(conformed):
    """Four sheet rows have no code, and their products — Atropt, Bactroban,
    Doxycycline paste, Panacur — are not on this list at all. ProVet issue a
    separate 'Product list' of names with no prices that does name some of
    them, so a product missing here is a price we have not been given, not a
    delisting.

    A fifth row used to sit here: Prednefrin was recorded N/A on the sheet, and
    page 3 prints it as PREDNEFRIN at the price the sheet already had. Corrected
    in the projection and written up in the conformance ledger.
    """
    names = {(r.raw_fields.get("product_name") or "").lower() for r in _products(conformed)}
    uncoded = [r for r in _sheet_rows() if not (r["supplier_product_code"] or "").strip()]

    assert len(uncoded) == 4
    for row in uncoded:
        stem = row["product_name"].split()[0].lower()
        assert not any(stem in name for name in names), row["product_name"]


# --- the same list, read a second time and a different way -------------------
#
# `whole_document.json` is a BizOps extraction of all four pages in one
# envelope, against the four per-page envelopes recorded here from the
# production path. They disagree about things a contract can be over-fitted to:
# theirs names the header one way on every page and STATES the page identity,
# where the recordings here alternate the header and leave identity null.


def _conform_whole_document():
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    try:
        patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
        patch.setenv("ANTHROPIC_API_KEY", "replay-only")
        _install_golden_replay(patch, [FIXTURES / "whole_document.json"])
        result = extract_evidence(_blank_pdf(1), "provet.pdf", "application/pdf")
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


def test_both_readings_agree_about_every_price(whole_document, conformed):
    """The contract must not depend on how the vision pass named the columns.
    Same codes, same money, same basis, either way — including the code the
    page prints twice, which must arrive twice in both."""
    def priced(outcome):
        out = {}
        for row in _products(outcome):
            code = (row.raw_fields.get("supplier_sku") or "").strip()
            if code:
                out.setdefault(code, []).append(
                    (_money(_cost(row).get("amount")),
                     (_cost(row).get("price_basis") or {}).get("code"))
                )
        return {k: sorted(v, key=lambda x: (x[0] is None, x[0])) for k, v in out.items()}

    mine, theirs = priced(conformed), priced(whole_document)

    assert set(mine) == set(theirs)
    assert mine == theirs


def test_the_page_identity_is_read_when_the_source_states_it(whole_document):
    """This reading STATES 'Kruuse Hong Kong Ltd' where the recorded pages leave
    it null — the name the golden sheet files these rows under, and not the one
    our own supplier record uses. Declared in also_trades_as, so the identity
    check vouches for it instead of refusing the whole document.

    The recorded pages never exercise this path, which is exactly how it would
    go untested until a live read stated an identity and blocked the lot.
    """
    import json

    from services.catalogue_conformance import _identity_names_overlap

    stated = json.loads((FIXTURES / "whole_document.json").read_text(encoding="utf-8"))
    identity = stated.get("supplier_identity_text")
    assert identity, "the fixture no longer states an identity"

    supplier = get_supplier_source_contract(CONTRACT, "v1").declaration.supplier
    names = [supplier.supplier_name, supplier.supplier_code, *supplier.also_trades_as]
    assert any(_identity_names_overlap(identity, n) for n in names if n)

    blocking = [
        i.issue_code
        for row in whole_document.items
        for i in row.issues
        if i.severity == "BLOCKING"
    ]
    assert blocking == []


def test_the_product_list_carries_no_price_to_read():
    """ProVet's other document, kept as a fixture so the claim is checkable
    rather than asserted in prose: 765 product names across 13 tables, one
    column, and not a price or a code anywhere in it. There is nothing for a
    contract to price."""
    import json

    doc = json.loads((FIXTURES / "product_list.json").read_text(encoding="utf-8"))
    columns = {tuple(t.get("columns") or ()) for t in doc["tables"]}

    assert columns == {("Product Name",)}
    assert sum(len(t["rows"]) for t in doc["tables"]) > 700


def test_every_heading_this_document_has_used_is_declared():
    """Three spellings of the same three columns, across four pages of one
    document, and which you get depends on the reading:

        Product Code | Description | List Price (HKD$)
        Code         | Description | Price
        Code         | Product     | Price

    The third was seen only on a live ingestion. With just "Description"
    declared, the whole page headed "Product" lost its name and 71 rows blocked
    as a missing required field — the contract had been fitted to the two
    spellings the recorded pages happened to show.

    Matching is on the exact folded heading, which is what makes "Product" safe
    to declare alongside "Product Code" rather than a way to put the code into
    the name.
    """
    from services.catalogue_conformance import _column_keys

    declaration = get_supplier_source_contract(CONTRACT, "v1").declaration
    fields = {f.field_key: f for f in declaration.fields}

    def declared(field):
        return {k for name in (field.source_column, *field.aliases) if name for k in _column_keys(name)}

    for heading in ("Product Code", "Code"):
        assert set(_column_keys(heading)) & declared(fields["supplier_sku"]), heading
    for heading in ("Description", "Product"):
        assert set(_column_keys(heading)) & declared(fields["product_name"]), heading
    for heading in ("List Price (HKD$)", "Price"):
        assert set(_column_keys(heading)) & declared(fields["unit_price"]), heading

    # The one that must NOT hold: the code column may never answer for the name.
    assert not set(_column_keys("Product Code")) & declared(fields["product_name"])

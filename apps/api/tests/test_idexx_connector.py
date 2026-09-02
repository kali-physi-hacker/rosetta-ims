"""IDEXX: a portal read into a snapshot, and what must not go wrong in between.

The portal prints a product as prose — a heading, a material number, a pack
line, and one or two money figures — so everything here guards the translation
from that prose into a row a pipeline can trust:

* OUR price is taken and IDEXX's list price is not, which on a row showing both
  is the difference between what we pay and what we don't;
* "Free item" is a price of ZERO, not a missing price, because an item IDEXX
  supplies with an analyser contract is stock we hold and count;
* the pack count comes from the page's own words, so a $4,766 box of 12 is
  never mistaken for a $4,766 test;
* an unchanged catalogue produces identical bytes, so re-reading submits nothing;
* and a half-finished walk is not allowed to look like a mass delisting.
"""

from __future__ import annotations

import csv
import os
import tempfile
from decimal import Decimal
from pathlib import Path

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/idexx.db")

import pytest  # noqa: E402

from services import idexx_connector as ix  # noqa: E402

SNAPSHOT = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "idexx" / "idexx_hk_snapshot.csv"

# A real block, as the portal renders one. Two figures: ours carries the
# asterisk, IDEXX's list price does not.
PRICED_BLOCK = """Catalyst Chem 18 Clip
Product: 99-0013506
12 tests per item
HKD 5,299.00
HKD 4,766.00 *
Add to cart"""

FREE_BLOCK = """SNAP Pro Analyser Printer Paper
Product: 98-0001234
5 rolls per item
Free item
Add to cart"""


def _rows() -> list[dict[str, str]]:
    with SNAPSHOT.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def test_our_price_is_taken_and_the_list_price_is_not():
    """The asterisk marks what this account pays. Reading the other figure
    would inflate every affected cost by whatever discount we negotiated."""
    row = ix.parse_block(PRICED_BLOCK, category="Analysers", url="https://order.idexx.com/x")

    assert row["price_hkd"] == "4766.00"
    assert "5299" not in str(row)


def test_a_free_item_is_priced_zero_rather_than_left_blank():
    """IDEXX has stated this price. Leaving it blank would send someone to
    chase a supplier who already answered."""
    row = ix.parse_block(FREE_BLOCK, category="Consumables", url="https://order.idexx.com/y")

    assert row["price_hkd"] == "0.00"
    assert row["is_free_item"] == "TRUE"


def test_the_pack_count_comes_from_the_page_not_a_guess():
    """'12 tests per item' is the whole reason a $4,766 line is not a $4,766
    test. Both halves are carried: the count to divide by, the noun to show."""
    row = ix.parse_block(PRICED_BLOCK)

    assert row["units_per_item"] == "12"
    assert row["pack_noun"] == "tests"
    assert row["pack_text"] == "12 tests per item"


def test_a_block_without_a_material_number_is_not_a_product():
    """Category blurbs and promotional panels render like products. Without a
    material number there is nothing to order, so there is nothing to ingest."""
    assert ix.parse_block("Shop our analyser range\nHKD 100.00 *") is None


def test_an_unchanged_catalogue_produces_identical_bytes():
    """The checksum is what stops a re-read becoming a new document; it can
    only do that if the ordering and formatting are ours, not the portal's."""
    blocks = [
        (FREE_BLOCK, "Consumables", ""),
        (PRICED_BLOCK, "Analysers", ""),
    ]
    first = ix.build_snapshot(blocks, captured_on="2026-09-02", pages_read=2)
    second = ix.build_snapshot(list(reversed(blocks)), captured_on="2026-09-02", pages_read=2)

    assert first.checksum == second.checksum
    assert first.csv_bytes == second.csv_bytes


def test_the_same_material_listed_twice_is_one_row():
    """A product filed under two categories is one thing to order. Two rows
    would double it on the desk and race to overwrite each other's cost."""
    twice = [
        (PRICED_BLOCK, "Analysers", ""),
        (PRICED_BLOCK, "Chemistry", ""),
    ]
    snapshot = ix.build_snapshot(twice, captured_on="2026-09-02", pages_read=2)

    assert snapshot.row_count == 1


def test_a_read_that_found_nothing_is_an_error_not_an_empty_catalogue():
    """An empty catalogue would delist everything IDEXX sells. A portal that
    returned no products has failed, and must say so."""
    with pytest.raises(ix.IdexxConnectorError):
        ix.build_snapshot([], captured_on="2026-09-02", pages_read=0)


def test_a_short_read_is_refused_because_it_looks_like_a_delisting():
    """A walk that dies halfway and a supplier who dropped half their range
    are indistinguishable downstream. Only a person can tell them apart."""
    assert ix.assess_completeness(current_rows=105, previous_rows=105).trustworthy
    assert ix.assess_completeness(current_rows=100, previous_rows=105).trustworthy
    assert not ix.assess_completeness(current_rows=40, previous_rows=105).trustworthy
    assert ix.assess_completeness(current_rows=40, previous_rows=None).trustworthy


def test_the_unit_price_divides_by_what_the_pack_holds():
    """$4,766 for a box of 12 is $397.17 a test. This is the arithmetic every
    margin downstream rests on."""
    assert ix.unit_price(ix.parse_block(PRICED_BLOCK)).quantize(Decimal("0.01")) == Decimal("397.17")
    assert ix.unit_price(ix.parse_block(FREE_BLOCK)) == Decimal("0.00")


# --- the captured snapshot itself -------------------------------------------


def test_the_captured_snapshot_declares_the_columns_the_contract_reads():
    """The contract names source columns. A rename here silently empties a
    field rather than failing, so the two are pinned together."""
    with SNAPSHOT.open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))

    assert tuple(header) == ix.SNAPSHOT_COLUMNS


def test_every_captured_row_can_be_ordered_and_priced():
    """A material number and a price on every row — the two things without
    which a row cannot become a purchase."""
    rows = _rows()

    assert len(rows) >= 100
    assert all(row["material"].strip() for row in rows)
    assert all(ix.unit_price(row) is not None for row in rows)


def test_the_captured_prices_match_what_bizops_recorded():
    """Six IDEXX rows on the BizOps golden sheet, priced by hand from real
    invoices. The portal agreed with all six to the cent — this pins that."""
    priced = {row["material"]: row for row in _rows()}
    golden = {
        "99-0013506": ("4766.00", "12"),   # Catalyst Chem 18 Profile
        "98-11003-02": ("3822.00", "12"),  # Catalyst Chem 17 CLIP
        "98-11009-02": ("836.00", "12"),   # Catalyst Lyte 4 CLIP
        "99-0009442": ("1371.00", "15"),   # SNAP Feline Triple Test
        "99-0001174": ("1966.00", "10"),   # SNAP Feline proBNP Test
        "99-26306-00": ("3372.00", "1"),   # ProCyte Dx Reagent Kit
    }
    for material, (price, units) in golden.items():
        assert priced[material]["price_hkd"] == price, material
        assert priced[material]["units_per_item"] == units, material


def test_a_browser_failure_never_carries_the_password_out():
    """This has already happened once: playwright reports a failed action by
    quoting its arguments, so a timeout on the password step put the real
    password into a traceback. Nothing derived from a browser exception may
    leave this module unfiltered."""
    leak = RuntimeError('Timeout 30000ms exceeded.\ncalling fill("Sup3rSecret#") on #password')

    message = str(ix.redacted(leak, "Sup3rSecret#", "vet@example.com"))

    assert "Sup3rSecret#" not in message
    assert "[redacted]" in message
    assert "Timeout 30000ms exceeded" in message, "the useful half must survive"

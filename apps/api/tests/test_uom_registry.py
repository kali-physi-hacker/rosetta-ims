"""Every spelling the catalogue actually holds, and what it resolves to.

The list below is not invented: it is every distinct value across
price_basis_uom_code, purchase_uom_code, sellable_unit_uom_code, content_uom_code,
products.uom and selling_items.sell_uom, read from production on 17 Sep 2026.
Eighty-six spellings of thirty-nine units, plus five strings that are not units
at all. A vocabulary that cannot name what is already recorded is not a
vocabulary, so this file is the check that it can.
"""

from __future__ import annotations

import pytest

from services import uom_vocabulary as uom

#: (spelling, canonical code or None) — the whole production vocabulary.
PRODUCTION_SPELLINGS = [
    ("UNIT", "UNIT"), ("Unit(s)", "UNIT"), ("unit(s)", "UNIT"), ("UNIT(S)", "UNIT"),
    ("Units", "UNIT"),
    ("PCS", "PIECE"), ("pc", "PIECE"), ("piece", "PIECE"), ("PIECE", "PIECE"),
    ("each", "PIECE"),
    ("Can(s)", "CAN"), ("can", "CAN"), ("CAN(S)", "CAN"), ("can(s)", "CAN"),
    ("Bag(s)", "BAG"), ("BAG(S)", "BAG"), ("bag", "BAG"), ("Bag", "BAG"),
    ("bag(s)", "BAG"),
    ("Box(es)", "BOX"), ("BOX(ES)", "BOX"), ("BOX", "BOX"), ("box", "BOX"),
    ("Bottle(s)", "BOTTLE"), ("bottle", "BOTTLE"), ("BOTTLE", "BOTTLE"),
    ("BOTTLE(S)", "BOTTLE"),
    ("Tablet(s)", "TABLET"), ("tablet", "TABLET"), ("TABLET", "TABLET"),
    ("TABLET(S)", "TABLET"),
    ("Pack(s)", "PACK"), ("pack", "PACK"), ("PACK", "PACK"), ("PACK(S)", "PACK"),
    ("Package(s)", "PACK"),
    ("Packet(s)", "PACKET"), ("packet", "PACKET"),
    ("Pouch(es)", "POUCH"), ("POUCH(ES)", "POUCH"), ("pouch", "POUCH"),
    ("pouch(es)", "POUCH"),
    ("Capsule(s)", "CAPSULE"), ("capsule", "CAPSULE"), ("CAPSULE(S)", "CAPSULE"),
    ("CAPSULE", "CAPSULE"),
    ("Tube(s)", "TUBE"), ("TUBE(S)", "TUBE"), ("TUBE", "TUBE"), ("tube", "TUBE"),
    ("Vial(s)", "VIAL"), ("Collar(s)", "COLLAR"), ("Syringe(s)", "SYRINGE"),
    ("syringe", "SYRINGE"), ("Pad(s)", "PAD"), ("CASE", "CASE"),
    ("set", "SET"), ("pipette", "PIPETTE"), ("jar", "JAR"), ("pot", "POT"),
    ("sachet", "SACHET"), ("Pen(s)", "PEN"), ("chew", "CHEW"), ("Roll(s)", "ROLL"),
    ("wipe", "WIPE"), ("tub", "TUB"), ("Dose(s)", "DOSE"), ("dose", "DOSE"),
    ("mL", "ML"), ("ml", "ML"), ("ML", "ML"),
    ("lb", "LB"), ("LB", "LB"), ("kg", "KG"), ("KG", "KG"),
    ("g", "G"), ("G", "G"), ("OZ", "OZ"), ("oz", "OZ"), ("gallon", "GALLON"),
    # Not units. These are what free text bought us.
    ("#N/A", None), ("", None), ("powder", None), ("10", None),
    ("1 Set 2 Pcs", None), (None, None), ("   ", None),
]


@pytest.mark.parametrize("spelling,expected", PRODUCTION_SPELLINGS)
def test_every_spelling_production_holds_resolves(spelling, expected):
    assert uom.normalise(spelling) == expected


def test_a_placeholder_never_resolves_to_a_unit():
    """`#N/A` on a thousand products is a spreadsheet error, not a sell unit.

    And it must not become OTHER either: a purchase unit that is wrong silently
    rebases every per-unit cost derived from it, so an unknown word is left for
    a human rather than mapped to something close.
    """
    for junk in ("#N/A", "n/a", "NA", "NaN", "null", "-", "—", ""):
        assert uom.normalise(junk) is None
        assert uom.canonical_for(junk) is None


def test_a_word_we_do_not_know_stays_unknown():
    assert uom.normalise("reel") is None
    assert uom.normalise("blister") is None


def test_display_spelling_is_one_per_unit():
    assert uom.canonical_for("BOX") == "Box(es)"
    assert uom.canonical_for("box") == "Box(es)"
    assert uom.canonical_for("BOX(ES)") == "Box(es)"
    assert uom.canonical_for("Case(s)") == "Case(s)"


def test_dimension_separates_counting_from_measuring():
    assert uom.dimension("Can(s)") == uom.COUNT
    assert uom.dimension("mL") == uom.VOLUME
    assert uom.dimension("kg") == uom.MASS
    assert uom.dimension("#N/A") is None


def test_a_measure_converts_within_its_dimension():
    assert uom.convert(30, "mL", "L") == pytest.approx(0.03)
    assert uom.convert(1, "kg", "g") == pytest.approx(1000)
    assert uom.convert(1, "lb", "g") == pytest.approx(453.59237)
    assert uom.convert(2, "mL", "mL") == pytest.approx(2)


def test_a_count_converts_to_nothing():
    """Twelve cans are a case for one product and a dozen loose cans for the
    next. No factor relates them, so none is invented — only a pack's stated
    content can bridge a count and a measure, and that is a fact about one
    product rather than about the words.
    """
    assert uom.convert(12, "Can(s)", "Case(s)") is None
    assert uom.convert(30, "mL", "Bottle(s)") is None
    assert uom.convert(1, "kg", "mL") is None
    assert uom.convert(1, "Box(es)", "reel") is None


def test_weight_helpers_round_trip_through_grams():
    assert uom.weight_to_grams(4, "lb") == pytest.approx(1814.36948)
    assert uom.weight_in(1814.36948, "lb") == pytest.approx(4)
    # An unstated unit means the number is already grams.
    assert uom.weight_to_grams(500, None) == 500
    assert uom.weight_in(500, "#N/A") == 500


def test_nothing_offered_for_selection_would_be_refused():
    """The import-time assertion, restated as a test so it is visible."""
    assert all(uom.fold(u) in uom.BASE_UOMS for u in uom.CANONICAL_UOMS)
    assert len(uom.CANONICAL_UOMS) == len(set(uom.CANONICAL_UOMS))


def test_every_unit_has_a_home_in_the_contract_enum():
    """The pipeline rebuilds UnitOfMeasure(code=<db string>) on read and raises
    on anything the enum does not know, so a unit this module can write and the
    contract cannot name is a crash waiting for the row that uses it.
    """
    from schemas.catalogue_pipeline.enums import UnitCode

    names = {u.value for u in UnitCode}
    missing = sorted(u.code for u in uom.UNITS if u.code not in names)
    assert not missing, f"units with no UnitCode member: {missing}"


def test_the_two_vocabularies_agree_on_spelling():
    """Every code the contract knows should also be a code we can spell, except
    the two that deliberately name an absence.
    """
    from schemas.catalogue_pipeline.enums import UnitCode

    unspellable = sorted(
        u.value for u in UnitCode
        if u.value not in {"OTHER", "UNKNOWN"} and uom.canonical_for(u.value) is None
    )
    assert not unspellable, f"UnitCode members with no spelling: {unspellable}"

"""The units we accept, and the one spelling each of them gets.

Two consumers need this and must not disagree: the channel sale-terms CSV,
which validates what a round trip may carry, and the SKU upload workbook,
whose dropdowns decide what anyone can type in the first place. Keeping the
vocabulary here rather than in either of them is what stops a unit being
offerable in one place and rejected in the other.

BASE_UOMS is folded for comparison — a channel may charge in a measure ("mL")
or in the item itself ("Can(s)"), and both are legitimate, so matching is
case- and space-insensitive and this only catches typos.

CANONICAL_UOMS is the display form: exactly one spelling per unit, which is
the half that was missing. The catalogue currently holds sixty-one spellings
of about twenty units — `Unit(s)` beside `unit(s)`, `Can(s)` beside `can`,
`mL` beside `ml` — and, worst of all, `#N/A` on 1,187 products, a spreadsheet
error string that became the single most common sell unit we have. Free text
bought that. A fixed list is the fix.
"""

from __future__ import annotations

from decimal import Decimal

#: What we accept, folded. Whatever is ALREADY recorded is added to this at
#: run time by `known_uoms` — an export must import back unchanged, and
#: rejecting a value the system itself wrote would make the round trip a lie.
BASE_UOMS = {
    "ml", "l", "g", "kg", "mg", "unit(s)", "units", "unit", "pcs", "pc",
    "bag(s)", "box(es)", "bottle(s)", "can(s)", "pouch(es)", "tablet(s)",
    "capsule(s)", "vial(s)", "tube(s)", "sachet(s)", "pack(s)", "syringe(s)",
    "collar(s)", "pipette(s)", "packet(s)", "pad(s)", "dose(s)", "strip(s)",
    # Purchase packaging. A supplier sells by the case or the carton far more
    # often than by the unit, and the upload workbook asks what one purchase
    # unit is called — a question its own dropdown could not answer.
    "case(s)", "carton(s)",
}

#: One spelling per unit, for anywhere a human is choosing rather than typing.
CANONICAL_UOMS = [
    "mL", "L", "g", "kg", "mg",
    "Unit(s)", "Pcs", "Bag(s)", "Box(es)", "Bottle(s)", "Can(s)", "Pouch(es)",
    "Tablet(s)", "Capsule(s)", "Vial(s)", "Tube(s)", "Sachet(s)", "Pack(s)",
    "Syringe(s)", "Collar(s)", "Pipette(s)", "Packet(s)", "Pad(s)", "Dose(s)",
    "Strip(s)", "Case(s)", "Carton(s)",
]


def fold(value: str) -> str:
    """Lower-case and collapse whitespace, for comparison only."""
    return " ".join(str(value or "").strip().lower().split())


def canonical_for(code) -> str | None:
    """The display spelling for a UOM code the pipeline stores — BOX, CASE, TABLET.

    Returns None when we hold no spelling for one. That is a real answer, not a
    failure: the sheet then shows the code itself and its off-list rule paints
    it, which sends someone to add the unit rather than letting a name nobody
    chose look official.
    """
    folded = fold(code)
    if not folded:
        return None
    for candidate in CANONICAL_UOMS:
        spelling = fold(candidate)
        if spelling in (folded, f"{folded}(s)", f"{folded}(es)"):
            return candidate
    return None


# ── weight ──────────────────────────────────────────────────────────────────
# Weight is stored canonically in grams and nothing else reads it, so the two
# functions below are the whole boundary between that and the figure a supplier
# actually printed. They live together because they are inverses: a sheet that
# shows 4 lb and an importer that reads 4 lb have to agree on what a pound is,
# and the way they stop agreeing is by each keeping their own table.

#: Grams in one of each unit anyone may state a weight in.
GRAMS_PER = {
    "g": Decimal(1),
    "kg": Decimal(1000),
    "lb": Decimal("453.59237"),
    "oz": Decimal("28.349523"),
}

#: Offered wherever a weight is entered. Grams first because that is what an
#: unstated unit means — the number is already canonical.
WEIGHT_UNITS = ["g", "kg", "lb", "oz"]


def weight_in(grams, unit) -> float | None:
    """`grams` as the figure a supplier would recognise, in `unit`.

    A missing or unrecognised unit means the number is already grams, which is
    how 5,543 products with no stated unit are recorded.
    """
    if grams in (None, ""):
        return None
    per = GRAMS_PER.get(fold(unit))
    if per is None:
        return float(grams)
    return float((Decimal(str(grams)) / per).quantize(Decimal("0.001")).normalize())


def weight_to_grams(value, unit) -> float | None:
    """The inverse: a printed figure and its unit, as canonical grams."""
    if value in (None, ""):
        return None
    return float(Decimal(str(value)) * GRAMS_PER.get(fold(unit), Decimal(1)))


# The guarantee that makes one vocabulary out of two: nothing may be offered
# for selection that the validator would then refuse. Checked at import so a
# careless addition fails loudly rather than at someone's keyboard.
_unofferable = sorted(u for u in CANONICAL_UOMS if fold(u) not in BASE_UOMS)
assert not _unofferable, (
    f"CANONICAL_UOMS offers units BASE_UOMS rejects: {_unofferable}"
)

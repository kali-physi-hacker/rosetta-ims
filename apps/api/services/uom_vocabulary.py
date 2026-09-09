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


# The guarantee that makes one vocabulary out of two: nothing may be offered
# for selection that the validator would then refuse. Checked at import so a
# careless addition fails loudly rather than at someone's keyboard.
_unofferable = sorted(u for u in CANONICAL_UOMS if fold(u) not in BASE_UOMS)
assert not _unofferable, (
    f"CANONICAL_UOMS offers units BASE_UOMS rejects: {_unofferable}"
)

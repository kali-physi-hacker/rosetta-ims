"""The units we accept, the one spelling each of them gets, and what they are.

Three consumers need this and must not disagree: the channel sale-terms CSV,
which validates what a round trip may carry; the SKU upload workbook, whose
dropdowns decide what anyone can type in the first place; and the cost
arithmetic, which has to know whether two units are even the same KIND of thing
before it can convert between them. Keeping the vocabulary here rather than in
any of them is what stops a unit being offerable in one place and rejected in
the next.

The catalogue holds eighty-six spellings of about forty units — `Unit(s)`
beside `unit(s)`, `Can(s)` beside `can`, `mL` beside `ml` — and, worst of all,
`#N/A` on over a thousand products, a spreadsheet error string that became one
of the most common sell units we have. Free text bought that. A fixed list is
the fix.

`UNITS` is that list. Each entry carries the canonical code the pipeline stores
(a `UnitCode` member), the spelling a person should see, and its DIMENSION —
whether it counts things, weighs them, or measures volume. The dimension is
what makes a real conversion possible and an impossible one refusable: 30 mL
converts to 0.03 L because both measure volume, and never to Bottle(s), because
no factor relates a measure to a count. Only the pack's stated content can
bridge those two, and that is a fact about one product, not about the words.

Deliberately NOT here: any notion of which units "contain" others. A BOTTLE
holds sixty tablets in one product and is the thing sold in the next, so a
container flag would be a guess dressed as a property — which is exactly the
mistake that made a $354 case publish as the cost of one can. What a price buys
is recorded on the price.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

COUNT = "COUNT"
MASS = "MASS"
VOLUME = "VOLUME"


@dataclass(frozen=True)
class Unit:
    """One real unit, and every way the catalogue spells it."""

    code: str
    one: str
    many: str
    dimension: str
    #: How many of the dimension's base unit (g for MASS, mL for VOLUME) one of
    #: these is. None for anything counted — two cans do not convert to a bag.
    base_factor: Decimal | None = None
    #: Spellings the generated ones do not cover: abbreviations, and words that
    #: mean the same unit ("each" is a piece).
    also: tuple[str, ...] = ()


#: Ordered for the workbook dropdown: measures first, then what you can count.
UNITS: tuple[Unit, ...] = (
    Unit("ML", "mL", "mL", VOLUME, Decimal(1), ("millilitre", "millilitres", "cc")),
    Unit("L", "L", "L", VOLUME, Decimal(1000), ("litre", "litres", "liter", "liters")),
    Unit("GALLON", "gallon", "gallon(s)", VOLUME, Decimal("3785.411784"), ("gal",)),
    Unit("MG", "mg", "mg", MASS, Decimal("0.001"), ("milligram", "milligrams")),
    Unit("G", "g", "g", MASS, Decimal(1), ("gram", "grams", "gm")),
    Unit("KG", "kg", "kg", MASS, Decimal(1000), ("kilo", "kilos", "kilogram", "kilograms")),
    Unit("LB", "lb", "lb", MASS, Decimal("453.59237"), ("lbs", "pound", "pounds")),
    Unit("OZ", "oz", "oz", MASS, Decimal("28.349523"), ("ounce", "ounces")),

    Unit("UNIT", "Unit", "Unit(s)", COUNT),
    Unit("PIECE", "Piece", "Piece(s)", COUNT, None, ("pcs", "pc", "each", "ea")),
    Unit("PACK", "Pack", "Pack(s)", COUNT, None, ("package", "packages", "pkt")),
    Unit("PACKET", "Packet", "Packet(s)", COUNT),
    Unit("BOX", "Box", "Box(es)", COUNT),
    Unit("CASE", "Case", "Case(s)", COUNT),
    Unit("CARTON", "Carton", "Carton(s)", COUNT, None, ("ctn",)),
    Unit("BOTTLE", "Bottle", "Bottle(s)", COUNT, None, ("bot", "bots")),
    Unit("BAG", "Bag", "Bag(s)", COUNT),
    Unit("CAN", "Can", "Can(s)", COUNT),
    Unit("POUCH", "Pouch", "Pouch(es)", COUNT),
    Unit("SACHET", "Sachet", "Sachet(s)", COUNT),
    Unit("TABLET", "Tablet", "Tablet(s)", COUNT, None, ("tab", "tabs")),
    Unit("CAPSULE", "Capsule", "Capsule(s)", COUNT, None, ("cap", "caps")),
    Unit("VIAL", "Vial", "Vial(s)", COUNT),
    Unit("TUBE", "Tube", "Tube(s)", COUNT),
    Unit("TUB", "Tub", "Tub(s)", COUNT),
    Unit("JAR", "Jar", "Jar(s)", COUNT),
    Unit("POT", "Pot", "Pot(s)", COUNT),
    Unit("TEST", "Test", "Test(s)", COUNT),
    Unit("STRIP", "Strip", "Strip(s)", COUNT),
    Unit("SYRINGE", "Syringe", "Syringe(s)", COUNT),
    Unit("PIPETTE", "Pipette", "Pipette(s)", COUNT),
    Unit("COLLAR", "Collar", "Collar(s)", COUNT),
    Unit("PAD", "Pad", "Pad(s)", COUNT),
    Unit("WIPE", "Wipe", "Wipe(s)", COUNT),
    Unit("CHEW", "Chew", "Chew(s)", COUNT),
    Unit("DOSE", "Dose", "Dose(s)", COUNT),
    Unit("PEN", "Pen", "Pen(s)", COUNT),
    Unit("ROLL", "Roll", "Roll(s)", COUNT),
    Unit("SET", "Set", "Set(s)", COUNT),
)

_BY_CODE = {u.code: u for u in UNITS}

#: Text that names no unit. Eight copies of this set had drifted apart across
#: the codebase — one was missing a bare "-", another "nan" — so a value the
#: importer dropped could survive in the exporter. One list, one meaning.
PLACEHOLDERS = frozenset({
    "", "-", "—", "#n/a", "n/a", "na", "nan", "none", "null", "#ref!", "#value!",
})


def fold(value: str) -> str:
    """Lower-case and collapse whitespace, for comparison only."""
    return " ".join(str(value or "").strip().lower().split())


def is_placeholder(value) -> bool:
    """True when the text names nothing: spreadsheet debris, not a value.

    Eight copies of this question lived in the codebase and had drifted apart —
    one set omitted a bare "-", another "nan" — so a value one module discarded
    the next one kept and wrote down. There is one answer now.
    """
    return fold(value) in PLACEHOLDERS


def _aliases() -> dict[str, str]:
    """Every folded spelling that resolves, to the code it resolves to.

    Generated rather than listed: a unit's code, its two spellings and the
    obvious plurals are all the same unit, and writing them out by hand is how
    one gets forgotten.
    """
    out: dict[str, str] = {}
    for unit in UNITS:
        forms = {unit.code, unit.one, unit.many, *unit.also}
        for word in (unit.one, *unit.also):
            forms |= {f"{word}s", f"{word}es", f"{word}(s)", f"{word}(es)"}
        for form in forms:
            key = fold(form)
            if key and key not in PLACEHOLDERS:
                out.setdefault(key, unit.code)
    return out


ALIASES = _aliases()

#: What we accept, folded. Whatever is ALREADY recorded is added to this at run
#: time by `known_uoms` — an export must import back unchanged, and rejecting a
#: value the system itself wrote would make the round trip a lie.
BASE_UOMS = frozenset(ALIASES)

#: One spelling per unit, for anywhere a human is choosing rather than typing.
CANONICAL_UOMS = [u.many for u in UNITS]


def normalise(value) -> str | None:
    """The canonical code for any spelling of a unit, or None.

    None is a real answer, not a failure. `#N/A` is not a unit, and neither is
    a word we have never seen — and the one thing that must not happen is
    resolving it to something close, because a purchase unit that is wrong
    silently rebases every per-unit cost derived from it.
    """
    folded = fold(value)
    if not folded or folded in PLACEHOLDERS:
        return None
    return ALIASES.get(folded)


def canonical_for(code) -> str | None:
    """The display spelling for a unit — BOX, CASE, TABLET, or any spelling.

    Returns None when we hold no spelling for one. The sheet then shows the code
    itself and its off-list rule paints it, which sends someone to add the unit
    rather than letting a name nobody chose look official.
    """
    resolved = normalise(code)
    return _BY_CODE[resolved].many if resolved else None


def dimension(code) -> str | None:
    """COUNT, MASS, VOLUME — or None for a unit we do not know.

    The question every conversion has to ask first. Two units convert only
    within a dimension; across one, nothing but a stated pack content relates
    them, and guessing a relationship is how a margin gets invented.
    """
    resolved = normalise(code)
    return _BY_CODE[resolved].dimension if resolved else None


def convert(amount, frm, to) -> float | None:
    """`amount` restated from one unit into another, or None if it cannot be.

    Cannot be, specifically: either unit unknown, the two measure different
    kinds of thing, or the dimension is COUNT — where a factor between units
    does not exist in general. Twelve cans are a case for one product and a
    dozen loose cans for the next.
    """
    if amount in (None, ""):
        return None
    source, target = normalise(frm), normalise(to)
    if source is None or target is None or source == target:
        return float(amount) if source is not None and source == target else None
    a, b = _BY_CODE[source], _BY_CODE[target]
    if a.dimension != b.dimension or a.base_factor is None or b.base_factor is None:
        return None
    return float(Decimal(str(amount)) * a.base_factor / b.base_factor)


# ── weight ──────────────────────────────────────────────────────────────────
# Weight is stored canonically in grams and nothing else reads it, so the two
# functions below are the whole boundary between that and the figure a supplier
# actually printed. They live together because they are inverses: a sheet that
# shows 4 lb and an importer that reads 4 lb have to agree on what a pound is,
# and the way they stop agreeing is by each keeping their own table. That table
# is now UNITS, so there is only one.

#: Grams in one of each unit anyone may state a weight in.
GRAMS_PER = {u.one.lower(): u.base_factor for u in UNITS if u.dimension == MASS}

#: Offered wherever a weight is entered. Grams first because that is what an
#: unstated unit means — the number is already canonical.
WEIGHT_UNITS = ["g", "kg", "lb", "oz"]


def weight_in(grams, unit) -> float | None:
    """`grams` as the figure a supplier would recognise, in `unit`.

    A missing or unrecognised unit means the number is already grams, which is
    how thousands of products with no stated unit are recorded.
    """
    if grams in (None, ""):
        return None
    code = normalise(unit)
    per = _BY_CODE[code].base_factor if code and _BY_CODE[code].dimension == MASS else None
    if per is None:
        return float(grams)
    return float((Decimal(str(grams)) / per).quantize(Decimal("0.001")).normalize())


def weight_to_grams(value, unit) -> float | None:
    """The inverse: a printed figure and its unit, as canonical grams."""
    if value in (None, ""):
        return None
    code = normalise(unit)
    per = _BY_CODE[code].base_factor if code and _BY_CODE[code].dimension == MASS else None
    return float(Decimal(str(value)) * (per if per is not None else Decimal(1)))


# The guarantee that makes one vocabulary out of several: nothing may be offered
# for selection that the validator would then refuse, and no two units may claim
# the same spelling. Checked at import so a careless addition fails loudly
# rather than at someone's keyboard.
_unofferable = sorted(u for u in CANONICAL_UOMS if fold(u) not in BASE_UOMS)
assert not _unofferable, f"CANONICAL_UOMS offers units BASE_UOMS rejects: {_unofferable}"

_dupes = sorted({u.code for u in UNITS} ^ set(_BY_CODE))
assert not _dupes, f"two units share a code: {_dupes}"
assert len(_BY_CODE) == len(UNITS), "two units share a code"

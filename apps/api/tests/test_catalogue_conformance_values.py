"""The two small readers the golden replays lean on, pinned at unit level.

``_decimal_value``: a price with one printed remark parses; a relative tier, a
packaging note and a phone number never do; and "HK$" is one currency token,
not "HK" plus "$" — stripping them in the wrong order reads "HK$130" as no
price at all.

``_sellable_unit_from_text``: countables only. A measure names the content of
the unit, not the unit, and refusing it is the design — the docstring's old
headline example ('30ml/ bot' -> ML) claimed the opposite of what the function
does on purpose.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

from decimal import Decimal  # noqa: E402

import pytest  # noqa: E402

from services.catalogue_conformance import _decimal_value, _sellable_unit_from_text  # noqa: E402


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("130.0 (Price Reduced)", Decimal("130.0")),
        ("$130.00 (net)", Decimal("130.00")),
        ("HK$130.00", Decimal("130.00")),
        ("HK$1,300.50 (promo)", Decimal("1300.50")),
        ("HKD 45", Decimal("45")),
        ("  45 (x) ", Decimal("45")),
    ],
)
def test_prices_that_must_parse(raw, expected):
    assert _decimal_value(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "10% discount",  # a relative tier is not an amount
        "1 box (12 pcs)",  # packaging, not a price
        "(852) 2982 2775",  # a phone number
        "130.00 (a) (b)",  # one note is a remark; two is a sentence
        "Price Reduced (130.0)",  # the head must be the amount
        "130 ()",
        "by quote",
        "",
        None,
    ],
)
def test_values_that_must_refuse(raw):
    assert _decimal_value(raw) is None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("100 tabs/ box", "TABLET"),
        ("10 tab", "TABLET"),
        ("2 pcs/ pack", "PIECE"),
        ("100 Tests/ Kit", "TEST"),
        ("1 strip", "STRIP"),
        ("pouch", "POUCH"),
        ("30ml/ bot", None),  # a measure is content, not a sellable unit
        ("100ml/ bot", None),
        ("set/ box", None),  # the enum has no honest home for 'set'
    ],
)
def test_sellable_units_are_countables_only(raw, expected):
    assert _sellable_unit_from_text(raw) == expected

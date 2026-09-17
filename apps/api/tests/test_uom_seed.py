"""The vocabulary, in a table someone can edit.

The module is what the code reads; this table is the same list made editable so
a unit can be added without a deploy. Two properties matter and neither is
obvious: running it twice must change nothing, and it must never overwrite a
spelling a person has corrected — the seed says a unit exists, not what it is
called forever after.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/uom_seed.db")

import database  # noqa: E402
import models  # noqa: E402
from services import uom_vocabulary as vocab  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


def _counts():
    with database.engine.connect() as conn:
        from sqlalchemy import text
        return (conn.execute(text("SELECT COUNT(*) FROM uoms")).scalar(),
                conn.execute(text("SELECT COUNT(*) FROM uom_aliases")).scalar())


def test_the_seed_puts_every_unit_and_spelling_in_the_table():
    database.seed_uoms(database.engine)
    units, aliases = _counts()
    assert units == len(vocab.UNITS)
    assert aliases == len(vocab.ALIASES)


def test_running_it_again_changes_nothing():
    database.seed_uoms(database.engine)
    first = _counts()
    database.seed_uoms(database.engine)
    assert _counts() == first


def test_a_hand_corrected_spelling_survives_the_next_boot():
    """The category seed makes the same promise, for the same reason: whoever
    tunes a value owns it, and a boot is not a decision to revert them.
    """
    from sqlalchemy import text

    database.seed_uoms(database.engine)
    with database.engine.connect() as conn:
        conn.execute(text("UPDATE uoms SET label_many = 'Cans' WHERE code = 'CAN'"))
        conn.commit()

    database.seed_uoms(database.engine)

    with database.engine.connect() as conn:
        kept = conn.execute(text("SELECT label_many FROM uoms WHERE code = 'CAN'")).scalar()
    assert kept == "Cans"


def test_the_table_and_the_module_hold_the_same_vocabulary():
    from sqlalchemy import text

    database.seed_uoms(database.engine)
    with database.engine.connect() as conn:
        rows = dict(conn.execute(text("SELECT normalized, uom_code FROM uom_aliases")).all())
    assert rows == vocab.ALIASES

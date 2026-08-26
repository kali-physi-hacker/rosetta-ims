"""Placeholder junk must never become a live spreadsheet error.

Legacy data holds the TEXT "#N/A" as a unit ("selling_items.sell_uom",
"products.uom"). Pushed with USER_ENTERED, that text parses into a real #N/A
error value, and every margin formula referencing the cell collapses to #N/A —
the sheet the user saw had 50 such cells from six poisoned unit cells. The
export states no unit instead, the writer escapes any error literal that
still arrives, and the formulas read a hand-typed error as "not stated".
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/ops_sheet.db")

from services import ops_db_export  # noqa: E402
from services.ops_db_sheet import _cell, _formula  # noqa: E402


def test_error_literal_text_is_escaped_to_stay_text():
    assert _cell("#N/A") == "'#N/A"
    assert _cell("#REF!") == "'#REF!"
    # Real values keep their behavior: numbers numeric, codes textual.
    assert _cell("13.1") == 13.1
    assert _cell("0330480090") == "0330480090"


def test_placeholder_channel_uom_is_not_printed():
    row = {"cost_per_unit": "10", "sellable_uom": "CAN", "purchase_uom": "CAN",
           "content_amount": "", "content_uom": ""}
    for n in range(1, ops_db_export.SLOTS + 1):
        row[f"mbb_tier_{n}_cost_per_unit"] = ""
    channel_data = {(7, "shopify"): {"price": None, "fee": None, "units_per_listing": None, "uom": "#N/A"}}
    filled = ops_db_export._fill_channels(dict(row), 7, channel_data)
    assert filled["selling_price_shopify_uom"] == ""
    # A stated unit keeps its printed spelling.
    channel_data[(7, "shopify")]["uom"] = "Can(s)"
    filled = ops_db_export._fill_channels(dict(row), 7, channel_data)
    assert filled["selling_price_shopify_uom"] == "Can(s)"


def test_margin_formulas_survive_an_error_valued_unit_cell():
    # A hand-typed error in a unit cell must read as "not stated", never
    # cascade: the comparisons are wrapped so the formula still yields a number.
    assert "IFERROR(AND(UPPER(" in _formula("shopify_gross_margin", 2, {})
    assert "IFERROR(UPPER(" in _formula("cost_per_unit", 2, {})

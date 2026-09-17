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

from decimal import Decimal  # noqa: E402

from services import ops_db_export  # noqa: E402
from services.ops_db_sheet import _a1, _cell, _formula  # noqa: E402


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


def test_a_margin_formula_cannot_be_poisoned_by_a_unit_cell():
    """It no longer reads one. The exporter restates the price and the formula
    reads that column, so there is nothing for an error-valued unit cell to
    cascade into — a stronger guarantee than wrapping the comparison was.
    """
    margin = _formula("shopify_gross_margin", 2, {})
    assert f"{_a1('selling_price_shopify_per_unit')}2" in margin
    assert f"{_a1('selling_price_shopify_uom')}2" not in margin
    assert f"{_a1('content_amount')}2" not in margin
    assert "UPPER(" not in margin
    # The cost still compares units, so it still wraps those comparisons.
    assert "IFERROR(UPPER(" in _formula("cost_per_unit", 2, {})


def test_the_sheet_takes_its_margin_from_the_restated_price():
    """HKTV lists a box of twelve against a cost per pouch. The sheet used to
    compare the whole box price to one pouch and publish 92.29% where the
    exporter had already worked out 7.49%.
    """
    row = {"cost_per_unit": "8.75", "sellable_uom": "POUCH", "purchase_uom": "BOX",
           "content_amount": "", "content_uom": ""}
    for n in range(1, ops_db_export.SLOTS + 1):
        row[f"mbb_tier_{n}_cost_per_unit"] = ""
    channel_data = {(7, "hktv"): {"price": Decimal("113.50"), "fee": None,
                                  "units_per_listing": None, "uom": "Box(es)",
                                  "uom_count": Decimal(12)}}
    filled = ops_db_export._fill_channels(dict(row), 7, channel_data)
    # listed price and the unit it comes to are both published, and they differ
    assert filled["selling_price_hktvm"] == "113.5"
    assert filled["selling_price_hktvm_per_unit"] == "9.4583"
    assert filled["hktvm_gross_margin"] == "7.49"

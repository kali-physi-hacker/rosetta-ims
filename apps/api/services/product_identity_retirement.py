"""Drop three `products` columns that never carried information.

Each of these was measured against the live table (11,160 products) before it
was condemned, and none of them holds a fact:

    products.hktv_cost          not one row has ever been populated
    products.daysmart_cost      1,055 rows, and every one of them is 0.0
    products.product_family_id  not one row, though the family model exists

A column whose every value is the same value is not data — it is a default
wearing a column's clothes. The two cost mirrors surfaced in exactly one
place, a pass-through dict in pricing_service, with no logic behind them.

products.daysmart_status was condemned with them and then reprieved, which is
the reason the guard below tests what it does. Every one of its values is
'active' too — but NULL versus set is what says whether a SKU is listed on
DaySmart at all, and the clinic channel filter reads it exactly that way. A
column can be uniform in its content and still carry its meaning in its
presence, so "one distinct value" is not sufficient grounds to drop anything.

There is deliberately no migration step here, unlike legacy_cost_retirement:
migration exists to stop a drop losing something, and these lose nothing. The
safety property is the measurement above, and it is re-checked at run time —
a column that turns out to hold a distinct value is left alone and reported,
because the measurement was taken on one database and this may be another.

Sibling columns that DO hold data — products.rrp, min_sellable_qty,
pack_unit, min_purchase_qty — are deliberately not here. Their newer homes
(product_suppliers.rrp, selling_items.order_multiple, the catalogue packaging
configuration) are all still empty, so dropping them would destroy the only
copy. They need migrate-then-drop, and a decision about which supplier or
channel inherits a value the product level never recorded.

One-shot: once every environment reports `already_retired`, this module and
its call in main.py can be deleted.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

#: (table, column, presence_mirror). A column qualifies when it holds no
#: distinct value AND its presence says nothing — either because it is empty,
#: or because `presence_mirror` is populated on exactly the same rows and
#: therefore already carries the flag. The mirror is verified at run time, not
#: taken on trust.
_DOOMED = [
    ("products", "hktv_cost", None),
    ("products", "daysmart_cost", "daysmart_status"),
    ("products", "product_family_id", None),
]


def retire_empty_product_columns(engine) -> dict:
    """Drop the condemned columns, skipping any that turn out to hold data."""

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    present = [
        (table, column, mirror)
        for table, column, mirror in _DOOMED
        if table in tables and column in {c["name"] for c in inspector.get_columns(table)}
    ]
    if not present:
        return {"status": "already_retired", "dropped": [], "kept": []}

    preparer = engine.dialect.identifier_preparer
    dropped: list[str] = []
    kept: list[dict] = []

    with engine.begin() as connection:
        for table, column, mirror in present:
            # Re-measure rather than trust the docstring: this runs on every
            # environment, and a column that is empty here may be the one place
            # someone recorded something there.
            #
            # Two ways a column can carry a fact, and both disqualify a drop:
            # more than one value, OR a null/set split that something reads as
            # a flag. Testing only the first is what nearly took daysmart_status
            # with it.
            distinct, populated, rows = connection.execute(
                text(
                    f"SELECT COUNT(DISTINCT {preparer.quote(column)}), "
                    f"       COUNT({preparer.quote(column)}), COUNT(*) "
                    f"FROM {preparer.quote(table)}"
                )
            ).one()
            partial = 0 < populated < rows
            if partial and mirror:
                # The flag may already live in another column. Only a mirror
                # that is set on EXACTLY the same rows makes this one's
                # presence redundant; anything else and the split means
                # something of its own.
                divergent = connection.execute(
                    text(
                        f"SELECT COUNT(*) FROM {preparer.quote(table)} WHERE "
                        f"({preparer.quote(column)} IS NULL) != ({preparer.quote(mirror)} IS NULL)"
                    )
                ).scalar() or 0
                if divergent == 0:
                    partial = False

            if distinct > 1 or partial:
                kept.append({
                    "column": f"{table}.{column}",
                    "distinct_values": int(distinct),
                    "populated": int(populated),
                    "of_rows": int(rows),
                })
                continue
            connection.execute(
                text(f"ALTER TABLE {preparer.quote(table)} DROP COLUMN {preparer.quote(column)}")
            )
            dropped.append(f"{table}.{column}")

    status = "retired" if dropped and not kept else ("blocked" if kept and not dropped else "partial")
    return {"status": status, "dropped": dropped, "kept": kept}

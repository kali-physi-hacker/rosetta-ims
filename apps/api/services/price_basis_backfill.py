"""Say out loud what each supplier price already means, once.

`catalogue_supplier_prices.basis` records what one `amount` buys — the pack, or
one sellable unit. Until now that was worked out at read time by comparing the
basis WORD against the packaging's words, and the words do not cooperate: the
basis reads "CASE" while the purchase unit reads "CAN(S)", so the comparison
refused and a $354 case published as nothing at all. On 94% of current rows the
word is "UNIT", the value meaning "no conversion needed", so the question was
never really asked.

This writes the answer down, using the same resolver the rest of the system
uses — offering_costs.resolve_basis — so there is one set of rules rather than
a Python copy and a SQL copy that drift. Rows where the words agreed are marked
verified; rows where a container word merely implies a pack keep their number
and stay unverified, which is not a defect but the honest state of a few
thousand rows nobody has checked, and the queue for whoever checks them.

Deliberately not clever. A heuristic could guess that a cost far above every
channel price must be a pack price, and would be right often enough to be
trusted — and a guess written into a NOT NULL column is indistinguishable from
a recorded fact a month later. That is the failure this change exists to leave
behind.

One-shot: once every environment reports `already_backfilled`, this module and
its call in main.py can be deleted.
"""

from __future__ import annotations

from sqlalchemy import inspect, text

from services.offering_costs import PER_PURCHASE_UNIT, PER_SELLABLE_UNIT, resolve_basis

_UNSTATED = """
    SELECT pr.id, pr.basis, pr.price_basis_uom_code,
           pk.purchase_uom_code, pk.sellable_unit_uom_code,
           pk.sellable_units_per_purchase_unit
      FROM catalogue_supplier_prices pr
      LEFT JOIN catalogue_packaging_configurations pk
        ON pk.supplier_product_id = pr.supplier_product_id
       AND pk.superseded_at IS NULL
     WHERE pr.basis_verified = 0
"""

_EMPTY = {"pack": 0, "unit": 0, "unverified": 0}


def backfill_price_basis(engine) -> dict:
    """State the basis on every price that can say it without guessing."""

    inspector = inspect(engine)
    if "catalogue_supplier_prices" not in set(inspector.get_table_names()):
        return {"status": "no_table", **_EMPTY}
    columns = {c["name"] for c in inspector.get_columns("catalogue_supplier_prices")}
    if not {"basis", "basis_verified"} <= columns:
        # The reconciler runs before this does, so the only way here is a
        # database mid-upgrade. The next boot picks it up rather than this one
        # guessing at a schema.
        return {"status": "awaiting_column", **_EMPTY}

    with engine.connect() as conn:
        changes: dict[tuple[str, int], list[int]] = {}
        for price_id, current, word, purchase, sellable, per_purchase in conn.execute(text(_UNSTATED)):
            basis, agreed = resolve_basis(
                word,
                (purchase, sellable, float(per_purchase) if per_purchase is not None else None),
            )
            verified = 1 if agreed else 0
            # A row already sitting at the answer is left alone, or every boot
            # would rewrite three thousand rows to the values they already hold.
            if (current, 0) == (basis, verified):
                continue
            changes.setdefault((basis, verified), []).append(price_id)

        if not changes:
            return {"status": "already_backfilled", **_EMPTY}

        counts = {"pack": 0, "unit": 0}
        for (basis, verified), ids in changes.items():
            _apply(conn, ids, basis, verified)
            counts["pack" if basis == PER_PURCHASE_UNIT else "unit"] += len(ids)
        conn.commit()

        unverified = conn.execute(
            text("SELECT COUNT(*) FROM catalogue_supplier_prices WHERE basis_verified = 0")
        ).scalar() or 0

    return {"status": "backfilled", **counts, "unverified": unverified}


def _apply(conn, ids: list[int], basis: str, verified: int) -> None:
    """Set the basis on a list of price ids, in chunks.

    By id rather than a correlated UPDATE ... FROM, because that syntax differs
    between SQLite and Postgres and this has to be one statement on both.
    """
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        conn.execute(
            text(
                "UPDATE catalogue_supplier_prices SET basis = :b, basis_verified = :v "
                f"WHERE id IN ({','.join(str(int(i)) for i in chunk)})"
            ),
            {"b": basis, "v": verified},
        )


__all__ = ["PER_PURCHASE_UNIT", "PER_SELLABLE_UNIT", "backfill_price_basis"]

"""How one channel sells a product: the unit it charges in, and the quantity.

Three facts, all per channel, all recorded rather than inferred:

* ``sell_uom``       — what the customer is charged in. The clinic sells a
  30 mL bottle by the millilitre while Shopify sells the bottle whole.
* ``sell_uom_count`` — how many of those make ONE SELLABLE UNIT, the thing
  cost is derived in. 30 for that bottle; null (read as 1) wherever the
  channel sells the unit whole, which is the ordinary case.
* ``order_multiple`` — the customer buys in multiples of this. 12 means 12,
  24, 36; never 1, never 18. The multiple IS the minimum, which is why the
  sell side needs one field where the buy side needs two.

Nothing here is derived from how we BUY. A case of twelve tells you what one
unit costs; it says nothing about whether a customer may buy one — and using it
for delivery charges a parcel to a quantity nobody can order.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.orm import Session

import models

_CACHE_KEY = "channel_sale_terms"


@dataclass(frozen=True)
class SaleTerms:
    """What one channel sells, in its own words. Blanks read as the plain case."""

    sell_uom: str | None = None
    sell_uom_count: Decimal = Decimal(1)
    order_multiple: int = 1

    @property
    def sells_by_measure(self) -> bool:
        """Priced in something other than the unit we cost in."""
        return self.sell_uom_count != 1


DEFAULT = SaleTerms()


def terms_for(session: Session, product_id: int | None, channel: str | None) -> SaleTerms:
    """This channel's terms for this product, or the plain defaults.

    Bulk-safe: the first call loads every listing once into ``session.info``,
    so serializing a catalogue costs one query rather than one per row.
    """
    if product_id is None or not channel:
        return DEFAULT
    return _map(session).get((product_id, str(channel).strip().lower()), DEFAULT)


def invalidate(session: Session) -> None:
    session.info.pop(_CACHE_KEY, None)


def _map(session: Session) -> dict[tuple[int, str], SaleTerms]:
    cached = session.info.get(_CACHE_KEY)
    if cached is not None:
        return cached

    out: dict[tuple[int, str], SaleTerms] = {}
    rows = session.query(
        models.SellingItem.product_variant_id,
        models.SellingItem.channel,
        models.SellingItem.sell_uom,
        models.SellingItem.sell_uom_count,
        models.SellingItem.order_multiple,
    ).all()
    for product_id, channel, sell_uom, count, multiple in rows:
        if product_id is None or not channel:
            continue
        # A count of zero or less is not a denomination; treat it as unstated
        # rather than dividing a price by nothing.
        units = Decimal(str(count)) if count is not None and Decimal(str(count)) > 0 else Decimal(1)
        out[(product_id, str(channel).strip().lower())] = SaleTerms(
            sell_uom=(sell_uom or "").strip() or None,
            sell_uom_count=units,
            order_multiple=int(multiple) if multiple and multiple > 1 else 1,
        )
    session.info[_CACHE_KEY] = out
    return out

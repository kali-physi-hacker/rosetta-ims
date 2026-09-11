"""Guards for SF Express logistics attribution and MBB cost-to-hit.

- shopify_logistics reproduces the SF Speedy Express HK-domestic rate card (weight billed rounded
  up to 0.5kg (<=5kg) then 1kg).
- _pack_sell_unit_delivery charges SF once per PARCEL (a pack ships together) and splits it across
  the pack's units; single-unit packs are unchanged.
- _cost_to_hit_mbb returns the real cash outlay to unlock a term, by term kind.
"""
import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_ROOT)

import database                                 # noqa: E402
import models                                   # noqa: E402
from services import pricing_service as P       # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)


def test_sf_rates_match_sheet():
    # SF Speedy Express, HK domestic, Limited-Time-Offer key tiers
    assert P.shopify_logistics(500) == 20.0
    assert P.shopify_logistics(600) == 30.0      # 0.6kg -> billed 1kg -> 30
    assert P.shopify_logistics(1000) == 30.0
    assert P.shopify_logistics(2500) == 51.0
    assert P.shopify_logistics(3000) == 58.0     # 3–5kg flat 58
    assert P.shopify_logistics(5000) == 58.0
    assert P.shopify_logistics(6000) == 68.0     # 6–10kg flat 68
    assert P.shopify_logistics(12000) == 78.0    # 11–15kg flat 78
    assert P.shopify_logistics(20000) == 88.0    # 16–20kg flat 88
    assert P.shopify_logistics(21000) == 294.0   # >20kg reverts to per-kg original
    assert P.shopify_logistics(None) == 58.0     # weight not recorded -> default
    assert P.shopify_logistics(0) == 58.0


def test_margin_still_subtracts_per_unit_logistics():
    # The margin formula is unchanged — it subtracts whatever per-sell-unit delivery it's given.
    # 500g -> SF $20; (100 - 40 - 20) / 100 = 0.40
    assert P._channel_margin(100.0, 40.0, None, P.shopify_logistics(500)) == 0.40


def test_sf_spread_over_pack():
    # Single-unit pack: the sell-unit IS the parcel -> unchanged from the old per-unit charge.
    assert P._pack_sell_unit_delivery(500, 1) == 20.0
    assert P._pack_sell_unit_delivery(2500, 1) == 51.0
    assert P._pack_sell_unit_delivery(2500, None) == 51.0
    # Box of 12 pouches @ 85g each: parcel weight 1020g -> SF $37, split over 12 = ~$3.08/pouch
    # (the fix: not $30+ charged to a single $18 pouch).
    assert round(P._pack_sell_unit_delivery(85, 12), 4) == round(P.shopify_logistics(1020) / 12, 4)
    assert P._pack_sell_unit_delivery(85, 12) < P._pack_sell_unit_delivery(85, 1)
    # Box of 24 cans @ 85g: parcel 2040g -> SF $51, /24 = ~$2.13/can.
    assert round(P._pack_sell_unit_delivery(85, 24), 4) == round(P.shopify_logistics(2040) / 24, 4)


def test_sf_spread_over_the_order_multiple_too():
    """A can you can only buy in 24s never ships alone.

    Hill's 3392 records units_per_pack 1 while the supplier sells it in 24s only. Charging one
    can a whole parcel read as a 95% loss on a product the business sells profitably.
    """
    # 79g can, pack size 1, but sold in 24s -> parcel is 1896g, split 24 ways.
    assert round(P._pack_sell_unit_delivery(79, 1, 24), 4) == round(P.shopify_logistics(79 * 24) / 24, 4)
    assert P._pack_sell_unit_delivery(79, 1, 24) < P._pack_sell_unit_delivery(79, 1)
    # Whichever floor is higher wins; neither above one leaves the old behaviour untouched.
    assert P._pack_sell_unit_delivery(85, 12, 4) == P._pack_sell_unit_delivery(85, 12)
    assert P._pack_sell_unit_delivery(500, 1, None) == 20.0
    assert P._pack_sell_unit_delivery(500, 1, 1) == 20.0


class _Term:
    """Minimal stand-in for an MBB term row (only the fields _cost_to_hit_mbb reads)."""
    def __init__(self, kind, min_qty=None, min_spend=None):
        self.kind, self.min_qty, self.min_spend = kind, min_qty, min_spend


def test_cost_to_hit_mbb_by_term_kind():
    # buy_x_get_y: pay BASIC price for the paid units (Frontline "buy 10 get 3 free": 10 × $215).
    assert P._cost_to_hit_mbb(_Term("buy_x_get_y", min_qty=10), 215.0, 165.38) == 2150.0
    # spend_discount: the spend threshold itself (Dermoscent "spend $1000 → 10% off").
    assert P._cost_to_hit_mbb(_Term("spend_discount", min_spend=1000.0), 156.0, 140.4) == 1000.0
    # tier / flat: min_qty at the achieved (discounted) cost (Advocate "12 @ 20% off": 12 × $217.6).
    assert P._cost_to_hit_mbb(_Term("tier", min_qty=12), 272.0, 217.6) == 2611.0
    # no term -> no value.
    assert P._cost_to_hit_mbb(None, 100.0, 90.0) is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))


# ── a stated term says what its amount buys ─────────────────────────────────
# mbb_terms.unit_cost was documented as per-sell-unit and had no way to say
# otherwise, so whoever entered a deal did the division in their head. Of 1,190
# stored terms carrying a cost, 113 hold a pack price and 66 hold a unit price
# divided by the pack size a second time. cost_basis is what removes the
# arithmetic from the person entering it.

def _stated(basis, amount=130.0, units_per_pack=60):
    """A stated term on a link with a known pack size. Real mapped objects, not
    stand-ins: the divisor is reached through the relationship, so a stub would
    not exercise the path that actually runs."""
    t = models.MbbTerm(kind="flat_unit_cost", unit_cost=amount, cost_basis=basis,
                       created_at="2026-09-10T00:00:00+00:00")
    t.product_supplier = models.ProductSupplier(
        units_per_pack=units_per_pack, updated_at="2026-09-10T00:00:00+00:00")
    return t


def test_a_pack_price_is_divided_by_the_pack():
    # $130 for a box of 60 is $2.17 a tablet, and nobody has to work that out.
    assert P._term_unit_cost(_stated("pack"), 3.0) == 130.0 / 60


def test_an_absent_basis_still_means_per_unit():
    """Every row written before the column existed meant per unit, and must not move."""
    assert P._term_unit_cost(_stated(None), 3.0) == 130.0
    assert P._term_unit_cost(_stated("unit"), 3.0) == 130.0


def test_the_basis_is_read_case_and_space_insensitively():
    assert P._term_unit_cost(_stated(" PACK "), 3.0) == 130.0 / 60


def test_a_pack_price_with_no_pack_size_is_absent_rather_than_wrong():
    """The same rule the catalogue prices follow: a cost nobody can derive has to
    be missing, because a blank margin sends someone to look and a confident
    wrong one does not."""
    assert P._term_unit_cost(_stated("pack", units_per_pack=None), 3.0) is None
    assert P._term_unit_cost(_stated("pack", units_per_pack=0), 3.0) is None


def test_a_flat_percentage_discount_needs_no_spend_threshold():
    """373 deals whose note reads "15%" were stored as flat_unit_cost with a
    number worked out by hand. They did not need a new kind: spend_discount
    computes it from the base, and min_spend is optional."""
    t = models.MbbTerm(kind="spend_discount", discount_pct=0.15, min_spend=None,
                       created_at="2026-09-10T00:00:00+00:00")
    assert P._term_unit_cost(t, 30.0) == 25.5
    assert P._cost_to_hit_mbb(t, 30.0, 25.5) is None    # nothing to hit


# ── the kinds added once the deal space was actually counted ────────────────
# Four labels were being asked to carry seven shapes. What unlocks a term
# (nothing / a quantity / a spend) and what it gives (a price / a percentage /
# free goods) are independent, and three real combinations had nowhere to go:
# 141 stored terms are a percentage with no minimum, 110 a percentage unlocked
# by quantity, 50 a price unlocked by spend. All were entered as flat_unit_cost
# with the number worked out by hand.


def _term(kind, **kw):
    return models.MbbTerm(kind=kind, created_at="2026-09-11T00:00:00+00:00", **kw)


def test_a_percentage_needs_no_minimum():
    """"15% off this brand" — 141 stored terms, none with a threshold."""
    t = _term("percentage_discount", discount_pct=0.15)
    assert P._term_unit_cost(t, 30.0) == 25.5
    assert P._cost_to_hit_mbb(t, 30.0, 25.5) is None      # nothing to reach


def test_a_percentage_can_be_unlocked_by_quantity():
    t = _term("qty_discount", min_qty=12, discount_pct=0.10)
    assert P._term_unit_cost(t, 30.0) == 27.0
    # 12 units at the discounted price is what it takes to get there
    assert P._cost_to_hit_mbb(t, 30.0, 27.0) == round(27.0 * 12, 0)


def test_a_price_can_be_unlocked_by_spend():
    """The pipeline already records this shape (minimum_spend x
    discounted_unit_price); the hand-entered side had no name for it."""
    t = _term("spend_unit_price", min_spend=5000, unit_cost=24.0)
    assert P._term_unit_cost(t, 30.0) == 24.0
    assert P._cost_to_hit_mbb(t, 30.0, 24.0) == 5000      # the threshold IS the outlay


def test_a_spend_unlocked_price_honours_its_basis_too():
    t = _term("spend_unit_price", min_spend=5000, unit_cost=130.0, cost_basis="pack")
    t.product_supplier = models.ProductSupplier(
        units_per_pack=60, updated_at="2026-09-11T00:00:00+00:00")
    assert P._term_unit_cost(t, 30.0) == 130.0 / 60


def test_the_older_kinds_are_unmoved():
    """Adding names must not shift what the 1,401 stored terms already read as."""
    assert P._term_unit_cost(_term("flat_unit_cost", unit_cost=9.0), 30.0) == 9.0
    assert P._term_unit_cost(_term("tier", min_qty=12, unit_cost=8.0), 30.0) == 8.0
    assert P._term_unit_cost(_term("spend_discount", min_spend=1000, discount_pct=0.1), 30.0) == 27.0

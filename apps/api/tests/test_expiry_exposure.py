"""Batch expiry on the product payload, and the expired/expiring split.

The old summary counted every batch dated `< 90 days` as "expiring soon", which
folded already-lapsed stock in with stock that is still sellable. Those are two
different decisions — write off vs. discount and clear — so they are two counts.

Guards:
  * expiry_batches is sorted soonest-first with days precomputed
  * a lapsed batch reports negative days
  * malformed / missing dates are skipped rather than blowing up the row
  * expiry_days is the soonest batch, None when nothing is tracked

Runnable directly (`python tests/test_expiry_exposure.py`) or under pytest.
"""
import os
import tempfile
from datetime import date, timedelta

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

import database               # noqa: E402
import models                 # noqa: E402
from services.pricing_service import expiry_batches, product_to_dict  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)

TODAY = date.today()


def _day(offset: int) -> str:
    return (TODAY + timedelta(days=offset)).isoformat()


def _mk_product(session, sku, batches):
    p = models.ProductVariant(sku_code=sku, name=sku, category="Food", status="ACTIVE",
                              storage_rule="any", created_at="2026-01-01T00:00:00",
                              updated_at="2026-01-01T00:00:00")
    session.add(p)
    session.flush()
    for ref, expiry, qty, loc in batches:
        session.add(models.ExpiryTracking(product_id=p.id, batch_ref=ref, expiry_date=expiry,
                                          qty=qty, location=loc, created_at="2026-01-01T00:00:00"))
    session.flush()
    session.refresh(p)
    return p


def test_batches_sort_soonest_first_with_signed_days():
    s = database.SessionLocal()
    try:
        p = _mk_product(s, "EXP-SORT", [
            ("late", _day(400), 5, "Warehouse"),
            ("lapsed", _day(-12), 3, "Clinic"),
            ("soon", _day(45), 9, "Warehouse"),
        ])
        out = expiry_batches(p)
        assert [b["batch_ref"] for b in out] == ["lapsed", "soon", "late"]
        assert out[0]["days"] == -12, "a lapsed batch must report negative days"
        assert out[1]["days"] == 45
        assert out[0]["qty"] == 3 and out[0]["location"] == "Clinic"
    finally:
        s.rollback(); s.close()


def test_unparseable_dates_are_skipped_not_fatal():
    s = database.SessionLocal()
    try:
        p = _mk_product(s, "EXP-JUNK", [
            ("junk", "not-a-date", 1, "Clinic"),
            ("good", _day(10), 2, "Clinic"),
        ])
        out = expiry_batches(p)
        assert [b["batch_ref"] for b in out] == ["good"]
    finally:
        s.rollback(); s.close()


def test_product_dict_carries_soonest_expiry():
    s = database.SessionLocal()
    try:
        rules = {r.category: r for r in s.query(models.CategoryRule).all()}
        tracked = _mk_product(s, "EXP-DICT", [("a", _day(-3), 4, "Clinic"), ("b", _day(70), 8, "Clinic")])
        bare = _mk_product(s, "EXP-NONE", [])

        d = product_to_dict(tracked, rules)
        assert d["expiry_days"] == -3, "soonest batch drives expiry_days"
        assert len(d["expiry_batches"]) == 2

        d2 = product_to_dict(bare, rules)
        assert d2["expiry_days"] is None
        assert d2["expiry_batches"] == []
    finally:
        s.rollback(); s.close()


def test_expired_and_expiring_are_counted_separately():
    """The summary split: lapsed is a write-off, inside-90-days is still sellable."""
    s = database.SessionLocal()
    try:
        lapsed = _mk_product(s, "EXP-LAPSED", [("a", _day(-5), 1, "Clinic")])
        soon = _mk_product(s, "EXP-SOON", [("b", _day(40), 1, "Clinic")])
        far = _mk_product(s, "EXP-FAR", [("c", _day(300), 1, "Clinic")])

        expiring = expired = 0
        for p in (lapsed, soon, far):
            batches = expiry_batches(p)
            if not batches:
                continue
            if batches[0]["days"] < 0:
                expired += 1
            elif batches[0]["days"] < 90:
                expiring += 1

        assert (expired, expiring) == (1, 1), "the far-dated batch counts as neither"
    finally:
        s.rollback(); s.close()


if __name__ == "__main__":
    test_batches_sort_soonest_first_with_signed_days()
    test_unparseable_dates_are_skipped_not_fatal()
    test_product_dict_carries_soonest_expiry()
    test_expired_and_expiring_are_counted_separately()
    print("expiry exposure: ok")

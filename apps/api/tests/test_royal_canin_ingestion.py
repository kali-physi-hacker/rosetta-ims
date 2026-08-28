"""Pressing the Royal Canin button: what gets queued, and what refuses to be.

Royal Canin's catalogue is fetched rather than uploaded, which introduces two
failure modes a file upload does not have — the same catalogue read twice, and
a read that stopped halfway. Both are decided here, before anything becomes a
run, because the desk downstream cannot tell a truncated fetch from a supplier
discontinuing half their range.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/rc_ingest.db")
os.environ.setdefault("CATALOGUE_UPLOAD_DIR", tempfile.mkdtemp(prefix="rc_uploads_"))

import pytest  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
from services import royal_canin_connector as connector  # noqa: E402
from services import royal_canin_ingestion as ingestion  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


@pytest.fixture()
def db():
    session = database.SessionLocal()
    try:
        for spec in ingestion.SUPPLIERS.values():
            if session.get(models.Supplier, spec["supplier_id"]) is None:
                session.add(models.Supplier(
                    id=spec["supplier_id"], name=spec["label"], code=f"RC{spec['supplier_id']}",
                    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
                ))
        session.flush()
        yield session
        session.rollback()
    finally:
        session.close()


def _snapshot(rows: int = 3, price: int = 105, tag: str = "x",
              product_range: str = connector.RANGE_VET) -> connector.Snapshot:
    """A snapshot for one test.

    `tag` keeps each test's catalogue distinct: the capture service COMMITS, so
    documents outlive the rolling-back fixture and identical snapshots would
    read as "unchanged" from a neighbouring test's run.
    """
    hits = [
        {
            "original_sku": f"{tag}-420060{index}",
            "sku": f"HK_{tag}420060{index}",
            "name": f"FHN CAT AGEING {index}",
            "ean_code": f"900357905016{index}",
            "nav_uom": "UNIT",
            "navision_weight": 2,
            "customer_groups_ids": ["879"],
            "categories": {"level0": ["VET DOG"] if product_range == connector.RANGE_VET
                                     else ["PET SHOP DOG"]},
            "stock_configuration": {"min_sale_qty": 1, "qty_increments": 0},
            "price": {"HKD": {"group_879_tier": price}},
        }
        for index in range(rows)
    ]
    return connector.build_snapshot(hits, customer_group="879", captured_on="2026-08-27",
                                    product_range=product_range)


def test_a_changed_catalogue_is_queued_for_review(db, monkeypatch):
    monkeypatch.setattr(ingestion.connector, "capture_snapshots",
                        lambda **kw: {connector.RANGE_VET: _snapshot(tag="new")})

    outcome = ingestion.capture_and_submit(db, submitted_by="tester")[0]

    assert outcome.status == "submitted"
    assert outcome.row_count == 3
    assert outcome.ingestion_run_id is not None
    run = db.query(models.IngestionRun).filter_by(run_uuid=str(outcome.ingestion_run_id)).first()
    assert run is not None
    assert run.supplier_source_contract_id == ingestion.SUPPLIERS[connector.RANGE_VET]["contract_id"]


def test_an_unchanged_catalogue_submits_nothing(db, monkeypatch):
    """A re-read is not a new document — the desk must not see the same work twice."""
    monkeypatch.setattr(ingestion.connector, "capture_snapshots",
                        lambda **kw: {connector.RANGE_VET: _snapshot(tag="same")})
    first = ingestion.capture_and_submit(db, submitted_by="tester")[0]
    db.commit()

    second = ingestion.capture_and_submit(db, submitted_by="tester")[0]

    assert first.status == "submitted"
    assert second.status == "unchanged"
    assert second.ingestion_run_id is None
    assert second.checksum == first.checksum
    assert "unchanged" in second.completeness or second.completeness


def test_a_price_change_is_a_new_document(db, monkeypatch):
    monkeypatch.setattr(ingestion.connector, "capture_snapshots",
                        lambda **kw: {connector.RANGE_VET: _snapshot(tag="pricechg")})
    first = ingestion.capture_and_submit(db, submitted_by="tester")[0]
    db.commit()

    monkeypatch.setattr(ingestion.connector, "capture_snapshots",
                        lambda **kw: {connector.RANGE_VET: _snapshot(price=999, tag="pricechg")})
    second = ingestion.capture_and_submit(db, submitted_by="tester")[0]

    assert second.status == "submitted"
    assert second.checksum != first.checksum


def test_a_short_read_refuses_rather_than_reporting_delistings(db, monkeypatch):
    """The failure mode a fetch has that a file does not."""
    monkeypatch.setattr(ingestion.connector, "capture_snapshots",
                        lambda **kw: {connector.RANGE_VET: _snapshot(rows=40, tag="short")})
    ingestion.capture_and_submit(db, submitted_by="tester")
    db.commit()

    # The next read comes back with a fraction of the catalogue.
    monkeypatch.setattr(ingestion.connector, "capture_snapshots",
                        lambda **kw: {connector.RANGE_VET: _snapshot(rows=4, tag="short")})
    with pytest.raises(ingestion.RoyalCaninCaptureRefused, match="came back short") as raised:
        ingestion.capture_and_submit(db, submitted_by="tester")
    # Nothing WAS submitted here, so the exception is honest — and it carries
    # the outcome so a caller can still say which supplier was held and why.
    assert [o.status for o in raised.value.outcomes] == ["refused"]
    assert raised.value.outcomes[0].releasable is True

    # A person who knows the range really shrank can still release it.
    released = ingestion.capture_and_submit(db, submitted_by="tester", force_incomplete=True)[0]
    assert released.status == "submitted"
    assert "must not be treated as proof" in released.completeness


def test_one_suppliers_refusal_does_not_unqueue_the_other(db, monkeypatch):
    """A submission COMMITS as it is made, so it cannot be taken back.

    Royal Canin invoices veterinary and retail separately and each half is
    judged alone. When one is queued and the other is held, saying "nothing was
    submitted" is simply false — the desk is already holding the queued run,
    and a reviewer told otherwise has no reason to go and look at it.
    """
    both = lambda vet_rows, retail_rows, price=105: (lambda **kw: {  # noqa: E731
        connector.RANGE_VET: _snapshot(rows=vet_rows, tag="split", price=price),
        connector.RANGE_NON_VET: _snapshot(rows=retail_rows, tag="split",
                                           product_range=connector.RANGE_NON_VET),
    })
    monkeypatch.setattr(ingestion.connector, "capture_snapshots", both(40, 40))
    ingestion.capture_and_submit(db, submitted_by="tester")
    db.commit()

    # Vet moved its prices; retail came back a fraction of its size.
    monkeypatch.setattr(ingestion.connector, "capture_snapshots", both(40, 3, price=222))
    outcomes = ingestion.capture_and_submit(db, submitted_by="tester")
    db.commit()

    by_range = {outcome.product_range: outcome for outcome in outcomes}
    assert by_range[connector.RANGE_VET].status == "submitted"
    assert by_range[connector.RANGE_VET].ingestion_run_id is not None
    assert by_range[connector.RANGE_NON_VET].status == "refused"
    assert "came back short" in by_range[connector.RANGE_NON_VET].refusal
    # And the queued run really is there for the reviewer the refusal used to
    # tell to go away.
    assert db.query(models.IngestionRun).filter_by(
        run_uuid=str(by_range[connector.RANGE_VET].ingestion_run_id)
    ).first() is not None


def test_a_range_that_came_back_empty_is_refused_not_skipped(db, monkeypatch):
    """An empty half is a filing change, not a delisting — and never a run.

    Royal Canin does not close one account's range and leave the other's
    intact on the same login. What it really means is that classification
    moved — a channel renamed, our group changed. Skipping the empty half
    quietly, as the loop used to, stops that supplier's catalogue updating
    with nothing said; queueing it would publish the mistake as truth.
    """
    monkeypatch.setattr(ingestion.connector, "capture_snapshots", lambda **kw: {
        connector.RANGE_VET: _snapshot(rows=0, tag="empty"),
        # Comfortably above anything a neighbouring test left behind: this
        # half must be judged healthy, so the empty half is the only refusal.
        connector.RANGE_NON_VET: _snapshot(rows=60, tag="empty",
                                           product_range=connector.RANGE_NON_VET),
    })
    outcomes = ingestion.capture_and_submit(db, submitted_by="tester")
    db.commit()

    by_range = {outcome.product_range: outcome for outcome in outcomes}
    assert by_range[connector.RANGE_VET].status == "refused"
    assert "no products at all" in by_range[connector.RANGE_VET].refusal
    # And it cannot be pressed past: there is no reading of the catalogue in
    # which this is true, so no button should be able to publish it.
    assert by_range[connector.RANGE_VET].releasable is False
    assert by_range[connector.RANGE_NON_VET].status == "submitted"

    # Pressing "queue it anyway" does not release it. On this second read the
    # retail half is unchanged too, so the whole capture queued nothing — which
    # is exactly when the refusal is allowed to be an exception.
    with pytest.raises(ingestion.RoyalCaninCaptureRefused, match="no products at all") as raised:
        ingestion.capture_and_submit(db, submitted_by="tester", force_incomplete=True)
    forced = {o.product_range: o for o in raised.value.outcomes}
    assert forced[connector.RANGE_VET].status == "refused"
    assert forced[connector.RANGE_NON_VET].status == "unchanged"


def test_a_document_this_connector_never_wrote_is_not_its_baseline(db, monkeypatch):
    """The baseline is the connector's own last snapshot, nothing else.

    A legacy import or a hand-registered document against the same supplier
    says nothing about how many products the shop held last time. Counted as
    the baseline, its row count either invents a shrinkage that never happened
    or hides a real one behind an unrelated number.
    """
    monkeypatch.setattr(ingestion.connector, "capture_snapshots",
                        lambda **kw: {connector.RANGE_VET: _snapshot(rows=40, tag="baseline")})
    ingestion.capture_and_submit(db, submitted_by="tester")
    db.commit()

    # A one-row document arrives for the same supplier carrying no contract —
    # the shape a legacy import leaves behind — and is now the newest.
    db.add(models.CatalogueSourceDocument(
        supplier_id=ingestion.SUPPLIERS[connector.RANGE_VET]["supplier_id"],
        filename="legacy-import.csv",
        source_ref=None,
        source_checksum="not-a-snapshot-checksum",
        received_at="2026-08-28T00:00:00+00:00",
        supplier_source_contract_id=None,
        created_at="2026-08-28T00:00:00+00:00",
    ))
    db.commit()

    # The next read is the same as the last REAL one. Against the legacy row it
    # would look like a 40x expansion with an unrecognised checksum, and would
    # be submitted all over again; against the connector's own snapshot it is
    # simply unchanged.
    outcome = ingestion.capture_and_submit(db, submitted_by="tester")[0]

    assert outcome.status == "unchanged"
    assert outcome.previous_checksum == outcome.checksum

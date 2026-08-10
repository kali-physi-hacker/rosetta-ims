"""Which SKUs went through the catalogue review desk.

The product pages used to show `hitl_verified`, which comes from the RETIRED
matching flow's audit trail and says nothing about the review desk — 8,854 of
11,160 live products carry it, including every SKU nobody has reviewed since.
The desk records the real thing: a human took a candidate to APPROVED and
published it, with their name and the time. That is what the pages now show.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import models
from routers.products import _catalogue_reviewed_skus, _mark_reviewed

NOW = "2026-08-01T00:00:00+00:00"


def _session() -> Session:
    engine = create_engine("sqlite:///:memory:")
    models.Base.metadata.create_all(engine)
    return Session(engine)


def _publish(db, *, sku="10006348", reviewed_by="seph", reviewed_at="2026-08-01T13:49:30+00:00",
             is_current=1, published_at="2026-08-01T13:49:58+00:00", candidate=True):
    candidate_uuid = f"cand-{sku}-{published_at}"
    if candidate:
        db.add(models.CatalogueMasteringCandidate(
            mastering_candidate_uuid=candidate_uuid, contract_version="catalogue.mastering_candidate.v1",
            ingestion_run_uuid="run-1", supplier_catalogue_uuid="s", source_file_uuid="f",
            catalogue_item_uuid="i", extraction_profile_id="p", extraction_profile_version="v1",
            raw_observation_ids_json="[]", lineage_json="{}",
            supplier_product_resolution_json="{}", product_variant_resolution_json="{}",
            packaging_resolution_json="{}", supplier_price_resolution_json="{}",
            mbb_resolution_json="{}", review_status="APPROVED",
            reviewed_by=reviewed_by, reviewed_at=reviewed_at, created_at=NOW,
        ))
    db.add(models.CatalogueServingPublication(
        contract_version="catalogue.serving_item.v1", publication_key=f"k-{sku}-{published_at}",
        publication_version="v2026-08-01", canonical_sku=sku, product_variant_key="pv",
        product_variant_name="Hill's Science Plan", product_id=1, supplier_id=14,
        supplier_product_id=1, supplier_product_key="supplier:14:offer:2968", supplier_sku="2968",
        current_approved_cost_amount=159.8, current_approved_cost_currency="HKD",
        current_approved_cost_basis_uom_code="UNIT", review_status="APPROVED",
        published_at=published_at, mastering_candidate_uuid=candidate_uuid,
        catalogue_item_uuid="i", raw_observation_ids_json="[]", lineage_json="{}",
        snapshot_json="{}", is_current=is_current, created_at=published_at,
    ))
    db.commit()


def test_a_published_sku_names_its_reviewer_and_the_time():
    with _session() as db:
        _publish(db)
        entry = _catalogue_reviewed_skus(db)["10006348"]
        assert entry["by"] == "seph"
        assert entry["at"] == "2026-08-01T13:49:30+00:00"
        assert entry["run"] == "run-1", "carried for the audit link only"


def test_a_sku_nobody_published_is_simply_absent():
    with _session() as db:
        assert _catalogue_reviewed_skus(db) == {}
        row = _mark_reviewed({"sku_code": "10006348"}, {})
        assert row["catalogue_reviewed"] is False
        assert row["catalogue_reviewed_by"] is None
        assert row["catalogue_reviewed_at"] is None


def test_a_superseded_publication_no_longer_counts_as_reviewed():
    """Only what is live now — a withdrawn publication is not a current review."""
    with _session() as db:
        _publish(db, is_current=0)
        assert _catalogue_reviewed_skus(db) == {}


def test_the_newest_current_publication_wins_for_a_multi_supplier_sku():
    with _session() as db:
        _publish(db, reviewed_by="team", reviewed_at="2026-07-30T09:00:00+00:00",
                 published_at="2026-07-30T09:05:00+00:00")
        _publish(db, reviewed_by="seph", reviewed_at="2026-08-01T13:49:30+00:00",
                 published_at="2026-08-01T13:49:58+00:00")
        assert _catalogue_reviewed_skus(db)["10006348"]["by"] == "seph"


def test_publication_time_stands_in_when_the_candidate_is_gone():
    """The publication is the fact; the reviewer stamp is detail on top of it."""
    with _session() as db:
        _publish(db, candidate=False)
        entry = _catalogue_reviewed_skus(db)["10006348"]
        assert entry["by"] is None
        assert entry["at"] == "2026-08-01T13:49:58+00:00"


def test_the_marker_is_independent_of_the_legacy_verified_flag():
    """The two answer different questions and must not be conflated.

    Live counts when this was written: 8,854 legacy-verified, 3 reviewed.
    """
    with _session() as db:
        _publish(db, sku="10006348")
        # A SKU the retired flow marked verified, which the desk never saw.
        db.add(models.CatalogueAuditEvent(
            sku_code="60000115", action="confirm_match", created_at="2026-06-27T04:32:09",
        ))
        db.commit()
        reviewed = _catalogue_reviewed_skus(db)

        assert "10006348" in reviewed
        assert "60000115" not in reviewed


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))

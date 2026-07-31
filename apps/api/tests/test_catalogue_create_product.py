"""Creating a canonical Product from an unmatched supplier offering.

An unmatched row used to be a dead end: the pipeline matches only on exact
supplier_sku/barcode, so the first time a supplier sends a product we already
stock, the row lands on PROPOSED_CREATE and can only be rejected or forced onto
some other product. These tests cover the third move — drafting a create — and,
more importantly, the guards that stop it becoming a duplicate factory.

The load-bearing design choice under test: the product is minted at APPLY, not
when the draft is confirmed. An abandoned or rejected draft must leave no SKU.
"""

from __future__ import annotations

import os
import tempfile
from uuid import UUID

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/create_product.db")

import database  # noqa: E402
import models  # noqa: E402
from schemas.catalogue_pipeline.enums import ReviewStatus  # noqa: E402
from services import catalogue_pipeline_stages as stages  # noqa: E402
from services import product_domain, variant_similarity  # noqa: E402

from tests.test_catalogue_pipeline_stage_services import (  # noqa: E402
    _build_claim,
    _capture_raw,
    _reset,
    _seed_context,
)

models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)


@pytest.fixture()
def db():
    session = database.SessionLocal()
    try:
        _reset(session)
        session.query(models.InventoryItem).delete()
        session.query(models.ProductVariant).delete()
        session.commit()
        variant_similarity.reset_index()
        yield session
        session.rollback()
        _reset(session)
        session.query(models.InventoryItem).delete()
        session.query(models.ProductVariant).delete()
        session.commit()
    finally:
        session.close()


DRAFT = {
    "name": "Hill's Prescription Diet - Wet Cat Food - GI Biome Chicken Stew",
    "category": "Food",
    "brand": "Hill's",
    "uom": "can",
}


def _unmatched_candidate(db, *, key="create-1", sku="10447"):
    """A row exactly as the matcher leaves it: PROPOSED_CREATE, no draft."""
    raw_id = _capture_raw(db)
    staging_id = _build_claim(db, raw_id)
    return stages.MasteringService(db).prepare_candidate(
        stages.PrepareMasteringCandidateCommand(
            catalogue_item_id=staging_id,
            idempotency_key=key,
            supplier_product_resolution={
                "state": "PROPOSED_CREATE",
                "supplier_id": 14,
                "supplier_product_id": f"supplier:14:offer:{sku}",
                "supplier_sku": sku,
            },
            product_variant_resolution={
                "state": "PROPOSED_CREATE",
                "proposed_name": "GI Biome GI Biome Chicken Stew",
                "product_variant_name": "GI Biome GI Biome Chicken Stew",
            },
        )
    ).output_ids[0]


def _candidate_with_draft(db, *, key="create-1", draft=DRAFT, sku="10447"):
    """The real reviewer path: an unmatched row corrected into a create draft.

    Corrections are immutable revisions, so what comes back is a NEW candidate
    superseding the original — the same object the UI then approves.
    """
    original = _unmatched_candidate(db, key=key, sku=sku)
    result = stages.MasteringService(db).revise_candidate(
        stages.ReviseMasteringCandidateCommand(
            mastering_candidate_id=original,
            actor_id="reviewer@example.com",
            reason="Not in the catalogue — drafting it.",
            product_variant_resolution={
                "state": "CONFIRMED_CREATE",
                "proposed_name": "GI Biome GI Biome Chicken Stew",
                "product_variant_name": draft["name"],
                "proposed_variant": draft,
            },
        )
    )
    return result.output_ids[0]


def _approve(db, candidate_id, key="approve"):
    return stages.ReviewDecisionService(db).record_decision(
        stages.RecordReviewDecisionCommand(
            mastering_candidate_id=candidate_id,
            actor_id="reviewer@example.com",
            review_status=ReviewStatus.APPROVED,
            decided_at="2026-07-31T00:05:00+00:00",
            reason="New to the catalogue; radar clear.",
            idempotency_key=key,
        )
    )


def _apply(db, candidate_id, key="apply"):
    return stages.ApprovedCommercialStateService(db).apply_approved_candidate(
        stages.ApplyApprovedCandidateCommand(
            mastering_candidate_id=candidate_id,
            applied_at="2026-07-31T00:06:00+00:00",
        )
    )


# ── the happy path ────────────────────────────────────────────────────────────

def test_confirmed_create_mints_the_product_at_apply_not_before(db):
    _seed_context(db)
    candidate_id = _candidate_with_draft(db)

    # Drafting and approving must not create anything.
    _approve(db, candidate_id)
    assert db.query(models.ProductVariant).count() == 0, "approve is a decision, not a write"

    _apply(db, candidate_id)

    variant = db.query(models.ProductVariant).one()
    assert variant.name == DRAFT["name"]
    assert variant.category == "Food"
    assert variant.brand == "Hill's"
    assert variant.status == "ACTIVE"
    assert variant.sku_code.startswith("1"), "Food takes SKU digit 1"
    assert len(variant.sku_code) == 8

    # The offering the run came to write is linked to it.
    offering = db.query(models.SupplierOffering).one()
    assert offering.product_variant_id == variant.id
    # And the SKU behaves like every other one from the moment it exists.
    assert db.query(models.InventoryItem).filter_by(product_variant_id=variant.id).count() == 1


def test_created_product_has_no_channel_or_stock(db):
    """Being able to buy something is not a decision to sell it."""
    _seed_context(db)
    candidate_id = _candidate_with_draft(db)
    _approve(db, candidate_id)
    _apply(db, candidate_id)

    variant = db.query(models.ProductVariant).one()
    assert db.query(models.ProductChannel).filter_by(product_id=variant.id).count() == 0
    assert db.query(models.StockLevel).filter_by(product_id=variant.id).count() == 0


def test_apply_is_replay_safe_and_creates_one_product(db):
    _seed_context(db)
    candidate_id = _candidate_with_draft(db)
    _approve(db, candidate_id)

    _apply(db, candidate_id)
    _apply(db, candidate_id)
    _apply(db, candidate_id)

    assert db.query(models.ProductVariant).count() == 1
    assert db.query(models.SupplierOffering).count() == 1


def test_apply_records_the_minted_sku_on_the_candidate(db):
    """Publish re-resolves the candidate's product, so apply must leave a trail."""
    _seed_context(db)
    candidate_id = _candidate_with_draft(db)
    _approve(db, candidate_id)
    _apply(db, candidate_id)

    import json

    row = db.query(models.CatalogueMasteringCandidate).filter_by(
        mastering_candidate_uuid=str(candidate_id)
    ).one()
    payload = json.loads(row.product_variant_resolution_json)
    minted = db.query(models.ProductVariant).one().sku_code
    assert payload["created_product_sku"] == minted
    assert payload["canonical_sku"] == minted


# ── the guards ────────────────────────────────────────────────────────────────

def test_proposed_create_is_still_a_dead_end(db):
    """Only a human-confirmed draft is applicable. The matcher's guess is not."""
    _seed_context(db)
    candidate_id = _unmatched_candidate(db)

    with pytest.raises(stages.AmbiguousProductVariant, match="must match an existing canonical product"):
        _approve(db, candidate_id)

    assert db.query(models.ProductVariant).count() == 0


def test_confirmed_create_without_a_draft_is_refused_by_the_contract(db):
    from pydantic import ValidationError

    from schemas.catalogue_pipeline.mastering_candidate_v1 import ProductVariantResolution

    lineage = {
        "catalogue_item_id": "44444444-4444-4444-8444-444444444444",
        "raw_observation_ids": ["55555555-5555-4555-8555-555555555555"],
    }
    with pytest.raises(ValidationError, match="CONFIRMED_CREATE requires proposed_variant"):
        ProductVariantResolution.model_validate({
            "state": "CONFIRMED_CREATE",
            "proposed_name": "Something new",
            "lineage": lineage,
        })


def test_a_category_with_no_sku_digit_fails_during_review_not_at_apply(db):
    """Category is the one field that can fail late — catch it in front of the human."""
    _seed_context(db)
    candidate_id = _candidate_with_draft(db, draft={**DRAFT, "category": "Spacecraft"})

    with pytest.raises(stages.AmbiguousProductVariant, match="no SKU digit"):
        _approve(db, candidate_id)

    assert db.query(models.ProductVariant).count() == 0


def test_rejected_draft_leaves_no_product_behind(db):
    _seed_context(db)
    candidate_id = _candidate_with_draft(db)

    stages.ReviewDecisionService(db).record_decision(
        stages.RecordReviewDecisionCommand(
            mastering_candidate_id=candidate_id,
            actor_id="reviewer@example.com",
            review_status=ReviewStatus.REJECTED,
            decided_at="2026-07-31T00:05:00+00:00",
            reason="Duplicate of 10006302 after all.",
            idempotency_key="reject",
        )
    )
    assert db.query(models.ProductVariant).count() == 0


def test_apply_adopts_an_offering_minted_by_the_legacy_path(db):
    """Offerings reach the table under two different key schemes.

    The pipeline mints "supplier:{id}:offer:{sku}"; offering_costs (legacy links
    and human cost edits) mints "…:offer:legacy-product-supplier:{n}". 2,791 of
    2,794 offerings in the real catalogue carry the second form, so looking up
    by key alone made apply try to INSERT a duplicate and die on the
    (supplier_id, supplier_sku) constraint. It must adopt the existing row —
    and, since that row already names a product, not mint a second one.
    """
    _seed_context(db)
    existing = models.ProductVariant(
        sku_code="10000999", name="Already in the catalogue", category="Food", status="ACTIVE",
        storage_rule="any", created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
    )
    db.add(existing)
    db.flush()
    db.add(models.SupplierOffering(
        supplier_product_key="supplier:14:offer:legacy-product-supplier:1737",
        supplier_id=14, supplier_sku="10447", product_variant_id=existing.id,
        status="active", created_at="2026-01-01T00:00:00",
    ))
    db.flush()

    candidate_id = _candidate_with_draft(db)
    _approve(db, candidate_id)
    _apply(db, candidate_id)

    assert db.query(models.SupplierOffering).count() == 1, "the existing offering must be adopted, not duplicated"
    assert db.query(models.ProductVariant).count() == 1, "the offering already names a product; do not mint a second"
    assert db.query(models.SupplierOffering).one().product_variant_id == existing.id

    # And the receipt must not claim the run created a product it merely adopted.
    import json as _json
    row = db.query(models.CatalogueMasteringCandidate).filter_by(
        mastering_candidate_uuid=str(candidate_id)).one()
    payload = _json.loads(row.product_variant_resolution_json)
    assert payload["canonical_sku"] == existing.sku_code
    assert payload.get("created_product_sku") is None


# ── SKU allocation ────────────────────────────────────────────────────────────

def test_a_cluster_of_creates_gets_distinct_skus(db):
    """Each create must see the previous one's SKU, or they all take the same number."""
    _seed_context(db)
    made = []
    for i in range(5):
        variant = product_domain.create_variant_from_draft(
            db, {**DRAFT, "name": f"Bulk create {i}"}, provenance={"actor": "reviewer@example.com"}
        )
        made.append(variant.sku_code)

    assert len(set(made)) == 5, f"duplicate SKUs allocated: {made}"
    assert all(s.startswith("1") for s in made)


def test_create_survives_a_taken_sku(db):
    """The unique index is the real guard; allocation retries around a collision."""
    _seed_context(db)
    from services import sku_service

    taken = sku_service.next_sku("Food", db)
    db.add(models.ProductVariant(
        sku_code=taken, name="Squatter", category="Food", status="ACTIVE",
        storage_rule="any", created_at="2026-07-31T00:00:00", updated_at="2026-07-31T00:00:00",
    ))
    db.flush()

    variant = product_domain.create_variant_from_draft(db, DRAFT)
    assert variant.sku_code != taken


def test_draft_without_a_name_is_refused(db):
    _seed_context(db)
    with pytest.raises(product_domain.VariantCreationError, match="no product name"):
        product_domain.create_variant_from_draft(db, {"name": "  ", "category": "Food"})


# ── the duplicate radar ───────────────────────────────────────────────────────

def _stock_the_catalogue(db):
    """A catalogue shaped like the real one: "Adult", "Digestive" and "Care"
    appear all over it, while "biome" and the diet codes appear once. That
    distribution is the scorer's whole premise, so a four-row fixture would
    prove nothing."""
    rows = [
        ("10006302", "Hill's Prescription Diet - Wet Cat Food - GI Biome Chicken & Vegetable Stew", "Hill's"),
        ("10006318", "Hill's Prescription Diet - Wet Dog Food - Canine i/d Digestive Care", "Hill's"),
        ("10008460", "Royal Canin - Mini Adult Dog Food (Digestive Care) - 3KG", "Royal Canin"),
        ("40007196", "Natural Core - All Natural Clumping Tofu Cat Litter - 20L", "Natural Core"),
        ("10008461", "Royal Canin - Maxi Adult Dog Food (Digestive Care) - 8KG", "Royal Canin"),
        ("10008462", "Royal Canin - Medium Adult Dog Food (Digestive Care) - 4KG", "Royal Canin"),
        ("10008463", "Royal Canin - Adult Cat Food (Digestive Care) - 2KG", "Royal Canin"),
        ("10009011", "Hill's Science Diet - Dry Dog Food - Adult Perfect Digestion", "Hill's"),
        ("10009012", "Hill's Science Diet - Dry Cat Food - Adult Oral Care", "Hill's"),
        ("10009013", "Hill's Science Diet - Wet Dog Food - Adult Chicken Stew", "Hill's"),
        ("10009014", "Canagan - Grain Free Canned Adult Dog Food - Country Game", "Canagan"),
        ("10009015", "Canagan - Grain Free Canned Adult Cat Food - Chicken", "Canagan"),
    ]
    for sku, name, brand in rows:
        db.add(models.ProductVariant(
            sku_code=sku, name=name, brand=brand, category="Food", status="ACTIVE",
            storage_rule="any", created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
        ))
    db.flush()
    variant_similarity.reset_index()


def test_radar_finds_the_product_a_substring_search_misses(db):
    """The case that motivated all of this: the words are there, just not adjacent."""
    _stock_the_catalogue(db)

    result = variant_similarity.duplicate_check(
        db, name="GI Biome GI Biome Chicken & Vegetable Stew - Digestive/ Fiber Care"
    )

    assert result["similar"], "the existing product must surface"
    assert result["similar"][0]["sku_code"] == "10006302"
    assert result["reason_required"] is True
    assert result["top_score"] >= variant_similarity.DUPLICATE_REASON_THRESHOLD


def test_radar_keeps_diet_codes_apart(db):
    """i/d must not collapse into the generic 'Digestive Care' crowd."""
    _stock_the_catalogue(db)

    result = variant_similarity.duplicate_check(db, name="i/d i/d Adult 1+ Canned - Digestive Care")

    assert result["similar"][0]["sku_code"] == "10006318", result["similar"][:2]


def test_radar_stays_quiet_for_a_genuinely_new_product(db):
    _stock_the_catalogue(db)

    result = variant_similarity.duplicate_check(db, name="Orijen Six Fish Freeze-Dried Dog Treats 92g")

    assert result["blockers"] == []
    assert result["reason_required"] is False


def test_barcode_collision_is_a_hard_block(db):
    _stock_the_catalogue(db)
    owner = db.query(models.ProductVariant).filter_by(sku_code="10006302").one()
    if db.get(models.Supplier, 14) is None:
        db.add(models.Supplier(id=14, code="SUP14", name="Hill's", created_at="2026-01-01T00:00:00"))
        db.flush()
    db.add(models.ProductSupplier(
        product_id=owner.id, supplier_id=14, barcode="052742104470",
        cost_source="manual", pack_source="manual", updated_at="2026-01-01T00:00:00",
    ))
    db.flush()

    result = variant_similarity.duplicate_check(db, name="Something else entirely", barcode="052742104470")

    assert [b["kind"] for b in result["blockers"]] == ["barcode"]
    assert result["blockers"][0]["sku_code"] == "10006302"


def test_identical_name_is_a_hard_block(db):
    _stock_the_catalogue(db)

    result = variant_similarity.duplicate_check(
        db, name="natural core   ALL NATURAL clumping tofu cat litter - 20L"
    )

    assert any(b["kind"] == "name" for b in result["blockers"]), result["blockers"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

"""Codeless rows are considered, matched by hand, and identified by the
internal SKU (ruling 2026-08-26).

A page that prints no supplier code used to strand its rows as
CONTRACT_REQUIRED_FIELD_MISSING(supplier_sku) forever. Now the row conforms,
lands in manual matching, and the moment a reviewer confirms its product
entity — an existing variant or a create draft — the entity's Rosetta SKU is
adopted as the offering identity at apply, back-written onto the candidate
the same way a minted SKU is. Absence of a code is workable; AMBIGUOUS
supplier evidence still refuses, because conflict is not absence.
"""

from __future__ import annotations

import json
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/adoption.db")

from pathlib import Path  # noqa: E402

import pytest  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
from schemas.catalogue_pipeline.enums import ResolutionState  # noqa: E402
from schemas.catalogue_pipeline.mastering_candidate_v1 import MasteringCandidateV1  # noqa: E402
from services import catalogue_pipeline_stages as stages  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FIXTURE = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "v1" / "valid" / "mastering_candidate_no_family.json"


@pytest.fixture()
def db():
    session = database.SessionLocal()
    try:
        # The matched entity must EXIST — the gate refuses matches into thin
        # air, which is its own correct behavior, not this ruling's subject.
        if session.query(models.ProductVariant).filter_by(sku_code="10447-1").first() is None:
            session.add(models.ProductVariant(
                sku_code="10447-1", name="Hill's c/d Chicken 4kg", category="Prescription Diet",
                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
            ))
            session.flush()
        yield session
        session.rollback()
    finally:
        session.close()


def _codeless_candidate(*, variant_state: str, canonical_sku: str | None) -> MasteringCandidateV1:
    payload = json.loads(FIXTURE.read_text())
    payload["supplier_product_resolution"].update(
        {"state": "UNRESOLVED", "supplier_product_id": None, "supplier_sku": None, "barcode": None}
    )
    payload["product_variant_resolution"].update(
        {"state": variant_state, "canonical_sku": canonical_sku, "product_variant_id": None}
    )
    return MasteringCandidateV1.model_validate(payload)


def _row_for(candidate: MasteringCandidateV1) -> models.CatalogueMasteringCandidate:
    return models.CatalogueMasteringCandidate(
        mastering_candidate_uuid=str(candidate.mastering_candidate_id),
        catalogue_item_uuid="55555555-5555-4555-8555-555555555555",
        ingestion_run_uuid="66666666-6666-4666-8666-666666666666",
        supplier_product_resolution_json=json.dumps(candidate.supplier_product_resolution.model_dump(mode="json")),
        product_variant_resolution_json=json.dumps(candidate.product_variant_resolution.model_dump(mode="json")),
    )


def test_confirmed_match_makes_a_codeless_candidate_approvable(db):
    candidate = _codeless_candidate(variant_state="CONFIRMED_MATCH", canonical_sku="10447-1")
    stages._assert_candidate_applicable(db, candidate)  # must not raise


def test_unconfirmed_variant_still_refuses_a_codeless_candidate(db):
    candidate = _codeless_candidate(variant_state="PROPOSED_MATCH", canonical_sku="10447-1")
    with pytest.raises(stages.AmbiguousSupplierOffer, match="codeless row qualifies only once"):
        stages._assert_candidate_applicable(db, candidate)


def test_adoption_keys_the_offering_by_the_internal_sku(db):
    candidate = _codeless_candidate(variant_state="CONFIRMED_MATCH", canonical_sku="10447-1")
    row = _row_for(candidate)
    service = stages.ApprovedCommercialStateService(db)

    adopted = service._adopt_internal_identity(candidate, row)

    assert adopted is True
    resolution = candidate.supplier_product_resolution
    assert resolution.supplier_sku == "10447-1"
    assert resolution.state is ResolutionState.PROPOSED_CREATE
    assert stages._candidate_supplier_product_key(candidate) == f"supplier:{resolution.supplier_id}:offer:10447-1"
    persisted = json.loads(row.supplier_product_resolution_json)
    assert persisted["supplier_sku"] == "10447-1"
    assert persisted["state"] == "PROPOSED_CREATE"


def test_adoption_declines_without_a_confirmed_entity(db):
    candidate = _codeless_candidate(variant_state="PROPOSED_MATCH", canonical_sku="10447-1")
    row = _row_for(candidate)
    service = stages.ApprovedCommercialStateService(db)

    assert service._adopt_internal_identity(candidate, row) is False
    assert candidate.supplier_product_resolution.supplier_sku is None

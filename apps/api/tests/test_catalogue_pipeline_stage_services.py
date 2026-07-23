"""Catalogue pipeline stage service tests."""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal
from uuid import UUID

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

import database  # noqa: E402
import models  # noqa: E402
import v2.models as v2_models  # noqa: E402
from schemas.catalogue_pipeline.enums import IssueResolutionStatus, ReviewStatus  # noqa: E402
from services import catalogue_pipeline_stages as stages  # noqa: E402


models.Base.metadata.create_all(bind=database.engine)
database.run_migrations(database.engine)


RUN_ID = UUID("11111111-1111-4111-8111-111111111111")
SOURCE_ID = UUID("22222222-2222-4222-8222-222222222222")
FILE_ID = UUID("33333333-3333-4333-8333-333333333333")
RUN_ID_2 = UUID("11111111-1111-4111-8111-111111111112")
SOURCE_ID_2 = UUID("22222222-2222-4222-8222-222222222223")
FILE_ID_2 = UUID("33333333-3333-4333-8333-333333333334")


@pytest.fixture()
def db():
    session = database.SessionLocal()
    try:
        _reset(session)
        yield session
        session.rollback()
        _reset(session)
    finally:
        session.close()


def _reset(session):
    for model in (
        v2_models.CatalogueServingPublication,
        v2_models.CatalogueSupplierMbbTerm,
        v2_models.CatalogueSupplierPrice,
        v2_models.CataloguePackagingConfiguration,
        v2_models.CatalogueSupplierProduct,
        v2_models.CatalogueReviewDecision,
        v2_models.CatalogueMasteringCandidate,
        v2_models.CatalogueValidationIssue,
        v2_models.CatalogueStagingRawObservation,
        v2_models.CatalogueStagingItem,
        v2_models.CatalogueRawObservation,
        v2_models.IngestionRun,
        v2_models.CatalogueSourceDocument,
    ):
        session.query(model).delete()
    session.query(models.CatalogueImport).filter(models.CatalogueImport.filename.like("stage-services-%")).delete()
    session.query(models.Product).filter(models.Product.sku_code.in_(("STAGE-SKU-10447", "STAGE-SKU-ALT"))).delete()
    session.commit()


def _seed_context(
    session,
    *,
    run_id: UUID = RUN_ID,
    source_id: UUID = SOURCE_ID,
    file_id: UUID = FILE_ID,
    supplier_id: int = 14,
    contract_id: str = "hills.price_list.v1",
    contract_version: str = "v1",
):
    supplier = session.get(models.Supplier, supplier_id)
    if supplier is None:
        supplier = models.Supplier(
            id=supplier_id,
            code=f"SUP{supplier_id}",
            name="Hill's" if supplier_id == 14 else f"Supplier {supplier_id}",
            created_at="2026-07-23T00:00:00+00:00",
        )
        session.add(supplier)
        session.flush()
    legacy_import = models.CatalogueImport(
        supplier_id=supplier_id,
        filename=f"stage-services-{run_id}.pdf",
        format="pdf",
        imported_at="2026-07-23T00:00:00+00:00",
        status="uploaded",
        item_count=0,
    )
    session.add(legacy_import)
    session.flush()
    source = v2_models.CatalogueSourceDocument(
        supplier_catalogue_uuid=str(source_id),
        source_file_uuid=str(file_id),
        legacy_import_id=legacy_import.id,
        supplier_id=supplier_id,
        filename=legacy_import.filename,
        source_format="PDF",
        received_at="2026-07-23T00:00:00+00:00",
        supplier_source_contract_id=contract_id,
        supplier_source_contract_version=contract_version,
        document_type="PRICE_LIST",
        created_at="2026-07-23T00:00:00+00:00",
    )
    session.add(source)
    session.flush()
    run = v2_models.IngestionRun(
        run_uuid=str(run_id),
        source_document_id=legacy_import.id,
        catalogue_source_document_id=source.id,
        supplier_id=supplier_id,
        supplier_source_contract_id=contract_id,
        supplier_source_contract_version=contract_version,
        document_type="PRICE_LIST",
        extractor_name="stage-test",
        extractor_version="v1",
        status="queued",
        started_at="2026-07-23T00:00:00+00:00",
        created_at="2026-07-23T00:00:00+00:00",
    )
    session.add(run)
    session.commit()
    return run, source


def _raw_input(key="row-1", text='10447 Healthy Cuisine 24/2.9 oz HK$13.10'):
    return stages.RawObservationInput(
        idempotency_key=key,
        source_location={"page_number": 1, "source_object_key": key},
        raw_text=text,
        extraction_method="MODEL_TEXT",
        captured_at="2026-07-23T00:01:00+00:00",
        extraction_model="fixture",
        extraction_model_version="v1",
        extraction_confidence="0.96",
        source_metadata={"row_key": key},
    )


def _capture_raw(db, *, run_id=RUN_ID, source_id=SOURCE_ID, file_id=FILE_ID, key="row-1"):
    return stages.RawObservationService(db).capture(
        stages.CaptureRawObservationsCommand(
            ingestion_run_id=run_id,
            supplier_catalogue_id=source_id,
            source_file_id=file_id,
            supplier_id=14,
            observations=(_raw_input(key),),
        )
    ).output_ids[0]


def _raw_fields(cost="13.10 HKD per can", packaging="24/2.9 oz"):
    return {
        "supplier_sku": "10447",
        "product_name": "Hill's Healthy Cuisine Chicken 2.9 oz",
        "brand": "Hill's",
        "category": "Food",
        "cost": cost,
        "packaging": packaging,
        "barcode": "052742104470",
        "variant": "2.9 oz",
    }


def _proposed_fields(raw_id: UUID, *, include_cost=True, include_packaging=True):
    evidence = {"raw_observation_id": str(raw_id), "field_path": "/raw_text", "confidence": "0.96"}
    proposed = {
        "supplier_sku": {"value": "10447", "evidence": evidence},
        "product_name": {"value": "Hill's Healthy Cuisine Chicken 2.9 oz", "evidence": evidence},
        "brand": {"value": "Hill's", "evidence": evidence},
        "category": {"value": "Food", "evidence": evidence},
        "barcode": {"value": "052742104470", "evidence": evidence},
        "variant": {"value": "2.9 oz", "evidence": evidence},
        "mbb_terms": [],
    }
    if include_cost:
        proposed["cost"] = {
            "amount": "13.10",
            "currency": "HKD",
            "price_basis": {"code": "CAN"},
            "evidence": evidence,
        }
    if include_packaging:
        proposed["packaging"] = {
            "purchase_uom": {"code": "CASE"},
            "price_basis": {"code": "CAN"},
            "sellable_unit_uom": {"code": "CAN"},
            "sellable_units_per_purchase_unit": "24",
            "content_amount": "82",
            "content_uom": {"code": "G"},
            "order_increment": {"amount": "24", "uom": {"code": "CAN"}},
            "source_text": "24/2.9 oz",
            "evidence": evidence,
        }
    return proposed


def _build_staging(db, raw_id: UUID, *, include_cost=True, include_packaging=True, key="stage-row-1"):
    return stages.StagingCatalogueService(db).build_item(
        stages.BuildStagingItemCommand(
            raw_observation_ids=(raw_id,),
            raw_fields=_raw_fields(),
            proposed_fields=_proposed_fields(raw_id, include_cost=include_cost, include_packaging=include_packaging),
            idempotency_key=key,
        )
    ).output_ids[0]


def _seed_product(db):
    product = db.query(models.Product).filter_by(sku_code="STAGE-SKU-10447").first()
    if product is None:
        product = models.Product(
            sku_code="STAGE-SKU-10447",
            name="Hill's Healthy Cuisine Chicken 2.9 oz",
            brand="Hill's",
            category="Food",
            storage_rule="any",
            status="ACTIVE",
            created_at="2026-07-23T00:00:00+00:00",
            updated_at="2026-07-23T00:00:00+00:00",
        )
        db.add(product)
        db.commit()
    return product


def _prepare_candidate(db, staging_id: UUID, *, key="candidate-row-1"):
    return stages.MasteringService(db).prepare_candidate(
        stages.PrepareMasteringCandidateCommand(
            catalogue_item_id=staging_id,
            idempotency_key=key,
            supplier_product_resolution={
                "state": "PROPOSED_CREATE",
                "supplier_id": 14,
                "supplier_product_id": "supplier:14:offer:10447",
                "supplier_sku": "10447",
                "barcode": "052742104470",
            },
            product_variant_resolution={
                "state": "PROPOSED_MATCH",
                "product_variant_id": "STAGE-SKU-10447",
                "canonical_sku": "STAGE-SKU-10447",
                "product_variant_name": "Hill's Healthy Cuisine Chicken 2.9 oz",
                "product_family_id": None,
            },
        )
    ).output_ids[0]


def test_raw_capture_uses_supported_contract_and_is_idempotent(db):
    _seed_context(db)

    service = stages.RawObservationService(db)
    command = stages.CaptureRawObservationsCommand(
        ingestion_run_id=RUN_ID,
        supplier_catalogue_id=SOURCE_ID,
        source_file_id=FILE_ID,
        supplier_id=14,
        observations=(_raw_input(),),
    )

    first = service.capture(command)
    second = service.capture(command)

    assert first.metrics.created_count == 1
    assert second.metrics.reused_count == 1
    assert db.query(v2_models.CatalogueRawObservation).count() == 1
    row = db.query(v2_models.CatalogueRawObservation).one()
    assert row.raw_text == '10447 Healthy Cuisine 24/2.9 oz HK$13.10'
    assert row.extraction_profile_id == "hills.price_list.v1"
    assert row.extraction_confidence == Decimal("0.9600")

    changed = stages.CaptureRawObservationsCommand(
        ingestion_run_id=RUN_ID,
        supplier_catalogue_id=SOURCE_ID,
        source_file_id=FILE_ID,
        supplier_id=14,
        observations=(_raw_input(text="changed text"),),
    )
    with pytest.raises(stages.IdempotencyConflict):
        service.capture(changed)


def test_raw_capture_rejects_unverified_supplier_contract(db):
    _seed_context(
        db,
        supplier_id=91,
        contract_id="vetapet.vet_price_list.v1",
        contract_version="v1",
    )

    with pytest.raises(stages.UnsupportedSupplierContract, match="not SUPPORTED"):
        stages.RawObservationService(db).capture(
            stages.CaptureRawObservationsCommand(
                ingestion_run_id=RUN_ID,
                supplier_catalogue_id=SOURCE_ID,
                source_file_id=FILE_ID,
                supplier_id=91,
                observations=(_raw_input(),),
            )
        )


def test_staging_preserves_lineage_and_rejects_cross_run_grouping(db):
    _seed_context(db)
    _seed_context(db, run_id=RUN_ID_2, source_id=SOURCE_ID_2, file_id=FILE_ID_2)
    raw_1 = _capture_raw(db)
    raw_2 = _capture_raw(db, run_id=RUN_ID_2, source_id=SOURCE_ID_2, file_id=FILE_ID_2, key="row-2")

    result = stages.StagingCatalogueService(db).build_item(
        stages.BuildStagingItemCommand(
            raw_observation_ids=(raw_1,),
            raw_fields=_raw_fields(),
            proposed_fields=_proposed_fields(raw_1),
            idempotency_key="stage-row-1",
        )
    )

    assert result.metrics.created_count == 1
    staging = db.query(v2_models.CatalogueStagingItem).one()
    assert staging.raw_fields_json != staging.proposed_fields_json
    assert db.query(v2_models.CatalogueStagingRawObservation).filter_by(raw_observation_uuid=str(raw_1)).count() == 1

    with pytest.raises(stages.MissingOrIncompatibleLineage, match="different ingestion runs"):
        stages.StagingCatalogueService(db).build_item(
            stages.BuildStagingItemCommand(
                raw_observation_ids=(raw_1, raw_2),
                raw_fields=_raw_fields(),
                proposed_fields=_proposed_fields(raw_1),
                idempotency_key="stage-cross-run",
            )
        )

    with pytest.raises(stages.MissingOrIncompatibleLineage, match="duplicates"):
        stages.StagingCatalogueService(db).build_item(
            stages.BuildStagingItemCommand(
                raw_observation_ids=(raw_1, raw_1),
                raw_fields=_raw_fields(),
                proposed_fields=_proposed_fields(raw_1),
                idempotency_key="stage-dupe",
            )
        )


def test_validation_dedupes_resolves_and_blocks_mastering_until_resolved(db):
    _seed_context(db)
    raw_id = _capture_raw(db)
    staging_id = _build_staging(db, raw_id, include_cost=False, include_packaging=False)

    validation = stages.CatalogueValidationService(db)
    first = validation.evaluate_staging(stages.EvaluateStagingCommand(catalogue_item_id=staging_id))
    second = validation.evaluate_staging(stages.EvaluateStagingCommand(catalogue_item_id=staging_id))

    assert first.metrics.created_count == 2
    assert second.metrics.reused_count == 2
    assert first.metrics.blocking_issue_count == 1
    assert db.query(v2_models.CatalogueValidationIssue).count() == 2
    with pytest.raises(stages.BlockingValidationIssues):
        _prepare_candidate(db, staging_id)

    blocking = (
        db.query(v2_models.CatalogueValidationIssue)
        .filter_by(issue_code="STAGING_COST_BASIS_UNRESOLVED")
        .one()
    )
    validation.resolve_issue(
        stages.ResolveValidationIssueCommand(
            validation_issue_id=UUID(blocking.validation_issue_uuid),
            resolver_id="bizops@example.com",
            resolution_status=IssueResolutionStatus.ACCEPTED_AS_IS,
            resolution_note="Proceeding as a reviewed exception for fixture coverage.",
            idempotency_key="resolve-cost-basis",
        )
    )
    candidate_id = _prepare_candidate(db, staging_id)
    assert candidate_id
    assert db.query(v2_models.CatalogueReviewDecision).filter_by(validation_issue_uuid=blocking.validation_issue_uuid).count() == 1


def test_stage_services_apply_approved_candidate_and_publish_idempotently(db):
    _seed_context(db)
    _seed_product(db)
    raw_id = _capture_raw(db)
    staging_id = _build_staging(db, raw_id)
    candidate_id = _prepare_candidate(db, staging_id)

    with pytest.raises(stages.PublicationIneligible):
        stages.ServingPublicationService(db).publish(
            stages.PublishServingItemCommand(
                mastering_candidate_id=candidate_id,
                publication_version="2026-07-23T00:10:00Z",
                idempotency_key="publish-before-apply",
            )
        )

    review = stages.ReviewDecisionService(db)
    decision = review.record_decision(
        stages.RecordReviewDecisionCommand(
            mastering_candidate_id=candidate_id,
            actor_id="reviewer@example.com",
            review_status=ReviewStatus.APPROVED,
            decided_at="2026-07-23T00:05:00+00:00",
            reason="Approved fixture candidate.",
            idempotency_key="approve-candidate",
        )
    )
    repeated_decision = review.record_decision(
        stages.RecordReviewDecisionCommand(
            mastering_candidate_id=candidate_id,
            actor_id="reviewer@example.com",
            review_status=ReviewStatus.APPROVED,
            decided_at="2026-07-23T00:05:00+00:00",
            reason="Approved fixture candidate.",
            idempotency_key="approve-candidate",
        )
    )

    assert decision.metrics.created_count == 1
    assert repeated_decision.metrics.reused_count == 1
    applied = stages.ApprovedCommercialStateService(db).apply_approved_candidate(
        stages.ApplyApprovedCandidateCommand(
            mastering_candidate_id=candidate_id,
            applied_at="2026-07-23T00:06:00+00:00",
        )
    )
    applied_again = stages.ApprovedCommercialStateService(db).apply_approved_candidate(
        stages.ApplyApprovedCandidateCommand(
            mastering_candidate_id=candidate_id,
            applied_at="2026-07-23T00:06:00+00:00",
        )
    )

    assert applied.metrics.created_count == 1
    assert applied_again.metrics.reused_count == 1
    supplier_product = db.query(v2_models.CatalogueSupplierProduct).one()
    assert supplier_product.product_family_id is None
    assert supplier_product.supplier_product_key == "supplier:14:offer:10447"
    price = db.query(v2_models.CatalogueSupplierPrice).one()
    assert price.amount == Decimal("13.1000")
    assert price.price_basis_uom_code == "CAN"
    packaging = db.query(v2_models.CataloguePackagingConfiguration).one()
    assert packaging.sellable_units_per_purchase_unit == Decimal("24.000000")
    assert packaging.content_amount == Decimal("82.000000")
    assert packaging.content_uom_code == "G"

    publisher = stages.ServingPublicationService(db)
    publication = publisher.publish(
        stages.PublishServingItemCommand(
            mastering_candidate_id=candidate_id,
            publication_version="2026-07-23T00:10:00Z",
            published_at="2026-07-23T00:10:00+00:00",
            idempotency_key="publish-candidate",
        )
    )
    repeated_publication = publisher.publish(
        stages.PublishServingItemCommand(
            mastering_candidate_id=candidate_id,
            publication_version="2026-07-23T00:10:00Z",
            published_at="2026-07-23T00:10:00+00:00",
            idempotency_key="publish-candidate",
        )
    )

    assert publication.metrics.created_count == 1
    assert repeated_publication.metrics.reused_count == 1
    serving = db.query(v2_models.CatalogueServingPublication).one()
    assert serving.review_status == "APPROVED"
    assert serving.cost_per_sellable_unit_amount == Decimal("13.1000")
    assert serving.is_current == 1


def test_review_rejects_stale_candidate_revision_and_staging_key_conflicts(db):
    _seed_context(db)
    raw_id = _capture_raw(db)
    staging_id = _build_staging(db, raw_id)
    candidate_id = _prepare_candidate(db, staging_id)

    with pytest.raises(stages.StaleCandidateRevision):
        stages.ReviewDecisionService(db).record_decision(
            stages.RecordReviewDecisionCommand(
                mastering_candidate_id=candidate_id,
                actor_id="reviewer@example.com",
                review_status=ReviewStatus.APPROVED,
                expected_candidate_created_at="2026-01-01T00:00:00+00:00",
                idempotency_key="stale-approval",
            )
        )

    with pytest.raises(stages.IdempotencyConflict):
        stages.StagingCatalogueService(db).build_item(
            stages.BuildStagingItemCommand(
                raw_observation_ids=(raw_id,),
                raw_fields={**_raw_fields(), "product_name": "Changed"},
                proposed_fields=_proposed_fields(raw_id),
                idempotency_key="stage-row-1",
            )
        )

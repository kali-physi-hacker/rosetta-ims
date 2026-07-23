"""Catalogue Prefect orchestration tests."""

from __future__ import annotations

import hashlib
import os
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
import pypdf

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_LOGGING_LEVEL", "ERROR")

import database  # noqa: E402
import models  # noqa: E402
import v2.models as v2_models  # noqa: E402
from orchestration.catalogue_contract_resolution import resolve_recorded_supplier_contract  # noqa: E402
from orchestration.catalogue_dispatch import dispatch_queued_runs  # noqa: E402
from orchestration.catalogue_extraction_adapter import (  # noqa: E402
    ExtractionEvidenceError,
    extract_source_evidence,
    staging_payload_from_extracted_row,
)
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from orchestration.catalogue_run_lifecycle import claim_queued_run, terminal_result_for_replay  # noqa: E402
from orchestration.catalogue_source_loader import SourceVerificationError, load_and_verify_source_asset  # noqa: E402
from orchestration.catalogue_types import DuplicateRunClaim, RecordedContractError  # noqa: E402
from services import extraction_service  # noqa: E402
from services.catalogue_submission import CatalogueSubmissionCommand, CatalogueSubmissionService  # noqa: E402


models.Base.metadata.create_all(bind=database.engine)
database.run_migrations(database.engine)


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CATALOGUE_ORCHESTRATION_MAX_SOURCE_BYTES", str(1024 * 1024))
    session = database.SessionLocal()
    try:
        _reset(session)
        _seed_supplier(session, 1, "ALF", "Alfamedic")
        _seed_supplier(session, 14, "HILLS", "Hill's")
        _seed_supplier(session, 91, "VETAPETV", "Vetapet Vet")
        yield session
        session.rollback()
        _reset(session)
    finally:
        session.close()


def _reset(session):
    for model in (
        v2_models.CatalogueSubmissionIdempotency,
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
    session.query(models.CatalogueItem).delete()
    session.query(models.CatalogueImport).delete()
    session.commit()


def _seed_supplier(session, supplier_id: int, code: str, name: str):
    supplier = session.get(models.Supplier, supplier_id)
    if supplier is None:
        session.add(
            models.Supplier(
                id=supplier_id,
                code=code,
                name=name,
                created_at="2026-07-23T00:00:00+00:00",
            )
        )
        session.commit()


def _pdf_bytes(page_count: int = 1) -> bytes:
    writer = pypdf.PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=72, height=72)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _submit(session, *, supplier_id: int = 14, contract_id: str | None = None, contract_version: str | None = None):
    service = CatalogueSubmissionService(
        session,
        upload_root=os.environ["CATALOGUE_UPLOAD_DIR"],
        max_upload_bytes=1024 * 1024,
    )
    return service.submit(
        CatalogueSubmissionCommand(
            supplier_id=supplier_id,
            original_filename="fixture.pdf",
            content_type="application/pdf",
            stream=BytesIO(_pdf_bytes()),
            contract_id=contract_id,
            contract_version=contract_version,
            idempotency_key=None,
            submitted_by="pytest",
        )
    )


def _source_path(session) -> Path:
    source = session.query(v2_models.CatalogueSourceDocument).one()
    return Path(os.environ["CATALOGUE_UPLOAD_DIR"]) / source.source_ref


def _hills_rows(_content, _filename, _content_type, contract=None):
    return (
        [
            {
                "description": "Hill's Healthy Cuisine Chicken 82g",
                "brand": "Hill's",
                "category": "Food",
                "supplier_sku": "10447",
                "barcode": "052742104470",
                "cost_price": 13.1,
                "pack_size": "82g",
                "variant": "82g",
                "confidence": 0.96,
                "_raw_text": "10447 Healthy Cuisine Chicken 82g HK$13.10",
                "bulk_buy_tiers": "ambiguous offer text",
            }
        ],
        "pdf",
    )


def _alfamedic_rows(_content, _filename, _content_type, contract=None):
    return (
        [
            {
                "description": "Syringe 10ml",
                "brand": "Alfamedic",
                "supplier_sku": "ALF-10",
                "cost_price": "12.50",
                "pack_size": "10 pieces",
                "order_increment_qty": "10",
                "confidence": "0.91",
                "_raw_text": "ALF-10 Syringe 10ml 10 pieces HK$12.50",
            }
        ],
        "pdf",
    )


def test_source_loader_verifies_file_path_size_signature_and_checksum(db):
    result = _submit(db)
    source = db.query(v2_models.CatalogueSourceDocument).one()

    asset = load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)
    assert asset.run_identity.run_uuid == result.ingestion_run_id
    assert asset.sha256 == source.source_checksum
    assert asset.source_ref.startswith("v2/")

    with pytest.raises(SourceVerificationError, match="size limit"):
        load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id, max_source_bytes=1)

    path = _source_path(db)
    path.write_bytes(b"%PDF-1.4\nchanged")
    with pytest.raises(SourceVerificationError, match="checksum"):
        load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)

    source.source_checksum = hashlib.sha256(b"not a pdf").hexdigest()
    path.write_bytes(b"not a pdf")
    db.commit()
    with pytest.raises(SourceVerificationError, match="signature"):
        load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)

    source.source_ref = "../outside.pdf"
    source.source_checksum = hashlib.sha256(_pdf_bytes()).hexdigest()
    db.commit()
    with pytest.raises(SourceVerificationError, match="relative path"):
        load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)


def test_source_loader_rejects_missing_file(db):
    result = _submit(db)
    _source_path(db).unlink()

    with pytest.raises(SourceVerificationError, match="missing"):
        load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)


def test_exact_recorded_contract_resolution_has_no_supplier_only_fallback(db):
    result = _submit(db)
    resolved = resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)
    assert resolved.slug == "hills.price_list.v1"

    run = db.query(v2_models.IngestionRun).one()
    run.supplier_source_contract_id = None
    run.supplier_source_contract_version = None
    db.commit()
    with pytest.raises(RecordedContractError, match="exact"):
        resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)


def test_recorded_contract_resolution_rejects_unsupported_unknown_mismatch_and_document_type(db):
    result = _submit(db)
    run = db.query(v2_models.IngestionRun).one()
    source = db.query(v2_models.CatalogueSourceDocument).one()

    run.supplier_id = 91
    source.supplier_id = 91
    run.supplier_source_contract_id = source.supplier_source_contract_id = "vetapet.vet_price_list.v1"
    run.supplier_source_contract_version = source.supplier_source_contract_version = "v1"
    db.commit()
    with pytest.raises(RecordedContractError, match="not SUPPORTED"):
        resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)

    run.supplier_id = source.supplier_id = 14
    run.supplier_source_contract_id = source.supplier_source_contract_id = "hills.price_list.v1"
    run.supplier_source_contract_version = source.supplier_source_contract_version = "v2"
    db.commit()
    with pytest.raises(RecordedContractError, match="hills.price_list.v1@v2"):
        resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)

    run.supplier_id = source.supplier_id = 1
    run.supplier_source_contract_version = source.supplier_source_contract_version = "v1"
    db.commit()
    with pytest.raises(RecordedContractError, match="belongs to supplier=14"):
        resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)

    run.supplier_id = source.supplier_id = 14
    run.document_type = "PROMOTION_SHEET"
    source.document_type = "PROMOTION_SHEET"
    db.commit()
    with pytest.raises(RecordedContractError, match="document_type"):
        resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)


def test_extraction_adapter_preserves_hills_page_evidence_and_unresolved_mbb(db, monkeypatch):
    result = _submit(db)
    asset = load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)
    contract = resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)
    monkeypatch.setattr(extraction_service, "extract", _hills_rows)

    extracted = extract_source_evidence(asset, contract)

    assert extracted.rejected_count == 0
    row = extracted.rows[0]
    assert row.source_location["page_number"] == 1
    assert row.raw_text == "10447 Healthy Cuisine Chicken 82g HK$13.10"
    assert row.extraction_confidence == Decimal("0.96")

    raw_fields, proposed = staging_payload_from_extracted_row(
        row,
        raw_observation_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        runtime_contract=contract,
    )
    assert raw_fields["mbb_text"] == "ambiguous offer text"
    assert proposed["mbb_terms"] == []
    assert "sellable_units_per_purchase_unit" not in proposed["packaging"]
    assert proposed["packaging"]["content_amount"] == "82"


def test_extraction_adapter_preserves_alfamedic_page_evidence(db, monkeypatch):
    result = _submit(db, supplier_id=1, contract_id="alfamedic.price_list.v1", contract_version="v1")
    asset = load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)
    contract = resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)
    monkeypatch.setattr(extraction_service, "extract", _alfamedic_rows)

    row = extract_source_evidence(asset, contract).rows[0]

    assert row.source_location["page_number"] == 1
    assert row.extracted_fields["supplier_sku"] == "ALF-10"
    assert row.extraction_confidence == Decimal("0.91")


def test_extraction_adapter_rejects_stub_errors_empty_and_missing_evidence(db, monkeypatch):
    result = _submit(db)
    asset = load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)
    contract = resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)

    monkeypatch.setattr(
        extraction_service,
        "extract",
        lambda *_a, **_k: ([{"_stub": True, "description": "[AI extraction disabled] fixture"}], "pdf"),
    )
    with pytest.raises(ExtractionEvidenceError, match="no truthful rows"):
        extract_source_evidence(asset, contract)

    monkeypatch.setattr(extraction_service, "extract", lambda *_a, **_k: ([], "pdf"))
    with pytest.raises(ExtractionEvidenceError, match="no rows"):
        extract_source_evidence(asset, contract)

    monkeypatch.setattr(
        extraction_service,
        "extract",
        lambda *_a, **_k: ([{"description": "No evidence", "confidence": "0.9"}], "pdf"),
    )
    with pytest.raises(ExtractionEvidenceError, match="no truthful rows"):
        extract_source_evidence(asset, contract)


def test_lifecycle_claim_and_terminal_replay_are_safe(db):
    result = _submit(db)

    claim_queued_run(db, ingestion_run_id=result.ingestion_run_id)
    run = db.query(v2_models.IngestionRun).one()
    assert run.status == "running"
    assert run.started_at is not None

    with pytest.raises(DuplicateRunClaim):
        claim_queued_run(db, ingestion_run_id=result.ingestion_run_id)

    run.status = "completed"
    run.completed_at = "2026-07-23T00:10:00+00:00"
    run.items_extracted = 1
    db.commit()
    replay = terminal_result_for_replay(db, ingestion_run_id=result.ingestion_run_id)
    assert replay.terminal_status == "completed"
    assert replay.rows_extracted == 1


def test_flow_unknown_run_returns_sanitized_failure_result(db):
    missing = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")

    result = catalogue_ingestion_flow(ingestion_run_id=missing)

    assert result.ingestion_run_id == missing
    assert result.terminal_status == "failed"
    assert result.error_code == "RUN_NOT_FOUND"


def test_flow_runs_machine_pipeline_and_stops_at_pending_review(db, monkeypatch):
    result = _submit(db)
    monkeypatch.setattr(extraction_service, "extract", _hills_rows)

    flow_result = catalogue_ingestion_flow(ingestion_run_id=result.ingestion_run_id)

    assert flow_result.terminal_status == "completed"
    assert flow_result.rows_extracted == 1
    assert flow_result.raw_observations_created == 1
    assert flow_result.staging_items_created == 1
    assert flow_result.mastering_candidates_created == 1
    assert flow_result.human_review_required is True

    run = db.query(v2_models.IngestionRun).one()
    assert run.status == "completed"
    assert run.started_at is not None
    assert run.completed_at is not None
    assert db.query(v2_models.CatalogueRawObservation).count() == 1
    assert db.query(v2_models.CatalogueStagingItem).count() == 1
    candidate = db.query(v2_models.CatalogueMasteringCandidate).one()
    assert candidate.review_status == "PENDING_REVIEW"
    assert db.query(v2_models.CatalogueReviewDecision).count() == 0
    assert db.query(v2_models.CatalogueSupplierProduct).count() == 0
    assert db.query(v2_models.CatalogueServingPublication).count() == 0

    replay = catalogue_ingestion_flow(ingestion_run_id=result.ingestion_run_id)
    assert replay.terminal_status == "completed"
    assert db.query(v2_models.CatalogueRawObservation).count() == 1


def test_flow_records_blocking_validation_and_skips_candidate(db, monkeypatch):
    result = _submit(db, supplier_id=1, contract_id="alfamedic.price_list.v1", contract_version="v1")

    def by_quote(_content, _filename, _content_type, contract=None):
        return (
            [
                {
                    "description": "Quoted item",
                    "supplier_sku": "Q-1",
                    "cost_price": "By Quote",
                    "pack_size": "1 piece",
                    "confidence": "0.8",
                    "_raw_text": "Q-1 Quoted item By Quote",
                }
            ],
            "pdf",
        )

    monkeypatch.setattr(extraction_service, "extract", by_quote)

    flow_result = catalogue_ingestion_flow(ingestion_run_id=result.ingestion_run_id)

    assert flow_result.terminal_status == "completed_with_warnings"
    assert flow_result.validation_issues_created == 1
    assert flow_result.mastering_candidates_created == 0
    issue = db.query(v2_models.CatalogueValidationIssue).one()
    assert issue.issue_code == "STAGING_COST_BASIS_UNRESOLVED"
    assert issue.publish_blocking == 1


def test_flow_failure_is_sanitized_and_durable(db, monkeypatch):
    result = _submit(db)
    _source_path(db).write_bytes(b"%PDF-1.4\nchanged")

    flow_result = catalogue_ingestion_flow(ingestion_run_id=result.ingestion_run_id)

    assert flow_result.terminal_status == "failed"
    run = db.query(v2_models.IngestionRun).one()
    assert run.status == "failed"
    assert "checksum" in run.error_summary
    assert str(Path(os.environ["CATALOGUE_UPLOAD_DIR"])) not in run.error_summary


def test_dispatcher_uses_bounded_batches_and_duplicate_dispatch_is_harmless(db, monkeypatch):
    first = _submit(db)
    _submit(db)
    called: list[UUID] = []

    def fake_flow(*, ingestion_run_id):
        called.append(ingestion_run_id)
        session = database.SessionLocal()
        try:
            claim_queued_run(session, ingestion_run_id=ingestion_run_id)
        finally:
            session.close()

    monkeypatch.setattr("orchestration.catalogue_dispatch.catalogue_ingestion_flow", fake_flow)

    result = dispatch_queued_runs(batch_size=1)

    assert result.queued_count == 1
    assert result.submitted_count == 1
    assert called == [first.ingestion_run_id]

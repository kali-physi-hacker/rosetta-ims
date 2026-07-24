"""Catalogue Prefect orchestration tests."""

from __future__ import annotations

import hashlib
import os
import tempfile
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
import pypdf
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_LOGGING_LEVEL", "ERROR")

import database  # noqa: E402
import models  # noqa: E402
import v2.models as v2_models  # noqa: E402
from orchestration import catalogue_extraction_adapter as extraction_adapter  # noqa: E402
from orchestration.catalogue_contract_resolution import resolve_recorded_supplier_contract  # noqa: E402
from orchestration.catalogue_dispatch import dispatch_queued_runs  # noqa: E402
from orchestration.catalogue_extraction_adapter import extract_source_evidence  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from orchestration.catalogue_run_lifecycle import claim_queued_run, terminal_result_for_replay  # noqa: E402
from orchestration.catalogue_source_loader import SourceVerificationError, load_and_verify_source_asset  # noqa: E402
from orchestration.catalogue_types import (  # noqa: E402
    DuplicateRunClaim,
    ExtractionEvidenceError,
    RecordedContractError,
    TransientProviderError,
)
from schemas.catalogue_pipeline.enums import ExtractionMethod, SourceFormat  # noqa: E402
from schemas.catalogue_pipeline.raw_observation_v1 import SourceLocation  # noqa: E402
from services import catalogue_interpretation  # noqa: E402
from services.catalogue_evidence_extraction import (  # noqa: E402
    ExtractedEvidence,
    ExtractionError,
    ExtractionResult,
    ExtractionStatus,
)
from services.catalogue_interpretation import interpret_observations  # noqa: E402
from services.catalogue_submission import CatalogueSubmissionCommand, CatalogueSubmissionService  # noqa: E402


models.Base.metadata.create_all(bind=database.engine)
database.run_migrations(database.engine)


HILLS_ROW_TEXT = "10447 Healthy Cuisine Chicken 82g HK$13.10"
HILLS_FIELDS = {
    "description": "Hill's Healthy Cuisine Chicken 82g",
    "brand": "Hill's",
    "category": "Food",
    "supplier_sku": "10447",
    "barcode": "052742104470",
    "cost_price": 13.1,
    "pack_size": "82g",
    "variant": "82g",
    "confidence": "0.96",
    "bulk_buy_tiers": "ambiguous offer text",
}


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CATALOGUE_ORCHESTRATION_MAX_SOURCE_BYTES", str(1024 * 1024))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
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


def _text_pdf_bytes(lines: list[str]) -> bytes:
    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    font_ref = writer._add_object(font)
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
    )
    parts = ["BT", "/F1 10 Tf", "36 750 Td", "14 TL"]
    for line in lines:
        escaped = line.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        parts.append(f"({escaped}) Tj")
        parts.append("T*")
    parts.append("ET")
    stream = DecodedStreamObject()
    stream.set_data("\n".join(parts).encode("utf-8"))
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def _submit(
    session,
    *,
    supplier_id: int = 14,
    contract_id: str | None = None,
    contract_version: str | None = None,
    content: bytes | None = None,
):
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
            stream=BytesIO(content if content is not None else _pdf_bytes()),
            contract_id=contract_id,
            contract_version=contract_version,
            idempotency_key=None,
            submitted_by="pytest",
        )
    )


def _source_path(session) -> Path:
    source = session.query(v2_models.CatalogueSourceDocument).one()
    return Path(os.environ["CATALOGUE_UPLOAD_DIR"]) / source.source_ref


def _evidence(
    *,
    key: str = "page:1:line:1",
    text: str | None = HILLS_ROW_TEXT,
    page: int = 1,
) -> ExtractedEvidence:
    return ExtractedEvidence(
        observation_key=key,
        source_location=SourceLocation(page_number=page, source_object_key=key),
        raw_text=text,
        extraction_method=ExtractionMethod.PDF_TEXT,
        provider="pypdf",
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


def test_real_pdf_text_extraction_produces_line_observations(db):
    result = _submit(db, content=_text_pdf_bytes(["Hill's price list", HILLS_ROW_TEXT]))
    asset = load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)

    outcome = extract_source_evidence(asset)

    assert outcome.rejected_units == 0
    assert outcome.warnings == ()
    assert [observation.observation_key for observation in outcome.observations] == [
        "page:1:line:1",
        "page:1:line:2",
    ]
    assert outcome.observations[1].raw_text == HILLS_ROW_TEXT
    assert outcome.observations[1].extraction_method == ExtractionMethod.PDF_TEXT
    assert outcome.observations[1].source_location.page_number == 1


def test_extraction_policy_maps_partial_and_failed_results(db, monkeypatch):
    result = _submit(db)
    asset = load_and_verify_source_asset(db, ingestion_run_id=result.ingestion_run_id)

    partial = ExtractionResult(
        status=ExtractionStatus.PARTIAL,
        source_format=SourceFormat.PDF,
        observations=(_evidence(),),
        units_attempted=2,
        units_completed=1,
        errors=(ExtractionError(code="SOURCE_PAGE_READ_ERROR", message="page 2 could not be read", unit_key="page:2"),),
    )
    monkeypatch.setattr(extraction_adapter, "extract_evidence", lambda *a, **k: partial)
    outcome = extract_source_evidence(asset)
    assert outcome.rejected_units == 1
    assert outcome.warnings == ("page:2: page 2 could not be read",)
    assert len(outcome.observations) == 1

    transient = ExtractionResult(
        status=ExtractionStatus.FAILED,
        source_format=SourceFormat.PDF,
        units_attempted=1,
        units_completed=0,
        errors=(ExtractionError(code="TRANSIENT_PROVIDER_ERROR", message="provider throttled", retryable=True),),
    )
    monkeypatch.setattr(extraction_adapter, "extract_evidence", lambda *a, **k: transient)
    with pytest.raises(TransientProviderError, match="throttled"):
        extract_source_evidence(asset)

    failed = ExtractionResult(
        status=ExtractionStatus.FAILED,
        source_format=SourceFormat.PDF,
        units_attempted=1,
        units_completed=0,
        errors=(ExtractionError(code="MALFORMED_PDF", message="PDF source could not be read"),),
    )
    monkeypatch.setattr(extraction_adapter, "extract_evidence", lambda *a, **k: failed)
    with pytest.raises(ExtractionEvidenceError, match="could not be read"):
        extract_source_evidence(asset)


def test_interpretation_maps_hills_row_and_preserves_unresolved_mbb(db, monkeypatch):
    result = _submit(db)
    contract = resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)
    observation = _evidence()
    raw_id = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
    monkeypatch.setattr(
        catalogue_interpretation,
        "_model_interpret_rows",
        lambda rows, _contract: {observation.observation_key: dict(HILLS_FIELDS)},
    )

    outcome = interpret_observations((observation,), (raw_id,), contract)

    assert outcome.skipped_count == 0
    item = outcome.items[0]
    assert item.observation_key == observation.observation_key
    assert item.raw_fields["mbb_text"] == "ambiguous offer text"
    assert item.raw_fields["cost"] == "13.1"
    assert item.proposed_fields["mbb_terms"] == []
    assert item.proposed_fields["supplier_sku"]["value"] == "10447"
    assert item.proposed_fields["supplier_sku"]["evidence"]["raw_observation_id"] == str(raw_id)
    assert item.proposed_fields["supplier_sku"]["evidence"]["field_path"] == "/raw_text"
    assert item.proposed_fields["cost"]["amount"] == "13.1"
    assert item.proposed_fields["packaging"]["content_amount"] == "82"
    assert "sellable_units_per_purchase_unit" not in item.proposed_fields["packaging"]


def test_interpretation_skips_non_catalogue_rows(db, monkeypatch):
    result = _submit(db)
    contract = resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)
    header = _evidence(key="page:1:line:1", text="Supplier SKU | Description | Cost")
    row = _evidence(key="page:1:line:2")
    ids = (UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))

    monkeypatch.setattr(
        catalogue_interpretation,
        "_model_interpret_rows",
        lambda rows, _contract: {header.observation_key: None, row.observation_key: dict(HILLS_FIELDS)},
    )
    outcome = interpret_observations((header, row), ids, contract)
    assert outcome.skipped_count == 1
    assert [item.observation_key for item in outcome.items] == [row.observation_key]


def test_interpretation_degrades_without_provider(db):
    # Without a configured provider the real seam degrades: nothing is skipped,
    # nothing is invented, and every observation stages for manual review.
    result = _submit(db)
    contract = resolve_recorded_supplier_contract(db, ingestion_run_id=result.ingestion_run_id)
    header = _evidence(key="page:1:line:1", text="Supplier SKU | Description | Cost")
    row = _evidence(key="page:1:line:2")
    ids = (UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))

    degraded = interpret_observations((header, row), ids, contract)
    assert degraded.skipped_count == 0
    assert len(degraded.items) == 2
    assert all(item.proposed_fields.get("cost") is None for item in degraded.items)
    assert any("not configured" in warning for warning in degraded.warnings)


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
    result = _submit(db, content=_text_pdf_bytes([HILLS_ROW_TEXT]))
    monkeypatch.setattr(
        catalogue_interpretation,
        "_model_interpret_rows",
        lambda rows, _contract: {key: dict(HILLS_FIELDS) for key in rows},
    )

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
    quoted_row = "Q-1 Quoted item By Quote"
    result = _submit(
        db,
        supplier_id=1,
        contract_id="alfamedic.price_list.v1",
        contract_version="v1",
        content=_text_pdf_bytes([quoted_row]),
    )
    monkeypatch.setattr(
        catalogue_interpretation,
        "_model_interpret_rows",
        lambda rows, _contract: {
            key: {
                "description": "Quoted item",
                "supplier_sku": "Q-1",
                "cost_price": "By Quote",
                "pack_size": "1 piece",
                "confidence": "0.8",
            }
            for key in rows
        },
    )

    flow_result = catalogue_ingestion_flow(ingestion_run_id=result.ingestion_run_id)

    assert flow_result.terminal_status == "completed_with_warnings"
    assert flow_result.validation_issues_created == 1
    assert flow_result.mastering_candidates_created == 0
    issue = db.query(v2_models.CatalogueValidationIssue).one()
    assert issue.issue_code == "STAGING_COST_BASIS_UNRESOLVED"
    assert issue.publish_blocking == 1


def test_flow_without_interpretation_provider_stages_everything_for_review(db):
    result = _submit(db, content=_text_pdf_bytes([HILLS_ROW_TEXT]))

    flow_result = catalogue_ingestion_flow(ingestion_run_id=result.ingestion_run_id)

    assert flow_result.terminal_status == "completed_with_warnings"
    assert flow_result.rows_extracted == 1
    assert flow_result.staging_items_created == 1
    assert flow_result.mastering_candidates_created == 1
    assert any("not configured" in warning for warning in flow_result.warnings)
    staging = db.query(v2_models.CatalogueStagingItem).one()
    assert staging.review_requirement in {"NOT_REQUIRED", "RECOMMENDED", "REQUIRED"}


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

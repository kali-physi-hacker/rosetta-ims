"""Re-parsing a run: same evidence, fresh interpretation, no provider.

A supplier contract is consumed at CONFORMANCE, which reaches no model
provider. So changing a mapping should cost nothing and take a second — but the
only re-run path was `retry`, which re-submits the file and puts every page back
through vision (~80s and real money for a Hill's-sized document) to re-derive
bytes already sitting in the RAW layer.

These pin the three properties that make a re-parse worth having: it never
opens the file, it never reaches the provider, and it leaves the source run's
decisions alone.
"""

from __future__ import annotations

import json
import os
import tempfile
from uuid import UUID, uuid4

import pytest

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/reparse.db")

import database  # noqa: E402
import models  # noqa: E402
from orchestration import catalogue_reparse as reparse  # noqa: E402
from services.catalogue_submission import (  # noqa: E402
    CatalogueSubmissionService,
    RetryNotAllowedError,
    SubmissionNotFoundError,
)

models.Base.metadata.create_all(bind=database.engine)


@pytest.fixture()
def db():
    session = database.SessionLocal()
    try:
        for model in (
            models.CatalogueExtractedEvidence,
            models.IngestionRun,
            models.CatalogueSourceDocument,
            models.CatalogueImport,
            models.Supplier,
        ):
            session.query(model).delete()
        session.commit()
        yield session
        session.rollback()
    finally:
        session.close()


def _seed_run(db, *, status="completed_with_warnings", observations=3):
    supplier = models.Supplier(code="ALF", name="Alfamedic", created_at="2026-01-01T00:00:00")
    db.add(supplier)
    db.flush()
    imp = models.CatalogueImport(supplier_id=supplier.id, filename="prices.pdf", imported_at="2026-01-01T00:00:00")
    db.add(imp)
    db.flush()
    source = models.CatalogueSourceDocument(
        legacy_import_id=imp.id, supplier_id=supplier.id, filename="prices.pdf",
        source_format="PDF", source_ref="stored/prices.pdf", source_checksum="abc",
        received_at="2026-01-01T00:00:00", supplier_source_contract_id="alfamedic.price_list.v1",
        supplier_source_contract_version="v1", document_type="PRICE_LIST",
        byte_size=1024, page_count=2, created_at="2026-01-01T00:00:00",
    )
    db.add(source)
    db.flush()
    run = models.IngestionRun(
        run_uuid=str(uuid4()), source_document_id=imp.id, catalogue_source_document_id=source.id,
        supplier_id=supplier.id, contract_version="catalogue.extraction_profile.v1",
        supplier_source_contract_id="alfamedic.price_list.v1", supplier_source_contract_version="v1",
        document_type="PRICE_LIST", extractor_name="queued-submission", extractor_version="v1",
        status=status, created_at="2026-01-01T00:00:00",
        completed_at="2026-01-01T00:05:00" if status not in {"queued", "running"} else None,
    )
    db.add(run)
    db.flush()
    for index in range(observations):
        db.add(models.CatalogueExtractedEvidence(
            raw_observation_uuid=str(uuid4()), ingestion_run_uuid=run.run_uuid,
            supplier_catalogue_uuid=source.supplier_catalogue_uuid,
            source_file_uuid=source.source_file_uuid,
            extraction_profile_id="p", extraction_profile_version="v1",
            contract_version="catalogue.extracted_evidence.v1",
            source_location_json=json.dumps({"page_number": 1, "source_object_key": f"page:1:row:{index}"}),
            source_object_key=f"page:1:row:{index}", page_number=1,
            raw_text=f"row {index}", raw_cells_json="[]",
            extraction_method="MODEL_VISION", captured_at="2026-01-01T00:01:00+00:00",
            extraction_model="gemini-flash-latest", extraction_model_version="gemini-flash-latest",
            source_metadata_json=json.dumps({
                "observation_key": f"page:1:row:{index}",
                "provider": "google", "provider_version": "gemini-flash-latest",
            }),
            created_at="2026-01-01T00:01:00",
        ))
    db.flush()
    return run


def test_a_reparse_queues_a_new_run_linked_to_its_source(db):
    run = _seed_run(db)
    result = CatalogueSubmissionService(db).reparse(UUID(run.run_uuid), submitted_by="reviewer")

    new = db.query(models.IngestionRun).filter_by(run_uuid=str(result.ingestion_run_id)).one()
    assert new.status == "queued"
    assert new.parent_run_id == run.id
    metrics = json.loads(new.metrics)
    assert metrics["reparse_of"] == run.run_uuid
    assert metrics["reparse_from_stage"] == "conformance"


def test_the_raw_stage_of_a_reparse_never_opens_the_file(db):
    """The upload may be long gone; that must not stop a re-parse.

    Identity is what the rest of the flow needs, and the source document
    already records it.
    """
    run = _seed_run(db)
    result = CatalogueSubmissionService(db).reparse(UUID(run.run_uuid))
    db.commit()

    outcome = reparse.reparse_raw_stage(db, ingestion_run_id=result.ingestion_run_id)
    assert outcome.run_identity.run_uuid == result.ingestion_run_id
    assert outcome.run_identity.contract_id == "alfamedic.price_list.v1"
    assert outcome.original_filename == "prices.pdf"
    assert outcome.page_count == 2


def test_the_reparse_reads_the_sources_stored_observations(db):
    run = _seed_run(db, observations=5)
    result = CatalogueSubmissionService(db).reparse(UUID(run.run_uuid))
    db.commit()

    stored = reparse.load_stored_evidence(db, ingestion_run_id=result.ingestion_run_id)
    assert stored.source_run_uuid == run.run_uuid
    assert len(stored.observations) == 5
    # The extraction-stage shape, which is what the flow hands to capture.
    assert stored.observations[0].observation_key


def test_reparsing_a_reparse_still_reads_the_original_evidence(db):
    """A fifth contract iteration must not start costing money."""
    run = _seed_run(db)
    service = CatalogueSubmissionService(db)
    first = service.reparse(UUID(run.run_uuid))
    # The chain only matters once the middle run has actually run.
    db.query(models.IngestionRun).filter_by(run_uuid=str(first.ingestion_run_id)).update(
        {"status": "completed_with_warnings", "completed_at": "2026-01-01T00:06:00"})
    db.commit()
    second = service.reparse(first.ingestion_run_id)
    db.commit()

    stored = reparse.load_stored_evidence(db, ingestion_run_id=second.ingestion_run_id)
    assert stored.source_run_uuid == run.run_uuid, "must chain back to the run that actually extracted"


def test_completing_a_reparse_keeps_its_lineage(db):
    """Finishing a run rewrites its metrics wholesale — provenance must survive.

    `reparse_of` is written before the flow runs and says how the run came to
    exist, not what it did. Losing it on completion would make a re-parse
    indistinguishable from a normal run afterwards, and would send a re-parse
    OF a re-parse back to the file.
    """
    from orchestration.catalogue_run_lifecycle import _metrics_json
    from orchestration.catalogue_types import CatalogueFlowResult

    before = json.dumps({"reparse_of": "abc-123", "reparse_from_stage": "conformance"})
    result = CatalogueFlowResult(
        ingestion_run_id=uuid4(), terminal_status="completed", rows_extracted=238,
    )
    after = json.loads(_metrics_json(result, existing=before))
    assert after["reparse_of"] == "abc-123"
    assert after["reparse_from_stage"] == "conformance"
    assert after["rows_seen"] == 238, "and the flow's own metrics still land"


def test_a_run_with_no_evidence_is_refused_with_a_reason(db):
    run = _seed_run(db, observations=0)
    with pytest.raises(RetryNotAllowedError, match="nothing to re-parse"):
        CatalogueSubmissionService(db).reparse(UUID(run.run_uuid))


def test_an_in_flight_run_cannot_be_reparsed(db):
    run = _seed_run(db, status="running")
    with pytest.raises(RetryNotAllowedError, match="wait for it to finish"):
        CatalogueSubmissionService(db).reparse(UUID(run.run_uuid))


def test_reparsing_from_extraction_is_refused(db):
    """Extraction is the thing a re-parse exists to avoid."""
    run = _seed_run(db)
    with pytest.raises(RetryNotAllowedError, match="not supported yet"):
        CatalogueSubmissionService(db).reparse(UUID(run.run_uuid), from_stage="extraction")


def test_an_unknown_stage_names_the_ones_that_work(db):
    run = _seed_run(db)
    with pytest.raises(RetryNotAllowedError, match="supported: conformance"):
        CatalogueSubmissionService(db).reparse(UUID(run.run_uuid), from_stage="banana")


def test_a_missing_run_is_a_not_found(db):
    with pytest.raises(SubmissionNotFoundError):
        CatalogueSubmissionService(db).reparse(uuid4())


def test_a_completed_run_can_be_reparsed_repeatedly(db):
    """Unlike retry, which is once-only and failure-only — you iterate on a contract."""
    run = _seed_run(db)
    service = CatalogueSubmissionService(db)
    first = service.reparse(UUID(run.run_uuid))
    db.commit()
    second = service.reparse(UUID(run.run_uuid))
    db.commit()
    assert first.ingestion_run_id != second.ingestion_run_id


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

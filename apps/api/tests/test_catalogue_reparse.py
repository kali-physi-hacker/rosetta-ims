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


# ── Re-parse under a sibling format (mixed-layout documents) ─────────────────
#
# One submission carries one contract, so a mixed-layout document's other
# pages dead-letter under the recorded format no matter how often they are
# re-driven (proven live: run 1382e559's 24 wet-can rows survived three
# attempts untouched). The override re-reads the SAME stored evidence under a
# sibling SUPPORTED contract of the same supplier — recorded on the child run,
# because everything downstream reads the run's record, never a parameter.

from schemas.catalogue_pipeline.supplier_contracts.suppliers.kangaroo_pet_nutrition import (  # noqa: E402
    KANGAROO_PET_NUTRITION_UNIT_PRICE_LIST_V1 as _UNIT,
)
from services import supplier_source_contract_runtime as _contract_runtime  # noqa: E402

# Since the Kangaroo merge (2026-08-25) no supplier carries TWO supported
# contracts in the registry, so the positive override path is exercised with a
# registry stub: the real merged declaration wearing a sibling id. The refusal
# paths below still hit the real registry.
_SIBLING_ID = "kangaroo_pet_nutrition.sibling_layout.v1"


def _fake_sibling_runtime():
    sibling = _UNIT.model_copy(update={
        "contract_id": _SIBLING_ID,
        "format_name": "Kangaroo sibling layout (test double)",
    })
    return _contract_runtime.SupplierSourceRuntimeContract(sibling)


def _stub_sibling_resolution(monkeypatch):
    sibling = _fake_sibling_runtime()
    real_resolve = _contract_runtime.resolve_supplier_contract

    def fake_resolve(*, supplier_id, contract_id=None, contract_version=None):
        if contract_id == _SIBLING_ID:
            assert int(supplier_id) == 81
            return sibling
        return real_resolve(
            supplier_id=supplier_id, contract_id=contract_id, contract_version=contract_version
        )

    monkeypatch.setattr(_contract_runtime, "resolve_supplier_contract", fake_resolve)
    return sibling


def _seed_kangaroo_run(db, *, observations=2):
    """A supplier-81 run recorded under the unit contract, matching the
    registry's declared document type and a stored PDF source."""
    supplier = models.Supplier(id=81, code="KANGAR", name="Kangaroo Pet Nutrition", created_at="2026-01-01T00:00:00")
    db.add(supplier)
    db.flush()
    imp = models.CatalogueImport(supplier_id=supplier.id, filename="kpn.pdf", imported_at="2026-01-01T00:00:00")
    db.add(imp)
    db.flush()
    document_type = _UNIT.document_type.value
    source = models.CatalogueSourceDocument(
        legacy_import_id=imp.id, supplier_id=supplier.id, filename="kpn.pdf",
        source_format="PDF", source_ref="stored/kpn.pdf", source_checksum="abc",
        received_at="2026-01-01T00:00:00",
        supplier_source_contract_id=_UNIT.contract_id,
        supplier_source_contract_version=_UNIT.contract_version,
        document_type=document_type, byte_size=1024, page_count=2, created_at="2026-01-01T00:00:00",
    )
    db.add(source)
    db.flush()
    run = models.IngestionRun(
        run_uuid=str(uuid4()), source_document_id=imp.id, catalogue_source_document_id=source.id,
        supplier_id=supplier.id, contract_version="catalogue.extraction_profile.v1",
        supplier_source_contract_id=_UNIT.contract_id,
        supplier_source_contract_version=_UNIT.contract_version,
        document_type=document_type, extractor_name="queued-submission", extractor_version="v1",
        status="completed_with_warnings", created_at="2026-01-01T00:00:00",
        completed_at="2026-01-01T00:05:00",
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
            source_metadata_json=json.dumps({"observation_key": f"page:1:row:{index}"}),
            created_at="2026-01-01T00:01:00",
        ))
    db.flush()
    return run


def test_a_reparse_can_override_to_a_sibling_supported_contract(db, monkeypatch):
    _stub_sibling_resolution(monkeypatch)
    run = _seed_kangaroo_run(db)
    result = CatalogueSubmissionService(db).reparse(
        UUID(run.run_uuid), submitted_by="reviewer", contract_id=_SIBLING_ID
    )
    db.commit()

    child = db.query(models.IngestionRun).filter_by(run_uuid=str(result.ingestion_run_id)).one()
    assert child.supplier_source_contract_id == _SIBLING_ID
    assert child.supplier_source_contract_version == "v1"
    metrics = json.loads(child.metrics)
    assert metrics["reparse_of"] == run.run_uuid
    assert metrics["reparse_contract_override"] == _SIBLING_ID

    # The raw stage rebuilds identity from the child's own record — so the
    # override IS what the whole downstream flow interprets under.
    outcome = reparse.reparse_raw_stage(db, ingestion_run_id=result.ingestion_run_id)
    assert outcome.run_identity.contract_id == _SIBLING_ID


def test_a_contract_override_refuses_what_the_registry_refuses(db):
    run = _seed_kangaroo_run(db)
    service = CatalogueSubmissionService(db)

    with pytest.raises(RetryNotAllowedError, match="no-such-contract"):
        service.reparse(UUID(run.run_uuid), contract_id="no-such-contract.v1")

    # A gated sibling is not selectable — same rule as at upload time.
    with pytest.raises(RetryNotAllowedError, match="not SUPPORTED"):
        service.reparse(UUID(run.run_uuid), contract_id="kangaroo_pet_nutrition.catalogue_bundle.v1")

    # Another supplier's contract never interprets this supplier's pages.
    with pytest.raises(RetryNotAllowedError):
        service.reparse(UUID(run.run_uuid), contract_id="alfamedic.price_list.v1")

    # Nothing was queued by any refusal.
    children = db.query(models.IngestionRun).filter(models.IngestionRun.parent_run_id == run.id).count()
    assert children == 0


def test_the_capture_guard_tolerates_a_recorded_contract_override(db, monkeypatch):
    """The source document records what it was UPLOADED under; an override
    child records what IT interprets under. The capture guard accepts that
    sanctioned divergence — and still refuses the same divergence when the
    run does not carry the override marker, which is accidental drift."""
    from services import catalogue_pipeline_stages as stages

    _stub_sibling_resolution(monkeypatch)
    run = _seed_kangaroo_run(db)
    result = CatalogueSubmissionService(db).reparse(
        UUID(run.run_uuid), contract_id=_SIBLING_ID
    )
    db.commit()

    child = db.query(models.IngestionRun).filter_by(run_uuid=str(result.ingestion_run_id)).one()
    source = db.query(models.CatalogueSourceDocument).one()
    command = stages.CaptureExtractedEvidenceCommand(
        ingestion_run_id=UUID(child.run_uuid),
        supplier_catalogue_id=UUID(source.supplier_catalogue_uuid),
        source_file_id=UUID(source.source_file_uuid),
        supplier_id=81,
        observations=(),
        contract_id=child.supplier_source_contract_id,
        contract_version=child.supplier_source_contract_version,
    )
    _, _, resolved = stages._resolve_run_source_contract(db, command)
    assert resolved.slug == _SIBLING_ID

    metrics = json.loads(child.metrics)
    del metrics["reparse_contract_override"]
    child.metrics = json.dumps(metrics)
    db.flush()
    with pytest.raises(stages.SupplierContractMismatch, match="Source Document"):
        stages._resolve_run_source_contract(db, command)

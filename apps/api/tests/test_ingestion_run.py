"""CIS-104.1 — Catalogue Ingestion Run model.

Proves the model's core rules: a run always references its source document,
retries create a new row linked via parent_run_id rather than mutating the
original, a run cannot be its own parent, status defaults to queued, and the
metrics JSON blob round-trips through its typed shape. Isolated v2 table —
these tests never touch routers/v1 or the live extraction flow.

Runnable directly (`python tests/test_ingestion_run.py`) or under pytest.
"""
import os
import tempfile
from datetime import datetime, timezone

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

import database  # noqa: E402
import models    # noqa: E402
import v2.models as v2_models  # noqa: E402
from v2.models import IngestionRun, IngestionRunStatus, IngestionRunMetrics  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


def _now():
    return datetime.now(timezone.utc).isoformat()


def _seed_supplier(session, code="TEST"):
    supplier = models.Supplier(code=code, name="Test Supplier", created_at=_now())
    session.add(supplier)
    session.commit()
    return supplier


def _seed_catalogue_import(session, supplier):
    imp = models.CatalogueImport(
        supplier_id=supplier.id,
        filename="test.pdf",
        format="pdf",
        imported_at=_now(),
        status="pending",
    )
    session.add(imp)
    session.commit()
    return imp


def _make_run(session, source_document, supplier, **overrides):
    kwargs = dict(
        source_document_id=source_document.id,
        supplier_id=supplier.id,
        contract_version="catalogue.extraction_profile.v1",
        extractor_name="claude-haiku",
        extractor_version="4.5-20251001",
        started_at=_now(),
        created_at=_now(),
    )
    kwargs.update(overrides)
    run = IngestionRun(**kwargs)
    session.add(run)
    session.commit()
    return run


def test_creates_run_with_required_fields():
    session = database.SessionLocal()
    try:
        supplier = _seed_supplier(session, "T1")
        imp = _seed_catalogue_import(session, supplier)
        run = _make_run(session, imp, supplier)

        assert run.id is not None
        assert run.source_document_id == imp.id
        assert run.extractor_name == "claude-haiku"
        assert run.extractor_version == "4.5-20251001"
    finally:
        session.close()


def test_defaults_to_queued_status():
    session = database.SessionLocal()
    try:
        supplier = _seed_supplier(session, "T2")
        imp = _seed_catalogue_import(session, supplier)
        run = _make_run(session, imp, supplier)

        session.refresh(run)
        assert run.status == IngestionRunStatus.QUEUED.value
    finally:
        session.close()


def test_queued_run_can_have_no_started_at_timestamp():
    session = database.SessionLocal()
    try:
        supplier = _seed_supplier(session, "T2Q")
        imp = _seed_catalogue_import(session, supplier)
        run = _make_run(session, imp, supplier, started_at=None)

        session.refresh(run)
        assert run.status == IngestionRunStatus.QUEUED.value
        assert run.started_at is None
        assert run.completed_at is None
    finally:
        session.close()


def test_retry_creates_new_row_linked_via_parent_run_id():
    session = database.SessionLocal()
    try:
        supplier = _seed_supplier(session, "T3")
        imp = _seed_catalogue_import(session, supplier)

        first = _make_run(session, imp, supplier, status=IngestionRunStatus.FAILED.value,
                           completed_at=_now())
        retry = _make_run(session, imp, supplier, parent_run_id=first.id)

        assert retry.id != first.id
        assert retry.parent_run_id == first.id
        # the original attempt is untouched — still failed, not overwritten
        session.refresh(first)
        assert first.status == IngestionRunStatus.FAILED.value
        assert first.parent_run_id is None
    finally:
        session.close()


def test_self_reference_as_parent_is_rejected():
    session = database.SessionLocal()
    try:
        supplier = _seed_supplier(session, "T4")
        imp = _seed_catalogue_import(session, supplier)
        run = _make_run(session, imp, supplier)

        try:
            run.parent_run_id = run.id
            raised = False
        except ValueError:
            raised = True
        assert raised, "IngestionRun.parent_run_id must reject referencing its own id"
    finally:
        session.close()


def test_relationships_read_existing_v1_rows():
    session = database.SessionLocal()
    try:
        supplier = _seed_supplier(session, "T5")
        imp = _seed_catalogue_import(session, supplier)
        run = _make_run(session, imp, supplier)

        session.refresh(run)
        assert run.source_document.filename == "test.pdf"
        assert run.supplier.code == "T5"
    finally:
        session.close()


def test_metrics_json_round_trip():
    metrics = IngestionRunMetrics(rows_seen=42, warnings_count=3, rejected_count=1)
    raw = metrics.to_json()

    restored = IngestionRunMetrics.from_json(raw)
    assert restored.rows_seen == 42
    assert restored.warnings_count == 3
    assert restored.rejected_count == 1
    assert restored.confidence_avg is None

    assert IngestionRunMetrics.from_json(None).rows_seen is None


if __name__ == "__main__":
    test_creates_run_with_required_fields()
    test_defaults_to_queued_status()
    test_queued_run_can_have_no_started_at_timestamp()
    test_retry_creates_new_row_linked_via_parent_run_id()
    test_self_reference_as_parent_is_rejected()
    test_relationships_read_existing_v1_rows()
    test_metrics_json_round_trip()
    print("\n✅ IngestionRun model behaves correctly — all checks passed")

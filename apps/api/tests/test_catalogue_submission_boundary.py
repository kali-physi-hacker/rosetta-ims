"""v2 catalogue submission boundary tests."""

from __future__ import annotations

import os
import tempfile
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from sqlalchemy import create_engine, text

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

import database  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402
import v2.models as v2_models  # noqa: E402
from dependencies import require_user  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services import catalogue_submission, extraction_service, tagging_service  # noqa: E402
from services.catalogue_submission import (  # noqa: E402
    CatalogueSubmissionCommand,
    CatalogueSubmissionService,
    EmptyUploadError,
    SubmissionPersistenceError,
    SubmissionIdempotencyConflict,
    UnsupportedSourceTypeError,
    UploadTooLargeError,
)


models.Base.metadata.create_all(bind=database.engine)
database.run_migrations(database.engine)


class _Admin:
    id = 99
    username = "submission-admin"
    display_name = "Submission Admin"
    role = "admin"


@pytest.fixture(autouse=True)
def _auth(monkeypatch):
    previous_root = main.app.dependency_overrides.get(require_user)
    previous_v2 = main.api_v2.dependency_overrides.get(require_user)
    main.app.dependency_overrides[require_user] = lambda: _Admin()
    main.api_v2.dependency_overrides[require_user] = lambda: _Admin()
    monkeypatch.setattr(extraction_service, "extract", lambda *a, **k: pytest.fail("v2 submission must not extract"))
    monkeypatch.setattr(tagging_service, "suggest_tags", lambda *a, **k: pytest.fail("v2 submission must not tag"))
    yield
    if previous_root is None:
        main.app.dependency_overrides.pop(require_user, None)
    else:
        main.app.dependency_overrides[require_user] = previous_root
    if previous_v2 is None:
        main.api_v2.dependency_overrides.pop(require_user, None)
    else:
        main.api_v2.dependency_overrides[require_user] = previous_v2


@pytest.fixture()
def db():
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


@pytest.fixture()
def client(tmp_path, monkeypatch, db):
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CATALOGUE_SUBMISSION_MAX_BYTES", str(1024 * 1024))
    return TestClient(main.app)


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


def _pdf(name="hills.pdf", body=b"%PDF-1.4\n% fixture\n"):
    return {"file": (name, body, "application/pdf")}


def _command(stream: BytesIO, *, key: str | None = None, supplier_id: int = 14, filename: str = "hills.pdf"):
    return CatalogueSubmissionCommand(
        supplier_id=supplier_id,
        original_filename=filename,
        content_type="application/pdf",
        stream=stream,
        idempotency_key=key,
        submitted_by="test",
    )


def test_submission_service_registers_source_import_and_queued_run(db, tmp_path):
    service = CatalogueSubmissionService(db, upload_root=tmp_path, max_upload_bytes=1024)

    result = service.submit(_command(BytesIO(b"%PDF-1.4\nsample"), key="submit-1"))

    assert result.status == "queued"
    assert result.contract_id == "hills.price_list.v1"
    assert result.contract_version == "v1"
    assert result.document_type == "PRICE_LIST"
    assert db.query(models.CatalogueImport).count() == 1
    assert db.query(models.CatalogueItem).count() == 0
    assert db.query(v2_models.CatalogueSourceDocument).count() == 1
    assert db.query(v2_models.IngestionRun).count() == 1
    assert db.query(v2_models.CatalogueRawObservation).count() == 0
    assert db.query(v2_models.CatalogueStagingItem).count() == 0
    assert db.query(v2_models.CatalogueMasteringCandidate).count() == 0
    assert db.query(v2_models.CatalogueServingPublication).count() == 0

    source = db.query(v2_models.CatalogueSourceDocument).one()
    run = db.query(v2_models.IngestionRun).one()
    legacy = db.query(models.CatalogueImport).one()

    assert source.source_checksum and len(source.source_checksum) == 64
    assert source.source_ref.startswith("v2/")
    assert source.filename == "hills.pdf"
    assert Path(tmp_path / source.source_ref).exists()
    assert legacy.status == "queued"
    assert legacy.item_count == 0
    assert legacy.source_ref == source.source_ref
    assert run.run_uuid == str(result.ingestion_run_id)
    assert run.status == "queued"
    assert run.started_at is None
    assert run.completed_at is None
    assert run.items_extracted is None
    assert run.supplier_source_contract_id == "hills.price_list.v1"


def test_submission_idempotency_replays_same_result_and_conflicts_on_changed_material(db, tmp_path):
    service = CatalogueSubmissionService(db, upload_root=tmp_path, max_upload_bytes=1024)

    first = service.submit(_command(BytesIO(b"%PDF-1.4\nsame"), key="same-key"))
    second = service.submit(_command(BytesIO(b"%PDF-1.4\nsame"), key="same-key"))

    assert second == first
    assert db.query(models.CatalogueImport).count() == 1
    assert db.query(v2_models.CatalogueSourceDocument).count() == 1
    assert db.query(v2_models.IngestionRun).count() == 1
    assert len(list((tmp_path / "v2").iterdir())) == 1

    with pytest.raises(SubmissionIdempotencyConflict):
        service.submit(_command(BytesIO(b"%PDF-1.4\nchanged"), key="same-key"))
    assert db.query(v2_models.IngestionRun).count() == 1
    assert len(list((tmp_path / "v2").iterdir())) == 1


def test_submission_without_idempotency_key_creates_distinct_runs(db, tmp_path):
    service = CatalogueSubmissionService(db, upload_root=tmp_path, max_upload_bytes=1024)

    first = service.submit(_command(BytesIO(b"%PDF-1.4\nsame")))
    second = service.submit(_command(BytesIO(b"%PDF-1.4\nsame")))

    assert second.ingestion_run_id != first.ingestion_run_id
    assert db.query(models.CatalogueImport).count() == 2
    assert db.query(v2_models.CatalogueSourceDocument).count() == 2
    assert db.query(v2_models.IngestionRun).count() == 2


def test_submission_file_validation_and_cleanup(db, tmp_path):
    service = CatalogueSubmissionService(db, upload_root=tmp_path, max_upload_bytes=12)

    with pytest.raises(EmptyUploadError):
        service.submit(_command(BytesIO(b""), key="empty"))
    with pytest.raises(UploadTooLargeError):
        service.submit(_command(BytesIO(b"%PDF-1.4\nthis is too large"), key="large"))
    with pytest.raises(UnsupportedSourceTypeError):
        service.submit(_command(BytesIO(b"%PDF-1.4\nsample"), key="txt", filename="fake.txt"))
    with pytest.raises(UnsupportedSourceTypeError):
        service.submit(_command(BytesIO(b"not a pdf"), key="bad-signature"))

    assert db.query(models.CatalogueImport).count() == 0
    if (tmp_path / "v2").exists():
        assert list((tmp_path / "v2").glob("*")) == []


def test_submission_storage_failure_commits_no_database_rows(db, tmp_path):
    blocked_root = tmp_path / "not-a-directory"
    blocked_root.write_text("file")
    service = CatalogueSubmissionService(db, upload_root=blocked_root, max_upload_bytes=1024)

    with pytest.raises(catalogue_submission.StorageUnavailableError):
        service.submit(_command(BytesIO(b"%PDF-1.4\nsample"), key="storage-failure"))

    assert db.query(models.CatalogueImport).count() == 0
    assert db.query(v2_models.IngestionRun).count() == 0


def test_submission_database_failure_cleans_new_file(db, tmp_path, monkeypatch):
    service = CatalogueSubmissionService(db, upload_root=tmp_path, max_upload_bytes=1024)

    def fail_commit():
        raise RuntimeError("commit failed")

    with monkeypatch.context() as patch:
        patch.setattr(db, "commit", fail_commit)
        with pytest.raises(SubmissionPersistenceError):
            service.submit(_command(BytesIO(b"%PDF-1.4\nsample"), key="db-failure"))

    if (tmp_path / "v2").exists():
        assert list((tmp_path / "v2").glob("*")) == []


def test_v2_submission_endpoint_accepts_and_status_polls(client, db, tmp_path):
    response = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "14"},
        files=_pdf(),
        headers={"Idempotency-Key": "api-submit-1"},
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued"
    assert body["contract_id"] == "hills.price_list.v1"
    assert body["contract_version"] == "v1"
    assert body["status_url"] == f"/v2/catalogues/ingestions/{body['ingestion_run_id']}"

    status_response = client.get(body["status_url"])
    assert status_response.status_code == 200, status_response.text
    status_body = status_response.json()
    assert status_body["ingestion_run_id"] == body["ingestion_run_id"]
    assert status_body["supplier_catalogue_id"] == body["supplier_catalogue_id"]
    assert status_body["source_file_id"] == body["source_file_id"]
    assert status_body["started_at"] is None
    assert status_body["completed_at"] is None

    source = db.query(v2_models.CatalogueSourceDocument).one()
    assert Path(tmp_path / "uploads" / source.source_ref).exists()
    assert db.query(models.CatalogueItem).count() == 0


def test_v2_submission_endpoint_contract_and_file_errors(client):
    partial = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "14", "contract_id": "hills.price_list.v1"},
        files=_pdf(),
    )
    assert partial.status_code == 422
    assert partial.json()["detail"]["code"] == "INVALID_CONTRACT_PARAMETERS"

    unknown_version = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "14", "contract_id": "hills.price_list.v1", "contract_version": "v2"},
        files=_pdf(),
    )
    assert unknown_version.status_code == 422

    mismatch = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "1", "contract_id": "hills.price_list.v1", "contract_version": "v1"},
        files=_pdf(),
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "SUPPLIER_CONTRACT_MISMATCH"

    unsupported = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "91", "contract_id": "vetapet.vet_price_list.v1", "contract_version": "v1"},
        files=_pdf("vetapet.pdf"),
    )
    assert unsupported.status_code == 422

    unsupported_type = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "14"},
        files={"file": ("fake.txt", b"%PDF-1.4\nsample", "application/pdf")},
    )
    assert unsupported_type.status_code == 415

    traversal = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "14"},
        files={"file": ("../evil.pdf", b"%PDF-1.4\nsample", "application/pdf")},
    )
    assert traversal.status_code == 400


def test_v2_submission_endpoint_idempotency_conflict_and_unknown_status(client):
    first = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "14"},
        files=_pdf(body=b"%PDF-1.4\nsame"),
        headers={"Idempotency-Key": "api-same"},
    )
    replay = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "14"},
        files=_pdf(body=b"%PDF-1.4\nsame"),
        headers={"Idempotency-Key": "api-same"},
    )
    conflict = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "14"},
        files=_pdf(body=b"%PDF-1.4\nchanged"),
        headers={"Idempotency-Key": "api-same"},
    )

    assert first.status_code == 202
    assert replay.status_code == 202
    assert replay.json()["ingestion_run_id"] == first.json()["ingestion_run_id"]
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"

    missing = client.get("/v2/catalogues/ingestions/99999999-9999-4999-8999-999999999999")
    assert missing.status_code == 404


def test_v2_submission_auth_and_openapi_contract(db, tmp_path, monkeypatch):
    main.app.dependency_overrides.pop(require_user, None)
    main.api_v2.dependency_overrides.pop(require_user, None)
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    client = TestClient(main.app)

    unauthenticated = client.post(
        "/v2/catalogues/ingestions",
        data={"supplier_id": "14"},
        files=_pdf(),
    )
    assert unauthenticated.status_code == 401

    main.app.dependency_overrides[require_user] = lambda: _Admin()
    main.api_v2.dependency_overrides[require_user] = lambda: _Admin()
    schema = client.get("/v2/openapi.json").json()
    assert "/catalogues/ingestions" in schema["paths"]
    assert "/catalogues/ingestions/{run_uuid}" in schema["paths"]
    root_schema = client.get("/openapi.json", follow_redirects=True).json()
    assert "/catalogues/ingestions" not in root_schema["paths"]


def test_submission_migration_relaxes_existing_started_at_not_null(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'old_run.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE suppliers (id INTEGER PRIMARY KEY, code TEXT UNIQUE NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL)"))
        conn.execute(text("CREATE TABLE catalogue_imports (id INTEGER PRIMARY KEY, supplier_id INTEGER, filename TEXT NOT NULL, format TEXT, imported_at TEXT NOT NULL, status TEXT NOT NULL, item_count INTEGER)"))
        conn.execute(text("""
            CREATE TABLE catalogue_ingestion_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_document_id INTEGER NOT NULL,
                supplier_id INTEGER,
                contract_version TEXT,
                extractor_name TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                parent_run_id INTEGER,
                status TEXT NOT NULL DEFAULT 'queued',
                started_at TEXT NOT NULL,
                completed_at TEXT,
                items_extracted INTEGER,
                metrics TEXT,
                error_summary TEXT,
                created_at TEXT NOT NULL
            )
        """))
        conn.execute(text("INSERT INTO suppliers VALUES (14, 'HILLS', 'Hill''s', '2026-07-23T00:00:00+00:00')"))
        conn.execute(text("INSERT INTO catalogue_imports VALUES (1, 14, 'hills.pdf', 'pdf', '2026-07-23T00:00:00+00:00', 'queued', 0)"))
        conn.execute(text("""
            INSERT INTO catalogue_ingestion_runs (
                source_document_id, supplier_id, contract_version, extractor_name, extractor_version,
                status, started_at, created_at
            )
            VALUES (1, 14, 'catalogue.extraction_profile.v1', 'old', 'v1', 'queued',
                    '2026-07-23T00:00:00+00:00', '2026-07-23T00:00:00+00:00')
        """))

    database.run_migrations(engine)

    inspector = sa.inspect(engine)
    started_at = next(column for column in inspector.get_columns("catalogue_ingestion_runs") if column["name"] == "started_at")
    assert started_at["nullable"] is True
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO catalogue_ingestion_runs (
                run_uuid, source_document_id, supplier_id, extractor_name, extractor_version, status, started_at, created_at
            )
            VALUES ('99999999-9999-4999-8999-999999999999', 1, 14, 'queued', 'v1', 'queued', NULL,
                    '2026-07-23T00:00:00+00:00')
        """))

"""Regression coverage for the legacy catalogue-ingestion-run SQLite schema."""

from sqlalchemy import create_engine, inspect, text

import database


def test_legacy_ingestion_runs_are_upgraded_without_losing_history(tmp_path):
    test_engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with test_engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE suppliers (
                id INTEGER PRIMARY KEY,
                name TEXT
            )
        """))
        conn.execute(text("""
            CREATE TABLE catalogue_imports (
                id INTEGER PRIMARY KEY,
                supplier_id INTEGER REFERENCES suppliers(id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE catalogue_ingestion_runs (
                id INTEGER PRIMARY KEY,
                catalogue_import_id INTEGER NOT NULL REFERENCES catalogue_imports(id),
                parent_run_id INTEGER,
                extraction_profile_id TEXT NOT NULL,
                extraction_profile_version TEXT NOT NULL,
                extractor_name TEXT,
                extractor_version TEXT,
                model_name TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                metrics_json TEXT,
                error_code TEXT,
                error_message TEXT,
                error_json TEXT,
                created_by TEXT
            )
        """))
        conn.execute(text("INSERT INTO suppliers VALUES (7, 'Legacy Supplier')"))
        conn.execute(text("INSERT INTO catalogue_imports VALUES (11, 7)"))
        conn.execute(text("""
            INSERT INTO catalogue_ingestion_runs (
                id, catalogue_import_id, extraction_profile_id,
                extraction_profile_version, model_name, status, created_at,
                metrics_json, error_code, error_message, error_json, created_by
            ) VALUES (
                23, 11, 'legacy-profile', '1.0', 'legacy-model', 'succeeded',
                '2026-07-01T10:00:00Z', '{"average": 0.9}', 'old-code',
                'old message', '{"page": 2}', 'migration-test'
            )
        """))

    database.run_migrations(test_engine)

    columns = {column["name"] for column in inspect(test_engine).get_columns(
        "catalogue_ingestion_runs"
    )}
    assert database._CATALOGUE_INGESTION_RUNS_COLUMNS <= columns
    assert "catalogue_import_id" not in columns

    with test_engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM catalogue_ingestion_runs WHERE id = 23"
        )).mappings().one()
        assert row["source_asset_id"] == 11
        assert row["supplier_id"] == 7
        assert row["extractor_name"] == "legacy-model"
        assert row["status"] == "completed"
        assert row["started_at"] == "2026-07-01T10:00:00Z"
        assert row["confidence_metrics"] == '{"average": 0.9}'
        assert row["error_type"] == "old-code"
        assert row["error_details"] == '{"page": 2}'

    # The migration is safe to run on every application startup.
    database.run_migrations(test_engine)
    with test_engine.connect() as conn:
        assert conn.scalar(text("SELECT COUNT(*) FROM catalogue_ingestion_runs")) == 1

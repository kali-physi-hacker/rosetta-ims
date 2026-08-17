"""A person can replace what extraction misread, and every re-drive reads it.

Driven through the golden replay: corrections operate on observations a real
run persisted, not hand-built rows. The seam that matters downstream is that
re-parse and retrigger children rehydrate evidence from ``raw_cells_json``
(load_stored_evidence), so a correction persisted there — on the viewed row
AND its extraction-source twin — is exactly what a re-drive re-reads.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

import json  # noqa: E402
from uuid import UUID, uuid4  # noqa: E402

import pytest  # noqa: E402

import models  # noqa: E402
from services import catalogue_evidence_corrections as corrections  # noqa: E402
from tests.test_catalogue_golden_suppliers import (  # noqa: E402, F401 — db fixture
    _load_expected,
    _replay_set,
    db,
    golden_set,
)


def _replayed_run(db, monkeypatch) -> models.IngestionRun:
    spec = golden_set("kpn_trading")
    _replay_set(db, monkeypatch, spec, only_skus=set(_load_expected(spec.path)), refused={})
    return db.query(models.IngestionRun).order_by(models.IngestionRun.id.desc()).first()


def _cell_observation(db, run_uuid: str) -> models.CatalogueExtractedEvidence:
    row = (
        db.query(models.CatalogueExtractedEvidence)
        .filter_by(ingestion_run_uuid=run_uuid)
        .filter(models.CatalogueExtractedEvidence.raw_cells_json.isnot(None))
        .filter(models.CatalogueExtractedEvidence.raw_cells_json != "[]")
        .first()
    )
    assert row is not None, "the replay must persist cell observations"
    return row


def _named_cell(row: models.CatalogueExtractedEvidence) -> tuple[str, object]:
    cell = next(
        c for c in json.loads(row.raw_cells_json)
        if c.get("column_name") and c.get("raw_value") not in (None, "")
    )
    return cell["column_name"], cell["raw_value"]


def test_a_correction_replaces_the_cell_and_stamps_the_original(db, monkeypatch):
    run = _replayed_run(db, monkeypatch)
    row = _cell_observation(db, run.run_uuid)
    column, original = _named_cell(row)

    result = corrections.correct_evidence(
        db,
        run_uuid=UUID(run.run_uuid),
        raw_observation_uuid=UUID(row.raw_observation_uuid),
        cells={column: "HK$123.4"},
        reason="the page prints HK$123.4 — the scan smudged the digit",
        corrected_by="tester",
    )

    assert result.corrected_columns == (column,)
    assert result.source_run_id is None, "an original run has no separate extraction source"
    stored = (
        db.query(models.CatalogueExtractedEvidence)
        .filter_by(raw_observation_uuid=row.raw_observation_uuid)
        .one()
    )
    cells = {c["column_name"]: c.get("raw_value") for c in json.loads(stored.raw_cells_json) if c.get("column_name")}
    assert cells[column] == "HK$123.4"
    (stamp,) = json.loads(stored.source_metadata_json)["human_corrections"]
    assert stamp["corrected_by"] == "tester"
    assert stamp["changes"][column] == {"from": original, "to": "HK$123.4"}


def test_unknown_column_no_change_and_bad_reason_refuse(db, monkeypatch):
    run = _replayed_run(db, monkeypatch)
    row = _cell_observation(db, run.run_uuid)
    column, original = _named_cell(row)
    before = row.raw_cells_json

    with pytest.raises(corrections.EvidenceCorrectionError, match="not on this observation"):
        corrections.correct_evidence(
            db, run_uuid=UUID(run.run_uuid), raw_observation_uuid=UUID(row.raw_observation_uuid),
            cells={"NO SUCH COLUMN": "x"}, reason="a fine reason", corrected_by="tester",
        )
    with pytest.raises(corrections.EvidenceCorrectionError, match="no cell changed"):
        corrections.correct_evidence(
            db, run_uuid=UUID(run.run_uuid), raw_observation_uuid=UUID(row.raw_observation_uuid),
            cells={column: str(original)}, reason="a fine reason", corrected_by="tester",
        )
    with pytest.raises(corrections.EvidenceCorrectionError, match="reason"):
        corrections.correct_evidence(
            db, run_uuid=UUID(run.run_uuid), raw_observation_uuid=UUID(row.raw_observation_uuid),
            cells={column: "HK$9"}, reason="  ", corrected_by="tester",
        )
    db.rollback()
    assert (
        db.query(models.CatalogueExtractedEvidence)
        .filter_by(raw_observation_uuid=row.raw_observation_uuid)
        .one()
        .raw_cells_json
        == before
    ), "a refused correction must change nothing"


def test_a_correction_on_a_reparse_child_lands_on_the_source_too(db, monkeypatch):
    parent = _replayed_run(db, monkeypatch)
    source_row = _cell_observation(db, parent.run_uuid)
    column, original = _named_cell(source_row)

    child = models.IngestionRun(
        run_uuid=str(uuid4()),
        source_document_id=parent.source_document_id,
        supplier_id=parent.supplier_id,
        extractor_name=parent.extractor_name,
        extractor_version=parent.extractor_version,
        parent_run_id=parent.id,
        status=parent.status,
        metrics=json.dumps({"reparse_of": parent.run_uuid}),
        created_at=parent.created_at,
    )
    db.add(child)
    db.flush()
    copy = models.CatalogueExtractedEvidence(
        raw_observation_uuid=str(uuid4()),
        contract_version=source_row.contract_version,
        ingestion_run_uuid=child.run_uuid,
        supplier_catalogue_uuid=source_row.supplier_catalogue_uuid,
        source_file_uuid=source_row.source_file_uuid,
        extraction_profile_id=source_row.extraction_profile_id,
        extraction_profile_version=source_row.extraction_profile_version,
        source_location_json=source_row.source_location_json,
        page_number=source_row.page_number,
        sheet_name=source_row.sheet_name,
        row_number=source_row.row_number,
        source_object_key=source_row.source_object_key,
        raw_text=source_row.raw_text,
        raw_cells_json=source_row.raw_cells_json,
        extraction_method=source_row.extraction_method,
        captured_at=source_row.captured_at,
        source_metadata_json=source_row.source_metadata_json,
        created_at=source_row.created_at,
    )
    db.add(copy)
    db.commit()

    result = corrections.correct_evidence(
        db,
        run_uuid=UUID(child.run_uuid),
        raw_observation_uuid=UUID(copy.raw_observation_uuid),
        cells={column: "HK$777"},
        reason="fixed while reviewing the re-parse",
        corrected_by="tester",
    )

    assert result.source_run_id == parent.run_uuid
    assert result.source_raw_observation_id == source_row.raw_observation_uuid
    for uuid_ in (copy.raw_observation_uuid, source_row.raw_observation_uuid):
        stored = db.query(models.CatalogueExtractedEvidence).filter_by(raw_observation_uuid=uuid_).one()
        cells = {c["column_name"]: c.get("raw_value") for c in json.loads(stored.raw_cells_json) if c.get("column_name")}
        assert cells[column] == "HK$777", f"{uuid_} must carry the correction"
        stamps = json.loads(stored.source_metadata_json)["human_corrections"]
        assert stamps[-1]["changes"][column]["to"] == "HK$777"

"""Golden supplier-file coverage: REAL recorded provider envelopes, offline.

The fixtures under ``tests/fixtures/catalogue_pipeline/golden/`` are actual
Gemini vision envelopes recorded from live extraction runs of real supplier
files (see each set's ``meta.json`` and ``scripts/record_golden_envelopes.py``).
Replaying them through the FULL pipeline — submission, raw, extraction parsing,
conformance, validation, mastering — proves end-to-end behaviour against real
provider output without network access or provider nondeterminism.
"""

from __future__ import annotations

import json
import os
import tempfile
from io import BytesIO
from pathlib import Path

import pytest
import pypdf

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

import database  # noqa: E402
import models  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from services import catalogue_evidence_extraction as extraction  # noqa: E402
from services.catalogue_submission import CatalogueSubmissionCommand, CatalogueSubmissionService  # noqa: E402


models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)

GOLDEN_ROOT = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "golden"
HILLS_CLASSIC = GOLDEN_ROOT / "hills_classic"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CATALOGUE_ORCHESTRATION_MAX_SOURCE_BYTES", str(4 * 1024 * 1024))
    session = database.SessionLocal()
    try:
        _reset(session)
        if session.get(models.Supplier, 14) is None:
            session.add(models.Supplier(id=14, code="HILLS", name="Hill's", created_at="2026-07-29T00:00:00+00:00"))
            session.commit()
        yield session
        session.rollback()
        _reset(session)
    finally:
        session.close()


def _reset(session):
    for model in (
        models.CatalogueSubmissionIdempotency,
        models.CatalogueRawStageAttempt,
        models.CatalogueExtractionAttempt,
        models.CatalogueServingPublication,
        models.CatalogueSupplierMbbTerm,
        models.CatalogueSupplierPrice,
        models.CataloguePackagingConfiguration,
        models.SupplierOffering,
        models.CatalogueReviewDecision,
        models.CatalogueMasteringCandidate,
        models.CatalogueValidationIssue,
        models.CatalogueNormalizedRowEvidence,
        models.CatalogueNormalizedRow,
        models.CatalogueExtractedEvidence,
        models.IngestionRun,
        models.CatalogueSourceDocument,
    ):
        session.query(model).delete()
    session.query(models.CatalogueImport).delete()
    session.commit()


def _blank_pdf(page_count: int) -> bytes:
    writer = pypdf.PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _install_golden_replay(monkeypatch, envelope_paths: list[Path]):
    """Replay recorded envelopes in page order through the provider seam.

    Concurrency is pinned to 1 so the thread pool preserves submission order —
    call N receives page N's recorded envelope, exactly as live extraction
    would have.
    """

    monkeypatch.setattr(extraction, "_VISION_CONCURRENCY", 1)
    payloads = [path.read_text(encoding="utf-8") for path in envelope_paths]
    calls = {"n": 0}

    def replay(_content: bytes, *, media_type: str):
        assert media_type == "application/pdf"
        index = calls["n"]
        calls["n"] += 1
        return extraction._VisionResponse(text=payloads[index], request_id=f"golden_{index + 1}")

    monkeypatch.setattr(extraction, "_call_vision", replay)
    return calls


def test_hills_classic_golden_pages_run_the_full_pipeline(db, monkeypatch):
    """Two REAL Hill's pages, one per header family (Life Stage / Disease
    Category), from a live gemini-3.1-pro-preview run: every recorded row must
    conform under the single hills.price_list.v1 contract and reach mastering.

    Pinned to the provider that recorded them, which also runs the whole
    pipeline once on the non-default provider — the toggle is not just a
    lookup, it has to carry a real run."""

    monkeypatch.setenv("CATALOGUE_VISION_PROVIDER", "google")
    monkeypatch.setenv("GEMINI_API_KEY", "golden-replay")
    pages = [HILLS_CLASSIC / "page_1.json", HILLS_CLASSIC / "page_4.json"]
    calls = _install_golden_replay(monkeypatch, pages)
    expected_rows = sum(len(json.loads(p.read_text())["rows"]) for p in pages)

    service = CatalogueSubmissionService(
        db, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"], max_upload_bytes=4 * 1024 * 1024
    )
    submitted = service.submit(
        CatalogueSubmissionCommand(
            supplier_id=14,
            original_filename="hills-golden.pdf",
            content_type="application/pdf",
            stream=BytesIO(_blank_pdf(len(pages))),
            contract_id=None,
            contract_version=None,
            idempotency_key=None,
            submitted_by="golden",
        )
    )
    result = catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)

    assert result.terminal_status == "completed_with_warnings"  # declared contract ambiguity
    assert calls["n"] == len(pages)
    assert result.rows_extracted == expected_rows == 59
    assert result.staging_items_created == expected_rows

    run_uuid = str(submitted.ingestion_run_id)
    attempt = db.query(models.CatalogueExtractionAttempt).filter_by(ingestion_run_uuid=run_uuid).one()
    assert attempt.status == "COMPLETE"
    assert attempt.units_attempted == attempt.units_completed == len(pages)

    # No required-header/field failures on EITHER header family, and no
    # sparse-page suspicion between two dense real pages.
    issues = db.query(models.CatalogueValidationIssue).filter_by(ingestion_run_uuid=run_uuid).all()
    codes = {issue.issue_code for issue in issues}
    assert "CONTRACT_REQUIRED_HEADER_MISSING" not in codes
    assert "CONTRACT_REQUIRED_FIELD_MISSING" not in codes
    run = db.query(models.IngestionRun).filter_by(run_uuid=run_uuid).one()
    assert "suspiciously sparse" not in (run.error_summary or "")

    # Every row produced a reviewable candidate (fresh DB: no mappings, so the
    # resolver must propose creation — never invent a canonical match).
    candidates = db.query(models.CatalogueMasteringCandidate).filter_by(ingestion_run_uuid=run_uuid).all()
    assert len(candidates) == expected_rows
    states = {json.loads(c.product_variant_resolution_json)["state"] for c in candidates}
    assert states == {"PROPOSED_CREATE"}

    # Representative REAL field values, one per header family.
    rows = db.query(models.CatalogueNormalizedRow).filter_by(ingestion_run_uuid=run_uuid).all()
    by_sku = {}
    for row in rows:
        fields = json.loads(row.normalized_fields_json)
        sku = (fields.get("supplier_sku") or {}).get("value")
        if sku:
            by_sku[sku] = fields
    classic = by_sku["10447"]  # Life Stage family (page 1)
    assert classic["cost"]["amount"] == "13.10"
    assert classic["cost"]["currency"] == "HKD"
    assert "Healthy Cuisine" in classic["product_name"]["value"]
    prescription = by_sku["607665"]  # Disease Category family (page 4)
    assert prescription["cost"]["amount"] == "25.20"
    assert "Cancer" in prescription["product_name"]["value"]

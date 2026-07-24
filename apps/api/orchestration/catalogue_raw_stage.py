"""Raw stage: preserve, validate, identify and audit the original supplier file.

The raw stage answers exactly one question: what did the supplier send us?
It verifies the durably stored original (existence, readability, size limit,
path safety, signature, checksum), performs lightweight structural inspection
only (PDF encryption flag and page count — never text extraction), persists a
durable completed/failed marker with integrity metadata, and returns a typed
result that carries identifiers and file facts but no file content.

This module must never import or reach an AI provider, OCR, document parsing,
extraction, interpretation, normalization or business validation. Anything
that tries to understand what the file MEANS belongs to the extraction stage
and later stages, which consume the stored original through its durable
``source_ref`` after this stage has completed.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

import pypdf
from sqlalchemy.orm import Session

import models

from .catalogue_source_loader import load_and_verify_source_asset
from .catalogue_types import RawStageResult, SourceVerificationError


def complete_raw_stage(
    db: Session,
    *,
    ingestion_run_id: UUID,
    upload_root: str | Path | None = None,
    max_source_bytes: int | None = None,
) -> RawStageResult:
    """Verify and audit the stored original file; never look inside its meaning.

    Idempotent: re-running for the same run re-verifies the same stored bytes
    and overwrites the same completion metadata without creating new records.
    Raises ``SourceVerificationError`` (persisting a durable ``failed`` marker)
    when the file is missing, empty, oversized, unreadable, corrupted,
    checksum-mismatched or password protected.
    """

    try:
        asset = load_and_verify_source_asset(
            db,
            ingestion_run_id=ingestion_run_id,
            upload_root=upload_root,
            max_source_bytes=max_source_bytes,
        )
        page_count = _structural_page_count(asset.content, asset.source_format)
    except SourceVerificationError:
        _mark_raw_stage_failed(db, ingestion_run_id=ingestion_run_id)
        raise

    source = _source_row(db, ingestion_run_id=ingestion_run_id)
    now = _now_iso()
    source.byte_size = asset.size_bytes
    source.page_count = page_count
    source.raw_stage_status = "completed"
    source.raw_stage_completed_at = now
    source.updated_at = now
    db.commit()

    metadata = _source_metadata(source)
    return RawStageResult(
        run_identity=asset.run_identity,
        catalogue_import_id=source.legacy_import_id,
        original_filename=asset.original_filename,
        content_type=metadata.get("content_type"),
        byte_size=asset.size_bytes,
        checksum_sha256=asset.sha256,
        source_ref=asset.source_ref,
        page_count=page_count,
        received_at=source.received_at,
    )


def _structural_page_count(content: bytes, source_format: str) -> int | None:
    """Lightweight structural inspection for PDFs only.

    Reads the document structure to detect password protection and count
    pages. Never extracts text, images, tables or layout.
    """

    if source_format not in {"PDF", "PDF_TABLE"}:
        return None
    try:
        reader = pypdf.PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            raise SourceVerificationError("Source PDF is password protected")
        return len(reader.pages)
    except SourceVerificationError:
        raise
    except Exception as exc:
        raise SourceVerificationError("Source PDF structure cannot be read") from exc


def _mark_raw_stage_failed(db: Session, *, ingestion_run_id: UUID) -> None:
    """Persist a durable raw-stage failure marker; never mask the original error."""

    try:
        source = _source_row(db, ingestion_run_id=ingestion_run_id)
    except SourceVerificationError:
        return
    now = _now_iso()
    source.raw_stage_status = "failed"
    source.updated_at = now
    db.commit()


def _source_row(db: Session, *, ingestion_run_id: UUID) -> models.CatalogueSourceDocument:
    run = db.query(models.IngestionRun).filter_by(run_uuid=str(ingestion_run_id)).first()
    if run is None:
        raise SourceVerificationError("Queued run has no canonical source document")
    source = run.pipeline_source_document
    if source is None and run.catalogue_source_document_id:
        source = db.get(models.CatalogueSourceDocument, run.catalogue_source_document_id)
    if source is None:
        raise SourceVerificationError("Queued run has no canonical source document")
    return source


def _source_metadata(source: models.CatalogueSourceDocument) -> dict:
    try:
        metadata = json.loads(source.source_metadata_json or "{}")
    except (TypeError, ValueError):
        return {}
    return metadata if isinstance(metadata, dict) else {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


__all__ = ["complete_raw_stage"]

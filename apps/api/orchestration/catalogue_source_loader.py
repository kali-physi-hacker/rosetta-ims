"""Secure source loading for catalogue ingestion orchestration."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

import v2.models as v2_models
from services.catalogue_submission import DEFAULT_UPLOAD_ROOT

from .catalogue_types import RunIdentity, RunNotFound, SourceVerificationError, VerifiedSourceAsset


DEFAULT_MAX_SOURCE_BYTES = 25 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


def load_and_verify_source_asset(
    db: Session,
    *,
    ingestion_run_id: UUID,
    upload_root: str | Path | None = None,
    max_source_bytes: int | None = None,
) -> VerifiedSourceAsset:
    """Load one persisted source file after path, size, signature and checksum checks."""

    run = db.query(v2_models.IngestionRun).filter_by(run_uuid=str(ingestion_run_id)).first()
    if run is None:
        raise RunNotFound(f"Ingestion run {ingestion_run_id} was not found")
    source = run.pipeline_source_document
    if source is None and run.catalogue_source_document_id:
        source = db.get(v2_models.CatalogueSourceDocument, run.catalogue_source_document_id)
    if source is None:
        raise SourceVerificationError("Queued run has no canonical source document")
    if not source.source_ref:
        raise SourceVerificationError("Source document has no durable source reference")
    if not source.source_checksum:
        raise SourceVerificationError("Source document has no checksum")
    if not run.supplier_id or not run.supplier_source_contract_id or not run.supplier_source_contract_version:
        raise SourceVerificationError("Queued run is missing supplier-source contract identity")
    if source.supplier_id and source.supplier_id != run.supplier_id:
        raise SourceVerificationError("Run supplier does not match source document supplier")

    root = Path(upload_root or os.environ.get("CATALOGUE_UPLOAD_DIR", DEFAULT_UPLOAD_ROOT)).resolve()
    source_path = _resolve_source_path(root, source.source_ref)
    if not source_path.exists() or not source_path.is_file():
        raise SourceVerificationError("Durable source file is missing")

    limit = int(
        max_source_bytes
        if max_source_bytes is not None
        else os.environ.get("CATALOGUE_ORCHESTRATION_MAX_SOURCE_BYTES", str(DEFAULT_MAX_SOURCE_BYTES))
    )
    sha = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    header = b""
    try:
        with source_path.open("rb") as handle:
            while True:
                chunk = handle.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise SourceVerificationError("Durable source file exceeds orchestration size limit")
                if len(header) < 16:
                    header += chunk[: 16 - len(header)]
                sha.update(chunk)
                chunks.append(chunk)
    except OSError as exc:
        raise SourceVerificationError("Durable source file is unreadable") from exc
    if total <= 0:
        raise SourceVerificationError("Durable source file is empty")
    digest = sha.hexdigest()
    if digest != source.source_checksum:
        raise SourceVerificationError("Durable source checksum does not match persisted checksum")
    source_format = (source.source_format or "").upper()
    if not _signature_matches(source_format, header):
        raise SourceVerificationError("Durable source signature does not match persisted source format")

    identity = RunIdentity(
        run_uuid=UUID(run.run_uuid),
        supplier_catalogue_id=UUID(source.supplier_catalogue_uuid),
        source_file_id=UUID(source.source_file_uuid),
        supplier_id=run.supplier_id,
        contract_id=run.supplier_source_contract_id,
        contract_version=run.supplier_source_contract_version,
        document_type=run.document_type or source.document_type or "",
        source_format=source_format,
        filename=source.filename,
    )
    return VerifiedSourceAsset(
        run_identity=identity,
        original_filename=source.filename,
        source_ref=source.source_ref,
        source_format=source_format,
        sha256=digest,
        size_bytes=total,
        content=b"".join(chunks),
    )


def _resolve_source_path(root: Path, source_ref: str) -> Path:
    if not source_ref or not source_ref.strip():
        raise SourceVerificationError("Source reference is blank")
    ref_path = Path(source_ref)
    if ref_path.is_absolute() or ".." in ref_path.parts:
        raise SourceVerificationError("Source reference is not a safe relative path")
    resolved = (root / ref_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise SourceVerificationError("Source reference escapes the configured upload root") from exc
    return resolved


def _signature_matches(source_format: str, header: bytes) -> bool:
    if source_format in {"PDF", "PDF_TABLE"}:
        return header.startswith(b"%PDF")
    if source_format == "SPREADSHEET":
        return header.startswith(b"PK\x03\x04") or header.startswith(b"\xd0\xcf\x11\xe0")
    if source_format == "CSV":
        return b"\x00" not in header
    return False

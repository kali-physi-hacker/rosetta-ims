"""Framework-neutral catalogue submission service.

This service registers a supplier catalogue upload as a durable source document
and queued ingestion run. It deliberately does not extract, stage, master,
publish, schedule background work, or import FastAPI/Prefect symbols.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Any
from uuid import UUID, uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from services import supplier_source_contract_runtime
from services.source_capability import (
    DEFAULT_UPLOAD_ROOT,
    SUPPORTED_SOURCE_SUFFIXES,
    signature_matches,
)
from schemas.catalogue_pipeline.enums import SourceFormat


DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


class CatalogueSubmissionError(ValueError):
    """Base error for submission-boundary failures."""


class UnknownSupplierError(CatalogueSubmissionError):
    """Raised when the submitted supplier_id is unknown."""


class ContractParameterError(CatalogueSubmissionError):
    """Raised when contract_id/version parameters are incomplete."""


class SupplierContractSelectionError(CatalogueSubmissionError):
    """Raised when the supplier-source contract is unknown or unsupported."""


class SupplierContractAmbiguousError(SupplierContractSelectionError):
    """Raised when supplier-only resolution has multiple supported formats."""


class SupplierContractMismatchError(SupplierContractSelectionError):
    """Raised when an exact contract belongs to a different supplier."""


class EmptyUploadError(CatalogueSubmissionError):
    """Raised when an uploaded file contains no bytes."""


class UnsupportedSourceTypeError(CatalogueSubmissionError):
    """Raised when the source type is not accepted by the resolved contract."""


class UploadTooLargeError(CatalogueSubmissionError):
    """Raised when an upload exceeds the configured limit."""


class MalformedFilenameError(CatalogueSubmissionError):
    """Raised when a submitted filename is unsafe or unusable."""


class StorageUnavailableError(CatalogueSubmissionError):
    """Raised when durable source storage cannot be written."""


class SubmissionPersistenceError(CatalogueSubmissionError):
    """Raised when a queued submission cannot be committed."""


class SubmissionIdempotencyConflict(CatalogueSubmissionError):
    """Raised when the same idempotency key is reused for different material."""


class RetryNotAllowedError(CatalogueSubmissionError):
    """The run is not in a retryable state (not failed, or already retried)."""


class SourceFileMissingError(CatalogueSubmissionError):
    """The stored source file for the run is gone — a retry needs a re-upload."""


class SubmissionNotFoundError(CatalogueSubmissionError):
    """Raised when a run UUID cannot be found."""


@dataclass(frozen=True)
class CatalogueSubmissionCommand:
    """Typed command for registering a supplier catalogue upload."""

    supplier_id: int
    original_filename: str
    content_type: str | None
    stream: BinaryIO
    contract_id: str | None = None
    contract_version: str | None = None
    idempotency_key: str | None = None
    submitted_by: str | None = None


@dataclass(frozen=True)
class CatalogueSubmissionResult:
    """Result returned after a catalogue submission is durably queued."""

    ingestion_run_id: UUID
    supplier_catalogue_id: UUID
    source_file_id: UUID
    supplier_id: int
    contract_id: str
    contract_version: str
    document_type: str
    status: str
    submitted_at: str
    status_url: str


@dataclass(frozen=True)
class CatalogueIngestionStatus:
    """Safe run-status representation for polling clients."""

    ingestion_run_id: UUID
    supplier_catalogue_id: UUID | None
    source_file_id: UUID | None
    supplier_id: int | None
    contract_id: str | None
    contract_version: str | None
    document_type: str | None
    status: str
    submitted_at: str
    started_at: str | None
    completed_at: str | None
    items_extracted: int | None
    metrics: dict[str, Any] | None
    error_summary: dict[str, Any] | str | None
    retry_of: str | None = None            # parent run uuid when this run is a retry
    superseded_by_run: str | None = None   # child run uuid when this run has been retried


@dataclass(frozen=True)
class StoredUpload:
    """A file successfully written to durable storage."""

    source_file_id: UUID
    source_ref: str
    original_filename: str
    source_format: str
    size_bytes: int
    sha256: str
    final_path: Path
    existed_before: bool = False


class CatalogueSubmissionService:
    """Register catalogue submissions and expose queued-run status."""

    def __init__(
        self,
        db: Session,
        *,
        upload_root: str | Path | None = None,
        max_upload_bytes: int | None = None,
        extractor_name: str = "queued-submission",
        extractor_version: str = "v1",
    ):
        self.db = db
        self.upload_root = Path(upload_root or os.environ.get("CATALOGUE_UPLOAD_DIR", DEFAULT_UPLOAD_ROOT))
        self.max_upload_bytes = int(
            max_upload_bytes
            if max_upload_bytes is not None
            else os.environ.get("CATALOGUE_SUBMISSION_MAX_BYTES", str(DEFAULT_MAX_UPLOAD_BYTES))
        )
        self.extractor_name = extractor_name
        self.extractor_version = extractor_version

    def submit(self, command: CatalogueSubmissionCommand) -> CatalogueSubmissionResult:
        """Persist a submission and return its queued ingestion run identity."""

        supplier = self._supplier(command.supplier_id)
        contract = self._resolve_contract(command)
        submitted_at = _iso(_now())
        source_file_id = uuid4()
        stored: StoredUpload | None = None
        try:
            stored = self._store_upload(
                command.stream,
                original_filename=command.original_filename,
                source_file_id=source_file_id,
                expected_source_format=contract.declaration.source_structure.source_format,
            )
            material_fingerprint = _material_fingerprint(
                file_sha256=stored.sha256,
                supplier_id=supplier.id,
                contract_id=contract.slug,
                contract_version=contract.version,
                document_type=contract.declaration.document_type.value,
            )
            if command.idempotency_key:
                existing = self._idempotency_record(command.idempotency_key)
                if existing:
                    if existing.material_fingerprint != material_fingerprint:
                        self._cleanup_new_file(stored)
                        raise SubmissionIdempotencyConflict("Idempotency-Key was already used for a different catalogue submission")
                    self._cleanup_new_file(stored)
                    return _result_from_json(existing.response_json)

            result = self._create_submission_records(
                command=command,
                supplier=supplier,
                contract=contract,
                stored=stored,
                material_fingerprint=material_fingerprint,
                submitted_at=submitted_at,
            )
            self.db.commit()
            return result
        except CatalogueSubmissionError:
            self.db.rollback()
            if stored:
                self._cleanup_new_file(stored)
            raise
        except IntegrityError as exc:
            self.db.rollback()
            if stored:
                self._cleanup_new_file(stored)
            if command.idempotency_key:
                existing = self._idempotency_record(command.idempotency_key)
                if existing:
                    if existing.material_fingerprint == material_fingerprint:
                        return _result_from_json(existing.response_json)
                    raise SubmissionIdempotencyConflict("Idempotency-Key was already used for a different catalogue submission") from exc
            raise SubmissionPersistenceError("Submission could not be persisted") from exc
        except Exception as exc:
            self.db.rollback()
            if stored:
                self._cleanup_new_file(stored)
            raise SubmissionPersistenceError("Submission could not be persisted") from exc
        
    def _get_status(self, run: models.IngestionRun) -> CatalogueIngestionStatus:
        source = run.pipeline_source_document
        if source is None and run.catalogue_source_document_id:
            source = self.db.get(models.CatalogueSourceDocument, run.catalogue_source_document_id)
        metrics = _json_or_none(run.metrics)
        error_summary = _json_or_text(run.error_summary)
        retry_of = run.parent_run.run_uuid if run.parent_run_id and run.parent_run else None
        child = (
            self.db.query(models.IngestionRun)
            .filter_by(parent_run_id=run.id)
            .order_by(models.IngestionRun.id.desc())
            .first()
        )
        return CatalogueIngestionStatus(
            ingestion_run_id=UUID(run.run_uuid),
            supplier_catalogue_id=UUID(source.supplier_catalogue_uuid) if source else None,
            source_file_id=UUID(source.source_file_uuid) if source else None,
            supplier_id=run.supplier_id,
            contract_id=run.supplier_source_contract_id,
            contract_version=run.supplier_source_contract_version,
            document_type=run.document_type,
            status=run.status,
            submitted_at=run.created_at,
            started_at=run.started_at,
            completed_at=run.completed_at,
            items_extracted=run.items_extracted,
            metrics=metrics,
            error_summary=error_summary,
            retry_of=retry_of,
            superseded_by_run=child.run_uuid if child else None,
        )
        
    def list(self) -> list[CatalogueIngestionStatus]: 
        """Returns a list of safe typed status payload for all current ingestion runs and their statuses"""
        runs = self.db.query(models.IngestionRun).all()
        run_statuses = [self._get_status(run) for run in runs]
        return run_statuses 

    def retry(self, run_uuid: UUID, *, submitted_by: str | None = None) -> CatalogueSubmissionResult:
        """Re-submit a failed run's stored source file as a NEW run.

        Append-only: the failed run is never mutated — the child links back
        through ``parent_run_id`` and the queued-run dispatcher picks it up
        exactly like a fresh submission. Same bytes, same contract; if the
        file itself was the problem, the caller should re-upload instead.
        """

        run = self.db.query(models.IngestionRun).filter_by(run_uuid=str(run_uuid)).first()
        if run is None:
            raise SubmissionNotFoundError(f"Ingestion run {run_uuid} was not found")
        if run.status != "failed":
            raise RetryNotAllowedError(f"Only failed runs can be retried (this one is {run.status})")
        existing_child = self.db.query(models.IngestionRun).filter_by(parent_run_id=run.id).first()
        if existing_child is not None:
            raise RetryNotAllowedError(f"This run was already retried as {existing_child.run_uuid}")
        if run.supplier_id is None or not run.supplier_source_contract_id:
            raise RetryNotAllowedError("This run has no supplier contract to retry with")
        source = run.pipeline_source_document
        if source is None and run.catalogue_source_document_id:
            source = self.db.get(models.CatalogueSourceDocument, run.catalogue_source_document_id)
        if source is None or not source.source_ref:
            raise SourceFileMissingError("The original file for this run is no longer available — upload it again")
        path = self.upload_root / source.source_ref
        if not path.exists():
            raise SourceFileMissingError("The original file for this run is no longer available — upload it again")

        with path.open("rb") as stream:
            result = self.submit(
                CatalogueSubmissionCommand(
                    supplier_id=run.supplier_id,
                    original_filename=source.filename,
                    content_type=None,
                    stream=stream,
                    contract_id=run.supplier_source_contract_id,
                    contract_version=run.supplier_source_contract_version,
                    idempotency_key=None,
                    submitted_by=submitted_by,
                )
            )
        new_run = self.db.query(models.IngestionRun).filter_by(run_uuid=str(result.ingestion_run_id)).first()
        if new_run is not None and new_run.parent_run_id is None:
            new_run.parent_run_id = run.id
            self.db.commit()
        return result

    def reparse(
        self,
        run_uuid: UUID,
        *,
        from_stage: str = "conformance",
        submitted_by: str | None = None,
    ) -> CatalogueSubmissionResult:
        """Re-run the interpretation over evidence this run already holds.

        A supplier contract is consumed at conformance, which reaches no model
        provider — so a mapping change needs the stored observations re-read,
        not the pages re-scanned. Unlike `retry`, this works on a COMPLETED run
        (that is the point: you changed the contract and want to see the
        result), can be repeated, and costs nothing at the provider.

        A NEW run is created either way. The parent may already carry approved
        and published decisions, and those are append-only.
        """
        from orchestration.catalogue_reparse import (
            ReparseNotAllowed,
            ReparseStage,
            SUPPORTED_STAGES,
            evidence_source_run,
            mark_reparse,
        )

        try:
            stage = ReparseStage(from_stage)
        except ValueError:
            allowed = ", ".join(s.value for s in SUPPORTED_STAGES)
            raise RetryNotAllowedError(f"Unknown stage '{from_stage}' — supported: {allowed}") from None
        if stage not in SUPPORTED_STAGES:
            allowed = ", ".join(s.value for s in SUPPORTED_STAGES)
            raise RetryNotAllowedError(
                f"Re-parsing from '{stage.value}' is not supported yet — supported: {allowed}. "
                "Extraction is what a re-parse exists to avoid; use retry to re-scan the file."
            )

        run = self.db.query(models.IngestionRun).filter_by(run_uuid=str(run_uuid)).first()
        if run is None:
            raise SubmissionNotFoundError(f"Ingestion run {run_uuid} was not found")
        if run.status in {"queued", "running"}:
            raise RetryNotAllowedError(f"Run {run_uuid} is {run.status} — wait for it to finish")
        if run.supplier_id is None or not run.supplier_source_contract_id:
            raise RetryNotAllowedError("This run has no supplier contract to re-parse with")

        # Follow the chain to whoever actually paid for the extraction, so a
        # fifth contract iteration still reads the first run's evidence.
        origin = evidence_source_run(self.db, run)
        evidence_count = (
            self.db.query(models.CatalogueExtractedEvidence)
            .filter(models.CatalogueExtractedEvidence.ingestion_run_uuid == origin.run_uuid)
            .count()
        )
        if evidence_count == 0:
            raise RetryNotAllowedError(
                f"Run {origin.run_uuid} captured no evidence, so there is nothing to re-parse — "
                "retry the run to scan the file again"
            )

        source = run.pipeline_source_document
        if source is None and run.catalogue_source_document_id:
            source = self.db.get(models.CatalogueSourceDocument, run.catalogue_source_document_id)
        if source is None:
            raise SourceFileMissingError("This run has no source document to link the re-parse to")

        new_run = self._queue_reparse_run(run, source, submitted_by=submitted_by)
        mark_reparse(new_run, source_run_uuid=origin.run_uuid, from_stage=stage)
        new_run.parent_run_id = run.id
        self.db.commit()
        return CatalogueSubmissionResult(
            ingestion_run_id=UUID(new_run.run_uuid),
            supplier_catalogue_id=UUID(source.supplier_catalogue_uuid)
            if getattr(source, "supplier_catalogue_uuid", None) else UUID(new_run.run_uuid),
            source_file_id=UUID(source.source_file_uuid)
            if getattr(source, "source_file_uuid", None) else UUID(new_run.run_uuid),
            supplier_id=new_run.supplier_id,
            contract_id=new_run.supplier_source_contract_id,
            contract_version=new_run.supplier_source_contract_version,
            document_type=new_run.document_type,
            status=new_run.status,
            submitted_at=new_run.created_at,
            status_url=f"/catalogues/ingestions/{new_run.run_uuid}",
        )

    def _queue_reparse_run(self, run, source, *, submitted_by: str | None):
        """A queued run pointing at the parent's source document.

        Deliberately reuses the parent's CatalogueImport and source document
        rather than re-storing the file: a re-parse is the same bytes, and the
        dispatcher will never read them anyway.
        """
        submitted_at = _iso(_now())
        new_run = models.IngestionRun(
            run_uuid=str(uuid4()),
            source_document_id=run.source_document_id,
            catalogue_source_document_id=source.id,
            supplier_id=run.supplier_id,
            contract_version=run.contract_version,
            supplier_source_contract_id=run.supplier_source_contract_id,
            supplier_source_contract_version=run.supplier_source_contract_version,
            document_type=run.document_type,
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            status=models.IngestionRunStatus.QUEUED.value,
            started_at=None,
            completed_at=None,
            items_extracted=None,
            created_at=submitted_at,
        )
        self.db.add(new_run)
        self.db.flush()
        return new_run

    def get_status(self, run_uuid: UUID) -> CatalogueIngestionStatus:
        """Return a safe typed status payload for one ingestion run."""

        run = self.db.query(models.IngestionRun).filter_by(run_uuid=str(run_uuid)).first()
        if run is None:
            raise SubmissionNotFoundError(f"Ingestion run {run_uuid} was not found")
        return self._get_status(run)

    def _supplier(self, supplier_id: int) -> models.Supplier:
        supplier = self.db.query(models.Supplier).filter_by(id=supplier_id).first()
        if supplier is None:
            raise UnknownSupplierError(f"Supplier {supplier_id} was not found")
        return supplier

    def _resolve_contract(self, command: CatalogueSubmissionCommand):
        if bool(command.contract_id) != bool(command.contract_version):
            raise ContractParameterError("contract_id and contract_version must be supplied together")
        try:
            return supplier_source_contract_runtime.resolve_supplier_contract(
                supplier_id=command.supplier_id,
                contract_id=command.contract_id,
                contract_version=command.contract_version,
            )
        except supplier_source_contract_runtime.SupplierContractAmbiguousError as exc:
            raise SupplierContractAmbiguousError(str(exc)) from exc
        except supplier_source_contract_runtime.SupplierContractIdentityError as exc:
            raise SupplierContractMismatchError(str(exc)) from exc
        except supplier_source_contract_runtime.SupplierContractResolutionError as exc:
            raise SupplierContractSelectionError(str(exc)) from exc

    def _store_upload(
        self,
        stream: BinaryIO,
        *,
        original_filename: str,
        source_file_id: UUID,
        expected_source_format: SourceFormat,
    ) -> StoredUpload:
        safe_filename = _safe_original_filename(original_filename)
        suffix = Path(safe_filename).suffix.lower()
        source_format = _source_format_from_suffix(suffix)
        if not source_format:
            raise UnsupportedSourceTypeError("Unsupported catalogue source file type")
        if not _format_matches_contract(source_format, expected_source_format):
            raise UnsupportedSourceTypeError(
                f"File type {source_format} does not match supplier contract source format {expected_source_format.value}"
            )

        temp_dir = self.upload_root / ".tmp"
        final_dir = self.upload_root / "v2"
        try:
            temp_dir.mkdir(parents=True, exist_ok=True)
            final_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StorageUnavailableError("Catalogue source storage is unavailable") from exc

        temp_path = temp_dir / f"{source_file_id}.part"
        source_ref = f"v2/{source_file_id}{suffix}"
        final_path = final_dir / f"{source_file_id}{suffix}"

        sha = hashlib.sha256()
        total = 0
        header = b""
        try:
            with temp_path.open("wb") as output:
                while True:
                    chunk = stream.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    if not isinstance(chunk, bytes):
                        chunk = bytes(chunk)
                    total += len(chunk)
                    if total > self.max_upload_bytes:
                        raise UploadTooLargeError(f"Catalogue upload exceeds {self.max_upload_bytes} bytes")
                    if len(header) < 16:
                        header += chunk[: 16 - len(header)]
                    sha.update(chunk)
                    output.write(chunk)
            if total == 0:
                raise EmptyUploadError("Uploaded catalogue file is empty")
            if not _signature_matches(source_format, header):
                raise UnsupportedSourceTypeError("Catalogue file content does not match its supported source type")
            if final_path.exists():
                raise StorageUnavailableError("Generated catalogue source path already exists")
            os.replace(temp_path, final_path)
            return StoredUpload(
                source_file_id=source_file_id,
                source_ref=source_ref,
                original_filename=safe_filename,
                source_format=source_format,
                size_bytes=total,
                sha256=sha.hexdigest(),
                final_path=final_path,
                existed_before=False,
            )
        except CatalogueSubmissionError:
            _unlink_quietly(temp_path)
            raise
        except OSError as exc:
            _unlink_quietly(temp_path)
            raise StorageUnavailableError("Catalogue source storage is unavailable") from exc

    def _create_submission_records(
        self,
        *,
        command: CatalogueSubmissionCommand,
        supplier: models.Supplier,
        contract,
        stored: StoredUpload,
        material_fingerprint: str,
        submitted_at: str,
    ) -> CatalogueSubmissionResult:
        supplier_catalogue_id = uuid4()
        import_row = models.CatalogueImport(
            supplier_id=supplier.id,
            filename=stored.original_filename,
            format=stored.source_format.lower(),
            imported_at=submitted_at,
            status="queued",
            item_count=0,
            supplier_source="user",
            supplier_status="confirmed",
            source_ref=stored.source_ref,
        )
        self.db.add(import_row)
        self.db.flush()

        source = models.CatalogueSourceDocument(
            supplier_catalogue_uuid=str(supplier_catalogue_id),
            source_file_uuid=str(stored.source_file_id),
            legacy_import_id=import_row.id,
            supplier_id=supplier.id,
            filename=stored.original_filename,
            source_format=stored.source_format,
            source_ref=stored.source_ref,
            source_checksum=stored.sha256,
            received_at=submitted_at,
            supplier_source_contract_id=contract.slug,
            supplier_source_contract_version=contract.version,
            document_type=contract.declaration.document_type.value,
            status="active",
            source_metadata_json=json.dumps(
                {
                    "original_filename": stored.original_filename,
                    "content_type": command.content_type,
                    "size_bytes": stored.size_bytes,
                    "sha256": stored.sha256,
                    "submitted_by": command.submitted_by,
                },
                sort_keys=True,
            ),
            created_at=submitted_at,
        )
        self.db.add(source)
        self.db.flush()

        run_uuid = uuid4()
        run = models.IngestionRun(
            run_uuid=str(run_uuid),
            source_document_id=import_row.id,
            catalogue_source_document_id=source.id,
            supplier_id=supplier.id,
            contract_version="catalogue.extraction_profile.v1",
            supplier_source_contract_id=contract.slug,
            supplier_source_contract_version=contract.version,
            document_type=contract.declaration.document_type.value,
            extractor_name=self.extractor_name,
            extractor_version=self.extractor_version,
            status=models.IngestionRunStatus.QUEUED.value,
            started_at=None,
            completed_at=None,
            items_extracted=None,
            created_at=submitted_at,
        )
        self.db.add(run)
        self.db.flush()

        result = CatalogueSubmissionResult(
            ingestion_run_id=run_uuid,
            supplier_catalogue_id=supplier_catalogue_id,
            source_file_id=stored.source_file_id,
            supplier_id=supplier.id,
            contract_id=contract.slug,
            contract_version=contract.version,
            document_type=contract.declaration.document_type.value,
            status=run.status,
            submitted_at=submitted_at,
            status_url=f"/catalogues/ingestions/{run_uuid}",
        )
        if command.idempotency_key:
            self.db.add(
                models.CatalogueSubmissionIdempotency(
                    idempotency_key=command.idempotency_key,
                    material_fingerprint=material_fingerprint,
                    ingestion_run_uuid=str(run_uuid),
                    supplier_catalogue_uuid=str(supplier_catalogue_id),
                    source_file_uuid=str(stored.source_file_id),
                    supplier_id=supplier.id,
                    contract_id=contract.slug,
                    contract_version=contract.version,
                    document_type=contract.declaration.document_type.value,
                    file_sha256=stored.sha256,
                    original_filename=stored.original_filename,
                    response_json=json.dumps(_result_to_json(result), sort_keys=True),
                    created_at=submitted_at,
                )
            )
        return result

    def _idempotency_record(self, key: str):
        return self.db.query(models.CatalogueSubmissionIdempotency).filter_by(idempotency_key=key).first()

    def _cleanup_new_file(self, stored: StoredUpload) -> None:
        if not stored.existed_before:
            _unlink_quietly(stored.final_path)


def _safe_original_filename(filename: str) -> str:
    name = (filename or "").strip()
    if not name:
        raise MalformedFilenameError("A catalogue filename is required")
    if name != Path(name).name or ".." in Path(name).parts:
        raise MalformedFilenameError("Catalogue filename must not contain path components")
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip(" .")
    if not cleaned or not Path(cleaned).suffix:
        raise MalformedFilenameError("Catalogue filename must include a supported extension")
    return cleaned[:180]


def _source_format_from_suffix(suffix: str) -> str | None:
    return SUPPORTED_SOURCE_SUFFIXES.get(suffix)


def _format_matches_contract(source_format: str, expected: SourceFormat) -> bool:
    if expected in {SourceFormat.PDF, SourceFormat.PDF_TABLE}:
        return source_format == "PDF"
    if expected == SourceFormat.SPREADSHEET:
        return source_format == "SPREADSHEET"
    if expected == SourceFormat.CSV:
        return source_format == "CSV"
    return expected == SourceFormat.OTHER


_signature_matches = signature_matches


def _material_fingerprint(
    *,
    file_sha256: str,
    supplier_id: int,
    contract_id: str,
    contract_version: str,
    document_type: str,
) -> str:
    material = {
        "file_sha256": file_sha256,
        "supplier_id": supplier_id,
        "contract_id": contract_id,
        "contract_version": contract_version,
        "document_type": document_type,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _result_to_json(result: CatalogueSubmissionResult) -> dict[str, Any]:
    data = asdict(result)
    for key in ("ingestion_run_id", "supplier_catalogue_id", "source_file_id"):
        data[key] = str(data[key])
    return data


def _result_from_json(raw: str) -> CatalogueSubmissionResult:
    data = json.loads(raw)
    return CatalogueSubmissionResult(
        ingestion_run_id=UUID(data["ingestion_run_id"]),
        supplier_catalogue_id=UUID(data["supplier_catalogue_id"]),
        source_file_id=UUID(data["source_file_id"]),
        supplier_id=data["supplier_id"],
        contract_id=data["contract_id"],
        contract_version=data["contract_version"],
        document_type=data["document_type"],
        status=data["status"],
        submitted_at=data["submitted_at"],
        status_url=data["status_url"],
    )


def _json_or_none(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except Exception:
        return None


def _json_or_text(raw: str | None) -> dict[str, Any] | str | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()

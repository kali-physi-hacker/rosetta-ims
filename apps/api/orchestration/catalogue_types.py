"""Serializable types shared by catalogue orchestration tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID


class CatalogueOrchestrationError(ValueError):
    """Base class for sanitized orchestration failures."""

    error_code = "CATALOGUE_ORCHESTRATION_ERROR"
    retryable = False

    def public_message(self) -> str:
        return str(self) or self.error_code


class RunNotFound(CatalogueOrchestrationError):
    error_code = "RUN_NOT_FOUND"


class InvalidRunTransition(CatalogueOrchestrationError):
    error_code = "INVALID_RUN_TRANSITION"


class DuplicateRunClaim(CatalogueOrchestrationError):
    error_code = "DUPLICATE_RUN_CLAIM"


class TerminalRunReplay(CatalogueOrchestrationError):
    error_code = "TERMINAL_RUN_REPLAY"


class SourceVerificationError(CatalogueOrchestrationError):
    error_code = "SOURCE_VERIFICATION_ERROR"


class RecordedContractError(CatalogueOrchestrationError):
    error_code = "RECORDED_CONTRACT_ERROR"


class ExtractionEvidenceError(CatalogueOrchestrationError):
    error_code = "EXTRACTION_EVIDENCE_ERROR"


class TransientExtractionError(CatalogueOrchestrationError):
    error_code = "TRANSIENT_EXTRACTION_ERROR"
    retryable = True


@dataclass(frozen=True)
class RunIdentity:
    """Persisted run/source/contract identity reloaded inside orchestration."""

    run_uuid: UUID
    supplier_catalogue_id: UUID
    source_file_id: UUID
    supplier_id: int
    contract_id: str
    contract_version: str
    document_type: str
    source_format: str
    filename: str


@dataclass(frozen=True)
class VerifiedSourceAsset:
    """A durably verified source file loaded for extraction."""

    run_identity: RunIdentity
    original_filename: str
    source_ref: str
    source_format: str
    sha256: str
    size_bytes: int
    content: bytes = field(repr=False)


@dataclass(frozen=True)
class RecordedSupplierContract:
    """Exact supplier-source contract identity resolved from persistence."""

    supplier_id: int
    contract_id: str
    contract_version: str
    document_type: str
    source_format: str


@dataclass(frozen=True)
class ExtractedCatalogueRow:
    """One source-located row emitted by the orchestration extraction adapter."""

    row_key: str
    source_location: dict[str, Any]
    raw_text: str | None
    raw_cells: tuple[dict[str, Any], ...]
    extracted_fields: dict[str, Any]
    extraction_method: str
    extraction_model: str | None = None
    extraction_model_version: str | None = None
    extraction_confidence: Decimal | None = None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExtractionEvidenceResult:
    """Rows plus rejected-row accounting from the extraction adapter."""

    rows: tuple[ExtractedCatalogueRow, ...]
    rejected_count: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CatalogueFlowResult:
    """Operational result for one machine ingestion attempt."""

    ingestion_run_id: UUID
    terminal_status: str
    rows_extracted: int = 0
    raw_observations_created: int = 0
    raw_observations_reused: int = 0
    staging_items_created: int = 0
    staging_items_reused: int = 0
    validation_issues_created: int = 0
    validation_issues_reused: int = 0
    mastering_candidates_created: int = 0
    mastering_candidates_reused: int = 0
    rows_rejected: int = 0
    warnings: tuple[str, ...] = ()
    human_review_required: bool = False
    error_code: str | None = None

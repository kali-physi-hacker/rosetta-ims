"""Catalogue Ingestion Run — CIS-104.1.

One attempt to extract data from one uploaded supplier catalogue file (a
CatalogueImport / Catalogue Source Asset, in v1 terms). This module is
intentionally isolated: it defines a new table and imports existing v1
classes for read-only relationship convenience only — it never edits
models.py, and nothing in routers/v1 or services/extraction_service.py
references it. Wiring a real upload into creating one of these rows is a
separate, later change (CIS-104.2).

Reprocessing a source document always creates a new row (a new `id`) rather
than mutating a prior attempt; a retry links back to the run it's retrying
via `parent_run_id`. Run `status` describes the extraction process itself
and is independent of whether a human has reviewed/approved the data the
run produced — that's tracked elsewhere (Catalogue Item Resolution /
Review Decision), not on this model.
"""
import enum
import json
from dataclasses import asdict, dataclass
from typing import Optional

from sqlalchemy import Column, Integer, String, ForeignKey
from sqlalchemy.orm import relationship, validates

from database import Base


class IngestionRunStatus(str, enum.Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = frozenset({
    IngestionRunStatus.COMPLETED.value,
    IngestionRunStatus.COMPLETED_WITH_WARNINGS.value,
    IngestionRunStatus.FAILED.value,
    IngestionRunStatus.CANCELLED.value,
})


@dataclass
class IngestionRunMetrics:
    """Typed shape for the `metrics` JSON column, so it isn't an arbitrary dict.

    Operational facts only (rows seen, warnings, timing) — never catalogue
    business data.
    """

    rows_seen: Optional[int] = None
    warnings_count: Optional[int] = None
    rejected_count: Optional[int] = None
    confidence_avg: Optional[float] = None
    duration_ms: Optional[int] = None

    def to_json(self) -> str:
        return json.dumps({k: v for k, v in asdict(self).items() if v is not None})

    @classmethod
    def from_json(cls, raw: Optional[str]) -> "IngestionRunMetrics":
        if not raw:
            return cls()
        return cls(**json.loads(raw))


class IngestionRun(Base):
    __tablename__ = "catalogue_ingestion_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Input — exactly one source document per run.
    source_document_id = Column(Integer, ForeignKey("catalogue_imports.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True)

    # Which contract version and which engine governed this attempt.
    contract_version = Column(String, nullable=True)   # e.g. 'catalogue.extraction_profile.v1'; null only for declared generic extraction
    extractor_name = Column(String, nullable=False)    # e.g. 'claude-haiku', 'rule-based-excel'
    extractor_version = Column(String, nullable=False)  # e.g. '4.5-20251001', 'v2.3'

    # Retry lineage — set only when this run is reprocessing an earlier one.
    parent_run_id = Column(Integer, ForeignKey("catalogue_ingestion_runs.id"), nullable=True)

    # Process lifecycle — separate from item review/approval state.
    status = Column(String, nullable=False, default=IngestionRunStatus.QUEUED.value)
    started_at = Column(String, nullable=False)     # ISO datetime
    completed_at = Column(String, nullable=True)    # ISO datetime; set once status is terminal

    # Operational facts.
    items_extracted = Column(Integer, nullable=True)
    metrics = Column(String, nullable=True)          # JSON text — see IngestionRunMetrics
    error_summary = Column(String, nullable=True)    # JSON text; populated on failure/warning

    created_at = Column(String, nullable=False)      # record-creation stamp

    # Read-only relationships onto existing v1 tables. No back_populates —
    # CatalogueImport and Supplier in models.py are never edited by this module.
    source_document = relationship(
        "CatalogueImport", foreign_keys=[source_document_id], viewonly=True,
    )
    supplier = relationship(
        "Supplier", foreign_keys=[supplier_id], viewonly=True,
    )
    parent_run = relationship(
        "IngestionRun", remote_side=[id], foreign_keys=[parent_run_id],
    )

    @validates("parent_run_id")
    def _validate_parent_run_id(self, _key, value):
        if value is not None and self.id is not None and value == self.id:
            raise ValueError("IngestionRun.parent_run_id cannot reference itself")
        return value

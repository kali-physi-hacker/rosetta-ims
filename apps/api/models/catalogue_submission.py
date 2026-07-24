"""Catalogue submission boundary persistence models."""

from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from database import Base


class CatalogueSubmissionIdempotency(Base):
    """Durable HTTP submission idempotency record.

    The key is unique across submissions. The material fingerprint records the
    supplier, resolved contract, document type, and file checksum used to create
    the queued ingestion run.
    """

    __tablename__ = "catalogue_submission_idempotency"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_catalogue_submission_idempotency_key"),
        CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_submission_idempotency_key_not_blank"),
        CheckConstraint("length(material_fingerprint) = 64", name="ck_submission_material_fingerprint_sha256"),
        Index("ix_submission_idempotency_run", "ingestion_run_uuid"),
        Index("ix_submission_idempotency_source", "supplier_catalogue_uuid", "source_file_uuid"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    idempotency_key = Column(String, nullable=False)
    material_fingerprint = Column(String(64), nullable=False)
    ingestion_run_uuid = Column(String(36), ForeignKey("catalogue_ingestion_runs.run_uuid"), nullable=False)
    supplier_catalogue_uuid = Column(String(36), nullable=False)
    source_file_uuid = Column(String(36), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    contract_id = Column(String, nullable=False)
    contract_version = Column(String, nullable=False)
    document_type = Column(String, nullable=False)
    file_sha256 = Column(String(64), nullable=False)
    original_filename = Column(String, nullable=False)
    response_json = Column(Text, nullable=False)
    created_at = Column(String, nullable=False)


__all__ = ["CatalogueSubmissionIdempotency"]

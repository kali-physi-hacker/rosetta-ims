"""Re-run a run's interpretation over evidence we already hold.

A supplier contract maps source columns to fields, and that mapping is consumed
at CONFORMANCE — which is deliberately model-free ("no model provider is
reachable from this task"). So when a contract changes, nothing about the RAW
evidence changes; only what we make of it does.

Without this, the only way to see a mapping change was `retry`, which
re-submits the stored file and puts every page back through the vision provider
— paying again to re-derive bytes we already have, and taking ~80s for a
Hill's-sized document instead of a second.

A re-parse creates a NEW run, linked to its source by ``parent_run_id`` and
marked in metrics, and feeds it the parent's persisted observations at exactly
the point extraction would have produced them. Everything downstream is the
ordinary flow. It is a new run on purpose: the parent may already carry approved
and published decisions, and those are append-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from sqlalchemy.orm import Session

import models
from services import catalogue_pipeline_persistence as persistence

from .catalogue_stage_adapter import evidence_from_persisted_observation
from .catalogue_types import RawStageResult, RunIdentity


class ReparseStage(str, Enum):
    """Where a re-parse picks the flow back up.

    CONFORMANCE re-reads the supplier contract over stored evidence — the case
    a contract change needs. The earlier stages are named so the vocabulary is
    stable, and refused with a reason rather than silently doing something else.
    """

    CONFORMANCE = "conformance"
    EXTRACTION = "extraction"


SUPPORTED_STAGES = (ReparseStage.CONFORMANCE,)


class ReparseNotAllowed(ValueError):
    """The run cannot be re-parsed (still running, or no evidence to reuse)."""


@dataclass(frozen=True)
class StoredEvidence:
    """The parent's observations, in the shape the extraction stage emits."""

    source_run_uuid: str
    observations: tuple


def evidence_source_run(db: Session, run: models.IngestionRun) -> models.IngestionRun:
    """The run whose evidence a re-parse should read.

    Re-parsing a re-parse follows the chain back to whoever actually paid for
    the extraction, so a fifth contract iteration still costs nothing.
    """
    seen: set[int] = set()
    current = run
    while current.parent_run_id is not None and current.id not in seen:
        seen.add(current.id)
        metrics = _metrics(current)
        if not metrics.get("reparse_of"):
            break
        parent = db.get(models.IngestionRun, current.parent_run_id)
        if parent is None:
            break
        current = parent
    return current


def _metrics(run: models.IngestionRun) -> dict:
    try:
        return json.loads(run.metrics or "{}") or {}
    except ValueError:
        return {}


def load_stored_evidence(db: Session, *, ingestion_run_id: UUID) -> StoredEvidence:
    """Rehydrate the persisted observations for a re-parse run's source.

    Reads the RAW layer, not the file — the point of the whole exercise.
    """
    run = db.query(models.IngestionRun).filter_by(run_uuid=str(ingestion_run_id)).first()
    if run is None:
        raise ReparseNotAllowed(f"Ingestion run {ingestion_run_id} was not found")
    metrics = _metrics(run)
    source_uuid = metrics.get("reparse_of")
    if not source_uuid:
        raise ReparseNotAllowed(f"Ingestion run {ingestion_run_id} is not a re-parse")
    rows = (
        db.query(models.CatalogueExtractedEvidence)
        .filter(models.CatalogueExtractedEvidence.ingestion_run_uuid == source_uuid)
        .order_by(models.CatalogueExtractedEvidence.id)
        .all()
    )
    if not rows:
        raise ReparseNotAllowed(
            f"Run {source_uuid} has no stored evidence to re-parse — upload the file again"
        )

    # A retrigger reads only the observations that produced the rows it is
    # clearing. Re-driving the whole run would mint fresh PENDING candidates
    # for every row a person already reviewed — inventing the very work
    # selectivity exists to protect.
    selection = retrigger_selection(run)
    if selection is not None:
        rows = [row for row in rows if row.raw_observation_uuid in selection]
        if not rows:
            raise ReparseNotAllowed(
                f"Run {ingestion_run_id} selects {len(selection)} observations but none "
                f"exist on source run {source_uuid} — the selection is stale or wrong"
            )
    # Two hops on purpose: the row rehydrates into the CONTRACT, and
    # evidence_from_persisted_observation turns that into the extraction-stage
    # shape the flow hands to capture. Conformance already consumes durable
    # evidence through the same converter, so a re-parse is fed exactly what a
    # first parse is.
    return StoredEvidence(
        source_run_uuid=source_uuid,
        observations=tuple(
            evidence_from_persisted_observation(persistence.extracted_evidence_to_contract(row))
            for row in rows
        ),
    )


def is_reparse(db: Session, ingestion_run_id: UUID) -> bool:
    run = db.query(models.IngestionRun).filter_by(run_uuid=str(ingestion_run_id)).first()
    return bool(run is not None and _metrics(run).get("reparse_of"))


def mark_reparse(run: models.IngestionRun, *, source_run_uuid: str, from_stage: ReparseStage) -> None:
    """Record the lineage on the new run before the flow claims it.

    Lives in `metrics` rather than a new column: it is provenance about how the
    run was produced, which is what that JSON already holds, and the pipeline
    tables are built fresh rather than migrated.
    """
    metrics = _metrics(run)
    metrics["reparse_of"] = source_run_uuid
    metrics["reparse_from_stage"] = from_stage.value
    run.metrics = json.dumps(metrics)


def mark_retrigger(
    run: models.IngestionRun,
    *,
    retrigger_of: str,
    observation_uuids: list[str],
    attempt: int,
) -> None:
    """A retrigger is a re-parse that reads only the observations named here.

    ``retrigger_of`` is the run whose dead letters were targeted — usually also
    the evidence source, but not when re-triggering a run that was itself a
    re-parse, so both facts are kept. The selection is stored on the run rather
    than passed in memory because the flow that consumes it runs in another
    process, later, and must see exactly what was chosen at request time.
    """
    metrics = _metrics(run)
    metrics["retrigger_of"] = retrigger_of
    metrics["retrigger_observations"] = sorted(observation_uuids)
    metrics["retrigger_attempt"] = attempt
    run.metrics = json.dumps(metrics)


def retrigger_selection(run: models.IngestionRun) -> set[str] | None:
    """The observation UUIDs this run is limited to, or None for a full re-parse."""
    raw = _metrics(run).get("retrigger_observations")
    return set(raw) if raw else None


def reparse_raw_stage(db: Session, *, ingestion_run_id: UUID) -> RawStageResult:
    """The raw stage for a re-parse: identity only, never the file.

    `complete_raw_stage` exists to verify the stored original — read it, hash
    it, check the signature. A re-parse has no business doing any of that: it
    is not re-reading the document, and insisting on the bytes would make the
    feature fail exactly where it is most useful, on a run whose upload has
    since been cleaned up.

    Everything downstream needs identity, which the source document already
    records, so it is rebuilt from persistence and the file is left alone.
    """
    run = db.query(models.IngestionRun).filter_by(run_uuid=str(ingestion_run_id)).first()
    if run is None:
        raise ReparseNotAllowed(f"Ingestion run {ingestion_run_id} was not found")
    source = run.pipeline_source_document
    if source is None and run.catalogue_source_document_id:
        source = db.get(models.CatalogueSourceDocument, run.catalogue_source_document_id)
    if source is None:
        raise ReparseNotAllowed(f"Ingestion run {ingestion_run_id} has no source document")

    identity = RunIdentity(
        run_uuid=UUID(run.run_uuid),
        supplier_catalogue_id=UUID(source.supplier_catalogue_uuid),
        source_file_id=UUID(source.source_file_uuid),
        supplier_id=run.supplier_id,
        contract_id=run.supplier_source_contract_id,
        contract_version=run.supplier_source_contract_version,
        document_type=run.document_type or source.document_type or "",
        source_format=(source.source_format or "").upper(),
        filename=source.filename,
    )
    return RawStageResult(
        run_identity=identity,
        catalogue_import_id=source.legacy_import_id,
        original_filename=source.filename,
        content_type=None,
        byte_size=source.byte_size or 0,
        checksum_sha256=source.source_checksum or "",
        source_ref=source.source_ref or "",
        page_count=source.page_count,
        received_at=source.received_at,
    )

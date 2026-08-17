"""Human corrections to persisted extracted evidence (HITL at the RAW layer).

Extraction is verbatim — but a vision misread is not the page's truth, and the
only person who can say so is the one looking at the page. A correction
replaces cell values IN PLACE on the persisted observation while stamping the
original values, the reason, and the author into ``source_metadata``, so the
observation stays one auditable record rather than forking.

Two rows can carry the same observation: re-parse and retrigger children
re-persist the source run's observations as their own copies, and a later
re-parse follows the chain back to the ORIGINAL extraction run
(``evidence_source_run``). A correction therefore lands on BOTH the observation
being viewed and its extraction-source twin — the desk shows the fix
immediately AND every future re-drive reads it. When the twin cannot be
resolved unambiguously, the whole correction refuses with the source run named
rather than saving a fix a re-parse would silently resurrect over.

Nothing re-runs here. Fixing evidence and re-driving rows are separate
decisions: re-parse (whole run) or retrigger (dead-lettered rows) pick the
corrected cells up from the RAW layer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

import models
from orchestration import catalogue_reparse


class EvidenceCorrectionError(ValueError):
    """The correction is not applicable as stated; nothing was changed."""


class EvidenceNotFound(EvidenceCorrectionError):
    """The run or observation does not exist."""


@dataclass(frozen=True)
class EvidenceCorrectionResult:
    corrected_columns: tuple[str, ...]
    raw_observation_id: str
    source_run_id: str | None
    source_raw_observation_id: str | None


def correct_evidence(
    db: Session,
    *,
    run_uuid: UUID,
    raw_observation_uuid: UUID,
    cells: dict[str, str | None],
    reason: str,
    corrected_by: str,
) -> EvidenceCorrectionResult:
    """Apply a per-column correction to one observation's raw cells."""

    reason = (reason or "").strip()
    if len(reason) < 4:
        raise EvidenceCorrectionError("a correction needs a reason a reviewer can act on")
    if not cells:
        raise EvidenceCorrectionError("no cells were given to correct")

    run = db.query(models.IngestionRun).filter_by(run_uuid=str(run_uuid)).first()
    if run is None:
        raise EvidenceNotFound(f"Ingestion run {run_uuid} was not found")
    row = (
        db.query(models.CatalogueExtractedEvidence)
        .filter_by(ingestion_run_uuid=str(run_uuid), raw_observation_uuid=str(raw_observation_uuid))
        .first()
    )
    if row is None:
        raise EvidenceNotFound(f"Observation {raw_observation_uuid} is not part of run {run_uuid}")

    changes = _apply_cells(row, cells)
    if not changes:
        raise EvidenceCorrectionError("no cell changed — the values given match what was already read")

    corrected_at = datetime.now(timezone.utc).isoformat()
    _stamp(row, corrected_at=corrected_at, corrected_by=corrected_by, reason=reason, changes=changes)

    source_run = catalogue_reparse.evidence_source_run(db, run)
    source_observation_uuid: str | None = None
    if source_run.id != run.id:
        twin = _source_twin(db, row, source_run)
        twin_changes = _apply_cells(twin, cells)
        if twin_changes:
            _stamp(twin, corrected_at=corrected_at, corrected_by=corrected_by, reason=reason, changes=twin_changes)
        source_observation_uuid = twin.raw_observation_uuid

    db.commit()
    return EvidenceCorrectionResult(
        corrected_columns=tuple(sorted(changes)),
        raw_observation_id=row.raw_observation_uuid,
        source_run_id=source_run.run_uuid if source_run.id != run.id else None,
        source_raw_observation_id=source_observation_uuid,
    )


def _apply_cells(
    row: models.CatalogueExtractedEvidence,
    cells: dict[str, str | None],
) -> dict[str, dict[str, object]]:
    """Replace values on named columns, returning {column: {from, to}}.

    Corrections replace what was read, never invent structure: a column the
    observation does not carry refuses by name, and a column the extraction
    read twice is ambiguous and refuses rather than guessing which cell.
    """

    raw_cells = json.loads(row.raw_cells_json or "[]")
    if not raw_cells:
        raise EvidenceCorrectionError(
            "this observation has no cells to correct — it is text evidence, not a table row"
        )
    changes: dict[str, dict[str, object]] = {}
    for column, new_value in cells.items():
        matches = [cell for cell in raw_cells if (cell.get("column_name") or "") == column]
        if not matches:
            known = ", ".join(sorted({str(c.get("column_name")) for c in raw_cells if c.get("column_name")}))
            raise EvidenceCorrectionError(
                f"column {column!r} is not on this observation (it carries: {known}) — "
                "corrections replace what was read, never invent columns"
            )
        if len(matches) > 1:
            raise EvidenceCorrectionError(
                f"column {column!r} appears {len(matches)} times on this observation — ambiguous, refusing to guess"
            )
        cleaned = new_value.strip() if isinstance(new_value, str) else new_value
        cleaned = cleaned if cleaned != "" else None
        old = matches[0].get("raw_value")
        if old == cleaned:
            continue
        changes[column] = {"from": old, "to": cleaned}
        matches[0]["raw_value"] = cleaned
    if changes:
        if all(cell.get("raw_value") in (None, "") for cell in raw_cells) and not (row.raw_text or "").strip():
            raise EvidenceCorrectionError("a correction cannot blank the whole observation")
        row.raw_cells_json = json.dumps(raw_cells, ensure_ascii=False)
    return changes


def _stamp(
    row: models.CatalogueExtractedEvidence,
    *,
    corrected_at: str,
    corrected_by: str,
    reason: str,
    changes: dict[str, dict[str, object]],
) -> None:
    metadata = json.loads(row.source_metadata_json or "{}") or {}
    metadata.setdefault("human_corrections", []).append(
        {
            "corrected_at": corrected_at,
            "corrected_by": corrected_by,
            "reason": reason,
            "changes": changes,
        }
    )
    row.source_metadata_json = json.dumps(metadata, ensure_ascii=False)


def _source_twin(
    db: Session,
    row: models.CatalogueExtractedEvidence,
    source_run: models.IngestionRun,
) -> models.CatalogueExtractedEvidence:
    """The same observation as persisted on the extraction-source run.

    Matched by source location (object key, page, sheet, row) — the identity
    that survives re-persistence, since each run's copies mint fresh uuids.
    """

    twins = (
        db.query(models.CatalogueExtractedEvidence)
        .filter_by(
            ingestion_run_uuid=source_run.run_uuid,
            source_object_key=row.source_object_key,
            page_number=row.page_number,
            sheet_name=row.sheet_name,
            row_number=row.row_number,
        )
        .all()
    )
    if len(twins) != 1:
        raise EvidenceCorrectionError(
            f"could not resolve this observation on its extraction-source run {source_run.run_uuid} "
            f"({len(twins)} matches) — correct the evidence on that run directly, or a future "
            "re-parse would read the uncorrected original"
        )
    return twins[0]

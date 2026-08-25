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

Corrections REPLACE cells by name — with one deliberate opening: a column the
contract REQUIRES that the scan never produced may be ADDED. Without it, a row
dead-lettered as CONTRACT_REQUIRED_FIELD_MISSING is unfixable from the desk —
the one value it needs has no cell to type it into. The allowed additions are
computed from the run's recorded contract, never taken from the caller, and an
added cell is stamped like any replacement (original recorded as absent).

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
from orchestration import catalogue_contract_resolution, catalogue_reparse
from services import catalogue_conformance


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

    runtime_contract = _resolve_runtime_contract(db, run)
    changes = _apply_cells(row, cells, addable_columns=_addable_map(runtime_contract, row))
    if not changes:
        raise EvidenceCorrectionError("no cell changed — the values given match what was already read")

    corrected_at = datetime.now(timezone.utc).isoformat()
    _stamp(row, corrected_at=corrected_at, corrected_by=corrected_by, reason=reason, changes=changes)

    source_run = catalogue_reparse.evidence_source_run(db, run)
    source_observation_uuid: str | None = None
    if source_run.id != run.id:
        twin = _source_twin(db, row, source_run)
        twin_changes = _apply_cells(twin, cells, addable_columns=_addable_map(runtime_contract, twin))
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


def _resolve_runtime_contract(db: Session, run: models.IngestionRun):
    """The run's recorded contract, or None when resolution has nothing to say.

    Additions are gated on what the contract REQUIRES, so a run without a
    resolvable contract (generic extraction, a retired registry entry) simply
    offers none — replacements never fail on the registry's account.
    """
    try:
        return catalogue_contract_resolution.resolve_recorded_supplier_contract(
            db, ingestion_run_id=UUID(run.run_uuid)
        )
    except Exception:
        return None


def _addable_map(runtime_contract, row: models.CatalogueExtractedEvidence) -> dict[str, str]:
    """{column_name: field_key} this observation may gain — computed per row,
    because the addressed observation and its twin each carry their own cells."""
    if runtime_contract is None:
        return {}
    try:
        raw_cells = json.loads(row.raw_cells_json or "[]")
    except ValueError:
        return {}
    return {
        col.column_name: col.field_key
        for col in catalogue_conformance.addable_required_columns(runtime_contract, raw_cells)
    }


def _apply_cells(
    row: models.CatalogueExtractedEvidence,
    cells: dict[str, str | None],
    *,
    addable_columns: dict[str, str] | None = None,
) -> dict[str, dict[str, object]]:
    """Replace values on named columns, returning {column: {from, to}}.

    Corrections replace what was read: a column the extraction read twice is
    ambiguous and refuses rather than guessing which cell, and an unknown
    column refuses by name — except a column in ``addable_columns``, the
    contract-REQUIRED columns this observation has no cell for, which is
    APPENDED as a new cell and stamped with the field it satisfies.
    """

    raw_cells = json.loads(row.raw_cells_json or "[]")
    if not raw_cells:
        raise EvidenceCorrectionError(
            "this observation has no cells to correct — it is text evidence, not a table row"
        )
    allowed_additions = addable_columns or {}
    changes: dict[str, dict[str, object]] = {}
    for column, new_value in cells.items():
        cleaned = new_value.strip() if isinstance(new_value, str) else new_value
        cleaned = cleaned if cleaned != "" else None
        matches = [cell for cell in raw_cells if (cell.get("column_name") or "") == column]
        if not matches:
            if column in allowed_additions:
                if cleaned is None:
                    continue  # adding an empty value is not an addition
                changes[column] = {
                    "from": None,
                    "to": cleaned,
                    "added_required_field": allowed_additions[column],
                }
                raw_cells.append({"column_name": column, "raw_value": cleaned})
                continue
            known = ", ".join(sorted({str(c.get("column_name")) for c in raw_cells if c.get("column_name")}))
            message = (
                f"column {column!r} is not on this observation (it carries: {known}) — "
                "corrections replace what was read, never invent columns"
            )
            if allowed_additions:
                message += (
                    "; the only columns that may be ADDED are the contract-required ones "
                    f"the scan never read: {', '.join(sorted(allowed_additions))}"
                )
            raise EvidenceCorrectionError(message)
        if len(matches) > 1:
            raise EvidenceCorrectionError(
                f"column {column!r} appears {len(matches)} times on this observation — ambiguous, refusing to guess"
            )
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

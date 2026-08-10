"""Where a row ends up, and which ones the machine could not read.

Every normalized row in a run resolves to exactly one lane. The lanes are a
classification over records that already exist rather than a new table:
``catalogue_validation_issues`` already stores why a row failed — stage, issue
code, severity, resolution status, publish_blocking, field path, raw and
expected values — and nothing about that was missing. What was missing is a
resting place you can name and enumerate.

Before this, a blocked row was written to staging, refused a mastering
candidate, and left with its only trace a sentence in the run's error_summary
keyed by an internal UUID. The count was derivable — RAW rows minus rows that
reached a candidate — but the identity was not, so there was nothing DEV-203
could select on to re-drive "the rows that failed under this code".
See docs/architecture/what-happens-to-a-blocked-row.md.

A rejected row is deliberately NOT dead-lettered. The pipeline saying "I could
not read this" and a person saying "no" are different facts, and conflating
them would let "fix the rule and re-drive" silently re-open something a human
already answered.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy.orm import Session

import models
from schemas.catalogue_pipeline.enums import IssueResolutionStatus, IssueSeverity, ReviewStatus


class Lane(str, Enum):
    """The resting place of one normalized row at the end of a run."""

    PUBLISHED = "published"
    AWAITING_REVIEW = "awaiting_review"
    REJECTED = "rejected"
    DEAD_LETTERED = "dead_lettered"
    #: No candidate, no open blocking issue, not published. A row here is a
    #: defect in the pipeline or in this classification — it is the case the
    #: invariant exists to surface, never a normal outcome.
    UNACCOUNTED = "unaccounted"


_REJECTED_STATES = {ReviewStatus.REJECTED.value}


@dataclass(frozen=True)
class DeadLetter:
    """One row the machine could not interpret, with enough identity to re-drive it."""

    catalogue_item_uuid: str
    ingestion_run_uuid: str
    supplier_id: int | None
    supplier_sku: str | None
    stage: str
    issue_code: str
    field_path: str | None
    raw_value_json: str | None
    review_guidance: str | None
    first_seen_at: str
    age_days: int | None


@dataclass(frozen=True)
class LaneReport:
    """Every row in the run, in exactly one lane."""

    ingestion_run_uuid: str
    lanes: dict[str, list[str]] = field(default_factory=dict)

    @property
    def counts(self) -> dict[str, int]:
        return {lane: len(items) for lane, items in self.lanes.items()}

    @property
    def total(self) -> int:
        return sum(len(items) for items in self.lanes.values())


def _age_days(created_at: str | None) -> int | None:
    if not created_at:
        return None
    try:
        stamped = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - stamped).days)


def _open_blocking_by_item(db: Session, run_uuid: str) -> dict[str, models.CatalogueValidationIssue]:
    """The earliest open blocking issue per row — the one that stopped it."""
    issues = (
        db.query(models.CatalogueValidationIssue)
        .filter(
            models.CatalogueValidationIssue.ingestion_run_uuid == run_uuid,
            models.CatalogueValidationIssue.severity == IssueSeverity.BLOCKING.value,
            models.CatalogueValidationIssue.resolution_status == IssueResolutionStatus.OPEN.value,
        )
        .order_by(models.CatalogueValidationIssue.id)
        .all()
    )
    first: dict[str, models.CatalogueValidationIssue] = {}
    for issue in issues:
        first.setdefault(issue.catalogue_item_uuid, issue)
    return first


def _candidates_by_item(db: Session, run_uuid: str) -> dict[str, models.CatalogueMasteringCandidate]:
    """The live candidate per row — corrections supersede, so skip the superseded."""
    rows = (
        db.query(models.CatalogueMasteringCandidate)
        .filter(
            models.CatalogueMasteringCandidate.ingestion_run_uuid == run_uuid,
            models.CatalogueMasteringCandidate.superseded_by_uuid.is_(None),
        )
        .order_by(models.CatalogueMasteringCandidate.id)
        .all()
    )
    return {row.catalogue_item_uuid: row for row in rows}


def _published_items(db: Session, run_uuid: str) -> set[str]:
    candidate_uuids = db.query(models.CatalogueMasteringCandidate.mastering_candidate_uuid).filter(
        models.CatalogueMasteringCandidate.ingestion_run_uuid == run_uuid
    )
    rows = (
        db.query(models.CatalogueServingPublication.catalogue_item_uuid)
        .filter(
            models.CatalogueServingPublication.is_current == 1,
            models.CatalogueServingPublication.mastering_candidate_uuid.in_(candidate_uuids),
        )
        .all()
    )
    return {row[0] for row in rows if row[0]}


def lanes_for_run(db: Session, run_uuid: str) -> LaneReport:
    """Classify every normalized row of a run into exactly one lane."""
    published = _published_items(db, run_uuid)
    candidates = _candidates_by_item(db, run_uuid)
    blocking = _open_blocking_by_item(db, run_uuid)

    lanes: dict[str, list[str]] = {lane.value: [] for lane in Lane}
    rows = (
        db.query(models.CatalogueNormalizedRow.catalogue_item_uuid)
        .filter(models.CatalogueNormalizedRow.ingestion_run_uuid == run_uuid)
        .order_by(models.CatalogueNormalizedRow.id)
        .all()
    )
    for (item_uuid,) in rows:
        candidate = candidates.get(item_uuid)
        if item_uuid in published:
            lane = Lane.PUBLISHED
        elif candidate is not None and candidate.review_status in _REJECTED_STATES:
            lane = Lane.REJECTED
        elif candidate is not None:
            lane = Lane.AWAITING_REVIEW
        elif item_uuid in blocking:
            lane = Lane.DEAD_LETTERED
        else:
            lane = Lane.UNACCOUNTED
        lanes[lane.value].append(item_uuid)
    return LaneReport(ingestion_run_uuid=run_uuid, lanes=lanes)


def dead_letters(
    db: Session,
    *,
    run_uuid: str | None = None,
    issue_code: str | None = None,
) -> list[DeadLetter]:
    """Rows the machine could not interpret, newest issue last.

    Filterable by run and by issue code, because the operational question is
    "what one rule change clears the most rows" and the answer is usually a
    single code.
    """
    query = db.query(models.CatalogueValidationIssue).filter(
        models.CatalogueValidationIssue.severity == IssueSeverity.BLOCKING.value,
        models.CatalogueValidationIssue.resolution_status == IssueResolutionStatus.OPEN.value,
    )
    if run_uuid is not None:
        query = query.filter(models.CatalogueValidationIssue.ingestion_run_uuid == run_uuid)
    if issue_code is not None:
        query = query.filter(models.CatalogueValidationIssue.issue_code == issue_code)

    issues = query.order_by(models.CatalogueValidationIssue.id).all()
    if not issues:
        return []

    # A row that reached a candidate is not dead-lettered even if an issue is
    # still open against it — it is in review, where a person can see it.
    run_uuids = {issue.ingestion_run_uuid for issue in issues}
    with_candidates: set[str] = set()
    for run in run_uuids:
        with_candidates |= set(_candidates_by_item(db, run))

    sku_by_item = _supplier_skus(db, {issue.catalogue_item_uuid for issue in issues})
    supplier_by_run = dict(
        db.query(models.IngestionRun.run_uuid, models.IngestionRun.supplier_id)
        .filter(models.IngestionRun.run_uuid.in_(run_uuids))
        .all()
    )

    seen: set[str] = set()
    out: list[DeadLetter] = []
    for issue in issues:
        item = issue.catalogue_item_uuid
        if item in with_candidates or item in seen:
            continue
        seen.add(item)
        out.append(
            DeadLetter(
                catalogue_item_uuid=item,
                ingestion_run_uuid=issue.ingestion_run_uuid,
                supplier_id=supplier_by_run.get(issue.ingestion_run_uuid),
                supplier_sku=sku_by_item.get(item),
                stage=issue.stage,
                issue_code=issue.issue_code,
                field_path=issue.field_path,
                raw_value_json=issue.raw_value_json,
                review_guidance=issue.review_guidance,
                first_seen_at=issue.created_at,
                age_days=_age_days(issue.created_at),
            )
        )
    return out


def counts_by_issue_code(db: Session, *, run_uuid: str | None = None) -> dict[str, int]:
    """How many rows each code is holding, most first.

    The operational question this answers: which single rule change clears the
    most rows.
    """
    tally: dict[str, int] = {}
    for entry in dead_letters(db, run_uuid=run_uuid):
        tally[entry.issue_code] = tally.get(entry.issue_code, 0) + 1
    return dict(sorted(tally.items(), key=lambda pair: (-pair[1], pair[0])))


def _supplier_skus(db: Session, item_uuids: set[str]) -> dict[str, str]:
    """The supplier's own code for each row, so a person can recognise it.

    A dead-letter entry a human cannot identify is only marginally better than
    no entry at all.
    """
    if not item_uuids:
        return {}
    out: dict[str, str] = {}
    rows = (
        db.query(
            models.CatalogueNormalizedRow.catalogue_item_uuid,
            models.CatalogueNormalizedRow.normalized_fields_json,
        )
        .filter(models.CatalogueNormalizedRow.catalogue_item_uuid.in_(item_uuids))
        .all()
    )
    for item_uuid, fields_json in rows:
        try:
            fields = json.loads(fields_json or "{}")
        except ValueError:
            continue
        value = ((fields.get("supplier_sku") or {}).get("value") or "").strip()
        if value:
            out[item_uuid] = value
    return out

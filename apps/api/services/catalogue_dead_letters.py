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
from typing import Iterable

from sqlalchemy.orm import Session

import models
from schemas.catalogue_pipeline.enums import IssueResolutionStatus, IssueSeverity, ReviewStatus

#: SQLite caps host parameters per statement, so an IN over every row of a
#: large catalogue has to be fed in bites. Well under the oldest limit (999).
_IN_CHUNK = 400


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
    #: EVERY open blocking code on the row, not just the first. A row held by
    #: two codes is not cleared by fixing one of them.
    issue_codes: tuple[str, ...]
    field_path: str | None
    raw_value_json: str | None
    review_guidance: str | None
    first_seen_at: str
    age_days: int | None

    @property
    def issue_code(self) -> str:
        """The earliest code, for display where one has to be shown."""
        return self.issue_codes[0]


@dataclass(frozen=True)
class IssueCodeTally:
    """What one rule change would actually buy.

    ``rows_blocked`` counts every row the code appears on. ``rows_cleared_if_fixed``
    counts only the rows where it is the ONLY thing holding them, which is the
    honest answer to "which single fix frees the most rows" — a row held by two
    codes is freed by neither on its own.
    """

    issue_code: str
    rows_blocked: int
    rows_cleared_if_fixed: int


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


@dataclass(frozen=True)
class Reconciliation:
    """The RAW layer against the lanes, so dedup cannot be mistaken for loss.

    Counting rows in and rows out gives a difference; it does not say whether
    the difference is a product silently dropped or the same product listed at
    three order quantities and collapsed into one. ``unlinked_product_rows`` is
    that difference, and the caller is expected to account for it rather than
    assume.
    """

    ingestion_run_uuid: str
    raw_observations: int
    raw_text_observations: int
    raw_product_rows: int
    normalized_rows: int
    unlinked_product_rows: int
    lane_counts: dict[str, int]

    @property
    def lanes_cover_normalized_rows(self) -> bool:
        return sum(self.lane_counts.values()) == self.normalized_rows


def _chunked(values: Iterable[str], size: int = _IN_CHUNK) -> Iterable[list[str]]:
    batch: list[str] = []
    for value in values:
        batch.append(value)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _age_days(created_at: str | None) -> int | None:
    """Whole days since the issue was raised.

    None only when there is no timestamp at all. A timestamp that is present
    and unparseable raises: showing "age unknown" would hide a storage format
    drifting out from under this.
    """
    if not created_at:
        return None
    try:
        stamped = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"validation issue created_at is not ISO-8601: {created_at!r}") from exc
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - stamped).days)


def _candidate_items(db: Session, run_uuids: Iterable[str]) -> set[str]:
    """Rows that reached a live mastering candidate, across the given runs.

    One query for all runs rather than one per run — corrections supersede, so
    a superseded candidate does not count as having reached review.
    """
    runs = list(dict.fromkeys(run_uuids))
    if not runs:
        return set()
    found: set[str] = set()
    for batch in _chunked(runs):
        rows = (
            db.query(models.CatalogueMasteringCandidate.catalogue_item_uuid)
            .filter(
                models.CatalogueMasteringCandidate.ingestion_run_uuid.in_(batch),
                models.CatalogueMasteringCandidate.superseded_by_uuid.is_(None),
            )
            .all()
        )
        found |= {row[0] for row in rows}
    return found


def _candidates_by_item(db: Session, run_uuid: str) -> dict[str, models.CatalogueMasteringCandidate]:
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


def _open_blocking(db: Session, **filters) -> list[models.CatalogueValidationIssue]:
    query = db.query(models.CatalogueValidationIssue).filter(
        models.CatalogueValidationIssue.severity == IssueSeverity.BLOCKING.value,
        models.CatalogueValidationIssue.resolution_status == IssueResolutionStatus.OPEN.value,
    )
    for column, value in filters.items():
        if value is not None:
            query = query.filter(getattr(models.CatalogueValidationIssue, column) == value)
    return query.order_by(models.CatalogueValidationIssue.id).all()


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
    blocking = {issue.catalogue_item_uuid for issue in _open_blocking(db, ingestion_run_uuid=run_uuid)}

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


def reconcile(db: Session, run_uuid: str) -> Reconciliation:
    """The RAW layer against the lanes.

    Product rows that carry no link to a normalized row are reported, not
    explained. On a real catalogue most of them are the same product listed at
    several order quantities and collapsed into one row — but "most" is not
    "all", and the difference between a duplicate and a loss is the whole
    point, so the number is surfaced for a caller to account for.
    """
    evidence = (
        db.query(
            models.CatalogueExtractedEvidence.raw_observation_uuid,
            models.CatalogueExtractedEvidence.raw_cells_json,
        )
        .filter(models.CatalogueExtractedEvidence.ingestion_run_uuid == run_uuid)
        .all()
    )
    product_uuids: set[str] = set()
    text_rows = 0
    for observation_uuid, cells_json in evidence:
        try:
            cells = json.loads(cells_json or "[]")
        except ValueError:
            cells = []
        if cells:
            product_uuids.add(observation_uuid)
        else:
            text_rows += 1

    linked: set[str] = set()
    for batch in _chunked(product_uuids):
        rows = (
            db.query(models.CatalogueNormalizedRowEvidence.raw_observation_uuid)
            .filter(models.CatalogueNormalizedRowEvidence.raw_observation_uuid.in_(batch))
            .all()
        )
        linked |= {row[0] for row in rows}

    normalized = (
        db.query(models.CatalogueNormalizedRow)
        .filter(models.CatalogueNormalizedRow.ingestion_run_uuid == run_uuid)
        .count()
    )
    return Reconciliation(
        ingestion_run_uuid=run_uuid,
        raw_observations=len(evidence),
        raw_text_observations=text_rows,
        raw_product_rows=len(product_uuids),
        normalized_rows=normalized,
        unlinked_product_rows=len(product_uuids - linked),
        lane_counts=lanes_for_run(db, run_uuid).counts,
    )


def dead_letters(
    db: Session,
    *,
    run_uuid: str | None = None,
    issue_code: str | None = None,
    stage: str | None = None,
) -> list[DeadLetter]:
    """Rows the machine could not interpret.

    Filterable by run, stage and issue code, because the operational question
    is "what one rule change clears the most rows" and the answer is usually a
    single code at a single stage.

    Filtering by code narrows which ROWS are returned; each row still reports
    every code holding it, so a row that also needs another fix says so.
    """
    matched = _open_blocking(db, ingestion_run_uuid=run_uuid, issue_code=issue_code, stage=stage)
    if not matched:
        return []
    wanted = {issue.catalogue_item_uuid for issue in matched}

    # Every open blocking issue on the matched rows, so issue_codes is complete
    # even when the caller filtered to one code.
    all_issues = _open_blocking(db, ingestion_run_uuid=run_uuid)
    by_item: dict[str, list[models.CatalogueValidationIssue]] = {}
    for issue in all_issues:
        if issue.catalogue_item_uuid in wanted:
            by_item.setdefault(issue.catalogue_item_uuid, []).append(issue)

    # A row that reached a candidate is not dead-lettered even if an issue is
    # still open against it — it is in review, where a person can see it.
    with_candidates = _candidate_items(db, {issue.ingestion_run_uuid for issue in matched})
    sku_by_item = _supplier_skus(db, set(by_item))
    supplier_by_run = _suppliers_by_run(db, {issue.ingestion_run_uuid for issue in matched})

    out: list[DeadLetter] = []
    for item_uuid, issues in by_item.items():
        if item_uuid in with_candidates:
            continue
        first = issues[0]
        out.append(
            DeadLetter(
                catalogue_item_uuid=item_uuid,
                ingestion_run_uuid=first.ingestion_run_uuid,
                supplier_id=supplier_by_run.get(first.ingestion_run_uuid),
                supplier_sku=sku_by_item.get(item_uuid),
                stage=first.stage,
                issue_codes=tuple(dict.fromkeys(issue.issue_code for issue in issues)),
                field_path=first.field_path,
                raw_value_json=first.raw_value_json,
                review_guidance=first.review_guidance,
                first_seen_at=first.created_at,
                age_days=_age_days(first.created_at),
            )
        )
    return out


def tallies_by_issue_code(db: Session, *, run_uuid: str | None = None) -> list[IssueCodeTally]:
    """Which single rule change frees the most rows, ordered by that answer.

    Computed from one pass over the dead letters rather than re-querying per
    code. ``rows_blocked`` will exceed the row count when rows carry more than
    one code; ``rows_cleared_if_fixed`` will not.
    """
    blocked: dict[str, int] = {}
    solely: dict[str, int] = {}
    for entry in dead_letters(db, run_uuid=run_uuid):
        for code in entry.issue_codes:
            blocked[code] = blocked.get(code, 0) + 1
        if len(entry.issue_codes) == 1:
            only = entry.issue_codes[0]
            solely[only] = solely.get(only, 0) + 1
    return sorted(
        (
            IssueCodeTally(
                issue_code=code,
                rows_blocked=count,
                rows_cleared_if_fixed=solely.get(code, 0),
            )
            for code, count in blocked.items()
        ),
        key=lambda tally: (-tally.rows_cleared_if_fixed, -tally.rows_blocked, tally.issue_code),
    )


def _suppliers_by_run(db: Session, run_uuids: set[str]) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for batch in _chunked(run_uuids):
        rows = (
            db.query(models.IngestionRun.run_uuid, models.IngestionRun.supplier_id)
            .filter(models.IngestionRun.run_uuid.in_(batch))
            .all()
        )
        out.update({run: supplier for run, supplier in rows})
    return out


def _supplier_skus(db: Session, item_uuids: set[str]) -> dict[str, str]:
    """The supplier's own code for each row, so a person can recognise it.

    A dead-letter entry a human cannot identify is only marginally better than
    no entry at all.
    """
    out: dict[str, str] = {}
    for batch in _chunked(item_uuids):
        rows = (
            db.query(
                models.CatalogueNormalizedRow.catalogue_item_uuid,
                models.CatalogueNormalizedRow.normalized_fields_json,
            )
            .filter(models.CatalogueNormalizedRow.catalogue_item_uuid.in_(batch))
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

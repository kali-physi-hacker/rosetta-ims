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
    #: Published once, and a later run has replaced it. Reviewed, approved and
    #: live at the time — history, not outstanding work. Without this lane a
    #: re-upload silently turns every previously published row of the older run
    #: back into "awaiting review", which reads as work nobody has done.
    SUPERSEDED = "superseded"
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
    #: How many runs have tried this row: 1, plus one per retrigger that
    #: selected it and still could not read it. A row re-driven three times is
    #: one entry carrying attempts=3, not three entries.
    attempts: int = 1
    #: The observations behind this row — the handles a retrigger re-drives.
    observation_uuids: tuple[str, ...] = ()

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


def _publication_items(db: Session, run_uuid: str) -> tuple[set[str], set[str]]:
    """Rows this run published, split into still-live and since-replaced.

    Both matter. Asking only "is a publication current" answers the wrong
    question for an older run, whose publications a later run supersedes —
    those rows were reviewed and put live, and reporting them as unreviewed
    would invent outstanding work out of ordinary history.
    """
    candidate_uuids = db.query(models.CatalogueMasteringCandidate.mastering_candidate_uuid).filter(
        models.CatalogueMasteringCandidate.ingestion_run_uuid == run_uuid
    )
    rows = (
        db.query(
            models.CatalogueServingPublication.catalogue_item_uuid,
            models.CatalogueServingPublication.is_current,
        )
        .filter(models.CatalogueServingPublication.mastering_candidate_uuid.in_(candidate_uuids))
        .all()
    )
    current = {item for item, is_current in rows if item and is_current}
    ever = {item for item, _ in rows if item}
    return current, ever - current


def lanes_for_run(db: Session, run_uuid: str) -> LaneReport:
    """Classify every normalized row of a run into exactly one lane."""
    published, superseded = _publication_items(db, run_uuid)
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
        elif item_uuid in superseded:
            lane = Lane.SUPERSEDED
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

    # The link table carries no run column, so this matches on observation UUID
    # alone. Safe because observation identities do not repeat across runs —
    # asserted by the re-upload test, which ingests the same file twice and
    # finds zero shared UUIDs. If that ever changes, this silently over-counts
    # links and the reconciliation stops catching drops.
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
    follow_retriggers: bool = True,
) -> list[DeadLetter]:
    """Rows the machine could not interpret.

    Filterable by run, stage and issue code, because the operational question
    is "what one rule change clears the most rows" and the answer is usually a
    single code at a single stage.

    Filtering by code narrows which ROWS are returned; each row still reports
    every code holding it, so a row that also needs another fix says so.

    With ``follow_retriggers`` (the default, run-scoped only) the queue is the
    CURRENT state: rows a retrigger has since cleared are gone, and rows that
    failed again appear once, carrying attempts and the latest codes. Pass
    False for the per-run historical view. Lanes never follow — what a run
    could not read remains true of that run.
    """
    matched = _open_blocking(db, ingestion_run_uuid=run_uuid, issue_code=issue_code, stage=stage)
    if not matched:
        return []
    wanted = {issue.catalogue_item_uuid for issue in matched}

    # Every open blocking issue on the matched rows, so issue_codes is complete
    # even when the caller filtered to one code. Unfiltered, that is the same
    # query again — reuse it rather than paying twice, which the tallies would.
    all_issues = (
        matched
        if issue_code is None and stage is None
        else _open_blocking(db, ingestion_run_uuid=run_uuid)
    )
    by_item: dict[str, list[models.CatalogueValidationIssue]] = {}
    for issue in all_issues:
        if issue.catalogue_item_uuid in wanted:
            by_item.setdefault(issue.catalogue_item_uuid, []).append(issue)

    # A row that reached a candidate is not dead-lettered even if an issue is
    # still open against it — it is in review, where a person can see it.
    with_candidates = _candidate_items(db, {issue.ingestion_run_uuid for issue in matched})
    sku_by_item = _supplier_skus(db, set(by_item))
    supplier_by_run = _suppliers_by_run(db, {issue.ingestion_run_uuid for issue in matched})
    links_by_run: dict[str, dict[str, tuple[str, ...]]] = {
        run: evidence_links(db, run) for run in {issue.ingestion_run_uuid for issue in matched}
    }

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
                observation_uuids=links_by_run.get(first.ingestion_run_uuid, {}).get(item_uuid, ()),
            )
        )
    if follow_retriggers and run_uuid is not None:
        out = _follow_retriggers(db, run_uuid, out)
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


def evidence_links(db: Session, run_uuid: str) -> dict[str, tuple[str, ...]]:
    """catalogue_item_uuid -> the raw observation UUIDs its row was built from.

    The link table itself has no run column; scoping happens through the run's
    normalized rows. For a re-parse or retrigger child the observations belong
    to the SOURCE run — the links still point at them, which is exactly what
    lets a retrigger's outcome be matched back to the rows it re-drove.
    """
    rows = (
        db.query(
            models.CatalogueNormalizedRow.catalogue_item_uuid,
            models.CatalogueNormalizedRowEvidence.raw_observation_uuid,
        )
        .join(
            models.CatalogueNormalizedRowEvidence,
            models.CatalogueNormalizedRowEvidence.staging_item_id == models.CatalogueNormalizedRow.id,
        )
        .filter(models.CatalogueNormalizedRow.ingestion_run_uuid == run_uuid)
        .order_by(models.CatalogueNormalizedRowEvidence.id)
        .all()
    )
    out: dict[str, list[str]] = {}
    for item_uuid, observation_uuid in rows:
        if observation_uuid:
            out.setdefault(item_uuid, []).append(observation_uuid)
    return {item: tuple(obs) for item, obs in out.items()}


def observation_keys(db: Session, run_uuid: str, observation_uuids: Iterable[str]) -> dict[str, str]:
    """raw_observation_uuid -> source_object_key, within one run.

    Observation UUIDs are minted per run — the same printed row gets a fresh
    identity every time it is captured, including by a retrigger. The
    source_object_key ("page:1:obs:<hash>:<n>") is derived from content and
    position, so it is the one name a row keeps across runs, and every match
    between a selection and a later run's outcome crosses on it.
    """
    wanted = list(dict.fromkeys(observation_uuids))
    out: dict[str, str] = {}
    for batch in _chunked(wanted):
        rows = (
            db.query(
                models.CatalogueExtractedEvidence.raw_observation_uuid,
                models.CatalogueExtractedEvidence.source_object_key,
            )
            .filter(
                models.CatalogueExtractedEvidence.ingestion_run_uuid == run_uuid,
                models.CatalogueExtractedEvidence.raw_observation_uuid.in_(batch),
            )
            .all()
        )
        out.update({uuid: key for uuid, key in rows if key})
    return out


def observation_uuids_for_keys(db: Session, run_uuid: str, keys: Iterable[str]) -> dict[str, str]:
    """source_object_key -> raw_observation_uuid, within one run. Inverse of the above."""
    wanted = list(dict.fromkeys(keys))
    out: dict[str, str] = {}
    for batch in _chunked(wanted):
        rows = (
            db.query(
                models.CatalogueExtractedEvidence.source_object_key,
                models.CatalogueExtractedEvidence.raw_observation_uuid,
            )
            .filter(
                models.CatalogueExtractedEvidence.ingestion_run_uuid == run_uuid,
                models.CatalogueExtractedEvidence.source_object_key.in_(batch),
            )
            .all()
        )
        out.update({key: uuid for key, uuid in rows if key})
    return out


def retrigger_children(db: Session, run_uuid: str) -> list[models.IngestionRun]:
    """Retrigger runs spawned from this run, oldest first.

    Identified by lineage plus the selection metric rather than a status: a
    plain re-parse child is NOT a retrigger and must not affect the queue —
    it re-reads everything, so its failures are its own run's story.

    One level of lineage is the whole story: the service refuses to retrigger
    a retrigger child, so every retrigger of this run is a direct sibling here
    and a chain cannot form.
    """
    parent = db.query(models.IngestionRun).filter_by(run_uuid=run_uuid).first()
    if parent is None:
        return []
    children = (
        db.query(models.IngestionRun)
        .filter(models.IngestionRun.parent_run_id == parent.id)
        .order_by(models.IngestionRun.id)
        .all()
    )
    out = []
    for child in children:
        try:
            metrics = json.loads(child.metrics or "{}") or {}
        except ValueError:
            continue
        if metrics.get("retrigger_observations"):
            out.append(child)
    return out


_COMPLETED_STATUSES = (
    models.IngestionRunStatus.COMPLETED.value,
    models.IngestionRunStatus.COMPLETED_WITH_WARNINGS.value,
)


def reread_children(db: Session, run_uuid: str) -> list[models.IngestionRun]:
    """Completed children that RE-READ this run's evidence, whatever the reason.

    Two shapes qualify: a re-parse under a SIBLING contract (a mixed-layout
    document's other format), and a plain re-parse under the run's own
    recorded contract after that contract learned something — new aliases, a
    merged layout. Both make rows reviewable that this run could not read,
    and the queue follows their SUCCESSES the same way. Retrigger children
    never appear here: they carry their own attempts accounting.
    """
    parent = db.query(models.IngestionRun).filter_by(run_uuid=run_uuid).first()
    if parent is None:
        return []
    children = (
        db.query(models.IngestionRun)
        .filter(models.IngestionRun.parent_run_id == parent.id)
        .order_by(models.IngestionRun.id)
        .all()
    )
    out: list[models.IngestionRun] = []
    for child in children:
        if child.status not in _COMPLETED_STATUSES:
            continue
        try:
            metrics = json.loads(child.metrics or "{}") or {}
        except ValueError:
            metrics = {}
        if metrics.get("retrigger_observations"):
            continue
        out.append(child)
    return out


@dataclass(frozen=True)
class Reread:
    """One completed re-read of this run's evidence, as the desk shows it."""

    run_uuid: str
    contract_id: str | None
    format_name: str
    status: str
    candidates: int


def rereads(db: Session, run_uuid: str) -> tuple[Reread, ...]:
    """Every completed re-read, with what it made reviewable."""
    out: list[Reread] = []
    for child in reread_children(db, run_uuid):
        format_name = child.supplier_source_contract_id or "generic extraction"
        try:
            from schemas.catalogue_pipeline.supplier_contracts import get_supplier_source_contract

            format_name = get_supplier_source_contract(
                child.supplier_source_contract_id, child.supplier_source_contract_version or "v1"
            ).declaration.format_name
        except Exception:
            pass
        out.append(
            Reread(
                run_uuid=child.run_uuid,
                contract_id=child.supplier_source_contract_id,
                format_name=format_name,
                status=child.status,
                candidates=len(set(_candidates_by_item(db, child.run_uuid))),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class RetriggerAttempt:
    """One retrigger child of a run, as the desk needs to see it.

    The queue deliberately ignores a child that has not finished — but a
    PERSON watching the held lane must not: a queued or running attempt is
    why "nothing changed yet", and a failed one is why nothing ever will
    until it is re-fired. This is that visibility, nothing more.
    """

    run_uuid: str
    status: str
    attempt: int
    observations: int
    created_at: str | None


def retrigger_attempts(db: Session, run_uuid: str) -> tuple[RetriggerAttempt, ...]:
    """Every retrigger fired at this run's queue, oldest first, with status."""
    out: list[RetriggerAttempt] = []
    for child in retrigger_children(db, run_uuid):
        metrics = json.loads(child.metrics or "{}") or {}
        out.append(
            RetriggerAttempt(
                run_uuid=child.run_uuid,
                status=child.status,
                attempt=int(metrics.get("retrigger_attempt") or 0),
                observations=len(metrics.get("retrigger_observations") or ()),
                created_at=str(child.created_at) if child.created_at is not None else None,
            )
        )
    return tuple(out)


def _follow_retriggers(db: Session, run_uuid: str, entries: list[DeadLetter]) -> list[DeadLetter]:
    """The queue after re-drives: cleared rows leave, survivors carry attempts.

    Lanes stay per-run history — a row the parent could not read is still a
    fact about the parent. The QUEUE is the actionable view, so it follows the
    chain: an observation whose latest retrigger produced a candidate is no
    longer stuck anywhere a person needs to act, and an observation that failed
    again is one entry with a count, not one entry per attempt. Completed
    RE-READS (a re-parse under a sibling contract, or a plain re-parse after
    the contract learned something) clear the same way: a row that became a
    candidate THERE has its work on that run's desk.

    All matching crosses runs on source_object_key, because observation UUIDs
    are re-minted per run — a child's links never carry the source's UUIDs.
    """
    children = retrigger_children(db, run_uuid)
    if not entries or (not children and not reread_children(db, run_uuid)):
        return entries

    from dataclasses import replace

    # This run's entries, translated to the names their rows keep across runs.
    entry_uuid_to_key = observation_keys(
        db, run_uuid, (obs for entry in entries for obs in entry.observation_uuids)
    )

    attempts_by_key: dict[str, int] = {}
    cleared_keys: set[str] = set()
    latest_codes_by_key: dict[str, tuple[str, ...]] = {}

    for child in children:
        # Only a retrigger that RAN counts. A queued or running child has
        # selection and no rows yet — reading that as "absorbed" empties the
        # queue the instant the 202 comes back, hours before the worker touches
        # a row. A failed child processed nothing either, and treating its
        # silence as success would vanish the very rows it failed to fix.
        if child.status not in (
            models.IngestionRunStatus.COMPLETED.value,
            models.IngestionRunStatus.COMPLETED_WITH_WARNINGS.value,
        ):
            continue
        metrics = json.loads(child.metrics or "{}") or {}
        selection_uuids = metrics.get("retrigger_observations") or ()
        if not selection_uuids:
            continue
        source_run = metrics.get("reparse_of") or run_uuid
        selection_keys = set(observation_keys(db, source_run, selection_uuids).values())
        if not selection_keys:
            continue

        child_links = evidence_links(db, child.run_uuid)
        child_uuid_to_key = observation_keys(
            db, child.run_uuid, (obs for obs_list in child_links.values() for obs in obs_list)
        )
        child_candidates = set(_candidates_by_item(db, child.run_uuid))
        blocked_codes: dict[str, list[str]] = {}
        for issue in _open_blocking(db, ingestion_run_uuid=child.run_uuid):
            blocked_codes.setdefault(issue.catalogue_item_uuid, []).append(issue.issue_code)

        keys_seen_in_child: set[str] = set()
        for item_uuid, obs_uuids in child_links.items():
            for obs in obs_uuids:
                key = child_uuid_to_key.get(obs)
                if key is None or key not in selection_keys:
                    continue
                keys_seen_in_child.add(key)
                attempts_by_key[key] = attempts_by_key.get(key, 1) + 1
                if item_uuid in child_candidates:
                    cleared_keys.add(key)
                    latest_codes_by_key.pop(key, None)
                elif item_uuid in blocked_codes:
                    cleared_keys.discard(key)
                    latest_codes_by_key[key] = tuple(dict.fromkeys(blocked_codes[item_uuid]))
        # A selected observation producing NO row in the child was absorbed as
        # a duplicate tier of a row that now parses — a term captured on a row
        # that made it, not a row still stuck.
        for key in selection_keys - keys_seen_in_child:
            attempts_by_key[key] = attempts_by_key.get(key, 1) + 1
            cleared_keys.add(key)

    # Re-reads clear too — a sibling-format re-parse OR a plain re-parse
    # after the contract learned something. When a completed re-read produced
    # a candidate for a row's key, that row is no longer stuck anywhere a
    # person needs to act: its work moved to that run's review queue. Only
    # candidates count, and they win over earlier re-blocks. Re-read FAILURES
    # change nothing here — a full re-parse re-reads everything, so its
    # failures are its own run's story.
    for child in reread_children(db, run_uuid):
        child_links = evidence_links(db, child.run_uuid)
        child_uuid_to_key = observation_keys(
            db, child.run_uuid, (obs for obs_list in child_links.values() for obs in obs_list)
        )
        child_candidates = set(_candidates_by_item(db, child.run_uuid))
        for item_uuid, obs_uuids in child_links.items():
            if item_uuid not in child_candidates:
                continue
            for obs in obs_uuids:
                key = child_uuid_to_key.get(obs)
                if key is not None:
                    cleared_keys.add(key)
                    latest_codes_by_key.pop(key, None)

    out: list[DeadLetter] = []
    for entry in entries:
        keys = [entry_uuid_to_key[obs] for obs in entry.observation_uuids if obs in entry_uuid_to_key]
        if keys and all(key in cleared_keys for key in keys):
            continue
        attempts = max((attempts_by_key.get(key, 1) for key in keys), default=1)
        codes = next((latest_codes_by_key[key] for key in keys if key in latest_codes_by_key), entry.issue_codes)
        if attempts != entry.attempts or codes != entry.issue_codes:
            entry = replace(entry, attempts=attempts, issue_codes=codes)
        out.append(entry)
    return out


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


# ── Cross-run visibility: the "what is rotting" view (DEV-303) ───────────────


@dataclass(frozen=True)
class HeldDocument:
    """The CURRENT reading of one uploaded document, with what is still stuck.

    Current = the newest completed ROOT run per (supplier, document checksum),
    so a re-upload replaces its predecessor here instead of double-counting the
    same printed rows. The held count is the FOLLOWED queue — rows a re-drive
    or re-read already rescued are not "stuck" anywhere.
    """

    ingestion_run_uuid: str
    filename: str | None
    submitted_at: str | None
    rows: int
    held: int
    held_share: float
    oldest_age_days: int | None
    top_issue_code: str | None
    top_issue_rows: int


@dataclass(frozen=True)
class SupplierHeldSummary:
    """One supplier's held picture across every current document."""

    supplier_id: int
    supplier_name: str | None
    documents: tuple[HeldDocument, ...]
    held_total: int
    oldest_age_days: int | None
    #: Median held share of this supplier's OLDER completed roots (superseded
    #: uploads included) — "the usual pattern". None until one exists.
    baseline_share: float | None
    latest_share: float | None
    #: True when the latest reading is notably worse than the baseline —
    #: the stated rule, not a black box: share exceeds BOTH 1.5× the baseline
    #: and baseline + 10 points, with something actually held.
    worse_than_usual: bool


def held_overview(db: Session) -> tuple[SupplierHeldSummary, ...]:
    """Held rows across suppliers and documents — the queue as an operated
    system, answering "what is held, how long, and is it getting worse?"
    without opening runs one by one."""

    roots = (
        db.query(models.IngestionRun)
        .filter(
            models.IngestionRun.parent_run_id.is_(None),
            models.IngestionRun.status.in_(_COMPLETED_STATUSES),
            models.IngestionRun.supplier_id.isnot(None),
        )
        .all()
    )
    # Newest SUBMISSION first — the first run seen per document is "current".
    roots.sort(key=lambda run: (str(run.created_at or ""), run.id), reverse=True)

    def _doc_key(run: models.IngestionRun) -> str:
        source = None
        if run.catalogue_source_document_id:
            source = db.get(models.CatalogueSourceDocument, run.catalogue_source_document_id)
        if source is not None and source.source_checksum:
            return f"checksum:{source.source_checksum}"
        return f"run:{run.run_uuid}"

    def _rows(run_uuid: str) -> int:
        return (
            db.query(models.CatalogueNormalizedRow)
            .filter_by(ingestion_run_uuid=run_uuid)
            .count()
        )

    current: dict[tuple[int, str], models.IngestionRun] = {}
    older_shares: dict[int, list[float]] = {}
    for run in roots:  # newest first — the first run per document wins
        key = (run.supplier_id, _doc_key(run))
        if key not in current:
            current[key] = run
        else:
            rows = _rows(run.run_uuid)
            if rows:
                held = len(dead_letters(db, run_uuid=run.run_uuid))
                older_shares.setdefault(run.supplier_id, []).append(held / rows)

    by_supplier: dict[int, list[HeldDocument]] = {}
    for (supplier_id, _), run in current.items():
        rows = _rows(run.run_uuid)
        if not rows:
            continue
        entries = dead_letters(db, run_uuid=run.run_uuid)
        code_counts: dict[str, int] = {}
        oldest: int | None = None
        for entry in entries:
            code_counts[entry.issue_code] = code_counts.get(entry.issue_code, 0) + 1
            if entry.age_days is not None and (oldest is None or entry.age_days > oldest):
                oldest = entry.age_days
        top_code = max(code_counts, key=code_counts.get) if code_counts else None
        filename = None
        if run.catalogue_source_document_id:
            source = db.get(models.CatalogueSourceDocument, run.catalogue_source_document_id)
            filename = source.filename if source is not None else None
        by_supplier.setdefault(supplier_id, []).append(
            HeldDocument(
                ingestion_run_uuid=run.run_uuid,
                filename=filename,
                submitted_at=str(run.created_at) if run.created_at is not None else None,
                rows=rows,
                held=len(entries),
                held_share=round(len(entries) / rows, 4),
                oldest_age_days=oldest,
                top_issue_code=top_code,
                top_issue_rows=code_counts.get(top_code, 0) if top_code else 0,
            )
        )

    supplier_names: dict[int, str] = {
        supplier.id: supplier.name
        for supplier in db.query(models.Supplier).filter(models.Supplier.id.in_(list(by_supplier))).all()
    } if by_supplier else {}

    out: list[SupplierHeldSummary] = []
    for supplier_id, documents in by_supplier.items():
        documents = sorted(documents, key=lambda d: d.submitted_at or "", reverse=True)
        held_total = sum(d.held for d in documents)
        ages = [d.oldest_age_days for d in documents if d.oldest_age_days is not None]
        shares = sorted(older_shares.get(supplier_id, []))
        baseline = shares[len(shares) // 2] if shares else None
        latest = documents[0].held_share if documents else None
        worse = (
            baseline is not None
            and latest is not None
            and held_total > 0
            and latest > max(baseline * 1.5, baseline + 0.10)
        )
        out.append(
            SupplierHeldSummary(
                supplier_id=supplier_id,
                supplier_name=supplier_names.get(supplier_id),
                documents=tuple(documents),
                held_total=held_total,
                oldest_age_days=max(ages) if ages else None,
                baseline_share=round(baseline, 4) if baseline is not None else None,
                latest_share=round(latest, 4) if latest is not None else None,
                worse_than_usual=worse,
            )
        )
    # Most stuck first — the screen is for finding trouble.
    return tuple(sorted(out, key=lambda s: s.held_total, reverse=True))

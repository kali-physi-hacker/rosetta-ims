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


def _follow_retriggers(db: Session, run_uuid: str, entries: list[DeadLetter]) -> list[DeadLetter]:
    """The queue after retriggers: cleared rows leave, survivors carry attempts.

    Lanes stay per-run history — a row the parent could not read is still a
    fact about the parent. The QUEUE is the actionable view, so it follows the
    chain: an observation whose latest retrigger produced a candidate is no
    longer stuck anywhere a person needs to act, and an observation that failed
    again is one entry with a count, not one entry per attempt.

    All matching crosses runs on source_object_key, because observation UUIDs
    are re-minted per run — a child's links never carry the source's UUIDs.
    """
    children = retrigger_children(db, run_uuid)
    if not children or not entries:
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

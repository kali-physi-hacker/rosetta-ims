"""Pressing the button: read Royal Canin's webshop and submit what changed.

The connector produces a snapshot; this decides whether it is worth ingesting
and hands it to the ordinary submission path if it is. Three judgements live
here, and all three exist to stop the pipeline acting on a bad read:

* an UNCHANGED catalogue submits nothing — a re-read is not a new document,
  and a run per button-press would bury the desk in identical work;
* a SHRUNKEN catalogue is refused by default — a part-finished read looks
  exactly like Royal Canin delisting half their range, and only a person can
  tell those apart;
* an EMPTY range is an error, never an empty catalogue.

One read becomes one run PER SUPPLIER, and each supplier is judged alone. A
refusal on one therefore cannot un-queue the other: a submission commits the
moment it is made, so every outcome is REPORTED rather than thrown away, and
the exception is raised only when nothing at all was submitted — the one case
where "nothing was submitted" is true.
"""

from __future__ import annotations

import csv
import io
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session

import models
from services import royal_canin_connector as connector
from services.catalogue_submission import (
    CatalogueSubmissionCommand,
    CatalogueSubmissionService,
)
from services.source_capability import DEFAULT_UPLOAD_ROOT

#: Royal Canin invoices as two suppliers with different rebates and terms, so
#: one read of the shop becomes one run per supplier (user ruling 2026-08-28).
CONTRACT_VERSION = "v1"
SUPPLIERS: dict[str, dict] = {
    connector.RANGE_VET: {
        "supplier_id": 40,
        "label": "Royal Canin (Vet)",
        "contract_id": "royal_canin.vet_webshop_snapshot.v1",
    },
    connector.RANGE_NON_VET: {
        "supplier_id": 39,
        "label": "Royal Canin (Non Vet)",
        "contract_id": "royal_canin.non_vet_webshop_snapshot.v1",
    },
}
#: Kept for callers that still speak of "the" Royal Canin supplier.
SUPPLIER_ID = SUPPLIERS[connector.RANGE_VET]["supplier_id"]
CONTRACT_ID = SUPPLIERS[connector.RANGE_VET]["contract_id"]


class RoyalCaninCaptureRefused(RuntimeError):
    """A read a person must look at before it becomes catalogue truth.

    Raised only when the whole capture queued nothing. It carries every
    supplier's outcome so the caller can still say what the other half of the
    read found — "unchanged" is an answer, and losing it to an exception would
    leave a person guessing whether that supplier was even looked at.
    """

    def __init__(self, message: str, outcomes: "list[CaptureOutcome] | None" = None):
        super().__init__(message)
        self.outcomes: list[CaptureOutcome] = list(outcomes or ())


@dataclass(frozen=True)
class CaptureOutcome:
    """What one press of the button did, for ONE of the two suppliers."""

    status: str                       # "submitted" | "unchanged" | "refused"
    row_count: int
    checksum: str
    filename: str
    completeness: str
    product_range: str = connector.RANGE_VET
    supplier_id: int = 0
    supplier_label: str = ""
    ingestion_run_id: UUID | None = None
    previous_checksum: str | None = None
    warnings: tuple[connector.SnapshotWarning, ...] = ()
    #: Why this supplier queued nothing, when it was refused rather than
    #: merely unchanged. Reported per supplier, because the other half of the
    #: same read may well have been submitted.
    refusal: str | None = None
    #: Whether a person CAN release this refusal by pressing again. A short
    #: read can be released — the products really may be gone. An empty one
    #: cannot: there is no reading of the catalogue in which one account's
    #: whole range vanishes and the other's does not, so offering a button
    #: would only invite publishing a misclassification.
    releasable: bool = False


def _previous_snapshot(
    db: Session, supplier_id: int, contract_id: str
) -> models.CatalogueSourceDocument | None:
    """The last snapshot THIS CONNECTOR ingested for this supplier, newest first.

    Narrowed to the connector's own contract on purpose. A document someone
    uploaded by hand against the same supplier is not a previous read of the
    webshop: comparing against it would either invent a shrinkage that never
    happened or hide a real one behind an unrelated row count.
    """

    return (
        db.query(models.CatalogueSourceDocument)
        .filter(
            models.CatalogueSourceDocument.supplier_id == supplier_id,
            models.CatalogueSourceDocument.supplier_source_contract_id == contract_id,
        )
        .order_by(models.CatalogueSourceDocument.id.desc())
        .first()
    )


def _previous_row_count(db: Session, document: models.CatalogueSourceDocument | None) -> int | None:
    """How many products the previous snapshot held.

    Counted from the stored snapshot itself, so the comparison is available the
    moment a capture lands rather than only after its run has been processed —
    a truncated read must be caught on the way IN, not a stage later.
    """

    if document is None or not document.source_ref:
        return None
    root = Path(os.environ.get("CATALOGUE_UPLOAD_DIR", DEFAULT_UPLOAD_ROOT))
    path = root / document.source_ref
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            rows = sum(1 for row in csv.reader(handle) if any(value.strip() for value in row))
    except (OSError, UnicodeDecodeError):
        return None
    # The heading row is not a product.
    return max(rows - 1, 0) or None


def _submit_one(
    db: Session,
    snapshot,
    *,
    submitted_by: str | None,
    force_incomplete: bool,
) -> CaptureOutcome:
    """Decide and, if warranted, queue ONE supplier's snapshot.

    A refusal is RETURNED, not raised. Each supplier's submission commits on
    its own, so an exception here would abandon a sibling that is already
    queued and report it as never having happened.
    """

    supplier = SUPPLIERS[snapshot.product_range]
    previous = _previous_snapshot(db, supplier["supplier_id"], supplier["contract_id"])
    previous_checksum = getattr(previous, "source_checksum", None)
    previous_rows = _previous_row_count(db, previous)
    verdict = connector.assess_completeness(
        current_rows=snapshot.row_count, previous_rows=previous_rows
    )
    common = {
        "row_count": snapshot.row_count,
        "checksum": snapshot.checksum,
        "filename": snapshot.filename,
        "completeness": verdict.reason,
        "product_range": snapshot.product_range,
        "supplier_id": supplier["supplier_id"],
        "supplier_label": supplier["label"],
        "previous_checksum": previous_checksum,
        "warnings": snapshot.warnings,
    }
    # A range that came back with NOTHING is refused outright, and force does
    # not release it. The shrink guard needs a baseline to argue from and has
    # none on a first read, but an empty catalogue is never a reading of a
    # catalogue: Royal Canin does not stop selling to the vet account and keep
    # selling to the retail one on the same login. What it really means is
    # that classification moved — a channel renamed, our group changed — and
    # queueing a header-only document would publish that mistake as truth.
    if not snapshot.row_count:
        return CaptureOutcome(
            status="refused",
            refusal=(
                f"{supplier['label']} came back with no products at all"
                + (f" (it held {previous_rows} last time)" if previous_rows else "")
                + ". Royal Canin does not empty one account's range and leave the other's "
                "intact, so this is a change in how the shop files its products, not a "
                "delisting. Re-verify the channel names and our customer group before "
                "anything from this read is trusted."
            ),
            **{**common, "completeness": "no products — not submitted"},
        )
    if not verdict.trustworthy and not force_incomplete:
        return CaptureOutcome(
            status="refused",
            releasable=True,
            refusal=(
                f"{supplier['label']}'s catalogue came back short: {verdict.reason} "
                f"Nothing was submitted for this supplier. Re-run the capture; if the "
                f"catalogue really did shrink, release this read deliberately."
            ),
            **common,
        )
    if previous_checksum and previous_checksum == snapshot.checksum:
        return CaptureOutcome(status="unchanged", **common)

    result = CatalogueSubmissionService(db).submit(
        CatalogueSubmissionCommand(
            supplier_id=supplier["supplier_id"],
            original_filename=snapshot.filename,
            content_type="text/csv",
            stream=io.BytesIO(snapshot.csv_bytes),
            contract_id=supplier["contract_id"],
            contract_version=CONTRACT_VERSION,
            # The catalogue's own content is the identity: pressing the button
            # twice on an unchanged catalogue cannot make two runs.
            idempotency_key=f"royal-canin:{snapshot.product_range}:{snapshot.checksum}",
            submitted_by=submitted_by,
        )
    )
    return CaptureOutcome(status="submitted", ingestion_run_id=result.ingestion_run_id, **common)


def capture_and_submit(
    db: Session,
    *,
    submitted_by: str | None = None,
    captured_on: str | None = None,
    force_incomplete: bool = False,
) -> list[CaptureOutcome]:
    """Read the live catalogue once and queue each supplier's half that changed.

    Royal Canin invoices veterinary and retail separately, on different rebate
    ladders and terms, so one read becomes two runs — each with its own
    change-detection, so a week where only the retail prices moved does not
    re-queue the veterinary range for review.

    `force_incomplete` releases a shrunken snapshot: the person pressing it has
    decided the missing products really are gone. It is deliberately not the
    default, and the reason is recorded either way.

    Every supplier gets an outcome, including the ones that queued nothing.
    ``RoyalCaninCaptureRefused`` is raised only when NOTHING was submitted —
    a submission commits as it is made, so raising past one would report a
    queued run as never having happened, and the desk would be holding work
    the screen says does not exist.
    """

    captured_on = captured_on or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshots = connector.capture_snapshots(captured_on=captured_on)
    outcomes: list[CaptureOutcome] = []
    for product_range in (connector.RANGE_VET, connector.RANGE_NON_VET):
        snapshot = snapshots.get(product_range)
        if snapshot is None:
            continue
        outcomes.append(
            _submit_one(db, snapshot, submitted_by=submitted_by, force_incomplete=force_incomplete)
        )
    refusals = [outcome.refusal for outcome in outcomes if outcome.refusal]
    if refusals and not any(outcome.status == "submitted" for outcome in outcomes):
        raise RoyalCaninCaptureRefused(" ".join(refusals), outcomes)
    return outcomes

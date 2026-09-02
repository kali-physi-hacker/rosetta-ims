"""Pressing the button: read IDEXX's portal and submit what changed.

The connector produces a snapshot; this decides whether it is worth ingesting.
Three judgements, all of them to stop the pipeline acting on a bad read:

* an UNCHANGED catalogue submits nothing — a re-read is not a new document;
* a SHRUNKEN catalogue is refused by default, because a login that half-failed
  or a category page that did not render looks exactly like IDEXX delisting
  half their range, and only a person can tell those apart;
* an EMPTY read is an error, never an empty catalogue.

The same discipline as the Royal Canin connector, for the same reason: a fetch
can come back short in ways a file never does.
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
from services import idexx_connector as connector
from services.catalogue_submission import (
    CatalogueSubmissionCommand,
    CatalogueSubmissionService,
)
from services.source_capability import DEFAULT_UPLOAD_ROOT

SUPPLIER_ID = 3
CONTRACT_ID = "idexx.order_portal_snapshot.v1"
CONTRACT_VERSION = "v1"
SUPPLIER_LABEL = "Asia Vet Medical Limited (IDEXX)"


class IdexxCaptureRefused(RuntimeError):
    """A read a person must look at before it becomes catalogue truth."""


@dataclass(frozen=True)
class CaptureOutcome:
    """What one press of the button did."""

    status: str                      # "submitted" | "unchanged" | "refused"
    row_count: int
    checksum: str
    filename: str
    completeness: str
    pages_read: int = 0
    ingestion_run_id: UUID | None = None
    previous_checksum: str | None = None
    warnings: tuple[connector.SnapshotWarning, ...] = ()
    refusal: str | None = None
    releasable: bool = False


def _previous_snapshot(db: Session) -> models.CatalogueSourceDocument | None:
    """The last snapshot THIS CONNECTOR ingested, newest first.

    Narrowed to its own contract: a file someone uploaded by hand against the
    same supplier is not a previous read of the portal, and comparing against
    it would either invent a shrinkage or hide a real one.
    """
    return (
        db.query(models.CatalogueSourceDocument)
        .filter(
            models.CatalogueSourceDocument.supplier_id == SUPPLIER_ID,
            models.CatalogueSourceDocument.supplier_source_contract_id == CONTRACT_ID,
        )
        .order_by(models.CatalogueSourceDocument.id.desc())
        .first()
    )


def _previous_row_count(db: Session, document) -> int | None:
    if document is None or not document.source_ref:
        return None
    root = Path(os.environ.get("CATALOGUE_UPLOAD_DIR", DEFAULT_UPLOAD_ROOT))
    try:
        with (root / document.source_ref).open("r", encoding="utf-8", newline="") as handle:
            rows = sum(1 for row in csv.reader(handle) if any(v.strip() for v in row))
    except (OSError, UnicodeDecodeError):
        return None
    return max(rows - 1, 0) or None      # the heading row is not a product


def capture_and_submit(
    db: Session,
    *,
    submitted_by: str | None = None,
    captured_on: str | None = None,
    force_incomplete: bool = False,
) -> CaptureOutcome:
    """Read the live portal and queue it if the catalogue changed."""

    captured_on = captured_on or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    snapshot = connector.capture(captured_on=captured_on)

    previous = _previous_snapshot(db)
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
        "pages_read": snapshot.pages_read,
        "previous_checksum": previous_checksum,
        "warnings": snapshot.warnings,
    }
    if not verdict.trustworthy and not force_incomplete:
        raise IdexxCaptureRefused(
            f"IDEXX's catalogue came back short: {verdict.reason} Nothing was submitted. "
            f"Re-run the capture; if the range really did shrink, release this read "
            f"deliberately."
        )
    if previous_checksum and previous_checksum == snapshot.checksum:
        return CaptureOutcome(status="unchanged", **common)

    result = CatalogueSubmissionService(db).submit(
        CatalogueSubmissionCommand(
            supplier_id=SUPPLIER_ID,
            original_filename=snapshot.filename,
            content_type="text/csv",
            stream=io.BytesIO(snapshot.csv_bytes),
            contract_id=CONTRACT_ID,
            contract_version=CONTRACT_VERSION,
            # The catalogue's own content is the identity: pressing twice on an
            # unchanged catalogue cannot make two runs.
            idempotency_key=f"idexx:{snapshot.checksum}",
            submitted_by=submitted_by,
        )
    )
    return CaptureOutcome(status="submitted", ingestion_run_id=result.ingestion_run_id, **common)

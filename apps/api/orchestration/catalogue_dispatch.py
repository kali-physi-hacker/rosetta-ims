"""Queued-run dispatcher/reconciler for catalogue ingestion flows."""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from uuid import UUID

from prefect import flow
from sqlalchemy.orm import Session

import database
import v2.models as v2_models
from v2.models import IngestionRunStatus

from .catalogue_flows import catalogue_ingestion_flow


@dataclass(frozen=True)
class DispatchResult:
    queued_count: int
    submitted_count: int
    run_ids: tuple[UUID, ...]


def find_queued_run_ids(db: Session, *, limit: int = 10) -> tuple[UUID, ...]:
    """Return a bounded deterministic batch of queued run UUIDs."""

    rows = (
        db.query(v2_models.IngestionRun)
        .filter_by(status=IngestionRunStatus.QUEUED.value)
        .order_by(v2_models.IngestionRun.created_at.asc(), v2_models.IngestionRun.id.asc())
        .limit(max(1, int(limit)))
        .all()
    )
    return tuple(UUID(row.run_uuid) for row in rows)


def dispatch_queued_runs(*, batch_size: int = 10, execute_inline: bool = True) -> DispatchResult:
    """Dispatch queued runs through the Prefect flow.

    The current repository deployment uses the scheduled/looping reconciler
    pattern. Duplicate dispatch is harmless because the flow atomically claims
    only queued runs before doing any extraction work.
    """

    db = database.SessionLocal()
    try:
        run_ids = find_queued_run_ids(db, limit=batch_size)
    finally:
        db.close()
    submitted = 0
    if execute_inline:
        for run_id in run_ids:
            catalogue_ingestion_flow(ingestion_run_id=run_id)
            submitted += 1
    return DispatchResult(queued_count=len(run_ids), submitted_count=submitted, run_ids=run_ids)


@flow(name="catalogue-ingestion-dispatcher")
def catalogue_dispatcher_flow(*, batch_size: int = 10) -> DispatchResult:
    """Prefect-scheduled reconciler that processes a bounded queued-run batch."""

    return dispatch_queued_runs(batch_size=batch_size, execute_inline=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dispatch queued catalogue ingestion runs.")
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--loop", action="store_true", help="Run forever, sleeping between bounded dispatch passes.")
    parser.add_argument("--interval-seconds", type=float, default=30.0)
    args = parser.parse_args(argv)

    while True:
        result = catalogue_dispatcher_flow(batch_size=args.batch_size)
        print(f"catalogue dispatch submitted={result.submitted_count} queued={result.queued_count}")
        if not args.loop:
            return 0
        time.sleep(max(1.0, args.interval_seconds))


if __name__ == "__main__":
    raise SystemExit(main())

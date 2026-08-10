"""A run in flight can say where it has got to.

Before this, `status` was the only thing a waiting client could read, and it
distinguished exactly two situations: queued, and running. A 56-page catalogue
therefore showed the word "running" for several minutes with no way to tell a
slow read from a wedged one.

These tests pin the three properties that make the report trustworthy rather
than decorative:

  - it advances, and only forward
  - it is cleared the moment the run stops, so a finished run never appears busy
  - it cannot take the run down with it
"""

from __future__ import annotations

import os
import tempfile
import threading

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/progress.db")

from datetime import datetime, timezone  # noqa: E402
from uuid import UUID, uuid4  # noqa: E402

import pytest  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
from models.ingestion_run import (  # noqa: E402
    STAGE_LABELS,
    STAGE_ORDER,
    IngestionRunStatus,
    IngestionStage,
)
from orchestration.catalogue_run_lifecycle import (  # noqa: E402
    cancel_run,
    fail_run,
    mark_stage,
    mark_stage_progress,
)
from services.catalogue_evidence_extraction import _unit_progress_reporter  # noqa: E402


@pytest.fixture()
def db():
    database.Base.metadata.create_all(bind=database.engine)
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


def _running_run(db) -> UUID:
    """A run mid-flight, with just enough of a source document to be legal."""
    upload = models.CatalogueImport(
        filename="alfamedic.pdf",
        status="queued",
        imported_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(upload)
    db.flush()
    run = models.IngestionRun(
        run_uuid=str(uuid4()),
        source_document_id=upload.id,
        extractor_name="claude-sonnet-5",
        extractor_version="test",
        status=IngestionRunStatus.RUNNING.value,
        started_at=datetime.now(timezone.utc).isoformat(),
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    db.add(run)
    db.commit()
    return UUID(run.run_uuid)


def _reload(db, run_id: UUID) -> models.IngestionRun:
    db.expire_all()
    return db.query(models.IngestionRun).filter_by(run_uuid=str(run_id)).one()


def test_every_stage_the_flow_can_report_has_a_label():
    """A stage added to the flow without a label would render as a blank line."""
    assert set(STAGE_LABELS) == {stage.value for stage in IngestionStage}
    assert all(STAGE_LABELS[key].strip() for key in STAGE_LABELS)
    assert STAGE_ORDER[0] == IngestionStage.VERIFYING_SOURCE.value
    assert STAGE_ORDER[-1] == IngestionStage.PREPARING_REVIEW.value


def test_the_stage_advances_as_the_flow_moves(db):
    run_id = _running_run(db)
    for stage in IngestionStage:
        mark_stage(db, ingestion_run_id=run_id, stage=stage.value)
        assert _reload(db, run_id).stage == stage.value


def test_entering_a_stage_clears_the_previous_stages_unit_count(db):
    """56 pages read is not 56 rows interpreted.

    Carrying the counter forward would show a full bar the instant the next
    stage began, which reads as "finished" on a run that has just started its
    longest remaining step.
    """
    run_id = _running_run(db)
    mark_stage(db, ingestion_run_id=run_id, stage=IngestionStage.EXTRACTING.value, units_total=56)
    mark_stage_progress(db, ingestion_run_id=run_id, units_done=56, units_total=56)
    assert _reload(db, run_id).units_done == 56

    mark_stage(db, ingestion_run_id=run_id, stage=IngestionStage.INTERPRETING.value)
    after = _reload(db, run_id)
    assert after.units_done is None and after.units_total is None


def test_page_progress_is_recorded_while_the_read_is_still_going(db):
    run_id = _running_run(db)
    mark_stage(db, ingestion_run_id=run_id, stage=IngestionStage.EXTRACTING.value, units_total=56)
    mark_stage_progress(db, ingestion_run_id=run_id, units_done=34, units_total=56)
    run = _reload(db, run_id)
    assert (run.units_done, run.units_total) == (34, 56)
    assert run.status == IngestionRunStatus.RUNNING.value


@pytest.mark.parametrize("finish", ["failed", "cancelled"])
def test_a_run_that_stops_is_no_longer_at_a_stage(db, finish):
    """Otherwise the desk shows "Reading the catalogue" beside a dead run."""
    run_id = _running_run(db)
    mark_stage(db, ingestion_run_id=run_id, stage=IngestionStage.EXTRACTING.value, units_total=56)
    mark_stage_progress(db, ingestion_run_id=run_id, units_done=12, units_total=56)

    if finish == "failed":
        fail_run(db, ingestion_run_id=run_id, error_code="X", message="boom")
    else:
        cancel_run(db, ingestion_run_id=run_id, reason="user asked")

    run = _reload(db, run_id)
    assert run.status in {IngestionRunStatus.FAILED.value, IngestionRunStatus.CANCELLED.value}
    assert (run.stage, run.stage_started_at, run.units_done, run.units_total) == (None, None, None, None)


def test_progress_is_not_written_onto_a_run_that_has_already_stopped(db):
    """A late page report from a thread that outlived the run must not revive it."""
    run_id = _running_run(db)
    cancel_run(db, ingestion_run_id=run_id, reason="user asked")

    mark_stage(db, ingestion_run_id=run_id, stage=IngestionStage.EXTRACTING.value, units_total=56)
    mark_stage_progress(db, ingestion_run_id=run_id, units_done=40, units_total=56)

    run = _reload(db, run_id)
    assert run.stage is None and run.units_done is None


def test_every_page_is_counted_exactly_once_across_threads():
    """The reporter is called from a pool; a lost or doubled count is visible."""
    seen: list[tuple[int, int]] = []
    lock = threading.Lock()

    def record(done: int, total: int) -> None:
        with lock:
            seen.append((done, total))

    report = _unit_progress_reporter(56, record)
    threads = [threading.Thread(target=report) for _ in range(56)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(done for done, _ in seen) == list(range(1, 57))
    assert {total for _, total in seen} == {56}


def test_a_failing_progress_callback_never_breaks_the_read():
    """Extraction does not fail because a progress report did."""
    def explode(done: int, total: int) -> None:
        raise RuntimeError("the database went away")

    report = _unit_progress_reporter(3, explode)
    for _ in range(3):
        report()  # must not raise


def test_no_callback_means_no_work():
    report = _unit_progress_reporter(56, None)
    report()  # a no-op, and cheap enough to call per page


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

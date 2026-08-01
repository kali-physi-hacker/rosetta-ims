"""OPT-IN live smoke: real supplier PDFs through the real vision provider.

Never runs in the normal suite. Enable explicitly:

    CATALOGUE_LIVE_SMOKE=1 \
    CATALOGUE_LIVE_SMOKE_HILLS_CLASSIC=/path/to/Hill's.pdf \
    CATALOGUE_LIVE_SMOKE_HILLS_2026="/path/to/Hill's 2026 new price.pdf" \
    CATALOGUE_LIVE_SMOKE_ALFAMEDIC=/path/to/alfamedic.pdf \
    pytest tests/test_live_catalogue_smoke.py -s

Runs against whichever provider CATALOGUE_VISION_PROVIDER selects (anthropic by
default), so it is also how a provider or model change is proven before it
ships — the golden fixtures replay recorded envelopes and cannot tell you a new
model still reads a real page.

Each provided file is submitted and processed end-to-end with the production
configuration (model, thinking budget, concurrency, retry backoff). Asserted
per file (the audit's live-smoke checklist):

- run reaches a terminal completed status (never silent partial progression);
- extraction attempt is COMPLETE with every page accounted for;
- row count meets the per-file baseline (env-overridable);
- empty pages stay within the expected bound;
- the expected supplier contract resolved;
- every extracted row produced a normalized row and a mastering candidate;
- no TECHNICAL blocking issue codes (data-driven review issues are reported,
  not failed);
- representative invariants: costs are HKD, sparse-page warnings surfaced;
- wall-clock duration is reported for cost/runtime tracking.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from io import BytesIO
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("CATALOGUE_LIVE_SMOKE"),
    reason="live provider smoke is opt-in: set CATALOGUE_LIVE_SMOKE=1",
)

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/live-smoke.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

import database  # noqa: E402
import models  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from services import catalogue_vision_provider as vision_provider  # noqa: E402
from services.catalogue_submission import CatalogueSubmissionCommand, CatalogueSubmissionService  # noqa: E402


models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)

# Blocking codes that indicate a PIPELINE/CONTRACT defect rather than a
# data-driven review condition. Data conditions (unresolved "By Quote" costs,
# declared ambiguities, ...) are reported but never fail the smoke.
TECHNICAL_BLOCKERS = {
    "CONTRACT_ROW_UNCONFORMABLE",
    "CONTRACT_REQUIRED_HEADER_MISSING",
    "CONTRACT_ISSUE_METADATA_INVALID",
}

CASES = [
    pytest.param(
        "CATALOGUE_LIVE_SMOKE_HILLS_CLASSIC",
        {
            "supplier_id": 14,
            "supplier": ("HILLS", "Hill's"),
            "contract_id": "hills.price_list.v1",
            "min_rows_env": ("CATALOGUE_LIVE_SMOKE_HILLS_CLASSIC_MIN_ROWS", 200),
            "max_empty_pages": 0,
        },
        id="hills-classic",
    ),
    pytest.param(
        "CATALOGUE_LIVE_SMOKE_HILLS_2026",
        {
            "supplier_id": 14,
            "supplier": ("HILLS", "Hill's"),
            "contract_id": "hills.price_list.v1",
            "min_rows_env": ("CATALOGUE_LIVE_SMOKE_HILLS_2026_MIN_ROWS", 40),
            "max_empty_pages": 1,
        },
        id="hills-2026",
    ),
    pytest.param(
        "CATALOGUE_LIVE_SMOKE_ALFAMEDIC",
        {
            "supplier_id": 1,
            "supplier": ("ALF", "Alfamedic"),
            "contract_id": "alfamedic.price_list.v1",
            "min_rows_env": ("CATALOGUE_LIVE_SMOKE_ALFAMEDIC_MIN_ROWS", 300),
            # 56 pages include a cover, TOC and per-section divider pages — the
            # live run classified 9 as no-catalogue-evidence.
            "max_empty_pages": 12,
        },
        id="alfamedic",
    ),
]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CATALOGUE_ORCHESTRATION_MAX_SOURCE_BYTES", str(32 * 1024 * 1024))
    session = database.SessionLocal()
    try:
        yield session
        session.rollback()
    finally:
        session.close()


def _ensure_supplier(db, supplier_id: int, code: str, name: str) -> None:
    if db.get(models.Supplier, supplier_id) is None:
        db.add(models.Supplier(id=supplier_id, code=code, name=name, created_at="2026-07-29T00:00:00+00:00"))
        db.commit()


@pytest.mark.parametrize("path_env, expected", CASES)
def test_live_supplier_file_end_to_end(db, path_env, expected):
    source_path = os.environ.get(path_env)
    if not source_path:
        pytest.skip(f"{path_env} not set")
    source = Path(source_path)
    assert source.exists(), f"{path_env} points at a missing file: {source}"
    provider = vision_provider.active_provider()
    assert provider.is_configured(), (
        f"{provider.name} is the selected vision provider but its key is not set"
    )
    print(f"\n[live smoke] provider={provider.name} model={provider.model}")

    _ensure_supplier(db, expected["supplier_id"], *expected["supplier"])
    service = CatalogueSubmissionService(
        db, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"], max_upload_bytes=32 * 1024 * 1024
    )
    submitted = service.submit(
        CatalogueSubmissionCommand(
            supplier_id=expected["supplier_id"],
            original_filename=source.name,
            content_type="application/pdf",
            stream=BytesIO(source.read_bytes()),
            contract_id=None,
            contract_version=None,
            idempotency_key=None,
            submitted_by="live-smoke",
        )
    )
    assert submitted.contract_id == expected["contract_id"]

    started = time.monotonic()
    result = catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)
    duration = time.monotonic() - started
    run_uuid = str(submitted.ingestion_run_id)

    # Extraction attempts are APPEND-ONLY per Prefect retry — assert against
    # the latest one, never .one().
    attempt = (
        db.query(models.CatalogueExtractionAttempt)
        .filter_by(ingestion_run_uuid=run_uuid)
        .order_by(models.CatalogueExtractionAttempt.id.desc())
        .first()
    )
    assert attempt is not None, "no extraction attempt was persisted"
    outcomes = json.loads(attempt.unit_outcomes_json or "[]")
    issues = db.query(models.CatalogueValidationIssue).filter_by(ingestion_run_uuid=run_uuid).all()
    rows = db.query(models.CatalogueNormalizedRow).filter_by(ingestion_run_uuid=run_uuid).count()
    candidates = db.query(models.CatalogueMasteringCandidate).filter_by(ingestion_run_uuid=run_uuid).count()
    blocked = {issue.catalogue_item_uuid for issue in issues if issue.publish_blocking and issue.catalogue_item_uuid}
    sparse = [w for w in (result.warnings or ()) if "suspiciously sparse" in w]

    print(
        f"\n[live-smoke] {source.name}: status={result.terminal_status} "
        f"pages={attempt.units_completed}/{attempt.units_attempted} empty={attempt.empty_units} "
        f"rows={rows} candidates={candidates} blocked_rows={len(blocked)} "
        f"issues={sorted({issue.issue_code for issue in issues})} "
        f"sparse_warnings={len(sparse)} duration={duration:.0f}s"
    )

    # Terminal, complete, and every page accounted for.
    assert result.terminal_status in {"completed", "completed_with_warnings"}, result.warnings
    assert attempt.status == "COMPLETE"
    assert attempt.units_completed == attempt.units_attempted == len(outcomes)
    assert attempt.empty_units <= expected["max_empty_pages"]

    # Volume baseline (env-overridable as documents evolve).
    min_env, min_default = expected["min_rows_env"]
    min_rows = int(os.environ.get(min_env, min_default))
    assert result.rows_extracted >= min_rows, f"extracted {result.rows_extracted} < baseline {min_rows}"

    # Every extracted CATALOGUE row normalized (headers/page furniture are
    # legitimately skipped); every unblocked row reached mastering.
    assert rows == result.rows_extracted - result.rows_skipped_non_catalogue
    assert candidates == rows - len(blocked)

    # No technical blockers; representative field invariants hold.
    technical = sorted({issue.issue_code for issue in issues} & TECHNICAL_BLOCKERS)
    assert not technical, f"technical blocking issues: {technical}"
    hkd = other = 0
    for row in db.query(models.CatalogueNormalizedRow).filter_by(ingestion_run_uuid=run_uuid).all():
        cost = json.loads(row.normalized_fields_json).get("cost")
        if cost:
            if cost.get("currency") == "HKD":
                hkd += 1
            else:
                other += 1
    assert other == 0, f"{other} rows normalized with a non-HKD currency"
    assert hkd > 0, "no row normalized a cost"

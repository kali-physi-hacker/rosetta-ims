"""Ingest the golden sets into a DEV database from stored evidence — no vision.

For local HITL work you want real runs with real pending candidates, without
paying a provider or waiting on a worker. This replays each golden set's
checked-in envelopes through the FULL pipeline synchronously — submission,
raw, extraction (replayed through the provider seam), conformance,
validation, mastering — and stops there: candidates stay PENDING_REVIEW so
the review desk has something to review.

    python scripts/seed_dev_runs_from_golden.py                 # all golden sets
    python scripts/seed_dev_runs_from_golden.py vetapet_vet ... # specific sets

Writes to DATABASE_URL (defaults to the dev sqlite at data/ims.db). Never run
against production; deploys ship functionality, not data.
"""

from __future__ import annotations

import json
import os
import sys
from io import BytesIO
from pathlib import Path

API_ROOT = Path(__file__).resolve().parent.parent
os.environ.setdefault("DATABASE_URL", f"sqlite:///{API_ROOT / 'data' / 'ims.db'}")
os.environ.setdefault("CATALOGUE_UPLOAD_DIR", str(API_ROOT / "data" / "catalogue_uploads"))
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

import pypdf  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from services import catalogue_evidence_extraction as extraction  # noqa: E402
from services.catalogue_submission import CatalogueSubmissionCommand, CatalogueSubmissionService  # noqa: E402

GOLDEN_ROOT = API_ROOT / "tests" / "fixtures" / "catalogue_pipeline" / "golden"
_PROVIDER_KEY_VAR = {"anthropic": "ANTHROPIC_API_KEY", "google": "GEMINI_API_KEY"}


def _blank_pdf(page_count: int) -> bytes:
    writer = pypdf.PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _install_replay(pages: list[Path]) -> None:
    payloads = [page.read_text(encoding="utf-8") for page in pages]
    state = {"n": 0}

    def replay(_content: bytes, *, media_type: str):
        index = state["n"]
        state["n"] += 1
        return extraction._VisionResponse(text=payloads[index], request_id=f"seed_{index + 1}")

    extraction._VISION_CONCURRENCY = 1
    extraction._call_vision = replay


def seed(set_names: list[str]) -> int:
    session = database.SessionLocal()
    try:
        for set_dir in sorted(p for p in GOLDEN_ROOT.iterdir() if p.is_dir()):
            if set_names and set_dir.name not in set_names:
                continue
            spec = json.loads((set_dir / "expectations.json").read_text(encoding="utf-8"))
            supplier = spec["supplier"]
            if session.get(models.Supplier, supplier["id"]) is None:
                session.add(models.Supplier(
                    id=supplier["id"],
                    code=supplier["code"],
                    name=supplier["name"],
                    created_at="2026-08-17T00:00:00+00:00",
                ))
                session.commit()
                print(f"{set_dir.name}: seeded supplier {supplier['id']} ({supplier['name']})")

            os.environ["CATALOGUE_VISION_PROVIDER"] = spec["provider"]
            os.environ.setdefault(_PROVIDER_KEY_VAR[spec["provider"]], "golden-replay")
            pages = [set_dir / name for name in spec["pages"]]
            _install_replay(pages)

            service = CatalogueSubmissionService(
                session,
                upload_root=os.environ["CATALOGUE_UPLOAD_DIR"],
                max_upload_bytes=8 * 1024 * 1024,
            )
            submitted = service.submit(CatalogueSubmissionCommand(
                supplier_id=int(supplier["id"]),
                original_filename=f"{set_dir.name}-golden-seed.pdf",
                content_type="application/pdf",
                stream=BytesIO(_blank_pdf(len(pages))),
                contract_id=spec.get("contract_id"),
                contract_version="v1" if spec.get("contract_id") else None,
                idempotency_key=None,
                submitted_by="golden-seed",
            ))
            result = catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)
            pending = (
                session.query(models.CatalogueMasteringCandidate)
                .filter_by(ingestion_run_uuid=str(submitted.ingestion_run_id), superseded_by_uuid=None)
                .count()
            )
            print(
                f"{set_dir.name}: run {submitted.ingestion_run_id} -> {result.terminal_status}, "
                f"{result.rows_extracted} rows extracted, {pending} candidates pending review"
            )
    finally:
        session.close()
    return 0


if __name__ == "__main__":
    sys.exit(seed(sys.argv[1:]))

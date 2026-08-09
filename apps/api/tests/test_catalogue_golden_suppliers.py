"""Golden supplier-file coverage: REAL recorded provider envelopes, offline.

The fixtures under ``tests/fixtures/catalogue_pipeline/golden/`` are actual
Gemini vision envelopes recorded from live extraction runs of real supplier
files (see each set's ``meta.json`` and ``scripts/record_golden_envelopes.py``).
Replaying them through the FULL pipeline — submission, raw, extraction parsing,
conformance, validation, mastering — proves end-to-end behaviour against real
provider output without network access or provider nondeterminism.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from io import BytesIO
from pathlib import Path
from uuid import UUID

import pytest
import pypdf

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

import database  # noqa: E402
import models  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from schemas.catalogue_pipeline.enums import ReviewStatus  # noqa: E402
from services import catalogue_evidence_extraction as extraction  # noqa: E402
from services import catalogue_pipeline_stages as stages  # noqa: E402
from services.catalogue_golden_export import GOLDEN_COLUMNS, golden_rows  # noqa: E402
from services.catalogue_submission import CatalogueSubmissionCommand, CatalogueSubmissionService  # noqa: E402


models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)

GOLDEN_ROOT = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "golden"
HILLS_CLASSIC = GOLDEN_ROOT / "hills_classic"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CATALOGUE_ORCHESTRATION_MAX_SOURCE_BYTES", str(4 * 1024 * 1024))
    session = database.SessionLocal()
    try:
        _reset(session)
        if session.get(models.Supplier, 14) is None:
            session.add(models.Supplier(id=14, code="HILLS", name="Hill's", created_at="2026-07-29T00:00:00+00:00"))
            session.commit()
        yield session
        session.rollback()
        _reset(session)
    finally:
        session.close()


def _reset(session):
    for model in (
        models.CatalogueSubmissionIdempotency,
        models.CatalogueRawStageAttempt,
        models.CatalogueExtractionAttempt,
        models.CatalogueServingPublication,
        models.CatalogueSupplierMbbTerm,
        models.CatalogueSupplierPrice,
        models.CataloguePackagingConfiguration,
        models.SupplierOffering,
        models.CatalogueReviewDecision,
        models.CatalogueMasteringCandidate,
        models.CatalogueValidationIssue,
        models.CatalogueNormalizedRowEvidence,
        models.CatalogueNormalizedRow,
        models.CatalogueExtractedEvidence,
        models.IngestionRun,
        models.CatalogueSourceDocument,
    ):
        session.query(model).delete()
    session.query(models.CatalogueImport).delete()
    session.commit()


def _blank_pdf(page_count: int) -> bytes:
    writer = pypdf.PdfWriter()
    for _ in range(page_count):
        writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _install_golden_replay(monkeypatch, envelope_paths: list[Path]):
    """Replay recorded envelopes in page order through the provider seam.

    Concurrency is pinned to 1 so the thread pool preserves submission order —
    call N receives page N's recorded envelope, exactly as live extraction
    would have.
    """

    monkeypatch.setattr(extraction, "_VISION_CONCURRENCY", 1)
    payloads = [path.read_text(encoding="utf-8") for path in envelope_paths]
    calls = {"n": 0}

    def replay(_content: bytes, *, media_type: str):
        assert media_type == "application/pdf"
        index = calls["n"]
        calls["n"] += 1
        return extraction._VisionResponse(text=payloads[index], request_id=f"golden_{index + 1}")

    monkeypatch.setattr(extraction, "_call_vision", replay)
    return calls


def _load_expected(fixture_dir: Path) -> dict[str, dict[str, str]]:
    """The human-authored expectations, keyed by supplier product code.

    ``expected.csv`` is the golden sample sheet's own flat table (tab
    gid=1535624888, header row 66) filtered to this supplier — its header IS
    ``GOLDEN_COLUMNS``, so the sheet and the export are compared in the same
    shape with nothing re-keyed by hand.
    """
    path = fixture_dir / "expected.csv"
    assert path.exists(), (
        f"{fixture_dir.name} has no expected.csv — a golden set without human-authored "
        f"values proves only that the pipeline ran, so it is a failure, not a skip."
    )
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        assert tuple(reader.fieldnames or ()) == GOLDEN_COLUMNS, (
            f"{path.name} header drifted from GOLDEN_COLUMNS — the sheet and the export "
            f"must stay in the same shape.\n  csv:    {reader.fieldnames}\n  export: {list(GOLDEN_COLUMNS)}"
        )
        return {row["supplier_product_code"]: row for row in reader if row["supplier_product_code"]}


def _take_through_review(db, run_uuid: str) -> int:
    """Stand in for the human review desk: confirm the create, approve, apply, publish.

    The serving layer is only reachable through a person today, and
    ``golden_rows`` reads published rows — so a test that stops at mastering can
    never see what a run actually put live. This drives the real services in the
    real order rather than writing publication rows directly, so every guard
    still has to pass.

    The confirm step is not ceremony. A fresh database holds no mappings, so the
    matcher leaves every row PROPOSED_CREATE, and approval refuses that state
    outright — a machine guess is not a decision. Confirming is a person saying
    "yes, this is a new product", and it returns a NEW candidate superseding the
    original, because corrections are immutable revisions.
    """
    candidates = (
        db.query(models.CatalogueMasteringCandidate)
        .filter_by(ingestion_run_uuid=run_uuid, superseded_by_uuid=None)
        .order_by(models.CatalogueMasteringCandidate.id)
        .all()
    )
    published = 0
    for candidate in candidates:
        variant = json.loads(candidate.product_variant_resolution_json or "{}")
        brand_res = json.loads(candidate.brand_resolution_json or "{}")
        name = (variant.get("product_variant_name") or variant.get("proposed_name") or "").strip() or "Unnamed"
        brand = (brand_res.get("brand_name") or brand_res.get("proposed_name") or "").strip() or "Hill's"
        # Take the draft's UOM from what the pipeline actually read, not a
        # constant — a stubbed "unit" here would surface as a packaging
        # mismatch in the diff and read as a pipeline defect that isn't one.
        packaging = (json.loads(candidate.packaging_resolution_json or "{}") or {}).get("packaging") or {}
        uom = (
            (packaging.get("sellable_unit_uom") or {}).get("code")
            or (packaging.get("purchase_uom") or {}).get("code")
            or "unit"
        ).lower()

        revised = stages.MasteringService(db).revise_candidate(
            stages.ReviseMasteringCandidateCommand(
                mastering_candidate_id=UUID(candidate.mastering_candidate_uuid),
                actor_id="golden-review",
                reason="Golden sample replay: new to the catalogue, drafting it.",
                product_variant_resolution={
                    "state": "CONFIRMED_CREATE",
                    "proposed_name": name,
                    "product_variant_name": name,
                    "proposed_variant": {"name": name, "category": "Food", "brand": brand, "uom": uom},
                },
            )
        )
        candidate_id = revised.output_ids[0]

        stages.ReviewDecisionService(db).record_decision(
            stages.RecordReviewDecisionCommand(
                mastering_candidate_id=candidate_id,
                actor_id="golden-review",
                review_status=ReviewStatus.APPROVED,
                reason="Golden sample replay.",
            )
        )
        stages.ApprovedCommercialStateService(db).apply_approved_candidate(
            stages.ApplyApprovedCandidateCommand(mastering_candidate_id=candidate_id)
        )
        stages.ServingPublicationService(db).publish(
            stages.PublishServingItemCommand(
                mastering_candidate_id=candidate_id,
                publication_version="golden-1",
            )
        )
        published += 1
    return published


# Columns where the sheet and the export state the same thing, and must agree.
_ENFORCED = (
    "supplier",
    "supplier_product_code",
    "catalogue_price_hkd",
    "catalogue_price_basis_qty",
    "sellable_qty",
)

# Columns the pipeline does not produce correctly yet. Each is a real defect
# with a real cause, pinned so it cannot quietly get worse and so fixing one
# fails this test rather than passing unnoticed.
_KNOWN_GAPS = {
    "weight": "packaging carries content_amount/content_uom (2.8 OZ) but the export reads weight off the product variant, so it comes out empty",
    "package_configuration": "purchase_uom and sellable_unit_uom are null on every Hill's row, so the export falls back to the variant UOM",
    "order_multiple": "order_increment has the right amount (24) but a generic UNIT — the contract does not map the printed pack unit",
    "catalogue_price_basis_uom": "price_basis resolves to UNIT rather than the CAN/POUCH/BOX the supplier prints",
    "sellable_uom": "sellable_unit_uom is never resolved",
    "sellable_units_per_price_basis": "sellable_units_per_purchase_unit is never derived",
    "rrp": "the normalized row carries an rrp field but it does not reach the published export",
}

# Columns where the sheet and the export describe genuinely different things.
# Excluded on purpose, with the reason, rather than left to fail forever.
_NOT_COMPARABLE = {
    "product_name": "the sheet holds a human shorthand ('Hills F9 i/d stew 2.9oz'); the export holds the supplier's printed description",
    "product name [Rosetta]": "a hand-authored retail name, not something the pipeline derives",
    "brand": "the sheet leaves it blank for Hill's own products",
    "mbb_tier_1": "the sheet records the supplier's written order-value discount policy; the pipeline captures the printed per-unit price tiers",
    "mbb_tier_2": "as mbb_tier_1",
    "mbb_tier_3": "as mbb_tier_1",
    "mbb_tier_4": "as mbb_tier_1",
    "commercial_offer_summary": "free-text summary written by hand",
}


def _diff(expected: dict[str, str], actual: dict[str, str], columns) -> list[str]:
    """Per-field comparison, reported as mismatched (expected X, got Y).

    A blank sheet cell is not an assertion that the value is empty — it means
    nobody filled it in, so there is no human statement to check against and
    the field is skipped. Only what a person actually wrote is enforced.
    """
    problems = []
    for column in columns:
        want = (expected.get(column) or "").strip()
        if not want:
            continue
        got = (actual.get(column) or "").strip()
        if want != got:
            problems.append(f"{column}: expected {want!r}, got {got!r}")
    return problems


def test_hills_classic_golden_pages_run_the_full_pipeline(db, monkeypatch):
    """Two REAL Hill's pages, one per header family (Life Stage / Disease
    Category), from a live gemini-3.1-pro-preview run: every recorded row must
    conform under the single hills.price_list.v1 contract and reach mastering.

    Pinned to the provider that recorded them, which also runs the whole
    pipeline once on the non-default provider — the toggle is not just a
    lookup, it has to carry a real run."""

    monkeypatch.setenv("CATALOGUE_VISION_PROVIDER", "google")
    monkeypatch.setenv("GEMINI_API_KEY", "golden-replay")
    pages = [HILLS_CLASSIC / "page_1.json", HILLS_CLASSIC / "page_4.json"]
    calls = _install_golden_replay(monkeypatch, pages)
    expected_rows = sum(len(json.loads(p.read_text())["rows"]) for p in pages)

    service = CatalogueSubmissionService(
        db, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"], max_upload_bytes=4 * 1024 * 1024
    )
    submitted = service.submit(
        CatalogueSubmissionCommand(
            supplier_id=14,
            original_filename="hills-golden.pdf",
            content_type="application/pdf",
            stream=BytesIO(_blank_pdf(len(pages))),
            contract_id=None,
            contract_version=None,
            idempotency_key=None,
            submitted_by="golden",
        )
    )
    result = catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)

    assert result.terminal_status == "completed_with_warnings"  # declared contract ambiguity
    assert calls["n"] == len(pages)
    assert result.rows_extracted == expected_rows == 59
    assert result.staging_items_created == expected_rows

    run_uuid = str(submitted.ingestion_run_id)
    attempt = db.query(models.CatalogueExtractionAttempt).filter_by(ingestion_run_uuid=run_uuid).one()
    assert attempt.status == "COMPLETE"
    assert attempt.units_attempted == attempt.units_completed == len(pages)

    # No required-header/field failures on EITHER header family, and no
    # sparse-page suspicion between two dense real pages.
    issues = db.query(models.CatalogueValidationIssue).filter_by(ingestion_run_uuid=run_uuid).all()
    codes = {issue.issue_code for issue in issues}
    assert "CONTRACT_REQUIRED_HEADER_MISSING" not in codes
    assert "CONTRACT_REQUIRED_FIELD_MISSING" not in codes
    run = db.query(models.IngestionRun).filter_by(run_uuid=run_uuid).one()
    assert "suspiciously sparse" not in (run.error_summary or "")

    # Every row produced a reviewable candidate (fresh DB: no mappings, so the
    # resolver must propose creation — never invent a canonical match).
    candidates = db.query(models.CatalogueMasteringCandidate).filter_by(ingestion_run_uuid=run_uuid).all()
    assert len(candidates) == expected_rows
    states = {json.loads(c.product_variant_resolution_json)["state"] for c in candidates}
    assert states == {"PROPOSED_CREATE"}

    # Representative REAL field values, one per header family.
    rows = db.query(models.CatalogueNormalizedRow).filter_by(ingestion_run_uuid=run_uuid).all()
    by_sku = {}
    for row in rows:
        fields = json.loads(row.normalized_fields_json)
        sku = (fields.get("supplier_sku") or {}).get("value")
        if sku:
            by_sku[sku] = fields
    classic = by_sku["10447"]  # Life Stage family (page 1)
    assert classic["cost"]["amount"] == "13.10"
    assert classic["cost"]["currency"] == "HKD"
    assert "Healthy Cuisine" in classic["product_name"]["value"]
    prescription = by_sku["607665"]  # Disease Category family (page 4)
    assert prescription["cost"]["amount"] == "25.20"
    assert "Cancer" in prescription["product_name"]["value"]


def test_hills_published_export_matches_the_hand_filled_sheet(db, monkeypatch):
    """The published export, compared field by field against the golden sheet.

    The test above proves the pipeline reaches mastering. This one goes the rest
    of the way — through review to the SERVING layer — and checks the numbers a
    person wrote down by hand.

    `catalogue_golden_export.golden_rows` reads published rows only, and the
    sheet's own flat table has exactly those columns, so the two are compared in
    the same shape. That comparison is what DEV-209 later reconciles the
    operations database against; if the sheet and the export ever stop agreeing
    here, everything built on top of the export is measuring the wrong thing.
    """
    monkeypatch.setenv("CATALOGUE_VISION_PROVIDER", "google")
    monkeypatch.setenv("GEMINI_API_KEY", "golden-replay")
    pages = [HILLS_CLASSIC / "page_1.json", HILLS_CLASSIC / "page_4.json"]
    calls = _install_golden_replay(monkeypatch, pages)

    service = CatalogueSubmissionService(
        db, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"], max_upload_bytes=4 * 1024 * 1024
    )
    submitted = service.submit(
        CatalogueSubmissionCommand(
            supplier_id=14,
            original_filename="hills-golden.pdf",
            content_type="application/pdf",
            stream=BytesIO(_blank_pdf(len(pages))),
            contract_id=None,
            contract_version=None,
            idempotency_key=None,
            submitted_by="golden",
        )
    )
    catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)
    assert calls["n"] == len(pages), "replayed from recorded envelopes — no provider call"

    run_uuid = str(submitted.ingestion_run_id)
    assert _take_through_review(db, run_uuid) > 0, "nothing reached the serving layer to compare"

    published = {row["supplier_product_code"]: row for row in golden_rows(db, UUID(run_uuid))}
    assert published, "golden_rows returned nothing after publishing"

    expected = _load_expected(HILLS_CLASSIC)
    covered = sorted(set(expected) & set(published))
    # The sheet's Hill's block was filled from a wider page selection than the two
    # pages recorded here, so only part of it is reachable end-to-end today. Pin
    # the overlap: it must never silently shrink.
    assert covered == ["3392", "604202", "605916", "608450"], (
        f"the sheet SKUs reachable from the recorded pages changed: {covered}. "
        f"Growing this list is good news — update it. Shrinking it means the "
        f"fixture and the sheet have drifted and the test is checking less than it claims."
    )

    failures = []
    for sku in covered:
        for problem in _diff(expected[sku], published[sku], _ENFORCED):
            failures.append(f"{sku}  {problem}")
    assert not failures, (
        "the published export disagrees with the hand-filled sheet on a column that must match:\n  "
        + "\n  ".join(failures)
    )

    # Every column is accounted for: enforced above, a known defect, or
    # documented as not comparable. A column that is none of these is a column
    # nobody decided about.
    unclassified = set(GOLDEN_COLUMNS) - set(_ENFORCED) - set(_KNOWN_GAPS) - set(_NOT_COMPARABLE)
    assert not unclassified, f"columns with no stated expectation: {sorted(unclassified)}"

    # The known defects, pinned. Fixing one makes this fail with the column
    # named, which is the point — a silent improvement is still a surprise.
    still_broken = {
        column
        for sku in covered
        for column in _KNOWN_GAPS
        if _diff(expected[sku], published[sku], (column,))
    }
    fixed = sorted(set(_KNOWN_GAPS) - still_broken)
    assert not fixed, (
        f"these columns now match the sheet and are no longer defects: {fixed}. "
        f"Move them from _KNOWN_GAPS into _ENFORCED so they stay fixed."
    )

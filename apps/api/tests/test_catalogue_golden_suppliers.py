"""Golden supplier-file coverage: real provider output, replayed offline.

The fixtures under ``tests/fixtures/catalogue_pipeline/golden/`` hold vision
envelopes for real supplier files. Replaying them through the FULL pipeline —
submission, raw, extraction parsing, conformance, validation, mastering, review,
serving — proves end-to-end behaviour against real model output with no network
access and no provider nondeterminism.

Each set's ``meta.json`` says how it was obtained, and the two differ:

  hills_classic  recorded live through the provider seam by
                 ``scripts/record_golden_envelopes.py`` — one envelope per page,
                 exactly as production calls it.
  alfamedic      hand-captured from a chat window, whole catalogue in a single
                 envelope. Everything from conformance onward is real; page
                 splitting, per-page retry and sparse-page detection are not
                 exercised. Worth having anyway: it reaches 36 of the sheet's 38
                 Alfamedic SKUs, against 4 of 13 for Hill's.

Both are compared against ``expected.csv``, which is the golden sample sheet's
own flat table for that supplier — same 20 columns, in the same order, as
``catalogue_golden_export`` emits.
"""

from __future__ import annotations

import csv
import json
import os
import tempfile
from dataclasses import dataclass
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

#: Which provider's key a set's recording was made under. The replay never
#: reaches a provider; the variable only has to exist so configuration checks
#: pass on the path the run takes.
_PROVIDER_KEY_VAR = {"anthropic": "ANTHROPIC_API_KEY", "google": "GEMINI_API_KEY"}


@dataclass(frozen=True)
class GoldenSet:
    """One golden sample: its pages, its expectations, and what it is for.

    Everything a comparison needs lives beside the fixture rather than in this
    file, so adding a supplier is dropping a directory in — envelopes,
    expected.csv, expectations.json — and nothing else. The moment a set has to
    be registered in code, "add a golden sample" stops being a data change and
    starts being a pull request against the test suite.
    """

    name: str
    path: Path
    supplier_id: int
    supplier_code: str
    supplier_name: str
    page_names: tuple[str, ...]
    provider: str
    min_covered_skus: int
    #: The exact SKUs expected to reach the export, when pinned. A bare floor
    #: lets a swap hide — lose one covered SKU, gain another, count unchanged.
    covered_skus: tuple[str, ...]
    enforced: tuple[str, ...]
    known_gaps: dict[str, str]
    not_comparable: dict[str, str]
    unfilled: dict[str, str]
    unpublishable: frozenset[str]

    @property
    def api_key_var(self) -> str:
        return _PROVIDER_KEY_VAR[self.provider]


def discover_golden_sets() -> list[GoldenSet]:
    """Every set under the golden directory, found by looking.

    A directory without expectations.json is a failure, not a skip: a golden
    sample nobody stated an expectation for proves only that the pipeline ran,
    and a suite that silently ignores it is worse than one that never had it.
    """
    sets: list[GoldenSet] = []
    for path in sorted(p for p in GOLDEN_ROOT.iterdir() if p.is_dir()):
        manifest = path / "expectations.json"
        assert manifest.exists(), (
            f"golden set {path.name!r} has no expectations.json — a set nobody has "
            f"stated an expectation for cannot be compared against anything"
        )
        # This runs at import, so a bad manifest stops the WHOLE suite — that
        # blast radius is deliberate (nothing green while a golden set is
        # unreadable), but the error must name the set, not just quote json's
        # column number with no file.
        try:
            spec = json.loads(manifest.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise AssertionError(
                f"golden set {path.name!r}: expectations.json is not valid JSON — {exc}"
            ) from exc

        # A column in two buckets is a contradiction, not a preference: the
        # test would both enforce it and excuse it. Refuse the manifest.
        claimed: dict[str, str] = {}
        for bucket in ("enforced", "known_gaps", "not_comparable", "unfilled"):
            for column in spec.get(bucket) or ():
                if column in claimed:
                    raise AssertionError(
                        f"golden set {path.name!r}: column {column!r} appears in both "
                        f"{claimed[column]!r} and {bucket!r} — it cannot be two things"
                    )
                claimed[column] = bucket

        try:
            supplier = spec["supplier"]
            sets.append(
                GoldenSet(
                    name=path.name,
                    path=path,
                    supplier_id=int(supplier["id"]),
                    supplier_code=supplier["code"],
                    supplier_name=supplier["name"],
                    page_names=tuple(spec["pages"]),
                    provider=spec["provider"],
                    min_covered_skus=int(spec["min_covered_skus"]),
                    covered_skus=tuple(spec.get("covered_skus") or ()),
                    enforced=tuple(spec["enforced"]),
                    known_gaps=dict(spec.get("known_gaps") or {}),
                    not_comparable=dict(spec.get("not_comparable") or {}),
                    unfilled=dict(spec.get("unfilled") or {}),
                    unpublishable=frozenset(spec.get("unpublishable") or ()),
                )
            )
        except KeyError as exc:
            raise AssertionError(
                f"golden set {path.name!r}: expectations.json is missing {exc}"
            ) from exc
    assert sets, f"no golden sets found under {GOLDEN_ROOT}"
    return sets


def golden_set(name: str) -> GoldenSet:
    """One discovered set by name, for a test that needs a specific supplier."""
    for candidate in discover_golden_sets():
        if candidate.name == name:
            return candidate

    raise AssertionError(f"no golden set named {name!r}")


GOLDEN_SETS = discover_golden_sets()


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CATALOGUE_ORCHESTRATION_MAX_SOURCE_BYTES", str(8 * 1024 * 1024))
    session = database.SessionLocal()
    try:
        _reset(session)
        # Seeded from the sets themselves, so a new fixture directory brings its
        # own supplier rather than needing one added here.
        for spec in GOLDEN_SETS:
            if session.get(models.Supplier, spec.supplier_id) is None:
                session.add(models.Supplier(
                    id=spec.supplier_id,
                    code=spec.supplier_code,
                    name=spec.supplier_name,
                    created_at="2026-07-29T00:00:00+00:00",
                ))
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


def _candidate_sku(candidate) -> str:
    resolution = json.loads(candidate.supplier_product_resolution_json or "{}")
    return (resolution.get("supplier_sku") or "").strip()


def _take_through_review(
    db,
    run_uuid: str,
    *,
    only_skus: set[str] | None = None,
    refused: dict[str, str] | None = None,
) -> int:
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
    refused = {} if refused is None else refused
    candidates = (
        db.query(models.CatalogueMasteringCandidate)
        .filter_by(ingestion_run_uuid=run_uuid, superseded_by_uuid=None)
        .order_by(models.CatalogueMasteringCandidate.id)
        .all()
    )
    if only_skus is not None:
        # A whole-catalogue capture produces far more rows than the sheet
        # covers, and driving every one through four services costs minutes
        # without strengthening a comparison that only looks at the SKUs a
        # person actually filled in.
        candidates = [c for c in candidates if _candidate_sku(c) in only_skus]

    published = 0
    for candidate in candidates:
        try:
            _review_one(db, candidate, sequence=published + 1)
        except Exception as exc:  # noqa: BLE001 — the reason IS the finding
            # A row the pipeline refuses to publish is a result, not a crash.
            # Aborting here would hide every other row behind the first bad one.
            db.rollback()
            refused[_candidate_sku(candidate) or candidate.mastering_candidate_uuid] = (
                f"{type(exc).__name__}: {exc}"
            )
        else:
            published += 1
    return published


def _review_one(db, candidate, *, sequence: int) -> None:
    """One candidate through the desk: confirm the create, approve, apply, publish."""
    variant = json.loads(candidate.product_variant_resolution_json or "{}")
    brand_res = json.loads(candidate.brand_resolution_json or "{}")
    name = (variant.get("product_variant_name") or variant.get("proposed_name") or "").strip() or "Unnamed"
    brand = (brand_res.get("brand_name") or brand_res.get("proposed_name") or "").strip()
    if not brand:
        # Fall back to the brand conformance read off the row rather than a
        # placeholder. A made-up value here would surface in the export as a
        # brand mismatch and read as a pipeline defect the harness invented.
        row = (
            db.query(models.CatalogueNormalizedRow)
            .filter_by(catalogue_item_uuid=candidate.catalogue_item_uuid)
            .first()
        )
        if row is not None:
            fields = json.loads(row.normalized_fields_json or "{}")
            brand = ((fields.get("brand") or {}).get("value") or "").strip()
    brand = brand or "Unbranded"
    # Take the draft's UOM from what the pipeline actually read, not a
    # constant — a stubbed "unit" here would surface as a packaging
    # mismatch in the diff and read as a pipeline defect that isn't one.
    packaging = (json.loads(candidate.packaging_resolution_json or "{}") or {}).get("packaging") or {}
    # Only the sellable unit. Falling back to the purchase unit would put "box"
    # on the product identity, and the export reads sellable_uom off the
    # identity when packaging has none — so the harness would be the thing
    # answering "what do you sell one of", wrongly, and it would read as a
    # pipeline defect.
    uom = ((packaging.get("sellable_unit_uom") or {}).get("code") or "unit").lower()

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
    # output_ids is typed UUID | str; the commands below want a UUID.
    candidate_id = UUID(str(revised.output_ids[0]))

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
    # One version per candidate. The version doubles as an idempotency key, so
    # reusing a single string collides the moment two rows resolve to the same
    # canonical product — which a real catalogue does routinely, listing one
    # product at several order quantities.
    stages.ServingPublicationService(db).publish(
        stages.PublishServingItemCommand(
            mastering_candidate_id=candidate_id,
            publication_version=f"golden-{sequence}",
        )
    )


def _replay_to_published(
    db,
    monkeypatch,
    *,
    fixture_dir: Path,
    page_names: list[str],
    supplier_id: int,
    provider: str,
    api_key_var: str,
    only_skus: set[str] | None = None,
    refused: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Replay a golden set through the whole pipeline and return its export.

    Submission → extraction (replayed) → conformance → mastering → review →
    serving, then the sheet's own columns out of `golden_rows`. Nothing here
    reaches a provider.
    """
    monkeypatch.setenv("CATALOGUE_VISION_PROVIDER", provider)
    monkeypatch.setenv(api_key_var, "golden-replay")
    pages = [fixture_dir / name for name in page_names]
    calls = _install_golden_replay(monkeypatch, pages)

    service = CatalogueSubmissionService(
        db, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"], max_upload_bytes=8 * 1024 * 1024
    )
    submitted = service.submit(
        CatalogueSubmissionCommand(
            supplier_id=supplier_id,
            original_filename=f"{fixture_dir.name}-golden.pdf",
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
    assert _take_through_review(db, run_uuid, only_skus=only_skus, refused=refused) > 0, (
        "nothing reached the serving layer to compare"
    )
    published = {row["supplier_product_code"]: row for row in golden_rows(db, UUID(run_uuid))}
    assert published, "golden_rows returned nothing after publishing"
    return published


#: Where the per-run quality reports land. Written by the comparison test and
#: uploaded by CI, so a run's numbers survive the job that produced them.
QUALITY_REPORT_DIR = Path(os.environ.get("GOLDEN_REPORT_DIR") or (Path(__file__).parent.parent / ".golden-reports"))


def _write_quality_report(
    spec: "GoldenSet",
    db,
    run_uuid: str,
    enforced_fields: dict[str, int],
    gaps_still_failing: list[str],
) -> dict:
    """Rows in, rows out, and how the comparison went — one file per set.

    The thing that replaces the manual read-through. The field tallies name
    their denominator: they cover the ENFORCED columns only, and the known-gap
    columns that still disagree are listed beside them — otherwise
    "mismatched=0" reads as the whole sheet agreeing when five columns of
    twenty were compared.
    """
    from services import catalogue_dead_letters as dead_letters

    reconciliation = dead_letters.reconcile(db, run_uuid)
    issues = (
        db.query(models.CatalogueValidationIssue.severity, models.CatalogueValidationIssue.issue_code)
        .filter(models.CatalogueValidationIssue.ingestion_run_uuid == run_uuid)
        .all()
    )
    by_severity: dict[str, int] = {}
    for severity, _code in issues:
        by_severity[severity] = by_severity.get(severity, 0) + 1

    report = {
        "golden_set": spec.name,
        "supplier": spec.supplier_name,
        "ingestion_run_id": run_uuid,
        "rows_detected": reconciliation.raw_product_rows,
        "rows_staged": reconciliation.normalized_rows,
        "lanes": reconciliation.lane_counts,
        "issues_raised": {"total": len(issues), "by_severity": by_severity},
        "enforced_fields": {"columns": sorted(spec.enforced), **enforced_fields},
        "known_gaps": {
            "columns": sorted(spec.known_gaps),
            "still_failing": gaps_still_failing,
        },
        "dead_letters_by_code": [
            {"issue_code": t.issue_code, "rows_blocked": t.rows_blocked,
             "rows_cleared_if_fixed": t.rows_cleared_if_fixed}
            for t in dead_letters.tallies_by_issue_code(db, run_uuid=run_uuid)
        ],
    }
    QUALITY_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (QUALITY_REPORT_DIR / f"{spec.name}.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        f"\n[golden] {spec.name}: detected={report['rows_detected']} staged={report['rows_staged']} "
        f"published={report['lanes'].get('published', 0)} dead_lettered={report['lanes'].get('dead_lettered', 0)} "
        f"| enforced matched={enforced_fields['matched']} mismatched={enforced_fields['mismatched']} "
        f"missing={enforced_fields['missing']} | known gaps still failing "
        f"{len(gaps_still_failing)}/{len(spec.known_gaps)}"
    )
    return report


def _replay_set(
    db,
    monkeypatch,
    spec: GoldenSet,
    *,
    only_skus: set[str] | None = None,
    refused: dict[str, str] | None = None,
) -> dict[str, dict[str, str]]:
    """Replay a discovered set, taking its pages and provider from the manifest."""
    return _replay_to_published(
        db,
        monkeypatch,
        fixture_dir=spec.path,
        page_names=list(spec.page_names),
        supplier_id=spec.supplier_id,
        provider=spec.provider,
        api_key_var=spec.api_key_var,
        only_skus=only_skus,
        refused=refused,
    )


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

    spec = golden_set("hills_classic")
    monkeypatch.setenv("CATALOGUE_VISION_PROVIDER", spec.provider)
    monkeypatch.setenv(spec.api_key_var, "golden-replay")
    pages = [spec.path / name for name in spec.page_names]
    calls = _install_golden_replay(monkeypatch, pages)
    expected_rows = sum(len(json.loads(p.read_text())["rows"]) for p in pages)

    service = CatalogueSubmissionService(
        db, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"], max_upload_bytes=4 * 1024 * 1024
    )
    submitted = service.submit(
        CatalogueSubmissionCommand(
            supplier_id=spec.supplier_id,
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



@pytest.mark.parametrize("spec", GOLDEN_SETS, ids=lambda spec: spec.name)
def test_the_published_export_matches_the_hand_filled_sheet(spec: GoldenSet, db, monkeypatch):
    """Every discovered golden set, compared field by field against the sheet.

    Parametrised over `discover_golden_sets()`, so dropping a directory in adds
    a case and touching this file is not part of adding a supplier. What each
    set asserts — which columns must match, which are known defects, which mean
    different things on the two sides — travels with the fixture in
    `expectations.json`, because those judgements are about that supplier's
    catalogue and belong beside it.
    """
    expected = _load_expected(spec.path)
    refused: dict[str, str] = {}
    published = _replay_set(db, monkeypatch, spec, only_skus=set(expected), refused=refused)

    covered = sorted(set(expected) & set(published))
    if spec.covered_skus:
        # Pinned exactly, because a floor lets a swap hide: lose one covered
        # SKU, gain another, and the count never moves. Growth is good news —
        # update the pin; the message says which direction it went.
        pinned = sorted(spec.covered_skus)
        gained = sorted(set(covered) - set(pinned))
        lost = sorted(set(pinned) - set(covered))
        assert covered == pinned, (
            f"{spec.name}: the covered SKU set changed.\n"
            f"  lost:   {lost or '—'}\n"
            f"  gained: {gained or '—'}\n"
            f"Losing one is a regression even when the count holds. Update covered_skus "
            f"in expectations.json only for genuine growth."
        )
    else:
        assert len(covered) >= spec.min_covered_skus, (
            f"{spec.name}: only {len(covered)} of the sheet's {len(expected)} SKUs reached the "
            f"export, expected at least {spec.min_covered_skus} — coverage must not shrink"
        )

    enforced_fields = {"matched": 0, "mismatched": 0, "missing": 0}
    for sku in covered:
        for column in spec.enforced:
            want = (expected[sku].get(column) or "").strip()
            if not want:
                continue
            got = (published[sku].get(column) or "").strip()
            if not got:
                enforced_fields["missing"] += 1
            elif want == got:
                enforced_fields["matched"] += 1
            else:
                enforced_fields["mismatched"] += 1

    # Computed before the report so the report can carry it: which known-gap
    # columns still disagree. Without this the report's "mismatched=0" reads as
    # the whole sheet agreeing, when it is only the enforced columns.
    gaps_still_failing = sorted({
        column
        for sku in covered
        for column in spec.known_gaps
        if _diff(expected[sku], published[sku], (column,))
    })
    _write_quality_report(
        spec,
        db,
        db.query(models.IngestionRun).order_by(models.IngestionRun.id.desc()).first().run_uuid,
        enforced_fields,
        gaps_still_failing,
    )

    failures = [
        f"{sku}  {problem}"
        for sku in covered
        for problem in _diff(expected[sku], published[sku], spec.enforced)
    ]
    assert not failures, (
        f"{spec.name}: the published export disagrees with the hand-filled sheet on a "
        f"column that must match:\n  " + "\n  ".join(failures)
    )

    # Every column decided about, one way or another. A column in none of the
    # four buckets is one nobody has looked at, and silence is not agreement.
    unclassified = (
        set(GOLDEN_COLUMNS)
        - set(spec.enforced)
        - set(spec.known_gaps)
        - set(spec.not_comparable)
        - set(spec.unfilled)
    )
    assert not unclassified, (
        f"{spec.name}: columns with no stated expectation in expectations.json: "
        f"{sorted(unclassified)}"
    )

    # A column called unfilled must actually be unfilled. Otherwise the sheet
    # gains a value and nothing compares it, because it was excused years ago.
    for column in spec.unfilled:
        filled = [sku for sku in covered if (expected[sku].get(column) or "").strip()]
        assert not filled, (
            f"{spec.name}: {column} is now filled in the sheet for {filled} — move it out "
            f"of unfilled in expectations.json so it is actually compared"
        )

    # The known defects, pinned. Fixing one fails here with the column named,
    # which is the point: an improvement nobody noticed is still a surprise.
    fixed = sorted(set(spec.known_gaps) - set(gaps_still_failing))
    assert not fixed, (
        f"{spec.name}: these columns now match the sheet and are no longer defects: {fixed}. "
        f"Move them from known_gaps into enforced in expectations.json so they stay fixed."
    )

    # Refusing to publish beats publishing a wrong number, so a refusal is
    # pinned as expected behaviour — but the population must not grow.
    assert set(refused) == set(spec.unpublishable), (
        f"{spec.name}: rows the pipeline refused to publish changed.\n"
        f"  expected: {sorted(spec.unpublishable)}\n"
        f"  actual:   " + "\n            ".join(f"{k}: {v[:90]}" for k, v in sorted(refused.items()))
    )

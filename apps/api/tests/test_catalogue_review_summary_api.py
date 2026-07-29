"""Reviewer summary view and product-variant picker over the pipeline API.

Same deterministic setup as the per-layer read tests (stubbed vision returns
contract-labeled cells; the supplier contract maps them with no AI): one real
submission runs the pipeline, then the HITL read models are asserted — the
decision-ready summary rows with their sanity context, and the run-scoped
variant search the correction picker uses.
"""

from __future__ import annotations

import json
import os
import tempfile
from io import BytesIO
from uuid import UUID

import pytest
import pypdf

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

import database  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402
from dependencies import require_user  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from services import catalogue_evidence_extraction as extraction  # noqa: E402
from services.catalogue_submission import CatalogueSubmissionCommand, CatalogueSubmissionService  # noqa: E402


models.Base.metadata.create_all(bind=database.engine)

HILLS_ROW = {
    "Product Code / 產品編號": "10447",
    "Product Range / 產品系列": "Science Plan",
    "Life Stage / 生命階段": "Adult",
    "Product Description / 產品名稱": "Chicken 82g",
    "Size / 重量": "82g",
    "Gross Wholesale Price / 每箱·罐": "13.10",
    "Order Multiple / 訂貨單位": "12",
}


class _Admin:
    id = 601
    username = "summary-admin"
    display_name = "Summary Admin"
    role = "admin"


@pytest.fixture(autouse=True)
def _auth():
    previous = main.app.dependency_overrides.get(require_user)
    main.app.dependency_overrides[require_user] = lambda: _Admin()
    yield
    if previous is None:
        main.app.dependency_overrides.pop(require_user, None)
    else:
        main.app.dependency_overrides[require_user] = previous


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("CATALOGUE_ORCHESTRATION_MAX_SOURCE_BYTES", str(1024 * 1024))
    session = database.SessionLocal()
    try:
        _reset(session)
        if session.get(models.Supplier, 14) is None:
            session.add(models.Supplier(id=14, code="HILLS", name="Hill's", created_at="2026-07-25T00:00:00+00:00"))
            session.commit()
        yield session
        session.rollback()
        _reset(session)
    finally:
        session.close()


@pytest.fixture()
def client(db):
    return TestClient(main.app)


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
    for sku in ("10447", "RIMS-SUM-1"):
        variant = session.query(models.ProductVariant).filter_by(sku_code=sku).first()
        if variant is not None:
            session.query(models.SellingItem).filter_by(product_variant_id=variant.id).delete()
            session.query(models.InventoryItem).filter_by(product_variant_id=variant.id).delete()
            session.query(models.ProductSupplier).filter_by(product_id=variant.id).delete()
            session.delete(variant)
    session.commit()


def _pdf_bytes() -> bytes:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def _vision_envelope(rows: list[dict[str, str]]) -> str:
    columns: list[str] = []
    for row in rows:
        for column in row:
            if column not in columns:
                columns.append(column)
    return json.dumps(
        {
            "page_outcome": "evidence",
            "columns": columns,
            "rows": [
                {"cells": [row.get(column) for column in columns], "confidence": "0.95"}
                for row in rows
            ],
        }
    )


def _run_pipeline(db, monkeypatch) -> UUID:
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setattr(
        extraction,
        "_call_gemini_vision",
        lambda content, *, media_type: extraction._VisionResponse(text=_vision_envelope([HILLS_ROW])),
    )
    service = CatalogueSubmissionService(db, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"], max_upload_bytes=1024 * 1024)
    result = service.submit(
        CatalogueSubmissionCommand(
            supplier_id=14,
            original_filename="hills.pdf",
            content_type="application/pdf",
            stream=BytesIO(_pdf_bytes()),
            contract_id=None,
            contract_version=None,
            idempotency_key=None,
            submitted_by="pytest",
        )
    )
    catalogue_ingestion_flow(ingestion_run_id=result.ingestion_run_id)
    return result.ingestion_run_id


def _seed_variant(db, sku: str, *, name: str, selling_price: float | None = None) -> models.ProductVariant:
    variant = models.ProductVariant(
        sku_code=sku,
        name=name,
        brand="Hill's",
        category="Food",
        storage_rule="any",
        status="ACTIVE",
        created_at="2026-07-29T00:00:00+00:00",
        updated_at="2026-07-29T00:00:00+00:00",
    )
    db.add(variant)
    db.flush()
    if selling_price is not None:
        db.add(
            models.SellingItem(
                selling_item_key=f"variant:{sku}:channel:shopify",
                product_variant_id=variant.id,
                channel="shopify",
                selling_price=selling_price,
                status="ACTIVE",
                created_at="2026-07-29T00:00:00+00:00",
                updated_at="2026-07-29T00:00:00+00:00",
            )
        )
    return variant


def test_summary_view_returns_decision_ready_rows(client, db, monkeypatch):
    run = _run_pipeline(db, monkeypatch)
    body = client.get(f"/catalogues/ingestions/{run}/intermediate?view=summary").json()

    assert body["layer"] == "intermediate"
    assert body["view"] == "summary"
    assert body["counts"]["total"] == 1
    assert body["counts"]["by_review_status"] == {"PENDING_REVIEW": 1}
    assert isinstance(body["run_issues"], list)
    # Full contracts stay out of the summary payload.
    assert "mastering_candidates" not in body
    assert "normalized_rows" not in body

    item = body["items"][0]
    assert item["supplier_sku"] == "10447"
    assert item["name"] == "Science Plan Adult Chicken 82g"
    assert item["cost_amount"] == 13.10
    assert item["cost_currency"] == "HKD"
    assert item["page"] == 1
    # Fresh database: no offering, no variant mapping — both propose creation.
    assert item["offering_state"] == "PROPOSED_CREATE"
    assert item["variant_state"] == "PROPOSED_CREATE"
    assert item["canonical_sku"] is None
    assert item["current_cost"] is None
    assert item["price_delta_pct"] is None
    assert item["selling_price"] is None
    assert item["published"] is False
    assert item["superseded_by"] is None
    # Family evidence comes from the contract's Range column, verbatim.
    assert item["family_key"] == "Science Plan"

    # The room's source panel: candidate detail carries its verbatim evidence.
    detail = client.get(
        f"/catalogues/ingestions/{run}/mastering-candidates/{item['mastering_candidate_id']}"
    ).json()
    assert len(detail["evidence"]) == 1
    assert detail["evidence"][0]["page"] == 1
    cells = {cell["column_name"]: cell["value"] for cell in detail["evidence"][0]["cells"]}
    assert cells["Product Code / 產品編號"] == "10447"
    assert cells["Product Range / 產品系列"] == "Science Plan"


def test_summary_matched_candidate_carries_price_delta_and_selling_price(client, db, monkeypatch):
    variant = _seed_variant(db, "10447", name="Hill's Science Plan Adult Chicken 82g", selling_price=79.0)
    offering = models.SupplierOffering(
        supplier_product_key="supplier:14:offer:10447",
        supplier_id=14,
        product_variant_id=variant.id,
        supplier_sku="10447",
        status="active",
        created_at="2026-07-29T00:00:00+00:00",
        updated_at="2026-07-29T00:00:00+00:00",
    )
    db.add(offering)
    db.flush()
    db.add(
        models.CatalogueSupplierPrice(
            supplier_product_id=offering.id,
            amount=12.80,
            currency="HKD",
            price_basis_uom_code="UNIT",
            is_current=1,
            created_at="2026-07-29T00:00:00+00:00",
        )
    )
    db.commit()

    run = _run_pipeline(db, monkeypatch)
    item = client.get(f"/catalogues/ingestions/{run}/intermediate?view=summary").json()["items"][0]

    assert item["offering_state"] == "PROPOSED_MATCH"
    assert item["variant_state"] == "PROPOSED_MATCH"
    assert item["canonical_sku"] == "10447"
    assert item["current_cost"] == 12.80
    assert item["price_delta_pct"] == 2.3
    assert item["selling_price"] == 79.0
    assert item["selling_channel"] == "shopify"


def test_variant_search_scopes_sanity_to_run_supplier(client, db, monkeypatch):
    variant = _seed_variant(db, "RIMS-SUM-1", name="Hill's d/d Duck & Green Pea 3.5lb", selling_price=79.0)
    db.add(
        models.ProductSupplier(
            product_id=variant.id,
            supplier_id=14,
            supplier_sku="5351",
            basic_cost=33.90,
            updated_at="2026-07-29T00:00:00+00:00",
        )
    )
    db.commit()
    run = _run_pipeline(db, monkeypatch)

    body = client.get(f"/catalogues/ingestions/{run}/variant-search?q=duck").json()
    assert body["query"] == "duck"
    match = next(r for r in body["results"] if r["sku_code"] == "RIMS-SUM-1")
    assert match["name"] == "Hill's d/d Duck & Green Pea 3.5lb"
    # No SupplierOffering yet: the legacy ProductSupplier cost is the current cost.
    assert match["offering_cost"] == 33.90
    assert match["offering_source"] == "legacy"
    assert match["selling_price"] == 79.0
    assert match["selling_channel"] == "shopify"

    assert client.get(f"/catalogues/ingestions/{run}/variant-search?q=d").status_code == 422
    missing = "99999999-9999-4999-8999-999999999999"
    assert client.get(f"/catalogues/ingestions/{missing}/variant-search?q=duck").status_code == 404


def test_summary_and_search_require_authorization(client, db, monkeypatch):
    run = _run_pipeline(db, monkeypatch)
    main.app.dependency_overrides.pop(require_user, None)
    try:
        assert client.get(f"/catalogues/ingestions/{run}/intermediate?view=summary").status_code in {401, 403}
        assert client.get(f"/catalogues/ingestions/{run}/variant-search?q=duck").status_code in {401, 403}
    finally:
        main.app.dependency_overrides[require_user] = lambda: _Admin()

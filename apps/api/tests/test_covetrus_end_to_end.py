"""The Covetrus catalogue from picture book to published row.

What conformance cannot show: that the forty-three unpriced tables stay out all
the way through. They describe products priced elsewhere in the same document,
so if they leak past staging the catalogue publishes itself twice — once with a
price and once without — and the desk has no way to tell which row is which.
"""

from __future__ import annotations

import os
import re
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/cov_e2e.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

import pytest  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from services.catalogue_golden_export import golden_rows  # noqa: E402
from services.catalogue_submission import (  # noqa: E402
    CatalogueSubmissionCommand,
    CatalogueSubmissionService,
)
from test_catalogue_golden_suppliers import (  # noqa: E402
    _blank_pdf,
    _install_golden_replay,
    _take_through_review,
)

models.Base.metadata.create_all(bind=database.engine)

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "covetrus"
SUPPLIER_ID = 62
CONTRACT_ID = "covetrus.branded_products_catalogue.v1"

#: code -> (price, sellable units). One from each of the two table shapes.
EXPECTED = {
    "CV-2800540": (Decimal("54.00"), "100"),   # box of a hundred syringes
    "PV-MAXINJ20": (Decimal("258"), None),     # a 20mL bottle, not twenty things
}


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    patch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path_factory.mktemp("cov_uploads")))
    patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
    patch.setenv("ANTHROPIC_API_KEY", "replay-only")
    calls = _install_golden_replay(patch, [FIXTURES / "whole_document.json"])

    session = database.SessionLocal()
    try:
        if session.get(models.Supplier, SUPPLIER_ID) is None:
            session.add(models.Supplier(
                id=SUPPLIER_ID, name="ProVet Kruuse HK", code="PROVETKR",
                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
            ))
            session.commit()

        submitted = CatalogueSubmissionService(
            session, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"]
        ).submit(CatalogueSubmissionCommand(
            supplier_id=SUPPLIER_ID,
            original_filename="covetrus-catalogue-2025.pdf",
            content_type="application/pdf",
            stream=BytesIO(_blank_pdf(1)),
            contract_id=CONTRACT_ID,
            contract_version="v1",
            submitted_by="e2e",
        ))
        catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)
        assert calls["n"] == 1, "replayed from the recorded document — no provider call"

        refused: dict[str, str] = {}
        _take_through_review(
            session, str(submitted.ingestion_run_id), only_skus=set(EXPECTED), refused=refused
        )
        assert not refused, f"the pipeline refused to publish {refused}"

        rows = {
            row["supplier_product_code"]: row
            for row in golden_rows(session, UUID(str(submitted.ingestion_run_id)))
        }
        staged = (
            session.query(models.CatalogueNormalizedRow)
            .filter_by(ingestion_run_uuid=str(submitted.ingestion_run_id))
            .count()
        )
        assert rows, "nothing reached the serving layer"
        yield rows, staged
    finally:
        session.close()
        patch.undo()


@pytest.fixture(scope="module")
def published(run):
    return run[0]


def _money(text):
    cleaned = re.sub(r"[^\d.]", "", str(text or ""))
    return Decimal(cleaned) if cleaned else None


def test_only_the_priced_rows_ever_reach_staging(run):
    """122 of the catalogue's rows are priced and the rest are pictures. If the
    picture pages leaked through, the same syringe would sit on the desk twice —
    once at $54.00 and once at nothing — and no one could tell them apart."""
    _, staged = run

    assert staged == 122


def test_both_table_shapes_publish(published):
    assert set(EXPECTED) <= set(published)


def test_the_published_price_and_pack_are_the_ones_printed(published):
    for code, (price, units) in EXPECTED.items():
        assert _money(published[code]["catalogue_price_hkd"]) == price, code
        assert published[code]["catalogue_price_basis_uom"] == "PACK", code
        got = str(published[code].get("sellable_units_per_price_basis") or "")
        assert got == (units or "") or (units is None and got in ("", "1", "None")), code


def test_a_millilitre_never_becomes_a_count_on_the_way_to_the_desk(published):
    """The Maxicam row's Pack Size is 20mL — what the bottle holds. Published as
    twenty sellable units it would price the injection at a twentieth of HK$258."""
    row = published["PV-MAXINJ20"]

    assert _money(row["catalogue_price_hkd"]) == Decimal("258")
    assert str(row.get("sellable_units_per_price_basis") or "") in ("", "1", "None")

"""Queen's Pharma from photographed form to published row.

The question this answers that the contract test cannot: do CODELESS rows
actually reach the serving layer? Every other supplier's end-to-end rests on a
printed item code carrying identity the whole way. Queen's has none, so each
row must create a product entity at the desk and adopt that entity's Rosetta
SKU as the offering identity at apply — the 2026-08-26 ruling. If that path is
broken, the contract test still passes and nothing publishes.

The source is a real JPEG, uploaded as a JPEG. That is the other thing under
test: a photograph is a carrier, and an image-sourced run must publish exactly
what the same page publishes wrapped in a PDF.
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/queens_e2e.db")
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
from test_catalogue_golden_suppliers import _install_golden_replay, _take_through_review  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "queens_pharma"
PAGES = [FIXTURES / f"page_{n}.json" for n in (1, 2, 3)]
SUPPLIER_ID = 63
CONTRACT_ID = "queens_pharma.zoetis_price_list.v1"

#: What each form prints, keyed by the product name the desk will see.
PRICES = {
    "CYTOPOINT 10mg": "650", "CYTOPOINT 20mg": "950",
    "CYTOPOINT 30mg": "1150", "CYTOPOINT 40mg": "1300",
    "LIBRELA 5mg": "600", "LIBRELA 10mg": "630", "LIBRELA 15mg": "680",
    "SOLENSIA 7MG": "650",
    "AlphaTRAK 3 (Blood Glucose Monitoring System ) Starter Kit": "1250",
    "AlphaTRAK 3": "350",
}

def _jpeg(seed: str) -> bytes:
    """A minimal, DISTINCT JPEG per form.

    The bytes never reach a vision provider — the recorded envelopes are
    replayed — but they must satisfy the capability gate's signature check,
    which is half of what this file exercises. Distinct because submission is
    idempotent on file content, so three identical uploads would be one run.
    """
    return (
        bytes.fromhex("ffd8ffe000104a46494600010100000100010000")
        + seed.encode("ascii").ljust(512, b"\x00")
        + bytes.fromhex("ffd9")
    )


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> dict[str, dict[str, str]]:
    """Three photographed forms, each uploaded as its own JPEG, all the way to
    the serving layer.

    Three submissions rather than one, because that is what Queen's actually
    send: a separate photo per product range. It is also what the pipeline
    requires — a PDF is split into pages and read one at a time, but an IMAGE
    is a single indivisible unit, so one photo yields one page of evidence.
    """
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    patch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path_factory.mktemp("queens_uploads")))
    patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
    patch.setenv("ANTHROPIC_API_KEY", "replay-only")

    session = database.SessionLocal()
    try:
        if session.get(models.Supplier, SUPPLIER_ID) is None:
            session.add(models.Supplier(
                id=SUPPLIER_ID, name="Queen's Pharma Limited", code="QUEENSPH",
                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
            ))
            session.commit()

        rows: dict[str, dict[str, str]] = {}
        for page in PAGES:
            calls = _install_golden_replay(patch, [page])
            submitted = CatalogueSubmissionService(
                session, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"]
            ).submit(CatalogueSubmissionCommand(
                supplier_id=SUPPLIER_ID,
                # A JPEG, uploaded as a JPEG, against a PDF_TABLE contract.
                original_filename=f"queens-{page.stem}.jpeg",
                content_type="image/jpeg",
                stream=BytesIO(_jpeg(page.stem)),
                contract_id=CONTRACT_ID,
                contract_version="v1",
                submitted_by="e2e",
            ))
            catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)
            assert calls["n"] == 1, f"{page.stem}: an image is one unit, not {calls['n']}"

            refused: dict[str, str] = {}
            _take_through_review(session, str(submitted.ingestion_run_id), refused=refused)
            assert not refused, f"{page.stem}: the pipeline refused to publish {refused}"
            rows.update({
                (row.get("product_name") or "").strip(): row
                for row in golden_rows(session, UUID(str(submitted.ingestion_run_id)))
            })

        assert rows, "nothing reached the serving layer"
        yield rows
    finally:
        session.close()
        patch.undo()


def test_a_jpeg_is_accepted_and_stored_as_an_image(published):
    """The capability gate, the submission format check and the flow's
    re-check, all three, on a file that never touches a PDF wrapper."""
    session = database.SessionLocal()
    try:
        doc = (
            session.query(models.CatalogueSourceDocument)
            .filter(models.CatalogueSourceDocument.supplier_id == SUPPLIER_ID)
            .order_by(models.CatalogueSourceDocument.id.desc())
            .first()
        )
        assert doc is not None
        assert doc.source_format == "IMAGE"
        assert doc.source_ref.endswith(".jpeg")
    finally:
        session.close()


def test_every_codeless_product_reaches_the_serving_layer(published):
    """The whole point. No row carries an item code, so each must take identity
    from the entity confirmed at the desk. A break here publishes nothing while
    every conformance test still passes."""
    assert set(published) == set(PRICES)


def test_the_published_price_is_the_price_the_form_printed(published):
    """Ten prices, read off a photograph, unchanged all the way to the desk."""
    for name, want in PRICES.items():
        got = published[name]["catalogue_price_hkd"].replace("$", "").replace(",", "")
        assert Decimal(got) == Decimal(want), f"{name}: form {want}, published {got}"


def test_the_published_row_carries_an_identity_it_was_never_given(published):
    """A Queen's form prints no code, yet a published offering must be keyed by
    something. That something is the Rosetta SKU minted for the product created
    at the match, back-written onto the candidate at apply."""
    for name, row in published.items():
        assert (row.get("supplier_product_code") or "").strip(), (
            f"{name} published with no identity at all"
        )


def test_the_box_of_two_survives_to_the_desk(published):
    """$650 buys a box of two vials — $325 each. The figure BizOps recorded by
    hand, arriving at the serving layer by itself."""
    row = published["CYTOPOINT 40mg"]

    assert row["catalogue_price_basis_uom"] == "BOX"
    assert str(row["sellable_units_per_price_basis"]) == "2"

"""United Italian from price list to published row.

The contract test proves the pages are READ correctly. This proves the reading
survives to the serving layer — which for this supplier means the thing most
worth protecting, the PRICE BASIS, is still attached to the money at the end.

Scoped to a handful of codes rather than all 657: each one has to be matched
and created at the desk, and what is under test here is the path, not the
volume. The codes chosen are the ones that would fail differently — a bag with
a case price beside it, a price naming a count, a price per piece where the
pack is a case, and an ordinary box.
"""

from __future__ import annotations

import os
import re
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/ui_e2e.db")
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

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "united_italian"
PAGES = [FIXTURES / f"page_{n}.json" for n in range(1, 12)]
SUPPLIER_ID = 46
CONTRACT_ID = "united_italian.gp_price_list.v1"

#: code -> (price, basis). One of each way this source can be misread.
EXPECTED = {
    "AHB1323HK": (Decimal("46.00"), "BAG"),    # a case price sits beside it
    "1208A – D": (Decimal("46.00"), "PACK"),   # the price names a count
    "89471": (Decimal("52.00"), "PIECE"),      # per piece, packed by the case
    "48100": (Decimal("100.00"), "BOX"),       # an ordinary box
    # A unit we have no code for. It resolves to OTHER carrying the supplier's
    # own word, and the export publishes that word — which is why the golden
    # sheet's "SLEEVE" is right and keeping it there was not a fudge.
    "2103-200": (Decimal("37.00"), "sleeve"),
}


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    """The recorded pages, taken all the way to the serving layer."""
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    patch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path_factory.mktemp("ui_uploads")))
    patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
    patch.setenv("ANTHROPIC_API_KEY", "replay-only")
    calls = _install_golden_replay(patch, PAGES)

    session = database.SessionLocal()
    try:
        if session.get(models.Supplier, SUPPLIER_ID) is None:
            session.add(models.Supplier(
                id=SUPPLIER_ID, name="United Italian Corp. (HK) Ltd", code="UNITEDIT",
                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
            ))
            session.commit()

        submitted = CatalogueSubmissionService(
            session, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"]
        ).submit(CatalogueSubmissionCommand(
            supplier_id=SUPPLIER_ID,
            original_filename="2025-ui-gp-price-list.pdf",
            content_type="application/pdf",
            stream=BytesIO(_blank_pdf(len(PAGES))),
            contract_id=CONTRACT_ID,
            contract_version="v1",
            submitted_by="e2e",
        ))
        catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)
        assert calls["n"] == len(PAGES), "replayed from the recorded pages — no provider call"

        refused: dict[str, str] = {}
        _take_through_review(
            session, str(submitted.ingestion_run_id), only_skus=set(EXPECTED), refused=refused
        )
        assert not refused, f"the pipeline refused to publish {refused}"

        rows = {
            row["supplier_product_code"]: row
            for row in golden_rows(session, UUID(str(submitted.ingestion_run_id)))
        }
        assert rows, "nothing reached the serving layer"
        yield rows
    finally:
        session.close()
        patch.undo()


def _money(text):
    cleaned = re.sub(r"[^\d.]", "", str(text or ""))
    return Decimal(cleaned) if cleaned else None


def test_the_chosen_products_all_reach_the_serving_layer(published):
    assert set(EXPECTED) <= set(published)


def test_the_published_price_is_the_price_the_page_printed(published):
    """657 lines and 23 basis spellings between the page and here; the money
    must arrive unchanged."""
    for code, (want, _) in EXPECTED.items():
        got = _money(published[code]["catalogue_price_hkd"])
        assert got == want, f"{code}: page {want}, published {got}"


def test_a_unit_we_have_no_code_for_still_reaches_the_desk_by_name(published):
    """$37.00 / sleeve. There is no SLEEVE unit code, so it resolves to OTHER —
    but the supplier's own word rides along as the label and is what the export
    publishes. The alternative, dropping to a bare OTHER, would tell the desk
    the price is per something-unnamed."""
    assert published["2103-200"]["catalogue_price_basis_uom"] == "sleeve"


def test_the_basis_survives_to_the_desk(published):
    """The half of the price that can be wrong by fiftyfold while the number
    looks perfect. A bag published as a box, or a piece as a case, is the
    defect that reaches a purchase order."""
    for code, (_, want) in EXPECTED.items():
        assert published[code]["catalogue_price_basis_uom"] == want, code


def test_a_case_price_never_becomes_the_cost(published):
    """AHB1323HK prints $46.00 / bag beside $828.00 / box. The bulk term must
    not arrive as the catalogue cost — that would overstate a single bag
    eighteenfold."""
    row = published["AHB1323HK"]

    assert _money(row["catalogue_price_hkd"]) == Decimal("46.00")
    assert "828" not in row["catalogue_price_hkd"]

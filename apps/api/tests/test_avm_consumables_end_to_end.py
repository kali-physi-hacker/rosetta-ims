"""AVM's consumables list from price list to published row.

One thing here cannot be proved by conformance and matters more than anything
else in this contract: the supplier code was not read from a column — there
isn't one — it was pulled out of the middle of a product name by pattern. This
takes such a row all the way to the serving layer and checks the code is still
attached to it, because a code that survives conformance and evaporates before
publication is worse than no code: the reconciliation it exists for happens
downstream of here.
"""

from __future__ import annotations

import os
import re
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/avm_e2e.db")
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

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "avm_consumables"
PAGES = [FIXTURES / f"page_{n}.json" for n in range(1, 14)]
SUPPLIER_ID = 3
CONTRACT_ID = "asia_vet_medical.consumables_price_list.v1"

#: The codes this test follows — all pulled out of a description by pattern.
EXPECTED = {
    "NG622": Decimal("270.00"),    # Mila nasogastric tube, no pack stated
    "E1030": Decimal("365.00"),    # Mila esophagostomy tube
    "J322a2": Decimal("279.00"),   # a stain, lower-case in the middle of the code
}


@pytest.fixture(scope="module")
def published(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    patch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path_factory.mktemp("avm_uploads")))
    patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
    patch.setenv("ANTHROPIC_API_KEY", "replay-only")
    calls = _install_golden_replay(patch, PAGES)

    session = database.SessionLocal()
    try:
        if session.get(models.Supplier, SUPPLIER_ID) is None:
            session.add(models.Supplier(
                id=SUPPLIER_ID, name="Asia Vet Medical Limited", code="AVM",
                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
            ))
            session.commit()

        submitted = CatalogueSubmissionService(
            session, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"]
        ).submit(CatalogueSubmissionCommand(
            supplier_id=SUPPLIER_ID,
            original_filename="avm-consumables-2025-10-20.pdf",
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


def test_a_code_pulled_out_of_a_name_is_the_published_supplier_code(published):
    """The point of the whole contract. There is no code column on this page —
    these three were read out of brackets inside the product name, and they are
    what a supplier invoice would be reconciled against."""
    assert set(EXPECTED) <= set(published)


def test_the_published_price_is_the_price_the_page_printed(published):
    for code, want in EXPECTED.items():
        got = _money(published[code]["catalogue_price_hkd"])
        assert got == want, f"{code}: page {want}, published {got}"


def test_a_price_publishes_as_the_pack_it_buys(published):
    """The page states no basis anywhere, so a price buys the pack."""
    for code in EXPECTED:
        assert published[code]["catalogue_price_basis_uom"] == "PACK", code


def test_a_product_with_no_pack_stated_publishes_without_inventing_one(published):
    """NG622 is one feeding tube. Its description carries a gauge and a length
    and no quantity, and neither may arrive as a count of tubes."""
    row = published["NG622"]

    assert str(row.get("sellable_units_per_price_basis") or "") in ("", "1", "None")

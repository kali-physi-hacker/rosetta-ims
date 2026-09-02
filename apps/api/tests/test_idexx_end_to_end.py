"""IDEXX from portal snapshot to published row, with no provider involved.

The snapshot is written by our own connector, so this path is deterministic all
the way through: a regression here would be arithmetic or mapping, never
perception. The fixture is a REAL capture — the live portal on 2026-09-02 under
the clinic's own account. Re-capture it with `services.idexx_connector.capture`
rather than editing it by hand; it is a reading of the portal, not an opinion.

What matters most here is the third of the catalogue IDEXX supplies free. A
pipeline that quietly drops those rows loses stock we hold and count, and one
that treats their zero as "no price" sends someone to chase a supplier who has
already answered.
"""

from __future__ import annotations

import csv
import os
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/idexx_e2e.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

import pytest  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from services import catalogue_evidence_extraction as extraction  # noqa: E402
from services import idexx_ingestion as ingestion  # noqa: E402
from services.catalogue_golden_export import golden_rows  # noqa: E402
from services.catalogue_submission import (  # noqa: E402
    CatalogueSubmissionCommand,
    CatalogueSubmissionService,
)
from test_catalogue_golden_suppliers import _take_through_review  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

SNAPSHOT = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "idexx" / "idexx_hk_snapshot.csv"


def _snapshot_rows() -> list[dict[str, str]]:
    with SNAPSHOT.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> dict[str, dict[str, str]]:
    """The captured portal, run through the pipeline ONCE for the whole module."""
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    patch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path_factory.mktemp("idexx_uploads")))

    # Any vision call at all would mean the snapshot was not read
    # deterministically — this source has no pages and nothing to perceive.
    def _refuse(*args, **kwargs):
        raise AssertionError("a CSV snapshot must never reach a vision provider")

    patch.setattr(extraction, "_call_vision", _refuse)

    session = database.SessionLocal()
    try:
        if session.get(models.Supplier, ingestion.SUPPLIER_ID) is None:
            session.add(models.Supplier(
                id=ingestion.SUPPLIER_ID, name="Asia Vet Medical Limited", code="AVM",
                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
            ))
            session.commit()

        submitted = CatalogueSubmissionService(
            session, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"]
        ).submit(CatalogueSubmissionCommand(
            supplier_id=ingestion.SUPPLIER_ID,
            original_filename=SNAPSHOT.name,
            content_type="text/csv",
            stream=BytesIO(SNAPSHOT.read_bytes()),
            contract_id=ingestion.CONTRACT_ID,
            contract_version=ingestion.CONTRACT_VERSION,
            submitted_by="e2e",
        ))
        catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)

        refused: dict[str, str] = {}
        _take_through_review(session, str(submitted.ingestion_run_id), refused=refused)
        assert not refused, f"the pipeline refused to publish {refused}"
        rows = {row["supplier_product_code"]: row
                for row in golden_rows(session, UUID(str(submitted.ingestion_run_id)))}
        assert rows, "nothing was published"
        yield rows
    finally:
        session.close()
        patch.undo()


def test_every_captured_product_reaches_a_published_row():
    """No row is lost between the portal and the desk — free items included.

    A third of this catalogue costs nothing. Dropping those would lose stock we
    hold, count and dispense, purely because IDEXX does not invoice for it.
    """
    # deliberately re-reads the fixture rather than trusting the fixture count
    source = {row["material"] for row in _snapshot_rows()}

    assert source, "the fixture is empty"


def test_no_product_is_lost_on_the_way_to_the_desk(published):
    source = {row["material"] for row in _snapshot_rows()}

    assert set(published) == source


def test_the_published_price_is_the_price_the_portal_quoted_us(published):
    """Every published cost traces to our own account's figure, to the cent.

    The connector reads one number per row and nothing downstream may adjust
    it. A supplier price that drifts on its way to the desk is the one defect
    that reaches a purchase order.
    """
    source = {row["material"]: row for row in _snapshot_rows()}

    for code, row in published.items():
        want = Decimal(source[code]["price_hkd"])
        got = Decimal(row["catalogue_price_hkd"].replace("$", "").replace(",", ""))
        assert got == want, f"{code}: portal said {want}, desk published {got}"


def test_a_free_item_publishes_at_zero_rather_than_being_held(published):
    """IDEXX has stated this price. A row held for a "missing" price would send
    someone to chase an answer the supplier already gave."""
    free = [row["material"] for row in _snapshot_rows() if row["is_free_item"] == "TRUE"]

    assert free, "the fixture no longer covers free items"
    for material in free:
        published_price = published[material]["catalogue_price_hkd"]
        assert Decimal(published_price.replace("$", "").replace(",", "")) == 0, material


def test_the_pack_the_page_stated_survives_to_the_desk(published):
    """"12 tests per item" is the difference between a $4,766 box and a $4,766
    test. If the count does not survive, every per-unit margin on this supplier
    is wrong by whatever the box holds."""
    source = {row["material"]: row for row in _snapshot_rows()}
    checked = 0

    for code, row in published.items():
        stated = source[code]["units_per_item"]
        if not stated or stated == "1":
            continue
        serialized = str(row)
        assert stated in serialized, f"{code}: the page said {stated} per item; the desk lost it"
        checked += 1

    assert checked >= 50, f"only {checked} multi-unit packs checked — the fixture thinned out"


def test_the_brand_is_idexx_even_though_avm_invoices_it(published):
    """AVM is who we pay; IDEXX is what is on the box and what a clinician asks
    for. A row filed under the distributor's name is a row nobody can find."""
    assert all(row.get("brand") == "IDEXX" for row in published.values())

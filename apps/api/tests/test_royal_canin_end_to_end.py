"""Royal Canin from webshop snapshot to published row, with no provider involved.

The other suppliers' end-to-end coverage replays recorded vision envelopes,
because their catalogues arrive as PDFs someone has to read. Royal Canin's does
not: the connector writes the snapshot itself, so this path is deterministic all
the way through and a real regression here would be arithmetic, not perception.

The fixtures are REAL snapshots — captured from the live shop on 2026-08-28 with
the clinic's own account, trimmed to the products BizOps put on the sample
sheet. Re-capture them with `services.royal_canin_connector.build_snapshot`
rather than editing them by hand; they are a reading of the shop, not an opinion.

What this pins is what the pipeline actually does with them today, including
where it comes up short — see `test_the_pack_breakdown_is_not_resolved_yet`.
A gap that is asserted is a gap somebody can close; a gap that is merely absent
is one the suite quietly blesses.
"""

from __future__ import annotations

import csv
import os
import tempfile
from io import BytesIO
from pathlib import Path
from uuid import UUID

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/rc_e2e.db")
os.environ.setdefault("PREFECT_API_MODE", "offline")
os.environ.setdefault("PREFECT_LOGGING_LEVEL", "ERROR")
os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
os.environ.setdefault("PREFECT_SERVER_ANALYTICS_ENABLED", "false")

import pytest  # noqa: E402

import database  # noqa: E402
import models  # noqa: E402
from orchestration.catalogue_flows import catalogue_ingestion_flow  # noqa: E402
from services import catalogue_evidence_extraction as extraction  # noqa: E402
from services import royal_canin_ingestion as ingestion  # noqa: E402
from services.catalogue_golden_export import golden_rows  # noqa: E402
from services.catalogue_submission import (  # noqa: E402
    CatalogueSubmissionCommand,
    CatalogueSubmissionService,
)
from test_catalogue_golden_suppliers import _take_through_review  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "royal_canin"
RANGES = ("vet", "non_vet")


def _snapshot_rows(product_range: str) -> list[dict[str, str]]:
    path = FIXTURES / f"{product_range}_webshop_snapshot.csv"
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _publish(db, product_range: str) -> dict[str, dict[str, str]]:
    """One supplier's snapshot, all the way to published rows."""
    spec = ingestion.SUPPLIERS[product_range]
    path = FIXTURES / f"{product_range}_webshop_snapshot.csv"
    submitted = CatalogueSubmissionService(
        db, upload_root=os.environ["CATALOGUE_UPLOAD_DIR"]
    ).submit(CatalogueSubmissionCommand(
        supplier_id=spec["supplier_id"],
        original_filename=path.name,
        content_type="text/csv",
        stream=BytesIO(path.read_bytes()),
        contract_id=spec["contract_id"],
        contract_version=ingestion.CONTRACT_VERSION,
        submitted_by="e2e",
    ))
    catalogue_ingestion_flow(ingestion_run_id=submitted.ingestion_run_id)

    refused: dict[str, str] = {}
    _take_through_review(db, str(submitted.ingestion_run_id), refused=refused)
    assert not refused, f"{product_range}: the pipeline refused to publish {refused}"
    published = {row["supplier_product_code"]: row
                 for row in golden_rows(db, UUID(str(submitted.ingestion_run_id)))}
    assert published, f"{product_range}: nothing was published"
    return published


@pytest.fixture(scope="module")
def published(tmp_path_factory) -> dict[str, dict[str, dict[str, str]]]:
    """Both suppliers, run through the pipeline ONCE for the whole module.

    Publication versions are unique per product, so re-running a range inside
    one database collides on the second pass — and the flow is expensive enough
    that repeating it per assertion would be its own reason not to.
    """
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    patch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path_factory.mktemp("rc_uploads")))
    # Any vision call at all would mean the snapshot was not read
    # deterministically — this source has no pages and nothing to perceive.
    def _refuse(*args, **kwargs):
        raise AssertionError("a CSV snapshot must never reach a vision provider")

    patch.setattr(extraction, "_call_vision", _refuse)

    session = database.SessionLocal()
    try:
        for spec in ingestion.SUPPLIERS.values():
            if session.get(models.Supplier, spec["supplier_id"]) is None:
                session.add(models.Supplier(
                    id=spec["supplier_id"], name=spec["label"], code=f"RC{spec['supplier_id']}",
                    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
                ))
        session.commit()
        yield {rng: _publish(session, rng) for rng in RANGES}
    finally:
        session.close()
        patch.undo()


@pytest.mark.parametrize("product_range", RANGES)
def test_every_snapshot_row_reaches_a_published_row(published, product_range):
    """No row is lost between the shop and the desk, and none needed a provider."""
    source = _snapshot_rows(product_range)

    assert set(published[product_range]) == {row["original_sku"] for row in source}


@pytest.mark.parametrize("product_range", RANGES)
def test_the_published_price_is_the_price_the_shop_quoted_us(published, product_range):
    """Every published cost traces to our customer group's tier, to the cent.

    The connector reads one number per row and nothing downstream may adjust
    it. A supplier price that drifts on its way to the desk is the one defect
    that reaches a purchase order.
    """
    source = {row["original_sku"]: row for row in _snapshot_rows(product_range)}

    for code, row in published[product_range].items():
        want = source[code]["price_hkd"]
        got = row["catalogue_price_hkd"].replace("$", "").replace(",", "")
        assert float(got) == float(want), f"{code}: published {got}, shop said {want}"


def test_the_rows_own_unit_decides_the_published_basis(published):
    """UNIT prices one item; INNER BOX prices a case — the whole commercial point.

    Same price column, two meanings, and the difference between them is
    $105 for a pouch and $105 for twelve. Pinned at the PUBLISHED end, not
    just at conformance, because that is where a wrong basis becomes a margin.
    """
    source = {row["original_sku"]: row for row in _snapshot_rows("vet")}

    seen = set()
    for code, row in published["vet"].items():
        basis = row["catalogue_price_basis_uom"].strip().upper()
        nav = source[code]["nav_uom"].strip().upper()
        seen.add(nav)
        if nav == "INNER BOX":
            assert basis == "CASE", f"{code}: a case priced as {basis!r}"
        elif nav == "UNIT":
            assert basis != "CASE", f"{code}: a single item priced as a case"
        assert row["catalogue_price_basis_qty"] == "1"
    # The fixture must keep covering both, or this test proves only one of them.
    assert {"UNIT", "INNER BOX"} <= seen


@pytest.mark.parametrize("product_range", RANGES)
def test_the_pack_breakdown_is_not_resolved_yet(published, product_range):
    """A KNOWN GAP, asserted so it cannot be forgotten (DEV-212).

    Royal Canin prints the case contents in the product's own name — "CAN
    410GX12" is twelve cans — but nothing reads it: the contract's packaging
    rules say so in prose and no declared field carries the count. So every
    case row publishes a case price with no idea how many sellable units are
    in the case, and no per-unit cost can be derived from it.

    This test passes TODAY and is meant to fail the moment that is fixed. When
    it does, invert it: assert the count is 12 rather than absent.
    """
    rows = published[product_range]
    unresolved = [code for code, row in rows.items()
                  if not row["sellable_units_per_price_basis"].strip()]
    assert len(unresolved) == len(rows), (
        "the pack breakdown now resolves for some rows — good. Invert this test: "
        f"rows still unresolved {sorted(unresolved)}"
    )
    assert all(not row["package_configuration"].strip() for row in rows.values())

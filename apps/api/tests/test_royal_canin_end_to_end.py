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
def test_a_case_publishes_the_count_royal_canin_printed(published, product_range):
    """"CAN 410GX12" is twelve cans, and the pipeline now says so.

    Royal Canin prices wet food by the case and dry food by the bag. Until the
    printed count was read, a case published with no idea what was in it — so
    a $123.60 case stood in as the cost of one $11.00 pouch and the margin read
    -1023%. The count comes from Royal Canin's own name, never from the legacy
    units_per_pack column, which on other suppliers carries the ORDER MULTIPLE
    and would relocate DEV-211 rather than fix this.
    """
    source = {row["original_sku"]: row for row in _snapshot_rows(product_range)}
    rows = published[product_range]

    for code, row in rows.items():
        stated = source[code]["pack_breakdown"]
        if not stated:
            continue
        count = stated.split("/")[0].strip()
        assert row["sellable_units_per_price_basis"] == count, f"{code}: lost the printed count"
        assert row["package_configuration"] == f"{count} UNIT / CASE"


def test_a_case_cost_is_the_case_price_divided_by_what_it_holds(published):
    """The number every margin runs on, checked at the end of the real path.

    ``get_unit_cost`` is documented as the cost of ONE SELL-UNIT and is the
    single figure margins divide by. For a case-priced row that means the case
    price over the printed count — the exact arithmetic that was missing.
    """
    from services import offering_costs

    session = database.SessionLocal()
    try:
        source = {row["original_sku"]: row for row in _snapshot_rows("vet")}
        offerings = {
            offering.supplier_sku: offering
            for offering in session.query(models.SupplierOffering).filter_by(supplier_id=40).all()
        }
        per_unit = offering_costs._session_map(session)

        checked = 0
        for sku, offering in offerings.items():
            row = source.get(sku)
            if row is None or offering.product_variant_id is None:
                continue
            cost = per_unit.get((offering.supplier_id, offering.product_variant_id))
            if cost is None:
                continue
            case_price = float(row["price_hkd"])
            if row["pack_breakdown"]:
                count = int(row["pack_breakdown"].split("/")[0].strip())
                assert cost == pytest.approx(case_price / count, rel=1e-6), (
                    f"{sku}: {case_price} over {count} should be {case_price / count}, got {cost}"
                )
                assert count > 1
            else:
                # Nothing to divide by: a bag is already one sellable unit.
                assert cost == pytest.approx(case_price, rel=1e-6), f"{sku}: a unit price was altered"
            checked += 1
        assert checked, "no Royal Canin offering carried a cost to check"
    finally:
        session.close()


@pytest.mark.parametrize("product_range", RANGES)
def test_a_pack_royal_canin_does_not_spell_out_stays_unresolved(published, product_range):
    """The refusal half, which is the reason the rest can be trusted.

    Three products print no count at all, and two print "85GX3X4" — three
    sachets to a sleeve, four sleeves to a case, without saying which one we
    sell. Those publish no count rather than a guess, and the contract's
    declared ambiguity is what a reviewer sees instead.
    """
    source = {row["original_sku"]: row for row in _snapshot_rows(product_range)}
    rows = published[product_range]

    for code, row in rows.items():
        if source[code]["pack_breakdown"]:
            continue
        assert not row["sellable_units_per_price_basis"].strip(), (
            f"{code}: a count was invented for a pack Royal Canin never printed"
        )

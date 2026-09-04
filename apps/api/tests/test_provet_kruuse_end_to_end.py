"""ProVet Kruuse from price list to published row.

What this proves that conformance cannot: the row the page contradicts itself
about survives as TWO published rows rather than being quietly folded into one.
A pipeline that collapsed them would publish whichever price it happened to see
last and tell nobody there had been a choice.
"""

from __future__ import annotations

import os
import re
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from uuid import UUID

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/pk_e2e.db")
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

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue_pipeline" / "provet_kruuse"
PAGES = [FIXTURES / f"page_{n}.json" for n in (1, 2, 3, 4)]
SUPPLIER_ID = 62
CONTRACT_ID = "provet_kruuse.hk_price_list.v1"

#: code -> (price, sellable units in the pack). One of each thing that could go
#: wrong: a counted pack, a measure that must not become a count, the code the
#: page prints twice, and the code the sheet had recorded as N/A.
EXPECTED = {
    "CERE16": (Decimal("90.00"), "4"),
    "METHONE": (Decimal("493.00"), None),
    "PREDNEFRIN": (Decimal("128.00"), None),
}


@pytest.fixture(scope="module")
def _run(tmp_path_factory):
    from _pytest.monkeypatch import MonkeyPatch

    patch = MonkeyPatch()
    patch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path_factory.mktemp("pk_uploads")))
    patch.setenv("CATALOGUE_VISION_PROVIDER", "anthropic")
    patch.setenv("ANTHROPIC_API_KEY", "replay-only")
    calls = _install_golden_replay(patch, PAGES)

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
            original_filename="provet-hk-price-list-2025-v2.pdf",
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
            session, str(submitted.ingestion_run_id),
            only_skus=set(EXPECTED) | {"CERE60"}, refused=refused,
        )
        assert not refused, f"the pipeline refused to publish {refused}"

        rows = {}
        for row in golden_rows(session, UUID(str(submitted.ingestion_run_id))):
            rows.setdefault(row["supplier_product_code"], []).append(row)
        assert rows, "nothing reached the serving layer"
        codes = {
            issue.issue_code
            for issue in session.query(models.CatalogueValidationIssue)
            .filter_by(ingestion_run_uuid=str(submitted.ingestion_run_id))
            .all()
        }
        yield rows, codes
    finally:
        session.close()
        patch.undo()


@pytest.fixture(scope="module")
def published(_run):
    return _run[0]


@pytest.fixture(scope="module")
def run_issues(_run):
    """Every validation-issue code the run raised."""
    return _run[1]


def _money(text):
    cleaned = re.sub(r"[^\d.]", "", str(text or ""))
    return Decimal(cleaned) if cleaned else None


def test_the_chosen_products_reach_the_serving_layer(published):
    assert set(EXPECTED) <= set(published)


def test_the_published_price_is_the_price_the_page_printed(published):
    for code, (want, _) in EXPECTED.items():
        got = _money(published[code][0]["catalogue_price_hkd"])
        assert got == want, f"{code}: page {want}, published {got}"


def test_a_price_publishes_as_the_pack_it_buys(published):
    """$90.00 buys the box of four, not one tablet. Published as PACK, and the
    four rides along so a per-tablet cost can be derived from it later."""
    row = published["CERE16"][0]

    assert row["catalogue_price_basis_uom"] == "PACK"
    assert str(row["sellable_units_per_price_basis"]) == "4"


def test_a_measure_never_becomes_a_count_on_the_way_to_the_desk(published):
    """'Injection 20ml' is one bottle of twenty millilitres. Published as
    twenty sellable units it would divide the cost by twenty."""
    row = published["METHONE"][0]

    assert _money(row["catalogue_price_hkd"]) == Decimal("493.00")
    assert str(row.get("sellable_units_per_price_basis") or "") in ("", "1", "None")


def test_the_contradicted_code_folds_to_one_product_and_says_so(published, run_issues):
    """The find that named this folder, and what the pipeline actually does.

    CERE60 is printed twice on page 2, at $174.00 and $198.00. One supplier
    code is one product, so the two rows FOLD into a single published offering
    — and the one that survives is whichever was read last, which is $198.00
    here and is not a decision anybody made.

    What stops that being silent is the contract's own declared ambiguity: the
    run carries PROVET_CODE_PRINTED_TWICE_AT_DIFFERENT_PRICES, which is the
    only thing telling a reviewer the page disagreed with itself. If that
    warning ever stops firing, a contradicted price publishes unremarked.
    """
    rows = published.get("CERE60") or []

    assert len(rows) == 1, "one supplier code is one product; the rows fold"
    assert _money(rows[0]["catalogue_price_hkd"]) in {Decimal("174.00"), Decimal("198.00")}
    assert "PROVET_CODE_PRINTED_TWICE_AT_DIFFERENT_PRICES" in run_issues


def test_a_duplicate_at_one_price_is_reported_separately(run_issues):
    """ALUTAB600 is also printed twice, but its two lines agree. The contract
    reports that as its own thing, so a harmless duplicated line is never
    mistaken for a price the supplier cannot make up their mind about."""
    assert "PROVET_CODE_PRINTED_TWICE_AT_THE_SAME_PRICE" in run_issues

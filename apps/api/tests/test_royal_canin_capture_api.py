"""The Royal Canin button, as the screen behind it actually sees it.

Two things the endpoint must never get wrong, both of which it once did:

* a capture that queued ONE supplier and held the other must say so — the
  submission has already committed, and a blanket "nothing was submitted"
  sends a reviewer away from work that is sitting on the board;
* a connector failure must arrive as the connector wrote it. Those messages
  name the key to re-read and the group to re-verify; replaced by a generic
  "Catalogue submission failed" they tell the person holding the button
  nothing they can act on.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/rc_api.db")

import pytest  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402
from dependencies import require_user  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services import royal_canin_connector as connector  # noqa: E402
from services import royal_canin_ingestion as ingestion  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


class _Admin:
    id = 601
    username = "rc-admin"
    display_name = "RC Admin"
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
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("CATALOGUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    session = database.SessionLocal()
    try:
        for spec in ingestion.SUPPLIERS.values():
            if session.get(models.Supplier, spec["supplier_id"]) is None:
                session.add(models.Supplier(
                    id=spec["supplier_id"], name=spec["label"], code=f"RC{spec['supplier_id']}",
                    created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
                ))
        session.commit()
    finally:
        session.close()
    return TestClient(main.app)


def _snapshot(rows: int, product_range: str, *, tag: str, price: int = 105):
    hits = [
        {
            "original_sku": f"{tag}-{product_range}-{index}",
            "sku": f"HK_{tag}{index}",
            "name": f"FHN CAT AGEING {index}",
            "ean_code": f"900357905{index:04d}",
            "nav_uom": "UNIT",
            "navision_weight": 2,
            "customer_groups_ids": ["879"],
            "categories": {"level0": ["VET DOG"] if product_range == connector.RANGE_VET
                                     else ["PET SHOP DOG"]},
            "stock_configuration": {"min_sale_qty": 1, "qty_increments": 0},
            "price": {"HKD": {"group_879_tier": price}},
        }
        for index in range(rows)
    ]
    return connector.build_snapshot(hits, customer_group="879", captured_on="2026-08-28",
                                    product_range=product_range)


def _both(vet_rows: int, retail_rows: int, *, tag: str, price: int = 105):
    return lambda **kw: {
        connector.RANGE_VET: _snapshot(vet_rows, connector.RANGE_VET, tag=tag, price=price),
        connector.RANGE_NON_VET: _snapshot(retail_rows, connector.RANGE_NON_VET, tag=tag),
    }


def test_one_supplier_queued_and_one_held_is_reported_as_both(client, monkeypatch):
    monkeypatch.setattr(ingestion.connector, "capture_snapshots", _both(40, 40, tag="api-split"))
    first = client.post("/catalogues/connectors/royal-canin/capture", json={})
    assert first.status_code == 202, first.text

    # Vet prices moved; retail came back a quarter of its size.
    monkeypatch.setattr(ingestion.connector, "capture_snapshots",
                        _both(40, 3, tag="api-split", price=222))
    response = client.post("/catalogues/connectors/royal-canin/capture", json={})

    assert response.status_code == 202, response.text
    body = response.json()
    by_range = {result["product_range"]: result for result in body["results"]}
    assert by_range["vet"]["status"] == "submitted"
    assert by_range["vet"]["ingestion_run_id"]
    assert by_range["non_vet"]["status"] == "refused"
    assert by_range["non_vet"]["releasable"] is True
    # The run really is queued, so the top-line message must not deny it.
    assert "Queued 40 products" in body["message"]
    assert "came back short" in body["message"]
    # And the row count is what reached the desk, not what was read.
    assert body["rows"] == 40


def test_a_wholly_refused_capture_still_says_what_the_other_supplier_found(client, monkeypatch):
    monkeypatch.setattr(ingestion.connector, "capture_snapshots", _both(40, 40, tag="api-all"))
    assert client.post("/catalogues/connectors/royal-canin/capture", json={}).status_code == 202

    # Retail shrinks; vet is byte-identical to last time.
    monkeypatch.setattr(ingestion.connector, "capture_snapshots", _both(40, 3, tag="api-all"))
    response = client.post("/catalogues/connectors/royal-canin/capture", json={})

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "ROYAL_CANIN_CAPTURE_INCOMPLETE"
    by_range = {result["product_range"]: result for result in detail["results"]}
    # "Unchanged" is an answer. Losing it to the exception would leave a person
    # unable to tell a supplier that was fine from one nobody looked at.
    assert by_range["vet"]["status"] == "unchanged"
    assert by_range["non_vet"]["status"] == "refused"


def test_a_connector_failure_reaches_the_person_holding_the_button(client, monkeypatch):
    def _rotated_key(**kwargs):
        raise connector.RoyalCaninConnectorError(
            "Royal Canin's search index refused the request (403). The public search key "
            "may have been rotated — re-read it from the shop and update "
            "ROYAL_CANIN_ALGOLIA_SEARCH_KEY."
        )

    monkeypatch.setattr(ingestion.connector, "capture_snapshots", _rotated_key)
    response = client.post("/catalogues/connectors/royal-canin/capture", json={})

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["code"] == "ROYAL_CANIN_SOURCE_UNAVAILABLE"
    assert "ROYAL_CANIN_ALGOLIA_SEARCH_KEY" in detail["message"]


def test_warnings_arrive_coded_so_the_screen_can_tell_them_apart(client, monkeypatch):
    """An unpriced product is not a filing question, and must not read as one."""
    def _one_unpriced(**kwargs):
        snapshot = _snapshot(2, connector.RANGE_VET, tag="api-warn")
        hits = [
            {
                "original_sku": "UNPRICED-1", "sku": "HK_U1", "name": "VHN DOG RENAL 2KG",
                "ean_code": "9003579050163", "nav_uom": "UNIT", "navision_weight": 2,
                "customer_groups_ids": ["879"], "categories": {"level0": ["VET DOG"]},
                "stock_configuration": {"min_sale_qty": 1, "qty_increments": 0},
                "price": {"HKD": {"default": connector.NO_PRICE_SENTINEL}},
            }
        ]
        return {
            connector.RANGE_VET: connector.build_snapshot(
                hits, customer_group="879", captured_on="2026-08-28",
                product_range=connector.RANGE_VET,
            ),
            connector.RANGE_NON_VET: snapshot,
        }

    monkeypatch.setattr(ingestion.connector, "capture_snapshots", _one_unpriced)
    response = client.post("/catalogues/connectors/royal-canin/capture", json={})

    assert response.status_code == 202, response.text
    warnings = [w for result in response.json()["results"] for w in result["warnings"]]
    assert [w["code"] for w in warnings] == [connector.WARN_NO_PRICE]
    assert "no price for customer group 879" in warnings[0]["message"]

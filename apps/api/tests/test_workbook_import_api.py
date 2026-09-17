"""The two upload endpoints, as the screen behind them sees them.

Separate from the service tests because what these pin is the boundary: that
the products sheet and the deals sheet reach different endpoints, that a
preview is a preview all the way through the HTTP layer, and that uploading a
workbook to the wrong one is refused with a reason rather than half-applied.
"""

from __future__ import annotations

import io
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/workbook_api.db")

import pytest  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402
from dependencies import require_user  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services import upload_workbook  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)

NOW = "2026-09-11T00:00:00"


class _Admin:
    id = 902
    username = "workbook-admin"
    display_name = "Workbook Admin"
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
def client():
    session = database.SessionLocal()
    for model in (models.MbbTerm, models.ProductSupplier, models.ProductVariant,
                  models.Supplier):
        session.query(model).delete()
    session.add(models.Supplier(code="ALFA", name="Alfamedic",
                                normalized_name="alfamedic", created_at=NOW))
    session.commit()
    session.close()
    with TestClient(main.app) as c:
        yield c


def _csv(*lines: str) -> tuple[str, io.BytesIO, str]:
    body = "\n".join(lines).encode()
    return ("sheet.csv", io.BytesIO(body), "text/csv")


def test_products_csv_previews_then_applies(client):
    file = lambda: _csv(  # noqa: E731
        "sku,name,category,unit,buy.supplier,buy.sku,buy.pack,buy.per_pack,buy.cost,buy.cost_per",
        ",Endpoint Product,Medicine,Unit(s),Alfamedic,EP-1,Box(es),10,90,pack",
    )
    preview = client.post("/products/import-workbook?dry_run=true", files={"file": file()})
    assert preview.status_code == 200, preview.text
    assert preview.json()["summary"]["created"] == 1
    assert preview.json()["dry_run"] is True

    session = database.SessionLocal()
    assert session.query(models.ProductVariant).count() == 0     # preview wrote nothing
    session.close()

    applied = client.post("/products/import-workbook", files={"file": file()})
    assert applied.status_code == 200, applied.text
    assert applied.json()["summary"]["created"] == 1
    session = database.SessionLocal()
    assert session.query(models.ProductVariant).count() == 1
    session.close()


def test_deals_go_to_their_own_endpoint(client):
    made = client.post("/products/import-workbook", files={"file": _csv(
        "sku,name,category,unit,buy.supplier,buy.sku,buy.pack,buy.per_pack,buy.cost,buy.cost_per",
        ",Dealt Product,Others,Unit(s),Alfamedic,DP-1,Box(es),10,90,pack",
    )})
    assert made.json()["summary"]["created"] == 1, made.text
    sku = made.json()["rows"][0]["sku"]

    deal_file = lambda: _csv(  # noqa: E731
        "sku,supplier,kind,min_qty,cost,cost_per",
        f"{sku},Alfamedic,tier,6,77,unit",
    )
    deals = client.post("/products/import-deals", files={"file": deal_file()})
    assert deals.status_code == 200, deals.text
    assert deals.json()["summary"]["created"] == 1

    # add-only: the same file again adds nothing
    again = client.post("/products/import-deals", files={"file": deal_file()})
    assert again.json()["summary"] == {"total": 1, "created": 0, "updated": 0,
                                       "duplicate": 1, "errors": 0}


def test_the_workbook_itself_uploads_to_both(client, tmp_path):
    """One .xlsx, two uploads — which is the whole shape of the workflow."""
    path = tmp_path / "book.xlsx"
    upload_workbook.build(str(path), with_data=False)
    payload = path.read_bytes()
    for endpoint in ("import-workbook", "import-deals"):
        response = client.post(
            f"/products/{endpoint}?dry_run=true",
            files={"file": ("book.xlsx", io.BytesIO(payload),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
        assert response.status_code == 200, response.text
        assert response.json()["summary"]["total"] == 0      # a template carries no rows


def test_a_file_with_the_wrong_columns_is_refused(client):
    response = client.post("/products/import-deals", files={"file": _csv(
        "colour,size", "red,large")})
    assert response.status_code == 400
    assert "deals columns" in response.json()["detail"]


def test_a_file_that_is_not_a_workbook_is_refused_not_a_500(client):
    """An .xlsx is a zip, and people upload the wrong file.

    A truncated upload, a .xls renamed, a CSV renamed, a protected book — each
    arrives here as a different exception from deep inside openpyxl. Unhandled
    they are all a 500 and a stack trace, which tells whoever picked the wrong
    file nothing about which file to pick instead.
    """
    response = client.post("/products/import-workbook", files={
        "file": ("book.xlsx", io.BytesIO(b"this is not a zip"),
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 400
    assert "could not be opened as a workbook" in response.json()["detail"]


def test_an_empty_upload_is_refused_not_a_500(client):
    response = client.post("/products/import-deals", files={
        "file": ("empty.xlsx", io.BytesIO(b""),
                 "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert response.status_code == 400

"""The IDEXX button, as the screen behind it actually sees it.

No browser runs here — the connector is replaced by a snapshot, because what
this pins is the endpoint's judgement, not the portal's HTML. Three things it
must not get wrong:

* a short read must queue NOTHING and say why, since a walk that stopped
  halfway and a supplier who dropped half their range look identical from here;
* a re-read of an unchanged catalogue must queue nothing at all, or every
  press buries the review board in identical runs;
* a connector failure must arrive as the connector wrote it, with the password
  nowhere in it.
"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/idexx_api.db")

import pytest  # noqa: E402

import database  # noqa: E402
import main  # noqa: E402
import models  # noqa: E402
from dependencies import require_user  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from services import idexx_connector as connector  # noqa: E402
from services import idexx_ingestion as ingestion  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


class _Admin:
    id = 731
    username = "idexx-admin"
    display_name = "IDEXX Admin"
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
        if session.get(models.Supplier, ingestion.SUPPLIER_ID) is None:
            session.add(models.Supplier(
                id=ingestion.SUPPLIER_ID, name="Asia Vet Medical Limited", code="AVM",
                created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00",
            ))
            session.commit()
    finally:
        session.close()
    return TestClient(main.app)


def _snapshot(rows: int, *, tag: str, price: int = 1034):
    """A snapshot of `rows` products, distinct per `tag` so checksums differ."""
    blocks = [
        (f"Product {tag}-{n}\nProduct: 99-{tag}-{n:04d}\n5 tests per item\n"
         f"HKD {price + n}.00 *\nAdd to cart", "Rapid tests", "https://order.idexx.com/x")
        for n in range(rows)
    ]
    return connector.build_snapshot(blocks, captured_on="2026-09-02", pages_read=3)


def _serve(monkeypatch, snapshot):
    monkeypatch.setattr(connector, "capture", lambda **kwargs: snapshot)


def test_a_changed_catalogue_is_queued(client, monkeypatch):
    _serve(monkeypatch, _snapshot(40, tag="a"))

    res = client.post("/catalogues/connectors/idexx/capture", json={})

    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "submitted"
    assert body["rows"] == 40
    assert body["results"][0]["ingestion_run_id"]
    assert body["results"][0]["supplier"] == ingestion.SUPPLIER_LABEL


def test_re_reading_an_unchanged_catalogue_queues_nothing(client, monkeypatch):
    """Pressing again is not new work. A run per press would bury the board."""
    _serve(monkeypatch, _snapshot(40, tag="b"))
    first = client.post("/catalogues/connectors/idexx/capture", json={}).json()
    assert first["status"] == "submitted"

    again = client.post("/catalogues/connectors/idexx/capture", json={}).json()

    assert again["status"] == "unchanged"
    assert again["rows"] == 0
    assert again["results"][0]["ingestion_run_id"] is None


def test_a_short_read_queues_nothing_and_says_why(client, monkeypatch):
    """The refusal is the whole point: half a catalogue must not be allowed to
    read as IDEXX discontinuing half their range."""
    _serve(monkeypatch, _snapshot(40, tag="c"))
    assert client.post("/catalogues/connectors/idexx/capture", json={}).json()["status"] == "submitted"

    _serve(monkeypatch, _snapshot(9, tag="d"))
    res = client.post("/catalogues/connectors/idexx/capture", json={})

    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "IDEXX_CAPTURE_INCOMPLETE"
    assert "short" in res.json()["detail"]["message"].lower()


def test_a_short_read_can_be_released_deliberately(client, monkeypatch):
    """When the range really did shrink, a person says so with a second press."""
    _serve(monkeypatch, _snapshot(40, tag="e"))
    client.post("/catalogues/connectors/idexx/capture", json={})

    _serve(monkeypatch, _snapshot(9, tag="f"))
    res = client.post("/catalogues/connectors/idexx/capture", json={"force_incomplete": True})

    assert res.status_code == 202
    assert res.json()["status"] == "submitted"
    assert res.json()["rows"] == 9


def test_a_connector_failure_arrives_as_the_connector_wrote_it(client, monkeypatch):
    """These messages name what a person can do about it. Flattened into
    'Catalogue submission failed' they tell whoever pressed the button nothing."""
    def _fail(**kwargs):
        raise connector.IdexxConnectorError(
            "IDEXX asked for something other than a password after the email step."
        )
    monkeypatch.setattr(connector, "capture", _fail)

    res = client.post("/catalogues/connectors/idexx/capture", json={})

    assert res.status_code == 502
    detail = res.json()["detail"]
    assert detail["code"] == "IDEXX_SOURCE_UNAVAILABLE"
    assert "password after the email step" in detail["message"]

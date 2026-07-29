"""Supplier-source contract ingestion wiring.

Extraction is monkeypatched, but contract selection/enforcement uses the real
Pydantic-backed supplier-source runtime adapter.
"""

import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

import pytest        # noqa: E402
import database      # noqa: E402
import models        # noqa: E402
import main          # noqa: E402
from fastapi.testclient import TestClient          # noqa: E402
from dependencies import require_user               # noqa: E402
from services import tagging_service  # noqa: E402

models.Base.metadata.create_all(bind=database.engine)
database.seed_category_rules(database.engine)


class _Admin:
    id, username, display_name, role = 9, "onboarder", "On Boarder", "admin"


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    prev = main.app.dependency_overrides.get(require_user)
    main.app.dependency_overrides[require_user] = lambda: _Admin()
    monkeypatch.setattr(
        tagging_service,
        "suggest_tags",
        lambda items: [{"tags": [], "category": None, "subcategory": None} for _ in items],
    )
    yield
    if prev is None:
        main.app.dependency_overrides.pop(require_user, None)
    else:
        main.app.dependency_overrides[require_user] = prev


_client = TestClient(main.app)


def test_v1_import_endpoint_is_removed_with_410_tombstone():
    r = _client.post(
        "/catalogues/import",
        data={"supplier_id": "14"},
        files={"file": ("hills.pdf", b"%PDF-1.4 fake", "application/pdf")},
    )
    assert r.status_code == 410
    detail = r.json()["detail"]
    assert detail["code"] == "ENDPOINT_REMOVED"
    assert "/catalogues/ingestions" in detail["message"]

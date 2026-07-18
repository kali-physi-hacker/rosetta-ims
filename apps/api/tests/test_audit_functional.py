"""End-to-end: hit real endpoints against a temp DB, assert attributed audit_log rows land.

Proves the request -> audit chain for representative routers, including the two cases that
motivated this work: a competitor delete (must snapshot the removed row) and a GP-floor change
(must record a before/after diff). Runnable directly (`python tests/test_audit_functional.py`)
or under pytest.
"""
import os
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")

import database  # noqa: E402
import models    # noqa: E402
import main       # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from dependencies import require_user       # noqa: E402

models.Base.metadata.create_all(bind=database.engine)


class _FakeAdmin:
    id, username, display_name, role = 7, "auditor", "Aud Itor", "admin"


main.app.dependency_overrides[require_user] = lambda: _FakeAdmin()
_client = TestClient(main.app)


def _audit_rows():
    d = database.SessionLocal()
    try:
        return [(r.action, r.actor_username, r.entity_type, r.entity_label, r.details)
                for r in d.query(models.AuditLog).order_by(models.AuditLog.id).all()]
    finally:
        d.close()


def _seed_competitor():
    d = database.SessionLocal()
    try:
        p = models.Product(sku_code="TEST-1", name="Test Widget", category="Medicine",
                           status="ACTIVE", storage_rule="any",
                           created_at="2026-01-01T00:00:00", updated_at="2026-01-01T00:00:00")
        d.add(p); d.flush()
        cp = models.CompetitorPrice(product_id=p.id, competitor_name="ePet. hk",
                                    url="https://www.epet.hk/x", created_at="2026-05-16T00:00:00",
                                    updated_at="2026-05-16T00:00:00")
        d.add(cp); d.commit()
        return cp.id
    finally:
        d.close()


def test_mutating_endpoints_write_attributed_audit_rows():
    cp_id = _seed_competitor()

    assert _client.post("/category-rules",
                        json={"category": "TestCat", "gp_floor": 0.4, "sku_digit": "9"}).status_code == 200
    assert _client.patch("/category-rules/TestCat", json={"gp_floor": 0.55}).status_code == 200
    assert _client.post("/collections", json={"name": "Test Coll", "rule": {
        "match": "all", "conditions": [{"field": "tag", "op": "has", "value": "test"}]}}).status_code == 200
    assert _client.delete(f"/competitors/{cp_id}").status_code == 200

    rows = _audit_rows()
    actions = {r[0] for r in rows}
    assert {"category.create", "category.update", "collection.create", "competitor.delete"} <= actions

    # every row is attributed to the acting user (snapshot survives rename/deactivation)
    assert all(r[1] == "auditor" for r in rows)
    # the GP-floor change carries a before/after diff
    upd = next(r for r in rows if r[0] == "category.update")
    assert upd[4] and "0.4" in upd[4] and "0.55" in upd[4], f"no gp_floor diff: {upd[4]}"
    # the competitor delete snapshots the identity that would otherwise be lost (the ePet case)
    dele = next(r for r in rows if r[0] == "competitor.delete")
    assert dele[4] and "ePet. hk" in dele[4], f"delete didn't snapshot the row: {dele[4]}"


if __name__ == "__main__":
    test_mutating_endpoints_write_attributed_audit_rows()
    for r in _audit_rows():
        print(f"  action={r[0]:24} actor={r[1]} entity={r[2]}/{r[3]}")
    print("\n✅ endpoints wrote attributed audit rows; diffs + deleted-row snapshots present")

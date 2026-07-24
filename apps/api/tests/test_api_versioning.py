"""Unified API surface tests.

One canonical, unversioned surface; /v1 and /v2 remain only as hidden,
deprecated aliases of the same routers until the frontend cutover.
"""
import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_ROOT)

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_client = TestClient(main.app)


def test_canonical_schema_exposes_legacy_and_pipeline_surface_unversioned():
    schema = _client.get("/openapi.json").json()
    paths = schema["paths"]

    # Legacy domains remain first-class.
    assert "/auth/login" in paths
    assert "/products" in paths
    assert "/products/{sku}" in paths
    assert "/suppliers" in paths
    assert "/pricing" in paths
    assert any(path.startswith("/audit") for path in paths)
    # The evidence-first catalogue ingestion boundary is part of the same surface.
    assert "/catalogues/ingestions" in paths
    assert "/catalogues/ingestions/{run_uuid}" in paths
    # No version prefixes anywhere in the canonical contract.
    assert not any(path.startswith(("/v1", "/v2")) for path in paths)


def test_health_and_index_are_unversioned():
    assert _client.get("/health").json() == {"status": "ok", "version": "1.0.0"}
    index = _client.get("/").json()
    assert index["openapi"] == "/openapi.json"


def test_deprecated_version_aliases_serve_the_same_routes_with_deprecation_headers():
    canonical = _client.get("/tags")
    for prefix in ("/v1", "/v2"):
        aliased = _client.get(f"{prefix}/tags")
        assert aliased.status_code == canonical.status_code
        assert aliased.headers.get("Deprecation") == "true"
        assert aliased.headers.get("Link") == '</tags>; rel="successor-version"'
    assert "Deprecation" not in canonical.headers


def test_alias_openapi_is_not_published():
    assert _client.get("/v1/openapi.json").status_code == 404
    assert _client.get("/v2/openapi.json").status_code == 404


def test_api_key_gate_covers_canonical_and_alias_paths():
    previous_key = main._API_KEY
    main._API_KEY = "test-key"
    try:
        assert _client.get("/tags").status_code == 401
        assert _client.get("/v1/tags").status_code == 401
        assert _client.get("/tags", headers={"X-API-Key": "test-key"}).status_code == 200
        assert _client.get("/v1/tags", headers={"X-API-Key": "test-key"}).status_code == 200
        # Public paths stay public in both forms.
        assert _client.get("/health").status_code == 200
        assert _client.get("/openapi.json").status_code == 200
    finally:
        main._API_KEY = previous_key


def test_queued_ingestion_endpoint_requires_auth_on_all_forms():
    # Clear any auth overrides other test modules may have left on the shared apps.
    saved_app = dict(main.app.dependency_overrides)
    saved_alias = dict(main.alias_app.dependency_overrides)
    main.app.dependency_overrides.clear()
    main.alias_app.dependency_overrides.clear()
    try:
        run = "99999999-9999-4999-8999-999999999999"
        for path in (
            f"/catalogues/ingestions/{run}",
            f"/v1/catalogues/ingestions/{run}",
            f"/v2/catalogues/ingestions/{run}",
        ):
            response = _client.get(path)
            assert response.status_code in {401, 403}, path
    finally:
        main.app.dependency_overrides.update(saved_app)
        main.alias_app.dependency_overrides.update(saved_alias)

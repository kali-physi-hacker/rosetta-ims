import os
import sys
import tempfile

os.environ.setdefault("DATABASE_URL", f"sqlite:///{tempfile.mkdtemp()}/t.db")
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BACKEND_ROOT)

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

_client = TestClient(main.app)


def test_v1_exposes_current_api_contract():
    schema = _client.get("/v1/openapi.json").json()
    paths = schema["paths"]

    assert schema["info"]["version"] == "1.0.0"
    assert "/auth/login" in paths
    assert "/products" in paths
    assert "/v1/products" not in paths
    assert _client.get("/v1/health").json() == {"status": "ok", "version": "1.0.0"}


def test_v2_exposes_auth_and_inventory_contract_without_catalogue_ingestion():
    schema = _client.get("/v2/openapi.json").json()
    paths = schema["paths"]

    assert schema["info"]["version"] == "2.0.0"
    assert "/auth/login" in paths
    assert "/products" in paths
    assert "/products/{sku}" in paths
    assert "/products/{sku}/stock/adjust" in paths
    assert "/suppliers" in paths
    assert "/pricing" in paths
    assert "/catalogues" not in paths
    assert "/catalogues/import" not in paths
    assert "/catalogues/reparse/latest" not in paths
    assert not any(path.startswith("/catalogues/") for path in paths)


def test_root_openapi_redirects_to_v1_schema():
    response = _client.get("/openapi.json", follow_redirects=False)

    assert response.status_code in {307, 308}
    assert response.headers["location"] == "v1/openapi.json"


def test_api_key_gate_applies_to_v1_routes():
    previous_key = main._API_KEY
    main._API_KEY = "test-key"
    try:
        assert _client.get("/v1/tags").status_code == 401
        assert _client.get("/v1/tags", headers={"X-API-Key": "test-key"}).status_code == 200
    finally:
        main._API_KEY = previous_key


def test_api_key_gate_applies_to_v2_inventory_routes():
    previous_key = main._API_KEY
    main._API_KEY = "test-key"
    try:
        assert _client.get("/v2/tags").status_code == 401
        assert _client.get("/v2/tags", headers={"X-API-Key": "test-key"}).status_code == 200
    finally:
        main._API_KEY = previous_key


def test_cors_preflight_applies_to_v1_routes():
    response = _client.options(
        "/v1/suppliers",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3001"


def test_cors_preflight_applies_to_v2_routes():
    response = _client.options(
        "/v2/suppliers",
        headers={
            "Origin": "http://localhost:3001",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3001"

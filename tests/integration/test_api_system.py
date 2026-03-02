"""FastAPI system endpoint regression tests using TestClient."""

import pytest
from fastapi.testclient import TestClient
from src.api.main import app

client = TestClient(app, raise_server_exceptions=False)


def test_root_not_500():
    """GET / returns 200 or 404 but never 500."""
    response = client.get("/")
    assert response.status_code in (200, 404)


def test_ui_returns_html():
    """GET /ui returns 200 and HTML content."""
    response = client.get("/ui")
    assert response.status_code == 200
    assert "html" in response.headers.get("content-type", "").lower()


def test_run_history_returns_list():
    """GET /api/v1/runs/history returns 200 and a list."""
    response = client.get("/api/v1/runs/history")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)


def test_docs_available():
    """GET /docs returns 200 (Swagger UI available)."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_openapi_json_has_paths():
    """GET /openapi.json returns 200 and has 'paths' key."""
    response = client.get("/openapi.json")
    assert response.status_code == 200
    data = response.json()
    assert "paths" in data


def test_trigger_valid_body_returns_202():
    """POST /api/v1/runs/trigger with valid body returns 202."""
    payload = {"suite": "config/test_suites/dummy.yaml"}
    response = client.post("/api/v1/runs/trigger", json=payload)
    assert response.status_code == 202


def test_trigger_missing_suite_returns_422():
    """POST /api/v1/runs/trigger with missing 'suite' field returns 422."""
    response = client.post("/api/v1/runs/trigger", json={})
    assert response.status_code == 422


def test_run_nonexistent_id_returns_404():
    """GET /api/v1/runs/nonexistent-id returns 404."""
    response = client.get("/api/v1/runs/nonexistent-id-xyz-999")
    assert response.status_code == 404


def test_health_endpoint_returns_healthy():
    """GET /api/v1/system/health returns 200 with healthy status."""
    response = client.get("/api/v1/system/health")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "healthy"


def test_system_info_endpoint():
    """GET /api/v1/system/info returns 200 with api_version field."""
    response = client.get("/api/v1/system/info")
    assert response.status_code == 200
    data = response.json()
    assert "api_version" in data

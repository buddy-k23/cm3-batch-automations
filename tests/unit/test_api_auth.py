"""API key authentication coverage for FastAPI routes."""

from fastapi.testclient import TestClient

from src.api.main import app


def test_health_endpoint_remains_public(monkeypatch):
    """Health endpoint should not require an API key."""
    monkeypatch.setenv("API_KEYS", "dev-key")
    client = TestClient(app)

    response = client.get("/api/v1/system/health")

    assert response.status_code == 200


def test_missing_api_key_returns_401(monkeypatch):
    """Protected route should reject requests without X-API-Key."""
    monkeypatch.setenv("API_KEYS", "dev-key")
    client = TestClient(app)

    response = client.get("/api/v1/system/info")

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing X-API-Key"


def test_invalid_api_key_returns_403(monkeypatch):
    """Protected route should reject invalid API keys."""
    monkeypatch.setenv("API_KEYS", "dev-key")
    client = TestClient(app)

    response = client.get("/api/v1/system/info", headers={"X-API-Key": "wrong"})

    assert response.status_code == 403
    assert response.json()["detail"] == "Invalid API key"


def test_valid_api_key_allows_protected_route(monkeypatch):
    """Protected route should allow known API keys."""
    monkeypatch.setenv("API_KEYS", "dev-key")
    client = TestClient(app)

    response = client.get("/api/v1/system/info", headers={"X-API-Key": "dev-key"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["api_version"] == "1.0.0"

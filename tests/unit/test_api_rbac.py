"""RBAC enforcement tests for API key roles."""

from fastapi.testclient import TestClient

from src.api.main import app


def test_tester_cannot_upload_mapping(monkeypatch):
    """Tester role must not upload mappings."""
    monkeypatch.setenv("API_KEYS", "tester-key:tester")
    client = TestClient(app)

    files = {"file": ("sample.csv", b"a,b\n1,2\n", "text/csv")}
    response = client.post("/api/v1/mappings/upload", files=files, headers={"X-API-Key": "tester-key"})

    assert response.status_code == 403
    assert "mapping_owner" in response.json()["detail"]


def test_mapping_owner_can_upload_mapping(monkeypatch):
    """Mapping owner role can call mapping upload endpoint."""
    monkeypatch.setenv("API_KEYS", "owner-key:mapping_owner")
    client = TestClient(app)

    files = {"file": ("sample.csv", b"name,type\nA,String\n", "text/csv")}
    response = client.post("/api/v1/mappings/upload", files=files, headers={"X-API-Key": "owner-key"})

    # endpoint may fail conversion depending on fixture, but must pass RBAC gate
    assert response.status_code != 403


def test_mapping_owner_cannot_access_admin_metrics(monkeypatch):
    """Mapping owner must not access admin-only endpoints."""
    monkeypatch.setenv("API_KEYS", "owner-key:mapping_owner")
    client = TestClient(app)

    response = client.get("/api/v1/system/metrics", headers={"X-API-Key": "owner-key"})

    assert response.status_code == 403
    assert "admin" in response.json()["detail"]

"""Unit tests for GET /api/v1/system/db-connections endpoint."""

import json
import os

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

# Ensure a test API key is registered before importing the test client.
_api_keys = os.getenv("API_KEYS", "")
if "dev-key" not in {k.split(":", 1)[0].strip() for k in _api_keys.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys},dev-key:admin" if _api_keys else "dev-key:admin"

client = TestClient(app)
API_HEADERS = {"X-API-Key": "dev-key"}

_STAGING_CONN = {
    "host": "stg:1522/DB",
    "user": "Valdo",
    "password": "secret",
    "schema": "APP_INT",
    "adapter": "oracle",
}

_DEV_CONN = {
    "host": "dev:1521/DEV",
    "user": "DEVUSER",
    "password": "devpass",
    "schema": "DEVSCHEMA",
    "adapter": "oracle",
}


class TestDbConnectionsEndpoint:
    """Tests for GET /api/v1/system/db-connections."""

    def test_returns_200_empty_list_when_env_not_set(self, monkeypatch):
        """Endpoint returns HTTP 200 with an empty array when DB_CONNECTIONS is unset."""
        monkeypatch.delenv("DB_CONNECTIONS", raising=False)
        response = client.get("/api/v1/system/db-connections", headers=API_HEADERS)
        assert response.status_code == 200
        assert response.json() == []

    def test_returns_200_with_connection_summaries(self, monkeypatch):
        """Endpoint returns HTTP 200 with a list of connection objects when DB_CONNECTIONS is set."""
        connections_json = json.dumps(
            {"STAGING": _STAGING_CONN, "DEV-1": _DEV_CONN}
        )
        monkeypatch.setenv("DB_CONNECTIONS", connections_json)

        response = client.get("/api/v1/system/db-connections", headers=API_HEADERS)
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert len(data) == 2
        assert {item["name"] for item in data} == {"STAGING", "DEV-1"}

    def test_response_items_have_required_fields(self, monkeypatch):
        """Each response item contains name, host, user, schema, and adapter fields."""
        connections_json = json.dumps({"STAGING": _STAGING_CONN})
        monkeypatch.setenv("DB_CONNECTIONS", connections_json)

        response = client.get("/api/v1/system/db-connections", headers=API_HEADERS)
        assert response.status_code == 200

        item = response.json()[0]
        assert item["name"] == "STAGING"
        assert item["host"] == "stg:1522/DB"
        assert item["user"] == "Valdo"
        assert item["schema"] == "APP_INT"
        assert item["adapter"] == "oracle"

    def test_password_field_absent_from_every_item(self, monkeypatch):
        """Password must never appear in any response item — security requirement."""
        connections_json = json.dumps(
            {"STAGING": _STAGING_CONN, "DEV-1": _DEV_CONN}
        )
        monkeypatch.setenv("DB_CONNECTIONS", connections_json)

        response = client.get("/api/v1/system/db-connections", headers=API_HEADERS)
        assert response.status_code == 200

        for item in response.json():
            assert "password" not in item, (
                f"password field must not appear in response item: {item}"
            )

    def test_returns_401_without_api_key(self, monkeypatch):
        """Endpoint returns HTTP 401 when no API key is provided."""
        monkeypatch.delenv("DB_CONNECTIONS", raising=False)
        response = client.get("/api/v1/system/db-connections")
        assert response.status_code == 401

    def test_returns_403_with_wrong_api_key(self, monkeypatch):
        """Endpoint returns HTTP 403 when an unrecognised API key is supplied."""
        monkeypatch.delenv("DB_CONNECTIONS", raising=False)
        response = client.get(
            "/api/v1/system/db-connections",
            headers={"X-API-Key": "wrong-key"},
        )
        assert response.status_code == 403

    def test_connection_names_match_env_keys(self, monkeypatch):
        """The name field in each item matches the key used in DB_CONNECTIONS JSON."""
        connections_json = json.dumps(
            {"STAGING": _STAGING_CONN, "DEV-1": _DEV_CONN}
        )
        monkeypatch.setenv("DB_CONNECTIONS", connections_json)

        response = client.get("/api/v1/system/db-connections", headers=API_HEADERS)
        assert response.status_code == 200

        names = {item["name"] for item in response.json()}
        assert names == {"STAGING", "DEV-1"}

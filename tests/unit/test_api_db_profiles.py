"""Unit tests for GET /api/v1/system/db-profiles endpoint (TDD)."""
from __future__ import annotations

import os
from unittest.mock import patch

os.environ.setdefault("API_KEYS", "test-key:admin")

import pytest
from fastapi.testclient import TestClient


def _make_client():
    from src.api.main import app
    return TestClient(app)


class TestGetDbProfiles:
    def test_endpoint_returns_200(self) -> None:
        client = _make_client()
        with patch("src.api.routers.system.load_profiles", return_value=[]):
            resp = client.get("/api/v1/system/db-profiles")
        assert resp.status_code == 200

    def test_no_auth_required(self) -> None:
        """Profiles list is non-sensitive — no API key needed."""
        client = _make_client()
        with patch("src.api.routers.system.load_profiles", return_value=[]):
            resp = client.get("/api/v1/system/db-profiles")
        assert resp.status_code == 200

    def test_returns_empty_list_when_no_profiles(self) -> None:
        client = _make_client()
        with patch("src.api.routers.system.load_profiles", return_value=[]):
            resp = client.get("/api/v1/system/db-profiles")
        assert resp.json() == {"profiles": []}

    def test_returns_profile_fields(self, monkeypatch) -> None:
        from src.api.models.db_profile import DbProfile
        monkeypatch.setenv("ORACLE_PASSWORD", "pw")
        client = _make_client()
        profile = DbProfile(
            name="Local Dev",
            adapter="oracle",
            host="localhost:1521/FREEPDB1",
            user="APP_INT",
            schema="APP_INT",
            password_env="ORACLE_PASSWORD",
        )
        with patch("src.api.routers.system.load_profiles", return_value=[profile]):
            resp = client.get("/api/v1/system/db-profiles")
        data = resp.json()
        assert len(data["profiles"]) == 1
        p = data["profiles"][0]
        assert p["name"] == "Local Dev"
        assert p["adapter"] == "oracle"
        assert p["host"] == "localhost:1521/FREEPDB1"
        assert p["user"] == "APP_INT"
        assert p["schema"] == "APP_INT"
        assert p["password_env"] == "ORACLE_PASSWORD"
        assert p["password_env_set"] is True
        assert "password" not in p

    def test_password_env_set_false_when_var_missing(self, monkeypatch) -> None:
        from src.api.models.db_profile import DbProfile
        monkeypatch.delenv("DB_NO_PW", raising=False)
        client = _make_client()
        profile = DbProfile(
            name="Prod",
            adapter="oracle",
            host="prod:1521/PROD",
            user="APP_INT",
            schema="APP_INT",
            password_env="DB_NO_PW",
        )
        with patch("src.api.routers.system.load_profiles", return_value=[profile]):
            resp = client.get("/api/v1/system/db-profiles")
        p = resp.json()["profiles"][0]
        assert p["password_env_set"] is False


class TestDbPingWithProfile:
    def test_db_ping_accepts_profile_name(self) -> None:
        """profile_name form field must be accepted without 422."""
        client = _make_client()
        from src.config.db_config import DbConfig
        mock_cfg = DbConfig(user="U", password="P", dsn="H:1/S", schema="SCH", db_adapter="oracle")
        with patch("src.api.routers.system.resolve_profile", return_value=mock_cfg), \
             patch("src.api.routers.system.OracleConnection") as mock_conn:
            mock_conn.return_value.connect.return_value = None
            resp = client.post(
                "/api/v1/system/db-ping",
                data={"profile_name": "Local Dev"},
                headers={"X-API-Key": "test-key"},
            )
        assert resp.status_code == 200

    def test_db_ping_profile_not_found_returns_error(self) -> None:
        client = _make_client()
        with patch("src.api.routers.system.resolve_profile",
                   side_effect=KeyError("Profile not found: 'Bad'")):
            resp = client.post(
                "/api/v1/system/db-ping",
                data={"profile_name": "Bad"},
                headers={"X-API-Key": "test-key"},
            )
        data = resp.json()
        assert resp.status_code == 200
        assert data["ok"] is False
        assert "Profile not found" in data["error"]

    def test_db_ping_missing_password_env_returns_error(self) -> None:
        client = _make_client()
        with patch("src.api.routers.system.resolve_profile",
                   side_effect=RuntimeError("DB_PROD_PASSWORD is not set")):
            resp = client.post(
                "/api/v1/system/db-ping",
                data={"profile_name": "Prod"},
                headers={"X-API-Key": "test-key"},
            )
        data = resp.json()
        assert data["ok"] is False
        assert "DB_PROD_PASSWORD" in data["error"]

    def test_db_ping_profile_success(self) -> None:
        client = _make_client()
        from src.config.db_config import DbConfig
        mock_cfg = DbConfig(
            user="APP_INT", password="secret",
            dsn="localhost:1521/FREEPDB1", schema="APP_INT", db_adapter="oracle"
        )
        with patch("src.api.routers.system.resolve_profile", return_value=mock_cfg), \
             patch("src.api.routers.system.OracleConnection") as mock_conn:
            mock_conn.return_value.connect.return_value = None
            resp = client.post(
                "/api/v1/system/db-ping",
                data={"profile_name": "Local Dev"},
                headers={"X-API-Key": "test-key"},
            )
        assert resp.json() == {"ok": True}

    def test_db_ping_adhoc_path_unchanged(self) -> None:
        """Existing ad-hoc credential path must still work when no profile_name."""
        client = _make_client()
        with patch("src.api.routers.system.OracleConnection") as mock_conn:
            mock_conn.return_value.connect.return_value = None
            resp = client.post(
                "/api/v1/system/db-ping",
                data={
                    "db_host": "localhost:1521/DEV",
                    "db_user": "USR",
                    "db_password": "PW",
                    "db_adapter": "oracle",
                },
                headers={"X-API-Key": "test-key"},
            )
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

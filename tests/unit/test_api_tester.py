"""Unit tests for /api/v1/api-tester/* endpoints."""
from __future__ import annotations

import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# The api-tester router now requires a valid X-API-Key (issue #9 fix 9-C).
# We force-overwrite ``API_KEYS`` (rather than ``setdefault``) so the value
# is deterministic regardless of test ordering with other modules that may
# have already populated the env var.
os.environ["API_KEYS"] = "test-key:admin"

from src.api.main import app

client = TestClient(app, raise_server_exceptions=False)
client.headers.update({"X-API-Key": "test-key"})


class TestProxy:
    def test_proxy_get_success(self):
        """Proxy forwards GET and returns status/body/elapsed."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.text = '{"status": "ok"}'

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=mock_resp)

        with patch("src.api.routers.api_tester.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/api/v1/api-tester/proxy",
                data={"config": json.dumps({"method": "GET", "url": "http://example.com/api"})},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["status_code"] == 200
        assert data["body"] == '{"status": "ok"}'
        assert data["elapsed_ms"] >= 0

    def test_proxy_connection_error_returns_502(self):
        """Connection failure returns 502."""
        import httpx as _httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=_httpx.ConnectError("refused"))

        with patch("src.api.routers.api_tester.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/api/v1/api-tester/proxy",
                data={"config": json.dumps({"method": "GET", "url": "http://unreachable"})},
            )

        assert resp.status_code == 502

    def test_proxy_timeout_returns_504(self):
        """Timeout returns 504."""
        import httpx as _httpx

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=_httpx.TimeoutException("timeout"))

        with patch("src.api.routers.api_tester.httpx.AsyncClient", return_value=mock_client):
            resp = client.post(
                "/api/v1/api-tester/proxy",
                data={"config": json.dumps({"method": "GET", "url": "http://slow"})},
            )

        assert resp.status_code == 504

    def test_proxy_missing_config_returns_422(self):
        """Missing config form field returns 422."""
        resp = client.post("/api/v1/api-tester/proxy", data={})
        assert resp.status_code == 422

    def test_proxy_file_scheme_rejected_422(self):
        """file:// URLs are rejected with 422 to prevent SSRF file reads."""
        resp = client.post(
            "/api/v1/api-tester/proxy",
            data={"config": json.dumps({"method": "GET", "url": "file:///etc/passwd"})},
        )
        assert resp.status_code == 422
        assert "http" in resp.json()["detail"].lower()

    def test_proxy_ftp_scheme_rejected_422(self):
        """ftp:// URLs are also rejected with 422."""
        resp = client.post(
            "/api/v1/api-tester/proxy",
            data={"config": json.dumps({"method": "GET", "url": "ftp://example.com/file"})},
        )
        assert resp.status_code == 422


class TestSuiteCRUD:
    def test_list_suites_returns_list(self, tmp_path, monkeypatch):
        """list_suites returns empty list when directory is empty, then one entry after create."""
        monkeypatch.setattr("src.api.routers.api_tester.SUITES_DIR", tmp_path)

        resp = client.get("/api/v1/api-tester/suites")
        assert resp.status_code == 200
        assert resp.json() == []

        payload = {"name": "Listed Suite", "base_url": "http://example.com", "requests": []}
        client.post("/api/v1/api-tester/suites", json=payload)

        resp2 = client.get("/api/v1/api-tester/suites")
        assert resp2.status_code == 200
        assert len(resp2.json()) == 1

    def test_create_and_get_suite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.api.routers.api_tester.SUITES_DIR", tmp_path)
        payload = {"name": "Test Suite", "base_url": "http://localhost", "requests": []}
        resp = client.post("/api/v1/api-tester/suites", json=payload)
        assert resp.status_code == 201
        created = resp.json()
        suite_id = created["id"]
        assert suite_id  # non-empty
        assert created["base_url"] == "http://localhost"
        assert created["requests"] == []

        resp2 = client.get(f"/api/v1/api-tester/suites/{suite_id}")
        assert resp2.status_code == 200
        assert resp2.json()["name"] == "Test Suite"
        assert resp2.json()["id"] == suite_id
        assert resp2.json()["base_url"] == "http://localhost"
        assert resp2.json()["requests"] == []

    def test_update_suite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.api.routers.api_tester.SUITES_DIR", tmp_path)
        payload = {"name": "Original", "base_url": "http://localhost", "requests": []}
        suite_id = client.post("/api/v1/api-tester/suites", json=payload).json()["id"]

        update = {"name": "Updated", "base_url": "http://localhost", "requests": []}
        resp = client.put(f"/api/v1/api-tester/suites/{suite_id}", json=update)
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    def test_delete_suite(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.api.routers.api_tester.SUITES_DIR", tmp_path)
        payload = {"name": "ToDelete", "base_url": "http://localhost", "requests": []}
        suite_id = client.post("/api/v1/api-tester/suites", json=payload).json()["id"]

        resp = client.delete(f"/api/v1/api-tester/suites/{suite_id}")
        assert resp.status_code == 204

        resp2 = client.get(f"/api/v1/api-tester/suites/{suite_id}")
        assert resp2.status_code == 404

    def test_get_nonexistent_suite_returns_404(self, tmp_path, monkeypatch):
        """A valid-format UUID that has no backing file returns 404."""
        monkeypatch.setattr("src.api.routers.api_tester.SUITES_DIR", tmp_path)
        resp = client.get("/api/v1/api-tester/suites/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404

    def test_get_invalid_suite_id_returns_400(self):
        """A non-UUID suite_id returns 400 (guards against path traversal via suite_id).

        Note: URL-level path traversal (../../../etc) is normalized by Starlette before
        reaching the handler. This test verifies the UUID regex in _suite_path rejects
        any non-UUID string that does reach the handler.
        """
        resp = client.get("/api/v1/api-tester/suites/not-a-valid-uuid")
        assert resp.status_code == 400
        assert "Invalid suite_id" in resp.json()["detail"]

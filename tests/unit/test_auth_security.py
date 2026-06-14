"""Security regression tests for issue #9 \u2014 authentication bypass and SSRF.

These tests lock in the post-fix behaviour:

* Empty ``API_KEYS`` no longer grants admin \u2014 it returns 503.
* The Referer-based bypass has been removed.
* All sensitive routers now require a valid API key.
* The global exception handler returns an opaque ``error_id`` and does
  not leak ``str(exc)`` internals.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


# Endpoints that previously could be accessed without auth and now must
# return 401 when no X-API-Key is sent.
PROTECTED_ENDPOINTS = [
    ("GET", "/api/v1/rules"),
    ("GET", "/api/v1/runs/trend"),
    ("GET", "/api/v1/schedules"),
    ("POST", "/api/v1/webhook/validate"),
]


@pytest.fixture
def client_with_keys(monkeypatch):
    """A TestClient with a known API key configured."""
    monkeypatch.setenv("API_KEYS", "dev-key:admin")
    return TestClient(app)


class TestEmptyApiKeysNoLongerGrantsAdmin:
    """Issue #9 \u2014 fix 9-A."""

    def test_empty_api_keys_returns_503(self, monkeypatch):
        """When API_KEYS is unset/empty, protected endpoints return 503.

        Previously the missing-keys branch silently granted admin.
        """
        monkeypatch.delenv("API_KEYS", raising=False)
        client = TestClient(app)
        resp = client.get("/api/v1/system/info")
        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"].lower()


class TestRefererBypassIsRemoved:
    """Issue #9 \u2014 fix 9-B.

    A request with a spoofed ``Referer: .../ui`` header and no API key
    must NOT be authenticated.
    """

    def test_referer_only_request_is_rejected(self, client_with_keys):
        resp = client_with_keys.get(
            "/api/v1/system/info",
            headers={"Referer": "https://attacker.example.com/ui"},
        )
        assert resp.status_code == 401
        assert resp.json()["detail"] == "Missing X-API-Key"

    def test_referer_with_invalid_key_still_rejected(self, client_with_keys):
        resp = client_with_keys.get(
            "/api/v1/system/info",
            headers={
                "Referer": "https://attacker.example.com/ui",
                "X-API-Key": "wrong",
            },
        )
        assert resp.status_code == 403


class TestSensitiveRoutersRequireApiKey:
    """Issue #9 \u2014 fix 9-C."""

    @pytest.mark.parametrize("method,path", PROTECTED_ENDPOINTS)
    def test_unauthenticated_request_returns_401(
        self, client_with_keys, method, path
    ):
        resp = client_with_keys.request(method, path, json={} if method == "POST" else None)
        # 401 = missing key (the desired enforcement); 422 would mean the
        # request body validation kicked in before auth, which still
        # requires the auth dep to fire \u2014 we only insist that the
        # status is NOT 200.
        assert resp.status_code in (401, 403), (
            f"{method} {path} returned {resp.status_code}; "
            "expected 401/403 for missing API key"
        )


class TestGlobalExceptionHandlerSanitised:
    """Issue #9 \u2014 fix 9-E."""

    def test_exception_handler_returns_error_id_not_str_exc(self, monkeypatch):
        """When a route raises an unexpected exception, the response must
        contain an opaque ``error_id`` and must NOT echo ``str(exc)``.
        """
        monkeypatch.setenv("API_KEYS", "dev-key:admin")

        # Inject a route that raises a unique-string exception so we can
        # detect leaks in the response body.
        secret_marker = "SUPERSECRET_DB_PASSWORD_LEAK_e8f3"

        @app.get("/__leak_probe__")
        async def _leak():
            raise RuntimeError(secret_marker)

        try:
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/__leak_probe__", headers={"X-API-Key": "dev-key"})
            assert resp.status_code == 500
            body = resp.json()
            assert body["success"] is False
            assert "error_id" in body
            assert isinstance(body["error_id"], str) and body["error_id"]
            # The exception text must not appear in the body.
            assert secret_marker not in resp.text
            # And the legacy 'details' key must be gone.
            assert "details" not in body
        finally:
            # Remove the probe route so we don't pollute other tests.
            app.router.routes = [
                r for r in app.router.routes
                if getattr(r, "path", "") != "/__leak_probe__"
            ]

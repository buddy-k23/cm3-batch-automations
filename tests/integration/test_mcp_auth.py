"""MCP auth bridge integration tests (EF-S7).

Covers the LDAPS + X-API-Key + stdio-token auth surface added in EF-S7,
replacing the EF-S1 dev-mode placeholder. Each test forces a fresh
import of ``src.api.main`` so the module-level FastAPI app picks up the
exact env state under test.

Test inventory:

* HTTP middleware
    - ``test_mcp_dev_mode_bypasses_auth``: VALDO_MCP_AUTH=dev still
      passes through unauthenticated.
    - ``test_mcp_production_mode_rejects_unauthenticated_request``:
      no env, no header, no cookie → 401.
    - ``test_mcp_accepts_x_api_key``: configured X-API-Key → 200.
    - ``test_mcp_rejects_invalid_x_api_key``: unknown key → 401.
    - ``test_mcp_accepts_session_cookie``: monkeypatched session →
      200.
    - ``test_mcp_accepts_bearer_token``: valid signed token in
      Authorization header → 200.
    - ``test_mcp_rejects_expired_bearer_token``: expired token → 401.
    - ``test_mcp_rejects_tampered_bearer_token``: bad signature → 401.

* ``POST /api/v2/mcp/login`` endpoint
    - ``test_mcp_login_endpoint_returns_token_on_valid_credentials``:
      mocked LDAP success, token returned + signature valid.
    - ``test_mcp_login_endpoint_rejects_invalid_credentials``:
      mocked LDAP failure → 401.

* ``valdo mcp-login`` CLI
    - ``test_mcp_login_cli_writes_token_to_home_dir``: CliRunner
      against a mocked HTTP endpoint; token file ends up at
      VALDO_MCP_TOKEN_PATH with 0600 perms.

* Stdio token loader
    - ``test_mcp_stdio_mode_reads_token``: valid token on disk →
      load_stdio_user returns the principal.
    - ``test_mcp_stdio_mode_rejects_expired_token``: helpful error
      mentions ``valdo mcp-login``.
    - ``test_mcp_stdio_mode_rejects_invalid_signature``: tamper the
      ``signature`` field, expect TokenError with informative msg.
"""

from __future__ import annotations

import importlib
import json
import os
import stat
import sys
import time
from pathlib import Path
from typing import Optional
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Constants reused across tests
# ---------------------------------------------------------------------------


_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_SIGNING_KEY = "ef-s7-test-key-do-not-reuse-in-production"


def _fresh_app():
    """Reload the FastAPI app so module-level env reads pick up monkeypatch."""
    for mod_name in [
        "src.api.main",
        "src.api.routers.mcp_auth",
        "src.mcp.server",
        "src.mcp.auth",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _session_signing_key(monkeypatch):
    """Mirror the scaffold fixture — UI auth.enabled wants this on import."""
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )
    # The TestClient uses host "testserver"; the MCP transport's
    # DNS-rebinding protection rejects unknown hosts in non-dev mode.
    # Allow-listing the test host here keeps the auth surface under
    # test (the middleware) while the transport defaults stay tight in
    # production.
    monkeypatch.setenv("VALDO_MCP_ALLOWED_HOSTS", "testserver,localhost")


@pytest.fixture
def signing_key_env(monkeypatch):
    """Configure the MCP token signing key for the duration of the test."""
    monkeypatch.setenv("VALDO_MCP_TOKEN_SIGNING_KEY", _SIGNING_KEY)
    yield _SIGNING_KEY


# ---------------------------------------------------------------------------
# HTTP middleware tests
# ---------------------------------------------------------------------------


def test_mcp_dev_mode_bypasses_auth(monkeypatch):
    """``VALDO_MCP_AUTH=dev`` keeps the EF-S1 pass-through behaviour."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()
    with TestClient(app) as client:
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=_MCP_HEADERS)
    assert response.status_code == 200, response.text


def test_mcp_production_mode_rejects_unauthenticated_request(monkeypatch):
    """No dev, no header, no cookie, no token → 401 with generic body."""
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)
    app = _fresh_app()
    with TestClient(app) as client:
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=_MCP_HEADERS)
    assert response.status_code == 401, response.text
    assert response.json() == {"error": "MCP auth required"}


def test_mcp_accepts_x_api_key(monkeypatch):
    """A configured X-API-Key authenticates the MCP request."""
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    monkeypatch.setenv("API_KEYS", "test-key-abc:admin")
    app = _fresh_app()
    with TestClient(app) as client:
        headers = {**_MCP_HEADERS, "X-API-Key": "test-key-abc"}
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=headers)
    assert response.status_code == 200, response.text


def test_mcp_rejects_invalid_x_api_key(monkeypatch):
    """An X-API-Key not in ``API_KEYS`` is rejected with 401."""
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    monkeypatch.setenv("API_KEYS", "real-key:admin")
    app = _fresh_app()
    with TestClient(app) as client:
        headers = {**_MCP_HEADERS, "X-API-Key": "wrong-key"}
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=headers)
    assert response.status_code == 401, response.text
    assert response.json() == {"error": "MCP auth required"}


def test_mcp_accepts_session_cookie(monkeypatch):
    """A Starlette session cookie populated with a user authenticates."""
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)

    # Patch _try_resolve_session_cookie at the import site used by
    # src.mcp.auth so the middleware sees a "logged-in" user without
    # having to round-trip through SessionMiddleware + LDAP. The patch
    # has to be installed BEFORE we build the fresh app so the import
    # path inside the auth module resolves to the patched callable on
    # every request.
    from src.api import auth as parent_auth

    def _fake_session(request):
        return parent_auth.AuthContext(
            key_id="ldap-fake",
            role="admin",
            auth_kind="ldap",
            subject="CN=test-user,OU=Users,DC=bank,DC=internal",
            email="t@bank.internal",
            name="Test User",
        )

    monkeypatch.setattr(parent_auth, "_try_resolve_session_cookie", _fake_session)
    app = _fresh_app()
    # After _fresh_app reloads, also patch on the freshly imported module
    from src.api import auth as parent_auth_reloaded

    monkeypatch.setattr(parent_auth_reloaded, "_try_resolve_session_cookie", _fake_session)

    with TestClient(app) as client:
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=_MCP_HEADERS)
    assert response.status_code == 200, response.text


def test_mcp_accepts_bearer_token(monkeypatch, signing_key_env):
    """A valid signed bearer token authenticates the request."""
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)

    # Mint a token before reloading the app. The reload re-imports
    # src.mcp.auth so the signing key env must remain set across the
    # boundary — which it does, courtesy of monkeypatch.setenv.
    from src.mcp import auth as mcp_auth

    token = mcp_auth.mint_token(
        user="alice",
        principal_dn="CN=alice,OU=Users,DC=bank,DC=internal",
        role="admin",
        ttl_hours=1,
    )
    bearer = mcp_auth.encode_bearer(token)

    app = _fresh_app()
    with TestClient(app) as client:
        headers = {**_MCP_HEADERS, "Authorization": f"Bearer {bearer}"}
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=headers)
    assert response.status_code == 200, response.text


def test_mcp_rejects_expired_bearer_token(monkeypatch, signing_key_env):
    """A signed but expired bearer token is rejected with 401."""
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)

    from src.mcp import auth as mcp_auth

    # Hand-build an expired token (issued 2 days ago, ttl 1 hour).
    issued = int(time.time()) - 2 * 24 * 3600
    expires = issued + 3600
    token = {
        "user": "alice",
        "principal_dn": "CN=alice,OU=Users,DC=bank,DC=internal",
        "role": "admin",
        "issued_at": issued,
        "expires_at": expires,
    }
    # Sign with the real key so signature passes — expiry alone fails.
    import hashlib
    import hmac

    sig_input = (
        f"{token['user']}|{token['principal_dn']}|{token['role']}|{issued}|{expires}"
    ).encode("utf-8")
    token["signature"] = hmac.new(
        _SIGNING_KEY.encode("utf-8"), sig_input, hashlib.sha256
    ).hexdigest()
    bearer = mcp_auth.encode_bearer(token)

    app = _fresh_app()
    with TestClient(app) as client:
        headers = {**_MCP_HEADERS, "Authorization": f"Bearer {bearer}"}
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=headers)
    assert response.status_code == 401, response.text


def test_mcp_rejects_tampered_bearer_token(monkeypatch, signing_key_env):
    """A bearer token with a bad signature is rejected with 401."""
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)

    from src.mcp import auth as mcp_auth

    token = mcp_auth.mint_token(
        user="alice",
        principal_dn="CN=alice,OU=Users,DC=bank,DC=internal",
        role="admin",
        ttl_hours=1,
    )
    # Tamper the role field — signature no longer matches.
    token["role"] = "tester-but-actually-tried-to-escalate"
    bearer = mcp_auth.encode_bearer(token)

    app = _fresh_app()
    with TestClient(app) as client:
        headers = {**_MCP_HEADERS, "Authorization": f"Bearer {bearer}"}
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=headers)
    assert response.status_code == 401, response.text


# ---------------------------------------------------------------------------
# POST /api/v2/mcp/login endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def ldap_success(monkeypatch):
    """Replace ldap_authenticate inside the router module with a happy stub."""
    from src.api import auth_ldap as parent_ldap

    fake_user = parent_ldap.LdapUser(
        dn="CN=alice,OU=Users,DC=bank,DC=internal",
        email="alice@bank.internal",
        name="Alice",
        groups=("valdo-admins",),
    )

    def _stub(username, password, cfg):
        if username == "alice" and password == "good-password":
            return fake_user
        raise parent_ldap.LdapAuthError("invalid_credentials")

    # The route imports `ldap_authenticate` at module load. Patch both
    # places so a reload-after-patch sees the stub.
    monkeypatch.setattr(parent_ldap, "ldap_authenticate", _stub)
    return _stub


def test_mcp_login_endpoint_returns_token_on_valid_credentials(
    monkeypatch, signing_key_env, ldap_success
):
    """Valid LDAP credentials → 200 + signed token + verifiable signature."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")  # bypass MCP middleware for /api/v2/mcp/login? No — /login is on FastAPI, not MCP sub-app
    monkeypatch.delenv("API_KEYS", raising=False)

    app = _fresh_app()
    # Re-patch on the post-reload import path.
    from src.api import auth_ldap as reloaded_ldap

    monkeypatch.setattr(reloaded_ldap, "ldap_authenticate", ldap_success)
    from src.api.routers import mcp_auth as router_mod

    monkeypatch.setattr(router_mod, "ldap_authenticate", ldap_success)

    with TestClient(app) as client:
        response = client.post(
            "/api/v2/mcp/login",
            json={"username": "alice", "password": "good-password", "ttl_hours": 2},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert "token" in body
    token = body["token"]
    assert token["user"] == "alice"
    assert token["principal_dn"] == "CN=alice,OU=Users,DC=bank,DC=internal"
    assert token["role"] == "admin"
    assert "signature" in token

    # Round-trip verify the signature.
    from src.mcp import auth as mcp_auth

    verified = mcp_auth.verify_token(token)
    assert verified.user == "alice"
    assert verified.role == "admin"
    # TTL of 2h ⇒ expires_at ≈ now + 7200s.
    assert 0 < verified.expires_at - verified.issued_at <= 2 * 3600 + 1


def test_mcp_login_endpoint_rejects_invalid_credentials(
    monkeypatch, signing_key_env, ldap_success
):
    """Bad password → 401 with the canonical generic detail."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()
    from src.api import auth_ldap as reloaded_ldap

    monkeypatch.setattr(reloaded_ldap, "ldap_authenticate", ldap_success)
    from src.api.routers import mcp_auth as router_mod

    monkeypatch.setattr(router_mod, "ldap_authenticate", ldap_success)

    with TestClient(app) as client:
        response = client.post(
            "/api/v2/mcp/login",
            json={"username": "alice", "password": "wrong"},
        )
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "Invalid credentials"


# ---------------------------------------------------------------------------
# CLI subcommand
# ---------------------------------------------------------------------------


def test_mcp_login_cli_writes_token_to_home_dir(monkeypatch, tmp_path, signing_key_env):
    """``valdo mcp-login`` writes a 0600 token at VALDO_MCP_TOKEN_PATH."""
    from src.commands import mcp_login as mcp_login_cmd
    from src.mcp import auth as mcp_auth

    token_target = tmp_path / "mcp-token"
    monkeypatch.setenv("VALDO_MCP_TOKEN_PATH", str(token_target))

    # Mint a token server-side that the CLI will receive from the
    # mocked HTTP response.
    minted = mcp_auth.mint_token(
        user="bob",
        principal_dn="CN=bob,OU=Users,DC=bank,DC=internal",
        role="mapping_owner",
        ttl_hours=8,
    )

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self):
            return {
                "token": minted,
                "expires_at": minted["expires_at"],
                "principal_dn": minted["principal_dn"],
                "role": minted["role"],
            }

    def _fake_post(url, json=None, timeout=None, verify=None):  # noqa: A002 — match requests sig
        assert url.endswith("/api/v2/mcp/login")
        assert json["username"] == "bob"
        assert json["password"] == "secret"
        return _FakeResponse()

    # Patch `requests.post` inside the CLI module. The CLI does
    # `import requests` lazily so we monkeypatch the top-level module.
    import requests

    monkeypatch.setattr(requests, "post", _fake_post)

    runner = CliRunner()
    result = runner.invoke(
        mcp_login_cmd.mcp_login,
        ["--server", "http://valdo.test", "--username", "bob"],
        input="secret\n",  # password prompt
    )
    assert result.exit_code == 0, result.output
    assert "MCP login successful" in result.output
    assert token_target.exists()

    # 0600 perm check — owner read/write, no group/other access.
    mode = stat.S_IMODE(token_target.stat().st_mode)
    assert mode == 0o600, f"token file perms should be 0600, got {oct(mode)}"

    # Round-trip: load the token via the same code stdio mode uses.
    loaded = mcp_auth.load_token_file(token_target)
    assert loaded.user == "bob"
    assert loaded.role == "mapping_owner"


# ---------------------------------------------------------------------------
# Stdio token loader
# ---------------------------------------------------------------------------


def test_mcp_stdio_mode_reads_token(monkeypatch, tmp_path, signing_key_env):
    """A valid token at the configured path populates the current-user CV."""
    from src.mcp import auth as mcp_auth

    token_path = tmp_path / "mcp-token"
    monkeypatch.setenv("VALDO_MCP_TOKEN_PATH", str(token_path))

    token = mcp_auth.mint_token(
        user="carol",
        principal_dn="CN=carol,OU=Users,DC=bank,DC=internal",
        role="tester",
        ttl_hours=12,
    )
    mcp_auth.write_token_file(token, target=token_path)

    principal = mcp_auth.load_stdio_user()
    assert principal.user == "carol"
    assert principal.role == "tester"
    assert principal.auth_kind == "token"
    assert mcp_auth.current_user() == principal


def test_mcp_stdio_mode_rejects_expired_token(monkeypatch, tmp_path, signing_key_env):
    """An expired token surfaces a helpful TokenError mentioning mcp-login."""
    from src.mcp import auth as mcp_auth

    token_path = tmp_path / "mcp-token"
    monkeypatch.setenv("VALDO_MCP_TOKEN_PATH", str(token_path))

    issued = int(time.time()) - 48 * 3600
    expires = issued + 3600
    import hashlib
    import hmac

    payload = {
        "user": "carol",
        "principal_dn": "CN=carol,OU=Users,DC=bank,DC=internal",
        "role": "tester",
        "issued_at": issued,
        "expires_at": expires,
    }
    sig_input = (
        f"{payload['user']}|{payload['principal_dn']}|{payload['role']}|{issued}|{expires}"
    ).encode("utf-8")
    payload["signature"] = hmac.new(
        _SIGNING_KEY.encode("utf-8"), sig_input, hashlib.sha256
    ).hexdigest()

    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(mcp_auth.TokenError) as excinfo:
        mcp_auth.load_stdio_user()
    assert "expired" in str(excinfo.value).lower()
    assert "valdo mcp-login" in str(excinfo.value).lower()


def test_mcp_stdio_mode_rejects_invalid_signature(monkeypatch, tmp_path, signing_key_env):
    """A token with a tampered signature surfaces a clear TokenError."""
    from src.mcp import auth as mcp_auth

    token_path = tmp_path / "mcp-token"
    monkeypatch.setenv("VALDO_MCP_TOKEN_PATH", str(token_path))

    token = mcp_auth.mint_token(
        user="carol",
        principal_dn="CN=carol,OU=Users,DC=bank,DC=internal",
        role="tester",
        ttl_hours=1,
    )
    # Tamper the signature — flip the last hex char.
    sig = token["signature"]
    token["signature"] = sig[:-1] + ("0" if sig[-1] != "0" else "1")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(json.dumps(token), encoding="utf-8")

    with pytest.raises(mcp_auth.TokenError) as excinfo:
        mcp_auth.load_stdio_user()
    assert "signature" in str(excinfo.value).lower()

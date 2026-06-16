"""Integration tests for MCP per-token revocation (S9-4, #389).

End-to-end coverage of the acceptance criteria:

* A token carries a ``jti`` (16-byte random) and verifies.
* A legacy (no-jti) token still validates during the 24h grace window
  AND is rejected once the window elapses.
* Revoking a ``jti`` via ``POST /api/v2/mcp/revoke`` immediately fails
  subsequent verification of that token, while a different token keeps
  working.
* The revoke endpoint is admin-only — a non-admin gets 403.
* Cache behaviour: a cache-hit lookup does not touch the DB; a revocation
  invalidates the snapshot so it is visible immediately.

Hermetic: SQLite engine (no Oracle), LDAP stubbed, signing key from env.
The FastAPI ``POST /api/v2/mcp/revoke`` endpoint and the
``src.mcp.auth.verify_token`` path share the process-wide revocation
cache, so a revocation issued through the endpoint is observed by a
subsequent ``verify_token`` in the same test.
"""

from __future__ import annotations

import importlib
import sys
import tempfile
import time
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

_SIGNING_KEY = "s9-4-revocation-test-key-do-not-reuse"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    """Session signing key + allowed hosts the app wants on import."""
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )
    monkeypatch.setenv("VALDO_MCP_ALLOWED_HOSTS", "testserver,localhost")
    monkeypatch.setenv("VALDO_MCP_TOKEN_SIGNING_KEY", _SIGNING_KEY)


@pytest.fixture
def revocation_db(monkeypatch) -> Iterator[Path]:
    """Temp SQLite DB with MCP_REVOKED_TOKENS; engine + cache repointed at it."""
    tmpdir = tempfile.mkdtemp(prefix="valdo_revoke_int_")
    db_path = Path(tmpdir) / "revoke.db"
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE MCP_REVOKED_TOKENS ("
            "jti TEXT PRIMARY KEY, "
            "reason TEXT, "
            "revoked_by TEXT NOT NULL, "
            "revoked_at TIMESTAMP NOT NULL)"
        ))
    engine.dispose()

    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("VALDO_SCHEMA", "")

    import src.database.engine as engine_mod
    engine_mod.reset_engine()

    import src.mcp.revocation as revocation_mod
    revocation_mod.reset_revocation_cache()

    try:
        yield db_path
    finally:
        engine_mod.reset_engine()
        revocation_mod.reset_revocation_cache()
        try:
            db_path.unlink()
        except OSError:
            pass


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


@pytest.fixture
def ldap_stub(monkeypatch):
    """Stub ldap_authenticate: alice=admin, bob=tester (non-admin)."""
    from src.api import auth_ldap as parent_ldap

    admin = parent_ldap.LdapUser(
        dn="CN=alice,OU=Users,DC=bank,DC=internal",
        email="alice@bank.internal",
        name="Alice",
        groups=("valdo-admins",),
    )
    nonadmin = parent_ldap.LdapUser(
        dn="CN=bob,OU=Users,DC=bank,DC=internal",
        email="bob@bank.internal",
        name="Bob",
        groups=("valdo-testers",),
    )

    def _stub(username, password, cfg):
        if password != "good-password":
            raise parent_ldap.LdapAuthError("invalid_credentials")
        if username == "alice":
            return admin
        if username == "bob":
            return nonadmin
        raise parent_ldap.LdapAuthError("user_not_found")

    monkeypatch.setattr(parent_ldap, "ldap_authenticate", _stub)
    return _stub


# ---------------------------------------------------------------------------
# Token format / grace window (verify_token level)
# ---------------------------------------------------------------------------


def test_minted_token_has_jti_and_verifies(revocation_db):
    """A fresh token carries a jti and verifies clean (not revoked)."""
    from src.mcp import auth as mcp_auth

    token = mcp_auth.mint_token("alice", "CN=alice,DC=bank", "admin")
    assert len(token["jti"]) == 32
    verified = mcp_auth.verify_token(token)
    assert verified.jti == token["jti"]


def _legacy_token(issued_at: int, ttl_hours: int = 12) -> dict:
    """Build a legacy EF-S7 token (no jti) signed the original 5-field way."""
    import hashlib
    import hmac

    user, dn, role = "legacy", "CN=legacy,DC=bank", "tester"
    expires_at = issued_at + ttl_hours * 3600
    sig = hmac.new(
        _SIGNING_KEY.encode("utf-8"),
        f"{user}|{dn}|{role}|{issued_at}|{expires_at}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {
        "user": user,
        "principal_dn": dn,
        "role": role,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "signature": sig,
    }


def test_legacy_token_valid_in_grace_then_rejected(revocation_db, monkeypatch):
    """A no-jti token validates before the grace cutoff and fails after it."""
    from src.mcp import auth as mcp_auth

    now = int(time.time())
    token = _legacy_token(issued_at=now)

    # Within grace (cutoff in the future) → accepted.
    monkeypatch.setenv("VALDO_MCP_JTI_GRACE_UNTIL", str(now + 3600))
    verified = mcp_auth.verify_token(token)
    assert verified.jti == ""

    # Grace elapsed (cutoff in the past) → rejected.
    monkeypatch.setenv("VALDO_MCP_JTI_GRACE_UNTIL", str(now - 1))
    with pytest.raises(mcp_auth.TokenError, match="no jti"):
        mcp_auth.verify_token(token)


# ---------------------------------------------------------------------------
# Revoke endpoint + auth-path enforcement (happy path)
# ---------------------------------------------------------------------------


def test_revoke_fails_subsequent_calls_other_tokens_unaffected(
    revocation_db, ldap_stub, monkeypatch
):
    """Revoking one jti fails that token; a different token keeps working."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")  # endpoint is on the FastAPI app
    app = _fresh_app()

    from src.api import auth_ldap as reloaded_ldap
    from src.api.routers import mcp_auth as router_mod
    from src.mcp import auth as mcp_auth

    monkeypatch.setattr(reloaded_ldap, "ldap_authenticate", ldap_stub)
    monkeypatch.setattr(router_mod, "ldap_authenticate", ldap_stub)

    # Two independent tokens.
    victim = mcp_auth.mint_token("alice", "CN=alice,DC=bank", "admin")
    bystander = mcp_auth.mint_token("carol", "CN=carol,DC=bank", "tester")

    # Both verify clean before any revocation.
    assert mcp_auth.verify_token(victim).jti == victim["jti"]
    assert mcp_auth.verify_token(bystander).jti == bystander["jti"]

    with TestClient(app) as client:
        resp = client.post(
            "/api/v2/mcp/revoke",
            json={
                "username": "alice",
                "password": "good-password",
                "token_id": victim["jti"],
                "reason": "laptop stolen INC-42",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["revoked"] is True
    assert body["token_id"] == victim["jti"]
    assert body["revoked_by"] == "CN=alice,OU=Users,DC=bank,DC=internal"

    # The revoked token now fails verification immediately (shared cache,
    # invalidated by the endpoint write).
    with pytest.raises(mcp_auth.TokenError, match="revoked"):
        mcp_auth.verify_token(victim)

    # The bystander token (different jti) is unaffected.
    assert mcp_auth.verify_token(bystander).jti == bystander["jti"]


def test_revoke_rejects_non_admin_with_403(revocation_db, ldap_stub, monkeypatch):
    """A non-admin (bob) is authenticated but forbidden — 403, not 401."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    from src.api import auth_ldap as reloaded_ldap
    from src.api.routers import mcp_auth as router_mod
    from src.mcp import auth as mcp_auth

    monkeypatch.setattr(reloaded_ldap, "ldap_authenticate", ldap_stub)
    monkeypatch.setattr(router_mod, "ldap_authenticate", ldap_stub)

    target = mcp_auth.mint_token("alice", "CN=alice,DC=bank", "admin")

    with TestClient(app) as client:
        resp = client.post(
            "/api/v2/mcp/revoke",
            json={
                "username": "bob",
                "password": "good-password",
                "token_id": target["jti"],
                "reason": "should not work",
            },
        )
    assert resp.status_code == 403, resp.text

    # The target token is NOT revoked — the forbidden call had no effect.
    assert mcp_auth.verify_token(target).jti == target["jti"]


def test_revoke_rejects_bad_credentials_with_401(revocation_db, ldap_stub, monkeypatch):
    """Wrong password → 401 (no revocation performed)."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    from src.api import auth_ldap as reloaded_ldap
    from src.api.routers import mcp_auth as router_mod

    monkeypatch.setattr(reloaded_ldap, "ldap_authenticate", ldap_stub)
    monkeypatch.setattr(router_mod, "ldap_authenticate", ldap_stub)

    with TestClient(app) as client:
        resp = client.post(
            "/api/v2/mcp/revoke",
            json={
                "username": "alice",
                "password": "wrong",
                "token_id": "deadbeef" * 4,
                "reason": "x",
            },
        )
    assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# Cache invalidation / DB-free hit path
# ---------------------------------------------------------------------------


def test_cache_hit_path_is_db_free_then_revoke_invalidates(revocation_db):
    """Repeated lookups don't reload; a revoke() makes the change visible."""
    from src.mcp.revocation import get_revocation_cache

    cache = get_revocation_cache()
    # Cold lookup loads once; subsequent in-TTL lookups do not reload.
    assert cache.is_revoked("nope") is False
    loads_after_first = cache.reload_count
    for _ in range(20):
        cache.is_revoked("nope")
    assert cache.reload_count == loads_after_first  # DB-free hits

    # Revoking through the cache persists + invalidates → immediately seen.
    cache.revoke("hot-jti", reason="incident", revoked_by="CN=alice")
    assert cache.is_revoked("hot-jti") is True

    # And it survived to the DB (a fresh cache over the same engine sees it).
    import src.mcp.revocation as revocation_mod
    revocation_mod.reset_revocation_cache()
    assert revocation_mod.get_revocation_cache().is_revoked("hot-jti") is True

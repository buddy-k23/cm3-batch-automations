"""Unit tests for MCP per-token revocation (S9-4, #389).

Covers three concerns in isolation (hermetic — SQLite + monkeypatched
env, no Oracle):

1. Token format — ``mint_token`` now emits a ``jti``; ``verify_token``
   round-trips it and still validates a legacy (no-jti) token inside the
   grace window while rejecting it once the window has elapsed.
2. :class:`~src.mcp.revocation.RevocationCache` — the cache-hit path does
   NOT touch the backing store (the <1ms / DB-free AC), TTL expiry
   triggers a reload, and a write invalidates the snapshot.
3. :class:`~src.mcp.revocation.DatabaseRevocationStore` — load/insert
   round-trip against a temp SQLite table, with parameterized SQL.

The integration-level "revoke immediately fails subsequent MCP calls"
scenario lives in ``tests/integration/test_mcp_token_revocation.py``.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterator, Set

import pytest
from sqlalchemy import create_engine, text

_SIGNING_KEY = "unit-test-signing-key-not-for-prod"


@pytest.fixture
def signing_key_env(monkeypatch):
    """Set the HMAC signing key for the duration of a test."""
    monkeypatch.setenv("VALDO_MCP_TOKEN_SIGNING_KEY", _SIGNING_KEY)


# ---------------------------------------------------------------------------
# Token format: jti present, round-trips, grace window
# ---------------------------------------------------------------------------


def test_mint_token_includes_jti(signing_key_env):
    """A freshly minted token carries a 32-char hex jti."""
    from src.mcp.auth import mint_token

    token = mint_token("jsmith", "CN=jsmith,DC=bank", "admin")
    assert "jti" in token
    assert isinstance(token["jti"], str)
    assert len(token["jti"]) == 32
    int(token["jti"], 16)  # hex-decodable


def test_two_tokens_have_distinct_jti(signing_key_env):
    """jti is random per mint — no collisions across two mints."""
    from src.mcp.auth import mint_token

    a = mint_token("u", "dn", "tester")
    b = mint_token("u", "dn", "tester")
    assert a["jti"] != b["jti"]


def test_verify_token_roundtrips_jti(signing_key_env):
    """verify_token returns the jti it was minted with."""
    from src.mcp.auth import mint_token, verify_token

    token = mint_token("jsmith", "CN=jsmith,DC=bank", "admin")
    verified = verify_token(token)
    assert verified.jti == token["jti"]


def test_tampering_with_jti_breaks_signature(signing_key_env):
    """Swapping the jti invalidates the signature (jti is signed)."""
    from src.mcp.auth import TokenError, mint_token, verify_token

    token = mint_token("jsmith", "dn", "admin")
    token["jti"] = "0" * 32
    with pytest.raises(TokenError):
        verify_token(token)


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


def test_legacy_no_jti_token_valid_within_grace(signing_key_env, monkeypatch):
    """A no-jti token issued before the grace cutoff still validates."""
    from src.mcp.auth import verify_token

    now = int(time.time())
    # Grace cutoff one hour in the future → this token (issued now) is
    # before the cutoff → accepted.
    monkeypatch.setenv("VALDO_MCP_JTI_GRACE_UNTIL", str(now + 3600))
    token = _legacy_token(issued_at=now)
    verified = verify_token(token)
    assert verified.jti == ""
    assert verified.user == "legacy"


def test_legacy_no_jti_token_rejected_after_grace(signing_key_env, monkeypatch):
    """A no-jti token issued at/after the grace cutoff is rejected."""
    from src.mcp.auth import TokenError, verify_token

    now = int(time.time())
    # Grace cutoff in the past → a token issued now is at/after the
    # cutoff → rejected, even though signature + expiry are fine.
    monkeypatch.setenv("VALDO_MCP_JTI_GRACE_UNTIL", str(now - 1))
    token = _legacy_token(issued_at=now)
    with pytest.raises(TokenError, match="no jti"):
        verify_token(token)


def test_grace_env_malformed_falls_back_safe(signing_key_env, monkeypatch):
    """A non-integer grace env var falls back to the default cutoff, not a crash."""
    from src.mcp.auth import _jti_grace_until, _DEFAULT_GRACE_UNTIL

    monkeypatch.setenv("VALDO_MCP_JTI_GRACE_UNTIL", "not-a-number")
    assert _jti_grace_until() == _DEFAULT_GRACE_UNTIL


# ---------------------------------------------------------------------------
# RevocationCache — cache-hit path is DB-free
# ---------------------------------------------------------------------------


class _FakeStore:
    """In-memory store that counts loads so tests can assert DB-free hits."""

    def __init__(self, initial: Set[str] | None = None) -> None:
        self.revoked: Set[str] = set(initial or set())
        self.load_calls = 0
        self.revoke_calls = 0

    def load_all(self) -> Set[str]:
        self.load_calls += 1
        return set(self.revoked)

    def revoke(self, jti: str, reason: str, revoked_by: str) -> None:
        self.revoke_calls += 1
        self.revoked.add(jti)


def test_cache_hit_does_not_touch_store():
    """Within the TTL, repeated lookups load the store exactly once."""
    from src.mcp.revocation import RevocationCache

    clock = {"t": 1000.0}
    store = _FakeStore({"revoked-jti"})
    cache = RevocationCache(store, ttl_seconds=60.0, clock=lambda: clock["t"])

    # First lookup: cold cache → one reload.
    assert cache.is_revoked("revoked-jti") is True
    assert store.load_calls == 1

    # Subsequent lookups inside the TTL: NO further store loads.
    for _ in range(50):
        assert cache.is_revoked("revoked-jti") is True
        assert cache.is_revoked("other-jti") is False
    assert store.load_calls == 1


def test_cache_reloads_after_ttl():
    """A lookup after the TTL elapses reloads the store and sees new state."""
    from src.mcp.revocation import RevocationCache

    clock = {"t": 1000.0}
    store = _FakeStore(set())
    cache = RevocationCache(store, ttl_seconds=60.0, clock=lambda: clock["t"])

    assert cache.is_revoked("j1") is False
    assert store.load_calls == 1

    # A new revocation lands directly in the store (simulating another
    # worker) — not yet visible until the TTL expires.
    store.revoked.add("j1")
    clock["t"] = 1030.0  # +30s, still fresh
    assert cache.is_revoked("j1") is False
    assert store.load_calls == 1

    clock["t"] = 1061.0  # +61s, expired
    assert cache.is_revoked("j1") is True
    assert store.load_calls == 2


def test_cache_revoke_invalidates_snapshot():
    """A write through the cache makes the revocation immediately visible."""
    from src.mcp.revocation import RevocationCache

    clock = {"t": 1000.0}
    store = _FakeStore(set())
    cache = RevocationCache(store, ttl_seconds=60.0, clock=lambda: clock["t"])

    assert cache.is_revoked("hot") is False  # warm the cache
    cache.revoke("hot", reason="stolen", revoked_by="admin")
    assert store.revoke_calls == 1
    # No clock advance — still within TTL — but revocation is visible
    # immediately because revoke() invalidated the snapshot.
    assert cache.is_revoked("hot") is True


def test_cache_reload_failsoft_keeps_prior_snapshot():
    """If the store raises on reload, the prior snapshot is retained."""
    from src.mcp.revocation import RevocationCache

    clock = {"t": 1000.0}

    class _FlakyStore(_FakeStore):
        def load_all(self):
            self.load_calls += 1
            if self.load_calls > 1:
                raise RuntimeError("db down")
            return {"cached"}

    store = _FlakyStore()
    cache = RevocationCache(store, ttl_seconds=60.0, clock=lambda: clock["t"])

    assert cache.is_revoked("cached") is True  # first load OK
    clock["t"] = 1100.0  # expire → reload raises → keep prior snapshot
    assert cache.is_revoked("cached") is True


# ---------------------------------------------------------------------------
# DatabaseRevocationStore — SQLite round-trip
# ---------------------------------------------------------------------------


@pytest.fixture
def sqlite_revocation_db() -> Iterator[Path]:
    """Create a temp SQLite DB with the MCP_REVOKED_TOKENS table."""
    import tempfile

    tmpdir = tempfile.mkdtemp(prefix="valdo_revoke_")
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
    try:
        yield db_path
    finally:
        try:
            db_path.unlink()
        except OSError:
            pass


def test_db_store_load_insert_roundtrip(sqlite_revocation_db):
    """revoke() persists; load_all() reads it back; duplicate is a no-op."""
    from src.mcp.revocation import DatabaseRevocationStore

    engine = create_engine(f"sqlite:///{sqlite_revocation_db}")
    store = DatabaseRevocationStore(engine=engine, schema_prefix="")
    try:
        assert store.load_all() == set()
        store.revoke("jti-a", reason="leak", revoked_by="admin")
        store.revoke("jti-b", reason="rotate", revoked_by="sre")
        assert store.load_all() == {"jti-a", "jti-b"}

        # Duplicate revoke is idempotent (no error, no second row).
        store.revoke("jti-a", reason="again", revoked_by="admin")
        assert store.load_all() == {"jti-a", "jti-b"}
    finally:
        engine.dispose()


def test_null_store_rejects_writes():
    """The null fallback store refuses to silently drop a revocation."""
    from src.mcp.revocation import _NullStore

    store = _NullStore()
    assert store.load_all() == set()
    with pytest.raises(RuntimeError, match="not available"):
        store.revoke("j", "r", "by")

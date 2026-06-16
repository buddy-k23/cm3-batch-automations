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

import importlib.util
import time
from pathlib import Path
from typing import Iterator, Set

import pytest
from sqlalchemy import create_engine, text

_HAS_FAKEREDIS = importlib.util.find_spec("fakeredis") is not None

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


# ---------------------------------------------------------------------------
# S17-2 (#420): jti surfaced on MCPPrincipal
# ---------------------------------------------------------------------------


def test_bearer_principal_carries_jti(signing_key_env):
    """A principal built from a minted+verified bearer token carries its jti."""
    from src.mcp.auth import (
        MCPPrincipal,
        _check_bearer_token,
        encode_bearer,
        mint_token,
    )

    token = mint_token("jsmith", "CN=jsmith,DC=bank", "admin")
    bearer = encode_bearer(token)

    class _Req:
        headers = {"authorization": f"Bearer {bearer}"}

    principal = _check_bearer_token(_Req())
    assert isinstance(principal, MCPPrincipal)
    assert principal.jti == token["jti"]
    assert principal.auth_kind == "token"


def test_legacy_token_principal_has_none_jti(signing_key_env, monkeypatch):
    """A legacy (no-jti) token within grace yields a principal with jti=None."""
    from src.mcp.auth import _check_bearer_token, encode_bearer

    now = int(time.time())
    monkeypatch.setenv("VALDO_MCP_JTI_GRACE_UNTIL", str(now + 3600))
    token = _legacy_token(issued_at=now)
    bearer = encode_bearer(token)

    class _Req:
        headers = {"authorization": f"Bearer {bearer}"}

    principal = _check_bearer_token(_Req())
    assert principal is not None
    assert principal.jti is None  # empty wire jti normalises to None


def test_load_stdio_user_carries_jti(signing_key_env, tmp_path):
    """The stdio principal also surfaces the token's jti."""
    from src.mcp.auth import load_stdio_user, mint_token, write_token_file

    token = mint_token("agent", "CN=agent,DC=bank", "tester")
    path = write_token_file(token, target=tmp_path / "mcp-token")
    principal = load_stdio_user(source=path)
    assert principal.jti == token["jti"]


# ---------------------------------------------------------------------------
# S17-2 (#420): cross-worker revocation propagation
# ---------------------------------------------------------------------------


class _SharedSet:
    """In-process stand-in for the optional shared (Redis) revocation set.

    Two RevocationCache instances ("workers") that share ONE _SharedSet see
    a revocation immediately — modelling the live-Redis SET that the real
    SharedRevocationStore publishes to. ``raise_on`` lets a test simulate an
    unreachable shared store to exercise the fail-soft path.
    """

    def __init__(self) -> None:
        self.members: Set[str] = set()
        self.raise_on: set[str] = set()

    def add(self, jti: str) -> None:
        if "add" in self.raise_on:
            raise ConnectionError("shared store unreachable")
        self.members.add(jti)

    def contains(self, jti: str) -> bool:
        if "contains" in self.raise_on:
            raise ConnectionError("shared store unreachable")
        return jti in self.members


def test_shared_store_propagates_revocation_immediately(sqlite_revocation_db):
    """With a shared store, a revoke on worker A is seen by worker B at once.

    Both workers have FRESH local snapshots (so a TTL reload alone would NOT
    converge), yet B rejects the jti immediately because the shared set is
    consulted before the local snapshot decides "not revoked".
    """
    from src.mcp.revocation import DatabaseRevocationStore, RevocationCache

    shared = _SharedSet()
    eng_a = create_engine(f"sqlite:///{sqlite_revocation_db}")
    eng_b = create_engine(f"sqlite:///{sqlite_revocation_db}")
    try:
        cache_a = RevocationCache(
            DatabaseRevocationStore(engine=eng_a, schema_prefix=""),
            ttl_seconds=60.0, shared_set=shared,
        )
        cache_b = RevocationCache(
            DatabaseRevocationStore(engine=eng_b, schema_prefix=""),
            ttl_seconds=60.0, shared_set=shared,
        )
        # Warm both local snapshots so neither will reload within the TTL.
        assert cache_a.is_revoked("hot") is False
        assert cache_b.is_revoked("hot") is False

        # Worker A revokes. DB is updated AND the shared set is published to.
        cache_a.revoke("hot", reason="stolen", revoked_by="admin")

        # Worker B sees it WITHOUT waiting for its TTL — via the shared set.
        assert cache_b.is_revoked("hot") is True
    finally:
        eng_a.dispose()
        eng_b.dispose()


def test_db_remains_source_of_truth_bounded_convergence(sqlite_revocation_db):
    """No shared store: DB is truth; a second worker converges within the TTL.

    Worker A revokes (DB row written). Worker B has a fresh snapshot so does
    NOT see it yet (bounded staleness); once B's TTL elapses it reloads from
    the DB — the source of truth — and rejects the jti.
    """
    from src.mcp.revocation import DatabaseRevocationStore, RevocationCache

    eng_a = create_engine(f"sqlite:///{sqlite_revocation_db}")
    eng_b = create_engine(f"sqlite:///{sqlite_revocation_db}")
    clock_b = {"t": 1000.0}
    try:
        cache_a = RevocationCache(
            DatabaseRevocationStore(engine=eng_a, schema_prefix=""),
            ttl_seconds=5.0,
        )
        cache_b = RevocationCache(
            DatabaseRevocationStore(engine=eng_b, schema_prefix=""),
            ttl_seconds=5.0, clock=lambda: clock_b["t"],
        )
        assert cache_b.is_revoked("hot") is False  # warm B's snapshot

        cache_a.revoke("hot", reason="stolen", revoked_by="admin")

        # Within B's TTL: not yet visible (bounded staleness).
        clock_b["t"] = 1003.0
        assert cache_b.is_revoked("hot") is False
        # After B's TTL: reload from the DB (source of truth) → revoked.
        clock_b["t"] = 1006.0
        assert cache_b.is_revoked("hot") is True
    finally:
        eng_a.dispose()
        eng_b.dispose()


def test_shared_store_unreachable_fails_soft_to_db(sqlite_revocation_db):
    """A broken shared set degrades to DB+local — no crash, still correct."""
    from src.mcp.revocation import DatabaseRevocationStore, RevocationCache

    shared = _SharedSet()
    shared.raise_on = {"add", "contains"}
    eng = create_engine(f"sqlite:///{sqlite_revocation_db}")
    try:
        cache = RevocationCache(
            DatabaseRevocationStore(engine=eng, schema_prefix=""),
            ttl_seconds=60.0, shared_set=shared,
        )
        # Revoke must still persist to the DB even though the shared add raises.
        cache.revoke("hot", reason="stolen", revoked_by="admin")
        # is_revoked must not crash on the shared contains() error; the local
        # snapshot (invalidated by revoke) reloads from the DB and finds it.
        assert cache.is_revoked("hot") is True
    finally:
        eng.dispose()


def test_cache_ttl_is_configurable_via_env(monkeypatch):
    """VALDO_MCP_REVOCATION_CACHE_TTL bounds convergence without a shared store."""
    from src.mcp import revocation

    monkeypatch.setenv("VALDO_MCP_REVOCATION_CACHE_TTL", "3")
    assert revocation._resolve_cache_ttl() == 3.0

    monkeypatch.setenv("VALDO_MCP_REVOCATION_CACHE_TTL", "not-a-number")
    assert revocation._resolve_cache_ttl() == revocation.DEFAULT_CACHE_TTL_SECONDS


def test_make_shared_set_defaults_to_none(monkeypatch):
    """Default (memory/db) backend → no shared set (DB-only propagation)."""
    from src.mcp import revocation

    monkeypatch.delenv("VALDO_MCP_REVOCATION_BACKEND", raising=False)
    assert revocation.make_shared_revocation_set() is None
    monkeypatch.setenv("VALDO_MCP_REVOCATION_BACKEND", "db")
    assert revocation.make_shared_revocation_set() is None


@pytest.mark.skipif(
    not _HAS_FAKEREDIS, reason="fakeredis (optional test dep) not installed"
)
def test_shared_set_against_fakeredis():
    """SharedRevocationSet add/contains round-trips against a fakeredis client."""
    import fakeredis

    from src.mcp.revocation import SharedRevocationSet

    client = fakeredis.FakeStrictRedis()
    shared = SharedRevocationSet(client=client, set_key="test:revoked")
    assert shared.contains("j1") is False
    shared.add("j1")
    assert shared.contains("j1") is True
    assert shared.contains("j2") is False

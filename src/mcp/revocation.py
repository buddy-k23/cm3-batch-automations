"""Per-token revocation blocklist for MCP bearer tokens (S9-4, #389).

EF-S7 MCP tokens are HMAC-signed and self-contained — historically the
only way to kill a leaked token was rotating
``VALDO_MCP_TOKEN_SIGNING_KEY``, invalidating *every* outstanding token.
S9-4 adds a per-token blocklist keyed on the token's opaque ``jti``
(see :func:`src.mcp.auth.mint_token`), so a single compromised token can
be revoked without disrupting the fleet.

Architecture
------------
The blocklist of record lives in the Oracle table ``MCP_REVOKED_TOKENS``
(Alembic ``0005``). The auth hot path (:func:`src.mcp.auth.verify_token`)
must check the blocklist on **every** request, so a naive
"SELECT on every verify" would add a DB round-trip to every MCP call.
Instead the auth path consults an **in-memory cache** with a 60s TTL:

* :class:`RevocationCache` holds the full set of revoked ``jti`` values
  plus a monotonic "loaded at" timestamp behind a lock.
* A lookup within the TTL is answered from the in-memory set — **no DB
  round-trip**, sub-millisecond (AC: blocklist lookups <1ms).
* A lookup after the TTL expires reloads the full set from the table
  (one ``SELECT jti`` query) and refreshes the timestamp.
* A successful :func:`RevocationStore.revoke` write **invalidates** the
  cache so the writing process sees the revocation immediately; other
  processes/workers converge within the TTL (documented in
  ``docs/PRODUCTION_DEPLOYMENT.md``).

Fail-soft posture
-----------------
The blocklist is a *defence-in-depth* layer on top of signature + expiry,
not the primary gate. If the database is unreachable at lookup time the
store logs a WARNING and reports "not revoked" rather than failing the
request closed — an infra blip must not lock every agent out (signature
and expiry still apply). A revoked ``jti`` already in the cache stays
revoked across a transient DB outage. This mirrors the "never refuse to
boot" posture of :mod:`src.mcp.run_registry`.

Security note
-------------
All SQL is parameterized (bound params) — never f-string-interpolated
values (Architecture Principle: SQL-injection prevention). The table
*name* is a fixed module constant resolved through the schema prefix; the
``jti``/``reason``/``revoked_by`` values are always bound parameters.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Set

logger = logging.getLogger(__name__)

__all__ = [
    "RevocationStore",
    "DatabaseRevocationStore",
    "RevocationCache",
    "make_revocation_store",
    "get_revocation_cache",
    "reset_revocation_cache",
]


# Default cache TTL — how long a loaded blocklist snapshot is trusted
# before the next lookup reloads it from the table. 60s per the issue AC.
DEFAULT_CACHE_TTL_SECONDS = 60.0

# Fixed table name (issue spec). Never interpolated with caller data.
_TABLE_NAME = "MCP_REVOKED_TOKENS"


def _qualified(schema_prefix: str, table: str) -> str:
    """Return ``schema.table`` when the schema prefix is non-empty.

    SQLite has no schema concept; the prefix is the empty string and we
    return the bare table name. The table name is a trusted module
    constant, never caller-supplied.

    Args:
        schema_prefix: Output of ``get_schema_prefix`` (e.g. ``"APP_INT."``
            or ``""``).
        table: Unqualified table name (a fixed constant).

    Returns:
        The qualified identifier suitable for the SQL FROM clause.
    """
    clean = schema_prefix.rstrip(".")
    return f"{clean}.{table}" if clean else table


# ---------------------------------------------------------------------------
# Store protocol + database implementation
# ---------------------------------------------------------------------------


class RevocationStore(Protocol):
    """Adapter contract for the revocation blocklist of record.

    The surface is intentionally tiny — load the full set, and insert a
    revocation. The auth path never calls the store directly; it goes
    through :class:`RevocationCache`, which calls :meth:`load_all` on a
    cache miss.
    """

    def load_all(self) -> Set[str]:
        """Return the full set of currently-revoked ``jti`` values."""

    def revoke(self, jti: str, reason: str, revoked_by: str) -> None:
        """Insert (or no-op on duplicate) a revocation row for *jti*."""


class DatabaseRevocationStore:
    """Blocklist store backed by ``MCP_REVOKED_TOKENS``.

    Uses the shared SQLAlchemy engine from :mod:`src.database.engine` so
    the same pool + DSN resolution powering run history / baselines is
    reused. Cross-dialect (Oracle / PostgreSQL / SQLite). All values are
    bound parameters.
    """

    def __init__(self, engine: Any, schema_prefix: str = "") -> None:
        """Construct a database-backed revocation store.

        Args:
            engine: SQLAlchemy ``Engine`` instance.
            schema_prefix: Schema prefix from ``get_schema_prefix`` (may
                be empty for SQLite).
        """
        self._engine = engine
        self._table = _qualified(schema_prefix, _TABLE_NAME)

    def _probe(self) -> None:
        """Verify the backing table exists (cross-dialect SELECT probe).

        Raises:
            Exception: Any SQLAlchemy / driver error. Caller catches.
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            conn.execute(text(f"SELECT 1 FROM {self._table} WHERE 1=0"))

    def load_all(self) -> Set[str]:
        """Return the full set of revoked ``jti`` values.

        One ``SELECT jti FROM <table>`` — the blocklist is expected to be
        small (a handful of incident-response entries), so loading it
        whole into a Python ``set`` keeps subsequent membership tests
        O(1) and DB-free for the cache TTL.

        Returns:
            Set of revoked ``jti`` strings (possibly empty).

        Raises:
            Exception: Propagated to the cache layer, which treats a load
                failure as fail-soft (keeps the prior snapshot / empty).
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            rows = conn.execute(text(f"SELECT jti FROM {self._table}")).fetchall()
        return {str(row[0]) for row in rows}

    def revoke(self, jti: str, reason: str, revoked_by: str) -> None:
        """Insert a revocation row for *jti*; idempotent on duplicates.

        If *jti* is already revoked the insert is skipped (a second
        revocation of the same token is a no-op, not an error). All three
        values are bound parameters.

        Args:
            jti: The opaque token id to revoke.
            reason: Operator-supplied free-text reason (audit value).
            revoked_by: Principal issuing the revocation (DN or short user).

        Raises:
            Exception: Propagated on any DB error so the caller (endpoint /
                CLI) can surface a failure — a silently-dropped revocation
                would be a security hole.
        """
        from sqlalchemy import text

        with self._engine.begin() as conn:
            existing = conn.execute(
                text(f"SELECT jti FROM {self._table} WHERE jti = :jti"),
                {"jti": jti},
            ).fetchone()
            if existing is not None:
                return
            conn.execute(
                text(
                    f"INSERT INTO {self._table} "
                    "(jti, reason, revoked_by, revoked_at) "
                    "VALUES (:jti, :reason, :revoked_by, :revoked_at)"
                ),
                {
                    "jti": jti,
                    "reason": reason,
                    "revoked_by": revoked_by,
                    "revoked_at": datetime.now(timezone.utc),
                },
            )


# ---------------------------------------------------------------------------
# In-memory TTL cache over the store
# ---------------------------------------------------------------------------


class RevocationCache:
    """In-memory, TTL-bounded cache of the revocation blocklist.

    Wraps a :class:`RevocationStore`. The auth hot path calls
    :meth:`is_revoked` on every token verification; within the TTL that
    call is answered from an in-process ``set`` with no DB round-trip
    (sub-millisecond — the <1ms AC). After the TTL the next lookup
    reloads the full set from the store.

    Thread-safe — FastMCP and FastAPI both dispatch from threadpools.
    """

    def __init__(
        self,
        store: RevocationStore,
        ttl_seconds: float = DEFAULT_CACHE_TTL_SECONDS,
        clock=time.monotonic,
    ) -> None:
        """Construct a TTL cache over *store*.

        Args:
            store: The backing :class:`RevocationStore`.
            ttl_seconds: How long a loaded snapshot is trusted before the
                next lookup reloads it. Defaults to
                :data:`DEFAULT_CACHE_TTL_SECONDS`.
            clock: Monotonic clock callable, injectable for deterministic
                tests. Production uses ``time.monotonic``.
        """
        self._store = store
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._revoked: Set[str] = set()
        self._loaded_at: Optional[float] = None
        # Counts DB reloads — lets tests assert the cache-hit path does
        # NOT touch the store (AC: assert cache hit doesn't hit the DB).
        self.reload_count = 0

    def _is_fresh(self, now: float) -> bool:
        """Return True if the current snapshot is within the TTL."""
        return self._loaded_at is not None and (now - self._loaded_at) < self._ttl

    def _reload_locked(self, now: float) -> None:
        """Reload the blocklist from the store. Caller holds the lock.

        Fail-soft: if the store raises (DB unreachable), keep the prior
        snapshot and refresh the timestamp so we do not hammer a dead DB
        on every request — a WARNING is logged. A previously-cached
        revoked jti therefore stays revoked across a transient outage.
        """
        try:
            self._revoked = self._store.load_all()
            self.reload_count += 1
        except Exception as exc:  # noqa: BLE001 — fail-soft, never break auth.
            logger.warning(
                "MCP revocation cache reload failed; retaining prior "
                "snapshot (%d entries). Underlying error: %s",
                len(self._revoked),
                exc,
            )
        self._loaded_at = now

    def is_revoked(self, jti: str) -> bool:
        """Return True if *jti* is on the blocklist.

        Answered from the in-memory snapshot when fresh (no DB); reloads
        from the store on a cold/expired cache.

        Args:
            jti: The opaque token id to check.

        Returns:
            True if the jti is revoked, False otherwise.
        """
        now = self._clock()
        with self._lock:
            if not self._is_fresh(now):
                self._reload_locked(now)
            return jti in self._revoked

    def revoke(self, jti: str, reason: str, revoked_by: str) -> None:
        """Persist a revocation and invalidate the local snapshot.

        After the store write succeeds, the local snapshot is marked
        stale so the next :meth:`is_revoked` reloads — the writing
        process sees its own revocation immediately. Other processes
        converge within the TTL.

        Args:
            jti: The opaque token id to revoke.
            reason: Operator-supplied reason (audit).
            revoked_by: Principal issuing the revocation.

        Raises:
            Exception: Propagated from the store on a DB write failure.
        """
        self._store.revoke(jti, reason, revoked_by)
        with self._lock:
            self._revoked.add(jti)
            # Force a reload on next lookup so any concurrently-added
            # revocations in other rows are also picked up promptly.
            self._loaded_at = None

    def invalidate(self) -> None:
        """Mark the snapshot stale so the next lookup reloads. Test aid."""
        with self._lock:
            self._loaded_at = None


# ---------------------------------------------------------------------------
# Factory + process-wide singleton
# ---------------------------------------------------------------------------


def make_revocation_store() -> Optional[RevocationStore]:
    """Build a :class:`DatabaseRevocationStore`, or ``None`` if unavailable.

    Resolution mirrors :func:`src.mcp.run_registry.make_run_registry`:
    construct the store against the shared engine, probe the table, and
    on any failure (missing DSN, missing table, auth error) log a WARNING
    and return ``None``. The cache treats a ``None`` store as an empty,
    DB-free blocklist — so a server with no migration applied still boots
    and authenticates (revocation is simply a no-op until the table
    exists). This is the same fail-soft posture as the run registry.

    Returns:
        A probed :class:`DatabaseRevocationStore`, or ``None``.
    """
    try:
        from src.database.engine import get_engine
        from src.database.db_url import get_valdo_schema

        engine = get_engine()
        schema = get_valdo_schema()
        schema_prefix = f"{schema}." if schema else ""
        store = DatabaseRevocationStore(engine=engine, schema_prefix=schema_prefix)
        store._probe()
    except Exception as exc:  # noqa: BLE001 — never refuse to boot/auth.
        logger.warning(
            "MCP revocation store: database backend unavailable; the "
            "blocklist will be treated as empty until the "
            "MCP_REVOKED_TOKENS table is reachable. Underlying error: %s",
            exc,
        )
        return None

    logger.info(
        "MCP revocation store: using database backend at table %s",
        store._table,
    )
    return store


class _NullStore:
    """No-op store used when no database backend is available.

    Reports an empty blocklist and refuses writes with a clear error so a
    revoke attempt against an unconfigured server fails loudly (rather
    than silently dropping the revocation).
    """

    def load_all(self) -> Set[str]:
        """Return an empty blocklist."""
        return set()

    def revoke(self, jti: str, reason: str, revoked_by: str) -> None:
        """Reject the write — no backing table to persist into.

        Raises:
            RuntimeError: Always — there is no store to write to.
        """
        raise RuntimeError(
            "MCP revocation table is not available — cannot persist a "
            "revocation. Apply Alembic migration 0005 and confirm the "
            "database is reachable."
        )


_cache_lock = threading.Lock()
_cache_singleton: Optional[RevocationCache] = None


def get_revocation_cache() -> RevocationCache:
    """Return the process-wide revocation cache, building it on first use.

    Lazily constructs a :class:`RevocationCache` over the database store
    (or a null store when the DB is unavailable). The same cache instance
    is shared by the auth path and the revoke endpoint within a process so
    a revocation issued via the endpoint is visible to subsequent
    in-process token verifications immediately.

    Returns:
        The shared :class:`RevocationCache`.
    """
    global _cache_singleton
    with _cache_lock:
        if _cache_singleton is None:
            store = make_revocation_store() or _NullStore()
            _cache_singleton = RevocationCache(store)
        return _cache_singleton


def reset_revocation_cache() -> None:
    """Drop the process-wide cache singleton. Test-only.

    Forces the next :func:`get_revocation_cache` to rebuild the store +
    cache from the current environment (e.g. after a test repoints the
    engine at a fresh SQLite DB).
    """
    global _cache_singleton
    with _cache_lock:
        _cache_singleton = None

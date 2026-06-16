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
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, Set

logger = logging.getLogger(__name__)

__all__ = [
    "RevocationStore",
    "DatabaseRevocationStore",
    "SharedRevocationSet",
    "RevocationCache",
    "make_revocation_store",
    "make_shared_revocation_set",
    "get_revocation_cache",
    "reset_revocation_cache",
    "DEFAULT_CACHE_TTL_SECONDS",
    "ENV_CACHE_TTL_SECONDS",
    "ENV_REVOCATION_BACKEND",
    "ENV_REVOCATION_REDIS_URL",
]


# Default cache TTL — how long a loaded blocklist snapshot is trusted
# before the next lookup reloads it from the table. 60s per the original
# S9-4 AC. S17-2 (#420) makes this configurable so an operator running
# multiple workers WITHOUT a shared store can tighten the bound on how long
# a revocation takes to propagate to every worker (each worker converges
# within its own TTL of the DB, the source of truth).
DEFAULT_CACHE_TTL_SECONDS = 60.0
ENV_CACHE_TTL_SECONDS = "VALDO_MCP_REVOCATION_CACHE_TTL"

# S17-2 (#420): optional shared revocation backend. Under multiple gunicorn
# workers the per-process cache means a revoked jti lingers up to one TTL
# per worker. Selecting ``redis`` publishes each revocation to a shared
# Redis SET that EVERY worker consults before trusting its local snapshot,
# so a revocation is visible fleet-wide within seconds (effectively
# immediately) rather than a per-worker TTL. ``memory``/``db`` (the
# default) keeps the DB-as-truth + per-process TTL behaviour. Redis stays
# an OPTIONAL dependency, imported lazily, fail-soft on unavailability.
ENV_REVOCATION_BACKEND = "VALDO_MCP_REVOCATION_BACKEND"
DEFAULT_REVOCATION_BACKEND = "memory"
ENV_REVOCATION_REDIS_URL = "VALDO_MCP_REVOCATION_REDIS_URL"
DEFAULT_REVOCATION_REDIS_URL = "redis://localhost:6379/0"

# Redis SET key under which revoked jtis are published/queried.
_SHARED_SET_KEY = "valdo:mcp:revoked_jti"

# Fixed table name (issue spec). Never interpolated with caller data.
_TABLE_NAME = "MCP_REVOKED_TOKENS"


def _resolve_cache_ttl() -> float:
    """Resolve the revocation cache TTL (seconds) from the environment.

    Reads :data:`ENV_CACHE_TTL_SECONDS` fresh; a missing, empty,
    non-numeric, or non-positive value falls back to
    :data:`DEFAULT_CACHE_TTL_SECONDS` (a malformed value must not silently
    disable the bound). A shorter TTL tightens cross-worker convergence at
    the cost of more frequent (still cheap, one ``SELECT jti``) reloads.

    Returns:
        The TTL in seconds as a float.
    """
    raw = os.environ.get(ENV_CACHE_TTL_SECONDS)
    if raw is None:
        return DEFAULT_CACHE_TTL_SECONDS
    try:
        value = float(raw.strip())
    except (ValueError, AttributeError):
        return DEFAULT_CACHE_TTL_SECONDS
    return value if value > 0 else DEFAULT_CACHE_TTL_SECONDS


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
# Optional shared (Redis) revocation set — cross-worker propagation (S17-2)
# ---------------------------------------------------------------------------


class SharedRevocationSet:
    """Redis-backed shared set of revoked jtis for cross-worker propagation.

    The DB table (:class:`DatabaseRevocationStore`) remains the **source of
    truth**; this is a fast shared *signal* so a revocation published by one
    gunicorn worker is visible to every other worker within seconds instead
    of a full per-process cache TTL. Each :meth:`add` does ``SADD`` on one
    Redis SET; each :meth:`contains` does ``SISMEMBER`` — both O(1).

    Reuses the S17-1 optional-Redis pattern: the ``redis`` package is
    imported **lazily** by :meth:`from_url`; this class itself takes an
    already-constructed client (or a test fake), so importing this module
    never requires ``redis``.

    Callers (:class:`RevocationCache`) wrap every method in fail-soft
    handling — an unreachable shared set must degrade to DB+local, never
    break auth. The signature gate in :func:`src.mcp.auth.verify_token`
    runs BEFORE any shared lookup, so an unauthenticated caller never
    reaches Redis.
    """

    def __init__(self, client: Any, set_key: str = _SHARED_SET_KEY) -> None:
        """Wrap an existing redis-py-compatible *client*.

        Args:
            client: A client exposing ``sadd`` / ``sismember``. A
                ``fakeredis`` client or a small fake satisfies this.
            set_key: The Redis SET key to publish/query. A fixed module
                constant by default; never caller-interpolated with secrets.
        """
        self._client = client
        self._key = set_key

    @classmethod
    def from_url(
        cls,
        url: str,
        connector: Optional[Any] = None,
    ) -> "SharedRevocationSet":
        """Build a shared set from a Redis URL, importing ``redis`` lazily.

        Args:
            url: Redis connection URL (e.g. ``redis://host:6379/0``).
            connector: Optional ``(url) -> client`` factory used in tests to
                inject a fake without importing ``redis``. Defaults to
                ``redis.Redis.from_url`` with a connection ping (fail fast at
                boot rather than on the first revocation).

        Returns:
            A connected :class:`SharedRevocationSet`.

        Raises:
            Exception: Any import/connection error is propagated so the
                factory can fail-soft to DB-only with a visible warning.
        """
        if connector is not None:
            client = connector(url)
        else:  # pragma: no cover - requires the optional redis package
            import redis  # lazy: optional dependency

            client = redis.Redis.from_url(url)
            client.ping()
        return cls(client=client)

    def add(self, jti: str) -> None:
        """Publish *jti* to the shared set (``SADD``). May raise on outage."""
        self._client.sadd(self._key, jti)

    def contains(self, jti: str) -> bool:
        """Return True if *jti* is in the shared set (``SISMEMBER``)."""
        return bool(self._client.sismember(self._key, jti))


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
        shared_set: Optional[Any] = None,
    ) -> None:
        """Construct a TTL cache over *store*.

        Args:
            store: The backing :class:`RevocationStore` (the source of
                truth, e.g. :class:`DatabaseRevocationStore`).
            ttl_seconds: How long a loaded snapshot is trusted before the
                next lookup reloads it. Defaults to
                :data:`DEFAULT_CACHE_TTL_SECONDS`.
            clock: Monotonic clock callable, injectable for deterministic
                tests. Production uses ``time.monotonic``.
            shared_set: Optional cross-worker shared set (S17-2, #420)
                exposing ``add(jti)`` and ``contains(jti)`` — e.g. a
                :class:`SharedRevocationSet`. When present, :meth:`revoke`
                publishes to it and :meth:`is_revoked` consults it BEFORE
                trusting a "not revoked" local snapshot, so a revocation on
                one worker is visible to others within seconds rather than a
                per-worker TTL. Every shared-set call is fail-soft: an
                unreachable shared set degrades to DB+local (logged), never
                breaking auth.
        """
        self._store = store
        self._ttl = ttl_seconds
        self._clock = clock
        self._shared = shared_set
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

        Resolution order (S17-2, #420):

        1. **Shared set** (when configured) — consulted first so a
           revocation published by ANOTHER worker is seen immediately,
           without waiting for this worker's local TTL to elapse. Fail-soft:
           if the shared set is unreachable the error is logged and we fall
           through to the local snapshot (degrade to DB+local).
        2. **Local TTL snapshot** — answered from the in-memory set when
           fresh (no DB round-trip); reloads from the store (the source of
           truth) on a cold/expired cache.

        The caller (:func:`src.mcp.auth.verify_token`) has already passed
        the signature + expiry gates, so only authenticated tokens reach
        this shared/DB lookup.

        Args:
            jti: The opaque token id to check.

        Returns:
            True if the jti is revoked, False otherwise.
        """
        # 1. Shared-set fast path (cross-worker). A True is authoritative;
        # a False (or an error) just falls through to the local snapshot —
        # the DB remains the source of truth, so the shared set can only
        # ADD a positive signal, never mask a DB-recorded revocation.
        if self._shared is not None:
            try:
                if self._shared.contains(jti):
                    return True
            except Exception as exc:  # noqa: BLE001 — fail-soft to DB+local.
                logger.warning(
                    "MCP revocation shared-set lookup failed; degrading to "
                    "DB+local for jti_suffix=%s. Underlying error: %s",
                    jti[-6:],
                    exc,
                )

        # 2. Local TTL snapshot over the DB source of truth.
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
            Exception: Propagated from the store on a DB write failure (a
                silently-dropped revocation would be a security hole — the
                DB is the source of truth and MUST persist the row).
        """
        # 1. Persist to the source of truth first. If this raises, the
        # revocation has NOT taken effect and the error propagates — we do
        # not touch the shared set or local snapshot on a failed DB write.
        self._store.revoke(jti, reason, revoked_by)

        # 2. Publish to the shared set so OTHER workers see it immediately
        # (S17-2, #420). Fail-soft: a shared-set outage must not undo the
        # already-persisted DB revocation — other workers still converge
        # within their TTL via the DB. Log loudly so the degraded
        # propagation is visible.
        if self._shared is not None:
            try:
                self._shared.add(jti)
            except Exception as exc:  # noqa: BLE001 — DB row already written.
                logger.warning(
                    "MCP revocation: shared-set publish failed for "
                    "jti_suffix=%s; the DB row is persisted so other workers "
                    "still converge within their cache TTL. Error: %s",
                    jti[-6:],
                    exc,
                )

        # 3. Invalidate the local snapshot so this worker sees its own
        # revocation immediately (and picks up any concurrent rows).
        with self._lock:
            self._revoked.add(jti)
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


def make_shared_revocation_set() -> Optional["SharedRevocationSet"]:
    """Build the optional cross-worker shared set, or ``None`` (S17-2, #420).

    Returns ``None`` for the default (``memory``/``db``) backend so the
    cache runs DB-as-truth + per-process TTL. When
    :data:`ENV_REVOCATION_BACKEND` selects ``redis``, builds a
    :class:`SharedRevocationSet` from :data:`ENV_REVOCATION_REDIS_URL`
    (importing ``redis`` lazily). On any import/connection failure it logs a
    WARNING and returns ``None`` — fail-soft to DB+local, mirroring the
    S17-1 rate-limit backend posture: a security control stays UP with
    bounded (TTL) propagation rather than crashing on a missing optional
    store.

    Returns:
        A connected :class:`SharedRevocationSet`, or ``None`` to run
        DB-only.
    """
    choice = (
        os.environ.get(ENV_REVOCATION_BACKEND) or DEFAULT_REVOCATION_BACKEND
    ).strip().lower()
    if choice in ("", "memory", "in-memory", "inmemory", "db", "database", "local"):
        return None
    if choice != "redis":
        logger.warning(
            "Unknown MCP revocation backend %r (env %s); using DB-only "
            "propagation (per-process TTL).",
            choice,
            ENV_REVOCATION_BACKEND,
        )
        return None

    url = os.environ.get(ENV_REVOCATION_REDIS_URL) or DEFAULT_REVOCATION_REDIS_URL
    try:
        shared = SharedRevocationSet.from_url(url)
        logger.info(
            "MCP revocation: using shared Redis set at %s (revocations "
            "propagate across workers within seconds).",
            url,
        )
        return shared
    except Exception as exc:  # noqa: BLE001 — fail-soft to DB-only.
        logger.warning(
            "MCP revocation shared Redis set unavailable (%s); FALLING BACK "
            "to DB-only propagation. A revoked token now converges across "
            "workers within the cache TTL (%s) rather than immediately. Set "
            "%s correctly or install the optional 'redis' package to restore "
            "immediate cross-worker propagation.",
            exc,
            ENV_CACHE_TTL_SECONDS,
            ENV_REVOCATION_REDIS_URL,
        )
        return None


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
            _cache_singleton = RevocationCache(
                store,
                ttl_seconds=_resolve_cache_ttl(),
                shared_set=make_shared_revocation_set(),
            )
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

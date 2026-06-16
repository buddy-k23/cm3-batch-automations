"""Persistent run registry for the MCP action-tool layer (S6-1, #386).

EF-S4 ships an in-process ``dict`` (see ``src/mcp/action_tools.py``) that
tracks every validation run started via ``validate_file``. The dict is
worker-local and lost on:

* FastAPI / gunicorn restart
* Worker rotation
* Graceful reload

Sprint 6 demo reliability hinges on the run registry surviving a restart
— a BA who started a long validation must still be able to poll
``get_run_status`` and ``get_violations`` after the operator restarts the
server. This module replaces the in-process dict with a small adapter
contract.

Two implementations are provided:

* :class:`InMemoryRunRegistry` — a thin wrapper around the legacy dict
  (with the same ``threading.Lock``). Used as a startup fallback when
  the database backend is unreachable so the server never refuses to
  boot.
* :class:`DatabaseRunRegistry` — persists records to
  ``APP_MCP_RUN_REGISTRY`` via the shared SQLAlchemy engine from
  ``src.database.engine``. Cross-dialect by design (Oracle / PostgreSQL
  / SQLite) — see :mod:`alembic.versions.0004_app_mcp_run_registry` for
  the table shape.

The factory :func:`make_run_registry` is the canonical entry point. It
attempts to construct a :class:`DatabaseRunRegistry`, runs a probe
``SELECT 1 FROM APP_MCP_RUN_REGISTRY`` to validate that the engine and
the table are both reachable, and falls back to :class:`InMemoryRunRegistry`
with a logged WARNING on any failure (missing DSN, auth fail, missing
table, etc.). This matches the EF-S4 "never refuse to boot" posture and
mirrors the fail-soft style used by :mod:`src.services.baseline_service`.

Design notes:
    * Payload is stored as a single JSON CLOB column. The MCP layer's
      view of a run record is small (status, timestamps, violation list)
      and is read whole every time, so column-per-field is unnecessary
      complexity. Searchable columns (``run_id``, ``source``,
      ``file_path``, ``status``) are duplicated outside the payload for
      the idempotency lookup in ``_existing_run``.
    * The public API mirrors the operations ``action_tools.py``
      performs today: ``put``, ``get``, ``update_status``,
      ``update_result``, ``find_inflight``. No surface beyond what the
      in-memory dict already exposes is added — this is a refactor, not
      a feature extension.
    * Concurrent access on the in-memory path uses ``threading.Lock``
      (FastMCP dispatches tool calls from a threadpool). The database
      path relies on the underlying connection's transactional
      semantics; the upsert is wrapped in a single transaction.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

logger = logging.getLogger(__name__)

__all__ = [
    "RunRecord",
    "RunRegistry",
    "InMemoryRunRegistry",
    "DatabaseRunRegistry",
    "make_run_registry",
]


# ---------------------------------------------------------------------------
# Run record — wire format between adapter + action_tools
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    """In-memory representation of a single MCP validation run.

    Mirrors the shape of the legacy ``_RunRecord`` slotted class in
    ``action_tools.py`` so the registry adapter is a near drop-in
    replacement. Stored as JSON in the database payload column.

    Attributes:
        run_id: UUID4 hex string, primary key.
        source: Canonical source name (e.g. ``"SHAW"``). Duplicated as a
            top-level column for the idempotency lookup.
        file_path: Absolute or repo-relative file path. Duplicated as a
            top-level column for the same reason.
        file_type: Resolved file-type token from the source overlay or
            ``None`` when the caller did not specify and inference failed.
        status: One of ``queued`` / ``running`` / ``completed`` / ``failed``.
        started_at: ISO-8601 UTC string captured at run construction.
        finished_at: ISO-8601 UTC string set when status reaches a
            terminal value; ``None`` otherwise.
        violations: Flat list of canonicalised violation dicts.
        error_message: Populated on ``failed`` with the exception text
            from the underlying engine call.
    """

    run_id: str
    source: str
    file_path: str
    file_type: Optional[str]
    status: str
    started_at: str
    finished_at: Optional[str] = None
    violations: List[Dict[str, Any]] = field(default_factory=list)
    error_message: Optional[str] = None

    def to_payload(self) -> str:
        """Serialise the record to a JSON string for CLOB storage.

        Returns:
            JSON-encoded record. The ``run_id`` is included redundantly
            so a reader that only has the payload can reconstruct the
            full object.
        """
        return json.dumps(
            {
                "run_id": self.run_id,
                "source": self.source,
                "file_path": self.file_path,
                "file_type": self.file_type,
                "status": self.status,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "violations": self.violations,
                "error_message": self.error_message,
            }
        )

    @classmethod
    def from_payload(cls, payload: str) -> "RunRecord":
        """Reconstruct a record from a JSON payload string.

        Args:
            payload: JSON string previously produced by :meth:`to_payload`.

        Returns:
            A fully-populated :class:`RunRecord`.

        Raises:
            ValueError: If *payload* is not valid JSON or is missing
                required fields.
        """
        data = json.loads(payload)
        return cls(
            run_id=str(data["run_id"]),
            source=str(data["source"]),
            file_path=str(data["file_path"]),
            file_type=data.get("file_type"),
            status=str(data["status"]),
            started_at=str(data["started_at"]),
            finished_at=data.get("finished_at"),
            violations=list(data.get("violations") or []),
            error_message=data.get("error_message"),
        )


# ---------------------------------------------------------------------------
# Registry protocol
# ---------------------------------------------------------------------------


_TERMINAL_STATUSES = frozenset({"completed", "failed"})


class RunRegistry(Protocol):
    """Adapter contract shared by the in-memory and database backends.

    The surface is intentionally narrow — only the operations
    ``action_tools.py`` performs today are included. Methods are
    synchronous because FastMCP's tool dispatch is itself synchronous
    (each JSON-RPC request runs in its own threadpool worker).
    """

    def put(self, record: RunRecord) -> None:
        """Insert (or overwrite) *record* under its ``run_id``."""

    def get(self, run_id: str) -> Optional[RunRecord]:
        """Return the record for *run_id*, or ``None`` if absent."""

    def find_inflight(self, source: str, file_path: str) -> Optional[RunRecord]:
        """Return any non-terminal record for ``(source, file_path)``.

        Used by the ``validate_file`` idempotency guard.
        """


# ---------------------------------------------------------------------------
# In-memory backend (always available)
# ---------------------------------------------------------------------------


class InMemoryRunRegistry:
    """Process-local registry backed by a ``dict``.

    Used as the EF-S4 fallback when no database is configured or
    reachable. Thread-safe (FastMCP dispatches from a threadpool).
    Records are lost on process exit — exactly the behaviour S6-1
    replaces, retained here only for boot resilience.
    """

    def __init__(self) -> None:
        """Initialise an empty registry with a fresh lock."""
        self._runs: Dict[str, RunRecord] = {}
        self._lock = threading.Lock()

    def put(self, record: RunRecord) -> None:
        """Store *record* by ``run_id``; replaces any prior entry.

        Args:
            record: The run record to persist.
        """
        with self._lock:
            self._runs[record.run_id] = record

    def get(self, run_id: str) -> Optional[RunRecord]:
        """Return the record for *run_id*, or ``None`` if absent.

        Args:
            run_id: Identifier to look up.

        Returns:
            The matching :class:`RunRecord`, or ``None``.
        """
        with self._lock:
            return self._runs.get(run_id)

    def find_inflight(self, source: str, file_path: str) -> Optional[RunRecord]:
        """Return an in-flight record for ``(source, file_path)``.

        Two records are considered "the same" when their source and
        file_path match exactly. Terminal records (``completed`` /
        ``failed``) are skipped so that a re-run of a previously
        completed file starts a new run.

        Args:
            source: Canonical source name.
            file_path: File path as supplied by the caller.

        Returns:
            The matching non-terminal :class:`RunRecord`, or ``None``.
        """
        with self._lock:
            for record in self._runs.values():
                if record.source != source:
                    continue
                if record.file_path != file_path:
                    continue
                if record.status in _TERMINAL_STATUSES:
                    continue
                return record
        return None

    def clear(self) -> None:
        """Drop every record. Test-only — production code MUST NOT call."""
        with self._lock:
            self._runs.clear()


# ---------------------------------------------------------------------------
# Database backend
# ---------------------------------------------------------------------------


_TABLE_NAME = "APP_MCP_RUN_REGISTRY"


def _qualified(schema_prefix: str, table: str) -> str:
    """Return ``schema.table`` when the schema prefix is non-empty.

    SQLite has no schema concept; the prefix is the empty string and we
    return the bare table name.

    Args:
        schema_prefix: Output of ``get_schema_prefix`` (e.g. ``"APP_INT."``
            or ``""``).
        table: Unqualified table name.

    Returns:
        The qualified identifier suitable for inlining into a SQL string.
    """
    clean = schema_prefix.rstrip(".")
    return f"{clean}.{table}" if clean else table


class DatabaseRunRegistry:
    """Persistent registry backed by ``APP_MCP_RUN_REGISTRY``.

    Uses the shared SQLAlchemy engine from ``src.database.engine`` so the
    same connection pool + DSN resolution that powers the rest of Valdo
    (run history, baselines, schema reconciliation) is reused.

    Concurrency:
        Reads and writes use short-lived connections from the pool, each
        wrapped in its own transaction. There is no in-process lock —
        the database itself is the synchronisation point. This is
        correct for the FastMCP threadpool dispatch model: two
        ``validate_file`` calls landing on the same ``(source,
        file_path)`` will both query ``find_inflight``, but a race here
        only causes a duplicate run start (which is harmless given the
        engine itself is idempotent on file inputs). The simpler
        connection-per-call model avoids holding a transaction open
        across a synchronous engine run.
    """

    def __init__(self, engine: Any, schema_prefix: str = "") -> None:
        """Construct a database-backed registry.

        Args:
            engine: SQLAlchemy ``Engine`` instance.
            schema_prefix: Schema prefix from ``get_schema_prefix`` (may
                be empty for SQLite).
        """
        self._engine = engine
        self._table = _qualified(schema_prefix, _TABLE_NAME)

    def _probe(self) -> None:
        """Verify the backing table exists.

        Issued by the factory at construction time so a missing table
        triggers fallback rather than a runtime failure on the first
        ``put``. We use ``SELECT 1`` with a LIMIT-style clause that
        works across dialects: a bare ``SELECT 1 FROM <table>`` returns
        zero or more rows but always succeeds when the table exists.

        Raises:
            Exception: Any SQLAlchemy / driver error. Caller catches.
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            # ``SELECT 1 FROM <table> WHERE 1=0`` is the cross-dialect
            # "table exists" probe — returns zero rows on Oracle,
            # PostgreSQL, and SQLite without scanning any data.
            conn.execute(text(f"SELECT 1 FROM {self._table} WHERE 1=0"))

    def put(self, record: RunRecord) -> None:
        """Upsert *record* into the backing table.

        SQLite supports ``INSERT OR REPLACE``; Oracle and PostgreSQL get
        a SELECT-then-INSERT-or-UPDATE strategy mirroring
        ``baseline_service._db_upsert_baseline``. All errors propagate
        — the caller (action_tools) holds the only run reference and
        must surface a failure rather than silently lose state.

        Args:
            record: The run record to persist.
        """
        from sqlalchemy import text

        adapter = os.getenv("DB_ADAPTER", "oracle").lower()
        payload = record.to_payload()
        # ``started_at`` is an ISO string in the dataclass but the
        # column is a DateTime; parse defensively so an older record
        # written by a different process still round-trips cleanly.
        started_dt = _parse_iso(record.started_at)
        finished_dt = _parse_iso(record.finished_at) if record.finished_at else None

        params = {
            "run_id": record.run_id,
            "source": record.source,
            "file_path": record.file_path,
            "status": record.status,
            "started_at": started_dt,
            "finished_at": finished_dt,
            "violation_count": len(record.violations)
            if record.status in _TERMINAL_STATUSES
            else None,
            "payload": payload,
        }

        with self._engine.begin() as conn:
            if adapter == "sqlite":
                conn.execute(
                    text(
                        f"INSERT OR REPLACE INTO {self._table} "
                        "(run_id, source, file_path, status, started_at, "
                        " finished_at, violation_count, payload) VALUES "
                        "(:run_id, :source, :file_path, :status, :started_at, "
                        " :finished_at, :violation_count, :payload)"
                    ),
                    params,
                )
                return

            existing = conn.execute(
                text(
                    f"SELECT run_id FROM {self._table} "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": record.run_id},
            ).fetchone()
            if existing is None:
                conn.execute(
                    text(
                        f"INSERT INTO {self._table} "
                        "(run_id, source, file_path, status, started_at, "
                        " finished_at, violation_count, payload) VALUES "
                        "(:run_id, :source, :file_path, :status, :started_at, "
                        " :finished_at, :violation_count, :payload)"
                    ),
                    params,
                )
            else:
                conn.execute(
                    text(
                        f"UPDATE {self._table} SET "
                        " source = :source, "
                        " file_path = :file_path, "
                        " status = :status, "
                        " started_at = :started_at, "
                        " finished_at = :finished_at, "
                        " violation_count = :violation_count, "
                        " payload = :payload "
                        "WHERE run_id = :run_id"
                    ),
                    params,
                )

    def get(self, run_id: str) -> Optional[RunRecord]:
        """Return the record for *run_id* or ``None``.

        Args:
            run_id: Identifier to look up.

        Returns:
            The matching :class:`RunRecord`, or ``None`` when the row is
            absent or the payload cannot be parsed (corrupt rows are
            treated as absent and logged at WARNING).
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT payload FROM {self._table} "
                    "WHERE run_id = :run_id"
                ),
                {"run_id": run_id},
            ).fetchone()
        if row is None:
            return None
        try:
            return RunRecord.from_payload(row[0])
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "APP_MCP_RUN_REGISTRY payload for run_id=%s is corrupt; "
                "treating as absent: %s",
                run_id,
                exc,
            )
            return None

    def find_inflight(self, source: str, file_path: str) -> Optional[RunRecord]:
        """Return an in-flight row matching ``(source, file_path)``.

        Args:
            source: Canonical source name.
            file_path: File path as supplied by the caller.

        Returns:
            The matching non-terminal :class:`RunRecord`, or ``None``.
        """
        from sqlalchemy import text

        with self._engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT payload FROM {self._table} "
                    "WHERE source = :source "
                    "  AND file_path = :file_path "
                    "  AND status NOT IN ('completed', 'failed') "
                    "ORDER BY started_at DESC"
                ),
                {"source": source, "file_path": file_path},
            ).fetchone()
        if row is None:
            return None
        try:
            return RunRecord.from_payload(row[0])
        except (ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "APP_MCP_RUN_REGISTRY inflight payload corrupt; "
                "treating as absent: %s",
                exc,
            )
            return None

    def clear(self) -> None:
        """Delete every row. Test-only — production code MUST NOT call."""
        from sqlalchemy import text

        with self._engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {self._table}"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 timestamp ending in ``Z`` into a ``datetime``.

    Args:
        value: ISO-8601 string with optional trailing ``Z``. ``None``
            short-circuits to ``None``.

    Returns:
        Timezone-aware UTC ``datetime``, or ``None`` for ``None`` input.

    Raises:
        ValueError: If *value* is non-None and not a parseable ISO-8601
            string. Callers (the upsert path) let this propagate because
            a malformed timestamp indicates a programming error, not a
            data condition we should silently swallow.
    """
    if value is None:
        return None
    text = value.rstrip("Z")
    # ``fromisoformat`` accepts microseconds and naive timestamps; we
    # attach UTC explicitly so the database driver gets a tz-aware value.
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def make_run_registry() -> RunRegistry:
    """Build the registry implementation appropriate for the environment.

    Resolution order:

    1. Try to construct a :class:`DatabaseRunRegistry` using the shared
       SQLAlchemy engine from ``src.database.engine.get_engine``.
    2. Probe the backing table with ``SELECT 1 FROM <table> WHERE 1=0``.
    3. On success, return the database registry.
    4. On any failure (engine construction, missing DSN, missing table,
       authentication error, etc.) log a WARNING and fall back to
       :class:`InMemoryRunRegistry`.

    The fallback path is non-negotiable per S6-1 AC #5 — the server must
    start cleanly even when the database is unreachable. Runs started
    against the in-memory fallback will not survive a restart, but the
    rest of the MCP surface is unaffected.

    Returns:
        The chosen :class:`RunRegistry` implementation. Always non-None.
    """
    try:
        from src.database.engine import get_engine
        from src.database.db_url import get_valdo_schema

        engine = get_engine()
        schema = get_valdo_schema()
        schema_prefix = f"{schema}." if schema else ""
        registry = DatabaseRunRegistry(engine=engine, schema_prefix=schema_prefix)
        registry._probe()
    except Exception as exc:  # noqa: BLE001 — never refuse to boot.
        logger.warning(
            "MCP run registry: database backend unavailable, falling back "
            "to in-memory store. Runs will not survive a restart. "
            "Underlying error: %s",
            exc,
        )
        return InMemoryRunRegistry()

    logger.info(
        "MCP run registry: using database backend at table %s",
        registry._table,
    )
    return registry

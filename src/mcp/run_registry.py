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
        last_heartbeat_at: ISO-8601 UTC string refreshed by the worker
            while a run is in flight (S10-1). The stuck-run reaper uses it
            to tell a live long validation apart from a dead worker. ``None``
            until the run is claimed/heartbeated.
        attempt_count: Number of times this run has been (re)queued. Started
            at 0; incremented by the reaper each time it reclaims a stuck
            ``running`` row back to ``queued`` (S10-1).
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
    last_heartbeat_at: Optional[str] = None
    attempt_count: int = 0

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
                "last_heartbeat_at": self.last_heartbeat_at,
                "attempt_count": self.attempt_count,
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
            last_heartbeat_at=data.get("last_heartbeat_at"),
            attempt_count=int(data.get("attempt_count") or 0),
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

    def claim_next(self) -> Optional[RunRecord]:
        """Atomically claim the oldest ``queued`` run and flip it to ``running``.

        Background-job primitive added by S9-5 / ADR 0021. The
        ``valdo run-job-worker`` process calls this to dequeue exactly one
        job. The transition is a *guarded* one — only a row still in the
        ``queued`` state is claimed and moved to ``running`` — so two
        concurrent workers can never claim the same row (the loser sees
        the row already ``running`` and gets either a different job or
        ``None``).

        Returns:
            The claimed :class:`RunRecord` (now in ``running`` state with
            its ``started_at`` refreshed to the claim time and an initial
            ``last_heartbeat_at`` stamped), or ``None`` when no ``queued``
            row is available.
        """

    def heartbeat(self, run_id: str) -> None:
        """Refresh ``last_heartbeat_at`` for *run_id* to "now" (S10-1).

        Called periodically by the worker while a run is in flight so the
        stuck-run reaper can distinguish a live long validation from a
        dead worker. A no-op when *run_id* is absent.
        """

    def reap_stuck(self, stale_before: datetime, now: datetime) -> int:
        """Reclaim stuck ``running`` rows back to ``queued`` (S10-1, ADR 0021).

        A ``running`` row is "stuck" when either:

        * its ``last_heartbeat_at`` is older than *stale_before*, or
        * it has no heartbeat AND its ``started_at`` is older than
          *stale_before* (a worker that died before its first heartbeat).

        Each reclaimed row is flipped back to ``queued``, its
        ``attempt_count`` is incremented, and its heartbeat is cleared.

        Args:
            stale_before: Heartbeat/started-at cutoff; rows older than this
                are reclaimed.
            now: Current time (injected for deterministic testing).

        Returns:
            The number of rows reclaimed.
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

    def claim_next(self) -> Optional[RunRecord]:
        """Atomically claim the oldest ``queued`` record (S9-5, ADR 0021).

        The ``threading.Lock`` is the synchronisation point here, mirroring
        the row-level lock the database backend relies on: the scan for the
        oldest queued row and the flip to ``running`` happen under one lock
        acquisition, so two threads cannot both claim the same record.

        "Oldest" is defined by ``started_at`` (ISO-8601 strings sort
        lexicographically in chronological order), matching the
        ``ORDER BY started_at`` the database backend uses.

        Returns:
            The claimed :class:`RunRecord` (now ``running``), or ``None``
            when no ``queued`` record exists.
        """
        with self._lock:
            queued = [r for r in self._runs.values() if r.status == "queued"]
            if not queued:
                return None
            oldest = min(queued, key=lambda r: r.started_at)
            oldest.status = "running"
            # Stamp an initial heartbeat at claim time (S10-1) so a freshly
            # claimed run is never immediately eligible for reaping.
            oldest.last_heartbeat_at = _utcnow_iso()
            return oldest

    def heartbeat(self, run_id: str) -> None:
        """Refresh ``last_heartbeat_at`` on the in-memory record (S10-1).

        Args:
            run_id: Identifier of the run to heartbeat. Absent ids are a
                no-op (the run may have already completed).
        """
        with self._lock:
            record = self._runs.get(run_id)
            if record is not None:
                record.last_heartbeat_at = _utcnow_iso()

    def reap_stuck(self, stale_before: datetime, now: datetime) -> int:
        """Reclaim stuck ``running`` records to ``queued`` (S10-1, ADR 0021).

        See :meth:`RunRegistry.reap_stuck`. Performed under the registry
        lock so the scan-and-flip is atomic with respect to ``claim_next``.

        Args:
            stale_before: Heartbeat/started-at cutoff.
            now: Current time (unused beyond symmetry with the DB backend;
                kept for an identical signature).

        Returns:
            Number of records reclaimed.
        """
        reclaimed = 0
        with self._lock:
            for record in self._runs.values():
                if record.status != "running":
                    continue
                hb = _parse_iso(record.last_heartbeat_at)
                if hb is not None:
                    stuck = hb < stale_before
                else:
                    started = _parse_iso(record.started_at)
                    stuck = started is not None and started < stale_before
                if not stuck:
                    continue
                record.status = "queued"
                record.attempt_count += 1
                record.last_heartbeat_at = None
                reclaimed += 1
        return reclaimed

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
        heartbeat_dt = (
            _parse_iso(record.last_heartbeat_at) if record.last_heartbeat_at else None
        )

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
            "last_heartbeat_at": heartbeat_dt,
            "attempt_count": record.attempt_count,
        }

        with self._engine.begin() as conn:
            if adapter == "sqlite":
                conn.execute(
                    text(
                        f"INSERT OR REPLACE INTO {self._table} "
                        "(run_id, source, file_path, status, started_at, "
                        " finished_at, violation_count, payload, "
                        " last_heartbeat_at, attempt_count) VALUES "
                        "(:run_id, :source, :file_path, :status, :started_at, "
                        " :finished_at, :violation_count, :payload, "
                        " :last_heartbeat_at, :attempt_count)"
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
                        " finished_at, violation_count, payload, "
                        " last_heartbeat_at, attempt_count) VALUES "
                        "(:run_id, :source, :file_path, :status, :started_at, "
                        " :finished_at, :violation_count, :payload, "
                        " :last_heartbeat_at, :attempt_count)"
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
                        " payload = :payload, "
                        " last_heartbeat_at = :last_heartbeat_at, "
                        " attempt_count = :attempt_count "
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

    def claim_next(self) -> Optional[RunRecord]:
        """Atomically claim the oldest ``queued`` row (S9-5, ADR 0021).

        Implements the guarded transition the ADR specifies. In ONE
        transaction:

        1. ``SELECT run_id ... WHERE status='queued' ORDER BY started_at``
           (oldest first), bounded to a single row, to pick a candidate.
        2. ``UPDATE ... SET status='running', started_at=:now
           WHERE run_id=:run_id AND status='queued'`` — the ``AND
           status='queued'`` guard is what makes the claim safe under
           concurrency: if another worker raced in and already moved the
           row to ``running``, this UPDATE matches **zero** rows and we
           return ``None`` (or, on a retry loop, would pick the next
           candidate). Relies on the database's row-level locking within
           the transaction so two workers never both win the same row.

        All SQL is parameterised — no value is ever f-string-interpolated
        (only the already-validated, schema-qualified table identifier is,
        exactly as every other method in this class does).

        Returns:
            The claimed :class:`RunRecord` in ``running`` state, or
            ``None`` when no ``queued`` row could be claimed.
        """
        from sqlalchemy import text

        # The single-row limit clause differs across dialects; Oracle <12c
        # has no LIMIT. We avoid the issue entirely by selecting ordered
        # candidates and taking the first the guarded UPDATE can win — for
        # the pilot's volume a bounded fetch is unnecessary, but we cap the
        # candidate scan to keep the round-trip small.
        now_dt = datetime.now(timezone.utc)

        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"SELECT run_id FROM {self._table} "
                    "WHERE status = 'queued' "
                    "ORDER BY started_at"
                )
            ).fetchall()

            for row in rows:
                candidate_id = row[0]
                updated = conn.execute(
                    text(
                        f"UPDATE {self._table} SET "
                        " status = 'running', "
                        " started_at = :started_at, "
                        " last_heartbeat_at = :started_at "
                        "WHERE run_id = :run_id AND status = 'queued'"
                    ),
                    {"run_id": candidate_id, "started_at": now_dt},
                )
                if updated.rowcount == 1:
                    # We won the row. Re-read the payload and reflect the
                    # running transition in the returned record.
                    payload_row = conn.execute(
                        text(
                            f"SELECT payload FROM {self._table} "
                            "WHERE run_id = :run_id"
                        ),
                        {"run_id": candidate_id},
                    ).fetchone()
                    if payload_row is None:
                        continue
                    try:
                        record = RunRecord.from_payload(payload_row[0])
                    except (ValueError, KeyError, TypeError) as exc:
                        logger.warning(
                            "APP_MCP_RUN_REGISTRY claimed payload for "
                            "run_id=%s is corrupt; skipping: %s",
                            candidate_id,
                            exc,
                        )
                        continue
                    record.status = "running"
                    # Reflect the claim time in the payload so the JSON
                    # ``started_at`` matches the searchable column we set.
                    record.started_at = (
                        now_dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
                    )
                    # Stamp the initial heartbeat (S10-1) in the payload too,
                    # matching the ``last_heartbeat_at`` column set above.
                    record.last_heartbeat_at = record.started_at
                    # Keep the persisted ``payload`` JSON consistent with
                    # the searchable ``status`` column we just flipped.
                    conn.execute(
                        text(
                            f"UPDATE {self._table} SET payload = :payload "
                            "WHERE run_id = :run_id"
                        ),
                        {"payload": record.to_payload(), "run_id": candidate_id},
                    )
                    return record

        return None

    def heartbeat(self, run_id: str) -> None:
        """Refresh ``last_heartbeat_at`` for *run_id* (S10-1, ADR 0021).

        Issues a single parameterised
        ``UPDATE ... SET last_heartbeat_at=:now WHERE run_id=:id``. Both the
        searchable column and the payload JSON are kept consistent so a
        ``get`` after a heartbeat reflects the fresh timestamp. Unknown ids
        match zero rows (a harmless no-op).

        Args:
            run_id: Identifier of the in-flight run to heartbeat.
        """
        from sqlalchemy import text

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

        with self._engine.begin() as conn:
            updated = conn.execute(
                text(
                    f"UPDATE {self._table} SET last_heartbeat_at = :now "
                    "WHERE run_id = :run_id"
                ),
                {"now": now_dt, "run_id": run_id},
            )
            if updated.rowcount != 1:
                return
            # Keep the payload JSON in step with the column.
            payload_row = conn.execute(
                text(f"SELECT payload FROM {self._table} WHERE run_id = :run_id"),
                {"run_id": run_id},
            ).fetchone()
            if payload_row is None:
                return
            try:
                record = RunRecord.from_payload(payload_row[0])
            except (ValueError, KeyError, TypeError):
                return
            record.last_heartbeat_at = now_iso
            conn.execute(
                text(
                    f"UPDATE {self._table} SET payload = :payload "
                    "WHERE run_id = :run_id"
                ),
                {"payload": record.to_payload(), "run_id": run_id},
            )

    def reap_stuck(self, stale_before: datetime, now: datetime) -> int:
        """Reclaim stuck ``running`` rows back to ``queued`` (S10-1, ADR 0021).

        Selects ``running`` rows whose ``last_heartbeat_at`` is older than
        *stale_before*, OR whose heartbeat is NULL and whose ``started_at``
        is older than *stale_before*. Each is reset to ``queued``, has its
        ``attempt_count`` incremented, and its heartbeat cleared — both in
        the searchable columns and the payload JSON. All SQL is
        parameterised (only the already-validated table identifier is
        interpolated, consistent with every other method here).

        Args:
            stale_before: Heartbeat/started-at cutoff.
            now: Current time (unused in the SQL but kept for an identical
                signature to the in-memory backend and future auditing).

        Returns:
            Number of rows reclaimed.
        """
        from sqlalchemy import text

        with self._engine.begin() as conn:
            rows = conn.execute(
                text(
                    f"SELECT run_id, payload FROM {self._table} "
                    "WHERE status = 'running' "
                    "  AND ( "
                    "        (last_heartbeat_at IS NOT NULL "
                    "         AND last_heartbeat_at < :stale_before) "
                    "     OR (last_heartbeat_at IS NULL "
                    "         AND started_at < :stale_before) "
                    "      )"
                ),
                {"stale_before": stale_before},
            ).fetchall()

            reclaimed = 0
            for run_id, payload in rows:
                try:
                    record = RunRecord.from_payload(payload)
                except (ValueError, KeyError, TypeError) as exc:
                    logger.warning(
                        "APP_MCP_RUN_REGISTRY reap payload for run_id=%s is "
                        "corrupt; skipping: %s",
                        run_id,
                        exc,
                    )
                    continue
                record.status = "queued"
                record.attempt_count += 1
                record.last_heartbeat_at = None
                conn.execute(
                    text(
                        f"UPDATE {self._table} SET "
                        " status = 'queued', "
                        " attempt_count = :attempt_count, "
                        " last_heartbeat_at = NULL, "
                        " payload = :payload "
                        "WHERE run_id = :run_id"
                    ),
                    {
                        "attempt_count": record.attempt_count,
                        "payload": record.to_payload(),
                        "run_id": run_id,
                    },
                )
                reclaimed += 1
        return reclaimed

    def clear(self) -> None:
        """Delete every row. Test-only — production code MUST NOT call."""
        from sqlalchemy import text

        with self._engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {self._table}"))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow_iso() -> str:
    """Return the current UTC time as the registry's ISO-8601 string.

    Matches the ``%Y-%m-%dT%H:%M:%S.%fZ`` format used by ``claim_next`` and
    ``action_tools`` so heartbeat timestamps sort and round-trip cleanly.

    Returns:
        ISO-8601 UTC string with a trailing ``Z``.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


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

"""Persistence for test-suite run history using SQLAlchemy Core.

Supports all three adapters — Oracle, PostgreSQL, and SQLite — through the
shared :func:`~src.database.engine.get_engine` factory.  The schema prefix
(e.g. ``APP_INT.``) is resolved by
:func:`~src.database.engine.get_schema_prefix`.

If any database call fails (connection error, table not found, etc.) the
method logs a warning and returns gracefully so callers are never broken by
an absent or misconfigured database.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import text

from src.database.engine import get_engine, get_schema_prefix

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _parse_ts(ts_str: str) -> datetime:
    """Parse ISO-8601 UTC string (with or without trailing Z) to aware datetime.

    Args:
        ts_str: UTC timestamp string, e.g. ``'2026-03-02T10:00:00.000000Z'``.

    Returns:
        Timezone-aware datetime in UTC.

    Raises:
        ValueError: If ts_str contains an explicit non-UTC offset.
    """
    if "+" in ts_str:
        raise ValueError(f"_parse_ts expects a UTC string, got: {ts_str!r}")
    return datetime.fromisoformat(ts_str.rstrip("Z")).replace(tzinfo=timezone.utc)


def _ts_to_iso(value: Any) -> Any:
    """Convert a datetime to ISO-8601 UTC string; pass non-datetime values through.

    Args:
        value: A datetime instance or any other value.

    Returns:
        ISO-8601 string ending in ``'Z'`` if value is a datetime,
        otherwise the original value unchanged.
    """
    if isinstance(value, datetime):
        ts = value.astimezone(timezone.utc)
        return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
    return value


def _qualified(schema: str, table: str) -> str:
    """Return a qualified ``schema.TABLE`` identifier.

    Accepts either a bare schema name (``'APP_INT'``) or one that already has a
    trailing dot (``'APP_INT.'``).  When *schema* is empty (SQLite) the table
    name is returned unqualified.

    Args:
        schema: Schema name, optionally with a trailing ``'.'``.
        table: Unqualified table name.

    Returns:
        ``'schema.TABLE'`` when schema is non-empty, otherwise ``'TABLE'``.
    """
    clean = schema.rstrip(".")
    return f"{clean}.{table}" if clean else table


def _sql_insert_run(schema: str) -> str:
    """Return the INSERT statement for APP_RUN_HISTORY, qualified by schema.

    Args:
        schema: Schema name (with or without trailing dot), e.g. ``'APP_INT'``
            or ``'APP_INT.'``.  Pass ``''`` for SQLite (no schema).

    Returns:
        Parameterised INSERT SQL string using ``:name`` bind params.
    """
    table = _qualified(schema, "APP_RUN_HISTORY")
    return (
        f"INSERT INTO {table} "
        "(run_id, suite_name, environment, run_timestamp, status, "
        "pass_count, fail_count, skip_count, total_count, report_url, archive_path, quality_score) "
        "VALUES "
        "(:run_id, :suite_name, :environment, :run_timestamp, :status, "
        ":pass_count, :fail_count, :skip_count, :total_count, :report_url, :archive_path, :quality_score)"
    )


def _sql_insert_test(schema: str) -> str:
    """Return the INSERT statement for APP_RUN_TESTS, qualified by schema.

    Args:
        schema: Schema name (with or without trailing dot), e.g. ``'APP_INT'``
            or ``'APP_INT.'``.  Pass ``''`` for SQLite (no schema).

    Returns:
        Parameterised INSERT SQL string using ``:name`` bind params.
    """
    table = _qualified(schema, "APP_RUN_TESTS")
    return (
        f"INSERT INTO {table} "
        "(run_id, test_name, test_type, status, row_count, error_count, duration_secs, report_path) "
        "VALUES "
        "(:run_id, :test_name, :test_type, :status, :row_count, :error_count, :duration_secs, :report_path)"
    )


def _sql_fetch_history(schema: str, limit: int = 20) -> str:
    """Return the SELECT statement for recent run history.

    Uses Oracle 12c+ FETCH FIRST syntax which is also compatible with
    PostgreSQL 8.4+ and SQLite 3.35+.

    Args:
        schema: Schema name (with or without trailing dot), e.g. ``'APP_INT'``
            or ``'APP_INT.'``.  Pass ``''`` for SQLite (no schema).
        limit: Maximum rows to return — embedded directly (not as a bind param)
            to avoid dialect quoting issues with LIMIT clauses.  Defaults to 20.

    Returns:
        SELECT SQL string.
    """
    table = _qualified(schema, "APP_RUN_HISTORY")
    return (
        f"SELECT run_id, suite_name, environment, run_timestamp, status, "
        f"pass_count, fail_count, skip_count, total_count, report_url, archive_path, quality_score "
        f"FROM {table} "
        f"ORDER BY run_timestamp DESC "
        f"FETCH FIRST {int(limit)} ROWS ONLY"
    )


# ---------------------------------------------------------------------------
# Public repository class
# ---------------------------------------------------------------------------


class RunHistoryRepository:
    """Reads and writes suite run history using SQLAlchemy Core.

    Works with Oracle, PostgreSQL, and SQLite adapters.  All DB calls are
    wrapped in try/except so the application continues even when the database
    is unavailable.

    Args:
        engine: Optional SQLAlchemy Engine.  Defaults to the shared engine
            returned by :func:`~src.database.engine.get_engine`.
        schema_prefix: Optional schema prefix override (e.g. ``'APP_INT.'``).
            Defaults to :func:`~src.database.engine.get_schema_prefix`.

    Example::

        repo = RunHistoryRepository()
        repo.insert_run(entry)
        repo.insert_tests(run_id, results)
        history = repo.fetch_history(limit=20)
    """

    def __init__(
        self,
        engine=None,
        schema_prefix: str | None = None,
    ) -> None:
        self._engine = engine if engine is not None else get_engine()
        self._schema_prefix = (
            schema_prefix if schema_prefix is not None else get_schema_prefix()
        )

    def insert_run(self, entry: dict[str, Any]) -> None:
        """Insert one run summary row into the APP_RUN_HISTORY table.

        Args:
            entry: Dict with keys matching run_history.json entries:
                ``run_id``, ``suite_name``, ``environment``, ``timestamp``
                (ISO-8601 UTC string), ``status``, ``pass_count``,
                ``fail_count``, ``skip_count``, ``total_count``,
                ``report_url``, ``archive_path``, ``quality_score`` (optional float).
        """
        params = {
            "run_id": entry["run_id"],
            "suite_name": entry["suite_name"],
            "environment": entry["environment"],
            "run_timestamp": _parse_ts(entry["timestamp"]),
            "status": entry["status"],
            "pass_count": entry.get("pass_count", 0),
            "fail_count": entry.get("fail_count", 0),
            "skip_count": entry.get("skip_count", 0),
            "total_count": entry.get("total_count", 0),
            "report_url": entry.get("report_url", ""),
            "archive_path": entry.get("archive_path", ""),
            "quality_score": entry.get("quality_score"),
        }
        sql = text(_sql_insert_run(self._schema_prefix))
        try:
            with self._engine.connect() as conn:
                conn.execute(sql, params)
                conn.commit()
        except Exception as exc:
            logger.warning("insert_run failed — DB may not be configured: %s", exc)

    def insert_tests(self, run_id: str, results: list[dict[str, Any]]) -> None:
        """Insert one row per test into the APP_RUN_TESTS table.

        Args:
            run_id: The parent run UUID.
            results: List of per-test result dicts from ``_run_single_test``.
                Expected keys: ``name``, ``type``, ``status``,
                ``rows_processed``, ``error_count``, ``duration_secs``,
                ``report_path``.
        """
        if not results:
            return

        sql = text(_sql_insert_test(self._schema_prefix))
        try:
            with self._engine.connect() as conn:
                for r in results:
                    params = {
                        "run_id": run_id,
                        "test_name": r.get("name", ""),
                        "test_type": r.get("type", ""),
                        "status": r.get("status", ""),
                        "row_count": r.get("rows_processed"),
                        "error_count": r.get("error_count", 0),
                        "duration_secs": r.get("duration_secs"),
                        "report_path": r.get("report_path", ""),
                    }
                    conn.execute(sql, params)
                conn.commit()
        except Exception as exc:
            logger.warning("insert_tests failed — DB may not be configured: %s", exc)

    def fetch_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recent run summaries from APP_RUN_HISTORY.

        Args:
            limit: Maximum number of rows to return (default 20).

        Returns:
            List of dicts with the same keys as run_history.json entries,
            ordered newest-first.  Returns ``[]`` if the DB is unavailable.
        """
        sql = text(_sql_fetch_history(self._schema_prefix, limit))
        try:
            with self._engine.connect() as conn:
                result = conn.execute(sql)
                rows = list(result)
        except Exception as exc:
            logger.warning("fetch_history failed — DB may not be configured: %s", exc)
            return []

        output = []
        for row in rows:
            d = dict(row._mapping)
            # Rename run_timestamp → timestamp and convert datetime → ISO string
            if "run_timestamp" in d:
                d["timestamp"] = _ts_to_iso(d.pop("run_timestamp"))
            output.append(d)
        return output

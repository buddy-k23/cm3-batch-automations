"""JSON-backed rolling baseline store for per-suite quality metrics.

Baselines are persisted to ``reports/baselines.json`` using the same
read-modify-write pattern as ``reports/run_history.json``.  Each suite
maintains a rolling window of its last 10 runs; averages are recomputed
on every call to :func:`update_baseline`.

When the ``DB_ADAPTER`` environment variable is set, each public function
attempts a database path first (UPSERT / SELECT against the ``CM3_BASELINES``
table) and falls back to JSON on any exception.

Storage format (``reports/baselines.json``)::

    {
        "SUITE_A": {
            "baseline": {
                "suite_name": "SUITE_A",
                "pass_rate": 87.5,
                "avg_quality_score": 91.2,
                "avg_error_rate": 2.3,
                "sample_size": 10,
                "updated_at": "2026-03-30T14:00:00"
            },
            "history": [
                {"pass_rate": 80.0, "quality_score": 90.0, "error_rate": 1.0},
                ...
            ]
        },
        ...
    }
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_BASELINES_PATH: Path = Path(__file__).parent.parent.parent / "reports" / "baselines.json"
"""Absolute path to the JSON file that stores all suite baselines and history."""

_ROLLING_WINDOW: int = 10
"""Maximum number of historical runs to keep per suite when computing averages."""


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _load_store(path: Path) -> dict[str, Any]:
    """Read the baselines JSON file from disk.

    Returns an empty dict when the file does not exist or contains corrupt JSON.
    The corrupt-JSON case is logged as a warning and treated as a fresh store
    (consistent with the run_history pattern).

    Args:
        path: Path to the baselines JSON file.

    Returns:
        Dict mapping suite_name to its ``{"baseline": ..., "history": [...]}`` record.
    """
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("baselines.json is unreadable, starting fresh: %s", exc)
        return {}


def _save_store(path: Path, store: dict[str, Any]) -> None:
    """Write the baselines store dict to disk as formatted JSON.

    Creates parent directories as needed.

    Args:
        path: Path to the baselines JSON file.
        store: Full in-memory store dict to persist.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(store, indent=2), encoding="utf-8")


def _compute_error_rate(invalid_rows: int, total_rows: int) -> float:
    """Compute per-run error rate as a percentage.

    Args:
        invalid_rows: Number of rows that failed validation.
        total_rows: Total rows processed.

    Returns:
        ``invalid_rows / total_rows * 100``, or ``0.0`` when ``total_rows`` is zero.
    """
    if total_rows == 0:
        return 0.0
    return invalid_rows / total_rows * 100.0


def _average_or_none(values: list[float]) -> Optional[float]:
    """Return the arithmetic mean of *values*, or ``None`` for an empty list.

    Args:
        values: Non-empty list of floats to average.

    Returns:
        Mean value, or ``None`` if *values* is empty.
    """
    if not values:
        return None
    return sum(values) / len(values)


def _recompute_baseline(suite_name: str, history: list[dict[str, Any]]) -> dict[str, Any]:
    """Recompute the baseline summary from the rolling history window.

    Args:
        suite_name: Name of the suite.
        history: List of per-run snapshot dicts with keys ``pass_rate``,
            ``quality_score`` (optional), and ``error_rate``.

    Returns:
        Baseline dict with keys: suite_name, pass_rate, avg_quality_score,
        avg_error_rate, sample_size, updated_at.
    """
    pass_rates = [h["pass_rate"] for h in history]
    quality_scores = [h["quality_score"] for h in history if h.get("quality_score") is not None]
    error_rates = [h["error_rate"] for h in history]

    return {
        "suite_name": suite_name,
        "pass_rate": _average_or_none(pass_rates),
        "avg_quality_score": _average_or_none(quality_scores),
        "avg_error_rate": _average_or_none(error_rates) or 0.0,
        "sample_size": len(history),
        "updated_at": datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# DB helpers (used when DB_ADAPTER env var is set)
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS CM3_BASELINES (
    suite_name        VARCHAR(200) PRIMARY KEY,
    pass_rate         REAL,
    avg_quality_score REAL,
    avg_error_rate    REAL,
    sample_size       INTEGER NOT NULL DEFAULT 0,
    updated_at        TEXT
)
"""


def _get_schema_prefix() -> str:
    """Return the schema prefix string including trailing dot, or empty string.

    Reads ``ORACLE_SCHEMA`` (falling back to ``ORACLE_USER``, then empty) and
    returns the value with a trailing ``.`` so it can be prepended directly to
    a table name.

    Returns:
        Schema prefix such as ``"CM3INT."`` or ``""`` when not configured.
    """
    schema = os.getenv("ORACLE_SCHEMA") or os.getenv("ORACLE_USER") or ""
    return f"{schema}." if schema else ""


def _ensure_baselines_table(connection: Any) -> None:
    """Create the CM3_BASELINES table if it does not already exist.

    Uses a dialect-agnostic ``CREATE TABLE IF NOT EXISTS`` statement that is
    compatible with SQLite and PostgreSQL.  For Oracle, the Alembic migration
    ``0003_cm3_baselines.py`` is the authoritative table-creation path.

    Args:
        connection: A raw DBAPI connection (e.g. ``sqlite3.Connection``).
    """
    try:
        connection.execute(_CREATE_TABLE_SQL)
        connection.commit()
    except Exception:  # noqa: BLE001 — best-effort; Oracle falls through to Alembic
        pass


def _update_baseline_db(suite_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Compute a single-run baseline from *result* and UPSERT it into CM3_BASELINES.

    Derives pass_rate, error_rate, and quality_score from the run result dict,
    then UPSERTs the row using ``INSERT OR REPLACE`` (SQLite) with a fallback
    DELETE + INSERT for Oracle/PostgreSQL.  Both paths commit the transaction.

    The CM3_BASELINES table stores the latest single-run snapshot per suite
    in the DB path; the rolling-window average is maintained separately in
    ``baselines.json`` by the JSON path.

    Args:
        suite_name: Primary key — logical name of the test suite.
        result: Run result dict.  Expected keys (all optional):
            ``pass_count``, ``total_count``, ``invalid_rows``,
            ``total_rows``, ``quality_score``.

    Returns:
        The computed single-run baseline dict (same shape as the JSON path).

    Raises:
        Exception: Propagates any DB exception so the caller can apply the
            JSON fallback.
    """
    from src.database.adapters.factory import get_database_adapter

    # Compute single-run metrics from the raw result dict
    pass_count = result.get("pass_count", 0)
    total_count = result.get("total_count", 0)
    pass_rate = (pass_count / total_count * 100.0) if total_count > 0 else 0.0

    invalid_rows = result.get("invalid_rows", 0)
    total_rows = result.get("total_rows", 0)
    error_rate = _compute_error_rate(invalid_rows, total_rows)
    quality_score = result.get("quality_score")
    updated_at = datetime.utcnow().isoformat()

    baseline_record: dict[str, Any] = {
        "suite_name": suite_name,
        "pass_rate": pass_rate,
        "avg_quality_score": quality_score,
        "avg_error_rate": error_rate,
        "sample_size": 1,
        "updated_at": updated_at,
    }

    adapter = get_database_adapter()
    adapter.connect()
    try:
        conn = adapter._connection  # type: ignore[attr-defined]
        _ensure_baselines_table(conn)

        prefix = _get_schema_prefix()
        table = f"{prefix}CM3_BASELINES"

        params = {
            "suite_name": suite_name,
            "pass_rate": pass_rate,
            "avg_quality_score": quality_score,
            "avg_error_rate": error_rate,
            "sample_size": 1,
            "updated_at": updated_at,
        }

        try:
            # SQLite-compatible UPSERT
            conn.execute(
                f"INSERT OR REPLACE INTO {table}"
                " (suite_name, pass_rate, avg_quality_score,"
                "  avg_error_rate, sample_size, updated_at)"
                " VALUES (:suite_name, :pass_rate, :avg_quality_score,"
                "         :avg_error_rate, :sample_size, :updated_at)",
                params,
            )
        except Exception:  # noqa: BLE001
            # Fallback for Oracle / PostgreSQL: DELETE then INSERT
            conn.execute(
                f"DELETE FROM {table} WHERE suite_name = :suite_name",
                {"suite_name": suite_name},
            )
            conn.execute(
                f"INSERT INTO {table}"
                " (suite_name, pass_rate, avg_quality_score,"
                "  avg_error_rate, sample_size, updated_at)"
                " VALUES (:suite_name, :pass_rate, :avg_quality_score,"
                "         :avg_error_rate, :sample_size, :updated_at)",
                params,
            )
        conn.commit()
    finally:
        adapter.disconnect()

    return baseline_record


def _get_baseline_db(suite_name: str) -> Optional[dict[str, Any]]:
    """Fetch a single baseline row from the CM3_BASELINES table.

    Args:
        suite_name: Suite name to look up (primary key).

    Returns:
        Baseline dict, or ``None`` when the row does not exist.

    Raises:
        Exception: Propagates any DB exception so the caller can apply the
            JSON fallback.
    """
    from src.database.adapters.factory import get_database_adapter

    adapter = get_database_adapter()
    adapter.connect()
    try:
        conn = adapter._connection  # type: ignore[attr-defined]
        _ensure_baselines_table(conn)

        prefix = _get_schema_prefix()
        table = f"{prefix}CM3_BASELINES"

        cursor = conn.execute(
            f"SELECT suite_name, pass_rate, avg_quality_score,"
            f"       avg_error_rate, sample_size, updated_at"
            f" FROM {table}"
            f" WHERE suite_name = :suite_name",
            {"suite_name": suite_name},
        )
        row = cursor.fetchone()
    finally:
        adapter.disconnect()

    if row is None:
        return None

    # Support both sqlite3.Row (dict-like) and plain tuple
    try:
        return {
            "suite_name": row["suite_name"],
            "pass_rate": row["pass_rate"],
            "avg_quality_score": row["avg_quality_score"],
            "avg_error_rate": row["avg_error_rate"],
            "sample_size": row["sample_size"],
            "updated_at": row["updated_at"],
        }
    except TypeError:
        cols = [
            "suite_name", "pass_rate", "avg_quality_score",
            "avg_error_rate", "sample_size", "updated_at",
        ]
        return dict(zip(cols, row))


def _list_baselines_db() -> list[dict[str, Any]]:
    """Return all baseline rows from CM3_BASELINES sorted by suite_name.

    Returns:
        List of baseline dicts sorted alphabetically by ``suite_name``.
        Empty list when the table is empty.

    Raises:
        Exception: Propagates any DB exception so the caller can apply the
            JSON fallback.
    """
    from src.database.adapters.factory import get_database_adapter

    adapter = get_database_adapter()
    adapter.connect()
    try:
        conn = adapter._connection  # type: ignore[attr-defined]
        _ensure_baselines_table(conn)

        prefix = _get_schema_prefix()
        table = f"{prefix}CM3_BASELINES"

        cursor = conn.execute(
            f"SELECT suite_name, pass_rate, avg_quality_score,"
            f"       avg_error_rate, sample_size, updated_at"
            f" FROM {table}"
            f" ORDER BY suite_name"
        )
        rows = cursor.fetchall()
    finally:
        adapter.disconnect()

    results = []
    for row in rows:
        try:
            results.append({
                "suite_name": row["suite_name"],
                "pass_rate": row["pass_rate"],
                "avg_quality_score": row["avg_quality_score"],
                "avg_error_rate": row["avg_error_rate"],
                "sample_size": row["sample_size"],
                "updated_at": row["updated_at"],
            })
        except TypeError:
            cols = [
                "suite_name", "pass_rate", "avg_quality_score",
                "avg_error_rate", "sample_size", "updated_at",
            ]
            results.append(dict(zip(cols, row)))
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def update_baseline(suite_name: str, result: dict[str, Any]) -> dict[str, Any]:
    """Append *result* to the rolling history and recompute the baseline.

    Reads the current store from ``reports/baselines.json``, appends a
    per-run snapshot derived from *result* to the suite's history, caps
    the history at :data:`_ROLLING_WINDOW` entries (oldest dropped first),
    recomputes all averages, persists the updated store, and returns the
    new baseline dict.

    When ``DB_ADAPTER`` is set in the environment, the function first attempts
    to delegate to :func:`_update_baseline_db`.  On any DB exception the JSON
    path is used as fallback (warning logged, no exception propagated).

    Args:
        suite_name: Logical name of the test suite (e.g. ``"ATOCTRAN"``).
        result: Run result dict.  Expected keys (all optional — missing keys
            are treated as zero or absent):

            * ``pass_count`` — number of tests that passed.
            * ``total_count`` — total tests executed.
            * ``invalid_rows`` — row-level validation failures.
            * ``total_rows`` — total rows inspected.
            * ``quality_score`` — optional float quality score (0–100).

    Returns:
        Updated baseline dict with keys: suite_name, pass_rate,
        avg_quality_score, avg_error_rate, sample_size, updated_at.
    """
    if os.getenv("DB_ADAPTER"):
        try:
            return _update_baseline_db(suite_name, result)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB baseline update failed, using JSON: %s", exc)

    path = _BASELINES_PATH

    store = _load_store(path)
    suite_record = store.get(suite_name, {"baseline": {}, "history": []})

    # Build per-run snapshot
    pass_count = result.get("pass_count", 0)
    total_count = result.get("total_count", 0)
    pass_rate = (pass_count / total_count * 100.0) if total_count > 0 else 0.0

    invalid_rows = result.get("invalid_rows", 0)
    total_rows = result.get("total_rows", 0)
    error_rate = _compute_error_rate(invalid_rows, total_rows)

    snapshot: dict[str, Any] = {
        "pass_rate": pass_rate,
        "quality_score": result.get("quality_score"),  # None when absent
        "error_rate": error_rate,
    }

    # Append and cap the rolling window
    history: list[dict[str, Any]] = suite_record["history"]
    history.append(snapshot)
    if len(history) > _ROLLING_WINDOW:
        history = history[-_ROLLING_WINDOW:]

    # Recompute and persist
    baseline = _recompute_baseline(suite_name, history)
    store[suite_name] = {"baseline": baseline, "history": history}
    _save_store(path, store)

    return baseline


def get_baseline(suite_name: str) -> Optional[dict[str, Any]]:
    """Return the stored baseline for *suite_name*, or ``None`` if absent.

    When ``DB_ADAPTER`` is set in the environment, the database is queried
    first via :func:`_get_baseline_db`.  On any DB exception the JSON file
    is used as fallback (warning logged).

    Args:
        suite_name: Name of the suite to look up.

    Returns:
        Baseline dict with keys: suite_name, pass_rate, avg_quality_score,
        avg_error_rate, sample_size, updated_at; or ``None`` if no baseline
        has been recorded for this suite yet.
    """
    if os.getenv("DB_ADAPTER"):
        try:
            return _get_baseline_db(suite_name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB baseline get failed, using JSON: %s", exc)

    path = _BASELINES_PATH
    store = _load_store(path)
    record = store.get(suite_name)
    if record is None:
        return None
    return record.get("baseline") or None


def list_baselines() -> list[dict[str, Any]]:
    """Return all stored baselines sorted alphabetically by suite name.

    When ``DB_ADAPTER`` is set in the environment, the database is queried
    first via :func:`_list_baselines_db`.  On any DB exception the JSON file
    is used as fallback (warning logged).

    Returns:
        List of baseline dicts (see :func:`get_baseline`), sorted by
        ``suite_name``.  Returns an empty list when no baselines exist.
    """
    if os.getenv("DB_ADAPTER"):
        try:
            return _list_baselines_db()
        except Exception as exc:  # noqa: BLE001
            logger.warning("DB baseline list failed, using JSON: %s", exc)

    path = _BASELINES_PATH
    store = _load_store(path)
    baselines = [
        record["baseline"]
        for record in store.values()
        if record.get("baseline")
    ]
    return sorted(baselines, key=lambda b: b.get("suite_name", ""))

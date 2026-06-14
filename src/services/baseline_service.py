"""Baseline store for per-suite quality metrics with JSON + DB persistence.

Baselines are persisted to ``reports/baselines.json`` using the same
read-modify-write pattern as ``reports/run_history.json``.  Each suite
maintains a rolling window of its last 10 runs; averages are recomputed
on every call to :func:`update_baseline`.

When a SQLAlchemy engine is available (via
:func:`~src.database.engine.get_engine`) each operation is also mirrored
to the ``APP_BASELINES`` database table.  DB calls are always wrapped in
``try/except`` so the JSON fallback is never bypassed — callers are never
broken by an absent or misconfigured database.

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

from sqlalchemy import text

from src.database.engine import get_engine, get_schema_prefix

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
# Private DB helpers
# ---------------------------------------------------------------------------


def _qualified(schema: str, table: str) -> str:
    """Return a schema-qualified table identifier.

    Args:
        schema: Schema name with optional trailing dot (e.g. ``'APP_INT.'``).
            Pass ``''`` for SQLite (no schema).
        table: Unqualified table name.

    Returns:
        ``'schema.TABLE'`` when schema is non-empty, otherwise ``'TABLE'``.
    """
    clean = schema.rstrip(".")
    return f"{clean}.{table}" if clean else table


def _db_upsert_baseline(suite_name: str, baseline: dict[str, Any]) -> None:
    """Upsert a baseline row into APP_BASELINES.

    Uses INSERT OR REPLACE for SQLite and a SELECT-then-INSERT-or-UPDATE
    strategy for Oracle/PostgreSQL so that a single primary key row is
    maintained per suite.  All errors are logged as warnings; callers are
    never broken.

    Args:
        suite_name: Primary key identifying the suite.
        baseline: Baseline dict to serialise as the row payload.
    """
    try:
        engine = get_engine()
        schema = get_schema_prefix()
        table = _qualified(schema, "APP_BASELINES")
        payload = json.dumps(baseline)
        recorded_at = datetime.utcnow()

        adapter = os.getenv("DB_ADAPTER", "oracle").lower()

        with engine.connect() as conn:
            if adapter == "sqlite":
                conn.execute(
                    text(
                        f"INSERT OR REPLACE INTO {table} "
                        "(suite_name, recorded_at, payload) "
                        "VALUES (:suite_name, :recorded_at, :payload)"
                    ),
                    {
                        "suite_name": suite_name,
                        "recorded_at": recorded_at,
                        "payload": payload,
                    },
                )
            else:
                # Generic strategy: SELECT then INSERT or UPDATE
                row = conn.execute(
                    text(
                        f"SELECT suite_name FROM {table} "
                        "WHERE suite_name = :suite_name"
                    ),
                    {"suite_name": suite_name},
                ).fetchone()
                if row is None:
                    conn.execute(
                        text(
                            f"INSERT INTO {table} "
                            "(suite_name, recorded_at, payload) "
                            "VALUES (:suite_name, :recorded_at, :payload)"
                        ),
                        {
                            "suite_name": suite_name,
                            "recorded_at": recorded_at,
                            "payload": payload,
                        },
                    )
                else:
                    conn.execute(
                        text(
                            f"UPDATE {table} "
                            "SET recorded_at = :recorded_at, payload = :payload "
                            "WHERE suite_name = :suite_name"
                        ),
                        {
                            "suite_name": suite_name,
                            "recorded_at": recorded_at,
                            "payload": payload,
                        },
                    )
            conn.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("APP_BASELINES DB write failed (JSON fallback still written): %s", exc)


def _db_fetch_baseline(suite_name: str) -> Optional[dict[str, Any]]:
    """Fetch a baseline row from APP_BASELINES by suite name.

    Args:
        suite_name: Suite name to look up.

    Returns:
        Parsed baseline dict, or ``None`` when the row is absent or the DB
        is unavailable.
    """
    try:
        engine = get_engine()
        schema = get_schema_prefix()
        table = _qualified(schema, "APP_BASELINES")
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    f"SELECT payload FROM {table} "
                    "WHERE suite_name = :suite_name"
                ),
                {"suite_name": suite_name},
            ).fetchone()
        if row is None:
            return None
        return json.loads(row["payload"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("APP_BASELINES DB read failed (falling back to JSON): %s", exc)
        return None


def _db_fetch_all_baselines() -> Optional[list[dict[str, Any]]]:
    """Fetch all baseline rows from APP_BASELINES.

    Returns:
        List of parsed baseline dicts, or ``None`` when the DB is unavailable.
    """
    try:
        engine = get_engine()
        schema = get_schema_prefix()
        table = _qualified(schema, "APP_BASELINES")
        with engine.connect() as conn:
            rows = conn.execute(
                text(f"SELECT suite_name, payload FROM {table}")
            ).fetchall()
        return [json.loads(row["payload"]) for row in rows]
    except Exception as exc:  # noqa: BLE001
        logger.warning("APP_BASELINES DB list failed (falling back to JSON): %s", exc)
        return None


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

    # Mirror to DB (best-effort — never breaks callers)
    _db_upsert_baseline(suite_name, baseline)

    return baseline


def get_baseline(suite_name: str) -> Optional[dict[str, Any]]:
    """Return the stored baseline for *suite_name*, or ``None`` if absent.

    Args:
        suite_name: Name of the suite to look up.

    Returns:
        Baseline dict with keys: suite_name, pass_rate, avg_quality_score,
        avg_error_rate, sample_size, updated_at; or ``None`` if no baseline
        has been recorded for this suite yet.
    """
    # Try DB first; fall back to JSON when DB is unavailable or returns nothing
    db_result = _db_fetch_baseline(suite_name)
    if db_result is not None:
        return db_result

    path = _BASELINES_PATH
    store = _load_store(path)
    record = store.get(suite_name)
    if record is None:
        return None
    return record.get("baseline") or None


def list_baselines() -> list[dict[str, Any]]:
    """Return all stored baselines sorted alphabetically by suite name.

    Returns:
        List of baseline dicts (see :func:`get_baseline`), sorted by
        ``suite_name``.  Returns an empty list when no baselines exist.
    """
    # Try DB first; fall back to JSON when DB is unavailable
    db_result = _db_fetch_all_baselines()
    if db_result is not None:
        return sorted(db_result, key=lambda b: b.get("suite_name", ""))

    path = _BASELINES_PATH
    store = _load_store(path)
    baselines = [
        record["baseline"]
        for record in store.values()
        if record.get("baseline")
    ]
    return sorted(baselines, key=lambda b: b.get("suite_name", ""))

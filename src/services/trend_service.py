"""Daily trend aggregation for run history — used by the trend API endpoint.

Aggregates run history into daily buckets, returning pass/fail counts and
average quality score per day. Supports both a JSON file path (default) and
a database path when ``DB_ADAPTER`` environment variable is set.
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Top-level import allows tests to patch src.services.trend_service.get_database_adapter
# and src.services.trend_service.get_db_config without needing to reach into sub-modules.
try:
    from src.database.adapters.factory import get_database_adapter
    from src.config.db_config import get_db_config
except ImportError:  # pragma: no cover
    get_database_adapter = None  # type: ignore[assignment]
    get_db_config = None  # type: ignore[assignment]

VALID_DAYS = (7, 14, 30, 90)

_RUN_HISTORY_PATH = Path("reports") / "run_history.json"


def _load_history() -> list[dict]:
    """Load run history entries from the JSON file.

    Returns:
        List of run history entry dicts. Returns an empty list if the file
        does not exist or cannot be parsed.
    """
    if not _RUN_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(_RUN_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def get_trend(suite: Optional[str] = None, days: int = 30) -> list[dict]:
    """Return daily-aggregated run history buckets.

    Tries the database path first when ``DB_ADAPTER`` is set, then falls back
    to reading ``reports/run_history.json``.

    Args:
        suite: Filter to a specific suite name. ``None`` means all suites.
        days: Number of days to look back. Must be one of ``(7, 14, 30, 90)``.

    Returns:
        List of daily bucket dicts sorted ascending by date::

            [
                {
                    "date": "2026-03-30",
                    "total_runs": 5,
                    "pass_runs": 4,
                    "fail_runs": 1,
                    "avg_quality_score": 88.5,  # or None
                    "pass_rate": 80.0,
                }
            ]

    Raises:
        ValueError: If ``days`` is not in :data:`VALID_DAYS`.
    """
    if days not in VALID_DAYS:
        raise ValueError(f"days must be one of {VALID_DAYS}, got {days}")

    db_adapter = os.getenv("DB_ADAPTER")
    if db_adapter:
        try:
            return _get_trend_from_db(suite, days)
        except Exception:
            pass  # fall through to JSON

    return _get_trend_from_json(suite, days)


def _get_trend_from_json(suite: Optional[str], days: int) -> list[dict]:
    """Aggregate run history from the JSON file into daily buckets.

    Args:
        suite: Optional suite name filter. ``None`` includes all suites.
        days: Number of past days to include.

    Returns:
        List of daily bucket dicts sorted ascending by date.
    """
    history = _load_history()
    cutoff = datetime.utcnow() - timedelta(days=days)

    # bucket structure: date_key -> aggregation state
    buckets: dict[str, dict] = defaultdict(lambda: {
        "total_runs": 0,
        "pass_runs": 0,
        "fail_runs": 0,
        "quality_scores": [],
    })

    for entry in history:
        # Suite filter
        if suite and entry.get("suite_name") != suite:
            continue

        # Parse timestamp — JSON entries use the "timestamp" key
        raw_ts = entry.get("timestamp") or entry.get("run_timestamp") or entry.get("run_date") or ""
        if isinstance(raw_ts, str):
            try:
                run_dt = datetime.fromisoformat(raw_ts[:19])
            except (ValueError, TypeError):
                continue
        elif isinstance(raw_ts, datetime):
            run_dt = raw_ts
        else:
            continue

        if run_dt < cutoff:
            continue

        date_key = run_dt.strftime("%Y-%m-%d")
        bucket = buckets[date_key]
        bucket["total_runs"] += 1

        status = entry.get("status", "")
        if status == "PASS":
            bucket["pass_runs"] += 1
        else:
            bucket["fail_runs"] += 1

        qs = entry.get("quality_score")
        if qs is not None:
            bucket["quality_scores"].append(float(qs))

    result = []
    for date_key in sorted(buckets.keys()):
        b = buckets[date_key]
        scores = b.pop("quality_scores")
        b["date"] = date_key
        b["avg_quality_score"] = round(sum(scores) / len(scores), 2) if scores else None
        b["pass_rate"] = round(b["pass_runs"] / b["total_runs"] * 100, 2) if b["total_runs"] else 0.0
        result.append(b)

    return result


def _get_trend_from_db(suite: Optional[str], days: int) -> list[dict]:
    """Aggregate run history from the configured database adapter.

    Uses :func:`~src.database.adapters.factory.get_database_adapter` to
    query the ``APP_RUN_HISTORY`` table and aggregate results into daily
    buckets via SQL GROUP BY.

    Args:
        suite: Optional suite name filter passed as a SQL bind parameter.
        days: Number of past days to include (used to compute the cutoff).

    Returns:
        List of daily bucket dicts sorted ascending by date.

    Raises:
        Exception: Re-raises any exception from the adapter so that
            :func:`get_trend` can fall back to the JSON path.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    schema = get_db_config().schema
    table = f"{schema}.APP_RUN_HISTORY"

    suite_filter = "AND suite_name = :suite" if suite else ""
    sql = f"""
        SELECT
            CAST(run_timestamp AS DATE) AS run_date,
            COUNT(*) AS total_runs,
            SUM(CASE WHEN status = 'PASS' THEN 1 ELSE 0 END) AS pass_runs,
            SUM(CASE WHEN status != 'PASS' THEN 1 ELSE 0 END) AS fail_runs,
            AVG(quality_score) AS avg_quality_score
        FROM {table}
        WHERE run_timestamp >= :cutoff
        {suite_filter}
        GROUP BY CAST(run_timestamp AS DATE)
        ORDER BY CAST(run_timestamp AS DATE)
    """

    params: dict = {"cutoff": cutoff}
    if suite:
        params["suite"] = suite

    adapter = get_database_adapter()
    with adapter:
        df = adapter.execute_query(sql, params)

    if df is None or df.empty:
        return []

    result = []
    for _, row in df.iterrows():
        total = int(row["total_runs"] or 0)
        pass_r = int(row["pass_runs"] or 0)
        fail_r = int(row["fail_runs"] or 0)
        qs_raw = row.get("avg_quality_score") if hasattr(row, "get") else row["avg_quality_score"]
        avg_qs: Optional[float] = round(float(qs_raw), 2) if qs_raw is not None else None

        run_date = row["run_date"]
        if isinstance(run_date, datetime):
            date_str = run_date.strftime("%Y-%m-%d")
        else:
            date_str = str(run_date)[:10]

        result.append({
            "date": date_str,
            "total_runs": total,
            "pass_runs": pass_r,
            "fail_runs": fail_r,
            "avg_quality_score": avg_qs,
            "pass_rate": round(pass_r / total * 100, 2) if total else 0.0,
        })

    return result

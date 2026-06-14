"""Per-suite summary aggregation for the dashboard cards."""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

_RUN_HISTORY_PATH = Path("reports") / "run_history.json"


def load_run_history() -> list[dict]:
    """Load run history entries from the JSON file on disk.

    Returns:
        List of run history entry dicts from ``reports/run_history.json``.
        Returns an empty list if the file does not exist or contains
        invalid JSON.
    """
    if not _RUN_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(_RUN_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []

# Number of days used for pass-rate and trend calculations.
_WINDOW_30D = 30
_WINDOW_7D = 7
_WINDOW_14D = 14

# Minimum percentage-point difference to classify a trend as up/down.
_TREND_THRESHOLD = 2.0


def _parse_timestamp(entry: dict) -> datetime:
    """Extract a comparable datetime from a run history entry.

    Tries the ``timestamp`` key (run_history.json format), then
    ``run_date``, then ``run_timestamp`` as fallbacks.

    Args:
        entry: A run history entry dict.

    Returns:
        Parsed datetime, or ``datetime.min`` when no valid value found.
    """
    raw = entry.get("timestamp") or entry.get("run_date") or entry.get("run_timestamp") or ""
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw:
        try:
            # Trim trailing 'Z' and microseconds for broad compatibility
            return datetime.fromisoformat(raw[:19])
        except (ValueError, TypeError):
            pass
    return datetime.min


def _is_pass(entry: dict) -> bool:
    """Return True when the run entry represents a PASS result.

    Args:
        entry: A run history entry dict.

    Returns:
        True if ``status`` == ``"PASS"``, False for FAIL or PARTIAL.
    """
    return entry.get("status") == "PASS"


def _pass_rate(runs: list[dict]) -> Optional[float]:
    """Calculate the pass rate for a list of runs.

    Args:
        runs: List of run history entry dicts.

    Returns:
        Pass rate as a percentage (0–100), or ``None`` if the list is empty.
    """
    if not runs:
        return None
    passes = sum(1 for r in runs if _is_pass(r))
    return passes / len(runs) * 100


def get_suite_summaries() -> list[dict]:
    """Return per-suite summary objects for dashboard cards.

    Reads the full run history (DB or JSON fallback), groups entries by
    ``suite_name``, and produces one summary dict per suite.

    Returns:
        List of suite summary dicts sorted by ``last_run_at`` descending::

            [
                {
                    "suite_name": str,
                    "last_run_status": "PASS" | "FAIL",
                    "last_run_at": str,          # ISO datetime string
                    "pass_rate_30d": float,      # 0.0–100.0
                    "avg_quality_score": float | None,
                    "trend_direction": "up" | "down" | "flat",
                },
                ...
            ]
    """
    history = load_run_history()
    if not history:
        return []

    now = datetime.utcnow()
    cutoff_30d = now - timedelta(days=_WINDOW_30D)
    cutoff_7d = now - timedelta(days=_WINDOW_7D)
    cutoff_14d = now - timedelta(days=_WINDOW_14D)

    # Group entries by suite name.
    suites: dict[str, list[dict]] = {}
    for entry in history:
        name = entry.get("suite_name") or entry.get("name") or "unknown"
        suites.setdefault(name, []).append(entry)

    results = []
    for suite_name, runs in suites.items():
        runs_sorted = sorted(runs, key=_parse_timestamp, reverse=True)
        last_run = runs_sorted[0]

        last_status = "PASS" if _is_pass(last_run) else "FAIL"
        last_at = _parse_timestamp(last_run).isoformat()

        # 30-day pass rate.
        recent_30 = [r for r in runs if _parse_timestamp(r) >= cutoff_30d]
        pass_30 = sum(1 for r in recent_30 if _is_pass(r))
        pass_rate_30d = round(pass_30 / len(recent_30) * 100, 1) if recent_30 else 0.0

        # Average quality score over the 30-day window.
        scores = [
            r["quality_score"]
            for r in recent_30
            if r.get("quality_score") is not None
        ]
        avg_quality: Optional[float] = round(sum(scores) / len(scores), 1) if scores else None

        # Trend: compare last 7 days vs prior 7 days (days 8–14).
        last_7 = [r for r in runs if _parse_timestamp(r) >= cutoff_7d]
        prior_7 = [
            r for r in runs if cutoff_14d <= _parse_timestamp(r) < cutoff_7d
        ]

        r_last = _pass_rate(last_7)
        r_prior = _pass_rate(prior_7)

        if r_last is None or r_prior is None:
            trend = "flat"
        elif r_last - r_prior > _TREND_THRESHOLD:
            trend = "up"
        elif r_prior - r_last > _TREND_THRESHOLD:
            trend = "down"
        else:
            trend = "flat"

        results.append(
            {
                "suite_name": suite_name,
                "last_run_status": last_status,
                "last_run_at": last_at,
                "pass_rate_30d": pass_rate_30d,
                "avg_quality_score": avg_quality,
                "trend_direction": trend,
            }
        )

    results.sort(key=lambda x: x["last_run_at"], reverse=True)
    return results

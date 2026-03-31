"""Unit tests for src/services/summary_service.py.

Tests cover:
- 3 suites in history → 3 summary objects, sorted by last_run_at desc
- Trend direction: up when recent pass rate > prior; down when lower; flat within 2%
- Empty history → []
- Single run → trend='flat', pass_rate_30d correct
- Pass/FAIL/PARTIAL status detection using 'status' field
- quality_score averaging
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest

from src.services.summary_service import get_suite_summaries


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_entry(
    suite_name: str = "MySuite",
    timestamp: str = "2026-03-30T12:00:00",
    status: str = "PASS",
    quality_score: float | None = None,
) -> dict:
    """Return a run_history.json-shaped entry dict.

    Args:
        suite_name: Name of the suite.
        timestamp: ISO datetime string.
        status: Run status — PASS, FAIL, or PARTIAL.
        quality_score: Optional quality score float.

    Returns:
        Dict matching run_history.json entry schema.
    """
    entry: dict = {
        "run_id": "test-id",
        "suite_name": suite_name,
        "environment": "test",
        "timestamp": timestamp,
        "status": status,
        "pass_count": 1 if status == "PASS" else 0,
        "fail_count": 0 if status == "PASS" else 1,
        "total_count": 1,
    }
    if quality_score is not None:
        entry["quality_score"] = quality_score
    return entry


def _ts(days_ago: int) -> str:
    """Return ISO timestamp string for N days ago from 2026-03-31."""
    base = datetime(2026, 3, 31, 12, 0, 0)
    return (base - timedelta(days=days_ago)).isoformat()


# ---------------------------------------------------------------------------
# Empty history
# ---------------------------------------------------------------------------

def test_empty_history_returns_empty_list():
    with patch("src.services.summary_service.load_run_history", return_value=[]):
        result = get_suite_summaries()
    assert result == []


# ---------------------------------------------------------------------------
# Single run
# ---------------------------------------------------------------------------

def test_single_run_returns_one_summary():
    history = [_make_entry("Alpha", _ts(1), "PASS")]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()
    assert len(result) == 1
    s = result[0]
    assert s["suite_name"] == "Alpha"
    assert s["last_run_status"] == "PASS"
    assert s["pass_rate_30d"] == 100.0
    assert s["trend_direction"] == "flat"


def test_single_run_fail_status():
    history = [_make_entry("Beta", _ts(1), "FAIL")]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()
    assert result[0]["last_run_status"] == "FAIL"
    assert result[0]["pass_rate_30d"] == 0.0


# ---------------------------------------------------------------------------
# Three suites — basic grouping and sort order
# ---------------------------------------------------------------------------

def test_three_suites_returns_three_summaries():
    history = [
        _make_entry("Alpha", _ts(1), "PASS"),
        _make_entry("Beta", _ts(2), "FAIL"),
        _make_entry("Gamma", _ts(3), "PASS"),
    ]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    assert len(result) == 3
    suite_names = [r["suite_name"] for r in result]
    assert "Alpha" in suite_names
    assert "Beta" in suite_names
    assert "Gamma" in suite_names


def test_summaries_sorted_by_last_run_at_descending():
    history = [
        _make_entry("Alpha", _ts(3), "PASS"),
        _make_entry("Beta", _ts(1), "PASS"),   # most recent
        _make_entry("Gamma", _ts(5), "PASS"),
    ]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    # Beta ran 1 day ago — should be first
    assert result[0]["suite_name"] == "Beta"
    assert result[1]["suite_name"] == "Alpha"
    assert result[2]["suite_name"] == "Gamma"


# ---------------------------------------------------------------------------
# pass_rate_30d calculation
# ---------------------------------------------------------------------------

def test_pass_rate_30d_with_mixed_results():
    # 2 passes, 2 fails within 30 days → 50%
    history = [
        _make_entry("Suite", _ts(1), "PASS"),
        _make_entry("Suite", _ts(5), "PASS"),
        _make_entry("Suite", _ts(10), "FAIL"),
        _make_entry("Suite", _ts(20), "FAIL"),
    ]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    assert result[0]["pass_rate_30d"] == 50.0


def test_runs_older_than_30d_excluded_from_pass_rate():
    # Only the old run is a pass; recent runs are all fails
    history = [
        _make_entry("Suite", _ts(1), "FAIL"),
        _make_entry("Suite", _ts(35), "PASS"),  # outside 30d window
    ]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    # Only the recent FAIL counts for 30d pass rate
    assert result[0]["pass_rate_30d"] == 0.0


# ---------------------------------------------------------------------------
# Trend direction
# ---------------------------------------------------------------------------

def test_trend_up_when_recent_pass_rate_higher():
    # Last 7 days: 5 passes → 100%
    # Prior 7 days (days 8-14): 0 passes out of 5 → 0%
    history = (
        [_make_entry("Suite", _ts(i), "PASS") for i in range(1, 6)]
        + [_make_entry("Suite", _ts(i), "FAIL") for i in range(8, 13)]
    )
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    assert result[0]["trend_direction"] == "up"


def test_trend_down_when_recent_pass_rate_lower():
    # Last 7 days: all fail → 0%
    # Prior 7 days: all pass → 100%
    history = (
        [_make_entry("Suite", _ts(i), "FAIL") for i in range(1, 6)]
        + [_make_entry("Suite", _ts(i), "PASS") for i in range(8, 13)]
    )
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    assert result[0]["trend_direction"] == "down"


def test_trend_flat_when_rates_within_2_percent():
    # Both windows: 1 pass out of 2 → 50%
    history = [
        _make_entry("Suite", _ts(1), "PASS"),
        _make_entry("Suite", _ts(3), "FAIL"),
        _make_entry("Suite", _ts(8), "PASS"),
        _make_entry("Suite", _ts(10), "FAIL"),
    ]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    assert result[0]["trend_direction"] == "flat"


def test_trend_flat_when_no_prior_data():
    # Only last 7 days, nothing in days 8-14
    history = [_make_entry("Suite", _ts(2), "PASS")]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    assert result[0]["trend_direction"] == "flat"


# ---------------------------------------------------------------------------
# Quality score averaging
# ---------------------------------------------------------------------------

def test_avg_quality_score_computed_correctly():
    history = [
        _make_entry("Suite", _ts(1), "PASS", quality_score=90.0),
        _make_entry("Suite", _ts(5), "PASS", quality_score=80.0),
    ]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    assert result[0]["avg_quality_score"] == 85.0


def test_avg_quality_score_none_when_no_scores():
    history = [_make_entry("Suite", _ts(1), "PASS")]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    assert result[0]["avg_quality_score"] is None


# ---------------------------------------------------------------------------
# PARTIAL status treated as FAIL
# ---------------------------------------------------------------------------

def test_partial_status_treated_as_fail():
    history = [_make_entry("Suite", _ts(1), "PARTIAL")]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    assert result[0]["last_run_status"] == "FAIL"
    assert result[0]["pass_rate_30d"] == 0.0


# ---------------------------------------------------------------------------
# Required keys in every summary object
# ---------------------------------------------------------------------------

def test_summary_has_all_required_keys():
    history = [_make_entry("Suite", _ts(1), "PASS")]
    with patch("src.services.summary_service.load_run_history", return_value=history):
        result = get_suite_summaries()

    required_keys = {
        "suite_name",
        "last_run_status",
        "last_run_at",
        "pass_rate_30d",
        "avg_quality_score",
        "trend_direction",
    }
    assert required_keys.issubset(set(result[0].keys()))

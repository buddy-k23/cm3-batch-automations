"""Unit tests for src/services/deviation_detector.py — check_deviation()."""
from __future__ import annotations

from unittest.mock import patch

import pytest


MODULE = "src.services.baseline_service.get_baseline"


def _make_result(
    passed: bool = True,
    total_rows: int = 100,
    invalid_rows: int = 0,
    quality_score: float = 95.0,
) -> dict:
    """Helper to build a minimal run result dict."""
    return {
        "passed": passed,
        "total_rows": total_rows,
        "invalid_rows": invalid_rows,
        "quality_score": quality_score,
    }


def _baseline(
    pass_rate: float = 90.0,
    avg_quality_score: float = 90.0,
    avg_error_rate: float = 5.0,
) -> dict:
    """Helper to build a minimal baseline dict."""
    return {
        "pass_rate": pass_rate,
        "avg_quality_score": avg_quality_score,
        "avg_error_rate": avg_error_rate,
    }


# ---------------------------------------------------------------------------
# No baseline
# ---------------------------------------------------------------------------

def test_no_baseline_returns_no_deviation():
    """When get_baseline returns None, result should indicate no_baseline."""
    with patch(MODULE, return_value=None):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("my_suite", _make_result())

    assert result["deviated"] is False
    assert result["alerts"] == []
    assert result["reason"] == "no_baseline"


# ---------------------------------------------------------------------------
# pass_rate checks
# ---------------------------------------------------------------------------

def test_pass_rate_drop_exceeds_threshold():
    """Pass rate drop of 15pp (> default 10pp threshold) triggers an alert."""
    # baseline pass_rate=90, current invalid=25/100 → pass_rate=75 → delta=-15
    with patch(MODULE, return_value=_baseline(pass_rate=90.0)):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("my_suite", _make_result(total_rows=100, invalid_rows=25))

    assert result["deviated"] is True
    assert len(result["alerts"]) == 1
    alert = result["alerts"][0]
    assert alert["metric"] == "pass_rate"
    assert alert["baseline_value"] == 90.0
    assert alert["current_value"] == 75.0
    assert alert["delta"] == -15.0
    assert alert["threshold"] == 10.0


def test_pass_rate_drop_within_threshold():
    """Pass rate drop of 5pp (≤ default 10pp threshold) does NOT trigger alert."""
    # baseline pass_rate=90, current invalid=5/100 → pass_rate=95 → delta=+5 (improvement)
    # Use invalid_rows=15 → pass_rate=85 → delta=-5 which is within threshold
    with patch(MODULE, return_value=_baseline(pass_rate=90.0)):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("my_suite", _make_result(total_rows=100, invalid_rows=15))

    assert result["deviated"] is False
    assert result["alerts"] == []


# ---------------------------------------------------------------------------
# quality_score checks
# ---------------------------------------------------------------------------

def test_quality_drop_exceeds_threshold():
    """Quality score drop of 10pp (> default 5pp threshold) triggers an alert."""
    baseline = _baseline(avg_quality_score=90.0)
    result_data = _make_result(quality_score=78.0)  # delta = -12

    with patch(MODULE, return_value=baseline):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("suite_q", result_data)

    quality_alerts = [a for a in result["alerts"] if a["metric"] == "quality_score"]
    assert len(quality_alerts) == 1
    alert = quality_alerts[0]
    assert alert["baseline_value"] == 90.0
    assert alert["current_value"] == 78.0
    assert alert["delta"] == -12.0
    assert alert["threshold"] == 5.0


def test_both_pass_rate_and_quality_drop():
    """Two metrics deviating produces two separate alerts."""
    baseline = _baseline(pass_rate=90.0, avg_quality_score=90.0, avg_error_rate=5.0)
    # pass_rate drops 15pp, quality drops 12pp, error_rate unchanged
    result_data = {
        "passed": False,
        "total_rows": 100,
        "invalid_rows": 25,   # pass_rate=75, delta=-15
        "quality_score": 78.0,  # delta=-12
    }

    with patch(MODULE, return_value=baseline):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("suite_both", result_data)

    assert result["deviated"] is True
    metrics = {a["metric"] for a in result["alerts"]}
    assert metrics == {"pass_rate", "quality_score"}
    assert len(result["alerts"]) == 2


# ---------------------------------------------------------------------------
# error_rate checks
# ---------------------------------------------------------------------------

def test_all_three_metrics_deviated():
    """All three metrics deviating produces three alerts."""
    baseline = _baseline(pass_rate=90.0, avg_quality_score=90.0, avg_error_rate=5.0)
    # pass_rate=75 (drop 15), quality=78 (drop 12), error_rate=25 (spike +20 → delta=20, > threshold 20 → NOT triggered)
    # Use error_rate spike of 30 (> 20 threshold): invalid_rows=55/100 → error_rate=55, delta=50
    result_data = {
        "passed": False,
        "total_rows": 100,
        "invalid_rows": 55,   # pass_rate=45 (drop 45), error_rate=55 (spike 50)
        "quality_score": 78.0,  # quality drop 12
    }

    with patch(MODULE, return_value=baseline):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("suite_all", result_data)

    assert result["deviated"] is True
    metrics = {a["metric"] for a in result["alerts"]}
    assert metrics == {"pass_rate", "quality_score", "error_rate"}
    assert len(result["alerts"]) == 3


def test_error_rate_spike_exactly_at_threshold_not_triggered():
    """Error rate spike equal to threshold does NOT trigger (strict > check)."""
    # baseline error_rate=5, current error_rate=25 → delta=20, threshold=20 → NOT triggered
    baseline = _baseline(pass_rate=100.0, avg_quality_score=95.0, avg_error_rate=5.0)
    result_data = _make_result(total_rows=100, invalid_rows=25, quality_score=95.0)  # error_rate=25

    with patch(MODULE, return_value=baseline):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("suite_at_threshold", result_data)

    error_alerts = [a for a in result["alerts"] if a["metric"] == "error_rate"]
    assert error_alerts == []


# ---------------------------------------------------------------------------
# Custom thresholds
# ---------------------------------------------------------------------------

def test_custom_thresholds_override_defaults():
    """Custom thresholds dict overrides DEFAULT_THRESHOLDS values."""
    # With default threshold=10, a 5pp drop would NOT trigger.
    # With custom threshold=3, a 5pp drop SHOULD trigger.
    baseline = _baseline(pass_rate=90.0)
    result_data = _make_result(total_rows=100, invalid_rows=15)  # pass_rate=85, delta=-5

    with patch(MODULE, return_value=baseline):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("suite_custom", result_data, thresholds={"pass_rate_drop": 3.0})

    assert result["deviated"] is True
    pass_alerts = [a for a in result["alerts"] if a["metric"] == "pass_rate"]
    assert len(pass_alerts) == 1
    assert pass_alerts[0]["threshold"] == 3.0


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_missing_quality_score_skips_quality_check():
    """Result without quality_score key does not raise and skips quality alert."""
    baseline = _baseline(avg_quality_score=90.0)
    result_data = {"passed": True, "total_rows": 100, "invalid_rows": 0}  # no quality_score

    with patch(MODULE, return_value=baseline):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("suite_no_quality", result_data)

    quality_alerts = [a for a in result["alerts"] if a["metric"] == "quality_score"]
    assert quality_alerts == []


def test_total_rows_zero_no_crash():
    """total_rows=0 should not cause a ZeroDivisionError and returns no deviation."""
    baseline = _baseline(pass_rate=90.0, avg_error_rate=5.0)
    result_data = {"passed": True, "total_rows": 0, "invalid_rows": 0, "quality_score": 95.0}

    with patch(MODULE, return_value=baseline):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("suite_zero_rows", result_data)

    # With total_rows=0 and passed=True, pass_rate=100. baseline=90 → no drop.
    assert isinstance(result, dict)
    assert "deviated" in result
    assert result["deviated"] is False


def test_return_shape_contains_deviated_and_alerts():
    """Result always contains 'deviated' (bool) and 'alerts' (list) keys."""
    with patch(MODULE, return_value=_baseline()):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("suite_shape", _make_result())

    assert isinstance(result["deviated"], bool)
    assert isinstance(result["alerts"], list)


def test_alert_contains_required_keys():
    """Each alert dict has metric, baseline_value, current_value, delta, threshold."""
    baseline = _baseline(pass_rate=90.0)
    result_data = _make_result(total_rows=100, invalid_rows=25)  # pass_rate=75, drop=15

    with patch(MODULE, return_value=baseline):
        from src.services.deviation_detector import check_deviation

        result = check_deviation("suite_keys", result_data)

    assert result["deviated"] is True
    alert = result["alerts"][0]
    for key in ("metric", "baseline_value", "current_value", "delta", "threshold"):
        assert key in alert, f"Alert missing key: {key}"

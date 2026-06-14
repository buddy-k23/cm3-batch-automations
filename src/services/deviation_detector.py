"""Statistical deviation detector — compares a run result against its suite baseline."""
from __future__ import annotations

from typing import Optional


DEFAULT_THRESHOLDS = {
    'pass_rate_drop': 10.0,    # percentage points
    'quality_drop': 5.0,       # percentage points
    'error_rate_spike': 20.0,  # percentage points
}


def check_deviation(
    suite_name: str,
    result: dict,
    thresholds: Optional[dict] = None,
) -> dict:
    """Compare a completed run against the stored suite baseline.

    Fetches the baseline for *suite_name* via ``baseline_service.get_baseline``
    and checks whether any of the three tracked metrics has moved beyond its
    configured threshold.

    Args:
        suite_name: Name of the suite to check.
        result: Run result dict with keys: passed, total_rows, invalid_rows,
            quality_score.
        thresholds: Override thresholds dict. Recognised keys:
            ``pass_rate_drop``, ``quality_drop``, ``error_rate_spike``.
            Defaults to ``DEFAULT_THRESHOLDS`` when ``None``.

    Returns:
        A dict with two guaranteed keys:

        - ``deviated`` (bool): True when at least one alert was raised.
        - ``alerts`` (list[dict]): One entry per breached threshold.
          Each alert contains: ``metric``, ``baseline_value``,
          ``current_value``, ``delta``, ``threshold``.

        When no baseline exists the dict also carries
        ``reason: 'no_baseline'`` and ``deviated`` is always ``False``.

    Raises:
        Nothing — all arithmetic edge cases (zero rows, missing keys) are
        handled gracefully.
    """
    from src.services.baseline_service import get_baseline

    baseline = get_baseline(suite_name)
    if baseline is None:
        return {'deviated': False, 'alerts': [], 'reason': 'no_baseline'}

    effective = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    alerts: list[dict] = []

    # Derive current metrics from the result dict
    total = result.get('total_rows') or 0
    passed = bool(result.get('passed'))
    invalid = result.get('invalid_rows') or 0
    quality = result.get('quality_score')

    # pass_rate: percentage of rows that are valid
    # When total_rows is available use arithmetic; fall back to the boolean flag.
    current_pass_rate = (
        (1 - invalid / total) * 100 if total else (100.0 if passed else 0.0)
    )
    current_error_rate = (invalid / total * 100) if total else 0.0

    # --- pass_rate drop check -------------------------------------------
    bl_pass_rate = baseline.get('pass_rate')
    if bl_pass_rate is not None:
        delta = current_pass_rate - bl_pass_rate
        if delta < -effective['pass_rate_drop']:
            alerts.append({
                'metric': 'pass_rate',
                'baseline_value': bl_pass_rate,
                'current_value': round(current_pass_rate, 2),
                'delta': round(delta, 2),
                'threshold': effective['pass_rate_drop'],
            })

    # --- quality_score drop check ----------------------------------------
    bl_quality = baseline.get('avg_quality_score')
    if bl_quality is not None and quality is not None:
        delta = float(quality) - bl_quality
        if delta < -effective['quality_drop']:
            alerts.append({
                'metric': 'quality_score',
                'baseline_value': bl_quality,
                'current_value': round(float(quality), 2),
                'delta': round(delta, 2),
                'threshold': effective['quality_drop'],
            })

    # --- error_rate spike check ------------------------------------------
    bl_error_rate = baseline.get('avg_error_rate')
    if bl_error_rate is not None:
        delta = current_error_rate - bl_error_rate
        if delta > effective['error_rate_spike']:
            alerts.append({
                'metric': 'error_rate',
                'baseline_value': bl_error_rate,
                'current_value': round(current_error_rate, 2),
                'delta': round(delta, 2),
                'threshold': effective['error_rate_spike'],
            })

    return {
        'deviated': len(alerts) > 0,
        'alerts': alerts,
    }

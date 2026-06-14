"""Tests that Chart.js is bundled inline and no CDN URLs are present in generated HTML."""

import re

from src.reports.renderers.validation_renderer import ValidationReporter, _CHART_JS_INLINE
from src.reports.renderers.comparison_renderer import HTMLReporter


def _sample_result():
    return {
        "valid": True,
        "timestamp": "2026-02-20T00:00:00Z",
        "file_metadata": {
            "file_name": "sample.txt",
            "format": "pipe_delimited",
            "size_bytes": 100,
            "size_mb": 0.0001,
            "modified_time": "2026-02-20T00:00:00",
        },
        "quality_metrics": {
            "quality_score": 99.5,
            "total_rows": 2,
            "total_columns": 2,
            "completeness_pct": 100.0,
            "uniqueness_pct": 100.0,
            "total_cells": 4,
            "filled_cells": 4,
            "null_cells": 0,
            "unique_rows": 2,
            "duplicate_rows": 0,
        },
        "errors": [],
        "warnings": [{"severity": "warning", "message": "sample warning"}],
        "info": [{"severity": "info", "message": "sample info"}],
        "error_count": 0,
        "warning_count": 1,
        "info_count": 1,
        "field_analysis": {
            "name": {"inferred_type": "string", "fill_rate_pct": 100.0, "unique_count": 2},
        },
        "duplicate_analysis": {"unique_rows": 2, "duplicate_rows": 0, "duplicate_pct": 0.0},
        "date_analysis": {},
        "business_rules": None,
        "appendix": {"sample_records": []},
    }


def test_generated_html_does_not_contain_cdn_url(tmp_path):
    """Generated HTML must not reference any external CDN URLs."""
    out = tmp_path / "report.html"
    reporter = ValidationReporter()
    reporter.generate(_sample_result(), str(out))
    html = out.read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in html, (
        "Generated HTML still references cdn.jsdelivr.net — Chart.js must be bundled inline."
    )


def test_generated_html_contains_chart_js_inline(tmp_path):
    """Generated HTML must contain Chart.js source inline (not via external script src)."""
    out = tmp_path / "report.html"
    reporter = ValidationReporter()
    reporter.generate(_sample_result(), str(out))
    html = out.read_text(encoding="utf-8")
    # The Chart.js UMD bundle defines the global `Chart` object.
    assert "!function" in html or "Chart.register" in html, (
        "Generated HTML does not contain Chart.js UMD source — "
        "ensure _CHART_JS_INLINE is being embedded in the <script> block."
    )


def test_chart_js_inline_module_variable_is_non_empty():
    """The module-level _CHART_JS_INLINE constant must be loaded from the bundled file."""
    assert _CHART_JS_INLINE, (
        "_CHART_JS_INLINE is empty — ensure src/reports/static/chart.umd.min.js exists."
    )
    assert "Chart" in _CHART_JS_INLINE, (
        "_CHART_JS_INLINE does not contain 'Chart' — the bundled file may be corrupt or wrong."
    )


def test_no_external_script_src_in_generated_html(tmp_path):
    """There must be no <script src='http...> tags loading external resources."""
    out = tmp_path / "report.html"
    reporter = ValidationReporter()
    reporter.generate(_sample_result(), str(out))
    html = out.read_text(encoding="utf-8")
    external_scripts = re.findall(r'<script[^>]+src=["\']https?://', html, re.IGNORECASE)
    assert not external_scripts, (
        f"Found external script tag(s) in generated HTML: {external_scripts}"
    )


def _sample_comparison_result():
    return {
        "total_rows_file1": 2,
        "total_rows_file2": 2,
        "matching_rows": 2,
        "only_in_file1": [],
        "only_in_file2": [],
        "differences": [],
    }


def test_comparison_renderer_no_cdn_url(tmp_path):
    """HTMLReporter (comparison_renderer) must not reference cdn.jsdelivr.net or load scripts via http."""
    out = tmp_path / "comparison_report.html"
    reporter = HTMLReporter()
    reporter.generate(_sample_comparison_result(), str(out))
    html = out.read_text(encoding="utf-8")
    assert "cdn.jsdelivr.net" not in html, (
        "Comparison report still references cdn.jsdelivr.net — no external CDN sources allowed."
    )
    external_scripts = re.findall(r'<script[^>]+src=["\']https?://', html, re.IGNORECASE)
    assert not external_scripts, (
        f"Comparison report contains external <script src='http...'> tag(s): {external_scripts}"
    )

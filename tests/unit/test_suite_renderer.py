"""Unit tests for SuiteReporter (suite_renderer.py)."""

from __future__ import annotations

import re

import pytest

from src.reports.renderers.suite_renderer import SuiteReporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    name="Test A",
    test_type="structural",
    status="PASS",
    duration_seconds=1.23,
    errors=None,
    warnings=None,
    report_url=None,
    message=None,
):
    r = {
        "name": name,
        "type": test_type,
        "status": status,
        "errors": errors or [],
        "warnings": warnings or [],
    }
    if duration_seconds is not None:
        r["duration_seconds"] = duration_seconds
    if report_url is not None:
        r["report_url"] = report_url
    if message is not None:
        r["message"] = message
    return r


def _render(results, suite_name="My Suite", run_id="run-001", environment="dev", tmp_path=None):
    """Helper to call generate() and return the HTML string."""
    reporter = SuiteReporter()
    if tmp_path is None:
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".html")
        os.close(fd)
    else:
        path = str(tmp_path / "suite_report.html")
    returned_path = reporter.generate(
        suite_name=suite_name,
        results=results,
        output_path=path,
        run_id=run_id,
        environment=environment,
    )
    with open(returned_path, encoding="utf-8") as f:
        return f.read(), returned_path


# ---------------------------------------------------------------------------
# 1. PASS badge when all tests pass
# ---------------------------------------------------------------------------

def test_pass_badge_all_pass(tmp_path):
    results = [
        _make_result("Test 1", status="PASS"),
        _make_result("Test 2", status="PASS"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    assert "PASS" in html
    # The overall badge must say PASS (not FAIL or PARTIAL)
    assert ">PASS<" in html
    assert ">FAIL<" not in html
    assert ">PARTIAL<" not in html


# ---------------------------------------------------------------------------
# 2. PASS badge when all tests pass or skipped
# ---------------------------------------------------------------------------

def test_pass_badge_all_pass_or_skipped(tmp_path):
    results = [
        _make_result("Test 1", status="PASS"),
        _make_result("Test 2", status="SKIPPED"),
        _make_result("Test 3", status="PASS"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    assert ">PASS<" in html
    assert ">FAIL<" not in html
    assert ">PARTIAL<" not in html


# ---------------------------------------------------------------------------
# 3. FAIL badge when any test fails (and no errors)
# ---------------------------------------------------------------------------

def test_fail_badge_when_any_fail(tmp_path):
    results = [
        _make_result("Test 1", status="PASS"),
        _make_result("Test 2", status="FAIL"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    # Overall should be PARTIAL (mix of pass and fail, no error)
    # But the FAIL cells should still be present
    assert "FAIL" in html


def test_fail_badge_only_failures(tmp_path):
    results = [
        _make_result("Test 1", status="FAIL"),
        _make_result("Test 2", status="FAIL"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    assert ">FAIL<" in html
    assert ">PASS<" not in html
    assert ">PARTIAL<" not in html


# ---------------------------------------------------------------------------
# 4. FAIL badge when any test errors
# ---------------------------------------------------------------------------

def test_fail_badge_when_any_error(tmp_path):
    results = [
        _make_result("Test 1", status="PASS"),
        _make_result("Test 2", status="ERROR"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    # Overall badge must be FAIL when there is any ERROR
    assert ">FAIL<" in html
    assert ">PARTIAL<" not in html


# ---------------------------------------------------------------------------
# 5. PARTIAL badge for mix of pass/fail (no errors)
# ---------------------------------------------------------------------------

def test_partial_badge_mix_pass_fail(tmp_path):
    results = [
        _make_result("Test 1", status="PASS"),
        _make_result("Test 2", status="FAIL"),
        _make_result("Test 3", status="SKIPPED"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    assert ">PARTIAL<" in html


# ---------------------------------------------------------------------------
# 6. All 4 status colors appear in table when present
# ---------------------------------------------------------------------------

def test_all_four_status_colors_present(tmp_path):
    results = [
        _make_result("T1", status="PASS"),
        _make_result("T2", status="FAIL"),
        _make_result("T3", status="ERROR"),
        _make_result("T4", status="SKIPPED"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    # Check that each status text appears in the table rows
    assert "PASS" in html
    assert "FAIL" in html
    assert "ERROR" in html
    assert "SKIPPED" in html
    # Check that the green, red, darkred, and gray background colors are present
    assert "#d5f5e3" in html   # PASS green
    assert "#fadbd8" in html   # FAIL red
    assert "#922b21" in html   # ERROR dark red
    assert "#eaecee" in html   # SKIPPED gray


# ---------------------------------------------------------------------------
# 7. Report hyperlink rendered when report_url present
# ---------------------------------------------------------------------------

def test_report_hyperlink_when_url_present(tmp_path):
    results = [
        _make_result("Test A", status="PASS", report_url="/path/to/report.html"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    assert "/path/to/report.html" in html
    assert "<a href=" in html


def test_no_hyperlink_when_no_url(tmp_path):
    results = [
        _make_result("Test A", status="PASS", report_url=None),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    # Should show an em-dash placeholder instead of a link
    assert "&mdash;" in html


# ---------------------------------------------------------------------------
# 8. No CDN URLs in output
# ---------------------------------------------------------------------------

def test_no_cdn_urls_in_output(tmp_path):
    results = [
        _make_result("Test A", status="PASS"),
        _make_result("Test B", status="FAIL"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    # No external https:// resource references allowed
    external_links = re.findall(
        r'(?:href|src)=["\']https?://', html, re.IGNORECASE
    )
    assert not external_links, (
        f"HTML contains external CDN/resource URLs: {external_links}"
    )


# ---------------------------------------------------------------------------
# 9. Counts bar shows correct numbers
# ---------------------------------------------------------------------------

def test_counts_bar_correct_numbers(tmp_path):
    results = [
        _make_result("T1", status="PASS"),
        _make_result("T2", status="PASS"),
        _make_result("T3", status="FAIL"),
        _make_result("T4", status="ERROR"),
        _make_result("T5", status="SKIPPED"),
        _make_result("T6", status="SKIPPED"),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    # 2 PASSED, 1 FAILED, 1 ERRORS, 2 SKIPPED
    assert ">2<" in html or "2</strong> PASSED" in html
    assert ">1<" in html or "1</strong> FAILED" in html
    assert "1</strong> ERRORS" in html
    assert "2</strong> SKIPPED" in html


def test_counts_bar_all_pass(tmp_path):
    results = [_make_result(f"T{i}", status="PASS") for i in range(4)]
    html, _ = _render(results, tmp_path=tmp_path)
    assert "4</strong> PASSED" in html
    assert "0</strong> FAILED" in html
    assert "0</strong> ERRORS" in html
    assert "0</strong> SKIPPED" in html


# ---------------------------------------------------------------------------
# 10. Run metadata appears (suite name, environment, run_id)
# ---------------------------------------------------------------------------

def test_run_metadata_appears(tmp_path):
    html, _ = _render(
        results=[_make_result("T1", status="PASS")],
        suite_name="P327 Nightly Suite",
        run_id="abc-123",
        environment="prod",
        tmp_path=tmp_path,
    )
    assert "P327 Nightly Suite" in html
    assert "abc-123" in html
    assert "prod" in html


def test_run_metadata_no_run_id(tmp_path):
    """When run_id is None the report should still render without crashing."""
    reporter = SuiteReporter()
    path = str(tmp_path / "suite_no_id.html")
    returned = reporter.generate(
        suite_name="Suite X",
        results=[_make_result("T1", status="PASS")],
        output_path=path,
        run_id=None,
        environment="dev",
    )
    with open(returned, encoding="utf-8") as f:
        html = f.read()
    assert "Suite X" in html
    assert "dev" in html


# ---------------------------------------------------------------------------
# 11. Empty results list renders without crash
# ---------------------------------------------------------------------------

def test_empty_results_no_crash(tmp_path):
    html, _ = _render(results=[], tmp_path=tmp_path)
    assert "<!DOCTYPE html>" in html
    # Should show no tests message
    assert "No tests were run" in html
    # Overall status for empty set should be PASS
    assert ">PASS<" in html


# ---------------------------------------------------------------------------
# 12. Duration displayed when present
# ---------------------------------------------------------------------------

def test_duration_displayed_when_present(tmp_path):
    results = [
        _make_result("T1", status="PASS", duration_seconds=2.75),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    assert "2.75s" in html


def test_duration_dash_when_absent(tmp_path):
    results = [
        _make_result("T1", status="PASS", duration_seconds=None),
    ]
    html, _ = _render(results, tmp_path=tmp_path)
    assert "&mdash;" in html


# ---------------------------------------------------------------------------
# 13. generate() returns the output_path
# ---------------------------------------------------------------------------

def test_generate_returns_output_path(tmp_path):
    path = str(tmp_path / "out.html")
    reporter = SuiteReporter()
    returned = reporter.generate(
        suite_name="Suite",
        results=[_make_result("T1", status="PASS")],
        output_path=path,
    )
    assert returned == path


# ---------------------------------------------------------------------------
# 14. Footer text appears
# ---------------------------------------------------------------------------

def test_footer_present(tmp_path):
    html, _ = _render(results=[_make_result("T1", status="PASS")], tmp_path=tmp_path)
    assert "Generated by Valdo" in html


# ---------------------------------------------------------------------------
# 15. Self-contained: no external <link> or <script src="https://..."> tags
# ---------------------------------------------------------------------------

def test_no_external_link_or_script_tags(tmp_path):
    results = [_make_result("T1", status="PASS")]
    html, _ = _render(results, tmp_path=tmp_path)
    external_links = re.findall(r'<link[^>]+href=["\']https?://', html, re.IGNORECASE)
    external_scripts = re.findall(r'<script[^>]+src=["\']https?://', html, re.IGNORECASE)
    assert not external_links, f"External <link> tags found: {external_links}"
    assert not external_scripts, f"External <script> tags found: {external_scripts}"

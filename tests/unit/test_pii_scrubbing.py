"""Tests for PII scrubbing in HTML validation reports (issue #18).

Verifies that ``ValidationReporter`` redacts raw field values from error
messages and affected-row details when ``suppress_pii=True`` (the default),
and preserves them when ``suppress_pii=False``.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest

from src.reports.renderers.validation_renderer import ValidationReporter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_results(
    errors: list[dict] | None = None,
    warnings: list[dict] | None = None,
    business_rules: dict | None = None,
    appendix: dict | None = None,
) -> Dict[str, Any]:
    """Build a minimal validation-results dict accepted by ValidationReporter."""
    return {
        "valid": False,
        "total_rows": 100,
        "valid_rows": 90,
        "error_count": len(errors or []),
        "warning_count": len(warnings or []),
        "errors": errors or [],
        "warnings": warnings or [],
        "info": [],
        "quality_metrics": {"quality_score": 75, "total_rows": 100},
        "field_analysis": {},
        "business_rules": business_rules,
        "appendix": appendix or {},
        "file_metadata": {"file_name": "test_data.txt", "file_size": 1024},
        "timestamp": "2026-03-25T00:00:00",
    }


def _generate_html(results: Dict[str, Any], suppress_pii: bool = True) -> str:
    """Generate an HTML report string using ValidationReporter.

    Returns the HTML content as a string (file is cleaned up automatically).
    """
    reporter = ValidationReporter()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_path = os.path.join(tmpdir, "report.html")
        reporter.generate(results, out_path, suppress_pii=suppress_pii)
        return Path(out_path).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Test: field values are redacted by default
# ---------------------------------------------------------------------------

class TestPiiRedactionDefault:
    """When suppress_pii=True (default), raw field values must be replaced."""

    def test_value_single_quoted_redacted(self):
        results = _minimal_results(errors=[
            {"row": 1, "severity": "error", "message": "value 'XSENSITIVE99' failed regex check"},
        ])
        html = _generate_html(results, suppress_pii=True)
        assert "XSENSITIVE99" not in html
        assert "[REDACTED]" in html

    def test_value_double_quoted_redacted(self):
        results = _minimal_results(errors=[
            {"row": 2, "severity": "error", "message": 'value "SensitiveData" failed regex check'},
        ])
        html = _generate_html(results, suppress_pii=True)
        assert "SensitiveData" not in html
        assert "[REDACTED]" in html

    def test_value_unquoted_redacted(self):
        results = _minimal_results(errors=[
            {"row": 3, "severity": "error", "message": "value 987654321 failed regex check"},
        ])
        html = _generate_html(results, suppress_pii=True)
        assert "987654321" not in html
        assert "[REDACTED]" in html

    def test_got_pattern_redacted(self):
        results = _minimal_results(errors=[
            {"row": 4, "severity": "error", "message": "expected numeric, got 'ABC123' in field"},
        ])
        html = _generate_html(results, suppress_pii=True)
        assert "ABC123" not in html

    def test_default_is_suppress_pii_true(self):
        """Calling generate() without suppress_pii still redacts by default."""
        reporter = ValidationReporter()
        results = _minimal_results(errors=[
            {"row": 1, "severity": "error", "message": "value 'SENSITIVE' failed regex"},
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "report.html")
            reporter.generate(results, out_path)  # no suppress_pii arg
            html = Path(out_path).read_text(encoding="utf-8")
        assert "SENSITIVE" not in html
        assert "[REDACTED]" in html


# ---------------------------------------------------------------------------
# Test: row numbers and error codes still appear
# ---------------------------------------------------------------------------

class TestRowNumbersAndCodesPreserved:
    """Row numbers and structural metadata must survive redaction."""

    def test_row_number_preserved(self):
        results = _minimal_results(errors=[
            {"row": 42, "source_row": 42, "severity": "error",
             "message": "value '999-00-0000' failed regex check"},
        ])
        html = _generate_html(results, suppress_pii=True)
        # Row number 42 should appear in the report
        assert "42" in html
        # The SSN-like value should NOT appear
        assert "999-00-0000" not in html

    def test_error_severity_preserved(self):
        results = _minimal_results(errors=[
            {"row": 1, "severity": "error", "code": "FW_REQ_001",
             "message": "value 'PII_VALUE' failed regex check"},
        ])
        html = _generate_html(results, suppress_pii=True)
        assert "error" in html.lower()

    def test_error_count_preserved(self):
        errors = [
            {"row": i, "severity": "error", "message": f"value '{i * 111}' failed regex"}
            for i in range(1, 4)
        ]
        results = _minimal_results(errors=errors)
        html = _generate_html(results, suppress_pii=True)
        # The count "Errors (3)" should appear
        assert "Errors (3)" in html


# ---------------------------------------------------------------------------
# Test: error messages with values are masked
# ---------------------------------------------------------------------------

class TestErrorMessageMasking:
    """Specific value patterns inside messages must be replaced."""

    def test_value_failed_regex_masked(self):
        reporter = ValidationReporter()
        reporter._suppress_pii = True
        msg = "value '123456789' failed regex check"
        result = reporter._redact_message(msg)
        assert "123456789" not in result
        assert "[REDACTED]" in result
        assert "failed regex" in result.lower() or "failed" in result.lower()

    def test_value_is_invalid_masked(self):
        reporter = ValidationReporter()
        reporter._suppress_pii = True
        msg = "value 'john.doe@example.com' is invalid for field EMAIL"
        result = reporter._redact_message(msg)
        assert "john.doe@example.com" not in result
        assert "[REDACTED]" in result

    def test_got_value_masked(self):
        reporter = ValidationReporter()
        reporter._suppress_pii = True
        msg = "expected numeric, got 'ABC' in column PHONE"
        result = reporter._redact_message(msg)
        assert "ABC" not in result
        assert "[REDACTED]" in result

    def test_multiple_values_masked(self):
        reporter = ValidationReporter()
        reporter._suppress_pii = True
        msg = "value '111' failed regex; got '222' extra"
        result = reporter._redact_message(msg)
        assert "111" not in result
        assert "222" not in result

    def test_no_value_pattern_unchanged(self):
        reporter = ValidationReporter()
        reporter._suppress_pii = True
        msg = "Missing required field ACCOUNT_NUMBER"
        result = reporter._redact_message(msg)
        assert result == msg  # no value pattern, should be unchanged


# ---------------------------------------------------------------------------
# Test: --no-suppress-pii shows raw values
# ---------------------------------------------------------------------------

class TestNoSuppressPii:
    """When suppress_pii=False, raw values must appear verbatim."""

    def test_raw_values_preserved(self):
        results = _minimal_results(errors=[
            {"row": 1, "severity": "error", "message": "value '123456789' failed regex check"},
        ])
        html = _generate_html(results, suppress_pii=False)
        assert "123456789" in html
        assert "[REDACTED]" not in html

    def test_got_value_preserved(self):
        results = _minimal_results(errors=[
            {"row": 2, "severity": "error", "message": "expected numeric, got 'ABC123' in field"},
        ])
        html = _generate_html(results, suppress_pii=False)
        assert "ABC123" in html

    def test_redact_message_noop_when_disabled(self):
        reporter = ValidationReporter()
        reporter._suppress_pii = False
        msg = "value '123456789' failed regex check"
        assert reporter._redact_message(msg) == msg


# ---------------------------------------------------------------------------
# Test: CSV sidecars also respect suppress_pii
# ---------------------------------------------------------------------------

class TestCsvSidecarRedaction:
    """CSV sidecar files should also redact PII when suppress_pii=True."""

    def test_errors_csv_redacted(self):
        results = _minimal_results(errors=[
            {"row": 1, "severity": "error", "message": "value '999-00-1234' failed regex"},
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "report.html")
            reporter = ValidationReporter()
            reporter.generate(results, out_path, suppress_pii=True)
            csv_path = Path(tmpdir) / "report_errors.csv"
            csv_content = csv_path.read_text(encoding="utf-8")
        assert "999-00-1234" not in csv_content
        assert "[REDACTED]" in csv_content

    def test_warnings_csv_redacted(self):
        results = _minimal_results(warnings=[
            {"row": 5, "severity": "warning", "message": "value 'SSN12345' is invalid format"},
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "report.html")
            reporter = ValidationReporter()
            reporter.generate(results, out_path, suppress_pii=True)
            csv_path = Path(tmpdir) / "report_warnings.csv"
            csv_content = csv_path.read_text(encoding="utf-8")
        assert "SSN12345" not in csv_content
        assert "[REDACTED]" in csv_content

    def test_csv_not_redacted_when_disabled(self):
        results = _minimal_results(errors=[
            {"row": 1, "severity": "error", "message": "value '999-00-1234' failed regex"},
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "report.html")
            reporter = ValidationReporter()
            reporter.generate(results, out_path, suppress_pii=False)
            csv_path = Path(tmpdir) / "report_errors.csv"
            csv_content = csv_path.read_text(encoding="utf-8")
        assert "999-00-1234" in csv_content


# ---------------------------------------------------------------------------
# Test: affected rows / business rules redaction
# ---------------------------------------------------------------------------

class TestAffectedRowsRedaction:
    """Top-problematic-rows issue strings must be redacted."""

    def test_problematic_row_issues_redacted(self):
        results = _minimal_results(
            errors=[],
            appendix={
                "affected_rows": {
                    "total_affected_rows": 1,
                    "affected_row_pct": 1.0,
                    "top_problematic_rows": [
                        {
                            "row_number": 7,
                            "issue_count": 1,
                            "issues": ["value '555-12-3456' failed regex check"],
                        }
                    ],
                },
            },
        )
        html = _generate_html(results, suppress_pii=True)
        assert "555-12-3456" not in html
        assert "7" in html  # row number preserved


class TestBusinessRulesRedaction:
    """Business-rule violation sample messages must be redacted."""

    def test_violation_message_redacted(self):
        results = _minimal_results(
            business_rules={
                "enabled": True,
                "statistics": {
                    "total_rules": 1,
                    "executed_rules": 1,
                    "total_violations": 1,
                    "compliance_rate": 99.0,
                },
                "violations": [
                    {
                        "rule_id": "R001",
                        "rule_name": "SSN Format",
                        "severity": "error",
                        "row_number": 10,
                        "message": "value '123-45-6789' does not match expected pattern",
                    }
                ],
            },
        )
        html = _generate_html(results, suppress_pii=True)
        assert "123-45-6789" not in html
        assert "Row 10" in html  # row number preserved
        assert "[REDACTED]" in html

"""Tests for threshold evaluation and report generation — issue #102.

Covers pass-by-error-count, boundary exact match, HTML report summary,
and JSON report machine-parseability.
"""

import json
import tempfile
from pathlib import Path

import pytest

from src.validators.threshold import (
    Threshold,
    ThresholdEvaluator,
    ThresholdResult,
)


def _make_comparison(missing: int = 0, extra: int = 0, different: int = 0):
    """Build a minimal comparison-results dict."""
    return {
        "total_rows_file1": 100,
        "total_rows_file2": 100,
        "only_in_file1": [{"id": i} for i in range(missing)],
        "only_in_file2": [{"id": i} for i in range(extra)],
        "differences": [{"row": i} for i in range(different)],
        "rows_with_differences": different,
        "field_statistics": {},
    }


class TestThresholdReports:
    """Threshold and report tests for issue #102."""

    def test_threshold_pass_by_error_count(self):
        """Evaluation should PASS when all error counts are within thresholds."""
        evaluator = ThresholdEvaluator()
        # Default max_value for missing_rows is 10 — use 3 (well under)
        result = evaluator.evaluate(_make_comparison(missing=3))

        assert result["passed"] is True
        assert result["overall_result"] == ThresholdResult.PASS
        assert result["metrics"]["missing_rows"] == 3

    def test_threshold_boundary_exact_match(self):
        """Evaluation at exactly the max_value boundary should PASS (not exceed)."""
        custom = {
            "missing_rows": Threshold(
                name="Missing Rows",
                metric="missing_rows",
                max_value=10,
                max_percent=None,
            )
        }
        evaluator = ThresholdEvaluator(thresholds=custom)
        result = evaluator.evaluate(_make_comparison(missing=10))

        # max_value check is "> max_value", so exactly 10 should still pass.
        assert result["passed"] is True
        assert result["metrics"]["missing_rows"] == 10

    def test_html_report_contains_summary_section(self, tmp_path):
        """HTML validation report should contain a recognisable summary section."""
        from src.services.validate_service import run_validate_service

        # Create a small valid pipe-delimited file
        data_file = tmp_path / "data.txt"
        data_file.write_text("Alice|30\nBob|25\n", encoding="utf-8")
        mapping = {
            "mapping_name": "html_test",
            "version": "1.0.0",
            "source": {"type": "file", "format": "pipe_delimited", "has_header": False},
            "fields": [
                {"name": "name", "data_type": "string"},
                {"name": "age", "data_type": "integer"},
            ],
        }
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(json.dumps(mapping), encoding="utf-8")

        output_html = str(tmp_path / "report.html")
        run_validate_service(
            file=str(data_file),
            mapping=str(mapping_file),
            output=output_html,
        )

        html = Path(output_html).read_text(encoding="utf-8")
        # The HTML report should contain recognisable summary markers
        assert "<html" in html.lower()
        assert "summary" in html.lower() or "validation" in html.lower()

    def test_json_report_is_machine_parseable(self, tmp_path):
        """Validation result dict must contain expected contract keys and be
        serialisable to JSON (after converting numpy types)."""
        from src.services.validate_service import run_validate_service

        data_file = tmp_path / "data.txt"
        data_file.write_text("Alice|30\nBob|25\n", encoding="utf-8")
        mapping = {
            "mapping_name": "json_test",
            "version": "1.0.0",
            "source": {"type": "file", "format": "pipe_delimited", "has_header": False},
            "fields": [
                {"name": "name", "data_type": "string"},
                {"name": "age", "data_type": "integer"},
            ],
        }
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(json.dumps(mapping), encoding="utf-8")

        result = run_validate_service(
            file=str(data_file),
            mapping=str(mapping_file),
        )

        # Result must have the standard contract keys
        assert isinstance(result, dict)
        assert "total_rows" in result
        assert "error_count" in result
        assert "valid" in result

        # Result should be JSON-round-trippable (using default=str for numpy)
        serialized = json.dumps(result, default=str)
        loaded = json.loads(serialized)
        assert loaded["total_rows"] == result["total_rows"]

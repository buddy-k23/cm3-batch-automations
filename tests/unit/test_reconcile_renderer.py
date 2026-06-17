"""Unit tests for ReconcileReporter (reconcile_renderer.py, S23-3, #445).

Covers:
* Rendering a single-mapping verdict (with type mismatches + advisories +
  errors + unmapped-required) to a real HTML file.
* Rendering a reconcile-all aggregate (with baseline drift) to HTML.
* PII redaction parity with the other renderers.
* The CLI surface: ``valdo reconcile --output *.html`` and
  ``valdo reconcile-all --output *.html`` write HTML (``.json`` still JSON).
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from src.main import cli
from src.reports.renderers.reconcile_renderer import ReconcileReporter


# ---------------------------------------------------------------------------
# Fixtures / sample data
# ---------------------------------------------------------------------------


def _single_verdict() -> dict:
    """A single-mapping verdict with errors, mismatches, and advisories."""
    return {
        "status": "mismatch",
        "valid": False,
        "mapping_name": "customer_mapping",
        "table": "CM3INT.CUSTOMER",
        "schema": "CM3INT",
        "db_adapter": "SQLiteAdapter",
        "summary": {
            "mapped_columns": 12,
            "database_columns": 14,
            "error_count": 1,
            "warning_count": 3,
            "advisory_count": 1,
            "mismatch_count": 2,
        },
        "errors": ["Column 'MISSING_COL' not found in table"],
        "mismatches": [
            "Type mismatch for ACCT_BALANCE: mapping NUMBER vs db VARCHAR2",
            "Type mismatch for OPEN_DATE: mapping DATE vs db VARCHAR2",
        ],
        "advisories": ["No native boolean for IS_ACTIVE; mapped to CHAR(1)"],
        "warnings": ["..."],
        "unmapped_required": ["LEGAL_NAME"],
    }


def _aggregate_with_drift() -> dict:
    """A reconcile-all aggregate that carries a baseline drift block."""
    return {
        "total_mappings": 3,
        "valid_mappings": 2,
        "invalid_mappings": 1,
        "total_errors": 2,
        "total_warnings": 4,
        "results": [
            {
                "mapping_file": "config/mappings/a.json",
                "mapping_name": "mapping_a",
                "valid": True,
                "error_count": 0,
                "warning_count": 1,
                "errors": [],
                "warnings": ["w"],
            },
            {
                "mapping_file": "config/mappings/b.json",
                "mapping_name": "mapping_b",
                "valid": False,
                "error_count": 2,
                "warning_count": 3,
                "errors": ["e1", "e2"],
                "warnings": ["w"],
            },
        ],
        "drift": {
            "baseline": "baseline.json",
            "added_files": ["config/mappings/a.json"],
            "removed_files": ["config/mappings/old.json"],
            "changed": [
                {
                    "mapping_file": "config/mappings/b.json",
                    "old_errors": 0,
                    "new_errors": 2,
                    "delta_errors": 2,
                    "old_warnings": 1,
                    "new_warnings": 3,
                    "delta_warnings": 2,
                }
            ],
            "new_errors": 2,
            "new_warnings": 2,
        },
    }


# ---------------------------------------------------------------------------
# Single-mapping verdict
# ---------------------------------------------------------------------------


class TestSingleVerdict:
    def test_produces_real_html_file(self, tmp_path):
        out = tmp_path / "reconcile.html"
        path = ReconcileReporter().generate(_single_verdict(), str(out))

        assert path == str(out)
        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_renders_header_status_and_counts(self, tmp_path):
        out = tmp_path / "r.html"
        ReconcileReporter().generate(_single_verdict(), str(out))
        html = out.read_text(encoding="utf-8")

        assert "customer_mapping" in html
        assert "CM3INT.CUSTOMER" in html
        assert "MISMATCHES" in html
        # status badge text
        assert "mismatch" in html

    def test_renders_mismatches_and_advisories_sections(self, tmp_path):
        out = tmp_path / "r.html"
        ReconcileReporter().generate(_single_verdict(), str(out))
        html = out.read_text(encoding="utf-8")

        assert "Type Mismatches (2)" in html
        assert "ACCT_BALANCE" in html
        assert "Advisories (1)" in html
        assert "No native boolean" in html
        assert "Errors (1)" in html
        assert "Unmapped Required Fields (1)" in html
        assert "LEGAL_NAME" in html

    def test_reuses_shared_shell_styling(self, tmp_path):
        """The report reuses the established Valdo report look, not a new one."""
        out = tmp_path / "r.html"
        ReconcileReporter().generate(_single_verdict(), str(out))
        html = out.read_text(encoding="utf-8")

        # Same font stack / page background as suite & validation renderers.
        assert "-apple-system" in html
        assert "#f5f7fa" in html
        assert "Generated by Valdo" in html

    def test_empty_verdict_renders_none_placeholders(self, tmp_path):
        out = tmp_path / "r.html"
        clean = {
            "status": "clean",
            "mapping_name": "m",
            "table": "T",
            "db_adapter": "SQLiteAdapter",
            "summary": {},
            "errors": [],
            "mismatches": [],
            "advisories": [],
            "unmapped_required": [],
        }
        ReconcileReporter().generate(clean, str(out))
        html = out.read_text(encoding="utf-8")
        assert "clean" in html
        assert "None." in html

    def test_pii_redaction_default_on(self, tmp_path):
        out = tmp_path / "r.html"
        verdict = _single_verdict()
        verdict["mismatches"] = ["Field FOO got 'SECRET123' which is invalid"]
        ReconcileReporter().generate(verdict, str(out))
        html = out.read_text(encoding="utf-8")
        assert "SECRET123" not in html
        assert "[REDACTED]" in html

    def test_pii_redaction_opt_out(self, tmp_path):
        out = tmp_path / "r.html"
        verdict = _single_verdict()
        verdict["mismatches"] = ["Field FOO got 'SECRET123' which is invalid"]
        ReconcileReporter().generate(verdict, str(out), suppress_pii=False)
        html = out.read_text(encoding="utf-8")
        assert "SECRET123" in html

    def test_html_escaping(self, tmp_path):
        out = tmp_path / "r.html"
        verdict = _single_verdict()
        verdict["errors"] = ["<script>alert(1)</script>"]
        ReconcileReporter().generate(verdict, str(out))
        html = out.read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in html
        assert "&lt;script&gt;" in html


# ---------------------------------------------------------------------------
# reconcile-all aggregate
# ---------------------------------------------------------------------------


class TestAggregate:
    def test_produces_real_html_file(self, tmp_path):
        out = tmp_path / "all.html"
        path = ReconcileReporter().generate_all(_aggregate_with_drift(), str(out))
        assert path == str(out)
        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_renders_per_mapping_results(self, tmp_path):
        out = tmp_path / "all.html"
        ReconcileReporter().generate_all(_aggregate_with_drift(), str(out))
        html = out.read_text(encoding="utf-8")
        assert "Per-Mapping Results" in html
        assert "mapping_a" in html
        assert "mapping_b" in html
        assert "VALID" in html
        assert "INVALID" in html

    def test_renders_drift_block(self, tmp_path):
        out = tmp_path / "all.html"
        ReconcileReporter().generate_all(_aggregate_with_drift(), str(out))
        html = out.read_text(encoding="utf-8")
        assert "Baseline Drift" in html
        assert "baseline.json" in html
        assert "NEW ERRORS" in html
        # changed row shows signed deltas
        assert "+2" in html

    def test_no_drift_when_absent(self, tmp_path):
        out = tmp_path / "all.html"
        agg = _aggregate_with_drift()
        del agg["drift"]
        ReconcileReporter().generate_all(agg, str(out))
        html = out.read_text(encoding="utf-8")
        assert "Baseline Drift" not in html

    def test_empty_results(self, tmp_path):
        out = tmp_path / "all.html"
        agg = {
            "total_mappings": 0,
            "valid_mappings": 0,
            "invalid_mappings": 0,
            "total_errors": 0,
            "total_warnings": 0,
            "results": [],
        }
        ReconcileReporter().generate_all(agg, str(out))
        html = out.read_text(encoding="utf-8")
        assert "ALL VALID" in html
        assert "No mapping files processed." in html


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


class TestCliOutputRouting:
    def test_reconcile_html_output_writes_html(self, tmp_path):
        out = tmp_path / "verdict.html"
        runner = CliRunner()
        with patch(
            "src.services.reconcile_service.reconcile_mapping_service",
            return_value=_single_verdict(),
        ):
            result = runner.invoke(
                cli,
                ["reconcile", "--mapping", "customer_mapping", "--output", str(out)],
            )

        assert result.exit_code == 1  # invalid verdict -> non-zero
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "Reconciliation Report" in html

    def test_reconcile_json_output_still_json(self, tmp_path):
        out = tmp_path / "verdict.json"
        runner = CliRunner()
        with patch(
            "src.services.reconcile_service.reconcile_mapping_service",
            return_value=_single_verdict(),
        ):
            result = runner.invoke(
                cli,
                ["reconcile", "--mapping", "customer_mapping", "--output", str(out)],
            )

        assert result.exit_code == 1
        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["mapping_name"] == "customer_mapping"

    def test_reconcile_all_html_output_writes_html(self, tmp_path):
        out = tmp_path / "all.html"
        runner = CliRunner()
        with patch(
            "src.services.reconcile_all_service.reconcile_all_service",
            return_value=_aggregate_with_drift(),
        ):
            result = runner.invoke(
                cli,
                ["reconcile-all", "--output", str(out)],
            )

        # one invalid mapping -> non-zero exit, but file must be written first
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        assert html.startswith("<!DOCTYPE html>")
        assert "Reconcile-All Report" in html
        assert "Baseline Drift" in html

    def test_reconcile_all_json_output_still_json(self, tmp_path):
        out = tmp_path / "all.json"
        runner = CliRunner()
        with patch(
            "src.services.reconcile_all_service.reconcile_all_service",
            return_value=_aggregate_with_drift(),
        ):
            runner.invoke(cli, ["reconcile-all", "--output", str(out)])

        loaded = json.loads(out.read_text(encoding="utf-8"))
        assert loaded["total_mappings"] == 3

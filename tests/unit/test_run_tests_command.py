"""Unit tests for the run-tests orchestrator command (#22 #25)."""
from __future__ import annotations

import textwrap
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner

from src.contracts.test_suite import TestConfig, TestSuiteConfig, ThresholdConfig
from src.utils.params import resolve_params


# ---------------------------------------------------------------------------
# 1. resolve_params — built-in variable substitution
# ---------------------------------------------------------------------------

class TestResolveParams:
    def test_substitutes_today(self):
        today_str = date.today().strftime("%Y%m%d")
        result = resolve_params("file_${today}.txt", {})
        assert result == f"file_{today_str}.txt"

    def test_substitutes_yesterday(self):
        yesterday_str = (date.today() - timedelta(days=1)).strftime("%Y%m%d")
        result = resolve_params("file_${yesterday}.dat", {})
        assert result == f"file_{yesterday_str}.dat"

    def test_substitutes_run_id(self):
        fixed_run_id = str(uuid.uuid4())
        result = resolve_params("run-${run_id}.log", {"run_id": fixed_run_id})
        assert result == f"run-{fixed_run_id}.log"

    def test_substitutes_custom_params(self):
        result = resolve_params("${region}_${batch}.txt", {"region": "APAC", "batch": "B01"})
        assert result == "APAC_B01.txt"

    def test_substitutes_environment_default_empty(self):
        result = resolve_params("${environment}", {})
        assert result == ""

    def test_substitutes_environment_from_params(self):
        result = resolve_params("${environment}", {"environment": "prod"})
        assert result == "prod"

    def test_no_placeholders_returns_unchanged(self):
        result = resolve_params("plain_string.txt", {})
        assert result == "plain_string.txt"

    def test_multiple_same_placeholder(self):
        today_str = date.today().strftime("%Y%m%d")
        result = resolve_params("${today}/${today}.txt", {})
        assert result == f"{today_str}/{today_str}.txt"


# ---------------------------------------------------------------------------
# 2. resolve_params — raises on unresolved placeholder
# ---------------------------------------------------------------------------

class TestResolveParamsErrors:
    def test_raises_on_unknown_variable(self):
        with pytest.raises(ValueError, match=r"\$\{unknown\}"):
            resolve_params("file_${unknown}.txt", {})

    def test_raises_on_multiple_unknown_variables(self):
        with pytest.raises(ValueError, match="Unresolved"):
            resolve_params("${foo}_${bar}.txt", {})

    def test_known_variable_does_not_raise(self):
        # Sanity check — supplying the variable should succeed.
        result = resolve_params("${custom}", {"custom": "value"})
        assert result == "value"


# ---------------------------------------------------------------------------
# 3. TestSuiteConfig — YAML round-trip validation
# ---------------------------------------------------------------------------

SUITE_YAML = textwrap.dedent("""\
    name: P327 UAT
    environment: uat
    tests:
      - name: P327 File Structure Check
        type: structural
        file: /data/${today}/p327.dat
        mapping: config/mappings/p327.json
        thresholds:
          max_errors: 0

      - name: Business Rules Check
        type: rules
        file: /data/${today}/p327.dat
        mapping: config/mappings/p327.json
        rules: config/rules/p327.json
        thresholds:
          max_errors: 5
          max_warnings: 10

      - name: Oracle vs File
        type: oracle_vs_file
        file: /data/${today}/p327_oracle.dat
        mapping: config/mappings/p327.json
        oracle_query: SELECT * FROM p327_view
        key_columns: [ACCOUNT_NO]
        thresholds:
          max_errors: 0
          max_missing_rows: 0
          max_extra_rows: 0
          max_different_rows_pct: 0.5
""")


class TestSuiteYamlLoading:
    def test_valid_yaml_loads_into_test_suite_config(self, tmp_path):
        suite_file = tmp_path / "suite.yaml"
        suite_file.write_text(SUITE_YAML)

        raw = yaml.safe_load(suite_file.read_text())
        suite = TestSuiteConfig(**raw)

        assert suite.name == "P327 UAT"
        assert suite.environment == "uat"
        assert len(suite.tests) == 3

    def test_first_test_is_structural(self, tmp_path):
        raw = yaml.safe_load(SUITE_YAML)
        suite = TestSuiteConfig(**raw)
        t = suite.tests[0]
        assert t.type == "structural"
        assert "${today}" in t.file
        assert t.thresholds.max_errors == 0

    def test_rules_test_has_rules_field(self, tmp_path):
        raw = yaml.safe_load(SUITE_YAML)
        suite = TestSuiteConfig(**raw)
        t = suite.tests[1]
        assert t.type == "rules"
        assert t.rules == "config/rules/p327.json"
        assert t.thresholds.max_warnings == 10

    def test_oracle_test_has_key_columns(self, tmp_path):
        raw = yaml.safe_load(SUITE_YAML)
        suite = TestSuiteConfig(**raw)
        t = suite.tests[2]
        assert t.type == "oracle_vs_file"
        assert t.key_columns == ["ACCOUNT_NO"]
        assert t.thresholds.max_different_rows_pct == 0.5

    def test_defaults_applied_when_optional_fields_absent(self):
        minimal = {
            "name": "Minimal Suite",
            "tests": [
                {"name": "T1", "type": "structural", "file": "f.dat", "mapping": "m.json"}
            ],
        }
        suite = TestSuiteConfig(**minimal)
        assert suite.environment == "dev"
        assert suite.tests[0].thresholds.max_errors == 0
        assert suite.tests[0].thresholds.max_warnings is None


# ---------------------------------------------------------------------------
# 4. dry-run: prints config without calling services
# ---------------------------------------------------------------------------

MINIMAL_SUITE_YAML = textwrap.dedent("""\
    name: Minimal Suite
    environment: dev
    tests:
      - name: File Check
        type: structural
        file: /data/${today}/file.dat
        mapping: config/mappings/m.json
""")


class TestDryRun:
    def test_dry_run_returns_empty_results(self, tmp_path):
        suite_file = tmp_path / "suite.yaml"
        suite_file.write_text(MINIMAL_SUITE_YAML)

        from src.commands.run_tests_command import run_tests_command

        with patch("src.services.validate_service.run_validate_service") as mock_svc:
            results = run_tests_command(
                suite_path=str(suite_file),
                params_str="",
                env="dev",
                output_dir=str(tmp_path / "reports"),
                dry_run=True,
            )

        assert results == []
        mock_svc.assert_not_called()

    def test_dry_run_prints_resolved_file(self, tmp_path):
        suite_file = tmp_path / "suite.yaml"
        suite_file.write_text(MINIMAL_SUITE_YAML)
        today_str = date.today().strftime("%Y%m%d")

        from src.main import cli

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "run-tests",
                "--suite", str(suite_file),
                "--env", "dev",
                "--output-dir", str(tmp_path / "reports"),
                "--dry-run",
            ],
        )

        assert "Minimal Suite" in result.output
        assert today_str in result.output
        assert "file.dat" in result.output


# ---------------------------------------------------------------------------
# 5. Structural test PASS — mock validate service returns 0 errors
# ---------------------------------------------------------------------------

class TestStructuralTestPass:
    def test_structural_test_passes_when_no_errors(self, tmp_path):
        suite_file = tmp_path / "suite.yaml"
        suite_file.write_text(MINIMAL_SUITE_YAML)

        mock_result = {
            "valid": True,
            "total_rows": 1000,
            "row_count": 1000,
            "error_count": 0,
            "warning_count": 0,
            "errors": [],
            "warnings": [],
        }

        from src.commands.run_tests_command import run_tests_command

        with patch("src.commands.run_tests_command._run_single_test") as mock_run:
            mock_run.return_value = {
                "name": "File Check",
                "type": "structural",
                "status": "PASS",
                "total_rows": 1000,
                "error_count": 0,
                "warning_count": 0,
                "duration_seconds": 1.0,
                "report_path": None,
                "detail": "",
            }
            results = run_tests_command(
                suite_path=str(suite_file),
                params_str="",
                env="dev",
                output_dir=str(tmp_path / "reports"),
                dry_run=False,
            )

        assert len(results) == 1
        assert results[0]["status"] == "PASS"
        assert results[0]["error_count"] == 0


# ---------------------------------------------------------------------------
# 6. Structural test FAIL — mock returns 5 errors, threshold=0
# ---------------------------------------------------------------------------

class TestStructuralTestFail:
    def test_structural_test_fails_when_errors_exceed_threshold(self, tmp_path):
        suite_file = tmp_path / "suite.yaml"
        suite_file.write_text(MINIMAL_SUITE_YAML)

        from src.commands.run_tests_command import run_tests_command

        with patch("src.commands.run_tests_command._run_single_test") as mock_run:
            mock_run.return_value = {
                "name": "File Check",
                "type": "structural",
                "status": "FAIL",
                "total_rows": 1000,
                "error_count": 5,
                "warning_count": 0,
                "duration_seconds": 1.2,
                "report_path": None,
                "detail": "error_count 5 exceeds max_errors 0",
            }
            results = run_tests_command(
                suite_path=str(suite_file),
                params_str="",
                env="dev",
                output_dir=str(tmp_path / "reports"),
                dry_run=False,
            )

        assert len(results) == 1
        assert results[0]["status"] == "FAIL"
        assert results[0]["error_count"] == 5

    def test_threshold_check_fails_when_errors_exceed_max(self):
        from src.commands.run_tests_command import _check_thresholds

        test = TestConfig(
            name="T",
            type="structural",
            file="f.dat",
            mapping="m.json",
            thresholds=ThresholdConfig(max_errors=0),
        )
        status, detail = _check_thresholds(test, {"error_count": 5, "warning_count": 0})
        assert status == "FAIL"
        assert "5" in detail

    def test_threshold_check_passes_when_errors_at_max(self):
        from src.commands.run_tests_command import _check_thresholds

        test = TestConfig(
            name="T",
            type="structural",
            file="f.dat",
            mapping="m.json",
            thresholds=ThresholdConfig(max_errors=5),
        )
        status, detail = _check_thresholds(test, {"error_count": 5, "warning_count": 0})
        assert status == "PASS"

    def test_threshold_check_warning_limit(self):
        from src.commands.run_tests_command import _check_thresholds

        test = TestConfig(
            name="T",
            type="rules",
            file="f.dat",
            mapping="m.json",
            thresholds=ThresholdConfig(max_errors=10, max_warnings=3),
        )
        status, detail = _check_thresholds(test, {"error_count": 0, "warning_count": 4})
        assert status == "FAIL"
        assert "warning_count" in detail


# ---------------------------------------------------------------------------
# 7. Exit code is 1 when any test fails — via Click test runner
# ---------------------------------------------------------------------------

class TestExitCode:
    def _make_suite(self, tmp_path: Path) -> Path:
        suite_file = tmp_path / "suite.yaml"
        suite_file.write_text(MINIMAL_SUITE_YAML)
        return suite_file

    def test_exit_code_0_when_all_pass(self, tmp_path):
        from src.main import cli

        suite_file = self._make_suite(tmp_path)
        runner = CliRunner()

        pass_result = {
            "name": "File Check",
            "type": "structural",
            "status": "PASS",
            "total_rows": 100,
            "error_count": 0,
            "warning_count": 0,
            "duration_seconds": 0.5,
            "report_path": None,
            "detail": "",
        }

        with patch("src.commands.run_tests_command.run_tests_command", return_value=[pass_result]):
            result = runner.invoke(cli, ["run-tests", "--suite", str(suite_file)])

        assert result.exit_code == 0

    def test_exit_code_is_1_when_any_test_fails(self, tmp_path):
        from src.main import cli

        suite_file = self._make_suite(tmp_path)
        runner = CliRunner()

        fail_result = {
            "name": "File Check",
            "type": "structural",
            "status": "FAIL",
            "total_rows": 100,
            "error_count": 5,
            "warning_count": 0,
            "duration_seconds": 0.5,
            "report_path": None,
            "detail": "error_count 5 exceeds max_errors 0",
        }

        with patch("src.commands.run_tests_command.run_tests_command", return_value=[fail_result]):
            result = runner.invoke(cli, ["run-tests", "--suite", str(suite_file)])

        assert result.exit_code == 1

    def test_exit_code_is_1_when_error_status(self, tmp_path):
        from src.main import cli

        suite_file = self._make_suite(tmp_path)
        runner = CliRunner()

        error_result = {
            "name": "File Check",
            "type": "structural",
            "status": "ERROR",
            "total_rows": 0,
            "error_count": 0,
            "warning_count": 0,
            "duration_seconds": 0.1,
            "report_path": None,
            "detail": "File not found",
        }

        with patch("src.commands.run_tests_command.run_tests_command", return_value=[error_result]):
            result = runner.invoke(cli, ["run-tests", "--suite", str(suite_file)])

        assert result.exit_code == 1

    def test_summary_table_printed_to_stdout(self, tmp_path):
        from src.main import cli

        suite_file = self._make_suite(tmp_path)
        runner = CliRunner()

        pass_result = {
            "name": "My Test",
            "type": "structural",
            "status": "PASS",
            "total_rows": 50000,
            "error_count": 0,
            "warning_count": 0,
            "duration_seconds": 8.2,
            "report_path": None,
            "detail": "",
        }

        with patch("src.commands.run_tests_command.run_tests_command", return_value=[pass_result]):
            result = runner.invoke(cli, ["run-tests", "--suite", str(suite_file)])

        assert "Minimal Suite" in result.output
        assert "PASS" in result.output
        assert "My Test" in result.output


# ---------------------------------------------------------------------------
# 8. ArchiveManager integration — archive_run called and archive_path recorded
# ---------------------------------------------------------------------------

class TestArchiveIntegration:
    """Verify run_suite_from_path archives reports after every run."""

    def test_archive_called_after_suite_run(self, tmp_path, monkeypatch):
        """archive_run() must be called once per suite run."""
        import yaml
        from src.commands.run_tests_command import run_suite_from_path

        # Write a minimal suite YAML with one api_check test (no file needed)
        suite_yaml = tmp_path / "suite.yaml"
        suite_yaml.write_text(
            yaml.dump({
                "name": "Archive Test Suite",
                "environment": "dev",
                "tests": [
                    {
                        "name": "Health check",
                        "type": "api_check",
                        "url": "http://localhost:9999/nope",
                    }
                ],
            }),
            encoding="utf-8",
        )

        archive_calls = []

        def fake_archive_run(self_inner, **kwargs):
            archive_calls.append(kwargs)
            return tmp_path

        import src.utils.archive as archive_mod
        monkeypatch.setattr(
            archive_mod.ArchiveManager, "archive_run",
            fake_archive_run,
        )

        run_suite_from_path(
            suite_path=str(suite_yaml),
            params={},
            env="dev",
            output_dir=str(tmp_path),
        )

        assert len(archive_calls) == 1
        assert archive_calls[0]["suite_name"] == "Archive Test Suite"

    def test_archive_path_added_to_run_history(self, tmp_path, monkeypatch):
        """run_history.json entry must include archive_path."""
        import json
        import yaml
        from pathlib import Path
        from src.commands.run_tests_command import run_suite_from_path

        suite_yaml = tmp_path / "suite.yaml"
        suite_yaml.write_text(
            yaml.dump({
                "name": "History Suite",
                "environment": "dev",
                "tests": [
                    {
                        "name": "API up",
                        "type": "api_check",
                        "url": "http://localhost:9999/nope",
                    }
                ],
            }),
            encoding="utf-8",
        )

        archive_dir = tmp_path / "archive"

        import src.utils.archive as archive_mod
        monkeypatch.setattr(
            archive_mod.ArchiveManager,
            "archive_run",
            lambda self_inner, **kwargs: archive_dir,
        )

        output_dir = tmp_path / "reports"
        output_dir.mkdir()

        run_suite_from_path(
            suite_path=str(suite_yaml),
            params={},
            env="dev",
            output_dir=str(output_dir),
        )

        import glob
        found = glob.glob(str(tmp_path / "**" / "run_history.json"), recursive=True)
        assert found, "run_history.json not created"
        history = json.loads(Path(found[0]).read_text(encoding="utf-8"))
        assert len(history) == 1
        assert "archive_path" in history[0]
        assert history[0]["archive_path"] == str(archive_dir)

    def test_run_history_written_even_if_archive_fails(self, tmp_path, monkeypatch):
        """_append_run_history must be called even when archive_run raises."""
        import yaml
        from src.commands.run_tests_command import run_suite_from_path

        suite_yaml = tmp_path / "suite.yaml"
        suite_yaml.write_text(
            yaml.dump({
                "name": "Resilient Suite",
                "environment": "dev",
                "tests": [
                    {"name": "Ping", "type": "api_check", "url": "http://localhost:9999/nope"}
                ],
            }),
            encoding="utf-8",
        )

        import src.utils.archive as archive_mod
        monkeypatch.setattr(
            archive_mod.ArchiveManager,
            "archive_run",
            lambda self_inner, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
        )

        output_dir = tmp_path / "reports"
        output_dir.mkdir()

        # Must not raise even though archive_run fails
        run_suite_from_path(suite_path=str(suite_yaml), params={}, env="dev", output_dir=str(output_dir))

        import glob
        import json
        found = glob.glob(str(tmp_path / "**" / "run_history.json"), recursive=True)
        assert found, "run_history.json must be written even after archive failure"
        history = json.loads(Path(found[0]).read_text(encoding="utf-8"))
        assert len(history) == 1
        assert history[0]["archive_path"] == ""

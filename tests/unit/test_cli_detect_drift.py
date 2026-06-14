"""Unit tests for the ``valdo detect-drift`` CLI command.

Tests cover:
- Clean file returns exit code 0 and no drift fields
- Drifted file with error severity returns exit code 1
- Drifted file with warnings only returns exit code 0
- ``--output`` flag writes JSON result to file
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

# Make sure src is importable.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.main import cli


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MAPPING_STUB = {
    "format": "fixed",
    "fields": [
        {"name": "id", "position": 1, "length": 5},
        {"name": "name", "position": 6, "length": 10},
    ],
}

CLEAN_RESULT = {"drifted": False, "fields": []}

DRIFTED_ERROR_RESULT = {
    "drifted": True,
    "fields": [
        {
            "name": "name",
            "expected_start": 6,
            "expected_length": 10,
            "actual_start": 12,
            "actual_length": 10,
            "severity": "error",
        }
    ],
}

DRIFTED_WARNING_RESULT = {
    "drifted": True,
    "fields": [
        {
            "name": "id",
            "expected_start": 1,
            "expected_length": 5,
            "actual_start": 3,
            "actual_length": 5,
            "severity": "warning",
        }
    ],
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _invoke(runner: CliRunner, args: list[str]) -> object:
    """Invoke ``valdo detect-drift`` with the given args."""
    return runner.invoke(cli, ["detect-drift"] + args)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDetectDriftCleanFile:
    """Clean file — no drift detected, exit code 0."""

    def test_exit_code_zero(self, tmp_path):
        mapping_file = tmp_path / "test.json"
        mapping_file.write_text(json.dumps(MAPPING_STUB))
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")

        runner = CliRunner()
        with patch("src.commands.detect_drift_command.detect_drift", return_value=CLEAN_RESULT):
            result = _invoke(runner, [
                "--file", str(data_file),
                "--mapping", "test",
                "--mappings-dir", str(tmp_path),
            ])

        assert result.exit_code == 0

    def test_no_drift_message_printed(self, tmp_path):
        mapping_file = tmp_path / "test.json"
        mapping_file.write_text(json.dumps(MAPPING_STUB))
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")

        runner = CliRunner()
        with patch("src.commands.detect_drift_command.detect_drift", return_value=CLEAN_RESULT):
            result = _invoke(runner, [
                "--file", str(data_file),
                "--mapping", "test",
                "--mappings-dir", str(tmp_path),
            ])

        assert "No drift" in result.output or "clean" in result.output.lower()


class TestDetectDriftErrorSeverity:
    """Drifted file with error-severity field — exit code 1."""

    def test_exit_code_one_on_error_severity(self, tmp_path):
        mapping_file = tmp_path / "test.json"
        mapping_file.write_text(json.dumps(MAPPING_STUB))
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")

        runner = CliRunner()
        with patch("src.commands.detect_drift_command.detect_drift", return_value=DRIFTED_ERROR_RESULT):
            result = _invoke(runner, [
                "--file", str(data_file),
                "--mapping", "test",
                "--mappings-dir", str(tmp_path),
            ])

        assert result.exit_code == 1

    def test_drifted_field_name_in_output(self, tmp_path):
        mapping_file = tmp_path / "test.json"
        mapping_file.write_text(json.dumps(MAPPING_STUB))
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")

        runner = CliRunner()
        with patch("src.commands.detect_drift_command.detect_drift", return_value=DRIFTED_ERROR_RESULT):
            result = _invoke(runner, [
                "--file", str(data_file),
                "--mapping", "test",
                "--mappings-dir", str(tmp_path),
            ])

        assert "name" in result.output


class TestDetectDriftWarningOnly:
    """Drifted file with warning-only severity — exit code 0."""

    def test_exit_code_zero_on_warnings_only(self, tmp_path):
        mapping_file = tmp_path / "test.json"
        mapping_file.write_text(json.dumps(MAPPING_STUB))
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")

        runner = CliRunner()
        with patch("src.commands.detect_drift_command.detect_drift", return_value=DRIFTED_WARNING_RESULT):
            result = _invoke(runner, [
                "--file", str(data_file),
                "--mapping", "test",
                "--mappings-dir", str(tmp_path),
            ])

        assert result.exit_code == 0

    def test_warning_field_shown_in_output(self, tmp_path):
        mapping_file = tmp_path / "test.json"
        mapping_file.write_text(json.dumps(MAPPING_STUB))
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")

        runner = CliRunner()
        with patch("src.commands.detect_drift_command.detect_drift", return_value=DRIFTED_WARNING_RESULT):
            result = _invoke(runner, [
                "--file", str(data_file),
                "--mapping", "test",
                "--mappings-dir", str(tmp_path),
            ])

        assert "id" in result.output or "warning" in result.output.lower()


class TestDetectDriftOutputFlag:
    """--output flag writes JSON report to file."""

    def test_output_file_written(self, tmp_path):
        mapping_file = tmp_path / "test.json"
        mapping_file.write_text(json.dumps(MAPPING_STUB))
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")
        output_file = tmp_path / "report.json"

        runner = CliRunner()
        with patch("src.commands.detect_drift_command.detect_drift", return_value=CLEAN_RESULT):
            result = _invoke(runner, [
                "--file", str(data_file),
                "--mapping", "test",
                "--mappings-dir", str(tmp_path),
                "--output", str(output_file),
            ])

        assert output_file.exists(), f"Output file was not written. CLI output: {result.output}"

    def test_output_file_contains_valid_json(self, tmp_path):
        mapping_file = tmp_path / "test.json"
        mapping_file.write_text(json.dumps(MAPPING_STUB))
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")
        output_file = tmp_path / "report.json"

        runner = CliRunner()
        with patch("src.commands.detect_drift_command.detect_drift", return_value=DRIFTED_ERROR_RESULT):
            _invoke(runner, [
                "--file", str(data_file),
                "--mapping", "test",
                "--mappings-dir", str(tmp_path),
                "--output", str(output_file),
            ])

        data = json.loads(output_file.read_text())
        assert "drifted" in data
        assert "fields" in data
        assert data["drifted"] is True

    def test_output_file_contains_fields_list(self, tmp_path):
        mapping_file = tmp_path / "test.json"
        mapping_file.write_text(json.dumps(MAPPING_STUB))
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")
        output_file = tmp_path / "report.json"

        runner = CliRunner()
        with patch("src.commands.detect_drift_command.detect_drift", return_value=DRIFTED_ERROR_RESULT):
            _invoke(runner, [
                "--file", str(data_file),
                "--mapping", "test",
                "--mappings-dir", str(tmp_path),
                "--output", str(output_file),
            ])

        data = json.loads(output_file.read_text())
        assert isinstance(data["fields"], list)
        assert len(data["fields"]) == 1
        assert data["fields"][0]["severity"] == "error"


class TestDetectDriftMappingNotFound:
    """Missing mapping file returns non-zero exit code."""

    def test_missing_mapping_exits_nonzero(self, tmp_path):
        data_file = tmp_path / "data.txt"
        data_file.write_text("12345ABCDEFGHIJ\n")

        runner = CliRunner()
        result = _invoke(runner, [
            "--file", str(data_file),
            "--mapping", "nonexistent_mapping",
            "--mappings-dir", str(tmp_path),
        ])

        assert result.exit_code != 0

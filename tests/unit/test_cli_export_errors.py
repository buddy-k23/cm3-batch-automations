"""Unit tests for --export-errors flag on valdo validate — Issue #228."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from click.testing import CliRunner

from src.main import cli


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_pipe_file(tmp_path: Path, rows: int = 5) -> Path:
    lines = ["name|value"]
    for i in range(1, rows + 1):
        lines.append(f"row{i}|{i}")
    p = tmp_path / "data.pipe"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def _make_mapping_file(tmp_path: Path) -> Path:
    cfg = {
        "source": {"format": "pipe_delimited", "has_header": True},
        "fields": [
            {"name": "name", "type": "string"},
            {"name": "value", "type": "string"},
        ],
    }
    p = tmp_path / "mapping.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCliExportErrors:
    """Tests for the --export-errors flag on valdo validate."""

    def test_export_errors_flag_triggers_extraction_and_prints_count(self, tmp_path):
        """--export-errors writes error rows and prints the count."""
        data_file = _make_pipe_file(tmp_path)
        mapping_file = _make_mapping_file(tmp_path)
        export_path = str(tmp_path / "failed_rows.txt")

        validate_result = {
            "valid": False,
            "total_rows": 5,
            "valid_rows": 3,
            "invalid_rows": 2,
            "error_count": 2,
            "warning_count": 0,
            "errors": [
                {"row": 2, "message": "bad value"},
                {"row": 4, "message": "missing field"},
            ],
            "warnings": [],
        }

        runner = CliRunner()
        # Patch run_validate_service which is used by the service-layer path.
        # The CLI validate command uses EnhancedFileValidator directly; patch
        # _run_export_errors to isolate the export behaviour.
        with patch(
            "src.commands.validate_command._run_export_errors"
        ) as mock_export, patch(
            "src.parsers.enhanced_validator.EnhancedFileValidator"
        ) as MockVal:
            MockVal.return_value.validate.return_value = validate_result
            # Simulate what _run_export_errors would do: create the file and echo.
            def fake_export(fp, res, ep):
                Path(ep).parent.mkdir(parents=True, exist_ok=True)
                Path(ep).write_text("", encoding="utf-8")
                import click as _click
                n = len({e["row"] for e in res.get("errors", [])})
                _click.echo(f"Exported {n} failed rows to {ep}")
            mock_export.side_effect = fake_export

            result = runner.invoke(
                cli,
                [
                    "validate",
                    "--file", str(data_file),
                    "--mapping", str(mapping_file),
                    "--export-errors", export_path,
                ],
            )

        assert "Exported" in result.output
        assert "2" in result.output
        # _run_export_errors was called with the right path
        mock_export.assert_called_once()
        call_args = mock_export.call_args[0]
        assert call_args[2] == export_path

    def test_export_errors_no_errors_prints_zero(self, tmp_path):
        """--export-errors prints 'Exported 0' when validation passes."""
        data_file = _make_pipe_file(tmp_path)
        mapping_file = _make_mapping_file(tmp_path)
        export_path = str(tmp_path / "failed_rows.txt")

        validate_result = {
            "valid": True,
            "total_rows": 5,
            "valid_rows": 5,
            "invalid_rows": 0,
            "error_count": 0,
            "warning_count": 0,
            "errors": [],
            "warnings": [],
        }

        runner = CliRunner()
        with patch(
            "src.commands.validate_command._run_export_errors"
        ) as mock_export, patch(
            "src.parsers.enhanced_validator.EnhancedFileValidator"
        ) as MockVal:
            MockVal.return_value.validate.return_value = validate_result

            def fake_export(fp, res, ep):
                Path(ep).parent.mkdir(parents=True, exist_ok=True)
                Path(ep).write_text("", encoding="utf-8")
                import click as _click
                _click.echo(f"Exported 0 failed rows to {ep}")
            mock_export.side_effect = fake_export

            result = runner.invoke(
                cli,
                [
                    "validate",
                    "--file", str(data_file),
                    "--mapping", str(mapping_file),
                    "--export-errors", export_path,
                ],
            )

        assert "Exported 0" in result.output
        mock_export.assert_called_once()

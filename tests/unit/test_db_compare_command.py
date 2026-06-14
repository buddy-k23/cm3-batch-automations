"""Unit tests for db_compare CLI command — written BEFORE implementation (TDD)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner


class TestDbCompareCommand:
    """Tests for run_db_compare_command."""

    def test_run_db_compare_command_passes_args_to_service(self, tmp_path: Path) -> None:
        """Command must forward all arguments to compare_db_to_file."""
        from src.commands.db_compare import run_db_compare_command

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}]}
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(json.dumps(mapping_cfg))

        actual_file = tmp_path / "actual.txt"
        actual_file.write_text("ID\n1\n")

        logger = MagicMock()

        with patch(
            "src.commands.db_compare.compare_db_to_file"
        ) as mock_svc:
            mock_svc.return_value = {
                "workflow": {
                    "status": "passed",
                    "db_rows_extracted": 1,
                    "query_or_table": "SELECT * FROM FOO",
                },
                "compare": {
                    "structure_compatible": True,
                    "total_rows_file1": 1,
                    "total_rows_file2": 1,
                    "matching_rows": 1,
                    "only_in_file1": 0,
                    "only_in_file2": 0,
                    "differences": 0,
                },
            }

            run_db_compare_command(
                query_or_table="SELECT * FROM FOO",
                mapping=str(mapping_file),
                actual_file=str(actual_file),
                output_format="json",
                key_columns="ID",
                output=None,
                logger=logger,
            )

            mock_svc.assert_called_once()
            call_kwargs = mock_svc.call_args
            assert call_kwargs.kwargs["query_or_table"] == "SELECT * FROM FOO"
            assert call_kwargs.kwargs["actual_file"] == str(actual_file)

    def test_run_db_compare_command_missing_mapping_exits(self, tmp_path: Path) -> None:
        """Non-existent mapping file must cause a logged error (no crash)."""
        from src.commands.db_compare import run_db_compare_command

        actual_file = tmp_path / "actual.txt"
        actual_file.write_text("ID\n1\n")

        logger = MagicMock()

        import sys
        with pytest.raises(SystemExit):
            run_db_compare_command(
                query_or_table="SELECT * FROM FOO",
                mapping=str(tmp_path / "no_such.json"),
                actual_file=str(actual_file),
                output_format="json",
                key_columns="ID",
                output=None,
                logger=logger,
            )

    def test_run_db_compare_command_writes_output_file(self, tmp_path: Path) -> None:
        """When --output is provided, the command must write a JSON report."""
        from src.commands.db_compare import run_db_compare_command

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}]}
        mapping_file = tmp_path / "mapping.json"
        mapping_file.write_text(json.dumps(mapping_cfg))

        actual_file = tmp_path / "actual.txt"
        actual_file.write_text("ID\n1\n")

        output_file = tmp_path / "report.json"
        logger = MagicMock()

        with patch("src.commands.db_compare.compare_db_to_file") as mock_svc:
            mock_svc.return_value = {
                "workflow": {
                    "status": "passed",
                    "db_rows_extracted": 1,
                    "query_or_table": "SELECT * FROM FOO",
                },
                "compare": {
                    "structure_compatible": True,
                    "total_rows_file1": 1,
                    "total_rows_file2": 1,
                    "matching_rows": 1,
                    "only_in_file1": 0,
                    "only_in_file2": 0,
                    "differences": 0,
                },
            }

            run_db_compare_command(
                query_or_table="SELECT * FROM FOO",
                mapping=str(mapping_file),
                actual_file=str(actual_file),
                output_format="json",
                key_columns="ID",
                output=str(output_file),
                logger=logger,
            )

        assert output_file.exists()
        data = json.loads(output_file.read_text())
        assert "workflow" in data


class TestDbCompareCliEntry:
    """Tests for the Click CLI entry point wiring."""

    def test_cli_db_compare_invokable(self) -> None:
        """The db-compare CLI command must be registered and invokable."""
        from src.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["db-compare", "--help"])
        assert result.exit_code == 0
        assert "query-or-table" in result.output or "query" in result.output.lower()

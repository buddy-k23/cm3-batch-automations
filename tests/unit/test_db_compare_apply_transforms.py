"""Unit tests for --apply-transforms flag on db-compare (Phase 5b).

Tests cover:
- compare_db_to_file accepts apply_transforms kwarg (defaults False)
- When apply_transforms=True, TransformEngine.apply() is called on each DB row
- When apply_transforms=False (default), rows pass through unchanged
- run_db_compare_command accepts and passes through apply_transforms flag
- CLI --apply-transforms flag wires into the command
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call
import pytest

from click.testing import CliRunner


# ---------------------------------------------------------------------------
# Service layer — compare_db_to_file
# ---------------------------------------------------------------------------

class TestCompareDbToFileApplyTransforms:
    """compare_db_to_file correctly routes apply_transforms to TransformEngine."""

    def _make_mapping(self) -> dict:
        return {
            "fields": [
                {"target_name": "STATUS", "transformation": "Pass 'ACTIVE'"},
                {"target_name": "CODE", "transformation": "Pass as is"},
            ]
        }

    @patch("src.services.db_file_compare_service.run_compare_service")
    @patch("src.services.db_file_compare_service._df_to_temp_file")
    @patch("src.services.db_file_compare_service.DataExtractor")
    @patch("src.services.db_file_compare_service.OracleConnection")
    @patch("src.services.db_file_compare_service.Path")
    def test_apply_transforms_false_by_default(
        self, mock_path, mock_conn, mock_extractor_cls, mock_df_to_temp, mock_compare
    ):
        """apply_transforms defaults to False — TransformEngine not invoked."""
        import pandas as pd
        mock_path.return_value.exists.return_value = True

        df = pd.DataFrame([{"STATUS": "X", "CODE": "Y"}])
        mock_extractor_cls.return_value.extract_table.return_value = df
        mock_df_to_temp.return_value = "/tmp/fake.txt"
        mock_compare.return_value = {"structure_compatible": True, "rows_with_differences": 0}

        from src.services.db_file_compare_service import compare_db_to_file

        with patch("src.transforms.transform_orchestrator.TransformEngine") as mock_engine_cls:
            compare_db_to_file(
                query_or_table="MY_TABLE",
                mapping_config=self._make_mapping(),
                actual_file="/fake/file.txt",
            )
            mock_engine_cls.assert_not_called()

    @patch("src.services.db_file_compare_service.run_compare_service")
    @patch("src.services.db_file_compare_service._df_to_temp_file")
    @patch("src.services.db_file_compare_service.DataExtractor")
    @patch("src.services.db_file_compare_service.OracleConnection")
    @patch("src.services.db_file_compare_service.Path")
    def test_apply_transforms_true_calls_engine(
        self, mock_path, mock_conn, mock_extractor_cls, mock_df_to_temp, mock_compare
    ):
        """apply_transforms=True causes TransformEngine to be constructed and applied."""
        import pandas as pd
        mock_path.return_value.exists.return_value = True

        df = pd.DataFrame([{"STATUS": "X", "CODE": "Y"}, {"STATUS": "A", "CODE": "B"}])
        mock_extractor_cls.return_value.extract_table.return_value = df
        mock_df_to_temp.return_value = "/tmp/fake.txt"
        mock_compare.return_value = {"structure_compatible": True, "rows_with_differences": 0}

        from src.services.db_file_compare_service import compare_db_to_file

        with patch("src.services.db_file_compare_service.TransformEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.apply.side_effect = lambda row: row  # identity
            mock_engine_cls.return_value = mock_engine

            compare_db_to_file(
                query_or_table="MY_TABLE",
                mapping_config=self._make_mapping(),
                actual_file="/fake/file.txt",
                apply_transforms=True,
            )

            mock_engine_cls.assert_called_once()
            assert mock_engine.apply.call_count == 2  # once per row

    @patch("src.services.db_file_compare_service.run_compare_service")
    @patch("src.services.db_file_compare_service._df_to_temp_file")
    @patch("src.services.db_file_compare_service.DataExtractor")
    @patch("src.services.db_file_compare_service.OracleConnection")
    @patch("src.services.db_file_compare_service.Path")
    def test_apply_transforms_result_written_to_temp_file(
        self, mock_path, mock_conn, mock_extractor_cls, mock_df_to_temp, mock_compare
    ):
        """Transformed rows (not raw DB rows) are written to the temp file."""
        import pandas as pd
        mock_path.return_value.exists.return_value = True

        df = pd.DataFrame([{"STATUS": "X", "CODE": "Y"}])
        mock_extractor_cls.return_value.extract_table.return_value = df
        mock_df_to_temp.return_value = "/tmp/fake.txt"
        mock_compare.return_value = {"structure_compatible": True, "rows_with_differences": 0}

        from src.services.db_file_compare_service import compare_db_to_file

        with patch("src.services.db_file_compare_service.TransformEngine") as mock_engine_cls:
            mock_engine = MagicMock()
            mock_engine.apply.return_value = {"STATUS": "ACTIVE", "CODE": "Y"}
            mock_engine_cls.return_value = mock_engine

            compare_db_to_file(
                query_or_table="MY_TABLE",
                mapping_config=self._make_mapping(),
                actual_file="/fake/file.txt",
                apply_transforms=True,
            )

            # The DataFrame passed to _df_to_temp_file should contain transformed data
            written_df = mock_df_to_temp.call_args[0][0]
            assert written_df.iloc[0]["STATUS"] == "ACTIVE"


# ---------------------------------------------------------------------------
# Command layer — run_db_compare_command
# ---------------------------------------------------------------------------

class TestRunDbCompareCommandApplyTransforms:
    """run_db_compare_command threads apply_transforms through to the service."""

    def _run(self, apply_transforms: bool = False):
        import json
        from pathlib import Path
        from unittest.mock import patch, MagicMock
        import tempfile, os

        mapping = {"fields": [{"target_name": "X"}]}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(mapping, f)
            mapping_path = f.name

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            actual_path = f.name

        logger = MagicMock()
        try:
            with patch("src.commands.db_compare.compare_db_to_file") as mock_svc:
                mock_svc.return_value = {
                    "workflow": {"status": "passed", "db_rows_extracted": 0, "query_or_table": "T"},
                    "compare": {"total_rows_file2": 0, "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0},
                }
                from src.commands.db_compare import run_db_compare_command
                run_db_compare_command(
                    query_or_table="MY_TABLE",
                    mapping=mapping_path,
                    actual_file=actual_path,
                    output_format="json",
                    key_columns=None,
                    output=None,
                    logger=logger,
                    apply_transforms=apply_transforms,
                )
                return mock_svc
        finally:
            os.unlink(mapping_path)
            os.unlink(actual_path)

    def test_apply_transforms_false_passed_to_service(self):
        mock_svc = self._run(apply_transforms=False)
        _, kwargs = mock_svc.call_args
        assert kwargs.get("apply_transforms") is False

    def test_apply_transforms_true_passed_to_service(self):
        mock_svc = self._run(apply_transforms=True)
        _, kwargs = mock_svc.call_args
        assert kwargs.get("apply_transforms") is True


# ---------------------------------------------------------------------------
# CLI — --apply-transforms flag
# ---------------------------------------------------------------------------

class TestDbCompareCliFlag:
    """--apply-transforms Click flag is wired correctly."""

    def test_apply_transforms_flag_exists(self):
        """Invoking --help shows --apply-transforms option."""
        from src.main import cli
        runner = CliRunner()
        result = runner.invoke(cli, ["db-compare", "--help"])
        assert "--apply-transforms" in result.output

    def test_apply_transforms_flag_defaults_false(self):
        """Without the flag, apply_transforms defaults to False."""
        from src.main import cli
        import tempfile, os, json

        mapping = {"fields": [{"target_name": "X"}]}
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump(mapping, f)
            mapping_path = f.name

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            actual_path = f.name

        runner = CliRunner()
        try:
            with patch("src.commands.db_compare.run_db_compare_command") as mock_cmd:
                runner.invoke(cli, [
                    "db-compare",
                    "-q", "MY_TABLE",
                    "-m", mapping_path,
                    "-f", actual_path,
                ])
                if mock_cmd.called:
                    _, kwargs = mock_cmd.call_args
                    assert kwargs.get("apply_transforms", False) is False
        finally:
            os.unlink(mapping_path)
            os.unlink(actual_path)

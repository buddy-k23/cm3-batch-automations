"""Command-module + CLI-parity tests for ``valdo extract`` (S-followup, #422).

These tests pin the behaviour of the relocated ``extract`` logic now living in
:mod:`src.commands.extract_command`.  They prove the move is behaviour-preserving:
the three extraction modes (table, table+limit, query, sql-file), the
mutually-exclusive option validation, and the error exit code are all exercised
both through the thin CLI stub (``CliRunner``) and the extracted command
function directly.

The S15 adapter factory and S13.5-4 SQL hardening are not re-tested here — they
live in ``extractor.py`` / the adapter factory and are covered elsewhere; these
tests only assert the orchestration relocation is faithful.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pandas as pd
from click.testing import CliRunner

from src.commands.extract_command import run_extract_command
from src.main import cli


@contextmanager
def _adapter_cm(adapter):
    """Context-manager shim so ``with get_database_adapter() as adapter`` works."""
    yield adapter


def _patch_factory_and_extractor(stats=None, df=None):
    """Patch the adapter factory + DataExtractor used by the command.

    Returns a tuple of (factory_patch, extractor_patch, mock_extractor).
    """
    mock_extractor = MagicMock()
    if stats is not None:
        mock_extractor.extract_to_file.return_value = stats
    if df is not None:
        mock_extractor.extract_table.return_value = df

    factory = patch(
        "src.database.adapters.factory.get_database_adapter",
        return_value=_adapter_cm(MagicMock()),
    )
    extractor = patch(
        "src.database.extractor.DataExtractor",
        return_value=mock_extractor,
    )
    return factory, extractor, mock_extractor


# ---------------------------------------------------------------------------
# Option validation (no DB access)
# ---------------------------------------------------------------------------
class TestExtractOptionValidation:
    def test_no_source_option_exits_1(self):
        logger = logging.getLogger("test")
        rc = None
        try:
            run_extract_command(
                table=None, query=None, sql_file=None,
                output="out.txt", limit=None, delimiter="|", logger=logger,
            )
        except SystemExit as exc:
            rc = exc.code
        assert rc == 1

    def test_multiple_source_options_exits_1(self):
        logger = logging.getLogger("test")
        rc = None
        try:
            run_extract_command(
                table="T", query="SELECT 1", sql_file=None,
                output="out.txt", limit=None, delimiter="|", logger=logger,
            )
        except SystemExit as exc:
            rc = exc.code
        assert rc == 1


# ---------------------------------------------------------------------------
# The three extraction modes (mocked adapter + extractor)
# ---------------------------------------------------------------------------
class TestExtractModes:
    def test_table_mode_uses_extract_to_file(self):
        factory, extractor, mock = _patch_factory_and_extractor(stats={"total_rows": 7})
        logger = logging.getLogger("test")
        with factory, extractor:
            run_extract_command(
                table="MY_TABLE", query=None, sql_file=None,
                output="out.txt", limit=None, delimiter="|", logger=logger,
            )
        mock.extract_to_file.assert_called_once_with(
            table_name="MY_TABLE", output_file="out.txt", delimiter="|"
        )

    def test_table_mode_with_limit_uses_extract_table(self, tmp_path):
        df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
        factory, extractor, mock = _patch_factory_and_extractor(df=df)
        out = tmp_path / "out.txt"
        logger = logging.getLogger("test")
        with factory, extractor:
            run_extract_command(
                table="MY_TABLE", query=None, sql_file=None,
                output=str(out), limit=5, delimiter="|", logger=logger,
            )
        mock.extract_table.assert_called_once_with("MY_TABLE", limit=5)
        assert out.exists()

    def test_query_mode_uses_extract_to_file(self):
        factory, extractor, mock = _patch_factory_and_extractor(stats={"total_rows": 3})
        logger = logging.getLogger("test")
        with factory, extractor:
            run_extract_command(
                table=None, query="SELECT 1 FROM dual", sql_file=None,
                output="out.txt", limit=None, delimiter=",", logger=logger,
            )
        mock.extract_to_file.assert_called_once_with(
            output_file="out.txt", query="SELECT 1 FROM dual", delimiter=","
        )

    def test_sql_file_mode_reads_file_then_extracts(self, tmp_path):
        sql = tmp_path / "q.sql"
        sql.write_text("SELECT * FROM t\n", encoding="utf-8")
        factory, extractor, mock = _patch_factory_and_extractor(stats={"total_rows": 1})
        logger = logging.getLogger("test")
        with factory, extractor:
            run_extract_command(
                table=None, query=None, sql_file=str(sql),
                output="out.txt", limit=None, delimiter="|", logger=logger,
            )
        mock.extract_to_file.assert_called_once_with(
            output_file="out.txt", query="SELECT * FROM t", delimiter="|"
        )

    def test_extractor_error_exits_1(self):
        mock_extractor = MagicMock()
        mock_extractor.extract_to_file.side_effect = RuntimeError("boom")
        factory = patch(
            "src.database.adapters.factory.get_database_adapter",
            return_value=_adapter_cm(MagicMock()),
        )
        extractor = patch(
            "src.database.extractor.DataExtractor", return_value=mock_extractor
        )
        logger = logging.getLogger("test")
        rc = None
        with factory, extractor:
            try:
                run_extract_command(
                    table="T", query=None, sql_file=None,
                    output="out.txt", limit=None, delimiter="|", logger=logger,
                )
            except SystemExit as exc:
                rc = exc.code
        assert rc == 1


# ---------------------------------------------------------------------------
# CLI parity — the thin stub delegates to the command module
# ---------------------------------------------------------------------------
class TestExtractCliParity:
    def test_cli_no_source_option_exits_nonzero(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["extract", "--output", "out.txt"])
        assert result.exit_code != 0
        assert "Must provide one of" in result.output

    def test_cli_table_mode_delegates(self):
        factory, extractor, mock = _patch_factory_and_extractor(stats={"total_rows": 2})
        runner = CliRunner()
        with factory, extractor:
            result = runner.invoke(
                cli, ["extract", "--table", "T", "--output", "out.txt"]
            )
        assert result.exit_code == 0
        assert "Extraction complete" in result.output
        mock.extract_to_file.assert_called_once()

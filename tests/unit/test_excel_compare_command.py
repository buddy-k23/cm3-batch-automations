"""Unit tests for run_excel_compare_command (S24-2) — written BEFORE implementation (TDD).

Mirror the db-compare command's thin-orchestration contract: delegate to the
service, print a summary, write ``.json`` here / ``.html`` via the service, and
exit non-zero on mismatch (matching ``compare`` / ``db-compare`` convention).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.commands.excel_compare import run_excel_compare_command


def _passing_result() -> dict:
    return {
        "workflow": {
            "status": "passed",
            "db_rows_extracted": 2,
            "query_or_table": "ACCOUNTS",
            "direction": "excel-source",
        },
        "compare": {
            "structure_compatible": True,
            "total_rows_file1": 2,
            "total_rows_file2": 2,
            "matching_rows": 2,
            "only_in_file1": 0,
            "only_in_file2": 0,
            "differences": 0,
            "rows_with_differences": 0,
        },
    }


def _failing_result() -> dict:
    r = _passing_result()
    r["workflow"]["status"] = "failed"
    r["compare"]["matching_rows"] = 1
    r["compare"]["rows_with_differences"] = 1
    r["compare"]["differences"] = 1
    return r


class TestRunExcelCompareCommand:
    def test_delegates_to_service(self) -> None:
        logger = MagicMock()
        with patch(
            "src.commands.excel_compare.compare_excel_to_db",
            return_value=_passing_result(),
        ) as mock_svc:
            run_excel_compare_command(
                excel_file="data.xlsx",
                query_or_table="ACCOUNTS",
                sheet=None,
                header_row=0,
                key_columns="ID",
                direction="excel-source",
                output_format="json",
                output=None,
                logger=logger,
                connection_override={"db_adapter": "sqlite"},
            )
            mock_svc.assert_called_once()
            kwargs = mock_svc.call_args.kwargs
            assert kwargs["excel_file"] == "data.xlsx"
            assert kwargs["query_or_table"] == "ACCOUNTS"
            assert kwargs["direction"] == "excel-source"

    def test_exit_zero_on_pass(self) -> None:
        logger = MagicMock()
        with patch(
            "src.commands.excel_compare.compare_excel_to_db",
            return_value=_passing_result(),
        ):
            # No SystemExit on a passing run.
            run_excel_compare_command(
                excel_file="data.xlsx",
                query_or_table="ACCOUNTS",
                sheet=None,
                header_row=0,
                key_columns="ID",
                direction="excel-source",
                output_format="json",
                output=None,
                logger=logger,
            )

    def test_exit_nonzero_on_mismatch(self) -> None:
        logger = MagicMock()
        with patch(
            "src.commands.excel_compare.compare_excel_to_db",
            return_value=_failing_result(),
        ):
            with pytest.raises(SystemExit) as exc:
                run_excel_compare_command(
                    excel_file="data.xlsx",
                    query_or_table="ACCOUNTS",
                    sheet=None,
                    header_row=0,
                    key_columns="ID",
                    direction="excel-source",
                    output_format="json",
                    output=None,
                    logger=logger,
                )
            assert exc.value.code == 1

    def test_writes_json_output(self, tmp_path: Path) -> None:
        logger = MagicMock()
        out = tmp_path / "result.json"
        with patch(
            "src.commands.excel_compare.compare_excel_to_db",
            return_value=_passing_result(),
        ):
            run_excel_compare_command(
                excel_file="data.xlsx",
                query_or_table="ACCOUNTS",
                sheet=None,
                header_row=0,
                key_columns="ID",
                direction="excel-source",
                output_format="json",
                output=str(out),
                logger=logger,
            )
        assert out.exists()
        assert '"workflow"' in out.read_text(encoding="utf-8")

    def test_html_output_path_forwarded_to_service(self, tmp_path: Path) -> None:
        logger = MagicMock()
        out = tmp_path / "result.html"
        result = _passing_result()
        result["report_path"] = str(out)
        with patch(
            "src.commands.excel_compare.compare_excel_to_db",
            return_value=result,
        ) as mock_svc:
            run_excel_compare_command(
                excel_file="data.xlsx",
                query_or_table="ACCOUNTS",
                sheet=None,
                header_row=0,
                key_columns="ID",
                direction="excel-source",
                output_format="html",
                output=str(out),
                logger=logger,
            )
            # The HTML path must be forwarded as output_path so the service renders it.
            assert mock_svc.call_args.kwargs["output_path"] == str(out)

    def test_service_error_exits_nonzero(self) -> None:
        logger = MagicMock()
        with patch(
            "src.commands.excel_compare.compare_excel_to_db",
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(SystemExit):
                run_excel_compare_command(
                    excel_file="data.xlsx",
                    query_or_table="ACCOUNTS",
                    sheet=None,
                    header_row=0,
                    key_columns="ID",
                    direction="excel-source",
                    output_format="json",
                    output=None,
                    logger=logger,
                )

    def test_missing_excel_exits_nonzero(self) -> None:
        logger = MagicMock()
        with patch(
            "src.commands.excel_compare.compare_excel_to_db",
            side_effect=FileNotFoundError("Excel file not found"),
        ):
            with pytest.raises(SystemExit):
                run_excel_compare_command(
                    excel_file="missing.xlsx",
                    query_or_table="ACCOUNTS",
                    sheet=None,
                    header_row=0,
                    key_columns="ID",
                    direction="excel-source",
                    output_format="json",
                    output=None,
                    logger=logger,
                )

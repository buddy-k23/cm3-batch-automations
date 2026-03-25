"""Unit tests for db_file_compare_service — written BEFORE implementation (TDD)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mapping(fields: list[dict]) -> dict:
    return {"name": "test_mapping", "fields": fields}


def _write_temp_csv(tmp_path: Path, rows: list[dict]) -> Path:
    """Write a pipe-delimited temp file and return its path."""
    if not rows:
        p = tmp_path / "empty.txt"
        p.write_text("")
        return p
    headers = list(rows[0].keys())
    lines = ["|".join(headers)]
    for row in rows:
        lines.append("|".join(str(row[h]) for h in headers))
    p = tmp_path / "actual.txt"
    p.write_text("\n".join(lines))
    return p


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"ID": "1", "NAME": "Alice", "AMOUNT": "100"},
            {"ID": "2", "NAME": "Bob", "AMOUNT": "200"},
        ]
    )


# ---------------------------------------------------------------------------
# Tests for compare_db_to_file
# ---------------------------------------------------------------------------


class TestCompareDbToFile:
    """Tests for the compare_db_to_file service function."""

    def test_returns_expected_keys(self, tmp_path: Path) -> None:
        """Result dict must contain the standard comparison + workflow keys."""
        from src.services.db_file_compare_service import compare_db_to_file

        actual_file = _write_temp_csv(
            tmp_path,
            [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"}],
        )
        mapping_cfg = _make_mapping(
            [{"name": "ID"}, {"name": "NAME"}, {"name": "AMOUNT"}]
        )

        mock_df = _sample_df()

        with (
            patch(
                "src.services.db_file_compare_service.OracleConnection"
            ) as mock_conn_cls,
            patch(
                "src.services.db_file_compare_service.DataExtractor"
            ) as mock_extractor_cls,
            patch(
                "src.services.db_file_compare_service.run_compare_service"
            ) as mock_compare,
        ):
            mock_extractor = MagicMock()
            mock_extractor.extract_by_query.return_value = mock_df
            mock_extractor_cls.return_value = mock_extractor
            mock_conn_cls.from_env.return_value = MagicMock()

            mock_compare.return_value = {
                "structure_compatible": True,
                "total_rows_file1": 2,
                "total_rows_file2": 1,
                "matching_rows": 1,
                "only_in_file1": 1,
                "only_in_file2": 0,
                "differences": 0,
            }

            result = compare_db_to_file(
                query_or_table="SELECT * FROM FOO",
                mapping_config=mapping_cfg,
                actual_file=str(actual_file),
                output_format="json",
                key_columns=["ID"],
            )

        assert "workflow" in result
        assert "compare" in result
        assert result["workflow"]["status"] in ("passed", "failed")

    def test_query_extraction_called_with_query(self, tmp_path: Path) -> None:
        """When query_or_table looks like SQL, extract_by_query must be used."""
        from src.services.db_file_compare_service import compare_db_to_file

        actual_file = _write_temp_csv(tmp_path, [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"}])
        mapping_cfg = _make_mapping([{"name": "ID"}, {"name": "NAME"}, {"name": "AMOUNT"}])

        with (
            patch("src.services.db_file_compare_service.OracleConnection") as mock_conn_cls,
            patch("src.services.db_file_compare_service.DataExtractor") as mock_extractor_cls,
            patch("src.services.db_file_compare_service.run_compare_service") as mock_compare,
            patch("src.services.db_file_compare_service.tempfile") as _mock_tf,
        ):
            mock_extractor = MagicMock()
            mock_extractor.extract_by_query.return_value = _sample_df()
            mock_extractor_cls.return_value = mock_extractor
            mock_conn_cls.from_env.return_value = MagicMock()
            mock_compare.return_value = {
                "structure_compatible": True,
                "total_rows_file1": 2,
                "total_rows_file2": 1,
                "matching_rows": 1,
                "only_in_file1": 1,
                "only_in_file2": 0,
                "differences": 0,
            }

            compare_db_to_file(
                query_or_table="SELECT ID FROM FOO",
                mapping_config=mapping_cfg,
                actual_file=str(actual_file),
                output_format="json",
                key_columns=["ID"],
            )

            mock_extractor.extract_by_query.assert_called_once()
            mock_extractor.extract_table.assert_not_called()

    def test_table_extraction_called_for_plain_name(self, tmp_path: Path) -> None:
        """When query_or_table is a bare table name, extract_table must be used."""
        from src.services.db_file_compare_service import compare_db_to_file

        actual_file = _write_temp_csv(tmp_path, [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"}])
        mapping_cfg = _make_mapping([{"name": "ID"}, {"name": "NAME"}, {"name": "AMOUNT"}])

        with (
            patch("src.services.db_file_compare_service.OracleConnection") as mock_conn_cls,
            patch("src.services.db_file_compare_service.DataExtractor") as mock_extractor_cls,
            patch("src.services.db_file_compare_service.run_compare_service") as mock_compare,
        ):
            mock_extractor = MagicMock()
            mock_extractor.extract_table.return_value = _sample_df()
            mock_extractor_cls.return_value = mock_extractor
            mock_conn_cls.from_env.return_value = MagicMock()
            mock_compare.return_value = {
                "structure_compatible": True,
                "total_rows_file1": 2,
                "total_rows_file2": 1,
                "matching_rows": 1,
                "only_in_file1": 1,
                "only_in_file2": 0,
                "differences": 0,
            }

            compare_db_to_file(
                query_or_table="SHAW_SRC_P327",
                mapping_config=mapping_cfg,
                actual_file=str(actual_file),
                output_format="json",
                key_columns=["ID"],
            )

            mock_extractor.extract_table.assert_called_once_with("SHAW_SRC_P327")
            mock_extractor.extract_by_query.assert_not_called()

    def test_db_extraction_error_surfaces_as_runtime_error(self, tmp_path: Path) -> None:
        """DB failures must propagate so the caller can handle them."""
        from src.services.db_file_compare_service import compare_db_to_file

        actual_file = _write_temp_csv(tmp_path, [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"}])
        mapping_cfg = _make_mapping([{"name": "ID"}])

        with (
            patch("src.services.db_file_compare_service.OracleConnection") as mock_conn_cls,
            patch("src.services.db_file_compare_service.DataExtractor") as mock_extractor_cls,
        ):
            mock_extractor = MagicMock()
            mock_extractor.extract_by_query.side_effect = RuntimeError("ORA-01017: invalid credentials")
            mock_extractor_cls.return_value = mock_extractor
            mock_conn_cls.from_env.return_value = MagicMock()

            with pytest.raises(RuntimeError, match="ORA-01017"):
                compare_db_to_file(
                    query_or_table="SELECT * FROM FOO",
                    mapping_config=mapping_cfg,
                    actual_file=str(actual_file),
                    output_format="json",
                    key_columns=["ID"],
                )

    def test_missing_actual_file_raises_file_not_found(self, tmp_path: Path) -> None:
        """Passing a non-existent actual_file must raise FileNotFoundError."""
        from src.services.db_file_compare_service import compare_db_to_file

        mapping_cfg = _make_mapping([{"name": "ID"}])

        with pytest.raises(FileNotFoundError):
            compare_db_to_file(
                query_or_table="SELECT * FROM FOO",
                mapping_config=mapping_cfg,
                actual_file=str(tmp_path / "does_not_exist.txt"),
                output_format="json",
                key_columns=["ID"],
            )

    def test_workflow_status_passed_on_matching_data(self, tmp_path: Path) -> None:
        """workflow.status must be 'passed' when compare returns no differences."""
        from src.services.db_file_compare_service import compare_db_to_file

        actual_file = _write_temp_csv(
            tmp_path,
            [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"},
             {"ID": "2", "NAME": "Bob", "AMOUNT": "200"}],
        )
        mapping_cfg = _make_mapping([{"name": "ID"}, {"name": "NAME"}, {"name": "AMOUNT"}])

        with (
            patch("src.services.db_file_compare_service.OracleConnection") as mock_conn_cls,
            patch("src.services.db_file_compare_service.DataExtractor") as mock_extractor_cls,
            patch("src.services.db_file_compare_service.run_compare_service") as mock_compare,
        ):
            mock_extractor = MagicMock()
            mock_extractor.extract_by_query.return_value = _sample_df()
            mock_extractor_cls.return_value = mock_extractor
            mock_conn_cls.from_env.return_value = MagicMock()
            mock_compare.return_value = {
                "structure_compatible": True,
                "total_rows_file1": 2,
                "total_rows_file2": 2,
                "matching_rows": 2,
                "only_in_file1": 0,
                "only_in_file2": 0,
                "differences": 0,
                "rows_with_differences": 0,
            }

            result = compare_db_to_file(
                query_or_table="SELECT * FROM FOO",
                mapping_config=mapping_cfg,
                actual_file=str(actual_file),
                output_format="json",
                key_columns=["ID"],
            )

        assert result["workflow"]["status"] == "passed"

    def test_workflow_status_failed_on_differences(self, tmp_path: Path) -> None:
        """workflow.status must be 'failed' when compare returns differences."""
        from src.services.db_file_compare_service import compare_db_to_file

        actual_file = _write_temp_csv(
            tmp_path,
            [{"ID": "1", "NAME": "Alice", "AMOUNT": "999"}],
        )
        mapping_cfg = _make_mapping([{"name": "ID"}, {"name": "NAME"}, {"name": "AMOUNT"}])

        with (
            patch("src.services.db_file_compare_service.OracleConnection") as mock_conn_cls,
            patch("src.services.db_file_compare_service.DataExtractor") as mock_extractor_cls,
            patch("src.services.db_file_compare_service.run_compare_service") as mock_compare,
        ):
            mock_extractor = MagicMock()
            mock_extractor.extract_by_query.return_value = _sample_df()
            mock_extractor_cls.return_value = mock_extractor
            mock_conn_cls.from_env.return_value = MagicMock()
            mock_compare.return_value = {
                "structure_compatible": True,
                "total_rows_file1": 2,
                "total_rows_file2": 1,
                "matching_rows": 0,
                "only_in_file1": 1,
                "only_in_file2": 0,
                "differences": 1,
                "rows_with_differences": 1,
            }

            result = compare_db_to_file(
                query_or_table="SELECT * FROM FOO",
                mapping_config=mapping_cfg,
                actual_file=str(actual_file),
                output_format="json",
                key_columns=["ID"],
            )

        assert result["workflow"]["status"] == "failed"

    def test_extracted_row_count_in_workflow(self, tmp_path: Path) -> None:
        """Workflow metadata must include db_rows_extracted."""
        from src.services.db_file_compare_service import compare_db_to_file

        actual_file = _write_temp_csv(tmp_path, [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"}])
        mapping_cfg = _make_mapping([{"name": "ID"}, {"name": "NAME"}, {"name": "AMOUNT"}])

        with (
            patch("src.services.db_file_compare_service.OracleConnection") as mock_conn_cls,
            patch("src.services.db_file_compare_service.DataExtractor") as mock_extractor_cls,
            patch("src.services.db_file_compare_service.run_compare_service") as mock_compare,
        ):
            mock_extractor = MagicMock()
            mock_extractor.extract_by_query.return_value = _sample_df()
            mock_extractor_cls.return_value = mock_extractor
            mock_conn_cls.from_env.return_value = MagicMock()
            mock_compare.return_value = {
                "structure_compatible": True,
                "total_rows_file1": 2,
                "total_rows_file2": 1,
                "matching_rows": 1,
                "only_in_file1": 1,
                "only_in_file2": 0,
                "differences": 0,
            }

            result = compare_db_to_file(
                query_or_table="SELECT * FROM FOO",
                mapping_config=mapping_cfg,
                actual_file=str(actual_file),
                output_format="json",
                key_columns=["ID"],
            )

        assert result["workflow"]["db_rows_extracted"] == 2


# ---------------------------------------------------------------------------
# Tests for _is_sql_query helper
# ---------------------------------------------------------------------------


class TestIsSqlQuery:
    """Tests for the internal _is_sql_query helper."""

    def test_select_detected_as_sql(self) -> None:
        from src.services.db_file_compare_service import _is_sql_query
        assert _is_sql_query("SELECT * FROM FOO") is True

    def test_plain_table_name_not_sql(self) -> None:
        from src.services.db_file_compare_service import _is_sql_query
        assert _is_sql_query("SHAW_SRC_P327") is False

    def test_lowercase_select_detected_as_sql(self) -> None:
        from src.services.db_file_compare_service import _is_sql_query
        assert _is_sql_query("select id from foo") is True

    def test_schema_qualified_table_not_sql(self) -> None:
        from src.services.db_file_compare_service import _is_sql_query
        assert _is_sql_query("CM3INT.SHAW_SRC_P327") is False

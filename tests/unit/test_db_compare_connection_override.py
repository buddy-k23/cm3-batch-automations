"""Tests for compare_db_to_file() connection_override parameter."""
from __future__ import annotations
from unittest.mock import MagicMock, patch
import pytest


def test_uses_env_connection_when_no_override():
    """Without override, OracleConnection.from_env() is called."""
    with (
        patch("src.services.db_file_compare_service.OracleConnection") as mock_conn,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value={
            "structure_compatible": True,
            "total_rows_file1": 0, "total_rows_file2": 0,
            "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0,
            "differences": 0,
        }),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        import pandas as pd
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        tmp.close()
        try:
            from src.services.db_file_compare_service import compare_db_to_file
            compare_db_to_file(
                query_or_table="SELECT 1 FROM DUAL",
                mapping_config={"fields": [{"name": "A"}]},
                actual_file=tmp.name,
            )
        finally:
            os.unlink(tmp.name)
        mock_conn.from_env.assert_called_once()


def test_uses_override_connection_when_provided():
    """With oracle override, OracleConnection is built from override values."""
    override = {
        "db_host": "myhost:1521/FREE",
        "db_user": "myuser",
        "db_password": "secret",
        "db_schema": "MYSCHEMA",
        "db_adapter": "oracle",
    }
    with (
        patch("src.services.db_file_compare_service.OracleConnection") as mock_conn,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value={
            "structure_compatible": True,
            "total_rows_file1": 0, "total_rows_file2": 0,
            "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0,
            "differences": 0,
        }),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        import pandas as pd
        mock_conn.return_value = MagicMock()
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        tmp.close()
        try:
            from src.services.db_file_compare_service import compare_db_to_file
            compare_db_to_file(
                query_or_table="SELECT 1 FROM DUAL",
                mapping_config={"fields": [{"name": "A"}]},
                actual_file=tmp.name,
                connection_override=override,
            )
        finally:
            os.unlink(tmp.name)
        mock_conn.assert_called_once_with(
            username="myuser",
            password="secret",
            dsn="myhost:1521/FREE",
        )
        mock_conn.from_env.assert_not_called()


def test_non_oracle_override_falls_back_to_env():
    """Non-oracle adapter in override still uses from_env()."""
    override = {
        "db_host": "myhost",
        "db_user": "u",
        "db_password": "p",
        "db_adapter": "postgresql",
    }
    with (
        patch("src.services.db_file_compare_service.OracleConnection") as mock_conn,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value={
            "structure_compatible": True,
            "total_rows_file1": 0, "total_rows_file2": 0,
            "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0,
            "differences": 0,
        }),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        import pandas as pd
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        import tempfile, os
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
        tmp.close()
        try:
            from src.services.db_file_compare_service import compare_db_to_file
            compare_db_to_file(
                query_or_table="SELECT 1 FROM DUAL",
                mapping_config={"fields": [{"name": "A"}]},
                actual_file=tmp.name,
                connection_override=override,
            )
        finally:
            os.unlink(tmp.name)
        mock_conn.from_env.assert_called_once()

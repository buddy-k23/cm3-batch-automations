"""Tests for compare_db_to_file() connection_override handling (S15-2, #405).

After ADR 0022 §5 the DB side is built via the adapter factory, not
``OracleConnection``.  These tests assert the override → adapter routing:

- no override / no connection values → the env-configured adapter
  (:func:`get_database_adapter` with no explicit type);
- an explicit override with credentials → the concrete adapter for the named
  backend constructed from the override values (Oracle, PostgreSQL, SQLite).

They patch the *constructors* the service calls so no real DB is touched, and
keep the DataExtractor/compare layers mocked.
"""
from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, patch

import pandas as pd

_COMPARE_RESULT = {
    "structure_compatible": True,
    "total_rows_file1": 0,
    "total_rows_file2": 0,
    "matching_rows": 0,
    "only_in_file1": 0,
    "only_in_file2": 0,
    "differences": 0,
}


def _run(connection_override=None):
    """Invoke compare_db_to_file with the DB + compare layers mocked."""
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".txt")
    tmp.close()
    try:
        from src.services.db_file_compare_service import compare_db_to_file

        return compare_db_to_file(
            query_or_table="SELECT 1 FROM DUAL",
            mapping_config={"fields": [{"name": "A"}]},
            actual_file=tmp.name,
            connection_override=connection_override,
        )
    finally:
        os.unlink(tmp.name)


def test_uses_env_adapter_when_no_override():
    """Without override, the env-configured adapter (factory, no type) is used."""
    with (
        patch("src.services.db_file_compare_service.get_database_adapter") as mock_factory,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value=_COMPARE_RESULT),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        mock_factory.return_value = MagicMock()
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        _run()
        mock_factory.assert_called_once_with(None)


def test_oracle_override_builds_oracle_adapter():
    """An oracle override with credentials builds OracleAdapter from them."""
    override = {
        "db_host": "myhost:1521/FREE",
        "db_user": "myuser",
        "db_password": "secret",
        "db_schema": "MYSCHEMA",
        "db_adapter": "oracle",
    }
    with (
        patch("src.database.adapters.oracle_adapter.OracleAdapter") as mock_oracle,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value=_COMPARE_RESULT),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        mock_oracle.return_value = MagicMock()
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        _run(override)
        mock_oracle.assert_called_once_with(
            username="myuser",
            password="secret",
            dsn="myhost:1521/FREE",
        )


def test_postgresql_override_builds_postgresql_adapter():
    """A postgresql override builds PostgreSQLAdapter (db-compare not oracle-locked)."""
    override = {
        "db_host": "pghost",
        "db_port": 5432,
        "db_name": "valdo",
        "db_user": "u",
        "db_password": "p",
        "db_adapter": "postgresql",
    }
    with (
        patch("src.database.adapters.postgresql_adapter.PostgreSQLAdapter") as mock_pg,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value=_COMPARE_RESULT),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        mock_pg.return_value = MagicMock()
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        _run(override)
        mock_pg.assert_called_once_with(
            host="pghost",
            port=5432,
            database="valdo",
            username="u",
            password="p",
        )


def test_adapter_only_override_uses_factory_for_env_credentials():
    """An override naming an adapter but no credentials uses the factory."""
    override = {"db_adapter": "postgresql"}
    with (
        patch("src.services.db_file_compare_service.get_database_adapter") as mock_factory,
        patch("src.services.db_file_compare_service.DataExtractor") as mock_ext,
        patch("src.services.db_file_compare_service.run_compare_service", return_value=_COMPARE_RESULT),
        patch("src.services.db_file_compare_service._df_to_temp_file", return_value="/tmp/x.txt"),
    ):
        mock_factory.return_value = MagicMock()
        mock_ext.return_value.extract_by_query.return_value = pd.DataFrame({"A": []})
        _run(override)
        mock_factory.assert_called_once_with("postgresql")

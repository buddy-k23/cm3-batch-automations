"""Unit tests for src.database.adapters — pluggable database adapter layer.

Tests cover:
- DatabaseAdapter ABC contract
- SQLiteAdapter full behaviour (no external DB required)
- OracleAdapter initialisation (mocked oracledb)
- PostgreSQLAdapter initialisation (mocked psycopg2)
- Factory function routing by DB_ADAPTER env var
- Backward compatibility: DB_ADAPTER=oracle with existing ORACLE_* vars
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# DatabaseAdapter ABC
# ---------------------------------------------------------------------------


class TestDatabaseAdapterABC:
    """The abstract base class must be un-instantiable directly."""

    def test_cannot_instantiate_abstract_base(self) -> None:
        """DatabaseAdapter cannot be instantiated; all abstract methods required."""
        from src.database.adapters.base import DatabaseAdapter

        with pytest.raises(TypeError):
            DatabaseAdapter()  # type: ignore[abstract]

    def test_context_manager_protocol_calls_connect_disconnect(self) -> None:
        """__enter__ calls connect(); __exit__ calls disconnect()."""
        from src.database.adapters.base import DatabaseAdapter

        class ConcreteAdapter(DatabaseAdapter):
            def connect(self) -> None:
                pass

            def disconnect(self) -> None:
                pass

            def execute_query(self, sql: str, params: dict = None) -> pd.DataFrame:
                return pd.DataFrame()

            def get_table_columns(self, table: str, schema: str = None) -> list:
                return []

            def table_exists(self, table: str, schema: str = None) -> bool:
                return False

            def extract_to_file(
                self, query: str, output_path: str, delimiter: str = "|"
            ) -> int:
                return 0

        adapter = ConcreteAdapter()
        connect_called = []
        disconnect_called = []
        adapter.connect = lambda: connect_called.append(True)  # type: ignore[assignment]
        adapter.disconnect = lambda: disconnect_called.append(True)  # type: ignore[assignment]

        with adapter:
            pass

        assert connect_called
        assert disconnect_called


# ---------------------------------------------------------------------------
# SQLiteAdapter
# ---------------------------------------------------------------------------


class TestSQLiteAdapter:
    """Full behavioural tests for SQLiteAdapter using in-memory / temp databases."""

    def _make_adapter(self, db_path: str = ":memory:") -> Any:
        from src.database.adapters.sqlite_adapter import SQLiteAdapter

        return SQLiteAdapter(db_path=db_path)

    def test_connect_and_disconnect(self) -> None:
        """connect() opens a connection; disconnect() closes it."""
        adapter = self._make_adapter()
        adapter.connect()
        assert adapter._connection is not None
        adapter.disconnect()
        assert adapter._connection is None

    def test_context_manager_connects_and_disconnects(self) -> None:
        """Using the adapter as a context manager yields the adapter itself."""
        adapter = self._make_adapter()
        with adapter as ctx:
            assert ctx is adapter
            assert adapter._connection is not None
        assert adapter._connection is None

    def test_execute_query_returns_dataframe(self) -> None:
        """execute_query returns a pandas DataFrame for a valid SELECT."""
        adapter = self._make_adapter()
        adapter.connect()
        df = adapter.execute_query("SELECT 1 AS value")
        adapter.disconnect()

        assert isinstance(df, pd.DataFrame)
        assert "value" in df.columns
        assert df.iloc[0]["value"] == 1

    def test_execute_query_with_params(self) -> None:
        """execute_query accepts named bind parameters."""
        adapter = self._make_adapter()
        adapter.connect()
        df = adapter.execute_query("SELECT :x AS result", params={"x": 42})
        adapter.disconnect()

        assert df.iloc[0]["result"] == 42

    def test_table_exists_returns_false_when_missing(self) -> None:
        """table_exists returns False for a table that does not exist."""
        adapter = self._make_adapter()
        adapter.connect()
        result = adapter.table_exists("no_such_table")
        adapter.disconnect()

        assert result is False

    def test_table_exists_returns_true_after_create(self) -> None:
        """table_exists returns True after CREATE TABLE."""
        adapter = self._make_adapter()
        adapter.connect()
        adapter._connection.execute(
            "CREATE TABLE test_table (id INTEGER, name TEXT)"
        )
        result = adapter.table_exists("test_table")
        adapter.disconnect()

        assert result is True

    def test_get_table_columns_returns_column_names(self) -> None:
        """get_table_columns returns column names for an existing table."""
        adapter = self._make_adapter()
        adapter.connect()
        adapter._connection.execute(
            "CREATE TABLE my_table (col_a INTEGER, col_b TEXT, col_c REAL)"
        )
        columns = adapter.get_table_columns("my_table")
        adapter.disconnect()

        assert columns == ["col_a", "col_b", "col_c"]

    def test_get_table_columns_empty_for_nonexistent_table(self) -> None:
        """get_table_columns returns an empty list for a missing table."""
        adapter = self._make_adapter()
        adapter.connect()
        columns = adapter.get_table_columns("phantom_table")
        adapter.disconnect()

        assert columns == []

    def test_extract_to_file_writes_pipe_delimited_output(self) -> None:
        """extract_to_file writes correct pipe-delimited CSV and returns row count."""
        adapter = self._make_adapter()
        adapter.connect()
        adapter._connection.execute(
            "CREATE TABLE fruits (id INTEGER, name TEXT)"
        )
        adapter._connection.executemany(
            "INSERT INTO fruits VALUES (?, ?)",
            [(1, "apple"), (2, "banana"), (3, "cherry")],
        )

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as fh:
            output_path = fh.name

        try:
            row_count = adapter.extract_to_file(
                "SELECT id, name FROM fruits", output_path, delimiter="|"
            )
            lines = Path(output_path).read_text(encoding="utf-8").splitlines()
        finally:
            Path(output_path).unlink(missing_ok=True)

        adapter.disconnect()

        assert row_count == 3
        assert lines[0] == "id|name"
        assert lines[1] == "1|apple"
        assert lines[2] == "2|banana"
        assert lines[3] == "3|cherry"

    def test_extract_to_file_custom_delimiter(self) -> None:
        """extract_to_file respects the delimiter argument."""
        adapter = self._make_adapter()
        adapter.connect()
        adapter._connection.execute("CREATE TABLE t (a INTEGER, b INTEGER)")
        adapter._connection.execute("INSERT INTO t VALUES (10, 20)")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as fh:
            output_path = fh.name

        try:
            adapter.extract_to_file("SELECT a, b FROM t", output_path, delimiter=",")
            content = Path(output_path).read_text(encoding="utf-8")
        finally:
            Path(output_path).unlink(missing_ok=True)

        adapter.disconnect()

        assert "a,b" in content
        assert "10,20" in content

    def test_extract_to_file_returns_zero_for_empty_result(self) -> None:
        """extract_to_file returns 0 rows for an empty table."""
        adapter = self._make_adapter()
        adapter.connect()
        adapter._connection.execute("CREATE TABLE empty_table (x INTEGER)")

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False
        ) as fh:
            output_path = fh.name

        try:
            row_count = adapter.extract_to_file(
                "SELECT x FROM empty_table", output_path
            )
        finally:
            Path(output_path).unlink(missing_ok=True)

        adapter.disconnect()

        assert row_count == 0

    def test_file_path_database(self) -> None:
        """SQLiteAdapter works with a file-based database path."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
            db_path = fh.name

        try:
            adapter = self._make_adapter(db_path=db_path)
            adapter.connect()
            adapter._connection.execute("CREATE TABLE t (v TEXT)")
            adapter._connection.execute("INSERT INTO t VALUES ('hello')")
            df = adapter.execute_query("SELECT v FROM t")
            adapter.disconnect()
            assert df.iloc[0]["v"] == "hello"
        finally:
            Path(db_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# OracleAdapter
# ---------------------------------------------------------------------------


class TestOracleAdapter:
    """Tests for OracleAdapter initialisation (oracledb mocked)."""

    def test_init_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OracleAdapter reads ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN from env."""
        monkeypatch.setenv("ORACLE_USER", "TESTUSER")
        monkeypatch.setenv("ORACLE_PASSWORD", "secret")
        monkeypatch.setenv("ORACLE_DSN", "host:1521/svc")

        from src.database.adapters.oracle_adapter import OracleAdapter

        adapter = OracleAdapter()
        assert adapter.username == "TESTUSER"
        assert adapter.password == "secret"
        assert adapter.dsn == "host:1521/svc"

    def test_connect_calls_oracledb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """connect() delegates to oracledb.connect with correct credentials."""
        monkeypatch.setenv("ORACLE_USER", "U")
        monkeypatch.setenv("ORACLE_PASSWORD", "P")
        monkeypatch.setenv("ORACLE_DSN", "H:1521/DB")

        mock_conn = MagicMock()

        with patch("src.database.adapters.oracle_adapter.oracledb") as mock_oracle:
            mock_oracle.connect.return_value = mock_conn
            mock_oracle.Error = Exception

            from src.database.adapters.oracle_adapter import OracleAdapter

            adapter = OracleAdapter()
            adapter.connect()

            mock_oracle.connect.assert_called_once_with(
                user="U", password="P", dsn="H:1521/DB"
            )
            assert adapter._connection is mock_conn

    def test_connect_fails_fast_without_password(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """connect() raises RuntimeError when password is empty."""
        monkeypatch.setenv("ORACLE_USER", "U")
        monkeypatch.delenv("ORACLE_PASSWORD", raising=False)
        monkeypatch.setenv("ORACLE_DSN", "H:1521/DB")

        from src.database.adapters.oracle_adapter import OracleAdapter

        adapter = OracleAdapter()
        with pytest.raises(RuntimeError, match="ORACLE_PASSWORD"):
            adapter.connect()

    def test_disconnect_closes_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """disconnect() calls close() on the underlying connection."""
        mock_conn = MagicMock()

        with patch("src.database.adapters.oracle_adapter.oracledb") as mock_oracle:
            mock_oracle.Error = Exception

            from src.database.adapters.oracle_adapter import OracleAdapter

            adapter = OracleAdapter(username="U", password="P", dsn="H:1521/DB")
            adapter._connection = mock_conn
            adapter.disconnect()

            mock_conn.close.assert_called_once()
            assert adapter._connection is None

    def test_execute_query_returns_dataframe(self) -> None:
        """execute_query returns a DataFrame from pd.read_sql."""
        expected_df = pd.DataFrame({"COL": [1, 2, 3]})
        mock_conn = MagicMock()

        with patch("src.database.adapters.oracle_adapter.pd") as mock_pd:
            mock_pd.read_sql.return_value = expected_df

            from src.database.adapters.oracle_adapter import OracleAdapter

            adapter = OracleAdapter(username="U", password="P", dsn="H:1521/DB")
            adapter._connection = mock_conn
            result = adapter.execute_query("SELECT 1 FROM DUAL")

            assert result is expected_df

    def test_table_exists_returns_true_when_found(self) -> None:
        """table_exists returns True when ALL_TABLES finds the table."""
        mock_conn = MagicMock()
        found_df = pd.DataFrame({"COUNT_": [1]})

        with patch("src.database.adapters.oracle_adapter.pd") as mock_pd:
            mock_pd.read_sql.return_value = found_df

            from src.database.adapters.oracle_adapter import OracleAdapter

            adapter = OracleAdapter(username="U", password="P", dsn="H:1521/DB")
            adapter._connection = mock_conn
            result = adapter.table_exists("MY_TABLE")

            assert result is True

    def test_table_exists_returns_false_when_not_found(self) -> None:
        """table_exists returns False when ALL_TABLES count is 0."""
        mock_conn = MagicMock()
        not_found_df = pd.DataFrame({"COUNT_": [0]})

        with patch("src.database.adapters.oracle_adapter.pd") as mock_pd:
            mock_pd.read_sql.return_value = not_found_df

            from src.database.adapters.oracle_adapter import OracleAdapter

            adapter = OracleAdapter(username="U", password="P", dsn="H:1521/DB")
            adapter._connection = mock_conn
            result = adapter.table_exists("GHOST_TABLE")

            assert result is False


# ---------------------------------------------------------------------------
# PostgreSQLAdapter
# ---------------------------------------------------------------------------


class TestPostgreSQLAdapter:
    """Tests for PostgreSQLAdapter initialisation (psycopg2 mocked)."""

    def test_init_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """PostgreSQLAdapter reads DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD."""
        monkeypatch.setenv("DB_HOST", "pghost")
        monkeypatch.setenv("DB_PORT", "5432")
        monkeypatch.setenv("DB_NAME", "mydb")
        monkeypatch.setenv("DB_USER", "pguser")
        monkeypatch.setenv("DB_PASSWORD", "pgpass")

        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        adapter = PostgreSQLAdapter()
        assert adapter.host == "pghost"
        assert adapter.port == 5432
        assert adapter.database == "mydb"
        assert adapter.username == "pguser"
        assert adapter.password == "pgpass"

    def test_connect_calls_psycopg2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """connect() delegates to psycopg2.connect with correct keyword args."""
        mock_conn = MagicMock()
        mock_psycopg2 = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        adapter = PostgreSQLAdapter(
            host="pghost", port=5432, database="mydb",
            username="pguser", password="pgpass",
        )

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            adapter.connect()

        mock_psycopg2.connect.assert_called_once_with(
            host="pghost",
            port=5432,
            database="mydb",
            user="pguser",
            password="pgpass",
        )
        assert adapter._connection is mock_conn

    def test_missing_psycopg2_raises_import_error_with_message(self) -> None:
        """When psycopg2 is not installed, connect() raises ImportError with install hint."""
        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        adapter = PostgreSQLAdapter(
            host="h", port=5432, database="d", username="u", password="p"
        )

        with patch.dict("sys.modules", {"psycopg2": None}):
            with pytest.raises(ImportError, match="psycopg2"):
                adapter.connect()

    def test_disconnect_closes_connection(self) -> None:
        """disconnect() calls close() on the underlying connection."""
        mock_conn = MagicMock()
        mock_psycopg2 = MagicMock()
        mock_psycopg2.connect.return_value = mock_conn

        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        adapter = PostgreSQLAdapter(
            host="h", port=5432, database="d", username="u", password="p"
        )

        with patch.dict("sys.modules", {"psycopg2": mock_psycopg2}):
            adapter.connect()

        adapter.disconnect()

        mock_conn.close.assert_called_once()
        assert adapter._connection is None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


class TestGetDatabaseAdapter:
    """Tests for the get_database_adapter factory function."""

    def test_factory_returns_oracle_by_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When DB_ADAPTER is unset, factory returns an OracleAdapter."""
        monkeypatch.delenv("DB_ADAPTER", raising=False)

        from src.database.adapters.factory import get_database_adapter
        from src.database.adapters.oracle_adapter import OracleAdapter

        adapter = get_database_adapter()
        assert isinstance(adapter, OracleAdapter)

    def test_factory_returns_oracle_when_explicit(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passing adapter_type='oracle' returns OracleAdapter."""
        from src.database.adapters.factory import get_database_adapter
        from src.database.adapters.oracle_adapter import OracleAdapter

        adapter = get_database_adapter(adapter_type="oracle")
        assert isinstance(adapter, OracleAdapter)

    def test_factory_returns_sqlite(self) -> None:
        """Passing adapter_type='sqlite' returns SQLiteAdapter."""
        from src.database.adapters.factory import get_database_adapter
        from src.database.adapters.sqlite_adapter import SQLiteAdapter

        adapter = get_database_adapter(adapter_type="sqlite")
        assert isinstance(adapter, SQLiteAdapter)

    def test_factory_returns_postgresql(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Passing adapter_type='postgresql' returns PostgreSQLAdapter."""
        from src.database.adapters.factory import get_database_adapter
        from src.database.adapters.postgresql_adapter import PostgreSQLAdapter

        adapter = get_database_adapter(adapter_type="postgresql")
        assert isinstance(adapter, PostgreSQLAdapter)

    def test_factory_reads_db_adapter_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Factory reads DB_ADAPTER from environment when adapter_type is None."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")

        from src.database.adapters import factory as f
        import importlib
        importlib.reload(f)

        from src.database.adapters.sqlite_adapter import SQLiteAdapter

        adapter = f.get_database_adapter()
        assert isinstance(adapter, SQLiteAdapter)

    def test_factory_raises_for_unknown_adapter(self) -> None:
        """Passing an unknown adapter_type raises ValueError."""
        from src.database.adapters.factory import get_database_adapter

        with pytest.raises(ValueError, match="Unknown adapter"):
            get_database_adapter(adapter_type="mysql")

    def test_factory_env_var_overridden_by_explicit_arg(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Explicit adapter_type argument overrides DB_ADAPTER env var."""
        monkeypatch.setenv("DB_ADAPTER", "oracle")

        from src.database.adapters.factory import get_database_adapter
        from src.database.adapters.sqlite_adapter import SQLiteAdapter

        adapter = get_database_adapter(adapter_type="sqlite")
        assert isinstance(adapter, SQLiteAdapter)


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """DB_ADAPTER=oracle must continue working with existing ORACLE_* vars."""

    def test_oracle_adapter_uses_oracle_env_vars(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """OracleAdapter still reads ORACLE_USER, ORACLE_PASSWORD, ORACLE_DSN."""
        monkeypatch.setenv("DB_ADAPTER", "oracle")
        monkeypatch.setenv("ORACLE_USER", "LEGACY_USER")
        monkeypatch.setenv("ORACLE_PASSWORD", "LEGACY_PASS")
        monkeypatch.setenv("ORACLE_DSN", "legacy:1521/SVC")

        from src.database.adapters.oracle_adapter import OracleAdapter

        adapter = OracleAdapter()
        assert adapter.username == "LEGACY_USER"
        assert adapter.password == "LEGACY_PASS"
        assert adapter.dsn == "legacy:1521/SVC"

    def test_db_config_exposes_adapter_type(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_db_config() exposes db_adapter from DB_ADAPTER env var."""
        monkeypatch.setenv("DB_ADAPTER", "postgresql")

        from importlib import reload
        import src.config.db_config as m
        reload(m)

        cfg = m.get_db_config()
        assert cfg.db_adapter == "postgresql"

    def test_db_config_adapter_defaults_to_oracle(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """db_adapter defaults to 'oracle' when DB_ADAPTER is unset."""
        monkeypatch.delenv("DB_ADAPTER", raising=False)

        from importlib import reload
        import src.config.db_config as m
        reload(m)

        cfg = m.get_db_config()
        assert cfg.db_adapter == "oracle"

    def test_db_config_exposes_generic_host_port_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """get_db_config() exposes db_host, db_port, db_name when set."""
        monkeypatch.setenv("DB_HOST", "myhost")
        monkeypatch.setenv("DB_PORT", "5432")
        monkeypatch.setenv("DB_NAME", "mydb")

        from importlib import reload
        import src.config.db_config as m
        reload(m)

        cfg = m.get_db_config()
        assert cfg.db_host == "myhost"
        assert cfg.db_port == "5432"
        assert cfg.db_name == "mydb"

    def test_db_config_generic_vars_default_to_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """db_host, db_port, db_name default to None when env vars are absent."""
        monkeypatch.delenv("DB_HOST", raising=False)
        monkeypatch.delenv("DB_PORT", raising=False)
        monkeypatch.delenv("DB_NAME", raising=False)

        from importlib import reload
        import src.config.db_config as m
        reload(m)

        cfg = m.get_db_config()
        assert cfg.db_host is None
        assert cfg.db_port is None
        assert cfg.db_name is None


# ---------------------------------------------------------------------------
# __init__ exports
# ---------------------------------------------------------------------------


class TestAdaptersPackageExports:
    """The adapters package must export the key symbols."""

    def test_exports_database_adapter(self) -> None:
        """src.database.adapters exports DatabaseAdapter."""
        from src.database.adapters import DatabaseAdapter

        assert DatabaseAdapter is not None

    def test_exports_get_database_adapter(self) -> None:
        """src.database.adapters exports get_database_adapter."""
        from src.database.adapters import get_database_adapter

        assert callable(get_database_adapter)

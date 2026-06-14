"""Unit tests for src.config.db_config — database configuration module."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from src.config.db_config import DbConfig, get_db_config, get_connection


# ---------------------------------------------------------------------------
# get_db_config
# ---------------------------------------------------------------------------


class TestGetDbConfig:
    """Tests for get_db_config()."""

    def test_defaults_when_no_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When no ORACLE_* env vars are set, defaults are applied."""
        monkeypatch.delenv("ORACLE_USER", raising=False)
        monkeypatch.delenv("ORACLE_PASSWORD", raising=False)
        monkeypatch.delenv("ORACLE_DSN", raising=False)
        monkeypatch.delenv("ORACLE_SCHEMA", raising=False)

        cfg = get_db_config()

        assert cfg.user == "APP_INT"
        assert cfg.password == ""
        assert cfg.dsn == "localhost:1521/FREEPDB1"
        # schema defaults to user
        assert cfg.schema == "APP_INT"

    def test_custom_values_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Env vars override every default."""
        monkeypatch.setenv("ORACLE_USER", "TESTUSER")
        monkeypatch.setenv("ORACLE_PASSWORD", "s3cret")
        monkeypatch.setenv("ORACLE_DSN", "dbhost:1522/TESTPDB")
        monkeypatch.setenv("ORACLE_SCHEMA", "TESTSCHEMA")

        cfg = get_db_config()

        assert cfg.user == "TESTUSER"
        assert cfg.password == "s3cret"
        assert cfg.dsn == "dbhost:1522/TESTPDB"
        assert cfg.schema == "TESTSCHEMA"

    def test_schema_defaults_to_user(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When ORACLE_SCHEMA is not set, it falls back to ORACLE_USER."""
        monkeypatch.setenv("ORACLE_USER", "MYUSER")
        monkeypatch.delenv("ORACLE_SCHEMA", raising=False)

        cfg = get_db_config()

        assert cfg.schema == "MYUSER"

    def test_config_is_immutable(self) -> None:
        """DbConfig instances are frozen dataclasses."""
        cfg = DbConfig(user="u", password="p", dsn="d", schema="s")
        with pytest.raises(AttributeError):
            cfg.user = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# get_connection
# ---------------------------------------------------------------------------


class TestGetConnection:
    """Tests for get_connection()."""

    def test_fails_fast_without_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Attempting to connect with an empty password raises RuntimeError."""
        monkeypatch.delenv("ORACLE_PASSWORD", raising=False)

        with pytest.raises(RuntimeError, match="ORACLE_PASSWORD is not set"):
            get_connection()

    def test_fails_fast_with_explicit_empty_password(self) -> None:
        """An explicit empty password in config also triggers fail-fast."""
        cfg = DbConfig(user="u", password="", dsn="d", schema="s")
        with pytest.raises(RuntimeError, match="ORACLE_PASSWORD is not set"):
            get_connection(config=cfg)

    @patch("src.config.db_config.oracledb")
    def test_creates_connection_with_config(self, mock_oracledb: MagicMock) -> None:
        """When password is present, oracledb.connect is called correctly."""
        mock_conn = MagicMock()
        mock_oracledb.connect.return_value = mock_conn

        cfg = DbConfig(user="TESTUSER", password="pass", dsn="host:1521/DB", schema="S")
        result = get_connection(config=cfg)

        mock_oracledb.connect.assert_called_once_with(
            user="TESTUSER",
            password="pass",
            dsn="host:1521/DB",
        )
        assert result is mock_conn

    @patch("src.config.db_config.oracledb")
    def test_wraps_oracledb_error_in_connection_error(self, mock_oracledb: MagicMock) -> None:
        """oracledb.Error is wrapped in ConnectionError with context."""
        import oracledb as real_oracledb

        mock_oracledb.connect.side_effect = real_oracledb.Error("ORA-12541")
        mock_oracledb.Error = real_oracledb.Error

        cfg = DbConfig(user="u", password="p", dsn="bad:1521/X", schema="s")
        with pytest.raises(ConnectionError, match="Failed to connect"):
            get_connection(config=cfg)

    @patch("src.config.db_config.oracledb")
    def test_reads_env_when_no_config_passed(
        self, mock_oracledb: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When config=None, get_connection reads from env via get_db_config."""
        monkeypatch.setenv("ORACLE_USER", "ENVUSER")
        monkeypatch.setenv("ORACLE_PASSWORD", "envpass")
        monkeypatch.setenv("ORACLE_DSN", "envhost:1521/ENVDB")

        mock_conn = MagicMock()
        mock_oracledb.connect.return_value = mock_conn

        result = get_connection()

        mock_oracledb.connect.assert_called_once_with(
            user="ENVUSER",
            password="envpass",
            dsn="envhost:1521/ENVDB",
        )
        assert result is mock_conn


# ---------------------------------------------------------------------------
# OracleConnection.from_env integration
# ---------------------------------------------------------------------------


class TestOracleConnectionFromEnv:
    """Verify OracleConnection.from_env uses centralised config."""

    def test_from_env_uses_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env should pick up default user and DSN from db_config."""
        monkeypatch.delenv("ORACLE_USER", raising=False)
        monkeypatch.delenv("ORACLE_PASSWORD", raising=False)
        monkeypatch.delenv("ORACLE_DSN", raising=False)

        from src.database.connection import OracleConnection

        conn = OracleConnection.from_env()

        assert conn.username == "APP_INT"
        assert conn.dsn == "localhost:1521/FREEPDB1"
        assert conn.password == ""

    def test_from_env_reads_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_env should honour ORACLE_* env vars."""
        monkeypatch.setenv("ORACLE_USER", "CUSTOM")
        monkeypatch.setenv("ORACLE_PASSWORD", "pw")
        monkeypatch.setenv("ORACLE_DSN", "remote:1521/SVC")

        from src.database.connection import OracleConnection

        conn = OracleConnection.from_env()

        assert conn.username == "CUSTOM"
        assert conn.password == "pw"
        assert conn.dsn == "remote:1521/SVC"

    def test_connect_fails_fast_without_password(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OracleConnection.connect() should fail fast when password is empty."""
        monkeypatch.delenv("ORACLE_PASSWORD", raising=False)

        from src.database.connection import OracleConnection

        conn = OracleConnection.from_env()
        with pytest.raises(RuntimeError, match="ORACLE_PASSWORD is not set"):
            conn.connect()


# ---------------------------------------------------------------------------
# RunHistoryRepository schema configuration
# ---------------------------------------------------------------------------


class TestRunHistorySchema:
    """Verify run_history SQL uses configurable schema."""

    def test_sql_uses_configured_schema(self) -> None:
        """SQL helpers should interpolate the schema parameter."""
        from src.database.run_history import (
            _sql_insert_run,
            _sql_insert_test,
            _sql_fetch_history,
        )

        assert "MYSCHEMA.APP_RUN_HISTORY" in _sql_insert_run("MYSCHEMA")
        assert "MYSCHEMA.APP_RUN_TESTS" in _sql_insert_test("MYSCHEMA")
        assert "MYSCHEMA.APP_RUN_HISTORY" in _sql_fetch_history("MYSCHEMA")

    def test_repository_uses_env_schema(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """RunHistoryRepository picks up schema prefix from get_schema_prefix()."""
        from unittest.mock import MagicMock, patch
        from src.database.run_history import RunHistoryRepository

        mock_engine = MagicMock()
        with patch("src.database.run_history.get_schema_prefix", return_value="PRODSCHEMA."):
            repo = RunHistoryRepository(engine=mock_engine)

        assert repo._schema_prefix == "PRODSCHEMA."

    def test_repository_schema_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Explicit schema_prefix kwarg overrides the default."""
        from unittest.mock import MagicMock
        from src.database.run_history import RunHistoryRepository

        mock_engine = MagicMock()
        repo = RunHistoryRepository(engine=mock_engine, schema_prefix="OVERRIDE.")

        assert repo._schema_prefix == "OVERRIDE."

"""Unit tests for src.database.engine — shared SQLAlchemy engine factory."""

from __future__ import annotations

from unittest.mock import MagicMock, patch, call

import pytest

from src.database.engine import (
    check_connection,
    get_engine,
    get_schema_prefix,
    reset_engine,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clear_cache() -> None:
    """Ensure the lru_cache is empty before each test that needs it."""
    get_engine.cache_clear()


# ---------------------------------------------------------------------------
# get_engine — caching and creation
# ---------------------------------------------------------------------------


class TestGetEngine:
    """Tests for get_engine() engine factory and its lru_cache behaviour."""

    def setup_method(self) -> None:
        """Clear the lru_cache before every test in this class."""
        _clear_cache()

    def teardown_method(self) -> None:
        """Restore a clean cache after every test in this class."""
        _clear_cache()

    def test_returns_engine_instance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_engine() returns the Engine produced by create_engine."""
        mock_engine = MagicMock(name="engine")

        with patch("src.database.engine.create_engine", return_value=mock_engine) as mock_create:
            monkeypatch.setenv("DB_ADAPTER", "sqlite")
            monkeypatch.setenv("DB_PATH", ":memory:")

            result = get_engine()

            assert result is mock_engine
            mock_create.assert_called_once()

    def test_engine_is_cached_second_call_returns_same_object(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Calling get_engine() twice returns the same cached Engine instance."""
        mock_engine = MagicMock(name="engine")

        with patch("src.database.engine.create_engine", return_value=mock_engine) as mock_create:
            monkeypatch.setenv("DB_ADAPTER", "sqlite")
            monkeypatch.setenv("DB_PATH", ":memory:")

            first = get_engine()
            second = get_engine()

            assert first is second
            mock_create.assert_called_once()

    def test_create_engine_called_with_pool_pre_ping(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_engine is called with pool_pre_ping=True."""
        mock_engine = MagicMock(name="engine")

        with patch("src.database.engine.create_engine", return_value=mock_engine) as mock_create:
            monkeypatch.setenv("DB_ADAPTER", "sqlite")
            monkeypatch.setenv("DB_PATH", ":memory:")

            get_engine()

            _, kwargs = mock_create.call_args
            assert kwargs.get("pool_pre_ping") is True

    def test_create_engine_called_with_db_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """create_engine receives the URL returned by get_db_url()."""
        mock_engine = MagicMock(name="engine")

        with patch("src.database.engine.create_engine", return_value=mock_engine) as mock_create:
            monkeypatch.setenv("DB_ADAPTER", "sqlite")
            monkeypatch.setenv("DB_PATH", ":memory:")

            get_engine()

            positional_url = mock_create.call_args[0][0]
            assert positional_url == "sqlite:///:memory:"


# ---------------------------------------------------------------------------
# reset_engine
# ---------------------------------------------------------------------------


class TestResetEngine:
    """Tests for reset_engine() cache-clear utility."""

    def setup_method(self) -> None:
        _clear_cache()

    def teardown_method(self) -> None:
        _clear_cache()

    def test_reset_causes_create_engine_to_be_called_again(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """After reset_engine(), a subsequent call to get_engine() recreates the engine."""
        call_count = {"n": 0}

        def _fake_create(url, **kw):
            call_count["n"] += 1
            return MagicMock(name=f"engine_{call_count['n']}")

        with patch("src.database.engine.create_engine", side_effect=_fake_create):
            monkeypatch.setenv("DB_ADAPTER", "sqlite")
            monkeypatch.setenv("DB_PATH", ":memory:")

            get_engine()          # first call — count == 1
            reset_engine()
            get_engine()          # second call after reset — count == 2

            assert call_count["n"] == 2

    def test_reset_returns_none(self) -> None:
        """reset_engine() has no return value."""
        result = reset_engine()
        assert result is None


# ---------------------------------------------------------------------------
# get_schema_prefix
# ---------------------------------------------------------------------------


class TestGetSchemaPrefix:
    """Tests for get_schema_prefix() across all supported adapters."""

    def test_oracle_default_returns_app_int_dot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Oracle default env (ORACLE_USER=APP_INT) returns 'APP_INT.'."""
        monkeypatch.setenv("DB_ADAPTER", "oracle")
        monkeypatch.delenv("ORACLE_SCHEMA", raising=False)
        monkeypatch.setenv("ORACLE_USER", "APP_INT")
        monkeypatch.delenv("VALDO_SCHEMA", raising=False)

        prefix = get_schema_prefix()

        assert prefix == "APP_INT."

    def test_postgresql_default_returns_public_dot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """PostgreSQL adapter (VALDO_SCHEMA defaults to 'public') returns 'public.'."""
        monkeypatch.setenv("DB_ADAPTER", "postgresql")
        monkeypatch.delenv("VALDO_SCHEMA", raising=False)

        prefix = get_schema_prefix()

        assert prefix == "public."

    def test_sqlite_returns_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SQLite adapter returns '' because SQLite has no schema concept."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")
        monkeypatch.delenv("VALDO_SCHEMA", raising=False)

        prefix = get_schema_prefix()

        assert prefix == ""

    def test_valdo_schema_override_adds_dot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """VALDO_SCHEMA override is honoured and a trailing dot is appended."""
        monkeypatch.setenv("DB_ADAPTER", "oracle")
        monkeypatch.setenv("VALDO_SCHEMA", "CUSTOM_SCHEMA")

        prefix = get_schema_prefix()

        assert prefix == "CUSTOM_SCHEMA."

    def test_empty_schema_returns_empty_string(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When get_valdo_schema() returns '', get_schema_prefix() returns ''."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")
        monkeypatch.setenv("VALDO_SCHEMA", "")

        prefix = get_schema_prefix()

        assert prefix == ""


# ---------------------------------------------------------------------------
# check_connection
# ---------------------------------------------------------------------------


class TestCheckConnection:
    """Tests for check_connection() database connectivity probe."""

    def setup_method(self) -> None:
        _clear_cache()

    def teardown_method(self) -> None:
        _clear_cache()

    def test_returns_true_when_connection_succeeds(self) -> None:
        """check_connection() returns True when the engine connects without error."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        with patch("src.database.engine.get_engine", return_value=mock_engine):
            result = check_connection()

        assert result is True

    def test_returns_false_when_engine_raises(self) -> None:
        """check_connection() returns False when the engine raises any exception."""
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("connection refused")

        with patch("src.database.engine.get_engine", return_value=mock_engine):
            result = check_connection()

        assert result is False

    def test_returns_false_when_execute_raises(self) -> None:
        """check_connection() returns False when conn.execute() raises."""
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.execute.side_effect = Exception("query failed")

        mock_engine = MagicMock()
        mock_engine.connect.return_value = mock_conn

        with patch("src.database.engine.get_engine", return_value=mock_engine):
            result = check_connection()

        assert result is False

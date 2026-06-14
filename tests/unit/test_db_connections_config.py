"""Unit tests for src/config/db_connections.py — written TDD before implementation.

Covers:
1. Valid JSON with multiple connections → returns correct dict
2. Invalid adapter value → entry skipped, warning logged, rest returned
3. Malformed JSON → returns {}, warning logged
4. Unset env var → returns {}
5. Empty DB_CONNECTIONS="" → returns {}
"""

from __future__ import annotations

import json
import logging

import pytest

from src.config.db_connections import NamedDbConnection, get_named_connections

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STAGING = {
    "host": "stg:1522/DB",
    "user": "Valdo",
    "password": "secret",
    "schema": "APP_INT",
    "adapter": "oracle",
}

_DEV1 = {
    "host": "dev1:1522/DB",
    "user": "Valdo",
    "password": "devpass",
    "schema": "APP_INT",
    "adapter": "oracle",
}

_PG_CONN = {
    "host": "pg-host:5432",
    "user": "pguser",
    "password": "pgpass",
    "schema": "public",
    "adapter": "postgresql",
}

_SQLITE_CONN = {
    "host": "/tmp/test.db",
    "user": "",
    "password": "",
    "schema": "main",
    "adapter": "sqlite",
}


# ---------------------------------------------------------------------------
# NamedDbConnection model tests
# ---------------------------------------------------------------------------


class TestNamedDbConnection:
    """Tests for the NamedDbConnection Pydantic model."""

    def test_valid_oracle_connection(self) -> None:
        """Model accepts a fully-valid Oracle connection dict."""
        conn = NamedDbConnection(name="STAGING", **_STAGING)
        assert conn.name == "STAGING"
        assert conn.host == "stg:1522/DB"
        assert conn.user == "Valdo"
        assert conn.password == "secret"
        assert conn.schema == "APP_INT"
        assert conn.adapter == "oracle"

    def test_valid_postgresql_connection(self) -> None:
        """Model accepts a valid PostgreSQL connection."""
        conn = NamedDbConnection(name="PG", **_PG_CONN)
        assert conn.adapter == "postgresql"

    def test_valid_sqlite_connection(self) -> None:
        """Model accepts a valid SQLite connection."""
        conn = NamedDbConnection(name="LOCAL", **_SQLITE_CONN)
        assert conn.adapter == "sqlite"

    def test_invalid_adapter_raises(self) -> None:
        """Model raises ValidationError for unsupported adapter values."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError, match="adapter"):
            NamedDbConnection(
                name="BAD",
                host="x",
                user="u",
                password="p",
                schema="s",
                adapter="mysql",
            )

    def test_extra_fields_ignored(self) -> None:
        """Extra fields in the dict are silently ignored."""
        conn = NamedDbConnection(
            name="X",
            host="h",
            user="u",
            password="p",
            schema="s",
            adapter="oracle",
            unknown_field="ignored",
        )
        assert conn.name == "X"


# ---------------------------------------------------------------------------
# get_named_connections() tests
# ---------------------------------------------------------------------------


class TestGetNamedConnections:
    """Tests for the get_named_connections() function."""

    def test_returns_correct_dict_for_multiple_connections(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Valid JSON with multiple connections returns a keyed dict."""
        payload = json.dumps({"STAGING": _STAGING, "DEV-1": _DEV1})
        monkeypatch.setenv("DB_CONNECTIONS", payload)

        result = get_named_connections()

        assert set(result.keys()) == {"STAGING", "DEV-1"}
        assert isinstance(result["STAGING"], NamedDbConnection)
        assert result["STAGING"].host == "stg:1522/DB"
        assert result["DEV-1"].host == "dev1:1522/DB"
        assert result["STAGING"].name == "STAGING"
        assert result["DEV-1"].name == "DEV-1"

    def test_invalid_adapter_skipped_and_rest_returned(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Entry with invalid adapter is skipped; valid entries are returned."""
        bad = dict(_STAGING, adapter="mysql")
        payload = json.dumps({"BAD": bad, "OK": _DEV1})
        monkeypatch.setenv("DB_CONNECTIONS", payload)

        with caplog.at_level(logging.WARNING, logger="src.config.db_connections"):
            result = get_named_connections()

        assert "BAD" not in result
        assert "OK" in result
        assert isinstance(result["OK"], NamedDbConnection)
        assert any("BAD" in msg for msg in caplog.messages)

    def test_malformed_json_returns_empty_dict_and_logs_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Malformed JSON string returns {} and logs a warning."""
        monkeypatch.setenv("DB_CONNECTIONS", "{not valid json}")

        with caplog.at_level(logging.WARNING, logger="src.config.db_connections"):
            result = get_named_connections()

        assert result == {}
        assert any("not valid JSON" in msg for msg in caplog.messages)

    def test_unset_env_var_returns_empty_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Missing DB_CONNECTIONS env var returns {}."""
        monkeypatch.delenv("DB_CONNECTIONS", raising=False)
        result = get_named_connections()
        assert result == {}

    def test_empty_env_var_returns_empty_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Empty string DB_CONNECTIONS="" returns {}."""
        monkeypatch.setenv("DB_CONNECTIONS", "")
        result = get_named_connections()
        assert result == {}

    def test_whitespace_only_env_var_returns_empty_dict(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Whitespace-only DB_CONNECTIONS returns {}."""
        monkeypatch.setenv("DB_CONNECTIONS", "   ")
        result = get_named_connections()
        assert result == {}

    def test_non_dict_json_returns_empty_dict_and_logs_warning(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A JSON array (not object) returns {} and logs a warning."""
        monkeypatch.setenv("DB_CONNECTIONS", json.dumps([_STAGING]))

        with caplog.at_level(logging.WARNING, logger="src.config.db_connections"):
            result = get_named_connections()

        assert result == {}
        assert any("JSON object" in msg for msg in caplog.messages)

    def test_single_valid_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Single valid connection is returned correctly."""
        monkeypatch.setenv("DB_CONNECTIONS", json.dumps({"PROD": _STAGING}))
        result = get_named_connections()
        assert len(result) == 1
        assert result["PROD"].adapter == "oracle"
        assert result["PROD"].name == "PROD"

    def test_all_adapters_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """All three valid adapter types are accepted without warnings."""
        payload = json.dumps({
            "ORA": _STAGING,
            "PG": _PG_CONN,
            "LITE": _SQLITE_CONN,
        })
        monkeypatch.setenv("DB_CONNECTIONS", payload)
        result = get_named_connections()
        assert len(result) == 3
        assert result["ORA"].adapter == "oracle"
        assert result["PG"].adapter == "postgresql"
        assert result["LITE"].adapter == "sqlite"

    def test_entry_missing_required_field_skipped_and_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Entry missing a required field (e.g. host) is skipped with a warning."""
        incomplete = {"user": "u", "password": "p", "schema": "s", "adapter": "oracle"}
        payload = json.dumps({"INCOMPLETE": incomplete, "GOOD": _DEV1})
        monkeypatch.setenv("DB_CONNECTIONS", payload)

        with caplog.at_level(logging.WARNING, logger="src.config.db_connections"):
            result = get_named_connections()

        assert "INCOMPLETE" not in result
        assert "GOOD" in result
        assert any("INCOMPLETE" in msg for msg in caplog.messages)

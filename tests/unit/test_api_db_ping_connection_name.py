"""Unit tests for POST /api/v1/system/db-ping — connection_name support + conn leak fix."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, call

_api_keys = os.getenv("API_KEYS", "")
if "test-key" not in {k.split(":", 1)[0].strip() for k in _api_keys.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys},test-key:admin" if _api_keys else "test-key:admin"

import pytest
from fastapi.testclient import TestClient

AUTH = {"X-API-Key": "test-key"}

_NAMED_CONN = {
    "STAGING": MagicMock(
        host="stg:1521/DB",
        user="stg_user",
        password="stg_pass",
        schema="STG_SCH",
        adapter="oracle",
    )
}


def _make_client() -> TestClient:
    from src.api.main import app
    return TestClient(app)


class TestDbPingConnectionName:
    """Tests for connection_name resolution on POST /api/v1/system/db-ping."""

    def test_connection_name_found_resolves_credentials(self) -> None:
        """When connection_name matches a named connection, credentials are resolved
        and the ping proceeds with those credentials."""
        mock_conn = MagicMock()
        with (
            patch(
                "src.api.routers.system.get_named_connections",
                return_value=_NAMED_CONN,
            ) as mock_get,
            patch(
                "src.api.routers.system.OracleConnection",
                return_value=mock_conn,
            ) as mock_cls,
        ):
            resp = _make_client().post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={"connection_name": "STAGING"},
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mock_get.assert_called_once()
        mock_cls.assert_called_once_with(
            username="stg_user",
            password="stg_pass",
            dsn="stg:1521/DB",
        )
        mock_conn.connect.assert_called_once()

    def test_connection_name_not_found_returns_404(self) -> None:
        """When connection_name does not match any named connection, 404 is returned
        with the exact required message."""
        with patch(
            "src.api.routers.system.get_named_connections",
            return_value={},
        ):
            resp = _make_client().post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={"connection_name": "MISSING"},
            )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Named connection 'MISSING' not found"

    def test_no_connection_name_does_not_call_get_named_connections(self) -> None:
        """When connection_name is not provided, get_named_connections is never called
        and the endpoint uses the explicit form fields."""
        mock_conn = MagicMock()
        with (
            patch(
                "src.api.routers.system.get_named_connections",
            ) as mock_get,
            patch(
                "src.api.routers.system.OracleConnection",
                return_value=mock_conn,
            ),
        ):
            resp = _make_client().post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={
                    "db_host": "localhost:1521/FREE",
                    "db_user": "usr",
                    "db_password": "pw",
                },
            )

        assert resp.status_code == 200
        mock_get.assert_not_called()

    def test_connection_leak_fixed_disconnect_called_on_success(self) -> None:
        """disconnect() must be called after a successful connect() (leak fix)."""
        mock_conn = MagicMock()
        with patch(
            "src.api.routers.system.OracleConnection",
            return_value=mock_conn,
        ):
            resp = _make_client().post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={
                    "db_host": "localhost:1521/FREE",
                    "db_user": "usr",
                    "db_password": "pw",
                },
            )

        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mock_conn.connect.assert_called_once()
        mock_conn.disconnect.assert_called_once()

    def test_connection_leak_fixed_disconnect_called_on_connect_failure(self) -> None:
        """disconnect() must be called even when connect() raises (try/finally)."""
        mock_conn = MagicMock()
        mock_conn.connect.side_effect = RuntimeError("cannot reach host")
        with patch(
            "src.api.routers.system.OracleConnection",
            return_value=mock_conn,
        ):
            resp = _make_client().post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={
                    "db_host": "bad:1521/FREE",
                    "db_user": "usr",
                    "db_password": "pw",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        assert "cannot reach host" in resp.json()["error"]
        mock_conn.disconnect.assert_called_once()

    def test_non_oracle_adapter_returns_error_without_connecting(self) -> None:
        """Non-oracle adapters return ok=False immediately without instantiating OracleConnection."""
        with patch(
            "src.api.routers.system.OracleConnection",
        ) as mock_cls:
            resp = _make_client().post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={
                    "db_host": "localhost",
                    "db_user": "usr",
                    "db_password": "pw",
                    "db_adapter": "postgresql",
                },
            )

        assert resp.status_code == 200
        assert resp.json()["ok"] is False
        mock_cls.assert_not_called()

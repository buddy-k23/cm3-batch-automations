"""Unit tests for extended POST /api/v1/files/db-compare endpoint (UI connection override)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

_api_keys = os.getenv("API_KEYS", "")
if "test-key" not in {k.split(":", 1)[0].strip() for k in _api_keys.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys},test-key:admin" if _api_keys else "test-key:admin"

import pytest
from fastapi.testclient import TestClient

AUTH = {"X-API-Key": "test-key"}

_MOCK_RESULT = {
    "workflow": {
        "status": "passed",
        "db_rows_extracted": 1,
        "query_or_table": "SELECT 1 FROM DUAL",
    },
    "compare": {
        "structure_compatible": True,
        "total_rows_file1": 1,
        "total_rows_file2": 1,
        "matching_rows": 1,
        "only_in_file1": 0,
        "only_in_file2": 0,
        "differences": 0,
    },
}


def _make_app():
    from src.api.main import app
    return app


class TestDbCompareExtendedParams:
    """Tests for connection override + apply_transforms params."""

    def test_invalid_db_adapter_returns_400(self, tmp_path: Path) -> None:
        """db_adapter must be oracle|postgresql|sqlite; anything else returns 400."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT),
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "db_adapter": "mysql",  # invalid
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )
        assert resp.status_code == 400
        assert "Invalid db_adapter" in resp.json()["detail"]

    def test_valid_connection_override_forwarded_to_service(self, tmp_path: Path) -> None:
        """Connection override fields must be forwarded to compare_db_to_file()."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT) as mock_svc,
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "db_host": "myhost:1521/FREE",
                    "db_user": "myuser",
                    "db_password": "secret",
                    "db_schema": "SCH",
                    "db_adapter": "oracle",
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )
        assert resp.status_code == 200
        call_kwargs = mock_svc.call_args.kwargs
        override = call_kwargs.get("connection_override")
        assert override is not None
        assert override["db_host"] == "myhost:1521/FREE"
        assert override["db_user"] == "myuser"
        assert override["db_password"] == "secret"
        assert override["db_schema"] == "SCH"
        assert override["db_adapter"] == "oracle"

    def test_apply_transforms_forwarded_to_service(self, tmp_path: Path) -> None:
        """apply_transforms=True must be forwarded to service."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT) as mock_svc,
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "apply_transforms": "true",
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )
        assert resp.status_code == 200
        assert mock_svc.call_args.kwargs.get("apply_transforms") is True

    def test_no_override_fields_still_works(self, tmp_path: Path) -> None:
        """Existing callers without override fields still get 200."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT) as mock_svc,
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={"query_or_table": "SELECT 1 FROM DUAL", "mapping_id": "m"},
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )
        assert resp.status_code == 200
        assert mock_svc.call_args.kwargs.get("connection_override") is None

    def test_adapter_only_override_is_forwarded(self, tmp_path: Path) -> None:
        """db_adapter alone (without host/user/password) must still be forwarded."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT) as mock_svc,
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "db_adapter": "sqlite",
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )
        assert resp.status_code == 200
        override = mock_svc.call_args.kwargs.get("connection_override")
        assert override is not None
        assert override["db_adapter"] == "sqlite"


class TestDbPingEndpoint:
    """Tests for POST /api/v1/system/db-ping."""

    def test_endpoint_requires_api_key(self) -> None:
        """Endpoint returns 401 without API key."""
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v1/system/db-ping",
            data={"db_host": "h", "db_user": "u", "db_password": "p"},
        )
        assert resp.status_code == 401

    def test_returns_ok_false_on_bad_credentials(self) -> None:
        """Bad credentials returns ok=false with error message."""
        client = TestClient(_make_app())
        with patch("src.api.routers.system.OracleConnection") as mock_conn:
            mock_conn.return_value.connect.side_effect = Exception("invalid credentials")
            resp = client.post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={"db_host": "bad:1521/FREE", "db_user": "u", "db_password": "bad"},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "error" in body

    def test_returns_ok_true_on_success(self) -> None:
        """Successful connection returns ok=true."""
        client = TestClient(_make_app())
        with patch("src.api.routers.system.OracleConnection") as mock_conn:
            mock_conn.return_value.connect.return_value = MagicMock()
            resp = client.post(
                "/api/v1/system/db-ping",
                headers=AUTH,
                data={"db_host": "h:1521/F", "db_user": "u", "db_password": "p"},
            )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_non_oracle_adapter_returns_not_implemented(self) -> None:
        """Non-oracle adapter returns ok=false with descriptive message."""
        client = TestClient(_make_app())
        resp = client.post(
            "/api/v1/system/db-ping",
            headers=AUTH,
            data={"db_host": "h", "db_user": "u", "db_password": "p", "db_adapter": "postgresql"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert "oracle" in body["error"].lower()

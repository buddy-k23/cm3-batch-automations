"""Unit tests for connection_name param on POST /api/v1/files/db-compare (#294)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

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

_NAMED_CONNECTIONS = {
    "STAGING": type(
        "NamedDbConnection",
        (),
        {
            "host": "stg:1522/DB",
            "user": "Valdo",
            "password": "secret",
            "schema": "APP_INT",
            "adapter": "oracle",
        },
    )()
}


def _make_app():
    from src.api.main import app

    return app


class TestDbCompareConnectionName:
    """Tests for connection_name form param on POST /api/v1/files/db-compare."""

    def test_connection_name_found_overrides_credentials(self, tmp_path: Path) -> None:
        """When connection_name is found, its credentials override individual fields."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch(
                "src.api.routers.files.compare_db_to_file",
                return_value=_MOCK_RESULT,
            ) as mock_svc,
            patch(
                "src.api.routers.files.get_named_connections",
                return_value=_NAMED_CONNECTIONS,
            ),
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "connection_name": "STAGING",
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )

        assert resp.status_code == 200
        call_kwargs = mock_svc.call_args.kwargs
        override = call_kwargs.get("connection_override")
        assert override is not None
        assert override["db_host"] == "stg:1522/DB"
        assert override["db_user"] == "Valdo"
        assert override["db_password"] == "secret"
        assert override["db_schema"] == "APP_INT"
        assert override["db_adapter"] == "oracle"

    def test_connection_name_not_found_returns_404(self, tmp_path: Path) -> None:
        """When connection_name is not in get_named_connections(), returns 404."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch("src.api.routers.files.compare_db_to_file", return_value=_MOCK_RESULT),
            patch(
                "src.api.routers.files.get_named_connections",
                return_value={},  # empty — name not found
            ),
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "connection_name": "UNKNOWN",
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )

        assert resp.status_code == 404
        assert resp.json()["detail"] == "Named connection 'UNKNOWN' not found"

    def test_no_connection_name_uses_existing_behavior(self, tmp_path: Path) -> None:
        """When connection_name is absent, existing form-field behavior is unchanged."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch(
                "src.api.routers.files.compare_db_to_file",
                return_value=_MOCK_RESULT,
            ) as mock_svc,
            patch(
                "src.api.routers.files.get_named_connections",
            ) as mock_named,
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={"query_or_table": "SELECT 1 FROM DUAL", "mapping_id": "m"},
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )

        assert resp.status_code == 200
        # No connection_name means get_named_connections is never called;
        # connection_override should still be None (no individual fields provided either)
        mock_named.assert_not_called()
        call_kwargs = mock_svc.call_args.kwargs
        assert call_kwargs.get("connection_override") is None

    def test_connection_name_overrides_any_individual_fields(self, tmp_path: Path) -> None:
        """When both connection_name and individual fields are provided, named connection wins."""
        client = TestClient(_make_app())
        mapping_file = tmp_path / "m.json"
        mapping_file.write_text(json.dumps({"fields": [{"name": "A"}]}))

        with (
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
            patch(
                "src.api.routers.files.compare_db_to_file",
                return_value=_MOCK_RESULT,
            ) as mock_svc,
            patch(
                "src.api.routers.files.get_named_connections",
                return_value=_NAMED_CONNECTIONS,
            ),
        ):
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "connection_name": "STAGING",
                    "db_host": "should-be-ignored:1521/OTHER",
                    "db_user": "ignored_user",
                },
                files={"actual_file": ("f.txt", b"A\n1\n")},
            )

        assert resp.status_code == 200
        override = mock_svc.call_args.kwargs.get("connection_override")
        assert override is not None
        # Named connection values win
        assert override["db_host"] == "stg:1522/DB"
        assert override["db_user"] == "Valdo"

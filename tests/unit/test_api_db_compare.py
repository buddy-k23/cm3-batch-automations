"""Unit tests for POST /api/v1/files/db-compare endpoint — written BEFORE implementation (TDD)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

os.environ.setdefault("API_KEYS", "test-key:admin")

import pytest
from fastapi.testclient import TestClient

AUTH = {"X-API-Key": "test-key"}


def _make_app():
    from src.api.main import app
    return app


class TestDbCompareEndpoint:
    """Tests for POST /api/v1/files/db-compare."""

    def test_endpoint_exists(self) -> None:
        """The db-compare endpoint must be registered in the router."""
        client = TestClient(_make_app())
        # A missing route returns 405 or 404; a wrong payload returns 422
        # We confirm it's 422 (found but bad request) not 404
        resp = client.post("/api/v1/files/db-compare", headers=AUTH)
        assert resp.status_code != 404

    def test_returns_compare_result_on_success(self, tmp_path: Path) -> None:
        """Valid request must return comparison result dict."""
        from src.api.main import app

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}, {"name": "NAME"}]}
        mapping_file = tmp_path / "test_mapping.json"
        mapping_file.write_text(json.dumps(mapping_cfg))

        actual_content = b"ID|NAME\n1|Alice\n"

        mock_result = {
            "workflow": {
                "status": "passed",
                "db_rows_extracted": 1,
                "query_or_table": "SELECT * FROM FOO",
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

        with (
            patch(
                "src.api.routers.files.compare_db_to_file",
                return_value=mock_result,
            ),
            patch(
                "src.api.routers.files.MAPPINGS_DIR",
                tmp_path,
            ),
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT * FROM FOO",
                    "mapping_id": "test_mapping",
                    "key_columns": "ID",
                    "output_format": "json",
                },
                files={"actual_file": ("actual.txt", actual_content, "text/plain")},
            )

        assert resp.status_code == 200
        body = resp.json()
        # API returns a flat DbCompareResult (Pydantic model), not the raw nested dict
        assert "workflow_status" in body
        assert "db_rows_extracted" in body
        assert body["workflow_status"] == "passed"

    def test_mapping_not_found_returns_404(self, tmp_path: Path) -> None:
        """A missing mapping must return HTTP 404."""
        from src.api.main import app

        actual_content = b"ID|NAME\n1|Alice\n"

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT * FROM FOO",
                    "mapping_id": "does_not_exist",
                    "key_columns": "ID",
                    "output_format": "json",
                },
                files={"actual_file": ("actual.txt", actual_content, "text/plain")},
            )

        assert resp.status_code == 404

    def test_service_error_returns_500(self, tmp_path: Path) -> None:
        """DB extraction failures must result in HTTP 500."""
        from src.api.main import app

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}]}
        mapping_file = tmp_path / "test_mapping.json"
        mapping_file.write_text(json.dumps(mapping_cfg))

        actual_content = b"ID\n1\n"

        with (
            patch(
                "src.api.routers.files.compare_db_to_file",
                side_effect=RuntimeError("ORA-01017: invalid credentials"),
            ),
            patch("src.api.routers.files.MAPPINGS_DIR", tmp_path),
        ):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/files/db-compare",
                headers=AUTH,
                data={
                    "query_or_table": "SELECT * FROM FOO",
                    "mapping_id": "test_mapping",
                    "key_columns": "ID",
                    "output_format": "json",
                },
                files={"actual_file": ("actual.txt", actual_content, "text/plain")},
            )

        assert resp.status_code == 500


class TestDbCompareResultModel:
    """Tests for DbCompareResult Pydantic model."""

    def test_model_instantiation(self) -> None:
        """DbCompareResult must be importable and instantiable."""
        from src.api.models.file import DbCompareResult

        result = DbCompareResult(
            workflow_status="passed",
            db_rows_extracted=5,
            query_or_table="SELECT * FROM FOO",
            total_rows_file1=5,
            total_rows_file2=5,
            matching_rows=5,
            only_in_file1=0,
            only_in_file2=0,
            differences=0,
        )
        assert result.workflow_status == "passed"
        assert result.db_rows_extracted == 5

    def test_model_optional_fields(self) -> None:
        """Optional fields must default to None."""
        from src.api.models.file import DbCompareResult

        result = DbCompareResult(
            workflow_status="passed",
            db_rows_extracted=0,
            query_or_table="T",
            total_rows_file1=0,
            total_rows_file2=0,
            matching_rows=0,
            only_in_file1=0,
            only_in_file2=0,
            differences=0,
        )
        assert result.report_url is None
        assert result.structure_errors is None


class TestDbCompareWithProfile:
    def test_accepts_profile_name_field(self, tmp_path: Path) -> None:
        """profile_name form field must be accepted without 422."""
        import json as _json
        from unittest.mock import patch
        from src.api.main import app

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}]}
        (tmp_path / "m.json").write_text(_json.dumps(mapping_cfg))

        mock_result = {
            "workflow": {"status": "passed", "db_rows_extracted": 0, "query_or_table": "T"},
            "compare": {
                "structure_compatible": True, "total_rows_file1": 0, "total_rows_file2": 0,
                "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0, "differences": 0,
            },
        }
        from src.config.db_config import DbConfig
        profile_cfg = DbConfig(user="U", password="P", dsn="H:1/S", schema="SCH", db_adapter="oracle")

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.compare_db_to_file", return_value=mock_result), \
             patch("src.api.routers.files.resolve_profile", return_value=profile_cfg):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/files/db-compare",
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "profile_name": "Local Dev",
                },
                files={"actual_file": ("f.txt", b"ID\n1\n", "text/plain")},
                headers=AUTH,
            )
        assert resp.status_code == 200

    def test_profile_credentials_used_in_connection_override(self, tmp_path: Path) -> None:
        """When profile_name set, connection_override must use profile credentials."""
        import json as _json
        from unittest.mock import patch
        from src.api.main import app

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}]}
        (tmp_path / "m.json").write_text(_json.dumps(mapping_cfg))

        mock_result = {
            "workflow": {"status": "passed", "db_rows_extracted": 0, "query_or_table": "T"},
            "compare": {
                "structure_compatible": True, "total_rows_file1": 0, "total_rows_file2": 0,
                "matching_rows": 0, "only_in_file1": 0, "only_in_file2": 0, "differences": 0,
            },
        }
        from src.config.db_config import DbConfig
        profile_cfg = DbConfig(
            user="PROFUSER", password="PROFPW", dsn="profhost:1521/SVC",
            schema="PROFSCH", db_adapter="oracle"
        )

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.compare_db_to_file", return_value=mock_result) as mock_cmp, \
             patch("src.api.routers.files.resolve_profile", return_value=profile_cfg):
            client = TestClient(app)
            client.post(
                "/api/v1/files/db-compare",
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "profile_name": "Local Dev",
                },
                files={"actual_file": ("f.txt", b"ID\n1\n", "text/plain")},
                headers=AUTH,
            )

        call_kwargs = mock_cmp.call_args.kwargs
        override = call_kwargs.get("connection_override")
        assert override is not None
        assert override["db_user"] == "PROFUSER"
        assert override["db_password"] == "PROFPW"
        assert override["db_host"] == "profhost:1521/SVC"
        assert override["db_adapter"] == "oracle"

    def test_profile_not_found_returns_500(self, tmp_path: Path) -> None:
        import json as _json
        from unittest.mock import patch
        from src.api.main import app

        mapping_cfg = {"name": "test", "fields": [{"name": "ID"}]}
        (tmp_path / "m.json").write_text(_json.dumps(mapping_cfg))

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.resolve_profile",
                   side_effect=KeyError("Profile not found: 'Bad'")):
            client = TestClient(app)
            resp = client.post(
                "/api/v1/files/db-compare",
                data={
                    "query_or_table": "SELECT 1 FROM DUAL",
                    "mapping_id": "m",
                    "profile_name": "Bad",
                },
                files={"actual_file": ("f.txt", b"ID\n1\n", "text/plain")},
                headers=AUTH,
            )
        assert resp.status_code == 500
        assert "Profile not found" in resp.json()["detail"]

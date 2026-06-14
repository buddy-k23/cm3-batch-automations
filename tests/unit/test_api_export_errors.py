"""Unit tests for POST /api/v1/files/export-errors — Issue #228."""

from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# App fixture — import after patching heavy optional dependencies
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Return a TestClient with a known dev API key configured.

    The export-errors endpoint requires a valid X-API-Key (auth dependency).
    Tests below ship the key as a default header so individual cases stay
    focused on the endpoint behaviour rather than auth wiring.
    """
    with patch.dict(os.environ, {"API_KEYS": "dev-key:admin"}):
        from src.api.main import app
        tc = TestClient(app, raise_server_exceptions=False)
        tc.headers.update({"X-API-Key": "dev-key"})
        yield tc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pipe_file_bytes(rows: int = 5) -> bytes:
    """Build a simple pipe-delimited file with a header and *rows* data rows."""
    lines = ["name|value"]
    for i in range(1, rows + 1):
        lines.append(f"row{i}|{i}")
    return "\n".join(lines).encode("utf-8")


def _validation_result_with_errors(row_numbers: list[int]) -> dict:
    return {
        "valid": False,
        "total_rows": 10,
        "valid_rows": 10 - len(set(row_numbers)),
        "invalid_rows": len(set(row_numbers)),
        "errors": [{"row": r, "message": f"Error row {r}"} for r in row_numbers],
        "warnings": [],
        "quality_score": None,
        "report_url": None,
    }


def _validation_result_clean() -> dict:
    return {
        "valid": True,
        "total_rows": 5,
        "valid_rows": 5,
        "invalid_rows": 0,
        "errors": [],
        "warnings": [],
        "quality_score": None,
        "report_url": None,
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestExportErrorsEndpoint:
    """Tests for POST /api/v1/files/export-errors."""

    def test_200_with_file_download_when_errors_exist(self, client, tmp_path):
        """Endpoint returns 200 with a downloadable file when validation errors exist."""
        mapping_id = "test_mapping_for_export"
        mapping_file = Path("config/mappings") / f"{mapping_id}.json"
        mapping_file.parent.mkdir(parents=True, exist_ok=True)
        mapping_file.write_text(json.dumps({
            "source": {"format": "pipe_delimited", "has_header": True},
            "fields": [
                {"name": "name", "type": "string"},
                {"name": "value", "type": "string"},
            ],
        }), encoding="utf-8")

        try:
            with patch(
                "src.api.routers.files.run_validate_service",
                return_value=_validation_result_with_errors([2, 4]),
            ):
                resp = client.post(
                    "/api/v1/files/export-errors",
                    data={"mapping_id": mapping_id},
                    files={"file": ("test.pipe", _pipe_file_bytes(), "text/plain")},
                )

            assert resp.status_code == 200
            # Should be a file download (text/plain or application/octet-stream)
            assert resp.content  # non-empty body
        finally:
            if mapping_file.exists():
                mapping_file.unlink()

    def test_200_empty_file_when_no_errors(self, client, tmp_path):
        """Endpoint returns 200 with empty-ish body when no validation errors."""
        mapping_id = "test_mapping_for_export_clean"
        mapping_file = Path("config/mappings") / f"{mapping_id}.json"
        mapping_file.parent.mkdir(parents=True, exist_ok=True)
        mapping_file.write_text(json.dumps({
            "source": {"format": "pipe_delimited", "has_header": True},
            "fields": [
                {"name": "name", "type": "string"},
                {"name": "value", "type": "string"},
            ],
        }), encoding="utf-8")

        try:
            with patch(
                "src.api.routers.files.run_validate_service",
                return_value=_validation_result_clean(),
            ):
                resp = client.post(
                    "/api/v1/files/export-errors",
                    data={"mapping_id": mapping_id},
                    files={"file": ("test.pipe", _pipe_file_bytes(), "text/plain")},
                )

            assert resp.status_code == 200
        finally:
            if mapping_file.exists():
                mapping_file.unlink()

    def test_404_unknown_mapping_id(self, client):
        """Returns 404 when mapping_id refers to a non-existent mapping."""
        resp = client.post(
            "/api/v1/files/export-errors",
            data={"mapping_id": "nonexistent_mapping_zzz"},
            files={"file": ("test.pipe", _pipe_file_bytes(), "text/plain")},
        )
        assert resp.status_code == 404

    def test_422_when_neither_mapping_nor_yaml(self, client):
        """Returns 422 when both mapping_id and multi_record_config are absent."""
        resp = client.post(
            "/api/v1/files/export-errors",
            files={"file": ("test.pipe", _pipe_file_bytes(), "text/plain")},
        )
        assert resp.status_code == 422

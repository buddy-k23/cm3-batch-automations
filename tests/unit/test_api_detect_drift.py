"""Unit tests for ``POST /api/v1/files/detect-drift`` endpoint.

Tests cover:
- 200 with drifted=False for clean file
- 200 with drifted=True and fields list for drifted file (mocked service)
- 404 for unknown mapping_id
- 422 for missing file field
"""

from __future__ import annotations

import json
import os
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

# Set up a dev API key before importing app.
_api_keys = os.getenv("API_KEYS", "")
if "dev-key" not in {k.split(":", 1)[0].strip() for k in _api_keys.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys},dev-key:admin" if _api_keys else "dev-key:admin"

from src.api.main import app

client = TestClient(app)
API_HEADERS = {"X-API-Key": "dev-key"}

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------

CLEAN_DRIFT_RESULT = {"drifted": False, "fields": []}

DRIFTED_RESULT = {
    "drifted": True,
    "fields": [
        {
            "name": "account_id",
            "expected_start": 1,
            "expected_length": 10,
            "actual_start": 7,
            "actual_length": 10,
            "severity": "error",
        }
    ],
}

MAPPING_STUB = json.dumps({
    "format": "fixed",
    "fields": [
        {"name": "account_id", "position": 1, "length": 10},
    ],
})


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDetectDriftEndpointClean:
    """200 + drifted=False for a clean file."""

    def test_returns_200(self, tmp_path):
        mapping_file = tmp_path / "my_mapping.json"
        mapping_file.write_text(MAPPING_STUB)

        file_content = b"ACCT000001DATA\n" * 5

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.detect_drift", return_value=CLEAN_DRIFT_RESULT):
            resp = client.post(
                "/api/v1/files/detect-drift",
                data={"mapping_id": "my_mapping"},
                files={"file": ("data.txt", BytesIO(file_content), "text/plain")},
                headers=API_HEADERS,
            )

        assert resp.status_code == 200

    def test_drifted_false_in_response(self, tmp_path):
        mapping_file = tmp_path / "my_mapping.json"
        mapping_file.write_text(MAPPING_STUB)

        file_content = b"ACCT000001DATA\n" * 5

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.detect_drift", return_value=CLEAN_DRIFT_RESULT):
            resp = client.post(
                "/api/v1/files/detect-drift",
                data={"mapping_id": "my_mapping"},
                files={"file": ("data.txt", BytesIO(file_content), "text/plain")},
                headers=API_HEADERS,
            )

        body = resp.json()
        assert body["drifted"] is False
        assert body["fields"] == []


class TestDetectDriftEndpointDrifted:
    """200 + drifted=True with fields list when service detects drift."""

    def test_returns_200(self, tmp_path):
        mapping_file = tmp_path / "my_mapping.json"
        mapping_file.write_text(MAPPING_STUB)

        file_content = b"      ACCT000001\n" * 5

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.detect_drift", return_value=DRIFTED_RESULT):
            resp = client.post(
                "/api/v1/files/detect-drift",
                data={"mapping_id": "my_mapping"},
                files={"file": ("data.txt", BytesIO(file_content), "text/plain")},
                headers=API_HEADERS,
            )

        assert resp.status_code == 200

    def test_drifted_true_in_response(self, tmp_path):
        mapping_file = tmp_path / "my_mapping.json"
        mapping_file.write_text(MAPPING_STUB)

        file_content = b"      ACCT000001\n" * 5

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.detect_drift", return_value=DRIFTED_RESULT):
            resp = client.post(
                "/api/v1/files/detect-drift",
                data={"mapping_id": "my_mapping"},
                files={"file": ("data.txt", BytesIO(file_content), "text/plain")},
                headers=API_HEADERS,
            )

        body = resp.json()
        assert body["drifted"] is True
        assert len(body["fields"]) == 1
        assert body["fields"][0]["name"] == "account_id"
        assert body["fields"][0]["severity"] == "error"

    def test_fields_list_structure(self, tmp_path):
        mapping_file = tmp_path / "my_mapping.json"
        mapping_file.write_text(MAPPING_STUB)

        file_content = b"      ACCT000001\n" * 5

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path), \
             patch("src.api.routers.files.detect_drift", return_value=DRIFTED_RESULT):
            resp = client.post(
                "/api/v1/files/detect-drift",
                data={"mapping_id": "my_mapping"},
                files={"file": ("data.txt", BytesIO(file_content), "text/plain")},
                headers=API_HEADERS,
            )

        body = resp.json()
        field = body["fields"][0]
        assert "name" in field
        assert "severity" in field
        assert "expected_start" in field
        assert "actual_start" in field


class TestDetectDriftEndpointNotFound:
    """404 when mapping_id does not resolve to a file."""

    def test_returns_404_for_unknown_mapping(self, tmp_path):
        file_content = b"ACCT000001DATA\n" * 5

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path):
            resp = client.post(
                "/api/v1/files/detect-drift",
                data={"mapping_id": "does_not_exist"},
                files={"file": ("data.txt", BytesIO(file_content), "text/plain")},
                headers=API_HEADERS,
            )

        assert resp.status_code == 404

    def test_404_detail_mentions_mapping(self, tmp_path):
        file_content = b"ACCT000001DATA\n" * 5

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path):
            resp = client.post(
                "/api/v1/files/detect-drift",
                data={"mapping_id": "does_not_exist"},
                files={"file": ("data.txt", BytesIO(file_content), "text/plain")},
                headers=API_HEADERS,
            )

        assert "does_not_exist" in resp.json()["detail"] or "not found" in resp.json()["detail"].lower()


class TestDetectDriftEndpointMissingFile:
    """422 when file field is missing from request."""

    def test_returns_422_without_file(self, tmp_path):
        mapping_file = tmp_path / "my_mapping.json"
        mapping_file.write_text(MAPPING_STUB)

        with patch("src.api.routers.files.MAPPINGS_DIR", tmp_path):
            resp = client.post(
                "/api/v1/files/detect-drift",
                data={"mapping_id": "my_mapping"},
                headers=API_HEADERS,
            )

        assert resp.status_code == 422

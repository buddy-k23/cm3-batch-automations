"""Negative tests for API error handling (#109)."""

import os

os.environ.setdefault("API_KEYS", "test-key:admin")

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


client = TestClient(app)
AUTH = {"X-API-Key": "test-key"}


class TestNegativeApi:
    """Negative tests for API endpoint error handling."""

    def test_api_validate_no_file_uploaded(self):
        """POST /api/v1/files/validate with no file should return 422."""
        response = client.post(
            "/api/v1/files/validate",
            data={"mapping_id": "some_mapping"},
            headers=AUTH,
        )
        assert response.status_code == 422

    def test_api_compare_only_one_file(self):
        """POST /api/v1/files/compare with only one file should return 422."""
        content = b"id|name\n1|Alice\n"
        response = client.post(
            "/api/v1/files/compare",
            files={"file1": ("f1.txt", content, "text/plain")},
            data={"mapping_id": "some_mapping"},
            headers=AUTH,
        )
        assert response.status_code == 422

    def test_api_compare_async_invalid_job_id(self):
        """GET /api/v1/files/compare-jobs/{bad_id} should return 404."""
        response = client.get(
            "/api/v1/files/compare-jobs/nonexistent-job-id-12345",
            headers=AUTH,
        )
        assert response.status_code == 404

    def test_api_upload_mapping_wrong_extension(self):
        """POST /api/v1/mappings/upload with a .txt file should return 400."""
        content = b"some random text content"
        response = client.post(
            "/api/v1/mappings/upload",
            files={"file": ("bad_mapping.txt", content, "text/plain")},
            headers=AUTH,
        )
        assert response.status_code == 400

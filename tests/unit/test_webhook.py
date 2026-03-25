"""Tests for the webhook validation endpoint."""

import asyncio
import uuid
from unittest.mock import patch, MagicMock, AsyncMock

import pytest
from fastapi.testclient import TestClient

from src.api.main import app


client = TestClient(app)


class TestWebhookValidateEndpoint:
    """Tests for POST /api/v1/webhook/validate."""

    def test_submit_returns_202_with_job_id(self):
        """Submitting a webhook validation request returns 202 with a job_id."""
        payload = {
            "file_path": "/tmp/test.dat",
            "mapping_id": "config/mappings/test.json",
        }
        with patch(
            "src.api.routers.webhook.Path.exists", return_value=True
        ), patch(
            "src.api.routers.webhook.Path.is_file", return_value=True
        ):
            resp = client.post("/api/v1/webhook/validate", json=payload)

        assert resp.status_code == 202
        body = resp.json()
        assert "job_id" in body
        # job_id must be a valid UUID
        uuid.UUID(body["job_id"])
        assert body["status"] == "queued"

    def test_submit_missing_file_path_returns_422(self):
        """Missing required field file_path returns 422."""
        resp = client.post("/api/v1/webhook/validate", json={"mapping_id": "x"})
        assert resp.status_code == 422

    def test_submit_nonexistent_file_returns_400(self):
        """Submitting a path to a file that does not exist returns 400."""
        payload = {
            "file_path": "/nonexistent/path/data.dat",
            "mapping_id": "config/mappings/test.json",
        }
        resp = client.post("/api/v1/webhook/validate", json=payload)
        assert resp.status_code == 400
        assert "not found" in resp.json()["detail"].lower()


class TestWebhookJobStatus:
    """Tests for GET /api/v1/webhook/jobs/{job_id}."""

    def test_unknown_job_returns_404(self):
        """Querying a non-existent job_id returns 404."""
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/webhook/jobs/{fake_id}")
        assert resp.status_code == 404

    def test_known_job_returns_status(self):
        """A submitted job can be polled for status."""
        payload = {
            "file_path": "/tmp/test.dat",
            "mapping_id": "config/mappings/test.json",
        }
        with patch(
            "src.api.routers.webhook.Path.exists", return_value=True
        ), patch(
            "src.api.routers.webhook.Path.is_file", return_value=True
        ):
            create_resp = client.post("/api/v1/webhook/validate", json=payload)

        job_id = create_resp.json()["job_id"]
        status_resp = client.get(f"/api/v1/webhook/jobs/{job_id}")
        assert status_resp.status_code == 200
        body = status_resp.json()
        assert body["job_id"] == job_id
        assert body["status"] in ("queued", "running", "completed", "failed")


class TestWebhookCallback:
    """Tests for the callback mechanism."""

    def test_callback_posts_result(self):
        """When callback_url is provided, the result is POSTed to it."""
        from src.api.routers.webhook import _run_validation_job, _jobs

        job_id = str(uuid.uuid4())
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "result": None,
            "metadata": None,
        }

        mock_result = {
            "total_rows": 100,
            "error_count": 0,
            "warning_count": 0,
            "valid": True,
        }

        with patch(
            "src.api.routers.webhook.run_validate_service", return_value=mock_result
        ), patch(
            "src.api.routers.webhook.httpx.AsyncClient"
        ) as MockClient:
            mock_client_instance = AsyncMock()
            mock_client_instance.post = AsyncMock(
                return_value=MagicMock(status_code=200)
            )
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=mock_client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            asyncio.run(_run_validation_job(
                job_id=job_id,
                file_path="/tmp/test.dat",
                mapping_id="config/mappings/test.json",
                rules_id=None,
                thresholds=None,
                callback_url="https://example.com/hook",
                metadata={"key": "value"},
            ))

        assert _jobs[job_id]["status"] == "completed"
        assert _jobs[job_id]["result"] == mock_result
        mock_client_instance.post.assert_awaited_once()

        # Clean up
        del _jobs[job_id]

    def test_validation_failure_sets_failed_status(self):
        """When validation raises an exception, the job status is 'failed'."""
        from src.api.routers.webhook import _run_validation_job, _jobs

        job_id = str(uuid.uuid4())
        _jobs[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "result": None,
            "metadata": None,
        }

        with patch(
            "src.api.routers.webhook.run_validate_service",
            side_effect=RuntimeError("boom"),
        ):
            asyncio.run(_run_validation_job(
                job_id=job_id,
                file_path="/tmp/test.dat",
                mapping_id="config/mappings/test.json",
                rules_id=None,
                thresholds=None,
                callback_url=None,
                metadata=None,
            ))

        assert _jobs[job_id]["status"] == "failed"
        assert "boom" in _jobs[job_id]["error"]

        # Clean up
        del _jobs[job_id]

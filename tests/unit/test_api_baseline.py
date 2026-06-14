"""Unit tests for GET /api/v1/runs/baseline-check and /api/v1/runs/baselines."""
from __future__ import annotations

import os

# Ensure a dev-key is configured for auth checks before the app is imported.
_api_keys_env = os.getenv("API_KEYS", "")
if "dev-key" not in {k.split(":", 1)[0].strip() for k in _api_keys_env.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys_env},dev-key:admin" if _api_keys_env else "dev-key:admin"

from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)

_AUTH = {"X-API-Key": "dev-key"}

_SAMPLE_HISTORY = [
    {
        "run_id": "abc123",
        "suite_name": "SUITE_A",
        "status": "passed",
        "passed": True,
        "total_rows": 100,
        "invalid_rows": 2,
        "quality_score": 95.0,
    },
    {
        "run_id": "def456",
        "suite_name": "SUITE_B",
        "status": "passed",
        "passed": True,
        "total_rows": 50,
        "invalid_rows": 0,
        "quality_score": 98.0,
    },
]

_SAMPLE_DEVIATION = {
    "deviated": False,
    "alerts": [],
}

_SAMPLE_BASELINES = [
    {
        "suite_name": "SUITE_A",
        "pass_rate": 90.0,
        "avg_quality_score": 93.0,
        "avg_error_rate": 1.5,
        "sample_size": 8,
        "updated_at": "2026-03-30T10:00:00",
    },
]


# ---------------------------------------------------------------------------
# GET /api/v1/runs/baseline-check
# ---------------------------------------------------------------------------


def test_baseline_check_returns_deviation_report():
    """Successful call returns 200 with the deviation report from check_deviation."""
    with (
        patch(
            "src.api.routers.runs.load_run_history",
            return_value=_SAMPLE_HISTORY,
        ),
        patch(
            "src.api.routers.runs.check_deviation",
            return_value=_SAMPLE_DEVIATION,
        ) as mock_check,
    ):
        resp = client.get(
            "/api/v1/runs/baseline-check",
            params={"suite": "SUITE_A", "run_id": "abc123"},
            headers=_AUTH,
        )

    assert resp.status_code == 200
    assert resp.json() == _SAMPLE_DEVIATION
    mock_check.assert_called_once()
    call_args = mock_check.call_args
    assert call_args[0][0] == "SUITE_A"


def test_baseline_check_unknown_run_id_returns_404():
    """Unknown run_id produces a 404 with a clear detail message."""
    with patch(
        "src.api.routers.runs.load_run_history",
        return_value=_SAMPLE_HISTORY,
    ):
        resp = client.get(
            "/api/v1/runs/baseline-check",
            params={"suite": "SUITE_A", "run_id": "NOPE"},
            headers=_AUTH,
        )

    assert resp.status_code == 404
    assert "NOPE" in resp.json()["detail"]


def test_baseline_check_unknown_suite_returns_404():
    """Unknown suite name produces a 404 with a clear detail message."""
    with patch(
        "src.api.routers.runs.load_run_history",
        return_value=_SAMPLE_HISTORY,
    ):
        resp = client.get(
            "/api/v1/runs/baseline-check",
            params={"suite": "NO_SUCH_SUITE", "run_id": "abc123"},
            headers=_AUTH,
        )

    assert resp.status_code == 404
    assert "NO_SUCH_SUITE" in resp.json()["detail"]


def test_baseline_check_no_auth_key_returns_401_or_403(monkeypatch):
    """Request without X-API-Key header is rejected (401 or 403)."""
    monkeypatch.setenv("API_KEYS", "dev-key:admin")
    resp = client.get(
        "/api/v1/runs/baseline-check",
        params={"suite": "SUITE_A", "run_id": "abc123"},
    )
    assert resp.status_code in (401, 403)


# ---------------------------------------------------------------------------
# GET /api/v1/runs/baselines
# ---------------------------------------------------------------------------


def test_list_baselines_returns_200_with_list():
    """Returns 200 and the list from list_baselines()."""
    with patch(
        "src.api.routers.runs.baseline_service.list_baselines",
        return_value=_SAMPLE_BASELINES,
    ):
        resp = client.get("/api/v1/runs/baselines", headers=_AUTH)

    assert resp.status_code == 200
    assert resp.json() == _SAMPLE_BASELINES


def test_list_baselines_returns_empty_list_when_none():
    """Returns 200 with an empty list when no baselines are stored."""
    with patch(
        "src.api.routers.runs.baseline_service.list_baselines",
        return_value=[],
    ):
        resp = client.get("/api/v1/runs/baselines", headers=_AUTH)

    assert resp.status_code == 200
    assert resp.json() == []


def test_list_baselines_no_auth_key_returns_401_or_403(monkeypatch):
    """Request without X-API-Key header is rejected (401 or 403)."""
    monkeypatch.setenv("API_KEYS", "dev-key:admin")
    resp = client.get("/api/v1/runs/baselines")
    assert resp.status_code in (401, 403)

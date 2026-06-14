"""Unit tests for GET /api/v1/runs/trend endpoint."""

from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

# Ensure a dev key is present before importing the app so auth is enabled.
_api_keys = os.getenv("API_KEYS", "")
if "dev-key" not in {k.split(":", 1)[0].strip() for k in _api_keys.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys},dev-key:admin" if _api_keys else "dev-key:admin"

from src.api.main import app  # noqa: E402

client = TestClient(app)
API_HEADERS = {"X-API-Key": "dev-key"}

_SAMPLE_TREND = [
    {
        "date": "2026-03-30",
        "total_runs": 5,
        "pass_runs": 4,
        "fail_runs": 1,
        "avg_quality_score": 88.5,
        "pass_rate": 80.0,
    }
]


def test_trend_returns_200_with_list():
    """GET /trend with valid params returns 200 and a list."""
    with patch("src.services.trend_service.get_trend", return_value=_SAMPLE_TREND) as mock_get:
        response = client.get("/api/v1/runs/trend", headers=API_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload, list)
    assert payload == _SAMPLE_TREND
    mock_get.assert_called_once_with(suite=None, days=30)


def test_trend_invalid_days_returns_422():
    """GET /trend?days=99 should return 422 (invalid days value)."""
    with patch(
        "src.services.trend_service.get_trend",
        side_effect=ValueError("days must be one of (7, 14, 30, 90), got 99"),
    ):
        response = client.get("/api/v1/runs/trend?days=99", headers=API_HEADERS)

    assert response.status_code == 422


def test_trend_nonexistent_suite_returns_200_empty_list():
    """GET /trend?suite=nonexistent returns 200 with empty list."""
    with patch("src.services.trend_service.get_trend", return_value=[]) as mock_get:
        response = client.get("/api/v1/runs/trend?suite=nonexistent", headers=API_HEADERS)

    assert response.status_code == 200
    assert response.json() == []
    mock_get.assert_called_once_with(suite="nonexistent", days=30)


def test_trend_no_auth_returns_401():
    """GET /trend without X-API-Key returns 401."""
    response = client.get("/api/v1/runs/trend")
    assert response.status_code == 401


def test_trend_invalid_auth_returns_403():
    """GET /trend with wrong X-API-Key returns 403."""
    response = client.get("/api/v1/runs/trend", headers={"X-API-Key": "bad-key"})
    assert response.status_code == 403


def test_trend_default_days_calls_get_trend_with_30():
    """GET /trend?days=30 calls get_trend(suite=None, days=30)."""
    with patch("src.services.trend_service.get_trend", return_value=[]) as mock_get:
        response = client.get("/api/v1/runs/trend?days=30", headers=API_HEADERS)

    assert response.status_code == 200
    mock_get.assert_called_once_with(suite=None, days=30)


def test_trend_suite_and_days_params_forwarded():
    """GET /trend?suite=ATOCTRAN&days=7 calls get_trend(suite='ATOCTRAN', days=7)."""
    with patch("src.services.trend_service.get_trend", return_value=_SAMPLE_TREND) as mock_get:
        response = client.get("/api/v1/runs/trend?suite=ATOCTRAN&days=7", headers=API_HEADERS)

    assert response.status_code == 200
    mock_get.assert_called_once_with(suite="ATOCTRAN", days=7)

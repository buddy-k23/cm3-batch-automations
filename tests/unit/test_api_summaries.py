"""Unit tests for GET /api/v1/runs/summaries endpoint.

Tests cover:
- 200 response with list of summary dicts (mocked service)
- No auth → 401 when API_KEYS is configured
- Invalid key → 403
- Empty result → 200 with []
"""
from __future__ import annotations

import os
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.api.main import app

# Ensure a test API key is always available for auth-required tests.
_api_keys = os.getenv("API_KEYS", "")
if "dev-key" not in {k.split(":", 1)[0].strip() for k in _api_keys.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys},dev-key:admin" if _api_keys else "dev-key:admin"

client = TestClient(app)
API_HEADERS = {"X-API-Key": "dev-key"}

_SAMPLE_SUMMARIES = [
    {
        "suite_name": "E2E Smoke",
        "last_run_status": "PASS",
        "last_run_at": "2026-03-30T12:00:00",
        "pass_rate_30d": 90.0,
        "avg_quality_score": 95.0,
        "trend_direction": "up",
    }
]


def test_summaries_returns_200_with_list():
    with patch(
        "src.api.routers.runs.summary_service.get_suite_summaries",
        return_value=_SAMPLE_SUMMARIES,
    ):
        r = client.get("/api/v1/runs/summaries", headers=API_HEADERS)

    assert r.status_code == 200
    payload = r.json()
    assert isinstance(payload, list)
    assert len(payload) == 1
    assert payload[0]["suite_name"] == "E2E Smoke"
    assert payload[0]["last_run_status"] == "PASS"
    assert payload[0]["trend_direction"] == "up"


def test_summaries_returns_empty_list_when_no_history():
    with patch(
        "src.api.routers.runs.summary_service.get_suite_summaries",
        return_value=[],
    ):
        r = client.get("/api/v1/runs/summaries", headers=API_HEADERS)

    assert r.status_code == 200
    assert r.json() == []


def test_summaries_requires_auth_missing_key():
    """When API_KEYS is set and no key is provided, expect 401."""
    with patch(
        "src.api.routers.runs.summary_service.get_suite_summaries",
        return_value=_SAMPLE_SUMMARIES,
    ):
        r = client.get("/api/v1/runs/summaries")  # no headers

    # With API_KEYS configured and no referer from /ui, expect 401
    assert r.status_code == 401


def test_summaries_requires_auth_invalid_key():
    """Invalid key should return 403."""
    with patch(
        "src.api.routers.runs.summary_service.get_suite_summaries",
        return_value=_SAMPLE_SUMMARIES,
    ):
        r = client.get(
            "/api/v1/runs/summaries",
            headers={"X-API-Key": "totally-wrong-key"},
        )

    assert r.status_code == 403


def test_summaries_response_contains_expected_fields():
    summaries = [
        {
            "suite_name": "Regression",
            "last_run_status": "FAIL",
            "last_run_at": "2026-03-29T08:00:00",
            "pass_rate_30d": 75.0,
            "avg_quality_score": None,
            "trend_direction": "down",
        }
    ]
    with patch(
        "src.api.routers.runs.summary_service.get_suite_summaries",
        return_value=summaries,
    ):
        r = client.get("/api/v1/runs/summaries", headers=API_HEADERS)

    assert r.status_code == 200
    item = r.json()[0]
    required_keys = {
        "suite_name",
        "last_run_status",
        "last_run_at",
        "pass_rate_30d",
        "avg_quality_score",
        "trend_direction",
    }
    assert required_keys.issubset(set(item.keys()))

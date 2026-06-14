"""Unit tests for the /api/v1/runs/history endpoint in ui.py."""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app, raise_server_exceptions=False)


class TestGetRunHistoryDbPath:
    """Unit tests for the DB-first read path in get_run_history."""

    def test_uses_db_when_oracle_user_set(self, monkeypatch):
        """Returns DB data when ORACLE_USER is set and fetch_history_from_db succeeds."""
        monkeypatch.setenv("ORACLE_USER", "APP_INT")
        mock_data = [
            {
                "run_id": "unit-001", "suite_name": "Unit Suite",
                "environment": "dev", "timestamp": "2026-03-02T10:00:00.000000Z",
                "status": "PASS", "pass_count": 1, "fail_count": 0,
                "skip_count": 0, "total_count": 1,
                "report_url": "/reports/x.html", "archive_path": "",
            }
        ]
        with patch("src.api.routers.ui.fetch_history_from_db", return_value=mock_data):
            response = client.get("/api/v1/runs/history")

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["run_id"] == "unit-001"

    def test_falls_back_to_json_when_db_raises(self, monkeypatch, tmp_path):
        """Falls back to JSON when fetch_history_from_db raises."""
        monkeypatch.setenv("ORACLE_USER", "APP_INT")
        monkeypatch.chdir(tmp_path)
        (tmp_path / "reports").mkdir()
        (tmp_path / "reports" / "run_history.json").write_text(
            json.dumps([{"run_id": "fallback-001", "suite_name": "Fallback"}]),
            encoding="utf-8",
        )

        with patch(
            "src.api.routers.ui.fetch_history_from_db",
            side_effect=RuntimeError("ORA-12170"),
        ):
            response = client.get("/api/v1/runs/history")

        assert response.status_code == 200
        assert isinstance(response.json(), list)

    def test_skips_db_when_oracle_user_unset(self, monkeypatch):
        """Does not call fetch_history_from_db when ORACLE_USER is absent."""
        monkeypatch.delenv("ORACLE_USER", raising=False)

        with patch("src.api.routers.ui.fetch_history_from_db") as mock_fn:
            response = client.get("/api/v1/runs/history")

        assert response.status_code == 200
        mock_fn.assert_not_called()

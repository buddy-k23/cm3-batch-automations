"""Tests for cross-field validation and run history — issue #103.

Covers run-history fetch ordering (mock DB) and the /api/v1/runs/history
endpoint (via TestClient with JSON fallback).
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

client = TestClient(app)


class TestRunHistoryFetch:
    """Run-history service tests with mocked DB."""

    def test_run_history_fetch_returns_recent_first(self):
        """fetch_history should return rows ordered newest-first."""
        from src.database.run_history import RunHistoryRepository

        def _row(run_id, suite_name, environment, ts, status,
                 pass_count, fail_count, skip_count, total_count,
                 report_url, archive_path):
            row = MagicMock()
            row._mapping = {
                "run_id": run_id,
                "suite_name": suite_name,
                "environment": environment,
                "run_timestamp": ts,
                "status": status,
                "pass_count": pass_count,
                "fail_count": fail_count,
                "skip_count": skip_count,
                "total_count": total_count,
                "report_url": report_url,
                "archive_path": archive_path,
            }
            return row

        # DB returns rows already in DESC order (newest first)
        db_rows = [
            _row("run-002", "Suite B", "uat",
                 datetime(2026, 3, 25, 12, 0, 0, tzinfo=timezone.utc),
                 "PASS", 5, 0, 0, 5, "/reports/b.html", "/archive/002"),
            _row("run-001", "Suite A", "dev",
                 datetime(2026, 3, 24, 10, 0, 0, tzinfo=timezone.utc),
                 "FAIL", 2, 1, 0, 3, "/reports/a.html", "/archive/001"),
        ]

        mock_engine = MagicMock()
        mock_conn = MagicMock()
        mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter(db_rows))
        mock_conn.execute.return_value = mock_result

        repo = RunHistoryRepository(engine=mock_engine, schema_prefix="APP_INT.")
        rows = repo.fetch_history(limit=20)

        assert len(rows) == 2
        # First row should be the newer run
        assert rows[0]["run_id"] == "run-002"
        assert rows[1]["run_id"] == "run-001"
        # Timestamps should be ISO strings
        assert "2026" in rows[0]["timestamp"]


class TestRunHistoryAPI:
    """API endpoint tests for /api/v1/runs/history."""

    def test_run_history_api_endpoint(self, tmp_path, monkeypatch):
        """GET /api/v1/runs/history should return JSON array from fallback file."""
        history_entries = [
            {
                "run_id": "aaa-111",
                "suite_name": "Suite X",
                "status": "PASS",
                "timestamp": "2026-03-24T08:00:00Z",
            },
            {
                "run_id": "bbb-222",
                "suite_name": "Suite Y",
                "status": "FAIL",
                "timestamp": "2026-03-25T09:00:00Z",
            },
        ]
        history_file = tmp_path / "run_history.json"
        history_file.write_text(json.dumps(history_entries), encoding="utf-8")

        # Ensure the JSON-fallback path is used (no Oracle env)
        monkeypatch.delenv("ORACLE_USER", raising=False)
        monkeypatch.setattr(
            "src.api.routers.ui._RUN_HISTORY_PATH", history_file
        )

        r = client.get("/api/v1/runs/history")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert len(data) == 2
        # Fallback reverses the list so newest is first
        assert data[0]["run_id"] == "bbb-222"

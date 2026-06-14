"""Unit tests for RunHistoryRepository — legacy test file, now uses SQLAlchemy Core mocks.

Kept alongside ``test_run_history_db.py`` for historical continuity.
All Oracle-specific assertions have been replaced with SQLAlchemy Core equivalents.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest


SAMPLE_ENTRY = {
    "run_id": "abc-123",
    "suite_name": "My Suite",
    "environment": "uat",
    "timestamp": "2026-03-02T10:00:00.000000Z",
    "status": "PASS",
    "pass_count": 3,
    "fail_count": 0,
    "skip_count": 1,
    "total_count": 4,
    "report_url": "/reports/My_Suite_abc-123_suite.html",
    "archive_path": "/reports/archive/2026/03/02/abc-123",
}

SAMPLE_RESULTS = [
    {"name": "Test A", "type": "structural", "status": "PASS",
     "rows_processed": 100, "error_count": 0, "duration_secs": 0.5, "report_path": ""},
    {"name": "Test B", "type": "api_check", "status": "FAIL",
     "rows_processed": None, "error_count": 1, "duration_secs": 0.1, "report_path": ""},
]


def _make_repo():
    """Return a RunHistoryRepository with a mocked SQLAlchemy engine."""
    from src.database.run_history import RunHistoryRepository

    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    repo = RunHistoryRepository(engine=mock_engine, schema_prefix="APP_INT.")
    return repo, mock_engine, mock_conn


class TestInsertRun:
    def test_executes_insert_with_correct_params(self):
        repo, mock_engine, mock_conn = _make_repo()
        repo.insert_run(SAMPLE_ENTRY)
        assert mock_conn.execute.called
        stmt, params = mock_conn.execute.call_args.args
        assert params["run_id"] == "abc-123"
        assert params["suite_name"] == "My Suite"
        assert params["status"] == "PASS"
        assert params["pass_count"] == 3
        assert params["total_count"] == 4

    def test_timestamp_converted_to_datetime(self):
        repo, mock_engine, mock_conn = _make_repo()
        repo.insert_run(SAMPLE_ENTRY)
        _, params = mock_conn.execute.call_args.args
        assert isinstance(params["run_timestamp"], datetime)
        assert params["run_timestamp"].tzinfo is not None

    def test_commits_after_insert(self):
        repo, mock_engine, mock_conn = _make_repo()
        repo.insert_run(SAMPLE_ENTRY)
        mock_conn.commit.assert_called_once()


class TestInsertTests:
    def test_executemany_called_with_all_rows(self):
        repo, mock_engine, mock_conn = _make_repo()
        repo.insert_tests("abc-123", SAMPLE_RESULTS)
        # New implementation issues one execute per row
        assert mock_conn.execute.call_count == 2
        first_stmt, first_params = mock_conn.execute.call_args_list[0].args
        assert first_params["run_id"] == "abc-123"
        assert first_params["test_name"] == "Test A"
        second_stmt, second_params = mock_conn.execute.call_args_list[1].args
        assert second_params["status"] == "FAIL"

    def test_empty_results_skips_db_call(self):
        repo, mock_engine, mock_conn = _make_repo()
        repo.insert_tests("abc-123", [])
        mock_conn.execute.assert_not_called()

    def test_commits_after_insert(self):
        repo, mock_engine, mock_conn = _make_repo()
        repo.insert_tests("abc-123", SAMPLE_RESULTS)
        mock_conn.commit.assert_called_once()


class TestFetchHistory:
    def _make_row(self):
        row = MagicMock()
        row._mapping = {
            "run_id": "abc-123",
            "suite_name": "My Suite",
            "environment": "uat",
            "run_timestamp": datetime(2026, 3, 2, 10, 0, 0, tzinfo=timezone.utc),
            "status": "PASS",
            "pass_count": 3,
            "fail_count": 0,
            "skip_count": 1,
            "total_count": 4,
            "report_url": "/reports/x.html",
            "archive_path": "/archive/abc-123",
        }
        return row

    def test_returns_list_of_dicts(self):
        repo, mock_engine, mock_conn = _make_repo()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([self._make_row()]))
        mock_conn.execute.return_value = mock_result
        results = repo.fetch_history(limit=20)
        assert len(results) == 1
        assert results[0]["run_id"] == "abc-123"
        assert results[0]["status"] == "PASS"

    def test_timestamp_serialized_to_iso_string(self):
        repo, mock_engine, mock_conn = _make_repo()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([self._make_row()]))
        mock_conn.execute.return_value = mock_result
        results = repo.fetch_history()
        assert isinstance(results[0]["timestamp"], str)
        assert "2026" in results[0]["timestamp"]

    def test_passes_limit_to_query(self):
        repo, mock_engine, mock_conn = _make_repo()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute.return_value = mock_result
        repo.fetch_history(limit=5)
        # Verify the SQL text passed to execute contains the LIMIT value
        stmt = mock_conn.execute.call_args.args[0]
        assert "5" in str(stmt)

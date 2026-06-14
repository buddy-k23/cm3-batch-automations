"""Unit tests for RunHistoryRepository rewritten with SQLAlchemy Core.

All database calls are mocked via ``get_engine`` so no real DB is required.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, call

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
    {
        "name": "Test A",
        "type": "structural",
        "status": "PASS",
        "rows_processed": 100,
        "error_count": 0,
        "duration_secs": 0.5,
        "report_path": "",
    },
    {
        "name": "Test B",
        "type": "api_check",
        "status": "FAIL",
        "rows_processed": None,
        "error_count": 1,
        "duration_secs": 0.1,
        "report_path": "",
    },
]


def _make_mock_engine():
    """Return a mock SQLAlchemy Engine with a mock connection context manager."""
    mock_engine = MagicMock()
    mock_conn = MagicMock()
    mock_engine.connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
    mock_engine.connect.return_value.__exit__ = MagicMock(return_value=False)
    return mock_engine, mock_conn


def _make_repo(mock_engine):
    """Construct a RunHistoryRepository injected with a mock engine."""
    from src.database.run_history import RunHistoryRepository

    repo = RunHistoryRepository(engine=mock_engine, schema_prefix="APP_INT.")
    return repo


# ---------------------------------------------------------------------------
# _parse_ts helper
# ---------------------------------------------------------------------------


class TestParsTs:
    def test_parses_iso_utc_string(self):
        from src.database.run_history import _parse_ts

        dt = _parse_ts("2026-03-02T10:00:00.000000Z")
        assert isinstance(dt, datetime)
        assert dt.tzinfo == timezone.utc
        assert dt.year == 2026

    def test_rejects_offset_string(self):
        from src.database.run_history import _parse_ts

        with pytest.raises(ValueError, match="UTC"):
            _parse_ts("2026-03-02T10:00:00+05:00")


class TestTsToIso:
    def test_datetime_converted_to_iso_string(self):
        from src.database.run_history import _ts_to_iso

        dt = datetime(2026, 3, 2, 10, 0, 0, tzinfo=timezone.utc)
        result = _ts_to_iso(dt)
        assert isinstance(result, str)
        assert result.endswith("Z")
        assert "2026-03-02" in result

    def test_non_datetime_passed_through(self):
        from src.database.run_history import _ts_to_iso

        assert _ts_to_iso("hello") == "hello"
        assert _ts_to_iso(42) == 42
        assert _ts_to_iso(None) is None


# ---------------------------------------------------------------------------
# insert_run
# ---------------------------------------------------------------------------


class TestInsertRun:
    def test_executes_insert_with_correct_params(self):
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_run(SAMPLE_ENTRY)
        assert mock_conn.execute.called
        stmt, params = mock_conn.execute.call_args.args
        # params is a dict passed to execute
        assert params["run_id"] == "abc-123"
        assert params["suite_name"] == "My Suite"
        assert params["status"] == "PASS"
        assert params["pass_count"] == 3
        assert params["total_count"] == 4

    def test_timestamp_converted_to_datetime(self):
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_run(SAMPLE_ENTRY)
        _, params = mock_conn.execute.call_args.args
        assert isinstance(params["run_timestamp"], datetime)
        assert params["run_timestamp"].tzinfo is not None

    def test_commits_after_insert(self):
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_run(SAMPLE_ENTRY)
        mock_conn.commit.assert_called_once()

    def test_engine_failure_is_graceful(self):
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("DB unavailable")
        repo = _make_repo(mock_engine)
        # Should not raise — just log and return
        repo.insert_run(SAMPLE_ENTRY)

    def test_execute_failure_is_graceful(self):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = Exception("table not found")
        repo = _make_repo(mock_engine)
        repo.insert_run(SAMPLE_ENTRY)


# ---------------------------------------------------------------------------
# insert_tests
# ---------------------------------------------------------------------------


class TestInsertTests:
    def test_executes_for_each_row(self):
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_tests("abc-123", SAMPLE_RESULTS)
        # Should call execute twice (one per result row)
        assert mock_conn.execute.call_count == 2

    def test_first_row_params(self):
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_tests("abc-123", SAMPLE_RESULTS)
        first_call_args = mock_conn.execute.call_args_list[0].args
        stmt, params = first_call_args
        assert params["run_id"] == "abc-123"
        assert params["test_name"] == "Test A"
        assert params["test_type"] == "structural"
        assert params["status"] == "PASS"
        assert params["row_count"] == 100
        assert params["error_count"] == 0

    def test_second_row_params(self):
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_tests("abc-123", SAMPLE_RESULTS)
        second_call_args = mock_conn.execute.call_args_list[1].args
        stmt, params = second_call_args
        assert params["test_name"] == "Test B"
        assert params["status"] == "FAIL"
        assert params["row_count"] is None

    def test_empty_results_skips_db_call(self):
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_tests("abc-123", [])
        mock_conn.execute.assert_not_called()

    def test_commits_after_insert(self):
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_tests("abc-123", SAMPLE_RESULTS)
        mock_conn.commit.assert_called_once()

    def test_engine_failure_is_graceful(self):
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("DB unavailable")
        repo = _make_repo(mock_engine)
        repo.insert_tests("abc-123", SAMPLE_RESULTS)

    def test_execute_failure_is_graceful(self):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = Exception("insert failed")
        repo = _make_repo(mock_engine)
        repo.insert_tests("abc-123", SAMPLE_RESULTS)


# ---------------------------------------------------------------------------
# fetch_history
# ---------------------------------------------------------------------------


class TestFetchHistory:
    def _make_row(self):
        """Return a mock Row object as SQLAlchemy Core returns."""
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
        mock_engine, mock_conn = _make_mock_engine()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([self._make_row()]))
        mock_conn.execute.return_value = mock_result
        repo = _make_repo(mock_engine)
        results = repo.fetch_history(limit=20)
        assert isinstance(results, list)
        assert len(results) == 1
        assert results[0]["run_id"] == "abc-123"
        assert results[0]["status"] == "PASS"

    def test_timestamp_serialized_to_iso_string(self):
        mock_engine, mock_conn = _make_mock_engine()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([self._make_row()]))
        mock_conn.execute.return_value = mock_result
        repo = _make_repo(mock_engine)
        results = repo.fetch_history()
        # run_timestamp should be renamed to "timestamp" and converted to ISO str
        assert "timestamp" in results[0]
        assert "run_timestamp" not in results[0]
        assert isinstance(results[0]["timestamp"], str)
        assert "2026" in results[0]["timestamp"]
        assert results[0]["timestamp"].endswith("Z")

    def test_returns_empty_list_when_no_rows(self):
        mock_engine, mock_conn = _make_mock_engine()
        mock_result = MagicMock()
        mock_result.__iter__ = MagicMock(return_value=iter([]))
        mock_conn.execute.return_value = mock_result
        repo = _make_repo(mock_engine)
        results = repo.fetch_history()
        assert results == []

    def test_engine_failure_returns_empty_list(self):
        mock_engine = MagicMock()
        mock_engine.connect.side_effect = Exception("DB unavailable")
        repo = _make_repo(mock_engine)
        results = repo.fetch_history()
        assert results == []

    def test_execute_failure_returns_empty_list(self):
        mock_engine, mock_conn = _make_mock_engine()
        mock_conn.execute.side_effect = Exception("query failed")
        repo = _make_repo(mock_engine)
        results = repo.fetch_history()
        assert results == []


# ---------------------------------------------------------------------------
# Default constructor — uses get_engine() and get_schema_prefix()
# ---------------------------------------------------------------------------


class TestDefaultConstructor:
    def test_uses_get_engine_and_schema_prefix(self):
        from src.database.run_history import RunHistoryRepository

        mock_engine = MagicMock()
        with (
            patch("src.database.run_history.get_engine", return_value=mock_engine),
            patch("src.database.run_history.get_schema_prefix", return_value="TEST."),
        ):
            repo = RunHistoryRepository()
            assert repo._engine is mock_engine
            assert repo._schema_prefix == "TEST."


# ---------------------------------------------------------------------------
# quality_score wiring — issue #239
# ---------------------------------------------------------------------------


SAMPLE_ENTRY_WITH_QUALITY_SCORE = {
    **SAMPLE_ENTRY,
    "quality_score": 95.5,
}

SAMPLE_ENTRY_WITHOUT_QUALITY_SCORE = {
    k: v for k, v in SAMPLE_ENTRY.items()
    # no quality_score key
}


class TestInsertRunQualityScore:
    """Verify quality_score is included in the INSERT params for APP_RUN_HISTORY."""

    def test_quality_score_passed_when_present(self):
        """insert_run must include quality_score=95.5 in the DB params."""
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_run(SAMPLE_ENTRY_WITH_QUALITY_SCORE)
        _, params = mock_conn.execute.call_args.args
        assert "quality_score" in params
        assert params["quality_score"] == 95.5

    def test_quality_score_is_none_when_absent(self):
        """insert_run must pass quality_score=None when entry has no quality_score."""
        mock_engine, mock_conn = _make_mock_engine()
        repo = _make_repo(mock_engine)
        repo.insert_run(SAMPLE_ENTRY_WITHOUT_QUALITY_SCORE)
        _, params = mock_conn.execute.call_args.args
        assert "quality_score" in params
        assert params["quality_score"] is None

    def test_quality_score_in_sql_insert(self):
        """_sql_insert_run must reference quality_score column and bind param."""
        from src.database.run_history import _sql_insert_run

        sql = _sql_insert_run("APP_INT.")
        assert "quality_score" in sql

    def test_quality_score_in_sql_fetch(self):
        """_sql_fetch_history must select quality_score column."""
        from src.database.run_history import _sql_fetch_history

        sql = _sql_fetch_history("APP_INT.", limit=20)
        assert "quality_score" in sql

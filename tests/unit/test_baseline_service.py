"""Unit tests for baseline_service.py — TDD: written before implementation.

Tests cover:
- First-run baseline creation (sample_size=1)
- Second-run rolling average recalculation (sample_size=2)
- Rolling window capped at 10 runs (11 runs → sample_size=10)
- get_baseline returns None for unknown suite
- list_baselines returns all suites sorted alphabetically
- Round-trip JSON serialization via tmp_path fixture
- Missing quality_score in result → avg_quality_score stays None
- total_rows=0 → avg_error_rate=0.0
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_result(
    pass_count: int = 8,
    total_count: int = 10,
    invalid_rows: int = 1,
    total_rows: int = 100,
    quality_score: float | None = 90.0,
) -> dict:
    """Build a minimal result dict matching the run_history entry shape."""
    result = {
        "pass_count": pass_count,
        "total_count": total_count,
        "invalid_rows": invalid_rows,
        "total_rows": total_rows,
    }
    if quality_score is not None:
        result["quality_score"] = quality_score
    return result


# ---------------------------------------------------------------------------
# Tests: update_baseline
# ---------------------------------------------------------------------------

class TestUpdateBaseline:
    """Tests for update_baseline()."""

    def test_first_run_creates_baseline_with_sample_size_1(self, tmp_path: Path):
        """First call creates a baseline with sample_size=1."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            result = _make_result(pass_count=8, total_count=10, quality_score=90.0)
            baseline = update_baseline("SUITE_A", result)

        assert baseline["suite_name"] == "SUITE_A"
        assert baseline["sample_size"] == 1
        assert baseline["pass_rate"] == pytest.approx(80.0)
        assert baseline["avg_quality_score"] == pytest.approx(90.0)
        assert "updated_at" in baseline

    def test_second_run_recalculates_averages_with_sample_size_2(
        self, tmp_path: Path
    ):
        """Second call recalculates averages from 2 runs."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("SUITE_A", _make_result(pass_count=8, total_count=10, quality_score=80.0))
            baseline = update_baseline(
                "SUITE_A",
                _make_result(pass_count=6, total_count=10, quality_score=90.0),
            )

        assert baseline["sample_size"] == 2
        # pass_rate: (80 + 60) / 2 = 70.0
        assert baseline["pass_rate"] == pytest.approx(70.0)
        # avg_quality_score: (80 + 90) / 2 = 85.0
        assert baseline["avg_quality_score"] == pytest.approx(85.0)

    def test_rolling_window_capped_at_10_after_11_runs(self, tmp_path: Path):
        """After 11 runs the rolling window holds exactly 10; sample_size=10."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            for i in range(11):
                baseline = update_baseline(
                    "SUITE_A",
                    _make_result(pass_count=i, total_count=10, quality_score=float(i * 10)),
                )

        assert baseline["sample_size"] == 10

    def test_avg_error_rate_computed_from_invalid_and_total_rows(
        self, tmp_path: Path
    ):
        """avg_error_rate = invalid_rows / total_rows * 100."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            baseline = update_baseline(
                "SUITE_A",
                _make_result(invalid_rows=5, total_rows=200),
            )

        assert baseline["avg_error_rate"] == pytest.approx(2.5)

    def test_total_rows_zero_gives_error_rate_zero(self, tmp_path: Path):
        """total_rows=0 avoids division by zero and yields avg_error_rate=0.0."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            baseline = update_baseline(
                "SUITE_A",
                _make_result(invalid_rows=3, total_rows=0),
            )

        assert baseline["avg_error_rate"] == pytest.approx(0.0)

    def test_missing_quality_score_yields_avg_quality_score_none(
        self, tmp_path: Path
    ):
        """When quality_score is absent from result, avg_quality_score is None."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            baseline = update_baseline(
                "SUITE_A",
                _make_result(quality_score=None),
            )

        assert baseline["avg_quality_score"] is None

    def test_mixed_quality_score_presence_averages_available_values(
        self, tmp_path: Path
    ):
        """When some runs lack quality_score, average computed from runs that have it."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("SUITE_A", _make_result(quality_score=80.0))
            baseline = update_baseline("SUITE_A", _make_result(quality_score=None))

        # Only 1 run had a score (80.0), so average is 80.0
        assert baseline["avg_quality_score"] == pytest.approx(80.0)

    def test_returns_updated_baseline_dict(self, tmp_path: Path):
        """update_baseline returns the updated baseline dict."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            result = update_baseline("SUITE_A", _make_result())

        assert isinstance(result, dict)
        assert result["suite_name"] == "SUITE_A"

    def test_multiple_suites_stored_independently(self, tmp_path: Path):
        """Baselines for different suites do not interfere with each other."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("SUITE_A", _make_result(pass_count=10, total_count=10))
            update_baseline("SUITE_B", _make_result(pass_count=5, total_count=10))
            a = update_baseline("SUITE_A", _make_result(pass_count=10, total_count=10))
            b = update_baseline("SUITE_B", _make_result(pass_count=5, total_count=10))

        assert a["pass_rate"] == pytest.approx(100.0)
        assert b["pass_rate"] == pytest.approx(50.0)


# ---------------------------------------------------------------------------
# Tests: get_baseline
# ---------------------------------------------------------------------------

class TestGetBaseline:
    """Tests for get_baseline()."""

    def test_returns_none_for_unknown_suite(self, tmp_path: Path):
        """get_baseline returns None when no baseline exists for the suite."""
        from src.services.baseline_service import get_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            result = get_baseline("UNKNOWN_SUITE")

        assert result is None

    def test_returns_none_when_file_does_not_exist(self, tmp_path: Path):
        """get_baseline returns None when baselines.json does not exist."""
        from src.services.baseline_service import get_baseline

        storage = tmp_path / "nonexistent.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            result = get_baseline("SUITE_A")

        assert result is None

    def test_returns_baseline_after_update(self, tmp_path: Path):
        """get_baseline retrieves a baseline previously written by update_baseline."""
        from src.services.baseline_service import get_baseline, update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("SUITE_A", _make_result(quality_score=75.0))
            result = get_baseline("SUITE_A")

        assert result is not None
        assert result["suite_name"] == "SUITE_A"
        assert result["avg_quality_score"] == pytest.approx(75.0)


# ---------------------------------------------------------------------------
# Tests: list_baselines
# ---------------------------------------------------------------------------

class TestListBaselines:
    """Tests for list_baselines()."""

    def test_returns_empty_list_when_no_file(self, tmp_path: Path):
        """list_baselines returns [] when baselines.json does not exist."""
        from src.services.baseline_service import list_baselines

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            result = list_baselines()

        assert result == []

    def test_returns_all_suites_sorted_alphabetically(self, tmp_path: Path):
        """list_baselines sorts results alphabetically by suite_name."""
        from src.services.baseline_service import list_baselines, update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("ZEBRA", _make_result())
            update_baseline("ALPHA", _make_result())
            update_baseline("MONKEY", _make_result())
            result = list_baselines()

        names = [b["suite_name"] for b in result]
        assert names == ["ALPHA", "MONKEY", "ZEBRA"]

    def test_returns_list_of_dicts(self, tmp_path: Path):
        """list_baselines returns a list of baseline dicts."""
        from src.services.baseline_service import list_baselines, update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("SUITE_A", _make_result())
            result = list_baselines()

        assert isinstance(result, list)
        assert len(result) == 1
        assert isinstance(result[0], dict)


# ---------------------------------------------------------------------------
# Tests: round-trip JSON serialization
# ---------------------------------------------------------------------------

class TestJsonRoundTrip:
    """Tests for JSON file persistence."""

    def test_written_file_is_valid_json(self, tmp_path: Path):
        """update_baseline writes valid JSON to disk."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("SUITE_A", _make_result())

        assert storage.exists()
        data = json.loads(storage.read_text(encoding="utf-8"))
        assert isinstance(data, dict)

    def test_baseline_survives_file_reload(self, tmp_path: Path):
        """Baseline persists after re-reading the storage file from disk."""
        from src.services.baseline_service import get_baseline, update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("SUITE_A", _make_result(quality_score=88.0))
            # Re-read from disk by calling get_baseline with the same patch
            result = get_baseline("SUITE_A")

        assert result is not None
        assert result["avg_quality_score"] == pytest.approx(88.0)

    def test_file_created_on_first_write(self, tmp_path: Path):
        """baselines.json is created if it does not exist yet."""
        from src.services.baseline_service import update_baseline

        storage = tmp_path / "baselines.json"
        assert not storage.exists()

        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("SUITE_A", _make_result())

        assert storage.exists()

    def test_baseline_record_has_required_fields(self, tmp_path: Path):
        """Stored baseline contains all required schema fields."""
        from src.services.baseline_service import get_baseline, update_baseline

        storage = tmp_path / "baselines.json"
        with patch("src.services.baseline_service._BASELINES_PATH", storage):
            update_baseline("SUITE_A", _make_result())
            baseline = get_baseline("SUITE_A")

        required_keys = {
            "suite_name",
            "pass_rate",
            "avg_quality_score",
            "avg_error_rate",
            "sample_size",
            "updated_at",
        }
        assert required_keys.issubset(set(baseline.keys()))


# ---------------------------------------------------------------------------
# Tests: DB path
# ---------------------------------------------------------------------------


class TestUpdateBaselineDbPath:
    """Tests for the DB path in update_baseline, get_baseline, list_baselines."""

    def test_update_baseline_calls_db_upsert_when_db_adapter_set(
        self, tmp_path: Path, monkeypatch
    ):
        """When DB_ADAPTER is set, update_baseline delegates to _update_baseline_db."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")

        from src.services import baseline_service

        storage = tmp_path / "baselines.json"
        mock_baseline = {
            "suite_name": "SUITE_A",
            "pass_rate": 80.0,
            "avg_quality_score": 90.0,
            "avg_error_rate": 1.0,
            "sample_size": 1,
            "updated_at": "2026-03-31T00:00:00",
        }
        with (
            patch.object(baseline_service, "_BASELINES_PATH", storage),
            patch.object(
                baseline_service, "_update_baseline_db", return_value=mock_baseline
            ) as mock_db,
        ):
            result = baseline_service.update_baseline("SUITE_A", _make_result())

        mock_db.assert_called_once_with("SUITE_A", _make_result())
        assert result == mock_baseline

    def test_update_baseline_falls_back_to_json_when_db_raises(
        self, tmp_path: Path, monkeypatch
    ):
        """When DB path raises, update_baseline falls back to JSON silently."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")

        from src.services import baseline_service

        storage = tmp_path / "baselines.json"
        with (
            patch.object(baseline_service, "_BASELINES_PATH", storage),
            patch.object(
                baseline_service, "_update_baseline_db", side_effect=RuntimeError("db down")
            ),
        ):
            result = baseline_service.update_baseline("SUITE_A", _make_result())

        # JSON fallback must succeed and return a valid baseline
        assert result["suite_name"] == "SUITE_A"
        assert result["sample_size"] == 1

    def test_get_baseline_calls_db_path_when_db_adapter_set(
        self, tmp_path: Path, monkeypatch
    ):
        """When DB_ADAPTER is set, get_baseline delegates to _get_baseline_db."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")

        from src.services import baseline_service

        mock_baseline = {
            "suite_name": "SUITE_A",
            "pass_rate": 75.0,
            "avg_quality_score": None,
            "avg_error_rate": 0.0,
            "sample_size": 3,
            "updated_at": "2026-03-31T00:00:00",
        }
        storage = tmp_path / "baselines.json"
        with (
            patch.object(baseline_service, "_BASELINES_PATH", storage),
            patch.object(
                baseline_service, "_get_baseline_db", return_value=mock_baseline
            ) as mock_db,
        ):
            result = baseline_service.get_baseline("SUITE_A")

        mock_db.assert_called_once_with("SUITE_A")
        assert result == mock_baseline

    def test_get_baseline_falls_back_to_json_when_db_raises(
        self, tmp_path: Path, monkeypatch
    ):
        """When DB path raises, get_baseline falls back to JSON."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")

        from src.services import baseline_service

        storage = tmp_path / "baselines.json"
        # Pre-populate JSON so the fallback has something to return
        storage.write_text(
            '{"SUITE_A": {"baseline": {"suite_name": "SUITE_A", "pass_rate": 50.0,'
            ' "avg_quality_score": null, "avg_error_rate": 0.0, "sample_size": 1,'
            ' "updated_at": "2026-01-01T00:00:00"}, "history": []}}',
            encoding="utf-8",
        )
        with (
            patch.object(baseline_service, "_BASELINES_PATH", storage),
            patch.object(
                baseline_service, "_get_baseline_db", side_effect=RuntimeError("db down")
            ),
        ):
            result = baseline_service.get_baseline("SUITE_A")

        assert result is not None
        assert result["suite_name"] == "SUITE_A"

    def test_list_baselines_calls_db_path_when_db_adapter_set(
        self, tmp_path: Path, monkeypatch
    ):
        """When DB_ADAPTER is set, list_baselines delegates to _list_baselines_db."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")

        from src.services import baseline_service

        mock_list = [
            {"suite_name": "A", "pass_rate": 90.0, "avg_quality_score": None,
             "avg_error_rate": 0.0, "sample_size": 2, "updated_at": "2026-03-31T00:00:00"},
        ]
        storage = tmp_path / "baselines.json"
        with (
            patch.object(baseline_service, "_BASELINES_PATH", storage),
            patch.object(
                baseline_service, "_list_baselines_db", return_value=mock_list
            ) as mock_db,
        ):
            result = baseline_service.list_baselines()

        mock_db.assert_called_once_with()
        assert result == mock_list

    def test_list_baselines_falls_back_to_json_when_db_raises(
        self, tmp_path: Path, monkeypatch
    ):
        """When DB path raises, list_baselines falls back to JSON."""
        monkeypatch.setenv("DB_ADAPTER", "sqlite")

        from src.services import baseline_service

        storage = tmp_path / "baselines.json"
        storage.write_text(
            '{"SUITE_A": {"baseline": {"suite_name": "SUITE_A", "pass_rate": 50.0,'
            ' "avg_quality_score": null, "avg_error_rate": 0.0, "sample_size": 1,'
            ' "updated_at": "2026-01-01T00:00:00"}, "history": []}}',
            encoding="utf-8",
        )
        with (
            patch.object(baseline_service, "_BASELINES_PATH", storage),
            patch.object(
                baseline_service, "_list_baselines_db", side_effect=RuntimeError("db down")
            ),
        ):
            result = baseline_service.list_baselines()

        assert len(result) == 1
        assert result[0]["suite_name"] == "SUITE_A"

    def test_update_baseline_skips_db_when_db_adapter_not_set(
        self, tmp_path: Path, monkeypatch
    ):
        """When DB_ADAPTER is not set, update_baseline uses JSON only."""
        monkeypatch.delenv("DB_ADAPTER", raising=False)

        from src.services import baseline_service

        storage = tmp_path / "baselines.json"
        with (
            patch.object(baseline_service, "_BASELINES_PATH", storage),
            patch.object(baseline_service, "_update_baseline_db") as mock_db,
        ):
            result = baseline_service.update_baseline("SUITE_A", _make_result())

        mock_db.assert_not_called()
        assert result["suite_name"] == "SUITE_A"

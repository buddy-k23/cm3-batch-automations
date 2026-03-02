"""Tests for watch_command.py and the /api/v1/runs/trigger endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_trigger(dir_: Path, name: str) -> Path:
    """Create a trigger file in dir_ and return its path."""
    p = dir_ / name
    p.write_text("")
    return p


def _make_yaml(dir_: Path, name: str) -> Path:
    """Create a minimal suite YAML in dir_ and return its path."""
    p = dir_ / name
    p.write_text("name: test\nenvironment: dev\ntests: []\n")
    return p


# ---------------------------------------------------------------------------
# _extract_run_date_from_trigger
# ---------------------------------------------------------------------------

class TestExtractRunDateFromTrigger:
    """Tests for _extract_run_date_from_trigger()."""

    def test_parses_standard_batch_complete_filename(self, tmp_path):
        """Test 6: parses batch_complete_YYYYMMDD.trigger correctly."""
        from src.commands.watch_command import _extract_run_date_from_trigger

        trigger = tmp_path / "batch_complete_20260301.trigger"
        trigger.write_text("")
        assert _extract_run_date_from_trigger(trigger) == "20260301"

    def test_returns_none_for_unrecognised_format(self, tmp_path):
        """Test 7: returns None when filename has no 8-digit date."""
        from src.commands.watch_command import _extract_run_date_from_trigger

        trigger = tmp_path / "batch_done.trigger"
        trigger.write_text("")
        assert _extract_run_date_from_trigger(trigger) is None

    def test_parses_date_embedded_anywhere_in_stem(self, tmp_path):
        """Extra: date can appear anywhere in stem."""
        from src.commands.watch_command import _extract_run_date_from_trigger

        trigger = tmp_path / "run_20260115_done.trigger"
        trigger.write_text("")
        assert _extract_run_date_from_trigger(trigger) == "20260115"


# ---------------------------------------------------------------------------
# _find_matching_suite
# ---------------------------------------------------------------------------

class TestFindMatchingSuite:
    """Tests for _find_matching_suite()."""

    def test_finds_yaml_by_date_in_filename(self, tmp_path):
        """Test 8: returns YAML whose filename contains run_date."""
        from src.commands.watch_command import _find_matching_suite

        _make_yaml(tmp_path, "other_suite.yaml")
        expected = _make_yaml(tmp_path, "p327_uat_20260301.yaml")
        result = _find_matching_suite(tmp_path, "20260301")
        assert result == expected

    def test_falls_back_to_first_yaml_when_no_date_match(self, tmp_path):
        """Test 9: returns first YAML alphabetically when no date match."""
        from src.commands.watch_command import _find_matching_suite

        first = _make_yaml(tmp_path, "aaa_suite.yaml")
        _make_yaml(tmp_path, "zzz_suite.yaml")
        result = _find_matching_suite(tmp_path, "99991231")
        assert result == first

    def test_returns_none_when_suites_dir_is_empty(self, tmp_path):
        """Test 10: returns None when no YAML files exist."""
        from src.commands.watch_command import _find_matching_suite

        result = _find_matching_suite(tmp_path, "20260301")
        assert result is None

    def test_returns_first_yaml_when_run_date_is_none(self, tmp_path):
        """Extra: when run_date is None, returns first YAML."""
        from src.commands.watch_command import _find_matching_suite

        first = _make_yaml(tmp_path, "alpha.yaml")
        _make_yaml(tmp_path, "beta.yaml")
        result = _find_matching_suite(tmp_path, None)
        assert result == first


# ---------------------------------------------------------------------------
# watch_once — the per-poll workhorse
# ---------------------------------------------------------------------------

class TestWatchOnce:
    """Tests for watch_once()."""

    def test_finds_trigger_file_in_watch_dir(self, tmp_path):
        """Test 1: watcher detects a .trigger file."""
        from src.commands.watch_command import watch_once

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        suites_dir = tmp_path / "suites"
        suites_dir.mkdir()
        output_dir = tmp_path / "reports"

        _make_trigger(watch_dir, "batch_complete_20260301.trigger")
        _make_yaml(suites_dir, "p327_uat.yaml")

        logger = MagicMock()
        with patch("src.commands.run_tests_command.run_suite_from_path"):
            ran = watch_once(watch_dir, suites_dir, "dev", output_dir, logger)

        assert ran == 1

    def test_trigger_maps_to_matching_suite_by_date(self, tmp_path):
        """Test 2: trigger filename date is used to select the correct suite."""
        from src.commands.watch_command import watch_once

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        suites_dir = tmp_path / "suites"
        suites_dir.mkdir()
        output_dir = tmp_path / "reports"

        _make_trigger(watch_dir, "batch_complete_20260301.trigger")
        _make_yaml(suites_dir, "other_suite.yaml")
        expected_suite = _make_yaml(suites_dir, "p327_uat_20260301.yaml")

        logger = MagicMock()
        captured_calls = []

        def fake_run(suite_path, params, env, output_dir):
            captured_calls.append(suite_path)

        with patch("src.commands.run_tests_command.run_suite_from_path", side_effect=fake_run):
            watch_once(watch_dir, suites_dir, "dev", output_dir, logger)

        assert len(captured_calls) == 1
        assert captured_calls[0] == str(expected_suite)

    def test_trigger_file_deleted_after_processing(self, tmp_path):
        """Test 3: trigger file is removed after the suite runs."""
        from src.commands.watch_command import watch_once

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        suites_dir = tmp_path / "suites"
        suites_dir.mkdir()
        output_dir = tmp_path / "reports"

        trigger = _make_trigger(watch_dir, "batch_complete_20260301.trigger")
        _make_yaml(suites_dir, "suite.yaml")

        logger = MagicMock()
        with patch("src.commands.run_tests_command.run_suite_from_path"):
            watch_once(watch_dir, suites_dir, "dev", output_dir, logger)

        assert not trigger.exists()

    def test_does_nothing_when_no_triggers(self, tmp_path):
        """Test 4: watch_once returns 0 when no .trigger files present."""
        from src.commands.watch_command import watch_once

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        suites_dir = tmp_path / "suites"
        suites_dir.mkdir()
        output_dir = tmp_path / "reports"

        logger = MagicMock()
        with patch("src.commands.run_tests_command.run_suite_from_path") as mock_run:
            ran = watch_once(watch_dir, suites_dir, "dev", output_dir, logger)

        assert ran == 0
        mock_run.assert_not_called()

    def test_ignores_non_trigger_files(self, tmp_path):
        """Test 5: .txt and .json files in the watch dir are ignored."""
        from src.commands.watch_command import watch_once

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        suites_dir = tmp_path / "suites"
        suites_dir.mkdir()
        output_dir = tmp_path / "reports"

        (watch_dir / "batch_complete_20260301.txt").write_text("")
        (watch_dir / "batch_complete_20260301.json").write_text("{}")
        _make_yaml(suites_dir, "suite.yaml")

        logger = MagicMock()
        with patch("src.commands.run_tests_command.run_suite_from_path") as mock_run:
            ran = watch_once(watch_dir, suites_dir, "dev", output_dir, logger)

        assert ran == 0
        mock_run.assert_not_called()

    def test_trigger_deleted_even_when_no_suite_found(self, tmp_path):
        """Extra: trigger file is removed even if no matching suite exists."""
        from src.commands.watch_command import watch_once

        watch_dir = tmp_path / "watch"
        watch_dir.mkdir()
        suites_dir = tmp_path / "suites"
        suites_dir.mkdir()  # empty — no YAMLs
        output_dir = tmp_path / "reports"

        trigger = _make_trigger(watch_dir, "batch_complete_20260301.trigger")
        logger = MagicMock()

        with patch("src.commands.run_tests_command.run_suite_from_path") as mock_run:
            ran = watch_once(watch_dir, suites_dir, "dev", output_dir, logger)

        assert ran == 0
        assert not trigger.exists()
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# API: POST /api/v1/runs/trigger
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client():
    """Return a TestClient wrapping the FastAPI app."""
    from src.api.main import app
    return TestClient(app)


class TestRunsTriggerEndpoint:
    """Tests for POST /api/v1/runs/trigger."""

    def test_returns_202_with_run_id(self, api_client):
        """Test 11: successful trigger returns 202 and run_id."""
        with patch("src.commands.run_tests_command.run_suite_from_path"):
            response = api_client.post(
                "/api/v1/runs/trigger",
                json={"suite": "config/test_suites/p327_uat.yaml"},
            )
        assert response.status_code == 202
        body = response.json()
        assert "run_id" in body
        assert body["status"] == "queued"

    def test_returns_422_when_suite_missing(self, api_client):
        """Test 12: missing suite field causes 422 Unprocessable Entity."""
        response = api_client.post(
            "/api/v1/runs/trigger",
            json={"env": "dev"},
        )
        assert response.status_code == 422

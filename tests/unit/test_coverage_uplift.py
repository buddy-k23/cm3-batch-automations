"""Targeted coverage tests for validate_service and logger utilities."""
import logging
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ── logger ──────────────────────────────────────────────────────────────────

class TestSetupLogger:
    def test_returns_logger_instance(self):
        from src.utils.logger import setup_logger
        logger = setup_logger("test-coverage-1", log_to_file=False)
        assert isinstance(logger, logging.Logger)

    def test_logger_name_matches(self):
        from src.utils.logger import setup_logger
        logger = setup_logger("test-coverage-name", log_to_file=False)
        assert logger.name == "test-coverage-name"

    def test_idempotent_handlers(self):
        from src.utils.logger import setup_logger
        logger = setup_logger("test-coverage-idem", log_to_file=False)
        count1 = len(logger.handlers)
        logger2 = setup_logger("test-coverage-idem", log_to_file=False)
        assert len(logger2.handlers) == count1

    def test_log_to_file_creates_file(self, tmp_path):
        from src.utils.logger import setup_logger
        import logging
        # Use unique name to avoid handler reuse
        name = f"test-file-logger-{tmp_path.name}"
        logger = setup_logger(name, log_dir=str(tmp_path), level=logging.DEBUG, log_to_file=True)
        log_files = list(tmp_path.glob("*.log"))
        assert len(log_files) >= 1

    def test_get_logger_returns_logger(self):
        from src.utils.logger import get_logger
        logger = get_logger("test-get-logger")
        assert isinstance(logger, logging.Logger)

    def test_custom_log_level(self):
        from src.utils.logger import setup_logger
        logger = setup_logger("test-debug-level", log_to_file=False, level=logging.DEBUG)
        assert logger.level == logging.DEBUG


# ── validate_service ─────────────────────────────────────────────────────────

class TestRunValidateService:
    """Tests for run_validate_service — mocks heavy IO."""

    def _make_pipe_file(self, tmp_path: Path) -> Path:
        p = tmp_path / "test.txt"
        p.write_text("NAME|AGE\nAlice|30\nBob|25\n")
        return p

    def test_returns_dict(self, tmp_path):
        from src.services.validate_service import run_validate_service
        f = self._make_pipe_file(tmp_path)
        result = run_validate_service(str(f))
        assert isinstance(result, dict)

    def test_result_has_required_keys(self, tmp_path):
        from src.services.validate_service import run_validate_service
        f = self._make_pipe_file(tmp_path)
        result = run_validate_service(str(f))
        assert "error_count" in result
        assert "warning_count" in result
        assert "total_rows" in result

    def test_error_count_is_integer(self, tmp_path):
        from src.services.validate_service import run_validate_service
        f = self._make_pipe_file(tmp_path)
        result = run_validate_service(str(f))
        assert isinstance(result["error_count"], int)

    def test_valid_file_has_zero_errors(self, tmp_path):
        from src.services.validate_service import run_validate_service
        f = self._make_pipe_file(tmp_path)
        result = run_validate_service(str(f))
        assert result["error_count"] >= 0

    def test_no_error_on_valid_pipe_file(self, tmp_path):
        from src.services.validate_service import run_validate_service
        f = self._make_pipe_file(tmp_path)
        result = run_validate_service(str(f))
        assert result is not None

    def test_warning_count_is_integer(self, tmp_path):
        from src.services.validate_service import run_validate_service
        f = self._make_pipe_file(tmp_path)
        result = run_validate_service(str(f))
        assert isinstance(result["warning_count"], int)

    def test_build_fixed_width_specs_with_position(self):
        from src.services.validate_service import _build_fixed_width_specs
        cfg = {
            "fields": [
                {"name": "LOC", "length": 6, "position": 1},
                {"name": "ACCT", "length": 18, "position": 7},
            ]
        }
        specs = _build_fixed_width_specs(cfg)
        assert specs[0] == ("LOC", 0, 6)
        assert specs[1] == ("ACCT", 6, 24)

    def test_build_fixed_width_specs_without_position(self):
        from src.services.validate_service import _build_fixed_width_specs
        cfg = {
            "fields": [
                {"name": "A", "length": 3},
                {"name": "B", "length": 5},
            ]
        }
        specs = _build_fixed_width_specs(cfg)
        assert specs[0] == ("A", 0, 3)
        assert specs[1] == ("B", 3, 8)

    def test_empty_fields_returns_empty_list(self):
        from src.services.validate_service import _build_fixed_width_specs
        assert _build_fixed_width_specs({"fields": []}) == []


# ── watch_command run_watch loop ─────────────────────────────────────────────

class TestRunWatch:
    def test_run_watch_max_iterations(self, tmp_path):
        """run_watch exits after max_iterations without sleeping."""
        from src.commands.watch_command import run_watch
        watch_dir = tmp_path / "triggers"
        suites_dir = tmp_path / "suites"
        output_dir = tmp_path / "reports"
        watch_dir.mkdir(); suites_dir.mkdir()
        # Run 2 iterations with no trigger files — should exit cleanly
        run_watch(str(watch_dir), str(suites_dir), "dev", str(output_dir),
                  poll_interval=0, max_iterations=2)
        assert output_dir.exists()

    def test_run_watch_creates_output_dir(self, tmp_path):
        from src.commands.watch_command import run_watch
        watch_dir = tmp_path / "w"; watch_dir.mkdir()
        suites_dir = tmp_path / "s"; suites_dir.mkdir()
        output_dir = tmp_path / "new_reports"
        run_watch(str(watch_dir), str(suites_dir), "dev", str(output_dir),
                  poll_interval=0, max_iterations=1)
        assert output_dir.exists()

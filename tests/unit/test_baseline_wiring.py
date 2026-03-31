"""Tests verifying update_baseline is wired into run_suite_from_path (#246).

Covers:
- After a suite run, update_baseline is called with the correct suite_name.
- When update_baseline raises, the suite run still succeeds (warning logged).
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

MINIMAL_SUITE_YAML = textwrap.dedent("""\
    name: Wiring Suite
    environment: test
    tests:
      - name: Structural Check
        type: structural
        file: {file_path}
        mapping: {mapping_path}
""")


def _write_suite(tmp_path: Path) -> tuple[Path, Path]:
    """Write a minimal suite YAML and dummy data/mapping files.

    Returns:
        Tuple of (suite_path, output_dir).
    """
    data_file = tmp_path / "data.dat"
    data_file.write_text("HELLO\n", encoding="utf-8")

    mapping_file = tmp_path / "mapping.json"
    mapping_file.write_text(
        '{"record_type": "delimited", "delimiter": "|", "fields": []}',
        encoding="utf-8",
    )

    suite_file = tmp_path / "suite.yaml"
    suite_file.write_text(
        MINIMAL_SUITE_YAML.format(
            file_path=str(data_file),
            mapping_path=str(mapping_file),
        )
    )
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    return suite_file, output_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBaselineWiredIntoRunSuiteFromPath:
    """update_baseline is called after a suite run completes."""

    def test_update_baseline_called_with_correct_suite_name(self, tmp_path: Path):
        """run_suite_from_path calls update_baseline(suite_name, result_summary)."""
        suite_file, output_dir = _write_suite(tmp_path)

        mock_svc_result = {
            "total_rows": 10,
            "error_count": 0,
            "warning_count": 0,
        }

        with (
            patch(
                "src.services.validate_service.run_validate_service",
                return_value=mock_svc_result,
            ),
            patch(
                "src.commands.run_tests_command._append_run_history"
            ) as mock_history,
            patch(
                "src.commands.run_tests_command.update_baseline"
            ) as mock_baseline,
            patch(
                "src.utils.archive.ArchiveManager.archive_run",
                return_value=tmp_path / "archive",
            ),
        ):
            from src.commands.run_tests_command import run_suite_from_path

            results = run_suite_from_path(
                suite_path=str(suite_file),
                params={},
                env="test",
                output_dir=str(output_dir),
            )

        mock_baseline.assert_called_once()
        args, kwargs = mock_baseline.call_args
        assert args[0] == "Wiring Suite"
        assert isinstance(args[1], dict)

    def test_update_baseline_raises_suite_run_still_succeeds(self, tmp_path: Path):
        """When update_baseline raises, run_suite_from_path returns results normally."""
        suite_file, output_dir = _write_suite(tmp_path)

        mock_svc_result = {
            "total_rows": 5,
            "error_count": 1,
            "warning_count": 0,
        }

        with (
            patch(
                "src.services.validate_service.run_validate_service",
                return_value=mock_svc_result,
            ),
            patch("src.commands.run_tests_command._append_run_history"),
            patch(
                "src.commands.run_tests_command.update_baseline",
                side_effect=RuntimeError("baseline exploded"),
            ),
            patch(
                "src.utils.archive.ArchiveManager.archive_run",
                return_value=tmp_path / "archive",
            ),
        ):
            from src.commands.run_tests_command import run_suite_from_path

            results = run_suite_from_path(
                suite_path=str(suite_file),
                params={},
                env="test",
                output_dir=str(output_dir),
            )

        # Suite run must have returned results despite baseline failure
        assert isinstance(results, list)
        assert len(results) == 1

    def test_update_baseline_receives_result_dict_with_counts(self, tmp_path: Path):
        """The result dict passed to update_baseline contains pass_count / total_count."""
        suite_file, output_dir = _write_suite(tmp_path)

        mock_svc_result = {
            "total_rows": 20,
            "error_count": 2,
            "warning_count": 0,
        }

        captured_args: list = []

        def _capture_baseline(suite_name, result):
            captured_args.append((suite_name, result))

        with (
            patch(
                "src.services.validate_service.run_validate_service",
                return_value=mock_svc_result,
            ),
            patch("src.commands.run_tests_command._append_run_history"),
            patch(
                "src.commands.run_tests_command.update_baseline",
                side_effect=_capture_baseline,
            ),
            patch(
                "src.utils.archive.ArchiveManager.archive_run",
                return_value=tmp_path / "archive",
            ),
        ):
            from src.commands.run_tests_command import run_suite_from_path

            run_suite_from_path(
                suite_path=str(suite_file),
                params={},
                env="test",
                output_dir=str(output_dir),
            )

        assert len(captured_args) == 1
        suite_name, result_dict = captured_args[0]
        assert suite_name == "Wiring Suite"
        # Must contain aggregate counts so baseline_service can compute pass_rate
        assert "pass_count" in result_dict
        assert "total_count" in result_dict

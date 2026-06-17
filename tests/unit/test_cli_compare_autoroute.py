"""Tests for the ``valdo compare`` CLI size-based auto-routing to chunked (S18-5, #423).

The API already auto-routes large keyed compares to the chunked comparator
(``files.py``); the CLI previously required ``--use-chunked`` manually. These
tests verify the CLI now mirrors the API's 50 MB threshold while keeping
``--use-chunked`` / ``--no-chunked`` as explicit overrides.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from click.testing import CliRunner

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.main import cli  # noqa: E402


def _make_keyed_pair(tmp_path: Path) -> tuple[str, str]:
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("id|value\n1|A\n2|B\n", encoding="utf-8")
    f2.write_text("id|value\n1|A\n2|B\n", encoding="utf-8")
    return str(f1), str(f2)


class TestCliAutoRoute:
    def test_large_file_auto_routes_to_chunked(self, tmp_path):
        """A large keyed input routes to chunked without --use-chunked."""
        f1, f2 = _make_keyed_pair(tmp_path)
        with patch(
            "src.commands.compare_command.should_use_chunked", return_value=True
        ), patch("src.commands.compare_command.run_compare_service") as mock_svc:
            mock_svc.return_value = {
                "total_rows_file1": 2, "total_rows_file2": 2, "matching_rows": 2,
                "only_in_file1": [], "only_in_file2": [], "differences": [],
                "rows_with_differences": 0, "only_in_file1_count": 0,
                "only_in_file2_count": 0,
            }
            result = CliRunner().invoke(
                cli, ["compare", "-f1", f1, "-f2", f2, "-k", "id", "--no-progress"]
            )
        assert result.exit_code == 0, result.output
        assert mock_svc.call_args[1]["use_chunked"] is True

    def test_small_file_stays_non_chunked(self, tmp_path):
        """A small keyed input stays non-chunked when no flag is given."""
        f1, f2 = _make_keyed_pair(tmp_path)
        with patch(
            "src.commands.compare_command.should_use_chunked", return_value=False
        ), patch("src.commands.compare_command.run_compare_service") as mock_svc:
            mock_svc.return_value = {
                "total_rows_file1": 2, "total_rows_file2": 2, "matching_rows": 2,
                "only_in_file1": [], "only_in_file2": [], "differences": [],
                "rows_with_differences": 0, "only_in_file1_count": 0,
                "only_in_file2_count": 0,
            }
            result = CliRunner().invoke(
                cli, ["compare", "-f1", f1, "-f2", f2, "-k", "id", "--no-progress"]
            )
        assert result.exit_code == 0, result.output
        assert mock_svc.call_args[1]["use_chunked"] is False

    def test_explicit_no_chunked_overrides_large(self, tmp_path):
        """--no-chunked forces non-chunked even for a large file."""
        f1, f2 = _make_keyed_pair(tmp_path)
        with patch(
            "src.commands.compare_command.should_use_chunked", return_value=True
        ), patch("src.commands.compare_command.run_compare_service") as mock_svc:
            mock_svc.return_value = {
                "total_rows_file1": 2, "total_rows_file2": 2, "matching_rows": 2,
                "only_in_file1": [], "only_in_file2": [], "differences": [],
                "rows_with_differences": 0, "only_in_file1_count": 0,
                "only_in_file2_count": 0,
            }
            result = CliRunner().invoke(
                cli,
                ["compare", "-f1", f1, "-f2", f2, "-k", "id",
                 "--no-chunked", "--no-progress"],
            )
        assert result.exit_code == 0, result.output
        assert mock_svc.call_args[1]["use_chunked"] is False

    def test_explicit_use_chunked_overrides_small(self, tmp_path):
        """--use-chunked forces chunked even for a small file."""
        f1, f2 = _make_keyed_pair(tmp_path)
        with patch(
            "src.commands.compare_command.should_use_chunked", return_value=False
        ), patch("src.commands.compare_command.run_compare_service") as mock_svc:
            mock_svc.return_value = {
                "total_rows_file1": 2, "total_rows_file2": 2, "matching_rows": 2,
                "only_in_file1": [], "only_in_file2": [], "differences": [],
                "rows_with_differences": 0, "only_in_file1_count": 0,
                "only_in_file2_count": 0,
            }
            result = CliRunner().invoke(
                cli,
                ["compare", "-f1", f1, "-f2", f2, "-k", "id",
                 "--use-chunked", "--no-progress"],
            )
        assert result.exit_code == 0, result.output
        assert mock_svc.call_args[1]["use_chunked"] is True

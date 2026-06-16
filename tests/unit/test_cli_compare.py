"""Unit tests for the ``valdo compare`` CLI exit-code contract.

Tracks S8-1 / #392 — prior to this fix the command always returned exit code
0, even when the two files differed. Tests cover:

* Differing files (rows missing OR field-level differences) → exit code 1.
* Matching files → exit code 0.
* Differences within ``--thresholds`` → exit code 0.
* Differences outside ``--thresholds`` → exit code 1.

The tests drive the real ``run_compare_service`` against the bundled
``templates/etl/csv_file_comparison_sample`` fixture pair (row-by-row mode),
a known-divergent CSV pair where ``CUST000004``'s EMAIL differs,
``CUST000005`` is only in left, and ``CUST000006`` is only in right.
Row-by-row mode is used because keyed mode currently raises an unrelated
``IndexError`` for these fixtures (out of scope for S8-1).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from click.testing import CliRunner

# Make sure src is importable when run via ``pytest tests/unit/...``.
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.main import cli  # noqa: E402

# Known-divergent CSV pair shipped with the repo (Sprint 6 reconciliation
# template). Using a real fixture avoids hand-rolled csv strings and ensures
# the exit-code contract survives changes to the sample.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SAMPLE_DIR = REPO_ROOT / "templates" / "etl" / "csv_file_comparison_sample"
LEFT_CSV = SAMPLE_DIR / "left.csv"
RIGHT_CSV = SAMPLE_DIR / "right.csv"


def _invoke(runner: CliRunner, args: list[str]):
    """Invoke the ``compare`` subcommand via Click's test runner."""
    return runner.invoke(cli, ["compare"] + args)


class TestCompareExitCodeOnDifferences:
    """AC #1 — differing files exit non-zero (S8-1, #392)."""

    def test_divergent_csv_exits_one_without_thresholds(self):
        """left.csv vs right.csv has rows-only-in-each + a field diff → exit 1."""
        runner = CliRunner()
        result = _invoke(runner, [
            "--file1", str(LEFT_CSV),
            "--file2", str(RIGHT_CSV),
            "--no-progress",
        ])

        # mix_stderr=True by default → diagnostic output is in result.output
        assert result.exit_code == 1, (
            f"Expected exit 1 on divergent files; got {result.exit_code}.\n"
            f"Output:\n{result.output}"
        )


class TestCompareExitCodeOnMatch:
    """AC #2 — matching files exit zero."""

    def test_identical_csv_exits_zero(self):
        """Comparing a file against itself yields zero differences → exit 0."""
        runner = CliRunner()
        result = _invoke(runner, [
            "--file1", str(LEFT_CSV),
            "--file2", str(LEFT_CSV),
            "--no-progress",
        ])

        assert result.exit_code == 0, (
            f"Expected exit 0 on identical files; got {result.exit_code}.\n"
            f"Output:\n{result.output}"
        )


class TestCompareExitCodeWithThresholds:
    """AC #3 — ``--thresholds`` governs the pass/fail decision."""

    def _write_thresholds(self, tmp_path: Path, *, lenient: bool) -> Path:
        """Write a thresholds JSON file.

        Args:
            tmp_path: pytest tmp dir.
            lenient: When True the band is wide enough to absorb the sample
                pair's differences (1 missing, 1 extra, 1 row diff). When
                False the band is tight so the evaluator fails.
        """
        if lenient:
            # Wide bands: up to 10 missing/extra/diff rows allowed.
            cfg = {
                "thresholds": {
                    "missing_rows": {
                        "name": "Missing Rows",
                        "metric": "missing_rows",
                        "max_value": 10,
                    },
                    "extra_rows": {
                        "name": "Extra Rows",
                        "metric": "extra_rows",
                        "max_value": 10,
                    },
                    "different_rows": {
                        "name": "Different Rows",
                        "metric": "different_rows",
                        "max_value": 10,
                    },
                }
            }
        else:
            # Zero-tolerance band: any miss/extra/diff fails.
            cfg = {
                "thresholds": {
                    "missing_rows": {
                        "name": "Missing Rows",
                        "metric": "missing_rows",
                        "max_value": 0,
                    },
                    "extra_rows": {
                        "name": "Extra Rows",
                        "metric": "extra_rows",
                        "max_value": 0,
                    },
                    "different_rows": {
                        "name": "Different Rows",
                        "metric": "different_rows",
                        "max_value": 0,
                    },
                }
            }
        path = tmp_path / ("lenient.json" if lenient else "strict.json")
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return path

    def test_within_thresholds_exits_zero(self, tmp_path):
        """Differences exist but fall within tolerance bands → exit 0."""
        thresholds = self._write_thresholds(tmp_path, lenient=True)

        runner = CliRunner()
        result = _invoke(runner, [
            "--file1", str(LEFT_CSV),
            "--file2", str(RIGHT_CSV),
            "--thresholds", str(thresholds),
            "--no-progress",
        ])

        assert result.exit_code == 0, (
            "Expected exit 0 when diffs are inside the threshold band; "
            f"got {result.exit_code}.\nOutput:\n{result.output}"
        )

    def test_outside_thresholds_exits_one(self, tmp_path):
        """Differences exceed zero-tolerance bands → exit 1."""
        thresholds = self._write_thresholds(tmp_path, lenient=False)

        runner = CliRunner()
        result = _invoke(runner, [
            "--file1", str(LEFT_CSV),
            "--file2", str(RIGHT_CSV),
            "--thresholds", str(thresholds),
            "--no-progress",
        ])

        assert result.exit_code == 1, (
            "Expected exit 1 when diffs exceed the threshold band; "
            f"got {result.exit_code}.\nOutput:\n{result.output}"
        )

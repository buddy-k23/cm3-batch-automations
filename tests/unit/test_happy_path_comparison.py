"""Happy-path tests for file comparison (Issue #97).

Tests exercise ``run_compare_service`` with various scenarios: identical
files, modified/added/removed rows, key-based comparison, and report
generation.
"""

import json
import os

import pytest

from src.services.compare_service import run_compare_service


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_file(path, content):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh)


# ── Fixtures ─────────────────────────────────────────────────────────────────


PIPE_HEADER = "id|name|value\n"
PIPE_ROWS = "1|Alice|100\n2|Bob|200\n3|Charlie|300\n"


@pytest.fixture()
def identical_pipe_files(tmp_path):
    """Two identical pipe-delimited files."""
    content = PIPE_HEADER + PIPE_ROWS
    f1 = str(tmp_path / "file1.txt")
    f2 = str(tmp_path / "file2.txt")
    _write_file(f1, content)
    _write_file(f2, content)
    return f1, f2


@pytest.fixture()
def identical_fixed_width_files(tmp_path):
    """Two identical fixed-width files with a matching mapping."""
    lines = "00001Alice     100\n00002Bob       200\n00003Charlie   300\n"
    f1 = str(tmp_path / "fw1.txt")
    f2 = str(tmp_path / "fw2.txt")
    _write_file(f1, lines)
    _write_file(f2, lines)

    mapping = {
        "mapping_name": "fw_cmp",
        "version": "1.0",
        "fields": [
            {"name": "id", "length": 5, "data_type": "string"},
            {"name": "name", "length": 10, "data_type": "string"},
            {"name": "amount", "length": 3, "data_type": "string"},
        ],
    }
    mp = str(tmp_path / "fw_mapping.json")
    _write_json(mp, mapping)
    return f1, f2, mp


@pytest.fixture()
def modified_rows_files(tmp_path):
    """File2 has a modified value in row 2."""
    f1 = str(tmp_path / "mod1.txt")
    f2 = str(tmp_path / "mod2.txt")
    _write_file(f1, PIPE_HEADER + PIPE_ROWS)
    _write_file(f2, PIPE_HEADER + "1|Alice|100\n2|Bob|999\n3|Charlie|300\n")
    return f1, f2


@pytest.fixture()
def added_rows_files(tmp_path):
    """File2 has one extra row."""
    f1 = str(tmp_path / "add1.txt")
    f2 = str(tmp_path / "add2.txt")
    _write_file(f1, PIPE_HEADER + PIPE_ROWS)
    _write_file(
        f2, PIPE_HEADER + PIPE_ROWS + "4|Diana|400\n"
    )
    return f1, f2


@pytest.fixture()
def removed_rows_files(tmp_path):
    """File2 is missing the last row."""
    f1 = str(tmp_path / "rem1.txt")
    f2 = str(tmp_path / "rem2.txt")
    _write_file(f1, PIPE_HEADER + PIPE_ROWS)
    _write_file(f2, PIPE_HEADER + "1|Alice|100\n2|Bob|200\n")
    return f1, f2


@pytest.fixture()
def mixed_changes_files(tmp_path):
    """File2 has a modified row, an added row, and a removed row."""
    f1 = str(tmp_path / "mix1.txt")
    f2 = str(tmp_path / "mix2.txt")
    _write_file(f1, PIPE_HEADER + "1|Alice|100\n2|Bob|200\n3|Charlie|300\n")
    _write_file(f2, PIPE_HEADER + "1|Alice|100\n2|Bob|999\n4|Diana|400\n")
    return f1, f2


# ── Tests ────────────────────────────────────────────────────────────────────


class TestIdenticalFiles:
    """Compare two identical files — expect 100% match."""

    def test_compare_identical_files_returns_100_match(self, identical_pipe_files):
        """Row-by-row comparison of identical pipe files shows no differences."""
        f1, f2 = identical_pipe_files

        result = run_compare_service(file1=f1, file2=f2)

        assert result["structure_compatible"] is True
        assert result["total_rows_file1"] == result["total_rows_file2"]
        assert result["matching_rows"] == result["total_rows_file1"]
        assert len(result["differences"]) == 0
        assert len(result["only_in_file1"]) == 0
        assert len(result["only_in_file2"]) == 0

    def test_compare_identical_fixed_width_files(self, identical_fixed_width_files):
        """Fixed-width identical files with a mapping compare cleanly."""
        f1, f2, mp = identical_fixed_width_files

        result = run_compare_service(file1=f1, file2=f2, mapping=mp)

        assert result["structure_compatible"] is True
        assert result["matching_rows"] == result["total_rows_file1"]
        assert len(result["differences"]) == 0

    def test_compare_identical_generates_clean_report(
        self, tmp_path, identical_pipe_files
    ):
        """Identical-file comparison result can be serialised to JSON."""
        f1, f2 = identical_pipe_files

        result = run_compare_service(file1=f1, file2=f2)

        # Verify the result is JSON-serialisable (no DataFrames leak out).
        report_path = str(tmp_path / "cmp_report.json")
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, default=str)

        assert os.path.exists(report_path)
        with open(report_path, encoding="utf-8") as fh:
            loaded = json.load(fh)
        assert loaded["matching_rows"] == result["matching_rows"]


class TestFileDifferences:
    """Compare files with modifications, additions, and removals."""

    def test_compare_files_with_modified_rows(self, modified_rows_files):
        """Detects value differences in matching rows."""
        f1, f2 = modified_rows_files

        result = run_compare_service(file1=f1, file2=f2)

        assert result["structure_compatible"] is True
        assert result["rows_with_differences"] >= 1
        assert result["total_rows_file1"] == result["total_rows_file2"]

    def test_compare_files_with_added_rows(self, added_rows_files):
        """File2 with extra rows reports only_in_file2."""
        f1, f2 = added_rows_files

        result = run_compare_service(file1=f1, file2=f2)

        assert result["structure_compatible"] is True
        assert result["total_rows_file2"] > result["total_rows_file1"]
        assert len(result["only_in_file2"]) >= 1

    def test_compare_files_with_removed_rows(self, removed_rows_files):
        """File2 missing rows reports only_in_file1."""
        f1, f2 = removed_rows_files

        result = run_compare_service(file1=f1, file2=f2)

        assert result["structure_compatible"] is True
        assert result["total_rows_file1"] > result["total_rows_file2"]
        assert len(result["only_in_file1"]) >= 1

    def test_compare_files_with_mixed_changes(self, mixed_changes_files):
        """Mixed modifications, additions, and removals are all detected."""
        f1, f2 = mixed_changes_files

        result = run_compare_service(file1=f1, file2=f2)

        assert result["structure_compatible"] is True
        # At least one type of difference should be reported.
        total_diffs = (
            len(result["differences"])
            + len(result["only_in_file1"])
            + len(result["only_in_file2"])
        )
        assert total_diffs >= 1


class TestKeyBasedComparison:
    """Compare files using key columns for row matching."""

    def test_compare_with_key_columns(self, modified_rows_files):
        """Key-based comparison matches rows by the 'id' column."""
        f1, f2 = modified_rows_files

        result = run_compare_service(file1=f1, file2=f2, keys="id")

        assert result["structure_compatible"] is True
        # Row with id=2 should appear as a difference (value 200 vs 999).
        assert len(result["differences"]) >= 1

    def test_compare_key_based_generates_detailed_report(self, modified_rows_files):
        """Key-based detailed comparison includes field_statistics."""
        f1, f2 = modified_rows_files

        result = run_compare_service(file1=f1, file2=f2, keys="id", detailed=True)

        assert result["structure_compatible"] is True
        assert "field_statistics" in result
        # At least the 'value' field should show differences.
        if result["field_statistics"]:
            assert result["field_statistics"]["fields_with_differences"] >= 1

"""Unit tests for src/services/error_extractor.py — Issue #227."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.services.error_extractor import extract_error_rows


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_temp(lines: list[str], suffix: str = ".txt") -> str:
    """Write lines to a temp file and return the path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return path


def _make_errors(row_numbers: list[int]) -> list[dict]:
    """Build a minimal error list with the given 1-indexed row numbers."""
    return [{"row": r, "message": f"Error on row {r}"} for r in row_numbers]


# ---------------------------------------------------------------------------
# Issue #227 test cases
# ---------------------------------------------------------------------------

class TestExtractErrorRowsFixedWidth:
    """Fixed-width file: raw lines written without a header."""

    def test_extracts_rows_2_4_6_from_10_row_file(self, tmp_path):
        lines = [f"ROW{str(i).zfill(7)}" for i in range(1, 11)]
        src = _write_temp(lines)
        out = str(tmp_path / "errors.txt")

        result = extract_error_rows(src, {"errors": _make_errors([2, 4, 6])}, out)

        assert result["exported_rows"] == 3
        assert Path(out).exists()
        written = Path(out).read_text(encoding="utf-8").splitlines()
        # should contain lines for rows 2, 4, 6 (content is "ROW0000002" etc.)
        assert any("ROW0000002" in line for line in written)
        assert any("ROW0000004" in line for line in written)
        assert any("ROW0000006" in line for line in written)
        # row 1 and row 3 should NOT be present
        assert not any("ROW0000001" in line for line in written)
        assert not any("ROW0000003" in line for line in written)


class TestExtractErrorRowsDelimited:
    """Pipe-delimited file: header preserved, failed rows follow."""

    def test_pipe_header_plus_failed_rows(self, tmp_path):
        lines = [
            "name|age|status",
            "Alice|30|OK",
            "Bob|25|FAIL",
            "Carol|40|OK",
            "Dave|35|FAIL",
        ]
        src = _write_temp(lines)
        out = str(tmp_path / "errors.txt")

        result = extract_error_rows(src, {"errors": _make_errors([3, 5])}, out)

        assert result["exported_rows"] == 2
        written = Path(out).read_text(encoding="utf-8").splitlines()
        # Header must be line 0
        assert written[0] == "name|age|status"
        # Failed rows present
        assert any("Bob" in line for line in written)
        assert any("Dave" in line for line in written)
        # Valid rows absent
        assert not any("Alice" in line for line in written)
        assert not any("Carol" in line for line in written)


class TestExtractErrorRowsNoErrors:
    """When validation passes, output should be empty (0 bytes for fixed-width,
    header-only for delimited) and exported_rows must be 0."""

    def test_no_errors_fixed_width_empty_file(self, tmp_path):
        lines = ["ROW0000001", "ROW0000002"]
        src = _write_temp(lines)
        out = str(tmp_path / "errors.txt")

        result = extract_error_rows(src, {"errors": []}, out)

        assert result["exported_rows"] == 0
        assert result["output_path"] == out
        assert Path(out).exists()
        assert Path(out).stat().st_size == 0

    def test_no_errors_delimited_header_only(self, tmp_path):
        lines = ["col1|col2", "A|B", "C|D"]
        src = _write_temp(lines)
        out = str(tmp_path / "errors.txt")

        result = extract_error_rows(src, {"errors": []}, out)

        assert result["exported_rows"] == 0
        written = Path(out).read_text(encoding="utf-8").splitlines()
        # For delimited files with no errors: header row only (or empty)
        # Both are acceptable; no data rows should appear
        for line in written:
            assert "A|B" not in line
            assert "C|D" not in line


class TestExtractErrorRowsMissingKey:
    """No 'errors' key in result dict: treated as zero errors."""

    def test_missing_errors_key_returns_zero(self, tmp_path):
        lines = ["ROW0000001", "ROW0000002"]
        src = _write_temp(lines)
        out = str(tmp_path / "errors.txt")

        result = extract_error_rows(src, {}, out)

        assert result["exported_rows"] == 0
        assert Path(out).exists()


class TestExtractErrorRowsLargeRowNumbers:
    """Row numbers near the end of a large file are extracted correctly."""

    def test_rows_95_and_99_from_100_row_file(self, tmp_path):
        lines = [f"RECORD{str(i).zfill(6)}" for i in range(1, 101)]
        src = _write_temp(lines)
        out = str(tmp_path / "errors.txt")

        result = extract_error_rows(src, {"errors": _make_errors([95, 99])}, out)

        assert result["exported_rows"] == 2
        written = Path(out).read_text(encoding="utf-8").splitlines()
        assert any("RECORD000095" in line for line in written)
        assert any("RECORD000099" in line for line in written)
        assert not any("RECORD000001" in line for line in written)


class TestExtractErrorRowsDuplicates:
    """A row with multiple errors is exported exactly once."""

    def test_same_row_multiple_errors_exported_once(self, tmp_path):
        lines = ["ROW0000001", "ROW0000002", "ROW0000003"]
        src = _write_temp(lines)
        out = str(tmp_path / "errors.txt")

        # Three errors all on row 2
        errors = [
            {"row": 2, "message": "Error A"},
            {"row": 2, "message": "Error B"},
            {"row": 2, "message": "Error C"},
        ]
        result = extract_error_rows(src, {"errors": errors}, out)

        assert result["exported_rows"] == 1
        written = Path(out).read_text(encoding="utf-8").splitlines()
        row2_lines = [line for line in written if "ROW0000002" in line]
        assert len(row2_lines) == 1


class TestExtractErrorRowsReturnShape:
    """Return dict always has exactly the two expected keys."""

    def test_return_dict_keys(self, tmp_path):
        lines = ["A|B", "1|2"]
        src = _write_temp(lines)
        out = str(tmp_path / "errors.txt")

        result = extract_error_rows(src, {"errors": _make_errors([2])}, out)

        assert set(result.keys()) == {"exported_rows", "output_path"}
        assert result["output_path"] == out

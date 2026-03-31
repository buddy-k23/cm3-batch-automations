"""Unit tests for src/services/drift_detector.py — delimited drift detection.

Covers _detect_delimited_drift() and the public detect_drift() entry point.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from src.services.drift_detector import _detect_delimited_drift, detect_drift


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _csv_mapping(*names: str) -> dict:
    """Build a minimal mapping dict for CSV drift detection.

    Args:
        names: Field names to include.

    Returns:
        Mapping dict with 'fields' list and format='csv'.
    """
    return {
        "format": "csv",
        "fields": [{"name": n} for n in names],
    }


def _pipe_mapping(*names: str) -> dict:
    """Build a minimal mapping dict for pipe-delimited drift detection.

    Args:
        names: Field names to include.

    Returns:
        Mapping dict with 'fields' list and format='pipe'.
    """
    return {
        "format": "pipe",
        "fields": [{"name": n} for n in names],
    }


def _fixed_mapping(*field_defs: tuple) -> dict:
    """Build a minimal fixed-width mapping dict.

    Args:
        field_defs: Tuples of (name, position_1indexed, length).

    Returns:
        Mapping dict with 'fields' list and format='fixed'.
    """
    return {
        "format": "fixed",
        "fields": [
            {"name": name, "position": pos, "length": length}
            for name, pos, length in field_defs
        ],
    }


def _write_tempfile(content: str) -> str:
    """Write content to a temporary file and return its path.

    Args:
        content: Text content to write.

    Returns:
        Absolute path to the created temp file.
    """
    fd, path = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w") as f:
        f.write(content)
    return path


# ---------------------------------------------------------------------------
# _detect_delimited_drift — CSV with header row (matching)
# ---------------------------------------------------------------------------


class TestDelimitedDriftCSVClean:
    """File headers match the mapping exactly — no drift expected."""

    def test_csv_matching_headers_no_drift(self):
        """CSV header row matches expected field names exactly."""
        lines = ["name,age,city\n", "Alice,30,NYC\n", "Bob,25,LA\n"]
        mapping = _csv_mapping("name", "age", "city")
        result = _detect_delimited_drift(lines, mapping, ",")

        assert result["drifted"] is False
        assert result["fields"] == []
        assert "skipped" not in result

    def test_csv_extra_data_rows_ignored(self):
        """Only header row is checked; data rows do not affect drift result."""
        lines = [
            "id,value\n",
            "1,100\n",
            "2,200\n",
            "3,300\n",
        ]
        mapping = _csv_mapping("id", "value")
        result = _detect_delimited_drift(lines, mapping, ",")

        assert result["drifted"] is False


# ---------------------------------------------------------------------------
# _detect_delimited_drift — CSV with drift (renamed / missing columns)
# ---------------------------------------------------------------------------


class TestDelimitedDriftCSVDrift:
    """File headers diverge from mapping — drift should be detected."""

    def test_csv_renamed_column_is_error(self):
        """A mapped column that does not appear in the header → severity='error'."""
        # Mapping expects 'customer_id', file has 'cust_id'
        lines = ["cust_id,name\n", "1,Alice\n", "2,Bob\n"]
        mapping = _csv_mapping("customer_id", "name")
        result = _detect_delimited_drift(lines, mapping, ",")

        assert result["drifted"] is True
        names = [f["name"] for f in result["fields"]]
        assert "customer_id" in names
        missing = next(f for f in result["fields"] if f["name"] == "customer_id")
        assert missing["severity"] == "error"
        assert missing["reason"] == "column_missing"

    def test_csv_extra_unknown_column_is_warning(self):
        """A column in the file not present in the mapping → severity='warning'."""
        # Mapping expects 'id', 'name'; file also has 'extra_col'
        lines = ["id,name,extra_col\n", "1,Alice,X\n"]
        mapping = _csv_mapping("id", "name")
        result = _detect_delimited_drift(lines, mapping, ",")

        assert result["drifted"] is True
        extra = next(
            (f for f in result["fields"] if f["name"] == "extra_col"), None
        )
        assert extra is not None
        assert extra["severity"] == "warning"
        assert extra["reason"] == "unexpected_column"

    def test_csv_both_missing_and_extra_column(self):
        """Combination: one column missing (error) + one unexpected (warning)."""
        lines = ["id,unexpected\n", "1,X\n"]
        mapping = _csv_mapping("id", "expected_col")
        result = _detect_delimited_drift(lines, mapping, ",")

        assert result["drifted"] is True
        reasons = {f["reason"] for f in result["fields"]}
        assert "column_missing" in reasons
        assert "unexpected_column" in reasons

    def test_csv_missing_field_has_none_positions(self):
        """Drifted field entries for missing/unexpected columns have None position values."""
        lines = ["wrong_col\n", "data\n"]
        mapping = _csv_mapping("correct_col")
        result = _detect_delimited_drift(lines, mapping, ",")

        assert result["drifted"] is True
        field = result["fields"][0]
        assert field["expected_start"] is None
        assert field["expected_length"] is None
        assert field["actual_start"] is None
        assert field["actual_length"] is None


# ---------------------------------------------------------------------------
# _detect_delimited_drift — no header (column count comparison)
# ---------------------------------------------------------------------------


class TestDelimitedDriftNoHeader:
    """Files with no header row — drift is detected via column count mismatch."""

    def test_column_count_mismatch_is_drift(self):
        """File has 3 columns but mapping expects 5 → drift with reason='column_count_mismatch'."""
        lines = ["1,2,3\n", "4,5,6\n"]
        mapping = {
            "fields": [{"name": f"col{i}"} for i in range(5)],
        }
        result = _detect_delimited_drift(lines, mapping, ",")

        assert result["drifted"] is True
        field = result["fields"][0]
        assert field["name"] == "_column_count"
        assert field["reason"] == "column_count_mismatch"
        assert field["expected_start"] == 5   # expected_count stored in expected_start
        assert field["actual_start"] == 3     # actual_count stored in actual_start
        assert field["severity"] == "error"

    def test_column_count_match_no_drift(self):
        """File has same column count as mapping — no drift."""
        lines = ["1,2,3\n", "4,5,6\n"]
        mapping = {
            "fields": [{"name": f"col{i}"} for i in range(3)],
        }
        result = _detect_delimited_drift(lines, mapping, ",")

        assert result["drifted"] is False
        assert result["fields"] == []


# ---------------------------------------------------------------------------
# _detect_delimited_drift — pipe delimiter
# ---------------------------------------------------------------------------


class TestDelimitedDriftPipe:
    """Same tests as CSV but using pipe as delimiter."""

    def test_pipe_matching_headers_no_drift(self):
        """Pipe-delimited file with matching headers → no drift."""
        lines = ["id|name|status\n", "1|Alice|ACTIVE\n"]
        mapping = _pipe_mapping("id", "name", "status")
        result = _detect_delimited_drift(lines, mapping, "|")

        assert result["drifted"] is False

    def test_pipe_missing_column_is_error(self):
        """Pipe file missing a mapped column → error severity."""
        lines = ["id|name\n", "1|Alice\n"]
        mapping = _pipe_mapping("id", "name", "status")
        result = _detect_delimited_drift(lines, mapping, "|")

        assert result["drifted"] is True
        names = [f["name"] for f in result["fields"]]
        assert "status" in names


# ---------------------------------------------------------------------------
# _detect_delimited_drift — skip / edge cases
# ---------------------------------------------------------------------------


class TestDelimitedDriftSkip:
    """Inputs that trigger early-exit skip conditions."""

    def test_empty_lines_returns_skipped(self):
        """Zero non-blank lines → skipped with reason='too_short'."""
        mapping = _csv_mapping("col1")
        result = _detect_delimited_drift([], mapping, ",")

        assert result["drifted"] is False
        assert result["skipped"] is True
        assert result["reason"] == "too_short"

    def test_all_blank_lines_returns_skipped(self):
        """Lines containing only whitespace → skipped."""
        lines = ["   \n", "\n", "  \n"]
        mapping = _csv_mapping("col1")
        result = _detect_delimited_drift(lines, mapping, ",")

        assert result["drifted"] is False
        assert result["skipped"] is True
        assert result["reason"] == "too_short"

    def test_no_fields_in_mapping_returns_skipped(self):
        """Empty fields list in mapping → skipped with reason='no_fields'."""
        lines = ["col1,col2\n", "a,b\n"]
        result = _detect_delimited_drift(lines, {"fields": []}, ",")

        assert result["drifted"] is False
        assert result["skipped"] is True
        assert result["reason"] == "no_fields"

    def test_missing_fields_key_returns_skipped(self):
        """Mapping with no 'fields' key → skipped."""
        lines = ["col1,col2\n", "a,b\n"]
        result = _detect_delimited_drift(lines, {}, ",")

        assert result["drifted"] is False
        assert result["skipped"] is True
        assert result["reason"] == "no_fields"


# ---------------------------------------------------------------------------
# detect_drift — routing
# ---------------------------------------------------------------------------


class TestDetectDriftRouting:
    """detect_drift() must route to the correct sub-detector based on format."""

    def test_routes_csv_format_to_delimited(self):
        """format='csv' → uses comma delimiter."""
        content = "id,name\n1,Alice\n"
        path = _write_tempfile(content)
        try:
            mapping = _csv_mapping("id", "name")
            result = detect_drift(path, mapping)
            assert result["drifted"] is False
        finally:
            os.unlink(path)

    def test_routes_pipe_format_to_delimited(self):
        """format='pipe' → uses pipe delimiter."""
        content = "id|name\n1|Alice\n"
        path = _write_tempfile(content)
        try:
            mapping = _pipe_mapping("id", "name")
            result = detect_drift(path, mapping)
            assert result["drifted"] is False
        finally:
            os.unlink(path)

    def test_routes_psv_format_to_delimited(self):
        """format='psv' → also uses pipe delimiter."""
        content = "id|name\n1|Alice\n"
        path = _write_tempfile(content)
        try:
            mapping = {"format": "psv", "fields": [{"name": "id"}, {"name": "name"}]}
            result = detect_drift(path, mapping)
            assert result["drifted"] is False
        finally:
            os.unlink(path)

    def test_routes_tsv_format_to_delimited(self):
        """format='tsv' → uses tab delimiter."""
        content = "id\tname\n1\tAlice\n"
        path = _write_tempfile(content)
        try:
            mapping = {"format": "tsv", "fields": [{"name": "id"}, {"name": "name"}]}
            result = detect_drift(path, mapping)
            assert result["drifted"] is False
        finally:
            os.unlink(path)

    def test_routes_fixed_format_to_fixed_width(self):
        """format='fixed' → delegates to _detect_fixed_width_drift."""
        # Build a fixed-width file with enough lines for the fixed-width detector
        line = "HELLO" + " " * 35  # 40 chars
        content = (line + "\n") * 5
        path = _write_tempfile(content)
        try:
            mapping = _fixed_mapping(("FIELD_A", 1, 5))
            result = detect_drift(path, mapping)
            # Just assert it ran without error and returned the expected shape
            assert "drifted" in result
            assert "fields" in result
        finally:
            os.unlink(path)

    def test_routes_empty_format_to_fixed_width(self):
        """format='' (empty string) → falls back to fixed-width detector."""
        line = "HELLO" + " " * 35
        content = (line + "\n") * 5
        path = _write_tempfile(content)
        try:
            mapping = {"format": "", "fields": [{"name": "F", "position": 1, "length": 5}]}
            result = detect_drift(path, mapping)
            assert "drifted" in result
        finally:
            os.unlink(path)

    def test_unsupported_format_returns_skipped(self):
        """Unknown format string → skipped with reason='unsupported_format'."""
        content = "some data\n"
        path = _write_tempfile(content)
        try:
            mapping = {"format": "parquet", "fields": [{"name": "col"}]}
            result = detect_drift(path, mapping)
            assert result["drifted"] is False
            assert result["skipped"] is True
            assert result["reason"] == "unsupported_format"
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# detect_drift — file I/O error cases
# ---------------------------------------------------------------------------


class TestDetectDriftFileErrors:
    """detect_drift() handles missing/unreadable files gracefully."""

    def test_file_not_found_returns_skipped(self):
        """Non-existent path → skipped with reason='file_not_found'."""
        mapping = _csv_mapping("col1")
        result = detect_drift("/nonexistent/path/to/file.csv", mapping)

        assert result["drifted"] is False
        assert result["skipped"] is True
        assert result["reason"] == "file_not_found"

    def test_csv_drift_detected_via_detect_drift(self):
        """detect_drift() correctly propagates drift from the delimited sub-detector."""
        content = "wrong_col,name\n1,Alice\n"
        path = _write_tempfile(content)
        try:
            mapping = _csv_mapping("correct_col", "name")
            result = detect_drift(path, mapping)
            assert result["drifted"] is True
            names = [f["name"] for f in result["fields"]]
            assert "correct_col" in names
        finally:
            os.unlink(path)

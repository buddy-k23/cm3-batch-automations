"""Tests for the infer-mapping service and command."""

from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path

import pytest

from src.services.infer_mapping_service import (
    _count_delimited_columns,
    _detect_fixed_width_boundaries,
    _infer_type,
    infer_mapping,
)


# ---------------------------------------------------------------------------
# Fixtures — temporary sample files
# ---------------------------------------------------------------------------

@pytest.fixture()
def fixed_width_file(tmp_path: Path) -> Path:
    """Create a fixed-width sample file with known boundaries."""
    # Fields: ID(5)  NAME(10)  DATE(8)  AMOUNT(8)
    lines = [
        "00001John      20260101 0001234",
        "00002Jane      20260215 0005678",
        "00003Bob       20251231 0009999",
    ]
    p = tmp_path / "fixed.dat"
    p.write_text("\n".join(lines) + "\n")
    return p


@pytest.fixture()
def pipe_file(tmp_path: Path) -> Path:
    """Create a pipe-delimited sample file."""
    lines = [
        "CUST001|John|20260101|1234.56",
        "CUST002|Jane|20260215|5678.90",
        "CUST003|Bob|20251231|9999.00",
    ]
    p = tmp_path / "pipe.txt"
    p.write_text("\n".join(lines) + "\n")
    return p


@pytest.fixture()
def csv_file(tmp_path: Path) -> Path:
    """Create a CSV sample file."""
    lines = [
        "CUST001,John,20260101,1234.56",
        "CUST002,Jane,20260215,5678.90",
        "CUST003,Bob,20251231,9999.00",
    ]
    p = tmp_path / "data.csv"
    p.write_text("\n".join(lines) + "\n")
    return p


@pytest.fixture()
def tsv_file(tmp_path: Path) -> Path:
    """Create a TSV sample file."""
    lines = [
        "CUST001\tJohn\t20260101\t1234.56",
        "CUST002\tJane\t20260215\t5678.90",
        "CUST003\tBob\t20251231\t9999.00",
    ]
    p = tmp_path / "data.tsv"
    p.write_text("\n".join(lines) + "\n")
    return p


# ---------------------------------------------------------------------------
# Type inference
# ---------------------------------------------------------------------------

class TestInferType:
    """Tests for the _infer_type helper."""

    def test_numeric_values(self):
        assert _infer_type(["123", "456", "789"]) == "number"

    def test_numeric_with_decimal(self):
        assert _infer_type(["12.34", "56.78"]) == "number"

    def test_numeric_with_sign(self):
        assert _infer_type(["+100", "-200", "300"]) == "number"

    def test_date_yyyymmdd(self):
        assert _infer_type(["20260101", "20260215", "20251231"]) == "date"

    def test_string_values(self):
        assert _infer_type(["hello", "world"]) == "string"

    def test_mixed_defaults_to_string(self):
        assert _infer_type(["123", "abc"]) == "string"

    def test_empty_list(self):
        assert _infer_type([]) == "string"

    def test_all_blank_values(self):
        assert _infer_type(["", "  ", ""]) == "string"


# ---------------------------------------------------------------------------
# Fixed-width boundary detection
# ---------------------------------------------------------------------------

class TestFixedWidthBoundaries:
    """Tests for _detect_fixed_width_boundaries."""

    def test_known_boundaries(self):
        lines = [
            "00001 John       20260101",
            "00002 Jane       20260215",
        ]
        boundaries = _detect_fixed_width_boundaries(lines)
        # Should detect at least 3 fields separated by space columns
        assert len(boundaries) >= 3
        # First field starts at 0
        assert boundaries[0][0] == 0

    def test_single_field_no_spaces(self):
        lines = ["ABCDEF", "GHIJKL"]
        boundaries = _detect_fixed_width_boundaries(lines)
        assert boundaries == [(0, 6)]

    def test_empty_lines(self):
        assert _detect_fixed_width_boundaries([]) == []


# ---------------------------------------------------------------------------
# Delimited column counting
# ---------------------------------------------------------------------------

class TestCountDelimitedColumns:
    """Tests for _count_delimited_columns."""

    def test_pipe_columns(self):
        lines = ["a|b|c", "d|e|f", "g|h|i"]
        assert _count_delimited_columns(lines, "|") == 3

    def test_csv_columns(self):
        lines = ["a,b,c,d", "e,f,g,h"]
        assert _count_delimited_columns(lines, ",") == 4

    def test_inconsistent_picks_most_common(self):
        lines = ["a|b|c", "d|e|f", "x|y"]  # 2 lines with 3 cols, 1 with 2
        assert _count_delimited_columns(lines, "|") == 3

    def test_empty_lines(self):
        assert _count_delimited_columns([], "|") == 1


# ---------------------------------------------------------------------------
# Placeholder naming
# ---------------------------------------------------------------------------

class TestPlaceholderNaming:
    """Ensure generated field names follow FIELD_NNN convention."""

    def test_pipe_field_names(self, pipe_file: Path):
        mapping = infer_mapping(str(pipe_file), format="pipe_delimited")
        names = [f["name"] for f in mapping["fields"]]
        assert names == ["FIELD_001", "FIELD_002", "FIELD_003", "FIELD_004"]

    def test_fixed_width_field_names(self, fixed_width_file: Path):
        mapping = infer_mapping(str(fixed_width_file), format="fixed_width")
        for field in mapping["fields"]:
            assert field["name"].startswith("FIELD_")
            assert len(field["name"]) == 9  # FIELD_NNN


# ---------------------------------------------------------------------------
# Full infer_mapping integration
# ---------------------------------------------------------------------------

class TestInferMapping:
    """Integration tests for the infer_mapping service function."""

    def test_pipe_delimited_explicit(self, pipe_file: Path):
        mapping = infer_mapping(str(pipe_file), format="pipe_delimited")

        assert mapping["_inferred"] is True
        assert mapping["_note"] == "DRAFT"
        assert mapping["source"]["format"] == "pipe_delimited"
        assert mapping["source"]["delimiter"] == "|"
        assert len(mapping["fields"]) == 4

    def test_csv_explicit(self, csv_file: Path):
        mapping = infer_mapping(str(csv_file), format="csv")

        assert mapping["source"]["format"] == "csv"
        assert mapping["source"]["delimiter"] == ","
        assert len(mapping["fields"]) == 4

    def test_tsv_explicit(self, tsv_file: Path):
        mapping = infer_mapping(str(tsv_file), format="tsv")

        assert mapping["source"]["format"] == "tsv"
        assert mapping["source"]["delimiter"] == "\t"
        assert len(mapping["fields"]) == 4

    def test_fixed_width_explicit(self, fixed_width_file: Path):
        mapping = infer_mapping(str(fixed_width_file), format="fixed_width")

        assert mapping["source"]["format"] == "fixed_width"
        assert len(mapping["fields"]) >= 1
        # Every field should have position + length for fixed-width
        for field in mapping["fields"]:
            assert "position" in field
            assert "length" in field

    def test_type_inference_in_pipe(self, pipe_file: Path):
        mapping = infer_mapping(str(pipe_file), format="pipe_delimited")
        types = {f["name"]: f["data_type"] for f in mapping["fields"]}
        # FIELD_001 = CUST001 → string
        assert types["FIELD_001"] == "string"
        # FIELD_003 = 20260101 → date
        assert types["FIELD_003"] == "date"
        # FIELD_004 = 1234.56 → number
        assert types["FIELD_004"] == "number"

    def test_metadata_present(self, pipe_file: Path):
        mapping = infer_mapping(str(pipe_file), format="pipe_delimited")
        meta = mapping["metadata"]
        assert meta["created_by"] == "infer_mapping"
        assert "created_date" in meta
        assert meta["sample_lines_analyzed"] == 3

    def test_mapping_name_derived_from_filename(self, pipe_file: Path):
        mapping = infer_mapping(str(pipe_file), format="pipe_delimited")
        assert mapping["mapping_name"] == "pipe_inferred"

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            infer_mapping("/nonexistent/path.txt")

    def test_empty_file(self, tmp_path: Path):
        empty = tmp_path / "empty.txt"
        empty.write_text("")
        with pytest.raises(ValueError, match="empty"):
            infer_mapping(str(empty), format="pipe_delimited")

    def test_output_is_json_serializable(self, pipe_file: Path):
        mapping = infer_mapping(str(pipe_file), format="pipe_delimited")
        # Should not raise
        json.dumps(mapping)


# ---------------------------------------------------------------------------
# Format auto-detection integration
# ---------------------------------------------------------------------------

class TestAutoDetection:
    """Verify that format auto-detection works end-to-end."""

    def test_auto_detect_pipe(self, pipe_file: Path):
        mapping = infer_mapping(str(pipe_file))
        assert mapping["source"]["format"] == "pipe_delimited"

    def test_auto_detect_csv(self, csv_file: Path):
        mapping = infer_mapping(str(csv_file))
        assert mapping["source"]["format"] == "csv"

    def test_auto_detect_fixed_width(self, tmp_path: Path):
        """Fixed-width detection needs >=3 lines with no delimiters."""
        lines = [
            "00001ABCDEFGH20260101",
            "00002IJKLMNOP20260215",
            "00003QRSTUVWX20251231",
        ]
        p = tmp_path / "fw.dat"
        p.write_text("\n".join(lines) + "\n")
        mapping = infer_mapping(str(p))
        assert mapping["source"]["format"] == "fixed_width"

    def test_sample_lines_limit(self, pipe_file: Path):
        mapping = infer_mapping(str(pipe_file), sample_lines=1)
        assert mapping["metadata"]["sample_lines_analyzed"] == 1

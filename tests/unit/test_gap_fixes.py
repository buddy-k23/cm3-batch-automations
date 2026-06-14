"""Failing tests for the three identified gaps.

Gap 1: __source_row__ produces a spurious "Unexpected field" warning
Gap 2: Format detector picks wrong parser for fixed-width files with commas/pipes
Gap 3: X(N) and A(N) COBOL format strings silently pass instead of being validated
"""

import json
import tempfile
from pathlib import Path

import pytest


# ── Gap 1: __source_row__ spurious warning ────────────────────────────────────


class TestSourceRowNoSpuriousWarning:
    """__source_row__ must never appear in validation warnings."""

    def _make_fw_file(self, tmp_path: Path, lines: list[str]) -> Path:
        p = tmp_path / "sample.txt"
        p.write_text("\n".join(lines) + "\n")
        return p

    def _make_mapping(self, tmp_path: Path, fields: list[dict]) -> Path:
        m = tmp_path / "mapping.json"
        m.write_text(json.dumps({"mapping_name": "test", "version": "1.0", "fields": fields}))
        return m

    def test_source_row_not_in_warnings(self, tmp_path):
        """Validate a fixed-width file; __source_row__ must not appear as an unexpected-field warning."""
        lines = ["ALICE     30", "BOB       25"]
        fw = self._make_fw_file(tmp_path, lines)
        mapping = self._make_mapping(tmp_path, [
            {"name": "NAME", "length": 10, "position": 1},
            {"name": "AGE",  "length": 2,  "position": 11},
        ])
        from src.services.validate_service import run_validate_service
        result = run_validate_service(str(fw), mapping=str(mapping))
        warning_messages = [w.get("message", "") for w in result.get("warnings", [])]
        source_row_warnings = [m for m in warning_messages if "__source_row__" in m]
        assert source_row_warnings == [], (
            f"Spurious __source_row__ warning(s) found: {source_row_warnings}"
        )

    def test_source_row_not_counted_in_warning_count(self, tmp_path):
        """warning_count must be 0 for a clean fixed-width file with no real issues."""
        lines = ["ALICE     30", "BOB       25"]
        fw = self._make_fw_file(tmp_path, lines)
        mapping = self._make_mapping(tmp_path, [
            {"name": "NAME", "length": 10, "position": 1},
            {"name": "AGE",  "length": 2,  "position": 11},
        ])
        from src.services.validate_service import run_validate_service
        result = run_validate_service(str(fw), mapping=str(mapping))
        # Only __source_row__ would cause a warning on an otherwise clean file.
        assert result.get("warning_count", 0) == 0, (
            f"Expected 0 warnings but got {result.get('warning_count')}; "
            f"warnings={result.get('warnings', [])}"
        )


# ── Gap 2: Fixed-width file with commas uses correct parser ──────────────────


class TestFixedWidthWithCommasUsesCorrectParser:
    """When mapping specifies fixed-width fields, use FixedWidthParser regardless of file content."""

    def _make_mapping(self, tmp_path: Path) -> Path:
        m = tmp_path / "mapping.json"
        fields = [
            {"name": "ID",    "length": 5,  "position": 1},
            {"name": "LABEL", "length": 10, "position": 6},
            {"name": "NOTES", "length": 15, "position": 16},
        ]
        m.write_text(json.dumps({"mapping_name": "test", "version": "1.0", "fields": fields}))
        return m

    def test_fixed_width_file_with_commas_parsed_correctly(self, tmp_path):
        """A fixed-width file whose data contains commas must not be misidentified as CSV."""
        # Each row is exactly 30 chars; NOTES field contains commas
        lines = [
            "00001WIDGET    item,one,extra ",
            "00002GADGET    item,two,extra ",
            "00003DOOHICKEY item,three,xx  ",
        ]
        fw = tmp_path / "fw_with_commas.txt"
        fw.write_text("\n".join(lines) + "\n")
        mapping = self._make_mapping(tmp_path)

        from src.services.validate_service import run_validate_service
        result = run_validate_service(str(fw), mapping=str(mapping))

        # Should parse as fixed-width: no critical format/parse errors
        critical_errors = [
            e for e in result.get("errors", [])
            if e.get("severity") == "critical"
        ]
        assert critical_errors == [], (
            f"Unexpected critical errors (file misidentified as wrong format): {critical_errors}"
        )
        # And the data_profile should show 3 rows parsed
        assert result.get("data_profile", {}).get("row_count", 0) == 3, (
            f"Expected 3 rows in data_profile but got "
            f"{result.get('data_profile', {}).get('row_count')}; errors={result.get('errors', [])}"
        )

    def test_fixed_width_file_with_pipes_parsed_correctly(self, tmp_path):
        """A fixed-width file whose data contains pipes must not be misidentified as pipe-delimited."""
        # Each row is 20 chars; CODE field contains a pipe char
        lines = [
            "A|001DATA      VALUE1",
            "B|002DATA      VALUE2",
            "C|003DATA      VALUE3",
        ]
        fw = tmp_path / "fw_with_pipes.txt"
        fw.write_text("\n".join(lines) + "\n")

        m = tmp_path / "mapping.json"
        m.write_text(json.dumps({
            "mapping_name": "test", "version": "1.0",
            "fields": [
                {"name": "CODE",  "length": 5,  "position": 1},
                {"name": "DATA",  "length": 10, "position": 6},
                {"name": "VALUE", "length": 6,  "position": 16},
            ]
        }))

        from src.services.validate_service import run_validate_service
        result = run_validate_service(str(fw), mapping=str(m))

        critical_errors = [
            e for e in result.get("errors", [])
            if e.get("severity") == "critical"
        ]
        assert critical_errors == [], (
            f"Unexpected critical errors (file misidentified as wrong format): {critical_errors}"
        )
        assert result.get("data_profile", {}).get("row_count", 0) == 3, (
            f"Expected 3 rows in data_profile but got "
            f"{result.get('data_profile', {}).get('row_count')}; errors={result.get('errors', [])}"
        )


# ── Gap 3: X(N) and A(N) COBOL format validation ─────────────────────────────


class TestCobolFormatXAndA:
    """_is_value_valid_for_format must validate X(N) and A(N) picture strings."""

    def _validator(self, tmp_path: Path):
        """Return a bare EnhancedFileValidator with a dummy parser stub."""
        import types
        from src.parsers.enhanced_validator import EnhancedFileValidator

        stub_parser = types.SimpleNamespace(
            file_path=str(tmp_path / "dummy.txt"),
            validate_format=lambda: True,
            parse=lambda: __import__("pandas").DataFrame(),
            column_specs=[],
        )
        return EnhancedFileValidator(stub_parser)

    # X(N) — alphanumeric, exactly N characters
    def test_X_N_accepts_exact_length_alphanumeric(self, tmp_path):
        v = self._validator(tmp_path)
        assert v._is_value_valid_for_format("ABC123", "X(6)") is True

    def test_X_N_rejects_special_characters(self, tmp_path):
        """X(N) allows alphanumeric and spaces; special chars like @ must be rejected."""
        v = self._validator(tmp_path)
        assert v._is_value_valid_for_format("ABC@@@", "X(6)") is False

    def test_X_N_allows_spaces(self, tmp_path):
        """X(N) allows embedded spaces (common in text fields after stripping)."""
        v = self._validator(tmp_path)
        assert v._is_value_valid_for_format("AB CD", "X(6)") is True

    # A(N) — alpha-only (letters and spaces), length check is by the row-length checker not format
    def test_A_N_accepts_exact_length_alpha(self, tmp_path):
        v = self._validator(tmp_path)
        assert v._is_value_valid_for_format("ABCDEF", "A(6)") is True

    def test_A_N_rejects_digits(self, tmp_path):
        """A(N) must reject values that contain digits."""
        v = self._validator(tmp_path)
        assert v._is_value_valid_for_format("ABC123", "A(6)") is False

    def test_A_N_allows_spaces(self, tmp_path):
        """A(N) allows spaces (stripped text fields may be shorter than declared width)."""
        v = self._validator(tmp_path)
        assert v._is_value_valid_for_format("AB CD", "A(6)") is True

    def test_A_N_rejects_special_characters(self, tmp_path):
        """A(N) must reject values that contain special characters."""
        v = self._validator(tmp_path)
        assert v._is_value_valid_for_format("ABC@@@", "A(6)") is False

    def test_X_N_case_insensitive_format_string(self, tmp_path):
        """Format strings should be handled case-insensitively."""
        v = self._validator(tmp_path)
        assert v._is_value_valid_for_format("ABC123", "x(6)") is True

    def test_A_N_case_insensitive_format_string(self, tmp_path):
        v = self._validator(tmp_path)
        assert v._is_value_valid_for_format("ABCDEF", "a(6)") is True

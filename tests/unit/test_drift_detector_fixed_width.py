"""Unit tests for src/services/drift_detector.py — fixed-width drift detection."""

from __future__ import annotations

import pytest

from src.services.drift_detector import _detect_fixed_width_drift, _find_actual_position


# ---------------------------------------------------------------------------
# Helpers — build fixed-width test lines
# ---------------------------------------------------------------------------

def _make_line(fields: list[tuple[int, str]], total_width: int = 40) -> str:
    """Build a fixed-width line by placing field values at given 0-indexed positions.

    Args:
        fields: List of (start_0idx, value) tuples.
        total_width: Total line width (padded with spaces).

    Returns:
        A string of exactly total_width characters.
    """
    buf = list(" " * total_width)
    for start, value in fields:
        for i, ch in enumerate(value):
            if start + i < total_width:
                buf[start + i] = ch
    return "".join(buf)


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------

def _mapping(*field_defs: tuple[str, int, int]) -> dict:
    """Build a minimal mapping dict for fixed-width drift detection.

    Args:
        field_defs: Tuples of (name, position_1indexed, length).

    Returns:
        Mapping dict with 'fields' list.
    """
    return {
        "fields": [
            {"name": name, "position": pos, "length": length}
            for name, pos, length in field_defs
        ]
    }


# ---------------------------------------------------------------------------
# _detect_fixed_width_drift — happy-path (no drift)
# ---------------------------------------------------------------------------


class TestDetectFixedWidthDriftClean:
    """File content matches the mapping exactly — no drift expected."""

    def test_clean_file_returns_drifted_false(self):
        # Field A: position 1 (0-idx 0), length 5
        # Field B: position 6 (0-idx 5), length 4
        lines = [
            _make_line([(0, "HELLO"), (5, "WXYZ")]) for _ in range(5)
        ]
        mapping = _mapping(("FIELD_A", 1, 5), ("FIELD_B", 6, 4))
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is False
        assert result["fields"] == []
        assert "skipped" not in result

    def test_clean_file_with_numeric_content(self):
        # Field at position 1 (0-idx 0), length 8 filled with digits
        lines = [
            _make_line([(0, "12345678")]) for _ in range(6)
        ]
        mapping = _mapping(("AMOUNT", 1, 8))
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is False

    def test_more_than_20_lines_only_samples_first_20(self):
        """25 matching lines → drifted=False (only first 20 sampled)."""
        lines = [_make_line([(0, "HELLO")]) for _ in range(25)]
        mapping = _mapping(("FIELD_A", 1, 5))
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is False


# ---------------------------------------------------------------------------
# _detect_fixed_width_drift — drift detected (shift left / right)
# ---------------------------------------------------------------------------


class TestDetectFixedWidthDriftShifted:
    """Content is systematically shifted vs. expected positions."""

    def test_shift_right_by_2_detected(self):
        """Field expected at position 1 (0-idx 0) but content starts at 0-idx 2."""
        # Field A: mapping says position=1, length=5.
        # Actual data is at 0-idx 2 (shifted right by 2).
        lines = [
            _make_line([(2, "HELLO")]) for _ in range(5)
        ]
        mapping = _mapping(("FIELD_A", 1, 5))
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is True
        assert len(result["fields"]) >= 1
        field = result["fields"][0]
        assert field["name"] == "FIELD_A"
        assert field["expected_start"] == 1  # 1-indexed as passed in
        assert field["actual_start"] != 1    # shifted

    def test_shift_right_by_6_is_error_severity(self):
        """Offset > 5 bytes → severity='error'."""
        # Field at position 1 (0-idx 0), length 5, but data at 0-idx 7 (offset=7).
        lines = [
            _make_line([(7, "HELLO")]) for _ in range(5)
        ]
        mapping = _mapping(("BIG_SHIFT", 1, 5))
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is True
        drifted_field = result["fields"][0]
        assert drifted_field["severity"] == "error"

    def test_shift_right_by_3_is_warning_severity(self):
        """Offset <= 5 bytes → severity='warning'."""
        # Field at position 1 (0-idx 0), length 5, but data at 0-idx 3 (offset=3).
        lines = [
            _make_line([(3, "HELLO")]) for _ in range(5)
        ]
        mapping = _mapping(("SMALL_SHIFT", 1, 5))
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is True
        drifted_field = result["fields"][0]
        assert drifted_field["severity"] == "warning"

    def test_drifted_field_carries_expected_and_actual_start(self):
        """Result dict carries expected_start (1-indexed) and actual_start (1-indexed)."""
        lines = [
            _make_line([(4, "HELLO")]) for _ in range(5)
        ]
        mapping = _mapping(("F", 1, 5))
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is True
        f = result["fields"][0]
        assert "expected_start" in f
        assert "actual_start" in f
        assert "expected_length" in f
        assert "actual_length" in f

    def test_multiple_fields_both_shifted_detects_both(self):
        """Two fields, both shifted — both appear in drifted_fields."""
        # FIELD_A: mapping pos=1 (0-idx 0), length=5 — data at 0-idx 2
        # FIELD_B: mapping pos=11 (0-idx 10), length=4 — data at 0-idx 13
        lines = [
            _make_line([(2, "HELLO"), (13, "WXYZ")]) for _ in range(5)
        ]
        mapping = _mapping(("FIELD_A", 1, 5), ("FIELD_B", 11, 4))
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is True
        names = [f["name"] for f in result["fields"]]
        assert "FIELD_A" in names
        assert "FIELD_B" in names

    def test_only_one_field_shifted(self):
        """First field matches, second field is shifted — only second flagged."""
        # FIELD_A at pos=1 (0-idx 0), length=5 — correct
        # FIELD_B at pos=8 (0-idx 7), length=4 — data actually at 0-idx 13
        lines = [
            _make_line([(0, "HELLO"), (13, "WXYZ")]) for _ in range(5)
        ]
        mapping = _mapping(("FIELD_A", 1, 5), ("FIELD_B", 8, 4))
        result = _detect_fixed_width_drift(lines, mapping)

        names = [f["name"] for f in result["fields"]]
        assert "FIELD_A" not in names
        assert "FIELD_B" in names


# ---------------------------------------------------------------------------
# _detect_fixed_width_drift — edge / skip cases
# ---------------------------------------------------------------------------


class TestDetectFixedWidthDriftSkip:
    """Inputs that trigger early-exit skip conditions."""

    def test_empty_lines_returns_skipped(self):
        mapping = _mapping(("FIELD_A", 1, 5))
        result = _detect_fixed_width_drift([], mapping)

        assert result["drifted"] is False
        assert result["skipped"] is True
        assert result["reason"] == "too_short"

    def test_fewer_than_3_non_empty_lines_returns_skipped(self):
        lines = ["HELLO" + " " * 15, "WORLD" + " " * 15]
        mapping = _mapping(("FIELD_A", 1, 5))
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is False
        assert result["skipped"] is True
        assert result["reason"] == "too_short"

    def test_exactly_3_non_empty_lines_does_not_skip(self):
        lines = [_make_line([(0, "HELLO")]) for _ in range(3)]
        mapping = _mapping(("FIELD_A", 1, 5))
        result = _detect_fixed_width_drift(lines, mapping)

        assert "skipped" not in result

    def test_blank_lines_ignored_when_counting_sample(self):
        """Blank lines must be excluded from the 3-line minimum check."""
        blank_lines = ["   ", "", "  "]
        content_lines = [_make_line([(0, "HELLO")]) for _ in range(3)]
        lines = blank_lines + content_lines
        mapping = _mapping(("FIELD_A", 1, 5))
        result = _detect_fixed_width_drift(lines, mapping)

        assert "skipped" not in result

    def test_no_fields_in_mapping_returns_skipped(self):
        lines = [_make_line([(0, "HELLO")]) for _ in range(5)]
        result = _detect_fixed_width_drift(lines, {"fields": []})

        assert result["drifted"] is False
        assert result["skipped"] is True
        assert result["reason"] == "no_fields"

    def test_missing_fields_key_returns_skipped(self):
        lines = [_make_line([(0, "HELLO")]) for _ in range(5)]
        result = _detect_fixed_width_drift(lines, {})

        assert result["drifted"] is False
        assert result["skipped"] is True
        assert result["reason"] == "no_fields"

    def test_field_with_zero_length_is_skipped(self):
        """A field with length=0 should be silently ignored (no crash)."""
        lines = [_make_line([(0, "HELLO")]) for _ in range(5)]
        mapping = {"fields": [{"name": "BAD", "position": 1, "length": 0}]}
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is False

    def test_field_without_position_key_is_skipped(self):
        """A field with no 'position' key cannot be drift-checked and is skipped silently."""
        lines = [_make_line([(0, "HELLO")]) for _ in range(5)]
        # Omit 'position' entirely — delimited-format fields have no byte offset
        mapping = {"fields": [{"name": "COL_A", "length": 5}]}
        result = _detect_fixed_width_drift(lines, mapping)

        assert result["drifted"] is False
        assert result["fields"] == []


# ---------------------------------------------------------------------------
# _find_actual_position
# ---------------------------------------------------------------------------


class TestFindActualPosition:
    """Unit tests for the internal position-scanning helper."""

    def test_finds_correct_offset_position(self):
        """Content consistently at 0-idx 5 — should return 5."""
        sample = [_make_line([(5, "HELLO")]) for _ in range(6)]
        pos = _find_actual_position(sample, expected_begin=0, length=5)

        assert pos == 5

    def test_returns_none_when_no_clear_winner(self):
        """All-whitespace lines — no clear non-blank position found."""
        sample = [" " * 30 for _ in range(6)]
        pos = _find_actual_position(sample, expected_begin=0, length=5)

        assert pos is None

    def test_returns_none_when_best_score_below_50_percent(self):
        """Only 2 of 6 lines have content — below 50% threshold."""
        sample = [_make_line([(5, "HELLO")]) if i < 2 else " " * 30 for i in range(6)]
        pos = _find_actual_position(sample, expected_begin=0, length=5)

        assert pos is None

    def test_ignores_expected_begin_position(self):
        """expected_begin itself must not be returned (it was already blank)."""
        # Content at 0 and at 5; expected_begin=5 is excluded from search
        sample = [_make_line([(0, "HELLO")]) for _ in range(6)]
        pos = _find_actual_position(sample, expected_begin=5, length=5)

        # Should find position 0, not 5 (which is excluded)
        assert pos == 0

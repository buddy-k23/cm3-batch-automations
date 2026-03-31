"""Unit tests for detect_discriminator service (Phase A — issue #207).

Tests cover:
- Consistent 3-char codes detected at column 1
- Empty file returns no candidates
- All-unique values returns no candidates
- High confidence for clean file
- max_lines parameter limits inspection
- Position returned as 1-indexed
- Single record type (all same code) returns no candidates
"""

from __future__ import annotations

import pytest

from src.services.multi_record_wizard_service import detect_discriminator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_lines(codes: list[str], width: int = 20) -> str:
    """Build a fixed-width file body with each code at position 0."""
    rows = []
    for code in codes:
        rows.append(code + "X" * (width - len(code)))
    return "\n".join(rows)


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestDetectDiscriminatorHappyPath:
    def test_detects_consistent_3char_code_at_col1(self):
        """3 distinct codes repeating → best candidate has position=1, confidence≥0.8."""
        content = _build_lines(["HDR", "DTL", "DTL", "DTL", "TRL"] * 4)
        result = detect_discriminator(content)
        best = result["best"]
        assert best is not None
        assert best["position"] == 1
        # Values must include the first chars of the codes (or full codes)
        assert len(best["values"]) == 3
        assert best["confidence"] >= 0.8

    def test_confidence_above_threshold_for_clean_file(self):
        """20-line file with 3 repeating codes → confidence ≥ 0.9."""
        codes = (["HDR"] + ["DTL"] * 17 + ["TRL"] + ["TRL"])[:20]
        content = _build_lines(codes)
        result = detect_discriminator(content)
        assert result["best"] is not None
        assert result["best"]["confidence"] >= 0.9

    def test_candidates_list_contains_best(self):
        """candidates list is non-empty and contains the best entry."""
        codes = ["HDR", "DTL", "DTL", "DTL", "TRL"] * 4
        content = _build_lines(codes)
        result = detect_discriminator(content)
        assert len(result["candidates"]) >= 1
        assert result["best"] in result["candidates"]

    def test_candidates_sorted_descending_by_confidence(self):
        """candidates are sorted from highest to lowest confidence."""
        codes = ["HDR", "DTL", "DTL", "TRL"] * 5
        content = _build_lines(codes)
        result = detect_discriminator(content)
        confidences = [c["confidence"] for c in result["candidates"]]
        assert confidences == sorted(confidences, reverse=True)


# ---------------------------------------------------------------------------
# Edge cases — empty / trivial inputs
# ---------------------------------------------------------------------------

class TestDetectDiscriminatorEdgeCases:
    def test_returns_empty_for_empty_file(self):
        """Empty string → candidates=[], best=None."""
        result = detect_discriminator("")
        assert result["candidates"] == []
        assert result["best"] is None

    def test_returns_empty_for_whitespace_only(self):
        """Whitespace-only input → candidates=[], best=None."""
        result = detect_discriminator("   \n   \n")
        assert result["candidates"] == []
        assert result["best"] is None

    def test_returns_empty_when_all_values_unique(self):
        """All unique 1-char codes (A–T) → n_distinct=20 > 8 → no candidates."""
        codes = [chr(65 + i) for i in range(20)]  # "A" through "T"
        content = _build_lines(codes)
        result = detect_discriminator(content)
        assert result["candidates"] == []
        assert result["best"] is None

    def test_single_record_type_file_returns_empty(self):
        """All same code → confidence below threshold (only 1 distinct value)."""
        codes = ["DTL"] * 20
        content = _build_lines(codes)
        result = detect_discriminator(content)
        assert result["best"] is None


# ---------------------------------------------------------------------------
# max_lines parameter
# ---------------------------------------------------------------------------

class TestDetectDiscriminatorMaxLines:
    def test_max_lines_parameter_respected(self):
        """100-line file with max_lines=5 → only 5 lines inspected.

        When only 5 lines are read and those have a consistent code,
        the algorithm can still detect it; the key check is that extra
        lines beyond the limit are *not* used (tested indirectly: if we
        supply all-DTL in lines 6+, the result must still be consistent
        with 5-line analysis).
        """
        # First 5 lines: HDR DTL DTL DTL TRL — detectable pattern
        first_five = ["HDR", "DTL", "DTL", "DTL", "TRL"]
        # Lines 6–100: all ZZZ — would corrupt pattern if read
        remaining = ["ZZZ"] * 95
        content = _build_lines(first_five + remaining, width=10)
        result = detect_discriminator(content, max_lines=5)
        # ZZZ should NOT appear in values since lines 6+ were skipped
        if result["best"] is not None:
            assert "ZZZ" not in result["best"]["values"]

    def test_max_lines_default_is_20(self):
        """Default max_lines=20 — passing explicitly gives same result."""
        codes = ["HDR", "DTL", "DTL", "TRL"] * 5
        content = _build_lines(codes)
        r1 = detect_discriminator(content)
        r2 = detect_discriminator(content, max_lines=20)
        assert r1 == r2


# ---------------------------------------------------------------------------
# 1-indexed position
# ---------------------------------------------------------------------------

class TestDetectDiscriminatorIndexing:
    def test_returns_1indexed_position(self):
        """First character position → position=1 (not 0)."""
        content = _build_lines(["HDR", "DTL", "DTL", "TRL"] * 5)
        result = detect_discriminator(content)
        assert result["best"]["position"] == 1

    def test_detects_code_starting_at_column_5(self):
        """Code at byte offset 4 (0-indexed) → position=5 (1-indexed).

        Use a unique numeric prefix per line so offsets 0–3 yield >8 distinct
        values and are rejected.  Only offset=4 is a valid discriminator.
        """
        rows = []
        codes = ["HDR", "DTL", "DTL", "DTL", "TRL"] * 4  # 20 rows
        for i, code in enumerate(codes):
            prefix = f"{i:04d}"  # "0000"–"0019" — all unique → rejected
            rows.append(prefix + code + "X" * 13)
        content = "\n".join(rows)
        result = detect_discriminator(content)
        assert result["best"] is not None
        assert result["best"]["position"] == 5
        assert len(result["best"]["values"]) == 3

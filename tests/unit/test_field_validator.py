"""Unit tests for FieldValidator predicates.

Covers the eight BA-friendly "native" predicates added under ADR 0006
(issue #13) plus a thin regression layer over the pre-existing predicates
to guard against accidental signature drift. Each new predicate has at
least three cases: a happy path (no violation), a violation case, and an
edge case relevant to the predicate's semantics.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.validators.field_validator import FieldValidator


@pytest.fixture
def validator() -> FieldValidator:
    """Return a fresh FieldValidator for each test."""
    return FieldValidator()


# ---------------------------------------------------------------------------
# validate_not_empty
# ---------------------------------------------------------------------------


class TestValidateNotEmpty:
    """``not_empty`` flags null, empty, and whitespace-only cells."""

    def test_happy_path_no_violations(self, validator: FieldValidator):
        """All cells populated with non-whitespace content -> no violations."""
        df = pd.DataFrame({"f": ["A", "B", "100"]})
        mask = validator.validate_not_empty(df, "f")
        assert mask.tolist() == [False, False, False]

    def test_null_and_empty_are_violators(self, validator: FieldValidator):
        """NaN and empty-string cells produce True in the mask."""
        df = pd.DataFrame({"f": [None, "", "value"]})
        mask = validator.validate_not_empty(df, "f")
        assert mask.tolist() == [True, True, False]

    def test_whitespace_only_is_violator(self, validator: FieldValidator):
        """Whitespace-only cells are treated as empty (fixed-width padding)."""
        df = pd.DataFrame({"f": ["   ", "\t\n", "ok"]})
        mask = validator.validate_not_empty(df, "f")
        assert mask.tolist() == [True, True, False]


# ---------------------------------------------------------------------------
# validate_numeric_format
# ---------------------------------------------------------------------------


class TestValidateNumericFormat:
    """``numeric`` flags non-numerically-parseable non-empty cells."""

    def test_integer_strings_pass(self, validator: FieldValidator):
        """Pure integer strings are valid numerics."""
        df = pd.DataFrame({"f": ["1", "42", "-7"]})
        mask = validator.validate_numeric_format(df, "f")
        assert mask.tolist() == [False, False, False]

    def test_non_numeric_is_violator(self, validator: FieldValidator):
        """Alphabetic content is a violator."""
        df = pd.DataFrame({"f": ["100", "abc", "12.5"]})
        mask = validator.validate_numeric_format(df, "f")
        # Decimals are permissive (matches ``pd.to_numeric``).
        assert mask.tolist() == [False, True, False]

    def test_leading_zero_and_empty_not_flagged(self, validator: FieldValidator):
        """Leading zeros parse fine; empty cells fall through to not_empty."""
        df = pd.DataFrame({"f": ["007", "", None]})
        mask = validator.validate_numeric_format(df, "f")
        assert mask.tolist() == [False, False, False]


# ---------------------------------------------------------------------------
# validate_date_format
# ---------------------------------------------------------------------------


class TestValidateDateFormat:
    """``date_format`` flags cells that don't match the named format's regex."""

    def test_ccyymmdd_happy_path(self, validator: FieldValidator):
        """Eight-digit values match CCYYMMDD."""
        df = pd.DataFrame({"f": ["20260101", "19991231"]})
        mask = validator.validate_date_format(df, "f", "CCYYMMDD")
        assert mask.tolist() == [False, False]

    def test_wrong_length_is_violator(self, validator: FieldValidator):
        """Shorter or longer values fail the regex."""
        df = pd.DataFrame({"f": ["20260101", "2026010", "202601010"]})
        mask = validator.validate_date_format(df, "f", "CCYYMMDD")
        assert mask.tolist() == [False, True, True]

    def test_unknown_format_falls_back_to_digits(self, validator: FieldValidator):
        """Unknown tokens use ``^\\d+$`` (mirrors BA converter default)."""
        df = pd.DataFrame({"f": ["12345", "abc"]})
        mask = validator.validate_date_format(df, "f", "PROBABLY_NOT_A_REAL_FORMAT")
        assert mask.tolist() == [False, True]

    def test_iso_dashed_format(self, validator: FieldValidator):
        """``YYYY-MM-DD`` regex matches dashed dates only."""
        df = pd.DataFrame({"f": ["2026-01-01", "20260101"]})
        mask = validator.validate_date_format(df, "f", "YYYY-MM-DD")
        assert mask.tolist() == [False, True]


# ---------------------------------------------------------------------------
# validate_valid_values
# ---------------------------------------------------------------------------


class TestValidateValidValues:
    """``valid_values`` flags cells outside the allowed set."""

    def test_happy_path(self, validator: FieldValidator):
        """All cells in the allowed set -> no violations."""
        df = pd.DataFrame({"f": ["A", "B", "A"]})
        mask = validator.validate_valid_values(df, "f", ["A", "B"])
        assert mask.tolist() == [False, False, False]

    def test_outsider_is_violator(self, validator: FieldValidator):
        """A value not in the allowed set is flagged."""
        df = pd.DataFrame({"f": ["A", "C", "B"]})
        mask = validator.validate_valid_values(df, "f", ["A", "B"])
        assert mask.tolist() == [False, True, False]

    def test_strips_whitespace_on_both_sides(self, validator: FieldValidator):
        """Fixed-width padding on either side is stripped before comparison."""
        df = pd.DataFrame({"f": ["LS  ", " LS", "OTHER"]})
        mask = validator.validate_valid_values(df, "f", [" LS ", "MM"])
        assert mask.tolist() == [False, False, True]


# ---------------------------------------------------------------------------
# validate_min_value / validate_max_value
# ---------------------------------------------------------------------------


class TestValidateMinValue:
    """``min_value`` flags values strictly less than the threshold."""

    def test_above_or_equal_passes(self, validator: FieldValidator):
        """Values >= threshold are clean."""
        df = pd.DataFrame({"f": [10, 5, 5.0001]})
        mask = validator.validate_min_value(df, "f", 5)
        assert mask.tolist() == [False, False, False]

    def test_below_is_violator(self, validator: FieldValidator):
        """Values strictly below the threshold are flagged."""
        df = pd.DataFrame({"f": [4, 5, 6]})
        mask = validator.validate_min_value(df, "f", 5)
        assert mask.tolist() == [True, False, False]

    def test_non_numeric_not_flagged(self, validator: FieldValidator):
        """Non-numeric coerces to NaN and ``NaN < x`` is False."""
        df = pd.DataFrame({"f": ["abc", "10"]})
        mask = validator.validate_min_value(df, "f", 5)
        # ``abc`` -> NaN -> NaN<5 == False; the ``numeric`` rule covers that.
        assert mask.tolist() == [False, False]


class TestValidateMaxValue:
    """``max_value`` flags values strictly greater than the threshold."""

    def test_below_or_equal_passes(self, validator: FieldValidator):
        """Values <= threshold are clean."""
        df = pd.DataFrame({"f": [99, 100, 100.0]})
        mask = validator.validate_max_value(df, "f", 100)
        assert mask.tolist() == [False, False, False]

    def test_above_is_violator(self, validator: FieldValidator):
        """Values strictly above the threshold are flagged."""
        df = pd.DataFrame({"f": [101, 100, 99]})
        mask = validator.validate_max_value(df, "f", 100)
        assert mask.tolist() == [True, False, False]

    def test_negative_threshold(self, validator: FieldValidator):
        """Negative thresholds work like any other numeric threshold."""
        df = pd.DataFrame({"f": [-5, -10, 0]})
        mask = validator.validate_max_value(df, "f", -7)
        assert mask.tolist() == [True, False, True]


# ---------------------------------------------------------------------------
# validate_exact_length / validate_min_length
# ---------------------------------------------------------------------------


class TestValidateExactLength:
    """``exact_length`` flags strings whose length isn't exactly *n*."""

    def test_matching_length_passes(self, validator: FieldValidator):
        """All cells at the exact length -> no violations."""
        df = pd.DataFrame({"f": ["AAA", "BBB", "CCC"]})
        mask = validator.validate_exact_length(df, "f", 3)
        assert mask.tolist() == [False, False, False]

    def test_wrong_length_is_violator(self, validator: FieldValidator):
        """Shorter or longer values are flagged."""
        df = pd.DataFrame({"f": ["AAA", "AA", "AAAA"]})
        mask = validator.validate_exact_length(df, "f", 3)
        assert mask.tolist() == [False, True, True]

    def test_whitespace_padding_is_counted(self, validator: FieldValidator):
        """Fixed-width padding contributes to the measured length."""
        df = pd.DataFrame({"f": ["AAA", "AA ", " AA"]})
        mask = validator.validate_exact_length(df, "f", 3)
        # All three are length 3 — padding is intentionally not stripped.
        assert mask.tolist() == [False, False, False]


class TestValidateMinLength:
    """``min_length`` flags strings shorter than *n*."""

    def test_at_or_above_length_passes(self, validator: FieldValidator):
        """Strings >= min length are clean."""
        df = pd.DataFrame({"f": ["abc", "abcd", "abcde"]})
        mask = validator.validate_min_length(df, "f", 3)
        assert mask.tolist() == [False, False, False]

    def test_too_short_is_violator(self, validator: FieldValidator):
        """Strings shorter than min length are flagged."""
        df = pd.DataFrame({"f": ["a", "ab", "abc"]})
        mask = validator.validate_min_length(df, "f", 3)
        assert mask.tolist() == [True, True, False]

    def test_empty_string_is_violator(self, validator: FieldValidator):
        """An empty cell has length 0 which is below any positive min."""
        df = pd.DataFrame({"f": ["", "x"]})
        mask = validator.validate_min_length(df, "f", 1)
        assert mask.tolist() == [True, False]

"""Unit tests for the transform engine.

Tests are written before implementation — they are expected to fail initially.
Covers apply_transform for each Transform subtype, padding, and truncation.
"""

import pytest
from src.transforms import apply_transform
from src.transforms.models import (
    Transform,
    DefaultTransform,
    BlankTransform,
    ConstantTransform,
    ConcatTransform,
    ConcatPart,
    FieldMapTransform,
)


# ---------------------------------------------------------------------------
# Noop transform
# ---------------------------------------------------------------------------

class TestNoopTransform:
    """Noop transform passes the source value through unchanged."""

    def test_noop_returns_source(self):
        """Noop with a present source value returns that value."""
        result = apply_transform("HELLO", Transform(type="noop"))
        assert result == "HELLO"

    def test_noop_empty_source(self):
        """Noop with empty source returns empty string."""
        result = apply_transform("", Transform(type="noop"))
        assert result == ""

    def test_noop_none_source(self):
        """Noop with None source returns empty string."""
        result = apply_transform(None, Transform(type="noop"))
        assert result == ""

    def test_noop_with_padding(self):
        """Noop pads short value to field_length with spaces on the right."""
        result = apply_transform("AB", Transform(type="noop"), field_length=5)
        assert result == "AB   "
        assert len(result) == 5

    def test_noop_with_truncation(self):
        """Noop truncates value longer than field_length."""
        result = apply_transform("ABCDEF", Transform(type="noop"), field_length=4)
        assert result == "ABCD"
        assert len(result) == 4


# ---------------------------------------------------------------------------
# ConstantTransform
# ---------------------------------------------------------------------------

class TestConstantTransform:
    """ConstantTransform always returns its configured value."""

    def test_constant_ignores_source(self):
        """Source value is ignored; constant is returned."""
        result = apply_transform("ANYTHING", ConstantTransform(value="000"))
        assert result == "000"

    def test_constant_with_empty_source(self):
        """Empty source still returns constant."""
        result = apply_transform("", ConstantTransform(value="500"))
        assert result == "500"

    def test_constant_with_none_source(self):
        """None source still returns constant."""
        result = apply_transform(None, ConstantTransform(value="XYZ"))
        assert result == "XYZ"

    def test_constant_padded_to_field_length(self):
        """Constant value is padded when shorter than field_length."""
        result = apply_transform("X", ConstantTransform(value="A"), field_length=3)
        assert result == "A  "
        assert len(result) == 3

    def test_constant_truncated_to_field_length(self):
        """Constant value is truncated when longer than field_length."""
        result = apply_transform("X", ConstantTransform(value="ABCDE"), field_length=3)
        assert result == "ABC"


# ---------------------------------------------------------------------------
# DefaultTransform
# ---------------------------------------------------------------------------

class TestDefaultTransform:
    """DefaultTransform returns source when present, otherwise the default."""

    def test_source_present_returns_source(self):
        """Non-empty source value is returned as-is."""
        result = apply_transform("REAL", DefaultTransform(value="FALLBACK"))
        assert result == "REAL"

    def test_source_empty_returns_default(self):
        """Empty source triggers the default value."""
        result = apply_transform("", DefaultTransform(value="FALLBACK"))
        assert result == "FALLBACK"

    def test_source_none_returns_default(self):
        """None source triggers the default value."""
        result = apply_transform(None, DefaultTransform(value="FALLBACK"))
        assert result == "FALLBACK"

    def test_source_whitespace_returns_default(self):
        """Whitespace-only source is treated as absent; default is used."""
        result = apply_transform("   ", DefaultTransform(value="FALLBACK"))
        assert result == "FALLBACK"

    def test_default_padded_to_field_length(self):
        """Default value is padded when shorter than field_length."""
        result = apply_transform("", DefaultTransform(value="AB"), field_length=5)
        assert result == "AB   "
        assert len(result) == 5

    def test_source_padded_to_field_length(self):
        """Source value is padded when shorter than field_length."""
        result = apply_transform("AB", DefaultTransform(value="XX"), field_length=5)
        assert result == "AB   "

    def test_source_truncated_to_field_length(self):
        """Source value is truncated when longer than field_length."""
        result = apply_transform("ABCDEF", DefaultTransform(value="X"), field_length=4)
        assert result == "ABCD"


# ---------------------------------------------------------------------------
# BlankTransform
# ---------------------------------------------------------------------------

class TestBlankTransform:
    """BlankTransform returns fill_value (or fill_char * field_length)."""

    def test_blank_returns_empty_string(self):
        """BlankTransform with default fill_value returns empty string."""
        result = apply_transform("ANYTHING", BlankTransform())
        assert result == ""

    def test_blank_with_fill_value(self):
        """BlankTransform with explicit fill_value returns that value."""
        result = apply_transform("ANYTHING", BlankTransform(fill_value="0000"))
        assert result == "0000"

    def test_blank_fill_char_with_field_length(self):
        """BlankTransform with fill_char=' ' pads to field_length."""
        result = apply_transform("ANYTHING", BlankTransform(fill_char=" "), field_length=5)
        assert result == "     "
        assert len(result) == 5

    def test_blank_fill_value_padded_to_field_length(self):
        """fill_value shorter than field_length is padded with fill_char."""
        result = apply_transform(
            "X",
            BlankTransform(fill_value="00", fill_char="0"),
            field_length=5,
        )
        assert result == "00000"

    def test_blank_fill_value_truncated_to_field_length(self):
        """fill_value longer than field_length is truncated."""
        result = apply_transform(
            "X",
            BlankTransform(fill_value="0000000000"),
            field_length=4,
        )
        assert result == "0000"

    def test_blank_none_source_still_blanks(self):
        """None source still returns fill_value."""
        result = apply_transform(None, BlankTransform(fill_value="0000"))
        assert result == "0000"


# ---------------------------------------------------------------------------
# Field-length edge cases
# ---------------------------------------------------------------------------

class TestFieldLengthEdgeCases:
    """apply_transform handles zero and unset field_length correctly."""

    def test_zero_field_length_no_padding(self):
        """field_length=0 means no padding or truncation is applied."""
        result = apply_transform("HELLO", Transform(type="noop"), field_length=0)
        assert result == "HELLO"

    def test_negative_field_length_treated_as_zero(self):
        """Negative field_length is treated as no constraint."""
        result = apply_transform("HELLO", Transform(type="noop"), field_length=-1)
        assert result == "HELLO"

    def test_exact_length_unchanged(self):
        """Value with exactly field_length chars is unchanged."""
        result = apply_transform("ABC", Transform(type="noop"), field_length=3)
        assert result == "ABC"


# ---------------------------------------------------------------------------
# ConcatTransform
# ---------------------------------------------------------------------------

class TestConcatTransformEngine:
    def test_concat_three_fields(self):
        transform = ConcatTransform(parts=[
            ConcatPart(field_name="BR"),
            ConcatPart(field_name="CUS"),
            ConcatPart(field_name="LN"),
        ])
        row = {"BR": "100", "CUS": "1234567", "LN": "4321"}
        result = apply_transform(None, transform, row=row)
        assert result == "10012345674321"

    def test_concat_with_lpad_space(self):
        transform = ConcatTransform(parts=[
            ConcatPart(field_name="BR", lpad_width=3),
            ConcatPart(field_name="CUS"),
            ConcatPart(field_name="LN"),
        ])
        row = {"BR": "10", "CUS": "1234567", "LN": "4321"}
        result = apply_transform(None, transform, row=row)
        assert result == " 1012345674321"

    def test_concat_with_lpad_zero_char(self):
        transform = ConcatTransform(parts=[
            ConcatPart(field_name="BR", lpad_width=3, lpad_char="0"),
        ])
        row = {"BR": "10"}
        result = apply_transform(None, transform, row=row)
        assert result == "010"

    def test_concat_missing_field_uses_empty_string(self):
        transform = ConcatTransform(parts=[
            ConcatPart(field_name="MISSING"),
            ConcatPart(field_name="CUS"),
        ])
        row = {"CUS": "ABC"}
        result = apply_transform(None, transform, row=row)
        assert result == "ABC"

    def test_concat_no_row_uses_empty_strings(self):
        transform = ConcatTransform(parts=[
            ConcatPart(field_name="BR"),
            ConcatPart(field_name="CUS"),
        ])
        result = apply_transform(None, transform)
        assert result == ""

    def test_concat_fitted_to_field_length(self):
        transform = ConcatTransform(parts=[
            ConcatPart(field_name="A"),
            ConcatPart(field_name="B"),
        ])
        row = {"A": "10", "B": "20"}
        result = apply_transform(None, transform, field_length=6, row=row)
        assert result == "1020  "
        assert len(result) == 6

    def test_concat_lpad_exceeds_width_not_truncated(self):
        """LPAD only pads; it never truncates the source value."""
        transform = ConcatTransform(parts=[
            ConcatPart(field_name="BR", lpad_width=2),
        ])
        row = {"BR": "12345"}
        result = apply_transform(None, transform, row=row)
        assert result == "12345"


# ---------------------------------------------------------------------------
# FieldMapTransform
# ---------------------------------------------------------------------------

class TestFieldMapTransformEngine:
    def test_field_map_returns_named_field(self):
        transform = FieldMapTransform(source_field="CUST_ID")
        row = {"CUST_ID": "ABC123"}
        result = apply_transform(None, transform, row=row)
        assert result == "ABC123"

    def test_field_map_missing_field_returns_empty(self):
        transform = FieldMapTransform(source_field="MISSING")
        row = {"CUST_ID": "ABC123"}
        result = apply_transform(None, transform, row=row)
        assert result == ""

    def test_field_map_no_row_returns_empty(self):
        transform = FieldMapTransform(source_field="CUST_ID")
        result = apply_transform(None, transform)
        assert result == ""

    def test_field_map_fitted_to_field_length(self):
        transform = FieldMapTransform(source_field="CODE")
        row = {"CODE": "AB"}
        result = apply_transform(None, transform, field_length=4, row=row)
        assert result == "AB  "
        assert len(result) == 4

    def test_field_map_source_value_ignored(self):
        """source_value param is ignored; value comes from row."""
        transform = FieldMapTransform(source_field="CODE")
        row = {"CODE": "XYZ"}
        result = apply_transform("IGNORED", transform, row=row)
        assert result == "XYZ"

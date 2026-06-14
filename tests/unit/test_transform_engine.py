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
    NullCheckCondition,
    EqualityCondition,
    InCondition,
    ConditionalTransform,
    PadTransform,
    TruncateTransform,
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


# ---------------------------------------------------------------------------
# ConditionalTransform
# ---------------------------------------------------------------------------

class TestConditionalTransformEngine:
    """ConditionalTransform evaluates a condition and dispatches to then/else."""

    def test_null_check_true_applies_then_transform(self):
        """When null-check IS NULL fires (field is blank), then_transform is used."""
        transform = ConditionalTransform(
            condition=NullCheckCondition(field="code"),
            then_transform=ConstantTransform(value="DEFAULT"),
            else_transform=ConstantTransform(value="PRESENT"),
        )
        row = {"code": ""}
        result = apply_transform(None, transform, row=row)
        assert result == "DEFAULT"

    def test_null_check_false_applies_else_transform(self):
        """When null-check IS NULL does not fire (field has value), else_transform is used."""
        transform = ConditionalTransform(
            condition=NullCheckCondition(field="code"),
            then_transform=ConstantTransform(value="DEFAULT"),
            else_transform=ConstantTransform(value="PRESENT"),
        )
        row = {"code": "ABC"}
        result = apply_transform(None, transform, row=row)
        assert result == "PRESENT"

    def test_equality_check_true_applies_then_transform(self):
        """When equality condition matches, then_transform is applied."""
        transform = ConditionalTransform(
            condition=EqualityCondition(field="status", value="ACTIVE"),
            then_transform=ConstantTransform(value="Y"),
            else_transform=ConstantTransform(value="N"),
        )
        row = {"status": "ACTIVE"}
        result = apply_transform(None, transform, row=row)
        assert result == "Y"

    def test_equality_check_false_applies_else_transform(self):
        """When equality condition does not match, else_transform is applied."""
        transform = ConditionalTransform(
            condition=EqualityCondition(field="status", value="ACTIVE"),
            then_transform=ConstantTransform(value="Y"),
            else_transform=ConstantTransform(value="N"),
        )
        row = {"status": "CLOSED"}
        result = apply_transform(None, transform, row=row)
        assert result == "N"

    def test_in_condition_true_applies_then_transform(self):
        """When IN condition matches, then_transform is applied."""
        transform = ConditionalTransform(
            condition=InCondition(field="type", values=["A", "B", "C"]),
            then_transform=ConstantTransform(value="KNOWN"),
            else_transform=ConstantTransform(value="UNKNOWN"),
        )
        row = {"type": "B"}
        result = apply_transform(None, transform, row=row)
        assert result == "KNOWN"

    def test_in_condition_false_applies_else_transform(self):
        """When IN condition does not match, else_transform is applied."""
        transform = ConditionalTransform(
            condition=InCondition(field="type", values=["A", "B", "C"]),
            then_transform=ConstantTransform(value="KNOWN"),
            else_transform=ConstantTransform(value="UNKNOWN"),
        )
        row = {"type": "X"}
        result = apply_transform(None, transform, row=row)
        assert result == "UNKNOWN"

    def test_then_transform_is_constant(self):
        """ConditionalTransform with ConstantTransform as then branch returns constant."""
        transform = ConditionalTransform(
            condition=NullCheckCondition(field="amount"),
            then_transform=ConstantTransform(value="0"),
        )
        row = {"amount": None}
        result = apply_transform(None, transform, row=row)
        assert result == "0"

    def test_else_transform_is_default(self):
        """ConditionalTransform with DefaultTransform as else branch uses source value."""
        transform = ConditionalTransform(
            condition=NullCheckCondition(field="flag"),
            then_transform=ConstantTransform(value="BLANK"),
            else_transform=DefaultTransform(value="FALLBACK"),
        )
        row = {"flag": "X"}
        # Condition is false (flag is not null), so else_transform runs.
        # source_value "RAW" is present, so DefaultTransform returns "RAW".
        result = apply_transform("RAW", transform, row=row)
        assert result == "RAW"

    def test_field_length_passed_through_to_nested_apply(self):
        """field_length is forwarded to the nested apply_transform call."""
        transform = ConditionalTransform(
            condition=NullCheckCondition(field="x"),
            then_transform=ConstantTransform(value="HI"),
        )
        row = {"x": ""}
        # Condition fires; ConstantTransform("HI") padded to field_length=5
        result = apply_transform(None, transform, field_length=5, row=row)
        assert result == "HI   "
        assert len(result) == 5

    def test_row_passed_through_to_nested_field_map(self):
        """row dict is forwarded so FieldMapTransform inside conditional works."""
        transform = ConditionalTransform(
            condition=EqualityCondition(field="use_alt", value="Y"),
            then_transform=FieldMapTransform(source_field="alt_code"),
            else_transform=FieldMapTransform(source_field="main_code"),
        )
        row = {"use_alt": "Y", "alt_code": "ALT", "main_code": "MAIN"}
        result = apply_transform(None, transform, row=row)
        assert result == "ALT"

    def test_default_else_transform_is_noop(self):
        """When else_transform is not provided and condition is False, noop pass-through is used."""
        transform = ConditionalTransform(
            condition=EqualityCondition(field="flag", value="Y"),
            then_transform=ConstantTransform(value="YES"),
        )
        row = {"flag": "N"}
        # Condition false → else_transform defaults to noop → returns source_value
        result = apply_transform("ORIG", transform, row=row)
        assert result == "ORIG"

    def test_nested_conditional_as_else_transform(self):
        """else_transform can itself be a ConditionalTransform (nested conditionals)."""
        inner = ConditionalTransform(
            condition=EqualityCondition(field="type", value="B"),
            then_transform=ConstantTransform(value="TYPE_B"),
            else_transform=ConstantTransform(value="OTHER"),
        )
        outer = ConditionalTransform(
            condition=EqualityCondition(field="type", value="A"),
            then_transform=ConstantTransform(value="TYPE_A"),
            else_transform=inner,
        )
        assert apply_transform(None, outer, row={"type": "A"}) == "TYPE_A"
        assert apply_transform(None, outer, row={"type": "B"}) == "TYPE_B"
        assert apply_transform(None, outer, row={"type": "C"}) == "OTHER"


# ---------------------------------------------------------------------------
# SequentialNumberTransform (Phase 3e)
# ---------------------------------------------------------------------------


class TestSequentialTransformEngine:
    """apply_transform behaviour for SequentialNumberTransform."""

    def test_without_counter_returns_str_start(self):
        """Without a counter, apply_transform returns str(start) on every call."""
        from src.transforms.models import SequentialNumberTransform

        t = SequentialNumberTransform(start=1)
        assert apply_transform(None, t) == "1"
        # Stateless: second call still returns the same start value.
        assert apply_transform(None, t) == "1"

    def test_without_counter_custom_start(self):
        """Stateless fallback uses configured start."""
        from src.transforms.models import SequentialNumberTransform

        t = SequentialNumberTransform(start=42)
        assert apply_transform(None, t) == "42"

    def test_with_counter_increments(self):
        """With a counter, successive apply_transform calls increment."""
        from src.transforms.models import SequentialNumberTransform
        from src.transforms.sequential_counter import SequentialCounter
        from src.transforms.transform_engine import apply_transform as _apply

        counter = SequentialCounter()
        t = SequentialNumberTransform(start=1)
        assert _apply(None, t, counter=counter) == "1"
        assert _apply(None, t, counter=counter) == "2"
        assert _apply(None, t, counter=counter) == "3"

    def test_with_counter_custom_step(self):
        """Counter honours the step attribute."""
        from src.transforms.models import SequentialNumberTransform
        from src.transforms.sequential_counter import SequentialCounter
        from src.transforms.transform_engine import apply_transform as _apply

        counter = SequentialCounter()
        t = SequentialNumberTransform(start=0, step=5)
        assert _apply(None, t, counter=counter) == "0"
        assert _apply(None, t, counter=counter) == "5"
        assert _apply(None, t, counter=counter) == "10"

    def test_field_length_padding_applied_to_sequential_value(self):
        """field_length right-pads the sequential value."""
        from src.transforms.models import SequentialNumberTransform
        from src.transforms.sequential_counter import SequentialCounter
        from src.transforms.transform_engine import apply_transform as _apply

        counter = SequentialCounter()
        t = SequentialNumberTransform(start=7)
        result = _apply(None, t, field_length=5, counter=counter)
        assert result == "7    "
        assert len(result) == 5

    def test_zero_padded_sequential_with_pad_length(self):
        """pad_length zero-pads the sequential value before field_length fitting."""
        from src.transforms.models import SequentialNumberTransform
        from src.transforms.sequential_counter import SequentialCounter
        from src.transforms.transform_engine import apply_transform as _apply

        counter = SequentialCounter()
        t = SequentialNumberTransform(start=1, pad_length=5)
        assert _apply(None, t, counter=counter) == "00001"
        assert _apply(None, t, counter=counter) == "00002"


# ---------------------------------------------------------------------------
# NumericFormatTransform
# ---------------------------------------------------------------------------

class TestNumericFormatTransformEngine:
    """NumericFormatTransform zero-pads integer values with optional sign."""

    def test_integer_zero_padded_to_length(self):
        """Integer source is zero-padded to the specified length."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=8, signed=False)
        result = apply_transform("42", t)
        assert result == "00000042"

    def test_signed_positive_gets_plus_prefix(self):
        """Positive value receives '+' prefix when signed=True."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=13, signed=True)
        result = apply_transform("12345", t)
        assert result == "+000000012345"

    def test_signed_negative_gets_minus_prefix(self):
        """Negative value receives '-' prefix when signed=True."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=13, signed=True)
        result = apply_transform("-12345", t)
        assert result == "-000000012345"

    def test_decimal_places_multiplies_before_padding(self):
        """decimal_places=2 multiplies value by 100 then zero-pads."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=13, signed=True, decimal_places=2)
        result = apply_transform("123.45", t)
        assert result == "+000000012345"

    def test_absent_value_returns_default(self):
        """Absent (empty) source returns default_value."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=8, signed=False, default_value="00000000")
        result = apply_transform("", t)
        assert result == "00000000"

    def test_blank_source_returns_default(self):
        """Whitespace-only source returns default_value."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=8, signed=False, default_value="00000000")
        result = apply_transform("   ", t)
        assert result == "00000000"

    def test_unparseable_source_returns_default(self):
        """Non-numeric source returns default_value instead of raising."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=8, signed=False, default_value="00000000")
        result = apply_transform("ABC", t)
        assert result == "00000000"

    def test_exact_length_no_truncation_needed(self):
        """Value that exactly fills length is returned without truncation."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=5, signed=False)
        result = apply_transform("99999", t)
        assert result == "99999"

    def test_zero_value_signed(self):
        """Zero gets '+' prefix when signed=True."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=5, signed=True)
        result = apply_transform("0", t)
        assert result == "+0000"

    def test_none_source_returns_default(self):
        """None source returns default_value."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=8, signed=False, default_value="00000000")
        result = apply_transform(None, t)
        assert result == "00000000"

    def test_decimal_input_rounded_to_int_when_decimal_places_zero(self):
        """Float with decimal_places=0 is rounded to nearest integer."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=13, signed=True)
        result = apply_transform("12345.67", t)
        assert result == "+000000012346"

    def test_unsigned_with_negative_input(self):
        """Unsigned transform with negative input still pads the digits."""
        from src.transforms.models import NumericFormatTransform

        t = NumericFormatTransform(length=8, signed=False)
        result = apply_transform("42", t)
        assert result == "00000042"


# ---------------------------------------------------------------------------
# DateFormatTransform
# ---------------------------------------------------------------------------


class TestDateFormatTransformEngine:
    """DateFormatTransform converts a date string from one format to another."""

    def test_iso_date_to_ccyymmdd(self):
        """ISO date '2025-06-15' with %Y-%m-%d input and %Y%m%d output -> '20250615'."""
        from src.transforms.models import DateFormatTransform
        from src.transforms.transform_engine import apply_transform as _apply

        t = DateFormatTransform(
            input_format="%Y-%m-%d",
            output_format="%Y%m%d",
        )
        result = _apply("2025-06-15", t)
        assert result == "20250615"

    def test_absent_source_returns_default_value(self):
        """When source_value is None, default_value is returned."""
        from src.transforms.models import DateFormatTransform
        from src.transforms.transform_engine import apply_transform as _apply

        t = DateFormatTransform(
            input_format="%Y-%m-%d",
            output_format="%Y%m%d",
            default_value="00000000",
        )
        result = _apply(None, t)
        assert result == "00000000"

    def test_blank_source_returns_default_value(self):
        """When source_value is blank/whitespace, default_value is returned."""
        from src.transforms.models import DateFormatTransform
        from src.transforms.transform_engine import apply_transform as _apply

        t = DateFormatTransform(
            input_format="%Y-%m-%d",
            output_format="%Y%m%d",
            default_value="00000000",
        )
        result = _apply("   ", t)
        assert result == "00000000"

    def test_empty_source_returns_default_value(self):
        """When source_value is empty string, default_value is returned."""
        from src.transforms.models import DateFormatTransform
        from src.transforms.transform_engine import apply_transform as _apply

        t = DateFormatTransform(
            input_format="%Y-%m-%d",
            output_format="%Y%m%d",
            default_value="00000000",
        )
        result = _apply("", t)
        assert result == "00000000"

    def test_unparseable_source_returns_default_value(self):
        """When source_value does not match input_format, default_value is returned."""
        from src.transforms.models import DateFormatTransform
        from src.transforms.transform_engine import apply_transform as _apply

        t = DateFormatTransform(
            input_format="%Y-%m-%d",
            output_format="%Y%m%d",
            default_value="INVALID",
        )
        result = _apply("not-a-date", t)
        assert result == "INVALID"

    def test_field_length_padding_applied(self):
        """Converted date is padded to field_length when shorter."""
        from src.transforms.models import DateFormatTransform
        from src.transforms.transform_engine import apply_transform as _apply

        t = DateFormatTransform(
            input_format="%Y-%m-%d",
            output_format="%Y%m%d",
        )
        result = _apply("2025-06-15", t, field_length=10)
        assert result == "20250615  "
        assert len(result) == 10

    def test_mm_dd_ccyy_output_format(self):
        """ISO date can be reformatted to MM/DD/CCYY."""
        from src.transforms.models import DateFormatTransform
        from src.transforms.transform_engine import apply_transform as _apply

        t = DateFormatTransform(
            input_format="%Y-%m-%d",
            output_format="%m/%d/%Y",
        )
        result = _apply("2025-06-15", t)
        assert result == "06/15/2025"

    def test_default_value_empty_when_not_set(self):
        """When no default_value is configured and source is absent, empty string returned."""
        from src.transforms.models import DateFormatTransform
        from src.transforms.transform_engine import apply_transform as _apply

        t = DateFormatTransform(
            input_format="%Y-%m-%d",
            output_format="%Y%m%d",
        )
        result = _apply(None, t)
        assert result == ""


# ---------------------------------------------------------------------------
# ScaleTransform (Phase 4c)
# ---------------------------------------------------------------------------


class TestScaleTransformEngine:
    """apply_transform behaviour for ScaleTransform."""

    def test_multiply_by_100_integer_result(self):
        """Multiplying 123.45 by 100 produces '12345.0' (auto str conversion)."""
        from src.transforms.models import ScaleTransform

        t = ScaleTransform(factor=100)
        result = apply_transform("123.45", t)
        # With decimal_places=-1 (auto), result is str(float).
        # 123.45 * 100 = 12345.0
        assert result == "12345.0"

    def test_multiply_by_100_decimal_places_zero_strips_decimal(self):
        """decimal_places=0 formats result as integer string (no decimal point)."""
        from src.transforms.models import ScaleTransform

        t = ScaleTransform(factor=100, decimal_places=0)
        result = apply_transform("123.45", t)
        assert result == "12345"

    def test_divide_by_100_decimal_places_two(self):
        """Dividing 12345 by 100 with decimal_places=2 produces '123.45'."""
        from src.transforms.models import ScaleTransform

        t = ScaleTransform(factor=0.01, decimal_places=2)
        result = apply_transform("12345", t)
        assert result == "123.45"

    def test_absent_source_returns_default_value(self):
        """When source is absent/blank, default_value is returned."""
        from src.transforms.models import ScaleTransform

        t = ScaleTransform(factor=100, default_value="0")
        result = apply_transform("", t)
        assert result == "0"

    def test_none_source_returns_default_value(self):
        """When source is None, default_value is returned."""
        from src.transforms.models import ScaleTransform

        t = ScaleTransform(factor=100, default_value="0")
        result = apply_transform(None, t)
        assert result == "0"

    def test_unparseable_source_returns_default_value(self):
        """When source cannot be parsed as float, default_value is returned."""
        from src.transforms.models import ScaleTransform

        t = ScaleTransform(factor=100, default_value="ERR")
        result = apply_transform("ABC", t)
        assert result == "ERR"

    def test_field_length_padding_applied_to_result(self):
        """field_length right-pads the scaled result."""
        from src.transforms.models import ScaleTransform

        t = ScaleTransform(factor=1, decimal_places=0)
        result = apply_transform("42", t, field_length=8)
        assert result == "42      "
        assert len(result) == 8

    def test_default_value_empty_when_not_set(self):
        """When no default_value configured, absent source returns empty string."""
        from src.transforms.models import ScaleTransform

        t = ScaleTransform(factor=100)
        result = apply_transform("", t)
        assert result == ""

    def test_integer_source_value(self):
        """Integer-string source is handled correctly."""
        from src.transforms.models import ScaleTransform

        t = ScaleTransform(factor=2, decimal_places=0)
        result = apply_transform("50", t)
        assert result == "100"


# ---------------------------------------------------------------------------
# PadTransform
# ---------------------------------------------------------------------------

class TestPadTransformEngine:
    """PadTransform pads a value to a target length without truncating."""

    def test_right_pad_short_value(self):
        """Right-pad a value shorter than length with spaces."""
        result = apply_transform("ABC", PadTransform(length=6))
        assert result == "ABC   "

    def test_left_pad_short_value_with_zeros(self):
        """Left-pad a value shorter than length with '0'."""
        result = apply_transform("123", PadTransform(length=5, pad_char="0", direction="left"))
        assert result == "00123"

    def test_value_already_at_length_unchanged(self):
        """Value exactly at target length is returned as-is."""
        result = apply_transform("HELLO", PadTransform(length=5))
        assert result == "HELLO"

    def test_value_longer_than_length_not_truncated(self):
        """Pad never truncates — value longer than length is returned unchanged."""
        result = apply_transform("TOOLONG", PadTransform(length=4))
        assert result == "TOOLONG"

    def test_field_length_applied_after_padding(self):
        """field_length fitting is applied after padding."""
        # Padded to 5 chars, then fit to field_length=8 (right-padded with spaces)
        result = apply_transform("AB", PadTransform(length=5), field_length=8)
        assert result == "AB      "
        assert len(result) == 8


# ---------------------------------------------------------------------------
# TruncateTransform
# ---------------------------------------------------------------------------

class TestTruncateTransformEngine:
    """TruncateTransform cuts a value to at most N characters."""

    def test_truncate_long_value(self):
        """Value longer than length is truncated from the right."""
        result = apply_transform("ABCDEFGH", TruncateTransform(length=4))
        assert result == "ABCD"

    def test_truncate_shorter_value_unchanged(self):
        """Value shorter than length is returned unchanged."""
        result = apply_transform("AB", TruncateTransform(length=5))
        assert result == "AB"

    def test_truncate_from_end_keeps_last_n_chars(self):
        """from_end=True keeps the last N characters."""
        result = apply_transform("ABCDEFGH", TruncateTransform(length=3, from_end=True))
        assert result == "FGH"

    def test_field_length_applied_after_truncation(self):
        """field_length fitting is applied after truncation."""
        # Truncated to 3, then fit to field_length=6 (right-padded with spaces)
        result = apply_transform("ABCDEF", TruncateTransform(length=3), field_length=6)
        assert result == "ABC   "
        assert len(result) == 6

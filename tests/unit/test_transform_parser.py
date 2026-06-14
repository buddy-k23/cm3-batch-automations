"""Unit tests for the transformation text parser.

Tests cover all recognized patterns from real mapping CSVs:
  Default to, Nullable -->, Pass, Hard-code, Hard-Code,
  Initialize to spaces, Leave Blank, unknown/empty inputs,
  and conditional IF/THEN/ELSE expressions.
"""

import pytest
from src.transforms import parse_transform, Transform
from src.transforms.models import (
    DefaultTransform,
    BlankTransform,
    ConstantTransform,
    ConcatTransform,
    ConditionalTransform,
    EqualityCondition,
    FieldMapTransform,
    InCondition,
    NullCheckCondition,
    PadTransform,
    TruncateTransform,
)


# ---------------------------------------------------------------------------
# DefaultTransform patterns
# ---------------------------------------------------------------------------

class TestDefaultTransform:
    def test_default_to_with_single_quotes(self):
        result = parse_transform("Default to '100030'")
        assert isinstance(result, DefaultTransform)
        assert result.value == "100030"

    def test_default_to_trailing_context_ignored(self):
        """Trailing comment text after the value is stripped."""
        result = parse_transform(
            "Default to '100030' FDR – Credit Card = 100010"
        )
        assert isinstance(result, DefaultTransform)
        assert result.value == "100030"

    def test_default_to_with_numeric_no_quotes(self):
        result = parse_transform("Default to +000000000000")
        assert isinstance(result, DefaultTransform)
        assert result.value == "+000000000000"

    def test_default_to_plus_zeros_no_quotes_long(self):
        result = parse_transform("Default to +000000000000000000")
        assert isinstance(result, DefaultTransform)
        assert result.value == "+000000000000000000"

    def test_default_to_quoted_zeros(self):
        result = parse_transform("Default to '00000000'")
        assert isinstance(result, DefaultTransform)
        assert result.value == "00000000"

    def test_default_to_case_insensitive(self):
        result = parse_transform("default to '001'")
        assert isinstance(result, DefaultTransform)
        assert result.value == "001"

    def test_default_to_quoted_string(self):
        result = parse_transform("Default to 'BATCH'")
        assert isinstance(result, DefaultTransform)
        assert result.value == "BATCH"

    def test_default_to_single_char(self):
        result = parse_transform("Default to 'C'")
        assert isinstance(result, DefaultTransform)
        assert result.value == "C"

    def test_default_equals_usd(self):
        result = parse_transform("Default = USD")
        assert isinstance(result, DefaultTransform)
        assert result.value == "USD"


# ---------------------------------------------------------------------------
# BlankTransform patterns
# ---------------------------------------------------------------------------

class TestBlankTransform:
    def test_nullable_leave_blank(self):
        result = parse_transform("Nullable --> Leave Blank")
        assert isinstance(result, BlankTransform)
        assert result.fill_value == ""
        assert result.fill_char == " "

    def test_nullable_with_quoted_fill_value(self):
        result = parse_transform("Nullable --> '0000'")
        assert isinstance(result, BlankTransform)
        assert result.fill_value == "0000"

    def test_nullable_with_quoted_zeros_8(self):
        result = parse_transform("Nullable --> '00000000'")
        assert isinstance(result, BlankTransform)
        assert result.fill_value == "00000000"

    def test_leave_blank_spaces(self):
        result = parse_transform("Leave Blank <spaces>")
        assert isinstance(result, BlankTransform)
        assert result.fill_value == ""

    def test_leave_blank_plain(self):
        result = parse_transform("Leave Blank")
        assert isinstance(result, BlankTransform)

    def test_leave_blank_lowercase(self):
        result = parse_transform("Leave blank")
        assert isinstance(result, BlankTransform)

    def test_pass_blank_spaces(self):
        result = parse_transform("Pass Blank <spaces>")
        assert isinstance(result, BlankTransform)

    def test_initialize_to_spaces(self):
        result = parse_transform("Initialize to spaces")
        assert isinstance(result, BlankTransform)
        assert result.fill_char == " "


# ---------------------------------------------------------------------------
# ConstantTransform patterns
# ---------------------------------------------------------------------------

class TestConstantTransform:
    def test_pass_quoted_value(self):
        result = parse_transform("Pass '000'")
        assert isinstance(result, ConstantTransform)
        assert result.value == "000"

    def test_hard_code_lowercase_h(self):
        result = parse_transform("Hard-code to '500'")
        assert isinstance(result, ConstantTransform)
        assert result.value == "500"

    def test_hard_code_capitalized(self):
        result = parse_transform("Hard-Code to '500'")
        assert isinstance(result, ConstantTransform)
        assert result.value == "500"

    def test_hard_code_double_quotes(self):
        result = parse_transform('Hard-Code to "DPD"')
        assert isinstance(result, ConstantTransform)
        assert result.value == "DPD"

    def test_hardcode_no_dash(self):
        result = parse_transform("Hardcode to 'USD'")
        assert isinstance(result, ConstantTransform)
        assert result.value == "USD"

    def test_pass_value_unquoted(self):
        """'Pass Value' is treated as noop — not a constant."""
        result = parse_transform("Pass Value")
        # generic "Pass Value" with no quoted token → noop
        assert isinstance(result, Transform)
        assert not isinstance(result, ConstantTransform)

    def test_pass_quoted_single_digit(self):
        result = parse_transform("pass '1'")
        assert isinstance(result, ConstantTransform)
        assert result.value == "1"


# ---------------------------------------------------------------------------
# Noop / unknown patterns
# ---------------------------------------------------------------------------

class TestNoopTransform:
    def test_empty_string(self):
        result = parse_transform("")
        assert result.type == "noop"
        assert type(result) is Transform

    def test_none_input(self):
        result = parse_transform(None)
        assert result.type == "noop"

    def test_complex_conditional_semicolon_syntax_is_noop(self):
        """Semicolon-separated syntax we don't support → noop."""
        result = parse_transform(
            "IF LN-BAL not null then LN-BAL; ELSE Default to +000000000000000000"
        )
        assert result.type == "noop"

    def test_pass_as_is(self):
        result = parse_transform("Pass as is")
        assert result.type == "noop"

    def test_transform_as_is(self):
        result = parse_transform("Transform as is")
        assert result.type == "noop"

    def test_ilmast_key_formula(self):
        result = parse_transform("ILMAST-KEY = BR + CUS + LN")
        assert result.type == "noop"


# ---------------------------------------------------------------------------
# ConcatTransform patterns
# ---------------------------------------------------------------------------

class TestConcatTransformParser:
    def test_simple_three_field_concat(self):
        result = parse_transform("BR + CUS + LN")
        assert isinstance(result, ConcatTransform)
        assert len(result.parts) == 3
        assert result.parts[0].field_name == "BR"
        assert result.parts[1].field_name == "CUS"
        assert result.parts[2].field_name == "LN"

    def test_two_field_concat(self):
        result = parse_transform("FIELD_A + FIELD_B")
        assert isinstance(result, ConcatTransform)
        assert len(result.parts) == 2
        assert result.parts[0].field_name == "FIELD_A"
        assert result.parts[1].field_name == "FIELD_B"

    def test_concat_with_lpad_default_space(self):
        result = parse_transform("LPAD(BR,3) + CUS + LN")
        assert isinstance(result, ConcatTransform)
        assert len(result.parts) == 3
        assert result.parts[0].field_name == "BR"
        assert result.parts[0].lpad_width == 3
        assert result.parts[0].lpad_char == " "
        assert result.parts[1].field_name == "CUS"
        assert result.parts[1].lpad_width == 0

    def test_concat_with_lpad_zero_char(self):
        result = parse_transform("LPAD(BR,3,'0') + CUS")
        assert isinstance(result, ConcatTransform)
        assert result.parts[0].field_name == "BR"
        assert result.parts[0].lpad_width == 3
        assert result.parts[0].lpad_char == "0"

    def test_concat_parts_have_zero_lpad_by_default(self):
        result = parse_transform("ACCT_NBR + SEQ_NUM")
        assert isinstance(result, ConcatTransform)
        for part in result.parts:
            assert part.lpad_width == 0

    def test_concat_assignment_formula_remains_noop(self):
        """Formula with target = expression is not a concat."""
        result = parse_transform("ILMAST-KEY = BR + CUS + LN")
        assert result.type == "noop"


# ---------------------------------------------------------------------------
# FieldMapTransform patterns
# ---------------------------------------------------------------------------

class TestFieldMapTransformParser:
    def test_bare_uppercase_field_name(self):
        result = parse_transform("CUST_ID")
        assert isinstance(result, FieldMapTransform)
        assert result.source_field == "CUST_ID"

    def test_field_name_with_hyphen(self):
        result = parse_transform("ACCT-NUM")
        assert isinstance(result, FieldMapTransform)
        assert result.source_field == "ACCT-NUM"

    def test_field_name_with_digits(self):
        result = parse_transform("FIELD1")
        assert isinstance(result, FieldMapTransform)
        assert result.source_field == "FIELD1"

    def test_lowercase_text_not_field_map(self):
        """Multi-word or lowercase text is not a field map."""
        result = parse_transform("Pass as is")
        assert not isinstance(result, FieldMapTransform)

    def test_single_concat_field_is_field_map_not_concat(self):
        """A single bare token produces FieldMapTransform, not ConcatTransform."""
        result = parse_transform("LOAN_NUM")
        assert isinstance(result, FieldMapTransform)
        assert not isinstance(result, ConcatTransform)


# ---------------------------------------------------------------------------
# ConditionalTransform patterns (Phase 3d)
# ---------------------------------------------------------------------------


class TestConditionalTransformParser:
    """Tests for IF/THEN/ELSE parsing → ConditionalTransform objects."""

    # --- Null-check conditions ---

    def test_not_null_then_field_else_default(self):
        """IF FIELD not null THEN FIELD ELSE Default to '000'"""
        result = parse_transform("IF FIELD not null THEN FIELD ELSE Default to '000'")
        assert isinstance(result, ConditionalTransform)
        cond = result.condition
        assert isinstance(cond, NullCheckCondition)
        assert cond.field == "FIELD"
        assert cond.negate is True  # "not null" → IS NOT NULL
        assert isinstance(result.then_transform, FieldMapTransform)
        assert result.then_transform.source_field == "FIELD"
        assert isinstance(result.else_transform, DefaultTransform)
        assert result.else_transform.value == "000"

    def test_is_null_then_leave_blank_else_field(self):
        """IF STATUS IS NULL THEN Leave Blank ELSE STATUS"""
        result = parse_transform("IF STATUS IS NULL THEN Leave Blank ELSE STATUS")
        assert isinstance(result, ConditionalTransform)
        cond = result.condition
        assert isinstance(cond, NullCheckCondition)
        assert cond.field == "STATUS"
        assert cond.negate is False  # IS NULL → negate=False
        assert isinstance(result.then_transform, BlankTransform)
        assert isinstance(result.else_transform, FieldMapTransform)
        assert result.else_transform.source_field == "STATUS"

    def test_is_not_null_condition(self):
        """IF FIELD IS NOT NULL THEN FIELD ELSE '00000'"""
        result = parse_transform("IF FIELD IS NOT NULL THEN FIELD ELSE '00000'")
        assert isinstance(result, ConditionalTransform)
        cond = result.condition
        assert isinstance(cond, NullCheckCondition)
        assert cond.negate is True  # IS NOT NULL → negate=True

    # --- Equality conditions ---

    def test_equality_then_constant_else_constant(self):
        """IF CODE = 'A' THEN 'X' ELSE 'Y'"""
        result = parse_transform("IF CODE = 'A' THEN 'X' ELSE 'Y'")
        assert isinstance(result, ConditionalTransform)
        cond = result.condition
        assert isinstance(cond, EqualityCondition)
        assert cond.field == "CODE"
        assert cond.value == "A"
        assert cond.negate is False
        assert isinstance(result.then_transform, ConstantTransform)
        assert result.then_transform.value == "X"
        assert isinstance(result.else_transform, ConstantTransform)
        assert result.else_transform.value == "Y"

    def test_equality_not_equal_operator(self):
        """IF CODE != 'X' THEN 'YES' ELSE 'NO'"""
        result = parse_transform("IF CODE != 'X' THEN 'YES' ELSE 'NO'")
        assert isinstance(result, ConditionalTransform)
        cond = result.condition
        assert isinstance(cond, EqualityCondition)
        assert cond.field == "CODE"
        assert cond.value == "X"
        assert cond.negate is True

    def test_equality_not_equal_diamond_operator(self):
        """IF CODE <> 'X' THEN 'YES' ELSE 'NO'"""
        result = parse_transform("IF CODE <> 'X' THEN 'YES' ELSE 'NO'")
        assert isinstance(result, ConditionalTransform)
        cond = result.condition
        assert isinstance(cond, EqualityCondition)
        assert cond.negate is True

    def test_equality_then_leave_blank(self):
        """IF CHG-OFF-CD = '1' THEN M-DATE-PAID-OFF ELSE Leave Blank"""
        result = parse_transform(
            "IF CHG-OFF-CD = '1' THEN M-DATE-PAID-OFF ELSE Leave Blank"
        )
        assert isinstance(result, ConditionalTransform)
        assert isinstance(result.then_transform, FieldMapTransform)
        assert result.then_transform.source_field == "M-DATE-PAID-OFF"
        assert isinstance(result.else_transform, BlankTransform)

    # --- IN conditions ---

    def test_or_sugar_becomes_in_condition(self):
        """IF TYPE = '7' or '8' THEN 'C' ELSE 'I' → InCondition"""
        result = parse_transform("IF TYPE = '7' or '8' THEN 'C' ELSE 'I'")
        assert isinstance(result, ConditionalTransform)
        cond = result.condition
        assert isinstance(cond, InCondition)
        assert cond.field == "TYPE"
        assert set(cond.values) == {"7", "8"}
        assert isinstance(result.then_transform, ConstantTransform)
        assert result.then_transform.value == "C"
        assert isinstance(result.else_transform, ConstantTransform)
        assert result.else_transform.value == "I"

    def test_explicit_in_list(self):
        """IF FIELD IN ('A','B','C') THEN 'KNOWN' ELSE 'OTHER'"""
        result = parse_transform("IF FIELD IN ('A','B','C') THEN 'KNOWN' ELSE 'OTHER'")
        assert isinstance(result, ConditionalTransform)
        cond = result.condition
        assert isinstance(cond, InCondition)
        assert cond.field == "FIELD"
        assert set(cond.values) == {"A", "B", "C"}
        assert isinstance(result.then_transform, ConstantTransform)
        assert result.then_transform.value == "KNOWN"

    # --- ELSE clause optional ---

    def test_no_else_clause_defaults_to_noop(self):
        """IF CODE = 'A' THEN 'X' (no ELSE) → else_transform is noop"""
        result = parse_transform("IF CODE = 'A' THEN 'X'")
        assert isinstance(result, ConditionalTransform)
        assert result.else_transform.type == "noop"
        assert type(result.else_transform) is Transform

    # --- Case-insensitive keywords ---

    def test_lowercase_if_then_else(self):
        """if CODE = 'A' then 'X' else 'Y' (lowercase keywords)"""
        result = parse_transform("if CODE = 'A' then 'X' else 'Y'")
        assert isinstance(result, ConditionalTransform)
        assert isinstance(result.condition, EqualityCondition)
        assert result.condition.field == "CODE"

    def test_mixed_case_keywords(self):
        """If CODE = 'A' Then 'X' Else 'Y' (mixed case)"""
        result = parse_transform("If CODE = 'A' Then 'X' Else 'Y'")
        assert isinstance(result, ConditionalTransform)

    # --- Existing patterns still pass (regression) ---

    def test_unrecognised_text_still_noop(self):
        """Random text not matching IF pattern → noop."""
        result = parse_transform("Use the source value when available")
        assert result.type == "noop"

    def test_semicolon_syntax_still_noop(self):
        """Semicolon-separated IF; ELSE syntax is unsupported → noop."""
        result = parse_transform(
            "IF LN-BAL not null then LN-BAL; ELSE Default to +000000000000000000"
        )
        assert result.type == "noop"

    # --- Real-world patterns from mapping CSVs ---

    def test_real_world_ln_bal_not_null(self):
        """IF LN_BAL not null THEN LN_BAL ELSE Default to +000000000000000000"""
        result = parse_transform(
            "IF LN_BAL not null THEN LN_BAL ELSE Default to +000000000000000000"
        )
        assert isinstance(result, ConditionalTransform)
        assert isinstance(result.condition, NullCheckCondition)
        assert result.condition.field == "LN_BAL"
        assert result.condition.negate is True
        assert isinstance(result.then_transform, FieldMapTransform)
        assert isinstance(result.else_transform, DefaultTransform)
        assert result.else_transform.value == "+000000000000000000"


# ---------------------------------------------------------------------------
# SequentialNumberTransform patterns (Phase 3e)
# ---------------------------------------------------------------------------


class TestSequentialTransformParser:
    """Parser recognises sequential numbering patterns."""

    def test_sequential_keyword(self):
        """'Sequential' maps to SequentialNumberTransform."""
        from src.transforms.models import SequentialNumberTransform

        result = parse_transform("Sequential")
        assert isinstance(result, SequentialNumberTransform)

    def test_sequential_number_phrase(self):
        """'sequential number' maps to SequentialNumberTransform."""
        from src.transforms.models import SequentialNumberTransform

        result = parse_transform("sequential number")
        assert isinstance(result, SequentialNumberTransform)

    def test_sequence_keyword(self):
        """'sequence' maps to SequentialNumberTransform."""
        from src.transforms.models import SequentialNumberTransform

        result = parse_transform("sequence")
        assert isinstance(result, SequentialNumberTransform)

    def test_sequential_case_insensitive_upper(self):
        """'SEQUENTIAL' (all caps) maps to SequentialNumberTransform."""
        from src.transforms.models import SequentialNumberTransform

        result = parse_transform("SEQUENTIAL")
        assert isinstance(result, SequentialNumberTransform)

    def test_sequential_default_start(self):
        """Parsed SequentialNumberTransform has default start=1."""
        from src.transforms.models import SequentialNumberTransform

        result = parse_transform("Sequential")
        assert isinstance(result, SequentialNumberTransform)
        assert result.start == 1

    def test_sequential_type_attribute(self):
        """Parsed SequentialNumberTransform has type='sequential'."""
        from src.transforms.models import SequentialNumberTransform

        result = parse_transform("sequence")
        assert isinstance(result, SequentialNumberTransform)
        assert result.type == "sequential"


# ---------------------------------------------------------------------------
# NumericFormatTransform patterns
# ---------------------------------------------------------------------------

class TestNumericFormatTransformParser:
    """Parsing patterns that produce NumericFormatTransform."""

    def test_signed_picture_clause_plus_n12(self):
        """+9(12) maps to NumericFormatTransform(length=13, signed=True)."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("+9(12)")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 13
        assert result.signed is True

    def test_unsigned_picture_clause_n8(self):
        """9(8) maps to NumericFormatTransform(length=8, signed=False)."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("9(8)")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 8
        assert result.signed is False

    def test_signed_picture_clause_case_insensitive(self):
        """+9(5) is recognised regardless of surrounding whitespace."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("  +9(5)  ")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 6
        assert result.signed is True

    def test_signed_numeric_length_phrase(self):
        """'Signed numeric, length 13' maps to NumericFormatTransform(length=13, signed=True)."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("Signed numeric, length 13")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 13
        assert result.signed is True

    def test_zero_pad_to_n_phrase(self):
        """'Zero-pad to 8' maps to NumericFormatTransform(length=8, signed=False)."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("Zero-pad to 8")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 8
        assert result.signed is False

    def test_pad_to_n_digits_phrase(self):
        """'Pad to 10 digits' maps to NumericFormatTransform(length=10, signed=False)."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("Pad to 10 digits")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 10
        assert result.signed is False

    def test_numeric_format_type_attribute(self):
        """Parsed NumericFormatTransform has type='numeric_format'."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("9(8)")
        assert isinstance(result, NumericFormatTransform)
        assert result.type == "numeric_format"


# ---------------------------------------------------------------------------
# DateFormatTransform parser patterns
# ---------------------------------------------------------------------------


class TestDateFormatTransformParser:
    """Parser recognises date-format conversion patterns."""

    def test_convert_to_ccyymmdd(self):
        """'Convert to CCYYMMDD' parses to DateFormatTransform with CCYYMMDD output."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Convert to CCYYMMDD")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%Y-%m-%d"
        assert result.output_format == "%Y%m%d"
        assert result.type == "date_format"

    def test_convert_to_yyyymmdd(self):
        """'Convert to YYYYMMDD' parses to DateFormatTransform with CCYYMMDD output."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Convert to YYYYMMDD")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%Y-%m-%d"
        assert result.output_format == "%Y%m%d"

    def test_format_as_ccyymmdd(self):
        """'Format as CCYYMMDD' parses to DateFormatTransform."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Format as CCYYMMDD")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%Y-%m-%d"
        assert result.output_format == "%Y%m%d"

    def test_convert_to_ccyymmdd_case_insensitive(self):
        """Parser handles mixed case: 'convert to ccyymmdd'."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("convert to ccyymmdd")
        assert isinstance(result, DateFormatTransform)
        assert result.output_format == "%Y%m%d"

    def test_date_format_ccyymmdd(self):
        """'Date format CCYYMMDD' parses to DateFormatTransform."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Date format CCYYMMDD")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%Y-%m-%d"
        assert result.output_format == "%Y%m%d"

    def test_convert_to_mm_dd_ccyy(self):
        """'Convert to MM/DD/CCYY' parses to DateFormatTransform with MM/DD/YYYY output."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Convert to MM/DD/CCYY")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%Y-%m-%d"
        assert result.output_format == "%m/%d/%Y"

    def test_unrelated_text_not_date_format(self):
        """Non-matching text does not produce DateFormatTransform."""
        result = parse_transform("Default to '20250101'")
        from src.transforms.models import DateFormatTransform
        assert not isinstance(result, DateFormatTransform)


# ---------------------------------------------------------------------------
# ScaleTransform patterns (Phase 4c)
# ---------------------------------------------------------------------------


class TestScaleTransformParser:
    """Parser recognises multiply/divide scaling patterns."""

    def test_multiply_by_100(self):
        """'Multiply by 100' → ScaleTransform with factor=100."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("Multiply by 100")
        assert isinstance(result, ScaleTransform)
        assert result.factor == 100.0
        assert result.decimal_places == 0

    def test_multiply_by_10(self):
        """'Multiply by 10' → ScaleTransform with factor=10."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("Multiply by 10")
        assert isinstance(result, ScaleTransform)
        assert result.factor == 10.0

    def test_divide_by_100(self):
        """'Divide by 100' → ScaleTransform with factor=0.01 and decimal_places=2."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("Divide by 100")
        assert isinstance(result, ScaleTransform)
        assert abs(result.factor - 0.01) < 1e-12
        assert result.decimal_places == 2

    def test_multiply_case_insensitive(self):
        """'MULTIPLY BY 100' (all caps) → ScaleTransform."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("MULTIPLY BY 100")
        assert isinstance(result, ScaleTransform)
        assert result.factor == 100.0

    def test_divide_case_insensitive(self):
        """'divide by 1000' (all lower) → ScaleTransform."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("divide by 1000")
        assert isinstance(result, ScaleTransform)
        assert abs(result.factor - 0.001) < 1e-12

    def test_multiply_type_attribute(self):
        """Parsed ScaleTransform has type='scale'."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("Multiply by 100")
        assert isinstance(result, ScaleTransform)
        assert result.type == "scale"

    def test_divide_decimal_factor(self):
        """'Divide by 4' → factor=0.25."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("Divide by 4")
        assert isinstance(result, ScaleTransform)
        assert abs(result.factor - 0.25) < 1e-12


# ---------------------------------------------------------------------------
# PadTransform parser
# ---------------------------------------------------------------------------

class TestPadTransformParser:
    """parse_transform recognises LPAD/RPAD/pad-to-N patterns."""

    def test_left_pad_with_zero(self):
        """'Left pad to 5 with '0'' → PadTransform(length=5, pad_char='0', direction='left')."""
        result = parse_transform("Left pad to 5 with '0'")
# ---------------------------------------------------------------------------


class TestPhase4eExtendedPatterns:
    """Phase 4e — additional natural-language parser variants for Phase 4 types."""

    # --- DateFormatTransform extended patterns ---

    def test_convert_date_ccyymmdd(self):
        """'Convert date CCYYMMDD' maps to DateFormatTransform CCYYMMDD output."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Convert date CCYYMMDD")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%Y-%m-%d"
        assert result.output_format == "%Y%m%d"

    def test_reformat_date_to_yyyymmdd(self):
        """'Reformat date to YYYYMMDD' maps to DateFormatTransform CCYYMMDD output."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Reformat date to YYYYMMDD")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%Y-%m-%d"
        assert result.output_format == "%Y%m%d"

    def test_date_ccyymmdd_format_token(self):
        """'CCYYMMDD format' maps to DateFormatTransform CCYYMMDD output."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("CCYYMMDD format")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%Y-%m-%d"
        assert result.output_format == "%Y%m%d"

    def test_mmddccyy_token_in_date_format_map(self):
        """'Convert to MMDDCCYY' maps to DateFormatTransform MMDDYYYY→CCYYMMDD."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Convert to MMDDCCYY")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%m%d%Y"
        assert result.output_format == "%Y%m%d"

    def test_mmddyyyy_token_in_date_format_map(self):
        """'Convert to MMDDYYYY' maps to DateFormatTransform MMDDYYYY→CCYYMMDD."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Convert to MMDDYYYY")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%m%d%Y"
        assert result.output_format == "%Y%m%d"

    def test_reformat_date_case_insensitive(self):
        """'reformat date to CCYYMMDD' is case-insensitive."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("reformat date to CCYYMMDD")
        assert isinstance(result, DateFormatTransform)
        assert result.output_format == "%Y%m%d"

    def test_convert_date_yyyymmdd(self):
        """'Convert date YYYYMMDD' maps to DateFormatTransform CCYYMMDD output."""
        from src.transforms.models import DateFormatTransform

        result = parse_transform("Convert date YYYYMMDD")
        assert isinstance(result, DateFormatTransform)
        assert result.input_format == "%Y-%m-%d"
        assert result.output_format == "%Y%m%d"

    # --- NumericFormatTransform extended patterns ---

    def test_n_digit_zero_filled(self):
        """'9-digit zero-filled' → NumericFormatTransform(length=9, signed=False)."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("9-digit zero-filled")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 9
        assert result.signed is False

    def test_n_digit_zero_fill_without_d(self):
        """'5-digit zero-fill' (no trailing d) → NumericFormatTransform(length=5)."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("5-digit zero-fill")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 5
        assert result.signed is False

    def test_zero_fill_to_n_positions(self):
        """'Zero-fill to 10 positions' → NumericFormatTransform(length=10, signed=False)."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("Zero-fill to 10 positions")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 10
        assert result.signed is False

    def test_zerofill_to_n_position_singular(self):
        """'Zerofill to 1 position' (no hyphen, singular) → NumericFormatTransform."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("Zerofill to 1 position")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 1

    def test_signed_n_digit(self):
        """'Signed 15-digit' → NumericFormatTransform(length=15, signed=True)."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("Signed 15-digit")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 15
        assert result.signed is True

    def test_signed_n_digit_case_insensitive(self):
        """'SIGNED 8-digit' is case-insensitive."""
        from src.transforms.models import NumericFormatTransform

        result = parse_transform("SIGNED 8-digit")
        assert isinstance(result, NumericFormatTransform)
        assert result.length == 8
        assert result.signed is True

    # --- ScaleTransform extended patterns ---

    def test_scale_by_n(self):
        """'Scale by 100' → ScaleTransform(factor=100, decimal_places=0)."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("Scale by 100")
        assert isinstance(result, ScaleTransform)
        assert result.factor == 100
        assert result.decimal_places == 0

    def test_times_n(self):
        """'Times 10' → ScaleTransform(factor=10, decimal_places=0)."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("Times 10")
        assert isinstance(result, ScaleTransform)
        assert result.factor == 10
        assert result.decimal_places == 0

    def test_divide_result_by_n(self):
        """'Divide result by 100' → ScaleTransform(factor=0.01, decimal_places=0)."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("Divide result by 100")
        assert isinstance(result, ScaleTransform)
        # Divide by 100 → factor = 1/100 = 0.01
        assert abs(result.factor - (1 / 100)) < 1e-9

    def test_scale_by_decimal(self):
        """'Scale by 1.5' → ScaleTransform(factor=1.5)."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("Scale by 1.5")
        assert isinstance(result, ScaleTransform)
        assert result.factor == 1.5

    def test_scale_by_case_insensitive(self):
        """'SCALE BY 10' is case-insensitive."""
        from src.transforms.models import ScaleTransform

        result = parse_transform("SCALE BY 10")
        assert isinstance(result, ScaleTransform)
        assert result.factor == 10

    # --- PadTransform extended patterns ---

    def test_space_pad_to_n(self):
        """'Space-pad to 20' → PadTransform(length=20, pad_char=' ', direction='right')."""
        from src.transforms.models import PadTransform

        result = parse_transform("Space-pad to 20")
        assert isinstance(result, PadTransform)
        assert result.length == 20
        assert result.pad_char == " "
        assert result.direction == "right"

    def test_spacepad_no_hyphen(self):
        """'Spacepad to 15' (no hyphen) → PadTransform(length=15, pad_char=' ')."""
        from src.transforms.models import PadTransform

        result = parse_transform("Spacepad to 15")
        assert isinstance(result, PadTransform)
        assert result.length == 15
        assert result.pad_char == " "

    def test_zero_fill_left_to_n(self):
        """'Zero-fill left to 8' → PadTransform(length=8, pad_char='0', direction='left')."""
        from src.transforms.models import PadTransform

        result = parse_transform("Zero-fill left to 8")
        assert isinstance(result, PadTransform)
        assert result.length == 8
        assert result.pad_char == "0"
        assert result.direction == "left"

    def test_zerofill_left_no_hyphen(self):
        """'Zerofill left to 6' (no hyphen) → PadTransform(length=6, pad_char='0', direction='left')."""
        from src.transforms.models import PadTransform

        result = parse_transform("Zerofill left to 6")
        assert isinstance(result, PadTransform)
        assert result.length == 6
        assert result.pad_char == "0"

    def test_pad_left_n_zeros(self):
        """'Pad left 5 zeros' → PadTransform(length=5, pad_char='0', direction='left')."""
        from src.transforms.models import PadTransform

        result = parse_transform("Pad left 5 zeros")
        assert isinstance(result, PadTransform)
        assert result.length == 5
        assert result.pad_char == "0"
        assert result.direction == "left"

    def test_lpad_alias(self):
        """'LPAD to 10 with '0'' is accepted as left pad."""
        result = parse_transform("LPAD to 10 with '0'")
        assert isinstance(result, PadTransform)
        assert result.length == 10
        assert result.pad_char == "0"
        assert result.direction == "left"

    def test_right_pad_default_space(self):
        """'Right pad to 8' → PadTransform(length=8, pad_char=' ', direction='right')."""
        result = parse_transform("Right pad to 8")
        assert isinstance(result, PadTransform)
        assert result.length == 8
        assert result.pad_char == " "
        assert result.direction == "right"

    def test_pad_to_n_with_spaces(self):
        """'Pad to 10 with spaces' → PadTransform(length=10, pad_char=' ', direction='right')."""
        result = parse_transform("Pad to 10 with spaces")
        assert isinstance(result, PadTransform)
        assert result.length == 10
        assert result.pad_char == " "
        assert result.direction == "right"

    def test_pad_type_attribute(self):
        """Parsed PadTransform has type='pad'."""
        result = parse_transform("Left pad to 3 with '0'")
        assert isinstance(result, PadTransform)
        assert result.type == "pad"


# ---------------------------------------------------------------------------
# TruncateTransform parser
# ---------------------------------------------------------------------------

class TestTruncateTransformParser:
    """parse_transform recognises truncate-to-N patterns."""

    def test_truncate_to_n(self):
        """'Truncate to 10' → TruncateTransform(length=10)."""
        result = parse_transform("Truncate to 10")
        assert isinstance(result, TruncateTransform)
        assert result.length == 10

    def test_truncate_to_n_chars(self):
        """'Truncate to 8 chars' → TruncateTransform(length=8)."""
        result = parse_transform("Truncate to 8 chars")
        assert isinstance(result, TruncateTransform)
        assert result.length == 8

    def test_truncate_to_n_characters(self):
        """'Truncate to 12 characters' → TruncateTransform(length=12)."""
        result = parse_transform("Truncate to 12 characters")
        assert isinstance(result, TruncateTransform)
        assert result.length == 12

    def test_truncate_decimal_places(self):
        """'Truncate decimal places' → TruncateTransform(length=0) (special noop)."""
        result = parse_transform("Truncate decimal places")
        assert isinstance(result, TruncateTransform)
        assert result.length == 0

    def test_truncate_case_insensitive(self):
        """'TRUNCATE TO 5' (all-caps) is recognised."""
        result = parse_transform("TRUNCATE TO 5")
        assert isinstance(result, TruncateTransform)
        assert result.length == 5

    def test_truncate_type_attribute(self):
        """Parsed TruncateTransform has type='truncate'."""
        result = parse_transform("Truncate to 7")
        assert isinstance(result, TruncateTransform)
        assert result.type == "truncate"

    def test_pad_left_1_zero_singular(self):
        """'Pad left 1 zero' (singular) → PadTransform(length=1, pad_char='0', direction='left')."""
        from src.transforms.models import PadTransform

        result = parse_transform("Pad left 1 zero")
        assert isinstance(result, PadTransform)
        assert result.length == 1
        assert result.pad_char == "0"

    def test_space_pad_to_case_insensitive(self):
        """'SPACE-PAD TO 10' is case-insensitive."""
        from src.transforms.models import PadTransform

        result = parse_transform("SPACE-PAD TO 10")
        assert isinstance(result, PadTransform)
        assert result.length == 10

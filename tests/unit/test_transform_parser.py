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

"""Unit tests for the transformation text parser.

Tests cover all recognized patterns from real mapping CSVs:
  Default to, Nullable -->, Pass, Hard-code, Hard-Code,
  Initialize to spaces, Leave Blank, and unknown/empty inputs.
"""

import pytest
from src.transforms import parse_transform, Transform
from src.transforms.models import (
    DefaultTransform,
    BlankTransform,
    ConstantTransform,
    ConcatTransform,
    FieldMapTransform,
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

    def test_complex_conditional(self):
        """Conditional logic we cannot pattern-match → noop."""
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

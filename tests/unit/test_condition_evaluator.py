"""Unit tests for the condition evaluator.

Tests are written before implementation — they are expected to fail initially.
Covers evaluate_condition for NullCheckCondition: missing key, None value,
whitespace-only string, non-null value, and negated variants.

Phase 3b adds EqualityCondition and InCondition test coverage.
"""

import pytest
from src.transforms import (
    EqualityCondition,
    InCondition,
    NullCheckCondition,
    evaluate_condition,
)


class TestNullCheckCondition:
    """evaluate_condition with NullCheckCondition (negate=False means IS NULL)."""

    def test_missing_key_is_null(self):
        """Field absent from the row dict is treated as null — condition is True."""
        condition = NullCheckCondition(field="amount")
        assert evaluate_condition(condition, {}) is True

    def test_none_value_is_null(self):
        """Field present but None is treated as null — condition is True."""
        condition = NullCheckCondition(field="amount")
        assert evaluate_condition(condition, {"amount": None}) is True

    def test_whitespace_only_is_null(self):
        """Field containing only whitespace is treated as null — condition is True."""
        condition = NullCheckCondition(field="amount")
        assert evaluate_condition(condition, {"amount": "   "}) is True

    def test_empty_string_is_null(self):
        """Empty string is treated as null — condition is True."""
        condition = NullCheckCondition(field="amount")
        assert evaluate_condition(condition, {"amount": ""}) is True

    def test_non_null_value_is_false(self):
        """Field with a real value is NOT null — condition is False."""
        condition = NullCheckCondition(field="amount")
        assert evaluate_condition(condition, {"amount": "100"}) is False

    def test_zero_string_is_not_null(self):
        """The string '0' has content and is NOT null — condition is False."""
        condition = NullCheckCondition(field="amount")
        assert evaluate_condition(condition, {"amount": "0"}) is False


class TestNullCheckConditionNegated:
    """evaluate_condition with NullCheckCondition(negate=True) means IS NOT NULL."""

    def test_negated_missing_key_is_false(self):
        """Field absent from row: IS NOT NULL → False."""
        condition = NullCheckCondition(field="amount", negate=True)
        assert evaluate_condition(condition, {}) is False

    def test_negated_none_is_false(self):
        """Field is None: IS NOT NULL → False."""
        condition = NullCheckCondition(field="amount", negate=True)
        assert evaluate_condition(condition, {"amount": None}) is False

    def test_negated_whitespace_is_false(self):
        """Whitespace-only: IS NOT NULL → False."""
        condition = NullCheckCondition(field="amount", negate=True)
        assert evaluate_condition(condition, {"amount": "   "}) is False

    def test_negated_non_null_is_true(self):
        """Field with a real value: IS NOT NULL → True."""
        condition = NullCheckCondition(field="amount", negate=True)
        assert evaluate_condition(condition, {"amount": "100"}) is True

    def test_negated_zero_string_is_true(self):
        """String '0' has content: IS NOT NULL → True."""
        condition = NullCheckCondition(field="amount", negate=True)
        assert evaluate_condition(condition, {"amount": "0"}) is True


class TestNullCheckConditionType:
    """NullCheckCondition model structure."""

    def test_default_type_is_null_check(self):
        """NullCheckCondition.type defaults to 'null_check'."""
        condition = NullCheckCondition(field="x")
        assert condition.type == "null_check"

    def test_default_negate_is_false(self):
        """NullCheckCondition.negate defaults to False."""
        condition = NullCheckCondition(field="x")
        assert condition.negate is False


class TestEvaluateConditionUnsupported:
    """evaluate_condition raises TypeError for unrecognised condition objects."""

    def test_unsupported_type_raises(self):
        """Passing an unrecognised condition type raises TypeError."""
        with pytest.raises(TypeError, match="Unsupported condition type"):
            evaluate_condition(object(), {"field": "value"})


class TestEqualityCondition:
    """evaluate_condition with EqualityCondition (field == value)."""

    def test_field_equals_value_returns_true(self):
        """Field value matches the condition value → True."""
        cond = EqualityCondition(field="status", value="ACTIVE")
        assert evaluate_condition(cond, {"status": "ACTIVE"}) is True

    def test_field_does_not_equal_value_returns_false(self):
        """Field value does not match the condition value → False."""
        cond = EqualityCondition(field="status", value="ACTIVE")
        assert evaluate_condition(cond, {"status": "INACTIVE"}) is False

    def test_negated_field_equals_value_returns_false(self):
        """Negated: field equals value → False (i.e. != is not satisfied)."""
        cond = EqualityCondition(field="status", value="ACTIVE", negate=True)
        assert evaluate_condition(cond, {"status": "ACTIVE"}) is False

    def test_negated_field_differs_returns_true(self):
        """Negated: field differs from value → True (field != value is satisfied)."""
        cond = EqualityCondition(field="status", value="ACTIVE", negate=True)
        assert evaluate_condition(cond, {"status": "INACTIVE"}) is True

    def test_strips_whitespace_before_comparing(self):
        """Leading/trailing whitespace is stripped before the equality check."""
        cond = EqualityCondition(field="code", value="LS")
        assert evaluate_condition(cond, {"code": "  LS  "}) is True

    def test_missing_field_treated_as_empty_string(self):
        """Absent field behaves as empty string; equals 'ACTIVE' → False."""
        cond = EqualityCondition(field="code", value="ACTIVE")
        assert evaluate_condition(cond, {}) is False

    def test_missing_field_equals_empty_string_value(self):
        """Absent field treated as empty string; equals '' → True."""
        cond = EqualityCondition(field="code", value="")
        assert evaluate_condition(cond, {}) is True

    def test_case_sensitive_match(self):
        """Matching is case-sensitive: 'active' != 'ACTIVE'."""
        cond = EqualityCondition(field="status", value="ACTIVE")
        assert evaluate_condition(cond, {"status": "active"}) is False

    def test_default_type_is_equality(self):
        """EqualityCondition.type defaults to 'equality'."""
        cond = EqualityCondition(field="x", value="y")
        assert cond.type == "equality"

    def test_default_negate_is_false(self):
        """EqualityCondition.negate defaults to False."""
        cond = EqualityCondition(field="x", value="y")
        assert cond.negate is False


class TestInCondition:
    """evaluate_condition with InCondition (field IN values)."""

    def test_field_in_values_returns_true(self):
        """Field value is a member of the values list → True."""
        cond = InCondition(field="type", values=["A", "B", "C"])
        assert evaluate_condition(cond, {"type": "B"}) is True

    def test_field_not_in_values_returns_false(self):
        """Field value is not a member of the values list → False."""
        cond = InCondition(field="type", values=["A", "B", "C"])
        assert evaluate_condition(cond, {"type": "D"}) is False

    def test_negated_field_in_list_returns_false(self):
        """Negated: field is in the list → False (NOT IN is not satisfied)."""
        cond = InCondition(field="type", values=["A", "B", "C"], negate=True)
        assert evaluate_condition(cond, {"type": "B"}) is False

    def test_negated_field_not_in_list_returns_true(self):
        """Negated: field is not in the list → True (NOT IN is satisfied)."""
        cond = InCondition(field="type", values=["A", "B", "C"], negate=True)
        assert evaluate_condition(cond, {"type": "D"}) is True

    def test_strips_whitespace_before_membership_check(self):
        """Leading/trailing whitespace on field value is stripped before IN check."""
        cond = InCondition(field="code", values=["LS", "RS"])
        assert evaluate_condition(cond, {"code": "  LS  "}) is True

    def test_strips_whitespace_from_values_list(self):
        """Leading/trailing whitespace on list entries is stripped before comparison."""
        cond = InCondition(field="code", values=["  LS  ", "  RS  "])
        assert evaluate_condition(cond, {"code": "LS"}) is True

    def test_empty_values_list_returns_false(self):
        """Empty values list — field can never be 'in' it → False."""
        cond = InCondition(field="type", values=[])
        assert evaluate_condition(cond, {"type": "A"}) is False

    def test_missing_field_treated_as_empty_string(self):
        """Absent field behaves as empty string for IN check."""
        cond = InCondition(field="code", values=["A", "B"])
        assert evaluate_condition(cond, {}) is False

    def test_missing_field_empty_string_in_list(self):
        """Absent field as empty string matches '' in the values list → True."""
        cond = InCondition(field="code", values=["", "A"])
        assert evaluate_condition(cond, {}) is True

    def test_default_type_is_in_condition(self):
        """InCondition.type defaults to 'in_condition'."""
        cond = InCondition(field="x", values=["a"])
        assert cond.type == "in_condition"

    def test_default_negate_is_false(self):
        """InCondition.negate defaults to False."""
        cond = InCondition(field="x", values=["a"])
        assert cond.negate is False

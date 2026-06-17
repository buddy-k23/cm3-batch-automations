"""Unit tests for CrossFieldValidator (S17-4, #432).

Exercises the field-comparison, depends-on, and mutually-exclusive checks with
real DataFrames so the boolean violation masks are asserted on actual behavior.
"""

import pandas as pd
import pytest

from src.validators.cross_field_validator import CrossFieldValidator


@pytest.fixture
def validator():
    return CrossFieldValidator()


class TestFieldComparison:
    def test_numeric_greater_than_flags_violations(self, validator):
        df = pd.DataFrame({"a": [10, 5, 7], "b": [3, 8, 7]})
        # operator '>' => violation when NOT (a > b)
        mask = validator.validate_field_comparison(df, "a", ">", "b")
        # row0: 10>3 ok; row1: 5>8 violation; row2: 7>7 violation
        assert mask.tolist() == [False, True, True]

    def test_numeric_less_than(self, validator):
        df = pd.DataFrame({"a": [1, 9], "b": [2, 2]})
        mask = validator.validate_field_comparison(df, "a", "<", "b")
        assert mask.tolist() == [False, True]

    def test_greater_or_equal(self, validator):
        df = pd.DataFrame({"a": [5, 4], "b": [5, 9]})
        mask = validator.validate_field_comparison(df, "a", ">=", "b")
        assert mask.tolist() == [False, True]

    def test_less_or_equal(self, validator):
        df = pd.DataFrame({"a": [5, 10], "b": [5, 9]})
        mask = validator.validate_field_comparison(df, "a", "<=", "b")
        assert mask.tolist() == [False, True]

    def test_equality(self, validator):
        df = pd.DataFrame({"a": [1, 2], "b": [1, 3]})
        mask = validator.validate_field_comparison(df, "a", "==", "b")
        assert mask.tolist() == [False, True]

    def test_inequality(self, validator):
        df = pd.DataFrame({"a": [1, 2], "b": [2, 2]})
        mask = validator.validate_field_comparison(df, "a", "!=", "b")
        # violation when NOT (a != b) => when equal
        assert mask.tolist() == [False, True]

    def test_date_comparison_uses_datetime_path(self, validator):
        df = pd.DataFrame(
            {
                "start_date": ["2020-01-01", "2021-06-01"],
                "end_date": ["2020-02-01", "2021-05-01"],
            }
        )
        mask = validator.validate_field_comparison(
            df, "start_date", "<", "end_date"
        )
        # row0: start<end ok; row1: start>end violation
        assert mask.tolist() == [False, True]

    def test_unknown_operator_raises(self, validator):
        df = pd.DataFrame({"a": [1], "b": [1]})
        with pytest.raises(ValueError, match="Unknown comparison operator"):
            validator.validate_field_comparison(df, "a", "<>", "b")


class TestDependsOn:
    def test_violation_when_trigger_set_but_dependent_empty(self, validator):
        df = pd.DataFrame(
            {"field": ["", "x", ""], "trigger": ["yes", "yes", ""]}
        )
        mask = validator.validate_depends_on(df, "field", "trigger")
        # row0: trigger set, field empty => violation
        # row1: both set => ok
        # row2: trigger empty => ok
        assert mask.tolist() == [True, False, False]

    def test_no_violation_when_both_present(self, validator):
        df = pd.DataFrame({"field": ["a"], "trigger": ["b"]})
        assert validator.validate_depends_on(df, "field", "trigger").tolist() == [
            False
        ]


class TestMutuallyExclusive:
    def test_violation_when_two_fields_set(self, validator):
        df = pd.DataFrame(
            {"x": ["1", "", ""], "y": ["2", "", "9"], "z": ["", "", ""]}
        )
        mask = validator.validate_mutually_exclusive(df, ["x", "y", "z"])
        # row0: x and y set => violation; row1: none => ok; row2: only y => ok
        assert mask.tolist() == [True, False, False]

    def test_missing_fields_are_skipped(self, validator):
        df = pd.DataFrame({"x": ["1"]})
        # 'missing' column not present — skipped, only x counts => no violation
        mask = validator.validate_mutually_exclusive(df, ["x", "missing"])
        assert mask.tolist() == [False]

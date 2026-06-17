"""Tests for the two JSON validators (ADR 0018 §4, S19-1, #395).

Covers ``validate_json_array_length`` (against a count column) and
``validate_nested_required`` (absent-vs-null distinction), plus their
dispatch through the rule engine.
"""

import pandas as pd

from src.validators.field_validator import FieldValidator
from src.validators.rule_engine import RuleEngine


class TestValidateJsonArrayLength:
    """Flag rows whose array-count column is outside [min_len, max_len]."""

    def test_min_len_only(self):
        df = pd.DataFrame({"transactions_count": [0, 1, 5]})
        mask = FieldValidator().validate_json_array_length(df, "transactions_count", min_len=1)
        # Row 0 (count 0) violates "at least 1"; the others pass.
        assert list(mask) == [True, False, False]

    def test_min_and_max_len(self):
        df = pd.DataFrame({"transactions_count": [0, 3, 600]})
        mask = FieldValidator().validate_json_array_length(
            df, "transactions_count", min_len=1, max_len=500
        )
        assert list(mask) == [True, False, True]

    def test_non_numeric_count_is_violation(self):
        # A missing/absent array column surfaces as NA -> cannot satisfy a
        # minimum, so it is flagged.
        df = pd.DataFrame({"transactions_count": [pd.NA, 2]})
        mask = FieldValidator().validate_json_array_length(df, "transactions_count", min_len=1)
        assert list(mask) == [True, False]


class TestValidateNestedRequired:
    """Flag rows where the JSON path did not resolve (absent), not null."""

    def test_absent_flagged_present_null_not_flagged(self):
        # pd.NA == absent (key missing); None == present-with-null.
        df = pd.DataFrame({"zip": pd.Series([pd.NA, None, "10001"], dtype="object")})
        mask = FieldValidator().validate_nested_required(df, "zip")
        # Only the absent row (pd.NA) is a violation. present-null passes
        # the *presence* check (validate_not_empty handles emptiness).
        assert mask.iloc[0] == True  # noqa: E712 - absent
        assert mask.iloc[1] == False  # noqa: E712 - present-null
        assert mask.iloc[2] == False  # noqa: E712 - present value

    def test_all_present(self):
        df = pd.DataFrame({"id": ["A", "B"]})
        mask = FieldValidator().validate_nested_required(df, "id")
        assert list(mask) == [False, False]


class TestRuleEngineDispatch:
    """The two new operators dispatch through RuleEngine._validate_field."""

    def test_json_array_length_dispatch(self):
        df = pd.DataFrame({"transactions_count": [0, 2]})
        rules = {
            "rules": [
                {
                    "id": "R1", "name": "min one txn", "type": "field_validation",
                    "field": "transactions_count", "operator": "json_array_length",
                    "min_len": 1,
                }
            ]
        }
        violations = RuleEngine(rules).validate(df)
        assert len(violations) == 1
        assert violations[0].row_number == 1  # 1-indexed

    def test_json_array_length_dispatch_with_max(self):
        df = pd.DataFrame({"transactions_count": [3, 600]})
        rules = {
            "rules": [
                {
                    "id": "R2", "name": "txn cap", "type": "field_validation",
                    "field": "transactions_count", "operator": "json_array_length",
                    "min_len": 1, "max_len": 500,
                }
            ]
        }
        violations = RuleEngine(rules).validate(df)
        assert len(violations) == 1
        assert violations[0].row_number == 2

    def test_nested_required_dispatch(self):
        df = pd.DataFrame({"zip": pd.Series([pd.NA, "10001"], dtype="object")})
        rules = {
            "rules": [
                {
                    "id": "R3", "name": "zip present", "type": "field_validation",
                    "field": "zip", "operator": "nested_required",
                }
            ]
        }
        violations = RuleEngine(rules).validate(df)
        assert len(violations) == 1
        assert violations[0].row_number == 1

"""Tests for Fix 1: Fixed-width valid values trimming.

Ensures field values padded with trailing spaces (e.g. 'LS  ') match
valid values that are trimmed (e.g. 'LS') when using validate_list.
"""

import pandas as pd
import pytest

from src.validators.field_validator import FieldValidator


def test_validate_list_strips_field_value_before_comparison():
    """Padded field value 'LS  ' should match valid value 'LS'."""
    validator = FieldValidator()
    df = pd.DataFrame({"status": ["LS  ", "CS  ", "XX  "]})
    mask = validator.validate_list(df, "status", "in", ["LS", "CS"])
    # 'XX  ' is the violation; 'LS  ' and 'CS  ' should pass after stripping
    violations = df[mask]["status"].tolist()
    assert "LS  " not in violations, "Padded 'LS  ' should match valid value 'LS'"
    assert "CS  " not in violations, "Padded 'CS  ' should match valid value 'CS'"
    assert "XX  " in violations, "'XX  ' is not a valid value and should be flagged"


def test_validate_list_strips_valid_values_before_comparison():
    """Valid values with leading/trailing spaces should be trimmed."""
    validator = FieldValidator()
    df = pd.DataFrame({"status": ["LS", "CS", "XX"]})
    # valid_values list itself has padded entries
    mask = validator.validate_list(df, "status", "in", ["LS  ", "  CS"])
    violations = df[mask]["status"].tolist()
    assert "LS" not in violations, "Valid value ' LS  ' should match field 'LS' after stripping"
    assert "CS" not in violations, "Valid value '  CS' should match field 'CS' after stripping"
    assert "XX" in violations, "'XX' is not a valid value and should be flagged"


def test_validate_list_strips_both_sides():
    """Both field value and valid value are padded — should still match."""
    validator = FieldValidator()
    df = pd.DataFrame({"status": ["  LS  "]})
    mask = validator.validate_list(df, "status", "in", ["  LS  "])
    violations = df[mask]["status"].tolist()
    assert len(violations) == 0, "Stripped value should match stripped valid value"


def test_validate_list_not_in_with_trimming():
    """not_in operator should also trim before comparing."""
    validator = FieldValidator()
    df = pd.DataFrame({"status": ["LS  ", "XX"]})
    mask = validator.validate_list(df, "status", "not_in", ["LS"])
    # 'LS  '.strip() == 'LS' which IS in the not_in list, so it's a violation
    violations = df[mask]["status"].tolist()
    assert "LS  " in violations, "Padded 'LS  ' should be flagged as violation for not_in ['LS']"
    assert "XX" not in violations, "'XX' is not in the excluded list so should not be flagged"

"""Happy-path tests for RuleEngine — issue #99.

Covers equality, range, regex, required-field, and combined-rules validation
with all-pass scenarios (zero violations expected).
"""

import pandas as pd
import pytest

from src.validators.rule_engine import RuleEngine


def _make_df():
    """Return a small DataFrame used across tests."""
    return pd.DataFrame({
        "id": [1, 2, 3],
        "name": ["Alice", "Bob", "Charlie"],
        "age": [30, 25, 35],
        "status": ["ACTIVE", "ACTIVE", "ACTIVE"],
    })


class TestRuleEngineHappy:
    """All tests validate zero-violation (happy) paths."""

    def test_rule_engine_equality_check_passes(self):
        """Equality rule should produce no violations when all values match."""
        df = _make_df()
        config = {
            "rules": [
                {
                    "id": "R1",
                    "name": "Status must be ACTIVE",
                    "type": "field_validation",
                    "field": "status",
                    "operator": "in",
                    "values": ["ACTIVE"],
                    "severity": "error",
                    "enabled": True,
                }
            ]
        }
        engine = RuleEngine(config)
        violations = engine.validate(df)
        assert len(violations) == 0

    def test_rule_engine_range_check_passes(self):
        """Range rule should produce no violations for in-range values."""
        df = _make_df()
        config = {
            "rules": [
                {
                    "id": "R2",
                    "name": "Age in valid range",
                    "type": "field_validation",
                    "field": "age",
                    "operator": "range",
                    "min": 18,
                    "max": 100,
                    "severity": "error",
                    "enabled": True,
                }
            ]
        }
        engine = RuleEngine(config)
        violations = engine.validate(df)
        assert len(violations) == 0

    def test_rule_engine_regex_pattern_passes(self):
        """Regex rule should produce no violations when all values match pattern."""
        df = _make_df()
        config = {
            "rules": [
                {
                    "id": "R3",
                    "name": "Name starts with uppercase letter",
                    "type": "field_validation",
                    "field": "name",
                    "operator": "regex",
                    "pattern": r"^[A-Z]",
                    "severity": "warning",
                    "enabled": True,
                }
            ]
        }
        engine = RuleEngine(config)
        violations = engine.validate(df)
        assert len(violations) == 0

    def test_rule_engine_required_field_passes(self):
        """not_null rule should produce no violations when all values present."""
        df = _make_df()
        config = {
            "rules": [
                {
                    "id": "R4",
                    "name": "Name is required",
                    "type": "field_validation",
                    "field": "name",
                    "operator": "not_null",
                    "severity": "error",
                    "enabled": True,
                }
            ]
        }
        engine = RuleEngine(config)
        violations = engine.validate(df)
        assert len(violations) == 0

    def test_rule_engine_combined_rules_all_pass(self):
        """Multiple rules executed together should all pass on clean data."""
        df = _make_df()
        config = {
            "rules": [
                {
                    "id": "R1",
                    "name": "Status must be ACTIVE",
                    "type": "field_validation",
                    "field": "status",
                    "operator": "in",
                    "values": ["ACTIVE"],
                    "severity": "error",
                    "enabled": True,
                },
                {
                    "id": "R2",
                    "name": "Age in valid range",
                    "type": "field_validation",
                    "field": "age",
                    "operator": "range",
                    "min": 18,
                    "max": 100,
                    "severity": "error",
                    "enabled": True,
                },
                {
                    "id": "R3",
                    "name": "Name is required",
                    "type": "field_validation",
                    "field": "name",
                    "operator": "not_null",
                    "severity": "error",
                    "enabled": True,
                },
                {
                    "id": "R4",
                    "name": "Name matches pattern",
                    "type": "field_validation",
                    "field": "name",
                    "operator": "regex",
                    "pattern": r"^[A-Z]",
                    "severity": "warning",
                    "enabled": True,
                },
            ]
        }
        engine = RuleEngine(config)
        violations = engine.validate(df)
        assert len(violations) == 0

        stats = engine.get_statistics()
        assert stats["executed_rules"] == 4
        assert stats["total_violations"] == 0

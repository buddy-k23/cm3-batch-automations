"""Negative tests for rule violation detection and categorization (#107)."""

import pytest
import pandas as pd

from src.validators.rule_engine import RuleEngine, RuleViolation
from src.validators.threshold import ThresholdEvaluator, ThresholdResult, Threshold


class TestNegativeViolations:
    """Negative tests for business rule violations and thresholds."""

    def test_rule_equality_violation_detected(self):
        """A numeric equality rule (==) should flag rows that do not match."""
        rules_config = {
            "rules": [
                {
                    "id": "EQ1",
                    "name": "Code must be 100",
                    "description": "code must equal 100",
                    "type": "field_validation",
                    "severity": "error",
                    "field": "code",
                    "operator": "==",
                    "value": 100,
                    "enabled": True,
                }
            ]
        }

        df = pd.DataFrame({
            "code": ["100", "200", "300"],
        })

        engine = RuleEngine(rules_config)
        violations = engine.validate(df)

        # Rows 2 and 3 violate the equality rule (200 != 100, 300 != 100)
        assert len(violations) == 2
        violated_rows = sorted([v.row_number for v in violations])
        assert violated_rows == [2, 3]

    def test_rule_range_violation_detected(self):
        """A range rule should flag values outside [min, max]."""
        rules_config = {
            "rules": [
                {
                    "id": "RNG1",
                    "name": "Score in range",
                    "description": "score must be 1-100",
                    "type": "field_validation",
                    "severity": "error",
                    "field": "score",
                    "operator": "range",
                    "min": 1,
                    "max": 100,
                    "enabled": True,
                }
            ]
        }

        df = pd.DataFrame({
            "score": ["50", "0", "101", "75"],
        })

        engine = RuleEngine(rules_config)
        violations = engine.validate(df)

        # 0 is below min, 101 is above max
        assert len(violations) == 2
        violated_rows = sorted([v.row_number for v in violations])
        assert violated_rows == [2, 3]

    def test_rule_regex_violation(self):
        """A regex rule should flag values that do not match the pattern."""
        rules_config = {
            "rules": [
                {
                    "id": "RGX1",
                    "name": "Email format",
                    "description": "must match email pattern",
                    "type": "field_validation",
                    "severity": "warning",
                    "field": "email",
                    "operator": "regex",
                    "pattern": r"^[^@]+@[^@]+\.[^@]+$",
                    "enabled": True,
                }
            ]
        }

        df = pd.DataFrame({
            "email": ["alice@example.com", "not-an-email", "bob@test.org", ""],
        })

        engine = RuleEngine(rules_config)
        violations = engine.validate(df)

        # "not-an-email" and "" do not match the pattern
        assert len(violations) >= 2
        assert all(v.severity == "warning" for v in violations)

    def test_threshold_fails_by_error_count(self):
        """When missing_rows exceed the threshold, evaluation should FAIL."""
        comparison_results = {
            "total_rows_file1": 50,
            "total_rows_file2": 50,
            "only_in_file1": [{"id": i} for i in range(20)],
            "only_in_file2": [],
            "differences": [],
            "rows_with_differences": 0,
            "field_statistics": {},
        }

        evaluator = ThresholdEvaluator()
        result = evaluator.evaluate(comparison_results)

        assert result["passed"] is False
        assert result["overall_result"] == ThresholdResult.FAIL
        assert result["metrics"]["missing_rows"] == 20

    def test_violations_categorized_by_severity(self):
        """Violations should be categorized correctly by severity in statistics."""
        rules_config = {
            "rules": [
                {
                    "id": "ERR1",
                    "name": "Error rule",
                    "description": "error severity",
                    "type": "field_validation",
                    "severity": "error",
                    "field": "a",
                    "operator": "not_null",
                    "enabled": True,
                },
                {
                    "id": "WARN1",
                    "name": "Warning rule",
                    "description": "warning severity",
                    "type": "field_validation",
                    "severity": "warning",
                    "field": "b",
                    "operator": "not_null",
                    "enabled": True,
                },
            ]
        }

        df = pd.DataFrame({
            "a": ["", "val"],
            "b": ["val", ""],
        })

        engine = RuleEngine(rules_config)
        violations = engine.validate(df)
        stats = engine.get_statistics()

        assert stats["violations_by_severity"]["error"] >= 1
        assert stats["violations_by_severity"]["warning"] >= 1
        assert stats["total_violations"] == len(violations)

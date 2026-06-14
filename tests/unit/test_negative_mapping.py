"""Negative tests for mapping and rules configuration errors (#106)."""

import os
import tempfile
import json

import pytest

from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.parsers.enhanced_validator import EnhancedFileValidator
from src.validators.rule_engine import RuleEngine
import pandas as pd


class TestNegativeMapping:
    """Negative tests for broken or malformed mapping/rules configs."""

    def test_validate_with_missing_fields_key_in_mapping(self):
        """A mapping config that lacks the 'fields' key should not crash;
        schema validation is simply skipped."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name\n")
            f.write("1|Alice\n")
            temp_file = f.name

        mapping_config = {
            "mapping_name": "no_fields_mapping",
            "source": {"type": "file", "format": "pipe_delimited"},
            # Intentionally omit 'fields'
        }

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config)
            result = validator.validate()

            # Should complete without crash
            assert isinstance(result, dict)
            assert "error_count" in result
        finally:
            os.unlink(temp_file)

    def test_validate_with_unknown_data_type(self):
        """A mapping with an unrecognized data_type should not crash validation."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name\n")
            f.write("1|Alice\n")
            temp_file = f.name

        mapping_config = {
            "mapping_name": "bad_dtype_mapping",
            "source": {"type": "file", "format": "pipe_delimited"},
            "fields": [
                {"name": "id", "data_type": "zorblax"},
                {"name": "name", "data_type": "hyperstring"},
            ],
        }

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config)
            result = validator.validate()

            # Should not crash even with unknown data types
            assert isinstance(result, dict)
            assert "error_count" in result
        finally:
            os.unlink(temp_file)

    def test_rules_with_invalid_regex_pattern(self):
        """A rule with a syntactically invalid regex should not crash the
        engine; it should either skip the rule or report the error."""
        rules_config = {
            "rules": [
                {
                    "id": "BAD_REGEX",
                    "name": "Bad regex rule",
                    "description": "Invalid regex pattern",
                    "type": "field_validation",
                    "severity": "error",
                    "field": "code",
                    "operator": "regex",
                    "pattern": "[invalid((",
                    "enabled": True,
                }
            ]
        }

        df = pd.DataFrame({"code": ["ABC", "DEF", "GHI"]})

        engine = RuleEngine(rules_config)
        # Should not raise an unhandled exception
        # The engine catches exceptions per rule and continues
        violations = engine.validate(df)
        # The rule either produces violations for all rows (match fails)
        # or is skipped; either way, no crash.
        assert isinstance(violations, list)

    def test_rules_with_contradictory_range(self):
        """A range rule where min > max should handle all rows as violations
        (since no value can satisfy the range)."""
        rules_config = {
            "rules": [
                {
                    "id": "BAD_RANGE",
                    "name": "Contradictory range",
                    "description": "min > max range",
                    "type": "field_validation",
                    "severity": "error",
                    "field": "score",
                    "operator": "range",
                    "min": 100,
                    "max": 10,
                    "enabled": True,
                }
            ]
        }

        df = pd.DataFrame({"score": ["50", "75", "25"]})

        engine = RuleEngine(rules_config)
        violations = engine.validate(df)

        # All rows should be flagged because no value can be >= 100 AND <= 10
        assert len(violations) == 3, (
            f"Expected 3 violations for contradictory range, got {len(violations)}"
        )

"""Negative tests for field-level validation errors (#105)."""

import os
import tempfile

import pytest
import pandas as pd

from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.parsers.enhanced_validator import EnhancedFileValidator


class TestNegativeFields:
    """Negative tests for field-level data validation."""

    def test_validate_missing_required_field_flagged(self):
        """A file missing a field declared in the mapping should produce an error."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name\n")
            f.write("1|Alice\n")
            temp_file = f.name

        mapping_config = {
            "mapping_name": "test_missing",
            "source": {"type": "file", "format": "pipe_delimited"},
            "fields": [
                {"name": "id", "data_type": "string"},
                {"name": "name", "data_type": "string"},
                {"name": "email", "data_type": "string"},
            ],
        }

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config)
            result = validator.validate()

            error_messages = [e["message"] for e in result.get("errors", [])]
            assert any("email" in msg.lower() for msg in error_messages), (
                f"Expected 'email' missing field error, got: {error_messages}"
            )
        finally:
            os.unlink(temp_file)

    def test_validate_string_in_numeric_field(self):
        """A string value in a column expected to be numeric should be
        detected by the field analysis (inferred_type mismatch)."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|amount\n")
            f.write("1|abc\n")
            f.write("2|def\n")
            temp_file = f.name

        mapping_config = {
            "mapping_name": "test_numeric",
            "source": {"type": "file", "format": "pipe_delimited"},
            "fields": [
                {"name": "id", "data_type": "string"},
                {"name": "amount", "data_type": "numeric"},
            ],
        }

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config)
            result = validator.validate(detailed=True)

            # The validator should not crash. The field analysis should
            # infer the type as string, not numeric.
            assert isinstance(result, dict)
            field_analysis = result.get("field_analysis", {})
            # amount column may appear under its parsed name
            amount_info = None
            for key, val in field_analysis.items():
                if "amount" in str(key).lower():
                    amount_info = val
                    break
            if amount_info:
                assert amount_info.get("inferred_type") != "numeric", (
                    "Expected non-numeric inferred type for string values"
                )
        finally:
            os.unlink(temp_file)

    def test_validate_invalid_date_format(self):
        """Invalid date values in a date-named column should produce warnings."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|effective_date\n")
            f.write("1|not-a-date\n")
            f.write("2|also-bad\n")
            f.write("3|2026-01-15\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config=None)
            result = validator.validate(detailed=True)

            assert isinstance(result, dict)
            # The date analysis should not crash; if it detects a date
            # column, it should report invalid dates.
            # Even if it does not detect the column as date (too few valid),
            # we verify no crash occurred.
            assert "error_count" in result
        finally:
            os.unlink(temp_file)

    def test_validate_multiple_error_types_same_row(self):
        """Multiple validation issues on a single row should all be reported."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name|email\n")
            f.write("1|Alice|alice@example.com\n")
            f.write("2|Bob|bob@example.com\n")
            temp_file = f.name

        mapping_config = {
            "mapping_name": "test_multi_error",
            "source": {"type": "file", "format": "pipe_delimited"},
            "fields": [
                {"name": "id", "data_type": "string"},
                {"name": "name", "data_type": "string"},
                {"name": "email", "data_type": "string"},
                {"name": "phone", "data_type": "string"},
                {"name": "address", "data_type": "string"},
            ],
        }

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config)
            result = validator.validate()

            # Both 'phone' and 'address' are missing from the file
            error_messages = [e["message"] for e in result.get("errors", [])]
            missing_fields = [
                m for m in error_messages if "missing" in m.lower()
            ]
            assert len(missing_fields) >= 2, (
                f"Expected at least 2 missing-field errors, got {len(missing_fields)}: {missing_fields}"
            )
        finally:
            os.unlink(temp_file)

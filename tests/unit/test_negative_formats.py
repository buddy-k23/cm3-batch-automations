"""Negative tests for file format detection and validation edge cases (#104)."""

import os
import tempfile

import pytest

from src.parsers.format_detector import FormatDetector, FileFormat
from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.parsers.enhanced_validator import EnhancedFileValidator


class TestNegativeFormats:
    """Negative tests for format detection and validation."""

    def test_validate_empty_file_returns_error(self):
        """An empty file should produce a validation error, not crash."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config=None)
            result = validator.validate()

            assert result["valid"] is False
            # Should report an empty-file error
            error_messages = [e["message"] for e in result["errors"]]
            assert any("empty" in msg.lower() for msg in error_messages), (
                f"Expected 'empty' in error messages, got: {error_messages}"
            )
        finally:
            os.unlink(temp_file)

    def test_validate_file_with_only_header_no_data(self):
        """A file with a header row but zero data rows should still validate
        gracefully and report zero rows or a warning."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("customer_id|name|status\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config=None)
            result = validator.validate()

            # Should not crash; total_rows should be <= 1 (header-only)
            assert isinstance(result, dict)
            # The result must contain error_count or quality_metrics
            total = result.get("quality_metrics", {}).get("total_rows", 0)
            # A single header line parsed with header=None yields 1 row
            assert total <= 1
        finally:
            os.unlink(temp_file)

    def test_detect_ambiguous_format_returns_low_confidence(self):
        """A file with no clear delimiters should get low confidence or UNKNOWN."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("hello world\n")
            f.write("foo bar\n")
            f.write("baz qux\n")
            temp_file = f.name

        try:
            detector = FormatDetector()
            result = detector.detect(temp_file)

            # Either low confidence or format is UNKNOWN / FIXED_WIDTH with low score
            assert result["confidence"] < 0.95 or result["format"] in (
                FileFormat.UNKNOWN,
                FileFormat.FIXED_WIDTH,
            )
        finally:
            os.unlink(temp_file)

    def test_validate_wrong_mapping_for_format(self):
        """Using a fixed-width mapping against a pipe-delimited file should
        produce errors or at least not crash."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name|amount\n")
            f.write("1|Alice|100\n")
            f.write("2|Bob|200\n")
            temp_file = f.name

        mapping_config = {
            "mapping_name": "wrong_mapping",
            "source": {"type": "file", "format": "pipe_delimited"},
            "fields": [
                {"name": "customer_id", "data_type": "string"},
                {"name": "full_name", "data_type": "string"},
                {"name": "balance", "data_type": "numeric"},
            ],
        }

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config)
            result = validator.validate()

            # The mapping field names don't match the file columns, so
            # schema validation should flag missing fields.
            assert isinstance(result, dict)
            all_messages = [e["message"] for e in result.get("errors", [])]
            all_messages += [w["message"] for w in result.get("warnings", [])]
            has_schema_issue = any(
                "missing" in m.lower() or "unexpected" in m.lower()
                for m in all_messages
            )
            assert has_schema_issue, (
                f"Expected schema mismatch messages, got: {all_messages}"
            )
        finally:
            os.unlink(temp_file)

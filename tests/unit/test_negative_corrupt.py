"""Negative tests for corrupt / malformed file handling (#108)."""

import os
import tempfile

import pytest

from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.parsers.enhanced_validator import EnhancedFileValidator


class TestNegativeCorrupt:
    """Negative tests for corrupt or malformed input files."""

    def test_validate_truncated_file(self):
        """A file that appears truncated (inconsistent column counts)
        should be handled gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name|amount\n")
            f.write("1|Alice|100\n")
            f.write("2|Bob\n")  # Truncated: missing 'amount' column
            f.write("3|Charlie|300\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config=None)
            result = validator.validate()

            # Should not crash
            assert isinstance(result, dict)
            assert "error_count" in result
        finally:
            os.unlink(temp_file)

    def test_validate_file_with_mixed_line_endings(self):
        """A file with mixed line endings (\\r\\n and \\n) should parse
        without crashing."""
        with tempfile.NamedTemporaryFile(mode="wb", delete=False, suffix=".txt") as f:
            f.write(b"id|name|status\r\n")
            f.write(b"1|Alice|ACTIVE\n")
            f.write(b"2|Bob|INACTIVE\r\n")
            f.write(b"3|Charlie|ACTIVE\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config=None)
            result = validator.validate()

            assert isinstance(result, dict)
            total = result.get("quality_metrics", {}).get("total_rows", 0)
            assert total >= 3
        finally:
            os.unlink(temp_file)

    def test_validate_file_with_extra_columns(self):
        """A file with more columns than the mapping expects should flag
        extra/unexpected fields."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name|amount|extra_col\n")
            f.write("1|Alice|100|bonus\n")
            f.write("2|Bob|200|extra\n")
            temp_file = f.name

        mapping_config = {
            "mapping_name": "test_extra_cols",
            "source": {"type": "file", "format": "pipe_delimited"},
            "fields": [
                {"name": "id", "data_type": "string"},
                {"name": "name", "data_type": "string"},
                {"name": "amount", "data_type": "numeric"},
            ],
        }

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config)
            result = validator.validate()

            # Should report extra/unexpected field warnings
            all_messages = [w["message"] for w in result.get("warnings", [])]
            has_extra = any("unexpected" in m.lower() for m in all_messages)
            assert has_extra, (
                f"Expected 'unexpected field' warning, got: {all_messages}"
            )
        finally:
            os.unlink(temp_file)

    def test_validate_file_with_missing_columns(self):
        """A file with fewer columns than the mapping expects should flag
        missing fields as errors."""
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".txt") as f:
            f.write("id|name\n")
            f.write("1|Alice\n")
            f.write("2|Bob\n")
            temp_file = f.name

        mapping_config = {
            "mapping_name": "test_missing_cols",
            "source": {"type": "file", "format": "pipe_delimited"},
            "fields": [
                {"name": "id", "data_type": "string"},
                {"name": "name", "data_type": "string"},
                {"name": "amount", "data_type": "numeric"},
                {"name": "status", "data_type": "string"},
            ],
        }

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = EnhancedFileValidator(parser, mapping_config)
            result = validator.validate()

            error_messages = [e["message"] for e in result.get("errors", [])]
            missing = [m for m in error_messages if "missing" in m.lower()]
            assert len(missing) >= 2, (
                f"Expected at least 2 missing-field errors, got {len(missing)}: {missing}"
            )
        finally:
            os.unlink(temp_file)

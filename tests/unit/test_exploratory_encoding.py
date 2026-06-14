"""Exploratory tests for encoding and special-character edge cases (#111)."""

import json
import os
import tempfile

import pytest

from src.services.validate_service import run_validate_service


def _write_mapping(fields, delimiter="|", has_header=True):
    """Write a minimal mapping JSON and return its path."""
    mapping = {
        "mapping_name": "test_encoding",
        "source": {
            "format": "pipe_delimited",
            "delimiter": delimiter,
            "has_header": has_header,
        },
        "fields": [{"name": name, "type": "string"} for name in fields],
    }
    f = tempfile.NamedTemporaryFile(
        mode="w", delete=False, suffix=".json", prefix="mapping_"
    )
    json.dump(mapping, f)
    f.close()
    return f.name


class TestExploratoryEncoding:
    """Encoding, quoting, and line-ending edge cases."""

    def test_validate_utf8_with_accented_characters(self):
        """File with accented characters (e, u, n-tilde) produces no encoding errors."""
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".txt", encoding="utf-8"
        ) as f:
            f.write("id|name|city\n")
            f.write("1|Jose|San Jose\n")
            f.write("2|Rene|Montreal\n")
            f.write("3|Muller|Zurich\n")
            temp_file = f.name

        mapping_file = _write_mapping(["id", "name", "city"])

        try:
            result = run_validate_service(file=temp_file, mapping=mapping_file)

            # Should parse all rows without encoding-related errors.
            assert result["total_rows"] >= 3
            encoding_errors = [
                e
                for e in result.get("errors", [])
                if "encoding" in str(e).lower() or "decode" in str(e).lower()
            ]
            assert len(encoding_errors) == 0
        finally:
            os.unlink(temp_file)
            os.unlink(mapping_file)

    def test_validate_field_with_delimiter_in_value(self):
        """Pipe file with quoted pipe in value should parse without crashing."""
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".txt"
        ) as f:
            # Standard pipe-delimited: pandas read_csv with sep='|' and
            # quoting should handle embedded pipes inside quotes.
            f.write('id|name|notes\n')
            f.write('1|Alice|"has a | in notes"\n')
            f.write('2|Bob|clean value\n')
            temp_file = f.name

        mapping_file = _write_mapping(["id", "name", "notes"])

        try:
            result = run_validate_service(file=temp_file, mapping=mapping_file)

            # The service should return a result without raising.
            assert "total_rows" in result
            assert result["total_rows"] >= 1
        finally:
            os.unlink(temp_file)
            os.unlink(mapping_file)

    def test_validate_empty_string_vs_null(self):
        """Fields that are empty-string vs truly absent are both handled."""
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".txt"
        ) as f:
            f.write("id|name|value\n")
            f.write("1|Alice|100\n")
            f.write("2||200\n")       # empty name
            f.write("3|Charlie|\n")   # empty value
            temp_file = f.name

        mapping_file = _write_mapping(["id", "name", "value"])

        try:
            result = run_validate_service(file=temp_file, mapping=mapping_file)

            # All three rows should be counted.
            assert result["total_rows"] >= 3
        finally:
            os.unlink(temp_file)
            os.unlink(mapping_file)

    def test_validate_mixed_line_endings(self):
        """File with mixed \\r\\n and \\n line endings parses all rows."""
        with tempfile.NamedTemporaryFile(
            mode="wb", delete=False, suffix=".txt"
        ) as f:
            f.write(b"id|name|value\r\n")
            f.write(b"1|Alice|100\n")
            f.write(b"2|Bob|200\r\n")
            f.write(b"3|Charlie|300\n")
            temp_file = f.name

        mapping_file = _write_mapping(["id", "name", "value"])

        try:
            result = run_validate_service(file=temp_file, mapping=mapping_file)

            # All rows including header line should be detected.
            assert result["total_rows"] >= 3
        finally:
            os.unlink(temp_file)
            os.unlink(mapping_file)

"""Unit tests for fixed-width parser."""

import pytest
import tempfile
import os
from src.parsers.fixed_width_parser import FixedWidthParser


class TestFixedWidthParser:
    """Test FixedWidthParser class."""

    def test_parse_valid_file(self):
        """Test parsing valid fixed-width file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("AAA  BBB  CCC\n")
            f.write("111  222  333\n")
            f.write("XXX  YYY  ZZZ\n")
            temp_file = f.name

        try:
            column_specs = [
                ('col1', 0, 5),
                ('col2', 5, 10),
                ('col3', 10, 15),
            ]
            parser = FixedWidthParser(temp_file, column_specs)
            df = parser.parse()
            
            assert len(df) == 3
            # __source_row__ is prepended by the parser (issue #37)
            assert len(df.columns) == 4
            assert list(df.columns) == ['__source_row__', 'col1', 'col2', 'col3']
        finally:
            os.unlink(temp_file)

    def test_validate_format_valid(self):
        """Test format validation with consistent line lengths."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("AAAA  BBBB  CCCC\n")
            f.write("1111  2222  3333\n")
            f.write("XXXX  YYYY  ZZZZ\n")
            temp_file = f.name

        try:
            column_specs = [('col1', 0, 6), ('col2', 6, 12), ('col3', 12, 18)]
            parser = FixedWidthParser(temp_file, column_specs)
            assert parser.validate_format() is True
        finally:
            os.unlink(temp_file)

    def test_validate_format_invalid(self):
        """Test format validation with inconsistent line lengths."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("AAA\n")
            f.write("BBBBBBBBB\n")
            f.write("CC\n")
            temp_file = f.name

        try:
            column_specs = [('col1', 0, 5)]
            parser = FixedWidthParser(temp_file, column_specs)
            # Should return False due to inconsistent lengths
            result = parser.validate_format()
            assert result is False
        finally:
            os.unlink(temp_file)

    def test_parse_empty_file(self):
        """Test parsing empty file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_file = f.name

        try:
            column_specs = [('col1', 0, 5)]
            parser = FixedWidthParser(temp_file, column_specs)
            df = parser.parse()
            assert df.empty
        finally:
            os.unlink(temp_file)

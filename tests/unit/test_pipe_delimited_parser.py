"""Unit tests for pipe-delimited parser."""

import pytest
import tempfile
import os
import pandas as pd
from src.parsers.pipe_delimited_parser import PipeDelimitedParser


class TestPipeDelimitedParser:
    """Test PipeDelimitedParser class."""

    def test_parse_valid_file(self):
        """Test parsing valid pipe-delimited file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("value1|value2|value3\n")
            f.write("value4|value5|value6\n")
            f.write("value7|value8|value9\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file, columns=['col1', 'col2', 'col3'])
            df = parser.parse()
            
            assert len(df) == 3
            # __source_row__ is prepended by the parser (issue #37)
            assert len(df.columns) == 4
            assert list(df.columns) == ['__source_row__', 'col1', 'col2', 'col3']
            assert df['col1'].iloc[0] == 'value1'
            assert df['col2'].iloc[1] == 'value5'
        finally:
            os.unlink(temp_file)

    def test_parse_without_column_names(self):
        """Test parsing without specifying column names."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("a|b|c\n")
            f.write("d|e|f\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            df = parser.parse()
            
            assert len(df) == 2
            # __source_row__ is prepended by the parser (issue #37), plus 3 data columns
            assert len(df.columns) == 4
        finally:
            os.unlink(temp_file)

    def test_validate_format_valid(self):
        """Test format validation with valid file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("a|b|c\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            assert parser.validate_format() is True
        finally:
            os.unlink(temp_file)

    def test_validate_format_invalid(self):
        """Test format validation with invalid file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("a,b,c\n")  # CSV, not pipe-delimited
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            assert parser.validate_format() is False
        finally:
            os.unlink(temp_file)

    def test_parse_empty_file(self):
        """Test parsing empty file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            df = parser.parse()
            assert df.empty
        finally:
            os.unlink(temp_file)

    def test_parse_with_empty_values(self):
        """Test parsing file with empty values."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("a||c\n")
            f.write("|b|\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file, columns=['col1', 'col2', 'col3'])
            df = parser.parse()
            
            assert df['col1'].iloc[0] == 'a'
            assert df['col2'].iloc[0] == ''
            assert df['col3'].iloc[0] == 'c'
        finally:
            os.unlink(temp_file)

    def test_get_metadata(self):
        """Test getting file metadata."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("a|b|c\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            metadata = parser.get_metadata()
            
            assert 'file_path' in metadata
            assert 'file_size' in metadata
            assert 'modified_time' in metadata
            assert metadata['file_path'] == temp_file
            assert metadata['file_size'] > 0
        finally:
            os.unlink(temp_file)

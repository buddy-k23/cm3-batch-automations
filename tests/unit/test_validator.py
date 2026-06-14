"""Unit tests for file validator."""

import pytest
import tempfile
import os
from src.parsers.pipe_delimited_parser import PipeDelimitedParser
from src.parsers.validator import FileValidator, SchemaValidator
import pandas as pd


class TestFileValidator:
    """Test FileValidator class."""

    def test_validate_valid_file(self):
        """Test validation of valid file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            f.write("col1|col2|col3\n")
            f.write("val1|val2|val3\n")
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = FileValidator(parser)
            result = validator.validate()
            
            assert result['valid'] is True
            assert len(result['errors']) == 0
        finally:
            os.unlink(temp_file)

    def test_validate_file_not_found(self):
        """Test validation with non-existent file."""
        parser = PipeDelimitedParser('nonexistent.txt')
        validator = FileValidator(parser)
        result = validator.validate()
        
        assert result['valid'] is False
        assert any('not found' in error.lower() for error in result['errors'])

    def test_validate_empty_file(self):
        """Test validation of empty file."""
        with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.txt') as f:
            temp_file = f.name

        try:
            parser = PipeDelimitedParser(temp_file)
            validator = FileValidator(parser)
            result = validator.validate()
            
            assert result['valid'] is False
            assert any('empty' in error.lower() for error in result['errors'])
        finally:
            os.unlink(temp_file)


class TestSchemaValidator:
    """Test SchemaValidator class."""

    def test_validate_matching_schema(self):
        """Test validation with matching schema."""
        df = pd.DataFrame({
            'col1': ['a', 'b'],
            'col2': ['c', 'd'],
            'col3': ['e', 'f']
        })
        
        validator = SchemaValidator(['col1', 'col2', 'col3'])
        result = validator.validate(df)
        
        assert result['valid'] is True
        assert len(result['errors']) == 0

    def test_validate_missing_required_column(self):
        """Test validation with missing required column."""
        df = pd.DataFrame({
            'col1': ['a', 'b'],
            'col2': ['c', 'd']
        })
        
        validator = SchemaValidator(['col1', 'col2', 'col3'], required_columns=['col1', 'col3'])
        result = validator.validate(df)
        
        assert result['valid'] is False
        assert len(result['errors']) > 0
        assert 'col3' in str(result['errors'])

    def test_validate_unexpected_columns(self):
        """Test validation with unexpected columns."""
        df = pd.DataFrame({
            'col1': ['a', 'b'],
            'col2': ['c', 'd'],
            'col4': ['g', 'h']
        })
        
        validator = SchemaValidator(['col1', 'col2', 'col3'])
        result = validator.validate(df)
        
        assert result['valid'] is True  # Unexpected columns are warnings, not errors
        assert len(result['warnings']) > 0
        assert 'col4' in str(result['warnings'])

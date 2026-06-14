"""Unit tests for mapping parser."""

import pytest
import json
from src.config.mapping_parser import MappingParser, MappingProcessor
import pandas as pd


class TestMappingParser:
    """Test MappingParser class."""

    def test_parse_valid_mapping(self):
        """Test parsing valid mapping document."""
        mapping_dict = {
            'mapping_name': 'test_mapping',
            'version': '1.0.0',
            'description': 'Test mapping',
            'source': {'type': 'file', 'format': 'pipe_delimited'},
            'target': {'type': 'database', 'table_name': 'TEST_TABLE'},
            'mappings': [
                {
                    'source_column': 'col1',
                    'target_column': 'COL1',
                    'data_type': 'string',
                    'required': True,
                    'transformations': [],
                    'validation_rules': []
                }
            ],
            'key_columns': ['col1']
        }
        
        parser = MappingParser()
        mapping = parser.parse(mapping_dict)
        
        assert mapping.mapping_name == 'test_mapping'
        assert mapping.version == '1.0.0'
        assert len(mapping.mappings) == 1
        assert mapping.mappings[0].source_column == 'col1'

    def test_parse_missing_required_field(self):
        """Test parsing with missing required field."""
        mapping_dict = {
            'mapping_name': 'test_mapping',
            'version': '1.0.0'
            # Missing other required fields
        }
        
        parser = MappingParser()
        
        with pytest.raises(ValueError):
            parser.parse(mapping_dict)


class TestMappingProcessor:
    """Test MappingProcessor class."""

    def test_apply_trim_transformation(self):
        """Test trim transformation."""
        mapping_dict = {
            'mapping_name': 'test',
            'version': '1.0.0',
            'description': 'Test',
            'source': {'type': 'file'},
            'target': {'type': 'database'},
            'mappings': [
                {
                    'source_column': 'col1',
                    'target_column': 'COL1',
                    'data_type': 'string',
                    'required': True,
                    'transformations': [{'type': 'trim'}],
                    'validation_rules': []
                }
            ],
            'key_columns': ['col1']
        }
        
        parser = MappingParser()
        mapping = parser.parse(mapping_dict)
        processor = MappingProcessor(mapping)
        
        df = pd.DataFrame({'col1': ['  test  ', ' value ']})
        result = processor.apply_transformations(df)
        
        assert result['col1'].iloc[0] == 'test'
        assert result['col1'].iloc[1] == 'value'

    def test_apply_upper_transformation(self):
        """Test upper transformation."""
        mapping_dict = {
            'mapping_name': 'test',
            'version': '1.0.0',
            'description': 'Test',
            'source': {'type': 'file'},
            'target': {'type': 'database'},
            'mappings': [
                {
                    'source_column': 'col1',
                    'target_column': 'COL1',
                    'data_type': 'string',
                    'required': True,
                    'transformations': [{'type': 'upper'}],
                    'validation_rules': []
                }
            ],
            'key_columns': ['col1']
        }
        
        parser = MappingParser()
        mapping = parser.parse(mapping_dict)
        processor = MappingProcessor(mapping)
        
        df = pd.DataFrame({'col1': ['test', 'value']})
        result = processor.apply_transformations(df)
        
        assert result['col1'].iloc[0] == 'TEST'
        assert result['col1'].iloc[1] == 'VALUE'

    def test_validate_not_null_rule(self):
        """Test not_null validation rule."""
        mapping_dict = {
            'mapping_name': 'test',
            'version': '1.0.0',
            'description': 'Test',
            'source': {'type': 'file'},
            'target': {'type': 'database'},
            'mappings': [
                {
                    'source_column': 'col1',
                    'target_column': 'COL1',
                    'data_type': 'string',
                    'required': True,
                    'transformations': [],
                    'validation_rules': [{'type': 'not_null'}]
                }
            ],
            'key_columns': ['col1']
        }
        
        parser = MappingParser()
        mapping = parser.parse(mapping_dict)
        processor = MappingProcessor(mapping)
        
        # Valid data
        df_valid = pd.DataFrame({'col1': ['test', 'value']})
        result_valid = processor.validate_data(df_valid)
        assert result_valid['valid'] is True
        
        # Invalid data with nulls
        df_invalid = pd.DataFrame({'col1': ['test', None]})
        result_invalid = processor.validate_data(df_invalid)
        assert result_invalid['valid'] is False

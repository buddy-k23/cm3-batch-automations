"""Unit tests for configuration loader."""

import pytest
import tempfile
import os
import json
from src.config.loader import ConfigLoader


class TestConfigLoader:
    """Test ConfigLoader class."""

    def test_load_valid_config(self):
        """Test loading valid configuration file."""
        # Create temporary config directory
        with tempfile.TemporaryDirectory() as temp_dir:
            config_file = os.path.join(temp_dir, 'test.json')
            config_data = {
                'environment': 'test',
                'database': {
                    'username': 'test_user',
                    'dsn': 'localhost:1521/TEST'
                }
            }
            
            with open(config_file, 'w') as f:
                json.dump(config_data, f)
            
            loader = ConfigLoader(config_dir=temp_dir)
            config = loader.load('test')
            
            assert config['environment'] == 'test'
            assert config['database']['username'] == 'test_user'

    def test_load_nonexistent_config(self):
        """Test loading non-existent configuration file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            loader = ConfigLoader(config_dir=temp_dir)
            
            with pytest.raises(FileNotFoundError):
                loader.load('nonexistent')

    def test_load_mapping(self):
        """Test loading mapping file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create mappings subdirectory
            mappings_dir = os.path.join(temp_dir, 'mappings')
            os.makedirs(mappings_dir)
            
            mapping_file = os.path.join(mappings_dir, 'test_mapping.json')
            mapping_data = {
                'col1': 'COL1',
                'col2': 'COL2'
            }
            
            with open(mapping_file, 'w') as f:
                json.dump(mapping_data, f)
            
            loader = ConfigLoader(config_dir=temp_dir)
            mapping = loader.load_mapping('test_mapping.json')
            
            assert mapping['col1'] == 'COL1'
            assert mapping['col2'] == 'COL2'

    def test_merge_with_env(self):
        """Test merging configuration with environment variables."""
        config = {
            'database': {
                'username': 'default_user',
                'dsn': 'default_dsn'
            }
        }
        
        # Set environment variables
        os.environ['ORACLE_USER'] = 'env_user'
        os.environ['ORACLE_DSN'] = 'env_dsn'
        
        try:
            merged = ConfigLoader.merge_with_env(config)
            
            assert merged['database']['username'] == 'env_user'
            assert merged['database']['dsn'] == 'env_dsn'
        finally:
            # Clean up environment variables
            os.environ.pop('ORACLE_USER', None)
            os.environ.pop('ORACLE_DSN', None)

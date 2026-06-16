"""Unit tests for configuration loader.

Note:
    The previous ``test_load_valid_config``, ``test_load_nonexistent_config``,
    and ``test_merge_with_env`` tests were removed in S16-4 (#424) along with
    the dead ``ConfigLoader.load(environment)`` / ``merge_with_env`` methods
    they exercised — those methods read ``config/<env>.json`` but had no runtime
    consumer (an operator trap).  Only :meth:`ConfigLoader.load_mapping` remains
    live (reconcile + ETL pipeline) and is tested here.
"""

import json
import os
import tempfile

from src.config.loader import ConfigLoader


class TestConfigLoader:
    """Test ConfigLoader class."""

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

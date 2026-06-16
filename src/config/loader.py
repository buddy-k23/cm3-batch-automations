"""Load column-mapping configuration files.

Note:
    The former ``ConfigLoader.load(environment)`` method (which read
    ``config/<env>.json``) and ``merge_with_env`` were removed in S16-4 (#424):
    they were exercised only by tests and had **no** runtime consumer — the
    live database configuration is resolved from environment variables via
    :func:`src.config.db_config.get_db_config` (and the secrets provider).
    Editing ``config/<env>.json`` therefore had zero runtime effect, an operator
    trap.  Only :meth:`ConfigLoader.load_mapping` is live (used by reconcile and
    the ETL pipeline runner) and is retained.
"""

import json
import os
from typing import Dict


class ConfigLoader:
    """Loads column-mapping JSON files for reconcile / ETL pipeline steps."""

    def __init__(self, config_dir: str = "config"):
        """Initialize config loader.

        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = config_dir

    def load_mapping(self, mapping_file: str) -> Dict[str, str]:
        """Load column mapping configuration.

        Args:
            mapping_file: Path to mapping JSON file. May be an absolute path,
                a path relative to cwd, or a filename resolved under
                <config_dir>/mappings/.

        Returns:
            Mapping dictionary
        """
        # Use the path directly if it is absolute or already exists as given
        if os.path.isabs(mapping_file) or os.path.exists(mapping_file):
            mapping_path = mapping_file
        else:
            mapping_path = os.path.join(self.config_dir, "mappings", mapping_file)

        if not os.path.exists(mapping_path):
            raise FileNotFoundError(f"Mapping file not found: {mapping_path}")
        
        with open(mapping_path, "r") as f:
            mapping = json.load(f)

        return mapping

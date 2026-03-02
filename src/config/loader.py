"""Load and manage configuration files."""

import json
import os
from typing import Dict, Any


class ConfigLoader:
    """Loads configuration from JSON files."""

    def __init__(self, config_dir: str = "config"):
        """Initialize config loader.
        
        Args:
            config_dir: Directory containing configuration files
        """
        self.config_dir = config_dir

    def load(self, environment: str = "dev") -> Dict[str, Any]:
        """Load configuration for specified environment.
        
        Args:
            environment: Environment name (dev, staging, prod)
            
        Returns:
            Configuration dictionary
        """
        config_file = os.path.join(self.config_dir, f"{environment}.json")
        
        if not os.path.exists(config_file):
            raise FileNotFoundError(f"Configuration file not found: {config_file}")
        
        with open(config_file, "r") as f:
            config = json.load(f)
        
        return config

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

    @staticmethod
    def merge_with_env(config: Dict[str, Any]) -> Dict[str, Any]:
        """Merge configuration with environment variables.
        
        Args:
            config: Base configuration dictionary
            
        Returns:
            Merged configuration
        """
        merged = config.copy()
        
        if "ORACLE_USER" in os.environ:
            merged.setdefault("database", {})["username"] = os.environ["ORACLE_USER"]
        if "ORACLE_PASSWORD" in os.environ:
            merged.setdefault("database", {})["password"] = os.environ["ORACLE_PASSWORD"]
        if "ORACLE_DSN" in os.environ:
            merged.setdefault("database", {})["dsn"] = os.environ["ORACLE_DSN"]
        
        return merged

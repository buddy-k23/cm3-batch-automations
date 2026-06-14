"""Configuration management."""

from .loader import ConfigLoader
from .db_config import DbConfig, get_db_config, get_connection
from .mapping_parser import MappingParser, MappingProcessor, MappingDocument, ColumnMapping
from .models import (
    FieldConfig,
    MappingConfig,
    RuleConfig,
    RulesConfig,
    RulesMetadata,
    SourceConfig,
)

__all__ = [
    "ConfigLoader",
    # Database configuration (issue #48)
    "DbConfig",
    "get_db_config",
    "get_connection",
    "MappingParser",
    "MappingProcessor",
    "MappingDocument",
    "ColumnMapping",
    # Pydantic typed models (issue #89)
    "FieldConfig",
    "MappingConfig",
    "RuleConfig",
    "RulesConfig",
    "RulesMetadata",
    "SourceConfig",
]

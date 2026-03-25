"""Configuration management."""

from .loader import ConfigLoader
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

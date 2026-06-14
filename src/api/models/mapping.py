"""Pydantic models for API requests and responses."""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime


class FieldSpec(BaseModel):
    """Field specification model."""
    name: str
    source_name: Optional[str] = None
    target_name: Optional[str] = None
    position: Optional[int] = None
    length: Optional[int] = None
    data_type: str
    format: Optional[str] = None
    required: bool = False
    default_value: Optional[Any] = None
    transformations: List[Dict[str, Any]] = []
    validation_rules: List[Dict[str, Any]] = []
    description: Optional[str] = None


class SourceConfig(BaseModel):
    """Source configuration model."""
    type: str
    format: str
    file_path: Optional[str] = None
    delimiter: Optional[str] = None
    encoding: str = "UTF-8"
    has_header: bool = False


class TargetConfig(BaseModel):
    """Target configuration model."""
    type: str = "database"
    table_name: Optional[str] = None
    file_path: Optional[str] = None


class MappingMetadata(BaseModel):
    """Mapping metadata model."""
    created_by: str = "api_user"
    created_date: str
    last_modified: str
    source_template: Optional[str] = None
    notes: Optional[str] = None


class MappingCreate(BaseModel):
    """Model for creating a new mapping."""
    mapping_name: str
    version: str = "1.0.0"
    description: Optional[str] = None
    source: SourceConfig
    target: Optional[TargetConfig] = None
    fields: List[FieldSpec]
    key_columns: List[str] = []


class MappingResponse(BaseModel):
    """Model for mapping response."""
    id: str
    mapping_name: str
    version: str
    description: Optional[str]
    source: SourceConfig
    target: Optional[TargetConfig]
    fields: List[FieldSpec]
    key_columns: List[str]
    metadata: Optional[MappingMetadata] = None
    total_fields: int
    total_record_length: Optional[int] = None


class MappingListItem(BaseModel):
    """Model for mapping list item."""
    id: str
    mapping_name: str
    version: str
    format: str
    total_fields: int
    created_date: str


class ValidationResult(BaseModel):
    """Model for validation result."""
    valid: bool
    errors: List[str] = []
    warnings: List[str] = []


class UploadResponse(BaseModel):
    """Model for file upload response."""
    filename: str
    size: int
    mapping_id: Optional[str] = None
    message: str

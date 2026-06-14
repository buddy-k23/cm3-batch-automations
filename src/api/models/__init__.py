"""Initialize API models package."""

from .mapping import (
    FieldSpec,
    SourceConfig,
    TargetConfig,
    MappingMetadata,
    MappingCreate,
    MappingResponse,
    MappingListItem,
    ValidationResult,
    UploadResponse
)
from .file import (
    FileDetectionResult,
    FileParseRequest,
    FileParseResult,
    FileCompareRequest,
    FileCompareResult,
    FileValidationResult
)
from .response import (
    SuccessResponse,
    ErrorResponse,
    HealthResponse,
    SystemInfoResponse
)

__all__ = [
    # Mapping models
    "FieldSpec",
    "SourceConfig",
    "TargetConfig",
    "MappingMetadata",
    "MappingCreate",
    "MappingResponse",
    "MappingListItem",
    "ValidationResult",
    "UploadResponse",
    # File models
    "FileDetectionResult",
    "FileParseRequest",
    "FileParseResult",
    "FileCompareRequest",
    "FileCompareResult",
    "FileValidationResult",
    # Response models
    "SuccessResponse",
    "ErrorResponse",
    "HealthResponse",
    "SystemInfoResponse",
]

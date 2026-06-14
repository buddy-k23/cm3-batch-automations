"""Pydantic models for common API responses."""

from pydantic import BaseModel
from typing import Optional, Any


class SuccessResponse(BaseModel):
    """Model for success response."""
    success: bool = True
    message: str
    data: Optional[Any] = None


class ErrorResponse(BaseModel):
    """Model for error response."""
    success: bool = False
    error: str
    details: Optional[str] = None


class HealthResponse(BaseModel):
    """Model for health check response."""
    status: str
    version: str
    timestamp: str


class SystemInfoResponse(BaseModel):
    """Model for system information response."""
    python_version: str
    api_version: str
    supported_formats: list
    database_connected: bool

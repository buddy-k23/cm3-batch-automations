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
    """Model for health check (liveness) response."""
    status: str
    version: str
    timestamp: str


class ReadinessResponse(BaseModel):
    """Model for the readiness probe response.

    Attributes:
        ready: ``True`` when the app can serve DB-backed traffic.
        database_connected: Result of the cheap, bounded DB connectivity probe.
        timestamp: ISO-8601 UTC timestamp of the check.
    """
    ready: bool
    database_connected: bool
    timestamp: str


class SystemInfoResponse(BaseModel):
    """Model for system information response."""
    python_version: str
    api_version: str
    supported_formats: list
    database_connected: bool

"""Pydantic models for file operations."""

from pydantic import BaseModel
from typing import List, Optional, Dict, Any


class FileDetectionResult(BaseModel):
    """Model for file format detection result."""
    format: str
    confidence: float
    delimiter: Optional[str] = None
    line_count: int
    record_length: Optional[int] = None
    sample_lines: List[str] = []


class FileParseRequest(BaseModel):
    """Model for file parse request."""
    mapping_id: str
    output_format: str = "csv"  # csv, json, excel


class FileParseResult(BaseModel):
    """Model for file parse result."""
    rows_parsed: int
    columns: int
    preview: List[Dict[str, Any]] = []
    download_url: Optional[str] = None
    errors: List[str] = []


class FileCompareRequest(BaseModel):
    """Model for file comparison request."""
    mapping_id: str
    key_columns: List[str]
    detailed: bool = True


class FileCompareResult(BaseModel):
    """Model for file comparison result."""
    total_rows_file1: int
    total_rows_file2: int
    matching_rows: int
    only_in_file1: int
    only_in_file2: int
    differences: int
    report_url: Optional[str] = None
    field_statistics: Optional[Dict[str, Any]] = None


class FileValidateRequest(BaseModel):
    """Model for file validate request."""
    mapping_id: str
    detailed: bool = True
    strict_fixed_width: bool = False
    strict_level: str = "format"
    output_html: bool = True


class FileValidationResult(BaseModel):
    """Model for file validation result."""
    valid: bool
    total_rows: int
    valid_rows: int
    invalid_rows: int
    errors: List[Dict[str, Any]] = []
    warnings: List[str] = []
    quality_score: Optional[float] = None
    report_url: Optional[str] = None


class FileCompareAsyncCreateResponse(BaseModel):
    """Response when creating an async compare job."""
    job_id: str
    status: str


class FileCompareAsyncStatusResponse(BaseModel):
    """Async compare job status/result."""
    job_id: str
    status: str
    result: Optional[FileCompareResult] = None
    error: Optional[str] = None

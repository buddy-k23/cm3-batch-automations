from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ThresholdConfig(BaseModel):
    max_errors: int = 0
    max_warnings: Optional[int] = None
    max_missing_rows: Optional[int] = None
    max_extra_rows: Optional[int] = None
    max_different_rows_pct: Optional[float] = None


class TestConfig(BaseModel):
    name: str
    type: Literal["structural", "oracle_vs_file", "rules", "api_check"]
    file: Optional[str] = None  # supports ${variable} placeholders; not required for api_check
    mapping: Optional[str] = None  # not required for api_check
    rules: Optional[str] = None  # for type=rules
    oracle_query: Optional[str] = None  # for type=oracle_vs_file
    oracle_params: Optional[Dict] = None
    key_columns: Optional[List[str]] = None
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)
    # api_check type fields
    url: Optional[str] = None
    method: Optional[str] = "GET"
    body: Optional[Dict] = None
    expected_status: Optional[int] = 200
    response_contains: Optional[Dict] = None
    timeout_seconds: Optional[int] = 30


class TestSuiteConfig(BaseModel):
    name: str
    environment: str = "dev"
    tests: List[TestConfig]

"""Pydantic models for the API Tester feature."""
from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel


class HeaderPair(BaseModel):
    """A single HTTP header key-value pair.

    Attributes:
        key: The header name (e.g. 'Authorization').
        value: The header value (e.g. 'Bearer token123').
    """

    key: str
    value: str


class FormField(BaseModel):
    """A form data field; is_file=True means the value is a file upload.

    Attributes:
        key: The field name.
        value: The field value (empty string for file fields).
        is_file: When True, value is ignored and a file upload is expected.
    """

    key: str
    value: str = ""
    is_file: bool = False


class Assertion(BaseModel):
    """A single test assertion on a proxy response.

    Attributes:
        field: The field to assert on: 'status_code' or a JSONPath like '$.key'.
        operator: Comparison operator: 'equals', 'contains', or 'exists'.
        expected: The expected value to compare against.
    """

    field: str        # "status_code" | "$.jsonPath"
    operator: str     # "equals" | "contains" | "exists"
    expected: str = ""


class SuiteRequest(BaseModel):
    """One HTTP request inside a test suite.

    Attributes:
        id: Unique identifier for this request within the suite.
        name: Human-readable name for the request step.
        method: HTTP method (GET, POST, PUT, DELETE, etc.).
        path: Path appended to the suite's base_url.
        headers: List of custom HTTP headers to send.
        body_type: Type of request body: 'none', 'json', or 'form'.
        body_json: Raw JSON string for body_type='json'.
        form_fields: List of form fields for body_type='form'.
        assertions: List of assertions to evaluate on the response.
    """

    id: str
    name: str
    method: str
    path: str
    headers: list[HeaderPair] = []
    body_type: str = "none"   # none | json | form
    body_json: str = ""
    form_fields: list[FormField] = []
    assertions: list[Assertion] = []


class SuiteCreate(BaseModel):
    """Payload for creating or updating a suite.

    Attributes:
        name: Human-readable name for the suite.
        base_url: Base URL prepended to each request's path.
        requests: Ordered list of HTTP requests in this suite.
    """

    name: str
    base_url: str
    requests: list[SuiteRequest] = []


class Suite(SuiteCreate):
    """A stored test suite with its assigned id.

    Attributes:
        id: Unique identifier for the suite (UUID).
    """

    id: str


class SuiteSummary(BaseModel):
    """Lightweight suite info for the list endpoint.

    Attributes:
        id: Unique identifier for the suite.
        name: Human-readable suite name.
        base_url: Base URL for the suite.
        request_count: Number of requests in the suite.
    """

    id: str
    name: str
    base_url: str
    request_count: int


class ProxyResponse(BaseModel):
    """Result returned by the proxy endpoint.

    Attributes:
        status_code: HTTP status code from the proxied response.
        headers: Response headers as a flat string dict.
        body: Response body as text.
        elapsed_ms: Round-trip time in milliseconds.
        error: Optional error message if the request partially failed.
    """

    status_code: int
    headers: dict[str, str]
    body: str
    elapsed_ms: float
    error: Optional[str] = None

"""FastAPI file endpoint regression tests using TestClient.

Note: The /parse and /compare endpoints use FastAPI Body() combined with
File() in a multipart form.  FastAPI/Pydantic v2 treats form parts as
plain strings, so the JSON-body ``request`` field cannot be decoded
automatically in multipart context.  Tests for those endpoints therefore
validate the HTTP contract (status codes, required-field validation) rather
than exercising the full parsing path.
"""

import io
import pytest
from fastapi.testclient import TestClient
from src.api.main import app
from src.api.auth import AuthContext, verify_session_or_api_key

client = TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True, scope="module")
def _authenticate_api():
    """Authenticate every request in this module as an admin AuthContext.

    These regression tests predate the auth-hardening change and call the
    file endpoints with no ``X-API-Key`` header, so they were exercising the
    pre-auth open-access behaviour. The endpoints now correctly fail closed
    (401, or 503 when no keys are configured at all). Overriding the shared
    base dependency ``verify_session_or_api_key`` makes ``require_api_key``
    and every ``require_role(...)`` resolve to this test context, so the
    tests reach the real handlers again. Product auth is unchanged — the
    override is installed only for this module and removed on teardown.

    None of the tests in this file assert an auth rejection (401/403), so
    applying the override module-wide does not weaken any fail-closed
    coverage.
    """
    app.dependency_overrides[verify_session_or_api_key] = (
        lambda: AuthContext(key_id="uat-int-test", role="admin")
    )
    yield
    app.dependency_overrides.pop(verify_session_or_api_key, None)

# Minimal pipe-delimited content that matches customer_batch_universal mapping
_PIPE_CONTENT = (
    b"customer_id|first_name|last_name|email|phone|account_balance|status\n"
    b"1001|Alice|Johnson|alice@example.com|555-0101|1250.50|ACTIVE\n"
    b"1002|Bob|Smith|bob@example.com|555-0102|980.00|INACTIVE\n"
)

_MAPPING_ID = "customer_batch_universal"


# ---------------------------------------------------------------------------
# /detect tests
# ---------------------------------------------------------------------------

def test_detect_valid_file_returns_200():
    """POST /api/v1/files/detect with a valid file returns 200."""
    files = {"file": ("test.txt", io.BytesIO(_PIPE_CONTENT), "text/plain")}
    response = client.post("/api/v1/files/detect", files=files)
    assert response.status_code == 200


def test_detect_result_has_format_field():
    """POST /api/v1/files/detect result has 'format' field."""
    files = {"file": ("test.txt", io.BytesIO(_PIPE_CONTENT), "text/plain")}
    response = client.post("/api/v1/files/detect", files=files)
    assert response.status_code == 200
    data = response.json()
    assert "format" in data


def test_detect_result_has_confidence_float():
    """POST /api/v1/files/detect result has 'confidence' as float between 0 and 1."""
    files = {"file": ("test.txt", io.BytesIO(_PIPE_CONTENT), "text/plain")}
    response = client.post("/api/v1/files/detect", files=files)
    assert response.status_code == 200
    data = response.json()
    assert "confidence" in data
    conf = data["confidence"]
    assert isinstance(conf, float)
    assert 0.0 <= conf <= 1.0


def test_detect_empty_body_returns_422():
    """POST /api/v1/files/detect with no file returns 422."""
    response = client.post("/api/v1/files/detect")
    assert response.status_code == 422


def test_detect_pipe_delimited_format():
    """POST /api/v1/files/detect identifies pipe-delimited content correctly."""
    files = {"file": ("test.txt", io.BytesIO(_PIPE_CONTENT), "text/plain")}
    response = client.post("/api/v1/files/detect", files=files)
    assert response.status_code == 200
    data = response.json()
    # pipe-delimited content should be detected as pipe_delimited or csv
    assert data["format"] in ("pipe_delimited", "csv", "fixed_width")


# ---------------------------------------------------------------------------
# /parse tests — verify HTTP contract
# ---------------------------------------------------------------------------

def test_parse_endpoint_exists():
    """POST /api/v1/files/parse endpoint exists (returns non-404 when file is sent)."""
    files = {"file": ("test.txt", io.BytesIO(_PIPE_CONTENT), "text/plain")}
    response = client.post("/api/v1/files/parse", files=files)
    # 422 = endpoint exists but validation failed; 404 = missing endpoint
    assert response.status_code != 404


def test_parse_no_file_returns_422():
    """POST /api/v1/files/parse with no file returns 422 Unprocessable Entity."""
    response = client.post("/api/v1/files/parse")
    assert response.status_code == 422


def test_parse_missing_request_body_returns_422():
    """POST /api/v1/files/parse with file but missing mapping_id Form field returns 422."""
    files = {"file": ("test.txt", io.BytesIO(_PIPE_CONTENT), "text/plain")}
    # Sending only the file without the required 'mapping_id' Form field
    response = client.post("/api/v1/files/parse", files=files)
    assert response.status_code == 422
    # Validate the error points to the 'mapping_id' field
    detail = response.json().get("detail", [])
    locs = [str(err.get("loc", "")) for err in detail]
    assert any("mapping_id" in loc for loc in locs)


# ---------------------------------------------------------------------------
# /compare tests — verify HTTP contract
# ---------------------------------------------------------------------------

def test_compare_endpoint_exists():
    """POST /api/v1/files/compare endpoint exists (returns non-404)."""
    files = {
        "file1": ("file1.txt", io.BytesIO(_PIPE_CONTENT), "text/plain"),
        "file2": ("file2.txt", io.BytesIO(_PIPE_CONTENT), "text/plain"),
    }
    response = client.post("/api/v1/files/compare", files=files)
    assert response.status_code != 404


def test_compare_no_files_returns_422():
    """POST /api/v1/files/compare with no files returns 422."""
    response = client.post("/api/v1/files/compare")
    assert response.status_code == 422


def test_compare_missing_request_body_returns_422():
    """POST /api/v1/files/compare with files but no mapping_id Form field returns 422."""
    files = {
        "file1": ("file1.txt", io.BytesIO(_PIPE_CONTENT), "text/plain"),
        "file2": ("file2.txt", io.BytesIO(_PIPE_CONTENT), "text/plain"),
    }
    response = client.post("/api/v1/files/compare", files=files)
    assert response.status_code == 422
    detail = response.json().get("detail", [])
    locs = [str(err.get("loc", "")) for err in detail]
    assert any("mapping_id" in loc for loc in locs)

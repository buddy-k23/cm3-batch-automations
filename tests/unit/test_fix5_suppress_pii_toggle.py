"""Tests for Fix 5: suppress_pii form parameter accepted by validate endpoint.

Verifies that the /api/v1/files/validate endpoint accepts the suppress_pii
form field and passes it through to run_validate_service.
"""

import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient
from pathlib import Path


def _make_app():
    """Create a minimal FastAPI app with only the files router mounted."""
    from fastapi import FastAPI
    from src.api.routers.files import router
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/files")
    return app


def _minimal_result():
    return {
        "valid": True,
        "errors": [],
        "warnings": [],
        "total_rows": 5,
        "valid_rows": 5,
        "error_count": 0,
        "warning_count": 0,
        "quality_score": 100,
        "elapsed_seconds": 0.01,
    }


def test_validate_endpoint_accepts_suppress_pii_true(tmp_path):
    """Endpoint must accept suppress_pii=true and pass suppress_pii=True to service."""
    mapping_dir = tmp_path / "config" / "mappings"
    mapping_dir.mkdir(parents=True)
    mapping_file = mapping_dir / "test_map.json"
    mapping_file.write_text(
        '{"mapping_name":"test_map","source":{"format":"pipe_delimited"},"fields":[]}',
        encoding="utf-8",
    )
    data_content = b"col1|col2\nA|B\n"

    with patch("src.api.routers.files.MAPPINGS_DIR", mapping_dir), \
         patch("src.api.routers.files.UPLOADS_DIR", tmp_path), \
         patch("src.api.routers.files.run_validate_service") as mock_svc, \
         patch("src.api.routers.files._should_use_chunked", return_value=False):

        mock_svc.return_value = _minimal_result()

        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/files/validate",
            data={"mapping_id": "test_map", "suppress_pii": "true"},
            files={"file": ("data.txt", data_content, "text/plain")},
        )

        assert resp.status_code == 200, resp.text
        call_kwargs = mock_svc.call_args
        assert call_kwargs is not None
        # suppress_pii should be passed as True
        passed_pii = call_kwargs.kwargs.get("suppress_pii", call_kwargs.args[0] if call_kwargs.args else None)
        # Check via keyword args
        kw = mock_svc.call_args[1] if mock_svc.call_args[1] else {}
        assert kw.get("suppress_pii") is True, f"suppress_pii=True was not forwarded; kwargs={kw}"


def test_validate_endpoint_accepts_suppress_pii_false(tmp_path):
    """Endpoint must accept suppress_pii=false and pass suppress_pii=False to service."""
    mapping_dir = tmp_path / "config" / "mappings"
    mapping_dir.mkdir(parents=True)
    mapping_file = mapping_dir / "test_map.json"
    mapping_file.write_text(
        '{"mapping_name":"test_map","source":{"format":"pipe_delimited"},"fields":[]}',
        encoding="utf-8",
    )
    data_content = b"col1|col2\nA|B\n"

    with patch("src.api.routers.files.MAPPINGS_DIR", mapping_dir), \
         patch("src.api.routers.files.UPLOADS_DIR", tmp_path), \
         patch("src.api.routers.files.run_validate_service") as mock_svc, \
         patch("src.api.routers.files._should_use_chunked", return_value=False):

        mock_svc.return_value = _minimal_result()

        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/files/validate",
            data={"mapping_id": "test_map", "suppress_pii": "false"},
            files={"file": ("data.txt", data_content, "text/plain")},
        )

        assert resp.status_code == 200, resp.text
        kw = mock_svc.call_args[1] if mock_svc.call_args[1] else {}
        assert kw.get("suppress_pii") is False, f"suppress_pii=False was not forwarded; kwargs={kw}"


def test_validate_endpoint_defaults_suppress_pii_true(tmp_path):
    """When suppress_pii is omitted, the service must default to True (safe)."""
    mapping_dir = tmp_path / "config" / "mappings"
    mapping_dir.mkdir(parents=True)
    mapping_file = mapping_dir / "test_map.json"
    mapping_file.write_text(
        '{"mapping_name":"test_map","source":{"format":"pipe_delimited"},"fields":[]}',
        encoding="utf-8",
    )
    data_content = b"col1|col2\nA|B\n"

    with patch("src.api.routers.files.MAPPINGS_DIR", mapping_dir), \
         patch("src.api.routers.files.UPLOADS_DIR", tmp_path), \
         patch("src.api.routers.files.run_validate_service") as mock_svc, \
         patch("src.api.routers.files._should_use_chunked", return_value=False):

        mock_svc.return_value = _minimal_result()

        app = _make_app()
        client = TestClient(app)

        resp = client.post(
            "/api/v1/files/validate",
            data={"mapping_id": "test_map"},
            files={"file": ("data.txt", data_content, "text/plain")},
        )

        assert resp.status_code == 200, resp.text
        kw = mock_svc.call_args[1] if mock_svc.call_args[1] else {}
        assert kw.get("suppress_pii") is True, f"Default suppress_pii should be True; kwargs={kw}"

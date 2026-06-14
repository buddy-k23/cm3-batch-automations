"""Tests for multi-record YAML support in POST /api/v1/files/validate.

Issue #213: when a multi_record_config file is supplied the endpoint must
route to MultiRecordValidator instead of the standard field-level validator.

Tests cover:
- 422 when neither mapping_id nor multi_record_config is provided
- 200 with valid multi-record YAML (mapping_id not required)
- 400 when multi_record_config is invalid YAML
- 200 with both mapping_id and multi_record_config (multi-record takes precedence)
"""

from __future__ import annotations

import io
import os
import textwrap
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

_api_keys = os.getenv("API_KEYS", "")
if "dev-key" not in {k.split(":", 1)[0].strip() for k in _api_keys.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys},dev-key:admin" if _api_keys else "dev-key:admin"

client = TestClient(app, raise_server_exceptions=True)
_HEADERS = {"X-API-Key": "dev-key"}

# Minimal valid multi-record YAML used throughout tests
_MULTI_RECORD_YAML = textwrap.dedent("""\
    multi_record:
      discriminator:
        position: 1
        length: 3
      record_types:
        HDR:
          mapping_id: test_mapping
        DTL:
          mapping_id: test_mapping
        TRL:
          mapping_id: test_mapping
""")

_BATCH_CONTENT = b"HDR" + b"X" * 17 + b"\n" + b"DTL" + b"X" * 17 + b"\n" + b"TRL" + b"X" * 17 + b"\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_batch_file(content: bytes = _BATCH_CONTENT, name: str = "batch.txt") -> tuple:
    return (name, io.BytesIO(content), "text/plain")


def _make_yaml_file(content: str = _MULTI_RECORD_YAML, name: str = "config.yaml") -> tuple:
    return (name, io.BytesIO(content.encode()), "application/x-yaml")


# ---------------------------------------------------------------------------
# Validation gate — neither mapping_id nor multi_record_config
# ---------------------------------------------------------------------------

class TestValidateMissingInputs:
    def test_missing_both_returns_422(self):
        """No mapping_id and no multi_record_config → 422."""
        resp = client.post(
            "/api/v1/files/validate",
            files={"file": _make_batch_file()},
            headers=_HEADERS,
        )
        assert resp.status_code == 422

    def test_only_mapping_id_still_works(self):
        """Existing behaviour: mapping_id alone is accepted (404 if unknown mapping)."""
        resp = client.post(
            "/api/v1/files/validate",
            files={"file": _make_batch_file()},
            data={"mapping_id": "nonexistent_mapping"},
            headers=_HEADERS,
        )
        # 404 because mapping doesn't exist — not 422
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Multi-record YAML path
# ---------------------------------------------------------------------------

class TestValidateWithMultiRecordConfig:
    def test_multi_record_yaml_accepted_without_mapping_id(self):
        """multi_record_config alone (no mapping_id) → 200 or 404/500 but not 422."""
        mock_result = {
            "valid": True,
            "total_rows": 3,
            "record_type_results": {},
            "cross_type_violations": [],
        }
        with patch(
            "src.api.routers.files.run_multi_record_validate_service",
            return_value=mock_result,
        ) as mock_svc:
            resp = client.post(
                "/api/v1/files/validate",
                files={
                    "file": _make_batch_file(),
                    "multi_record_config": _make_yaml_file(),
                },
                headers=_HEADERS,
            )
        assert resp.status_code == 200
        assert mock_svc.called

    def test_multi_record_result_has_valid_field(self):
        """Response body contains 'valid' key when multi-record path taken."""
        mock_result = {
            "valid": True,
            "total_rows": 3,
            "record_type_results": {},
            "cross_type_violations": [],
        }
        with patch(
            "src.api.routers.files.run_multi_record_validate_service",
            return_value=mock_result,
        ):
            resp = client.post(
                "/api/v1/files/validate",
                files={
                    "file": _make_batch_file(),
                    "multi_record_config": _make_yaml_file(),
                },
                headers=_HEADERS,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "valid" in data

    def test_multi_record_invalid_yaml_returns_400(self):
        """Malformed YAML in multi_record_config → 400."""
        bad_yaml = b": this is not : valid yaml :\n  - broken"
        resp = client.post(
            "/api/v1/files/validate",
            files={
                "file": _make_batch_file(),
                "multi_record_config": ("bad.yaml", io.BytesIO(bad_yaml), "application/x-yaml"),
            },
            headers=_HEADERS,
        )
        assert resp.status_code == 400

    def test_multi_record_takes_precedence_over_mapping_id(self):
        """When both mapping_id and multi_record_config are supplied, multi-record path runs."""
        mock_result = {
            "valid": True,
            "total_rows": 3,
            "record_type_results": {},
            "cross_type_violations": [],
        }
        with patch(
            "src.api.routers.files.run_multi_record_validate_service",
            return_value=mock_result,
        ) as mock_svc:
            resp = client.post(
                "/api/v1/files/validate",
                files={
                    "file": _make_batch_file(),
                    "multi_record_config": _make_yaml_file(),
                },
                data={"mapping_id": "some_mapping"},
                headers=_HEADERS,
            )
        assert resp.status_code == 200
        assert mock_svc.called

    def test_failed_multi_record_returns_valid_false(self):
        """Service returns valid=False → response body valid=False."""
        mock_result = {
            "valid": False,
            "total_rows": 3,
            "record_type_results": {},
            "cross_type_violations": [{"severity": "error", "message": "Missing TRL"}],
        }
        with patch(
            "src.api.routers.files.run_multi_record_validate_service",
            return_value=mock_result,
        ):
            resp = client.post(
                "/api/v1/files/validate",
                files={
                    "file": _make_batch_file(),
                    "multi_record_config": _make_yaml_file(),
                },
                headers=_HEADERS,
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False

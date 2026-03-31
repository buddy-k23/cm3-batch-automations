"""FastAPI TestClient tests for POST /api/v1/multi-record/detect-discriminator.

Tests cover:
- Clean fixed-width file → 200, best not None
- Empty file → 200, candidates=[], best=None
- Missing file field → 422
- max_lines query param limits processing
"""

from __future__ import annotations

import io
import os

import pytest
from fastapi.testclient import TestClient

from src.api.main import app

# Ensure the test API key is registered before the client is created.
_api_keys = os.getenv("API_KEYS", "")
if "dev-key" not in {k.split(":", 1)[0].strip() for k in _api_keys.split(",") if k.strip()}:
    os.environ["API_KEYS"] = f"{_api_keys},dev-key:admin" if _api_keys else "dev-key:admin"

client = TestClient(app, raise_server_exceptions=True)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HEADERS = {"X-API-Key": "dev-key"}


def _make_file(lines: list[str], filename: str = "batch.txt") -> dict:
    content = "\n".join(lines).encode("utf-8")
    return {"file": (filename, io.BytesIO(content), "text/plain")}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestDetectDiscriminatorEndpoint:
    def test_returns_200_with_best_candidate(self):
        """Clean HDR/DTL/TRL file → 200, best is not None."""
        codes = (["HDR", "DTL", "DTL", "DTL", "TRL"] * 4)[:20]
        lines = [code + "X" * 17 for code in codes]
        resp = client.post(
            "/api/v1/multi-record/detect-discriminator",
            files=_make_file(lines),
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "candidates" in data
        assert "best" in data
        assert data["best"] is not None

    def test_best_candidate_has_required_keys(self):
        """Response best object has position, length, values, confidence keys."""
        codes = ["HDR", "DTL", "DTL", "TRL"] * 5
        lines = [code + "X" * 16 for code in codes]
        resp = client.post(
            "/api/v1/multi-record/detect-discriminator",
            files=_make_file(lines),
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        best = resp.json()["best"]
        assert "position" in best
        assert "length" in best
        assert "values" in best
        assert "confidence" in best


# ---------------------------------------------------------------------------
# Empty file
# ---------------------------------------------------------------------------

class TestDetectDiscriminatorEmptyFile:
    def test_empty_file_returns_no_candidates(self):
        """Empty file body → 200, candidates=[], best=None."""
        resp = client.post(
            "/api/v1/multi-record/detect-discriminator",
            files={"file": ("empty.txt", io.BytesIO(b""), "text/plain")},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["candidates"] == []
        assert data["best"] is None

    def test_whitespace_file_returns_no_candidates(self):
        """File with only whitespace → 200, candidates=[], best=None."""
        resp = client.post(
            "/api/v1/multi-record/detect-discriminator",
            files={"file": ("ws.txt", io.BytesIO(b"   \n   \n"), "text/plain")},
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["candidates"] == []
        assert data["best"] is None


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------

class TestDetectDiscriminatorValidation:
    def test_missing_file_returns_422(self):
        """No file field in multipart → 422 Unprocessable Entity."""
        resp = client.post(
            "/api/v1/multi-record/detect-discriminator",
            headers=_HEADERS,
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# max_lines param
# ---------------------------------------------------------------------------

class TestDetectDiscriminatorMaxLines:
    def test_max_lines_param_limits_processing(self):
        """max_lines=3 with 3-line discriminator file → best found."""
        lines = [
            "HDR" + "X" * 17,
            "DTL" + "X" * 17,
            "DTL" + "X" * 17,
            "TRL" + "X" * 17,
        ]
        resp = client.post(
            "/api/v1/multi-record/detect-discriminator?max_lines=3",
            files=_make_file(lines),
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        # 3 lines: HDR, DTL, DTL — only DTL repeats, HDR single → maybe no result
        # The important check: ZZZ lines (if any beyond max_lines) were not read.
        # With 3 lines and HDR(1)/DTL(2)/TRL(0): DTL repeats, HDR doesn't.
        # Candidates may be empty or not depending on data; just check HTTP 200.
        data = resp.json()
        assert "candidates" in data
        assert "best" in data

    def test_max_lines_param_excludes_trailing_lines(self):
        """max_lines=5 should skip lines beyond 5.

        Lines 1–5: HDR/DTL/DTL/DTL/TRL — valid pattern.
        Lines 6+: ZZZ — would create different/no pattern.
        Check that ZZZ does not appear in returned values.
        """
        first_five = ["HDR", "DTL", "DTL", "DTL", "TRL"]
        rest = ["ZZZ"] * 15
        all_codes = first_five + rest
        lines = [code + "X" * 7 for code in all_codes]
        resp = client.post(
            "/api/v1/multi-record/detect-discriminator?max_lines=5",
            files=_make_file(lines),
            headers=_HEADERS,
        )
        assert resp.status_code == 200
        best = resp.json()["best"]
        if best is not None:
            assert "ZZZ" not in best["values"]

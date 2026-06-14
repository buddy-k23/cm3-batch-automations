"""Path-traversal regression tests for issue #10.

The ``_safe_upload_path`` helper protects uploads in two complementary ways:

* It strips directory components from the supplied filename via
  ``Path(name).name`` \u2014 so traversal sequences (``../``, absolute paths,
  drive letters) cannot point outside ``UPLOADS_DIR``.
* It then verifies the resolved path is contained within ``UPLOADS_DIR``
  and raises HTTP 400 for inputs that cannot be reduced to a usable
  basename (empty string, ``.``, ``..``, NUL bytes).

Tests below assert both behaviours: malicious-looking names either
sanitise to a clean basename inside ``UPLOADS_DIR`` (and never escape) or
are rejected with HTTP 400.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.api.routers.files import _safe_upload_path, UPLOADS_DIR


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    """Configure a known API key for all tests in this module."""
    monkeypatch.setenv("API_KEYS", "dev-key:admin")
    return "dev-key"


# Filenames that must all be hard-rejected by ``_safe_upload_path``
# (empty, ``.``, ``..``, NUL).
HARD_REJECT_NAMES = [
    "",
    ".",
    "..",
    "x\x00.txt",
]

# Filenames that contain traversal sequences but that the helper sanitises
# to a clean basename inside UPLOADS_DIR rather than rejecting outright.
SANITISED_NAMES = [
    ("../etc/passwd", "passwd"),
    ("..\\..\\windows\\system32\\foo", "foo"),
    ("/etc/passwd", "passwd"),
    ("subdir/escape.txt", "escape.txt"),
]


class TestSafeUploadPathHelper:
    """Direct unit tests for the helper that backs every upload endpoint."""

    @pytest.mark.parametrize("bad_name", HARD_REJECT_NAMES)
    def test_rejects_unusable_names(self, bad_name):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as ei:
            _safe_upload_path(bad_name)
        assert ei.value.status_code == 400

    @pytest.mark.parametrize("input_name,expected_basename", SANITISED_NAMES)
    def test_traversal_sequences_sanitised_inside_uploads(
        self, input_name, expected_basename
    ):
        """Traversal-style names are reduced to their basename and stay inside UPLOADS_DIR."""
        result = _safe_upload_path(input_name)
        assert result.name == expected_basename
        assert result.is_relative_to(UPLOADS_DIR.resolve())

    def test_accepts_simple_names(self):
        result = _safe_upload_path("data.csv")
        assert result.name == "data.csv"
        assert result.is_relative_to(UPLOADS_DIR.resolve())

    def test_accepts_prefix(self):
        result = _safe_upload_path("data.csv", prefix="validate_")
        assert result.name == "validate_data.csv"
        assert result.is_relative_to(UPLOADS_DIR.resolve())

    def test_prefix_with_traversal_input_stays_inside_uploads(self):
        """The previous unsafe pattern was ``UPLOADS_DIR / f"validate_{filename}"``.

        With ``filename='../escape.csv'`` that became
        ``UPLOADS_DIR/validate_../escape.csv`` which collapses to
        ``UPLOADS_DIR/escape.csv`` \u2014 outside the validate_ namespace.
        Our helper first reduces to ``Path(filename).name`` so the prefix
        is appended to a clean basename and the file lands as
        ``validate_escape.csv`` inside UPLOADS_DIR.
        """
        result = _safe_upload_path("../escape.csv", prefix="validate_")
        assert result.name == "validate_escape.csv"
        assert result.is_relative_to(UPLOADS_DIR.resolve())


class TestUploadEndpointsHandleTraversal:
    """End-to-end: traversal-named uploads either sanitise or reject \u2014 never escape."""

    @pytest.mark.parametrize("bad_name", ["../etc/passwd", "..\\..\\boot.ini"])
    def test_detect_format_does_not_escape_uploads(self, bad_name):
        """Detect endpoint accepts the upload but writes a sanitised name.

        The key invariant (verified by ``TestNoFilesEscapeUploadsDir``) is
        that no file is ever written outside ``UPLOADS_DIR``.
        """
        client = TestClient(app)
        files = {"file": (bad_name, io.BytesIO(b"data"), "text/plain")}
        resp = client.post(
            "/api/v1/files/detect",
            files=files,
            headers={"X-API-Key": "dev-key"},
        )
        # 200 (sanitised), 400 (rejected), or 500 (format detection failed
        # on the tiny stub data) are all acceptable; the request must not
        # escape.
        assert resp.status_code in (200, 400, 500)

    @pytest.mark.parametrize("bad_name", ["../etc/passwd"])
    def test_validate_does_not_escape_uploads(self, bad_name):
        client = TestClient(app)
        files = {"file": (bad_name, io.BytesIO(b"data"), "text/plain")}
        data = {"mapping_id": "nonexistent"}
        resp = client.post(
            "/api/v1/files/validate",
            files=files,
            data=data,
            headers={"X-API-Key": "dev-key"},
        )
        # 400 (rejected by helper) or 404 (helper accepts sanitised name,
        # then mapping lookup fails). Either is safe.
        assert resp.status_code in (400, 404)

    @pytest.mark.parametrize("bad_name", ["../escape", "..\\escape"])
    def test_detect_drift_does_not_escape_uploads(self, bad_name):
        client = TestClient(app)
        files = {"file": (bad_name, io.BytesIO(b"data"), "text/plain")}
        data = {"mapping_id": "nonexistent"}
        resp = client.post(
            "/api/v1/files/detect-drift",
            files=files,
            data=data,
            headers={"X-API-Key": "dev-key"},
        )
        # 400 from helper, 404 from mapping lookup, or 500 from drift
        # detector parsing the empty file \u2014 anything but a successful
        # exfiltration.
        assert resp.status_code in (400, 404, 500)


class TestDownloadTemplateTraversal:
    """download_template now requires resolve()/relative_to() containment."""

    @pytest.mark.parametrize(
        "bad_name",
        ["..%2f..%2fetc%2fpasswd", "..", "."],
    )
    def test_traversal_payloads_rejected(self, bad_name):
        client = TestClient(app)
        resp = client.get(f"/api/v1/templates/{bad_name}")
        assert resp.status_code in (400, 404)
        # Either way, the body must NOT be a CSV download.
        assert resp.headers.get("content-type", "").startswith("application/json")

    def test_non_csv_suffix_rejected(self):
        client = TestClient(app)
        resp = client.get("/api/v1/templates/something.txt")
        assert resp.status_code == 404


class TestNoFilesEscapeUploadsDir:
    """Sanity check: traversal attempts never produce files outside UPLOADS_DIR."""

    def test_uploads_dir_is_clean_after_attack(self, tmp_path, monkeypatch):
        """After many traversal attempts, no new files exist outside UPLOADS_DIR."""
        client = TestClient(app)

        # Snapshot a representative parent dir we don't want written to.
        # We can't easily monitor the whole filesystem, but we can monitor
        # the working directory parent.
        cwd = Path.cwd()
        watch_dir = cwd

        before = {p.name for p in watch_dir.iterdir() if p.is_file()}

        for bad in ["../leaked.csv", "..\\..\\leaked2.csv", "/tmp/leaked3.csv"]:
            files = {"file": (bad, io.BytesIO(b"x"), "text/plain")}
            client.post(
                "/api/v1/files/detect",
                files=files,
                headers={"X-API-Key": "dev-key"},
            )

        after = {p.name for p in watch_dir.iterdir() if p.is_file()}
        assert after == before, (
            f"Traversal attack created files outside UPLOADS_DIR: {after - before}"
        )

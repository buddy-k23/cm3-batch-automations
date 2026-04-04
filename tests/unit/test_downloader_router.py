"""Unit tests for the file downloader API router."""

import io
import os
import tarfile
from importlib import reload
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


def _make_client(tmp_path: Path, env: dict, allowed_path: str = None):
    """Reload the FastAPI app and return (client, patch_ctx) tuple.

    The caller must use the patch context as a context manager to ensure
    environment variables are active when requests are made.

    Args:
        tmp_path: Temporary directory to use as the fd_config allowed path.
        env: Environment variables to apply via ``patch.dict``.
        allowed_path: Override for the single allowed path label entry.
            Defaults to ``str(tmp_path)``.

    Returns:
        Tuple of ``(TestClient, app_module)`` with env already patched in.
    """
    import src.api.main as m
    reload(m)
    path_str = allowed_path if allowed_path is not None else str(tmp_path)
    m.app.state.fd_config = {"paths": [{"label": "T", "path": path_str}]}
    return TestClient(m.app)


def test_get_paths(tmp_path):
    """GET /paths returns configured paths from app state."""
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.get("/api/v1/downloader/paths", headers={"X-API-Key": "k"})
    assert r.status_code == 200
    assert r.json()["paths"][0]["label"] == "T"


def test_browse_lists_files(tmp_path):
    """GET /browse returns file entries in the specified directory."""
    (tmp_path / "report.csv").write_text("a,b")
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.get(
            f"/api/v1/downloader/browse?path={tmp_path}",
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 200
    assert any(e["name"] == "report.csv" for e in r.json()["entries"])


def test_browse_rejects_unlisted_path(tmp_path):
    """GET /browse returns 403 for a path not in allowed_paths."""
    other = tmp_path / "other"
    other.mkdir()
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env, allowed_path=str(tmp_path / "safe"))
        r = client.get(
            f"/api/v1/downloader/browse?path={other}",
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 403


def test_download_plain_file(tmp_path):
    """POST /download streams a plain file from disk."""
    (tmp_path / "r.csv").write_text("col1,col2\n")
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.post(
            "/api/v1/downloader/download",
            json={"path": str(tmp_path), "filename": "r.csv"},
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 200
    assert b"col1" in r.content


def test_download_rejects_absolute_filename(tmp_path):
    """POST /download returns 400 when filename is an absolute path."""
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.post(
            "/api/v1/downloader/download",
            json={"path": str(tmp_path), "filename": "/etc/passwd"},
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 400


def test_download_rejects_path_traversal_filename(tmp_path):
    """POST /download returns 400 when filename contains path traversal."""
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.post(
            "/api/v1/downloader/download",
            json={"path": str(tmp_path), "filename": "../secret.txt"},
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 400


def _setup_app(tmp_path: Path, env: dict) -> TestClient:
    """Create a TestClient with env patched in and fd_config set.

    Unlike ``_make_client``, this helper patches the environment permanently
    for the lifetime of the returned client (uses ``patch.dict`` without a
    context-manager exit), which is suitable for tests that make requests
    outside of a ``with`` block.

    Args:
        tmp_path: Directory used as the single allowed path in fd_config.
        env: Environment variables to inject via ``os.environ``.

    Returns:
        Configured ``TestClient`` with the patched app state.
    """
    patch.dict(os.environ, env).__enter__()
    import src.api.main as m
    reload(m)
    m.app.state.fd_config = {"paths": [{"label": "T", "path": str(tmp_path)}]}
    return TestClient(m.app)


def test_search_files_returns_results(tmp_path):
    (tmp_path / "errors.log").write_text("line1\nERROR found\nline3\n")
    client = _setup_app(tmp_path, {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"})
    r = client.post("/api/v1/downloader/search-files",
                    json={"path": str(tmp_path), "filename_pattern": "*.log", "search_string": "ERROR"},
                    headers={"X-API-Key": "k"})
    assert r.status_code == 200
    data = r.json()
    assert data["total_matches"] == 1
    assert data["results"][0]["file"] == "errors.log"


def test_search_archive_returns_results(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"line1\nERROR in archive\n"
        info = tarfile.TarInfo(name="errors.log")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    (tmp_path / "batch.tar.gz").write_bytes(buf.getvalue())
    client = _setup_app(tmp_path, {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"})
    r = client.post("/api/v1/downloader/search-archive",
                    json={"path": str(tmp_path), "archive_pattern": "*.tar.gz",
                          "file_pattern": "*.log", "search_string": "ERROR"},
                    headers={"X-API-Key": "k"})
    assert r.status_code == 200
    assert r.json()["results"][0]["archive"] == "batch.tar.gz"


def test_browse_returns_404_for_missing_directory(tmp_path):
    """GET /browse returns 404 when the directory does not exist."""
    missing = str(tmp_path / "nonexistent")
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    # Allow the parent so the path validation passes, but the subdir is missing
    with patch.dict(os.environ, env):
        import src.api.main as m
        from importlib import reload
        reload(m)
        m.app.state.fd_config = {"paths": [{"label": "T", "path": str(tmp_path)}]}
        client = _make_client(tmp_path, env)
        r = client.get(
            f"/api/v1/downloader/browse?path={missing}",
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 404


def test_archive_contents_returns_404_when_archive_missing(tmp_path):
    """GET /archive-contents returns 404 when archive file does not exist."""
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.get(
            f"/api/v1/downloader/archive-contents?path={tmp_path}&archive=missing.tar.gz",
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 404


def test_archive_contents_returns_400_for_unsupported_format(tmp_path):
    """GET /archive-contents returns 400 for an unsupported archive format."""
    bad_archive = tmp_path / "data.rar"
    bad_archive.write_bytes(b"not a real rar")
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.get(
            f"/api/v1/downloader/archive-contents?path={tmp_path}&archive=data.rar",
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 400


def test_download_archive_not_found_returns_404(tmp_path):
    """POST /download returns 404 when the named archive does not exist."""
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.post(
            "/api/v1/downloader/download",
            json={"path": str(tmp_path), "filename": "report.csv", "archive": "missing.tar.gz"},
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 404


def test_download_inner_file_not_found_raises(tmp_path):
    """POST /download raises FileNotFoundError when inner file is absent from archive.

    The exception is raised lazily by the generator during streaming, so it
    propagates through the StreamingResponse rather than being caught by the
    endpoint's try/except block (which only guards the generator creation).
    """
    import pytest
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"content\n"
        info = tarfile.TarInfo(name="actual.csv")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    (tmp_path / "batch.tar.gz").write_bytes(buf.getvalue())
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        with pytest.raises(FileNotFoundError, match="missing.csv"):
            client.post(
                "/api/v1/downloader/download",
                json={"path": str(tmp_path), "filename": "missing.csv", "archive": "batch.tar.gz"},
                headers={"X-API-Key": "k"},
            )


def test_download_plain_file_not_found_returns_404(tmp_path):
    """POST /download returns 404 when plain file does not exist."""
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.post(
            "/api/v1/downloader/download",
            json={"path": str(tmp_path), "filename": "ghost.csv"},
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 404


def test_download_rejects_path_traversal_archive_name(tmp_path):
    """POST /download returns 400 when archive name contains path traversal."""
    env = {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.post(
            "/api/v1/downloader/download",
            json={"path": str(tmp_path), "filename": "report.csv", "archive": "../evil.tar.gz"},
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 400


def test_download_nested_archive_inner_file(tmp_path):
    """Inner archive paths with '/' separators should be downloadable."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        data = b"nested content"
        info = tarfile.TarInfo(name="subdir/nested.log")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    (tmp_path / "a.tar.gz").write_bytes(buf.getvalue())
    client = _setup_app(tmp_path, {"ENABLE_FILE_DOWNLOADER": "true", "API_KEYS": "k"})
    r = client.post(
        "/api/v1/downloader/download",
        json={"path": str(tmp_path), "filename": "subdir/nested.log", "archive": "a.tar.gz"},
        headers={"X-API-Key": "k"},
    )
    assert r.status_code == 200
    assert b"nested content" in r.content

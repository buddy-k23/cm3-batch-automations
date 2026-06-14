"""Unit tests for the file downloader API router."""

import importlib
import io
import os
import tarfile
from importlib import reload
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_app_module():
    """Reload src.api.main after each test to prevent auth state leaking.

    Captures the environment BEFORE the test runs so the restore snapshot
    never contains test-injected API_KEYS values.
    """
    saved = os.environ.copy()
    yield
    import src.api.main as _m
    os.environ.clear()
    os.environ.update(saved)
    importlib.reload(_m)


def _make_client(tmp_path: Path, env: dict, allowed_path: str = None):
    """Reload the FastAPI app and return a TestClient with ui_config set.

    Patches ``yaml.safe_load`` during the reload so the downloader router is
    conditionally registered (``enabled: True``), then injects ``ui_config``
    into ``app.state`` with the allowed paths for path-validation tests.

    Args:
        tmp_path: Temporary directory to use as the ui_config allowed path.
        env: Environment variables to apply via ``patch.dict``.
        allowed_path: Override for the single allowed path label entry.
            Defaults to ``str(tmp_path)``.

    Returns:
        Configured ``TestClient`` with downloader ui_config injected.
    """
    import yaml
    import src.api.main as m
    path_str = allowed_path if allowed_path is not None else str(tmp_path)
    dl_cfg = {
        "enabled": True,
        "log_path": "logs/file-downloads.log",
        "paths": [{"label": "T", "path": path_str}],
    }
    ui_cfg = {"downloader": dl_cfg}
    # Patch yaml.safe_load so _ui_cfg_early picks up enabled=True at module
    # reload time, which causes the router to be registered.
    with patch.object(yaml, "safe_load", return_value=ui_cfg):
        reload(m)
    m.app.state.ui_config = ui_cfg
    return TestClient(m.app)


def test_get_paths(tmp_path):
    """GET /paths returns configured paths from app state."""
    env = {"API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.get("/api/v1/downloader/paths", headers={"X-API-Key": "k"})
    assert r.status_code == 200
    assert r.json()["paths"][0]["label"] == "T"


def test_browse_lists_files(tmp_path):
    """GET /browse returns file entries in the specified directory."""
    (tmp_path / "report.csv").write_text("a,b")
    env = {"API_KEYS": "k"}
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
    env = {"API_KEYS": "k"}
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
    env = {"API_KEYS": "k"}
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
    env = {"API_KEYS": "k"}
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
    env = {"API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.post(
            "/api/v1/downloader/download",
            json={"path": str(tmp_path), "filename": "../secret.txt"},
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 400


def _setup_app(tmp_path: Path, env: dict) -> TestClient:
    """Create a TestClient with env patched in and ui_config set.

    Unlike ``_make_client``, this helper patches the environment permanently
    for the lifetime of the returned client (uses ``patch.dict`` without a
    context-manager exit), which is suitable for tests that make requests
    outside of a ``with`` block.

    Args:
        tmp_path: Directory used as the single allowed path in ui_config.
        env: Environment variables to inject via ``os.environ``.

    Returns:
        Configured ``TestClient`` with the patched app state.
    """
    patch.dict(os.environ, env).__enter__()
    return _make_client(tmp_path, env)


def test_search_files_returns_results(tmp_path):
    (tmp_path / "errors.log").write_text("line1\nERROR found\nline3\n")
    client = _setup_app(tmp_path, {"API_KEYS": "k"})
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
    client = _setup_app(tmp_path, {"API_KEYS": "k"})
    r = client.post("/api/v1/downloader/search-archive",
                    json={"path": str(tmp_path), "archive_pattern": "*.tar.gz",
                          "file_pattern": "*.log", "search_string": "ERROR"},
                    headers={"X-API-Key": "k"})
    assert r.status_code == 200
    assert r.json()["results"][0]["archive"] == "batch.tar.gz"


def test_browse_returns_404_for_missing_directory(tmp_path):
    """GET /browse returns 404 when the directory does not exist."""
    missing = str(tmp_path / "nonexistent")
    env = {"API_KEYS": "k"}
    # Allow the parent so the path validation passes, but the subdir is missing
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.get(
            f"/api/v1/downloader/browse?path={missing}",
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 404


def test_archive_contents_returns_404_when_archive_missing(tmp_path):
    """GET /archive-contents returns 404 when archive file does not exist."""
    env = {"API_KEYS": "k"}
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
    env = {"API_KEYS": "k"}
    with patch.dict(os.environ, env):
        client = _make_client(tmp_path, env)
        r = client.get(
            f"/api/v1/downloader/archive-contents?path={tmp_path}&archive=data.rar",
            headers={"X-API-Key": "k"},
        )
    assert r.status_code == 400


def test_download_archive_not_found_returns_404(tmp_path):
    """POST /download returns 404 when the named archive does not exist."""
    env = {"API_KEYS": "k"}
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
    env = {"API_KEYS": "k"}
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
    env = {"API_KEYS": "k"}
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
    env = {"API_KEYS": "k"}
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
    client = _setup_app(tmp_path, {"API_KEYS": "k"})
    r = client.post(
        "/api/v1/downloader/download",
        json={"path": str(tmp_path), "filename": "subdir/nested.log", "archive": "a.tar.gz"},
        headers={"X-API-Key": "k"},
    )
    assert r.status_code == 200
    assert b"nested content" in r.content


# ---------------------------------------------------------------------------
# Search cancellation / timeout endpoints
# ---------------------------------------------------------------------------

def test_search_files_response_includes_stopped_reason_and_id(tmp_path):
    """Successful search returns stopped_reason=None and a server-assigned search_id."""
    (tmp_path / "f.log").write_text("ERROR\n")
    client = _setup_app(tmp_path, {"API_KEYS": "[REDACTED]"})
    r = client.post(
        "/api/v1/downloader/search-files",
        json={"path": str(tmp_path), "filename_pattern": "*.log", "search_string": "ERROR"},
        headers={"X-API-Key": "[REDACTED]"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["stopped_reason"] is None
    assert isinstance(data["search_id"], str) and data["search_id"]


def test_search_files_echoes_client_supplied_search_id(tmp_path):
    """Client-supplied search_id round-trips so the UI can match cancel calls."""
    (tmp_path / "f.log").write_text("ERROR\n")
    client = _setup_app(tmp_path, {"API_KEYS": "[REDACTED]"})
    r = client.post(
        "/api/v1/downloader/search-files",
        json={
            "path": str(tmp_path), "filename_pattern": "*.log",
            "search_string": "ERROR", "search_id": "client-id-123",
        },
        headers={"X-API-Key": "[REDACTED]"},
    )
    assert r.status_code == 200
    assert r.json()["search_id"] == "client-id-123"


def test_search_files_timeout_zero_disables_kill(tmp_path):
    """timeout_seconds=0 disables auto-kill (search still completes for tiny inputs)."""
    (tmp_path / "f.log").write_text("ERROR\n")
    client = _setup_app(tmp_path, {"API_KEYS": "[REDACTED]"})
    r = client.post(
        "/api/v1/downloader/search-files",
        json={
            "path": str(tmp_path), "filename_pattern": "*.log",
            "search_string": "ERROR", "timeout_seconds": 0,
        },
        headers={"X-API-Key": "[REDACTED]"},
    )
    assert r.status_code == 200
    assert r.json()["stopped_reason"] is None


def test_search_cancel_unknown_id_returns_false(tmp_path):
    """POST /search-cancel returns cancelled=False for an unknown id."""
    client = _setup_app(tmp_path, {"API_KEYS": "[REDACTED]"})
    r = client.post(
        "/api/v1/downloader/search-cancel",
        json={"search_id": "nope-not-real"},
        headers={"X-API-Key": "[REDACTED]"},
    )
    assert r.status_code == 200
    assert r.json() == {"cancelled": False, "search_id": "nope-not-real"}


def test_search_cancel_requires_auth(tmp_path):
    """POST /search-cancel rejects requests without a valid API key."""
    client = _setup_app(tmp_path, {"API_KEYS": "[REDACTED]"})
    r = client.post(
        "/api/v1/downloader/search-cancel",
        json={"search_id": "any"},
    )
    assert r.status_code in (401, 403)


def test_search_cancel_kills_in_flight_search(tmp_path, monkeypatch):
    """End-to-end: a concurrent /search-cancel call kills an in-flight search.

    Patches the service-level search function with a stub that polls the token,
    then issues the cancel from the same TestClient on another thread.
    """
    import threading
    import time as _time
    from src.services import downloader_service as svc

    (tmp_path / "f.log").write_text("ERROR\n")
    started = threading.Event()

    def slow_search(path, filename_pattern, search_string, token=None):
        started.set()
        for _ in range(500):  # up to ~5s
            if token is not None and token.is_done():
                break
            _time.sleep(0.01)
        return svc.SearchResult(
            results=[], truncated=False, total_matches=0, shown=0,
            download_ref=None,
            stopped_reason=(token.is_done() if token else None),
            search_id=token.search_id if token else None,
        )

    monkeypatch.setattr(svc, "search_in_files", slow_search)

    client = _setup_app(tmp_path, {"API_KEYS": "[REDACTED]"})
    result_holder = {}

    def do_search():
        result_holder["r"] = client.post(
            "/api/v1/downloader/search-files",
            json={
                "path": str(tmp_path), "filename_pattern": "*.log",
                "search_string": "ERROR", "search_id": "kill-me",
            },
            headers={"X-API-Key": "[REDACTED]"},
        )

    t = threading.Thread(target=do_search)
    t.start()
    assert started.wait(timeout=5), "search did not start in time"

    cancel_r = client.post(
        "/api/v1/downloader/search-cancel",
        json={"search_id": "kill-me"},
        headers={"X-API-Key": "[REDACTED]"},
    )
    assert cancel_r.status_code == 200
    assert cancel_r.json()["cancelled"] is True

    t.join(timeout=10)
    assert not t.is_alive(), "search did not return after cancel"
    body = result_holder["r"].json()
    assert body["stopped_reason"] == "cancelled"
    assert body["search_id"] == "kill-me"

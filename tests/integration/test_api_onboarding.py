"""Integration tests for the EE-S1 Source Editor onboarding endpoints.

Five scenarios mapped to the EE-S1 story acceptance criteria:

1. ``test_get_sources_returns_committed_sources`` — SHAW + SRC_A appear
   in the response.
2. ``test_get_sources_returns_counts`` — at least one source has a
   non-zero input/output count.
3. ``test_post_preview_with_valid_workbook`` — upload the canonical
   ``templates/SHAW_onboarding.xlsx``, assert 200 and a non-empty
   ``would_write`` list.
4. ``test_post_preview_with_invalid_workbook`` — upload a minimal /
   malformed .xlsx, assert 4xx.
5. ``test_post_preview_does_not_touch_disk`` — snapshot the repo's
   ``config/`` directory before + after the call and assert no new
   paths appear.

The auth fixture sets ``API_KEYS`` *before* the FastAPI app is
imported so the dependency-injected ``require_api_key`` finds a valid
key. The Starlette session signing key fixture mirrors the MCP suite
so the ``auth.enabled: true`` ui.yml posture does not block app
construction.
"""

from __future__ import annotations

import importlib
import io
import os
import shutil
import sys
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Module-level env setup — must run BEFORE ``from src.api.main import app``.
# ---------------------------------------------------------------------------

# The auth chain refuses to construct the app without a session signing
# key (issue #9 fail-closed posture); we inject a deterministic test
# value here so import-time succeeds.
os.environ.setdefault(
    "VALDO_SESSION_SIGNING_KEY",
    "test-only-key-not-for-production-do-not-reuse",
)

# Force-set API_KEYS so ``require_api_key`` accepts our test header.
# ``setdefault`` is not enough — a prior test module may have set a
# different value and left a stale ``src.api.main`` in ``sys.modules``.
os.environ["API_KEYS"] = "test-key:admin"

_AUTH_HEADERS = {"X-API-Key": "test-key"}

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHAW_WORKBOOK = _REPO_ROOT / "templates" / "SHAW_onboarding.xlsx"
_REPO_CONFIG_DIR = _REPO_ROOT / "config"


def _fresh_app():
    """Reload ``src.api.main`` so module-level env changes take effect.

    Other test modules may have imported the app under a different
    ``API_KEYS`` / ``VALDO_E2E_SOURCES_DIR`` posture; reloading the
    module ensures the dependency wiring picks up the current
    environment.
    """
    for mod_name in [
        "src.api.main",
        "src.api.routers.onboarding",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_sources_dir(tmp_path: Path, monkeypatch) -> Path:
    """Point the sources router at a per-test copy of the real configs.

    We copy the canonical ``config/e2e/sources/*.yml`` files into a
    tmp directory and redirect the router via ``VALDO_E2E_SOURCES_DIR``
    so tests can freely add / mutate / delete sources without polluting
    the repo working tree.
    """
    dst = tmp_path / "sources"
    dst.mkdir()
    src = _REPO_CONFIG_DIR / "e2e" / "sources"
    if src.is_dir():
        for yml in src.glob("*.yml"):
            shutil.copy2(yml, dst / yml.name)
    monkeypatch.setenv("VALDO_E2E_SOURCES_DIR", str(dst))
    return dst


@pytest.fixture
def client(isolated_sources_dir: Path) -> Iterator[TestClient]:
    """Provide a TestClient with a freshly reloaded FastAPI app."""
    app = _fresh_app()
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


# ---------------------------------------------------------------------------
# 1. GET /sources — committed source list
# ---------------------------------------------------------------------------


def test_get_sources_returns_committed_sources(client: TestClient):
    """SHAW + SRC_A must appear in the source list."""
    resp = client.get("/api/v2/onboarding/sources", headers=_AUTH_HEADERS)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert "sources" in payload, payload
    names = {s["name"] for s in payload["sources"]}
    # Both canonical sources committed to the repo.
    assert "SHAW" in names, f"SHAW missing from sources list: {names!r}"
    assert "SRC_A" in names, f"SRC_A missing from sources list: {names!r}"


# ---------------------------------------------------------------------------
# 2. GET /sources — non-zero input/output counts
# ---------------------------------------------------------------------------


def test_get_sources_returns_counts(client: TestClient):
    """At least one source must report a non-zero input + output count."""
    resp = client.get("/api/v2/onboarding/sources", headers=_AUTH_HEADERS)
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    sources = payload.get("sources", [])
    assert sources, "Expected at least one committed source"

    # Every entry has the documented shape.
    for entry in sources:
        assert isinstance(entry["name"], str) and entry["name"], entry
        assert isinstance(entry["input_files_count"], int), entry
        assert isinstance(entry["output_files_count"], int), entry
        assert entry["input_files_count"] >= 0, entry
        assert entry["output_files_count"] >= 0, entry

    # SHAW + SRC_A both ship with multiple input + output files.
    has_inputs = any(s["input_files_count"] > 0 for s in sources)
    has_outputs = any(s["output_files_count"] > 0 for s in sources)
    assert has_inputs, f"No source reported a positive input count: {sources!r}"
    assert has_outputs, f"No source reported a positive output count: {sources!r}"


# ---------------------------------------------------------------------------
# 3. POST /preview — valid workbook
# ---------------------------------------------------------------------------


def test_post_preview_with_valid_workbook(client: TestClient):
    """Upload the canonical SHAW workbook; preview returns would_write."""
    assert _SHAW_WORKBOOK.is_file(), (
        f"Expected canonical SHAW workbook at {_SHAW_WORKBOOK}; the test "
        "cannot run without the bundled fixture."
    )

    with _SHAW_WORKBOOK.open("rb") as fh:
        resp = client.post(
            "/api/v2/onboarding/preview",
            files={
                "file": (
                    "SHAW_onboarding.xlsx",
                    fh,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
            headers=_AUTH_HEADERS,
        )

    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload.get("source_code") == "SHAW", payload
    would_write = payload.get("would_write")
    assert isinstance(would_write, list) and would_write, payload
    summary = payload.get("summary") or {}
    assert summary.get("total_files") == len(would_write), summary
    assert isinstance(summary.get("total_bytes"), int)
    assert summary.get("total_bytes", 0) > 0, summary

    # Each entry carries the documented {path, bytes, kind} shape.
    for entry in would_write:
        assert isinstance(entry.get("path"), str) and entry["path"], entry
        assert isinstance(entry.get("bytes"), int) and entry["bytes"] > 0, entry
        assert entry.get("kind"), entry


# ---------------------------------------------------------------------------
# 4. POST /preview — invalid workbook
# ---------------------------------------------------------------------------


def test_post_preview_with_invalid_workbook(client: TestClient, tmp_path: Path):
    """A workbook that is not a valid onboarding spec returns 4xx."""
    from openpyxl import Workbook

    # Build a minimal .xlsx that lacks every required sheet — the EC-S1
    # schema validator must reject it.
    bogus_path = tmp_path / "bogus.xlsx"
    wb = Workbook()
    wb.active.title = "NotASource"
    wb.active["A1"] = "garbage"
    wb.save(str(bogus_path))

    with bogus_path.open("rb") as fh:
        resp = client.post(
            "/api/v2/onboarding/preview",
            files={
                "file": (
                    "bogus.xlsx",
                    fh,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
            headers=_AUTH_HEADERS,
        )

    assert 400 <= resp.status_code < 500, resp.text
    detail = resp.json().get("detail", "")
    # The router maps EC-S1 ToolError to 422 with the schema validator's
    # multi-line "missing required sheet" payload.
    assert "missing" in str(detail).lower() or "sheet" in str(detail).lower(), detail


def test_post_preview_rejects_non_xlsx_extension(client: TestClient):
    """A .txt upload is rejected at the extension check (400)."""
    resp = client.post(
        "/api/v2/onboarding/preview",
        files={
            "file": ("not_a_workbook.txt", io.BytesIO(b"hello"), "text/plain"),
        },
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 400, resp.text
    assert "xlsx" in resp.json().get("detail", "").lower()


# ---------------------------------------------------------------------------
# 5. POST /preview — no disk side effects
# ---------------------------------------------------------------------------


def test_post_preview_does_not_touch_disk(client: TestClient):
    """Preview must not create any new files under the repo's config/ tree.

    Snapshot every path under ``config/`` before + after the call and
    assert the set is identical. We use the REAL repo config dir here
    (not an isolated copy) because the preview endpoint reads from
    ``Path.cwd()`` for its emitter planning — and we want to prove no
    new files land under the live tree.
    """
    assert _SHAW_WORKBOOK.is_file()

    def _snapshot() -> set:
        if not _REPO_CONFIG_DIR.is_dir():
            return set()
        return {p for p in _REPO_CONFIG_DIR.rglob("*") if p.is_file()}

    before = _snapshot()

    with _SHAW_WORKBOOK.open("rb") as fh:
        resp = client.post(
            "/api/v2/onboarding/preview",
            files={
                "file": (
                    "SHAW_onboarding.xlsx",
                    fh,
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                ),
            },
            headers=_AUTH_HEADERS,
        )
    assert resp.status_code == 200, resp.text

    after = _snapshot()
    new_paths = after - before
    assert not new_paths, (
        f"Preview wrote new files under config/: "
        f"{sorted(p.as_posix() for p in new_paths)!r}"
    )

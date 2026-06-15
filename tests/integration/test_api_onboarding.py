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


def test_preview_response_includes_drift_status(client: TestClient):
    """EE-S2: preview response carries per-artefact ``status`` + aggregate drift.

    Uploads the canonical SHAW workbook and asserts the EE-S2 shape:

      * Every ``would_write`` entry has a ``status`` of ``"new"`` /
        ``"changed"`` / ``"unchanged"``.
      * The ``summary`` carries a ``drift`` block with the three
        counts; the sum equals ``total_files``.
    """
    assert _SHAW_WORKBOOK.is_file()
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

    would_write = payload.get("would_write") or []
    assert would_write, payload
    valid_statuses = {"new", "changed", "unchanged"}
    for entry in would_write:
        assert entry.get("status") in valid_statuses, entry

    summary = payload.get("summary") or {}
    drift = summary.get("drift")
    assert isinstance(drift, dict), summary
    for token in ("new", "changed", "unchanged"):
        assert token in drift, drift
        assert isinstance(drift[token], int), drift
        assert drift[token] >= 0, drift

    # Aggregate sanity check: per-status counts equal the artefact count.
    assert drift["new"] + drift["changed"] + drift["unchanged"] == len(
        would_write
    ), (drift, len(would_write))
    assert summary.get("total_files") == len(would_write), summary


def test_preview_response_distinguishes_new_changed_unchanged(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """EE-S2: artefacts comparing against different committed states get
    different statuses.

    We build a synthetic 3-source layout the planner will write under:

      * One artefact path that has NO committed file → ``new``.
      * One artefact path that has a byte-identical committed file →
        ``unchanged``.
      * One artefact path that has a deliberately-mutated committed
        file → ``changed``.

    We achieve this by redirecting ``Path.cwd()`` to a tmp directory
    seeded with a tweaked copy of the real ``config/`` tree.

    The test relies on the canonical SHAW workbook producing a deep
    tree that, when run against an empty cwd, generates all-``new``
    artefacts. We then seed a SUBSET of those paths under the tmp cwd
    — verbatim for one, mutated for another — and assert the three
    statuses all show up.
    """
    # Stage a tmp "repo root" the planner will resolve paths against.
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)

    # First pass: run the preview with no committed state — every
    # entry should land as ``new``.
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
    would_write = payload.get("would_write") or []
    assert would_write, payload
    statuses = {e["status"] for e in would_write}
    assert statuses == {"new"}, statuses
    assert payload["summary"]["drift"]["new"] == len(would_write)
    assert payload["summary"]["drift"]["changed"] == 0
    assert payload["summary"]["drift"]["unchanged"] == 0

    # Stash the source YAML (a mapping JSON content for second source)
    # so we can write committed copies of two artefacts before pass 2.
    mapping_entries = [
        e for e in would_write if e["kind"] == "mapping_json"
    ]
    assert mapping_entries, would_write
    unchanged_entry = mapping_entries[0]
    changed_entry = mapping_entries[1] if len(mapping_entries) > 1 else None
    assert changed_entry is not None, mapping_entries

    # Re-run the planner ourselves to grab the byte-identical content
    # the next API call will emit (so we can pre-seed it as committed).
    from src.commands.onboard_source import _plan_writes
    from src.onboarding.workbook_reader import read_workbook

    workbook = read_workbook(_SHAW_WORKBOOK)
    plans = _plan_writes(
        workbook,
        output_root=repo_dir,
        source_dir=None,
        mapping_dir=None,
        rules_dir=None,
        reconciliation_dir=None,
        sql_dir=None,
        frozen_timestamp=None,
    )

    by_display = {}
    for plan in plans:
        try:
            display = plan.path.resolve().relative_to(repo_dir).as_posix()
        except ValueError:
            display = plan.path.as_posix()
        by_display[display] = plan

    # Seed ``unchanged_entry`` verbatim — the EC-S10 normalisation in the
    # drift helper auto-extracts timestamps, so byte-equal is the right
    # bar here.
    unchanged_plan = by_display[unchanged_entry["path"]]
    unchanged_plan.path.parent.mkdir(parents=True, exist_ok=True)
    unchanged_plan.path.write_text(unchanged_plan.content, encoding="utf-8")

    # Seed ``changed_entry`` with mutated content — flip an obvious
    # field so the JSON parses but the deep-equality check rejects.
    changed_plan = by_display[changed_entry["path"]]
    mutated = changed_plan.content.replace(
        '"file_type":', '"file_type_renamed":', 1
    )
    if mutated == changed_plan.content:
        # Fall back to a guaranteed-divergent payload.
        mutated = '{"unrelated": "payload"}'
    changed_plan.path.parent.mkdir(parents=True, exist_ok=True)
    changed_plan.path.write_text(mutated, encoding="utf-8")

    # Second pass: the preview should now show one ``unchanged`` +
    # one ``changed`` + remaining ``new``.
    with _SHAW_WORKBOOK.open("rb") as fh:
        resp2 = client.post(
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
    assert resp2.status_code == 200, resp2.text
    payload2 = resp2.json()

    by_path = {e["path"]: e for e in payload2["would_write"]}
    assert by_path[unchanged_entry["path"]]["status"] == "unchanged", (
        by_path[unchanged_entry["path"]]
    )
    changed_resp = by_path[changed_entry["path"]]
    assert changed_resp["status"] == "changed", changed_resp
    assert "drift_reason" in changed_resp, changed_resp

    drift = payload2["summary"]["drift"]
    assert drift["unchanged"] >= 1
    assert drift["changed"] >= 1
    assert drift["new"] >= 1


def test_get_committed_artefact_returns_content_for_whitelisted_path(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """EE-S2: the diff-backing endpoint returns the file content for an
    artefact under a whitelisted directory."""
    repo_dir = tmp_path / "repo"
    target = repo_dir / "config" / "mappings" / "SAMPLE.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"hello": "world"}', encoding="utf-8")
    monkeypatch.chdir(repo_dir)

    resp = client.get(
        "/api/v2/onboarding/committed-artefact",
        params={"path": "config/mappings/SAMPLE.json"},
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    payload = resp.json()
    assert payload["path"] == "config/mappings/SAMPLE.json"
    assert payload["content"] == '{"hello": "world"}'
    assert payload["bytes"] == len('{"hello": "world"}')


def test_get_committed_artefact_rejects_directory_traversal(client: TestClient):
    """EE-S2: traversal payloads (``../../etc/passwd``) get 400, not 200."""
    resp = client.get(
        "/api/v2/onboarding/committed-artefact",
        params={"path": "../../etc/passwd"},
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 400, resp.text


def test_get_committed_artefact_rejects_off_whitelist_prefix(client: TestClient):
    """EE-S2: paths outside the artefact directories return 400."""
    resp = client.get(
        "/api/v2/onboarding/committed-artefact",
        params={"path": "src/api/main.py"},
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 400, resp.text


def test_get_committed_artefact_returns_404_for_missing_file(
    client: TestClient, tmp_path: Path, monkeypatch
):
    """EE-S2: a whitelisted path that doesn't exist returns 404."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.chdir(repo_dir)
    resp = client.get(
        "/api/v2/onboarding/committed-artefact",
        params={"path": "config/mappings/NOPE.json"},
        headers=_AUTH_HEADERS,
    )
    assert resp.status_code == 404, resp.text


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

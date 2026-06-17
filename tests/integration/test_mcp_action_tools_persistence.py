"""Integration tests for MCP run-registry persistence (S6-1, #386).

The headline test :func:`test_get_run_status_survives_app_restart`
verifies the AC that drove the whole story: a run started against a
database-backed registry must remain pollable after the FastAPI app is
torn down and rebuilt.

Implementation strategy
-----------------------
- Use a temporary SQLite database as the registry backend (same
  ``DatabaseRunRegistry`` code path as Oracle, exercised cheaply for CI).
- Force ``DB_ADAPTER=sqlite`` and point ``get_engine`` at the temp DB
  via env vars + the engine cache reset.
- Build the FastAPI app once, kick off ``validate_file``, then tear the
  app down (close the TestClient, drop module-level state).
- Build a fresh FastAPI app pointing at the *same* SQLite database and
  verify ``get_run_status`` and ``get_violations`` both return the
  recorded state.

Why SQLite, not Oracle?
    The Sprint 6 kickoff explicitly allows the SQLite test DB. Oracle
    survival is exercised by the manual test plan + the integration
    test against the seeded Oracle in ``tests/manual/seed_db.py``
    (manual operator runs). The point of this test file is to prove the
    *adapter contract* survives, not to test Oracle-specific behaviour.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text


_INIT_PARAMS = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1.0"},
}

_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


# ---------------------------------------------------------------------------
# Helpers — borrowed from test_mcp_action_tools.py
# ---------------------------------------------------------------------------


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    Mirrors the helper in ``test_mcp_action_tools.py``. All MCP modules
    cache state at import time so we must blow them out between
    constructions.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.action_tools",
        "src.mcp.run_registry",
        "src.mcp.tools",
        "src.mcp.taxonomy",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    return importlib.import_module("src.api.main").app


def _parse_streamable_body(response) -> Dict[str, Any]:
    """Decode an MCP Streamable-HTTP response body to a JSON dict."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for raw_line in response.text.splitlines():
            line = raw_line.strip()
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(f"SSE response had no data line: {response.text!r}")
    return response.json()


def _rpc(client, method, *, request_id, params=None):
    """Send a single JSON-RPC POST to ``/mcp/`` and parse the body."""
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    response = client.post("/mcp/", json=payload, headers=_MCP_HEADERS)
    assert response.status_code == 200, (
        f"{method} returned {response.status_code}: {response.text}"
    )
    return _parse_streamable_body(response)


def _call_tool(client, tool_name, *, request_id, arguments=None):
    """Invoke ``tools/call`` for *tool_name* and return the parsed body."""
    return _rpc(
        client,
        "tools/call",
        request_id=request_id,
        params={"name": tool_name, "arguments": arguments or {}},
    )


def _structured_or_text(result):
    """Extract the JSON payload from a CallToolResult."""
    structured = result.get("structuredContent")
    if structured is not None:
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured
    contents = result.get("content") or []
    assert contents, f"CallToolResult has no content: {result!r}"
    text_value = contents[0].get("text")
    assert isinstance(text_value, str) and text_value, (
        f"CallToolResult first content has no text: {contents[0]!r}"
    )
    return json.loads(text_value)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _ensure_session_signing_key(monkeypatch):
    """Provide the dummy session signing key the FastAPI app needs at import.

    Mirrors the fixture in ``test_mcp_action_tools.py``.
    """
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


@pytest.fixture
def persistent_sqlite_db() -> Iterator[Path]:
    """Create a real on-disk SQLite database with the registry table.

    The DB lives in a temp directory so it survives across two FastAPI
    app constructions in the same test. We tear it down explicitly at
    the end.

    Yields:
        Path to the SQLite database file.
    """
    tmpdir = tempfile.mkdtemp(prefix="valdo_mcp_persist_")
    db_path = Path(tmpdir) / "registry.db"

    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE APP_MCP_RUN_REGISTRY ("
            "run_id TEXT PRIMARY KEY, "
            "source TEXT NOT NULL, "
            "file_path TEXT NOT NULL, "
            "status TEXT NOT NULL, "
            "started_at TIMESTAMP NOT NULL, "
            "finished_at TIMESTAMP, "
            "violation_count INTEGER, "
            "payload TEXT NOT NULL, "
            # last_heartbeat_at + attempt_count were added by Alembic 0006
            # (S10-1, #397). DatabaseRunRegistry.put() writes them, so the
            # hand-built fixture table must include them or inserts fail with
            # 'no column named last_heartbeat_at'.
            "last_heartbeat_at TIMESTAMP, "
            "attempt_count INTEGER DEFAULT 0, "
            "created_ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        ))
    engine.dispose()

    try:
        yield db_path
    finally:
        try:
            db_path.unlink()
        except OSError:
            pass


@pytest.fixture
def temp_data_file() -> Iterator[str]:
    """Create a tiny temporary file the validate tool can point at."""
    fd, path = tempfile.mkstemp(prefix="valdo_mcp_persist_", suffix=".dat")
    os.write(fd, b"dummy row\n")
    os.close(fd)
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _patch_validate_service(monkeypatch, *, result=None, raises=None):
    """Replace ``run_validate_service`` with a deterministic stub.

    Mirrors the helper in ``test_mcp_action_tools.py``.
    """
    import src.services.validate_service as svc

    def _stub(**kwargs):
        if raises is not None:
            raise raises
        return result or {
            "valid": True,
            "errors": [],
            "warnings": [],
            "info": [],
            "total_rows": 1,
            "error_count": 0,
            "warning_count": 0,
            "elapsed_seconds": 0.0,
        }

    monkeypatch.setattr(svc, "run_validate_service", _stub)


def _build_stub_result_with_violations(n_errors: int, n_warnings: int) -> Dict[str, Any]:
    """Mirror the ``test_mcp_action_tools.py`` helper."""
    errors = [
        {
            "severity": "error",
            "code": f"E{i:03d}",
            "message": f"error row {i}",
            "row": i,
            "field": f"FLD_{i}",
            "value": f"val-{i}",
        }
        for i in range(1, n_errors + 1)
    ]
    warnings = [
        {
            "severity": "warning",
            "code": f"W{i:03d}",
            "message": f"warning row {i}",
            "row": i,
            "field": f"FLD_W_{i}",
        }
        for i in range(1, n_warnings + 1)
    ]
    return {
        "valid": False,
        "errors": errors,
        "warnings": warnings,
        "info": [],
        "total_rows": max(n_errors, n_warnings, 1),
        "error_count": n_errors,
        "warning_count": n_warnings,
    }


def _point_engine_at(db_path: Path, monkeypatch):
    """Force the shared SQLAlchemy engine to talk to *db_path*.

    Sets ``DB_ADAPTER=sqlite`` + ``DB_PATH``, clears the engine LRU
    cache, and resets the schema helper so ``get_valdo_schema`` returns
    ``""`` (SQLite has no schema concept).

    Args:
        db_path: Path to the persistent SQLite database.
        monkeypatch: pytest fixture for safe env var teardown.
    """
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    monkeypatch.setenv("DB_PATH", str(db_path))
    monkeypatch.setenv("VALDO_SCHEMA", "")

    # Clear the LRU cache so the engine factory builds a fresh engine
    # against the new env vars. The engine module is imported indirectly
    # via the registry — import it now so the cache reset runs.
    import src.database.engine as engine_mod
    engine_mod.reset_engine()


# ---------------------------------------------------------------------------
# Headline test — restart survival
# ---------------------------------------------------------------------------


def test_get_run_status_survives_app_restart(
    monkeypatch, persistent_sqlite_db, temp_data_file
):
    """A run started in app instance #1 is still pollable in app instance #2.

    This is the AC that drove S6-1: when the operator restarts FastAPI
    mid-validation, the BA's run_id must remain valid. The test mirrors
    that lifecycle with two distinct ``TestClient`` instances against
    the *same* underlying SQLite database.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    _point_engine_at(persistent_sqlite_db, monkeypatch)
    _patch_validate_service(
        monkeypatch,
        result=_build_stub_result_with_violations(n_errors=3, n_warnings=2),
    )

    # --- App instance #1: validate_file completes synchronously ----------
    app_v1 = _fresh_app()
    with TestClient(app_v1) as client_v1:
        _rpc(client_v1, "initialize", request_id=1, params=_INIT_PARAMS)
        start_body = _call_tool(
            client_v1,
            "validate_file",
            request_id=2,
            arguments={"source": "SHAW", "file_path": temp_data_file},
        )
    run_id = _structured_or_text(start_body["result"])["run_id"]
    assert isinstance(run_id, str) and run_id

    # --- "Restart": new TestClient, fresh module state, same SQLite DB ---
    # The validate_service stub needs to be re-applied because module
    # reload blows it away too — but it isn't called on the second
    # client, so we only need a no-op stub to be safe.
    _patch_validate_service(monkeypatch)
    app_v2 = _fresh_app()
    with TestClient(app_v2) as client_v2:
        _rpc(client_v2, "initialize", request_id=1, params=_INIT_PARAMS)
        status_body = _call_tool(
            client_v2,
            "get_run_status",
            request_id=2,
            arguments={"run_id": run_id},
        )
        viol_body = _call_tool(
            client_v2,
            "get_violations",
            request_id=3,
            arguments={"run_id": run_id, "page": 1, "page_size": 100},
        )

    # Status must have been preserved across the restart.
    status_payload = _structured_or_text(status_body["result"])
    assert status_payload["run_id"] == run_id
    assert status_payload["status"] == "completed", (
        f"expected completed after restart; got {status_payload!r}"
    )
    assert status_payload["violation_count"] == 5  # 3 errors + 2 warnings

    # Violations must round-trip through the JSON payload column.
    viol_payload = _structured_or_text(viol_body["result"])
    assert viol_payload["total_count"] == 5
    assert len(viol_payload["violations"]) == 5
    severities = sorted(v["severity"] for v in viol_payload["violations"])
    assert severities == ["error", "error", "error", "warning", "warning"]


# ---------------------------------------------------------------------------
# Secondary tests
# ---------------------------------------------------------------------------


def test_inflight_lookup_survives_restart(
    monkeypatch, persistent_sqlite_db, temp_data_file
):
    """A second validate_file call after a restart returns the original run_id.

    Verifies the idempotency-guard path: if the first run is still
    in-flight in the database, the second app instance's
    ``validate_file`` call must short-circuit to the existing record.

    We simulate "in-flight" by pre-populating the database with a
    ``running`` record (because the stubbed validate_service completes
    synchronously, we can't naturally leave a row in ``running``).
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    _point_engine_at(persistent_sqlite_db, monkeypatch)
    _patch_validate_service(monkeypatch)

    # Seed the SQLite DB with a "running" row for (SHAW, temp_data_file).
    engine = create_engine(f"sqlite:///{persistent_sqlite_db}")
    seeded_run_id = "preseeded-running-run-id-xyz"
    payload = {
        "run_id": seeded_run_id,
        "source": "SHAW",
        "file_path": temp_data_file,
        "file_type": None,
        "status": "running",
        "started_at": "2026-06-15T09:00:00.000000Z",
        "finished_at": None,
        "violations": [],
        "error_message": None,
    }
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO APP_MCP_RUN_REGISTRY "
                "(run_id, source, file_path, status, started_at, payload) "
                "VALUES (:run_id, :source, :file_path, :status, :started_at, :payload)"
            ),
            {
                "run_id": seeded_run_id,
                "source": "SHAW",
                "file_path": temp_data_file,
                "status": "running",
                "started_at": "2026-06-15T09:00:00.000000Z",
                "payload": json.dumps(payload),
            },
        )
    engine.dispose()

    # Fresh app boots, sees the pre-seeded row, returns the same run_id.
    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client,
            "validate_file",
            request_id=2,
            arguments={"source": "SHAW", "file_path": temp_data_file},
        )

    payload_out = _structured_or_text(body["result"])
    assert payload_out["run_id"] == seeded_run_id, (
        f"validate_file did not short-circuit to the pre-seeded run: {payload_out!r}"
    )


def test_registry_falls_back_to_inmemory_when_db_unreachable(
    monkeypatch, temp_data_file
):
    """When the DB engine fails at boot, the registry falls back to in-memory.

    Verifies AC #5: the server MUST still start even if Oracle is down.
    We simulate the failure by pointing ``DB_PATH`` at an unwritable
    path and letting the probe blow up; the factory should swallow the
    error, log a WARNING, and return :class:`InMemoryRunRegistry`.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    # Point at a path that cannot be opened — no parent dir, no perms.
    monkeypatch.setenv("DB_PATH", "/dev/null/cannot/open/this.db")
    monkeypatch.setenv("VALDO_SCHEMA", "")

    import src.database.engine as engine_mod
    engine_mod.reset_engine()

    _patch_validate_service(monkeypatch)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        # validate_file must still succeed — proves the in-memory
        # fallback is wired and the app booted cleanly despite the
        # broken DB target.
        body = _call_tool(
            client,
            "validate_file",
            request_id=2,
            arguments={"source": "SHAW", "file_path": temp_data_file},
        )

    payload = _structured_or_text(body["result"])
    assert isinstance(payload.get("run_id"), str) and payload["run_id"]

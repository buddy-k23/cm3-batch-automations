"""Integration tests for `/mcp/` rate limiting (S9-3, #388).

These exercise the limiter wired into :class:`src.mcp.auth.MCPAuthMiddleware`
against a live FastAPI ``TestClient``, the same harness the EF-S2/EF-S4
MCP integration tests use. They assert the externally-visible contract:

* **31st call → 429 + Retry-After** — firing one more tool call than the
  per-token cap (30) within a single window returns HTTP 429 carrying a
  positive ``Retry-After`` header (the #388 headline AC).
* **Resource reads are NOT rate-limited** — ``resources/read`` calls past
  the tool cap still succeed (cheap, idempotent).
* **``get_run_status`` polling is allowed at a higher cap** — a BA polling
  a long run past the normal tool cap is not self-DOSed.
* **Per-IP vs per-token limits are independently observable** — overriding
  the per-IP cap below the per-token cap surfaces a 429 attributed to the
  IP scope.

To keep the suite deterministic and fast we drive the caps down via the
``VALDO_MCP_RATE_LIMIT_*`` env vars rather than firing literal 30+ calls
where a smaller cap proves the same behaviour — EXCEPT the headline
"31 calls" test, which keeps the documented default cap of 30 so the AC
is reproduced verbatim. All calls land inside one window (the limiter's
clock advances only when wall time does, and a TestClient loop runs in
well under the 60s window), so no sleeping is required.
"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient


_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

_INIT_PARAMS = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1.0"},
}


def _fresh_app():
    """Reload the FastAPI app so it observes the current env state."""
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.auth",
        "src.mcp.rate_limit",
        "src.mcp.tools",
        "src.mcp.action_tools",
        "src.mcp.taxonomy",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


@pytest.fixture(autouse=True)
def _session_signing_key(monkeypatch):
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


def _rpc(client: TestClient, method: str, *, request_id: int,
         params: Dict[str, Any] | None = None, headers=None):
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method,
               "params": params or {}}
    return client.post("/mcp/", json=payload, headers=headers or _MCP_HEADERS)


def _init(client: TestClient):
    resp = _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
    assert resp.status_code == 200, resp.text


def _call_tool(client: TestClient, name: str, *, request_id: int,
               arguments: Dict[str, Any] | None = None, headers=None):
    return _rpc(
        client,
        "tools/call",
        request_id=request_id,
        params={"name": name, "arguments": arguments or {}},
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Headline AC — 31st call returns 429 with Retry-After
# ---------------------------------------------------------------------------


def test_thirty_first_tool_call_in_window_returns_429(monkeypatch):
    """30 tool calls succeed; the 31st returns 429 + a Retry-After header.

    Reproduces the #388 AC verbatim with the documented default per-token
    cap of 30. Dev-auth gives every call the same principal (one token
    identity) and the same client IP, so both the per-token and per-IP
    counters see one stream — the per-token cap (30) is the binding wall.
    The per-IP default (60) is left untouched so this test isolates the
    per-token limit.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    # Leave the per-token + per-IP caps at their documented defaults
    # (30 / 60). Use a tool that fails fast / cheaply so we are not timing
    # real validation work — get_run_status on an unknown id returns an
    # MCP error envelope but still a 200 transport response that counts
    # against the limiter. We instead use list-style validate calls that
    # the limiter counts regardless of the tool's own outcome.
    monkeypatch.delenv("VALDO_MCP_RATE_LIMIT_PER_MINUTE", raising=False)
    monkeypatch.delenv("VALDO_MCP_RATE_LIMIT_PER_IP_PER_MINUTE", raising=False)
    app = _fresh_app()

    with TestClient(app) as client:
        _init(client)
        # Fire 30 tool calls — all admitted (transport 200).
        for i in range(30):
            resp = _call_tool(client, "validate_file", request_id=100 + i,
                              arguments={"source": "SHAW",
                                         "file_path": "/nonexistent.txt"})
            assert resp.status_code != 429, (
                f"call {i + 1} was rate-limited early: {resp.text}"
            )
        # The 31st within the same window must be rejected with 429.
        resp = _call_tool(client, "validate_file", request_id=999,
                          arguments={"source": "SHAW",
                                     "file_path": "/nonexistent.txt"})
        assert resp.status_code == 429, resp.text
        retry_after = resp.headers.get("Retry-After")
        assert retry_after is not None, "429 missing Retry-After header"
        assert int(retry_after) > 0


# ---------------------------------------------------------------------------
# Resource reads are exempt
# ---------------------------------------------------------------------------


def test_resource_reads_are_not_rate_limited(monkeypatch):
    """A tight tool cap does not throttle ``resources/read``.

    Drives the per-token cap to 1 so a single tool call exhausts it, then
    proves the taxonomy resource still reads many times over — resource
    handlers are cheap + idempotent and explicitly exempt.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("VALDO_MCP_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("VALDO_MCP_RATE_LIMIT_PER_IP_PER_MINUTE", "1")
    app = _fresh_app()

    with TestClient(app) as client:
        _init(client)
        # Exhaust the (cap=1) tool budget.
        first = _call_tool(client, "validate_file", request_id=10,
                           arguments={"source": "SHAW",
                                      "file_path": "/nonexistent.txt"})
        assert first.status_code != 429
        blocked = _call_tool(client, "validate_file", request_id=11,
                             arguments={"source": "SHAW",
                                        "file_path": "/nonexistent.txt"})
        assert blocked.status_code == 429

        # Resource reads keep working well past the tool cap.
        for i in range(5):
            resp = _rpc(client, "resources/read", request_id=20 + i,
                        params={"uri": "taxonomy://violations"})
            assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# get_run_status elevated cap
# ---------------------------------------------------------------------------


def test_get_run_status_allowed_past_normal_tool_cap(monkeypatch):
    """``get_run_status`` polling survives past the normal tool cap.

    Normal tools capped at 1/min; the elevated ``get_run_status`` cap is
    set to 5/min. Five polls succeed even though a normal tool would have
    been blocked after the first call.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("VALDO_MCP_RATE_LIMIT_PER_MINUTE", "1")
    monkeypatch.setenv("VALDO_MCP_RATE_LIMIT_PER_IP_PER_MINUTE", "1000")
    monkeypatch.setenv("VALDO_MCP_RATE_LIMIT_RUN_STATUS_PER_MINUTE", "5")
    app = _fresh_app()

    with TestClient(app) as client:
        _init(client)
        for i in range(5):
            resp = _call_tool(client, "get_run_status", request_id=30 + i,
                              arguments={"run_id": "unknown-run-id"})
            assert resp.status_code != 429, (
                f"get_run_status poll {i + 1} throttled early: {resp.text}"
            )
        # The 6th exceeds even the elevated cap.
        resp = _call_tool(client, "get_run_status", request_id=99,
                          arguments={"run_id": "unknown-run-id"})
        assert resp.status_code == 429, resp.text


# ---------------------------------------------------------------------------
# Per-IP limit independently observable
# ---------------------------------------------------------------------------


def test_per_ip_limit_returns_429(monkeypatch):
    """The per-IP cap throttles independently of the per-token cap.

    Per-token cap left wide (1000); per-IP cap driven to 1. The second
    tool call from the same client IP is blocked on the IP scope even
    though the token is nowhere near its own cap.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("VALDO_MCP_RATE_LIMIT_PER_MINUTE", "1000")
    monkeypatch.setenv("VALDO_MCP_RATE_LIMIT_PER_IP_PER_MINUTE", "1")
    app = _fresh_app()

    with TestClient(app) as client:
        _init(client)
        first = _call_tool(client, "validate_file", request_id=40,
                           arguments={"source": "SHAW",
                                      "file_path": "/nonexistent.txt"})
        assert first.status_code != 429
        blocked = _call_tool(client, "validate_file", request_id=41,
                             arguments={"source": "SHAW",
                                        "file_path": "/nonexistent.txt"})
        assert blocked.status_code == 429, blocked.text
        assert int(blocked.headers.get("Retry-After", "0")) > 0

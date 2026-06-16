"""MCP ``/mcp/health`` load-balancer probe integration tests (S9-2, #390).

The MCP server can be wedged (failing handshake, dead session manager,
broken resource handler) while the surrounding FastAPI process is still
happily answering ``/api/v1/system/health`` with a 200. A load balancer
polling the FastAPI health endpoint would keep routing BA agents at a
broken MCP transport. ``/mcp/health`` closes that gap by exercising the
MCP-specific paths — a session-manager liveness check, a
``taxonomy://violations`` resource read, and a tool/resource/prompt
registry enumeration — and returning 503 the moment any of them fails.

Acceptance scenarios (mirrors the #390 AC list):

1. ``test_health_happy_path_200`` — with the session manager running, the
   endpoint returns 200 with ``status="healthy"`` and the live registry
   counts (tool/resource/prompt), the negotiated protocol version, and a
   non-negative uptime.

2. ``test_health_tool_count_matches_registry`` — the reported
   ``tool_count`` equals the number of tools the FastMCP registry
   actually advertises (computed from the registry, never a hardcoded
   magic literal), so the probe catches a half-registered tool surface.

3. ``test_health_no_auth_returns_200`` — a request carrying NO auth
   credential (no ``VALDO_MCP_AUTH=dev``, no API key, no cookie, no
   bearer token) still returns 200. Load balancers do not authenticate;
   the route must sit outside the MCP auth gate.

4. ``test_health_session_manager_dead_returns_503`` — when the session
   manager's liveness check is monkeypatched to report dead, the endpoint
   returns 503 with a structured failure reason rather than masking the
   outage behind a 200.

5. ``test_health_resource_read_failure_returns_503`` — when the
   ``taxonomy://violations`` resource read raises, the endpoint returns
   503 — proving the probe genuinely exercises the resource path and is
   not merely a process-liveness rubber stamp.

6. ``test_health_response_under_budget`` — the endpoint answers well
   inside the load-balancer poll budget. The #390 target is <100 ms
   (in-process only, no DB round-trip); CI asserts a generous <500 ms
   bound to stay stable on shared runners while documenting the tighter
   target.

These tests reuse the ``_fresh_app`` reload trick from
``test_mcp_scaffold.py`` so each test observes the MCP sub-app built
against the precise env it monkeypatches in.
"""

from __future__ import annotations

import importlib
import sys
import time

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _ensure_session_signing_key(monkeypatch):
    """Provide a dummy session signing key for app construction.

    Matches the fixture in ``test_mcp_scaffold.py``; ``config/ui.yml`` may
    enable ``auth.enabled: true``, in which case importing ``src.api.main``
    requires ``VALDO_SESSION_SIGNING_KEY`` (issue #9 fail-closed posture).
    """
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    The FastAPI app, the MCP server, the health service, and the dev-auth
    middleware all read env state at module import time, so we force a
    re-import to pick up monkeypatched env vars in each test.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.health",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


def test_health_happy_path_200(monkeypatch):
    """Happy path: running MCP server -> 200 with the full health payload.

    Dev-mode auth is irrelevant to the health route (it is registered
    outside the auth gate) but we set it here so the rest of the sub-app
    is in its normal dev posture. The payload must carry every field the
    #390 contract names.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        response = client.get("/mcp/health")

    assert response.status_code == 200, response.text
    body = response.json()

    assert body["status"] == "healthy", body
    assert isinstance(body["mcp_protocol_version"], str) and body["mcp_protocol_version"]
    # Today: ten tools registered (EF-S2 + EF-S4 + EF-S5 + S7-4). The probe
    # reports whatever the registry holds; we assert it is positive rather
    # than pinning a literal here — the registry-equality assertion lives in
    # ``test_health_tool_count_matches_registry``.
    assert body["tool_count"] > 0, body
    assert body["resource_count"] > 0, body
    assert body["prompt_count"] > 0, body
    assert body["uptime_seconds"] >= 0, body


def test_health_tool_count_matches_registry(monkeypatch):
    """Reported ``tool_count`` equals the live FastMCP registry size.

    We compute the expected count directly from the in-process FastMCP
    server (``await server.list_tools()``) rather than hardcoding a magic
    number — the probe and the test both derive the truth from the same
    registry, so a half-registered tool surface is caught without a
    brittle literal to bump on every new tool.
    """
    import anyio

    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    import src.api.main as main_module

    expected_tools = len(anyio.run(main_module._mcp_server.list_tools))
    expected_resources = len(anyio.run(main_module._mcp_server.list_resources))
    expected_prompts = len(anyio.run(main_module._mcp_server.list_prompts))

    with TestClient(app) as client:
        response = client.get("/mcp/health")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tool_count"] == expected_tools, (
        f"tool_count {body['tool_count']} != registry {expected_tools}"
    )
    assert body["resource_count"] == expected_resources, body
    assert body["prompt_count"] == expected_prompts, body


def test_health_no_auth_returns_200(monkeypatch):
    """No credential of any kind -> still 200 (load balancers don't auth).

    With ``VALDO_MCP_AUTH`` unset and no API key / cookie / bearer token,
    the MCP transport (``/mcp/``) fails closed with 401 — but the health
    route is registered OUTSIDE the auth gate, so it must answer 200. This
    is the load-balancer contract: the probe never carries a token.
    """
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    monkeypatch.delenv("API_KEYS", raising=False)
    app = _fresh_app()

    with TestClient(app) as client:
        response = client.get("/mcp/health")

    assert response.status_code == 200, (
        f"no-auth health probe must return 200, got {response.status_code}: "
        f"{response.text}"
    )
    assert response.json()["status"] == "healthy"


def test_health_session_manager_dead_returns_503(monkeypatch):
    """A dead session manager -> 503 with a structured failure reason.

    We monkeypatch the health service's session-manager liveness probe to
    report the manager as not running (the in-test stand-in for "the
    handshake transport is wedged"). The endpoint must surface a 503 with
    a machine-readable ``failed_check`` and human ``reason`` rather than a
    misleading 200.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    import src.mcp.health as health_mod

    monkeypatch.setattr(
        health_mod,
        "_session_manager_is_live",
        lambda server: False,
    )

    with TestClient(app) as client:
        response = client.get("/mcp/health")

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "unhealthy", body
    assert body["failed_check"] == "session_manager", body
    assert isinstance(body.get("reason"), str) and body["reason"], body


def test_health_resource_read_failure_returns_503(monkeypatch):
    """A broken ``taxonomy://violations`` read -> 503.

    Forcing the resource read to raise proves the probe genuinely
    exercises the resource-handler path. A process-liveness-only health
    check would still return 200 here; the MCP-aware probe must not.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    import src.mcp.health as health_mod

    async def _boom(server):
        raise RuntimeError("resource handler exploded")

    monkeypatch.setattr(health_mod, "_read_violations_resource", _boom)

    with TestClient(app) as client:
        response = client.get("/mcp/health")

    assert response.status_code == 503, response.text
    body = response.json()
    assert body["status"] == "unhealthy", body
    assert body["failed_check"] == "resource_read", body


def test_health_response_under_budget(monkeypatch):
    """The probe answers inside the load-balancer poll budget.

    #390 target is <100 ms (in-process, no DB round-trip). CI asserts a
    generous <500 ms bound so the test stays stable on contended shared
    runners; the tighter 100 ms target is documented in
    ``docs/PRODUCTION_DEPLOYMENT.md``.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        # Warm one call so import/JIT costs don't taint the measurement.
        client.get("/mcp/health")
        start = time.perf_counter()
        response = client.get("/mcp/health")
        elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert response.status_code == 200, response.text
    assert elapsed_ms < 500.0, f"health probe took {elapsed_ms:.1f}ms (CI bound 500ms)"

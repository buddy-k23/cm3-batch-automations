"""MCP Streamable HTTP scaffold integration tests (EF-S1).

Three acceptance scenarios:

1. ``test_mcp_initialize_handshake`` — happy path. With dev-mode auth set,
   a JSON-RPC ``initialize`` request to ``/mcp/`` completes successfully
   and the response advertises the three capability buckets (tools,
   resources, prompts), confirming the FastMCP server is wired into the
   FastAPI lifespan via its session manager.

2. ``test_mcp_without_dev_auth_returns_401`` — the auth gate fails closed.
   Without ``VALDO_MCP_AUTH=dev`` the MCP sub-app returns 401 with a
   deterministic JSON body, preventing anonymous access to the transport.

3. ``test_mcp_capabilities_advertise_empty_registries`` — registry shape
   guard. The scaffold registers no tools, resources, or prompts; the
   ``*/list`` calls must therefore return empty arrays so downstream
   stories can be verified against a clean baseline.

The tests use ``TestClient`` as a context manager so the FastAPI
``lifespan`` runs (the MCP session manager must be entered before the
transport accepts requests).

NOTE: The FastAPI app and the MCP sub-app are constructed at import time
in ``src/api/main.py``. We use ``importlib.reload`` plus environment
manipulation to force a fresh build per test so the
``TransportSecuritySettings`` and auth-middleware behaviour can be observed
against the precise env state each test wants.
"""

from __future__ import annotations

import importlib
import json
import sys

import pytest
from fastapi.testclient import TestClient


# JSON-RPC payload reused across tests. Matches the MCP 2025-06-18 protocol
# version specified in the EF-S1 ACs.
_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}

# Streamable HTTP transport expects clients to advertise that they can
# receive either JSON or an SSE event stream.
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    The FastAPI app, the MCP server, and the dev-auth middleware all read
    env state at module import time, so we must force re-import to pick up
    monkeypatched env vars in each test.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


@pytest.fixture(autouse=True)
def _ensure_session_signing_key(monkeypatch):
    """Provide a dummy session signing key for app construction.

    ``config/ui.yml`` may enable ``auth.enabled: true``, in which case
    importing ``src.api.main`` requires the ``VALDO_SESSION_SIGNING_KEY``
    secret to be present (issue #9 fail-closed posture). This fixture
    injects a deterministic test key so the FastAPI app can finish
    constructing — the EF-S1 MCP scaffold is orthogonal to LDAP auth and
    we should not exercise it here.
    """
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


def _parse_streamable_body(response):
    """Decode an MCP Streamable-HTTP response body to a JSON dict.

    The MCP server can answer either as a plain JSON object (because we
    set ``json_response=True`` in the FastMCP constructor) or as a
    single-event SSE stream depending on what the client asked for. We
    accept either shape and unwrap the SSE ``data:`` line when present.
    """
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        # Single-event SSE: extract the first ``data: {...}`` line.
        for raw_line in response.text.splitlines():
            line = raw_line.strip()
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(
            f"SSE response had no data line: {response.text!r}"
        )
    return response.json()


def test_mcp_initialize_handshake(monkeypatch):
    """``initialize`` handshake succeeds and advertises the three buckets.

    With ``VALDO_MCP_AUTH=dev`` set, the auth middleware passes the
    request through, the FastAPI lifespan has entered the MCP session
    manager, and the FastMCP server responds with a fully-formed
    ``initialize`` result. We assert:

    * HTTP 200 (no transport-level failure)
    * ``result.capabilities`` contains ``tools``, ``resources``, and
      ``prompts`` keys — FastMCP auto-advertises all three even when the
      registries are empty (this guarantee is part of the EF-S1 contract).
    * ``result.serverInfo.name == "valdo"`` so MCP clients see the
      expected server identity.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=_MCP_HEADERS)

    assert response.status_code == 200, response.text
    body = _parse_streamable_body(response)

    assert body.get("jsonrpc") == "2.0"
    assert "result" in body, f"missing 'result' in response: {body!r}"
    result = body["result"]

    capabilities = result.get("capabilities", {})
    assert "tools" in capabilities, f"tools missing: {capabilities!r}"
    assert "resources" in capabilities, f"resources missing: {capabilities!r}"
    assert "prompts" in capabilities, f"prompts missing: {capabilities!r}"

    server_info = result.get("serverInfo", {})
    assert server_info.get("name") == "valdo", server_info


def test_mcp_without_dev_auth_returns_401(monkeypatch):
    """Without dev-mode auth the MCP sub-app fails closed with 401.

    Removing ``VALDO_MCP_AUTH`` from the environment forces the auth
    middleware (mounted on the MCP sub-app only) to short-circuit every
    request with a deterministic 401 body. This guards against the
    scaffold accidentally exposing the MCP transport in environments
    that have not opted in to the dev-mode bypass.
    """
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    app = _fresh_app()

    with TestClient(app) as client:
        response = client.post("/mcp/", json=_INIT_PAYLOAD, headers=_MCP_HEADERS)

    assert response.status_code == 401, response.text
    assert response.json() == {"error": "MCP auth not configured"}


def test_mcp_capabilities_advertise_empty_registries(monkeypatch):
    """Registry shape guard: ``*/list`` return empty arrays in the scaffold.

    EF-S1 does not register any tools, resources, or prompts. The
    Streamable-HTTP transport must therefore answer the corresponding
    JSON-RPC ``list`` calls with empty arrays. EF-S2 / EF-S3 / EF-S6 will
    populate these registries; this test pins the baseline.

    Each ``*/list`` call is sent as its own POST because the scaffold uses
    stateless HTTP (``stateless_http=True``); there is no session token
    to thread between calls.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        # Re-issue the initialize handshake so any future stateful-mode
        # variants of this test continue to work. In stateless_http mode
        # the FastMCP transport does not require an explicit initialize
        # before list/* calls, but issuing it costs nothing and matches
        # the spec-mandated client flow.
        init = client.post("/mcp/", json=_INIT_PAYLOAD, headers=_MCP_HEADERS)
        assert init.status_code == 200, init.text

        for idx, (method, result_key) in enumerate(
            [
                ("tools/list", "tools"),
                ("resources/list", "resources"),
                ("prompts/list", "prompts"),
            ],
            start=2,
        ):
            payload = {
                "jsonrpc": "2.0",
                "id": idx,
                "method": method,
                "params": {},
            }
            response = client.post("/mcp/", json=payload, headers=_MCP_HEADERS)
            assert response.status_code == 200, (
                f"{method} returned {response.status_code}: {response.text}"
            )
            body = _parse_streamable_body(response)
            assert "result" in body, f"{method} body missing 'result': {body!r}"
            registry = body["result"].get(result_key)
            assert registry == [], (
                f"{method} expected empty {result_key} array, got {registry!r}"
            )

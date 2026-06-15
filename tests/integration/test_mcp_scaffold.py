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


def test_mcp_capabilities_advertise_expected_registries(monkeypatch):
    """Registry shape guard: ``*/list`` returns the current sprint baseline.

    Baseline pinned after each MCP-track story lands:

    * ``tools/list``     — exactly nine entries: three read-only added
      by EF-S2 (``list_sources``, ``get_source_spec``,
      ``list_recent_runs``), three action tools added by EF-S4
      (``validate_file``, ``get_run_status``, ``get_violations``), and
      three onboarding tools added by EF-S5
      (``upload_workbook_as_spec``, ``onboard_source_dry_run``,
      ``infer_mapping_from_sample``). Stories EF-S6 and onwards will
      extend this set.
    * ``resources/list`` — exactly two entries, the
      ``taxonomy://violations`` and ``taxonomy://rules`` URIs added by
      EF-S3. Stories EF-S5 and onwards will extend this; this test pins
      the current expected set so accidental drift is caught.
    * ``prompts/list``   — empty array (EF-S6 will populate).

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

        list_results: dict[str, list] = {}
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
            list_results[method] = body["result"].get(result_key, [])

    # EF-S6 still pending — prompts stay empty.
    assert list_results["prompts/list"] == [], list_results["prompts/list"]

    # EF-S2 + EF-S4 + EF-S5 — exactly nine tools: three read-only,
    # three action, three onboarding, no more, no less. The full
    # input-schema shape of each tool is asserted in
    # ``test_mcp_read_tools.py`` (EF-S2), ``test_mcp_action_tools.py``
    # (EF-S4), and ``test_mcp_onboarding_tools.py`` (EF-S5); here we
    # only pin the registry size and tool names so accidental drift is
    # caught at the registry level.
    tool_names = sorted(t["name"] for t in list_results["tools/list"])
    assert tool_names == [
        "get_run_status",
        "get_source_spec",
        "get_violations",
        "infer_mapping_from_sample",
        "list_recent_runs",
        "list_sources",
        "onboard_source_dry_run",
        "upload_workbook_as_spec",
        "validate_file",
    ], f"tools/list names drifted from EF-S2+EF-S4+EF-S5 baseline: {tool_names!r}"

    # EF-S3 — exactly the two taxonomy resources, no more, no less. The
    # shape of each entry is asserted in
    # ``test_mcp_taxonomy_resources.py``; here we only verify the
    # registry size and URIs so this test stays a focused baseline.
    resource_uris = sorted(r["uri"] for r in list_results["resources/list"])
    assert resource_uris == ["taxonomy://rules", "taxonomy://violations"], (
        f"resources/list URIs drifted from EF-S3 baseline: {resource_uris!r}"
    )

"""MCP read-only tool integration tests (EF-S2).

Five acceptance scenarios mapped to the EF-S2 story:

1. ``test_list_sources_returns_committed_sources`` — ``tools/call``
   ``list_sources`` includes the committed ``SHAW`` source.
2. ``test_get_source_spec_returns_bundle_for_shaw`` — ``tools/call``
   ``get_source_spec(name="SHAW")`` returns a bundle whose
   ``source_yaml``, ``mapping_files``, and ``rules_files`` are non-empty
   and whose first mapping content carries a recognisable marker.
3. ``test_get_source_spec_unknown_source_raises`` — ``tools/call`` on a
   non-existent source returns an MCP error (``isError`` set on the
   ``CallToolResult``).
4. ``test_list_recent_runs_empty_when_offline`` — with the run-history
   backend unavailable the tool returns ``[]`` rather than raising. We
   force the offline path by monkey-patching the underlying
   ``fetch_history_from_db`` to raise an exception that mimics a missing
   Oracle adapter.
5. ``test_list_recent_runs_source_filter`` — the ``source`` parameter is
   honoured: when patched to return a mixed slice of run history, only
   the runs whose ``suite_name`` mentions the filter source come back.

The tests reuse the ``_fresh_app`` reload trick from
``test_mcp_scaffold.py`` so each test observes a clean MCP sub-app under
its monkey-patched env state.
"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient


# JSON-RPC payload reused across tests. Matches the MCP 2025-06-18
# protocol version specified in the EF-S1 ACs.
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

    Mirrors the helper in ``test_mcp_scaffold.py`` — the FastAPI app, MCP
    server, and dev-auth middleware all read env state at module import
    time so we must force a re-import per test.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.tools",
        "src.mcp.taxonomy",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


@pytest.fixture(autouse=True)
def _ensure_session_signing_key(monkeypatch):
    """Provide a dummy session signing key for app construction.

    Matches the fixture in ``test_mcp_scaffold.py``; see that file's
    docstring for context on why this is required by the FastAPI auth
    layer at import time.
    """
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


def _parse_streamable_body(response) -> Dict[str, Any]:
    """Decode an MCP Streamable-HTTP response body to a JSON dict."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for raw_line in response.text.splitlines():
            line = raw_line.strip()
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(
            f"SSE response had no data line: {response.text!r}"
        )
    return response.json()


def _rpc(
    client: TestClient,
    method: str,
    *,
    request_id: int,
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Send a single JSON-RPC POST to ``/mcp/`` and parse the body."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    response = client.post("/mcp/", json=payload, headers=_MCP_HEADERS)
    assert response.status_code == 200, (
        f"{method} returned {response.status_code}: {response.text}"
    )
    return _parse_streamable_body(response)


def _call_tool(
    client: TestClient,
    tool_name: str,
    *,
    request_id: int,
    arguments: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Invoke ``tools/call`` for *tool_name* and return the parsed body.

    The MCP spec wraps tool results in a ``CallToolResult`` carrying
    ``content`` (list of content blocks) and optional ``structuredContent``
    and ``isError`` fields. Callers are responsible for inspecting the
    shape they expect; this helper only handles the JSON-RPC envelope.
    """
    return _rpc(
        client,
        "tools/call",
        request_id=request_id,
        params={
            "name": tool_name,
            "arguments": arguments or {},
        },
    )


def _structured_or_text(result: Dict[str, Any]) -> Any:
    """Extract the JSON payload from a CallToolResult.

    FastMCP returns structured output via the ``structuredContent`` field
    when the tool's return type is a structured shape (list / dict). When
    structuredContent is absent we fall back to parsing the first
    ``TextContent`` block as JSON, which is FastMCP's default rendering
    for primitives or for older clients that don't advertise structured
    output support.
    """
    structured = result.get("structuredContent")
    if structured is not None:
        # FastMCP wraps list results under the ``result`` key when the
        # tool's return type is a list; dict results are passed through
        # as-is. Both shapes are valid per the MCP spec.
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured
    contents = result.get("content") or []
    assert contents, f"CallToolResult has no content: {result!r}"
    first = contents[0]
    text = first.get("text")
    assert isinstance(text, str) and text, (
        f"CallToolResult first content has no text: {first!r}"
    )
    return json.loads(text)


# ---------------------------------------------------------------------------
# 1. list_sources
# ---------------------------------------------------------------------------


def test_list_sources_returns_committed_sources(monkeypatch):
    """``tools/call list_sources`` enumerates on-disk sources.

    The repo ships ``config/e2e/sources/SHAW.yml`` and
    ``config/e2e/sources/SRC_A.yml``; the tool MUST surface both. Each
    entry must carry the ``name`` field plus the three ``last_run_*``
    slots (which may be ``None`` in a dev environment without a
    populated Oracle backend — the schema shape is what we pin here).
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(client, "list_sources", request_id=2)

    assert "result" in body, f"tools/call missing 'result': {body!r}"
    result = body["result"]
    assert not result.get("isError"), f"tool reported isError: {result!r}"

    payload = _structured_or_text(result)
    assert isinstance(payload, list), f"list_sources payload not a list: {payload!r}"

    names = {entry["name"] for entry in payload}
    assert "SHAW" in names, f"SHAW missing from list_sources: {sorted(names)}"

    # Every entry must carry the four documented keys, even when the
    # last-run pointer is null.
    expected_keys = {"name", "last_run_id", "last_run_status", "last_run_at"}
    for entry in payload:
        assert expected_keys.issubset(entry.keys()), (
            f"list_sources entry missing keys {expected_keys - entry.keys()}: {entry!r}"
        )


# ---------------------------------------------------------------------------
# 2. get_source_spec — happy path
# ---------------------------------------------------------------------------


def test_get_source_spec_returns_bundle_for_shaw(monkeypatch):
    """``tools/call get_source_spec(name="SHAW")`` returns the bundle.

    The bundle MUST contain the source overlay YAML (non-empty),
    non-empty ``mapping_files`` and ``rules_files`` lists, and a non-null
    ``reconciliation_yaml`` (SHAW ships
    ``config/e2e/sources/SHAW/reconciliation/tranert.yml``). At least
    one mapping file's content must contain a recognisable marker —
    SHAW mapping JSONs all carry the ``"fields"`` array, so we assert
    that literal string appears in the first mapping's content.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "get_source_spec",
            request_id=2,
            arguments={"name": "SHAW"},
        )

    assert "result" in body, f"tools/call missing 'result': {body!r}"
    result = body["result"]
    assert not result.get("isError"), f"tool reported isError: {result!r}"

    bundle = _structured_or_text(result)
    assert isinstance(bundle, dict), f"get_source_spec payload not a dict: {bundle!r}"

    # 1. source_yaml — non-empty raw text.
    source_yaml = bundle.get("source_yaml") or ""
    assert "source: SHAW" in source_yaml, (
        f"source_yaml missing SHAW declaration: head={source_yaml[:200]!r}"
    )

    # 2. mapping_files — at least one entry, with path + content.
    mapping_files = bundle.get("mapping_files") or []
    assert mapping_files, "mapping_files unexpectedly empty for SHAW"
    first_mapping = mapping_files[0]
    assert first_mapping.get("path", "").startswith("config/mappings/SHAW"), (
        f"mapping_files[0].path does not look right: {first_mapping!r}"
    )
    content = first_mapping.get("content") or ""
    # All SHAW mapping artefacts — both the flat per-record JSONs (which
    # carry a top-level ``"fields"`` array) and the umbrella YAMLs
    # (which carry ``mapping:`` references to those per-record JSONs) —
    # mention the word ``mapping`` somewhere. Use it as a smoke marker
    # that doesn't couple to either schema shape.
    assert "mapping" in content.lower(), (
        f"mapping_files[0].content missing 'mapping' marker; head={content[:200]!r}"
    )
    # Sanity-check that the bundle includes at least one entry that
    # carries the per-record-JSON marker too, so we know the JSON walk
    # picked them up and not just the umbrella YAML.
    assert any(
        '"fields"' in (m.get("content") or "") for m in mapping_files
    ), "no mapping file in bundle contains the per-record JSON 'fields' marker"

    # 3. rules_files — non-empty list with path entries.
    rules_files = bundle.get("rules_files") or []
    assert rules_files, "rules_files unexpectedly empty for SHAW"
    assert all(
        r.get("path", "").startswith("config/rules/SHAW") for r in rules_files
    ), f"rules_files contains non-SHAW entries: {[r['path'] for r in rules_files]!r}"

    # 4. reconciliation_yaml — SHAW ships tranert.yml.
    recon = bundle.get("reconciliation_yaml")
    assert isinstance(recon, str) and recon.strip(), (
        f"reconciliation_yaml unexpectedly empty: {recon!r}"
    )

    # 5. expected_sql_files — SHAW ships a SQL bundle under sql/tranert/.
    expected_sql = bundle.get("expected_sql_files") or []
    assert expected_sql, "expected_sql_files unexpectedly empty for SHAW"


# ---------------------------------------------------------------------------
# 3. get_source_spec — unknown source raises
# ---------------------------------------------------------------------------


def test_get_source_spec_unknown_source_raises(monkeypatch):
    """``get_source_spec`` on an unknown source returns an MCP tool error.

    FastMCP catches the ``ToolError`` thrown by the implementation and
    surfaces it via the ``CallToolResult.isError = True`` channel. The
    JSON-RPC envelope itself still arrives with ``result`` populated;
    callers must inspect ``isError`` to distinguish success from failure.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "get_source_spec",
            request_id=2,
            arguments={"name": "DOES_NOT_EXIST"},
        )

    result = body.get("result") or {}
    # The MCP spec allows either an error in the JSON-RPC envelope or an
    # ``isError: true`` flag on the CallToolResult. FastMCP uses the
    # latter for tool-level failures. Accept either to keep the test
    # robust to FastMCP version drift.
    error_envelope = body.get("error")
    is_error_flag = result.get("isError") is True
    assert error_envelope is not None or is_error_flag, (
        f"Expected an MCP error for unknown source; got: {body!r}"
    )

    # Whichever channel carried the error, the human-readable message
    # must name the unknown source so an agent can surface it.
    if error_envelope is not None:
        message = (error_envelope.get("message") or "").lower()
    else:
        # ``content`` carries the error text as a text block when
        # ``isError`` is set.
        content_blocks = result.get("content") or []
        message = " ".join(
            (block.get("text") or "") for block in content_blocks
        ).lower()
    assert "does_not_exist" in message, (
        f"Error message does not name the unknown source: {message!r}"
    )


# ---------------------------------------------------------------------------
# 4. list_recent_runs — graceful degradation when offline
# ---------------------------------------------------------------------------


def test_list_recent_runs_empty_when_offline(monkeypatch):
    """Run-history backend offline -> tool returns ``[]`` (no exception).

    We patch ``fetch_history_from_db`` to raise so we can force the
    degraded path even when the developer happens to have a working
    Oracle DSN exported in their shell. The contract is: the tool must
    swallow the failure, log a warning, and return ``[]``.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    # Patch BEFORE we build the app so the tools module picks up the
    # patched fetch when its lazy import runs at first invocation.
    import src.services.run_history_service as svc

    def _boom(limit: int = 20):  # noqa: ARG001 — signature match
        raise RuntimeError("simulated DB unreachable")

    monkeypatch.setattr(svc, "fetch_history_from_db", _boom)

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(client, "list_recent_runs", request_id=2)

    result = body.get("result") or {}
    assert not result.get("isError"), (
        f"list_recent_runs raised on offline backend (should degrade): {result!r}"
    )
    payload = _structured_or_text(result)
    assert payload == [], (
        f"list_recent_runs did not degrade to empty list: {payload!r}"
    )


# ---------------------------------------------------------------------------
# 5. list_recent_runs — source filter is honoured
# ---------------------------------------------------------------------------


def test_list_recent_runs_source_filter(monkeypatch):
    """``source`` parameter filters by case-insensitive suite_name substring.

    We stub the run-history backend with a deterministic mixed slice of
    runs and assert only the SHAW-tagged entries survive the filter. The
    response shape (``run_id``, ``source``, ``status``, ``started_at``,
    ``duration_seconds``) is pinned here too so downstream EF-S4 stories
    can verify they extend rather than replace the contract.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    fake_history: List[Dict[str, Any]] = [
        {
            "run_id": "run-shaw-1",
            "suite_name": "SHAW_TRANERT_sit",
            "environment": "sit",
            "timestamp": "2026-06-13T22:00:00.000000Z",
            "status": "PASS",
            "pass_count": 12,
            "fail_count": 0,
            "skip_count": 0,
            "total_count": 12,
            "report_url": "/reports/shaw1.html",
            "archive_path": "",
            "quality_score": None,
        },
        {
            "run_id": "run-srca-1",
            "suite_name": "SRC_A_smoke",
            "environment": "sit",
            "timestamp": "2026-06-13T21:30:00.000000Z",
            "status": "FAIL",
            "pass_count": 2,
            "fail_count": 1,
            "skip_count": 0,
            "total_count": 3,
            "report_url": "/reports/srca1.html",
            "archive_path": "",
            "quality_score": None,
        },
        {
            "run_id": "run-shaw-2",
            "suite_name": "shaw-atoctran-smoke",  # lowercase + dash form
            "environment": "sit",
            "timestamp": "2026-06-13T20:00:00.000000Z",
            "status": "PASS",
            "pass_count": 5,
            "fail_count": 0,
            "skip_count": 0,
            "total_count": 5,
            "report_url": "/reports/shaw2.html",
            "archive_path": "",
            "quality_score": None,
        },
    ]

    import src.services.run_history_service as svc

    def _fake_fetch(limit: int = 20):
        return list(fake_history[:limit])

    monkeypatch.setattr(svc, "fetch_history_from_db", _fake_fetch)

    app = _fresh_app()

    # Both the filtered and unfiltered calls go through a single
    # TestClient context — the FastMCP session manager refuses to be
    # re-entered on the same app instance, so we do not pay the cost of
    # rebuilding the app between calls. The stateless_http transport
    # (set in src/mcp/server.build_mcp_server) makes this safe: every
    # request is independent.
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "list_recent_runs",
            request_id=2,
            arguments={"source": "SHAW", "limit": 10},
        )
        body_all = _call_tool(
            client,
            "list_recent_runs",
            request_id=3,
            arguments={"limit": 10},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"tool reported isError: {result!r}"
    payload = _structured_or_text(result)
    assert isinstance(payload, list) and payload, (
        f"list_recent_runs payload not a non-empty list: {payload!r}"
    )

    run_ids = {entry["run_id"] for entry in payload}
    assert run_ids == {"run-shaw-1", "run-shaw-2"}, (
        f"source filter did not isolate SHAW runs: {run_ids!r}"
    )

    # Pin the EF-S2 response shape so EF-S4 can extend it deliberately.
    expected_keys = {
        "run_id",
        "source",
        "file_type",
        "status",
        "started_at",
        "duration_seconds",
    }
    for entry in payload:
        assert expected_keys.issubset(entry.keys()), (
            f"list_recent_runs entry missing keys "
            f"{expected_keys - entry.keys()}: {entry!r}"
        )
        # The ``source`` slot is filled in from the filter when a filter
        # was supplied.
        assert entry["source"] == "SHAW", entry

    # Unfiltered call must return all three rows.
    payload_all = _structured_or_text((body_all.get("result") or {}))
    assert {e["run_id"] for e in payload_all} == {
        "run-shaw-1",
        "run-srca-1",
        "run-shaw-2",
    }, f"unfiltered call did not return all rows: {payload_all!r}"

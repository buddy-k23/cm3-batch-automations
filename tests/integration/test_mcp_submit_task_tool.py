"""MCP ``submit_task`` tool integration tests (S22-4, #441).

Exercises the submit-task tool end-to-end over the MCP Streamable-HTTP transport
(JSON-RPC ``tools/call``) against a throwaway :class:`JobStateStore` rooted under
``tmp_path`` (no Oracle, never tracked). Mirrors the S22-1 ``parse_file`` /
S22-2 ``run_etl_pipeline`` / S22-3 ``export_failed_rows`` MCP test harnesses
(``_fresh_app`` reload, SSE-or-JSON body parser, ``_assert_is_error`` channel
acceptance).

The ``submit_task`` tool is a thin wrapper over the same canonical task-ingest
code path the ``valdo submit-task`` CLI command
(:func:`src.commands.submit_task_command.run_submit_task_command`) and the
``POST /api/v1/tasks/submit`` REST endpoint
(:func:`src.api.routers.tasks.submit_task`) drive: normalise -> contract-validate
-> idempotency-key dedup -> store write. It returns the canonical task id +
status JSON.

Scenarios:

1. ``test_submit_task_returns_task_id_and_status`` — a valid request enqueues a
   task and returns a non-empty ``task_id`` + ``status == "queued"``.
2. ``test_submit_task_idempotent_dedup`` — re-submitting with the same
   idempotency key returns the SAME ``task_id`` (deduplicated, not a second
   task) and a duplicate-key warning.
3. ``test_submit_task_invalid_intent_raises`` — a blank intent is a
   caller-fixable contract failure -> MCP tool error.
4. ``test_submit_task_advertised_in_tools_list`` — the tool is listed by
   ``tools/list`` with the expected name + non-empty description.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
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
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.task_tools",
        "src.mcp.export_tools",
        "src.mcp.etl_pipeline_tools",
        "src.mcp.parse_tools",
        "src.mcp.drift_tools",
        "src.mcp.extract_tools",
        "src.mcp.mask_tools",
        "src.mcp.reconcile_all_tools",
        "src.mcp.db_compare_tools",
        "src.mcp.reconcile_tools",
        "src.mcp.compare_tools",
        "src.mcp.action_tools",
        "src.mcp.tools",
        "src.mcp.taxonomy",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    return importlib.import_module("src.api.main").app


@pytest.fixture(autouse=True)
def _ensure_session_signing_key(monkeypatch):
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


@pytest.fixture(autouse=True)
def _isolated_job_store(monkeypatch, tmp_path):
    """Root the JobStateStore DB under tmp_path so tests never touch valdo.db.

    The submit-task code path constructs a fresh ``JobStateStore()`` per call;
    patching the module-level default ``DB_PATH`` redirects every such
    construction at the throwaway sqlite file under ``tmp_path``.
    """
    import src.services.job_state_store as store_mod

    monkeypatch.setattr(store_mod, "DB_PATH", tmp_path / "job_state.db")


def _parse_streamable_body(response) -> Dict[str, Any]:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for raw_line in response.text.splitlines():
            line = raw_line.strip()
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(f"SSE response had no data line: {response.text!r}")
    return response.json()


def _rpc(client, method, *, request_id, params=None) -> Dict[str, Any]:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
    response = client.post("/mcp/", json=payload, headers=_MCP_HEADERS)
    assert response.status_code == 200, f"{method} -> {response.status_code}: {response.text}"
    return _parse_streamable_body(response)


def _call_tool(client, tool_name, *, request_id, arguments=None) -> Dict[str, Any]:
    return _rpc(
        client,
        "tools/call",
        request_id=request_id,
        params={"name": tool_name, "arguments": arguments or {}},
    )


def _structured_or_text(result: Dict[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if structured is not None:
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured
    contents = result.get("content") or []
    assert contents, f"CallToolResult has no content: {result!r}"
    text = contents[0].get("text")
    assert isinstance(text, str) and text, f"first content has no text: {contents[0]!r}"
    return json.loads(text)


def _assert_is_error(body: Dict[str, Any], needle: str) -> None:
    result = body.get("result") or {}
    error_envelope = body.get("error")
    is_error_flag = result.get("isError") is True
    assert error_envelope is not None or is_error_flag, (
        f"Expected an MCP error mentioning {needle!r}; got: {body!r}"
    )
    if error_envelope is not None:
        message = (error_envelope.get("message") or "").lower()
    else:
        content_blocks = result.get("content") or []
        message = " ".join((b.get("text") or "") for b in content_blocks).lower()
    assert needle.lower() in message, (
        f"Tool error message did not mention {needle!r}: {message!r}"
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_submit_task_returns_task_id_and_status(monkeypatch):
    """A valid task request enqueues a task and returns its id + queued status."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "submit_task", request_id=2,
            arguments={
                "intent": "validate",
                "payload": {"source": "HR", "file": "/tmp/hr.psv"},
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"submit_task reported isError: {result!r}"
    payload = _structured_or_text(result)

    assert payload["status"] == "queued", payload
    assert isinstance(payload["task_id"], str) and payload["task_id"], payload
    assert isinstance(payload["trace_id"], str) and payload["trace_id"], payload
    assert payload["errors"] == [], payload


def test_submit_task_idempotent_dedup(monkeypatch):
    """Same idempotency key -> dedup to the SAME task_id (not a second task)."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    args = {
        "intent": "validate",
        "payload": {"source": "HR", "file": "/tmp/hr.psv"},
        "idempotency_key": "S22-4-dedup-key-001",
    }

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        first = _call_tool(client, "submit_task", request_id=2, arguments=args)
        second = _call_tool(client, "submit_task", request_id=3, arguments=args)

    first_payload = _structured_or_text(first.get("result") or {})
    second_payload = _structured_or_text(second.get("result") or {})

    assert first_payload["task_id"], first_payload
    # The second submission must NOT mint a new task — it returns the first id.
    assert second_payload["task_id"] == first_payload["task_id"], (
        first_payload, second_payload,
    )
    assert "duplicate idempotency key" in second_payload["warnings"], second_payload


def test_submit_task_invalid_intent_raises(monkeypatch):
    """A blank intent fails contract validation -> MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "submit_task", request_id=2,
            arguments={"intent": "", "payload": {"source": "HR"}},
        )

    _assert_is_error(body, "intent")


def test_submit_task_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises submit_task with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "submit_task" in by_name, sorted(by_name)
    assert by_name["submit_task"].get("description")

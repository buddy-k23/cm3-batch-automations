"""MCP ``run_suite`` tool integration tests (S22-5, #442).

Exercises the run-suite tool end-to-end over the MCP Streamable-HTTP transport
(JSON-RPC ``tools/call``) against a small, database-free suite fixture written
under ``tmp_path`` (a single ``structural`` test over a pipe-delimited file — no
Oracle, no SQLite). Mirrors the S22-1 ``parse_file`` / S22-2 ``run_etl_pipeline``
/ S22-3 ``export_failed_rows`` / S22-4 ``submit_task`` MCP test harnesses
(``_fresh_app`` reload, SSE-or-JSON body parser, ``_assert_is_error`` channel
acceptance).

The ``run_suite`` tool is a thin wrapper over the same suite runner the
``valdo run-tests`` CLI command drives
(:func:`src.commands.run_tests_command.run_suite_from_path`): load the suite
YAML, run each test, evaluate thresholds, aggregate. It returns the per-test
pass/fail breakdown plus the overall verdict. This tool COMPLETES MCP parity.

Scenarios:

1. ``test_run_suite_returns_summary`` — a clean file-based structural suite runs
   and returns ``overall_status == "PASS"`` with a per-test record.
2. ``test_run_suite_missing_file_raises`` — a non-existent suite path is a
   caller-fixable MCP tool error mentioning "not found".
3. ``test_run_suite_advertised_in_tools_list`` — the tool is listed by
   ``tools/list`` with the expected name + non-empty description.
"""

from __future__ import annotations

import importlib
import json
import sys
import textwrap
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
        "src.mcp.suite_tools",
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


@pytest.fixture
def _suite_fixture(tmp_path):
    """Write a database-free structural suite + its data/mapping under tmp_path.

    Returns the absolute path to the suite YAML. The suite runner writes HTML
    reports + run history under ``output_dir`` (passed as an argument), so no
    repo state is touched.
    """
    mapping = {
        "mapping_name": "s22_5_customers",
        "version": "1.0.0",
        "source": {"type": "file", "format": "pipe_delimited"},
        "target": {"type": "database", "table_name": "CUSTOMER"},
        "mappings": [
            {
                "source_column": "customer_id",
                "target_column": "CUSTOMER_ID",
                "data_type": "string",
                "required": True,
                "validation_rules": [{"type": "not_null"}],
            },
            {
                "source_column": "name",
                "target_column": "NAME",
                "data_type": "string",
                "required": True,
                "validation_rules": [{"type": "not_null"}],
            },
        ],
        "key_columns": ["customer_id"],
    }
    mapping_path = tmp_path / "mapping.json"
    mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

    data_path = tmp_path / "customers.psv"
    data_path.write_text(
        "customer_id|name\nCUST01|alice\nCUST02|bob\n", encoding="utf-8"
    )

    suite_path = tmp_path / "suite.yaml"
    suite_path.write_text(
        textwrap.dedent(
            f"""\
            name: S22-5 MCP File Suite
            environment: dev
            tests:
              - name: Customer Structure Check
                type: structural
                file: {data_path}
                mapping: {mapping_path}
                thresholds:
                  max_errors: 0
            """
        ),
        encoding="utf-8",
    )
    return suite_path


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


def test_run_suite_returns_summary(monkeypatch, tmp_path, _suite_fixture):
    """A clean file-based structural suite runs and returns a PASS summary."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    out_dir = tmp_path / "out"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "run_suite", request_id=2,
            arguments={
                "suite": str(_suite_fixture),
                "env": "dev",
                "output_dir": str(out_dir),
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"run_suite reported isError: {result!r}"
    payload = _structured_or_text(result)

    assert payload["suite"] == "S22-5 MCP File Suite", payload
    assert payload["overall_status"] == "PASS", payload
    assert payload["total_count"] == 1, payload
    assert payload["pass_count"] == 1, payload
    assert payload["fail_count"] == 0, payload
    assert len(payload["tests"]) == 1, payload
    assert payload["tests"][0]["name"] == "Customer Structure Check", payload
    assert payload["tests"][0]["status"] == "PASS", payload


def test_run_suite_missing_file_raises(monkeypatch, tmp_path):
    """A non-existent suite path is a caller-fixable MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    missing = tmp_path / "does_not_exist.yaml"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "run_suite", request_id=2,
            arguments={"suite": str(missing), "output_dir": str(tmp_path)},
        )

    _assert_is_error(body, "not found")


def test_run_suite_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises run_suite with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "run_suite" in by_name, sorted(by_name)
    assert by_name["run_suite"].get("description")

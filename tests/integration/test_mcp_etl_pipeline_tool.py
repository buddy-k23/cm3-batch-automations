"""MCP ``run_etl_pipeline`` tool integration tests (S22-2, #439).

Exercises the ETL-pipeline runner tool end-to-end over the MCP Streamable-HTTP
transport (JSON-RPC ``tools/call``) against a small, hermetic, FILE-BASED pipeline
written under ``tmp_path`` (never tracked, no Oracle). Mirrors the S22-1
``parse_file`` MCP test harness (``_fresh_app`` reload, SSE-or-JSON body parser,
``_assert_is_error`` channel acceptance).

The ``run_etl_pipeline`` tool is a thin wrapper over the same ETL pipeline
orchestrator the ``valdo run-etl-pipeline`` CLI command drives
(:class:`~src.pipeline.etl_pipeline_runner.ETLPipelineRunner`). It returns the
runner's structured aggregate result — the per-gate pass/fail breakdown plus the
overall pipeline verdict.

The fixture pipeline uses a single ``validate`` gate against a tiny pipe-delimited
data file + mapping JSON, so the test drives a REAL gate end-to-end with no
database. A second fixture flips one threshold so the same data trips a gate
failure — proving the per-gate + overall ``status`` propagates as a RESULT (not a
tool error).

Scenarios:

1. ``test_run_etl_pipeline_passes`` — a clean file-based pipeline returns
   ``status == "passed"`` with a passing per-gate record.
2. ``test_run_etl_pipeline_reports_gate_failure`` — a breached threshold yields
   ``status == "failed"`` with the failing gate flagged — a RESULT, not an error.
3. ``test_run_etl_pipeline_missing_config_raises`` — a non-existent config is a
   caller-fixable problem -> MCP tool error.
4. ``test_run_etl_pipeline_advertised_in_tools_list`` — the tool is listed by
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
# Fixture builders — a hermetic, file-only validate pipeline (no Oracle).
# ---------------------------------------------------------------------------

# Three clean rows: string name + integer age. Headerless (has_header=False).
_PIPE_DATA = "Alice|30\nBob|25\nCarol|35\n"

_MAPPING = {
    "mapping_name": "etl_pipeline_tool_test",
    "version": "1.0.0",
    "source": {"type": "file", "format": "pipe_delimited", "has_header": False},
    "fields": [
        {"name": "name", "data_type": "string"},
        {"name": "age", "data_type": "integer"},
    ],
    "key_columns": ["name"],
}


def _write_pipeline(tmp_path: Path, *, min_rows: int) -> str:
    """Write data + mapping + a single-gate validate pipeline; return config path.

    The gate validates the pipe-delimited data file against the mapping with a
    ``min_rows`` threshold. With ``min_rows <= 3`` the three-row file passes;
    with ``min_rows > 3`` the row-count threshold is breached so the gate (and
    pipeline) fail.
    """
    data_file = tmp_path / "people.psv"
    data_file.write_text(_PIPE_DATA, encoding="utf-8")

    mapping_file = tmp_path / "people_mapping.json"
    mapping_file.write_text(json.dumps(_MAPPING), encoding="utf-8")

    pipeline = {
        "name": "etl-tool-test-pipeline",
        "description": "Hermetic file-only validate pipeline for the MCP tool test.",
        "gates": [
            {
                "name": "input_validation",
                "blocking": True,
                "steps": [
                    {
                        "type": "validate",
                        "file": str(data_file),
                        "mapping": str(mapping_file),
                        "thresholds": {"min_rows": min_rows},
                    }
                ],
            }
        ],
    }
    config_file = tmp_path / "pipeline.yaml"
    # JSON is valid YAML — avoids a hard yaml dependency in the test body.
    config_file.write_text(json.dumps(pipeline), encoding="utf-8")
    return str(config_file)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_etl_pipeline_passes(monkeypatch, tmp_path):
    """A clean file-based pipeline returns overall + per-gate ``passed``."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    config = _write_pipeline(tmp_path, min_rows=1)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "run_etl_pipeline", request_id=2,
            arguments={"config": config},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"run_etl_pipeline reported isError: {result!r}"
    payload = _structured_or_text(result)

    assert payload["pipeline_name"] == "etl-tool-test-pipeline", payload
    assert payload["status"] == "passed", payload
    gates = payload["gates"]
    assert isinstance(gates, list) and len(gates) == 1, payload
    assert gates[0]["name"] == "input_validation", payload
    assert gates[0]["status"] == "passed", payload
    # The single validate step ran and passed.
    steps = gates[0]["steps"]
    assert isinstance(steps, list) and len(steps) == 1, payload
    assert steps[0]["status"] == "passed", payload
    # Timestamps are emitted by the runner.
    assert payload.get("started_at") and payload.get("finished_at"), payload


def test_run_etl_pipeline_reports_gate_failure(monkeypatch, tmp_path):
    """A breached threshold yields status 'failed' — a RESULT, not a tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    # min_rows=10 cannot be met by the 3-row file -> gate fails.
    config = _write_pipeline(tmp_path, min_rows=10)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "run_etl_pipeline", request_id=2,
            arguments={"config": config},
        )

    result = body.get("result") or {}
    # A gate failure is a RESULT — the tool call itself succeeds.
    assert not result.get("isError"), f"gate failure must not be a tool error: {result!r}"
    payload = _structured_or_text(result)

    assert payload["status"] == "failed", payload
    gates = payload["gates"]
    assert gates[0]["status"] == "failed", payload
    assert gates[0]["steps"][0]["status"] == "failed", payload


def test_run_etl_pipeline_missing_config_raises(monkeypatch, tmp_path):
    """A non-existent config surfaces an MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    bogus = tmp_path / "does_not_exist.yaml"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "run_etl_pipeline", request_id=2,
            arguments={"config": str(bogus)},
        )

    _assert_is_error(body, "not found")


def test_run_etl_pipeline_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises run_etl_pipeline with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "run_etl_pipeline" in by_name, sorted(by_name)
    assert by_name["run_etl_pipeline"].get("description")

"""MCP ``export_failed_rows`` tool integration tests (S22-3, #440).

Exercises the export-failed-rows tool end-to-end over the MCP Streamable-HTTP
transport (JSON-RPC ``tools/call``) against small fixture files written under
``tmp_path`` (never tracked, no Oracle). Mirrors the S22-1 ``parse_file`` /
S22-2 ``run_etl_pipeline`` MCP test harnesses (``_fresh_app`` reload,
SSE-or-JSON body parser, ``_assert_is_error`` channel acceptance).

The ``export_failed_rows`` tool is a thin wrapper over the same validate +
error-extract code path the ``valdo validate --export-errors`` CLI flag and the
``POST /api/v1/files/export-errors`` REST endpoint drive. It validates a file
against a mapping, writes the failed rows to ``output`` in the original format,
and returns the export-file PATH + the failed-row count + total/valid counts.

CRITICAL PII posture: the response carries ONLY the path + counts — NEVER the
raw failed-row values. A dedicated scenario asserts that a sentinel token in a
failing row appears in the on-disk export file but NOT in the MCP response.

Scenarios:

1. ``test_export_writes_failed_rows_and_returns_counts`` — a fixed-width fixture
   with one invalid row writes that row to the export file and returns
   ``failed_row_count == 1`` plus total/valid counts.
2. ``test_export_response_has_no_raw_row_values`` — the sentinel token in the
   failing row is in the export FILE but NOT in the MCP response.
3. ``test_export_missing_file_raises`` — a non-existent input file is a
   caller-fixable problem -> MCP tool error.
4. ``test_export_advertised_in_tools_list`` — the tool is listed by
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
# Fixture builder — a fixed-width file with one known-invalid row (no Oracle).
# ---------------------------------------------------------------------------

# Headerless pipe-delimited mapping: id + amount. A rules config requires amount
# to be numeric. The second data row carries a non-numeric sentinel token
# "SECRET99" so we can assert it never leaks into the MCP response (it must
# appear only in the on-disk export file).
_MAPPING = {
    "mapping_name": "export_tool_test",
    "version": "1.0.0",
    "source": {"type": "file", "format": "pipe_delimited", "has_header": False},
    "fields": [
        {"name": "id", "data_type": "string"},
        {"name": "amount", "data_type": "string"},
    ],
}

_RULES = {
    "rules": [
        {
            "id": "amount_numeric",
            "name": "Amount must be numeric",
            "type": "field_validation",
            "field": "amount",
            "operator": "numeric",
            "severity": "error",
        }
    ]
}

_PIPE_DATA = "0001|100\n0002|SECRET99\n0003|300\n"


def _write_fixture(tmp_path: Path) -> tuple[str, str, str]:
    data_file = tmp_path / "txns.psv"
    data_file.write_text(_PIPE_DATA, encoding="utf-8")
    mapping_file = tmp_path / "txn_mapping.json"
    mapping_file.write_text(json.dumps(_MAPPING), encoding="utf-8")
    rules_file = tmp_path / "txn_rules.json"
    rules_file.write_text(json.dumps(_RULES), encoding="utf-8")
    return str(data_file), str(mapping_file), str(rules_file)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_export_writes_failed_rows_and_returns_counts(monkeypatch, tmp_path):
    """A fixed-width fixture with one invalid row writes it + returns counts."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    data_file, mapping_file, rules_file = _write_fixture(tmp_path)
    output = tmp_path / "errors.psv"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "export_failed_rows", request_id=2,
            arguments={
                "file": data_file,
                "mapping": mapping_file,
                "output": str(output),
                "rules": rules_file,
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"export_failed_rows reported isError: {result!r}"
    payload = _structured_or_text(result)

    assert payload["output_path"] == str(output), payload
    assert Path(payload["output_path"]).exists(), payload
    assert payload["failed_row_count"] == 1, payload
    assert payload["total_rows"] == 3, payload
    assert payload["valid_rows"] == 2, payload
    assert payload["valid"] is False, payload


def test_export_response_has_no_raw_row_values(monkeypatch, tmp_path):
    """CRITICAL: the failing row's sentinel is in the FILE but NOT the response."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    data_file, mapping_file, rules_file = _write_fixture(tmp_path)
    output = tmp_path / "errors.psv"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "export_failed_rows", request_id=2,
            arguments={
                "file": data_file,
                "mapping": mapping_file,
                "output": str(output),
                "rules": rules_file,
            },
        )

    result = body.get("result") or {}
    payload = _structured_or_text(result)

    # The raw failed-row content must NOT appear anywhere in the MCP response —
    # serialise the entire CallToolResult and assert the sentinel is absent.
    full_blob = json.dumps(body)
    assert "SECRET99" not in full_blob, "raw failed-row value leaked into MCP response"
    assert "rows" not in payload, payload
    assert "errors" not in payload, payload

    # But the failed rows ARE written to the on-disk export file.
    assert "SECRET99" in output.read_text(encoding="utf-8")


def test_export_missing_file_raises(monkeypatch, tmp_path):
    """A non-existent input file surfaces an MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    _, mapping_file, _ = _write_fixture(tmp_path)
    bogus = tmp_path / "does_not_exist.psv"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "export_failed_rows", request_id=2,
            arguments={
                "file": str(bogus),
                "mapping": mapping_file,
                "output": str(tmp_path / "errors.psv"),
            },
        )

    _assert_is_error(body, "not found")


def test_export_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises export_failed_rows with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "export_failed_rows" in by_name, sorted(by_name)
    assert by_name["export_failed_rows"].get("description")

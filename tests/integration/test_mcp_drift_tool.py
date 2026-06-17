"""MCP ``detect_drift`` tool integration tests (S21-4, #436).

Exercises the schema-drift tool end-to-end over the MCP Streamable-HTTP
transport (JSON-RPC ``tools/call``) against small fixture files plus a mapping
JSON written under ``tmp_path`` (never tracked). Mirrors the S21-3 ``mask_file``
MCP test harness (``_fresh_app`` reload, SSE-or-JSON body parser,
``_assert_is_error`` channel acceptance).

The ``detect_drift`` tool is a thin wrapper over
:func:`src.services.drift_detector.detect_drift`; it loads the mapping JSON and
returns the drift-report dict (``{drifted, fields, ...}``) that the
``valdo detect-drift`` CLI and the ``POST /api/v1/files/detect-drift`` endpoint
also produce. A genuine drift finding is a RESULT (``drifted == True``), NOT a
tool error.

Scenarios:

1. ``test_detect_drift_reports_drift`` — a delimited fixture whose header row
   omits a mapped column produces ``drifted == True`` with a ``column_missing``
   error finding for that field.
2. ``test_detect_drift_clean_case`` — a delimited fixture whose header matches
   the mapping exactly produces ``drifted == False`` with no findings.
3. ``test_detect_drift_missing_mapping_raises`` — a non-existent mapping path
   is a caller-fixable problem -> MCP tool error.
4. ``test_detect_drift_advertised_in_tools_list`` — the tool is listed by
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
        "src.mcp.drift_tools",
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
# Fixture builders
# ---------------------------------------------------------------------------

# A pipe-delimited mapping declaring three columns; the drift detector matches
# header tokens against these names.
_MAPPED_FIELDS = ["ACCT_ID", "AMOUNT", "TXN_DATE"]


def _write_mapping(path: Path) -> None:
    doc = {
        "mapping_name": "drift_fixture",
        "version": "1.0.0",
        "format": "pipe",
        "source": {"type": "file", "format": "pipe", "file_path": "x.txt"},
        "fields": [{"name": name} for name in _MAPPED_FIELDS],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _write_drifted_file(path: Path) -> None:
    # Header is missing the mapped TXN_DATE column (renamed to POSTED_DT) -> drift.
    path.write_text(
        "ACCT_ID|AMOUNT|POSTED_DT\n"
        "0001|100.00|2026-01-01\n"
        "0002|250.50|2026-01-02\n",
        encoding="utf-8",
    )


def _write_clean_file(path: Path) -> None:
    # Header matches the mapping exactly -> no drift.
    path.write_text(
        "ACCT_ID|AMOUNT|TXN_DATE\n"
        "0001|100.00|2026-01-01\n"
        "0002|250.50|2026-01-02\n",
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_detect_drift_reports_drift(monkeypatch, tmp_path):
    """A header omitting a mapped column yields drifted=True with a finding."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    mapping = tmp_path / "mapping.json"
    fixture = tmp_path / "drifted.txt"
    _write_mapping(mapping)
    _write_drifted_file(fixture)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "detect_drift", request_id=2,
            arguments={"file": str(fixture), "mapping": str(mapping)},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"detect_drift reported isError: {result!r}"
    report = _structured_or_text(result)

    # Drift IS a result, not an error.
    assert report["drifted"] is True, report
    findings = {f["name"]: f for f in report["fields"]}
    assert "TXN_DATE" in findings, report
    assert findings["TXN_DATE"]["severity"] == "error", findings["TXN_DATE"]
    assert findings["TXN_DATE"]["reason"] == "column_missing", findings["TXN_DATE"]


def test_detect_drift_clean_case(monkeypatch, tmp_path):
    """A header matching the mapping exactly yields drifted=False, no findings."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    mapping = tmp_path / "mapping.json"
    fixture = tmp_path / "clean.txt"
    _write_mapping(mapping)
    _write_clean_file(fixture)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "detect_drift", request_id=2,
            arguments={"file": str(fixture), "mapping": str(mapping)},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"detect_drift reported isError: {result!r}"
    report = _structured_or_text(result)

    assert report["drifted"] is False, report
    assert report["fields"] == [], report


def test_detect_drift_missing_mapping_raises(monkeypatch, tmp_path):
    """A non-existent mapping path surfaces an MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    fixture = tmp_path / "clean.txt"
    _write_clean_file(fixture)
    bogus_mapping = tmp_path / "does_not_exist.json"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "detect_drift", request_id=2,
            arguments={"file": str(fixture), "mapping": str(bogus_mapping)},
        )

    _assert_is_error(body, "not found")


def test_detect_drift_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises detect_drift with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "detect_drift" in by_name, sorted(by_name)
    assert by_name["detect_drift"].get("description")

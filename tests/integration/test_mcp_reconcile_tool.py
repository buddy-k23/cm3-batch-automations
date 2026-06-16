"""MCP ``reconcile_mapping`` tool integration tests (#407).

Exercises the tool end-to-end over the MCP Streamable-HTTP transport
(JSON-RPC ``tools/call``) against a REAL SQLite fixture DB created under
``tmp_path`` (never tracked). Mirrors the EF-S4 / S7-4 MCP test harness
(``_fresh_app`` reload, SSE-or-JSON body parser, ``_assert_is_error`` channel
acceptance).

Scenarios:

1. ``test_reconcile_mapping_clean`` — a mapping whose columns all match the
   fixture table returns ``status="clean"`` and ``valid=True``.
2. ``test_reconcile_mapping_flags_mismatch_and_advisory`` — a seeded type
   mismatch surfaces in ``mismatches`` and a boolean/INTEGER advisory in
   ``advisories``.
3. ``test_reconcile_mapping_unknown_mapping_raises`` — a non-existent mapping
   path is surfaced as a tool error.
4. ``test_reconcile_mapping_advertised_in_tools_list`` — the tool is listed
   by ``tools/list`` with the expected name + non-empty description.
"""

from __future__ import annotations

import importlib
import json
import sqlite3
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
        message = " ".join(
            (b.get("text") or "") for b in (result.get("content") or [])
        ).lower()
    assert needle.lower() in message, f"error did not mention {needle!r}: {message!r}"


# ---------------------------------------------------------------------------
# Fixture builders (shared shape with test_reconcile_service_api.py)
# ---------------------------------------------------------------------------


def _create_fixture_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE CUSTOMER ("
            "CUSTOMER_ID TEXT NOT NULL, "
            "FIRST_NAME TEXT, "
            "AGE VARCHAR(10), "
            "IS_ACTIVE INTEGER"
            ")"
        )
        conn.commit()
    finally:
        conn.close()


def _write_mapping(mapping_path: Path, *, with_mismatch: bool, with_advisory: bool) -> None:
    mappings = [
        {"source_column": "customer_id", "target_column": "CUSTOMER_ID",
         "data_type": "string", "required": True, "transformations": [], "validation_rules": []},
        {"source_column": "first_name", "target_column": "FIRST_NAME",
         "data_type": "string", "required": False, "transformations": [], "validation_rules": []},
    ]
    if with_mismatch:
        mappings.append(
            {"source_column": "age", "target_column": "AGE", "data_type": "integer",
             "required": False, "transformations": [], "validation_rules": []})
    if with_advisory:
        mappings.append(
            {"source_column": "is_active", "target_column": "IS_ACTIVE", "data_type": "boolean",
             "required": False, "transformations": [], "validation_rules": []})

    doc = {
        "mapping_name": "reconcile_fixture",
        "version": "1.0.0",
        "description": "Fixture mapping for #407 MCP reconcile tests",
        "source": {"type": "file", "format": "pipe_delimited"},
        "target": {"type": "database", "table_name": "CUSTOMER"},
        "mappings": mappings,
        "key_columns": ["customer_id"],
        "metadata": {},
    }
    mapping_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reconcile_mapping_clean(monkeypatch, tmp_path):
    """A matching mapping returns status=clean via the MCP transport."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))
    mapping_path = tmp_path / "clean.json"
    _write_mapping(mapping_path, with_mismatch=False, with_advisory=False)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "reconcile_mapping", request_id=2,
            arguments={"mapping": str(mapping_path), "table": "CUSTOMER"},
        )

    payload = _structured_or_text(body.get("result") or {})
    assert payload["status"] == "clean", payload
    assert payload["valid"] is True
    assert payload["table"] == "CUSTOMER"


def test_reconcile_mapping_flags_mismatch_and_advisory(monkeypatch, tmp_path):
    """A seeded mismatch + boolean advisory surface in the verdict."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))
    mapping_path = tmp_path / "both.json"
    _write_mapping(mapping_path, with_mismatch=True, with_advisory=True)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "reconcile_mapping", request_id=2,
            arguments={"mapping": str(mapping_path)},
        )

    payload = _structured_or_text(body.get("result") or {})
    assert payload["status"] == "mismatch", payload
    assert payload["summary"]["mismatch_count"] == 1
    assert payload["summary"]["advisory_count"] >= 1
    assert any("AGE" in m for m in payload["mismatches"])
    assert any("IS_ACTIVE" in a for a in payload["advisories"])


def test_reconcile_mapping_unknown_mapping_raises(monkeypatch, tmp_path):
    """A non-existent mapping path is surfaced as a tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "reconcile_mapping", request_id=2,
            arguments={"mapping": str(tmp_path / "nope.json")},
        )

    _assert_is_error(body, "not found")


def test_reconcile_mapping_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises reconcile_mapping with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "reconcile_mapping" in by_name, sorted(by_name)
    assert by_name["reconcile_mapping"].get("description")

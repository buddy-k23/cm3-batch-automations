"""MCP ``extract_table`` tool integration tests (S21-5, #437).

Exercises the DB-extract-to-file tool end-to-end over the MCP Streamable-HTTP
transport (JSON-RPC ``tools/call``) against a **real** SQLite fixture DB created
under ``tmp_path`` (never a tracked fixture). Mirrors the S21-1 ``db_compare``
MCP harness (``DB_ADAPTER=sqlite`` + ``DB_PATH`` env, ``_fresh_app`` reload,
SSE-or-JSON body parser, ``_assert_is_error`` channel acceptance).

The ``extract_table`` tool is a thin wrapper over
:class:`src.database.extractor.DataExtractor` — the same code path the
``valdo extract`` CLI drives. It returns the output-file path + the extracted
row count; it never returns extracted row data or any credentials.

Scenarios:

1. ``test_extract_table_writes_file`` — a whole-table extract writes the
   expected pipe-delimited rows and reports the right row count + output path.
2. ``test_extract_table_with_limit`` — a row limit caps the output and the
   limit is honoured (S13.5-4 bound-limit path).
3. ``test_extract_table_query_mode`` — a SQL query extract writes the projected
   rows.
4. ``test_extract_table_malicious_identifier_rejected`` — a malicious table
   name (``"CUSTOMERS; DROP TABLE CUSTOMERS"``) is REJECTED as a tool error
   (the S13.5-4 hardening holds on the MCP path) and the source table survives.
5. ``test_extract_table_requires_one_source`` — neither table nor query is a
   caller-fixable tool error.
6. ``test_extract_table_advertised_in_tools_list`` — the tool is listed by
   ``tools/list`` with the expected name + non-empty description.
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
        "src.mcp.extract_tools",
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


def _create_fixture_db(db_path: Path) -> None:
    """Create and seed a SQLite CUSTOMERS table with four deterministic rows."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE CUSTOMERS (ID INTEGER, NAME TEXT, BALANCE REAL)"
        )
        conn.executemany(
            "INSERT INTO CUSTOMERS VALUES (?, ?, ?)",
            [(1, "alice", 10.5), (2, "bob", 20.0), (3, "carol", 30.25), (4, "dave", 40.0)],
        )
        conn.commit()
    finally:
        conn.close()


def _table_exists(db_path: Path, table: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_extract_table_writes_file(monkeypatch, tmp_path):
    """A whole-table extract writes the rows and reports path + row count."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))
    out = tmp_path / "out.txt"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "extract_table", request_id=2,
            arguments={"table": "CUSTOMERS", "output": str(out)},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"extract_table reported isError: {result!r}"
    payload = _structured_or_text(result)

    assert payload["row_count"] == 4, payload
    assert payload["output_file"] == str(out), payload
    assert payload["mode"] == "table", payload
    assert payload["db_adapter"] == "sqlite", payload

    # No credentials echoed in the response.
    assert "DB_PATH" not in json.dumps(payload)

    lines = out.read_text(encoding="utf-8").splitlines()
    # header + 4 data rows
    assert lines[0].split("|") == ["ID", "NAME", "BALANCE"]
    assert len(lines) == 5
    assert "alice" in lines[1]


def test_extract_table_with_limit(monkeypatch, tmp_path):
    """A row limit caps the output (S13.5-4 bound-limit path)."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))
    out = tmp_path / "out.txt"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "extract_table", request_id=2,
            arguments={"table": "CUSTOMERS", "output": str(out), "limit": 2},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"extract_table reported isError: {result!r}"
    payload = _structured_or_text(result)
    assert payload["row_count"] == 2, payload
    # No header in the limit path (header=False), just 2 data rows.
    assert len(out.read_text(encoding="utf-8").splitlines()) == 2


def test_extract_table_query_mode(monkeypatch, tmp_path):
    """A SQL query extract writes the projected rows."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))
    out = tmp_path / "q_out.txt"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "extract_table", request_id=2,
            arguments={
                "query": "SELECT NAME, BALANCE FROM CUSTOMERS WHERE BALANCE >= 30",
                "output": str(out),
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"extract_table reported isError: {result!r}"
    payload = _structured_or_text(result)
    assert payload["row_count"] == 2, payload
    assert payload["mode"] == "query", payload
    names = {ln.split("|")[0] for ln in out.read_text(encoding="utf-8").splitlines()[1:]}
    assert names == {"carol", "dave"}, names


def test_extract_table_malicious_identifier_rejected(monkeypatch, tmp_path):
    """A malicious table name is REJECTED (S13.5-4 hardening holds on MCP path)."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))
    out = tmp_path / "out.txt"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "extract_table", request_id=2,
            arguments={
                "table": "CUSTOMERS; DROP TABLE CUSTOMERS",
                "output": str(out),
            },
        )

    _assert_is_error(body, "invalid sql identifier")
    # The injection never executed — the source table is intact.
    assert _table_exists(db_path, "CUSTOMERS"), "CUSTOMERS table must survive"


def test_extract_table_requires_one_source(monkeypatch, tmp_path):
    """Neither table nor query supplied -> caller-fixable tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    out = tmp_path / "out.txt"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "extract_table", request_id=2,
            arguments={"output": str(out)},
        )

    _assert_is_error(body, "exactly one of")


def test_extract_table_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises extract_table with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "extract_table" in by_name, sorted(by_name)
    assert by_name["extract_table"].get("description")

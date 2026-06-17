"""MCP ``db_compare`` tool integration tests (S21-1, #433).

Exercises the tool end-to-end over the MCP Streamable-HTTP transport
(JSON-RPC ``tools/call``) against a REAL SQLite fixture DB created under
``tmp_path`` (never tracked) plus an on-disk actual file. Mirrors the #407
``reconcile_mapping`` MCP test harness (``_fresh_app`` reload, SSE-or-JSON
body parser, ``_assert_is_error`` channel acceptance).

The ``db_compare`` tool is a thin wrapper over
:func:`src.services.db_file_compare_service.compare_db_to_file`; it extracts
rows from whichever backend ``DB_ADAPTER`` selects (here: SQLite), writes them
to a temp file, and diffs that against the supplied actual file. A genuine
comparison difference is a RESULT (``workflow.status == "failed"``), not a
tool error — only caller-fixable problems (mapping not found, actual file
missing, bad adapter) raise.

Scenarios:

1. ``test_db_compare_matches`` — DB extract and the actual file agree, so the
   verdict's ``workflow.status`` is ``"passed"`` with zero diffs.
2. ``test_db_compare_flags_difference`` — a seeded mismatch between the DB row
   and the actual file surfaces as ``workflow.status == "failed"`` (a result,
   not an error).
3. ``test_db_compare_unknown_mapping_raises`` — a non-existent mapping path is
   surfaced as a tool error.
4. ``test_db_compare_advertised_in_tools_list`` — the tool is listed by
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
        message = " ".join(
            (b.get("text") or "") for b in (result.get("content") or [])
        ).lower()
    assert needle.lower() in message, f"error did not mention {needle!r}: {message!r}"


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _create_fixture_db(db_path: Path) -> None:
    """Create a SQLite CUSTOMER table with two deterministic rows."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE CUSTOMER ("
            "ID TEXT NOT NULL, "
            "NAME TEXT, "
            "AMOUNT TEXT"
            ")"
        )
        conn.executemany(
            "INSERT INTO CUSTOMER (ID, NAME, AMOUNT) VALUES (?, ?, ?)",
            [("1", "Alice", "100"), ("2", "Bob", "200")],
        )
        conn.commit()
    finally:
        conn.close()


def _write_mapping(mapping_path: Path) -> None:
    """Write a db-compare mapping JSON (``fields`` list of named columns)."""
    doc = {
        "name": "db_compare_fixture",
        "fields": [{"name": "ID"}, {"name": "NAME"}, {"name": "AMOUNT"}],
    }
    mapping_path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _write_actual_file(actual_path: Path, rows: list[dict]) -> None:
    """Write a pipe-delimited actual file with a header row."""
    headers = ["ID", "NAME", "AMOUNT"]
    lines = ["|".join(headers)]
    for row in rows:
        lines.append("|".join(str(row[h]) for h in headers))
    actual_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_db_compare_matches(monkeypatch, tmp_path):
    """DB extract and the actual file agree -> workflow.status == passed."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))

    mapping_path = tmp_path / "mapping.json"
    _write_mapping(mapping_path)
    actual_path = tmp_path / "actual.txt"
    _write_actual_file(
        actual_path,
        [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"},
         {"ID": "2", "NAME": "Bob", "AMOUNT": "200"}],
    )

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "db_compare", request_id=2,
            arguments={
                "mapping": str(mapping_path),
                "table": "CUSTOMER",
                "actual_file": str(actual_path),
                "key_columns": ["ID"],
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"db_compare reported isError: {result!r}"
    payload = _structured_or_text(result)
    assert payload["workflow"]["status"] == "passed", payload
    assert payload["workflow"]["db_rows_extracted"] == 2, payload
    assert payload["compare"]["matching_rows"] == 2, payload


def test_db_compare_flags_difference(monkeypatch, tmp_path):
    """A seeded difference is a RESULT (status=failed), not a tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))

    mapping_path = tmp_path / "mapping.json"
    _write_mapping(mapping_path)
    actual_path = tmp_path / "actual.txt"
    # Seed a difference: Bob's AMOUNT differs from the DB (200 -> 999).
    _write_actual_file(
        actual_path,
        [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"},
         {"ID": "2", "NAME": "Bob", "AMOUNT": "999"}],
    )

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "db_compare", request_id=2,
            arguments={
                "mapping": str(mapping_path),
                "table": "CUSTOMER",
                "actual_file": str(actual_path),
                "key_columns": ["ID"],
            },
        )

    result = body.get("result") or {}
    # A genuine difference must NOT be surfaced as a tool error.
    assert not result.get("isError"), f"db_compare wrongly raised on a diff: {result!r}"
    payload = _structured_or_text(result)
    assert payload["workflow"]["status"] == "failed", payload


def test_db_compare_unknown_mapping_raises(monkeypatch, tmp_path):
    """A non-existent mapping path is surfaced as a tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))
    actual_path = tmp_path / "actual.txt"
    _write_actual_file(actual_path, [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"}])

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "db_compare", request_id=2,
            arguments={
                "mapping": str(tmp_path / "nope.json"),
                "table": "CUSTOMER",
                "actual_file": str(actual_path),
            },
        )

    _assert_is_error(body, "not found")


def test_db_compare_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises db_compare with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "db_compare" in by_name, sorted(by_name)
    assert by_name["db_compare"].get("description")

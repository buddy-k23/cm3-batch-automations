"""MCP ``excel_db_compare`` tool integration tests (S24-4, Sprint 24).

Exercises the tool end-to-end over the MCP Streamable-HTTP transport
(JSON-RPC ``tools/call``) against a REAL SQLite fixture DB created under
``tmp_path`` (never tracked) plus a generated ``.xlsx`` workbook. Mirrors the
S21-1 ``db_compare`` MCP test harness (``_fresh_app`` reload, SSE-or-JSON body
parser, ``_assert_is_error`` channel acceptance) — the only differences are the
Excel side of the input and the both-direction coverage that S24-2's service
contract advertises (``db-source`` / ``excel-source``).

The ``excel_db_compare`` tool is a thin wrapper over
:func:`src.services.excel_db_compare_service.compare_excel_to_db`; it reads a
sheet of Excel *data*, extracts the DB side from whichever backend
``DB_ADAPTER`` selects (here: SQLite), normalises both sides, and diffs them.
A genuine comparison difference is a RESULT (``workflow.status == "failed"``),
not a tool error — only caller-fixable problems (Excel file not found, bad
direction, bad adapter) raise.

Scenarios:

1. ``test_excel_db_compare_matches`` — DB extract and the Excel sheet agree, so
   the verdict's ``workflow.status`` is ``"passed"`` with zero diffs.
2. ``test_excel_db_compare_flags_difference`` — a seeded mismatch between the DB
   row and the Excel cell surfaces as ``workflow.status == "failed"`` (a
   result, not an error).
3. ``test_excel_db_compare_excel_source_direction`` — the ``excel-source``
   direction (Excel is source/expected) is honoured end-to-end.
4. ``test_excel_db_compare_no_password_or_rows_in_response`` — the serialized
   response contains NEITHER the connection password NOR any raw row value
   (ADR 0023 redaction posture parity with ``db_compare``).
5. ``test_excel_db_compare_missing_excel_raises`` — a non-existent Excel path is
   surfaced as a tool error.
6. ``test_excel_db_compare_advertised_in_tools_list`` — the tool is listed by
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

# A unique sentinel password that must NEVER appear in any serialized response.
_SECRET_PASSWORD = "s24-4-super-secret-password-do-not-leak"


def _fresh_app():
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.excel_db_compare_tools",
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


def _write_xlsx(xlsx_path: Path, rows: list[dict]) -> None:
    """Write a single-sheet .xlsx with an ID/NAME/AMOUNT header + rows."""
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Customers"
    headers = ["ID", "NAME", "AMOUNT"]
    ws.append(headers)
    for row in rows:
        ws.append([row[h] for h in headers])
    wb.save(str(xlsx_path))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_excel_db_compare_matches(monkeypatch, tmp_path):
    """Excel sheet and the DB extract agree -> workflow.status == passed."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))

    xlsx_path = tmp_path / "data.xlsx"
    _write_xlsx(
        xlsx_path,
        [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"},
         {"ID": "2", "NAME": "Bob", "AMOUNT": "200"}],
    )

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "excel_db_compare", request_id=2,
            arguments={
                "excel_file": str(xlsx_path),
                "table": "CUSTOMER",
                "key_columns": ["ID"],
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"excel_db_compare reported isError: {result!r}"
    payload = _structured_or_text(result)
    assert payload["workflow"]["status"] == "passed", payload
    assert payload["workflow"]["db_rows_extracted"] == 2, payload
    assert payload["workflow"]["excel_rows_read"] == 2, payload
    assert payload["workflow"]["direction"] == "db-source", payload
    assert payload["compare"]["matching_rows"] == 2, payload
    assert payload["compare"]["only_in_db"] == 0, payload
    assert payload["compare"]["only_in_excel"] == 0, payload
    assert payload["compare"]["differences"] == 0, payload


def test_excel_db_compare_flags_difference(monkeypatch, tmp_path):
    """A seeded difference is a RESULT (status=failed), not a tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))

    xlsx_path = tmp_path / "data.xlsx"
    # Seed a difference: Bob's AMOUNT differs from the DB (200 -> 999).
    _write_xlsx(
        xlsx_path,
        [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"},
         {"ID": "2", "NAME": "Bob", "AMOUNT": "999"}],
    )

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "excel_db_compare", request_id=2,
            arguments={
                "excel_file": str(xlsx_path),
                "table": "CUSTOMER",
                "key_columns": ["ID"],
            },
        )

    result = body.get("result") or {}
    # A genuine difference must NOT be surfaced as a tool error.
    assert not result.get("isError"), f"excel_db_compare wrongly raised on a diff: {result!r}"
    payload = _structured_or_text(result)
    assert payload["workflow"]["status"] == "failed", payload
    assert payload["compare"]["differences"] >= 1, payload


def test_excel_db_compare_excel_source_direction(monkeypatch, tmp_path):
    """The ``excel-source`` direction (Excel is source/expected) is honoured."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))

    xlsx_path = tmp_path / "data.xlsx"
    # Excel has an extra row (3/Carol) not present in the DB. With Excel as the
    # source (file1), Carol is "only_in_excel".
    _write_xlsx(
        xlsx_path,
        [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"},
         {"ID": "2", "NAME": "Bob", "AMOUNT": "200"},
         {"ID": "3", "NAME": "Carol", "AMOUNT": "300"}],
    )

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "excel_db_compare", request_id=2,
            arguments={
                "excel_file": str(xlsx_path),
                "table": "CUSTOMER",
                "key_columns": ["ID"],
                "direction": "excel-source",
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"excel_db_compare reported isError: {result!r}"
    payload = _structured_or_text(result)
    assert payload["workflow"]["direction"] == "excel-source", payload
    assert payload["workflow"]["status"] == "failed", payload
    # Excel is file1 (source), DB is file2 (actual). Carol exists only in Excel.
    assert payload["compare"]["only_in_excel"] == 1, payload
    assert payload["compare"]["only_in_db"] == 0, payload


def test_excel_db_compare_no_password_or_rows_in_response(monkeypatch, tmp_path):
    """The serialized response leaks NEITHER the password NOR raw row values.

    ADR 0023 redaction posture parity with ``db_compare``: the connection
    password is passed straight to the adapter and never echoed; the response
    carries only counts + a summary, never raw cell values like ``Alice`` or
    ``Bob``.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)

    xlsx_path = tmp_path / "data.xlsx"
    _write_xlsx(
        xlsx_path,
        [{"ID": "1", "NAME": "Alice", "AMOUNT": "100"},
         {"ID": "2", "NAME": "Bob", "AMOUNT": "200"}],
    )

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "excel_db_compare", request_id=2,
            arguments={
                "excel_file": str(xlsx_path),
                "table": "CUSTOMER",
                "key_columns": ["ID"],
                # SQLite is file-based: supply the adapter + db_path explicitly
                # (the realistic per-request connection flow) plus a sentinel
                # password that must never surface in the response.
                "db_adapter": "sqlite",
                "connection": {
                    "db_path": str(db_path),
                    "db_password": _SECRET_PASSWORD,
                },
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"excel_db_compare reported isError: {result!r}"

    # Serialize the ENTIRE response body and assert the secret never appears.
    serialized = json.dumps(body)
    assert _SECRET_PASSWORD not in serialized, (
        "connection password leaked into the excel_db_compare response"
    )
    # And no raw row values (the seeded NAME cells) appear anywhere either.
    assert "Alice" not in serialized, "raw row value 'Alice' leaked into response"
    assert "Bob" not in serialized, "raw row value 'Bob' leaked into response"

    payload = _structured_or_text(result)
    assert payload["workflow"]["status"] == "passed", payload


def test_excel_db_compare_missing_excel_raises(monkeypatch, tmp_path):
    """A non-existent Excel path is surfaced as a tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "excel_db_compare", request_id=2,
            arguments={
                "excel_file": str(tmp_path / "nope.xlsx"),
                "table": "CUSTOMER",
            },
        )

    _assert_is_error(body, "not found")


def test_excel_db_compare_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises excel_db_compare with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "excel_db_compare" in by_name, sorted(by_name)
    assert by_name["excel_db_compare"].get("description")

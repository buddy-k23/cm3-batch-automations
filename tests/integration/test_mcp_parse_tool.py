"""MCP ``parse_file`` tool integration tests (S22-1, #438).

Exercises the parse/inspect tool end-to-end over the MCP Streamable-HTTP
transport (JSON-RPC ``tools/call``) against small fixture files written under
``tmp_path`` (never tracked). Mirrors the S21-4 ``detect_drift`` MCP test
harness (``_fresh_app`` reload, SSE-or-JSON body parser, ``_assert_is_error``
channel acceptance).

The ``parse_file`` tool is a thin wrapper over the same parser layer the
``valdo parse`` CLI command drives (:class:`~src.parsers.format_detector.FormatDetector`
+ :class:`~src.parsers.pipe_delimited_parser.PipeDelimitedParser` /
:class:`~src.parsers.fixed_width_parser.FixedWidthParser`). It returns a
BOUNDED preview (column names + first N rows + total row count) — never an
unbounded dump of the file.

Scenarios:

1. ``test_parse_file_returns_bounded_preview`` — a small pipe-delimited fixture
   produces ``columns`` + ``rows`` (capped) + ``row_count`` matching the data.
2. ``test_parse_file_preview_is_bounded`` — a file with more rows than the
   preview cap returns at most ``limit`` rows while ``row_count`` reflects the
   true total.
3. ``test_parse_file_missing_file_raises`` — a non-existent file is a
   caller-fixable problem -> MCP tool error.
4. ``test_parse_file_advertised_in_tools_list`` — the tool is listed by
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
# Fixture builders
# ---------------------------------------------------------------------------


def _write_pipe_file(path: Path, n_rows: int) -> None:
    # Headerless data — the parser defaults to has_header=False (matching the
    # `valdo parse` CLI), so every line is a data row and the count is exact.
    lines = []
    for i in range(1, n_rows + 1):
        lines.append(f"{i:04d}|{i * 10}.00|2026-01-{i % 28 + 1:02d}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_parse_file_returns_bounded_preview(monkeypatch, tmp_path):
    """A small pipe-delimited file yields columns + rows + row_count."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    fixture = tmp_path / "txns.psv"
    _write_pipe_file(fixture, n_rows=3)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "parse_file", request_id=2,
            arguments={"file": str(fixture), "format": "pipe"},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"parse_file reported isError: {result!r}"
    preview = _structured_or_text(result)

    assert preview["row_count"] == 3, preview
    assert isinstance(preview["columns"], list) and preview["columns"], preview
    rows = preview["rows"]
    assert isinstance(rows, list) and len(rows) == 3, preview
    # Each preview row is a dict keyed by the advertised columns.
    assert set(rows[0].keys()) == set(preview["columns"]), preview
    assert preview["truncated"] is False, preview


def test_parse_file_preview_is_bounded(monkeypatch, tmp_path):
    """A file larger than the cap returns at most `limit` rows; count is true."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    fixture = tmp_path / "big.psv"
    _write_pipe_file(fixture, n_rows=50)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "parse_file", request_id=2,
            arguments={"file": str(fixture), "format": "pipe", "limit": 5},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"parse_file reported isError: {result!r}"
    preview = _structured_or_text(result)

    assert preview["row_count"] == 50, preview
    assert len(preview["rows"]) == 5, preview
    assert preview["truncated"] is True, preview


def test_parse_file_missing_file_raises(monkeypatch, tmp_path):
    """A non-existent file surfaces an MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    bogus = tmp_path / "does_not_exist.psv"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "parse_file", request_id=2,
            arguments={"file": str(bogus), "format": "pipe"},
        )

    _assert_is_error(body, "not found")


def test_parse_file_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises parse_file with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "parse_file" in by_name, sorted(by_name)
    assert by_name["parse_file"].get("description")

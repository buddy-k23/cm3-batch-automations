"""MCP ad-hoc compare-tool integration tests (S7-4, #382).

Five acceptance scenarios mapped to the S7-4 story:

1. ``test_compare_two_csvs_happy_path`` — two CSVs with 5 rows where the
   non-key column differs on exactly one row returns ``differing=1``,
   ``matched=4``, and a non-empty ``top_differences`` slice.
2. ``test_compare_two_tsvs_auto_detected`` — auto-detection works for the
   ``.tsv`` extension; the comparator sees the tab-separated rows
   correctly and returns the same shape.
3. ``test_compare_two_files_missing_file_raises`` — a non-existent file
   path is surfaced as an MCP tool error mentioning ``"not found"``.
4. ``test_compare_two_files_missing_key_column_raises`` — a key column
   that is absent from either header row is surfaced as a tool error
   mentioning ``"key_columns"``.
5. ``test_compare_two_files_advertised_in_tools_list`` — the
   ``compare_two_files`` tool is listed by ``tools/list`` with the
   expected name + non-empty description string.

Tests exercise the MCP transport end-to-end (JSON-RPC ``tools/call``)
rather than calling the adapter directly so the FastMCP registration and
the response projection are both covered. The tests share helpers with
the EF-S4 action-tool integration suite — same ``_fresh_app`` reload
pattern, same SSE-or-JSON body parser, same ``_assert_is_error`` channel
acceptance.

Fixture posture: every test writes its own temp CSV / TSV pair under
:func:`tempfile.mkdtemp` so the suite is hermetic — no committed
fixtures are required and the cleanup is handled by pytest's tmp_path.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient


# JSON-RPC payload reused across tests. Matches the MCP 2025-06-18
# protocol version specified in the EF-S1 ACs.
_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1.0"},
    },
}

# Streamable HTTP transport expects clients to advertise that they can
# receive either JSON or an SSE event stream.
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    Mirrors the helper in ``test_mcp_action_tools.py`` — the FastAPI app,
    MCP server, dev-auth middleware, and the compare-tool adapter all
    read env state at module import time, so we force re-import per test
    to pick up monkeypatched env vars.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.compare_tools",
        "src.mcp.action_tools",
        "src.mcp.tools",
        "src.mcp.taxonomy",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


@pytest.fixture(autouse=True)
def _ensure_session_signing_key(monkeypatch):
    """Provide a dummy session signing key for app construction.

    Mirrors the fixture in ``test_mcp_action_tools.py``; required because
    ``config/ui.yml`` may enable ``auth.enabled: true`` and the FastAPI
    app construction reads the secret at import time.
    """
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


def _parse_streamable_body(response) -> Dict[str, Any]:
    """Decode an MCP Streamable-HTTP response body to a JSON dict.

    Borrowed verbatim from the EF-S4 test helper — the FastMCP transport
    may answer either as plain JSON (we set ``json_response=True``) or as
    a single-event SSE frame depending on what the client asked for.
    """
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for raw_line in response.text.splitlines():
            line = raw_line.strip()
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(
            f"SSE response had no data line: {response.text!r}"
        )
    return response.json()


def _rpc(
    client: TestClient,
    method: str,
    *,
    request_id: int,
    params: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Send a single JSON-RPC POST to ``/mcp/`` and parse the body."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    response = client.post("/mcp/", json=payload, headers=_MCP_HEADERS)
    assert response.status_code == 200, (
        f"{method} returned {response.status_code}: {response.text}"
    )
    return _parse_streamable_body(response)


def _call_tool(
    client: TestClient,
    tool_name: str,
    *,
    request_id: int,
    arguments: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Invoke ``tools/call`` for *tool_name* and return the parsed body."""
    return _rpc(
        client,
        "tools/call",
        request_id=request_id,
        params={
            "name": tool_name,
            "arguments": arguments or {},
        },
    )


def _structured_or_text(result: Dict[str, Any]) -> Any:
    """Extract the JSON payload from a CallToolResult.

    FastMCP can return the tool's return value either as the canonical
    ``structuredContent`` field or as a single ``text`` content block
    that the client has to ``json.loads``. We accept either channel so
    the test is robust against FastMCP transport defaults flipping
    between releases.
    """
    structured = result.get("structuredContent")
    if structured is not None:
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured
    contents = result.get("content") or []
    assert contents, f"CallToolResult has no content: {result!r}"
    first = contents[0]
    text = first.get("text")
    assert isinstance(text, str) and text, (
        f"CallToolResult first content has no text: {first!r}"
    )
    return json.loads(text)


def _assert_is_error(body: Dict[str, Any], needle: str) -> None:
    """Assert the response carries an MCP tool error mentioning *needle*.

    FastMCP can surface a tool failure either via a JSON-RPC envelope
    error or via ``isError: true`` on the CallToolResult. We accept
    either channel and look for *needle* (case-insensitive) anywhere in
    the surfaced message.
    """
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
        message = " ".join(
            (block.get("text") or "") for block in content_blocks
        ).lower()
    assert needle.lower() in message, (
        f"Tool error message did not mention {needle!r}: {message!r}"
    )


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _write_csv_pair(tmp_path: Path, sep: str, ext: str) -> tuple[Path, Path]:
    """Write two delimited files with 5 rows each, one row differs.

    Both files share the same header (``id,name,balance``) and the same
    five primary keys (``1..5``). Row with id=3 has a different
    ``balance`` value between left and right, while ``name`` matches on
    every row. This gives the comparator exactly one differing row and
    four matching rows when ``key_columns=["id"]``.

    Args:
        tmp_path: pytest tmp_path fixture (or a derived path).
        sep: Field separator — ``,`` for CSV, ``\\t`` for TSV.
        ext: File extension to write (``.csv`` or ``.tsv``).

    Returns:
        Tuple of ``(left_path, right_path)``.
    """
    header = sep.join(["id", "name", "balance"]) + "\n"
    left_rows = [
        sep.join(["1", "Alice", "100.00"]),
        sep.join(["2", "Bob", "200.00"]),
        sep.join(["3", "Charlie", "300.00"]),
        sep.join(["4", "Dave", "400.00"]),
        sep.join(["5", "Eve", "500.00"]),
    ]
    right_rows = [
        sep.join(["1", "Alice", "100.00"]),
        sep.join(["2", "Bob", "200.00"]),
        # id=3 differs on `balance` only — the key column matches so
        # the comparator should classify this row as `differing`.
        sep.join(["3", "Charlie", "350.00"]),
        sep.join(["4", "Dave", "400.00"]),
        sep.join(["5", "Eve", "500.00"]),
    ]

    left = tmp_path / f"left{ext}"
    right = tmp_path / f"right{ext}"
    left.write_text(header + "\n".join(left_rows) + "\n", encoding="utf-8")
    right.write_text(header + "\n".join(right_rows) + "\n", encoding="utf-8")
    return left, right


# ---------------------------------------------------------------------------
# 1. Happy path — two CSVs with one differing row
# ---------------------------------------------------------------------------


def test_compare_two_csvs_happy_path(monkeypatch, tmp_path):
    """5-row CSVs differing on 1 non-key column produce ``differing=1``.

    Verifies the end-to-end contract:
    * Summary counts match the AC numbers (``matched=4``, ``differing=1``,
      no extras on either side).
    * ``comparison_id`` is a non-empty string (UUID hex).
    * ``top_differences`` contains exactly one entry whose key is the
      differing row and whose ``differences`` dict carries the
      ``balance`` field with the correct ``left`` / ``right`` values.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    left, right = _write_csv_pair(tmp_path, sep=",", ext=".csv")

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "compare_two_files",
            request_id=2,
            arguments={
                "left_path": str(left),
                "right_path": str(right),
                "key_columns": ["id"],
            },
        )

    assert "result" in body, f"tools/call missing 'result': {body!r}"
    result = body["result"]
    assert not result.get("isError"), (
        f"compare_two_files reported isError: {result!r}"
    )

    payload = _structured_or_text(result)
    assert isinstance(payload, dict), (
        f"compare_two_files payload not a dict: {payload!r}"
    )

    # comparison_id sanity — non-empty string. The exact value is a
    # fresh UUID per call so we cannot pin it.
    comparison_id = payload.get("comparison_id")
    assert isinstance(comparison_id, str) and comparison_id, (
        f"comparison_id missing or not a string: {payload!r}"
    )

    # Summary shape — the four expected counts with the AC-pinned
    # values for the fixture.
    summary = payload.get("summary")
    assert isinstance(summary, dict), f"summary missing or wrong type: {payload!r}"
    assert summary.get("matched") == 4, summary
    assert summary.get("differing") == 1, summary
    assert summary.get("only_in_left") == 0, summary
    assert summary.get("only_in_right") == 0, summary

    # top_differences — exactly one row, key id=3, balance field
    # carries the before/after values.
    top = payload.get("top_differences")
    assert isinstance(top, list) and len(top) == 1, (
        f"expected exactly one diff entry; got: {top!r}"
    )
    diff = top[0]
    assert diff.get("keys", {}).get("id") == "3", diff
    field_diffs = diff.get("differences", {})
    assert "balance" in field_diffs, field_diffs
    balance_diff = field_diffs["balance"]
    assert balance_diff.get("left") == "300.00", balance_diff
    assert balance_diff.get("right") == "350.00", balance_diff


# ---------------------------------------------------------------------------
# 2. TSV auto-detection
# ---------------------------------------------------------------------------


def test_compare_two_tsvs_auto_detected(monkeypatch, tmp_path):
    """``.tsv`` auto-detection produces the same shape as the CSV path.

    The fixture mirrors the CSV happy path (5 rows, 1 differs) but with
    tab separators. The tool should detect the ``.tsv`` extension, use
    ``sep="\\t"`` for ``pandas.read_csv``, and produce identical summary
    counts. This guards against the CSV-routing workaround silently
    breaking the tab-separated branch.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    left, right = _write_csv_pair(tmp_path, sep="\t", ext=".tsv")

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "compare_two_files",
            request_id=2,
            arguments={
                "left_path": str(left),
                "right_path": str(right),
                "key_columns": ["id"],
            },
        )

    assert "result" in body, body
    result = body["result"]
    assert not result.get("isError"), result
    payload = _structured_or_text(result)

    summary = payload.get("summary") or {}
    assert summary.get("matched") == 4, summary
    assert summary.get("differing") == 1, summary
    assert summary.get("only_in_left") == 0, summary
    assert summary.get("only_in_right") == 0, summary


# ---------------------------------------------------------------------------
# 3. Missing file path
# ---------------------------------------------------------------------------


def test_compare_two_files_missing_file_raises(monkeypatch, tmp_path):
    """A non-existent left_path is surfaced as a tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    # Right file does exist so we can prove the error comes from the
    # missing-left check, not from a degenerate right-side fallback.
    _, right = _write_csv_pair(tmp_path, sep=",", ext=".csv")
    bogus = tmp_path / "does_not_exist_compare_test.csv"
    assert not bogus.exists()

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "compare_two_files",
            request_id=2,
            arguments={
                "left_path": str(bogus),
                "right_path": str(right),
                "key_columns": ["id"],
            },
        )

    _assert_is_error(body, "not found")


# ---------------------------------------------------------------------------
# 4. Missing key column
# ---------------------------------------------------------------------------


def test_compare_two_files_missing_key_column_raises(monkeypatch, tmp_path):
    """A key column absent from either header is surfaced as a tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    left, right = _write_csv_pair(tmp_path, sep=",", ext=".csv")

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "compare_two_files",
            request_id=2,
            arguments={
                "left_path": str(left),
                "right_path": str(right),
                # ``not_a_real_column`` is not in either header — the tool
                # should reject this with a ToolError before invoking
                # the comparator.
                "key_columns": ["not_a_real_column"],
            },
        )

    _assert_is_error(body, "key_columns")


# ---------------------------------------------------------------------------
# 5. tools/list advertises compare_two_files
# ---------------------------------------------------------------------------


def test_compare_two_files_advertised_in_tools_list(monkeypatch):
    """``tools/list`` includes ``compare_two_files`` with a description.

    The scaffold baseline test (``test_mcp_capabilities_advertise_expected_registries``)
    asserts the exact ten-entry set; here we add a per-tool guard so
    a future story that inadvertently renames the tool fails *this*
    test rather than only the baseline.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    matching = [t for t in tools if t.get("name") == "compare_two_files"]
    assert len(matching) == 1, (
        f"compare_two_files missing from tools/list: "
        f"{[t.get('name') for t in tools]!r}"
    )
    tool = matching[0]
    assert isinstance(tool.get("description"), str) and tool["description"], (
        f"compare_two_files description missing: {tool!r}"
    )
    # The input schema must include the four parameters by name. We
    # don't pin the schema verbatim because FastMCP regenerates the
    # JSON-Schema body, but we do guard the parameter names so an
    # accidental rename surfaces here.
    schema_props = (tool.get("inputSchema") or {}).get("properties") or {}
    assert "left_path" in schema_props, schema_props
    assert "right_path" in schema_props, schema_props
    assert "key_columns" in schema_props, schema_props
    assert "mapping_path" in schema_props, schema_props

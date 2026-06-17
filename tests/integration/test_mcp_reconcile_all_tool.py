"""MCP ``reconcile_all`` tool integration tests (S21-2, #434).

Exercises the tool end-to-end over the MCP Streamable-HTTP transport
(JSON-RPC ``tools/call``) against a REAL SQLite fixture DB created under
``tmp_path`` (never tracked) plus a mappings directory holding both a valid
and an invalid mapping. Mirrors the S21-1 ``db_compare`` MCP test harness
(``_fresh_app`` reload, SSE-or-JSON body parser, ``_assert_is_error``
channel acceptance).

The ``reconcile_all`` tool is a thin wrapper over
:func:`src.services.reconcile_all_service.reconcile_all_service`; it iterates
the mappings directory, builds the adapter from whichever backend
``DB_ADAPTER`` selects (here: SQLite), reconciles each mapping against the
live DB, and aggregates a summary (plus a baseline drift block when a
baseline report is supplied). A single mapping that fails to reconcile is a
RESULT (an invalid entry in ``results``), NOT a tool error.

Scenarios:

1. ``test_reconcile_all_aggregate_verdict`` — a mappings dir with one valid
   mapping (target table exists) and one invalid (target table missing)
   yields ``total_mappings == 2``, ``valid_mappings == 1``,
   ``invalid_mappings == 1``.
2. ``test_reconcile_all_with_baseline_drift`` — supplying a prior baseline
   report adds a ``drift`` block to the aggregate.
3. ``test_reconcile_all_advertised_in_tools_list`` — the tool is listed by
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


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _create_fixture_db(db_path: Path) -> None:
    """Create a SQLite CUSTOMER table the valid mapping reconciles against."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE CUSTOMER ("
            "CUSTOMER_ID TEXT NOT NULL, "
            "FIRST_NAME TEXT"
            ")"
        )
        conn.commit()
    finally:
        conn.close()


def _write_valid_mapping(path: Path) -> None:
    """A mapping whose target table (CUSTOMER) exists in the fixture DB."""
    doc = {
        "mapping_name": "valid_customer",
        "version": "1.0.0",
        "description": "Valid mapping whose target table exists.",
        "source": {"type": "file", "format": "pipe_delimited", "file_path": "x.txt"},
        "target": {"type": "database", "table_name": "CUSTOMER"},
        "mappings": [
            {
                "source_column": "customer_id",
                "target_column": "CUSTOMER_ID",
                "data_type": "string",
                "required": True,
                "validation_rules": [],
            },
            {
                "source_column": "first_name",
                "target_column": "FIRST_NAME",
                "data_type": "string",
                "required": False,
                "validation_rules": [],
            },
        ],
        "key_columns": ["customer_id"],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _write_invalid_mapping(path: Path) -> None:
    """A mapping whose target table does NOT exist -> invalid reconcile."""
    doc = {
        "mapping_name": "invalid_orders",
        "version": "1.0.0",
        "description": "Invalid mapping whose target table does not exist.",
        "source": {"type": "file", "format": "pipe_delimited", "file_path": "y.txt"},
        "target": {"type": "database", "table_name": "DOES_NOT_EXIST"},
        "mappings": [
            {
                "source_column": "order_id",
                "target_column": "ORDER_ID",
                "data_type": "string",
                "required": True,
                "validation_rules": [],
            },
        ],
        "key_columns": ["order_id"],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _build_mappings_dir(tmp_path: Path) -> Path:
    mappings_dir = tmp_path / "mappings"
    mappings_dir.mkdir()
    _write_valid_mapping(mappings_dir / "valid.json")
    _write_invalid_mapping(mappings_dir / "invalid.json")
    return mappings_dir


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_reconcile_all_aggregate_verdict(monkeypatch, tmp_path):
    """One valid + one invalid mapping -> aggregate counts add up."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))

    mappings_dir = _build_mappings_dir(tmp_path)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "reconcile_all", request_id=2,
            arguments={"mappings_dir": str(mappings_dir)},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"reconcile_all reported isError: {result!r}"
    payload = _structured_or_text(result)
    assert payload["total_mappings"] == 2, payload
    assert payload["valid_mappings"] == 1, payload
    assert payload["invalid_mappings"] == 1, payload
    assert "drift" not in payload, payload  # no baseline supplied


def test_reconcile_all_with_baseline_drift(monkeypatch, tmp_path):
    """Supplying a baseline report adds a 'drift' block to the aggregate."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("DB_ADAPTER", "sqlite")
    db_path = tmp_path / "fixture.db"
    _create_fixture_db(db_path)
    monkeypatch.setenv("DB_PATH", str(db_path))

    mappings_dir = _build_mappings_dir(tmp_path)

    # A baseline report in which the invalid mapping previously had 0 errors,
    # so the current run shows newly-introduced errors as drift.
    baseline_path = tmp_path / "baseline.json"
    baseline_report = {
        "results": [
            {
                "mapping_file": str(mappings_dir / "invalid.json"),
                "valid": True,
                "error_count": 0,
                "warning_count": 0,
            }
        ]
    }
    baseline_path.write_text(json.dumps(baseline_report), encoding="utf-8")

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "reconcile_all", request_id=2,
            arguments={
                "mappings_dir": str(mappings_dir),
                "baseline": str(baseline_path),
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"reconcile_all reported isError: {result!r}"
    payload = _structured_or_text(result)
    assert "drift" in payload, payload
    drift = payload["drift"]
    assert drift["baseline"] == str(baseline_path), drift
    # The valid mapping is new to this run vs the baseline.
    assert str(mappings_dir / "valid.json") in drift["added_files"], drift
    # The invalid mapping went 0 -> 1 errors, i.e. newly-introduced drift.
    assert drift["new_errors"] >= 1, drift


def test_reconcile_all_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises reconcile_all with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "reconcile_all" in by_name, sorted(by_name)
    assert by_name["reconcile_all"].get("description")

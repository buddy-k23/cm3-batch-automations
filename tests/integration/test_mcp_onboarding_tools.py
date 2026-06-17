"""MCP onboarding-tool integration tests (EF-S5).

Eight acceptance scenarios mapped to the EF-S5 story:

1. ``test_upload_workbook_as_spec_happy_path`` — upload SHAW workbook;
   assert sandbox_path returned and file exists at that path.
2. ``test_upload_workbook_as_spec_invalid_schema_raises`` — synthetic
   invalid workbook -> tool error.
3. ``test_onboard_source_dry_run_returns_planned_writes`` — SHAW
   workbook -> ``would_write`` list contains source_yaml + mappings +
   rules + reconciliation entries.
4. ``test_onboard_source_dry_run_does_not_touch_disk`` — call dry_run,
   assert no files appear under ``config/``.
5. ``test_infer_mapping_from_sample_csv`` — 3-column / 5-row CSV ->
   3 fields with names from header row.
6. ``test_infer_mapping_from_sample_fixed_width`` — fixed-width sample
   -> fields with position + length.
7. ``test_infer_mapping_unknown_format_raises`` — bogus file with
   un-detectable format -> tool error.
8. ``test_mcp_tools_list_has_nine_entries`` — ``tools/list`` advertises
   all nine tools after EF-S5.

All tests reuse the same JSON-RPC plumbing as the EF-S4 integration
suite (``_fresh_app``, ``_rpc``, ``_call_tool``, ``_structured_or_text``,
``_assert_is_error``) — see ``test_mcp_action_tools.py`` for the rationale
behind the helpers (importlib reload, SSE vs JSON body handling, etc.).
"""

from __future__ import annotations

import importlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# JSON-RPC plumbing (mirrors ``test_mcp_action_tools.py``)
# ---------------------------------------------------------------------------


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

_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}


# Path to the canonical SHAW onboarding workbook bundled with the repo.
# Resolved relative to this test file so the suite is location-agnostic
# (works under both ``pytest tests/`` and IDE-driven single-test runs).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHAW_WORKBOOK = _REPO_ROOT / "templates" / "SHAW_onboarding.xlsx"


def _fresh_app():
    """Reload the FastAPI app + every MCP-side module per test.

    The onboarding tools store their sandbox-root override in a
    module-level slot; reloading ``src.mcp.onboarding_tools`` between
    tests guarantees the slot is reset to ``None`` (no leakage from a
    prior test that forgot to restore).
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.action_tools",
        "src.mcp.tools",
        "src.mcp.onboarding_tools",
        "src.mcp.taxonomy",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


@pytest.fixture(autouse=True)
def _ensure_session_signing_key(monkeypatch):
    """Provide a dummy session signing key (see ``test_mcp_scaffold.py``)."""
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


@pytest.fixture
def sandbox_root(monkeypatch, tmp_path: Path) -> Path:
    """Redirect the MCP sandbox root into a per-test tmp directory.

    Without this fixture the production default (``~/.valdo/mcp_sandbox``)
    would receive workbooks from every test run; the developer's home
    directory would slowly accumulate stale state. We point the override
    at ``tmp_path`` instead so pytest cleans up at test end.
    """
    root = tmp_path / "mcp_sandbox"
    monkeypatch.setenv("VALDO_MCP_SANDBOX_ROOT", str(root))
    return root


def _parse_streamable_body(response) -> Dict[str, Any]:
    """Decode an MCP Streamable-HTTP response body to a JSON dict."""
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
    """Send a JSON-RPC POST to ``/mcp/`` and parse the body."""
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
    """Extract the JSON payload from a CallToolResult."""
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
    """Assert the response carries an MCP tool error mentioning *needle*."""
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
# 1. upload_workbook_as_spec — happy path
# ---------------------------------------------------------------------------


def test_upload_workbook_as_spec_happy_path(monkeypatch, sandbox_root):
    """Staging a valid workbook returns sandbox_path + creates the file."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    assert _SHAW_WORKBOOK.is_file(), (
        f"Expected canonical SHAW workbook at {_SHAW_WORKBOOK}; "
        "test cannot run without the bundled fixture."
    )

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "upload_workbook_as_spec",
            request_id=2,
            arguments={"workbook_path": str(_SHAW_WORKBOOK)},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), (
        f"upload_workbook_as_spec reported isError: {result!r}"
    )
    payload = _structured_or_text(result)

    assert isinstance(payload, dict), f"payload not a dict: {payload!r}"
    assert payload.get("source_code") == "SHAW", payload
    sandbox_path = payload.get("sandbox_path")
    assert isinstance(sandbox_path, str) and sandbox_path, payload
    staged = Path(sandbox_path)
    assert staged.is_file(), (
        f"Sandbox copy missing from disk: {sandbox_path}"
    )
    # Sandbox path must live under the override root.
    assert str(staged).startswith(str(sandbox_root.resolve())) or str(
        staged
    ).startswith(str(sandbox_root)), (
        f"Sandbox path {sandbox_path!r} not rooted under override "
        f"{sandbox_root!r}"
    )
    assert payload.get("sheet_count", 0) > 0, payload
    assert isinstance(payload.get("warnings"), list), payload


# ---------------------------------------------------------------------------
# 2. upload_workbook_as_spec — invalid schema
# ---------------------------------------------------------------------------


def test_upload_workbook_as_spec_invalid_schema_raises(
    monkeypatch, tmp_path: Path, sandbox_root
):
    """A workbook missing required sheets fails with a tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    # Build a minimal .xlsx that lacks the required Source / InputFiles
    # / OutputFiles sheets. openpyxl gives us a stub workbook with a
    # single ``Sheet`` tab — the schema validator must reject that.
    from openpyxl import Workbook

    bogus_path = tmp_path / "bogus.xlsx"
    wb = Workbook()
    wb.active.title = "NotASource"
    wb.active["A1"] = "garbage"
    wb.save(str(bogus_path))

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "upload_workbook_as_spec",
            request_id=2,
            arguments={"workbook_path": str(bogus_path)},
        )

    # The validator surfaces "missing required sheet" — the agent
    # should see that verbatim.
    _assert_is_error(body, "missing")


# ---------------------------------------------------------------------------
# 3. onboard_source_dry_run — happy path
# ---------------------------------------------------------------------------


def test_onboard_source_dry_run_returns_planned_writes(monkeypatch):
    """SHAW workbook dry-run returns every artefact category."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    assert _SHAW_WORKBOOK.is_file()

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "onboard_source_dry_run",
            request_id=2,
            arguments={"workbook_path": str(_SHAW_WORKBOOK)},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), (
        f"onboard_source_dry_run reported isError: {result!r}"
    )
    payload = _structured_or_text(result)

    assert payload.get("source_code") == "SHAW", payload
    would_write = payload.get("would_write")
    assert isinstance(would_write, list) and would_write, payload

    summary = payload.get("summary") or {}
    assert summary.get("total_files") == len(would_write)
    assert isinstance(summary.get("total_bytes"), int)
    assert summary.get("total_bytes", 0) > 0

    # Each entry must have the documented {path, bytes, kind} shape.
    for entry in would_write:
        assert isinstance(entry.get("path"), str) and entry["path"]
        assert isinstance(entry.get("bytes"), int) and entry["bytes"] > 0
        assert entry.get("kind") in {
            "source_yaml",
            "mapping_json",
            "rules_json",
            "reconciliation_yaml",
            "sql",
        }, entry

    kinds = {e["kind"] for e in would_write}
    # SHAW always produces source_yaml + mappings + rules + reconciliation.
    # SQL artefacts only land when a reconciliation row lacks an
    # ``expected_sql_override`` — SHAW's BA has populated overrides for
    # every record type, so ``sql`` may be absent. We assert the
    # mandatory four only.
    expected_minimum = {
        "source_yaml",
        "mapping_json",
        "rules_json",
        "reconciliation_yaml",
    }
    assert expected_minimum.issubset(kinds), (
        f"Missing artefact categories: {expected_minimum - kinds}; "
        f"saw: {kinds!r}"
    )


# ---------------------------------------------------------------------------
# 4. onboard_source_dry_run — no disk side effects
# ---------------------------------------------------------------------------


def test_onboard_source_dry_run_does_not_touch_disk(
    monkeypatch, tmp_path: Path
):
    """Dry-run must NOT create artefacts under any config/ directory.

    We chdir into a clean tmp dir so the only paths under ``config/``
    that could possibly exist are the ones the tool would create
    itself. We then snapshot the contents before + after the call and
    assert no new files appeared.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.chdir(tmp_path)

    app = _fresh_app()

    # Snapshot tmp_path BEFORE the call. We rglob everything so we
    # would notice any rogue file (not just under config/).
    before = set(p for p in tmp_path.rglob("*"))

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "onboard_source_dry_run",
            request_id=2,
            arguments={"workbook_path": str(_SHAW_WORKBOOK)},
        )

    result = body.get("result") or {}
    assert not result.get("isError"), result

    after = set(p for p in tmp_path.rglob("*"))
    new = after - before
    assert not new, (
        f"Dry-run touched disk; new paths: {sorted(p.as_posix() for p in new)!r}"
    )


# ---------------------------------------------------------------------------
# 5. infer_mapping_from_sample — CSV with header row
# ---------------------------------------------------------------------------


def test_infer_mapping_from_sample_csv(monkeypatch, tmp_path: Path):
    """A 3-column CSV with header returns 3 fields named from the header."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    csv_path = tmp_path / "sample.csv"
    csv_path.write_text(
        "CUST_ID,CUST_NAME,BALANCE\n"
        "1,alice,100.50\n"
        "2,bob,250.75\n"
        "3,carol,99.99\n"
        "4,dave,500.00\n"
        "5,eve,1.23\n",
        encoding="utf-8",
    )

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "infer_mapping_from_sample",
            request_id=2,
            arguments={
                "sample_file_path": str(csv_path),
                "file_type": "TRANERT",
                "format_hint": "csv",
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), (
        f"infer_mapping_from_sample reported isError: {result!r}"
    )
    payload = _structured_or_text(result)

    assert payload.get("file_type") == "TRANERT", payload
    assert payload.get("format") == "csv", payload
    fields = payload.get("fields") or []
    assert len(fields) == 3, fields
    names = [f["name"] for f in fields]
    assert names == ["CUST_ID", "CUST_NAME", "BALANCE"], names
    # Every field surfaces a target_name; CSV samples carry no
    # position/length (those are fixed-width-only).
    for f in fields:
        assert "data_type" in f
        assert "target_name" in f
        assert f.get("target_name") is not None


# ---------------------------------------------------------------------------
# 6. infer_mapping_from_sample — fixed-width
# ---------------------------------------------------------------------------


def test_infer_mapping_from_sample_fixed_width(monkeypatch, tmp_path: Path):
    """A fixed-width sample returns fields with position + length set."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    fw_path = tmp_path / "fw.dat"
    # Three columns separated by consistent spacing so the
    # space-mask detector finds two field boundaries.
    fw_path.write_text(
        "123 alice    100\n"
        "456 bob      250\n"
        "789 carol     99\n",
        encoding="utf-8",
    )

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "infer_mapping_from_sample",
            request_id=2,
            arguments={
                "sample_file_path": str(fw_path),
                "file_type": "ATOCTRAN",
                "format_hint": "fixed_width",
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), (
        f"infer_mapping_from_sample reported isError: {result!r}"
    )
    payload = _structured_or_text(result)

    assert payload.get("file_type") == "ATOCTRAN", payload
    assert payload.get("format") == "fixed_width", payload
    fields = payload.get("fields") or []
    assert len(fields) >= 2, (
        f"Fixed-width inference produced too few fields: {fields!r}"
    )
    # Every fixed-width field must carry a position and a length.
    for f in fields:
        assert isinstance(f.get("position"), int), f
        assert isinstance(f.get("length"), int) and f["length"] > 0, f


# ---------------------------------------------------------------------------
# 7. infer_mapping_from_sample — un-detectable format
# ---------------------------------------------------------------------------


def test_infer_mapping_unknown_format_raises(monkeypatch, tmp_path: Path):
    """An empty / undetectable file surfaces a tool error.

    An empty file is the cleanest way to drive the service's
    ``"File is empty"`` ValueError branch (which the MCP adapter
    re-raises as a ``ToolError``). Auto-detect failure is harder to
    trigger reliably across machines because the detector returns
    something for almost any text input.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    empty_path = tmp_path / "empty.dat"
    empty_path.write_text("", encoding="utf-8")

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "infer_mapping_from_sample",
            request_id=2,
            arguments={
                "sample_file_path": str(empty_path),
                "file_type": "TRANERT",
            },
        )

    _assert_is_error(body, "empty")


# ---------------------------------------------------------------------------
# 8. tools/list advertises every registered tool
#
# Count reconciled to reality: EF-S2 (3) + EF-S4 (3) + EF-S5 (3) +
# S7-4 compare_two_files (1) + #407 reconcile_mapping (1) +
# S21-1 db_compare (1) + S21-2 reconcile_all (1) + S21-3 mask_file (1) +
# S21-4 detect_drift (1) = 15 tools. We set the expected list to the TRUE
# registered surface here.
# ---------------------------------------------------------------------------


def test_mcp_tools_list_has_nine_entries(monkeypatch):
    """``tools/list`` advertises every registered tool (EF-S2..S21-2)."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    names = sorted(t["name"] for t in tools)
    assert names == [
        "compare_two_files",
        "db_compare",
        "detect_drift",
        "get_run_status",
        "get_source_spec",
        "get_violations",
        "infer_mapping_from_sample",
        "list_recent_runs",
        "list_sources",
        "mask_file",
        "onboard_source_dry_run",
        "reconcile_all",
        "reconcile_mapping",
        "upload_workbook_as_spec",
        "validate_file",
    ], f"tools/list drifted from the registered tool surface: {names!r}"

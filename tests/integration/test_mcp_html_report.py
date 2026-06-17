"""MCP HTML-report-over-MCP integration tests (S23-4, #446, ADR 0023).

These tests exercise the FINAL story of the MCP-parity + HTML program: making
rendered HTML reports retrievable over the MCP transport. They cover the
contract defined by ADR 0023 end-to-end (JSON-RPC over the Streamable-HTTP
transport, not by calling the adapter functions directly):

1. ``test_validate_file_include_report_then_read`` — call ``validate_file``
   with ``include_report=True``, assert the response carries ``report_uri``
   (``report://<run_id>``), ``report_url`` (``/reports/<id>.html``), and an
   absolute ``report_path``; then ``resources/read`` the ``report_uri`` and
   assert an HTML body (``<!DOCTYPE html>`` / ``</html>``) comes back.

2. ``test_compare_two_files_include_report_then_read`` — same round-trip for a
   second report-producing tool (``compare_two_files``), proving the contract
   is shared, not validate-specific.

3. ``test_validate_file_default_is_json_only`` — the default (no
   ``include_report``) is JSON-only and UNCHANGED: the three report fields are
   ABSENT (not null-padded), so existing callers see no contract change.

4. ``test_report_resource_rejects_traversal`` — a traversal attempt on the
   ``report://`` resource (``report://../etc/passwd``) is rejected with a clean
   error, never serving a file outside the reports dir.

The reports dir is redirected to a per-test tmp dir via ``VALDO_REPORTS_DIR``
so the suite is hermetic and never touches the committed ``reports/`` dir. The
``run_validate_service`` engine call is stubbed (mirroring
``test_mcp_action_tools.py``) so the validation report renders from a
deterministic result without any real file parsing.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

import pytest
from fastapi.testclient import TestClient


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


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    Mirrors the helper in ``test_mcp_action_tools.py`` / ``test_mcp_compare_tool.py``
    — the FastAPI app, MCP server, dev-auth middleware, and the tool adapters all
    read env state at module import time, so we force re-import per test to pick
    up monkeypatched env vars (``VALDO_MCP_AUTH``, ``VALDO_REPORTS_DIR``).
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.resources.reports",
        "src.mcp.resources",
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
    """Provide a dummy session signing key for app construction."""
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


@pytest.fixture
def reports_tmp_dir(monkeypatch, tmp_path):
    """Redirect the reports dir to a per-test tmp dir (hermetic)."""
    reports = tmp_path / "reports"
    reports.mkdir()
    monkeypatch.setenv("VALDO_REPORTS_DIR", str(reports))
    return reports


@pytest.fixture
def temp_data_file():
    """Yield a path to a small temporary data file (existence-only check)."""
    fd, path = tempfile.mkstemp(prefix="valdo_mcp_report_test_", suffix=".dat")
    os.write(fd, b"dummy row\n")
    os.close(fd)
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _patch_validate_service(monkeypatch):
    """Stub ``run_validate_service`` with a renderable deterministic result.

    The result carries enough structure for :class:`ValidationReporter` to
    render a full dashboard (``valid`` flag, ``file_metadata``, the issue
    buckets, and ``quality_metrics``). Patched on the source module because the
    adapter imports the symbol lazily inside the run.
    """
    import src.services.validate_service as svc

    def _stub(**kwargs):
        return {
            "valid": True,
            "errors": [],
            "warnings": [],
            "info": [],
            "total_rows": 1,
            "error_count": 0,
            "warning_count": 0,
            "elapsed_seconds": 0.0,
            "file_metadata": {"file_name": "dummy.dat", "format": "csv", "size_mb": 0.0},
            "quality_metrics": {
                "quality_score": 100,
                "total_rows": 1,
                "total_columns": 1,
                "completeness_pct": 100,
                "uniqueness_pct": 100,
            },
        }

    monkeypatch.setattr(svc, "run_validate_service", _stub)


def _parse_streamable_body(response) -> Dict[str, Any]:
    """Decode an MCP Streamable-HTTP response body to a JSON dict."""
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        for raw_line in response.text.splitlines():
            line = raw_line.strip()
            if line.startswith("data:"):
                return json.loads(line[len("data:"):].strip())
        raise AssertionError(f"SSE response had no data line: {response.text!r}")
    return response.json()


def _rpc(client, method, *, request_id, params=None) -> Dict[str, Any]:
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


def _call_tool(client, tool_name, *, request_id, arguments=None) -> Dict[str, Any]:
    """Invoke ``tools/call`` for *tool_name* and return the parsed body."""
    return _rpc(
        client,
        "tools/call",
        request_id=request_id,
        params={"name": tool_name, "arguments": arguments or {}},
    )


def _structured_or_text(result: Dict[str, Any]) -> Any:
    """Extract the JSON payload from a CallToolResult (structured or text)."""
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


def _read_resource_text(client, uri: str, *, request_id: int) -> Dict[str, Any]:
    """``resources/read`` *uri* and return the raw JSON-RPC body."""
    return _rpc(
        client, "resources/read", request_id=request_id, params={"uri": uri}
    )


def _extract_resource_text(read_body: Dict[str, Any]) -> str:
    """Pull the first ``text`` field from a ``resources/read`` response."""
    assert "result" in read_body, f"resources/read missing 'result': {read_body!r}"
    contents = read_body["result"].get("contents")
    assert isinstance(contents, list) and contents, (
        f"resources/read contents not a non-empty list: {contents!r}"
    )
    text = contents[0].get("text")
    assert isinstance(text, str) and text, (
        f"resources/read first content has no text: {contents[0]!r}"
    )
    return text


def _assert_report_fields(payload: Dict[str, Any], reports_dir: Path) -> str:
    """Assert the three ADR-0023 report fields are present + coherent.

    Returns the ``report_uri`` so the caller can read it back.
    """
    report_uri = payload.get("report_uri")
    report_url = payload.get("report_url")
    report_path = payload.get("report_path")

    assert isinstance(report_uri, str) and report_uri.startswith("report://"), (
        f"report_uri missing / malformed: {report_uri!r}"
    )
    assert isinstance(report_url, str) and report_url.startswith("/reports/"), (
        f"report_url missing / malformed: {report_url!r}"
    )
    assert isinstance(report_path, str) and report_path, (
        f"report_path missing: {report_path!r}"
    )
    # report_path is absolute and lives inside the (redirected) reports dir.
    assert os.path.isabs(report_path), f"report_path not absolute: {report_path!r}"
    assert Path(report_path).resolve().is_relative_to(reports_dir.resolve()), (
        f"report_path {report_path!r} is outside reports dir {reports_dir!r}"
    )
    assert Path(report_path).is_file(), f"report file was not written: {report_path!r}"
    # The id in report_uri, report_url, and the file name all agree.
    run_id = report_uri[len("report://"):]
    assert report_url == f"/reports/{run_id}.html"
    assert Path(report_path).name == f"{run_id}.html"
    return report_uri


# ---------------------------------------------------------------------------
# 1. validate_file include_report -> report_uri -> resources/read HTML
# ---------------------------------------------------------------------------


def test_validate_file_include_report_then_read(
    monkeypatch, reports_tmp_dir, temp_data_file
):
    """include_report=True yields report fields; the report:// reads as HTML."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    # Force the inline path regardless of any live worker so the report is
    # produced synchronously (validate_file also forces inline on include_report).
    monkeypatch.setenv("VALDO_MCP_ASYNC_VALIDATE", "0")
    _patch_validate_service(monkeypatch)
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "validate_file",
            request_id=2,
            arguments={
                "source": "SHAW",
                "file_path": temp_data_file,
                "file_type": "TRANERT",
                "include_report": True,
            },
        )
        result = body["result"]
        assert not result.get("isError"), f"validate_file reported isError: {result!r}"
        payload = _structured_or_text(result)

        report_uri = _assert_report_fields(payload, reports_tmp_dir)

        read_body = _read_resource_text(client, report_uri, request_id=3)

    html = _extract_resource_text(read_body)
    assert "<!DOCTYPE html>" in html or "<html" in html, (
        f"report body did not look like HTML: {html[:120]!r}"
    )
    assert "</html>" in html, "report body missing closing </html>"


# ---------------------------------------------------------------------------
# 2. compare_two_files include_report -> report_uri -> resources/read HTML
# ---------------------------------------------------------------------------


def test_compare_two_files_include_report_then_read(monkeypatch, reports_tmp_dir, tmp_path):
    """A second report-producing tool honours the same include_report contract."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    header = "id,name,balance\n"
    left = tmp_path / "left.csv"
    right = tmp_path / "right.csv"
    left.write_text(header + "1,Alice,100\n2,Bob,200\n3,Charlie,300\n", encoding="utf-8")
    right.write_text(header + "1,Alice,100\n2,Bob,200\n3,Charlie,350\n", encoding="utf-8")

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
                "include_report": True,
            },
        )
        result = body["result"]
        assert not result.get("isError"), f"compare_two_files reported isError: {result!r}"
        payload = _structured_or_text(result)

        report_uri = _assert_report_fields(payload, reports_tmp_dir)

        read_body = _read_resource_text(client, report_uri, request_id=3)

    html = _extract_resource_text(read_body)
    assert "<html" in html.lower() and "</html>" in html.lower(), (
        f"comparison report body did not look like HTML: {html[:120]!r}"
    )


# ---------------------------------------------------------------------------
# 3. default (no include_report) is JSON-only, UNCHANGED
# ---------------------------------------------------------------------------


def test_validate_file_default_is_json_only(monkeypatch, reports_tmp_dir, temp_data_file):
    """Without include_report the three report fields are ABSENT (unchanged)."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    monkeypatch.setenv("VALDO_MCP_ASYNC_VALIDATE", "0")
    _patch_validate_service(monkeypatch)
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "validate_file",
            request_id=2,
            arguments={
                "source": "SHAW",
                "file_path": temp_data_file,
                "file_type": "TRANERT",
            },
        )

    payload = _structured_or_text(body["result"])
    assert isinstance(payload.get("run_id"), str) and payload["run_id"]
    # The report fields must be absent — not null-padded (ADR 0023 §2).
    assert "report_uri" not in payload, f"report_uri leaked into default response: {payload!r}"
    assert "report_url" not in payload, f"report_url leaked into default response: {payload!r}"
    assert "report_path" not in payload, f"report_path leaked into default response: {payload!r}"


# ---------------------------------------------------------------------------
# 4. traversal attempt on report:// is rejected
# ---------------------------------------------------------------------------


def test_report_resource_rejects_traversal(monkeypatch, reports_tmp_dir):
    """``report://../etc/passwd`` is rejected — never served from outside reports dir."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        read_body = _read_resource_text(
            client, "report://../etc/passwd", request_id=2
        )

    # The read must NOT return file contents. Either a JSON-RPC error envelope,
    # or (if FastMCP surfaces resource errors as content) no readable text body.
    error_envelope = read_body.get("error")
    if error_envelope is not None:
        message = (error_envelope.get("message") or "").lower()
        assert "invalid" in message or "not found" in message or "report" in message, (
            f"traversal rejection message unexpected: {message!r}"
        )
    else:
        result = read_body.get("result") or {}
        contents = result.get("contents") or []
        # If any content came back it must NOT be /etc/passwd (no root: line).
        for entry in contents:
            text = entry.get("text") or ""
            assert "root:" not in text, (
                f"traversal read leaked passwd-like content: {text[:80]!r}"
            )

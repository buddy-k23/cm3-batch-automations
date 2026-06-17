"""MCP action-tool integration tests (EF-S4).

Nine acceptance scenarios mapped to the EF-S4 story:

1. ``test_validate_file_returns_run_id`` — ``tools/call validate_file``
   against ``SHAW`` + a temp file returns a string ``run_id`` and ISO
   ``started_at``.
2. ``test_validate_file_unknown_source_raises`` — unknown source -> MCP
   tool error.
3. ``test_validate_file_unknown_file_path_raises`` — non-existent file
   -> MCP tool error.
4. ``test_get_run_status_returns_state`` — status after ``validate_file``
   is one of the canonical lifecycle values.
5. ``test_get_run_status_unknown_run_id_raises`` — bogus id -> tool
   error.
6. ``test_get_violations_paged`` — pagination shape is honoured.
7. ``test_get_violations_severity_filter`` — severity filter is
   case-insensitive and only returns rows of the requested bucket.
8. ``test_get_violations_unknown_run_id_raises`` — bogus id -> tool
   error.
9. ``test_mcp_tools_list_has_nine_entries`` — ``tools/list`` advertises
   all nine tools after EF-S5 lands (EF-S2 read-only + EF-S4 action +
   EF-S5 onboarding).

All tests stub out :func:`src.services.validate_service.run_validate_service`
so the MCP adapter is exercised without standing up the Oracle backend
or shipping a real fixture file. The point is to verify the adapter
calls the engine with the right args and surfaces the result shape
faithfully — not to re-test the engine itself.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from typing import Any, Dict, List, Optional

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

    Mirrors the helper in ``test_mcp_scaffold.py`` and
    ``test_mcp_read_tools.py`` — the FastAPI app, MCP server, dev-auth
    middleware, and our action-tool registry all read env state at
    module import time so we must force a re-import per test.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
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

    Matches the fixture in ``test_mcp_scaffold.py`` / ``test_mcp_read_tools.py``;
    see those for context on why this is required by the FastAPI auth
    layer at import time.
    """
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


@pytest.fixture(autouse=True)
def _reset_action_registry():
    """Clear the in-process run registry between tests.

    The EF-S4 action tools keep run state in a module-level dict; tests
    that don't reset risk cross-talk (e.g. test 6 polling a run_id
    created by test 4). We reset both before and after each test so a
    test that bails halfway through doesn't poison its successor.
    """
    yield  # the _fresh_app reload below already wipes module state at
    # entry; we ensure post-test cleanup too.
    try:
        from src.mcp.action_tools import _reset_runs_for_tests
        _reset_runs_for_tests()
    except Exception:  # noqa: BLE001 — defensive cleanup
        pass


def _parse_streamable_body(response) -> Dict[str, Any]:
    """Decode an MCP Streamable-HTTP response body to a JSON dict.

    Borrowed verbatim from ``test_mcp_read_tools.py`` — the FastMCP
    transport may answer either as plain JSON or as a single-event SSE
    frame depending on what the client asked for.
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

    Mirrors the helper in ``test_mcp_read_tools.py``.
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


@pytest.fixture
def temp_data_file():
    """Create a tiny temporary file the validate tool can point at.

    The contents don't matter — we stub the engine entry point so the
    file is only opened by the MCP adapter's existence check. We do
    write a non-empty body so any defensive size check inside the
    adapter (none today, but cheap insurance) won't reject it.
    """
    fd, path = tempfile.mkstemp(prefix="valdo_mcp_test_", suffix=".dat")
    os.write(fd, b"dummy row\n")
    os.close(fd)
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _patch_validate_service(
    monkeypatch,
    result: Dict[str, Any] | None = None,
    *,
    raises: Exception | None = None,
):
    """Replace ``run_validate_service`` with a deterministic stub.

    The adapter imports the symbol lazily inside the run thread, so we
    have to patch it *on the source module* — patching
    ``src.mcp.action_tools.run_validate_service`` would no-op because
    the import happens after the patch.
    """
    import src.services.validate_service as svc

    captured: Dict[str, Any] = {}

    def _stub(**kwargs):
        captured.update(kwargs)
        if raises is not None:
            raise raises
        return result or {
            "valid": True,
            "errors": [],
            "warnings": [],
            "info": [],
            "total_rows": 1,
            "error_count": 0,
            "warning_count": 0,
            "elapsed_seconds": 0.0,
        }

    monkeypatch.setattr(svc, "run_validate_service", _stub)
    return captured


# ---------------------------------------------------------------------------
# 1. validate_file happy path
# ---------------------------------------------------------------------------


def test_validate_file_returns_run_id(monkeypatch, temp_data_file):
    """``validate_file`` returns a string ``run_id`` + ``started_at``."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    captured = _patch_validate_service(monkeypatch)

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

    assert "result" in body, f"tools/call missing 'result': {body!r}"
    result = body["result"]
    assert not result.get("isError"), f"validate_file reported isError: {result!r}"

    payload = _structured_or_text(result)
    assert isinstance(payload, dict), f"validate_file payload not a dict: {payload!r}"

    assert isinstance(payload.get("run_id"), str) and payload["run_id"], (
        f"run_id missing or not a string: {payload!r}"
    )
    started_at = payload.get("started_at")
    assert isinstance(started_at, str) and started_at.endswith("Z"), (
        f"started_at not an ISO-8601 UTC string: {started_at!r}"
    )

    # Sanity-check the adapter wired the call to the engine with the
    # right primary arg — the file_path we supplied must have been
    # forwarded verbatim.
    assert captured.get("file") == temp_data_file, (
        f"engine was called with the wrong file path: {captured!r}"
    )


# ---------------------------------------------------------------------------
# 2. validate_file unknown source
# ---------------------------------------------------------------------------


def test_validate_file_unknown_source_raises(monkeypatch, temp_data_file):
    """``validate_file`` on an unknown source returns an MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    _patch_validate_service(monkeypatch)  # should not be called

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "validate_file",
            request_id=2,
            arguments={
                "source": "DOES_NOT_EXIST",
                "file_path": temp_data_file,
            },
        )

    _assert_is_error(body, "does_not_exist")


# ---------------------------------------------------------------------------
# 3. validate_file unknown file path
# ---------------------------------------------------------------------------


def test_validate_file_unknown_file_path_raises(monkeypatch):
    """``validate_file`` on a non-existent file returns an MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    _patch_validate_service(monkeypatch)  # should not be called

    app = _fresh_app()

    bogus = "/tmp/this/path/definitely/does/not/exist_valdo_ef_s4.dat"

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "validate_file",
            request_id=2,
            arguments={
                "source": "SHAW",
                "file_path": bogus,
            },
        )

    _assert_is_error(body, "file not found")


# ---------------------------------------------------------------------------
# 4. get_run_status after a happy-path validate
# ---------------------------------------------------------------------------


def test_get_run_status_returns_state(monkeypatch, temp_data_file):
    """``get_run_status`` returns the lifecycle state after validate_file."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    _patch_validate_service(monkeypatch)

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        start_body = _call_tool(
            client,
            "validate_file",
            request_id=2,
            arguments={
                "source": "SHAW",
                "file_path": temp_data_file,
            },
        )
        run_id = _structured_or_text(start_body["result"])["run_id"]

        status_body = _call_tool(
            client,
            "get_run_status",
            request_id=3,
            arguments={"run_id": run_id},
        )

    result = status_body.get("result") or {}
    assert not result.get("isError"), f"get_run_status reported isError: {result!r}"
    payload = _structured_or_text(result)

    expected_keys = {
        "run_id",
        "status",
        "started_at",
        "finished_at",
        "violation_count",
    }
    assert expected_keys.issubset(payload.keys()), (
        f"get_run_status missing keys {expected_keys - payload.keys()}: {payload!r}"
    )
    assert payload["run_id"] == run_id
    assert payload["status"] in {"queued", "running", "completed", "failed"}, (
        f"unknown status value: {payload!r}"
    )
    # The stub completes synchronously, so the status MUST have reached
    # ``completed`` by the time we poll.
    assert payload["status"] == "completed", (
        f"expected completed after sync run; got: {payload!r}"
    )
    assert payload["violation_count"] == 0


# ---------------------------------------------------------------------------
# 5. get_run_status unknown run_id
# ---------------------------------------------------------------------------


def test_get_run_status_unknown_run_id_raises(monkeypatch):
    """``get_run_status`` on a bogus id surfaces an MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    # Force the run-history fallback to return an empty list so the
    # tool can't accidentally match against a real Oracle row in a dev
    # environment that happens to have one.
    import src.services.run_history_service as svc

    monkeypatch.setattr(svc, "fetch_history_from_db", lambda limit=20: [])

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "get_run_status",
            request_id=2,
            arguments={"run_id": "does-not-exist-1234567890"},
        )

    _assert_is_error(body, "unknown run_id")


# ---------------------------------------------------------------------------
# 6. get_violations pagination
# ---------------------------------------------------------------------------


def _build_stub_result_with_violations(n_errors: int, n_warnings: int) -> Dict[str, Any]:
    """Construct a stub engine result carrying *n* violations."""
    errors = [
        {
            "severity": "error",
            "category": "schema",
            "code": f"E{i:03d}",
            "message": f"error row {i}",
            "row": i,
            "field": f"FLD_{i}",
            "value": f"val-{i}",
        }
        for i in range(1, n_errors + 1)
    ]
    warnings = [
        {
            "severity": "warning",
            "category": "format",
            "code": f"W{i:03d}",
            "message": f"warning row {i}",
            "row": i,
            "field": f"FLD_W_{i}",
        }
        for i in range(1, n_warnings + 1)
    ]
    return {
        "valid": False,
        "errors": errors,
        "warnings": warnings,
        "info": [],
        "total_rows": max(n_errors, n_warnings, 1),
        "error_count": n_errors,
        "warning_count": n_warnings,
    }


def test_get_violations_paged(monkeypatch, temp_data_file):
    """``get_violations`` honours ``page`` + ``page_size`` correctly."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    stub_result = _build_stub_result_with_violations(n_errors=7, n_warnings=3)
    _patch_validate_service(monkeypatch, result=stub_result)

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        start_body = _call_tool(
            client,
            "validate_file",
            request_id=2,
            arguments={
                "source": "SHAW",
                "file_path": temp_data_file,
            },
        )
        run_id = _structured_or_text(start_body["result"])["run_id"]

        page_one = _call_tool(
            client,
            "get_violations",
            request_id=3,
            arguments={"run_id": run_id, "page": 1, "page_size": 5},
        )
        page_two = _call_tool(
            client,
            "get_violations",
            request_id=4,
            arguments={"run_id": run_id, "page": 2, "page_size": 5},
        )

    p1 = _structured_or_text(page_one["result"])
    p2 = _structured_or_text(page_two["result"])

    # 7 errors + 3 warnings = 10 total
    assert p1["total_count"] == 10, p1
    assert p1["page"] == 1
    assert p1["page_size"] == 5
    assert p1["has_more"] is True
    assert len(p1["violations"]) == 5

    # Page 2 picks up the remaining 5.
    assert p2["total_count"] == 10, p2
    assert p2["page"] == 2
    assert p2["has_more"] is False
    assert len(p2["violations"]) == 5

    # Pin the per-row schema documented in the EF-S4 AC.
    sample = p1["violations"][0]
    expected_keys = {"rule_id", "field", "severity", "message", "record_index", "actual_value"}
    assert expected_keys.issubset(sample.keys()), (
        f"violation row missing keys {expected_keys - sample.keys()}: {sample!r}"
    )


# ---------------------------------------------------------------------------
# 7. get_violations severity filter
# ---------------------------------------------------------------------------


def test_get_violations_severity_filter(monkeypatch, temp_data_file):
    """``get_violations`` returns only rows of the requested severity."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    stub_result = _build_stub_result_with_violations(n_errors=4, n_warnings=6)
    _patch_validate_service(monkeypatch, result=stub_result)

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        start_body = _call_tool(
            client,
            "validate_file",
            request_id=2,
            arguments={
                "source": "SHAW",
                "file_path": temp_data_file,
            },
        )
        run_id = _structured_or_text(start_body["result"])["run_id"]

        # Case-insensitive filter: feed uppercase to confirm.
        errs = _call_tool(
            client,
            "get_violations",
            request_id=3,
            arguments={"run_id": run_id, "severity": "ERROR", "page_size": 100},
        )
        warns = _call_tool(
            client,
            "get_violations",
            request_id=4,
            arguments={"run_id": run_id, "severity": "warning", "page_size": 100},
        )

    errs_payload = _structured_or_text(errs["result"])
    warns_payload = _structured_or_text(warns["result"])

    assert errs_payload["total_count"] == 4, errs_payload
    assert warns_payload["total_count"] == 6, warns_payload

    assert all(v["severity"] == "error" for v in errs_payload["violations"])
    assert all(v["severity"] == "warning" for v in warns_payload["violations"])


# ---------------------------------------------------------------------------
# 8. get_violations unknown run_id
# ---------------------------------------------------------------------------


def test_get_violations_unknown_run_id_raises(monkeypatch):
    """``get_violations`` on a bogus id surfaces an MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")

    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        body = _call_tool(
            client,
            "get_violations",
            request_id=2,
            arguments={"run_id": "bogus-run-id-9876543210"},
        )

    _assert_is_error(body, "unknown run_id")


# ---------------------------------------------------------------------------
# 9. tools/list advertises every registered tool (post-EF-S5 + S7-4 + #407 + S21)
# ---------------------------------------------------------------------------


def test_mcp_tools_list_has_ten_entries(monkeypatch):
    """``tools/list`` advertises every registered tool (EF-S2..S22-2).

    #407 added ``reconcile_mapping`` (eleven); S21-1 added ``db_compare``
    (twelve); S21-2 added ``reconcile_all`` (thirteen); S21-3 added
    ``mask_file`` (fourteen); S21-4 added ``detect_drift`` (fifteen); S21-5
    added ``extract_table`` (sixteen); S22-1 added ``parse_file`` (seventeen);
    S22-2 added ``run_etl_pipeline``, taking the surface to eighteen. The
    expected list below is the TRUE registered surface.
    """
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
        "extract_table",
        "get_run_status",
        "get_source_spec",
        "get_violations",
        "infer_mapping_from_sample",
        "list_recent_runs",
        "list_sources",
        "mask_file",
        "onboard_source_dry_run",
        "parse_file",
        "reconcile_all",
        "reconcile_mapping",
        "run_etl_pipeline",
        "upload_workbook_as_spec",
        "validate_file",
    ], f"tools/list drifted from the registered tool surface: {names!r}"

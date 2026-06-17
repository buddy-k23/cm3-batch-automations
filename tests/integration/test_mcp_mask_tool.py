"""MCP ``mask_file`` tool integration tests (S21-3, #435).

Exercises the PII-masking tool end-to-end over the MCP Streamable-HTTP
transport (JSON-RPC ``tools/call``) against a small fixture file plus a
mapping JSON and a masking-rules JSON written under ``tmp_path`` (never
tracked). Mirrors the S21-2 ``reconcile_all`` MCP test harness (``_fresh_app``
reload, SSE-or-JSON body parser, ``_assert_is_error`` channel acceptance).

The ``mask_file`` tool is a thin wrapper over
:meth:`src.services.masking_service.MaskingService.mask_file`; it loads the
mapping + masking-rules JSON, masks the input file, writes a masked copy, and
returns a PII-safe summary (output path + record count + per-field strategy
NAMES). The CRITICAL contract verified here is that the tool response carries
NO raw field values — neither the unmasked originals nor the masked
replacements.

Scenarios:

1. ``test_mask_file_produces_output_and_summary`` — masking a fixed-width
   fixture with redact / deterministic_hash / preserve rules produces the
   masked output file on disk and returns the record count + per-field
   strategy summary.
2. ``test_mask_file_response_contains_no_raw_pii`` — the tool response (the
   entire JSON blob) contains NONE of the raw PII values from the fixture and
   NONE of the masked replacement values read back from the output file —
   only paths, counts, and strategy/field names.
3. ``test_mask_file_missing_input_raises`` — a non-existent input file is a
   caller-fixable problem -> MCP tool error.
4. ``test_mask_file_advertised_in_tools_list`` — the tool is listed by
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

# Fixed-width layout: SSN(11) | NAME(10) | ACCT(8). The raw PII values below
# are what MUST NOT appear anywhere in the tool response.
_RAW_SSN = "123456789"   # padded to 11
_RAW_NAME = "ALICE"      # padded to 10
_RAW_ACCT = "00012345"   # 8 wide


def _write_mapping(path: Path) -> None:
    doc = {
        "mapping_name": "pii_fixture",
        "version": "1.0.0",
        "source": {"type": "file", "format": "fixed_width", "file_path": "x.txt"},
        "fields": [
            {"name": "SSN", "length": 11},
            {"name": "NAME", "length": 10},
            {"name": "ACCT", "length": 8},
        ],
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _write_masking_rules(path: Path) -> None:
    doc = {
        "fields": {
            "SSN": {"strategy": "redact"},
            "NAME": {"strategy": "deterministic_hash"},
            # ACCT intentionally omitted -> defaults to preserve.
        }
    }
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")


def _write_fixture_file(path: Path) -> None:
    # Two records, fixed-width per the mapping above.
    line = (
        _RAW_SSN.ljust(11) + _RAW_NAME.ljust(10) + _RAW_ACCT.ljust(8)
    )
    path.write_text(line + "\n" + line + "\n", encoding="utf-8")


def _build_inputs(tmp_path: Path):
    mapping = tmp_path / "mapping.json"
    rules = tmp_path / "masking.json"
    fixture = tmp_path / "input.txt"
    output = tmp_path / "masked.txt"
    _write_mapping(mapping)
    _write_masking_rules(rules)
    _write_fixture_file(fixture)
    return mapping, rules, fixture, output


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_mask_file_produces_output_and_summary(monkeypatch, tmp_path):
    """``mask_file`` writes the masked file and returns count + summary."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    mapping, rules, fixture, output = _build_inputs(tmp_path)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "mask_file", request_id=2,
            arguments={
                "file": str(fixture),
                "mapping": str(mapping),
                "masking_config": str(rules),
                "output": str(output),
            },
        )

    result = body.get("result") or {}
    assert not result.get("isError"), f"mask_file reported isError: {result!r}"
    payload = _structured_or_text(result)

    assert payload["output_path"] == str(output), payload
    assert payload["records_masked"] == 2, payload

    # The masked output file must actually exist on disk.
    assert output.exists(), "masked output file was not written"

    # Per-field strategy summary: names only, in mapping order.
    strategies = {s["field"]: s["strategy"] for s in payload["field_strategies"]}
    assert strategies == {
        "SSN": "redact",
        "NAME": "deterministic_hash",
        "ACCT": "preserve",
    }, payload["field_strategies"]


def test_mask_file_response_contains_no_raw_pii(monkeypatch, tmp_path):
    """CRITICAL: the tool response leaks NO raw PII — original or masked."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    mapping, rules, fixture, output = _build_inputs(tmp_path)

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "mask_file", request_id=2,
            arguments={
                "file": str(fixture),
                "mapping": str(mapping),
                "masking_config": str(rules),
                "output": str(output),
            },
        )

    # Serialise the ENTIRE response body (envelope + payload) to a string and
    # assert no raw PII value appears anywhere in it.
    blob = json.dumps(body)

    # 1. No unmasked original PII values.
    assert _RAW_SSN not in blob, "raw SSN leaked into the tool response"
    assert _RAW_NAME not in blob, "raw NAME leaked into the tool response"

    # 2. No masked replacement values either. Read them back from the output
    #    file and confirm none of them appear in the response.
    masked_line = output.read_text(encoding="utf-8").splitlines()[0]
    masked_ssn = masked_line[0:11].strip()
    masked_name = masked_line[11:21].strip()
    # deterministic_hash produces a non-empty token; it must NOT be in the
    # response.
    assert masked_name, "fixture sanity: masked NAME should be non-empty"
    assert masked_name not in blob, "masked NAME value leaked into the response"
    if masked_ssn:  # redact yields spaces -> empty after strip; guard anyway
        assert masked_ssn not in blob

    # 3. Positive: the response DOES carry the safe metadata.
    payload = _structured_or_text(body["result"])
    assert payload["records_masked"] == 2
    assert "field_strategies" in payload


def test_mask_file_missing_input_raises(monkeypatch, tmp_path):
    """A non-existent input file surfaces an MCP tool error."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    mapping, rules, _fixture, output = _build_inputs(tmp_path)
    bogus = tmp_path / "does_not_exist.txt"

    app = _fresh_app()
    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _call_tool(
            client, "mask_file", request_id=2,
            arguments={
                "file": str(bogus),
                "mapping": str(mapping),
                "masking_config": str(rules),
                "output": str(output),
            },
        )

    _assert_is_error(body, "not found")


def test_mask_file_advertised_in_tools_list(monkeypatch):
    """``tools/list`` advertises mask_file with a non-empty description."""
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        body = _rpc(client, "tools/list", request_id=2)

    tools = (body.get("result") or {}).get("tools") or []
    by_name = {t["name"]: t for t in tools}
    assert "mask_file" in by_name, sorted(by_name)
    assert by_name["mask_file"].get("description")

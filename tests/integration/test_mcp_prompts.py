"""MCP workflow-prompt integration tests (EF-S6).

Five acceptance scenarios:

1. ``test_mcp_prompts_list_has_three_entries`` — handshake → ``prompts/list``
   returns exactly the three EF-S6 workflow prompts (``onboard_new_source``,
   ``diagnose_validation_failure``, ``infer_field_map``).

2. ``test_onboard_new_source_prompt_returns_instructional_messages`` —
   ``prompts/get`` for ``onboard_new_source`` returns a non-empty
   message list whose body references both
   ``upload_workbook_as_spec`` and ``onboard_source_dry_run`` by their
   exact registered tool names.

3. ``test_diagnose_validation_failure_prompt_references_correct_tools`` —
   the diagnose prompt body references ``get_run_status``,
   ``get_violations``, and the ``taxonomy://violations`` resource URI.

4. ``test_infer_field_map_prompt_references_inference_tool`` — the
   infer-field-map prompt body references ``infer_mapping_from_sample``.

5. ``test_prompt_with_required_param_missing_raises`` — calling
   ``prompts/get`` for ``onboard_new_source`` without the required
   ``workbook_path`` argument returns a JSON-RPC error rather than a
   silently-rendered message list.

The tests share the same ``_fresh_app`` reload trick as the other
MCP integration suites so they observe the MCP sub-app with the env
they monkey-patch in (``VALDO_MCP_AUTH=dev``).
"""

from __future__ import annotations

import importlib
import json
import sys
from typing import Any, Dict, List

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

_EXPECTED_PROMPT_NAMES = sorted([
    "onboard_new_source",
    "diagnose_validation_failure",
    "infer_field_map",
])


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    The FastAPI app, the MCP server, and the dev-auth middleware all read
    env state at module import time, so we must force re-import to pick up
    monkeypatched env vars in each test.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.prompts",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


@pytest.fixture(autouse=True)
def _ensure_session_signing_key(monkeypatch):
    """Provide a dummy session signing key for app construction.

    Matches the fixture in ``test_mcp_scaffold.py``; see that file's
    docstring for context on why this is required by the FastAPI auth
    layer at import time.
    """
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


def _parse_streamable_body(response) -> Dict[str, Any]:
    """Decode an MCP Streamable-HTTP response body to a JSON dict.

    The MCP server can answer either as a plain JSON object (because we
    set ``json_response=True`` in the FastMCP constructor) or as a
    single-event SSE stream depending on the negotiated content type.
    We accept either shape and unwrap the SSE ``data:`` line when present.
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


def _join_prompt_text(get_result: Dict[str, Any]) -> str:
    """Concatenate all text payloads in a ``prompts/get`` response.

    The MCP spec wraps a prompt fetch in ``result.messages: list[PromptMessage]``
    where each message's ``content`` is a content block (``TextContent``,
    etc.). For our purposes the prompts only emit ``text`` blocks, so we
    join every block's ``text`` field into a single string for substring
    assertions.

    Raises:
        AssertionError: if the response shape is missing ``messages`` or
        any message lacks a textual content body.
    """
    assert "result" in get_result, f"prompts/get missing 'result': {get_result!r}"
    messages = get_result["result"].get("messages")
    assert isinstance(messages, list) and messages, (
        f"prompts/get messages not a non-empty list: {messages!r}"
    )
    parts: List[str] = []
    for msg in messages:
        assert msg.get("role") == "user", (
            f"prompt message role expected 'user', got: {msg!r}"
        )
        content = msg.get("content")
        assert isinstance(content, dict), f"content not a dict: {msg!r}"
        text = content.get("text")
        assert isinstance(text, str) and text, (
            f"prompt content has no text: {content!r}"
        )
        parts.append(text)
    return "\n".join(parts)


def test_mcp_prompts_list_has_three_entries(monkeypatch):
    """``prompts/list`` returns exactly the three EF-S6 workflow prompts.

    The list is asserted as a sorted name set so the test does not care
    about registration order; each entry must also carry a non-empty
    ``description`` so MCP clients show the BA / SRE something useful
    in their prompt picker without having to invoke the prompt first.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        init = _rpc(
            client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"]
        )
        assert "result" in init, init

        list_body = _rpc(client, "prompts/list", request_id=2)

    prompts = list_body["result"].get("prompts", [])
    names = sorted(p["name"] for p in prompts)
    assert names == _EXPECTED_PROMPT_NAMES, (
        f"prompts/list names drifted: {names!r}"
    )

    by_name = {p["name"]: p for p in prompts}
    for prompt_name in _EXPECTED_PROMPT_NAMES:
        description = (by_name[prompt_name].get("description") or "").strip()
        assert description, (
            f"{prompt_name} has empty description: {by_name[prompt_name]!r}"
        )


def test_onboard_new_source_prompt_returns_instructional_messages(monkeypatch):
    """``onboard_new_source`` body references the two onboarding tools.

    With ``workbook_path`` provided (and ``source_code`` omitted), the
    rendered messages must:

    * be a non-empty list
    * use role="user" (asserted inside ``_join_prompt_text``)
    * mention ``upload_workbook_as_spec`` AND ``onboard_source_dry_run``
      by their exact registered tool names — so any tool rename in
      EF-S5 fails this test fast and forces a coordinated prompt update.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        init = _rpc(
            client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"]
        )
        assert "result" in init, init

        get_body = _rpc(
            client,
            "prompts/get",
            request_id=2,
            params={
                "name": "onboard_new_source",
                "arguments": {"workbook_path": "/tmp/shaw-onboarding.xlsx"},
            },
        )

    text = _join_prompt_text(get_body)
    assert "upload_workbook_as_spec" in text, (
        f"onboard_new_source body missing upload_workbook_as_spec reference: {text!r}"
    )
    assert "onboard_source_dry_run" in text, (
        f"onboard_new_source body missing onboard_source_dry_run reference: {text!r}"
    )
    # The workbook_path argument must be interpolated into the body so
    # the agent reads it without having to re-derive it from RPC
    # arguments — this is the main user-facing affordance of the prompt.
    assert "/tmp/shaw-onboarding.xlsx" in text, (
        f"onboard_new_source body did not interpolate workbook_path: {text!r}"
    )


def test_diagnose_validation_failure_prompt_references_correct_tools(monkeypatch):
    """``diagnose_validation_failure`` body references status + violations + taxonomy.

    Asserts the prompt body contains ``get_run_status``, ``get_violations``,
    and ``taxonomy://violations`` by their exact registered identifiers.
    Also asserts the ``run_id`` argument is interpolated into the body
    so the agent sees the same identifier the user typed.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        init = _rpc(
            client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"]
        )
        assert "result" in init, init

        get_body = _rpc(
            client,
            "prompts/get",
            request_id=2,
            params={
                "name": "diagnose_validation_failure",
                "arguments": {"run_id": "run_abc_20260613"},
            },
        )

    text = _join_prompt_text(get_body)
    assert "get_run_status" in text, (
        f"diagnose body missing get_run_status reference: {text!r}"
    )
    assert "get_violations" in text, (
        f"diagnose body missing get_violations reference: {text!r}"
    )
    assert "taxonomy://violations" in text, (
        f"diagnose body missing taxonomy://violations reference: {text!r}"
    )
    assert "run_abc_20260613" in text, (
        f"diagnose body did not interpolate run_id: {text!r}"
    )


def test_infer_field_map_prompt_references_inference_tool(monkeypatch):
    """``infer_field_map`` body references the inference tool by name.

    Asserts the prompt body contains ``infer_mapping_from_sample`` and
    the ``FIELD_NNN`` placeholder pattern (so the BA is reminded to
    rename low-confidence guesses before committing).
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        init = _rpc(
            client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"]
        )
        assert "result" in init, init

        get_body = _rpc(
            client,
            "prompts/get",
            request_id=2,
            params={
                "name": "infer_field_map",
                "arguments": {
                    "sample_file_path": "/tmp/sample_transactions.csv",
                    "file_type": "transactions",
                },
            },
        )

    text = _join_prompt_text(get_body)
    assert "infer_mapping_from_sample" in text, (
        f"infer body missing infer_mapping_from_sample reference: {text!r}"
    )
    assert "FIELD_" in text, (
        f"infer body missing FIELD_NNN low-confidence flag: {text!r}"
    )
    # The file_type drives the target sheet name in the workbook
    # walkthrough step — assert it interpolates verbatim.
    assert "transactions_Mapping" in text, (
        f"infer body did not interpolate file_type into sheet name: {text!r}"
    )


def test_prompt_with_required_param_missing_raises(monkeypatch):
    """Omitting a required parameter returns a JSON-RPC error.

    Calling ``prompts/get`` for ``onboard_new_source`` without the
    required ``workbook_path`` argument must NOT silently render the
    prompt body with a stringified ``None`` — the MCP server must
    surface a JSON-RPC ``error`` envelope so the calling agent knows
    its invocation was malformed.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        init = _rpc(
            client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"]
        )
        assert "result" in init, init

        # Send the malformed get directly — do not use ``_rpc`` because
        # it asserts ``result`` is present; this scenario expects
        # ``error`` instead.
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "prompts/get",
            "params": {
                "name": "onboard_new_source",
                "arguments": {},  # missing required workbook_path
            },
        }
        response = client.post("/mcp/", json=payload, headers=_MCP_HEADERS)
        assert response.status_code == 200, response.text
        body = _parse_streamable_body(response)

    # JSON-RPC errors live in an ``error`` envelope (not ``result``).
    assert "error" in body, (
        f"missing-required-param expected JSON-RPC error envelope, got: {body!r}"
    )
    err = body["error"]
    # Error message should reference the missing argument so the agent
    # can self-correct without round-tripping through the human.
    err_text = json.dumps(err).lower()
    assert "workbook_path" in err_text or "required" in err_text or "missing" in err_text, (
        f"error envelope did not surface the missing-param cause: {err!r}"
    )

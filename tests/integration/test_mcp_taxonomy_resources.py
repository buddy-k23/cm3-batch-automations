"""MCP ``taxonomy://*`` resource integration tests (EF-S3).

Three acceptance scenarios:

1. ``test_taxonomy_resources_listed`` — handshake + ``resources/list``
   returns both ``taxonomy://violations`` and ``taxonomy://rules``,
   each with the ``application/json`` MIME type and a non-empty
   description. EF-S1 advertised the resources bucket empty; this
   pins the EF-S3 baseline so EF-S4 / EF-S6 stories can be verified
   against a known starting set.

2. ``test_violation_taxonomy_read`` — ``resources/read`` on
   ``taxonomy://violations`` returns JSON containing the 6 canonical
   violation kinds documented in ``ReconciliationReport`` (the
   single source of truth for kinds the SQL-truth comparator can
   emit). Each entry has a non-empty ``description``.

3. ``test_rule_taxonomy_read`` — ``resources/read`` on
   ``taxonomy://rules`` returns JSON with at least one well-known
   entry from each of the three categories (``per-field``,
   ``cross-row``, ``cross-type``), proving the introspection covers
   the full rule surface rather than a single validator.

The tests share the same ``_fresh_app`` reload trick as
``test_mcp_scaffold.py`` so they observe the MCP sub-app with the env
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

_VIOLATIONS_URI = "taxonomy://violations"
_RULES_URI = "taxonomy://rules"


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    The FastAPI app, the MCP server, and the dev-auth middleware all read
    env state at module import time, so we must force re-import to pick up
    monkeypatched env vars in each test.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
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

    Mirrors ``test_mcp_scaffold._parse_streamable_body`` — the
    server may answer as plain JSON or a single-event SSE stream
    depending on the negotiated content type.
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


def _rpc(client: TestClient, method: str, *, request_id: int, params: Dict[str, Any] | None = None):
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


def _extract_resource_text(read_body: Dict[str, Any]) -> str:
    """Pull the first ``text`` field from a ``resources/read`` response.

    The MCP spec wraps a resource read in
    ``result.contents: list[TextResourceContents | BlobResourceContents]``
    — for our JSON taxonomy resources the first entry is a
    ``TextResourceContents`` whose ``text`` field carries the
    JSON-encoded payload.
    """
    assert "result" in read_body, f"resources/read missing 'result': {read_body!r}"
    contents = read_body["result"].get("contents")
    assert isinstance(contents, list) and contents, (
        f"resources/read contents not a non-empty list: {contents!r}"
    )
    first = contents[0]
    text = first.get("text")
    assert isinstance(text, str) and text, (
        f"resources/read first content has no text: {first!r}"
    )
    return text


def test_taxonomy_resources_listed(monkeypatch):
    """Both ``taxonomy://*`` URIs appear in ``resources/list``.

    Each entry must:

    * Have its URI present
    * Advertise ``application/json`` so MCP clients know how to parse
      a subsequent ``resources/read`` without sniffing
    * Carry a non-empty ``description`` so a generic MCP browser shows
      the agent something useful before they fetch the body
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        init = _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        assert "result" in init, init

        list_body = _rpc(client, "resources/list", request_id=2)

    resources = list_body["result"].get("resources", [])
    by_uri = {r["uri"]: r for r in resources}

    assert _VIOLATIONS_URI in by_uri, (
        f"{_VIOLATIONS_URI} missing from resources/list: {sorted(by_uri)}"
    )
    assert _RULES_URI in by_uri, (
        f"{_RULES_URI} missing from resources/list: {sorted(by_uri)}"
    )

    for uri in (_VIOLATIONS_URI, _RULES_URI):
        entry = by_uri[uri]
        assert entry.get("mimeType") == "application/json", (
            f"{uri} mimeType not JSON: {entry!r}"
        )
        description = entry.get("description") or ""
        assert description.strip(), f"{uri} has empty description: {entry!r}"


def test_violation_taxonomy_read(monkeypatch):
    """``resources/read taxonomy://violations`` returns the canonical 6 kinds.

    The 6 kinds are the deterministic set in
    ``scripts/e2e_lib/db_truth_comparator._KIND_ORDER`` — the engine's
    single source of truth for SQL-truth reconciliation violations. If
    a future story adds a new kind, this test will surface the gap by
    flagging the description (the introspection falls back to a
    placeholder if no description is registered in ``src/mcp/taxonomy``).
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        read_body = _rpc(
            client,
            "resources/read",
            request_id=2,
            params={"uri": _VIOLATIONS_URI},
        )

    payload_text = _extract_resource_text(read_body)
    payload: List[Dict[str, Any]] = json.loads(payload_text)

    assert isinstance(payload, list) and payload, (
        f"violations payload not a non-empty list: {payload!r}"
    )

    names = {entry["name"] for entry in payload}
    expected_kinds = {
        "field_mismatch",
        "missing_expected",
        "unexpected_file_row",
        "cardinality_violation",
        "assertion_failed",
        "unknown_record_type",
    }
    assert expected_kinds.issubset(names), (
        f"missing violation kinds: {expected_kinds - names}; "
        f"got: {sorted(names)}"
    )

    # Every entry must have a real description — no empty strings and no
    # placeholder text. The placeholder format is defined in
    # ``src.mcp.taxonomy._describe``; if it shows up here, an engine
    # change introduced a kind without a description in the taxonomy.
    for entry in payload:
        desc = entry.get("description", "")
        assert desc and "no description registered" not in desc, (
            f"violation kind {entry.get('name')!r} has placeholder "
            f"description — add it to src/mcp/taxonomy._VIOLATION_DESCRIPTIONS"
        )


def test_rule_taxonomy_read(monkeypatch):
    """``resources/read taxonomy://rules`` covers per-field, cross-row, cross-type.

    Asserts:

    * The payload is a non-empty JSON list of ``{name, category,
      description}`` entries.
    * At least one well-known check appears in each category. For
      per-field we check ``not_empty`` (the most-used BA operator);
      for cross-row we check ``unique`` (the story's named example);
      for cross-type we check ``header_trailer_count`` (a hallmark
      multi-record-type check).
    * No entry has a placeholder description.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        read_body = _rpc(
            client,
            "resources/read",
            request_id=2,
            params={"uri": _RULES_URI},
        )

    payload_text = _extract_resource_text(read_body)
    payload: List[Dict[str, Any]] = json.loads(payload_text)

    assert isinstance(payload, list) and payload, (
        f"rules payload not a non-empty list: {payload!r}"
    )

    by_category: Dict[str, set] = {}
    for entry in payload:
        category = entry.get("category")
        assert category in {"per-field", "cross-row", "cross-type"}, (
            f"unknown category in entry: {entry!r}"
        )
        by_category.setdefault(category, set()).add(entry["name"])

    assert "not_empty" in by_category.get("per-field", set()), (
        f"per-field category missing 'not_empty': {by_category.get('per-field')}"
    )
    assert "unique" in by_category.get("cross-row", set()), (
        f"cross-row category missing 'unique': {by_category.get('cross-row')}"
    )
    assert "header_trailer_count" in by_category.get("cross-type", set()), (
        f"cross-type category missing 'header_trailer_count': "
        f"{by_category.get('cross-type')}"
    )

    for entry in payload:
        desc = entry.get("description", "")
        assert desc and "no description registered" not in desc, (
            f"rule {entry.get('name')!r} ({entry.get('category')!r}) has "
            "placeholder description — add it to the relevant table in "
            "src/mcp/taxonomy"
        )

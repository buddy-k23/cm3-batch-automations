"""MCP ``formats://supported`` resource integration tests (S7-3, #381).

Four acceptance scenarios cover the resource registered by
:mod:`src.mcp.resources.formats` and wired into
:func:`src.mcp.server.build_mcp_server`:

1. ``test_formats_supported_top_level_shape`` — ``resources/read`` on
   ``formats://supported`` returns a dict with both
   ``supported_today`` and ``planned`` keys, each a non-empty list
   (story AC #1).

2. ``test_supported_today_template_uris_resolve`` — for every entry in
   ``supported_today`` whose ``template`` field is not ``None``, the
   URI resolves through the sibling ``templates://etl/<shape>``
   resource landed in S7-2 (story AC #2).

3. ``test_planned_entries_link_to_real_github_issues`` — every entry
   in ``planned`` has an ``issue`` URL matching the
   ``https://github.com/buddy-k23/valdo/issues/<digits>`` shape
   (story AC #3).

4. ``test_supported_today_entry_shape`` — guard test that pins the
   per-entry contract (``format`` is a string, ``since`` is a string,
   ``template`` is a ``templates://etl/...`` URI or ``None``). Prevents
   silent drift in the module-level constants (story AC #4).

All four tests share the ``_fresh_app`` reload trick used in
``test_mcp_template_resources.py`` so the MCP sub-app is rebuilt under
``VALDO_MCP_AUTH=dev`` per test.
"""

from __future__ import annotations

import importlib
import json
import re
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

# Resource URI under test. Kept literal so a future rename surfaces as
# a failing test rather than a silent breakage.
_FORMATS_URI = "formats://supported"

# Templates resource URI prefix from S7-2. Used to convert a
# ``templates://etl/<shape>`` entry into the ``shape`` we cross-check
# against the ``templates://etl/list`` payload.
_TEMPLATES_URI_PREFIX = "templates://etl/"
_TEMPLATES_LIST_URI = "templates://etl/list"

# GitHub issue URL contract for ``planned`` entries. Pinned as a regex
# so a placeholder (or a typo'd issue number) cannot land. The repo
# slug is hardcoded to ``buddy-k23/valdo`` because S7-3 explicitly
# requires real GitHub issue URLs in that organisation/repo.
_GITHUB_ISSUE_RE = re.compile(
    r"^https://github\.com/buddy-k23/valdo/issues/\d+$"
)


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    Mirrors the helper in ``test_mcp_template_resources.py``. We force a
    re-import so the MCP sub-app picks up monkeypatched env state
    (``VALDO_MCP_AUTH``) at construction time.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.resources.formats",
        "src.mcp.resources.etl_templates",
        "src.mcp.resources",
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

    Matches the fixture in ``test_mcp_scaffold.py`` /
    ``test_mcp_template_resources.py``; see those files' docstrings for
    context on why the FastAPI auth layer requires this at import time.
    """
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "test-only-key-not-for-production-do-not-reuse",
    )


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
    — for our JSON resource the first entry is a
    ``TextResourceContents`` whose ``text`` field carries the body.
    """
    assert "result" in read_body, (
        f"resources/read missing 'result': {read_body!r}"
    )
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


def _read_formats_payload(client: TestClient) -> Dict[str, List[Dict[str, Any]]]:
    """Issue ``initialize`` + ``resources/read`` on ``formats://supported``.

    Returns the parsed JSON payload so each test can assert on it
    directly. Kept as a helper because three of the four scenarios
    need identical setup.
    """
    _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
    read_body = _rpc(
        client,
        "resources/read",
        request_id=2,
        params={"uri": _FORMATS_URI},
    )
    payload_text = _extract_resource_text(read_body)
    return json.loads(payload_text)


def test_formats_supported_top_level_shape(monkeypatch):
    """``formats://supported`` returns both arrays, each non-empty.

    Story AC #1: ``Resource registered; integration test asserts both
    arrays present``. We assert the top-level dict contains exactly
    the two expected keys (no extras) and that each list has at least
    one entry — the empty-list edge case would indicate a silent
    constants-file truncation.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        payload = _read_formats_payload(client)

    assert isinstance(payload, dict), (
        f"formats://supported root must be a dict; got: {type(payload).__name__}"
    )
    assert set(payload.keys()) == {"supported_today", "planned"}, (
        f"formats://supported keys drifted: {sorted(payload.keys())!r}"
    )

    supported = payload["supported_today"]
    planned = payload["planned"]
    assert isinstance(supported, list) and supported, (
        f"supported_today must be a non-empty list; got: {supported!r}"
    )
    assert isinstance(planned, list) and planned, (
        f"planned must be a non-empty list; got: {planned!r}"
    )


def test_supported_today_template_uris_resolve(monkeypatch):
    """Every non-null ``template`` URI resolves through S7-2.

    Story AC #2: ``Each supported_today entry has a working template
    URI (or null if the template hasn't been built yet)``. We cross-
    check the ``template`` field against the ``templates://etl/list``
    catalogue produced by :mod:`src.mcp.resources.etl_templates` — if
    an entry references a shape that is not discoverable, this test
    fails before an agent hits a 404.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        payload = _read_formats_payload(client)
        # Pull the canonical shape list once so the cross-check is a
        # set membership instead of N round-trips.
        list_body = _rpc(
            client,
            "resources/read",
            request_id=3,
            params={"uri": _TEMPLATES_LIST_URI},
        )
        list_text = _extract_resource_text(list_body)
        templates_catalogue = json.loads(list_text)

    known_shapes = {entry["shape"] for entry in templates_catalogue}
    assert known_shapes, (
        "templates://etl/list returned an empty catalogue; S7-2 must "
        "be wired before S7-3 can validate template URIs."
    )

    for entry in payload["supported_today"]:
        template = entry.get("template")
        # ``None`` is an explicitly-allowed state (e.g. pipe_delimited
        # has no public template yet). Skip those.
        if template is None:
            continue
        assert isinstance(template, str) and template.startswith(
            _TEMPLATES_URI_PREFIX
        ), (
            f"supported_today entry for {entry.get('format')!r} has a "
            f"non-conforming template URI: {template!r} — must start "
            f"with {_TEMPLATES_URI_PREFIX!r} or be null."
        )
        shape = template[len(_TEMPLATES_URI_PREFIX):]
        assert shape in known_shapes, (
            f"supported_today entry for {entry.get('format')!r} "
            f"references unknown template shape {shape!r}; "
            f"known shapes: {sorted(known_shapes)!r}"
        )


def test_planned_entries_link_to_real_github_issues(monkeypatch):
    """Every ``planned`` entry's ``issue`` URL matches the GH issue shape.

    Story AC #3: ``Each planned entry links to a real GitHub issue``.
    We pin the URL shape with a regex
    (``https://github.com/buddy-k23/valdo/issues/<digits>``) so a
    placeholder cannot ship. We also assert ``format`` is a non-empty
    string — a placeholder format name is just as bad as a placeholder
    URL.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        payload = _read_formats_payload(client)

    for entry in payload["planned"]:
        fmt = entry.get("format")
        assert isinstance(fmt, str) and fmt, (
            f"planned entry has missing/empty format: {entry!r}"
        )
        issue_url = entry.get("issue")
        assert isinstance(issue_url, str) and issue_url, (
            f"planned entry {fmt!r} has missing/empty issue URL: {entry!r}"
        )
        assert _GITHUB_ISSUE_RE.match(issue_url), (
            f"planned entry {fmt!r} issue URL does not match the "
            f"GitHub issue shape: {issue_url!r}"
        )


def test_supported_today_entry_shape(monkeypatch):
    """Per-entry contract guard for ``supported_today``.

    Story AC #4: ``Source-of-truth for the lists is module-level
    constants ... easy to update when new formats land``. To keep
    those constants honest, we pin the per-entry shape so a careless
    edit (e.g. dropping the ``since`` field, or accidentally setting
    ``template`` to a non-string non-None value) surfaces as a failing
    test rather than silently breaking an agent.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        payload = _read_formats_payload(client)

    for entry in payload["supported_today"]:
        assert set(entry.keys()) == {"format", "since", "template"}, (
            f"supported_today entry has unexpected keys "
            f"{sorted(entry.keys())!r} (expected exactly "
            f"['format', 'since', 'template']): {entry!r}"
        )
        fmt = entry["format"]
        since = entry["since"]
        template = entry["template"]
        assert isinstance(fmt, str) and fmt, (
            f"supported_today entry has missing/empty format: {entry!r}"
        )
        assert isinstance(since, str) and since, (
            f"supported_today entry {fmt!r} has missing/empty since: {entry!r}"
        )
        assert template is None or (
            isinstance(template, str) and template.startswith(_TEMPLATES_URI_PREFIX)
        ), (
            f"supported_today entry {fmt!r} template must be null or a "
            f"templates://etl/... URI; got: {template!r}"
        )

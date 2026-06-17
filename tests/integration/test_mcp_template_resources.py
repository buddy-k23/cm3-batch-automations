"""MCP ``templates://etl/*`` resource integration tests (S7-2, #380).

Three acceptance scenarios cover the resources registered by
:mod:`src.mcp.resources.etl_templates` and wired into
:func:`src.mcp.server.build_mcp_server`:

1. ``test_etl_templates_list_resource`` — ``resources/read`` on
   ``templates://etl/list`` returns the auto-discovered catalogue. We
   assert the three currently-committed shapes
   (``csv_file_comparison``, ``fixed_width_single_record``,
   ``db_to_file_reconciliation``) are all present with non-empty
   descriptions. The assertion is ``>= 3``, not ``== 3``, so adding a
   new template never silently breaks this test.

2. ``test_etl_template_yaml_resource`` — ``resources/read`` on
   ``templates://etl/csv_file_comparison`` returns the YAML body
   verbatim. We re-read the file directly and compare for exact
   equality, pinning the "no transformation" contract called out in
   the module docstring of
   :mod:`src.mcp.resources.etl_templates`.

3. ``test_etl_template_sample_manifest_resource`` — ``resources/read``
   on ``templates://etl/csv_file_comparison/sample`` returns a manifest
   whose ``sample_dir`` field is the repo-root-relative sample path
   and whose ``files`` list contains every committed sample file. We
   assert ``left.csv``, ``right.csv``, ``mapping.json``, and
   ``expected_report.json`` are in the file set with positive
   ``size_bytes`` and non-null previews (they are UTF-8 text).

All three tests share the ``_fresh_app`` reload trick used in
``test_mcp_taxonomy_resources.py`` so the MCP sub-app is rebuilt under
``VALDO_MCP_AUTH=dev`` per test.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
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

# Resource URIs under test. Kept literal so a future rename surfaces as
# a failing test rather than a silent breakage.
_LIST_URI = "templates://etl/list"
_TEMPLATE_URI = "templates://etl/csv_file_comparison"
_SAMPLE_URI = "templates://etl/csv_file_comparison/sample"

# Repo-root path to the template under test. Drives the verbatim-content
# equality check in ``test_etl_template_yaml_resource``.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CSV_TEMPLATE_PATH = _REPO_ROOT / "templates" / "etl" / "csv_file_comparison.yml"
_CSV_SAMPLE_DIR = _REPO_ROOT / "templates" / "etl" / "csv_file_comparison_sample"


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    Mirrors the helper in ``test_mcp_taxonomy_resources.py``. We force a
    re-import so the MCP sub-app picks up monkeypatched env state
    (``VALDO_MCP_AUTH``) at construction time.
    """
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
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
    ``test_mcp_taxonomy_resources.py``; see those files' docstrings for
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
    — for our text and JSON resources the first entry is a
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


def test_etl_templates_list_resource(monkeypatch):
    """``templates://etl/list`` lists at least the three committed shapes.

    The acceptance criterion in the story is "at least 3 templates"; we
    encode that as ``>= 3`` rather than ``== 3`` so adding a future
    shape (S7-3 etc.) does not silently break this test.

    Each discovered entry must have a non-empty ``description`` so an
    agent browsing the catalogue gets useful context without fetching
    the full YAML body.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        read_body = _rpc(
            client,
            "resources/read",
            request_id=2,
            params={"uri": _LIST_URI},
        )

    payload_text = _extract_resource_text(read_body)
    payload: List[Dict[str, str]] = json.loads(payload_text)

    assert isinstance(payload, list) and len(payload) >= 3, (
        f"templates://etl/list should expose at least 3 shapes; got: {payload!r}"
    )

    shapes = {entry["shape"] for entry in payload}
    expected = {
        "csv_file_comparison",
        "fixed_width_single_record",
        "db_to_file_reconciliation",
        # JSON (NDJSON) single-record template (ADR 0018, S19-2, #395). Picked
        # up by templates://etl auto-discovery with zero MCP code change — its
        # presence here is the acceptance criterion.
        "json_single_record",
    }
    assert expected.issubset(shapes), (
        f"templates://etl/list missing committed shapes: "
        f"{expected - shapes}; saw: {sorted(shapes)}"
    )

    for entry in payload:
        description = entry.get("description", "")
        assert description and description.strip(), (
            f"shape {entry.get('shape')!r} has empty description: {entry!r}"
        )


def test_etl_template_yaml_resource(monkeypatch):
    """``templates://etl/csv_file_comparison`` returns the YAML verbatim.

    Pins the "no transformation" contract — the MCP body must equal
    ``csv_file_comparison.yml`` on disk byte-for-byte (after the
    transport's UTF-8 decode). Comments and ``<FILL_IN_*>`` placeholders
    must survive so the agent can paste the body into a working spec.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        read_body = _rpc(
            client,
            "resources/read",
            request_id=2,
            params={"uri": _TEMPLATE_URI},
        )

    body_text = _extract_resource_text(read_body)
    on_disk = _CSV_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert body_text == on_disk, (
        "templates://etl/csv_file_comparison body diverged from disk. "
        "The resource must return the YAML verbatim — no formatting, "
        "no template expansion."
    )


def test_etl_template_sample_manifest_resource(monkeypatch):
    """``templates://etl/csv_file_comparison/sample`` returns a manifest.

    Asserts the manifest shape (``sample_dir`` + ``files: [{path,
    size_bytes, preview}]``) plus that every committed sample file is
    enumerated. The CSV sample directory ships four files; we pin them
    all rather than spot-checking one so a future fixture rename or
    addition shows up here.
    """
    monkeypatch.setenv("VALDO_MCP_AUTH", "dev")
    app = _fresh_app()

    with TestClient(app) as client:
        _rpc(client, "initialize", request_id=1, params=_INIT_PAYLOAD["params"])
        read_body = _rpc(
            client,
            "resources/read",
            request_id=2,
            params={"uri": _SAMPLE_URI},
        )

    manifest_text = _extract_resource_text(read_body)
    manifest = json.loads(manifest_text)

    assert isinstance(manifest, dict), manifest
    assert manifest.get("sample_dir") == "templates/etl/csv_file_comparison_sample", (
        f"unexpected sample_dir: {manifest.get('sample_dir')!r}"
    )

    files = manifest.get("files")
    assert isinstance(files, list) and files, (
        f"manifest 'files' not a non-empty list: {files!r}"
    )

    by_path = {entry["path"]: entry for entry in files}
    committed = {p.name for p in _CSV_SAMPLE_DIR.iterdir() if p.is_file()}
    assert committed.issubset(set(by_path)), (
        f"manifest missing committed sample files: "
        f"{committed - set(by_path)}; manifest had: {sorted(by_path)}"
    )

    # Every text fixture in this sample directory is UTF-8, so each
    # entry must carry a non-empty preview and a positive size. The
    # preview cap is 200 chars per the module-level convention.
    for path, entry in by_path.items():
        assert entry["size_bytes"] > 0, (
            f"manifest entry {path!r} has non-positive size: {entry!r}"
        )
        preview = entry.get("preview")
        assert isinstance(preview, str) and preview, (
            f"manifest entry {path!r} preview missing/empty: {entry!r}"
        )
        assert len(preview) <= 200, (
            f"manifest entry {path!r} preview exceeded 200 chars: "
            f"len={len(preview)}"
        )

"""MCP segment of the #407 cross-dialect reconcile demo.

Drives the REAL ``reconcile_mapping`` MCP tool over the MCP Streamable-HTTP
transport (JSON-RPC ``initialize`` handshake + ``tools/call``) against the
seeded SQLite demo DB, then writes the full request + JSON verdict to
``artifacts/mcp_reconcile_transcript.json`` (machine) and ``.txt`` (human).

This reuses the exact transport mechanism from
``tests/integration/test_mcp_reconcile_tool.py`` so the demo shows a genuine
MCP request/response cycle — NOT a service shortcut.

The MCP sub-app is gated by ``VALDO_MCP_AUTH=dev`` (the documented dev-mode
opt-in) and the reconcile service reads ``DB_ADAPTER=sqlite`` + ``DB_PATH``,
all of which we set on the environment before importing the app.

Run directly (after seeding):

    python demo/reconcile_e2e/run_mcp_demo.py
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.reconcile_e2e.seed import seed_db  # noqa: E402

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"

_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}
_INIT_PARAMS = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "reconcile-demo", "version": "1.0"},
}


def _fresh_app():
    """Reload MCP + API modules so they pick up the env we just set."""
    for mod_name in [
        "src.api.main",
        "src.mcp.server",
        "src.mcp.reconcile_tools",
        "src.mcp.compare_tools",
        "src.mcp.action_tools",
        "src.mcp.tools",
        "src.mcp.taxonomy",
        "src.mcp",
    ]:
        sys.modules.pop(mod_name, None)
    return importlib.import_module("src.api.main").app


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


def _structured_or_text(result: Dict[str, Any]) -> Any:
    structured = result.get("structuredContent")
    if structured is not None:
        if isinstance(structured, dict) and set(structured.keys()) == {"result"}:
            return structured["result"]
        return structured
    contents = result.get("content") or []
    assert contents, f"CallToolResult has no content: {result!r}"
    text = contents[0].get("text")
    return json.loads(text)


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    db = seed_db.db_path()
    mapping = seed_db.mapping_path()
    if not db.exists():
        print(f"[mcp] seeding (DB not found at {db})")
        seed_db.main()

    # Configure the MCP dev-mode gate + the SQLite adapter BEFORE app import.
    os.environ["VALDO_MCP_AUTH"] = "dev"
    os.environ["DB_ADAPTER"] = "sqlite"
    os.environ["DB_PATH"] = str(db)
    os.environ.setdefault(
        "VALDO_SESSION_SIGNING_KEY",
        "demo-only-key-not-for-production-do-not-reuse",
    )

    from fastapi.testclient import TestClient  # imported after env is set

    tool_arguments = {"mapping": mapping.stem, "table": seed_db.TABLE_NAME}
    request_envelope = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "reconcile_mapping", "arguments": tool_arguments},
    }

    app = _fresh_app()
    with TestClient(app) as client:
        # 1) MCP handshake
        init = _rpc(client, "initialize", request_id=1, params=_INIT_PARAMS)
        # 2) tools/list (proves the tool is genuinely advertised)
        tools = _rpc(client, "tools/list", request_id=99)
        tool_names = [t["name"] for t in (tools.get("result") or {}).get("tools", [])]
        # 3) the real reconcile_mapping tools/call
        call = _rpc(
            client,
            "tools/call",
            request_id=2,
            params={"name": "reconcile_mapping", "arguments": tool_arguments},
        )

    verdict = _structured_or_text(call.get("result") or {})

    transcript = {
        "demo": "reconcile_e2e / MCP segment (#407)",
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "transport": "MCP Streamable-HTTP (JSON-RPC) via /mcp/",
        "env": {
            "VALDO_MCP_AUTH": os.environ["VALDO_MCP_AUTH"],
            "DB_ADAPTER": os.environ["DB_ADAPTER"],
            "DB_PATH": os.environ["DB_PATH"],
        },
        "tools_advertised": sorted(tool_names),
        "request": request_envelope,
        "raw_tool_response": call,
        "verdict": verdict,
    }

    json_path = ARTIFACTS_DIR / "mcp_reconcile_transcript.json"
    json_path.write_text(json.dumps(transcript, indent=2), encoding="utf-8")

    def _bullets(items: list) -> list:
        return [f"  - {x}" for x in items] if items else ["  (none)"]

    # Human-readable companion.
    lines = [
        "=" * 72,
        "Valdo #407 cross-dialect reconcile — MCP transcript",
        "=" * 72,
        f"captured_at : {transcript['captured_at']}",
        f"transport   : {transcript['transport']}",
        f"DB_ADAPTER  : {transcript['env']['DB_ADAPTER']}",
        f"DB_PATH     : {transcript['env']['DB_PATH']}",
        f"tool present: {'reconcile_mapping' in tool_names}",
        "",
        "--- REQUEST (JSON-RPC tools/call) ---",
        json.dumps(request_envelope, indent=2),
        "",
        "--- VERDICT ---",
        f"status            : {verdict.get('status')}",
        f"valid             : {verdict.get('valid')}",
        f"table             : {verdict.get('table')}",
        f"db_adapter        : {verdict.get('db_adapter')}",
        f"summary           : {json.dumps(verdict.get('summary'))}",
        "",
        "mismatches:",
        *_bullets(verdict.get("mismatches", [])),
        "",
        "advisories:",
        *_bullets(verdict.get("advisories", [])),
        "",
        "errors:",
        *_bullets(verdict.get("errors", [])),
        "=" * 72,
    ]
    txt_path = ARTIFACTS_DIR / "mcp_reconcile_transcript.txt"
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Print to stdout for live inspection.
    print("\n".join(lines))
    print(f"\n[mcp] wrote {json_path}")
    print(f"[mcp] wrote {txt_path}")

    status_ok = verdict.get("status") == "mismatch"
    print(f"[mcp] expected status='mismatch' -> got '{verdict.get('status')}' "
          f"({'OK' if status_ok else 'UNEXPECTED'})")
    return 0 if status_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""MCP agentic E2E walkthrough — program completion test (EF-S8).

This integration test is the final story of the 5-sprint Valdo
workbook-driven onboarding + agentic MCP surface program. It scripts a
mock MCP client through the same tool-call sequence a properly-instructed
agent (Claude Desktop, mcp-cli, etc.) would issue when handed the
``onboard_new_source`` prompt and tasked with onboarding a brand-new
source from an Excel workbook.

No real LLM is involved. The test IS the agent in this scenario — it
makes the calls a correctly-prompted agent would make. The point is to
prove that the full MCP surface (auth + prompts + onboarding tools +
action tools) allows a scripted tool sequence to complete the BA
onboarding flow without intervention from the production code path.

External services that would normally execute under each step are
either mocked (``run_validate_service``) or stubbed by virtue of the
EF-S4 in-process run registry being deterministic. No real LDAPS, no
real Oracle, no real ``gh`` CLI is exercised.

Walkthrough — 10 steps:

1. **Authenticate.** Mint a signed bearer token via
   :func:`src.mcp.auth.mint_token`; attach to the MCP client; assert
   the JSON-RPC ``initialize`` handshake succeeds (HTTP 200).
2. **Fetch the ``onboard_new_source`` prompt.** ``prompts/get`` with
   ``workbook_path="templates/SHAW_onboarding.xlsx"``, ``source_code="SHAW"``;
   assert the returned messages reference both
   ``upload_workbook_as_spec`` and ``onboard_source_dry_run`` by their
   exact registered tool names.
3. **Stage the workbook.** Call ``upload_workbook_as_spec`` with the
   SHAW workbook path; assert a ``sandbox_path`` is returned and the
   staged file exists on disk under the per-test sandbox root.
4. **Dry-run.** Call ``onboard_source_dry_run`` with the sandbox path;
   assert ``summary`` + ``would_write`` returned. Then call the
   ``/api/v2/onboarding/preview`` HTTP endpoint with the same workbook
   so we can also assert EE-S2 drift counts — the MCP dry-run tool
   itself does not surface drift; the preview API does.
5. **Inspect the diff.** Pick the ``changed`` artefact from the
   preview drift report. Assert it is
   ``config/rules/SHAW_TRANERT_CUS_rules.json`` (the R028B carve-out;
   see Sprint 3 ED-S1 / Sprint 4 ED-S4 history).
6. **Validate (mocked).** Mock
   :func:`src.services.validate_service.run_validate_service` to return
   a synthetic success result. Call ``validate_file`` for a tiny
   fixture; assert a string ``run_id`` is returned.
7. **Poll status.** Call ``get_run_status(run_id)``; assert the status
   is one of the canonical lifecycle values and equals ``"completed"``
   (the stub completes synchronously).
8. **Fetch the diagnose prompt.** Fetch
   ``diagnose_validation_failure`` for the same ``run_id`` to exercise
   the prompt even though step 6 was a success. Assert it references
   ``get_run_status``, ``get_violations``, and ``taxonomy://violations``
   by their exact registered identifiers.
9. **Fetch violations (empty).** Call ``get_violations`` for the
   success run; assert the violations list is empty.
10. **Disconnect cleanly.** Exit the ``with TestClient(...)`` block.
    Asserting no zombie state means the test must return without
    raising from the manager's ``__exit__``.

Runtime budget: under 10 seconds. The single end-to-end test reuses
one FastAPI process across all 10 steps so we pay app-construction
cost once.

Closes the 5-sprint program (Sprint 1: Pydantic defaults; Sprint 2:
workbook + CLI; Sprint 3: drift fix + Move 4 starts; Sprint 4: Move 4
closes + MCP action/onboarding tools + UI scaffold; Sprint 5: UI tree
+ drift + commit + MCP prompts + auth bridge + this walkthrough).
"""

from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Module-level constants — shared with the helper trio below
# ---------------------------------------------------------------------------


# JSON-RPC initialize payload mirrors the other MCP integration suites
# (``test_mcp_auth.py``, ``test_mcp_action_tools.py``,
# ``test_mcp_onboarding_tools.py``). The 2025-06-18 protocol version is
# the contract pinned by the EF-S1 acceptance criteria.
_INIT_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {
            "name": "valdo-agentic-walkthrough-test",
            "version": "1.0",
        },
    },
}

# Streamable HTTP transport requires clients to advertise that they can
# receive either a plain JSON body or a single-event SSE frame.
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

# Signing key used to mint + verify the bearer token throughout the
# walkthrough. Held module-level so the helpers can reach it without a
# fixture round-trip.
_WALKTHROUGH_SIGNING_KEY = "ef-s8-walkthrough-key-do-not-reuse-in-production"

# API key used for the FastAPI auth chain on the ``/api/v2/onboarding/preview``
# endpoint hit in step 4. The MCP sub-app accepts the bearer token
# minted in step 1; the parent FastAPI surface needs the X-API-Key.
_WALKTHROUGH_API_KEY = "ef-s8-walkthrough-api-key"

# Canonical SHAW onboarding workbook bundled with the repo. The R028B
# carve-out diff lives in this file's TRANERT_CUS rules sheet — step 5
# asserts that exact relationship.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHAW_WORKBOOK = _REPO_ROOT / "templates" / "SHAW_onboarding.xlsx"

# The single ``changed`` artefact the SHAW workbook is known to produce
# vs the committed state. This is the R028B carve-out and is the
# program's exit-state assertion: if it ever drifts to a different
# path, either the workbook or the committed config has moved out from
# under the program and the walkthrough catches it on the next run.
_EXPECTED_CHANGED_ARTEFACT = "config/rules/SHAW_TRANERT_CUS_rules.json"


# ---------------------------------------------------------------------------
# Helper functions (per AC #3)
# ---------------------------------------------------------------------------


def _fresh_app():
    """Reload ``src.api.main`` and return the freshly built FastAPI app.

    Mirrors the pattern used by every other MCP integration suite in
    this repo — the FastAPI app, MCP server, auth middleware, and tool
    registries all read env state at module import time, so we must
    force a re-import per test invocation. The walkthrough only calls
    this once because the entire 10-step flow runs against one app
    instance.
    """
    for mod_name in [
        "src.api.main",
        "src.api.routers.mcp_auth",
        "src.api.routers.onboarding",
        "src.mcp.server",
        "src.mcp.auth",
        "src.mcp.action_tools",
        "src.mcp.onboarding_tools",
        "src.mcp.prompts",
        "src.mcp.tools",
        "src.mcp.taxonomy",
        "src.mcp",
    ]:
        if mod_name in sys.modules:
            del sys.modules[mod_name]
    main_module = importlib.import_module("src.api.main")
    return main_module.app


def _mint_test_token(user: str = "alice", role: str = "admin") -> str:
    """Mint a valid bearer token for the walkthrough's MCP client.

    Encapsulates :func:`src.mcp.auth.mint_token` + the URL-safe-base64
    encoding step so callers can simply hand the returned string to
    :func:`_authed_client`. The signing key env var
    (``VALDO_MCP_TOKEN_SIGNING_KEY``) must already be set — the fixture
    ``_walkthrough_env`` arranges this before the test body runs.

    Args:
        user: Short user identifier baked into the token payload.
            Defaults to ``"alice"`` to match the BA persona in the
            EF-S6 prompts.
        role: Valdo role asserted in the token. Defaults to ``"admin"``
            so the walkthrough exercises a fully-privileged caller —
            the read/action/onboarding tool registry treats role as
            advisory at the MCP edge today, but pinning ``admin``
            insulates the test from a future RBAC tightening.

    Returns:
        The URL-safe-base64-encoded bearer string ready to drop into
        an ``Authorization: Bearer <token>`` header.
    """
    from src.mcp import auth as mcp_auth

    payload = mcp_auth.mint_token(
        user=user,
        principal_dn=f"CN={user},OU=Users,DC=bank,DC=internal",
        role=role,
        ttl_hours=1,
    )
    return mcp_auth.encode_bearer(payload)


def _authed_client(app, token: str) -> TestClient:
    """Build a ``TestClient`` with the walkthrough's bearer token attached.

    The ``Authorization`` header is set on the client's session so every
    subsequent request inherits it automatically — the helper makes the
    test body read like a sequence of pure MCP calls, free of repeated
    auth boilerplate.

    Args:
        app: The freshly built FastAPI app from :func:`_fresh_app`.
        token: URL-safe-base64-encoded bearer payload as returned by
            :func:`_mint_test_token`.

    Returns:
        A :class:`fastapi.testclient.TestClient` that carries the
        bearer header by default. The caller still owns the ``with``
        lifecycle.
    """
    client = TestClient(app)
    # We deliberately do NOT set Content-Type on the session because
    # httpx auto-sets it per request (application/json for ``json=...``,
    # multipart/form-data with a boundary for ``files=...``). Setting
    # a session-level Content-Type would force every call to look like
    # JSON and break the multipart upload in step 4.
    client.headers.update(
        {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json, text/event-stream",
        }
    )
    return client


def _assert_prompt_messages_reference(
    text: str, *tool_names: str
) -> None:
    """Assert the concatenated prompt body mentions every named tool.

    Substring search (case-sensitive) so a tool rename in a future
    sprint fails this test fast and forces a coordinated prompt update.

    Args:
        text: The joined message bodies returned by
            :func:`_join_prompt_text`.
        *tool_names: The exact registered tool / resource identifiers
            that MUST appear verbatim in the prompt body.

    Raises:
        AssertionError: If any of *tool_names* is missing from *text*.
    """
    missing = [name for name in tool_names if name not in text]
    assert not missing, (
        f"Prompt body missing references to: {missing!r}. Body: {text!r}"
    )


# ---------------------------------------------------------------------------
# JSON-RPC plumbing (borrowed from the other MCP suites)
# ---------------------------------------------------------------------------


def _parse_streamable_body(response) -> Dict[str, Any]:
    """Decode an MCP Streamable-HTTP response body to a JSON dict.

    The FastMCP transport replies either as plain JSON (because we set
    ``json_response=True`` in the FastMCP constructor) or as a single
    SSE ``data:`` line. We accept either shape and unwrap the SSE form
    when present.
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
    """Send a single JSON-RPC POST to ``/mcp/`` and return the parsed body."""
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    response = client.post("/mcp/", json=payload)
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

    FastMCP surfaces tool return values either via ``structuredContent``
    (preferred — the agent sees a parsed dict) or via a ``content`` list
    of text blocks. The walkthrough's downstream assertions need a
    Python dict either way; this helper hides the SDK shape from the
    test body.
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


def _join_prompt_text(get_result: Dict[str, Any]) -> str:
    """Concatenate every text-block from a ``prompts/get`` response.

    Mirrors the helper in ``test_mcp_prompts.py``. Each prompt message
    is a ``UserMessage`` with a single ``TextContent`` block; this
    helper joins them on newline so the caller can run substring
    assertions.
    """
    assert "result" in get_result, (
        f"prompts/get missing 'result': {get_result!r}"
    )
    messages = get_result["result"].get("messages")
    assert isinstance(messages, list) and messages, (
        f"prompts/get messages not a non-empty list: {messages!r}"
    )
    parts: List[str] = []
    for msg in messages:
        content = msg.get("content")
        assert isinstance(content, dict), f"content not a dict: {msg!r}"
        text = content.get("text")
        assert isinstance(text, str) and text, (
            f"prompt content has no text: {content!r}"
        )
        parts.append(text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def _walkthrough_env(monkeypatch, tmp_path: Path) -> Path:
    """Stage the env state the walkthrough needs.

    1. Pin the MCP token signing key so :func:`_mint_test_token`
       produces verifiable tokens.
    2. Allow-list ``testserver`` so the FastMCP transport's
       DNS-rebinding protection lets the TestClient through (production
       leaves the allow-list empty — the parent app's reverse proxy is
       responsible for host filtering there).
    3. Provide a session-signing key so the FastAPI auth layer's
       import-time read succeeds (mirrors ``test_mcp_auth.py``).
    4. Redirect ``VALDO_MCP_SANDBOX_ROOT`` into a per-test tmp dir so
       the workbook-staging side effect never lands in the developer's
       home directory.
    5. Clear ``VALDO_MCP_AUTH`` so the production auth chain (not the
       dev pass-through) is what the walkthrough exercises.
    6. Pin ``API_KEYS`` to a known value. The MCP middleware still
       accepts the bearer token (proving the token path); the API key
       is what the parent FastAPI auth chain requires for the EE-S2
       ``/api/v2/onboarding/preview`` endpoint hit in step 4.

    Returns:
        The per-test sandbox root directory (so the test body can
        assert the staged workbook lives under it).
    """
    monkeypatch.setenv("VALDO_MCP_TOKEN_SIGNING_KEY", _WALKTHROUGH_SIGNING_KEY)
    monkeypatch.setenv("VALDO_MCP_ALLOWED_HOSTS", "testserver,localhost")
    monkeypatch.setenv(
        "VALDO_SESSION_SIGNING_KEY",
        "ef-s8-session-key-do-not-reuse-in-production",
    )
    sandbox = tmp_path / "mcp_sandbox"
    monkeypatch.setenv("VALDO_MCP_SANDBOX_ROOT", str(sandbox))
    monkeypatch.delenv("VALDO_MCP_AUTH", raising=False)
    monkeypatch.setenv("API_KEYS", _WALKTHROUGH_API_KEY + ":admin")
    return sandbox


@pytest.fixture
def _mocked_validate_service(monkeypatch) -> Dict[str, Any]:
    """Mock :func:`src.services.validate_service.run_validate_service`.

    Returns a captured-kwargs dict so the test body can confirm the
    MCP adapter forwarded the validate request with the right args.
    The synthetic success result mirrors the shape ``validate_service``
    returns on a clean run (no errors, no warnings, 1 row).

    The mock is installed BEFORE :func:`_fresh_app` runs so the
    re-import picks up the patched symbol. The patch target is the
    source module (``src.services.validate_service``) not the MCP
    adapter, because the adapter imports lazily.
    """
    import src.services.validate_service as svc

    captured: Dict[str, Any] = {}

    def _stub(**kwargs):
        captured.update(kwargs)
        return {
            "valid": True,
            "errors": [],
            "warnings": [],
            "info": [],
            "total_rows": 1,
            "error_count": 0,
            "warning_count": 0,
            "elapsed_seconds": 0.01,
        }

    monkeypatch.setattr(svc, "run_validate_service", _stub)
    return captured


@pytest.fixture
def _validation_fixture_file() -> Iterable[str]:
    """Create a tiny non-empty fixture the validate tool can point at.

    The contents don't matter because :func:`_mocked_validate_service`
    short-circuits the engine. We only need the path to exist so the
    MCP adapter's defensive ``file_path.exists()`` check passes.
    """
    fd, path = tempfile.mkstemp(prefix="valdo_ef_s8_", suffix=".dat")
    os.write(fd, b"walkthrough fixture row\n")
    os.close(fd)
    try:
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# The headline test
# ---------------------------------------------------------------------------


def test_agentic_onboarding_walkthrough_end_to_end(
    _walkthrough_env: Path,
    _mocked_validate_service: Dict[str, Any],
    _validation_fixture_file: str,
):
    """Drive the MCP server through the full BA onboarding flow.

    This is the EF-S8 program-completion test — the 10-step walkthrough
    enumerated in this module's docstring is executed in order against
    a single FastAPI app instance, with a single ``TestClient`` session
    carrying the bearer token from step 1 onward. The test fails if any
    step degrades, and the degradation is reported with the step number
    so the operator can correlate against the story body.
    """
    sandbox_root = _walkthrough_env
    captured_validate_kwargs = _mocked_validate_service
    fixture_file = _validation_fixture_file

    # Sanity-check the bundled workbook exists. If it's missing, the
    # whole walkthrough is meaningless — fail fast with a helpful
    # message rather than letting a cryptic upload error bury the
    # cause.
    assert _SHAW_WORKBOOK.is_file(), (
        f"Expected canonical SHAW workbook at {_SHAW_WORKBOOK}; "
        "EF-S8 cannot run without the bundled fixture."
    )

    # ------------------------------------------------------------------
    # Step 1 — Authenticate.
    #
    # Mint a signed bearer token; build a TestClient with the
    # Authorization header attached; issue the JSON-RPC ``initialize``
    # handshake. A 200 response means the auth middleware accepted the
    # token AND the FastMCP transport negotiated the session.
    # ------------------------------------------------------------------
    app = _fresh_app()
    token = _mint_test_token(user="alice", role="admin")

    with _authed_client(app, token) as client:
        init_body = _rpc(
            client,
            "initialize",
            request_id=1,
            params=_INIT_PAYLOAD["params"],
        )
        assert "result" in init_body, (
            f"Step 1 (initialize) handshake failed: {init_body!r}"
        )
        server_info = init_body["result"].get("serverInfo", {})
        assert server_info.get("name") == "valdo", (
            f"Step 1: handshake returned unexpected serverInfo: {server_info!r}"
        )

        # --------------------------------------------------------------
        # Step 2 — Fetch the ``onboard_new_source`` prompt.
        #
        # ``prompts/get`` with the workbook path + source code, then
        # assert the returned messages reference both onboarding tools
        # the agent is expected to invoke next.
        # --------------------------------------------------------------
        prompt_body = _rpc(
            client,
            "prompts/get",
            request_id=2,
            params={
                "name": "onboard_new_source",
                "arguments": {
                    "workbook_path": str(_SHAW_WORKBOOK),
                    "source_code": "SHAW",
                },
            },
        )
        prompt_text = _join_prompt_text(prompt_body)
        _assert_prompt_messages_reference(
            prompt_text,
            "upload_workbook_as_spec",
            "onboard_source_dry_run",
        )
        # The workbook path must interpolate verbatim so the agent
        # reads it without having to re-derive it from RPC arguments.
        assert str(_SHAW_WORKBOOK) in prompt_text, (
            f"Step 2: onboard_new_source prompt did not interpolate "
            f"workbook_path: {prompt_text!r}"
        )

        # --------------------------------------------------------------
        # Step 3 — Stage the workbook.
        #
        # Following the prompt's first instruction, call
        # ``upload_workbook_as_spec``. Assert the sandbox copy exists
        # under the per-test sandbox root (so we know the staging
        # actually wrote to disk and didn't return a phantom path).
        # --------------------------------------------------------------
        upload_body = _call_tool(
            client,
            "upload_workbook_as_spec",
            request_id=3,
            arguments={
                "workbook_path": str(_SHAW_WORKBOOK),
                "source_code": "SHAW",
            },
        )
        upload_result = upload_body.get("result") or {}
        assert not upload_result.get("isError"), (
            f"Step 3 (upload_workbook_as_spec) reported isError: "
            f"{upload_result!r}"
        )
        upload_payload = _structured_or_text(upload_result)
        assert upload_payload.get("source_code") == "SHAW", upload_payload
        sandbox_path_str = upload_payload.get("sandbox_path")
        assert isinstance(sandbox_path_str, str) and sandbox_path_str, (
            f"Step 3: sandbox_path missing or not a string: {upload_payload!r}"
        )
        sandbox_path = Path(sandbox_path_str)
        assert sandbox_path.is_file(), (
            f"Step 3: sandbox file missing from disk: {sandbox_path}"
        )
        # The staged copy MUST live under the per-test sandbox root,
        # not under the developer's home directory.
        sandbox_root_resolved = sandbox_root.resolve()
        assert str(sandbox_path.resolve()).startswith(
            str(sandbox_root_resolved)
        ), (
            f"Step 3: sandbox path {sandbox_path!r} not rooted under "
            f"override {sandbox_root_resolved!r}"
        )

        # --------------------------------------------------------------
        # Step 4 — Dry-run.
        #
        # Following the prompt's second instruction, call
        # ``onboard_source_dry_run`` with the sandboxed workbook path.
        # The MCP tool returns ``summary`` + ``would_write`` but NOT
        # drift counts — those live on the EE-S2 preview API. So we
        # ALSO hit ``/api/v2/onboarding/preview`` with the same
        # workbook to surface the drift block; an MCP agent driving
        # the BA flow would do the same (the prompt mentions "drift
        # report" explicitly).
        # --------------------------------------------------------------
        dry_run_body = _call_tool(
            client,
            "onboard_source_dry_run",
            request_id=4,
            arguments={"workbook_path": str(sandbox_path)},
        )
        dry_run_result = dry_run_body.get("result") or {}
        assert not dry_run_result.get("isError"), (
            f"Step 4 (onboard_source_dry_run) reported isError: "
            f"{dry_run_result!r}"
        )
        dry_run_payload = _structured_or_text(dry_run_result)

        assert dry_run_payload.get("source_code") == "SHAW", dry_run_payload
        would_write = dry_run_payload.get("would_write")
        assert isinstance(would_write, list) and would_write, (
            f"Step 4: would_write empty or wrong shape: {dry_run_payload!r}"
        )
        summary = dry_run_payload.get("summary") or {}
        assert summary.get("total_files") == len(would_write), summary
        assert isinstance(summary.get("total_bytes"), int), summary
        assert summary["total_bytes"] > 0, summary

        # Hit the preview API for drift counts. The preview endpoint
        # is on the parent FastAPI surface (not the MCP sub-app) so it
        # gates on ``X-API-Key`` via the existing ``require_api_key``
        # dependency. The MCP bearer token in the client's default
        # headers is harmless here — FastAPI ignores Authorization
        # values on routes that don't gate on them.
        with open(_SHAW_WORKBOOK, "rb") as wb_fh:
            preview_response = client.post(
                "/api/v2/onboarding/preview",
                files={
                    "file": (
                        _SHAW_WORKBOOK.name,
                        wb_fh,
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    )
                },
                headers={"X-API-Key": _WALKTHROUGH_API_KEY},
            )
        assert preview_response.status_code == 200, (
            f"Step 4 (preview API) failed: {preview_response.status_code} "
            f"{preview_response.text}"
        )
        preview_payload = preview_response.json()
        preview_summary = preview_payload.get("summary") or {}
        drift_counts = preview_summary.get("drift")
        assert isinstance(drift_counts, dict), (
            f"Step 4: drift counts missing from preview: {preview_payload!r}"
        )
        # Drift bucket keys are pinned by EE-S2 contract.
        assert set(drift_counts.keys()) == {"new", "changed", "unchanged"}, (
            f"Step 4: drift bucket keys drifted: {drift_counts!r}"
        )
        # Every bucket value must be a non-negative int.
        for bucket, count in drift_counts.items():
            assert isinstance(count, int) and count >= 0, (
                f"Step 4: drift count {bucket!r} not non-neg int: {count!r}"
            )

        # --------------------------------------------------------------
        # Step 5 — Inspect the diff.
        #
        # Pick the ``changed`` artefact from the preview drift report.
        # For the SHAW workbook against committed state the program's
        # end-state is exactly one ``changed`` entry: the R028B
        # carve-out in TRANERT_CUS rules. If this assertion ever fires,
        # either the workbook or the committed config has moved out
        # from under the program — investigate before suppressing.
        # --------------------------------------------------------------
        changed_entries = [
            entry
            for entry in preview_payload.get("would_write", [])
            if entry.get("status") == "changed"
        ]
        assert len(changed_entries) == 1, (
            f"Step 5: expected exactly one changed artefact (R028B "
            f"carve-out); got {len(changed_entries)}: "
            f"{[e.get('path') for e in changed_entries]!r}"
        )
        changed = changed_entries[0]
        assert changed.get("path") == _EXPECTED_CHANGED_ARTEFACT, (
            f"Step 5: changed artefact drifted from R028B carve-out: "
            f"expected {_EXPECTED_CHANGED_ARTEFACT!r}, got "
            f"{changed.get('path')!r}"
        )
        # The EE-S2 contract pins ``drift_reason`` on changed entries so
        # the UI can render the one-line diff hint. Assert it surfaces
        # so the agent has something human-readable to show the BA.
        assert isinstance(changed.get("drift_reason"), str) and changed[
            "drift_reason"
        ], (
            f"Step 5: changed artefact missing drift_reason: {changed!r}"
        )

        # --------------------------------------------------------------
        # Step 6 — Validate (mocked).
        #
        # ``run_validate_service`` is mocked to return a synthetic
        # success. Call ``validate_file`` and assert a run_id is
        # returned + the mock saw the right file path.
        # --------------------------------------------------------------
        validate_body = _call_tool(
            client,
            "validate_file",
            request_id=6,
            arguments={
                "source": "SHAW",
                "file_path": fixture_file,
                "file_type": "TRANERT",
            },
        )
        validate_result = validate_body.get("result") or {}
        assert not validate_result.get("isError"), (
            f"Step 6 (validate_file) reported isError: {validate_result!r}"
        )
        validate_payload = _structured_or_text(validate_result)
        run_id = validate_payload.get("run_id")
        assert isinstance(run_id, str) and run_id, (
            f"Step 6: run_id missing or not a string: {validate_payload!r}"
        )
        # Confirm the mock was actually called with our fixture file
        # — proves the MCP adapter forwarded the request and didn't
        # short-circuit on a stale registry entry.
        assert captured_validate_kwargs.get("file") == fixture_file, (
            f"Step 6: engine was called with wrong file path: "
            f"{captured_validate_kwargs!r}"
        )

        # --------------------------------------------------------------
        # Step 7 — Poll status.
        #
        # Call ``get_run_status``; assert the status reached
        # ``"completed"`` (the stub completes synchronously) and the
        # violation_count is zero.
        # --------------------------------------------------------------
        status_body = _call_tool(
            client,
            "get_run_status",
            request_id=7,
            arguments={"run_id": run_id},
        )
        status_result = status_body.get("result") or {}
        assert not status_result.get("isError"), (
            f"Step 7 (get_run_status) reported isError: {status_result!r}"
        )
        status_payload = _structured_or_text(status_result)
        assert status_payload.get("run_id") == run_id, status_payload
        assert status_payload.get("status") in {
            "queued",
            "running",
            "completed",
            "failed",
        }, f"Step 7: unknown status value: {status_payload!r}"
        assert status_payload["status"] == "completed", (
            f"Step 7: expected completed after sync mock; got: {status_payload!r}"
        )
        assert status_payload.get("violation_count") == 0, status_payload

        # --------------------------------------------------------------
        # Step 8 — Fetch the diagnose prompt.
        #
        # Exercise ``diagnose_validation_failure`` even though step 6
        # was a success: an agent driving a real BA session might fetch
        # this prompt speculatively so it knows the failure-triage tool
        # sequence in case the next run goes red.
        # --------------------------------------------------------------
        diagnose_body = _rpc(
            client,
            "prompts/get",
            request_id=8,
            params={
                "name": "diagnose_validation_failure",
                "arguments": {"run_id": run_id},
            },
        )
        diagnose_text = _join_prompt_text(diagnose_body)
        _assert_prompt_messages_reference(
            diagnose_text,
            "get_run_status",
            "get_violations",
            "taxonomy://violations",
        )
        assert run_id in diagnose_text, (
            f"Step 8: diagnose prompt did not interpolate run_id: "
            f"{diagnose_text!r}"
        )

        # --------------------------------------------------------------
        # Step 9 — Fetch violations (empty).
        #
        # The mock returned a clean success so the violations list is
        # empty. Assert pagination shape is sane even when the run has
        # zero violations.
        # --------------------------------------------------------------
        violations_body = _call_tool(
            client,
            "get_violations",
            request_id=9,
            arguments={"run_id": run_id, "page": 1, "page_size": 50},
        )
        violations_result = violations_body.get("result") or {}
        assert not violations_result.get("isError"), (
            f"Step 9 (get_violations) reported isError: {violations_result!r}"
        )
        violations_payload = _structured_or_text(violations_result)
        assert violations_payload.get("total_count") == 0, violations_payload
        assert violations_payload.get("violations") == [], violations_payload
        assert violations_payload.get("has_more") is False, violations_payload

        # --------------------------------------------------------------
        # Step 10 — Disconnect cleanly.
        #
        # The TestClient's ``__exit__`` runs lifespan shutdown + closes
        # the underlying ASGI transport. If any background task left a
        # zombie, the exit would raise. We assert nothing here — the
        # implicit lack of an exception IS the assertion.
        # --------------------------------------------------------------
        # (Falling out of the ``with`` block performs the disconnect.)

    # Belt-and-braces: after the manager exits, the client session
    # state should not be reusable. ``client.headers`` outlives the
    # context but issuing a real request would fail — the contract is
    # that the test reaches this line without raising from __exit__.
    # The point of step 10 is to PROVE no zombie state.

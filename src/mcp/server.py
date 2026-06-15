"""MCP (Model Context Protocol) server scaffold for Valdo (EF-S1 + EF-S2 + EF-S3 + EF-S4 + EF-S5).

This module wires a Streamable-HTTP MCP server (built on `mcp.server.fastmcp`)
into the existing FastAPI process. The server registers the following
capabilities:

* Tools: nine tools total. Three read-only (EF-S2) — ``list_sources``,
  ``get_source_spec``, ``list_recent_runs``. Three action tools (EF-S4)
  — ``validate_file``, ``get_run_status``, ``get_violations``. Three
  onboarding tools (EF-S5) — ``upload_workbook_as_spec``,
  ``onboard_source_dry_run``, ``infer_mapping_from_sample``. All wrap
  the existing Valdo service layer; implementations live in
  :mod:`src.mcp.tools` (read-only), :mod:`src.mcp.action_tools`
  (mutating), and :mod:`src.mcp.onboarding_tools` (onboarding) to keep
  this module focused on FastMCP registration.
* Resources: ``taxonomy://violations`` and ``taxonomy://rules`` (EF-S3 —
  live introspection of the engine's violation kinds and rule check names;
  see :mod:`src.mcp.taxonomy`).
* Prompts: three workflow prompts (EF-S6) — ``onboard_new_source``,
  ``diagnose_validation_failure``, and ``infer_field_map``. Each is a
  templated free-text instruction set that surfaces in MCP clients'
  prompt pickers; implementations live in :mod:`src.mcp.prompts`.

The MCP capability advertisement therefore exposes tools, resources, and
prompts — ``tools/list`` returns the nine tools (three read-only + three
action + three onboarding), ``resources/list`` returns the two taxonomy
URIs, and ``prompts/list`` returns the three EF-S6 workflow prompts.

Auth posture (dev-only, replaced in EF-S7):
    The MCP sub-app is protected by a small Starlette ``BaseHTTPMiddleware``
    that requires the env var ``VALDO_MCP_AUTH=dev``. With that flag set, the
    middleware is a pass-through (no other check is performed). Without it,
    every request to ``/mcp/*`` returns a ``401`` with a JSON body of
    ``{"error": "MCP auth not configured"}``. This is deliberately blunt —
    the real bridge into LDAPS + the existing X-API-Key flow is scheduled
    for EF-S7. The middleware is mounted ONLY on the MCP sub-app so it
    cannot accidentally alter the parent FastAPI auth behaviour.

Transport security:
    When ``VALDO_MCP_AUTH=dev`` is set, DNS-rebinding protection is disabled
    on the MCP transport so the FastAPI ``TestClient`` (host ``testserver``)
    can complete the handshake. Production deployments leave DNS-rebinding
    protection on by default; the proper allowed-hosts configuration is
    delivered in EF-S7.
"""

from __future__ import annotations

import json
import os
from typing import Tuple

from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.mcp.action_tools import (
    GET_RUN_STATUS_DESCRIPTION,
    GET_VIOLATIONS_DESCRIPTION,
    VALIDATE_FILE_DESCRIPTION,
    get_run_status_payload,
    get_violations_payload,
    validate_file_payload,
)
from src.mcp.onboarding_tools import (
    INFER_MAPPING_FROM_SAMPLE_DESCRIPTION,
    ONBOARD_SOURCE_DRY_RUN_DESCRIPTION,
    UPLOAD_WORKBOOK_AS_SPEC_DESCRIPTION,
    infer_mapping_from_sample_payload,
    onboard_source_dry_run_payload,
    upload_workbook_as_spec_payload,
)
from src.mcp.prompts import (
    DIAGNOSE_VALIDATION_FAILURE_DESCRIPTION,
    INFER_FIELD_MAP_DESCRIPTION,
    ONBOARD_NEW_SOURCE_DESCRIPTION,
    build_diagnose_validation_failure_messages,
    build_infer_field_map_messages,
    build_onboard_new_source_messages,
)
from src.mcp.taxonomy import list_rule_taxonomy, list_violation_taxonomy
from src.mcp.tools import (
    GET_SOURCE_SPEC_DESCRIPTION,
    LIST_RECENT_RUNS_DESCRIPTION,
    LIST_SOURCES_DESCRIPTION,
    get_source_spec_bundle,
    list_recent_runs_payload,
    list_sources_payload,
)

__all__ = ["build_mcp_server", "MCPAuthMiddleware"]

# Resource URIs (kept module-level so tests and external callers can import
# them rather than hard-coding string literals).
TAXONOMY_VIOLATIONS_URI = "taxonomy://violations"
TAXONOMY_RULES_URI = "taxonomy://rules"

# JSON MIME type advertised on each taxonomy resource — agents that fetch
# the resource know to ``json.loads`` the text body without sniffing.
_JSON_MIME = "application/json"

# Server identity advertised in the MCP `initialize` response. Kept in sync
# with the FastAPI app version in ``src/api/main.py``.
MCP_SERVER_NAME = "valdo"
MCP_SERVER_VERSION = "1.0.0"

# Sentinel value that unlocks the dev-mode auth pass-through.
_DEV_AUTH_SENTINEL = "dev"
_DEV_AUTH_ENV_VAR = "VALDO_MCP_AUTH"


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Dev-mode auth gate for the MCP sub-app.

    The middleware is intentionally minimal: it inspects the
    ``VALDO_MCP_AUTH`` environment variable on every request. When the value
    is exactly ``"dev"`` the request is passed through to the MCP transport
    untouched; otherwise the request is short-circuited with a ``401``.

    This stand-in exists only so the EF-S1 scaffold can demonstrate the
    handshake without leaking the MCP transport to anonymous traffic. EF-S7
    replaces it with a real LDAPS + X-API-Key bridge that mirrors the parent
    FastAPI ``require_api_key`` dependency.

    The env var is intentionally read per-request (rather than captured at
    middleware construction) so test cases can toggle it via
    ``monkeypatch.setenv`` / ``monkeypatch.delenv`` without having to rebuild
    the FastAPI app between assertions.
    """

    async def dispatch(self, request: Request, call_next):
        if os.environ.get(_DEV_AUTH_ENV_VAR) == _DEV_AUTH_SENTINEL:
            return await call_next(request)
        return JSONResponse(
            status_code=401,
            content={"error": "MCP auth not configured"},
        )


def build_mcp_server() -> Tuple[FastMCP, Starlette]:
    """Construct the Valdo MCP server and its mountable Starlette sub-app.

    The function performs three steps in order:

    1. Instantiate ``FastMCP`` with stateless / JSON-response transport
       settings tuned for low-latency, single-shot JSON-RPC calls. The
       ``streamable_http_path`` is set to ``"/"`` because the resulting
       Starlette app is mounted on the parent FastAPI app under ``"/mcp"``;
       the externally visible path is therefore ``/mcp/``.

    2. Invoke ``mcp_server.streamable_http_app()`` once to materialise the
       Starlette transport. This call has the important side effect of
       creating the lazily-initialised session manager — the parent
       FastAPI lifespan needs ``mcp_server.session_manager.run()`` to
       successfully enter its async context.

    3. Wrap the resulting Starlette app with :class:`MCPAuthMiddleware` so
       that the dev-mode ``VALDO_MCP_AUTH`` gate is enforced before the
       MCP transport sees any traffic. The middleware is installed on the
       sub-app only — the parent FastAPI auth chain is unaffected.

    Returns:
        A tuple ``(mcp_server, mounted_app)`` where:

        * ``mcp_server`` is the :class:`FastMCP` instance; the caller MUST
          enter ``mcp_server.session_manager.run()`` inside the FastAPI
          lifespan or the transport will reject all requests.
        * ``mounted_app`` is the Starlette application ready to be passed
          to ``FastAPI.mount("/mcp", mounted_app)``.

    Notes:
        Prompts remain empty (EF-S6). Three read-only tools are
        registered (EF-S2) — ``list_sources``, ``get_source_spec``,
        ``list_recent_runs`` — all thin adapters over :mod:`src.mcp.tools`
        so business logic stays in the service layer. Two resources are
        registered here — ``taxonomy://violations`` and
        ``taxonomy://rules`` — backed by live introspection of the engine
        via :mod:`src.mcp.taxonomy`. We deliberately do NOT cache the
        taxonomy snapshots at registration time: each ``resources/read``
        call re-runs the introspection so an in-process engine edit
        (during development or hot-reload) is reflected immediately, and
        stale snapshots cannot accidentally mislead an agent.
    """
    # DNS-rebinding protection is disabled only when we are explicitly in
    # dev mode (the same flag that opens the auth middleware). Production
    # deployments fall through to the FastMCP default, which keeps
    # rebinding protection ON.
    transport_security = None
    if os.environ.get(_DEV_AUTH_ENV_VAR) == _DEV_AUTH_SENTINEL:
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )

    mcp_server = FastMCP(
        name=MCP_SERVER_NAME,
        # NOTE: FastMCP carries its own server-info version distinct from
        # the FastAPI app version. We pin them to the same string so MCP
        # clients see consistent server identity.
        instructions=(
            "Valdo MCP server. Read-only tools (EF-S2), taxonomy "
            "resources (EF-S3), action tools (EF-S4: validate_file, "
            "get_run_status, get_violations), onboarding tools "
            "(EF-S5: upload_workbook_as_spec, onboard_source_dry_run, "
            "infer_mapping_from_sample), and workflow prompts (EF-S6: "
            "onboard_new_source, diagnose_validation_failure, "
            "infer_field_map) are registered."
        ),
        stateless_http=True,
        json_response=True,
        # Mounted at /mcp by the parent FastAPI app, so the sub-app's own
        # transport path collapses to "/".
        streamable_http_path="/",
        transport_security=transport_security,
    )
    # Pin the server-info version reported in the `initialize` response.
    # FastMCP exposes this through the underlying low-level Server's
    # version attribute.
    try:
        mcp_server._mcp_server.version = MCP_SERVER_VERSION  # type: ignore[attr-defined]
    except AttributeError:
        # If the FastMCP internals change shape, the handshake still
        # succeeds — we just lose the version stamp. Surface the failure
        # only when the test asserts the specific version (it does not
        # today; the test only asserts `serverInfo.name == "valdo"`).
        pass

    # ------------------------------------------------------------------
    # EF-S3 — taxonomy resources.
    #
    # The resource handlers MUST be registered before
    # ``streamable_http_app()`` materialises the session manager so the
    # MCP transport advertises them in ``resources/list`` from the very
    # first request. They return plain JSON strings (``application/json``
    # MIME type); FastMCP wraps them in the spec-mandated
    # ``ReadResourceContents`` envelope automatically.
    #
    # We use ``json.dumps`` with ``ensure_ascii=False`` and stable
    # ``indent=2`` formatting so the resource text is human-readable when
    # an agent surfaces it back to a user verbatim — diff-friendly too.
    # ------------------------------------------------------------------

    @mcp_server.resource(
        TAXONOMY_VIOLATIONS_URI,
        name="violation-taxonomy",
        title="Valdo violation taxonomy",
        description=(
            "Live list of violation kinds the Valdo engine emits during "
            "validation and SQL-truth reconciliation. Each entry has a "
            "canonical 'name' (the literal string used in reports) and a "
            "one-line 'description'. Introspected from the engine on "
            "every read — no hardcoded duplicates."
        ),
        mime_type=_JSON_MIME,
    )
    def _violation_taxonomy_resource() -> str:
        return json.dumps(list_violation_taxonomy(), ensure_ascii=False, indent=2)

    @mcp_server.resource(
        TAXONOMY_RULES_URI,
        name="rule-taxonomy",
        title="Valdo rule taxonomy",
        description=(
            "Live list of rule check types the Valdo rule engine accepts, "
            "covering per-field validations, cross-row checks, and "
            "cross-record-type checks. Each entry has 'name', 'category' "
            "('per-field' | 'cross-row' | 'cross-type'), and a one-line "
            "'description'. Introspected from the engine on every read."
        ),
        mime_type=_JSON_MIME,
    )
    def _rule_taxonomy_resource() -> str:
        return json.dumps(list_rule_taxonomy(), ensure_ascii=False, indent=2)

    # ------------------------------------------------------------------
    # EF-S2 — read-only MCP tools.
    #
    # Each tool is a 1-3 line adapter around :mod:`src.mcp.tools`. The
    # business logic for source enumeration, artefact bundling, and
    # run-history fetching lives in that module so this file stays
    # focused on FastMCP registration. The descriptions are imported as
    # module-level constants so tests can assert on them without
    # round-tripping through the JSON-RPC ``tools/list`` payload.
    #
    # All three tools are READ-ONLY. The first mutating tool
    # (``validate_file`` / ``submit_run``) is EF-S4's scope and MUST land
    # in its own story to keep the read/write surface separation
    # explicit.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="list_sources",
        title="List Valdo sources",
        description=LIST_SOURCES_DESCRIPTION,
    )
    def _list_sources_tool() -> List[Dict[str, Any]]:
        return list_sources_payload()

    @mcp_server.tool(
        name="get_source_spec",
        title="Get Valdo source spec bundle",
        description=GET_SOURCE_SPEC_DESCRIPTION,
    )
    def _get_source_spec_tool(name: str) -> Dict[str, Any]:
        return get_source_spec_bundle(name)

    @mcp_server.tool(
        name="list_recent_runs",
        title="List recent Valdo runs",
        description=LIST_RECENT_RUNS_DESCRIPTION,
    )
    def _list_recent_runs_tool(
        source: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        return list_recent_runs_payload(source=source, limit=limit)

    # ------------------------------------------------------------------
    # EF-S4 — action MCP tools.
    #
    # First mutating tool surface: ``validate_file`` kicks off a run and
    # returns a freshly-minted run_id; ``get_run_status`` and
    # ``get_violations`` page the result out. The validation itself
    # executes synchronously inside the ``validate_file`` call today —
    # see :mod:`src.mcp.action_tools` for the EF-S5 follow-up plan.
    #
    # All three are thin adapters around the existing service layer
    # (`src.services.validate_service.run_validate_service` and
    # `src.services.run_history_service.fetch_history_from_db`). No
    # validation logic, run-history schema, or violation reporting code
    # is duplicated.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="validate_file",
        title="Start a Valdo validation run",
        description=VALIDATE_FILE_DESCRIPTION,
    )
    def _validate_file_tool(
        source: str,
        file_path: str,
        file_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        return validate_file_payload(
            source=source,
            file_path=file_path,
            file_type=file_type,
        )

    @mcp_server.tool(
        name="get_run_status",
        title="Get Valdo run status",
        description=GET_RUN_STATUS_DESCRIPTION,
    )
    def _get_run_status_tool(run_id: str) -> Dict[str, Any]:
        return get_run_status_payload(run_id=run_id)

    @mcp_server.tool(
        name="get_violations",
        title="Page Valdo run violations",
        description=GET_VIOLATIONS_DESCRIPTION,
    )
    def _get_violations_tool(
        run_id: str,
        page: int = 1,
        page_size: int = 50,
        severity: Optional[str] = None,
    ) -> Dict[str, Any]:
        return get_violations_payload(
            run_id=run_id,
            page=page,
            page_size=page_size,
            severity=severity,
        )

    # ------------------------------------------------------------------
    # EF-S5 — onboarding MCP tools.
    #
    # Three "agent-driven onboarding" tools that wrap the existing
    # EC-S6 onboard-source service layer plus the EC-track
    # infer-mapping service. The split into a dedicated
    # :mod:`src.mcp.onboarding_tools` module mirrors the EF-S2 / EF-S4
    # pattern — this server file stays a registration sheet, business
    # logic lives in the adapter module.
    #
    # Sandbox convention: ``upload_workbook_as_spec`` stages workbooks
    # under ``~/.valdo/mcp_sandbox/<SOURCE>/onboarding.xlsx`` (overridable
    # via ``VALDO_MCP_SANDBOX_ROOT`` for ops + tests).
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="upload_workbook_as_spec",
        title="Stage an onboarding workbook in the MCP sandbox",
        description=UPLOAD_WORKBOOK_AS_SPEC_DESCRIPTION,
    )
    def _upload_workbook_as_spec_tool(
        workbook_path: str,
        source_code: Optional[str] = None,
    ) -> Dict[str, Any]:
        return upload_workbook_as_spec_payload(
            workbook_path=workbook_path,
            source_code=source_code,
        )

    @mcp_server.tool(
        name="onboard_source_dry_run",
        title="Preview onboard-source artefact tree (dry-run)",
        description=ONBOARD_SOURCE_DRY_RUN_DESCRIPTION,
    )
    def _onboard_source_dry_run_tool(workbook_path: str) -> Dict[str, Any]:
        return onboard_source_dry_run_payload(workbook_path=workbook_path)

    @mcp_server.tool(
        name="infer_mapping_from_sample",
        title="Infer a draft mapping from a sample file",
        description=INFER_MAPPING_FROM_SAMPLE_DESCRIPTION,
    )
    def _infer_mapping_from_sample_tool(
        sample_file_path: str,
        file_type: str,
        format_hint: Optional[str] = None,
    ) -> Dict[str, Any]:
        return infer_mapping_from_sample_payload(
            sample_file_path=sample_file_path,
            file_type=file_type,
            format_hint=format_hint,
        )

    # ------------------------------------------------------------------
    # EF-S6 — workflow prompts.
    #
    # Three templated free-text prompts that surface in MCP clients'
    # prompt pickers (Claude Desktop, mcp-cli, etc.) and guide an agent
    # through the correct Valdo tool-call sequence. The prompt bodies
    # are plain instructional text — agents read them and decide which
    # already-registered tools to invoke — so this layer adds no new
    # business logic and cannot diverge from the tool surface beyond
    # the test-pinned name references in :mod:`src.mcp.prompts`.
    #
    # All three prompts return ``list[UserMessage]`` because prompt
    # content is "context the LLM sees on the user's behalf", not a
    # system directive. The host application's own system prompt
    # continues to govern overall behaviour.
    # ------------------------------------------------------------------

    @mcp_server.prompt(
        name="onboard_new_source",
        title="Onboard a new Valdo source",
        description=ONBOARD_NEW_SOURCE_DESCRIPTION,
    )
    def _onboard_new_source_prompt(
        workbook_path: str,
        source_code: Optional[str] = None,
    ):
        return build_onboard_new_source_messages(
            workbook_path=workbook_path,
            source_code=source_code,
        )

    @mcp_server.prompt(
        name="diagnose_validation_failure",
        title="Diagnose a failed Valdo validation run",
        description=DIAGNOSE_VALIDATION_FAILURE_DESCRIPTION,
    )
    def _diagnose_validation_failure_prompt(run_id: str):
        return build_diagnose_validation_failure_messages(run_id=run_id)

    @mcp_server.prompt(
        name="infer_field_map",
        title="Infer a Valdo field mapping from a sample file",
        description=INFER_FIELD_MAP_DESCRIPTION,
    )
    def _infer_field_map_prompt(sample_file_path: str, file_type: str):
        return build_infer_field_map_messages(
            sample_file_path=sample_file_path,
            file_type=file_type,
        )

    # Materialise the Streamable HTTP transport. This call is what creates
    # the session manager; accessing `mcp_server.session_manager` before
    # this raises a RuntimeError ("Session manager can only be accessed
    # after calling streamable_http_app()").
    transport_app = mcp_server.streamable_http_app()

    # Wrap the transport in our dev-auth gate. We use Starlette's
    # add_middleware (not FastAPI's) because the transport is a plain
    # Starlette application.
    transport_app.add_middleware(MCPAuthMiddleware)

    return mcp_server, transport_app

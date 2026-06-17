"""MCP (Model Context Protocol) server scaffold for Valdo (EF-S1 + EF-S2 + EF-S3 + EF-S4 + EF-S5).

This module wires a Streamable-HTTP MCP server (built on `mcp.server.fastmcp`)
into the existing FastAPI process. The server registers the following
capabilities:

* Tools: ten tools total. Three read-only (EF-S2) — ``list_sources``,
  ``get_source_spec``, ``list_recent_runs``. Three action tools (EF-S4)
  — ``validate_file``, ``get_run_status``, ``get_violations``. Three
  onboarding tools (EF-S5) — ``upload_workbook_as_spec``,
  ``onboard_source_dry_run``, ``infer_mapping_from_sample``. One
  ad-hoc compare tool (S7-4) — ``compare_two_files``. All wrap the
  existing Valdo service layer; implementations live in
  :mod:`src.mcp.tools` (read-only), :mod:`src.mcp.action_tools`
  (mutating), :mod:`src.mcp.onboarding_tools` (onboarding), and
  :mod:`src.mcp.compare_tools` (ad-hoc compare) to keep this module
  focused on FastMCP registration.
* Resources: ``taxonomy://violations`` and ``taxonomy://rules`` (EF-S3 —
  live introspection of the engine's violation kinds and rule check names;
  see :mod:`src.mcp.taxonomy`), three ``templates://etl/*`` resources
  (S7-2 — auto-discovered ETL template catalogue, single-shape YAML body,
  and per-shape sample-directory manifest; see
  :mod:`src.mcp.resources.etl_templates`), and ``formats://supported``
  (S7-3 — enumeration of supported + planned input formats, backed by
  module-level constants in :mod:`src.mcp.resources.formats`).
* Prompts: four workflow prompts — three from EF-S6
  (``onboard_new_source``, ``diagnose_validation_failure``,
  ``infer_field_map``) plus the BA-facing capstone ``pick_etl_shape``
  added by S7-5. Each is a templated free-text instruction set that
  surfaces in MCP clients' prompt pickers; implementations live in
  :mod:`src.mcp.prompts`.

The MCP capability advertisement therefore exposes tools, resources, and
prompts — ``tools/list`` returns the ten tools (three read-only + three
action + three onboarding + one ad-hoc compare), ``resources/list``
returns the two taxonomy URIs plus the S7-2 / S7-3 ETL surface, and
``prompts/list`` returns the four workflow prompts (EF-S6 trio + S7-5
``pick_etl_shape``).

Auth posture (EF-S7 — production bridge):
    The MCP sub-app is protected by :class:`src.mcp.auth.MCPAuthMiddleware`
    which accepts (in this priority order): the LDAPS session cookie minted
    by the existing ``/auth/login`` flow, an ``X-API-Key`` header validated
    against the same ``API_KEYS`` env var the parent API uses, or an
    ``Authorization: Bearer <token>`` HMAC-SHA256 signed token issued by
    ``POST /api/v2/mcp/login`` and ``valdo mcp-login``. The dev-mode
    bypass (``VALDO_MCP_AUTH=dev``) is preserved as a strict opt-in for
    local testing; in any other mode the middleware fails closed with a
    generic 401 JSON body. The middleware is mounted ONLY on the MCP
    sub-app so it cannot alter the parent FastAPI auth behaviour.

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
from starlette.responses import JSONResponse

from src.mcp.auth import (
    MCPAuthMiddleware,
    is_dev_auth_enabled as _is_dev_auth_enabled,
)
from src.mcp.action_tools import (
    GET_RUN_STATUS_DESCRIPTION,
    GET_VIOLATIONS_DESCRIPTION,
    VALIDATE_FILE_DESCRIPTION,
    get_run_status_payload,
    get_violations_payload,
    validate_file_payload,
)
from src.mcp.compare_tools import (
    COMPARE_TWO_FILES_DESCRIPTION,
    compare_two_files_payload,
)
from src.mcp.reconcile_tools import (
    RECONCILE_MAPPING_DESCRIPTION,
    reconcile_mapping_payload,
)
from src.mcp.db_compare_tools import (
    DB_COMPARE_DESCRIPTION,
    db_compare_payload,
)
from src.mcp.reconcile_all_tools import (
    RECONCILE_ALL_DESCRIPTION,
    reconcile_all_payload,
)
from src.mcp.mask_tools import (
    MASK_FILE_DESCRIPTION,
    mask_file_payload,
)
from src.mcp.drift_tools import (
    DETECT_DRIFT_DESCRIPTION,
    detect_drift_payload,
)
from src.mcp.extract_tools import (
    EXTRACT_TABLE_DESCRIPTION,
    extract_table_payload,
)
from src.mcp.parse_tools import (
    PARSE_FILE_DESCRIPTION,
    parse_file_payload,
)
from src.mcp.etl_pipeline_tools import (
    RUN_ETL_PIPELINE_DESCRIPTION,
    run_etl_pipeline_payload,
)
from src.mcp.export_tools import (
    EXPORT_FAILED_ROWS_DESCRIPTION,
    export_failed_rows_payload,
)
from src.mcp.task_tools import (
    SUBMIT_TASK_DESCRIPTION,
    submit_task_payload,
)
from src.mcp.suite_tools import (
    RUN_SUITE_DESCRIPTION,
    run_suite_payload,
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
    PICK_ETL_SHAPE_DESCRIPTION,
    build_diagnose_validation_failure_messages,
    build_infer_field_map_messages,
    build_onboard_new_source_messages,
    build_pick_etl_shape_messages,
)
from src.mcp.resources.etl_templates import (
    list_templates_payload,
    load_sample_manifest,
    load_template_yaml,
)
from src.mcp.resources.formats import formats_supported_payload
from src.mcp.resources.reports import read_report_html
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

# ETL template resource URIs (S7-2). The two single-shape URIs use the
# FastMCP ``{shape}`` placeholder convention so a single registered
# handler covers every auto-discovered template — no per-template
# registration, no hardcoded shape names.
ETL_TEMPLATES_LIST_URI = "templates://etl/list"
ETL_TEMPLATE_BY_SHAPE_URI = "templates://etl/{shape}"
ETL_TEMPLATE_SAMPLE_URI = "templates://etl/{shape}/sample"

# Supported-formats resource URI (S7-3). A single static URI; the
# payload is read from module-level constants in
# :mod:`src.mcp.resources.formats` so adding a new format when an ADR
# closes is a one-line edit there, not a server-file change.
FORMATS_SUPPORTED_URI = "formats://supported"

# Report retrieval resource URI (S23-4, #446, ADR 0023). The ``{run_id}``
# placeholder is bound by a single handler (mirroring
# ``templates://etl/{shape}``); no per-run registration is needed. The report
# id is resolved to a file traversal-safely and served ONLY from the reports
# dir, behind the same MCP auth as every other call.
REPORT_BY_RUN_ID_URI = "report://{run_id}"

# JSON MIME type advertised on each taxonomy resource — agents that fetch
# the resource know to ``json.loads`` the text body without sniffing.
_JSON_MIME = "application/json"

# Server identity advertised in the MCP `initialize` response. Kept in sync
# with the FastAPI app version in ``src/api/main.py``.
MCP_SERVER_NAME = "valdo"
MCP_SERVER_VERSION = "1.0.0"

# EF-S7 — the dev-mode sentinels and the production-grade
# :class:`MCPAuthMiddleware` are imported from :mod:`src.mcp.auth` at the
# top of this module. The auth module is the single source of truth so
# the CLI subcommand (``valdo mcp-login``) and the FastAPI sub-app share
# identical sentinel values without copy-pasting constants.


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
    # dev mode OR an explicit allow-list is configured via the env var
    # ``VALDO_MCP_ALLOWED_HOSTS`` (comma-separated). Production
    # deployments behind a reverse proxy should set the allow-list to
    # the public hostnames the MCP transport is served at; dev mode is
    # the only path that fully disables the check. Tests that exercise
    # the production auth chain set the allow-list to ``testserver``.
    transport_security = None
    if _is_dev_auth_enabled():
        # Dev bypass is deliberately opted in (S13.5-3, #409:
        # VALDO_MCP_AUTH=dev + VALDO_ALLOW_DEV_AUTH); relax DNS-rebinding
        # protection so local TestClient/loopback handshakes succeed.
        transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=False,
        )
    elif os.environ.get("VALDO_MCP_ALLOWED_HOSTS"):
        hosts = [
            h.strip()
            for h in os.environ["VALDO_MCP_ALLOWED_HOSTS"].split(",")
            if h.strip()
        ]
        transport_security = TransportSecuritySettings(
            allowed_hosts=hosts,
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
            "infer_field_map; S7-5: pick_etl_shape) are registered."
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
    # S7-2 — ETL template resources.
    #
    # Three resources back the agent-facing ``templates://etl/*`` surface:
    #
    # * ``templates://etl/list``        — JSON listing of every shape +
    #   one-line description (auto-discovered from templates/etl/*.yml).
    # * ``templates://etl/{shape}``     — the YAML body of one template,
    #   returned verbatim (text/yaml) so the agent can paste it into a
    #   working spec without parsing.
    # * ``templates://etl/{shape}/sample`` — a JSON manifest of the
    #   paired <shape>_sample/ directory (file paths + sizes + 200-char
    #   previews); the manifest is intentionally NOT a tarball so large
    #   fixtures don't blow the response budget.
    #
    # Discovery, validation, and description-sourcing live in
    # :mod:`src.mcp.resources.etl_templates` — this layer stays a
    # declarative registration manifest. We do NOT cache the discovery
    # output: each ``resources/read`` call re-scans the templates
    # directory so a new template dropped in during a dev cycle is
    # picked up immediately (same posture as the taxonomy resources).
    # ------------------------------------------------------------------

    @mcp_server.resource(
        ETL_TEMPLATES_LIST_URI,
        name="etl-templates-list",
        title="Valdo ETL template catalogue",
        description=(
            "Live list of ETL template shapes available under "
            "templates/etl/. Each entry has a 'shape' (the URI suffix "
            "for templates://etl/<shape>) and a one-line 'description' "
            "drawn from the template's own 'description:' field. "
            "Auto-discovered on every read — no hardcoded shape names."
        ),
        mime_type=_JSON_MIME,
    )
    def _etl_templates_list_resource() -> str:
        return json.dumps(list_templates_payload(), ensure_ascii=False, indent=2)

    @mcp_server.resource(
        ETL_TEMPLATE_BY_SHAPE_URI,
        name="etl-template",
        title="Valdo ETL template YAML",
        description=(
            "Raw YAML body of one ETL template shape. The content is "
            "returned verbatim — comments, indentation, and "
            "<FILL_IN_*> placeholders are preserved so the agent can "
            "surface the template to the user as a copy-pasteable "
            "starting point."
        ),
        mime_type="text/yaml",
    )
    def _etl_template_resource(shape: str) -> str:
        return load_template_yaml(shape)

    @mcp_server.resource(
        ETL_TEMPLATE_SAMPLE_URI,
        name="etl-template-sample",
        title="Valdo ETL template sample directory manifest",
        description=(
            "JSON manifest of the paired <shape>_sample/ directory: "
            "each file's relative path, size in bytes, and a short "
            "text preview (or null for binary files). The manifest is "
            "NOT a tarball — large fixtures are listed but not inlined."
        ),
        mime_type=_JSON_MIME,
    )
    def _etl_template_sample_resource(shape: str) -> str:
        return json.dumps(
            load_sample_manifest(shape), ensure_ascii=False, indent=2
        )

    # ------------------------------------------------------------------
    # S7-3 — supported-formats resource.
    #
    # ``formats://supported`` enumerates every Valdo input format an
    # agent can ask the engine to validate today, plus the formats
    # tracked by open ADR issues. Backed by module-level constants in
    # :mod:`src.mcp.resources.formats` — see that module's docstring for
    # the source-of-truth contract. The handler does no I/O at request
    # time, so the response budget is bounded and deterministic.
    # ------------------------------------------------------------------

    @mcp_server.resource(
        FORMATS_SUPPORTED_URI,
        name="formats-supported",
        title="Valdo supported formats",
        description=(
            "Enumeration of every input format Valdo's engine can "
            "validate today (each with its sprint of introduction "
            "and, when one exists, a pointer to the matching "
            "templates://etl/<shape> template) plus the formats "
            "tracked by open ADR issues. Source-of-truth is "
            "module-level constants — easy to update when a new "
            "format lands or an ADR closes."
        ),
        mime_type=_JSON_MIME,
    )
    def _formats_supported_resource() -> str:
        return json.dumps(
            formats_supported_payload(), ensure_ascii=False, indent=2
        )

    # ------------------------------------------------------------------
    # S23-4 (#446) — HTML report retrieval resource (ADR 0023).
    #
    # ``report://{run_id}`` returns the rendered HTML body of a report
    # produced by a report-producing tool (validate_file, compare_two_files,
    # db_compare, reconcile_mapping, reconcile_all) when that tool was called
    # with ``include_report=True``. The tool advertises the report by
    # returning ``report_uri = report://<run_id>``; the agent then passes it
    # to ``resources/read``.
    #
    # The ``{run_id}`` placeholder is bound by this single handler — same
    # model as ``templates://etl/{shape}`` (server.py ~:405) — so no per-run
    # registration is needed. Resolution is traversal-safe and serves ONLY
    # from the reports dir (see :func:`src.mcp.resources.reports.read_report_html`,
    # modelled on ``_safe_upload_path``). The read is served by the MCP
    # transport, which is wrapped by ``MCPAuthMiddleware``, so it inherits
    # the same auth as every other MCP call. A missing / expired report
    # yields a clean ResourceError, not a stack trace or directory probe.
    # ------------------------------------------------------------------

    @mcp_server.resource(
        REPORT_BY_RUN_ID_URI,
        name="report",
        title="Valdo HTML report",
        description=(
            "Rendered HTML body of a Valdo report (validation, comparison, "
            "db-compare, or reconcile) produced when a report-producing tool "
            "was called with include_report=true. The producing tool returns "
            "the report_uri (report://<run_id>) to read here. The id is "
            "resolved traversal-safely and served only from the reports dir; "
            "a traversal attempt or a missing / expired report yields a clean "
            "error. Behind the same MCP auth as every other call."
        ),
        mime_type="text/html",
    )
    def _report_resource(run_id: str) -> str:
        return read_report_html(run_id)

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
        include_report: bool = False,
    ) -> Dict[str, Any]:
        return validate_file_payload(
            source=source,
            file_path=file_path,
            file_type=file_type,
            include_report=include_report,
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
    # S7-4 — ad-hoc file-compare action tool.
    #
    # ``compare_two_files`` lets an agent diff two arbitrary files by a
    # declared set of key columns without first registering them as a
    # Valdo *source*. Auto-detection by extension (.csv / .tsv / .txt);
    # fixed-width files require a mapping JSON. The tool is a thin
    # adapter over :class:`src.comparators.file_comparator.FileComparator`
    # — no business logic lives here. See :mod:`src.mcp.compare_tools`
    # for the rationale on the ``severity_filter`` parameter being
    # omitted (the comparator does not classify severity) and the
    # CSV-routing workaround for the S6-2 bug.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="compare_two_files",
        title="Compare two files row-by-row",
        description=COMPARE_TWO_FILES_DESCRIPTION,
    )
    def _compare_two_files_tool(
        left_path: str,
        right_path: str,
        key_columns: List[str],
        mapping_path: Optional[str] = None,
        include_report: bool = False,
    ) -> Dict[str, Any]:
        return compare_two_files_payload(
            left_path=left_path,
            right_path=right_path,
            key_columns=key_columns,
            mapping_path=mapping_path,
            include_report=include_report,
        )

    # ------------------------------------------------------------------
    # #407 — adapter-agnostic mapping-vs-table reconcile tool.
    #
    # ``reconcile_mapping`` wraps the shared reconcile service seam
    # (:func:`src.services.reconcile_service.reconcile_mapping_service`),
    # which the CLI (``valdo reconcile``) and the REST endpoint
    # (``POST /api/v2/reconcile``) also call. Adapter-agnostic per ADR 0022:
    # it reconciles against whichever backend DB_ADAPTER selects. Thin
    # adapter only — business logic lives in the service layer.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="reconcile_mapping",
        title="Reconcile a Valdo mapping against a database table",
        description=RECONCILE_MAPPING_DESCRIPTION,
    )
    def _reconcile_mapping_tool(
        mapping: str,
        table: Optional[str] = None,
        schema: Optional[str] = None,
        include_report: bool = False,
    ) -> Dict[str, Any]:
        return reconcile_mapping_payload(
            mapping=mapping,
            table=table,
            schema=schema,
            include_report=include_report,
        )

    # ------------------------------------------------------------------
    # S21-1 (#433) — adapter-agnostic DB-extract-vs-file compare tool.
    #
    # ``db_compare`` wraps the existing db-compare service
    # (:func:`src.services.db_file_compare_service.compare_db_to_file`),
    # which the CLI (``valdo db-compare``) and the REST endpoint
    # (``POST /api/v1/files/db-compare``) also call. Adapter-agnostic per
    # ADR 0022 §5: it extracts from whichever backend DB_ADAPTER selects.
    # Thin adapter only — business logic lives in the service layer. A
    # genuine comparison difference is a result (workflow.status=failed),
    # not a tool error; per-request credentials are never echoed back.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="db_compare",
        title="Compare a database extract against a file",
        description=DB_COMPARE_DESCRIPTION,
    )
    def _db_compare_tool(
        mapping: str,
        actual_file: str,
        table: Optional[str] = None,
        query: Optional[str] = None,
        key_columns: Optional[List[str]] = None,
        db_adapter: Optional[str] = None,
        connection: Optional[Dict[str, Any]] = None,
        include_report: bool = False,
    ) -> Dict[str, Any]:
        return db_compare_payload(
            mapping=mapping,
            actual_file=actual_file,
            table=table,
            query=query,
            key_columns=key_columns,
            db_adapter=db_adapter,
            connection=connection,
            include_report=include_report,
        )

    # ------------------------------------------------------------------
    # S21-2 (#434) — adapter-agnostic bulk mapping-vs-table reconcile tool.
    #
    # ``reconcile_all`` wraps the shared bulk-reconcile service seam
    # (:func:`src.services.reconcile_all_service.reconcile_all_service`),
    # which the CLI (``valdo reconcile-all``) also calls. Adapter-agnostic
    # per ADR 0022: it reconciles every mapping in a directory against
    # whichever backend DB_ADAPTER selects, aggregates a summary, and
    # — when a baseline report is supplied — computes the baseline
    # drift-diff. Thin adapter only — business logic lives in the service
    # layer. A single mapping that fails to process is a result (an
    # invalid entry in ``results``), not a tool error; no credentials are
    # echoed back.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="reconcile_all",
        title="Bulk-reconcile every mapping in a directory",
        description=RECONCILE_ALL_DESCRIPTION,
    )
    def _reconcile_all_tool(
        mappings_dir: str = "config/mappings",
        pattern: str = "*.json",
        baseline: Optional[str] = None,
        db_adapter: Optional[str] = None,
        include_report: bool = False,
    ) -> Dict[str, Any]:
        return reconcile_all_payload(
            mappings_dir=mappings_dir,
            pattern=pattern,
            baseline=baseline,
            db_adapter=db_adapter,
            include_report=include_report,
        )

    # ------------------------------------------------------------------
    # S21-3 (#435) — PII masking tool.
    #
    # ``mask_file`` wraps the existing masking service
    # (:class:`src.services.masking_service.MaskingService`), the same code
    # path the ``valdo mask`` CLI drives. Thin adapter only — masking logic
    # (the six strategies, format detection, output plumbing) lives in the
    # service layer. CRITICAL PII posture: the tool response carries only the
    # masked-file path, the record count, and a per-field strategy summary
    # (strategy + field NAMES) — NEVER raw masked/unmasked field values. The
    # original input file is never modified.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="mask_file",
        title="Mask PII in a batch file",
        description=MASK_FILE_DESCRIPTION,
    )
    def _mask_file_tool(
        file: str,
        mapping: str,
        masking_config: str,
        output: str,
    ) -> Dict[str, Any]:
        return mask_file_payload(
            file=file,
            mapping=mapping,
            masking_config=masking_config,
            output=output,
        )

    # ------------------------------------------------------------------
    # S21-4 (#436) — schema drift-detection tool.
    #
    # ``detect_drift`` wraps the existing drift-detector service
    # (:func:`src.services.drift_detector.detect_drift`), the same code path
    # the ``valdo detect-drift`` CLI and the ``POST /api/v1/files/detect-drift``
    # REST endpoint drive. Thin adapter only — the sampling + the fixed-width
    # position heuristic + the delimited header comparison live in the service
    # layer. A genuine drift finding is a result (report.drifted=True), not a
    # tool error; the response carries no raw field values.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="detect_drift",
        title="Detect schema drift between a file and its mapping",
        description=DETECT_DRIFT_DESCRIPTION,
    )
    def _detect_drift_tool(file: str, mapping: str) -> Dict[str, Any]:
        return detect_drift_payload(file=file, mapping=mapping)

    # ------------------------------------------------------------------
    # S21-5 (#437) — adapter-agnostic DB-extract-to-file tool.
    #
    # ``extract_table`` wraps the existing adapter-based extract path
    # (:class:`src.database.extractor.DataExtractor`), the same code path the
    # ``valdo extract`` CLI drives. Adapter-agnostic per ADR 0022 §4: it
    # extracts from whichever backend DB_ADAPTER selects. Thin adapter only —
    # SQL building, the S13.5-4 hardening (identifier allow-listing, bound
    # limit, raw-WHERE rejection), and file writing live in the extractor.
    # The response carries only the output-file path + row count — no extracted
    # row data and no credentials.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="extract_table",
        title="Extract a database table or query to a file",
        description=EXTRACT_TABLE_DESCRIPTION,
    )
    def _extract_table_tool(
        table: Optional[str] = None,
        query: Optional[str] = None,
        columns: Optional[List[str]] = None,
        limit: Optional[int] = None,
        output: str = "",
        delimiter: str = "|",
        db_adapter: Optional[str] = None,
    ) -> Dict[str, Any]:
        return extract_table_payload(
            table=table,
            query=query,
            columns=columns,
            limit=limit,
            output=output,
            delimiter=delimiter,
            db_adapter=db_adapter,
        )

    # ------------------------------------------------------------------
    # S22-1 (#438) — parse/inspect tool.
    #
    # ``parse_file`` wraps the existing Valdo parser layer (FormatDetector +
    # PipeDelimitedParser / FixedWidthParser), the same code path the
    # ``valdo parse`` CLI drives. Thin adapter only — the format detection, the
    # mapping-driven field layout, and the DataFrame construction live in the
    # parser classes. The response is a BOUNDED preview (column names + first N
    # rows + the true total row count) — never an unbounded file dump; the
    # input file is never modified.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="parse_file",
        title="Parse and inspect a batch file (bounded preview)",
        description=PARSE_FILE_DESCRIPTION,
    )
    def _parse_file_tool(
        file: str,
        mapping: Optional[str] = None,
        format: Optional[str] = None,
        limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        return parse_file_payload(
            file=file,
            mapping=mapping,
            format=format,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # S22-2 (#439) — ETL-pipeline runner tool.
    #
    # ``run_etl_pipeline`` wraps the existing Valdo ETL pipeline orchestrator
    # (:class:`src.pipeline.etl_pipeline_runner.ETLPipelineRunner`), the same code
    # path the ``valdo run-etl-pipeline`` CLI drives. Thin adapter only — gate
    # sequencing, template-variable expansion, per-step threshold evaluation, and
    # service delegation all live in the runner. The response is the runner's
    # structured aggregate result (per-gate pass/fail + overall verdict); a
    # pipeline/gate that reports status "failed" is a RESULT, not a tool error.
    # The response carries no secrets.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="run_etl_pipeline",
        title="Run a multi-gate ETL validation pipeline",
        description=RUN_ETL_PIPELINE_DESCRIPTION,
    )
    def _run_etl_pipeline_tool(
        config: str,
        run_date: Optional[str] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        return run_etl_pipeline_payload(
            config=config,
            run_date=run_date,
            params=params,
        )

    # ------------------------------------------------------------------
    # S22-3 (#440) — export-failed-rows tool.
    #
    # ``export_failed_rows`` wraps the existing Valdo validate + error-extract
    # path (:func:`src.services.validate_service.run_validate_service` +
    # :func:`src.services.error_extractor.extract_error_rows`), the same code path
    # the ``valdo validate --export-errors`` CLI flag and the
    # ``POST /api/v1/files/export-errors`` REST endpoint drive. Thin adapter only
    # — validation, failed-row collection, and the original-format write-back live
    # in the service layer. CRITICAL PII posture: the response carries ONLY the
    # export-file path + the counts (failed/total/valid) — NEVER the raw
    # failed-row values, which exist solely in the on-disk export file. The input
    # file is never modified; a clean file (zero failed rows) is a RESULT.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="export_failed_rows",
        title="Export a file's failed validation rows",
        description=EXPORT_FAILED_ROWS_DESCRIPTION,
    )
    def _export_failed_rows_tool(
        file: str,
        mapping: str,
        output: str,
        rules: Optional[str] = None,
        use_chunked: bool = False,
    ) -> Dict[str, Any]:
        return export_failed_rows_payload(
            file=file,
            mapping=mapping,
            output=output,
            rules=rules,
            use_chunked=use_chunked,
        )

    # ------------------------------------------------------------------
    # S22-4 (#441) — submit-task tool.
    #
    # ``submit_task`` wraps the existing Valdo canonical task-ingest path
    # (:func:`src.adapters.api_task_adapter.normalize_api_task_request` +
    # :func:`src.contracts.validation.validate_task_request` +
    # :class:`src.services.job_state_store.JobStateStore`), the same code path the
    # ``valdo submit-task`` CLI command and the ``POST /api/v1/tasks/submit`` REST
    # endpoint drive (normalise -> contract-validate -> idempotency-dedup -> store
    # write). Thin adapter only — request shaping, contract validation, and dedup
    # all live in those layers. Idempotency-aware: a repeat submission with the
    # same idempotency key (for the same intent + source) returns the EXISTING
    # task id/status rather than minting a second task. The response carries no
    # secrets; a contract-validation failure is a caller-fixable tool error.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="submit_task",
        title="Submit a canonical task request",
        description=SUBMIT_TASK_DESCRIPTION,
    )
    def _submit_task_tool(
        intent: str,
        payload: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        priority: str = "normal",
        deadline: Optional[str] = None,
    ) -> Dict[str, Any]:
        return submit_task_payload(
            intent=intent,
            payload=payload,
            task_id=task_id,
            trace_id=trace_id,
            idempotency_key=idempotency_key,
            priority=priority,
            deadline=deadline,
        )

    # ------------------------------------------------------------------
    # S22-5 (#442) — run-suite tool. COMPLETES MCP parity.
    #
    # ``run_suite`` wraps the existing Valdo suite runner
    # (:func:`src.commands.run_tests_command.run_suite_from_path`), the same code
    # path the ``valdo run-tests`` CLI command and the webhook / file-watcher
    # triggers drive. Thin adapter only — suite YAML loading, per-test execution
    # (structural | rules | oracle_vs_file | api_check), threshold evaluation,
    # report generation, run-history persistence, and baseline updates all live in
    # the runner. The response is the per-test pass/fail breakdown plus the overall
    # verdict; a suite whose tests FAIL (or are mixed) is a RESULT
    # (overall_status PARTIAL/FAIL), not a tool error. The response carries no
    # secrets. With this tool every CLI verb now has a matching MCP tool.
    # ------------------------------------------------------------------

    @mcp_server.tool(
        name="run_suite",
        title="Run a Valdo test suite",
        description=RUN_SUITE_DESCRIPTION,
    )
    def _run_suite_tool(
        suite: str,
        env: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
        output_dir: str = "reports",
    ) -> Dict[str, Any]:
        return run_suite_payload(
            suite=suite,
            env=env,
            params=params,
            output_dir=output_dir,
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

    # ------------------------------------------------------------------
    # S7-5 — BA-facing capstone prompt.
    #
    # ``pick_etl_shape`` closes the Sprint 7 demo loop: a BA describes
    # their data problem in free text and the agent uses the S7-2
    # template catalogue plus the S7-3 supported-formats resource to
    # recommend a shape, walk them through filling it in, and validate
    # via the onboarding dry-run (or compare_two_files for ad-hoc
    # diffs) before any commit. The body deliberately points at
    # ``docs/etl/CHOOSE_YOUR_SHAPE.md`` rather than re-encoding the
    # decision tree inline so there is a single source of truth.
    # ------------------------------------------------------------------

    @mcp_server.prompt(
        name="pick_etl_shape",
        title="Recommend a Valdo ETL template from a BA's description",
        description=PICK_ETL_SHAPE_DESCRIPTION,
    )
    def _pick_etl_shape_prompt(description: str):
        return build_pick_etl_shape_messages(description=description)

    # Materialise the Streamable HTTP transport. This call is what creates
    # the session manager; accessing `mcp_server.session_manager` before
    # this raises a RuntimeError ("Session manager can only be accessed
    # after calling streamable_http_app()").
    transport_app = mcp_server.streamable_http_app()

    # ------------------------------------------------------------------
    # S9-2 (#390) — load-balancer health probe at GET /mcp/health.
    #
    # The route is added to the transport's own route table (it becomes
    # ``/mcp/health`` once the parent FastAPI app mounts this sub-app at
    # ``/mcp``). It is registered OUTSIDE the token-auth dependency: the
    # MCP transport itself sits behind :class:`MCPAuthMiddleware`, but
    # load balancers do not authenticate, so the auth middleware below
    # explicitly bypasses the health path (see HEALTH_PATH / the dispatch
    # short-circuit in src.mcp.auth).
    #
    # The handler is intentionally thin (Architecture Principle #1): it
    # delegates every check to :func:`src.mcp.health.check_mcp_health`
    # and only maps the structured result onto an HTTP status code. The
    # probe is in-process only — NO DB round-trip — to stay inside the
    # #390 <100 ms budget.
    # ------------------------------------------------------------------
    from starlette.routing import Route as _Route

    async def _mcp_health_route(request):  # noqa: ANN001 - Starlette handler
        # Imported lazily inside the handler so the health service module
        # (and its process-start timestamp) initialises with the rest of
        # the app rather than at server-build time.
        from src.mcp.health import check_mcp_health

        result = await check_mcp_health(mcp_server)
        status_code = 200 if result.healthy else 503
        return JSONResponse(result.to_payload(), status_code=status_code)

    transport_app.router.routes.append(
        _Route("/health", _mcp_health_route, methods=["GET"], name="mcp_health")
    )

    # Wrap the transport in our dev-auth gate. We use Starlette's
    # add_middleware (not FastAPI's) because the transport is a plain
    # Starlette application. The middleware bypasses the health path so the
    # probe works with no token (see src.mcp.auth.HEALTH_PATH).
    transport_app.add_middleware(MCPAuthMiddleware)

    return mcp_server, transport_app

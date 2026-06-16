"""Valdo MCP (Model Context Protocol) sub-application package.

Exposes a Streamable HTTP MCP server that is mounted onto the main FastAPI
application at ``/mcp``. See :mod:`src.mcp.server` for the builder and
:mod:`src.mcp.taxonomy` for the live engine-introspection used by the
``taxonomy://*`` resources.

Stories landed:

* EF-S1 — scaffold + Streamable HTTP handshake + dev-mode auth gate.
* EF-S2 — read-only tools (``list_sources``, ``get_source_spec``,
  ``list_recent_runs``); see :mod:`src.mcp.tools`.
* EF-S3 — ``taxonomy://violations`` and ``taxonomy://rules`` resources.
* EF-S4 — action tools (``validate_file``, ``get_run_status``,
  ``get_violations``); see :mod:`src.mcp.action_tools`.
* S7-2 — ``templates://etl/list``, ``templates://etl/<shape>``, and
  ``templates://etl/<shape>/sample`` resources backed by
  :mod:`src.mcp.resources.etl_templates`.

Stories pending: EF-S6 (prompt templates), EF-S7 (real LDAPS + X-API-Key
auth bridge).
"""

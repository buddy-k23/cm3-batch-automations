"""MCP resource adapters for Valdo (S7-2 onwards).

Submodules in this package back the agent-facing ``*://*`` resources
registered on the FastMCP server in :mod:`src.mcp.server`. Each
submodule is a thin adapter over an underlying engine artefact
(templates, taxonomy snapshots, etc.) and is intentionally
side-effect-free so the registration layer in ``server.py`` stays a
declarative manifest.

Stories landed:

* S7-2 — ``templates://etl/*`` resources backed by
  :mod:`src.mcp.resources.etl_templates`.
* S7-3 — ``formats://supported`` resource backed by
  :mod:`src.mcp.resources.formats`.
"""

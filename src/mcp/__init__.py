"""Valdo MCP (Model Context Protocol) sub-application package.

Exposes a Streamable HTTP MCP server that is mounted onto the main FastAPI
application at ``/mcp``. See :mod:`src.mcp.server` for the builder and
:mod:`src.mcp.taxonomy` for the live engine-introspection used by the
``taxonomy://*`` resources.

Stories landed:

* EF-S1 — scaffold + Streamable HTTP handshake + dev-mode auth gate.
* EF-S3 — ``taxonomy://violations`` and ``taxonomy://rules`` resources.

Stories pending: EF-S2 (validation tool surface), EF-S6 (prompt templates),
EF-S7 (real LDAPS + X-API-Key auth bridge).
"""

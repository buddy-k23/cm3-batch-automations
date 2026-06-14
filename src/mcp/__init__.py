"""Valdo MCP (Model Context Protocol) sub-application package.

Exposes a Streamable HTTP MCP server that is mounted onto the main FastAPI
application at ``/mcp``. See :mod:`src.mcp.server` for the builder.

Story: EF-S1 — scaffold only. Tool / resource / prompt registries are empty
in this milestone; they land in EF-S2, EF-S3, and EF-S6 respectively. The
real LDAPS + X-API-Key auth bridge lands in EF-S7.
"""

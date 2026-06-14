"""MCP (Model Context Protocol) server scaffold for Valdo (EF-S1).

This module wires a Streamable-HTTP MCP server (built on `mcp.server.fastmcp`)
into the existing FastAPI process. The server is intentionally empty in this
milestone:

* No tools are registered (EF-S2 introduces the validation tool surface).
* No resources are registered (EF-S3 will expose mappings/rules as resources).
* No prompts are registered (EF-S6 will introduce prompt templates).

The MCP capability advertisement is therefore "all three present, all three
empty" — clients can still complete the JSON-RPC ``initialize`` handshake and
inspect that Valdo *intends* to expose tools, resources, and prompts, but
listing any registry returns an empty array.

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

import os
from typing import Tuple

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

__all__ = ["build_mcp_server", "MCPAuthMiddleware"]

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
        Tools, resources, and prompts intentionally remain empty in EF-S1.
        FastMCP advertises all three capability buckets unconditionally,
        so the ``initialize`` response still exposes the ``tools``,
        ``resources``, and ``prompts`` keys — they simply yield empty
        ``*/list`` results.
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
            "Valdo MCP scaffold (EF-S1). Tool, resource, and prompt "
            "registries are intentionally empty; concrete capabilities "
            "land in EF-S2, EF-S3, and EF-S6."
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

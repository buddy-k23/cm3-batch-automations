"""MCP-aware health probe service (S9-2, #390).

The FastAPI process can be perfectly alive — ``/api/v1/system/health``
returns 200 — while the *MCP* surface is wedged: the Streamable-HTTP
session manager never entered its run loop, a resource handler started
raising, or a tool registration regressed to zero. A load balancer
polling the process-liveness endpoint would keep routing BA agents at a
broken MCP transport.

This module is the **service layer** behind ``GET /mcp/health`` (the
route itself is a thin Starlette handler registered in
:func:`src.mcp.server.build_mcp_server`, per Architecture Principle #1 —
no business logic in the routing layer). It exercises the MCP-specific
paths, *not* just process liveness:

1. **Session-manager liveness** — confirm the FastMCP session manager has
   entered its ``run()`` task group. This is the in-process analogue of a
   successful ``initialize`` handshake: if the session manager is not
   running, every ``/mcp/`` JSON-RPC call (including ``initialize``) would
   fail, so the probe treats a dead manager as the primary failure mode.
2. **Resource read** — actually read the ``taxonomy://violations``
   resource through the registered handler. A handler that raises (engine
   import broke, introspection regressed) fails the probe even though the
   process is fine.
3. **Registry enumeration** — list the registered tools, resources, and
   prompts and report their counts. The counts are computed from the live
   registry; the probe never hardcodes a magic literal (the expected tool
   count moves every sprint — pinning it here would rot).

Latency budget: every check is in-process and touches only the FastMCP
registries / the engine-introspection taxonomy. There is deliberately
**no DB round-trip** so the endpoint answers in well under the #390
100 ms target even under frequent load-balancer polling.

Failure contract: any check that fails collapses to a structured
:class:`MCPHealthResult` with ``healthy=False``, a machine-readable
``failed_check`` token (``"session_manager"`` | ``"resource_read"`` |
``"registry"``), and a human ``reason``. The router maps an unhealthy
result to HTTP 503; the reason string is operator-facing and never echoes
a raw stack trace to anonymous callers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

__all__ = [
    "MCPHealthResult",
    "MCP_PROTOCOL_VERSION",
    "check_mcp_health",
]

# Process start timestamp, captured at module import. ``uptime_seconds`` in
# the health payload is measured against this — a cheap, dependency-free
# liveness signal an SRE can correlate against a deploy/restart event.
_PROCESS_START_MONOTONIC = time.monotonic()

# URI of the resource the probe reads to prove the resource-handler path
# works. Kept as a module constant (not a literal in the check) so it
# stays in lockstep with :data:`src.mcp.server.TAXONOMY_VIOLATIONS_URI`.
_VIOLATIONS_URI = "taxonomy://violations"


def _resolve_protocol_version() -> str:
    """Return the MCP protocol version this server speaks.

    Sourced from ``mcp.types.LATEST_PROTOCOL_VERSION`` so the value tracks
    the installed ``mcp`` library rather than a hand-maintained string that
    could drift out of sync with the actual handshake. Falls back to an
    explicit ``"unknown"`` sentinel if the library shape changes, so the
    probe never raises just to stamp a version.

    Returns:
        The protocol-version string (e.g. ``"2025-11-25"``), or
        ``"unknown"`` if it cannot be resolved.
    """
    try:
        from mcp.types import LATEST_PROTOCOL_VERSION

        return str(LATEST_PROTOCOL_VERSION)
    except Exception:  # pragma: no cover - defensive; library always exports it
        return "unknown"


MCP_PROTOCOL_VERSION = _resolve_protocol_version()


@dataclass(frozen=True)
class MCPHealthResult:
    """Outcome of an MCP health probe.

    Attributes:
        healthy: ``True`` when every internal check passed.
        protocol_version: The MCP protocol version the server advertises.
        tool_count: Number of registered tools (live registry count).
        resource_count: Number of registered (static-URI) resources.
        prompt_count: Number of registered prompts.
        uptime_seconds: Whole seconds since this process imported the
            health module — a proxy for "since the MCP server came up".
        failed_check: ``None`` when healthy; otherwise a machine-readable
            token identifying the first failed stage
            (``"session_manager"`` | ``"resource_read"`` | ``"registry"``).
        reason: ``None`` when healthy; otherwise a short operator-facing
            explanation. Never contains a raw traceback.
    """

    healthy: bool
    protocol_version: str
    tool_count: int
    resource_count: int
    prompt_count: int
    uptime_seconds: int
    failed_check: Optional[str] = None
    reason: Optional[str] = None

    def to_payload(self) -> dict[str, Any]:
        """Render the result as the JSON body the ``/mcp/health`` route returns.

        Healthy results match the #390 schema exactly
        (``status="healthy"`` + counts + version + uptime). Unhealthy
        results swap in ``status="unhealthy"`` and surface the
        ``failed_check`` / ``reason`` so the operator (and the failing
        load-balancer log line) can see *why* without a token.

        Returns:
            A JSON-serialisable dict.
        """
        if self.healthy:
            return {
                "status": "healthy",
                "mcp_protocol_version": self.protocol_version,
                "tool_count": self.tool_count,
                "resource_count": self.resource_count,
                "prompt_count": self.prompt_count,
                "uptime_seconds": self.uptime_seconds,
            }
        return {
            "status": "unhealthy",
            "mcp_protocol_version": self.protocol_version,
            "tool_count": self.tool_count,
            "resource_count": self.resource_count,
            "prompt_count": self.prompt_count,
            "uptime_seconds": self.uptime_seconds,
            "failed_check": self.failed_check,
            "reason": self.reason,
        }


def _uptime_seconds() -> int:
    """Return whole seconds since the health module was imported."""
    return int(time.monotonic() - _PROCESS_START_MONOTONIC)


def _session_manager_is_live(server: FastMCP) -> bool:
    """Report whether the FastMCP session manager has entered ``run()``.

    Accessing ``server.session_manager`` before ``streamable_http_app()``
    has been called raises ``RuntimeError`` ("Session manager can only be
    accessed after calling streamable_http_app()"). The parent FastAPI
    lifespan calls ``streamable_http_app()`` at build time and then enters
    ``session_manager.run()`` for the duration of the serving window, which
    sets the manager's ``_has_started`` flag and populates ``_task_group``.

    We treat "session manager exists AND has started AND holds a live task
    group" as the in-process equivalent of a healthy ``initialize``
    handshake: if that task group is not running, the Streamable-HTTP
    transport cannot service a single JSON-RPC request.

    This function is module-level (not a closure) specifically so tests can
    monkeypatch it to simulate a wedged manager without spinning up a real
    broken transport.

    Args:
        server: The FastMCP server instance.

    Returns:
        ``True`` if the session manager is running; ``False`` otherwise.
    """
    try:
        manager = server.session_manager
    except Exception:
        # streamable_http_app() was never called, or the internals moved.
        return False
    started = bool(getattr(manager, "_has_started", False))
    task_group = getattr(manager, "_task_group", None)
    return started and task_group is not None


async def _read_violations_resource(server: FastMCP) -> None:
    """Read ``taxonomy://violations`` through the registered handler.

    Exercising a real ``resources/read`` proves the resource-handler path
    is wired and the engine-introspection taxonomy still imports cleanly.
    We discard the content — the probe cares only that the read completes
    without raising and yields at least one content part.

    Module-level (not a closure) so tests can monkeypatch it to simulate a
    broken resource handler.

    Args:
        server: The FastMCP server instance.

    Raises:
        Exception: Whatever the underlying resource handler raises, or a
            ``RuntimeError`` if the read yields no content.
    """
    contents = await server.read_resource(_VIOLATIONS_URI)
    parts = list(contents)
    if not parts:
        raise RuntimeError(
            f"{_VIOLATIONS_URI} read returned no content parts"
        )


async def check_mcp_health(server: FastMCP) -> MCPHealthResult:
    """Run the full MCP health probe and return a structured result.

    Stages, in order (first failure short-circuits):

    1. Session-manager liveness (:func:`_session_manager_is_live`).
    2. ``taxonomy://violations`` resource read
       (:func:`_read_violations_resource`).
    3. Registry enumeration — list tools, resources, and prompts and count
       them from the live registry.

    All stages are in-process; there is no DB round-trip, keeping the probe
    inside the #390 <100 ms budget.

    Args:
        server: The FastMCP server instance to probe (the same instance the
            FastAPI lifespan entered ``session_manager.run()`` on).

    Returns:
        :class:`MCPHealthResult` — ``healthy=True`` with the live counts on
        success, or ``healthy=False`` with ``failed_check`` / ``reason`` on
        the first failed stage.
    """
    uptime = _uptime_seconds()

    # Stage 1 — session manager / handshake liveness.
    if not _session_manager_is_live(server):
        logger.warning("mcp_health failed_check=session_manager")
        return MCPHealthResult(
            healthy=False,
            protocol_version=MCP_PROTOCOL_VERSION,
            tool_count=0,
            resource_count=0,
            prompt_count=0,
            uptime_seconds=uptime,
            failed_check="session_manager",
            reason=(
                "MCP session manager is not running — the Streamable-HTTP "
                "transport cannot complete an initialize handshake."
            ),
        )

    # Stage 2 — resource handler liveness.
    try:
        await _read_violations_resource(server)
    except Exception as exc:
        logger.warning("mcp_health failed_check=resource_read error=%s", exc)
        return MCPHealthResult(
            healthy=False,
            protocol_version=MCP_PROTOCOL_VERSION,
            tool_count=0,
            resource_count=0,
            prompt_count=0,
            uptime_seconds=uptime,
            failed_check="resource_read",
            reason=(
                f"Reading the {_VIOLATIONS_URI} resource failed — the "
                "resource handler is not serving."
            ),
        )

    # Stage 3 — registry enumeration. Counts are derived from the live
    # registry; we never compare against a hardcoded expected literal.
    try:
        tool_count = len(await server.list_tools())
        resource_count = len(await server.list_resources())
        prompt_count = len(await server.list_prompts())
    except Exception as exc:
        logger.warning("mcp_health failed_check=registry error=%s", exc)
        return MCPHealthResult(
            healthy=False,
            protocol_version=MCP_PROTOCOL_VERSION,
            tool_count=0,
            resource_count=0,
            prompt_count=0,
            uptime_seconds=uptime,
            failed_check="registry",
            reason="Enumerating the MCP tool/resource/prompt registry failed.",
        )

    if tool_count <= 0:
        # A zero-tool registry means the registration pass regressed — the
        # server would advertise an empty capability surface to agents.
        logger.warning("mcp_health failed_check=registry tool_count=0")
        return MCPHealthResult(
            healthy=False,
            protocol_version=MCP_PROTOCOL_VERSION,
            tool_count=0,
            resource_count=resource_count,
            prompt_count=prompt_count,
            uptime_seconds=uptime,
            failed_check="registry",
            reason="MCP tool registry is empty — no tools are registered.",
        )

    return MCPHealthResult(
        healthy=True,
        protocol_version=MCP_PROTOCOL_VERSION,
        tool_count=tool_count,
        resource_count=resource_count,
        prompt_count=prompt_count,
        uptime_seconds=uptime,
    )

"""In-process sliding-window rate limiter for the MCP ``/mcp/`` route (S9-3, #388).

This module provides defence-in-depth throttling for the MCP JSON-RPC
surface. It sits in the request path *after*
:class:`src.mcp.auth.MCPAuthMiddleware` has resolved the caller's identity
(so per-token limiting keys on the authenticated principal) and uses the
proxy-corrected client IP recovered by S9-1's ``ProxyHeadersMiddleware``
(so per-IP limiting sees the real agent, not the nginx hop).

Design
------
Three concerns, deliberately separated so each is unit-testable in
isolation and the middleware that calls them stays thin (Architecture
Principle #1):

1. :func:`classify_request` — a pure function over a parsed JSON-RPC body
   that answers "is this a billable tool call, and which tool?". Only
   ``tools/call`` requests are rate-limited; ``resources/read`` and the
   handshake methods (``initialize`` / ``*/list`` / ``ping``) are exempt
   because they are cheap and idempotent.

2. :class:`InMemorySlidingWindowBackend` — the counter primitive. A
   sliding window of per-key hit timestamps; a hit is admitted when the
   number of timestamps still inside the window is below the cap, else
   rejected with a ``retry_after`` equal to the whole seconds until the
   oldest in-window hit ages out. The backend implements the
   :class:`RateLimitBackend` protocol so a Redis (or other shared-state)
   backend can drop in later for multi-node deployments **without**
   adding Redis as a dependency now — in-memory is the only
   implementation this story ships. The window clock is **injectable**
   (``clock`` callable) so tests are deterministic and never sleep.

3. :class:`RateLimiter` — the env-configured facade the middleware calls.
   It owns three logical counters per request:

   * per-token tool calls — default 30/min
     (``VALDO_MCP_RATE_LIMIT_PER_MINUTE``);
   * per-IP tool calls — default 60/min, wider as a token-theft backstop
     (``VALDO_MCP_RATE_LIMIT_PER_IP_PER_MINUTE``);
   * per-token ``get_run_status`` polls — default 240/min, a separate
     elevated counter so a BA polling a long run is not self-DOSed by the
     normal cap (``VALDO_MCP_RATE_LIMIT_RUN_STATUS_PER_MINUTE``).

All caps come from environment variables with documented defaults
(Architecture Principle #5 — config from settings, not literals).

Concurrency
-----------
The in-memory backend guards its state with a ``threading.Lock``. FastMCP
dispatches tool calls from a threadpool, and the Starlette middleware runs
on the event loop, so a single shared lock around the small per-key
deques is sufficient and cheap.

Out of scope (per the Sprint 9 kickoff)
---------------------------------------
Multi-node / distributed rate-limit state. Process-memory is enough for
the INT pilot; a shared backend behind :class:`RateLimitBackend` is a
scale-out follow-up.
"""

from __future__ import annotations

import os
import threading
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable, Deque, Dict, Optional, Protocol

__all__ = [
    "DEFAULT_PER_TOKEN_PER_MINUTE",
    "DEFAULT_PER_IP_PER_MINUTE",
    "DEFAULT_RUN_STATUS_PER_MINUTE",
    "ENV_PER_TOKEN_PER_MINUTE",
    "ENV_PER_IP_PER_MINUTE",
    "ENV_RUN_STATUS_PER_MINUTE",
    "WINDOW_SECONDS",
    "RateLimitDecision",
    "RequestClassification",
    "RateLimitBackend",
    "InMemorySlidingWindowBackend",
    "RateLimiter",
    "classify_request",
]


# ---------------------------------------------------------------------------
# Configuration — env var names + documented defaults (Architecture #5)
# ---------------------------------------------------------------------------

# Per-token tool-call budget. A token (authenticated principal) gets this
# many billable tool calls per rolling minute.
ENV_PER_TOKEN_PER_MINUTE = "VALDO_MCP_RATE_LIMIT_PER_MINUTE"
DEFAULT_PER_TOKEN_PER_MINUTE = 30

# Per-IP tool-call budget — wider than per-token as a defence-in-depth
# backstop against token theft (a stolen token used from one box still
# hits this wall; a thief rotating tokens from one IP is caught here).
ENV_PER_IP_PER_MINUTE = "VALDO_MCP_RATE_LIMIT_PER_IP_PER_MINUTE"
DEFAULT_PER_IP_PER_MINUTE = 60

# Elevated per-token budget for ``get_run_status`` polling. BAs poll long
# validation runs; the normal 30/min cap would self-DOS them, so polls
# use a separate, higher counter. 240/min ≈ one poll every 250 ms.
ENV_RUN_STATUS_PER_MINUTE = "VALDO_MCP_RATE_LIMIT_RUN_STATUS_PER_MINUTE"
DEFAULT_RUN_STATUS_PER_MINUTE = 240

# Rolling-window width. One minute is the natural unit for "per-minute"
# caps; kept as a module constant rather than a literal so the seam is
# obvious if a future story wants a configurable window.
WINDOW_SECONDS = 60

# The tool name whose polling gets the elevated cap. Kept as a constant so
# the middleware and the facade agree on the spelling without a literal in
# two places.
RUN_STATUS_TOOL = "get_run_status"

# JSON-RPC method that carries a billable tool invocation.
_TOOLS_CALL_METHOD = "tools/call"


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RateLimitDecision:
    """Outcome of a single rate-limit check.

    Attributes:
        allowed: ``True`` when the hit is admitted, ``False`` when the cap
            is exceeded.
        retry_after: Whole seconds until a slot frees (the time until the
            oldest in-window hit ages out). ``0`` when ``allowed`` is
            ``True``. Surfaced verbatim in the HTTP ``Retry-After`` header
            on a 429.
        scope: Which budget rejected the hit — ``"per_token"`` or
            ``"per_ip"`` — or ``None`` when admitted. Lets the middleware
            log *why* a caller was throttled without re-deriving it.
    """

    allowed: bool
    retry_after: int
    scope: Optional[str] = None


@dataclass(frozen=True)
class RequestClassification:
    """How the limiter should treat one JSON-RPC request.

    Attributes:
        is_tool_call: ``True`` only for ``tools/call`` requests — the sole
            billable surface. Resource reads, handshakes, and list calls
            are ``False`` (exempt).
        tool_name: The invoked tool's name for a tool call, else ``None``.
            Used to route ``get_run_status`` to its elevated counter.
    """

    is_tool_call: bool
    tool_name: Optional[str] = None


# ---------------------------------------------------------------------------
# Request classification — pure function over the parsed JSON-RPC body
# ---------------------------------------------------------------------------


def classify_request(body: Any) -> RequestClassification:
    """Classify a parsed JSON-RPC body for rate-limiting purposes.

    Only ``tools/call`` requests are billable. Everything else —
    ``resources/read`` (cheap, idempotent), the ``initialize`` handshake,
    the ``*/list`` discovery calls, ``ping`` — is exempt.

    The function is intentionally total: a malformed or non-dict body
    (e.g. a parse failure upstream) classifies as *not a tool call* so the
    limiter fails open on garbage rather than 500-ing. Auth has already
    gated the request, so a body the limiter cannot parse is harmless to
    let through to the transport, which will reject it itself.

    Args:
        body: The decoded JSON-RPC request body — normally a dict, but any
            value is accepted defensively.

    Returns:
        A :class:`RequestClassification`.
    """
    if not isinstance(body, dict):
        return RequestClassification(is_tool_call=False)
    if body.get("method") != _TOOLS_CALL_METHOD:
        return RequestClassification(is_tool_call=False)
    params = body.get("params")
    tool_name: Optional[str] = None
    if isinstance(params, dict):
        name = params.get("name")
        if isinstance(name, str) and name:
            tool_name = name
    return RequestClassification(is_tool_call=True, tool_name=tool_name)


# ---------------------------------------------------------------------------
# Backend protocol + in-memory sliding-window implementation
# ---------------------------------------------------------------------------


class RateLimitBackend(Protocol):
    """Pluggable counter contract.

    A single method records a hit against ``key`` under ``cap`` and returns
    the admission decision. Keeping this a one-method protocol means a
    Redis (or memcached, or DB) backend can drop in for multi-node
    deployments without touching :class:`RateLimiter` or the middleware —
    the in-memory backend below is the only implementation this story
    ships, per the Sprint 9 scope.
    """

    def hit(self, key: str, cap: int) -> RateLimitDecision:  # pragma: no cover - protocol
        """Record a hit against *key*; admit it if within *cap*."""
        ...


class InMemorySlidingWindowBackend:
    """Process-local sliding-window counter.

    Each ``key`` owns a deque of hit timestamps. On every :meth:`hit` the
    deque is pruned of timestamps older than ``window_seconds``; the hit is
    admitted iff fewer than ``cap`` timestamps remain in the window. When
    rejected, ``retry_after`` is the whole seconds until the *oldest*
    in-window timestamp ages out (i.e. when the next slot frees).

    The clock is injected (default :func:`time.monotonic`) so tests can
    advance time deterministically without sleeping.

    Thread-safe: a single lock guards the per-key deques. FastMCP's
    threadpool dispatch plus the event-loop middleware mean concurrent
    hits are possible; the critical section is tiny (a prune + length
    check + append).
    """

    def __init__(
        self,
        window_seconds: int = WINDOW_SECONDS,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Construct an in-memory sliding-window backend.

        Args:
            window_seconds: Width of the rolling window in seconds.
            clock: Zero-arg callable returning a monotonically increasing
                float of seconds. Defaults to :func:`time.monotonic`.
                Injected in tests for determinism.
        """
        import time

        self._window = float(window_seconds)
        self._clock: Callable[[], float] = clock or time.monotonic
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, cap: int) -> RateLimitDecision:
        """Record a hit against *key* and decide admission under *cap*.

        Args:
            key: The bucket identity (e.g. ``"token:<id>"`` or
                ``"ip:<addr>"``).
            cap: Maximum hits permitted within the window. A cap ``<= 0``
                rejects unconditionally (defensive; not used by the
                facade, which clamps caps to ``>= 1``).

        Returns:
            A :class:`RateLimitDecision`. On rejection no timestamp is
            recorded, so a blocked caller does not push its own slot
            further into the future.
        """
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            bucket = self._hits[key]
            # Prune everything that has aged out of the window.
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()

            if cap <= 0 or len(bucket) >= cap:
                # Rejected — compute seconds until the oldest hit expires.
                if bucket:
                    seconds_until_free = (bucket[0] + self._window) - now
                else:
                    seconds_until_free = self._window
                # Round up so Retry-After never under-promises a free slot;
                # floor at 1 so a sub-second remainder still tells the
                # client to back off for at least a second.
                retry_after = max(1, _ceil_int(seconds_until_free))
                return RateLimitDecision(
                    allowed=False, retry_after=retry_after, scope=None
                )

            bucket.append(now)
            return RateLimitDecision(allowed=True, retry_after=0, scope=None)

    def reset(self) -> None:
        """Drop all counters. Test-only — production code MUST NOT call."""
        with self._lock:
            self._hits.clear()


def _ceil_int(value: float) -> int:
    """Ceil a non-negative float to an int without importing math.ceil.

    Args:
        value: A non-negative number of seconds.

    Returns:
        The smallest integer ``>= value``.
    """
    truncated = int(value)
    return truncated if truncated == value else truncated + 1


# ---------------------------------------------------------------------------
# Facade — env-configured, the surface the middleware calls
# ---------------------------------------------------------------------------


class RateLimiter:
    """Env-configured per-token + per-IP rate limiter for ``/mcp/``.

    Holds the three resolved caps and a single :class:`RateLimitBackend`.
    The backend namespaces keys by scope so the per-token, per-IP, and
    elevated ``get_run_status`` counters never collide:

    * ``token:<id>``           — normal per-token tool budget
    * ``ip:<addr>``            — per-IP tool budget
    * ``runstatus:<id>``       — elevated per-token get_run_status budget

    Use :meth:`from_env` to build one; construct directly only in tests
    that want to pin caps without env juggling.
    """

    def __init__(
        self,
        per_token_cap: int,
        per_ip_cap: int,
        run_status_cap: int,
        backend: Optional[RateLimitBackend] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        """Construct a limiter with explicit caps.

        Args:
            per_token_cap: Per-token tool-call cap per minute.
            per_ip_cap: Per-IP tool-call cap per minute.
            run_status_cap: Elevated per-token ``get_run_status`` cap per
                minute.
            backend: Counter backend; defaults to a fresh
                :class:`InMemorySlidingWindowBackend`.
            clock: Injected clock forwarded to the default backend (ignored
                when an explicit *backend* is supplied).
        """
        self.per_token_cap = max(1, int(per_token_cap))
        self.per_ip_cap = max(1, int(per_ip_cap))
        self.run_status_cap = max(1, int(run_status_cap))
        self._backend: RateLimitBackend = backend or InMemorySlidingWindowBackend(
            window_seconds=WINDOW_SECONDS, clock=clock
        )

    @classmethod
    def from_env(
        cls,
        backend: Optional[RateLimitBackend] = None,
        clock: Optional[Callable[[], float]] = None,
    ) -> "RateLimiter":
        """Build a limiter from ``VALDO_MCP_RATE_LIMIT_*`` env vars.

        Each cap falls back to its documented default when the env var is
        unset or not a positive integer (a malformed value must not
        silently disable throttling — it falls back to the safe default).

        Args:
            backend: Optional explicit backend (shared store seam).
            clock: Optional injected clock for the default backend.

        Returns:
            A configured :class:`RateLimiter`.
        """
        return cls(
            per_token_cap=_env_int(ENV_PER_TOKEN_PER_MINUTE, DEFAULT_PER_TOKEN_PER_MINUTE),
            per_ip_cap=_env_int(ENV_PER_IP_PER_MINUTE, DEFAULT_PER_IP_PER_MINUTE),
            run_status_cap=_env_int(ENV_RUN_STATUS_PER_MINUTE, DEFAULT_RUN_STATUS_PER_MINUTE),
            backend=backend,
            clock=clock,
        )

    def check_tool_call(
        self,
        token_id: str,
        client_ip: str,
        tool_name: Optional[str],
    ) -> RateLimitDecision:
        """Apply the rate-limit budgets to one tool call.

        Order of evaluation:

        1. Per-token budget. ``get_run_status`` is metered on its own
           elevated counter (``runstatus:<id>``); every other tool shares
           the normal ``token:<id>`` counter. A token that hammers
           ``get_run_status`` therefore does not erode its normal tool
           budget, and vice-versa.
        2. Per-IP budget. Always the normal ``ip:<addr>`` counter — the
           IP backstop covers all tools uniformly (it is the token-theft
           defence, not a per-tool quota).

        The per-token check runs first so a single misbehaving token is
        attributed to ``per_token`` rather than masked by the wider IP
        budget. Only when the per-token check passes is an IP slot
        consumed — so a token-throttled call does not also burn the
        caller's IP allowance.

        Args:
            token_id: Stable identity of the authenticated principal
                (see :func:`token_identity`).
            client_ip: Proxy-corrected client IP.
            tool_name: The invoked tool name, or ``None`` if the body did
                not carry one (treated as a normal tool).

        Returns:
            A :class:`RateLimitDecision`. ``scope`` names the binding
            budget on rejection.
        """
        if tool_name == RUN_STATUS_TOOL:
            token_key = f"runstatus:{token_id}"
            token_cap = self.run_status_cap
        else:
            token_key = f"token:{token_id}"
            token_cap = self.per_token_cap

        token_decision = self._backend.hit(token_key, token_cap)
        if not token_decision.allowed:
            return RateLimitDecision(
                allowed=False,
                retry_after=token_decision.retry_after,
                scope="per_token",
            )

        ip_decision = self._backend.hit(f"ip:{client_ip}", self.per_ip_cap)
        if not ip_decision.allowed:
            return RateLimitDecision(
                allowed=False,
                retry_after=ip_decision.retry_after,
                scope="per_ip",
            )

        return RateLimitDecision(allowed=True, retry_after=0, scope=None)


def _env_int(name: str, default: int) -> int:
    """Read a positive-int env var, falling back to *default* on anything else.

    A missing, empty, non-numeric, or non-positive value all resolve to
    *default* — a misconfigured cap must never silently disable the
    limiter.

    Args:
        name: Environment variable name.
        default: Fallback value when unset/invalid.

    Returns:
        The parsed positive integer, or *default*.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw.strip())
    except (ValueError, AttributeError):
        return default
    return value if value > 0 else default


# ---------------------------------------------------------------------------
# Identity helpers — keep the middleware thin
# ---------------------------------------------------------------------------


def token_identity(principal: Any) -> str:
    """Derive a stable per-token bucket key from an :class:`MCPPrincipal`.

    S9-4 will add a ``jti`` to the token format; until then the principal's
    ``user`` plus ``auth_kind`` is the most stable available identity (an
    API-key caller's ``user`` is a 6-char key suffix, a token caller's is
    their username). The function is defensive — a ``None`` principal (dev
    mode without a seeded principal, or a transport edge) collapses to a
    fixed ``"anonymous"`` key so the limiter still meters the stream.

    Args:
        principal: The authenticated principal, or ``None``.

    Returns:
        A non-empty string suitable as a bucket key.
    """
    if principal is None:
        return "anonymous"
    user = getattr(principal, "user", None) or "anonymous"
    auth_kind = getattr(principal, "auth_kind", None) or "unknown"
    return f"{auth_kind}:{user}"


def client_ip_of(request: Any) -> str:
    """Extract the proxy-corrected client IP from a Starlette request.

    Relies on S9-1's ``ProxyHeadersMiddleware`` having already rewritten
    ``request.client.host`` from ``X-Forwarded-For`` when the peer is a
    trusted proxy. Falls back to ``"unknown"`` when the client tuple is
    absent (e.g. an in-process test transport).

    Args:
        request: The Starlette/FastAPI request.

    Returns:
        The client IP string, or ``"unknown"``.
    """
    client = getattr(request, "client", None)
    host = getattr(client, "host", None) if client is not None else None
    return host or "unknown"

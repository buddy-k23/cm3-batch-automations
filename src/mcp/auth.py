"""MCP auth bridge (EF-S7) — LDAPS + X-API-Key + stdio token.

This module replaces the EF-S1 dev-mode placeholder (``MCPAuthMiddleware``
in :mod:`src.mcp.server`) with a production-grade auth bridge that mirrors
the auth posture of the existing Valdo HTTP API.

Three transport modes are supported:

1. **HTTP — X-API-Key.** When the MCP sub-app is mounted on the FastAPI
   process (``valdo serve``), the middleware accepts requests carrying an
   ``X-API-Key`` header that validates against the same ``API_KEYS`` env
   var the rest of the API uses (:func:`src.api.auth.verify_api_key`).
2. **HTTP — session cookie.** When the LDAPS bridge is enabled
   (``auth.enabled: true`` in ``config/ui.yml``), the middleware accepts
   the signed Starlette session cookie minted by ``POST /auth/login``
   (:func:`src.api.auth._try_resolve_session_cookie`). Session wins when
   both header and cookie are present (interactive user precedence).
3. **Stdio — signed bearer token.** When the MCP server runs over stdio
   (Mac→RHEL agentic flow with no HTTP machinery), the token loader
   reads ``~/.valdo/mcp-token`` and verifies its HMAC-SHA256 signature
   against the ``VALDO_MCP_TOKEN_SIGNING_KEY`` secret. The same token
   format is also accepted on HTTP via ``Authorization: Bearer <token>``
   so agents that already hold a token can use it interchangeably.

A dev-mode bypass (``VALDO_MCP_AUTH=dev``) is preserved for local
testing — it remains a strict opt-in, never the default. In any other
mode the middleware fails closed with HTTP 401.

Token shape:

.. code-block:: json

    {
      "user": "jsmith",
      "principal_dn": "CN=jsmith,OU=Users,DC=bank,DC=internal",
      "role": "admin",
      "issued_at": 1718323200,
      "expires_at": 1718366400,
      "signature": "9f3e...b4"
    }

The ``signature`` field is the hex-encoded HMAC-SHA256 of
``user|principal_dn|role|issued_at|expires_at`` (UTF-8, pipe-separated),
keyed with ``VALDO_MCP_TOKEN_SIGNING_KEY``. Token verification is
constant-time (``hmac.compare_digest``) and out-of-band revocation is
intentionally out of scope — rotate the signing key to invalidate all
outstanding tokens.

SECURITY NOTES
--------------
* Tokens are self-contained — server never persists them.
* Default TTL: 12 hours. Hard upper bound: 48 hours.
* Tokens are HMAC-signed, **not** encrypted — they reveal the principal
  DN to anyone who can read the file. ``~/.valdo/mcp-token`` therefore
  MUST be written with ``0o600`` permissions; the CLI subcommand
  enforces this.
* The signing key is read fresh on every verification so key rotation
  takes effect immediately (no app restart required).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Env var that opens the dev-mode auth pass-through. The literal sentinel
# value is intentionally narrow ("dev") so accidental truthy values
# (e.g. "1", "true") do not silently disable auth.
DEV_AUTH_ENV_VAR = "VALDO_MCP_AUTH"
DEV_AUTH_SENTINEL = "dev"

# Env var that holds the HMAC-SHA256 signing key for stdio bearer tokens.
# Must be set in production (any non-dev mode) or the middleware refuses
# to verify tokens. The CLI ``valdo mcp-login`` flow likewise requires
# the server to have this configured.
TOKEN_SIGNING_KEY_ENV_VAR = "VALDO_MCP_TOKEN_SIGNING_KEY"

# Default location of the stdio bearer token on the user's machine.
# Overridable via ``VALDO_MCP_TOKEN_PATH`` for ops and tests.
DEFAULT_TOKEN_PATH = Path.home() / ".valdo" / "mcp-token"
TOKEN_PATH_ENV_VAR = "VALDO_MCP_TOKEN_PATH"

# TTL bounds for ``POST /api/v2/mcp/login`` and ``valdo mcp-login``.
DEFAULT_TOKEN_TTL_HOURS = 12
MAX_TOKEN_TTL_HOURS = 48

# Per-request context var that downstream MCP tool implementations can
# read via :func:`current_user`. Populated by the middleware after the
# request authenticates; populated by :func:`load_stdio_user` at startup
# for stdio transport.
_current_user_ctx: ContextVar[Optional["MCPPrincipal"]] = ContextVar(
    "valdo_mcp_current_user",
    default=None,
)


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MCPPrincipal:
    """The authenticated principal making the current MCP request.

    Attributes:
        user: Display name / username — short identifier suitable for
            logging. For API-key callers this is a 6-char suffix of the
            key; for LDAP callers it is the leftmost CN of the DN.
        principal_dn: Full LDAP DN when the principal is an LDAP user;
            empty string for API-key callers.
        role: Resolved Valdo role (``tester`` | ``mapping_owner`` |
            ``admin``). Mirrors the role assigned to the same caller on
            the parent FastAPI surface.
        auth_kind: How the principal was authenticated —
            ``"api_key"`` | ``"session"`` | ``"token"`` | ``"dev"``.
    """

    user: str
    principal_dn: str
    role: str
    auth_kind: str


@dataclass(frozen=True)
class TokenPayload:
    """Decoded + verified stdio bearer token payload.

    Mirrors :class:`MCPPrincipal` but adds the issued/expires timestamps
    so callers can surface remaining TTL. Tokens are HMAC-SHA256 signed
    against ``VALDO_MCP_TOKEN_SIGNING_KEY`` and self-contained — there is
    no server-side store to consult.
    """

    user: str
    principal_dn: str
    role: str
    issued_at: int
    expires_at: int


class TokenError(Exception):
    """Raised on any failure to load, decode, or verify a stdio token.

    The error message is intentionally informative for the CLI surface
    (``valdo mcp-login`` needs to tell the user *why* their token was
    rejected) but is NEVER echoed back to anonymous HTTP callers — the
    middleware catches and collapses to a generic 401.
    """


# ---------------------------------------------------------------------------
# Token mint / verify — HMAC-SHA256 with constant-time compare
# ---------------------------------------------------------------------------


def _signing_key() -> bytes:
    """Resolve the HMAC signing key, raising TokenError when unconfigured.

    Read fresh on every call so key rotation takes effect immediately —
    no app restart required.

    Returns:
        The signing key as raw bytes (UTF-8 encoded env value).

    Raises:
        TokenError: If ``VALDO_MCP_TOKEN_SIGNING_KEY`` is unset or empty.
    """
    key = os.environ.get(TOKEN_SIGNING_KEY_ENV_VAR, "")
    if not key:
        raise TokenError(
            f"{TOKEN_SIGNING_KEY_ENV_VAR} is not configured. "
            "Set it on the MCP server before issuing or verifying tokens."
        )
    return key.encode("utf-8")


def _signature_payload(
    user: str,
    principal_dn: str,
    role: str,
    issued_at: int,
    expires_at: int,
) -> bytes:
    """Build the canonical signing input as UTF-8 bytes.

    The five fields are joined with ``|`` (which cannot appear in an
    LDAP DN without escaping; even if it did, the join is unambiguous
    because the field order is fixed). Reordering or omitting fields
    invalidates the signature.
    """
    return f"{user}|{principal_dn}|{role}|{issued_at}|{expires_at}".encode("utf-8")


def mint_token(
    user: str,
    principal_dn: str,
    role: str,
    ttl_hours: int = DEFAULT_TOKEN_TTL_HOURS,
) -> dict:
    """Mint a signed token for a freshly-authenticated LDAP user.

    Args:
        user: Short user identifier (e.g. sAMAccountName).
        principal_dn: Full LDAP DN as returned by ldap_authenticate.
        role: Valdo role (``tester`` | ``mapping_owner`` | ``admin``).
        ttl_hours: How long the token should remain valid. Clamped to
            [1, :data:`MAX_TOKEN_TTL_HOURS`].

    Returns:
        Dict with keys ``user``, ``principal_dn``, ``role``,
        ``issued_at``, ``expires_at``, ``signature`` — directly
        JSON-serialisable. The caller writes this to ``~/.valdo/mcp-token``.

    Raises:
        TokenError: If the signing key is unconfigured.
    """
    if ttl_hours < 1:
        ttl_hours = 1
    if ttl_hours > MAX_TOKEN_TTL_HOURS:
        ttl_hours = MAX_TOKEN_TTL_HOURS

    issued_at = int(time.time())
    expires_at = issued_at + ttl_hours * 3600
    sig = hmac.new(
        _signing_key(),
        _signature_payload(user, principal_dn, role, issued_at, expires_at),
        hashlib.sha256,
    ).hexdigest()
    return {
        "user": user,
        "principal_dn": principal_dn,
        "role": role,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "signature": sig,
    }


def verify_token(payload: dict) -> TokenPayload:
    """Verify a token dict and return the decoded payload.

    Args:
        payload: Parsed JSON dict (i.e. the contents of the token file
            or the ``Authorization: Bearer <token>`` value after base64
            decode — see :func:`_decode_bearer`).

    Returns:
        :class:`TokenPayload` populated from the verified fields.

    Raises:
        TokenError: If the payload is malformed, the signature is
            invalid, or the token has expired.
    """
    required = ("user", "principal_dn", "role", "issued_at", "expires_at", "signature")
    missing = [k for k in required if k not in payload]
    if missing:
        raise TokenError(f"Token missing required fields: {missing}")

    try:
        issued_at = int(payload["issued_at"])
        expires_at = int(payload["expires_at"])
    except (TypeError, ValueError) as exc:
        raise TokenError("Token timestamps are not integers") from exc

    expected = hmac.new(
        _signing_key(),
        _signature_payload(
            str(payload["user"]),
            str(payload["principal_dn"]),
            str(payload["role"]),
            issued_at,
            expires_at,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, str(payload["signature"])):
        raise TokenError("Token signature does not match — token tampered or signing key rotated")

    now = int(time.time())
    if expires_at <= now:
        raise TokenError("Token has expired. Run 'valdo mcp-login' to mint a new one.")

    return TokenPayload(
        user=str(payload["user"]),
        principal_dn=str(payload["principal_dn"]),
        role=str(payload["role"]),
        issued_at=issued_at,
        expires_at=expires_at,
    )


# ---------------------------------------------------------------------------
# Token persistence helpers
# ---------------------------------------------------------------------------


def resolve_token_path() -> Path:
    """Resolve the on-disk path for the stdio bearer token.

    Honours the ``VALDO_MCP_TOKEN_PATH`` env var for ops + tests so
    integration tests can sandbox the token file under ``tmp_path``.

    Returns:
        Absolute path to the token file. The file may or may not exist.
    """
    override = os.environ.get(TOKEN_PATH_ENV_VAR)
    if override:
        return Path(override).expanduser().resolve()
    return DEFAULT_TOKEN_PATH


def write_token_file(payload: dict, target: Optional[Path] = None) -> Path:
    """Write a minted token to disk with strict 0600 permissions.

    The parent directory is created with ``0o700`` if necessary. After
    writing, the file mode is explicitly set to ``0o600`` to defeat any
    umask that might have widened it.

    Args:
        payload: The dict returned by :func:`mint_token`.
        target: Optional override path; defaults to
            :func:`resolve_token_path`.

    Returns:
        Absolute path the token was written to.
    """
    path = target if target is not None else resolve_token_path()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Write first, then chmod — atomic-enough for the local-only token
    # file. We don't bother with a tmpfile-rename pattern because the
    # write is small (<1 KB) and the user is the sole reader.
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(path, 0o600)
    return path


def load_token_file(source: Optional[Path] = None) -> TokenPayload:
    """Load and verify the token file from disk.

    Args:
        source: Optional override path; defaults to
            :func:`resolve_token_path`.

    Returns:
        Verified :class:`TokenPayload`.

    Raises:
        TokenError: If the file is missing, unreadable, malformed,
            signature-invalid, or expired.
    """
    path = source if source is not None else resolve_token_path()
    if not path.exists():
        raise TokenError(
            f"No MCP token found at {path}. Run 'valdo mcp-login' first."
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TokenError(f"Could not read token file {path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TokenError(f"Token file {path} is not valid JSON: {exc}") from exc
    return verify_token(payload)


# ---------------------------------------------------------------------------
# Current-user accessor (used by tools to audit who called them)
# ---------------------------------------------------------------------------


def current_user() -> Optional[MCPPrincipal]:
    """Return the authenticated principal for the current MCP call, if any.

    Reads from the per-request ``ContextVar`` populated by the auth
    middleware (HTTP transport) or by :func:`load_stdio_user` (stdio
    transport). Returns ``None`` when called outside a request context
    or when running in dev mode without an explicit principal.

    The MCP tool functions in :mod:`src.mcp.tools`,
    :mod:`src.mcp.action_tools`, and :mod:`src.mcp.onboarding_tools` MAY
    call this for audit logging — they do not need it for authorisation
    because the middleware has already gated the request.
    """
    return _current_user_ctx.get()


def set_current_user(principal: Optional[MCPPrincipal]) -> None:
    """Set the per-request principal in the ContextVar.

    Public so :func:`load_stdio_user` and tests can seed a principal
    without going through the middleware path.
    """
    _current_user_ctx.set(principal)


def load_stdio_user(source: Optional[Path] = None) -> MCPPrincipal:
    """Load the token file and populate the current-user ContextVar.

    Called once at stdio MCP server startup. Returning a principal
    instead of a bare bool gives the caller something to surface in
    audit logs ("MCP stdio session started for user=X role=Y").

    Args:
        source: Optional override token path (passed through to
            :func:`load_token_file`).

    Returns:
        :class:`MCPPrincipal` describing the authenticated stdio caller.

    Raises:
        TokenError: Same conditions as :func:`load_token_file`.
    """
    payload = load_token_file(source)
    principal = MCPPrincipal(
        user=payload.user,
        principal_dn=payload.principal_dn,
        role=payload.role,
        auth_kind="token",
    )
    set_current_user(principal)
    return principal


# ---------------------------------------------------------------------------
# HTTP auth helpers — wrap src.api.auth so the same key/cookie machinery
# is reused (per the EF-S7 design contract: "Do not reimplement").
# ---------------------------------------------------------------------------


def _check_api_key_header(request: Request) -> Optional[MCPPrincipal]:
    """Validate an ``X-API-Key`` header against the configured key set.

    Returns ``None`` (not raises) when the header is missing or invalid,
    so the middleware can fall through to the next auth mode.
    """
    header_val = request.headers.get("x-api-key")
    if not header_val:
        return None
    # Reuse the parser from src.api.auth — same env var, same role-suffix
    # convention, single source of truth.
    from src.api.auth import _parse_api_keys

    keys = _parse_api_keys()
    role = keys.get(header_val)
    if role is None:
        return None
    return MCPPrincipal(
        user=f"apikey:{header_val[-6:]}",
        principal_dn="",
        role=role,
        auth_kind="api_key",
    )


def _check_session_cookie(request: Request) -> Optional[MCPPrincipal]:
    """Validate a Starlette session cookie via the existing LDAP bridge.

    Returns ``None`` when no SessionMiddleware is installed (the parent
    FastAPI app did not enable auth) or when no/expired user is present.
    """
    from src.api.auth import _try_resolve_session_cookie

    ctx = _try_resolve_session_cookie(request)
    if ctx is None:
        return None
    # Translate the parent AuthContext into an MCPPrincipal. The DN goes
    # into principal_dn; the leftmost CN gives us a short user handle.
    dn = ctx.subject or ""
    short_user = ctx.name or _short_user_from_dn(dn) or "ldap"
    return MCPPrincipal(
        user=short_user,
        principal_dn=dn,
        role=ctx.role,
        auth_kind="session",
    )


def _check_bearer_token(request: Request) -> Optional[MCPPrincipal]:
    """Validate an ``Authorization: Bearer <json>`` token header.

    The token value is the raw JSON dict (URL-safe-base64 encoded) so
    the same token format works over HTTP and stdio. Returns ``None``
    when the header is missing; raises nothing on bad tokens (callers
    fall through to the 401 path).
    """
    header_val = request.headers.get("authorization")
    if not header_val or not header_val.lower().startswith("bearer "):
        return None
    bearer = header_val.split(" ", 1)[1].strip()
    try:
        payload = _decode_bearer(bearer)
        verified = verify_token(payload)
    except TokenError:
        return None
    return MCPPrincipal(
        user=verified.user,
        principal_dn=verified.principal_dn,
        role=verified.role,
        auth_kind="token",
    )


def _decode_bearer(bearer: str) -> dict:
    """Decode a bearer-token string to a JSON payload dict.

    Accepts two shapes for ergonomics:

    * URL-safe-base64-encoded JSON (preferred — header-safe).
    * Raw JSON (only when the leading char is ``{``) — convenient for
      local curl testing.

    Raises:
        TokenError: If the string cannot be decoded.
    """
    import base64

    if bearer.startswith("{"):
        try:
            return json.loads(bearer)
        except json.JSONDecodeError as exc:
            raise TokenError(f"Bearer token is not valid JSON: {exc}") from exc
    try:
        # Allow missing padding (common in URL-safe-b64 transport).
        padded = bearer + "=" * (-len(bearer) % 4)
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TokenError(f"Bearer token could not be decoded: {exc}") from exc


def encode_bearer(payload: dict) -> str:
    """Encode a token payload for use in an ``Authorization`` header.

    URL-safe base64 of the JSON form, padding stripped per common
    convention. Symmetric counterpart of :func:`_decode_bearer`.
    """
    import base64

    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _short_user_from_dn(dn: str) -> str:
    """Extract the leftmost CN= value from an LDAP DN; '' if none."""
    if not dn:
        return ""
    head = dn.split(",", 1)[0].strip()
    if "=" in head:
        return head.split("=", 1)[1].strip()
    return dn.strip()


# ---------------------------------------------------------------------------
# Starlette middleware
# ---------------------------------------------------------------------------


class MCPAuthMiddleware(BaseHTTPMiddleware):
    """Production auth gate for the MCP HTTP sub-app (EF-S7).

    Auth resolution order (first wins):

    1. ``VALDO_MCP_AUTH=dev`` — pass-through (no other check). Preserves
       the EF-S1 dev-mode behaviour for local testing.
    2. Starlette session cookie (LDAP-issued) — interactive user
       precedence, mirrors the parent FastAPI auth chain.
    3. ``X-API-Key`` header — service-to-service callers.
    4. ``Authorization: Bearer <token>`` — agents holding a stdio token
       can use HTTP transport interchangeably.

    All four populate the per-request ContextVar so downstream tools
    can call :func:`current_user` for audit logging. Any other case
    returns HTTP 401 with a generic JSON body — the error message does
    not distinguish "missing" from "invalid" to avoid user enumeration.
    """

    async def dispatch(self, request: Request, call_next):
        # 1. Dev-mode bypass.
        if os.environ.get(DEV_AUTH_ENV_VAR) == DEV_AUTH_SENTINEL:
            dev_principal = MCPPrincipal(
                user="dev",
                principal_dn="",
                role="admin",
                auth_kind="dev",
            )
            token = _current_user_ctx.set(dev_principal)
            try:
                return await call_next(request)
            finally:
                _current_user_ctx.reset(token)

        # 2-4. Production auth chain. Order matters: session > API key >
        # bearer token, mirroring the parent FastAPI behaviour (session
        # cookie always wins when both forms are present).
        principal: Optional[MCPPrincipal] = None
        for check in (_check_session_cookie, _check_api_key_header, _check_bearer_token):
            try:
                principal = check(request)
            except Exception:
                # Any unexpected error inside an auth check is treated as
                # "did not authenticate" — fail closed. The error is
                # logged so an operator can correlate against access logs.
                logger.exception("mcp_auth_check_error check=%s", check.__name__)
                principal = None
            if principal is not None:
                break

        if principal is None:
            return JSONResponse(
                status_code=401,
                content={"error": "MCP auth required"},
            )

        token = _current_user_ctx.set(principal)
        try:
            return await call_next(request)
        finally:
            _current_user_ctx.reset(token)


# Re-export the dev-auth sentinels at module level for the legacy
# ``src.mcp.server._DEV_AUTH_ENV_VAR`` consumers. Kept here so the new
# auth module is the single source of truth.
__all__ = [
    "DEV_AUTH_ENV_VAR",
    "DEV_AUTH_SENTINEL",
    "TOKEN_SIGNING_KEY_ENV_VAR",
    "TOKEN_PATH_ENV_VAR",
    "DEFAULT_TOKEN_PATH",
    "DEFAULT_TOKEN_TTL_HOURS",
    "MAX_TOKEN_TTL_HOURS",
    "MCPAuthMiddleware",
    "MCPPrincipal",
    "TokenError",
    "TokenPayload",
    "current_user",
    "set_current_user",
    "load_stdio_user",
    "load_token_file",
    "mint_token",
    "verify_token",
    "write_token_file",
    "resolve_token_path",
    "encode_bearer",
]

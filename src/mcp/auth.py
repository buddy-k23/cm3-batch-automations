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
testing — it remains a strict opt-in, never the default. As of S13.5-3
(#409) the bypass also requires an explicit ``VALDO_ALLOW_DEV_AUTH=1``
opt-in: ``VALDO_MCP_AUTH=dev`` ALONE no longer grants the zero-credential
admin (a copied sample ``.env`` is therefore not auth-bypassed). In any
other mode the middleware fails closed with HTTP 401.

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
import secrets
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.mcp.rate_limit import (
    RateLimiter,
    classify_request,
    client_ip_of,
    token_identity,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------

# Env var that opens the dev-mode auth pass-through. The literal sentinel
# value is intentionally narrow ("dev") so accidental truthy values
# (e.g. "1", "true") do not silently disable auth.
DEV_AUTH_ENV_VAR = "VALDO_MCP_AUTH"
DEV_AUTH_SENTINEL = "dev"

# S13.5-3 (#409): explicit opt-in required for the zero-credential dev
# bypass. ``VALDO_MCP_AUTH=dev`` ALONE is no longer sufficient — it must
# be paired with a truthy ``VALDO_ALLOW_DEV_AUTH``. This way a copied
# ``.env.example`` (which ships ``VALDO_MCP_AUTH=`` empty) is never
# auth-bypassed, and even a stray ``VALDO_MCP_AUTH=dev`` left in an env
# fails closed unless the operator deliberately opts in. Local developers
# set BOTH vars to keep the convenient no-credential workflow.
DEV_AUTH_OPT_IN_ENV_VAR = "VALDO_ALLOW_DEV_AUTH"
_DEV_AUTH_OPT_IN_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_dev_auth_enabled() -> bool:
    """Return whether the zero-credential dev-auth bypass is active.

    The dev bypass is enabled only when BOTH of these hold:

    * ``VALDO_MCP_AUTH`` equals the exact sentinel ``"dev"``, AND
    * ``VALDO_ALLOW_DEV_AUTH`` is a truthy opt-in (``1``/``true``/``yes``/
      ``on``, case-insensitive).

    Requiring the explicit opt-in (S13.5-3, #409) means a copied sample
    ``.env`` is never auth-bypassed: ``VALDO_MCP_AUTH=dev`` without the
    opt-in is treated as "auth misconfigured" and the middleware falls
    through to the production (no-bypass) auth chain.

    Returns:
        True when the deliberate dev bypass is active, False otherwise.
    """
    if os.environ.get(DEV_AUTH_ENV_VAR) != DEV_AUTH_SENTINEL:
        return False
    opt_in = os.environ.get(DEV_AUTH_OPT_IN_ENV_VAR, "").strip().lower()
    return opt_in in _DEV_AUTH_OPT_IN_TRUTHY


# Guard so the misconfiguration warning is emitted at most once per process
# even on a hot request path.
_dev_auth_warned = False


def _warn_dev_auth_not_opted_in() -> None:
    """Warn once that ``VALDO_MCP_AUTH=dev`` is set without the opt-in.

    Emitted from the middleware when the dev sentinel is present but
    ``VALDO_ALLOW_DEV_AUTH`` is missing/falsy (S13.5-3, #409). The request
    is NOT bypassed — it falls through to the production auth chain — but
    the operator is told how to deliberately enable the dev path.
    """
    global _dev_auth_warned
    if _dev_auth_warned:
        return
    _dev_auth_warned = True
    logger.warning(
        "mcp_dev_auth_misconfigured: %s=dev is set but %s is not enabled; "
        "the zero-credential dev bypass is DISABLED (fail-closed). Set "
        "%s=1 to deliberately enable it for local development.",
        DEV_AUTH_ENV_VAR,
        DEV_AUTH_OPT_IN_ENV_VAR,
        DEV_AUTH_OPT_IN_ENV_VAR,
    )

# Env var that holds the HMAC-SHA256 signing key for stdio bearer tokens.
# Must be set in production (any non-dev mode) or the middleware refuses
# to verify tokens. The CLI ``valdo mcp-login`` flow likewise requires
# the server to have this configured.
TOKEN_SIGNING_KEY_ENV_VAR = "VALDO_MCP_TOKEN_SIGNING_KEY"

# S9-2 (#390) — the load-balancer health probe path. The MCP sub-app is
# mounted at ``/mcp`` by the parent FastAPI app, so the externally-visible
# path of the health route is ``/mcp/health`` and that is what the
# Starlette middleware observes on ``request.url.path``. The probe MUST
# work with no credential (load balancers do not authenticate), so the
# auth middleware short-circuits this exact path before any auth check.
HEALTH_PATH = "/mcp/health"

# Default location of the stdio bearer token on the user's machine.
# Overridable via ``VALDO_MCP_TOKEN_PATH`` for ops and tests.
DEFAULT_TOKEN_PATH = Path.home() / ".valdo" / "mcp-token"
TOKEN_PATH_ENV_VAR = "VALDO_MCP_TOKEN_PATH"

# TTL bounds for ``POST /api/v2/mcp/login`` and ``valdo mcp-login``.
DEFAULT_TOKEN_TTL_HOURS = 12
MAX_TOKEN_TTL_HOURS = 48

# S9-4 (#389) — per-token revocation.
#
# Tokens minted from S9-4 onward carry an opaque ``jti`` (16 random bytes,
# hex-encoded → 32 chars) so a single token can be revoked without
# rotating the signing key. The ``jti`` is part of the signed payload.
#
# Backward-compatibility grace window: EF-S7 tokens minted *before* S9-4
# have no ``jti``. Those still validate, but only while they are within a
# 24-hour grace window — after that a missing ``jti`` is rejected, forcing
# a re-login that mints a jti-bearing (and therefore revocable) token.
#
# The grace boundary is a single epoch cutoff. A no-jti token is accepted
# iff its ``issued_at`` is STRICTLY BEFORE the cutoff. Resolution order:
#   1. ``VALDO_MCP_JTI_GRACE_UNTIL`` env var (epoch seconds) — a deploy-time
#      hard cutoff an operator can pin (e.g. "no-jti tokens die at 02:00").
#   2. Unset → a rolling cutoff of ``process_start_time + 24h``, computed
#      once at import. A fresh deploy thus accepts the no-jti tokens already
#      in the wild for 24h, then rejects them.
JTI_BYTES = 16
JTI_GRACE_ENV_VAR = "VALDO_MCP_JTI_GRACE_UNTIL"
JTI_GRACE_WINDOW_SECONDS = 24 * 3600

# Process-start grace cutoff for the env-unset case, computed once so a
# long-running process has a stable boundary (not a per-request sliding
# window that would never expire).
_PROCESS_START = int(time.time())
_DEFAULT_GRACE_UNTIL = _PROCESS_START + JTI_GRACE_WINDOW_SECONDS


def _jti_grace_until() -> int:
    """Resolve the epoch cutoff after which no-jti tokens are rejected.

    Reads :data:`JTI_GRACE_ENV_VAR` fresh on every call so an operator can
    pin the boundary without an app restart. Falls back to the
    process-start + 24h default when the env var is unset, empty, or
    non-integer (a malformed value must not silently disable the gate —
    it falls back to the safe rolling default).

    Returns:
        Epoch seconds. A no-jti token whose ``issued_at`` is strictly
        before this value is still accepted; at or after it, rejected.
    """
    raw = os.environ.get(JTI_GRACE_ENV_VAR, "")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            logger.warning(
                "%s=%r is not an integer epoch; falling back to the "
                "process-start + 24h grace cutoff.",
                JTI_GRACE_ENV_VAR,
                raw,
            )
    return _DEFAULT_GRACE_UNTIL


def generate_jti() -> str:
    """Return a fresh opaque token id — 16 random bytes, hex-encoded.

    Uses :func:`secrets.token_hex` (CSPRNG) so the value is unguessable.
    The id is opaque: it carries no structure and is only ever compared
    for equality against the revocation blocklist.

    Returns:
        A 32-character lowercase hex string.
    """
    return secrets.token_hex(JTI_BYTES)

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
    # S9-4: opaque token id. Empty string for legacy EF-S7 tokens minted
    # before the jti format change (accepted only within the grace window).
    jti: str = ""


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
    jti: str = "",
) -> bytes:
    """Build the canonical signing input as UTF-8 bytes.

    The five core fields are joined with ``|`` (which cannot appear in an
    LDAP DN without escaping; even if it did, the join is unambiguous
    because the field order is fixed). Reordering or omitting fields
    invalidates the signature.

    The S9-4 ``jti`` is appended as a sixth pipe-delimited field **only
    when it is non-empty**. This is deliberate: a legacy EF-S7 token (no
    jti) reproduces the *exact* original five-field signing input, so old
    signatures remain valid without a key rotation. A new token's jti is
    bound into the signature, so a thief cannot strip or swap the jti to
    dodge revocation without invalidating the signature.

    Args:
        user: Short user identifier.
        principal_dn: Full LDAP DN.
        role: Valdo role.
        issued_at: Issue epoch seconds.
        expires_at: Expiry epoch seconds.
        jti: Opaque token id, or empty string for legacy tokens.

    Returns:
        UTF-8 canonical signing input bytes.
    """
    base = f"{user}|{principal_dn}|{role}|{issued_at}|{expires_at}"
    if jti:
        base = f"{base}|{jti}"
    return base.encode("utf-8")


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
        ``issued_at``, ``expires_at``, ``jti``, ``signature`` — directly
        JSON-serialisable. The caller writes this to ``~/.valdo/mcp-token``.
        The ``jti`` (S9-4) is a fresh opaque 16-byte hex id bound into the
        signature so the token can be individually revoked.

    Raises:
        TokenError: If the signing key is unconfigured.
    """
    if ttl_hours < 1:
        ttl_hours = 1
    if ttl_hours > MAX_TOKEN_TTL_HOURS:
        ttl_hours = MAX_TOKEN_TTL_HOURS

    issued_at = int(time.time())
    expires_at = issued_at + ttl_hours * 3600
    jti = generate_jti()
    sig = hmac.new(
        _signing_key(),
        _signature_payload(user, principal_dn, role, issued_at, expires_at, jti),
        hashlib.sha256,
    ).hexdigest()
    return {
        "user": user,
        "principal_dn": principal_dn,
        "role": role,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "jti": jti,
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
            invalid, the token has expired, a missing ``jti`` is outside
            the 24h grace window, or the token's ``jti`` has been revoked.

    Verification order (each gate runs only after the prior passes):
        1. Required fields present + integer timestamps.
        2. **HMAC signature** (constant-time compare) — nothing
           token-derived is trusted until this passes, so a forged token
           never reaches the DB/cache.
        3. **Expiry**.
        4. **jti grace gate** (S9-4) — a token *without* a jti is rejected
           once it is outside the 24h grace window.
        5. **Revocation blocklist** (S9-4) — a token *with* a jti is
           rejected if that jti is on the blocklist. Backed by a 60s-TTL
           in-memory cache so the lookup is sub-millisecond.
    """
    # ``signature`` plus the five core fields are always required.
    # ``jti`` is optional on the wire (legacy tokens lack it) and handled
    # by the grace gate below.
    required = ("user", "principal_dn", "role", "issued_at", "expires_at", "signature")
    missing = [k for k in required if k not in payload]
    if missing:
        raise TokenError(f"Token missing required fields: {missing}")

    try:
        issued_at = int(payload["issued_at"])
        expires_at = int(payload["expires_at"])
    except (TypeError, ValueError) as exc:
        raise TokenError("Token timestamps are not integers") from exc

    jti = str(payload.get("jti", "") or "")

    expected = hmac.new(
        _signing_key(),
        _signature_payload(
            str(payload["user"]),
            str(payload["principal_dn"]),
            str(payload["role"]),
            issued_at,
            expires_at,
            jti,
        ),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, str(payload["signature"])):
        raise TokenError("Token signature does not match — token tampered or signing key rotated")

    now = int(time.time())
    if expires_at <= now:
        raise TokenError("Token has expired. Run 'valdo mcp-login' to mint a new one.")

    # S9-4 grace gate: a token without a jti predates the revocation
    # format. Accept it only while it is within the grace window — i.e.
    # it was issued strictly before the grace cutoff. After that, force a
    # re-login so the agent gets a revocable (jti-bearing) token.
    if not jti:
        cutoff = _jti_grace_until()
        if issued_at >= cutoff:
            raise TokenError(
                "Token has no jti and the revocation grace window has "
                "elapsed. Run 'valdo mcp-login' to mint a new token."
            )
    else:
        # S9-4 blocklist check — only meaningful for jti-bearing tokens.
        # Consults the 60s-TTL in-memory cache (DB-free on the hot path).
        if _jti_is_revoked(jti):
            raise TokenError(
                "Token has been revoked. Run 'valdo mcp-login' to mint a "
                "new token (contact an administrator if this is unexpected)."
            )

    return TokenPayload(
        user=str(payload["user"]),
        principal_dn=str(payload["principal_dn"]),
        role=str(payload["role"]),
        issued_at=issued_at,
        expires_at=expires_at,
        jti=jti,
    )


def _jti_is_revoked(jti: str) -> bool:
    """Return True if *jti* is on the revocation blocklist.

    Thin indirection over :func:`src.mcp.revocation.get_revocation_cache`
    so the import stays lazy (the revocation module pulls in the DB
    engine; auth must stay importable in DB-less contexts such as the CLI
    token loader). Fail-soft: any unexpected error is logged and treated
    as "not revoked" — an infra blip must not lock out every agent, since
    signature + expiry already gated the request.

    Args:
        jti: The opaque token id to check.

    Returns:
        True if revoked, False otherwise (including on lookup error).
    """
    try:
        from src.mcp.revocation import get_revocation_cache

        return get_revocation_cache().is_revoked(jti)
    except Exception:  # noqa: BLE001 — fail-soft; never break auth on infra.
        logger.warning("mcp_revocation_check_error jti_suffix=%s", jti[-6:])
        return False


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

    Rate limiting (S9-3, #390):
        Once the principal is resolved, billable ``tools/call`` requests
        pass through a per-token + per-IP sliding-window limiter
        (:class:`src.mcp.rate_limit.RateLimiter`). On exceedance the
        middleware short-circuits with HTTP 429 and a ``Retry-After``
        header — the transport never sees the over-limit call. Resource
        reads and handshake methods are exempt; ``get_run_status`` polling
        uses an elevated cap. The limiter is constructed once per
        middleware instance so its in-process counters persist across
        requests; all throttling logic lives in
        :mod:`src.mcp.rate_limit` (this middleware just calls it —
        Architecture Principle #1).
    """

    def __init__(self, app) -> None:
        """Wire the auth gate and build the request-path rate limiter.

        Args:
            app: The wrapped ASGI application (the MCP transport).
        """
        super().__init__(app)
        # One limiter per middleware instance so the in-memory sliding
        # windows accumulate across requests. Caps are read from the
        # ``VALDO_MCP_RATE_LIMIT_*`` env vars at construction time.
        self._rate_limiter = RateLimiter.from_env()

    async def dispatch(self, request: Request, call_next):
        # 0. Health-probe bypass (S9-2, #390). The load-balancer health
        # endpoint must answer WITHOUT any credential — register it
        # outside the token-auth gate. We match the exact mounted path so
        # the bypass cannot be widened by a crafted prefix.
        if request.url.path == HEALTH_PATH:
            return await call_next(request)

        # 1. Dev-mode bypass — STRICT opt-in (S13.5-3, #409). Requires
        # BOTH VALDO_MCP_AUTH=dev AND a truthy VALDO_ALLOW_DEV_AUTH so a
        # copied sample .env is never auth-bypassed. A bare
        # VALDO_MCP_AUTH=dev without the opt-in is treated as misconfigured
        # and falls through to the production auth chain (fail-closed); we
        # emit a one-time warning so the operator can correct it.
        if is_dev_auth_enabled():
            dev_principal = MCPPrincipal(
                user="dev",
                principal_dn="",
                role="admin",
                auth_kind="dev",
            )
            token = _current_user_ctx.set(dev_principal)
            try:
                limited = await self._enforce_rate_limit(request, dev_principal)
                if limited is not None:
                    return limited
                return await call_next(request)
            finally:
                _current_user_ctx.reset(token)

        # Misconfiguration guard (S13.5-3, #409): VALDO_MCP_AUTH=dev set
        # WITHOUT the VALDO_ALLOW_DEV_AUTH opt-in. We do NOT bypass; instead
        # we warn once and fall through to the production auth chain so the
        # request still has to present a real credential.
        if os.environ.get(DEV_AUTH_ENV_VAR) == DEV_AUTH_SENTINEL:
            _warn_dev_auth_not_opted_in()

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
            # S13.5-2 (#415): audit the MCP auth failure before returning
            # the 401. This covers the X-API-Key + bearer-token failure
            # paths that previously fell through to a bare 401 with no
            # audit record (the LDAP /mcp/login path already audits via
            # mcp_login_failure). We do NOT log any credential value — only
            # whether a header/cookie was present, plus the proxy-corrected
            # client IP (S9-1). Fail-closed (S13.5-1): if the audit write
            # itself fails the AuditWriteError is caught and logged so a
            # genuine audit outage is visible, but the original 401 still
            # reaches the client (an audit hiccup must not turn an
            # unauthenticated request into an authenticated one).
            self._audit_mcp_auth_failure(request)
            return JSONResponse(
                status_code=401,
                content={"error": "MCP auth required"},
            )

        token = _current_user_ctx.set(principal)
        try:
            limited = await self._enforce_rate_limit(request, principal)
            if limited is not None:
                return limited
            return await call_next(request)
        finally:
            _current_user_ctx.reset(token)

    def _audit_mcp_auth_failure(self, request: Request) -> None:
        """Emit an auth_failure audit event for a rejected MCP request.

        Records the proxy-corrected client IP (S9-1) and which credential
        forms were *presented* (booleans only — never the values) so a SOC
        analyst can tell a no-credential probe from a bad-credential
        attempt without any secret landing in the log.

        Fail-closed handling (S13.5-2 + S13.5-1): the audit write can raise
        :class:`AuditWriteError`. Unlike the request-handler sites (which
        let it bubble to a 500), the auth middleware MUST still return its
        401 — turning an audit-file outage into a path that *fails open*
        would be a worse security outcome than a missing audit line. We
        therefore catch the error, log it loudly for ops to alert on, and
        proceed to the 401. The loud ERROR log is the visibility signal.
        """
        from src.utils.audit_logger import AuditWriteError, audit_auth_failure

        client_ip = client_ip_of(request)
        had_api_key = bool(request.headers.get("x-api-key"))
        had_bearer = bool(
            (request.headers.get("authorization") or "").lower().startswith("bearer ")
        )
        try:
            audit_auth_failure(
                auth_kind="mcp",
                reason="no_valid_credential",
                client_ip=client_ip,
                triggered_by="mcp",
                presented_api_key=had_api_key,
                presented_bearer=had_bearer,
                path=request.url.path,
            )
        except AuditWriteError:
            # Visible (ERROR) but non-fatal here: the 401 must still stand.
            logger.error(
                "mcp_auth_failure_audit_write_failed path=%s client_ip=%s",
                request.url.path,
                client_ip,
            )

    async def _enforce_rate_limit(
        self,
        request: Request,
        principal: MCPPrincipal,
    ) -> Optional[JSONResponse]:
        """Apply per-token + per-IP throttling to a billable tool call.

        Reads the JSON-RPC body (cached on the request so the downstream
        transport re-reads it for free), classifies it, and — only for
        ``tools/call`` — meters the call against the per-token and per-IP
        budgets. ``get_run_status`` routes to its elevated cap; resource
        reads and handshakes are exempt and return ``None`` immediately.

        The body read is wrapped defensively: a body that cannot be parsed
        as JSON is treated as exempt (fails open) rather than 500-ing —
        auth has already gated the request, and the transport will reject
        a malformed body itself.

        Args:
            request: The incoming MCP request (principal already resolved).
            principal: The authenticated caller, used for the per-token
                bucket key.

        Returns:
            A 429 :class:`JSONResponse` with a ``Retry-After`` header when
            the call exceeds a budget, or ``None`` when the call is
            admitted or exempt.
        """
        try:
            raw = await request.body()
        except Exception:  # noqa: BLE001 — never let a body read break auth.
            return None
        if not raw:
            return None
        try:
            body = json.loads(raw)
        except (ValueError, TypeError):
            return None

        classification = classify_request(body)
        if not classification.is_tool_call:
            return None

        decision = self._rate_limiter.check_tool_call(
            token_id=token_identity(principal),
            client_ip=client_ip_of(request),
            tool_name=classification.tool_name,
        )
        if decision.allowed:
            return None

        logger.warning(
            "mcp_rate_limited scope=%s tool=%s user=%s retry_after=%ss",
            decision.scope,
            classification.tool_name,
            principal.user,
            decision.retry_after,
        )
        return JSONResponse(
            status_code=429,
            content={
                "error": "Rate limit exceeded",
                "scope": decision.scope,
                "retry_after_seconds": decision.retry_after,
            },
            headers={"Retry-After": str(decision.retry_after)},
        )


# Re-export the dev-auth sentinels at module level for the legacy
# ``src.mcp.server._DEV_AUTH_ENV_VAR`` consumers. Kept here so the new
# auth module is the single source of truth.
__all__ = [
    "DEV_AUTH_ENV_VAR",
    "DEV_AUTH_SENTINEL",
    "DEV_AUTH_OPT_IN_ENV_VAR",
    "is_dev_auth_enabled",
    "HEALTH_PATH",
    "TOKEN_SIGNING_KEY_ENV_VAR",
    "TOKEN_PATH_ENV_VAR",
    "DEFAULT_TOKEN_PATH",
    "DEFAULT_TOKEN_TTL_HOURS",
    "MAX_TOKEN_TTL_HOURS",
    "JTI_GRACE_ENV_VAR",
    "JTI_GRACE_WINDOW_SECONDS",
    "generate_jti",
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

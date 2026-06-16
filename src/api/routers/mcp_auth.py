"""``POST /api/v2/mcp/login`` — mint stdio bearer tokens for MCP agents (EF-S7).

Public endpoint (no ``Depends(require_api_key)``) — the caller is
authenticating in this very request and has no prior credential. The
endpoint validates the supplied username + password against the same
LDAPS bridge the existing ``/auth/login`` route uses
(:func:`src.api.auth_ldap.ldap_authenticate`), maps the user's LDAP
groups to a Valdo role, and returns a freshly-minted HMAC-SHA256-signed
token. The token is **self-contained** — the server keeps no
revocation list; rotate ``VALDO_MCP_TOKEN_SIGNING_KEY`` to invalidate
all outstanding tokens.

The client (``valdo mcp-login``) writes the returned token to
``~/.valdo/mcp-token`` with 0600 permissions. Subsequent stdio MCP
sessions load that file and use it to authenticate.

SECURITY POSTURE
----------------
* Bad credentials surface as HTTP 401 with the generic detail
  ``"Invalid credentials"`` — no enumeration channel.
* LDAP unavailability surfaces as HTTP 503 so clients can distinguish
  "wrong password" from "infrastructure problem".
* The endpoint never logs the password (it goes straight into the
  LDAP bind call); only the username appears in audit events.
* TTL is clamped to ``[1, MAX_TOKEN_TTL_HOURS]`` — clients cannot mint
  long-lived tokens by passing ``ttl_hours=10000``.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from src.api.auth_ldap import LdapAuthError, ldap_authenticate, map_groups_to_role
from src.mcp.auth import (
    DEFAULT_TOKEN_TTL_HOURS,
    MAX_TOKEN_TTL_HOURS,
    TokenError,
    mint_token,
)
from src.mcp.revocation import get_revocation_cache
from src.utils.audit_logger import get_audit_logger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v2/mcp", tags=["MCP Auth"])


class MCPLoginRequest(BaseModel):
    """Request body for ``POST /api/v2/mcp/login``.

    Pydantic validates the shape at the FastAPI boundary so the handler
    body can assume well-formed input.
    """

    username: str = Field(..., min_length=1, max_length=256, description="LDAP username")
    password: str = Field(..., min_length=1, description="LDAP password — never logged")
    ttl_hours: Optional[int] = Field(
        default=DEFAULT_TOKEN_TTL_HOURS,
        ge=1,
        le=MAX_TOKEN_TTL_HOURS,
        description=f"Token TTL in hours (default {DEFAULT_TOKEN_TTL_HOURS}, max {MAX_TOKEN_TTL_HOURS})",
    )


class MCPLoginResponse(BaseModel):
    """Response body for ``POST /api/v2/mcp/login``."""

    token: dict = Field(..., description="The full signed token payload — write to ~/.valdo/mcp-token")
    expires_at: int = Field(..., description="UNIX epoch seconds when the token expires")
    principal_dn: str = Field(..., description="The authenticated LDAP DN")
    role: str = Field(..., description="Resolved Valdo role: tester | mapping_owner | admin")


@router.post("/login", response_model=MCPLoginResponse, include_in_schema=True)
async def mcp_login(request: Request, body: MCPLoginRequest) -> MCPLoginResponse:
    """Validate LDAP credentials and return a freshly-minted MCP token.

    The flow mirrors the existing UI login path
    (:mod:`src.api.routers.auth`) but returns a signed JSON token
    instead of setting a session cookie — the client is a CLI tool, not
    a browser.

    Args:
        request: FastAPI request (used to read ``app.state.ui_config``
            for the LDAP and role-map config).
        body: Validated :class:`MCPLoginRequest`.

    Returns:
        :class:`MCPLoginResponse` with the token and metadata.

    Raises:
        HTTPException: 401 on bad credentials; 503 when LDAP is
            unreachable or the auth bridge is not configured; 500 if
            the signing key env var is missing (operator misconfiguration).
    """
    cfg = (getattr(request.app.state, "ui_config", {}) or {}).get("auth", {})
    ldap_cfg = cfg.get("ldap", {})
    role_map = ldap_cfg.get("group_role_map", {"*": "tester"})

    # If the parent app does not have auth.enabled (no LDAP config), the
    # endpoint cannot do its job. Surface a 503 so deployers know to
    # enable auth.ldap in config/ui.yml.
    if not ldap_cfg:
        logger.error("mcp_login_misconfigured reason=ldap_config_missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LDAP auth is not configured on this server.",
        )

    # S9-1 (#387): behind the nginx reverse proxy the TCP peer is nginx, not
    # the agent host. ProxyHeadersMiddleware (wired in src.api.main, trust
    # list via VALDO_MCP_TRUSTED_PROXIES) has already rewritten
    # ``request.client`` from a trusted X-Forwarded-For, so this captures the
    # REAL client IP for the token-mint audit trail — critical for SOX
    # attribution of who minted which credential, not the proxy's address.
    client_ip = request.client.host if request.client else "unknown"

    audit = get_audit_logger()
    try:
        user = ldap_authenticate(body.username, body.password, ldap_cfg)
    except LdapAuthError as exc:
        # Mirror the parent /auth/login audit-event taxonomy so SIEM
        # rules covering one cover the other.
        audit.emit(
            "mcp_login_failure",
            triggered_by="mcp_login_cli",
            username=body.username,
            client_ip=client_ip,
            reason=str(exc),
        )
        # "invalid_credentials" and "user_not_found" both collapse to 401
        # — no user-enumeration channel. "ldap_unavailable" returns 503.
        if str(exc) in {"ldap_unavailable", "ldap_service_account_not_configured"}:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LDAP backend is unavailable.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    role = map_groups_to_role(user.groups, role_map)
    ttl_hours = body.ttl_hours or DEFAULT_TOKEN_TTL_HOURS
    try:
        token = mint_token(
            user=body.username,
            principal_dn=user.dn,
            role=role,
            ttl_hours=ttl_hours,
        )
    except TokenError as exc:
        # Signing key missing — operator misconfiguration. Surface 503
        # (the LDAP bind succeeded, so 401 would be misleading).
        logger.error("mcp_login_misconfigured reason=%s", exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP token signing is not configured on this server.",
        )

    audit.emit(
        "mcp_login_success",
        triggered_by="mcp_login_cli",
        sub=user.dn,
        username=body.username,
        role=role,
        client_ip=client_ip,
        expires_at=token["expires_at"],
    )
    return MCPLoginResponse(
        token=token,
        expires_at=token["expires_at"],
        principal_dn=user.dn,
        role=role,
    )


# ---------------------------------------------------------------------------
# POST /api/v2/mcp/revoke — admin-only per-token revocation (S9-4, #389)
# ---------------------------------------------------------------------------


class MCPRevokeRequest(BaseModel):
    """Request body for ``POST /api/v2/mcp/revoke``.

    The caller authenticates with their *own* LDAP credentials in the
    same request (the endpoint is admin-gated, not API-key-gated) and
    supplies the ``token_id`` (the target token's ``jti``) to revoke plus
    a free-text ``reason`` for the audit trail.
    """

    username: str = Field(..., min_length=1, max_length=256, description="Admin LDAP username")
    password: str = Field(..., min_length=1, description="Admin LDAP password — never logged")
    token_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="The jti of the token to revoke (from the target token's payload)",
    )
    reason: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="Why the token is being revoked (incident ref, 'laptop stolen', etc.)",
    )


class MCPRevokeResponse(BaseModel):
    """Response body for ``POST /api/v2/mcp/revoke``."""

    revoked: bool = Field(..., description="True when the jti is now on the blocklist")
    token_id: str = Field(..., description="The revoked jti, echoed back")
    revoked_by: str = Field(..., description="The admin DN that issued the revocation")


@router.post("/revoke", response_model=MCPRevokeResponse, include_in_schema=True)
async def mcp_revoke(request: Request, body: MCPRevokeRequest) -> MCPRevokeResponse:
    """Revoke an MCP token by its ``jti`` — admin only (S9-4, #389).

    Authorization model: the caller authenticates with their own LDAP
    credentials against the same bridge as ``/api/v2/mcp/login``; their
    LDAP groups are mapped to a Valdo role and the request is rejected
    with **403** unless that role is ``admin`` (i.e. membership in the
    ``valdo-admins`` group per ``auth.ldap.group_role_map``). On success
    the ``jti`` is written to the ``MCP_REVOKED_TOKENS`` blocklist and the
    in-process revocation cache is invalidated so the revocation takes
    effect on this node immediately; other nodes converge within the
    cache TTL (60s).

    Args:
        request: FastAPI request (used to read ``app.state.ui_config``).
        body: Validated :class:`MCPRevokeRequest`.

    Returns:
        :class:`MCPRevokeResponse` confirming the revocation.

    Raises:
        HTTPException: 401 on bad credentials; 403 when the authenticated
            caller is not an admin; 503 when LDAP is unreachable / not
            configured or the blocklist table is unavailable.
    """
    cfg = (getattr(request.app.state, "ui_config", {}) or {}).get("auth", {})
    ldap_cfg = cfg.get("ldap", {})
    role_map = ldap_cfg.get("group_role_map", {"*": "tester"})

    if not ldap_cfg:
        logger.error("mcp_revoke_misconfigured reason=ldap_config_missing")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="LDAP auth is not configured on this server.",
        )

    client_ip = request.client.host if request.client else "unknown"
    audit = get_audit_logger()

    try:
        user = ldap_authenticate(body.username, body.password, ldap_cfg)
    except LdapAuthError as exc:
        audit.emit(
            "mcp_revoke_failure",
            triggered_by="mcp_revoke",
            username=body.username,
            client_ip=client_ip,
            token_id=body.token_id,
            reason=f"auth:{exc}",
        )
        if str(exc) in {"ldap_unavailable", "ldap_service_account_not_configured"}:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="LDAP backend is unavailable.",
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    role = map_groups_to_role(user.groups, role_map)
    if role != "admin":
        # Authenticated but not authorised. Distinct 403 (not 401) so the
        # caller knows the credentials were accepted but the privilege was
        # insufficient. SOX-relevant: log the denied attempt.
        audit.emit(
            "mcp_revoke_forbidden",
            triggered_by="mcp_revoke",
            sub=user.dn,
            username=body.username,
            role=role,
            client_ip=client_ip,
            token_id=body.token_id,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token revocation requires the 'valdo-admins' (admin) role.",
        )

    try:
        get_revocation_cache().revoke(
            jti=body.token_id,
            reason=body.reason,
            revoked_by=user.dn,
        )
    except Exception as exc:  # noqa: BLE001 — surface a clear 503, never 500.
        logger.error("mcp_revoke_persist_failed token_id_suffix=%s err=%s",
                     body.token_id[-6:], exc)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Revocation blocklist is unavailable — token NOT revoked.",
        )

    audit.emit(
        "mcp_revoke_success",
        triggered_by="mcp_revoke",
        sub=user.dn,
        username=body.username,
        role=role,
        client_ip=client_ip,
        token_id=body.token_id,
        revoke_reason=body.reason,
    )
    return MCPRevokeResponse(
        revoked=True,
        token_id=body.token_id,
        revoked_by=user.dn,
    )

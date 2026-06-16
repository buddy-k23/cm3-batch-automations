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

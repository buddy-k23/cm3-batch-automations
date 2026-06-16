"""Authentication helpers for API key verification and role checks."""

from __future__ import annotations

import hmac
import logging
import os
import time
from dataclasses import dataclass
from typing import Dict

from fastapi import Depends, Header, HTTPException, Request, status


@dataclass(frozen=True)
class AuthContext:
    key_id: str
    role: str
    auth_kind: str = "api_key"   # "api_key" | "ldap"
    subject: str | None = None   # LDAP DN for ldap auth, None for api_key
    email: str | None = None
    name: str | None = None
    groups: tuple = ()           # tuple[str, ...] — group CNs from LDAP memberOf


def _parse_api_keys() -> Dict[str, str]:
    """Parse API key configuration into key->role mapping.

    Returns:
        Mapping of configured API keys to assigned role names.
    """
    raw = os.getenv("API_KEYS", "")
    keys: Dict[str, str] = {}
    for token in [x.strip() for x in raw.split(",") if x.strip()]:
        if ":" in token:
            key, role = token.split(":", 1)
            keys[key.strip()] = role.strip() or "tester"
        else:
            keys[token] = "tester"
    return keys


logger = logging.getLogger(__name__)


def _resolve_api_key_role(presented: str, keys: Dict[str, str]) -> str | None:
    """Resolve the role for a presented API key using a constant-time scan.

    Security (S13.5-3, #409): the previous implementation used a dict
    lookup (``keys.get(presented)``) whose short-circuiting comparison is
    timing-attackable — an attacker can recover a configured key byte by
    byte from response-time differences. This helper instead iterates the
    configured keys and compares each with :func:`hmac.compare_digest`,
    which compares the full length in (effectively) constant time. The
    scan is O(n) in the number of configured keys; that linear cost is the
    intended trade-off for not leaking match position via timing.

    The ``key:role`` mapping and role resolution are preserved unchanged:
    the role of the matched key is returned. Every configured key is
    compared even after a match so the running time does not depend on
    WHICH key matched.

    Args:
        presented: The candidate API key value from the request header.
        keys: Mapping of configured API keys to their assigned roles.

    Returns:
        The role assigned to the matched key, or ``None`` if no configured
        key matches.
    """
    presented_bytes = presented.encode("utf-8")
    matched_role: str | None = None
    for configured_key, role in keys.items():
        if hmac.compare_digest(configured_key.encode("utf-8"), presented_bytes):
            matched_role = role
    return matched_role


def verify_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> AuthContext:
    """Validate caller API key and return auth context.

    Args:
        request: Current FastAPI request object.
        x_api_key: API key value from ``X-API-Key`` header.

    Returns:
        AuthContext containing key identifier and resolved role.

    Raises:
        HTTPException: 401 if header is missing.
        HTTPException: 403 if key is invalid.
    """
    keys = _parse_api_keys()

    # SECURITY: Refuse to authenticate when no API keys are configured.
    # Previously this branch granted unrestricted admin access, which
    # constitutes a complete authentication bypass in production.
    if not keys:
        logger.error(
            "api_auth_misconfigured path=%s reason=no_api_keys_configured",
            request.url.path,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server is not configured with API keys.",
        )

    # S9-1 (#387): request.client.host is already proxy-corrected by
    # ProxyHeadersMiddleware (trusted X-Forwarded-For), so this is the REAL
    # client IP for the SOX auth-failure trail — not nginx's address.
    client_ip = request.client.host if request.client else "unknown"

    if not x_api_key:
        # S13.5-2 (#415): audit the failure FIRST, then raise the 401.
        # Ordering rationale (fail-closed, S13.5-1): if the audit write
        # itself fails it raises AuditWriteError, which the API error
        # handler turns into a 500 — a genuine audit outage is therefore
        # visible rather than silently swallowed. On the normal path the
        # event is recorded and the original 401 still reaches the client
        # (the audit emit never masks the auth error). No secret is logged.
        _audit_auth_failure(
            auth_kind="api_key", reason="missing_api_key", client_ip=client_ip
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing X-API-Key")

    # S13.5-3 (#409): constant-time scan over configured keys instead of a
    # timing-attackable dict lookup. Preserves the key:role mapping.
    role = _resolve_api_key_role(x_api_key, keys)
    if role is None:
        logger.warning("api_auth_failed path=%s reason=invalid_api_key", request.url.path)
        # Audit before raising the 403; the attempted key VALUE is never
        # logged (we record nothing key-derived for a failed lookup —
        # x_api_key[-6:] could leak entropy of a real near-miss key).
        _audit_auth_failure(
            auth_kind="api_key", reason="invalid_api_key", client_ip=client_ip
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid API key")

    return AuthContext(key_id=x_api_key[-6:], role=role)


def _audit_auth_failure(*, auth_kind: str, reason: str, client_ip: str) -> None:
    """Emit an auth_failure audit event (S13.5-2, #415).

    Thin indirection over :func:`src.utils.audit_logger.audit_auth_failure`
    with a lazy import so this module stays importable in contexts that do
    not pull in the audit logger. The audit write is fail-closed
    (AuditWriteError propagates) — see the call sites for the documented
    ordering vs. raising the 401/403.
    """
    from src.utils.audit_logger import audit_auth_failure

    audit_auth_failure(
        auth_kind=auth_kind,
        reason=reason,
        client_ip=client_ip,
        triggered_by="api",
    )


ROLE_ORDER = {"tester": 10, "mapping_owner": 20, "admin": 30}


def _try_resolve_session_cookie(request: Request) -> AuthContext | None:
    """Return AuthContext from a Starlette session cookie if present and fresh.

    Returns None when no SessionMiddleware is installed (auth.enabled=false)
    or when no/expired user session cookie is present, so the caller falls
    through to X-API-Key verification.
    """
    try:
        session = request.session
    except (AssertionError, AttributeError):
        return None
    user = session.get("user")
    if not user:
        return None
    iat = int(user.get("iat", 0))
    cfg = getattr(request.app.state, "ui_config", {}) or {}
    max_age_min = (
        cfg.get("auth", {})
        .get("session", {})
        .get("max_age_minutes", 60)
    )
    if iat and (time.time() - iat) > max_age_min * 60:
        return None
    sub = user.get("sub", "")
    return AuthContext(
        key_id=sub[-12:] if sub else "ldap",
        role=user.get("role", "tester"),
        auth_kind="ldap",
        subject=sub,
        email=user.get("email"),
        name=user.get("name"),
        groups=tuple(user.get("groups", []) or ()),
    )


def verify_session_or_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> AuthContext:
    """Accept either an LDAP-issued session cookie or a configured X-API-Key.

    Session wins when both are present (interactive user takes precedence
    over service-to-service header).
    """
    session_ctx = _try_resolve_session_cookie(request)
    if session_ctx is not None:
        return session_ctx
    return verify_api_key(request, x_api_key=x_api_key)


def require_api_key(_: AuthContext = Depends(verify_session_or_api_key)) -> AuthContext:
    """Require any valid auth (session cookie or API key) and return auth context.

    Symbol name is preserved so all existing routers that import and use
    ``require_api_key`` continue to work without modification.
    """
    return _


def require_role(minimum_role: str):
    """Create a dependency enforcing a minimum role.

    Args:
        minimum_role: Required minimum role (tester, mapping_owner, admin).

    Returns:
        Dependency function that validates caller role.
    """

    if minimum_role not in ROLE_ORDER:
        raise ValueError(f"Unsupported role requirement: {minimum_role}")

    def _require_role(ctx: AuthContext = Depends(verify_session_or_api_key)) -> AuthContext:
        caller_rank = ROLE_ORDER.get(ctx.role, -1)
        required_rank = ROLE_ORDER[minimum_role]
        if caller_rank < required_rank:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{minimum_role}' required",
            )
        return ctx

    return _require_role

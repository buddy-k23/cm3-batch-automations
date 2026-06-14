"""Login / logout / whoami endpoints for LDAPS-based authentication.

These routes are PUBLIC — they are never wrapped in Depends(require_api_key).
When auth.enabled is false in config/ui.yml, this router is not registered
at all (see src/api/main.py), so the endpoints simply do not exist and the
app behaves exactly as before (X-API-Key only).

Endpoints
---------
GET  /auth/login   — Serve the HTML login form.
POST /auth/login   — Accept username + password, bind via LDAPS, set session.
POST /auth/logout  — Clear the session cookie.
GET  /auth/whoami  — Return the current session user (401 if not signed in).
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from fastapi import APIRouter, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from src.api.auth_ldap import LdapAuthError, ldap_authenticate, map_groups_to_role
from src.utils.audit_logger import get_audit_logger

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

# Path to the login HTML page served from the existing static mount.
_LOGIN_HTML_PATH = (
    Path(__file__).parent.parent.parent
    / "reports" / "static" / "login.html"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_next(value: str | None) -> str:
    """Validate a post-login redirect target to prevent open-redirect attacks.

    Only same-site relative paths starting with a single '/' are accepted.
    Protocol-relative URLs (//evil.com) and absolute URLs are rejected.

    Args:
        value: The raw ``next`` parameter from the query string or form.

    Returns:
        A safe redirect path, defaulting to ``/ui``.
    """
    if not value:
        return "/ui"
    if not value.startswith("/") or value.startswith("//"):
        return "/ui"
    return value


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/login", response_class=HTMLResponse, include_in_schema=False)
async def login_page(request: Request, next: str = "/ui") -> HTMLResponse:
    """Serve the LDAPS login form HTML page.

    Stores the validated ``next`` redirect target in the session so it
    survives the POST round-trip even if the form does not include it.
    """
    request.session["auth_next"] = _safe_next(next)
    return HTMLResponse(_LOGIN_HTML_PATH.read_text(encoding="utf-8"))


@router.post("/login", include_in_schema=False)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/ui"),
):
    """Process a login form submission.

    Binds the supplied credentials against the corporate LDAP directory,
    maps the user's groups to a Valdo role, stores the user dict in the
    signed session cookie, and redirects to the validated ``next`` URL.

    On failure, emits an ``ldap_login_failure`` audit event and returns
    HTTP 401 with a generic error message (no username/password hint).
    """
    cfg = (getattr(request.app.state, "ui_config", {}) or {}).get("auth", {})
    ldap_cfg = cfg.get("ldap", {})
    role_map = ldap_cfg.get("group_role_map", {"*": "tester"})

    audit = get_audit_logger()
    try:
        user = ldap_authenticate(username, password, ldap_cfg)
    except LdapAuthError as exc:
        audit.emit(
            "ldap_login_failure",
            triggered_by="web_ui",
            username=username,
            reason=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    role = map_groups_to_role(user.groups, role_map)
    # Note: groups list is intentionally omitted from the session cookie.
    # Some corporate AD users belong to dozens of groups, which can push the
    # signed cookie past the ~4 KB per-cookie limit enforced by browsers and
    # HTTP clients, causing the cookie to be silently rejected. The role has
    # already been resolved from groups above, so groups don't need to live
    # in the session for authorization decisions.
    request.session["user"] = {
        "sub": user.dn,
        "email": user.email,
        "name": user.name,
        "groups": [],
        "role": role,
        "iat": int(time.time()),
    }
    audit.emit(
        "ldap_login_success",
        triggered_by="web_ui",
        sub=user.dn,
        role=role,
        groups=list(user.groups),
    )
    # Prefer the session-stored next (set on GET /auth/login) over the form
    # field, then fall back to the form field, then default to /ui.
    target = _safe_next(request.session.pop("auth_next", None) or next)
    return RedirectResponse(target, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    """Clear the session cookie and emit an audit event."""
    user = request.session.get("user") or {}
    request.session.clear()
    get_audit_logger().emit(
        "ldap_logout",
        triggered_by="web_ui",
        sub=user.get("sub", "unknown"),
    )
    return JSONResponse({"ok": True})


@router.get("/whoami")
async def whoami(request: Request):
    """Return a trimmed view of the current session user.

    Returns HTTP 401 when no valid session is present. The raw LDAP DN
    (``sub``) is deliberately omitted from the response to limit info
    disclosure; it is stored in the session but not surfaced to the client.
    """
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return {
        "name": user.get("name"),
        "email": user.get("email"),
        "role": user.get("role"),
        "groups": user.get("groups", []),
    }


# ---------------------------------------------------------------------------
# E2E stub — replaces ldap_authenticate with an in-memory fake when the
# VALDO_E2E_STUB env var is set to "1". This is opt-in via env var so
# production deployments are unaffected; the stub is loaded only when the
# CI/test fixture (tests/e2e/conftest.py::auth_enabled_server) explicitly
# requests it. Never set VALDO_E2E_STUB=1 outside of E2E test runs.
# ---------------------------------------------------------------------------
import os as _os  # noqa: E402  (intentional late import — guarded block)

if _os.getenv("VALDO_E2E_STUB") == "1":  # pragma: no cover - test-only path
    from src.api import auth_ldap as _auth_ldap_mod

    def _stub_ldap_authenticate(username, password, cfg):  # noqa: ARG001
        """In-memory replacement for ldap_authenticate used by E2E tests.

        Returns a fixed LdapUser for the canonical e2e credentials, otherwise
        raises LdapAuthError so the 401 invalid-credentials UX can be tested.
        """
        if username == "e2e-user" and password == "e2e-password":
            return _auth_ldap_mod.LdapUser(
                dn=f"CN={username},OU=E2E,DC=test,DC=local",
                email=f"{username}@test.local",
                name="E2E User",
                groups=("valdo-admins",),
            )
        raise _auth_ldap_mod.LdapAuthError("invalid_credentials")

    # Replace the imported symbol used by login_submit AND the source module
    # so any subsequent re-import does not pick up the real LDAP function.
    ldap_authenticate = _stub_ldap_authenticate  # type: ignore[assignment]  # noqa: F811
    _auth_ldap_mod.ldap_authenticate = _stub_ldap_authenticate  # type: ignore[assignment]

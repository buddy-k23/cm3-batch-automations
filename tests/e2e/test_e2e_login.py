"""E2E Playwright tests for the LDAPS login flow.

These tests boot a dedicated uvicorn subprocess (via the
``auth_enabled_server`` fixture in ``tests/e2e/conftest.py``) with
``config/ui.yml`` temporarily flipped to ``auth.enabled: true`` and
``cookie_secure: false``, plus ``VALDO_E2E_STUB=1`` so the guarded
stub at the bottom of ``src/api/routers/auth.py`` replaces
``ldap_authenticate`` with an in-memory fake. This means the tests
exercise the real FastAPI application end-to-end — including
``SessionMiddleware``, the ``/auth`` router, and the login HTML page —
without ever opening a TCP connection to the corporate LDAP directory.

The tests cover the three observable end-to-end behaviours that
together prove "LDAP login works":

1. The login HTML form is served and wired to ``POST /auth/login``.
2. Invalid credentials surface as an inline ``#err`` message and the
   ``whoami`` endpoint reports no session.
3. Valid credentials (``e2e-user`` / ``e2e-password`` — see the stub)
   set a signed session cookie, redirect the browser to ``/ui``, and
   subsequent calls to ``/auth/whoami`` return 200 with the mapped
   role and group memberships.

The "protected route" check uses ``/api/v1/mappings/`` because
``/api/v1/system/health`` and ``/ui`` are intentionally anonymous.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect


class TestLoginPageRenders:
    """The /auth/login page must serve the form HTML with the expected widgets."""

    def test_login_page_serves_form(
        self, page: Page, auth_enabled_server: str
    ) -> None:
        page.goto(f"{auth_enabled_server}/auth/login")
        expect(page.locator("#username")).to_be_visible()
        expect(page.locator("#password")).to_be_visible()
        expect(page.locator("#submitBtn")).to_be_visible()
        expect(page.locator("form#loginForm")).to_have_attribute(
            "action", "/auth/login"
        )

    def test_protected_route_rejects_anonymous_users(
        self, page: Page, auth_enabled_server: str
    ) -> None:
        """An auth-protected API route must reject anonymous callers.

        ``/api/v1/system/health`` and ``/ui`` are intentionally anonymous,
        so we hit ``/api/v1/mappings/`` (which is wrapped in
        ``Depends(require_api_key)``) without any X-API-Key header.
        """
        resp = page.request.get(
            f"{auth_enabled_server}/api/v1/mappings/",
            headers={},  # explicitly no X-API-Key
        )
        # 401 Missing X-API-Key — see src/api/auth.py::verify_api_key
        assert resp.status == 401, (
            f"expected 401, got {resp.status}: {resp.text()[:200]}"
        )


class TestInvalidCredentialsRejected:
    """Bad credentials must surface as a 401 with the inline #err message."""

    def test_bad_credentials_show_error(
        self, page: Page, auth_enabled_server: str
    ) -> None:
        page.goto(f"{auth_enabled_server}/auth/login")
        page.locator("#username").fill("nobody")
        page.locator("#password").fill("wrong")
        page.locator("#submitBtn").click()
        # The fetch handler in login.html renders #err on 401
        expect(page.locator("#err")).to_have_text(
            "Invalid username or password.", timeout=5000
        )

    def test_whoami_returns_401_without_session(
        self, page: Page, auth_enabled_server: str
    ) -> None:
        resp = page.request.get(f"{auth_enabled_server}/auth/whoami")
        assert resp.status == 401, (
            f"expected 401, got {resp.status}: {resp.text()[:200]}"
        )


class TestSuccessfulLogin:
    """Stubbed-LDAP success path: cookie set, redirect lands on /ui, whoami 200.

    The ``auth_enabled_server`` fixture sets ``VALDO_E2E_STUB=1`` for the
    uvicorn subprocess, so the guarded block at the bottom of
    ``src/api/routers/auth.py`` replaces ``ldap_authenticate`` with a
    fixture that accepts ``e2e-user`` / ``e2e-password``.
    """

    def test_successful_login_sets_session_and_redirects(
        self, page: Page, auth_enabled_server: str
    ) -> None:
        page.goto(f"{auth_enabled_server}/auth/login")
        page.locator("#username").fill("e2e-user")
        page.locator("#password").fill("e2e-password")
        # The login form's JS uses `fetch(..., {redirect: 'follow'})` and
        # then `window.location.href = r.url`, so we wait for the
        # navigation kicked off by that assignment.
        with page.expect_navigation(url=lambda u: "/ui" in u, timeout=10000):
            page.locator("#submitBtn").click()

        # The browser is now on the /ui page (stub maps groups to admin role)
        assert "/ui" in page.url, f"expected /ui in URL, got {page.url}"

        # whoami must now succeed via the session cookie
        resp = page.request.get(f"{auth_enabled_server}/auth/whoami")
        assert resp.status == 200, (
            f"expected 200, got {resp.status}: {resp.text()[:200]}"
        )
        body = resp.json()
        assert body["name"] == "E2E User"
        assert body["email"] == "e2e-user@test.local"
        # The stub returns groups=("valdo-admins",) which maps to "admin"
        # via auth.ldap.group_role_map in config/ui.yml.
        assert body["role"] in {"tester", "mapping_owner", "admin"}
        assert "valdo-admins" in (body.get("groups") or [])

    def test_logged_in_session_authenticates_protected_api(
        self, page: Page, auth_enabled_server: str
    ) -> None:
        """After login, the session cookie alone authenticates /api/v1/mappings/.

        This proves verify_session_or_api_key prefers the LDAP session
        over the missing X-API-Key header.
        """
        # Log in first
        page.goto(f"{auth_enabled_server}/auth/login")
        page.locator("#username").fill("e2e-user")
        page.locator("#password").fill("e2e-password")
        with page.expect_navigation(url=lambda u: "/ui" in u, timeout=10000):
            page.locator("#submitBtn").click()

        # Now hit a protected API route without X-API-Key — the session
        # cookie is sent automatically by the browser context.
        resp = page.request.get(f"{auth_enabled_server}/api/v1/mappings/")
        assert resp.status == 200, (
            f"expected 200 (session-authenticated), got {resp.status}: "
            f"{resp.text()[:200]}"
        )

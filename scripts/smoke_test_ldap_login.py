"""Smoke-test the LDAPS login flow end-to-end without a browser.

This script boots the FastAPI app via uvicorn with auth.enabled=true and
VALDO_E2E_STUB=1, then drives the /auth/login flow with httpx and asserts
the key behaviours that the Playwright tests would normally cover. It is
useful for environments where corporate IT policy blocks Playwright from
launching Edge/Chromium in automation mode.

Run with:

    python scripts/smoke_test_ldap_login.py
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).parent.parent
UI_YML = PROJECT_ROOT / "config" / "ui.yml"


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> int:
    if not UI_YML.exists():
        print(f"FAIL: ui.yml not found at {UI_YML}", file=sys.stderr)
        return 2

    original_text = UI_YML.read_text(encoding="utf-8")
    backup_path = UI_YML.with_suffix(".yml.smoke-bak")
    shutil.copy(UI_YML, backup_path)

    patched = original_text.replace(
        "auth:\n  enabled: false",
        "auth:\n  enabled: true",
    ).replace(
        "    cookie_secure: true",
        "    cookie_secure: false",
    )
    if patched == original_text:
        print("FAIL: ui.yml shape changed; smoke-test cannot proceed.", file=sys.stderr)
        return 3
    UI_YML.write_text(patched, encoding="utf-8")

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "API_KEYS": "dev-key:admin",
        "VALDO_SESSION_SIGNING_KEY": "0123456789abcdef" * 4,
        "LDAP_SERVICE_DN": "CN=svc,DC=test,DC=local",
        "LDAP_SERVICE_PASSWORD": "stub",
        "VALDO_E2E_STUB": "1",
        "ALLOWED_ORIGINS": f"http://127.0.0.1:{port}",
    }

    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "src.api.main:app",
            "--host", "127.0.0.1", "--port", str(port),
        ],
        cwd=str(PROJECT_ROOT),
        env=env,
    )

    failures: list[str] = []
    try:
        # Wait for /api/v1/system/health
        ready = False
        for _ in range(60):
            if proc.poll() is not None:
                print(f"FAIL: uvicorn exited early with code {proc.returncode}", file=sys.stderr)
                return 4
            try:
                r = httpx.get(f"{base}/api/v1/system/health", timeout=2.0)
                if r.is_success:
                    ready = True
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if not ready:
            failures.append("server never became healthy")
            return 5

        print(f"OK   : server is up at {base}")

        # ---- Test 1: login page renders with the expected form ----
        r = httpx.get(f"{base}/auth/login")
        if r.status_code != 200:
            failures.append(f"login page status={r.status_code}")
        elif 'id="username"' not in r.text or 'id="password"' not in r.text:
            failures.append("login page missing #username or #password")
        elif 'action="/auth/login"' not in r.text:
            failures.append("login page form action mismatch")
        else:
            print("OK   : GET /auth/login serves the form HTML")

        # ---- Test 2: anonymous protected route returns 401 ----
        r = httpx.get(f"{base}/api/v1/mappings/")
        if r.status_code != 401:
            failures.append(f"protected route status={r.status_code} (expected 401)")
        else:
            print("OK   : GET /api/v1/mappings/ rejects anonymous (401)")

        # ---- Test 3: whoami returns 401 without session ----
        r = httpx.get(f"{base}/auth/whoami")
        if r.status_code != 401:
            failures.append(f"whoami unauth status={r.status_code} (expected 401)")
        else:
            print("OK   : GET /auth/whoami rejects unauth (401)")

        # ---- Test 4: bad credentials return 401 ----
        with httpx.Client(base_url=base, follow_redirects=False) as c:
            r = c.post(
                "/auth/login",
                data={"username": "nobody", "password": "wrong", "next": "/ui"},
            )
            if r.status_code != 401:
                failures.append(f"bad creds status={r.status_code} (expected 401)")
            else:
                print("OK   : POST /auth/login with bad creds returns 401")

        # ---- Test 5: good creds set session cookie + 303 redirect ----
        with httpx.Client(base_url=base, follow_redirects=False) as c:
            r = c.post(
                "/auth/login",
                data={"username": "e2e-user", "password": "e2e-password", "next": "/ui"},
            )
            if r.status_code != 303:
                failures.append(f"good creds status={r.status_code} (expected 303)")
            elif r.headers.get("location") != "/ui":
                failures.append(f"redirect location={r.headers.get('location')!r} (expected /ui)")
            else:
                print("OK   : POST /auth/login with good creds returns 303 -> /ui")

            cookies = c.cookies
            if "valdo_session" not in cookies:
                failures.append("session cookie 'valdo_session' was not set")
            else:
                print("OK   : valdo_session cookie set after login")

            # ---- Test 6: whoami returns 200 with the stub user ----
            r = c.get("/auth/whoami")
            if r.status_code != 200:
                failures.append(f"whoami after login status={r.status_code}")
            else:
                body = r.json()
                if body.get("name") != "E2E User":
                    failures.append(f"whoami.name={body.get('name')!r}")
                elif body.get("role") not in {"tester", "mapping_owner", "admin"}:
                    failures.append(f"whoami.role={body.get('role')!r}")
                elif "valdo-admins" not in (body.get("groups") or []):
                    failures.append(f"whoami.groups={body.get('groups')!r}")
                else:
                    print(f"OK   : GET /auth/whoami returns user with role={body['role']!r}")

            # ---- Test 7: session cookie authenticates protected route ----
            r = c.get("/api/v1/mappings/")
            if r.status_code != 200:
                failures.append(
                    f"session-auth on /api/v1/mappings/ status={r.status_code} "
                    f"body={r.text[:150]}"
                )
            else:
                print("OK   : session cookie authenticates /api/v1/mappings/")

        # ---- Test 8: /ui still serves anonymously ----
        r = httpx.get(f"{base}/ui")
        if r.status_code != 200:
            failures.append(f"/ui status={r.status_code}")
        else:
            print("OK   : GET /ui serves the SPA")

    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        try:
            UI_YML.write_text(original_text, encoding="utf-8")
        finally:
            try:
                backup_path.unlink()
            except FileNotFoundError:
                pass

    print()
    if failures:
        print(f"FAIL: {len(failures)} check(s) failed:")
        for f in failures:
            print(f"   - {f}")
        return 1
    print("PASS: all LDAP-login smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

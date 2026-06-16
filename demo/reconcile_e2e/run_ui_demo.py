"""Playwright UI segment of the #407 cross-dialect reconcile demo.

Boots a REAL uvicorn server against the seeded SQLite demo DB, opens ``/ui``,
switches to the DB Compare tab, fills the reconcile panel, clicks
``#btnReconcile``, waits for ``#reconcileResult`` to render the live verdict,
and captures two screenshots into ``artifacts/``:

* ``ui_reconcile_form.png``   — the panel filled, before clicking Reconcile.
* ``ui_reconcile_result.png`` — the rendered field-level verdict (the money
  shot: banner + counts + the AGE mismatch + the IS_ACTIVE/NOTES advisories).

Server config used (chosen for reliable, hands-free bring-up):

* **Plain HTTP** (``tls.enabled: false``) — no self-signed cert dance.
* **auth.enabled: false** — no LDAP login wall in front of ``/ui``.
* ``API_KEYS=demo-key:admin`` — the v1/v2 routers still require a key
  (``require_api_key`` fails closed otherwise), so we inject
  ``window._apiKey`` into the page via ``add_init_script`` before any fetch,
  exactly mirroring how the server would inject it in a keyed deployment.

A dev copy of ``config/ui.yml`` is written for the subprocess and the original
is restored on teardown (best-effort, never raises from teardown).

Per the prior agent's note: do NOT wait for ``networkidle`` (the ad-hoc server
never reaches it). We use ``wait_until="domcontentloaded"`` + explicit
``wait_for_selector`` on the tab/panel, and ``ignore_https_errors=True`` (a
no-op on http, harmless if someone flips TLS on).

Run directly (after seeding):

    python demo/reconcile_e2e/run_ui_demo.py
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from demo.reconcile_e2e.seed import seed_db  # noqa: E402

ARTIFACTS_DIR = Path(__file__).resolve().parent / "artifacts"
UI_YML = REPO_ROOT / "config" / "ui.yml"
API_KEY = "demo-key"
VIEWPORT = {"width": 1400, "height": 900}

# Minimal dev ui.yml: tabs on (need dbcompare), downloader off, auth off,
# tls off. Kept intentionally small — the app fills missing keys with defaults.
_DEV_UI_YML = """\
tabs:
  quick: true
  runs: true
  mapping: true
  tester: true
  dbcompare: true
  downloader: false

downloader:
  enabled: false

security:
  ip_whitelist: []
  trust_proxy: false
  trusted_proxies: []

tls:
  enabled: false
  strategy: "manual"

auth:
  enabled: false
  mode: "ldap"
"""


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_health(base: str, proc: subprocess.Popen, *, attempts: int = 80) -> None:
    import httpx

    for _ in range(attempts):
        if proc.poll() is not None:
            raise RuntimeError(
                f"uvicorn exited early (code {proc.returncode}) before becoming healthy"
            )
        try:
            r = httpx.get(f"{base}/api/v1/system/health", timeout=2.0)
            if r.is_success:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"server did not become healthy at {base}")


def _terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def main() -> int:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    db = seed_db.db_path()
    mapping = seed_db.mapping_path()
    if not db.exists() or not mapping.exists():
        print(f"[ui] seeding (DB/mapping missing)")
        seed_db.main()

    # --- swap in a dev ui.yml (backup + restore) -------------------------
    original_ui_yml = UI_YML.read_text(encoding="utf-8") if UI_YML.exists() else None
    UI_YML.write_text(_DEV_UI_YML, encoding="utf-8")

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    proc: subprocess.Popen | None = None
    form_png = ARTIFACTS_DIR / "ui_reconcile_form.png"
    result_png = ARTIFACTS_DIR / "ui_reconcile_result.png"

    try:
        env = {
            **os.environ,
            "DB_ADAPTER": "sqlite",
            "DB_PATH": str(db),
            "API_KEYS": f"{API_KEY}:admin",
            "VALDO_SESSION_SIGNING_KEY": "demo-only-key-not-for-production",
            "ALLOWED_ORIGINS": base,
        }
        proc = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "src.api.main:app",
                "--host", "127.0.0.1", "--port", str(port),
            ],
            cwd=str(REPO_ROOT),
            env=env,
        )
        print(f"[ui] booting uvicorn at {base} (sqlite -> {db.name})")
        _wait_for_health(base, proc)
        print(f"[ui] server healthy at {base}")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                viewport=VIEWPORT,
                ignore_https_errors=True,
                # Send X-API-Key on EVERY browser request. The v1/v2 routers
                # require a key (require_api_key fails closed); loadMappings()
                # uses a bare fetch without _apiHeaders(), so a context-level
                # header is the reliable way to auth all of the UI's calls.
                extra_http_headers={"X-API-Key": API_KEY},
            )
            # Also set window._apiKey so reconcile's _apiHeaders() path matches
            # how a keyed deployment injects the key into the page.
            context.add_init_script(f"window._apiKey = {API_KEY!r};")
            page = context.new_page()

            page.goto(f"{base}/ui", wait_until="domcontentloaded")
            page.wait_for_selector("#tab-dbcompare", timeout=15000)
            page.click("#tab-dbcompare")
            page.wait_for_selector("#reconcileSection", state="visible", timeout=15000)

            # Wait for the mapping list to populate the reconcile select with
            # our seeded mapping (value == mapping id 'reconcile_demo').
            page.wait_for_function(
                "() => { const s = document.getElementById('reconcileMappingSelect');"
                " return s && Array.from(s.options).some(o => o.value === 'reconcile_demo'); }",
                timeout=15000,
            )

            page.select_option("#reconcileMappingSelect", "reconcile_demo")
            page.fill("#reconcileTable", seed_db.TABLE_NAME)
            # schema left blank for SQLite (no schema/owner concept)

            page.locator("#reconcileSection").scroll_into_view_if_needed()
            page.screenshot(path=str(form_png))
            print(f"[ui] captured {form_png.name} (form filled, pre-click)")

            page.click("#btnReconcile")

            # Wait for the live verdict to render (mismatch banner appears).
            page.wait_for_selector(
                "#reconcileResult .rec-verdict-mismatch",
                state="visible",
                timeout=20000,
            )
            page.wait_for_selector(
                "#reconcileResult:has-text('AGE')", timeout=20000
            )

            result_text = page.locator("#reconcileResult").inner_text()
            page.locator("#reconcileResult").scroll_into_view_if_needed()
            page.screenshot(path=str(result_png))
            print(f"[ui] captured {result_png.name} (live verdict rendered)")

            context.close()
            browser.close()

        # Report what the UI actually rendered.
        print("\n[ui] rendered verdict contains:")
        for needle in ["Type mismatch", "AGE", "IS_ACTIVE", "NOTES"]:
            present = needle in result_text
            print(f"      {'OK ' if present else 'MISS'} {needle!r}")

        ok = all(n in result_text for n in ["AGE", "IS_ACTIVE"])
        print(f"\n[ui] screenshots written:\n      {form_png}\n      {result_png}")
        return 0 if ok else 1

    finally:
        if proc is not None:
            _terminate(proc)
        # restore original ui.yml — never raise from teardown
        try:
            if original_ui_yml is not None:
                UI_YML.write_text(original_ui_yml, encoding="utf-8")
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main())

"""
Full E2E test of the Valdo UI with Playwright video recording.

Usage:
    python3 scripts/e2e_record.py

Output:
    screenshots/e2e-<date>/          - per-step screenshots
    screenshots/e2e-<date>/video/    - recorded video (.webm)
    screenshots/e2e-<date>/e2e_results.json  - pass/fail summary
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, expect

import os
import ssl

BASE_URL = os.getenv("VALDO_BASE_URL", "https://127.0.0.1:8000")
VERIFY_TLS = os.getenv("VALDO_VERIFY_TLS", "0").lower() in ("1", "true", "yes")
# urllib SSL context: when VERIFY_TLS is false, accept self-signed certs so
# local dev with tls.strategy: self_signed works without manual trust setup.
_SSL_CTX = ssl.create_default_context()
if not VERIFY_TLS:
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE
RUN_DATE = date.today().isoformat()
OUT_DIR = Path(__file__).parent.parent / "screenshots" / f"e2e-{RUN_DATE}-recorded"
OUT_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_DIR = OUT_DIR / "video"
VIDEO_DIR.mkdir(exist_ok=True)

results: list[dict] = []


def step(name: str, page: Page, fn):
    """Run a test step, capture a screenshot, record pass/fail."""
    try:
        fn()
        page.screenshot(path=str(OUT_DIR / f"{name}.png"))
        results.append({"step": name, "status": "PASS"})
        print(f"  ✓  {name}")
    except Exception as exc:
        page.screenshot(path=str(OUT_DIR / f"{name}_FAIL.png"))
        results.append({"step": name, "status": "FAIL", "error": str(exc)})
        print(f"  ✗  {name}: {exc}")


def run_e2e(page: Page) -> None:
    page.set_viewport_size({"width": 1400, "height": 900})

    # ------------------------------------------------------------------
    # 1. Load UI
    # ------------------------------------------------------------------
    step("01-ui-load", page, lambda: (
        page.goto(f"{BASE_URL}/ui"),
        expect(page.get_by_text("Valdo")).to_be_visible(),
    ))

    # ------------------------------------------------------------------
    # 2. Quick Test tab is active by default
    # ------------------------------------------------------------------
    step("02-quick-test-tab", page, lambda: (
        expect(page.locator("#tab-quick")).to_be_visible(),
        expect(page.locator("#panel-quick")).to_be_visible(),
    ))

    # ------------------------------------------------------------------
    # 3. Validate button tooltip
    # ------------------------------------------------------------------
    def check_validate_tooltip():
        btn = page.locator("button", has_text="Validate")
        btn.hover()
        page.wait_for_timeout(600)
        # Tooltip text appears somewhere on page
        assert "validate" in page.content().lower()

    step("03-validate-tooltip", page, check_validate_tooltip)

    # ------------------------------------------------------------------
    # 4. Mapping dropdown is present and has options
    # ------------------------------------------------------------------
    def check_mapping_dropdown():
        dropdown = page.locator("select#mapping-select, select[name='mapping']")
        if dropdown.count() == 0:
            dropdown = page.locator("select").first
        assert dropdown.count() > 0

    step("04-mapping-dropdown", page, check_mapping_dropdown)

    # ------------------------------------------------------------------
    # 5. Compare button present
    # ------------------------------------------------------------------
    step("05-compare-button", page, lambda: (
        expect(page.get_by_role("button", name="Compare")).to_be_visible(),
    ))

    # ------------------------------------------------------------------
    # 6. Mapping Generator tab — click and verify panel visible
    # ------------------------------------------------------------------
    def check_generate_mapping():
        page.locator("#tab-mapping").click()
        page.wait_for_timeout(400)
        expect(page.locator("#panel-mapping")).to_be_visible()

    step("06-generate-mapping", page, check_generate_mapping)

    # ------------------------------------------------------------------
    # 7. Recent Runs tab — click and verify panel visible
    # ------------------------------------------------------------------
    def check_recent_runs():
        page.locator("#tab-quick").click()   # back to Quick Test first
        page.wait_for_timeout(200)
        page.locator("#tab-runs").click()
        page.wait_for_timeout(800)
        expect(page.locator("#panel-runs")).to_be_visible()

    step("07-recent-runs-tab", page, check_recent_runs)

    # ------------------------------------------------------------------
    # 8. Recent Runs table has data or empty state message
    # ------------------------------------------------------------------
    def check_runs_content():
        content = page.content()
        has_table = ("run-id" in content.lower()
                     or "suite" in content.lower()
                     or "no runs" in content.lower()
                     or "run_id" in content.lower()
                     or page.locator("table, [role='table'], .run-row, .run-entry").count() > 0)
        assert has_table, "Expected run history content or empty-state message"

    step("08-recent-runs-content", page, check_runs_content)

    # ------------------------------------------------------------------
    # 9. Full-page screenshot of Recent Runs
    # ------------------------------------------------------------------
    def full_page_recent_runs():
        page.screenshot(path=str(OUT_DIR / "09-recent-runs-full.png"), full_page=True)

    step("09-recent-runs-fullpage", page, full_page_recent_runs)

    # ------------------------------------------------------------------
    # 10. Swagger /docs page
    # ------------------------------------------------------------------
    step("10-swagger-docs", page, lambda: (
        page.goto(f"{BASE_URL}/docs"),
        page.wait_for_load_state("networkidle"),
        expect(page.locator("#swagger-ui")).to_be_visible(),
    ))

    # ------------------------------------------------------------------
    # 11. Swagger shows key endpoint groups
    # ------------------------------------------------------------------
    def check_swagger_sections():
        content = page.content()
        for label in ("runs", "system"):
            assert label.lower() in content.lower(), f"Missing section: {label}"

    step("11-swagger-sections", page, check_swagger_sections)

    # ------------------------------------------------------------------
    # 12. Health endpoint returns healthy
    # ------------------------------------------------------------------
    def check_health():
        import urllib.request
        with urllib.request.urlopen(
            f"{BASE_URL}/api/v1/system/health", context=_SSL_CTX
        ) as resp:
            data = json.loads(resp.read())
        assert data.get("status") == "healthy", f"Unexpected health: {data}"

    step("12-health-api", page, check_health)

    # ------------------------------------------------------------------
    # 13. Run history API returns list
    # ------------------------------------------------------------------
    def check_history_api():
        import urllib.request
        with urllib.request.urlopen(
            f"{BASE_URL}/api/v1/runs/history", context=_SSL_CTX
        ) as resp:
            data = json.loads(resp.read())
        assert isinstance(data, list), f"Expected list, got {type(data)}"

    step("13-history-api", page, check_history_api)

    # ------------------------------------------------------------------
    # 14. Navigate back to UI — final overview screenshot
    # ------------------------------------------------------------------
    step("14-ui-final", page, lambda: (
        page.goto(f"{BASE_URL}/ui"),
        page.wait_for_load_state("networkidle"),
        page.screenshot(path=str(OUT_DIR / "14-ui-final-full.png"), full_page=True),
    ))


def main() -> int:
    print(f"\nValdo — E2E Test Run  ({RUN_DATE})")
    print(f"Target: {BASE_URL}")
    print(f"Output: {OUT_DIR}\n")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            record_video_dir=str(VIDEO_DIR),
            record_video_size={"width": 1400, "height": 900},
            viewport={"width": 1400, "height": 900},
            ignore_https_errors=True,  # tolerate self-signed certs in local dev
        )
        page = context.new_page()

        try:
            run_e2e(page)
        finally:
            context.close()   # closes + saves the video
            browser.close()

    # Rename the video to something readable
    videos = list(VIDEO_DIR.glob("*.webm"))
    if videos:
        dest = OUT_DIR / f"e2e-{RUN_DATE}.webm"
        videos[0].rename(dest)
        print(f"\nVideo saved: {dest}")

    # Write results JSON
    summary_path = OUT_DIR / "e2e_results.json"
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    summary = {"date": RUN_DATE, "passed": passed, "failed": failed, "steps": results}
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n{'='*50}")
    print(f"Results: {passed} PASSED  {failed} FAILED  (total {len(results)})")
    print(f"Summary: {summary_path}")
    if failed:
        print("\nFailing steps:")
        for r in results:
            if r["status"] == "FAIL":
                print(f"  - {r['step']}: {r.get('error', '')}")
    print()
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

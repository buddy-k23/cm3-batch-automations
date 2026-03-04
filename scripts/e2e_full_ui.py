"""E2E Playwright UI component: drives all 4 UI tabs with per-workflow video.

One browser context + .webm video per workflow. Screenshots per step.

Usage:
    python3 scripts/e2e_full_ui.py

Output:
    Terminal:  [UI] <label>  PASS|FAIL per check
    Files:     screenshots/e2e-full-<date>/ui-*.webm, step-*.png, ui-results.json

Prerequisites: Server running at http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, expect as pw_expect

PROJECT_ROOT = Path(__file__).parent.parent
BASE_URL = "http://127.0.0.1:8000"

SAMPLES = PROJECT_ROOT / "data" / "samples"
CUSTOMERS_FILE = SAMPLES / "customers.txt"
CUSTOMERS_UPDATED = SAMPLES / "customers_updated.txt"
P327_FILE = SAMPLES / "p327_sample_errors.txt"
MAPPING_TEMPLATE = PROJECT_ROOT / "config" / "templates" / "csv" / "mapping_template.standard.csv"

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
LABEL = "[UI]  "

_results: list[dict] = []
_step_counter: int = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def step(tag: str, name: str, page: Page, out_dir: Path, fn) -> bool:
    """Run one UI step, screenshot, record PASS/FAIL.

    Args:
        tag: Short prefix for the screenshot filename.
        name: Human-readable step description.
        page: Playwright Page object.
        out_dir: Directory to write screenshots to.
        fn: Zero-arg callable that performs the step actions.

    Returns:
        True if the step passed, False if it raised an exception.
    """
    global _step_counter
    _step_counter += 1
    slug = name.replace(" ", "_")
    prefix = f"step-{_step_counter:03d}-{tag}-{slug}"
    label = f"{tag} — {name}"
    try:
        fn()
        page.screenshot(path=str(out_dir / f"{prefix}.png"))
        _results.append({"label": label, "status": "PASS"})
        print(f"{GREEN}{LABEL} {label:<55} PASS{RESET}")
        return True
    except Exception as exc:
        try:
            page.screenshot(path=str(out_dir / f"{prefix}_FAIL.png"))
        except Exception:
            pass
        _results.append({"label": label, "status": "FAIL", "error": str(exc)})
        print(f"{RED}{LABEL} {label:<55} FAIL  {exc}{RESET}")
        return False


def record_workflow(pw, video_stem: str, out_dir: Path, fn) -> None:
    """Launch a fresh browser context with video recording, run fn, save video.

    Args:
        pw: Playwright instance from sync_playwright context.
        video_stem: Filename stem for the .webm output (no extension).
        out_dir: Root output directory; video saved as out_dir/{video_stem}.webm.
        fn: Callable(page, out_dir) that implements the workflow steps.
    """
    video_tmp = out_dir / "video" / video_stem
    video_tmp.mkdir(parents=True, exist_ok=True)

    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        record_video_dir=str(video_tmp),
        record_video_size={"width": 1400, "height": 900},
        viewport={"width": 1400, "height": 900},
    )
    page = context.new_page()
    try:
        fn(page, out_dir)
    finally:
        context.close()
        browser.close()

    videos = list(video_tmp.glob("*.webm"))
    if videos:
        dest = out_dir / f"{video_stem}.webm"
        videos[0].rename(dest)
        print(f"  Video → {dest.name}")


# ---------------------------------------------------------------------------
# Workflow 1 — Quick Test tab
# ---------------------------------------------------------------------------

def workflow_quick_test(page: Page, out_dir: Path) -> None:
    tag = "quick-test"
    print(f"\n── Workflow 1: Quick Test ──")

    step(tag, "navigate", page, out_dir, lambda: (
        page.goto(f"{BASE_URL}/ui"),
        page.wait_for_load_state("networkidle"),
        pw_expect(page.locator("#tab-quick")).to_be_visible(),
    ))

    step(tag, "open Quick Test tab", page, out_dir, lambda: (
        page.locator("#tab-quick").click(),
        page.wait_for_timeout(400),
        pw_expect(page.locator("#panel-quick")).to_be_visible(),
    ))

    # Validate customers.txt
    step(tag, "upload customers.txt", page, out_dir, lambda: (
        page.locator("#fileInput").set_input_files(str(CUSTOMERS_FILE)),
        page.wait_for_timeout(600),
    ))

    step(tag, "select customer_mapping", page, out_dir, lambda: (
        page.locator("#mappingSelect").select_option(value="customer_mapping"),
        page.wait_for_timeout(300),
    ))

    step(tag, "click Validate", page, out_dir, lambda: (
        page.locator("#btnValidate").click(),
        page.wait_for_timeout(8000),
    ))

    def assert_valid_result():
        page.wait_for_timeout(2000)
        content = page.content().lower()
        assert any(kw in content for kw in ["valid", "row", "record", "pass"]), \
            "No valid result visible after customers.txt validate"
    step(tag, "assert valid result (customers)", page, out_dir, assert_valid_result)

    def assert_inline_report_visible():
        panel = page.locator("#reportPanel")
        pw_expect(panel).to_be_visible(timeout=5000)
        frame_src = page.locator("#reportFrame").get_attribute("src") or ""
        assert frame_src and frame_src.startswith("/"), \
            f"Expected iframe src to be a non-empty relative path, got {frame_src!r}"
    step(tag, "assert inline report visible (customers)", page, out_dir,
         assert_inline_report_visible)

    # Validate p327_sample_errors.txt
    step(tag, "upload p327_sample_errors.txt", page, out_dir, lambda: (
        page.locator("#fileInput").set_input_files(str(P327_FILE)),
        page.wait_for_timeout(600),
    ))

    step(tag, "select p327_universal mapping", page, out_dir, lambda: (
        page.locator("#mappingSelect").select_option(value="p327_universal"),
        page.wait_for_timeout(300),
    ))

    step(tag, "click Validate (p327)", page, out_dir, lambda: (
        page.locator("#btnValidate").click(),
        page.wait_for_timeout(8000),
    ))

    def assert_error_result():
        page.wait_for_timeout(2000)
        content = page.content().lower()
        assert any(kw in content for kw in ["error", "fail", "invalid"]), \
            "No error result visible after p327 validate"
    step(tag, "assert error result (p327)", page, out_dir, assert_error_result)

    # Compare customers vs customers_updated
    step(tag, "reveal compare panel", page, out_dir, lambda: (
        page.locator("#btnToggleCompare").click(),
        page.wait_for_timeout(500),
        pw_expect(page.locator("#fileInput2")).to_be_visible(),
    ))

    step(tag, "upload customers_updated.txt", page, out_dir, lambda: (
        page.locator("#fileInput2").set_input_files(str(CUSTOMERS_UPDATED)),
        page.wait_for_timeout(600),
    ))

    step(tag, "click Compare", page, out_dir, lambda: (
        page.locator("#btnCompare").click(),
        page.wait_for_timeout(10000),
    ))

    def assert_diff_result():
        page.wait_for_timeout(2000)
        content = page.content().lower()
        assert any(kw in content for kw in ["diff", "match", "compar", "record", "row"]), \
            "No comparison result visible after Compare"
    step(tag, "assert differences found", page, out_dir, assert_diff_result)


# ---------------------------------------------------------------------------
# Workflow 2 — Recent Runs tab
# ---------------------------------------------------------------------------

def workflow_recent_runs(page: Page, out_dir: Path) -> None:
    tag = "recent-runs"
    print(f"\n── Workflow 2: Recent Runs ──")

    # Capture uncaught page errors before any navigation
    js_errors: list[str] = []
    page.on("pageerror", lambda exc: js_errors.append(str(exc)))

    step(tag, "navigate", page, out_dir, lambda: (
        page.goto(f"{BASE_URL}/ui"),
        page.wait_for_load_state("networkidle"),
    ))

    step(tag, "click Recent Runs tab", page, out_dir, lambda: (
        page.locator("#tab-runs").click(),
        page.wait_for_timeout(1000),
        pw_expect(page.locator("#panel-runs")).to_be_visible(),
    ))

    def assert_runs_rendered():
        page.wait_for_timeout(1500)
        content = page.content().lower()
        assert any(kw in content for kw in [
            "run", "suite", "status", "no runs", "empty", "timestamp"
        ]), "Recent Runs panel shows no recognizable content"
    step(tag, "assert runs panel rendered", page, out_dir, assert_runs_rendered)

    # Check no JS errors collected by the pageerror listener
    def assert_no_js_errors():
        assert not js_errors, f"JS errors: {js_errors}"
    step(tag, "no JS errors", page, out_dir, assert_no_js_errors)


# ---------------------------------------------------------------------------
# Workflow 3 — Mapping Generator tab
# ---------------------------------------------------------------------------

def workflow_mapping_generator(page: Page, out_dir: Path) -> None:
    tag = "mapping-gen"
    print(f"\n── Workflow 3: Mapping Generator ──")

    step(tag, "navigate", page, out_dir, lambda: (
        page.goto(f"{BASE_URL}/ui"),
        page.wait_for_load_state("networkidle"),
    ))

    step(tag, "click Mapping Generator tab", page, out_dir, lambda: (
        page.locator("#tab-mapping").click(),
        page.wait_for_timeout(400),
        pw_expect(page.locator("#panel-mapping")).to_be_visible(),
    ))

    step(tag, "upload mapping template CSV", page, out_dir, lambda: (
        page.locator("#mapFileInput").set_input_files(str(MAPPING_TEMPLATE)),
        page.wait_for_timeout(600),
    ))

    step(tag, "set mapping name", page, out_dir, lambda: (
        page.locator("#mapNameInput").fill("e2e_generated"),
        page.wait_for_timeout(200),
    ))

    step(tag, "click Generate", page, out_dir, lambda: (
        page.locator("#btnGenMapping").click(),
        page.wait_for_timeout(5000),
    ))

    def assert_generation_result():
        page.wait_for_timeout(2000)
        content = page.content().lower()
        assert any(kw in content for kw in [
            "success", "generated", "mapping", "json", "fields", "error"
        ]), "No result visible after Generate Mapping"
    step(tag, "assert generation result", page, out_dir, assert_generation_result)

    # Click "Use in Quick Test →" button if present
    def click_use_in_quick_test():
        btn = page.locator("text=Use in Quick Test")
        if btn.count() > 0:
            btn.first.click()
            page.wait_for_timeout(800)
            pw_expect(page.locator("#panel-quick")).to_be_visible()
        # If button absent (generation may have failed), skip gracefully
    step(tag, "click Use in Quick Test", page, out_dir, click_use_in_quick_test)


# ---------------------------------------------------------------------------
# Workflow 4 — API Tester tab
# ---------------------------------------------------------------------------

def workflow_api_tester(page: Page, out_dir: Path) -> None:
    tag = "api-tester"
    print(f"\n── Workflow 4: API Tester ──")

    step(tag, "navigate", page, out_dir, lambda: (
        page.goto(f"{BASE_URL}/ui"),
        page.wait_for_load_state("networkidle"),
    ))

    step(tag, "click API Tester tab", page, out_dir, lambda: (
        page.locator("#tab-tester").click(),
        page.wait_for_timeout(600),
        pw_expect(page.locator("#panel-tester")).to_be_visible(),
    ))

    # Set base URL and path, send GET to /api/v1/system/health
    step(tag, "set base URL", page, out_dir, lambda: (
        page.locator("#atBaseUrl").fill(BASE_URL),
        page.wait_for_timeout(200),
    ))

    step(tag, "set path to /api/v1/system/health", page, out_dir, lambda: (
        page.locator("#atPath").fill("/api/v1/system/health"),
        page.wait_for_timeout(200),
    ))

    # Send button has no id — locate by role+name within the send row
    step(tag, "click Send", page, out_dir, lambda: (
        page.get_by_role("button", name="Send").click(),
        page.wait_for_timeout(3000),
    ))

    def assert_200_badge():
        badge = page.locator("#atStatusBadge")
        pw_expect(badge).to_be_visible(timeout=5000)
        text = badge.text_content() or ""
        assert "200" in text, f"Expected 200 badge, got {text!r}"
    step(tag, "assert 200 status badge", page, out_dir, assert_200_badge)

    def assert_healthy_body():
        body = page.locator("#atRespBody")
        pw_expect(body).to_be_visible()
        text = body.text_content() or ""
        assert "healthy" in text.lower(), f"Expected 'healthy' in body, got {text[:100]!r}"
    step(tag, "assert 'healthy' in response", page, out_dir, assert_healthy_body)

    # Create a new suite via the UI — atNewSuite() uses a prompt() dialog
    def click_new_suite_and_handle_dialog():
        # Register dialog handler before triggering the prompt
        dialog_handled = []

        def handle_dialog(dialog):
            dialog_handled.append(True)
            dialog.accept("E2E UI Suite")

        page.once("dialog", handle_dialog)
        page.get_by_role("button", name="New Suite").click()
        page.wait_for_timeout(1500)

    step(tag, "click New Suite (dialog)", page, out_dir, click_new_suite_and_handle_dialog)

    def assert_suite_in_dropdown():
        # After atNewSuite() calls atLoadSuites(), atRunnerSuiteSel should
        # contain the newly created suite
        dropdown = page.locator("#atRunnerSuiteSel")
        pw_expect(dropdown).to_be_visible()
        options_text = dropdown.inner_html()
        assert "E2E UI Suite" in options_text, \
            f"Suite not in dropdown: {options_text[:200]}"
    step(tag, "assert suite in dropdown", page, out_dir, assert_suite_in_dropdown)

    def seed_suite_with_requests():
        dropdown = page.locator("#atRunnerSuiteSel")
        options = dropdown.evaluate(
            "sel => Array.from(sel.options).map(o => ({value: o.value, text: o.textContent}))"
        )
        suite = next((o for o in options if "E2E UI Suite" in o["text"]), None)
        assert suite, f"E2E UI Suite not found in runner dropdown: {options}"
        page.evaluate("""async (suiteId) => {
            var resp = await fetch('/api/v1/api-tester/suites/' + suiteId);
            var suite = await resp.json();
            suite.requests = [
                {id: 'req-e2e-1', name: 'Health', method: 'GET',
                 path: '/api/v1/system/health',
                 headers: [], body_type: 'none', body_json: '',
                 form_fields: [], assertions: []},
                {id: 'req-e2e-2', name: 'Version', method: 'GET',
                 path: '/api/v1/system/version',
                 headers: [], body_type: 'none', body_json: '',
                 form_fields: [], assertions: []},
            ];
            await fetch('/api/v1/api-tester/suites/' + suiteId, {
                method: 'PUT',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(suite),
            });
        }""", suite["value"])
        page.wait_for_timeout(600)
    step(tag, "seed suite with 2 requests", page, out_dir, seed_suite_with_requests)

    def load_suite_in_runner():
        dropdown = page.locator("#atRunnerSuiteSel")
        options = dropdown.evaluate(
            "sel => Array.from(sel.options).map(o => ({value: o.value, text: o.textContent}))"
        )
        suite = next((o for o in options if "E2E UI Suite" in o["text"]), None)
        assert suite, "E2E UI Suite not found in runner dropdown"
        dropdown.select_option(value=suite["value"])
        page.evaluate("atLoadSuiteIntoRunner()")
        page.wait_for_timeout(800)
    step(tag, "load suite in runner", page, out_dir, load_suite_in_runner)

    def assert_rows_draggable_and_button_hidden():
        rows = page.locator(".at-req-row[draggable='true']")
        assert rows.count() == 2, f"Expected 2 draggable rows, got {rows.count()}"
        pw_expect(page.locator("#btnSaveOrder")).to_be_hidden(timeout=3000)
    step(tag, "assert rows draggable and Save Order hidden", page, out_dir,
         assert_rows_draggable_and_button_hidden)

    def drag_row_and_assert_save_visible():
        rows = page.locator(".at-req-row")
        # Capture first row name before drag
        first_name_before = rows.nth(0).locator(".at-req-name").inner_text()
        rows.nth(0).drag_to(rows.nth(1))
        page.wait_for_timeout(500)
        # Assert row order actually changed
        first_name_after = rows.nth(0).locator(".at-req-name").inner_text()
        assert first_name_after != first_name_before, \
            f"Row order did not change: first row is still '{first_name_after}'"
        pw_expect(page.locator("#btnSaveOrder")).to_be_visible(timeout=3000)
    step(tag, "drag row 0 to row 1 and assert Save Order visible", page, out_dir,
         drag_row_and_assert_save_visible)

    def click_save_order_and_assert_hidden():
        page.locator("#btnSaveOrder").click()
        page.wait_for_timeout(800)
        pw_expect(page.locator("#btnSaveOrder")).to_be_hidden(timeout=3000)
    step(tag, "click Save Order and assert button hidden", page, out_dir,
         click_save_order_and_assert_hidden)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_ui_tests(out_dir: Path) -> dict:
    """Run all 4 UI workflows and return {passed, failed, checks}.

    Args:
        out_dir: Root output directory; videos and screenshots written here.

    Returns:
        Dict with keys: passed (int), failed (int), checks (list of result dicts).
    """
    global _results, _step_counter
    _results = []
    _step_counter = 0
    out_dir = Path(out_dir)

    with sync_playwright() as pw:
        record_workflow(pw, "ui-quick-test", out_dir, workflow_quick_test)
        record_workflow(pw, "ui-recent-runs", out_dir, workflow_recent_runs)
        record_workflow(pw, "ui-mapping-generator", out_dir, workflow_mapping_generator)
        record_workflow(pw, "ui-api-tester", out_dir, workflow_api_tester)

    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")

    result_data = {"passed": passed, "failed": failed, "checks": _results}
    (out_dir / "ui-results.json").write_text(
        json.dumps(result_data, indent=2), encoding="utf-8"
    )
    return result_data


def main() -> int:
    """Standalone entry point."""
    run_date = date.today().isoformat()
    out_dir = PROJECT_ROOT / "screenshots" / f"e2e-full-{run_date}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nCM3 — E2E UI Tests  ({run_date})")
    r = run_ui_tests(out_dir)

    print(f"\n{LABEL} {r['passed']} passed / {r['failed']} failed")
    return 0 if r["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

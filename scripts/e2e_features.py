"""
E2E Playwright video recordings for the three core UI features:
  1. Mapping Generation (Mapping Generator tab)
  2. File Validation   (Quick Test → Validate)
  3. File Comparison   (Quick Test → Compare)

Each feature is recorded as its own .webm video.

Usage:
    python3 scripts/e2e_features.py

Output (all under screenshots/e2e-features-<date>/):
    01-mapping-generation.webm
    02-file-validation.webm
    03-file-compare.webm
    <feature>-<step>.png  (per-step screenshots)
    e2e_features_results.json
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path

from playwright.sync_api import sync_playwright, Page, expect

import os

BASE_URL = os.getenv("VALDO_BASE_URL", "https://127.0.0.1:8000")
RUN_DATE = date.today().isoformat()

PROJECT_ROOT = Path(__file__).parent.parent
OUT_DIR = PROJECT_ROOT / "screenshots" / f"e2e-features-{RUN_DATE}"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Sample files
SAMPLES = PROJECT_ROOT / "data" / "samples"
P327_FILE = SAMPLES / "p327_sample_errors.txt"
CUSTOMERS_FILE = SAMPLES / "customers.txt"
CUSTOMERS_UPDATED = SAMPLES / "customers_updated.txt"

# Real Excel mapping template
MAPPING_EXCEL = Path("/Users/buddy/Downloads/c360-automations-main/mappings/P327_SHAW.xlsx")

all_results: list[dict] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def step(tag: str, name: str, page: Page, fn):
    label = f"{tag}-{name}"
    try:
        fn()
        page.screenshot(path=str(OUT_DIR / f"{label}.png"))
        all_results.append({"feature": tag, "step": name, "status": "PASS"})
        print(f"    ✓  {name}")
    except Exception as exc:
        page.screenshot(path=str(OUT_DIR / f"{label}_FAIL.png"))
        all_results.append({"feature": tag, "step": name, "status": "FAIL", "error": str(exc)})
        print(f"    ✗  {name}: {exc}")


def wait_for_result(page: Page, timeout: int = 15_000):
    """Wait for any result/error panel to appear after an API call."""
    page.wait_for_selector(
        ".result, .results, .error, #result, #results, #output, "
        "[class*='result'], [class*='report'], [class*='error'], "
        ".alert, .card:not(.upload-card), pre",
        timeout=timeout,
        state="visible",
    )


# ---------------------------------------------------------------------------
# Feature 1: Mapping Generation
# ---------------------------------------------------------------------------

def run_mapping_generation(page: Page) -> None:
    tag = "01-mapping"
    print("\n  [Feature 1] Mapping Generation")

    step(tag, "01-navigate", page, lambda: (
        page.set_viewport_size({"width": 1400, "height": 900}),
        page.goto(f"{BASE_URL}/ui"),
        page.wait_for_load_state("networkidle"),
        expect(page.locator("#tab-mapping")).to_be_visible(),
    ))

    step(tag, "02-open-tab", page, lambda: (
        page.locator("#tab-mapping").click(),
        page.wait_for_timeout(500),
        expect(page.locator("#panel-mapping")).to_be_visible(),
    ))

    # Upload the Excel mapping template
    def upload_excel():
        if MAPPING_EXCEL.exists():
            page.locator("#mapFileInput").set_input_files(str(MAPPING_EXCEL))
        else:
            # Fallback: create a minimal CSV template
            csv_path = OUT_DIR / "sample_mapping_template.csv"
            csv_path.write_text(
                "source_field,target_field,data_type,length,description\n"
                "CUST_ID,customer_id,NUMBER,10,Customer identifier\n"
                "CUST_NAME,customer_name,VARCHAR2,100,Customer full name\n"
                "TXN_DATE,transaction_date,DATE,,Transaction date\n",
                encoding="utf-8",
            )
            page.locator("#mapFileInput").set_input_files(str(csv_path))
        page.wait_for_timeout(600)

    step(tag, "03-upload-template", page, upload_excel)

    step(tag, "04-set-name", page, lambda: (
        page.locator("#mapNameInput").fill("p327_generated"),
        page.wait_for_timeout(200),
    ))

    step(tag, "05-set-format", page, lambda: (
        page.locator("#mapFormatSelect").select_option(label="Auto-detect"),
        page.wait_for_timeout(200),
    ))

    step(tag, "06-generate", page, lambda: (
        page.locator("#btnGenMapping").click(),
        page.wait_for_timeout(5000),
        page.screenshot(path=str(OUT_DIR / f"{tag}-06-generate-result.png"), full_page=True),
    ))

    # Show result area
    def capture_result():
        page.wait_for_timeout(2000)
        content = page.content()
        has_result = any(kw in content.lower() for kw in [
            "success", "generated", "mapping", "error", "fields", "json"
        ])
        assert has_result, "No result visible after Generate Mapping"
        page.screenshot(path=str(OUT_DIR / f"{tag}-07-result-full.png"), full_page=True)

    step(tag, "07-result", page, capture_result)

    print("    Mapping generation flow complete.")


# ---------------------------------------------------------------------------
# Feature 2: File Validation
# ---------------------------------------------------------------------------

def run_file_validation(page: Page) -> None:
    tag = "02-validate"
    print("\n  [Feature 2] File Validation")

    step(tag, "01-navigate", page, lambda: (
        page.set_viewport_size({"width": 1400, "height": 900}),
        page.goto(f"{BASE_URL}/ui"),
        page.wait_for_load_state("networkidle"),
        expect(page.locator("#tab-quick")).to_be_visible(),
    ))

    step(tag, "02-quick-test-tab", page, lambda: (
        page.locator("#tab-quick").click(),
        page.wait_for_timeout(300),
        expect(page.locator("#panel-quick")).to_be_visible(),
    ))

    # Upload sample file
    def upload_file():
        assert P327_FILE.exists(), f"Sample file not found: {P327_FILE}"
        page.locator("#fileInput").set_input_files(str(P327_FILE))
        page.wait_for_timeout(600)

    step(tag, "03-upload-file", page, upload_file)

    # Select mapping
    step(tag, "04-select-mapping", page, lambda: (
        page.locator("#mappingSelect").select_option(value="p327_universal"),
        page.wait_for_timeout(300),
        page.screenshot(path=str(OUT_DIR / f"{tag}-04-mapping-selected.png")),
    ))

    # Click Validate
    step(tag, "05-validate", page, lambda: (
        page.locator("#btnValidate").click(),
        page.wait_for_timeout(8000),
        page.screenshot(path=str(OUT_DIR / f"{tag}-05-validating.png")),
    ))

    # Wait for and capture full results
    def capture_results():
        page.wait_for_timeout(3000)
        content = page.content()
        has_result = any(kw in content.lower() for kw in [
            "valid", "error", "warning", "field", "row", "record", "pass", "fail"
        ])
        assert has_result, "No validation results visible"
        page.screenshot(path=str(OUT_DIR / f"{tag}-06-results-full.png"), full_page=True)

    step(tag, "06-results", page, capture_results)

    print("    File validation flow complete.")


# ---------------------------------------------------------------------------
# Feature 3: File Comparison
# ---------------------------------------------------------------------------

def run_file_compare(page: Page) -> None:
    tag = "03-compare"
    print("\n  [Feature 3] File Comparison")

    step(tag, "01-navigate", page, lambda: (
        page.set_viewport_size({"width": 1400, "height": 900}),
        page.goto(f"{BASE_URL}/ui"),
        page.wait_for_load_state("networkidle"),
        expect(page.locator("#tab-quick")).to_be_visible(),
    ))

    step(tag, "02-quick-test-tab", page, lambda: (
        page.locator("#tab-quick").click(),
        page.wait_for_timeout(300),
        expect(page.locator("#panel-quick")).to_be_visible(),
    ))

    # Upload file 1
    def upload_file1():
        assert CUSTOMERS_FILE.exists(), f"Sample file not found: {CUSTOMERS_FILE}"
        page.locator("#fileInput").set_input_files(str(CUSTOMERS_FILE))
        page.wait_for_timeout(600)

    step(tag, "03-upload-file1", page, upload_file1)

    # Select mapping
    step(tag, "04-select-mapping", page, lambda: (
        page.locator("#mappingSelect").select_option(value="customer_batch_universal"),
        page.wait_for_timeout(300),
    ))

    # Reveal the second file picker
    step(tag, "05-show-compare", page, lambda: (
        page.locator("#btnToggleCompare").click(),
        page.wait_for_timeout(500),
        page.screenshot(path=str(OUT_DIR / f"{tag}-05-compare-revealed.png")),
    ))

    # Upload file 2
    def upload_file2():
        assert CUSTOMERS_UPDATED.exists(), f"Sample file not found: {CUSTOMERS_UPDATED}"
        page.locator("#fileInput2").set_input_files(str(CUSTOMERS_UPDATED))
        page.wait_for_timeout(600)

    step(tag, "06-upload-file2", page, upload_file2)

    # Screenshot both files selected
    step(tag, "07-both-files", page, lambda: (
        page.screenshot(path=str(OUT_DIR / f"{tag}-07-both-files.png")),
    ))

    # Click Compare
    step(tag, "08-compare", page, lambda: (
        page.locator("#btnCompare").click(),
        page.wait_for_timeout(10000),
        page.screenshot(path=str(OUT_DIR / f"{tag}-08-comparing.png")),
    ))

    # Capture results
    def capture_results():
        page.wait_for_timeout(3000)
        content = page.content()
        has_result = any(kw in content.lower() for kw in [
            "match", "diff", "compar", "record", "row", "error", "result", "report"
        ])
        assert has_result, "No comparison results visible"
        page.screenshot(path=str(OUT_DIR / f"{tag}-09-results-full.png"), full_page=True)

    step(tag, "09-results", page, capture_results)

    print("    File compare flow complete.")


# ---------------------------------------------------------------------------
# Main: run each feature in its own context (= its own video)
# ---------------------------------------------------------------------------

def record_feature(pw, feature_name: str, video_name: str, fn) -> None:
    print(f"\n{'─'*55}")
    print(f"  Recording: {feature_name}")
    print(f"{'─'*55}")

    video_dir = OUT_DIR / "video" / video_name
    video_dir.mkdir(parents=True, exist_ok=True)

    browser = pw.chromium.launch(headless=True)
    context = browser.new_context(
        record_video_dir=str(video_dir),
        record_video_size={"width": 1400, "height": 900},
        viewport={"width": 1400, "height": 900},
        ignore_https_errors=True,  # tolerate self-signed certs in local dev
    )
    page = context.new_page()
    try:
        fn(page)
    finally:
        context.close()
        browser.close()

    # Rename video to readable name
    videos = list(video_dir.glob("*.webm"))
    if videos:
        dest = OUT_DIR / f"{video_name}.webm"
        videos[0].rename(dest)
        print(f"  Video → {dest.name}")


def main() -> int:
    print(f"\nValdo — Feature E2E Recordings  ({RUN_DATE})")
    print(f"Output directory: {OUT_DIR}\n")

    with sync_playwright() as pw:
        record_feature(pw, "Mapping Generation", "01-mapping-generation", run_mapping_generation)
        record_feature(pw, "File Validation",    "02-file-validation",    run_file_validation)
        record_feature(pw, "File Comparison",    "03-file-compare",       run_file_compare)

    # Summary
    passed = sum(1 for r in all_results if r["status"] == "PASS")
    failed = sum(1 for r in all_results if r["status"] == "FAIL")

    summary = {
        "date": RUN_DATE,
        "passed": passed,
        "failed": failed,
        "features": {
            "mapping_generation": [r for r in all_results if r["feature"] == "01-mapping"],
            "file_validation":    [r for r in all_results if r["feature"] == "02-validate"],
            "file_compare":       [r for r in all_results if r["feature"] == "03-compare"],
        },
    }
    summary_path = OUT_DIR / "e2e_features_results.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"\n{'='*55}")
    print(f"Results: {passed} PASSED  {failed} FAILED  (total {len(all_results)} steps)")
    if failed:
        print("\nFailing steps:")
        for r in all_results:
            if r["status"] == "FAIL":
                print(f"  [{r['feature']}] {r['step']}: {r.get('error','')}")
    print(f"\nSummary: {summary_path}")
    print(f"Videos:  {OUT_DIR}/*.webm\n")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

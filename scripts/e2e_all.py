"""E2E full orchestrator: runs all three components and writes summary.json.

Usage:
    python3 scripts/e2e_all.py          # run batch + api + ui
    python3 scripts/e2e_all.py --no-ui  # skip Playwright (faster)

Output:
    All output under screenshots/e2e-full-<date>/
    Terminal: colored [BATCH]/[API]/[UI] PASS/FAIL lines + final count
    Files:    batch-results.json, api-results.json, ui-results.json, summary.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import httpx  # noqa: E402  (import after sys.path fix)

BASE_URL = "http://127.0.0.1:8000"

GREEN = "\033[32m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _check_server() -> None:
    """Exit with code 2 if the server is not reachable."""
    try:
        httpx.get(f"{BASE_URL}/api/v1/system/health", timeout=5)
    except Exception as exc:
        print(
            f"\n{RED}ERROR: Cannot reach server at {BASE_URL}: {exc}{RESET}\n"
            "Start the server with:  uvicorn src.api.main:app --reload\n",
            file=sys.stderr,
        )
        sys.exit(2)


def main() -> int:
    parser = argparse.ArgumentParser(description="CM3 E2E full regression suite")
    parser.add_argument("--no-ui", action="store_true", help="Skip Playwright UI tests")
    args = parser.parse_args()

    _check_server()

    run_date = date.today().isoformat()
    out_dir = PROJECT_ROOT / "screenshots" / f"e2e-full-{run_date}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n{BOLD}CM3 Batch Automations — E2E Full Regression Suite  ({run_date}){RESET}")
    print(f"Output: {out_dir}\n")

    # ── Component 1: Batch ──────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("Component 1 / 3 — Batch (CLI suite runner)")
    print('─'*60)
    from scripts.e2e_batch import run_batch_tests  # noqa: E402
    batch = run_batch_tests(out_dir)

    # ── Component 2: API ────────────────────────────────────────────────────
    print(f"\n{'─'*60}")
    print("Component 2 / 3 — API (httpx direct)")
    print('─'*60)
    from scripts.e2e_api import run_api_tests  # noqa: E402
    api = run_api_tests(out_dir)

    # ── Component 3: UI ─────────────────────────────────────────────────────
    ui: dict = {"passed": 0, "failed": 0, "checks": []}
    if not args.no_ui:
        print(f"\n{'─'*60}")
        print("Component 3 / 3 — UI (Playwright)")
        print('─'*60)
        from scripts.e2e_full_ui import run_ui_tests  # noqa: E402
        ui = run_ui_tests(out_dir)
    else:
        print("\n[UI] Skipped (--no-ui)")

    # ── Summary ─────────────────────────────────────────────────────────────
    total_passed = batch["passed"] + api["passed"] + ui["passed"]
    total_failed = batch["failed"] + api["failed"] + ui["failed"]
    exit_code = 0 if total_failed == 0 else 1

    summary = {
        "date": datetime.utcnow().isoformat() + "Z",
        "total_passed": total_passed,
        "total_failed": total_failed,
        "exit_code": exit_code,
        "components": {
            "batch": {"passed": batch["passed"], "failed": batch["failed"]},
            "api":   {"passed": api["passed"],   "failed": api["failed"]},
            "ui":    {"passed": ui["passed"],     "failed": ui["failed"]},
        },
    }
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    color = GREEN if exit_code == 0 else RED
    print(f"\n{'─'*60}")
    print(f"{BOLD}{color}TOTAL   {total_passed} passed / {total_failed} failed"
          f"   (exit {exit_code}){RESET}")
    print(f"{'─'*60}")
    print(f"  Batch: {batch['passed']} passed / {batch['failed']} failed")
    print(f"  API:   {api['passed']} passed / {api['failed']} failed")
    print(f"  UI:    {ui['passed']} passed / {ui['failed']} failed")
    print(f"\nSummary: {summary_path}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())

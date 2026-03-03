"""E2E batch component: runs config/test_suites/e2e_full.yaml via the
run_suite_from_path service and prints [BATCH] PASS/FAIL per test.

Usage:
    python3 scripts/e2e_batch.py

Output:
    Terminal: [BATCH] <name>  PASS|FAIL per test
    File:     screenshots/e2e-full-<date>/batch-results.json
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.commands.run_tests_command import run_suite_from_path  # noqa: E402

BASE_URL = "http://127.0.0.1:8000"
RUN_DATE = date.today().isoformat()
SUITE_PATH = str(PROJECT_ROOT / "config" / "test_suites" / "e2e_full.yaml")

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
RESET = "\033[0m"
LABEL = "[BATCH]"


def run_batch_tests(out_dir: Path) -> dict:
    """Run e2e_full.yaml suite and return {passed, failed, tests}.

    Args:
        out_dir: Output directory; batch HTML reports go to out_dir/batch-reports/.

    Returns:
        Dict with keys: passed (int), failed (int), tests (list of result dicts).
    """
    batch_reports_dir = out_dir / "batch-reports"
    batch_reports_dir.mkdir(parents=True, exist_ok=True)

    results = run_suite_from_path(
        SUITE_PATH,
        params={},
        env="dev",
        output_dir=str(batch_reports_dir),
    )

    passed = 0
    failed = 0
    detail_list = []

    for r in results:
        name = r["name"]
        status = r["status"]
        ok = status in ("PASS", "SKIPPED")
        color = GREEN if ok else RED
        label_str = f"{LABEL} {name}"
        print(f"{color}{label_str:<55} {status}{RESET}")
        if ok:
            passed += 1
        else:
            failed += 1
        detail_list.append({
            "name": name,
            "status": status,
            "type": r.get("type", ""),
            "error_count": r.get("error_count", 0),
            "total_rows": r.get("total_rows", 0),
            "duration_seconds": r.get("duration_seconds", 0),
        })

    result_data = {"passed": passed, "failed": failed, "tests": detail_list}
    results_path = out_dir / "batch-results.json"
    results_path.write_text(json.dumps(result_data, indent=2), encoding="utf-8")
    return result_data


def main() -> int:
    """Standalone entry point."""
    out_dir = PROJECT_ROOT / "screenshots" / f"e2e-full-{RUN_DATE}"
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nCM3 — E2E Batch Tests  ({RUN_DATE})")
    r = run_batch_tests(out_dir)

    print(f"\n{LABEL} {r['passed']} passed / {r['failed']} failed")
    return 0 if r["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

"""E2E API component: tests all 27 HTTP endpoints via httpx.

Grouped by router. Each check prints [API] PASS / FAIL inline.

Usage:
    python3 scripts/e2e_api.py

Output:
    Terminal: [API]  <label>  PASS|FAIL per check
    File:     screenshots/e2e-full-<date>/api-results.json

Prerequisites: Server running at http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import httpx

PROJECT_ROOT = Path(__file__).parent.parent
BASE = "http://127.0.0.1:8000"

SAMPLES = PROJECT_ROOT / "data" / "samples"
CUSTOMERS_FILE = SAMPLES / "customers.txt"
CUSTOMERS_UPDATED = SAMPLES / "customers_updated.txt"
TRANSACTIONS_FILE = SAMPLES / "transactions.txt"
P327_FILE = SAMPLES / "p327_sample_errors.txt"
MAPPING_TEMPLATE = PROJECT_ROOT / "config" / "templates" / "csv" / "mapping_template.standard.csv"
RULES_TEMPLATE = PROJECT_ROOT / "config" / "templates" / "csv" / "business_rules_template.standard.csv"

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"
LABEL = "[API] "

_results: list[dict] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def check(label: str, ok: bool, detail: str = "") -> None:
    """Record and print one check result."""
    color = GREEN if ok else RED
    status = "PASS" if ok else "FAIL"
    print(f"{color}{LABEL} {label:<48} {status}{RESET}")
    if not ok and detail:
        print(f"       {detail}")
    _results.append({"label": label, "status": status, "detail": detail})


def get(path: str, **kwargs) -> httpx.Response:
    return httpx.get(f"{BASE}{path}", timeout=15, **kwargs)


def post(path: str, **kwargs) -> httpx.Response:
    return httpx.post(f"{BASE}{path}", timeout=30, **kwargs)


def put(path: str, **kwargs) -> httpx.Response:
    return httpx.put(f"{BASE}{path}", timeout=15, **kwargs)


def delete(path: str, **kwargs) -> httpx.Response:
    return httpx.delete(f"{BASE}{path}", timeout=15, **kwargs)


def expect(label: str, resp: httpx.Response, status: int,
           body_checks: dict[str, Any] | None = None) -> Any:
    """Run status + optional body checks; return parsed JSON or None."""
    ok = resp.status_code == status
    detail = "" if ok else f"expected {status}, got {resp.status_code}: {resp.text[:200]}"
    check(label, ok, detail)
    if not ok:
        return None
    try:
        data = resp.json()
    except Exception:
        return None
    if body_checks:
        for key, val in body_checks.items():
            found = data.get(key) if isinstance(data, dict) else None
            if val is None:
                check(f"  {label} → {key} present", key in (data or {}))
            elif callable(val):
                check(f"  {label} → {key} check", val(found), f"got {found!r}")
            else:
                check(f"  {label} → {key}=={val!r}", found == val, f"got {found!r}")
    return data


# ---------------------------------------------------------------------------
# Group 1: Root & Static  (3 endpoints)
# ---------------------------------------------------------------------------

def test_root_and_static() -> None:
    print("\n── Root & Static ──")

    r = httpx.get(BASE + "/", timeout=10, follow_redirects=True)
    check("GET /", r.status_code == 200)

    r = httpx.get(BASE + "/ui", timeout=10, follow_redirects=True)
    check("GET /ui", r.status_code == 200)

    r = httpx.get(BASE + "/docs", timeout=10, follow_redirects=True)
    check("GET /docs", r.status_code == 200)


# ---------------------------------------------------------------------------
# Group 2: System  (2 endpoints)
# ---------------------------------------------------------------------------

def test_system() -> None:
    print("\n── System ──")

    r = get("/api/v1/system/health")
    expect("GET /api/v1/system/health", r, 200,
           {"status": "healthy"})

    r = get("/api/v1/system/info")
    data = expect("GET /api/v1/system/info", r, 200)
    if data:
        check("  /system/info → has python_version", "python_version" in data)


# ---------------------------------------------------------------------------
# Group 3: Mappings  (5 endpoints)
# ---------------------------------------------------------------------------

def test_mappings() -> None:
    print("\n── Mappings ──")

    # Upload CSV mapping template
    mapping_id = None
    with open(MAPPING_TEMPLATE, "rb") as fh:
        r = post("/api/v1/mappings/upload",
                 files={"file": ("e2e_test_mapping.csv", fh, "text/csv")},
                 params={"mapping_name": "e2e_test_mapping"})
    data = expect("POST /api/v1/mappings/upload", r, 200)
    if data:
        mapping_id = data.get("mapping_id") or data.get("id")
        check("  /upload → mapping_id present", bool(mapping_id), f"got {data!r}")

    # List mappings
    r = get("/api/v1/mappings/")
    data = expect("GET /api/v1/mappings/", r, 200)
    if data is not None:
        check("  /mappings/ → returns list", isinstance(data, list))

    # Get specific mapping
    if mapping_id:
        r = get(f"/api/v1/mappings/{mapping_id}")
        expect(f"GET /api/v1/mappings/{{id}}", r, 200)

    # Validate mapping schema — endpoint requires a full MappingCreate body
    r = post("/api/v1/mappings/validate",
             json={
                 "mapping_name": "e2e_validate_test",
                 "source": {"type": "file", "format": "fixed_width"},
                 "fields": [
                     {"name": "customer_id", "data_type": "string",
                      "description": "Customer ID"}
                 ],
             })
    check("POST /api/v1/mappings/validate", r.status_code == 200,
          f"got {r.status_code}: {r.text[:200]}")

    # Delete uploaded test mapping
    if mapping_id:
        r = delete(f"/api/v1/mappings/{mapping_id}")
        check(f"DELETE /api/v1/mappings/{{id}}",
              r.status_code in (200, 204),
              f"got {r.status_code}")


# ---------------------------------------------------------------------------
# Group 4: Files  (6 endpoints, multiple scenarios)
# ---------------------------------------------------------------------------

def test_files() -> None:
    print("\n── Files ──")

    # Detect format
    with open(CUSTOMERS_FILE, "rb") as fh:
        r = post("/api/v1/files/detect",
                 files={"file": ("customers.txt", fh, "text/plain")})
    expect("POST /api/v1/files/detect", r, 200)

    # Parse
    with open(CUSTOMERS_FILE, "rb") as fh:
        r = post("/api/v1/files/parse",
                 files={"file": ("customers.txt", fh, "text/plain")},
                 data={"mapping_id": "customer_mapping"})
    data = expect("POST /api/v1/files/parse", r, 200)
    if data:
        records = (data.get("records") or data.get("rows")
                   or data.get("preview") or [])
        count = (data.get("rows_parsed") or data.get("row_count")
                 or len(records) if isinstance(records, list) else 0)
        check("  /parse → records > 0", count > 0, f"rows_parsed={count}")

    # Validate customers → valid: true
    with open(CUSTOMERS_FILE, "rb") as fh:
        r = post("/api/v1/files/validate",
                 files={"file": ("customers.txt", fh, "text/plain")},
                 data={"mapping_id": "customer_mapping"})
    data = expect("POST /api/v1/files/validate (customers)", r, 200)
    if data:
        check("  validate customers → valid=true", data.get("valid") is True,
              f"valid={data.get('valid')}")
        check("  validate customers → row_count > 0",
              (data.get("total_rows") or data.get("row_count") or 0) > 0)

    # Validate transactions (mapping format mismatch by design — checks endpoint returns 200)
    with open(TRANSACTIONS_FILE, "rb") as fh:
        r = post("/api/v1/files/validate",
                 files={"file": ("transactions.txt", fh, "text/plain")},
                 data={"mapping_id": "transaction_mapping"})
    data = expect("POST /api/v1/files/validate (transactions)", r, 200)
    if data:
        check("  validate transactions → has valid field", "valid" in data,
              f"keys={list(data.keys())}")

    # Validate p327 errors → valid: false, error_count > 0
    with open(P327_FILE, "rb") as fh:
        r = post("/api/v1/files/validate",
                 files={"file": ("p327_sample_errors.txt", fh, "text/plain")},
                 data={"mapping_id": "p327_universal"})
    data = expect("POST /api/v1/files/validate (p327 errors)", r, 200)
    if data:
        check("  validate p327 → valid=false", data.get("valid") is False,
              f"valid={data.get('valid')}")
        ec = (data.get("error_count") or data.get("errors_found")
              or data.get("invalid_rows") or 0)
        check("  validate p327 → errors > 0", ec > 0, f"invalid_rows={ec}")

    # Compare customers vs customers_updated → differences > 0
    with open(CUSTOMERS_FILE, "rb") as f1, open(CUSTOMERS_UPDATED, "rb") as f2:
        r = post("/api/v1/files/compare",
                 files={
                     "file1": ("customers.txt", f1, "text/plain"),
                     "file2": ("customers_updated.txt", f2, "text/plain"),
                 },
                 data={"mapping_id": "customer_mapping"})
    data = expect("POST /api/v1/files/compare", r, 200)
    if data:
        diffs = (data.get("differences") or data.get("rows_with_differences")
                 or data.get("difference_count") or 0)
        check("  compare → differences > 0", diffs > 0, f"differences={diffs}")

    # Async compare + poll to completion
    job_id = None
    with open(CUSTOMERS_FILE, "rb") as f1, open(CUSTOMERS_UPDATED, "rb") as f2:
        r = post("/api/v1/files/compare-async",
                 files={
                     "file1": ("customers.txt", f1, "text/plain"),
                     "file2": ("customers_updated.txt", f2, "text/plain"),
                 },
                 data={"mapping_id": "customer_mapping"})
    data = expect("POST /api/v1/files/compare-async", r, 200)
    if data:
        job_id = data.get("job_id") or data.get("id")
        check("  compare-async → job_id present", bool(job_id))

    if job_id:
        # Poll up to 30 s
        status = None
        for _ in range(15):
            r = get(f"/api/v1/files/compare-jobs/{job_id}")
            if r.status_code == 200:
                status = r.json().get("status")
                if status == "completed":
                    break
            time.sleep(2)
        check(f"GET /api/v1/files/compare-jobs/{{id}}", status == "completed",
              f"final status={status!r}")


# ---------------------------------------------------------------------------
# Group 5: Rules  (2 endpoints)
# ---------------------------------------------------------------------------

def test_rules() -> None:
    print("\n── Rules ──")

    rules_id = None
    with open(RULES_TEMPLATE, "rb") as fh:
        r = post("/api/v1/rules/upload",
                 files={"file": ("e2e_rules.csv", fh, "text/csv")},
                 params={"rules_name": "e2e_rules", "rules_type": "technical"})
    data = expect("POST /api/v1/rules/upload", r, 200)
    if data:
        rules_id = data.get("rules_id") or data.get("id")
        check("  /rules/upload → rules_id present", bool(rules_id), f"got {data!r}")

    if rules_id:
        r = get(f"/api/v1/rules/{rules_id}.json")
        data = expect(f"GET /api/v1/rules/{{id}}.json", r, 200)
        if data:
            check("  rules JSON → is dict", isinstance(data, dict))


# ---------------------------------------------------------------------------
# Group 6: Runs  (3 endpoints)
# ---------------------------------------------------------------------------

def test_runs() -> None:
    print("\n── Runs ──")

    run_id = None
    r = post("/api/v1/runs/trigger",
             json={"suite": "config/test_suites/e2e_full.yaml",
                   "env": "dev",
                   "output_dir": "reports"})
    data = expect("POST /api/v1/runs/trigger", r, 202)
    if data:
        run_id = data.get("run_id")
        check("  /trigger → run_id present", bool(run_id))
        check("  /trigger → status=queued", data.get("status") == "queued",
              f"status={data.get('status')!r}")

    if run_id:
        r = get(f"/api/v1/runs/{run_id}")
        data = expect(f"GET /api/v1/runs/{{run_id}}", r, 200)
        if data:
            check("  /runs/{id} → status field present", "status" in data)

    r = get("/api/v1/runs/history")
    data = expect("GET /api/v1/runs/history", r, 200)
    if data is not None:
        check("  /runs/history → returns list", isinstance(data, list))


# ---------------------------------------------------------------------------
# Group 7: API Tester  (6 endpoints)
# ---------------------------------------------------------------------------

def test_api_tester() -> None:
    print("\n── API Tester ──")

    # Proxy GET to /api/v1/system/health
    config_json = json.dumps({
        "method": "GET",
        "url": f"{BASE}/api/v1/system/health",
        "headers": [],
        "body_type": "none",
        "timeout": 10,
    })
    r = post("/api/v1/api-tester/proxy",
             data={"config": config_json})
    data = expect("POST /api/v1/api-tester/proxy", r, 200)
    if data:
        check("  proxy → status_code=200", data.get("status_code") == 200,
              f"got {data.get('status_code')!r}")
        body_str = data.get("body", "")
        check("  proxy → body contains 'healthy'", "healthy" in body_str,
              f"body={body_str[:100]!r}")

    # List suites (empty or not)
    r = get("/api/v1/api-tester/suites")
    data = expect("GET /api/v1/api-tester/suites", r, 200)
    if data is not None:
        check("  /suites → returns list", isinstance(data, list))

    # Create a suite
    suite_id = None
    r = post("/api/v1/api-tester/suites",
             json={
                 "name": "E2E Test Suite",
                 "base_url": BASE,
                 "requests": [
                     {
                         "id": "req-1",
                         "name": "Health check",
                         "method": "GET",
                         "path": "/api/v1/system/health",
                         "headers": [],
                         "body_type": "none",
                         "body_json": "",
                         "form_fields": [],
                         "assertions": [
                             {"field": "status_code",
                              "operator": "equals",
                              "expected": "200"},
                         ],
                     }
                 ],
             })
    data = expect("POST /api/v1/api-tester/suites", r, 201)
    if data:
        suite_id = data.get("id")
        check("  /suites POST → id present", bool(suite_id))
        check("  /suites POST → name correct", data.get("name") == "E2E Test Suite")

    # Get the suite
    if suite_id:
        r = get(f"/api/v1/api-tester/suites/{suite_id}")
        data = expect(f"GET /api/v1/api-tester/suites/{{id}}", r, 200)
        if data:
            check("  /suites GET → id matches", data.get("id") == suite_id)

    # Update the suite
    if suite_id:
        r = put(f"/api/v1/api-tester/suites/{suite_id}",
                json={
                    "name": "E2E Test Suite (updated)",
                    "base_url": BASE,
                    "requests": [],
                })
        data = expect(f"PUT /api/v1/api-tester/suites/{{id}}", r, 200)
        if data:
            check("  /suites PUT → name updated",
                  data.get("name") == "E2E Test Suite (updated)")

    # Delete the suite
    if suite_id:
        r = delete(f"/api/v1/api-tester/suites/{suite_id}")
        check(f"DELETE /api/v1/api-tester/suites/{{id}}",
              r.status_code == 204,
              f"got {r.status_code}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_api_tests(out_dir: Path) -> dict:
    """Run all API checks and return {passed, failed, checks}.

    Args:
        out_dir: Output directory; api-results.json written here.

    Returns:
        Dict with keys: passed (int), failed (int), checks (list of result dicts).
    """
    global _results
    _results = []

    test_root_and_static()
    test_system()
    test_mappings()
    test_files()
    test_rules()
    test_runs()
    test_api_tester()

    passed = sum(1 for r in _results if r["status"] == "PASS")
    failed = sum(1 for r in _results if r["status"] == "FAIL")

    result_data = {"passed": passed, "failed": failed, "checks": _results}
    out_dir = Path(out_dir)
    (out_dir / "api-results.json").write_text(
        json.dumps(result_data, indent=2), encoding="utf-8"
    )
    return result_data


def main() -> int:
    """Standalone entry point."""
    run_date = date.today().isoformat()
    out_dir = PROJECT_ROOT / "screenshots" / f"e2e-full-{run_date}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Quick server reachability check
    try:
        httpx.get(f"{BASE}/api/v1/system/health", timeout=5)
    except Exception as exc:
        print(f"\nERROR: Cannot reach server at {BASE}: {exc}", file=sys.stderr)
        sys.exit(2)

    print(f"\nCM3 — E2E API Tests  ({run_date})")
    r = run_api_tests(out_dir)

    print(f"\n{LABEL} {r['passed']} passed / {r['failed']} failed")
    return 0 if r["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

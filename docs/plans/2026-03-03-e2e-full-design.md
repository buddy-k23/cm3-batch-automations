# E2E Full Regression Suite — Design Document

**Date:** 2026-03-03

---

## Goal

Add a comprehensive end-to-end regression suite that tests the entire Valdo stack in one command: CLI batch workflow, all 27 HTTP API endpoints, and all 4 UI tabs with video recording.

---

## Architecture

Three components run sequentially under a single orchestrator script:

```
scripts/
  e2e_api.py        — httpx: tests all 27 HTTP endpoints directly
  e2e_full_ui.py    — Playwright: drives all 4 UI tabs with per-workflow video recording
  e2e_all.py        — orchestrator: runs all 3 components, merges summaries, exits non-zero on failure

config/test_suites/
  e2e_full.yaml     — YAML suite: 5 test cases across 4 sample files
```

All output goes to `screenshots/e2e-full-<date>/`:
- `batch-results.json`, `api-results.json`, `ui-results.json`
- `summary.json` — merged totals from all three components
- `.webm` video per UI workflow
- Terminal: colored PASS / FAIL per check, final count line, non-zero exit on any failure

**Prerequisites:** Server running at `http://127.0.0.1:8000` before scripts start.

---

## Component 1: YAML Suite + CLI Batch Workflow

File: `config/test_suites/e2e_full.yaml`

Covers 4 sample files across validate and compare actions:

| # | Name | File | Action | Expected |
|---|------|------|--------|----------|
| 1 | Customer file — all valid | `data/samples/customers.txt` | validate | `valid: true`, `min_rows: 5` |
| 2 | Customer comparison | `customers.txt` vs `customers_updated.txt` | compare | `differences_found: true` |
| 3 | Transaction file — all valid | `data/samples/transactions.txt` | validate | `valid: true`, `min_rows: 5` |
| 4 | P327 error file — expect failures | `data/samples/p327_sample_errors.txt` | validate | `valid: false`, `min_errors: 1` |
| 5 | Manifest scenario 01 — all valid | `config/mappings/manifest_scenarios/scenario_01_all_valid.json` | validate | `valid: true` |

`e2e_all.py` invokes: `valdo run-tests --suite config/test_suites/e2e_full.yaml`

Parses exit code and output to produce `batch-results.json`.

---

## Component 2: API Test Script (`e2e_api.py`)

Uses `httpx` directly. Grouped by router. Each check prints `PASS` / `FAIL` inline.

### Coverage

| Group | Endpoints | Scenarios |
|-------|-----------|-----------|
| Root & Static | `/`, `/ui`, `/docs` | 200 + correct content-type |
| System | `/api/v1/system/health`, `/system/info` | `status=healthy`, all fields present |
| Mappings | list, upload, get, validate, delete | Upload customer template → get → validate schema → delete |
| Files | detect, parse, validate, compare, compare-async, poll | Multiple files; async poll to completion |
| Rules | upload, download | Upload rules CSV → download JSON |
| Runs | trigger, poll, history | Trigger → poll → history entry |
| API Tester | proxy, suites CRUD | Proxy GET to health → create suite → get → update → delete |

### Multi-file scenarios

- Validate `customers.txt` → `valid: true`
- Validate `p327_sample_errors.txt` → `valid: false`, `error_count > 0`
- Validate `transactions.txt` → `valid: true`
- Compare `customers.txt` vs `customers_updated.txt` → `differences > 0`
- Async compare with job polling until `status = completed`

---

## Component 3: Playwright UI Script (`e2e_full_ui.py`)

Four browser contexts, one `.webm` video per workflow, screenshots per step.

### Workflow 1 — Quick Test tab
1. Load UI → Quick Test tab active by default
2. Upload `customers.txt` + `customer_mapping` → Validate → green result, row count > 0
3. Upload `p327_sample_errors.txt` + P327 mapping → Validate → error result
4. Reveal compare panel → upload `customers_updated.txt` → Compare → differences found

### Workflow 2 — Recent Runs tab
1. Click Recent Runs tab → table or empty-state renders, no JS errors
2. Assert run history column headers present

### Workflow 3 — Mapping Generator tab
1. Click Mapping Generator tab
2. Upload mapping template CSV → set name → Generate → assert success
3. Click **Use in Quick Test →** → Quick Test tab becomes active with mapping pre-selected

### Workflow 4 — API Tester tab
1. Click API Tester tab → panel loads, suite dropdowns populate
2. GET `http://127.0.0.1:8000` / `/api/v1/system/health` → Send → green `200` badge, `"healthy"` in body
3. New Suite → enter name → verify in dropdown
4. Add assertion (`status_code equals 200`) → Save → Run Suite → ✓ pass indicator

---

## Output Format

### Terminal
```
[BATCH] Customer file — all valid                    PASS
[BATCH] Customer comparison                          PASS
[BATCH] Transaction file — all valid                 PASS
[BATCH] P327 error file — expect failures            PASS
[BATCH] Manifest scenario 01 — all valid             PASS
[API]   GET /                                        PASS
[API]   GET /api/v1/system/health                    PASS
...
[UI]    Quick Test — validate customers              PASS
[UI]    Quick Test — validate P327 errors            PASS
...
─────────────────────────────────────────────────────
TOTAL   47 passed / 0 failed   (exit 0)
```

### Files
```
screenshots/e2e-full-2026-03-03/
  batch-results.json
  api-results.json
  ui-results.json
  summary.json
  ui-quick-test.webm
  ui-recent-runs.webm
  ui-mapping-generator.webm
  ui-api-tester.webm
  step-*.png
```

### `summary.json` schema
```json
{
  "date": "2026-03-03T...",
  "total_passed": 47,
  "total_failed": 0,
  "exit_code": 0,
  "components": {
    "batch": {"passed": 5, "failed": 0},
    "api":   {"passed": 30, "failed": 0},
    "ui":    {"passed": 12, "failed": 0}
  }
}
```

---

## Error Handling

- If the server is not reachable, all three components fail immediately with a clear message.
- A failed batch test, API check, or UI step does **not** stop the run — all checks execute and the full report is shown.
- Exit code is non-zero if **any** component has failures.

---

## Out of Scope

- Authenticated endpoints (no auth in scope for this project)
- Load / performance testing
- Cross-browser UI testing (Chromium only, matching existing scripts)

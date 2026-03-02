# Next Features Design — 2026-03-01

## Priority Order
1. Row Numbers in All Outputs
2. Simple Web UI
3. CI/CD Post-Batch Validation
4. API Automation Testing

---

## Feature 1 (Priority 2): Row Numbers in All CSV/HTML Outputs

### Problem
Parsers track row numbers internally but the exported CSVs and HTML reports do not
surface the original source file line number. Users cannot trace errors back to exact
lines in the source batch file.

### Design
Add a `__source_row__` column (1-indexed integer) to all parser output DataFrames.

**Parsers:** `FixedWidthParser`, `PipeDelimitedParser` append `__source_row__` before
returning the DataFrame. The column reflects the physical line number in the source file.

**Comparator:** `FileComparator` preserves `source_row` in difference dicts so HTML
comparison reports show which file line differed.

**CSV exports:** `__source_row__` appears as the first column in all downloaded CSVs.

**HTML reports:** Validation and comparison renderers display source row in error tables
and difference tables.

**Oracle guard:** `__source_row__` is stripped before any Oracle comparison or upload to
avoid schema conflicts.

### Components
- `src/parsers/fixed_width_parser.py` — add `__source_row__`
- `src/parsers/pipe_delimited_parser.py` — add `__source_row__`
- `src/comparators/file_comparator.py` — preserve in diff output
- `src/reports/renderers/validation_renderer.py` — show source row in error tables
- `src/reports/renderers/comparison_renderer.py` — show source row in diff tables
- `tests/unit/test_row_numbers.py` — verify row numbers in all outputs

---

## Feature 2 (Priority 4): Simple Web UI

### Problem
Non-technical users cannot use the CLI or interpret raw API responses. They need a
browser-based interface to quickly validate a file and check if recent batch runs passed.

### Design
A single self-contained HTML page served by FastAPI at `/ui`. No framework, no npm,
no build step. Pure HTML + vanilla JS + inline CSS.

**Two panels:**

**Quick Test panel** — drag-and-drop a file, select a mapping from a dropdown (populated
from `GET /api/v1/mappings`), click Validate or Compare. The resulting HTML report
opens in a new tab.

**Recent Runs panel** — table of the last 20 suite runs read from a lightweight
`reports/run_history.json` log (appended by `run_tests_command.py` on each run).
Shows suite name, run date, overall status badge (PASS/FAIL/PARTIAL), and a link to
the suite HTML report.

### Components
- `src/api/routers/ui.py` — serves `/ui` (static HTML) and `GET /api/v1/runs/history`
- `src/reports/static/ui.html` — single-page UI (inline CSS+JS, no CDN)
- `src/commands/run_tests_command.py` — append entry to `reports/run_history.json`
- `src/api/main.py` — mount `/ui` route and static `uploads/` for report links

---

## Feature 3 (Priority 1): CI/CD Post-Batch Validation

### Problem
The Java batch process runs nightly and generates files. There is no automated step
that validates those files against mappings and Oracle source data after the batch
completes.

### Design
**Event-driven trigger** — Java batch drops a trigger file
(`{watch_dir}/batch_complete_{YYYYMMDD}.trigger`) when it finishes. A
`cm3-batch watch` command polls the directory, matches the trigger to a suite YAML,
runs it, and removes the trigger file.

**Webhook trigger** — `POST /api/v1/runs/trigger` accepts `{suite, params, env}` JSON,
runs the suite asynchronously, returns `{run_id}`. Useful for GitLab/Azure pipelines
calling the API directly.

**Manual override** — `cm3-batch run-tests` already works for single reruns.

**Pipeline templates** — a `ci/` directory with ready-to-use templates:
- `ci/gitlab-cm3-validate.yml` — GitLab CI include template
- `ci/azure-cm3-validate.yml` — Azure DevOps pipeline task

### Components
- `src/commands/watch_command.py` — `cm3-batch watch --dir /batch/triggers --suites config/test_suites/`
- `src/api/routers/runs.py` — `POST /api/v1/runs/trigger`, `GET /api/v1/runs/{run_id}`
- `ci/gitlab-cm3-validate.yml`
- `ci/azure-cm3-validate.yml`
- `tests/unit/test_watch_command.py`

---

## Feature 4 (Priority 3): API Automation Testing

### Part A — FastAPI Regression Tests
`tests/integration/` directory with `TestClient`-based tests for all API endpoints:
- `/api/v1/files/detect`, `/parse`, `/compare`
- `/api/v1/mappings/`
- `/api/v1/system/health`

Uses sample files from `data/samples/`. Runs in GitHub Actions CI.

### Part B — `api_check` Test Type in YAML Suites
Extend `TestSuiteConfig` with a new test type for calling external HTTP endpoints:

```yaml
- name: Batch service health check
  type: api_check
  url: "http://internal-batch-svc/health"
  method: GET
  expected_status: 200
  response_contains:
    status: "ok"
```

Supports: GET/POST, JSON request body, status code assertion, JSON path assertions,
response body contains check.

### Components
- `tests/integration/test_api_files.py`
- `tests/integration/test_api_mappings.py`
- `tests/integration/test_api_system.py`
- `src/contracts/test_suite.py` — add `api_check` type fields
- `src/commands/run_tests_command.py` — handle `api_check` test execution
- `.github/workflows/ci.yml` — add integration test job

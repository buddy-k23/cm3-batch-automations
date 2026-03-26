# End-to-End Testing Guide

Covers the complete Sprint 2 feature surface: CLI validation, test suite orchestration,
Web UI, CI/CD trigger mechanisms, and API automation checks.

---

## Prerequisites

### 1. Python environment

```bash
cd /path/to/cm3-batch-automations
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-api.txt
pip install -r requirements-dev.txt
```

### 2. Environment variables

Copy and edit the example:

```bash
cp .env.example .env
```

Minimum required for Oracle tests:

```env
ORACLE_USER=CM3INT
ORACLE_PASSWORD=<password>
ORACLE_DSN=localhost:1521/FREEPDB1
ENVIRONMENT=dev
LOG_LEVEL=INFO
FILE_RETENTION_HOURS=24
```

For non-Oracle tests the Oracle variables can be left as placeholders.

### 3. Quick sanity check

```bash
python3 -m src.main info
```

Expected: version, Python path, environment variables displayed.
Oracle connectivity line will show `connected` if the `.env` values are correct.

---

## Section 1 — CLI Validation

### 1.1 Detect file format

```bash
python3 -m src.main detect -f data/samples/p327_sample_errors.txt
python3 -m src.main detect -f data/samples/customers.txt
```

Expected: format, delimiter, and estimated column count printed.

### 1.2 Parse and inspect a file

```bash
python3 -m src.main parse \
  -f data/samples/p327_sample_errors.txt \
  -m config/mappings/p327_mapping.json
```

Expected: tabular preview of parsed rows. The first column `__source_row__`
shows the 1-based physical line number from the source file.

### 1.3 Validate a file

**Basic (no report):**

```bash
python3 -m src.main validate \
  -f data/samples/p327_sample_errors.txt \
  -m config/mappings/p327_mapping.json
```

**With HTML report:**

```bash
python3 -m src.main validate \
  -f data/samples/p327_sample_errors.txt \
  -m config/mappings/p327_mapping.json \
  -o reports/p327_validation.html
```

Open `reports/p327_validation.html` in a browser.
The **Source Row** column in the error table shows the exact line number in
`p327_sample_errors.txt` for every failed record.

### 1.4 Compare two files

```bash
python3 -m src.main compare \
  -f1 data/samples/customers.txt \
  -f2 data/samples/customers_updated.txt \
  -k NAME \
  -m config/mappings/customer_mapping.json \
  -o reports/customers_diff.html
```

Open `reports/customers_diff.html`.
The difference table shows `source_row_file1` and `source_row_file2` columns
so you can trace every discrepancy back to its exact line in each file.

---

## Section 2 — Test Suite Orchestration

### 2.1 Inspect the demo suite

```bash
cat config/test_suites/e2e_demo.yaml
```

The suite contains three test types:

| Test | Type | What it checks |
|------|------|----------------|
| P327 structural validation | `structural` | Error count ≤ 50 |
| Customer file comparison | `structural` | Exact match (0 errors) |
| API health check | `api_check` | `GET /api/v1/runs/history` returns 200 |

The `api_check` test requires the API server to be running (see Section 3).

### 2.2 Dry run (resolve config without executing)

```bash
python3 -m src.main run-tests \
  -s config/test_suites/e2e_demo.yaml \
  --dry-run
```

Expected: resolved suite config printed, no files processed.

### 2.3 Run the suite (structural tests only — no server needed)

Edit a copy of the suite to remove the `api_check` test, or run with the full
suite after starting the server in Section 3.

**Run all three tests (server must be up):**

```bash
python3 -m src.main run-tests \
  -s config/test_suites/e2e_demo.yaml \
  -o reports/
```

Expected output:

```
Running test suite: E2E Demo Suite
  [1/3] P327 structural validation ... PASS
  [2/3] Customer file comparison   ... FAIL  (threshold: max_errors=0, actual: N)
  [3/3] API health check           ... PASS
Suite status: PARTIAL
Suite report: reports/E2E_Demo_Suite_<timestamp>.html
```

A new entry is appended to `reports/run_history.json` automatically.
The HTML suite report links to individual validation/comparison reports.

### 2.4 Create a suite from Excel

Generate a blank Excel template:

```bash
python3 -m src.main convert-suite --template /tmp/my_suite_template.xlsx
```

Fill it in (one row per test), then convert:

```bash
python3 -m src.main convert-suite \
  --input /tmp/my_suite_template.xlsx \
  --output-dir config/test_suites/
```

---

## Section 3 — Web UI

### 3.1 Start the API server

```bash
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Or via Python:

```bash
python3 -m src.api.main
```

### 3.2 Open the Web UI

Navigate to: **http://localhost:8000/ui**

The UI has two tabs:

**Quick Test tab**
- Drag-and-drop a file (e.g., `data/samples/p327_sample_errors.txt`)
- Select a mapping from the dropdown (populated from `/api/v1/mappings`)
- Click **Validate** or **Compare** (Compare requires a second file)
- The HTML report opens in a new browser tab

**Recent Runs tab**
- Shows the last 20 suite runs from `reports/run_history.json`
- Columns: Suite name, Run date, Status badge (PASS / FAIL / PARTIAL), Report link
- Runs `valdo run-tests` first (Section 2.3) to populate this table

### 3.3 API docs

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 3.4 Serve static reports

Reports generated by `run-tests` are served statically at `/reports/`:

```
http://localhost:8000/reports/E2E_Demo_Suite_<timestamp>.html
```

Uploaded files are served at `/uploads/`.

---

## Section 4 — CI/CD Trigger Mechanisms

### 4.1 Trigger file watcher

The watcher polls a directory for files matching `batch_complete_YYYYMMDD.trigger`.

**Start the watcher (leave running in a separate terminal):**

```bash
python3 -m src.main watch \
  --dir /tmp/batch_triggers \
  --suites config/test_suites/ \
  --env dev \
  --output-dir reports/ \
  --interval 5
```

**Drop a trigger file to simulate a batch completion:**

```bash
mkdir -p /tmp/batch_triggers
touch /tmp/batch_triggers/batch_complete_$(date +%Y%m%d).trigger
```

Expected: the watcher detects the trigger within 5 seconds, finds a matching
suite YAML in `config/test_suites/`, runs it, removes the trigger file, and
writes the result to `reports/run_history.json`.

> **Trigger → Suite matching rule:** the watcher picks the first `.yaml` file
> found in the suites directory. To target a specific suite, rename the trigger
> file to include the suite base name (e.g., `batch_complete_e2e_demo_20260301.trigger`)
> or configure the suite directory to contain only one YAML.

### 4.2 Webhook trigger (API)

The API server must be running (Section 3.1).

**Trigger a suite run asynchronously:**

```bash
curl -s -X POST http://localhost:8000/api/v1/runs/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "suite": "config/test_suites/e2e_demo.yaml",
    "env": "dev",
    "output_dir": "reports/"
  }' | python3 -m json.tool
```

Expected response (HTTP 202):

```json
{
  "run_id": "a1b2c3d4",
  "status": "queued",
  "suite": "config/test_suites/e2e_demo.yaml"
}
```

**Poll for status:**

```bash
curl -s http://localhost:8000/api/v1/runs/a1b2c3d4 | python3 -m json.tool
```

Status transitions: `queued` → `running` → `completed` / `failed`.

### 4.3 GitLab / Azure pipeline templates

Ready-to-use pipeline templates are in the `ci/` directory:

| File | Platform |
|------|----------|
| `ci/gitlab-cm3-validate.yml` | GitLab CI (include template) |
| `ci/azure-cm3-validate.yml` | Azure DevOps pipeline task |

Follow the comments in each file to set the required variables
(`CM3_ORACLE_USER`, `CM3_ORACLE_PASSWORD`, `CM3_ORACLE_DSN`, `CM3_SUITE`).

---

## Section 5 — API Automation (`api_check`)

### 5.1 Add an `api_check` test to a suite

```yaml
# config/test_suites/my_api_suite.yaml
name: API Regression Suite
environment: dev
tests:
  - name: Health check
    type: api_check
    url: "http://localhost:8000/api/v1/system/health"
    method: GET
    expected_status: 200

  - name: Run history returns list
    type: api_check
    url: "http://localhost:8000/api/v1/runs/history"
    method: GET
    expected_status: 200
    response_contains:
      # top-level key check — value must match if provided
      # leave empty to just assert status code

  - name: POST trigger queued
    type: api_check
    url: "http://localhost:8000/api/v1/runs/trigger"
    method: POST
    body:
      suite: "config/test_suites/e2e_demo.yaml"
      env: "dev"
    expected_status: 202
    response_contains:
      status: "queued"
```

### 5.2 Run the API suite

```bash
python3 -m src.main run-tests \
  -s config/test_suites/my_api_suite.yaml \
  -o reports/
```

Each `api_check` result shows: method, URL, actual status, expected status,
PASS/FAIL, and any response body assertion failures.

---

## Section 6 — Unit and Integration Tests

### 6.1 Run all unit tests

```bash
python3 -m pytest tests/unit/ -q
```

Expected: 289+ tests passing, coverage ≥ 80%.

### 6.2 Run integration tests (no running server needed)

```bash
python3 -m pytest tests/integration/ -q
```

Uses FastAPI `TestClient` — the server does not need to be started separately.

### 6.3 Run everything

```bash
python3 -m pytest -q
```

Expected: 314+ tests (unit + integration) passing, coverage gate met.

### 6.4 Run with HTML coverage report

```bash
python3 -m pytest --cov-report=html -q
open htmlcov/index.html     # macOS; use xdg-open on Linux
```

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `api_check` test returns ERROR | Server not running | Start `uvicorn` (Section 3.1) |
| Report links return 404 | `/reports` not mounted | Ensure running via `uvicorn src.api.main:app` (mounts static dirs) |
| `ORA-01017` on extract | Wrong credentials in `.env` | Verify `ORACLE_USER` / `ORACLE_PASSWORD` / `ORACLE_DSN` |
| `__source_row__` missing in CSV | Old parser cache | Re-run with `--detailed` flag or delete `.pyc` files |
| Watcher exits immediately | `--dir` doesn't exist | Create the watch directory first: `mkdir -p /tmp/batch_triggers` |
| Coverage below 80% | New untested code | Add tests or check `setup.cfg` `[coverage:run] source` scope |

---

## Quick Reference — All CLI Commands

```bash
# Format detection
python3 -m src.main detect -f <file>

# Parse preview
python3 -m src.main parse -f <file> [-m <mapping>]

# Validate
python3 -m src.main validate -f <file> [-m <mapping>] [-o report.html]

# Compare
python3 -m src.main compare -f1 <file1> -f2 <file2> [-k <keys>] [-o report.html]

# Run test suite
python3 -m src.main run-tests -s <suite.yaml> [-o reports/] [--dry-run]

# File watcher
python3 -m src.main watch --dir <trigger_dir> --suites <suites_dir> --env dev

# Excel suite builder
python3 -m src.main convert-suite --template <out.xlsx>
python3 -m src.main convert-suite --input <in.xlsx> --output-dir config/test_suites/

# Oracle extract
python3 -m src.main extract --table SHAW_SRC_P327 -o data/oracle_p327.csv

# API server
uvicorn src.api.main:app --host 0.0.0.0 --port 8000 --reload
```

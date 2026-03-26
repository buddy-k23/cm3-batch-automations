# Valdo -- Usage and Operations Guide

Comprehensive reference for installing, configuring, and operating Valdo
across CLI, Web UI, REST API, and CI/CD environments.

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Web UI Guide](#2-web-ui-guide)
3. [CLI Reference](#3-cli-reference)
4. [API Reference](#4-api-reference)
5. [Configuration Reference](#5-configuration-reference)
6. [CI Pipeline Integration](#6-ci-pipeline-integration)
7. [Operations Guide](#7-operations-guide)
8. [Troubleshooting](#8-troubleshooting)

---

## 1. Getting Started

### Prerequisites

| Requirement | Notes |
|---|---|
| Python 3.9+ | 3.11 recommended |
| pip | Any recent version |
| Oracle database (optional) | Required only for `db-compare`, `extract`, `reconcile` commands |
| Docker (optional) | For containerised deployment |

Oracle Instant Client is **not** required -- the tool uses `oracledb` thin mode.

### Installation

#### From pip (recommended)

```bash
pip install valdo-automations
```

#### From source

```bash
git clone https://github.com/your-org/valdo-automations.git
cd valdo-automations
pip install -e .
```

#### Docker

```bash
docker build -t valdo .
docker run --rm valdo --help
```

### Quick Start

**Validate a file from the CLI:**

```bash
valdo validate \
  --file data/samples/customers.txt \
  --mapping config/mappings/customer_mapping.json \
  --output report.html
```

**Start the API server:**

```bash
valdo serve --port 8000
```

**Open the Web UI:**

Navigate to `http://localhost:8000/ui` in your browser.

---

## 2. Web UI Guide

The Web UI is served at `/ui` and provides four main tabs. It supports dark and
light themes (toggled via the theme button) and respects your system preference
on first visit. All tabs are fully keyboard-accessible.

### Quick Test Tab

![Quick Test -- dark theme](screenshots/ui-quick-test-dark.png)

![Quick Test -- light theme](screenshots/ui-quick-test-light.png)

The Quick Test tab is the primary interface for ad-hoc file validation and
comparison.

**Features:**

- **Drop zone** -- Drag and drop a batch file or click to browse. Accepted
  formats include fixed-width, pipe-delimited, CSV, and TSV.
- **Mapping selector** -- Choose from available mapping configurations. The
  dropdown is populated from `config/mappings/`.
- **Validate button** -- Runs schema and business-rule validation against the
  selected mapping. Results appear as metric cards showing total rows, valid
  rows, invalid rows, and quality score.
- **Compare button** -- Upload two files and compare them field-by-field.
  Generates an HTML diff report with match counts and field-level statistics.
- **Metric cards** -- Colour-coded summary tiles (green for pass, red for fail,
  amber for partial) displayed immediately after validation completes.
- **Report links** -- Download or view the generated HTML/JSON report directly
  from the results area.

### Recent Runs Tab

![Recent Runs -- dark theme](screenshots/ui-recent-runs-dark.png)

![Recent Runs -- light theme](screenshots/ui-recent-runs-light.png)

The Recent Runs tab shows a history of validation and comparison operations.

**Features:**

- **Status badges** -- Green (passed), red (failed), and amber (partial)
  indicators for each run.
- **Sortable columns** -- Click column headers to sort by date, status, file
  name, or error count.
- **Auto-refresh** -- The table polls for updates automatically so background
  and async jobs appear without manual reload.
- **Run detail** -- Click a row to expand the full result summary including
  per-field error breakdowns.

### Mapping Generator Tab

![Mapping Generator -- dark theme](screenshots/ui-mapping-generator-dark.png)

![Mapping Generator -- light theme](screenshots/ui-mapping-generator-light.png)

![Mapping Generator -- full page](screenshots/ui-mapping-fullpage.png)

The Mapping Generator provides a guided workflow for creating mapping JSON from
Excel or CSV templates.

**Features:**

- **Two-panel layout** -- Upload panel on the left, live JSON preview on the
  right.
- **Step indicators** -- Visual progress through upload, field detection,
  review, and save steps.
- **Format detection** -- Automatically determines the file format
  (fixed-width, pipe-delimited, CSV, TSV) from the uploaded template.
- **Field editor** -- Inline editing of field names, types, positions, and
  lengths before saving.
- **JSON preview** -- Real-time preview of the generated mapping JSON that
  updates as you edit fields.
- **Save and download** -- Save the mapping to the server or download the JSON
  file locally.

### API Tester Tab

![API Tester -- dark theme](screenshots/ui-api-tester-dark.png)

![API Tester -- light theme](screenshots/ui-api-tester-light.png)

The API Tester provides an interactive REST client for testing CM3 endpoints
without leaving the browser.

**Features:**

- **Method selector** -- Choose GET, POST, PUT, or DELETE from a dropdown.
- **URL and header builder** -- Pre-populated with the current server URL.
  Add custom headers including `X-API-Key`.
- **Request body editor** -- JSON editor with syntax highlighting for POST/PUT
  payloads. Supports file upload fields.
- **Response viewer** -- Formatted JSON response with status code, headers, and
  timing information.
- **Suite runner** -- Execute a saved suite of API requests in sequence and
  view pass/fail assertions for each.

### Theme Toggle and Accessibility

- **Theme toggle** -- A button in the top navigation switches between dark and
  light themes. The preference is persisted in `localStorage`.
- **System preference** -- On first visit, the UI respects your operating
  system's dark/light preference via `prefers-color-scheme`.
- **Keyboard navigation** -- All interactive elements are reachable via Tab.
  Form controls and buttons have visible focus indicators.

### Mobile Responsive Layout

![Mobile responsive view](screenshots/ui-mobile-responsive.png)

The UI adapts to smaller viewports. Tab navigation collapses into a scrollable
bar and panels stack vertically on narrow screens.

---

## 3. CLI Reference

All commands are invoked through the `valdo` entry point.

```bash
valdo --version
valdo --help
```

### validate

Validate a file against a mapping schema and optional business rules.

```bash
valdo validate \
  --file data/batch/customers.txt \
  --mapping config/mappings/customer_mapping.json \
  --rules config/rules/customer_rules.json \
  --output report.html \
  --detailed \
  --progress
```

| Option | Default | Description |
|---|---|---|
| `--file, -f` | (required) | File to validate |
| `--mapping, -m` | | Mapping JSON for schema validation |
| `--rules, -r` | | Business rules JSON |
| `--output, -o` | | Output report file (`.json` or `.html`) |
| `--detailed / --basic` | `--detailed` | Include field-level analysis |
| `--use-chunked` | off | Enable chunked processing for large files |
| `--chunk-size` | 100000 | Rows per chunk |
| `--workers` | 1 | Parallel worker processes for chunked mode |
| `--progress / --no-progress` | `--progress` | Show progress bar |
| `--strict-fixed-width / --no-strict-fixed-width` | off | Strict fixed-width field checks |
| `--strict-level` | `format` | Strict depth: `basic`, `format`, or `all` |

### compare

Compare two files and generate a diff report.

```bash
valdo compare \
  --file1 data/expected/customers.txt \
  --file2 data/actual/customers.txt \
  --mapping config/mappings/customer_mapping.json \
  --keys "CUST_ID,ACCOUNT_NUM" \
  --output comparison.html
```

| Option | Default | Description |
|---|---|---|
| `--file1, -f1` | (required) | First file (expected) |
| `--file2, -f2` | (required) | Second file (actual) |
| `--keys, -k` | | Comma-separated key columns for row matching |
| `--mapping, -m` | | Mapping JSON (recommended for fixed-width) |
| `--output, -o` | | Output HTML report file |
| `--thresholds, -t` | | Threshold configuration file |
| `--detailed / --basic` | `--detailed` | Field-level analysis |
| `--chunk-size` | 100000 | Rows per chunk |
| `--use-chunked` | off | Chunked processing |
| `--progress / --no-progress` | `--progress` | Show progress bar |

### db-compare

Extract data from Oracle and compare against a batch file.

```bash
valdo db-compare \
  --query-or-table "SELECT * FROM SHAW_SRC_P327" \
  --mapping config/mappings/p327_mapping.json \
  --actual-file data/batch/p327_output.txt \
  --key-columns "ACCOUNT_NUM" \
  --output-format json \
  --output db_compare_report.json
```

| Option | Default | Description |
|---|---|---|
| `--query-or-table, -q` | (required) | SQL SELECT or table name |
| `--mapping, -m` | (required) | Mapping JSON config |
| `--actual-file, -f` | (required) | Batch file to compare against |
| `--key-columns, -k` | | Comma-separated key columns |
| `--output-format` | `json` | Output format: `json` or `html` |
| `--output, -o` | | File path for the report |

### parse

Parse a file and display or export its contents.

```bash
valdo parse \
  --file data/batch/customers.txt \
  --mapping config/mappings/customer_mapping.json \
  --output parsed_output.csv
```

| Option | Default | Description |
|---|---|---|
| `--file, -f` | (required) | File to parse |
| `--mapping, -m` | | Mapping configuration file |
| `--format, -t` | auto-detect | File format override |
| `--output, -o` | stdout | Output file |
| `--use-chunked` | off | Chunked processing |
| `--chunk-size` | 100000 | Rows per chunk |

### detect

Auto-detect file format.

```bash
valdo detect --file data/batch/unknown_file.dat
```

### run-tests

Run a complete test suite defined in YAML.

```bash
valdo run-tests \
  --suite config/suites/example_daily.yaml \
  --params "run_date=2026-03-25" \
  --env prod \
  --output-dir reports/daily
```

| Option | Default | Description |
|---|---|---|
| `--suite, -s` | (required) | Path to suite YAML file |
| `--params, -p` | | Key=value parameters (comma-separated) |
| `--env` | `dev` | Environment name |
| `--output-dir, -o` | `reports` | Report output directory |
| `--dry-run` | off | Print resolved config without running |

### schedule

Manage scheduled validation suites.

```bash
# List configured suites
valdo schedule list

# Run a suite immediately
valdo schedule run --suite-name daily-validation
```

### serve

Start the FastAPI server.

```bash
valdo serve --host 0.0.0.0 --port 8000
```

| Option | Default | Description |
|---|---|---|
| `--host` | `0.0.0.0` | Bind address |
| `--port` | `8000` | Listen port |

### submit-task

Submit a canonical task request from the CLI.

```bash
valdo submit-task \
  --intent validate \
  --payload '{"file": "data/batch/customers.txt", "mapping": "customer_mapping"}' \
  --priority normal \
  --machine-errors
```

| Option | Default | Description |
|---|---|---|
| `--intent` | (required) | Task intent (validate, compare) |
| `--payload` | (required) | JSON payload string |
| `--task-id` | auto-generated | Task ID override |
| `--trace-id` | auto-generated | Trace ID override |
| `--idempotency-key` | | Deduplication key |
| `--priority` | `normal` | Task priority |
| `--deadline` | now | ISO timestamp deadline |
| `--machine-errors` | off | Emit JSON error output |

### Other Commands

| Command | Description |
|---|---|
| `convert-mappings` | Bulk convert CSV/Excel templates to mapping JSON |
| `convert-rules` | Convert Excel/CSV rules template to JSON |
| `convert-suite` | Convert Excel test suite to YAML |
| `extract` | Extract data from Oracle to file |
| `reconcile` | Reconcile a mapping with database schema |
| `reconcile-all` | Reconcile all mappings in a directory (with optional drift detection) |
| `generate-oracle-expected` | Generate expected files from Oracle SQL |
| `run-pipeline` | Run a source-system orchestration profile |
| `gx-checkpoint1` | Run Great Expectations Checkpoint 1 |
| `watch` | Watch a directory for trigger files and run matching suites |
| `list-runs` | List archived test suite runs |
| `get-run` | Retrieve archived files for a specific run |
| `info` | Display system info and dependency check |

---

## 4. API Reference

The REST API is served at `http://localhost:8000` by default. Interactive
documentation is available at `/docs` (Swagger UI) and `/redoc` (ReDoc).

### Authentication

Most endpoints require an API key passed in the `X-API-Key` header. Configure
keys via the `API_KEYS` environment variable (see
[Environment Variables](#environment-variables)).

```bash
curl -H "X-API-Key: key-dev-abc123" http://localhost:8000/api/v1/mappings/
```

**Roles:** Keys can include a role suffix (`key:role`). The three built-in
roles are `tester`, `mapping_owner`, and `admin`, ordered by increasing
privilege. Certain endpoints require a minimum role (e.g., mapping upload
requires `mapping_owner`, metrics require `admin`).

### File Operations

#### POST /api/v1/files/validate

Validate a file against a mapping.

```bash
curl -X POST http://localhost:8000/api/v1/files/validate \
  -H "X-API-Key: key-dev-abc123" \
  -F "file=@data/batch/customers.txt" \
  -F "mapping_id=customer_mapping" \
  -F "detailed=true" \
  -F "output_html=true"
```

**Response:**

```json
{
  "valid": false,
  "total_rows": 15000,
  "valid_rows": 14980,
  "invalid_rows": 20,
  "errors": [...],
  "warnings": [...],
  "quality_score": 99.87,
  "report_url": "/uploads/validate_customers.html"
}
```

#### POST /api/v1/files/compare

Compare two files and generate a diff report.

```bash
curl -X POST http://localhost:8000/api/v1/files/compare \
  -H "X-API-Key: key-dev-abc123" \
  -F "file1=@data/expected/customers.txt" \
  -F "file2=@data/actual/customers.txt" \
  -F "mapping_id=customer_mapping" \
  -F "key_columns=CUST_ID,ACCOUNT_NUM" \
  -F "output_format=html"
```

**Response:**

```json
{
  "total_rows_file1": 15000,
  "total_rows_file2": 15000,
  "matching_rows": 14950,
  "only_in_file1": 25,
  "only_in_file2": 25,
  "differences": 50,
  "report_url": "/uploads/compare_expected_actual.html",
  "field_statistics": {...},
  "threshold_result": {"passed": true, "overall_result": "PASS"}
}
```

#### POST /api/v1/files/compare-async

Submit an asynchronous compare job. Same parameters as `/compare`.

```bash
curl -X POST http://localhost:8000/api/v1/files/compare-async \
  -H "X-API-Key: key-dev-abc123" \
  -F "file1=@expected.txt" \
  -F "file2=@actual.txt" \
  -F "mapping_id=customer_mapping"
```

**Response (202 Accepted):**

```json
{"job_id": "a1b2c3d4-...", "status": "running"}
```

#### GET /api/v1/files/compare-jobs/{job_id}

Poll for async compare job status.

```bash
curl -H "X-API-Key: key-dev-abc123" \
  http://localhost:8000/api/v1/files/compare-jobs/a1b2c3d4-...
```

#### POST /api/v1/files/db-compare

Compare Oracle data against a batch file.

```bash
curl -X POST http://localhost:8000/api/v1/files/db-compare \
  -H "X-API-Key: key-dev-abc123" \
  -F "actual_file=@data/batch/p327_output.txt" \
  -F "query_or_table=SELECT * FROM SHAW_SRC_P327" \
  -F "mapping_id=p327_mapping" \
  -F "key_columns=ACCOUNT_NUM"
```

#### POST /api/v1/files/detect

Auto-detect file format.

```bash
curl -X POST http://localhost:8000/api/v1/files/detect \
  -H "X-API-Key: key-dev-abc123" \
  -F "file=@data/batch/unknown.dat"
```

#### POST /api/v1/files/parse

Parse a file using a mapping.

```bash
curl -X POST http://localhost:8000/api/v1/files/parse \
  -H "X-API-Key: key-dev-abc123" \
  -F "file=@data/batch/customers.txt" \
  -F "mapping_id=customer_mapping" \
  -F "output_format=csv"
```

### Mappings

#### GET /api/v1/mappings/

List all available mappings.

```bash
curl -H "X-API-Key: key-dev-abc123" http://localhost:8000/api/v1/mappings/
```

#### GET /api/v1/mappings/{mapping_id}

Get a specific mapping by ID.

```bash
curl -H "X-API-Key: key-dev-abc123" \
  http://localhost:8000/api/v1/mappings/customer_mapping
```

#### POST /api/v1/mappings/upload

Upload an Excel/CSV template and convert to mapping JSON. Requires
`mapping_owner` role.

```bash
curl -X POST http://localhost:8000/api/v1/mappings/upload \
  -H "X-API-Key: key-owner-xyz789" \
  -F "file=@templates/customer_template.xlsx" \
  -F "mapping_name=customer_mapping" \
  -F "file_format=fixed_width"
```

#### POST /api/v1/mappings/validate

Validate a mapping structure without saving.

```bash
curl -X POST http://localhost:8000/api/v1/mappings/validate \
  -H "X-API-Key: key-dev-abc123" \
  -H "Content-Type: application/json" \
  -d @mapping.json
```

#### DELETE /api/v1/mappings/{mapping_id}

Delete a mapping. Requires `mapping_owner` role.

```bash
curl -X DELETE -H "X-API-Key: key-owner-xyz789" \
  http://localhost:8000/api/v1/mappings/customer_mapping
```

### Rules

#### POST /api/v1/rules/upload

Upload an Excel/CSV rules template and convert to JSON. Requires
`mapping_owner` role.

```bash
curl -X POST http://localhost:8000/api/v1/rules/upload \
  -H "X-API-Key: key-owner-xyz789" \
  -F "file=@templates/customer_rules.xlsx" \
  -F "rules_name=customer_rules" \
  -F "rules_type=ba_friendly"
```

#### GET /api/v1/rules/{rules_id}.json

Download a rules JSON file.

```bash
curl http://localhost:8000/api/v1/rules/customer_rules.json -o customer_rules.json
```

### Runs

#### POST /api/v1/runs/trigger

Trigger a test suite run asynchronously.

```bash
curl -X POST http://localhost:8000/api/v1/runs/trigger \
  -H "Content-Type: application/json" \
  -d '{
    "suite": "config/suites/example_daily.yaml",
    "params": {"run_date": "2026-03-25"},
    "env": "prod",
    "output_dir": "reports/daily"
  }'
```

**Response (202 Accepted):**

```json
{"run_id": "a1b2c3d4", "status": "queued", "message": "Suite run queued as a1b2c3d4"}
```

#### GET /api/v1/runs/{run_id}

Poll run status.

```bash
curl http://localhost:8000/api/v1/runs/a1b2c3d4
```

#### GET /api/v1/schedules

List configured validation suites.

```bash
curl http://localhost:8000/api/v1/schedules
```

#### POST /api/v1/schedules/run

Trigger a named suite immediately.

```bash
curl -X POST http://localhost:8000/api/v1/schedules/run \
  -H "Content-Type: application/json" \
  -d '{"suite_name": "daily-validation"}'
```

### Webhook (Async Validation)

#### POST /api/v1/webhook/validate

Submit an asynchronous validation job. Optionally posts results to a callback URL.

```bash
curl -X POST http://localhost:8000/api/v1/webhook/validate \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/data/batch/customers.txt",
    "mapping_id": "config/mappings/customer_mapping.json",
    "rules_id": "config/rules/customer_rules.json",
    "callback_url": "https://ci.example.com/hooks/cm3-result",
    "metadata": {"pipeline_run_id": "build-1234"}
  }'
```

**Response (202 Accepted):**

```json
{"job_id": "f47ac10b-...", "status": "queued"}
```

#### GET /api/v1/webhook/jobs/{job_id}

Poll for validation job status and result.

```bash
curl http://localhost:8000/api/v1/webhook/jobs/f47ac10b-...
```

**Response:**

```json
{
  "job_id": "f47ac10b-...",
  "status": "completed",
  "result": {"total_rows": 15000, "error_count": 0, ...},
  "metadata": {"pipeline_run_id": "build-1234"}
}
```

### System

#### GET /api/v1/system/health

Health check (no authentication required).

```bash
curl http://localhost:8000/api/v1/system/health
```

```json
{"status": "healthy", "version": "1.0.0", "timestamp": "2026-03-25T12:00:00Z"}
```

#### GET /api/v1/system/info

System information (requires API key).

```bash
curl -H "X-API-Key: key-dev-abc123" http://localhost:8000/api/v1/system/info
```

#### GET /api/v1/system/metrics

Runtime metrics snapshot (requires `admin` role).

```bash
curl -H "X-API-Key: key-admin-secret:admin" \
  http://localhost:8000/api/v1/system/metrics
```

```json
{
  "counters": {"tasks.submitted": 42, "tasks.failed": 1, "compare.async.success": 10},
  "latencies": {"compare.async": {"count": 10, "avg_ms": 1200.5, "p95_ms": 3500.0}}
}
```

#### GET /api/v1/system/slo-alerts

Evaluate SLO thresholds and return active alerts (requires `admin` role).

```bash
curl -H "X-API-Key: key-admin-secret:admin" \
  http://localhost:8000/api/v1/system/slo-alerts
```

```json
{
  "alerts": [
    {"name": "task_failure_rate", "severity": "high", "value": 0.08, "threshold": 0.05}
  ]
}
```

---

## 5. Configuration Reference

### Mapping JSON Schema

Mapping files live in `config/mappings/` and define how to parse and validate
batch files.

```json
{
  "mapping_name": "customer_mapping",
  "version": "1.0.0",
  "description": "Shaw customer batch file mapping",
  "source": {
    "format": "fixed_width",
    "encoding": "utf-8",
    "record_length": 400
  },
  "target": {
    "table_name": "C360_CUSTOMERS",
    "schema": "CM3INT"
  },
  "fields": [
    {
      "name": "CUST_ID",
      "source_start": 1,
      "source_length": 10,
      "data_type": "string",
      "nullable": false,
      "description": "Customer identifier"
    },
    {
      "name": "ACCOUNT_NUM",
      "source_start": 11,
      "source_length": 15,
      "data_type": "string",
      "nullable": false
    }
  ],
  "key_columns": ["CUST_ID"],
  "metadata": {
    "created_date": "2026-01-15",
    "author": "QA Team"
  }
}
```

Supported `format` values: `fixed_width`, `pipe_delimited`, `csv`, `tsv`.

### Rules JSON Schema

Rules files live in `config/rules/` and define business rules to apply during
validation.

```json
{
  "metadata": {
    "name": "customer_rules",
    "version": "1.0.0"
  },
  "rules": [
    {
      "name": "CUST_ID not blank",
      "type": "not_blank",
      "field": "CUST_ID",
      "enabled": true,
      "severity": "error"
    },
    {
      "name": "BALANCE is numeric",
      "type": "data_type",
      "field": "BALANCE",
      "expected_type": "numeric",
      "enabled": true,
      "severity": "warning"
    },
    {
      "name": "STATUS in allowed values",
      "type": "allowed_values",
      "field": "STATUS",
      "values": ["A", "I", "C"],
      "enabled": true
    }
  ]
}
```

### Suite YAML Format

Suite files define multi-step validation or comparison workflows.

```yaml
name: daily-validation
description: Daily validation suite for Shaw-to-C360 migration files
steps:
  - name: Validate customer file
    type: validate
    file_pattern: data/samples/customers.txt
    mapping: config/mappings/customer_mapping.json
    rules: null

  - name: Validate transaction file
    type: validate
    file_pattern: data/samples/transactions.txt
    mapping: config/mappings/transaction_mapping.json
    rules: null

thresholds:
  max_errors: 0

notifications:
  on_failure:
    - type: email
      to:
        - qa-team@example.com
        - dev-lead@example.com
    - type: teams
      url: https://outlook.office.com/webhook/YOUR-TEAMS-WEBHOOK-URL
    - type: slack
      url: https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK
  on_success:
    - type: email
      to:
        - qa-team@example.com
```

### Notification Configuration

Notifications are configured per-suite in the YAML `notifications` block.
Three channel types are supported:

| Channel | Required fields | Environment variables |
|---|---|---|
| `email` | `to` (list of addresses) | `SMTP_HOST`, `SMTP_PORT`, `SMTP_FROM`, `SMTP_USER`, `SMTP_PASSWORD` |
| `teams` | `url` (incoming webhook URL) | None |
| `slack` | `url` (incoming webhook URL) | None |

Notifications are dispatched after suite completion. Failures in notification
delivery are logged but never crash the suite runner.

### Environment Variables

Create a `.env` file by copying `.env.example`:

```bash
cp .env.example .env
```

| Variable | Default | Description |
|---|---|---|
| `ORACLE_USER` | `CM3INT` | Oracle database username |
| `ORACLE_PASSWORD` | (none) | Oracle database password |
| `ORACLE_DSN` | `localhost:1521/FREEPDB1` | Oracle Easy Connect string |
| `ORACLE_SCHEMA` | value of `ORACLE_USER` | Schema prefix for SQL references |
| `ENVIRONMENT` | `dev` | Application environment name |
| `LOG_LEVEL` | `INFO` | Logging level |
| `API_KEYS` | `key-dev-abc123` | Comma-separated API keys. Optional role suffix: `key:role` |
| `ALLOWED_ORIGINS` | `http://localhost,http://127.0.0.1` | CORS allowed origins (comma-separated) |
| `FILE_RETENTION_HOURS` | `24` | Auto-delete uploaded files older than this |
| `SMTP_HOST` | (none) | SMTP server hostname for email notifications |
| `SMTP_PORT` | `587` | SMTP server port |
| `SMTP_FROM` | (none) | Sender email address |
| `SMTP_USER` | (none) | SMTP auth username |
| `SMTP_PASSWORD` | (none) | SMTP auth password |

---

## 6. CI Pipeline Integration

Valdo provides ready-made templates for the three most common
CI platforms, plus generic approaches for any Docker-capable system.

### GitHub Actions

Use the reusable composite action at `.github/actions/cm3-validate/`:

```yaml
# .github/workflows/validate-batch.yml
name: Validate Batch Files
on: [push, pull_request]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - name: Validate customers
        uses: ./.github/actions/cm3-validate
        with:
          file: data/batch/customers.txt
          mapping: config/mappings/customer_mapping.json
          rules: config/rules/customer_rules.json
          threshold-max-errors: '50'
          threshold-max-error-pct: '5'
          output-format: json
          fail-on-threshold: 'true'

      - name: Check results
        if: always()
        run: |
          echo "Valid: ${{ steps.validate.outputs.valid }}"
          echo "Errors: ${{ steps.validate.outputs.error-count }}"
          echo "Report: ${{ steps.validate.outputs.report-path }}"
```

**Action inputs:**

| Input | Required | Default | Description |
|---|---|---|---|
| `file` | yes | | Batch file to validate |
| `mapping` | yes | | Mapping JSON file path |
| `rules` | no | | Business rules JSON file |
| `threshold-max-errors` | no | | Max absolute error count |
| `threshold-max-error-pct` | no | | Max error percentage (0-100) |
| `output-format` | no | `json` | Report format: `json` or `html` |
| `fail-on-threshold` | no | `true` | Fail the step on threshold breach |
| `python-version` | no | `3.11` | Python version |
| `cm3-version` | no | `valdo-automations` | pip specifier |

**Action outputs:** `valid`, `total-rows`, `error-count`, `report-path`.

The report is automatically uploaded as a build artifact retained for 30 days.

### Azure DevOps

Use the step template at `ci/templates/azure-cm3-validate.yml`:

```yaml
# azure-pipelines.yml
trigger:
  - main

pool:
  vmImage: ubuntu-latest

steps:
  - template: ci/templates/azure-cm3-validate.yml
    parameters:
      file: data/batch/customers.txt
      mapping: config/mappings/customer_mapping.json
      rules: config/rules/customer_rules.json
      thresholdMaxErrors: 50
      thresholdMaxErrorPct: 5
      outputFormat: json
```

The template publishes the report as a build artifact named
`cm3-validation-report`.

### GitLab CI

Include the template and extend the hidden job at
`ci/templates/gitlab-cm3-validate.yml`:

```yaml
# .gitlab-ci.yml
include:
  - project: 'your-group/valdo-automations'
    file: 'ci/templates/gitlab-cm3-validate.yml'

validate-customers:
  extends: .cm3-validate
  variables:
    CM3_FILE: data/batch/customers.txt
    CM3_MAPPING: config/mappings/customer_mapping.json
    CM3_RULES: config/rules/customer_rules.json
    CM3_THRESHOLD_MAX_ERRORS: "50"
    CM3_THRESHOLD_MAX_ERROR_PCT: "5"
```

Reports are stored as GitLab CI artifacts for 30 days.

### Docker-based (Any CI System)

For Jenkins, CircleCI, Buildkite, or any Docker-capable CI:

```bash
docker run --rm \
  -v "$(pwd)/data:/app/data" \
  -v "$(pwd)/config:/app/config" \
  valdo validate \
    --file data/batch/customers.txt \
    --mapping config/mappings/customer_mapping.json \
    --output /app/data/report.json \
    --no-progress
```

### Webhook Integration (Async)

For CI systems that support webhook callbacks, submit validation asynchronously
and receive results at a callback URL:

```bash
# Step 1: Submit the job
JOB_ID=$(curl -s -X POST http://cm3-server:8000/api/v1/webhook/validate \
  -H "Content-Type: application/json" \
  -d '{
    "file_path": "/shared/data/customers.txt",
    "mapping_id": "config/mappings/customer_mapping.json",
    "callback_url": "https://ci.example.com/hooks/cm3-result",
    "metadata": {"build_id": "'$BUILD_ID'"}
  }' | python3 -c "import sys,json; print(json.load(sys.stdin)['job_id'])")

# Step 2: Poll until complete (or rely on the callback)
curl -s http://cm3-server:8000/api/v1/webhook/jobs/$JOB_ID
```

### Direct API Integration

For pipelines with direct HTTP access to the CM3 server:

```bash
# Upload and validate directly
RESULT=$(curl -s -X POST http://cm3-server:8000/api/v1/files/validate \
  -H "X-API-Key: $CM3_API_KEY" \
  -F "file=@data/batch/customers.txt" \
  -F "mapping_id=customer_mapping")

VALID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['valid'])")
if [ "$VALID" = "False" ]; then
  echo "Validation failed"
  exit 1
fi
```

---

## 7. Operations Guide

### Docker Deployment

#### Build the image

```bash
docker build -t valdo:latest .
```

#### Run the API server

```bash
docker run -d \
  --name cm3-server \
  -p 8000:8000 \
  -e API_KEYS="production-key-1:admin,readonly-key-2:tester" \
  -e ORACLE_USER=CM3INT \
  -e ORACLE_PASSWORD=secret \
  -e ORACLE_DSN=oracle-host:1521/PROD \
  -e FILE_RETENTION_HOURS=12 \
  -v /data/uploads:/app/uploads \
  -v /data/reports:/app/reports \
  valdo:latest serve --host 0.0.0.0 --port 8000
```

#### Docker Compose

```yaml
# docker-compose.yml
version: '3.8'
services:
  cm3-api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - API_KEYS=prod-key:admin
      - ORACLE_DSN=oracle:1521/FREEPDB1
      - ORACLE_USER=CM3INT
      - ORACLE_PASSWORD=${ORACLE_PASSWORD}
      - FILE_RETENTION_HOURS=12
      - LOG_LEVEL=INFO
    volumes:
      - uploads:/app/uploads
      - reports:/app/reports
      - ./config:/app/config:ro
    command: ["serve", "--host", "0.0.0.0", "--port", "8000"]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/api/v1/system/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  uploads:
  reports:
```

### Health Monitoring

#### Health check

The `/api/v1/system/health` endpoint requires no authentication and returns a
simple status response. Use it for load-balancer health probes and container
orchestration liveness checks.

```bash
curl -sf http://localhost:8000/api/v1/system/health || echo "UNHEALTHY"
```

#### Metrics

The `/api/v1/system/metrics` endpoint (admin role) returns a snapshot of
runtime counters and latency histograms:

- **Counters:** `tasks.submitted`, `tasks.failed`, `compare.async.success`,
  `compare.async.dead_letter`
- **Latencies:** `compare.async` (count, avg_ms, p95_ms)

Scrape this endpoint periodically from Prometheus, Datadog, or any monitoring
agent:

```bash
curl -s -H "X-API-Key: admin-key:admin" \
  http://localhost:8000/api/v1/system/metrics | jq .
```

#### SLO Alerts

The `/api/v1/system/slo-alerts` endpoint evaluates built-in SLO thresholds
and returns active alerts:

| Alert | Severity | Condition |
|---|---|---|
| `task_failure_rate` | high | >5% failure rate (minimum 10 tasks) |
| `compare_async_p95_latency` | medium | p95 latency >5000 ms |

Integrate with PagerDuty, Opsgenie, or custom alerting by polling this
endpoint.

### Structured Logging

The application uses Python's `logging` module. Set `LOG_LEVEL` to control
verbosity (`DEBUG`, `INFO`, `WARNING`, `ERROR`).

Structured log events from the `src.utils.structured_logger` module emit JSON
fields including `trace_id`, `job_id`, and `status` for correlation in
centralized logging systems (ELK, Splunk, CloudWatch).

### Performance Tuning

#### Chunked Processing

For files larger than 50 MB, the API automatically enables chunked processing.
On the CLI, use the `--use-chunked` flag explicitly.

**Tune chunk size** to balance memory usage and throughput:

```bash
# Smaller chunks use less memory
valdo validate --file large_file.txt --mapping mapping.json \
  --use-chunked --chunk-size 50000

# Larger chunks reduce overhead on fast machines
valdo validate --file large_file.txt --mapping mapping.json \
  --use-chunked --chunk-size 200000
```

#### Parallel Workers

Chunked validation supports parallel worker processes:

```bash
valdo validate --file large_file.txt --mapping mapping.json \
  --use-chunked --workers 4
```

Set `--workers` to the number of available CPU cores. Using more workers than
cores provides no benefit.

#### File Retention

Uploaded and temporary files are automatically cleaned up on server startup.
Control retention with `FILE_RETENTION_HOURS` (default: 24 hours).

---

## 8. Troubleshooting

### Common Errors

| Error | Cause | Solution |
|---|---|---|
| `Missing X-API-Key` (401) | No API key in request header | Add `-H "X-API-Key: your-key"` to curl |
| `Invalid API key` (403) | Key not in `API_KEYS` env var | Check `.env` or environment config |
| `Role 'mapping_owner' required` (403) | Key has insufficient role | Use a key with the correct role suffix |
| `Mapping 'X' not found` (404) | Mapping JSON not in `config/mappings/` | Upload the mapping or check the ID |
| `Error detecting format` | File is empty, binary, or unsupported | Verify the file contains text data |

### Oracle Connection Issues

| Symptom | Solution |
|---|---|
| `ORACLE_PASSWORD is not set` | Set `ORACLE_PASSWORD` in `.env` or export it |
| `Failed to connect to Oracle` | Verify `ORACLE_DSN` is correct and the database is reachable |
| `ORA-12541: TNS:no listener` | Oracle listener is not running or DSN hostname/port is wrong |
| `ORA-01017: invalid username/password` | Check `ORACLE_USER` and `ORACLE_PASSWORD` |

Oracle Instant Client is not required. The tool uses `oracledb` thin mode. If
you see `DPI-1047` errors, ensure you are using `oracledb` >= 1.0 (thin mode,
no client libraries needed).

### File Format Detection

If format detection returns unexpected results:

1. Check the file encoding -- the tool expects UTF-8 by default.
2. Ensure the file has at least a few representative data lines.
3. For fixed-width files, verify all lines have the same length.
4. Specify the format explicitly with `--format` (CLI) or `file_format` (API)
   to bypass auto-detection.

### Large File Performance

| Symptom | Solution |
|---|---|
| Out of memory | Enable chunked processing (`--use-chunked`) and reduce `--chunk-size` |
| Slow validation | Increase `--workers` for parallel processing |
| Slow comparison | Provide `--keys` so the comparator can use indexed lookups |
| Timeout on API | Use the async endpoints (`/compare-async`, `/webhook/validate`) |
| Upload too large | Mount files directly via Docker volumes instead of uploading |

### Server Startup Issues

| Symptom | Solution |
|---|---|
| Port 8000 already in use | Change port with `--port 8001` or stop the conflicting process |
| Module not found | Run `pip install -e .` from the project root |
| Static files 404 | Ensure the `uploads/` and `reports/` directories exist |

---

*This document covers Valdo v1.0.0. For the full API schema,
visit `/docs` (Swagger UI) or `/redoc` when the server is running.*

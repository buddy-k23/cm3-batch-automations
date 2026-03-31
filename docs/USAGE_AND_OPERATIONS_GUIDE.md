# Valdo -- Usage and Operations Guide

Comprehensive reference for installing, configuring, and operating Valdo
across CLI, Web UI, REST API, and CI/CD environments.

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Key Concepts](#2-key-concepts)
3. [Web UI Guide](#3-web-ui-guide)
4. [CLI Reference](#4-cli-reference)
5. [API Reference](#5-api-reference)
6. [Configuration Reference](#6-configuration-reference)
   - [Cross-Row Validation](#cross-row-validation)
   - [Multi-Record-Type File Validation](#multi-record-type-file-validation)
7. [CI Pipeline Integration](#7-ci-pipeline-integration)
8. [Database Integration](#8-database-integration)
9. [ETL Pipeline Testing](#9-etl-pipeline-testing)
10. [Data Masking](#10-data-masking)
11. [AI Prompt Library](#11-ai-prompt-library)
12. [Operations Guide](#12-operations-guide)
13. [FAQ](#13-faq)
14. [Troubleshooting](#14-troubleshooting)

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

### Your First Validation in 5 Minutes

This walkthrough takes you from zero to a validated file. No Oracle database or
special configuration is needed.

**Step 1: Install Valdo**

```bash
git clone https://github.com/your-org/valdo-automations.git
cd valdo-automations
pip install -e .
```

After installation finishes, verify it worked:

```bash
valdo --version
```

Expected output:

```
valdo-automations, version 1.0.0
```

**Step 2: Start the server**

```bash
valdo serve
```

Expected output:

```
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
INFO:     Started server process
```

Leave this terminal running. Open a new terminal (or browser) for the next step.

**Step 3: Open the Web UI**

Navigate to `http://localhost:8000/ui` in your browser. You should see the
Quick Test tab with a file drop zone, a mapping dropdown, and Validate /
Compare buttons.

**Step 4: Upload a sample file**

A "batch file" is simply a data file -- often a text file where each line
represents one record (such as a customer, a transaction, or an account). The
file might be fixed-width (every field occupies a set number of characters),
pipe-delimited (`|` between fields), or comma-separated (CSV).

Drag and drop any batch file onto the drop zone, or click the zone to browse
for a file. If you do not have one handy, look in the `data/samples/` directory
of this repository for example files.

**Step 5: Select a mapping**

A "mapping" tells Valdo how to read the file -- which columns exist, where each
column starts and ends, and what data type it should contain. Think of it as a
blueprint for the file's layout.

Choose a mapping from the dropdown. The list is populated from the
`config/mappings/` directory. Pick the mapping that matches your file (for
example, `customer_mapping` for a customer batch file).

**Step 6: Click Validate and read the results**

Click the **Validate** button. After a moment, you will see metric cards:

- **Total Rows** -- how many data rows were in the file.
- **Valid Rows** -- how many rows passed all checks.
- **Invalid Rows** -- how many rows had at least one error.
- **Quality Score** -- the percentage of valid rows (e.g., 99.87 means 99.87%
  of rows are clean).

A green card means everything passed. Red means errors were found -- click the
report link below the cards to see exactly which rows and fields failed and why.

---

## 2. Key Concepts

If you are new to Valdo, this section explains the core ideas in plain language
before you dive into the detailed reference sections.

### What is a batch file?

A batch file is a plain-text data file where each line is one record. The three
most common formats are:

**Fixed-width** -- every field occupies a fixed number of characters. There are
no delimiters; position determines meaning.

```
CUST001   John Smith        2026-01-15ACTIVE
CUST002   Jane Doe          2025-11-03INACTIVE
CUST003   Bob Johnson       2026-03-20ACTIVE
```

In this example, the customer ID is always characters 1-10, the name is 11-28,
the date is 29-38, and the status is 39-46.

**Pipe-delimited** -- fields are separated by the `|` character.

```
CUST001|John Smith|2026-01-15|ACTIVE
CUST002|Jane Doe|2025-11-03|INACTIVE
CUST003|Bob Johnson|2026-03-20|ACTIVE
```

**CSV (comma-separated)** -- fields are separated by commas, with optional
quoting.

```
CUST001,John Smith,2026-01-15,ACTIVE
CUST002,Jane Doe,2025-11-03,INACTIVE
CUST003,"Bob Johnson",2026-03-20,ACTIVE
```

Valdo also supports **TSV** (tab-separated) files. It can auto-detect which
format a file uses, but you can specify it explicitly if auto-detection gets it
wrong.

### What is a mapping?

A mapping is a JSON file that acts as a blueprint for a batch file's structure.
It tells Valdo:

- What format the file uses (fixed-width, pipe-delimited, CSV, etc.).
- What fields (columns) exist.
- Where each field starts and how wide it is (for fixed-width files).
- What data type each field should contain (string, numeric, date, etc.).
- Which fields are required (not nullable).

For example, a mapping might say: "Column `CUST_ID` starts at position 1, is 10
characters wide, must be a string, and cannot be blank." Without a mapping,
Valdo would not know how to interpret the raw bytes of the file.

### What are business rules?

Business rules are extra validation checks that go beyond basic format and data
type. They encode domain knowledge -- things that a data analyst or business
user knows should be true about the data.

Examples of business rules:

- "The ACCOUNT_NUM field must start with the digit 1."
- "The STATUS field can only contain A, I, or C."
- "The BALANCE field must not be negative."
- "The CUST_ID field must never be blank."

Rules are defined in a separate JSON file and are optional. You can validate a
file with just a mapping (checking format and types) or add rules for deeper
business-logic checks.

### What is validation?

Validation is the process of checking every row in a batch file against a
mapping (and optionally business rules). For each row, Valdo checks:

1. **Structure** -- Does the row have the right length or number of fields?
2. **Data types** -- Does each field contain the expected type (number, date, string)?
3. **Business rules** -- Does each field satisfy any additional rules?

The result is a report listing every error found, along with summary statistics
(total rows, valid rows, invalid rows, and a quality score).

### What is comparison?

Comparison (or "diffing") takes two files and checks them row by row to find
differences. This is useful when you have an "expected" file and an "actual"
file and want to know exactly what changed.

Valdo matches rows using key columns (such as a customer ID), then compares
every field and reports mismatches, missing rows, and extra rows. The output is
an HTML or JSON report with field-level statistics showing how many values
matched and how many differed.

### What is a test suite?

A test suite is a YAML file that bundles multiple validation and comparison
steps into a single run -- like a checklist. Instead of running five separate
`valdo validate` commands, you define all five in one suite file and run them
with a single `valdo run-tests` command.

Suites also support thresholds (e.g., "fail if more than 10 errors total") and
notifications (email, Slack, or Teams alerts on failure). They are commonly used
in CI/CD pipelines to validate data files automatically on every build.

---

## 3. Web UI Guide

The Web UI is served at `/ui` and provides four main tabs. It supports dark and
light themes (toggled via the theme button) and respects your system preference
on first visit. All tabs are fully keyboard-accessible.

### Quick Test Tab

![Quick Test -- dark theme](screenshots/ui-quick-test-dark.png)

![Quick Test -- light theme](screenshots/ui-quick-test-light.png)

The Quick Test tab is the primary interface for ad-hoc file validation and
comparison.

**How to use this tab:**

1. Drag and drop your batch file onto the drop zone (or click the zone to
   browse). The filename appears once the file is accepted.
2. Select a mapping from the dropdown that matches your file. If you are
   unsure which mapping to use, check with your team lead or look at the
   mapping names in `config/mappings/` -- they are usually named after the
   data source (e.g., `customer_mapping`, `p327_mapping`).
3. (Optional) If you also want to apply business rules, select a rules file
   from the rules dropdown.
4. Click **Validate** to check the file. Wait a few seconds -- metric cards
   will appear showing total rows, valid rows, invalid rows, and a quality
   score. Green means the file is clean; red means errors were found.
5. Click the report link below the metric cards to open a detailed HTML
   report. The report shows exactly which rows failed and why.
6. To compare two files instead, upload both files using the file selectors,
   then click **Compare**. You will get a diff report showing matches,
   mismatches, and missing rows.

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
- **Redact PII checkbox** -- "Redact PII in report" toggle (checked by default).
  When enabled, field values in the generated report are scrubbed. This passes
  the `suppress_pii` parameter to the validate API. Uncheck it only when you
  need to see raw values for debugging.
- **Elapsed time tile** -- After validation completes, an "Elapsed" metric card
  shows total processing time in seconds.
- **Report links** -- Download or view the generated HTML/JSON report directly
  from the results area.

### Recent Runs Tab

![Recent Runs -- dark theme](screenshots/ui-recent-runs-dark.png)

![Recent Runs -- light theme](screenshots/ui-recent-runs-light.png)

The Recent Runs tab shows a history of validation and comparison operations.

**How to use this tab:**

1. Click the **Recent Runs** tab in the navigation bar. The table loads
   automatically with your most recent validation and comparison runs.
2. Look at the status badges to get a quick overview: green means passed, red
   means failed, amber means partial (some errors, but within threshold).
3. Click any column header (Date, Status, File Name, Error Count) to sort
   the table and find what you need.
4. Click a row to expand it and see the full result summary, including
   per-field error breakdowns. This is useful for diagnosing exactly where
   problems occurred without re-running the validation.
5. The table auto-refreshes, so if a colleague triggers a validation or an
   async job finishes in the background, it will appear without you needing
   to reload the page.

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

**How to use this tab:**

1. Click the **Mapping Generator** tab. You will see a two-panel layout: an
   upload area on the left and a JSON preview on the right.
2. Upload an Excel (.xlsx) or CSV file that describes your batch file's
   layout. This is typically a spreadsheet where each row represents a field
   and columns specify the field name, start position, length, and data
   type.
3. Valdo automatically detects the file format and extracts field
   definitions. Watch the step indicators at the top to track your progress
   through the workflow.
4. Review the detected fields in the field editor. You can rename fields,
   change data types, adjust positions, or correct lengths inline.
5. Check the live JSON preview on the right -- it updates in real time as
   you edit. This is the mapping JSON that will be generated.
6. When everything looks correct, click **Save** to store the mapping on the
   server (it goes into `config/mappings/`), or click **Download** to save
   the JSON file locally.

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

**How to use this tab:**

1. Click the **API Tester** tab. The interface resembles tools like Postman
   or Insomnia, but runs directly in your browser.
2. Select an HTTP method (GET, POST, PUT, DELETE) from the dropdown.
3. Enter the endpoint URL. It is pre-populated with the current server
   address, so you only need to type the path (e.g.,
   `/api/v1/mappings/`).
4. Add headers if needed. For authenticated endpoints, add an `X-API-Key`
   header with your API key.
5. For POST or PUT requests, type or paste the JSON request body into the
   editor. If the endpoint accepts file uploads, use the file upload field
   instead.
6. Click **Send**. The response viewer below shows the status code, response
   headers, timing, and the formatted JSON body.
7. To run a batch of requests, use the **Suite runner** section: load a
   saved suite of API requests, execute them in sequence, and review the
   pass/fail result for each.

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

## 4. CLI Reference

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
| `--multi-record` | | Path to multi-record YAML config (for files with multiple record types) |

#### Common examples

**Basic validation with an HTML report:**

```bash
valdo validate -f data/batch/customers.txt -m config/mappings/customer_mapping.json -o report.html
```

Expected output:

```
Validating customers.txt against customer_mapping...
Total rows: 15000 | Valid: 14980 | Invalid: 20 | Quality: 99.87% | Elapsed: 2.34s
Report saved to report.html
```

**Validation with business rules and JSON output (useful for CI):**

```bash
valdo validate \
  -f data/batch/customers.txt \
  -m config/mappings/customer_mapping.json \
  -r config/rules/customer_rules.json \
  -o results.json
```

**Validate a large file using chunked processing with 4 parallel workers:**

```bash
valdo validate \
  -f data/batch/large_transactions.txt \
  -m config/mappings/transaction_mapping.json \
  --use-chunked --chunk-size 50000 --workers 4 \
  -o report.html
```

**Quick validation with only basic checks (faster, less strict):**

```bash
valdo validate -f data/batch/customers.txt -m config/mappings/customer_mapping.json --strict-level basic
```

**Validate a multi-record-type file (header/detail/trailer):**

```bash
valdo validate \
  -f output/ATOCTRAN.txt \
  --multi-record config/multi-record/atoctran_config.yaml
```

See [Multi-Record-Type File Validation](#multi-record-type-file-validation)
in the Configuration Reference for the full YAML format and cross-type rule
reference.

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

#### Common examples

**Compare expected vs. actual output, matching rows by customer ID:**

```bash
valdo compare \
  -f1 data/expected/customers.txt \
  -f2 data/actual/customers.txt \
  -m config/mappings/customer_mapping.json \
  -k "CUST_ID" \
  -o comparison.html
```

Expected output:

```
Comparing expected/customers.txt vs actual/customers.txt...
Rows in file 1: 15000 | Rows in file 2: 15000
Matching: 14950 | Only in file 1: 25 | Only in file 2: 25 | Differences: 50
Report saved to comparison.html
```

**Compare using multiple key columns:**

```bash
valdo compare \
  -f1 data/expected/transactions.txt \
  -f2 data/actual/transactions.txt \
  -m config/mappings/transaction_mapping.json \
  -k "ACCOUNT_NUM,TRANS_DATE,TRANS_SEQ" \
  -o txn_diff.html
```

**Compare large files with chunked processing:**

```bash
valdo compare \
  -f1 expected_large.txt -f2 actual_large.txt \
  -m config/mappings/large_mapping.json \
  -k "ID" --use-chunked --chunk-size 100000 \
  -o diff_report.html
```

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

#### Common examples

**Start the server with default settings (port 8000, all interfaces):**

```bash
valdo serve
```

**Start on a custom port (useful if 8000 is already in use):**

```bash
valdo serve --port 9000
```

Then open `http://localhost:9000/ui` in your browser.

**Bind to localhost only (reject external connections):**

```bash
valdo serve --host 127.0.0.1 --port 8000
```

**Start with debug logging (helpful for troubleshooting):**

```bash
LOG_LEVEL=DEBUG valdo serve
```

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

### infer-mapping

Auto-generate a draft mapping configuration from a sample data file. Valdo
analyses the file to detect the format (fixed-width, pipe-delimited, CSV, TSV),
identify field boundaries, and infer data types.

```bash
valdo infer-mapping \
  --file data/samples/unknown_file.txt \
  --output config/mappings/draft_mapping.json
```

| Option | Default | Description |
|---|---|---|
| `--file, -f` | (required) | Sample data file to analyse |
| `--format, -t` | auto-detect | Override format detection: `fixed_width`, `pipe_delimited`, `csv`, `tsv` |
| `--output, -o` | stdout | Output JSON file path (prints to stdout if omitted) |
| `--sample-lines` | `100` | Number of lines to analyse for inference |

#### How It Works

1. Valdo reads the first N lines (default 100) of the sample file.
2. It detects the format automatically (or uses your `--format` override).
3. For delimited files, it splits on the delimiter and names fields `FIELD_1`,
   `FIELD_2`, etc.
4. For fixed-width files, it analyses character-position frequency to detect
   field boundaries.
5. Data types are inferred by sampling values: numeric, date, or string.
6. The output is a complete mapping JSON ready for review and editing.

#### Common examples

**Infer mapping and print to stdout (for quick inspection):**

```bash
valdo infer-mapping -f data/samples/mystery_batch.txt
```

**Infer mapping with format override and save to file:**

```bash
valdo infer-mapping \
  -f data/samples/pipe_file.txt \
  -t pipe_delimited \
  --sample-lines 200 \
  -o config/mappings/pipe_file_draft.json
```

Expected output:

```
Draft mapping written to: config/mappings/pipe_file_draft.json
Fields inferred: 14
Format detected: pipe_delimited
```

Review the draft, adjust field names and types as needed, then use it with
`valdo validate`.

---

### Other Commands

| Command | Description |
|---|---|
| `convert-mappings` | Bulk convert CSV/Excel templates to mapping JSON |
| `convert-rules` | Convert Excel/CSV rules template to JSON |
| `convert-suite` | Convert Excel test suite to YAML |
| `extract` | Extract data from Oracle to file |
| `generate-multi-record` | Interactive wizard (or non-interactive) to create multi-record YAML configs |
| `infer-mapping` | Auto-generate a draft mapping JSON from a sample data file |
| `mask` | Mask PII fields in batch files using configurable strategies |
| `reconcile` | Reconcile a mapping with database schema |
| `reconcile-all` | Reconcile all mappings in a directory (with optional drift detection) |
| `generate-oracle-expected` | Generate expected files from Oracle SQL |
| `run-pipeline` | Run a source-system orchestration profile |
| `run-etl-pipeline` | Execute ETL pipeline validation gates from a YAML config |
| `gx-checkpoint1` | Run Great Expectations Checkpoint 1 |
| `watch` | Watch a directory for trigger files and run matching suites |
| `list-runs` | List archived test suite runs |
| `get-run` | Retrieve archived files for a specific run |
| `info` | Display system info and dependency check |

---

## 5. API Reference

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
  "elapsed_seconds": 2.34,
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

## 6. Configuration Reference

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

#### Mapping CSV template columns

When creating mappings from CSV templates (via the Mapping Generator tab,
`valdo convert-mappings`, or `POST /api/v1/mappings/upload`), the expected
columns are:

| Column | Required | Description |
|---|---|---|
| `field_name` | Yes | Source field name |
| `data_type` | Yes | `String`, `Numeric`, `Date`, etc. |
| `position` | Fixed-width only | 1-indexed start column |
| `length` | Fixed-width only | Field width in characters |
| `target_name` | No | Target/destination field name |
| `required` | No | `Yes` or `No` |
| `format` | No | Format mask (e.g. `CCYYMMDD`, `9(10)V99`) |
| `default_value` | No | Default value for the field (e.g. `USD`, `00000000`). The converter also extracts defaults from the `transformation` column when it contains phrases like "Default to '100030'". |
| `transformation` | No | Free-text transformation description |
| `valid_values` | No | Pipe-separated list of allowed values (e.g. `A\|I\|C`) |
| `description` | No | Human-readable field description |

The `default_value` column sits between `format` and `transformation`. If both
an explicit `default_value` and a "Default to ..." phrase in `transformation`
are present, the explicit column value takes precedence.

**Note on fixed-width length warnings:** If any fixed-width field has a missing
`length` value, the converter emits a warning. This warning is included in the
upload API response so you can fix the template before validation.

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

#### Rules converter behaviour notes

**Descriptive text filtering:** When converting a rules CSV/Excel template, the
converter automatically skips descriptive sentences in the Expected/Values
column.  Only actual codes and values (e.g. `A`, `I`, `C`) are treated as
`valid_values`.  Sentences like "Must be one of the following codes" are
filtered out.

**Fixed-width valid values trimming:** During validation of fixed-width files,
the validator strips leading and trailing whitespace from both the field value
and each entry in `valid_values` before comparison.  This means a field value
of `"LS  "` (padded to fill its fixed-width length) correctly matches the rule
value `"LS"`.

### Cross-Row Validation

Cross-row rules validate properties that span multiple rows rather than
individual field values.  Set `"type": "cross_row"` and add a `"check"`
key that names one of the six supported check types.

The optional `when` condition is applied first (as for all rule types), so
cross-row checks operate only on the filtered subset of rows.

#### check: unique

No duplicate values in a single field.

```json
{
  "id": "CR001",
  "name": "Account number must be unique",
  "type": "cross_row",
  "check": "unique",
  "field": "LN-NUM-ERT",
  "severity": "error",
  "enabled": true
}
```

#### check: unique_composite

No duplicate combinations across a list of fields.

```json
{
  "id": "CR002",
  "name": "Account + batch item must be unique",
  "type": "cross_row",
  "check": "unique_composite",
  "fields": ["LN-NUM-ERT", "BAT-ITM-NUM-ERT"],
  "severity": "error",
  "enabled": true
}
```

#### check: consistent

All rows that share the same value in `key_field` must have the same value in
`target_field`.

```json
{
  "id": "CR003",
  "name": "Bank number must match for same account",
  "type": "cross_row",
  "check": "consistent",
  "key_field": "LN-NUM-ERT",
  "target_field": "BK-NUM-ERT",
  "severity": "error",
  "enabled": true
}
```

#### check: sequential

The `sequence_field` values within each `key_field` group must form a complete
sequence `1, 2, 3, …, N` (where `N` is the number of rows in the group).
Row order in the file does not matter — only the set of values is checked.

```json
{
  "id": "CR004",
  "name": "Batch items must be sequential per account",
  "type": "cross_row",
  "check": "sequential",
  "key_field": "LN-NUM-ERT",
  "sequence_field": "BAT-ITM-NUM-ERT",
  "severity": "error",
  "enabled": true
}
```

#### check: group_count

The actual number of rows per `key_field` group must equal the value declared
in `count_field`.  All rows in a mismatched group are flagged.

```json
{
  "id": "CR005",
  "name": "Transaction count must match actual records",
  "type": "cross_row",
  "check": "group_count",
  "key_field": "LN-NUM-ERT",
  "count_field": "TRN-CNT-ERT",
  "severity": "error",
  "enabled": true
}
```

#### check: group_sum

The sum of `sum_field` across a `key_field` group must fall within
`[min_value, max_value]`.  Either bound is optional.

```json
{
  "id": "CR006",
  "name": "Total payments per account must not exceed limit",
  "type": "cross_row",
  "check": "group_sum",
  "key_field": "LN-NUM-ERT",
  "sum_field": "OGL-PMT-AMT-LTD-ORI",
  "max_value": 999999999,
  "severity": "warning",
  "enabled": true
}
```

| Check | Required keys | Optional keys |
|---|---|---|
| `unique` | `field` | `when` |
| `unique_composite` | `fields` (list) | `when` |
| `consistent` | `key_field`, `target_field` | `when` |
| `sequential` | `key_field`, `sequence_field` | `when` |
| `group_count` | `key_field`, `count_field` | `when` |
| `group_sum` | `key_field`, `sum_field` | `min_value`, `max_value`, `when` |

Missing columns are silently skipped (a warning is emitted to the log) so that
a rules file remains forward-compatible with files that may not yet have all
fields present.

### Multi-Record-Type File Validation

#### What it solves

Some batch files (e.g. ATOCTRAN, TRANERT) contain multiple record types
interleaved in a single file.  For example, a file may have record-type codes
100/200/300, or Header/CUS/ORI/COD rows, where each type has different fields
at different positions.  Standard single-mapping validation cannot handle these
files because every line would be parsed against the wrong layout.

Multi-record validation solves this by:

1. Reading a **discriminator** value from a fixed position in each line.
2. Routing each line to the correct **per-type mapping and rules**.
3. Enforcing **cardinality constraints** (exactly one header, at least one detail, etc.).
4. Running **cross-type rules** that validate relationships between record type groups.

#### CLI usage

```bash
valdo validate \
  --file output/ATOCTRAN.txt \
  --multi-record config/multi-record/atoctran_config.yaml
```

When `--multi-record` is provided, the `--mapping` and `--rules` flags are
ignored because each record type specifies its own mapping and rules inside the
YAML config.

#### Multi-record YAML config format

```yaml
discriminator:
  field: REC_TYPE        # Logical name (used in violation messages)
  position: 1            # 1-indexed start column
  length: 3              # Characters to read

record_types:
  header:
    match: "HDR"                              # Discriminator value
    mapping: "config/mappings/hdr_mapping.json"
    rules: "config/rules/hdr_rules.json"      # Optional
    expect: exactly_one                       # exactly_one | at_least_one | any

  detail:
    match: "DTL"
    mapping: "config/mappings/dtl_mapping.json"
    rules: "config/rules/dtl_rules.json"
    expect: at_least_one

  trailer:
    match: "TRL"
    mapping: "config/mappings/trl_mapping.json"
    expect: exactly_one

cross_type_rules:
  - check: required_companion
    when_type: header
    requires_type: detail
    severity: error

  - check: header_trailer_count
    record_type: trailer
    trailer_field: RECORD_COUNT
    count_of: detail
    severity: error

default_action: warn   # warn | error | skip (for unrecognized discriminator values)
```

**Position-based matching:** Instead of `match`, use `position: first` or
`position: last` to identify header/trailer rows by their location in the file.

#### Cross-type rule reference

| Check | Description |
|---|---|
| `required_companion` | If `when_type` is present, `requires_type` must also exist |
| `header_trailer_count` | Trailer count field must equal the actual number of detail rows |
| `header_trailer_sum` | Trailer sum field must equal the sum of a detail field across all detail rows |
| `header_detail_consistent` | A header field value must match the same field in every detail row |
| `header_trailer_match` | A header field and a trailer field must have equal values |
| `type_sequence` | Record types must appear in the declared order (e.g. header before detail before trailer) |
| `expect_count` | A record type group must contain exactly N rows |

Each rule supports `severity` (`error` or `warning`) and an optional `message`
template.

#### API endpoint

**POST /api/v1/multi-record/generate** -- accepts a JSON body describing the
multi-record structure and returns a downloadable YAML config file.

```json
{
  "discriminator": {"field": "REC_TYPE", "position": 1, "length": 3},
  "record_types": {
    "header":  {"match": "HDR", "mapping": "config/mappings/hdr.json"},
    "detail":  {"match": "DTL", "mapping": "config/mappings/dtl.json"},
    "trailer": {"match": "TRL", "mapping": "config/mappings/trl.json", "expect": "exactly_one"}
  },
  "cross_type_rules": [
    {"check": "required_companion", "when_type": "header", "requires_type": "detail"},
    {"check": "header_trailer_count", "record_type": "trailer", "trailer_field": "RECORD_COUNT", "count_of": "detail"}
  ],
  "default_action": "warn"
}
```

The response is an `application/x-yaml` attachment that can be saved directly
and used with `--multi-record`.

**POST /api/v1/multi-record/detect-discriminator** — scan an uploaded batch
file to auto-detect the discriminator field position and length.

```
POST /api/v1/multi-record/detect-discriminator?max_lines=20
Content-Type: multipart/form-data
file: <batch file>
```

Response:
```json
{
  "candidates": [
    {"position": 1, "length": 3, "values": ["HDR", "DTL", "TRL"], "confidence": 0.95}
  ],
  "best": {"position": 1, "length": 3, "values": ["HDR", "DTL", "TRL"], "confidence": 0.95}
}
```

`candidates` is empty and `best` is `null` when no repeating pattern is found.

#### Multi-Record Config Wizard (Web UI)

The **Mapping Generator** tab includes a guided 5-step wizard that builds a
multi-record YAML config without writing YAML by hand.

1. **Select Record Types** — choose one or more mappings from the list.
2. **Configure Discriminator** — enter the field name, position (1-indexed),
   and length.  Click **Auto-detect** to upload a sample batch file and have
   the server suggest the most likely discriminator position automatically.
3. **Map Codes to Record Types** — for each selected mapping, enter the
   discriminator code value (e.g. `HDR`) and optional cardinality settings.
4. **Cross-Type Rules** *(optional)* — add rules that span record types such
   as `required_companion`, `header_trailer_count`, or `type_sequence`.
   Click **Skip** to go straight to Step 5.
5. **Preview & Download** — click **Generate YAML** to call
   `POST /api/v1/multi-record/generate` and display the YAML config.  Use
   **Copy YAML**, **Download YAML**, or **Validate File With This Config**
   (switches to the Quick Test tab with the config ready).

To open the wizard: switch to the **Mapping Generator** tab and click
**Start Wizard** in the Multi-Record Config Wizard section.

#### CLI config generator (`valdo generate-multi-record`)

The `generate-multi-record` command creates a multi-record YAML config file
without manually writing YAML by hand.

**Interactive mode (wizard):**

```bash
valdo generate-multi-record --output config/multi-record/my_config.yaml
```

The wizard walks you through:
1. Entering the discriminator field name, position, and length.
2. Selecting mapping JSON files for each record type.
3. Auto-matching rules files to mappings (e.g. `header_mapping.json` finds
   `header_rules.json` or `header_mapping_rules.json` automatically).
4. Optionally adding cross-type rules.

**Non-interactive mode:**

```bash
valdo generate-multi-record \
  --output config/multi-record/atoctran_config.yaml \
  --discriminator "REC_TYPE:1:3" \
  --type "header:HDR:config/mappings/hdr_mapping.json" \
  --type "detail:DTL:config/mappings/dtl_mapping.json" \
  --type "trailer:TRL:config/mappings/trl_mapping.json"
```

The `--discriminator` flag takes `FIELD:POSITION:LENGTH` format. Each `--type`
flag takes `NAME:MATCH_VALUE:MAPPING_PATH` format. Rules files are
auto-discovered from the mapping directory.

#### Generating mapping/rules CSVs from Excel specs

For batch generation of per-record-type mappings and rules from Excel
specification documents, use the AI prompt library in `prompts/`.  The prompts
guide an LLM (Copilot, Claude, ChatGPT) to produce Valdo-compatible CSV
templates from COBOL copybooks, Excel field specs, or PDF layouts.  The
generated CSVs can then be uploaded via the Mapping Generator tab or the
`POST /api/v1/mappings/upload` endpoint to produce the JSON config files
referenced in the multi-record YAML.

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
| `DB_ADAPTER` | `oracle` | Database backend: `oracle`, `postgresql`, or `sqlite` |
| `DB_HOST` | `localhost` | PostgreSQL server hostname (used when `DB_ADAPTER=postgresql`) |
| `DB_PORT` | `5432` | PostgreSQL server port (used when `DB_ADAPTER=postgresql`) |
| `DB_NAME` | `postgres` | Database name or SQLite file path (used when `DB_ADAPTER=postgresql` or `sqlite`) |
| `DB_USER` | `postgres` | PostgreSQL username (used when `DB_ADAPTER=postgresql`) |
| `DB_PASSWORD` | (none) | PostgreSQL password (used when `DB_ADAPTER=postgresql`) |
| `AUDIT_LOG_PATH` | `logs/audit.jsonl` | Path to the structured JSONL audit log file |
| `CM3_ENVIRONMENT` | `DEV` | Environment tag included in every audit event |
| `SMTP_HOST` | (none) | SMTP server hostname for email notifications |
| `SMTP_PORT` | `587` | SMTP server port |
| `SMTP_FROM` | (none) | Sender email address |
| `SMTP_USER` | (none) | SMTP auth username |
| `SMTP_PASSWORD` | (none) | SMTP auth password |

---

## 7. CI Pipeline Integration

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

Use the step template at `ci/templates/azure-valdo-validate.yml`:

```yaml
# azure-pipelines.yml
trigger:
  - main

pool:
  vmImage: ubuntu-latest

steps:
  - template: ci/templates/azure-valdo-validate.yml
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
`ci/templates/gitlab-valdo-validate.yml`:

```yaml
# .gitlab-ci.yml
include:
  - project: 'your-group/valdo-automations'
    file: 'ci/templates/gitlab-valdo-validate.yml'

validate-customers:
  extends: .valdo-validate
  variables:
    VALDO_FILE: data/batch/customers.txt
    VALDO_MAPPING: config/mappings/customer_mapping.json
    VALDO_RULES: config/rules/customer_rules.json
    VALDO_THRESHOLD_MAX_ERRORS: "50"
    VALDO_THRESHOLD_MAX_ERROR_PCT: "5"
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

## 8. Database Integration

This section covers Valdo's Oracle database features: connecting to a database,
comparing DB data against batch files, extracting tables to flat files,
reconciling mapping schemas, generating expected output files, and tracking
run history.

### 8.1 Connection Setup

Valdo connects to Oracle using the `oracledb` Python driver in **thin mode**.
Thin mode communicates directly over the network -- you do **not** need to
install Oracle Instant Client or set `ORACLE_HOME`.

#### Environment Variables

Set these variables in a `.env` file at the project root or export them in your
shell before running any database command:

| Variable | Required | Default | Description |
|---|---|---|---|
| `ORACLE_USER` | No | `CM3INT` | Database username |
| `ORACLE_PASSWORD` | **Yes** | *(none)* | Database password |
| `ORACLE_DSN` | No | `localhost:1521/FREEPDB1` | Oracle Easy Connect string (`host:port/service`) |
| `ORACLE_SCHEMA` | No | Value of `ORACLE_USER` | Schema prefix for SQL table references (e.g. `CM3INT.TABLE`) |
| `SECRETS_PROVIDER` | No | `env` | How the password is resolved: `env`, `vault`, or `azure` |

Example `.env` file:

```bash
ORACLE_USER=CM3INT
ORACLE_PASSWORD=my_secret_pw
ORACLE_DSN=db-host.example.com:1521/FREEPDB1
ORACLE_SCHEMA=CM3INT
```

#### Testing Connectivity

Run `valdo info` to verify that the `oracledb` driver is available and thin
mode is active:

```bash
valdo info
```

Expected output (when oracledb is installed):

```
Valdo v0.1.0
Python version: 3.11.x
Working directory: /path/to/project
oracledb version: 2.x.x
Oracle connectivity available (thin mode)
```

If you see "oracledb not available", install it:

```bash
pip install oracledb
```

#### Thin Mode vs Thick Mode

Valdo defaults to **thin mode**, which requires no native libraries. If you need
thick mode (e.g. for advanced Oracle features like Kerberos authentication),
install Oracle Instant Client separately and call `oracledb.init_oracle_client()`
before connecting. For most batch validation workflows, thin mode is sufficient.

#### Using a Secrets Provider for Passwords

By default, `ORACLE_PASSWORD` is read from the environment (`SECRETS_PROVIDER=env`).
For production environments, you can use HashiCorp Vault or Azure Key Vault:

```bash
export SECRETS_PROVIDER=vault
# Valdo will resolve ORACLE_PASSWORD through the Vault backend
```

Supported backends: `env` (default), `vault`, `azure`. See the
`src/utils/secrets` module for configuration details on each provider.

---

### 8.2 DB-to-File Comparison (`db-compare`)

The `db-compare` command extracts data from an Oracle table (or SQL query),
writes the result to a temporary pipe-delimited file, and compares it against
an actual batch file. This lets you verify that a batch file matches the
current state of the database.

#### How It Works

1. Valdo connects to Oracle using your environment variables.
2. It runs your SQL query (or `SELECT *` on the specified table).
3. The extracted rows are written to a temporary pipe-delimited file.
4. The standard comparison engine diffs the temp file against your actual file.
5. A unified result is returned with workflow metadata and comparison statistics.

#### CLI Usage

```bash
# Compare a table against a batch file
valdo db-compare \
  --query-or-table "SHAW_SRC_P327" \
  --mapping config/mappings/p327_mapping.json \
  --actual-file data/actual/p327_output.txt \
  --key-columns "ACCOUNT_ID,TRANS_DATE" \
  --output-format json \
  --output reports/db_compare_p327.json
```

```bash
# Compare using a SQL query instead of a bare table name
valdo db-compare \
  --query-or-table "SELECT ACCOUNT_ID, NAME, BALANCE FROM CM3INT.CUSTOMERS WHERE STATUS = 'ACTIVE'" \
  --mapping config/mappings/customer_mapping.json \
  --actual-file data/actual/customers.txt \
  --output-format html \
  --output reports/db_compare_customers.html
```

**Options reference:**

| Option | Short | Required | Description |
|---|---|---|---|
| `--query-or-table` | `-q` | Yes | A SQL SELECT statement or bare Oracle table name |
| `--mapping` | `-m` | Yes | Path to the JSON mapping config file |
| `--actual-file` | `-f` | Yes | Path to the actual batch file to compare against |
| `--key-columns` | `-k` | No | Comma-separated column names used as join keys for row matching |
| `--output-format` | | No | `json` (default) or `html` |
| `--output` | `-o` | No | File path for the written report |

#### API Usage

The same workflow is available through the REST API:

```bash
curl -X POST http://localhost:8000/api/v1/db-compare \
  -F "actual_file=@data/actual/p327_output.txt" \
  -F "query_or_table=SHAW_SRC_P327" \
  -F "mapping_id=p327_mapping" \
  -F "key_columns=ACCOUNT_ID,TRANS_DATE" \
  -F "output_format=json"
```

The `mapping_id` is the filename stem of a mapping JSON file in the configured
mappings directory (e.g. `p327_mapping` resolves to `config/mappings/p327_mapping.json`).

#### How Key Columns Work

When you provide `--key-columns`, Valdo uses those columns as join keys to match
rows between the DB extract and the actual file. This is essential when rows may
appear in a different order. Without key columns, comparison is done row-by-row
in file order.

**Tip:** Choose columns that together form a unique identifier for each row
(e.g. a composite primary key like `ACCOUNT_ID,TRANS_DATE`).

#### Reading the Comparison Report

The JSON output contains two sections:

- **`workflow`** -- metadata about the extraction step:
  - `status`: `"passed"` or `"failed"`
  - `db_rows_extracted`: number of rows pulled from Oracle
  - `query_or_table`: the query or table that was used
- **`compare`** -- the full comparison output:
  - `total_rows_file1` / `total_rows_file2`: row counts
  - `matching_rows`: rows that match exactly
  - `only_in_file1` / `only_in_file2`: rows present in only one source
  - `differences`: number of rows with field-level differences
  - `structure_compatible`: whether column structures match
  - `field_statistics`: per-field match/mismatch counts

A `workflow.status` of `"passed"` means zero differences and zero
unmatched rows.

---

### 8.3 Data Extraction (`extract`)

The `extract` command pulls data from Oracle and writes it to a flat file.
It supports three modes: table extraction, inline SQL query, and SQL file.

#### Extract a Table

```bash
valdo extract \
  --table SHAW_SRC_P327 \
  --output data/extracts/p327_full.txt
```

This runs `SELECT *` on the table and writes all rows as a pipe-delimited file.

#### Extract with a Custom SQL Query

```bash
valdo extract \
  --query "SELECT ACCOUNT_ID, NAME, BALANCE FROM CM3INT.CUSTOMERS WHERE STATUS = 'ACTIVE'" \
  --output data/extracts/active_customers.txt
```

#### Extract with a SQL File

For complex queries, save your SQL in a `.sql` file and reference it:

```bash
valdo extract \
  --sql-file config/queries/monthly_transactions.sql \
  --output data/extracts/monthly_trans.txt
```

#### Limiting Rows for Testing

When you only need a sample (e.g. during development), use `--limit`:

```bash
valdo extract \
  --table SHAW_SRC_P327 \
  --limit 100 \
  --output data/extracts/p327_sample.txt
```

**Note:** `--limit` only works with `--table` mode, not with `--query` or
`--sql-file`. For SQL-based extraction, add `FETCH FIRST N ROWS ONLY` to
your query.

#### Choosing a Delimiter

The default delimiter is pipe (`|`). Use `--delimiter` to change it:

```bash
valdo extract \
  --table SHAW_SRC_P327 \
  --delimiter "," \
  --output data/extracts/p327.csv
```

**Options reference:**

| Option | Short | Required | Description |
|---|---|---|---|
| `--table` | `-t` | One of three | Table name to extract |
| `--query` | `-q` | One of three | SQL query to execute |
| `--sql-file` | `-s` | One of three | Path to a `.sql` file |
| `--output` | `-o` | Yes | Output file path |
| `--limit` | `-l` | No | Limit rows (only for `--table`) |
| `--delimiter` | `-d` | No | Output delimiter (default: `\|`) |

You must provide exactly one of `--table`, `--query`, or `--sql-file`.

---

### 8.4 Schema Reconciliation (`reconcile`)

Schema reconciliation validates that your mapping document matches the actual
database schema. It catches configuration errors early -- before you run a
full comparison -- by checking that the tables, columns, types, and lengths
in your mapping correspond to what exists in Oracle.

#### Checks Performed

| Check | What it verifies |
|---|---|
| Table exists | The target table specified in the mapping exists in the schema |
| Columns exist | Every mapped column exists in the database table |
| Types compatible | Mapping data types are compatible with the Oracle column types |
| Length sufficient | Oracle column lengths can hold the data described by the mapping |

#### CLI Usage

```bash
valdo reconcile \
  --mapping config/mappings/p327_mapping.json \
  --output reports/reconcile_p327.json
```

Sample output:

```
Reconciling mapping: P327 Customer Mapping
Target table: SHAW_SRC_P327

Mapping is valid

Mapped columns: 12
Database columns: 15
Report written to: reports/reconcile_p327.json
```

If errors are found:

```
Mapping validation failed
  ERROR: Column ACCT_BALANCE not found in table SHAW_SRC_P327
  ERROR: Column TYPE has incompatible type: mapping says VARCHAR(10), DB has NUMBER(5)

Warnings:
  DB column CREATED_DATE is not referenced in mapping
```

Use `--fail-on-warnings` to make the command return a non-zero exit code when
warnings are found (useful in CI pipelines):

```bash
valdo reconcile \
  --mapping config/mappings/p327_mapping.json \
  --fail-on-warnings
```

#### Bulk Reconciliation with `reconcile-all`

When you have many mapping files, use `reconcile-all` to validate them all at
once:

```bash
valdo reconcile-all \
  --mappings-dir config/mappings \
  --pattern "*.json" \
  --output reports/reconcile_all.json
```

This produces an aggregate report with per-mapping results and a summary of
total errors and warnings.

#### Drift Detection with `--baseline`

Compare the current reconciliation results against a previous run to detect
schema drift:

```bash
# First, create a baseline
valdo reconcile-all \
  --mappings-dir config/mappings \
  --output reports/reconcile_baseline.json

# Later, check for drift against the baseline
valdo reconcile-all \
  --mappings-dir config/mappings \
  --baseline reports/reconcile_baseline.json \
  --fail-on-drift \
  --output reports/reconcile_current.json
```

The `--fail-on-drift` flag causes the command to return a non-zero exit code
when new errors or warnings appear that were not present in the baseline.
This is ideal for CI/CD gates where you want to catch regressions.

**`reconcile-all` options reference:**

| Option | Short | Required | Description |
|---|---|---|---|
| `--mappings-dir` | `-d` | No | Directory containing mapping files (default: `config/mappings`) |
| `--pattern` | | No | Glob pattern for mapping files (default: `*.json`) |
| `--output` | `-o` | No | Write aggregate report to this file (`.json` recommended) |
| `--baseline` | `-b` | No | Baseline JSON report to compare drift against |
| `--fail-on-warnings` | | No | Non-zero exit code if any warnings found |
| `--fail-on-drift` | | No | Non-zero exit code if new issues vs baseline |

---

### 8.5 Generate Expected Files (`generate-oracle-expected`)

This command runs SQL queries defined in a manifest file against Oracle and
writes the results to output files. Use it to generate baseline "expected"
files for comparison tests.

#### When to Use

- Creating golden files for regression tests
- Generating expected output from transformation SQL in `CM3INT`
- Automating baseline refresh as part of a CI pipeline

#### Manifest Format

Create a JSON manifest that lists each extraction job:

```json
{
  "schema": "cm3int",
  "jobs": [
    {
      "name": "SRC_A_P327",
      "query_file": "config/queries/cm3int/SRC_A_p327_transform.sql",
      "output_file": "outputs/expected/SRC_A/p327.txt",
      "delimiter": "|"
    },
    {
      "name": "SRC_B_EAC",
      "query_file": "config/queries/cm3int/SRC_B_eac_transform.sql",
      "output_file": "outputs/expected/SRC_B/eac.txt",
      "delimiter": "|"
    }
  ]
}
```

Each job requires:
- `name`: A human-readable label for the job
- `query_file`: Path to a `.sql` file containing the SELECT statement
- `output_file`: Where to write the extracted data
- `delimiter`: Column separator in the output (default: `|`)

#### CLI Usage

By default the command runs in **dry-run** mode, which validates the manifest
without executing any queries:

```bash
# Dry run (default) -- validate the manifest
valdo generate-oracle-expected \
  --manifest config/oracle_expected_manifest.json

# Execute for real
valdo generate-oracle-expected \
  --manifest config/oracle_expected_manifest.json \
  --run \
  --output reports/oracle_expected_summary.json
```

The `--output` flag writes a JSON summary of all jobs with their pass/fail
status.

---

### 8.6 Run History in Database

Valdo can automatically store test run results in Oracle tables, giving you a
persistent, queryable history of all validation and comparison runs.

#### Schema

Two tables are used (created under your configured `ORACLE_SCHEMA`):

**`CM3_RUN_HISTORY`** -- one row per test suite run:

| Column | Type | Description |
|---|---|---|
| `RUN_ID` | VARCHAR | Unique run identifier (UUID) |
| `SUITE_NAME` | VARCHAR | Name of the test suite |
| `ENVIRONMENT` | VARCHAR | Environment label (e.g. `dev`, `staging`) |
| `RUN_TIMESTAMP` | TIMESTAMP | When the run started (UTC) |
| `STATUS` | VARCHAR | Overall status (`passed`, `failed`) |
| `PASS_COUNT` | NUMBER | Number of passing tests |
| `FAIL_COUNT` | NUMBER | Number of failing tests |
| `SKIP_COUNT` | NUMBER | Number of skipped tests |
| `TOTAL_COUNT` | NUMBER | Total tests in the run |
| `REPORT_URL` | VARCHAR | URL to the HTML report |
| `ARCHIVE_PATH` | VARCHAR | Path to the archived report |

**`CM3_RUN_TESTS`** -- one row per individual test within a run:

| Column | Type | Description |
|---|---|---|
| `RUN_ID` | VARCHAR | Foreign key to `CM3_RUN_HISTORY` |
| `TEST_NAME` | VARCHAR | Name of the individual test |
| `TEST_TYPE` | VARCHAR | Type of test (validate, compare, etc.) |
| `STATUS` | VARCHAR | Test result status |
| `ROW_COUNT` | NUMBER | Rows processed |
| `ERROR_COUNT` | NUMBER | Errors found |
| `DURATION_SECS` | NUMBER | Test duration in seconds |
| `REPORT_PATH` | VARCHAR | Path to the individual test report |

#### Querying Run History via API

The REST API exposes run history for dashboards and integrations:

```bash
# Fetch the 20 most recent runs
curl http://localhost:8000/api/v1/runs/history

# Fetch with a custom limit
curl http://localhost:8000/api/v1/runs/history?limit=50
```

The response is a JSON array of run summary objects, ordered newest first.

#### Querying via CLI

Use the `list-runs` command to view recent run history from the terminal:

```bash
valdo list-runs
```

---

### 8.7 Cross-Row Validation with DB Data

When you extract data from Oracle using `extract` or `db-compare`, you can
apply cross-row business rules to the extracted data just as you would with
any batch file. Cross-row rules validate relationships *across* rows rather
than checking individual fields.

#### Supported Cross-Row Rule Types

| Rule | Description |
|---|---|
| `unique` | Values in a column must not repeat |
| `consistent` | When key columns match, dependent columns must also match |
| `sequential` | Values must follow a sequential order (e.g. line numbers) |
| `sum` | Column values must sum to an expected total |
| `referential` | Values must exist in a reference set |

#### Example: Validate Extracted Transaction Records

```bash
# Step 1: Extract transaction data
valdo extract \
  --table SHAW_SRC_ATOCTRAN \
  --output data/extracts/transactions.txt

# Step 2: Validate with cross-row rules
valdo validate \
  --file data/extracts/transactions.txt \
  --mapping config/mappings/atoctran_mapping.json \
  --rules config/rules/transaction_rules.json \
  --output reports/transaction_validation.html
```

The rules file can define cross-row checks like:

```json
{
  "cross_row_rules": [
    {
      "type": "unique",
      "columns": ["TRANSACTION_ID"],
      "description": "Transaction IDs must be unique"
    },
    {
      "type": "consistent",
      "key_columns": ["ACCOUNT_ID"],
      "check_columns": ["ACCOUNT_NAME"],
      "description": "Same account ID must always have the same name"
    }
  ]
}
```

---

### 8.8 Pluggable Database Adapters

Valdo supports multiple database backends through a pluggable adapter
architecture. Each adapter implements the same interface
(`DatabaseAdapter`), so all database commands (`db-compare`, `extract`,
`reconcile`, `reconcile-all`, `generate-oracle-expected`) work identically
regardless of which backend is active. Your mapping files, rules, and
comparison workflows remain the same.

#### Supported Adapters

| Database | `DB_ADAPTER` value | Driver | Status |
|---|---|---|---|
| Oracle | `oracle` (default) | `oracledb` (thin mode) | Supported |
| PostgreSQL | `postgresql` | `psycopg2` | Supported |
| SQLite | `sqlite` | `sqlite3` (stdlib) | Supported |

#### Selecting an Adapter

Set the `DB_ADAPTER` environment variable in your `.env` file or shell:

```bash
# Use Oracle (default -- same as omitting DB_ADAPTER)
export DB_ADAPTER=oracle

# Use PostgreSQL
export DB_ADAPTER=postgresql

# Use SQLite
export DB_ADAPTER=sqlite
```

All database commands automatically use the active adapter. No code or
command-line changes are needed.

#### PostgreSQL Setup

Set these environment variables when using `DB_ADAPTER=postgresql`:

```bash
DB_ADAPTER=postgresql
DB_HOST=pg-server.example.com    # default: localhost
DB_PORT=5432                     # default: 5432
DB_NAME=valdo_db                 # default: postgres
DB_USER=valdo_user               # default: postgres
DB_PASSWORD=secret
```

Install the driver:

```bash
pip install psycopg2-binary
```

Then run database commands as usual:

```bash
valdo db-compare \
  --query-or-table "SELECT * FROM customers" \
  --mapping config/mappings/customer_mapping.json \
  --actual-file data/actual/customers.txt
```

#### SQLite Setup

SQLite requires no external driver (uses Python's built-in `sqlite3`).
Set these environment variables:

```bash
DB_ADAPTER=sqlite
DB_NAME=path/to/local.db    # default: :memory: (in-memory database)
```

Using `:memory:` creates an ephemeral in-memory database, ideal for unit
tests and CI pipelines that need a database backend without infrastructure.

```bash
# Example: extract from a SQLite database
DB_ADAPTER=sqlite DB_NAME=test.db valdo extract \
  --query "SELECT * FROM test_table" \
  --output data/extracts/test_output.txt
```

---

## 9. ETL Pipeline Testing

The `run-etl-pipeline` command executes multi-gate ETL validation pipelines
defined in YAML. It is designed for CI/CD integration -- the command exits
non-zero when any blocking gate fails, so pipelines can gate deployments on
data quality.

### CLI Usage

```bash
valdo run-etl-pipeline \
  --config config/pipelines/example_etl.yaml \
  --run-date 20260326 \
  --output reports/pipeline_run.json
```

| Option | Default | Description |
|---|---|---|
| `--config` | (required) | Path to the pipeline YAML configuration file |
| `--run-date` | (none) | Run date string injected as `{run_date}` in templates |
| `--params` | `{}` | JSON object of extra template parameters |
| `--output, -o` | (none) | Optional file path for the JSON result report |

### Pipeline YAML Config Format

A pipeline config defines **sources** (the data feeds to validate) and
**gates** (ordered validation stages). Each gate contains one or more steps
and can iterate over all sources with `for_each: source`.

```yaml
name: nightly-etl-pipeline
description: Validate ETL outputs for the nightly batch run.

sources:
  - name: customers
    mapping: config/mappings/customer_mapping.json
    rules: config/rules/customer_rules.json
    input_path: data/incoming/customers_{run_date}.txt
    output_pattern: output/customers_*.txt

  - name: accounts
    mapping: config/mappings/account_mapping.json
    rules: config/rules/account_rules.json
    input_path: data/incoming/accounts_{run_date}.txt
    output_pattern: output/accounts_*.txt

gates:
  - name: input_validation
    stage: gate1
    description: Validate raw source files before staging
    for_each: source
    blocking: true
    steps:
      - type: validate
        file: "{source.input_path}"
        mapping: "{source.mapping}"
        rules: "{source.rules}"
        thresholds:
          max_error_pct: 5.0
          min_rows: 1

  - name: output_validation
    stage: gate3
    description: Validate transformed output files
    for_each: source
    blocking: true
    steps:
      - type: validate
        file: "{source.output_pattern}"
        mapping: "{source.mapping}"
        thresholds:
          max_error_pct: 0.0

  - name: post_load_check
    stage: gate5
    description: Verify loaded records (non-blocking)
    blocking: false
    steps:
      - type: db_compare
        query: "SELECT * FROM loaded_data WHERE batch_date = '{run_date}'"
        file: output/final_{run_date}.txt
        mapping: config/mappings/customer_mapping.json
        key_columns:
          - ACCT-KEY
```

### Template Variable Expansion

Template variables use `{...}` syntax and are expanded before each step
executes:

| Variable | Source | Example value |
|---|---|---|
| `{source.name}` | Current source in `for_each` loop | `customers` |
| `{source.mapping}` | Source mapping path | `config/mappings/customer_mapping.json` |
| `{source.input_path}` | Source input file path | `data/incoming/customers_20260326.txt` |
| `{source.output_pattern}` | Glob for output files | `output/customers_*.txt` |
| `{source.rules}` | Source rules path | `config/rules/customer_rules.json` |
| `{run_date}` | Value of `--run-date` flag | `20260326` |

Extra parameters passed via `--params '{"env": "staging"}'` are also available
as `{env}`.

### Blocking vs Non-Blocking Gates

- **`blocking: true`** (default) -- if the gate fails, the pipeline stops
  immediately and reports overall status as `failed`. Use for critical quality
  gates (e.g. input validation, output validation).
- **`blocking: false`** -- if the gate fails, the failure is recorded but
  execution continues. Use for informational checks (e.g. post-load
  verification) where you want visibility without halting the pipeline.

### CI Integration Example (Azure DevOps)

```yaml
# azure-pipelines.yml
trigger:
  - main

pool:
  vmImage: ubuntu-latest

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: '3.11'

  - script: pip install valdo-automations
    displayName: Install Valdo

  - script: |
      valdo run-etl-pipeline \
        --config config/pipelines/nightly_etl.yaml \
        --run-date $(Build.BuildNumber) \
        --output $(Build.ArtifactStagingDirectory)/pipeline_report.json
    displayName: Run ETL validation gates

  - publish: $(Build.ArtifactStagingDirectory)/pipeline_report.json
    artifact: etl-validation-report
    condition: always()
```

The command exits non-zero on failure, so the Azure DevOps pipeline step
will fail automatically if any blocking gate does not pass.

---

## 10. Data Masking

The `valdo mask` command replaces sensitive (PII) field values in batch files
with safe alternatives, producing dev/test-safe copies of production data.

### CLI Usage

```bash
valdo mask \
  --file data/production/customers.txt \
  --mapping config/mappings/customer_mapping.json \
  --rules config/masking/customer_masking.json \
  --output data/dev/customers_masked.txt
```

| Option | Default | Description |
|---|---|---|
| `--file, -f` | (required) | Input batch file to mask |
| `--mapping, -m` | (required) | Mapping JSON file (defines field positions) |
| `--rules, -r` | (none) | Masking rules JSON (strategy per field). If omitted, all fields are preserved |
| `--output, -o` | (required) | Output file path for the masked data |

### Masking Strategies

Six strategies are available. Each field in the masking rules JSON maps to
one strategy:

| Strategy | Description | Example input | Example output |
|---|---|---|---|
| `preserve` | Keep the original value unchanged | `ON` | `ON` |
| `preserve_format` | Replace characters but keep the pattern (digits stay digits, letters stay letters) | `2026-01-15` | `7391-08-42` |
| `deterministic_hash` | SHA-based hash truncated to field length. Same input always produces the same output | `ACCT00123` | `a7f3c9e2b1` |
| `random_range` | Replace with a random number in `[min, max]` | `50000` | `27341` |
| `redact` | Replace entire value with `*` characters | `123-45-6789` | `***********` |
| `fake_name` | Replace with a realistic fake name | `John Smith` | `Maria Garcia` |

### Masking Rules JSON Format

```json
{
  "description": "Masking rules for customer batch files",
  "fields": {
    "CUSTOMER-NAME": {
      "strategy": "fake_name"
    },
    "SSN": {
      "strategy": "redact"
    },
    "ACCT-NUM": {
      "strategy": "deterministic_hash",
      "length": 18
    },
    "BALANCE-AMT": {
      "strategy": "random_range",
      "min": 0,
      "max": 999999
    },
    "EXPIRATION-DATE": {
      "strategy": "preserve_format"
    },
    "LOCATION-CODE": {
      "strategy": "preserve"
    }
  }
}
```

Fields not listed in the rules are preserved unchanged by default.

### Use Case: Creating PII-Free Test Data

Production batch files often contain real customer names, account numbers,
and social security numbers. Regulations (GDPR, PIPEDA, CCPA) prohibit
using this data in non-production environments.

The masking workflow:

1. Get a production batch file (or extract one with `valdo extract`).
2. Create masking rules that redact or randomise PII fields.
3. Run `valdo mask` to produce a safe copy.
4. Use the masked file for development, testing, and CI validation.

`deterministic_hash` is particularly useful for join keys: the same input
always produces the same hash, so relationships between files are preserved
(e.g. customer ID in a customer file maps to the same masked ID in a
transaction file).

---

## 11. AI Prompt Library

Valdo includes a set of reusable LLM prompts in the `prompts/` directory that
help teams generate mapping and rules CSV templates from specification
documents.

### What It Solves

Teams typically receive batch file specifications as Excel spreadsheets, PDFs,
or Word documents containing natural-language field descriptions, COBOL picture
clauses, and business rules in free text. Manually translating these into
Valdo's CSV template format can take hours.

These prompts let any LLM (GitHub Copilot, GitLab Duo, Claude, ChatGPT,
Gemini) perform the translation in seconds.

### Available Prompts

| Prompt | Input | Output | File |
|---|---|---|---|
| **Mapping CSV** | Spec doc with fields, positions, types | `mapping_template.csv` for upload | `prompts/generate-mapping-csv.md` |
| **Rules CSV** | Spec doc with validation logic, required flags | `rules_template.csv` for upload | `prompts/generate-rules-csv.md` |
| **Both** | Full spec doc | Both CSVs in one go | `prompts/generate-both.md` |

### Workflow

1. Open your mapping specification (Excel, PDF, text).
2. Copy the content (or paste as a table).
3. Paste the appropriate prompt into your LLM tool.
4. Paste the spec content after the prompt.
5. The LLM generates a CSV.
6. Save the output as a `.csv` file.
7. Upload to Valdo's Web UI (Mapping Generator tab) or use
   `valdo convert-mappings`.
8. Valdo converts to JSON -- done.

### Tips

- **Large specs**: If your spec has 100+ fields, paste in batches of 20-30
  fields per prompt.
- **Multiple record types**: If your file has different record types (Header,
  Detail, Trailer), process each type separately.
- **Review output**: Always review the generated CSV before uploading -- AI
  may misinterpret ambiguous specs.
- **Iterative refinement**: After the first generation, ask follow-up
  questions like "add cross-row rules for sequential numbering."

For full details, see `prompts/README.md`.

---

## 12. Operations Guide

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

### Audit Logger

Valdo emits structured audit events to a JSONL file for compliance and
operational traceability. Every validation run, file upload, authentication
failure, and cleanup event is logged.

#### Configuration

| Variable | Default | Description |
|---|---|---|
| `AUDIT_LOG_PATH` | `logs/audit.jsonl` | Path to the JSONL audit log file |
| `CM3_ENVIRONMENT` | `DEV` | Environment tag included in every event |

#### JSONL Event Format

Each line in the audit log is a self-contained JSON object:

```json
{
  "event": "test_run_completed",
  "timestamp": "2026-03-26T14:30:00.000000+00:00",
  "run_id": "a1b2c3d4e5f6...",
  "environment": "PROD",
  "triggered_by": "api",
  "file": "customers.txt",
  "file_hash": "sha256:abc123...",
  "result": {"total_rows": 15000, "error_count": 0}
}
```

Recognised event types: `test_run_started`, `test_run_completed`,
`file_uploaded`, `file_cleanup`, `auth_failure`, `suite_step_completed`.

#### Splunk Integration

The JSONL format is directly compatible with Splunk Universal Forwarder using
`sourcetype=_json`. See `docs/splunk-setup.md` for detailed configuration
steps including:

- Universal Forwarder `inputs.conf` configuration
- Index and sourcetype setup
- Example SPL queries for dashboards

---

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

## 13. FAQ

### What file formats does Valdo support?

Valdo supports four text-based batch file formats:

- **Fixed-width** -- each field occupies a fixed number of characters per line.
- **Pipe-delimited** -- fields separated by the `|` character.
- **CSV** -- comma-separated values, with optional quoting.
- **TSV** -- tab-separated values.

Valdo auto-detects the format in most cases. You can override detection with the
`--format` flag on the CLI or the `file_format` parameter in the API. Binary
files (Excel, Parquet, etc.) are not supported as input data files, though Excel
files are used as mapping and rules templates.

### Do I need Oracle installed?

No. Oracle is only required for the database-related commands (`db-compare`,
`extract`, `reconcile`, `generate-oracle-expected`). All file validation,
comparison, and parsing features work without any database.

When you do need Oracle, Valdo uses the `oracledb` Python package in thin mode,
which means you do not need to install Oracle Instant Client or any native
libraries. Just set the `ORACLE_USER`, `ORACLE_PASSWORD`, and `ORACLE_DSN`
environment variables in your `.env` file.

### How do I create a mapping for a new file?

There are three ways:

1. **Web UI (easiest)** -- Go to the Mapping Generator tab, upload an Excel or
   CSV template that describes the file layout, review the detected fields,
   and click Save.
2. **CLI** -- Use `valdo convert-mappings` to convert an Excel/CSV template to
   a mapping JSON file in batch.
3. **By hand** -- Copy an existing mapping JSON from `config/mappings/`, rename
   it, and edit the fields to match your new file. See the
   [Mapping JSON Schema](#mapping-json-schema) section for the expected
   structure.

### Can I run Valdo without the web UI?

Yes. The CLI provides full functionality without starting the server. For
example:

```bash
# Validate a file
valdo validate -f myfile.txt -m mapping.json -o report.html

# Compare two files
valdo compare -f1 expected.txt -f2 actual.txt -m mapping.json -o diff.html

# Run a full test suite
valdo run-tests --suite config/suites/daily.yaml
```

The Web UI is a convenience layer built on top of the same API. Everything the
UI can do, the CLI can do too.

### How do I add custom validation rules?

Create a rules JSON file (or use the API/CLI to convert from an Excel
template):

1. **From Excel/CSV** -- Prepare a spreadsheet with columns for rule name,
   type, field, and expected values, then run:

   ```bash
   valdo convert-rules --file my_rules_template.xlsx --output config/rules/my_rules.json
   ```

2. **By hand** -- Create a JSON file following the
   [Rules JSON Schema](#rules-json-schema) format. Supported rule types
   include `not_blank`, `data_type`, `allowed_values`, `regex`, `range`,
   and more.

Then pass the rules file when validating:

```bash
valdo validate -f myfile.txt -m mapping.json -r config/rules/my_rules.json -o report.html
```

### What does the quality score mean?

The quality score is the percentage of rows that passed all validation checks:

```
quality_score = (valid_rows / total_rows) * 100
```

A score of 100.0 means every row is clean. A score of 99.87 means 99.87% of
rows passed and 0.13% had at least one error. The score gives you a quick
at-a-glance measure of file quality. For detailed information about which rows
failed and why, open the full HTML or JSON report.

---

## 14. Troubleshooting

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

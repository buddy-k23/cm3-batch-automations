# CM3 Batch Automations - Usage Guide

## Overview

This guide provides practical examples for using CM3 Batch Automations in both **CLI mode** and **API mode**.

> **Offline-capable reports**: All HTML reports (validation, comparison, suite summary) embed Chart.js inline.
> No internet access is required to view reports.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [CLI Usage](#cli-usage)
3. [API Usage](#api-usage)
4. [Universal Mapping](#universal-mapping)
5. [Common Workflows](#common-workflows)
6. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Starting the API Server

```bash
# Activate virtual environment
source venv/bin/activate

# Start API server (development mode)
uvicorn src.api.main:app --reload --port 8000

# Access Swagger UI
open http://localhost:8000/docs
```

### CLI Commands

```bash
# Detect file format
cm3-batch detect -f data/samples/customers.txt

# Parse file
cm3-batch parse -f data/samples/customers.txt

# Compare files
cm3-batch compare -f1 file1.txt -f2 file2.txt -k customer_id

# Parse/validate/compare with chunked processing for large files
cm3-batch parse -f data/samples/customers.txt --use-chunked --chunk-size 50000 -o reports/parsed.csv

# End-to-end regression workflow (config-driven)
./scripts/run_regression_workflow.sh config/pipeline/regression_workflow.sample.json
```

---

## CLI Usage

### 1. File Format Detection

Automatically detect the format of a file:

```bash
cm3-batch detect -f data/samples/p327_test_data.txt
```

**Output:**
```
Format: fixed_width
Confidence: 0.95
Record Length: 2809
Line Count: 10
```

### 2. File Parsing

Parse a file using a mapping:

```bash
# Parse with universal mapping
cm3-batch parse -f data/samples/p327_test_data.txt \
  -m config/mappings/p327_universal.json \
  -o output.csv
```

**Options:**
- `-f, --file`: Input file path
- `-m, --mapping`: Mapping configuration file
- `-o, --output`: Output file path
- `--format`: Output format (csv, json, excel)

#### Chunk-based parsing (large files)

```bash
cm3-batch parse -f data/samples/p327_test_data.txt \
  -m config/mappings/p327_universal.json \
  --use-chunked \
  --chunk-size 50000 \
  -o reports/p327_parsed_chunked.csv
```

> All exported CSV files include a `source_row` column identifying the original line number in the source file. This column is also shown in HTML error and difference tables for easy trace-back.

### 3. File Comparison

Compare two files and generate a report:

```bash
cm3-batch compare \
  -f1 data/samples/file1.txt \
  -f2 data/samples/file2.txt \
  -k ACCT-NUM \
  -m config/mappings/p327_universal.json \
  -o reports/comparison_report.html
```

**Options:**
- `-f1, --file1`: First file to compare
- `-f2, --file2`: Second file to compare
- `-k, --keys`: Key columns for matching (comma-separated)
- `-m, --mapping`: Mapping configuration
- `-o, --output`: Output report path
- `--detailed`: Include field-level differences

#### Chunk-based comparison (large files)

```bash
cm3-batch compare \
  -f1 data/samples/file1.txt \
  -f2 data/samples/file2.txt \
  -k ACCT-NUM \
  --use-chunked \
  --chunk-size 50000 \
  -o reports/comparison_chunked.html
```

> Chunked comparison requires key columns (`-k/--keys`). Row-by-row mode is not supported in chunked compare.

> **Auto-threshold:** When using the API (`POST /api/v1/files/compare`), files larger than 50 MB are automatically routed to chunked mode — no `--use-chunked` flag needed. Key columns must still be provided for chunked compare to activate.

#### Structure compatibility check

Before any data comparison, the compare service checks that both files are structurally compatible:

1. **Column count** — must be equal (hard stop if not)
2. **Column names** — all columns in file1 must exist in file2, and vice versa
3. **Column order** — when a mapping is provided, columns must match the mapping field order

If a mismatch is found, comparison stops early and the result includes:

```json
{
  "structure_compatible": false,
  "structure_errors": [
    {"type": "column_count_mismatch", "file1_count": 10, "file2_count": 9}
  ]
}
```

No data diff is performed when `structure_compatible` is `false`. Structurally compatible comparisons include `"structure_compatible": true` in the result.

### 4. Database Operations

Extract data from Oracle database:

```bash
cm3-batch extract \
  -t CUSTOMER_TABLE \
  -o output.txt \
  -l 1000 \
  --format fixed_width
```

**Options:**
- `-t, --table`: Table name
- `-o, --output`: Output file path
- `-l, --limit`: Number of rows to extract
- `--format`: Output format

Reconcile mapping with database schema:

```bash
cm3-batch reconcile \
  -m config/mappings/customer_mapping.json \
  -t CUSTOMER_TABLE
```

#### Source Data Verification (New!)

Validate generated files against their source data using custom SQL queries.

**Extract from SQL File:**
```bash
cm3-batch extract \
  --sql-file config/queries/p327_source.sql \
  -o trusted_p327.txt \
  -d "|"
```

**Extract with Inline Query:**
```bash
cm3-batch extract \
  --query "SELECT l.location_code, a.account_number FROM accounts a JOIN locations l ON a.location_id = l.id WHERE a.status = 'ACTIVE'" \
  -o trusted_data.txt
```

**Trusted Source Verification Workflow:**

1. **Create Source Query** (`config/queries/p327_source.sql`):
   ```sql
   SELECT 
       l.location_code,
       a.account_number,
       c.currency_code
   FROM accounts a
   JOIN locations l ON a.location_id = l.id
   JOIN currencies c ON a.currency_id = c.id
   WHERE a.status = 'ACTIVE'
   AND a.balance > 0
   ```

2. **Extract Trusted Source:**
   ```bash
   cm3-batch extract --sql-file config/queries/p327_source.sql -o trusted.txt
   ```

3. **Compare with Generated File:**
   ```bash
   cm3-batch compare \
     -f1 generated_p327.txt \
     -f2 trusted.txt \
     -k account_number \
     -o verification_report.html
   ```

**Extract Command Options:**
- `-t, --table`: Table name (for simple table extraction)
- `-q, --query`: Inline SQL query
- `-s, --sql-file`: Path to SQL file
- `-o, --output`: Output file path (required)
- `-l, --limit`: Row limit (only for --table mode)
- `-d, --delimiter`: Output delimiter (default: |)

### 5. Business Rules (New!)

Convert business rules from Excel template to JSON:

```bash
cm3-batch convert-rules \
  -t config/templates/rules_template.xlsx \
  -o config/rules/business_rules.json
```

**Options:**
- `-t, --template`: Excel (.xlsx) or CSV (.csv) template file
- `-o, --output`: Output JSON rules file
- `-s, --sheet`: Sheet name (for Excel, optional)

> All exported CSV files include a `source_row` column identifying the original line number in the source file. This column is also shown in HTML error and difference tables for easy trace-back.

### 6. Validation

Validate a file against mapping rules and generate comprehensive HTML reports:

#### Basic Validation

```bash
cm3-batch validate \
  -f data/samples/customers.txt \
  -m config/mappings/customer_mapping.json
```

**Console Output:**
```
✓ File is valid

Data Quality Score: 95.5%
  Total Rows: 1,000
  Total Columns: 25
  Completeness: 98.2%
  Uniqueness: 87.3%
```

#### Generate HTML Report

```bash
cm3-batch validate \
  -f data/samples/p327_test_data.txt \
  -m config/mappings/p327_universal.json \
  -o reports/validation_report.html \
  --rules config/rules/p327_business_rules.json \
  --detailed
```

**Options:**
- `-f, --file`: Input file to validate
- `-m, --mapping`: Mapping configuration file
- `-r, --rules`: Business rules configuration file (JSON)
- `-o, --output`: Output HTML or JSON report path
- `--detailed/--basic`: Report depth
- `--strict-fixed-width`: Enable strict fixed-width field checks
- `--strict-level [basic|format|all]`: Strict validation depth
- `--workers <n>`: Parallel worker processes for chunked validation (default: 1)

#### Strict field-level validation (fixed-width)

```bash
cm3-batch validate \
  -f data/files/p327_sample_errored.txt \
  -m config/mappings/p327_mapping.json \
  --strict-fixed-width --strict-level format --detailed \
  -o reports/p327_strict_validation.html
```

#### Chunk-based validation (large files)

```bash
cm3-batch validate \
  -f data/samples/p327_test_data.txt \
  -m config/mappings/p327_universal.json \
  --use-chunked \
  --chunk-size 50000 \
  --progress \
  -o reports/validation_report_chunked.json
```

#### Chunk-based strict fixed-width validation

```bash
cm3-batch validate \
  -f data/files/p327_sample_errored.txt \
  -m config/mappings/p327_mapping.json \
  --use-chunked --strict-fixed-width --strict-level format \
  --progress \
  -o reports/p327_chunked_strict_validation.html
```

> Chunked validation supports strict fixed-width field checks, row-level mismatch detection, and progress display.

> **Auto-threshold:** When using the API (`POST /api/v1/files/validate`), files larger than 50 MB are automatically routed to chunked mode — no `--use-chunked` flag needed.

#### Parallel chunked validation

```bash
cm3-batch validate \
  -f data/files/p327_sample_errored.txt \
  -m config/mappings/p327_mapping.json \
  --use-chunked --chunk-size 50000 --workers 3 \
  --strict-fixed-width --strict-level format \
  -o reports/p327_chunked_strict_parallel_validation.html
```

> In parallel mode (`--workers > 1`), duplicate-row detection is disabled for performance.

#### Data-type checking in chunked validation

When a mapping defines `data_type` on a field, chunked validation checks every value against that type:

| Type | Check |
|------|-------|
| `integer` | Value must be a whole number |
| `float` / `decimal` | Value must be a number |

Violations are reported as `data_type` category errors (e.g. code `DT_INT_001`) with row number and field name, consistent with non-chunked validation output.

#### Validation Report Features

The generated HTML report includes:

**1. Executive Summary**
- Overall validation status (✓ Valid / ✗ Invalid)
- Data quality score (0-100%)
- Key metrics: total rows, columns, completeness, uniqueness
- Issue counts: errors, warnings, info messages

**2. File Metadata**
- File size and record count
- Detected format and confidence score
- Processing timestamp

**3. Quality Metrics Dashboard**
- Visual quality score gauge
- Completeness percentage
- Uniqueness percentage
- Interactive charts (Chart.js)

**4. Issues and Warnings**
- Categorized by severity (errors, warnings, info)
- Field-specific issues with row numbers
- Schema validation failures
- Data quality concerns

**5. Interactive Field-Level Analysis**
- **Search**: Filter fields by name in real-time
- **Sort**: Click column headers to sort by:
  - Field Name (alphabetical)
  - Data Type (numeric, string, datetime)
  - Fill Rate (percentage)
  - Unique Values (count)
- **Pagination**: Navigate through fields (50 per page)
- **Details**: For each field:
  - Inferred data type
  - Fill rate and null count
  - Unique value count and ratio
  - Sample values

**6. Date Field Analysis** (when date fields detected)
- Date range (earliest to latest, span in days)
- Invalid dates count and percentage
- Future dates count and percentage
- Null dates count and percentage
- Detected date format (e.g., YYYYMMDD, YYYY-MM-DD)

**7. Duplicate Analysis**
- Total duplicate count
- Percentage of duplicates
- Sample duplicate records

**8. Appendix**
- **Validation Configuration**:
  - Detailed mode setting
  - Mapping file path
  - Validation timestamp
  - Validator version
- **Mapping File Details**:
  - Total fields count
  - Total width (for fixed-width files)
  - Required fields count
  - Collapsible list of required field names
- **Affected Rows Summary**:
  - Total rows with issues
  - Percentage affected
  - Total rows with issues
  - Percentage affected
  - Top 10 most problematic rows with issue details

**9. Business Rule Validation** (when rules provided)
- **Execution Statistics**:
  - Rules executed / total rules
  - Total violations count
  - Compliance rate percentage
- **Violations by Rule**:
  - Rule name and ID
  - Severity level (Error/Warning/Info)
  - Violation count
  - Sample issues with row numbers

#### Example: Comprehensive Validation

```bash
# Validate P327 file with detailed analysis
cm3-batch validate \
  -f data/samples/p327_test_data_a_20000.txt \
  -m config/mappings/p327_universal.json \
  -o reports/p327_validation.html \
  --detailed

# Output:
# ✓ File is valid
# 
# Data Quality Score: 60.26%
#   Total Rows: 20,000
#   Total Columns: 252
#   Completeness: 100.0%
#   Uniqueness: 0.65%
# 
# ✓ Validation report generated: reports/p327_validation.html
```

Open the HTML report in your browser to explore:
- Interactive field filtering and sorting
- Date field analysis with YYYYMMDD format detection
- Detailed appendix with mapping configuration
- Visual quality metrics and charts

---

## Great Expectations (Checkpoint 1, BA-friendly)

Run configurable, no-code/low-code data quality checks using CSV config files.

### Templates

- `config/templates/csv/gx_checkpoint1_targets_template.csv`
- `config/templates/csv/gx_checkpoint1_expectations_template.csv`

### Command

```bash
cm3-batch gx-checkpoint1 \
  --targets config/gx/targets.sample.csv \
  --expectations config/gx/expectations.sample.csv \
  --output reports/gx_checkpoint1_summary.json \
  --csv-output reports/gx_checkpoint1_summary.csv \
  --html-output reports/gx_checkpoint1_summary.html
```

### What this validates

- Schema and column order
- Required field non-null
- Key uniqueness
- Allowed values
- Numeric ranges
- Row-count thresholds

See `docs/GREAT_EXPECTATIONS_CHECKPOINT1.md` for full BA-oriented setup.

---

## Test Suite Orchestration

Run multiple tests in one command using a YAML suite file.

### Create a suite from Excel

```bash
# Write an empty Excel template
cm3-batch convert-suite --template config/test_suites/my_suite.xlsx

# Convert a filled-in Excel to YAML
cm3-batch convert-suite --input config/test_suites/my_suite.xlsx --output-dir config/test_suites
```

### Run a test suite

```bash
# Dry-run (shows what would run without executing)
cm3-batch run-tests --suite config/test_suites/p327_uat.yaml --dry-run

# Run with a specific date parameter
cm3-batch run-tests --suite config/test_suites/p327_uat.yaml \
  --params "run_date=20260301" \
  --env dev \
  --output-dir reports
```

> **Run history storage:** Results are always written to `reports/run_history.json`. When `ORACLE_USER` is set in `.env`, each run is also persisted to `CM3INT.CM3_RUN_HISTORY` and `CM3INT.CM3_RUN_TESTS` in Oracle. The Recent Runs tab in the Web UI reads from the DB when available, falling back to the JSON file. See [Database Setup](INSTALL.md#database-setup) in the install guide for table setup instructions.

**Suite YAML format:**
```yaml
name: P327 UAT Suite
environment: dev
tests:
  - name: P327 structural check
    type: structural
    file: data/p327_${run_date}.dat
    mapping: P327_full_in_sheet_order_strict
    thresholds:
      max_errors: 0
  - name: P327 Oracle vs file
    type: oracle_vs_file
    file: data/p327_${run_date}.dat
    mapping: P327_full_in_sheet_order_strict
    oracle_query: "SELECT * FROM CM3INT.SHAW_SRC_P327 WHERE BATCH_DATE = :run_date"
    oracle_params:
      run_date: "${run_date}"
    key_columns: [LN]
    thresholds:
      max_different_rows_pct: 0.5
```

**Built-in parameters:** `${today}`, `${yesterday}`, `${run_date}` (same as today unless overridden), `${run_id}`, `${environment}`.

**Test types:**
- `structural` — validates file format against mapping (field lengths, required fields, data types)
- `rules` — validates business rules from a rules file
- `oracle_vs_file` — extracts Oracle data to CSV then compares against the file
- `api_check` — calls an external HTTP endpoint and asserts on status code / JSON response

**Exit codes:** 0 = all tests passed, 1 = one or more tests failed or errored.

### Listing archived runs

```bash
cm3-batch list-runs            # show latest 20 runs
cm3-batch list-runs --limit 5  # show latest 5 runs
```

Lists all archived suite runs, newest first. Runs older than `REPORT_RETENTION_DAYS` (default 365) are purged automatically on each call.

### Inspecting a specific run

```bash
cm3-batch get-run <run_id>
```

Prints the SHA-256 manifest for the given run and lists all archived file paths. Exits with code 1 if the run ID is not found.

### Tamper-evident archive

Every suite run is automatically archived to `reports/archive/YYYY/MM/DD/{run_id}/` with a SHA-256 manifest. The manifest covers all report files and itself, allowing auditors to detect post-run tampering.

Environment variables:
- `REPORT_ARCHIVE_PATH` (default: `reports/archive`) — root of the archive tree
- `REPORT_RETENTION_DAYS` (default: `365`) — runs older than this are deleted by `list-runs`

### API Check Tests

Use `type: api_check` to validate external HTTP endpoints in your suite:

```yaml
tests:
  - name: Batch service health check
    type: api_check
    url: "http://internal-batch-svc/health"
    method: GET
    expected_status: 200
    response_contains:
      status: "ok"
    timeout_seconds: 30
```

Supported fields: `url`, `method` (GET/POST), `body` (JSON dict for POST),
`expected_status`, `response_contains` (JSON key/value assertions), `timeout_seconds`.

---

## API Usage

### Accessing the API

**Swagger UI (Interactive):**
```
http://localhost:8000/docs
```

**ReDoc (Alternative):**
```
http://localhost:8000/redoc
```

**Health Check:**
```bash
curl http://localhost:8000/api/v1/system/health
```

### API Security

Most API endpoints require an API key passed via `X-API-Key`.

```bash
# .env
API_KEYS=key-dev-abc123,key-int-xyz789
ALLOWED_ORIGINS=https://internal-dashboard.bank.com
```

```bash
curl -H "X-API-Key: key-dev-abc123" http://localhost:8000/api/v1/system/info
```

`GET /api/v1/system/health` stays unauthenticated for monitoring.

### 1. System Endpoints

#### Health Check

```bash
curl http://localhost:8000/api/v1/system/health
```

**Response:**
```json
{
  "status": "healthy",
  "version": "1.0.0",
  "timestamp": "2026-02-07T19:30:00Z"
}
```

#### System Information

```bash
curl -H "X-API-Key: key-dev-abc123" http://localhost:8000/api/v1/system/info
```

**Response:**
```json
{
  "python_version": "3.9.0",
  "api_version": "1.0.0",
  "supported_formats": ["pipe_delimited", "fixed_width", "csv", "tsv"],
  "database_connected": false
}
```

### 2. Mapping Endpoints

#### Upload Excel/CSV Template

```bash
curl -X POST "http://localhost:8000/api/v1/mappings/upload" \
  -F "file=@data/mappings/p327-target-template.xlsx" \
  -F "mapping_name=p327_mapping" \
  -F "file_format=fixed_width"
```

**Response:**
```json
{
  "filename": "p327-target-template.xlsx",
  "size": 45678,
  "mapping_id": "p327_mapping",
  "message": "Template converted successfully. Mapping saved as 'p327_mapping'"
}
```

#### List All Mappings

```bash
curl http://localhost:8000/api/v1/mappings/
```

**Response:**
```json
[
  {
    "id": "p327_universal",
    "mapping_name": "p327_universal",
    "version": "1.0.0",
    "format": "fixed_width",
    "total_fields": 252,
    "created_date": "2026-02-07T18:31:00Z"
  }
]
```

#### Get Mapping by ID

```bash
curl http://localhost:8000/api/v1/mappings/p327_universal
```

**Response:**
```json
{
  "id": "p327_universal",
  "mapping_name": "p327_universal",
  "version": "1.0.0",
  "description": "P327 Fixed-Width Mapping",
  "source": {
    "type": "file",
    "format": "fixed_width",
    "encoding": "UTF-8"
  },
  "fields": [...],
  "total_fields": 252,
  "total_record_length": 2809
}
```

#### Delete Mapping

```bash
curl -X DELETE http://localhost:8000/api/v1/mappings/p327_mapping
```

### 3. File Endpoints

#### Detect File Format

```bash
curl -X POST "http://localhost:8000/api/v1/files/detect" \
  -F "file=@data/samples/p327_test_data.txt"
```

**Response:**
```json
{
  "format": "fixed_width",
  "confidence": 0.95,
  "line_count": 10,
  "record_length": 2809,
  "sample_lines": ["...", "..."]
}
```

#### Parse File

```bash
curl -X POST "http://localhost:8000/api/v1/files/parse" \
  -F "file=@data/samples/p327_test_data.txt" \
  -F 'request={"mapping_id": "p327_universal", "output_format": "csv"}'
```

**Response:**
```json
{
  "rows_parsed": 10,
  "columns": 252,
  "preview": [{...}, {...}],
  "download_url": "/api/v1/files/download/parsed_p327_test_data.txt.csv",
  "errors": []
}
```

#### Compare Files

```bash
curl -X POST "http://localhost:8000/api/v1/files/compare" \
  -F "file1=@data/samples/file1.txt" \
  -F "file2=@data/samples/file2.txt" \
  -F 'request={"mapping_id": "p327_universal", "key_columns": ["ACCT-NUM"]}'
```

**Response:**
```json
{
  "total_rows_file1": 10,
  "total_rows_file2": 10,
  "matching_rows": 8,
  "only_in_file1": 1,
  "only_in_file2": 1,
  "differences": 2,
  "report_url": "/api/v1/reports/comparison_12345.html"
}
```

### 4. Using Python Requests

```python
import requests

# Upload template
with open('template.xlsx', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/api/v1/mappings/upload',
        files={'file': f},
        params={'mapping_name': 'my_mapping', 'file_format': 'fixed_width'}
    )
    print(response.json())

# List mappings
response = requests.get('http://localhost:8000/api/v1/mappings/')
mappings = response.json()
for mapping in mappings:
    print(f"{mapping['id']}: {mapping['total_fields']} fields")

# Parse file
with open('data.txt', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/api/v1/files/parse',
        files={'file': f},
        json={'mapping_id': 'my_mapping', 'output_format': 'csv'}
    )
    result = response.json()
    print(f"Parsed {result['rows_parsed']} rows")
```

---

## Universal Mapping

### Creating Mappings from Templates

#### From Excel Template

```bash
python src/config/template_converter.py \
  data/mappings/my_template.xlsx \
  config/mappings/my_mapping.json \
  my_mapping_name \
  fixed_width
```

#### From CSV Template

```bash
python src/config/template_converter.py \
  data/mappings/my_template.csv \
  config/mappings/my_mapping.json \
  my_mapping_name \
  pipe_delimited
```

### Validating Mappings

```bash
python src/config/universal_mapping_parser.py \
  config/mappings/my_mapping.json
```

**Output:**
```
✓ Mapping is valid
Format: fixed_width
Total Fields: 252
Total Record Length: 2809
```

### Using Mappings in Code

```python
from src.config.universal_mapping_parser import UniversalMappingParser
from src.parsers.fixed_width_parser import FixedWidthParser

# Load mapping
parser = UniversalMappingParser(mapping_path="config/mappings/p327_universal.json")

# Get field positions for fixed-width
if parser.get_format() == 'fixed_width':
    positions = parser.get_field_positions()
    # Returns: [('FIELD1', 0, 10), ('FIELD2', 10, 25), ...]
    
    # Parse file
    file_parser = FixedWidthParser('data.txt', positions)
    df = file_parser.parse()

# Get column names for delimited
else:
    columns = parser.get_column_names()
    delimiter = parser.get_delimiter()
```

---

## Common Workflows

### Workflow 1: Upload Template and Parse File (API)

```bash
# 1. Upload Excel template
curl -X POST "http://localhost:8000/api/v1/mappings/upload" \
  -F "file=@template.xlsx" \
  -F "mapping_name=my_mapping"

# 2. Parse data file
curl -X POST "http://localhost:8000/api/v1/files/parse" \
  -F "file=@data.txt" \
  -F 'request={"mapping_id": "my_mapping", "output_format": "csv"}'

# 3. Download parsed file
curl -O http://localhost:8000/api/v1/files/download/parsed_data.txt.csv
```

### Workflow 2: Compare Two Files (CLI)

```bash
# 1. Create mapping from template
python src/config/template_converter.py \
  template.xlsx \
  config/mappings/my_mapping.json \
  my_mapping \
  fixed_width

# 2. Compare files
cm3-batch compare \
  -f1 file1.txt \
  -f2 file2.txt \
  -k ACCT-NUM \
  -m config/mappings/my_mapping.json \
  -o reports/comparison.html

# 3. View report
open reports/comparison.html
```

### Workflow 3: Database Extract and Validate

```bash
# 1. Extract from database
cm3-batch extract \
  -t CUSTOMER_TABLE \
  -o extracted_data.txt \
  --format fixed_width

# 2. Validate against mapping
cm3-batch validate \
  -f extracted_data.txt \
  -m config/mappings/customer_mapping.json

# 3. Generate report
cm3-batch parse \
  -f extracted_data.txt \
  -m config/mappings/customer_mapping.json \
  -o validated_data.csv
```

### Workflow 4: Automated Testing (API)

```python
import requests

base_url = "http://localhost:8000"

# 1. Health check
health = requests.get(f"{base_url}/api/v1/system/health")
assert health.json()["status"] == "healthy"

# 2. Upload template
with open("template.xlsx", "rb") as f:
    upload = requests.post(
        f"{base_url}/api/v1/mappings/upload",
        files={"file": f},
        params={"mapping_name": "test_mapping"}
    )
    mapping_id = upload.json()["mapping_id"]

# 3. Detect file format
with open("data.txt", "rb") as f:
    detect = requests.post(
        f"{base_url}/api/v1/files/detect",
        files={"file": f}
    )
    print(f"Detected format: {detect.json()['format']}")

# 4. Parse file
with open("data.txt", "rb") as f:
    parse = requests.post(
        f"{base_url}/api/v1/files/parse",
        files={"file": f},
        json={"mapping_id": mapping_id}
    )
    print(f"Parsed {parse.json()['rows_parsed']} rows")
```

---

## Troubleshooting

### API Server Won't Start

```bash
# Check if port is in use
lsof -i :8000

# Kill process using port
kill -9 <PID>

# Start with different port
uvicorn src.api.main:app --port 8001
```

### File Upload Fails

```bash
# Check file size
ls -lh file.txt

# Check uploads directory exists
mkdir -p uploads

# Check permissions
chmod 755 uploads
```

### Mapping Validation Errors

```bash
# Validate mapping schema
python src/config/universal_mapping_parser.py config/mappings/my_mapping.json

# Check JSON syntax
python -m json.tool config/mappings/my_mapping.json
```

### Oracle Connection Issues

```bash
# Test Oracle client
python -c "import cx_Oracle; print(cx_Oracle.clientversion())"

# Check environment variables
echo $ORACLE_HOME
echo $LD_LIBRARY_PATH

# Test connection
python -c "import cx_Oracle; conn = cx_Oracle.connect('user/pass@dsn'); print('Connected')"
```

### File Parsing Errors

```bash
# Check file encoding
file -i data.txt

# Detect format first
curl -X POST "http://localhost:8000/api/v1/files/detect" -F "file=@data.txt"

# Validate mapping matches file format
python src/config/universal_mapping_parser.py config/mappings/my_mapping.json
```

---

## Environment Variables

Create a `.env` file in the project root:

```bash
# Oracle Database
ORACLE_USER=your_username
ORACLE_PASSWORD=your_password
ORACLE_DSN=hostname:port/service_name

# API Configuration
API_PORT=8000
API_HOST=0.0.0.0
API_WORKERS=4

# Logging
LOG_LEVEL=INFO
LOG_DIR=logs
```

---

## Additional Resources

- **Universal Mapping Guide**: [docs/UNIVERSAL_MAPPING_GUIDE.md](UNIVERSAL_MAPPING_GUIDE.md)
- **Testing Guide**: [docs/TESTING_GUIDE.md](TESTING_GUIDE.md)
- **Deployment Options**: [docs/DEPLOYMENT_OPTIONS.md](DEPLOYMENT_OPTIONS.md)
- **Architecture**: [docs/architecture.md](architecture.md)
- **API Walkthrough**: [api_walkthrough.md](API_UPLOAD_GUIDE.md)

---

## Quick Reference

### CLI Commands
```bash
cm3-batch detect -f <file>              # Detect format
cm3-batch parse -f <file> -m <mapping>  # Parse file
cm3-batch compare -f1 <f1> -f2 <f2>     # Compare files
cm3-batch validate -f <file>            # Validate file
cm3-batch convert-rules -t <template>   # Convert rules
cm3-batch extract -t <table>            # Extract from DB
cm3-batch reconcile -m <mapping>        # Reconcile mapping
cm3-batch list-runs                     # List archived suite runs
cm3-batch get-run <run_id>              # Inspect a specific run
```

### API Endpoints
```
GET  /api/v1/system/health              # Health check
POST /api/v1/mappings/upload            # Upload template
GET  /api/v1/mappings/                  # List mappings
POST /api/v1/files/detect               # Detect format
POST /api/v1/files/parse                # Parse file
POST /api/v1/files/compare              # Compare files
```

### Python Usage
```python
# Universal Mapping
from src.config.universal_mapping_parser import UniversalMappingParser
parser = UniversalMappingParser(mapping_path="config/mappings/my_mapping.json")

# File Parsing
from src.parsers.fixed_width_parser import FixedWidthParser
parser = FixedWidthParser(file_path, field_positions)
df = parser.parse()

# API Client
import requests
response = requests.get("http://localhost:8000/api/v1/system/health")
```

---

**Need Help?** Check the [Swagger UI](http://localhost:8000/docs) for interactive API documentation!

---

## CI/CD Integration

### Trigger File Watcher

Drop a file named `batch_complete_YYYYMMDD.trigger` in your watch directory when the
batch job completes. The watcher picks it up, runs the matching suite, and deletes the trigger.

```bash
cm3-batch watch \
  --dir /batch/triggers \
  --suites config/test_suites/ \
  --env dev \
  --output-dir reports \
  --interval 30
```

### Webhook Trigger

Call the API from GitLab/Azure after the batch completes:

```bash
curl -X POST http://cm3-server:8000/api/v1/runs/trigger \
  -H "Content-Type: application/json" \
  -d '{"suite": "config/test_suites/p327_uat.yaml", "params": {"run_date": "20260301"}, "env": "dev"}'
```

Check status: `GET /api/v1/runs/{run_id}`

### Pipeline Templates

Copy from the `ci/` directory:
- `ci/gitlab-cm3-validate.yml` — GitLab CI include template
- `ci/azure-cm3-validate.yml` — Azure DevOps pipeline task

### E2E Tests (Playwright)

The `e2e` CI job runs `scripts/e2e_full_ui.py` on every push. It starts the
FastAPI server with uvicorn, waits for the health endpoint, then drives all
four UI workflows with a headless Chromium browser. Failures block merges.

To run the E2E suite locally (server must already be running on port 8000):

```bash
# Install Playwright and browser once
pip install playwright
playwright install chromium

# Run suite
python3 scripts/e2e_full_ui.py
```

Screenshots are written to `screenshots/e2e-full-{date}/`. On CI failure,
they are uploaded as the `e2e-screenshots` artifact for debugging.

---

## Web UI

Start the API server and open `http://localhost:8000/ui` in your browser.

**Quick Test** — Upload a batch file, select a mapping, and click Validate or Compare.
The HTML report opens in a new tab.

**Recent Runs** — Shows the last 20 test suite runs with pass/fail status and links
to suite reports.

### Mapping Generator Tab

Upload an Excel or CSV template to generate JSON config files without using the CLI.

#### Generate a Field Mapping

1. Open the web UI at `http://localhost:8000/ui`
2. Click the **Mapping Generator** tab
3. Drop your mapping template (`.xlsx` or `.csv`) into the **Field Mapping** drop zone
4. Optionally enter a mapping name and select a format (defaults to auto-detect)
5. Click **Generate Mapping**
6. On success: download the JSON or click **Use in Quick Test →** to use it immediately

**Required template columns:** `Field Name`, `Data Type`
**Optional columns:** `Position`, `Length`, `Format`, `Required`, `Description`, `Default Value`, `Target Name`, `Valid Values`

#### Generate Validation Rules

1. Drop your rules template into the **Validation Rules** drop zone
2. Select the template type: **BA-friendly** (default) or **Technical**
3. Click **Generate Rules**
4. Download the generated rules JSON

**BA-friendly required columns:** `Rule ID`, `Rule Name`, `Field`, `Rule Type`, `Severity`, `Expected / Values`, `Enabled`
**Technical required columns:** `Rule ID`, `Rule Name`, `Description`, `Type`, `Severity`, `Operator`

### API Tester Tab

Test any REST API without leaving the app. All requests are proxied server-side, so there are no CORS restrictions.

#### Send a Single Request

1. Click the **API Tester** tab
2. Select the HTTP method (GET / POST / PUT / PATCH / DELETE)
3. Enter the **Base URL** (e.g. `http://127.0.0.1:8000`) and **Path** (e.g. `/api/v1/system/health`)
4. Optionally expand **Headers**, **Body**, or **Assertions** to add request data
5. Click **Send** — the response appears on the right with a status badge and elapsed time
6. Switch between **Body** (pretty-printed JSON), **Headers**, and **Raw** sub-tabs

**Body types:** None, JSON (paste raw JSON), Form Data (key/value fields + file uploads)

#### Save a Request to a Suite

1. Enter a **Request name** in the send row
2. Select a suite from the **— save to suite —** dropdown (or click **New Suite** in the Suite Runner)
3. Click **Save** — the request is appended to the suite on the server

#### Run a Test Suite

1. In the **Suite Runner** section, select a suite from the dropdown
2. Assertions are defined per-request (field + operator + expected):
   - `status_code` `equals` `200`
   - `$.status` `equals` `healthy`
   - `$.data[0].id` `exists`
3. Click **Run Suite** — all requests execute sequentially; pass (✓) / fail (✗) appears inline per assertion
4. The summary bar shows total passed / failed / elapsed ms

#### Reorder requests in a suite

Drag any request row to a new position within the suite runner list:

1. Hover over a request row — the cursor changes to a grab hand
2. Drag the row to the desired position; the target row highlights in blue
3. Drop — the list reorders immediately in memory
4. Click **Save Order** (appears below the list) to persist the new order to the server
5. Loading a different suite clears the Save Order button and any unsaved reorder

**Assertion operators:** `equals` (exact match), `contains` (substring or array element), `exists` (field is present and non-null)

#### Suite Storage

Suites are saved as JSON files in `config/api-tester/suites/`. Each suite is a `{uuid}.json` file containing the suite name, base URL, and all saved requests with their assertions.

### UI tooltips

Hover over any button, dropdown, or upload zone to see a contextual tooltip describing what it does.

## Canonical Task Contracts (Sprint 1)

### API ingest
- `POST /api/v1/tasks/submit` accepts canonical request fields and persists an initial `queued` lifecycle row.
- `GET /api/v1/tasks/{task_id}` returns durable lifecycle state.
- `GET /api/v1/tasks?limit=50` lists recent jobs.

### CLI ingest
Use `submit-task` to submit canonical requests from CLI:

```bash
python -m src.main submit-task \
  --intent validate \
  --payload '{"mapping_id":"p327_mapping","file":"input.txt"}' \
  --machine-errors
```

If input is invalid, CLI exits non-zero and prints machine-readable errors when `--machine-errors` is set.

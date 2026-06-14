# Database-to-File Validations — Step-by-Step Guide

This guide covers every step for validating that data extracted from a database matches the contents of a batch file. It includes schema reconciliation, DB-to-file comparison, extraction, and end-to-end regression workflows using Valdo.

---

## Prerequisites

1. **Python 3.9+** with virtual environment activated
2. **Valdo installed**: `pip install -e .` and `valdo --help` works
3. **Database access** configured — set environment variables in `.env`:

   ```ini
   # Database adapter (oracle, postgresql, or sqlite)
   DB_ADAPTER=oracle

   # Oracle connection
   ORACLE_USER=APP_INT
   ORACLE_PASSWORD=your_password_here
   ORACLE_DSN=hostname:1521/FREEPDB1
   ORACLE_SCHEMA=APP_INT
   ```

4. **Mapping JSON** for the file under test (see [Testing Fixed-Width Files](TESTING_FIXED_WIDTH_FILES.md) for creation steps)

> **Note:** Valdo uses `python-oracledb` in thin mode by default. Oracle Instant Client is not required for basic connectivity.

---

## Step 1: Verify Database Connectivity

Test that Valdo can reach the database:

```powershell
valdo info
```

This displays system info including the configured database adapter and connection status.

For a direct connection test:

```python
from src.database.connection import OracleConnection

conn = OracleConnection.from_env()
print("Connected successfully")
```

Or use the standalone smoke test:

```powershell
python test_oracle_connection.py
```

---

## Step 2: Schema Reconciliation (Mapping vs. Database)

Before comparing data, verify that your mapping fields align with the actual database schema.

#### 2a. Reconcile a Single Mapping

```powershell
valdo reconcile -m config\mappings\p327_mapping.json
```

**What this checks:**
- Target table exists in the database
- Every mapped column exists in the target table
- Data types are compatible (e.g., mapping `string` matches DB `VARCHAR2`)
- Required fields in the mapping vs. nullable columns in the DB
- String length constraints (mapping `max_length` vs. DB `data_length`)
- Numeric precision/scale compatibility
- Date format compatibility (mapping date format vs. DB column type)
- Mapping key columns align with DB PK/UNIQUE constraints
- Unmapped NOT NULL columns are flagged as warnings

**Sample output:**
```
======================================================================
MAPPING RECONCILIATION REPORT
======================================================================
Mapping: P327 Target Template (v1)
Target Table: APP_INT.P327_TARGET
Status: VALID

Mapped Columns: 252
Database Columns: 260

WARNINGS:
  ⚠ Required database columns not in mapping: ['CREATED_DATE', 'MODIFIED_BY']
  ⚠ Column CREDIT-LIMIT-AMT: mapping type is integer but DB scale is 2
======================================================================
```

#### 2b. Reconcile All Mappings in a Directory

```powershell
valdo reconcile-all `
  -d config\mappings `
  -o reports\reconcile_all.json
```

#### 2c. Detect Reconciliation Drift Against a Baseline

Save a baseline, then compare future runs against it:

```powershell
# Create baseline
valdo reconcile-all -d config\mappings -o reports\reconcile_baseline.json

# Later — detect drift
valdo reconcile-all `
  -d config\mappings `
  -o reports\reconcile_current.json `
  --baseline reports\reconcile_baseline.json `
  --fail-on-drift
```

The `--fail-on-drift` flag causes a non-zero exit code if any mapping's reconciliation results have changed, making it suitable for CI gates.

---

## Step 3: Extract Data from Database

Extract a table or query result to a flat file for manual inspection:

#### 3a. Extract a Full Table

```powershell
valdo extract -t APP_INT.P327_TARGET -o data\files\p327_db_extract.txt -l 1000
```

- `-t` — table name (with optional schema prefix)
- `-o` — output file path
- `-l` — row limit (optional, useful for sampling)

#### 3b. Extract Using a SQL Query

```powershell
valdo extract `
  -q "SELECT LOCATION_CODE, ACCT_NUM, CREDIT_LIMIT_AMT FROM APP_INT.P327_TARGET WHERE ROWNUM <= 500" `
  -o data\files\p327_query_extract.txt
```

The output is a pipe-delimited file by default, suitable for comparison.

---

## Step 4: DB-to-File Comparison

This is the core workflow: extract data from the database and compare it row-by-row against an actual batch file.

#### 4a. Compare Using a Table Name

```powershell
valdo db-compare `
  --query "APP_INT.P327_TARGET" `
  --mapping config\mappings\p327_mapping.json `
  --actual-file data\files\p327_batch_output.txt `
  --key-columns "ACCT-NUM" `
  --output reports\db_compare_result.json
```

#### 4b. Compare Using a SQL Query

```powershell
valdo db-compare `
  --query "SELECT * FROM APP_INT.P327_TARGET WHERE BATCH_DATE = '20260415'" `
  --mapping config\mappings\p327_mapping.json `
  --actual-file data\files\p327_batch_output.txt `
  --key-columns "LOCATION-CODE,ACCT-NUM" `
  --output reports\db_compare_result.json
```

#### 4c. Compare with Field Transforms

When the mapping defines field-level transforms (e.g., date formatting, padding), apply them to DB rows before comparison:

```powershell
valdo db-compare `
  --query "APP_INT.P327_TARGET" `
  --mapping config\mappings\p327_mapping.json `
  --actual-file data\files\p327_batch_output.txt `
  --key-columns "ACCT-NUM" `
  --apply-transforms `
  --output reports\db_compare_transformed.json
```

**Understanding the output:**

```json
{
  "workflow": {
    "status": "passed",
    "db_rows_extracted": 1500,
    "query_or_table": "APP_INT.P327_TARGET"
  },
  "compare": {
    "total_rows_file1": 1500,
    "total_rows_file2": 1500,
    "matching_rows": 1500,
    "only_in_file1": 0,
    "only_in_file2": 0,
    "rows_with_differences": 0,
    "structure_compatible": true
  }
}
```

| Field                  | Meaning                                              |
|------------------------|------------------------------------------------------|
| `status`               | `passed` if all rows match, `failed` otherwise       |
| `db_rows_extracted`    | Number of rows pulled from the database              |
| `total_rows_file2`     | Number of rows in the actual batch file              |
| `matching_rows`        | Rows that are identical in both sources              |
| `only_in_file1`        | Rows in DB extract but not in the file               |
| `only_in_file2`        | Rows in the file but not in the DB extract           |
| `rows_with_differences`| Rows present in both but with field-level mismatches |

---

## Step 5: Web UI — DB Compare Tab

For interactive testing, use the browser-based DB Compare tab:

1. Start the server:
   ```powershell
   valdo serve
   ```

2. Open `http://localhost:8000/ui`

3. Go to the **DB Compare** tab

4. Enter connection details:
   - The connection chip stores credentials in `sessionStorage` (password is never persisted)
   - Select direction: **DB → File** or **File → DB**

5. Enter the SQL query or table name

6. Upload the actual batch file

7. Specify key columns for row matching

8. Click **Compare**

9. Review the split-panel diff view and download the CSV diff report

---

## Step 6: Validate the Batch File Independently

After confirming DB-to-file alignment, validate the file's structural integrity:

```powershell
valdo validate `
  -f data\files\p327_batch_output.txt `
  -m config\mappings\p327_mapping.json `
  -r config\rules\p327_business_rules.json `
  --strict-fixed-width --strict-level format `
  --detailed `
  -o reports\p327_file_validation.html
```

This catches issues that DB comparison alone won't find:
- Line length violations
- Invalid date formats in the file
- Business rule violations (e.g., credit limit out of range)
- Missing required fields

---

## Step 7: End-to-End Regression Workflow

Run a complete Parse → Validate → Compare regression in one command:

#### 7a. Create a Regression Config

Create `config/pipeline/regression_workflow.json`:

```json
{
  "name": "P327 Regression",
  "steps": [
    {
      "type": "parse",
      "file": "data/files/p327_batch_output.txt",
      "mapping": "config/mappings/p327_mapping.json"
    },
    {
      "type": "validate",
      "file": "data/files/p327_batch_output.txt",
      "mapping": "config/mappings/p327_mapping.json",
      "rules": "config/rules/p327_business_rules.json",
      "strict_fixed_width": true
    },
    {
      "type": "compare",
      "file1": "data/files/p327_baseline.txt",
      "file2": "data/files/p327_batch_output.txt",
      "keys": "ACCT-NUM"
    }
  ]
}
```

#### 7b. Run the Workflow

```powershell
# PowerShell
.\scripts\run_regression_workflow.ps1 `
  -Config config\pipeline\regression_workflow.json `
  -SummaryOut reports\regression_workflow\summary.json
```

```bash
# Bash
./scripts/run_regression_workflow.sh \
  config/pipeline/regression_workflow.json \
  reports/regression_workflow/summary.json
```

---

## Step 8: ETL Pipeline Validation Gates

For multi-step ETL pipelines, define validation gates in YAML:

```yaml
# config/pipelines/p327_etl.yaml
name: P327 ETL Pipeline
gates:
  - name: Source File Validation
    type: validate
    file: data/files/p327_source.txt
    mapping: config/mappings/p327_mapping.json
    rules: config/rules/p327_business_rules.json
    blocking: true
    thresholds:
      max_errors: 0
      max_error_pct: 0.0

  - name: DB Load Verification
    type: db_compare
    query: "SELECT * FROM APP_INT.P327_TARGET"
    mapping: config/mappings/p327_mapping.json
    actual_file: data/files/p327_source.txt
    key_columns: "ACCT-NUM"
    blocking: true

  - name: Output File Validation
    type: validate
    file: data/files/p327_output.txt
    mapping: config/mappings/p327_mapping.json
    blocking: false
    thresholds:
      max_error_pct: 1.0
```

Run the pipeline:

```powershell
valdo run-etl-pipeline --config config\pipelines\p327_etl.yaml
```

**Gate behavior:**
- `blocking: true` — pipeline stops on failure (non-zero exit code)
- `blocking: false` — failure is logged as a warning, pipeline continues
- Thresholds control acceptable error levels (`max_errors`, `max_error_pct`, `min_rows`)

---

## Step 9: Automated Test Suites

Run multiple validation and comparison tests from a YAML suite:

```yaml
# config/suites/p327_suite.yaml
name: P327 Nightly Suite
tests:
  - name: Validate P327 Output
    type: validate
    file: data/files/p327_batch_output.txt
    mapping: config/mappings/p327_mapping.json
    rules: config/rules/p327_business_rules.json

  - name: Compare DB vs File
    type: db_compare
    query: "APP_INT.P327_TARGET"
    mapping: config/mappings/p327_mapping.json
    actual_file: data/files/p327_batch_output.txt
    key_columns: "ACCT-NUM"

  - name: Compare Baseline vs Current
    type: compare
    file1: data/files/p327_baseline.txt
    file2: data/files/p327_batch_output.txt
    keys: "ACCT-NUM"
```

```powershell
valdo run-tests --suite config\suites\p327_suite.yaml
```

---

## Step 10: CI/CD Integration

#### GitLab CI Example

```yaml
db-validation:
  stage: test
  script:
    - valdo reconcile -m config/mappings/p327_mapping.json
    - valdo db-compare
        --query "APP_INT.P327_TARGET"
        --mapping config/mappings/p327_mapping.json
        --actual-file data/files/p327_batch_output.txt
        --key-columns "ACCT-NUM"
        --output reports/db_compare.json
    - valdo validate
        -f data/files/p327_batch_output.txt
        -m config/mappings/p327_mapping.json
        -r config/rules/p327_business_rules.json
        -o reports/validation.html
  artifacts:
    paths:
      - reports/
```

#### Azure DevOps Example

```yaml
- task: PythonScript@0
  inputs:
    scriptSource: inline
    script: |
      valdo reconcile -m config/mappings/p327_mapping.json
      valdo db-compare --query "APP_INT.P327_TARGET" --mapping config/mappings/p327_mapping.json --actual-file $(Build.SourcesDirectory)/data/files/p327_batch_output.txt --key-columns "ACCT-NUM"
```

---

## Step 11: Pluggable Database Adapters

Valdo supports three database backends. Switch by setting `DB_ADAPTER`:

| Adapter      | Env Var        | Use Case                          |
|--------------|----------------|-----------------------------------|
| `oracle`     | `DB_ADAPTER=oracle`     | Production Oracle databases       |
| `postgresql` | `DB_ADAPTER=postgresql` | PostgreSQL environments           |
| `sqlite`     | `DB_ADAPTER=sqlite`     | Local testing without a DB server |

For SQLite (useful for local development):

```ini
DB_ADAPTER=sqlite
# SQLite uses a file path instead of host/port
DB_NAME=data/test.db
```

---

## Step 12: Secrets Management

For production environments, avoid storing credentials in `.env`:

| Provider       | Config                                                  |
|----------------|---------------------------------------------------------|
| Environment    | `SECRETS_PROVIDER=env` (default)                        |
| HashiCorp Vault| `SECRETS_PROVIDER=vault` + `VAULT_ADDR`, `VAULT_ROLE_ID`, `VAULT_SECRET_ID` |
| Azure Key Vault| `SECRETS_PROVIDER=azure` + `AZURE_VAULT_URL`            |

---

## Quick Reference — Common Commands

| Task                              | Command                                                                                     |
|-----------------------------------|---------------------------------------------------------------------------------------------|
| Test DB connection                | `valdo info`                                                                                |
| Reconcile mapping vs. DB schema   | `valdo reconcile -m <mapping>`                                                              |
| Reconcile all mappings            | `valdo reconcile-all -d config/mappings -o report.json`                                     |
| Detect reconciliation drift       | `valdo reconcile-all -d config/mappings --baseline baseline.json --fail-on-drift`           |
| Extract table to file             | `valdo extract -t <TABLE> -o output.txt -l 1000`                                           |
| Extract query to file             | `valdo extract -q "SELECT ..." -o output.txt`                                              |
| DB-to-file comparison             | `valdo db-compare --query <TABLE_OR_SQL> --mapping <mapping> --actual-file <file> --key-columns <keys>` |
| DB compare with transforms        | Add `--apply-transforms` to the above                                                       |
| Run ETL pipeline gates            | `valdo run-etl-pipeline --config <pipeline.yaml>`                                           |
| Run test suite                    | `valdo run-tests --suite <suite.yaml>`                                                      |
| Regression workflow               | `./scripts/run_regression_workflow.ps1 -Config <config.json>`                               |

---

## Troubleshooting

| Symptom                                         | Cause                                              | Fix                                                              |
|-------------------------------------------------|----------------------------------------------------|------------------------------------------------------------------|
| `DPI-1047: Cannot locate Oracle Client library` | Oracle Instant Client not installed                | Use thin mode (default) or install Instant Client                |
| `ORA-12154: TNS could not resolve`              | Incorrect DSN format                               | Use `hostname:port/service_name` format                          |
| `Target table does not exist`                   | Wrong schema prefix or table name                  | Check `ORACLE_SCHEMA` env var; use `SCHEMA.TABLE` format         |
| `mapping_config must contain a 'fields' list`   | Mapping JSON uses `mappings` key instead of `fields`| Use universal mapping format with `fields` array                 |
| `status: failed` with 0 diffs but `only_in_*`   | Row count mismatch between DB and file             | Check WHERE clause filters; verify file has no header/trailer    |
| Reconciliation warns about type mismatch        | Mapping type doesn't match DB column type          | Review the compatibility matrix in the reconciliation report     |
| Connection timeout                              | Network/firewall blocking DB port                  | Verify port 1521 is accessible; check VPN if remote              |

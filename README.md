# Valdo

Automated batch file validation, comparison, and ETL testing tool with multi-database support, REST API, and modern web UI.

**~3,850 unit tests + ~145 E2E tests | 80%+ coverage | ADA/WCAG 2.1 AA compliant**

## Features

### File Validation
- **Multi-format support**: Fixed-width, CSV, TSV, pipe-delimited with auto-detection
- **Multi-record-type files**: Configurable discriminator routes rows to per-type mappings (e.g. ATOCTRAN 100/200/300, TRANERT Header/CUS/ORI/COD)
- **Header/trailer validation**: 7 cross-type checks (count reconciliation, sum matching, companion records, sequence ordering)
- **Business rules engine**: not_empty, regex, numeric, date_format, valid_values, min/max, exact_length
- **Cross-field rules**: IF/THEN validation between fields in the same row
- **Cross-row rules**: unique, sequential, consistent, group_count, group_sum across rows grouped by key
- **PII scrubbing**: Redact field values in HTML/CSV reports by default (`--suppress-pii`); Web UI toggle in Quick Test tab
- **Default values**: `default_value` column in mapping CSV templates, auto-extracted from transformation text
- **Elapsed time reporting**: `elapsed_seconds` field in JSON results, "Elapsed" tile in HTML dashboard
- **Multi-record config wizard**: `valdo generate-multi-record` interactive wizard or non-interactive mode with `--discriminator`/`--type` flags

### Database Integration
- **Pluggable adapters**: Oracle, PostgreSQL, SQLite (configurable via `DB_ADAPTER` env var)
- **Backend-agnostic features (ADR 0022)**: `reconcile`/`reconcile-all`, `extract`, and `db-compare` all run on Oracle, PostgreSQL, **and** SQLite via the shared `get_database_adapter()` factory — the same mapping reconciles correctly against any backend. (`run-tests`' Oracle gate and `generate-oracle-expected` remain Oracle-only by design.)
- **DB-to-file comparison**: Extract from database, compare against batch file
- **Schema reconciliation**: Validate mappings against actual DB schema with drift detection
- **Data extraction**: Export tables/queries to flat files

### ETL Pipeline Testing
- **Gate-based orchestration**: `valdo run-etl-pipeline` with YAML-defined validation gates
- **Per-source validation**: Template expansion for multi-source batch pipelines
- **Blocking/non-blocking gates**: Stop pipeline on critical failures, warn on soft checks
- **Threshold evaluation**: Max errors, max error percentage, min rows

### Data Masking
- **6 strategies**: preserve, preserve_format, deterministic_hash, random_range, redact, fake_name
- **Fixed-width safe**: Preserves exact record lengths
- **Deterministic**: Same input always produces same masked output (referential integrity)

### Security & Compliance
- **Secret vault integration**: env, HashiCorp Vault, Azure Key Vault (`SECRETS_PROVIDER`)
- **Structured audit logging**: Splunk-compatible JSONL with SHA-256 file hashing
- **CODEOWNERS**: Config change approval workflow
- **Config validation CI**: Automated mapping/rules/suite validation on PRs
- **LDAPS login**: Corporate username + password authentication via `ldaps://ldap-wil.bank.internal:636` with group→role mapping; X-API-Key remains supported for service-to-service access

### Web UI
- **4 tabs**: Quick Test, Recent Runs, Mapping Generator, API Tester
- **Dark/light theme**: System preference detection, localStorage persistence
- **Help sidebar**: Searchable usage guide with TOC navigation
- **Downloadable templates**: Sample mapping and rules CSV files
- **ADA/WCAG 2.1 AA**: Color contrast, keyboard navigation, ARIA, reduced motion

### CI Pipeline Integration
- **GitHub Actions**: Reusable composite action
- **Azure DevOps**: Step template
- **GitLab CI**: Extendable job template
- **Docker**: `valdo` entrypoint, `ghcr.io` image
- **Webhook**: Async validation with callback URL

### AI Prompt Library (`prompts/`)
- LLM prompts to generate mapping/rules CSVs from Excel/PDF specification documents
- Works with Copilot, GitLab Duo, Claude, ChatGPT, Gemini
- Handles COBOL picture clauses, IF/ELSE transformations, multi-sheet workbooks

## Documentation

| Guide | Description |
|-------|-------------|
| [Usage & Operations Guide](docs/USAGE_AND_OPERATIONS_GUIDE.md) | Comprehensive guide (2,900+ lines) |
| [Quick Reference](docs/USAGE_GUIDE.md) | CLI commands and API endpoints |
| [Documentation Index](docs/DOCUMENTATION_INDEX.md) | All docs in one place |
| [CI Integration Guide](docs/CI_INTEGRATION_GUIDE.md) | GitHub Actions, Azure, GitLab |
| [Change Management](docs/CHANGE_MANAGEMENT.md) | Config approval workflow |
| [Splunk Setup](docs/splunk-setup.md) | Audit log integration |
| [Installation](docs/INSTALL.md) | Windows, Linux, VSCode setup |

### For agents / AI clients

Wire Claude Desktop, VSCode Copilot Chat, or GitLab Duo to Valdo's
10-tool + 4-prompt MCP surface (BAs: try the `pick_etl_shape`
prompt to get a template recommendation from a free-text problem
description — S7-5):

| Guide | Description |
|-------|-------------|
| [MCP Server Reference](docs/MCP_SERVER.md) | Server-side: auth modes, run-state persistence, table shape |
| [MCP Clients Overview](docs/MCP_CLIENTS.md) | Per-client capability matrix + setup links |
| [VSCode + Copilot Chat](docs/mcp-clients/vscode/vscode-setup.md) | Drop-in `.vscode/mcp.json` + step-by-step BA walkthrough |
| [GitLab Duo Chat](docs/mcp-clients/gitlab-duo/gitlab-duo-setup.md) | Native MCP path + Custom-Tool fallback for older Duo builds |

### For BAs / POs

Start here if you're scoping a new source system or picking a template:

| Guide | Description |
|-------|-------------|
| [Choose Your ETL Shape](docs/etl/CHOOSE_YOUR_SHAPE.md) | Decision tree — which template fits your data (file, DB, fixed-width, CSV, multi-record)? |
| [Mapping Quickstart](docs/MAPPING_QUICKSTART.md) | 5-minute mapping primer |
| [Functionality Matrix](docs/FUNCTIONALITY_MATRIX.md) | Feature-by-feature support reference |

## Quick Start

For Java multi-step ETL regression orchestration, see:
- `docs/PIPELINE_REGRESSION_GUIDE.md`

### First-Time Setup (one command)

**The single, recommended local path** — works on macOS, Linux, RHEL, and WSL/Git Bash. From a fresh clone, one command takes you to a running Valdo on **SQLite with zero external infrastructure** (no Oracle required):

```bash
bash scripts/valdo-setup.sh
```

This detects your OS, finds Python 3.11+, creates/refreshes `.venv`, installs the project + API deps, writes a local `.env` (SQLite defaults, never clobbering an existing one), creates the working directories, runs `alembic upgrade head` against local SQLite, and smoke-checks `valdo info`. It is **idempotent** — safe to re-run.

```bash
# After setup completes:
source .venv/bin/activate        # .venv/Scripts/activate on Git Bash
valdo serve                      # Web UI at http://localhost:8000/ui
```

**Full-stack (Postgres via docker-compose), one command** — preflights Docker, builds and starts the stack, waits for healthy, smoke-checks the health endpoint:

```bash
bash scripts/valdo-setup.sh --env full-stack    # Web UI at http://localhost:8000/ui
bash scripts/valdo-setup.sh --env full-stack --down       # tear it down (keeps DB volume)
bash scripts/valdo-setup.sh --env full-stack --down -v    # tear down + drop the DB volume
```

**INT-region (Oracle) scaffold + validate, one command** — creates/validates `.env.int` from the committed `.env.int.example` template, reports any required vars still set to placeholders, and (only when all are set *and* the Oracle DSN is reachable) runs migrations + a connection smoke:

```bash
bash scripts/valdo-setup.sh --env int           # scaffold .env.int, validate required vars
```

> **Environments:** `--env local` (default, SQLite), `--env full-stack` (docker-compose: Valdo app + Postgres), and `--env int` (INT-region Oracle **scaffold + validate**) are all implemented. There is no live INT environment in this repo, so `--env int` produces/validates the INT config and connects only if the DSN is reachable — otherwise it prints the remaining steps and exits cleanly. See `docs/sprints/SPRINT_11_KICKOFF.md`.

> **Windows (no WSL):** use `scripts/setup_windows.ps1` (PowerShell) or `setup-windows.bat`. The per-OS scripts (`scripts/setup_mac.sh`, `scripts/setup_rhel.sh`, `setup-linux.sh`) remain available but `valdo-setup.sh` is the recommended local entry point.

**Important:** the script resolves the repo root from its own location, so you can run it from anywhere — but navigating to the project root first keeps relative paths predictable.

For full details (Windows, INT/Oracle, VSCode), see `docs/INSTALL.md`.

#### Windows (PowerShell)

Open PowerShell as Administrator (recommended) or ensure you have script execution permissions.

```powershell
# Run the setup script
powershell -ExecutionPolicy Bypass -File scripts/setup_windows.ps1

# Activate the virtual environment
.\.venv\Scripts\Activate.ps1

# Verify installation
valdo --help
```

#### VS Code Users (All OS)

If you use VS Code, you can automatically configure the workspace settings (interpreters, test explorer, tasks).

```bash
# macOS/Linux
bash scripts/setup_vscode.sh

# Windows PowerShell
powershell -ExecutionPolicy Bypass -File scripts/setup_vscode.ps1
```

> Tip: In shells, `\` line continuation must be the **last character** on the line (no trailing spaces).

### CLI Usage

```bash
# Install dependencies (if not using setup scripts)
pip install -r requirements.txt
pip install -e .

# If valdo is not found, use module form:
# .venv/bin/python -m src.main <command>

# Check system info
valdo info

# Detect file format
valdo detect -f data/samples/customers.txt

# Parse file
valdo parse -f data/samples/customers.txt

# Parse with chunked processing (large files)
valdo parse -f data/samples/customers.txt --use-chunked --chunk-size 50000 -o reports/parsed.csv

# Validate file with HTML report
valdo validate -f data/samples/customers.txt -m config/mappings/customer_mapping.json -o reports/validation.html --detailed

# Validate with chunked processing (large files)
valdo validate -f data/samples/customers.txt -m config/mappings/customer_mapping.json --use-chunked -o reports/validation.json

# Strict fixed-width validation (field-level checks)
valdo validate -f data/files/p327_sample_errored.txt -m config/mappings/p327_mapping.json \
  --strict-fixed-width --strict-level format --detailed \
  -o reports/p327_strict_validation.html

# Chunked strict fixed-width validation (now includes field-level checks)
valdo validate -f data/files/p327_sample_errored.txt -m config/mappings/p327_mapping.json \
  --use-chunked --strict-fixed-width --strict-level format --progress \
  -o reports/p327_chunked_strict_validation.html

# Parallel chunked strict validation (multi-core)
valdo validate -f data/files/p327_sample_errored.txt -m config/mappings/p327_mapping.json \
  --use-chunked --chunk-size 50000 --workers 3 --strict-fixed-width --strict-level format --progress \
  -o reports/p327_chunked_strict_parallel_validation.html

# Compare files
valdo compare -f1 file1.txt -f2 file2.txt -k customer_id -o report.html

# Compare with chunked processing (requires keys)
valdo compare -f1 file1.txt -f2 file2.txt -k customer_id --use-chunked --chunk-size 50000 -o report.html

# Validate mapping against database
valdo reconcile -m config/mappings/customer_mapping.json

# Validate all mappings in a directory
valdo reconcile-all -d config/mappings -o reports/reconcile_all.json

# Detect reconciliation drift vs baseline
valdo reconcile-all -d config/mappings -o reports/reconcile_all_new.json \
  --baseline reports/reconcile_all_baseline.json --fail-on-drift

# Extract data from database
valdo extract -t CUSTOMER -o output.txt -l 1000

# Convert business rules template
valdo convert-rules -t config/templates/rules.xlsx -o config/rules.json
```

Chunk-based processing is supported by these CLI commands:
- `parse` (`--use-chunked`, `--chunk-size`)
- `validate` (`--use-chunked`, `--chunk-size`)
- `compare` (`--use-chunked`, `--chunk-size`, **requires** `-k/--keys`)

For deeper tuning/performance guidance, see `docs/CHUNKED_PROCESSING.md`.

### Batch Automation Scripts

These scripts are designed to simplify common tasks. They are located in the `scripts/` directory and should be run from the **project root directory**.

#### 1. Convert Mapping Templates to JSON

This script converts all CSV mapping templates from `mappings/csv/` to JSON configuration files in `config/mappings/`.

**Usage:**

```bash
# Default usage (parses mappings/csv -> config/mappings)
./scripts/run_convert_mappings.sh

# Specify custom input/output directories
./scripts/run_convert_mappings.sh "my/custom/csv_dir" "my/custom/json_dir"

# Force a specific format (e.g., fixed_width or pipe_delimited) for all files
./scripts/run_convert_mappings.sh "mappings/csv" "config/mappings" "fixed_width"
```

> **Note:** Strict mode is enabled. Malformed rows will cause conversion to fail and generate error reports in `reports/template_validation/*.errors.csv`.

#### 2. Convert Business Rules to JSON

This script converts all CSV business rules templates from `rules/csv/` to JSON configuration files in `config/rules/`.

**Usage:**

```bash
# Default usage (parses rules/csv -> config/rules)
./scripts/run_convert_rules.sh

# Specify custom input/output directories
./scripts/run_convert_rules.sh "my/custom/rules_csv" "my/custom/rules_json"
```

#### 3. Validate Data Files

This script validates data files based on a manifest file (CSV). It supports auto-discovery of mappings if not explicitly defined in the manifest.

**Usage:**

```bash
# Default usage (uses config/validation_manifest.csv)
./scripts/run_validate_all.sh

# Use a custom manifest file
./scripts/run_validate_all.sh "path/to/my_manifest.csv"

# Enable auto-discovery fallback (tries to find matching mapping if missing in manifest)
./scripts/run_validate_all.sh "config/validation_manifest.csv" "true"
```

#### 4. Run Regression Workflow (Parse → Validate → Compare)

This script runs an end-to-end regression workflow from a JSON config and writes a summary JSON.

**Usage (Bash):**

```bash
# Use sample config
./scripts/run_regression_workflow.sh

# Custom config + summary output
./scripts/run_regression_workflow.sh \
  config/pipeline/regression_workflow.sample.json \
  reports/regression_workflow/my_summary.json
```

**Usage (PowerShell):**

```powershell
# Use sample config
./scripts/run_regression_workflow.ps1

# Custom config + summary output
./scripts/run_regression_workflow.ps1 `
  -Config config/pipeline/regression_workflow.sample.json `
  -SummaryOut reports/regression_workflow/my_summary.json
```

Config template: `config/pipeline/regression_workflow.sample.json`

Manifest recommendation: **CSV** (not .properties), because QA teams can edit it easily in Excel.

Starter sample files checked in:

- `mappings/csv/mapping_template.sample.csv`
- `rules/csv/rules_template.sample.csv`
- `config/validation_manifest.sample.csv`

Sample manifest columns:

- `data_file` (required)
- `mapping_file` (required)
- `rules_file` (optional)
- `report_file` (optional)
- `chunked` (optional)
- `chunk_size` (optional)

### Business Rule Validation (Build Gate Friendly)

You can run validation with business rules JSON and fail builds when violations are found:

```bash
valdo validate \
  -f data/samples/customers.txt \
  -m config/mappings/customer_mapping.json \
  -r config/rules/p327_business_rules.json \
  -o reports/validation-with-rules.html \
  --detailed
```

### On-Prem Oracle Smoke Test (No Docker Required)

Set your Oracle credentials in `.env`:

```bash
ORACLE_USER=APP_INT
ORACLE_PASSWORD=<password>
ORACLE_DSN=<hostname>:1521/<service_name>
```

Create test objects and run parse -> validate -> load verification:

```bash
# Create test tables in APP_INT/APP_AUDIT
sqlplus system/<sys_password>@<hostname>:1521/<service_name> @scripts/setup_app_test_tables.sql

# Run smoke ETL
python scripts/app_smoke_etl.py
```

### Universal Mapping (New!)

```bash
# Convert Excel template to universal mapping
python src/config/template_converter.py \
  data/mappings/my_template.xlsx \
  config/mappings/my_mapping.json \
  my_mapping_name \
  fixed_width

# Validate mapping
python src/config/universal_mapping_parser.py \
  config/mappings/my_mapping.json
```

### Standardized CSV -> Mapping JSON + Rules JSON

Use standardized CSV templates:
- `config/templates/csv/mapping_template.standard.csv`
- `config/templates/csv/business_rules_template.standard.csv`

Generate JSON artifacts from CSV in one command:

```bash
python scripts/generate_from_csv_templates.py \
  --mapping-csv config/templates/csv/mapping_template.standard.csv \
  --mapping-out config/mappings/customer_batch_universal.json \
  --mapping-name customer_batch_universal \
  --mapping-format pipe_delimited \
  --rules-csv config/templates/csv/business_rules_template.standard.csv \
  --rules-out config/rules/customer_business_rules.json
```

### REST API (New!)

```bash
# Start API server
uvicorn src.api.main:app --reload --port 8000

# Access Swagger UI
open http://localhost:8000/docs

# Example API calls
curl -X POST "http://localhost:8000/api/v1/mappings/upload" \
  -F "file=@template.xlsx" \
  -F "mapping_name=my_mapping"

curl "http://localhost:8000/api/v1/mappings/"
```

## Running Test Suites

Run multiple tests in one command using a YAML suite file.

```bash
# Create YAML suite from Excel template
valdo convert-suite --template config/test_suites/my_suite.xlsx
# Run the suite
valdo run-tests --suite config/test_suites/my_suite.yaml --params "run_date=20260301"
```

For full suite YAML format and test type reference, see the [Test Suite Orchestration](#test-suite-orchestration) section in `docs/USAGE_GUIDE.md`.

## Project Structure

```
valdo/
├── src/                  # Source code
│   ├── api/             # REST API (FastAPI)
│   │   ├── main.py      # API application
│   │   ├── models/      # Pydantic models
│   │   └── routers/     # API endpoints
│   ├── parsers/         # File parsers
│   ├── database/        # DB connectivity (Oracle/PostgreSQL/SQLite via adapters/)
│   ├── validators/      # Validation logic
│   ├── comparators/     # File comparison
│   ├── config/          # Configuration management
│   │   ├── universal_mapping_parser.py  # Universal parser
│   │   └── template_converter.py        # Excel/CSV converter
│   ├── contracts/       # Typed contracts (pipeline/workflow)
│   ├── services/        # Shared business services (CLI/API parity)
│   ├── workflows/       # Shared workflow engine used by scripts
│   ├── reports/         # Unified reporting namespace
│   │   ├── renderers/   # HTML/report renderers
│   │   ├── adapters/    # Result adapters (standard/chunked)
│   │   └── contracts/   # Reporting contracts/schemas
│   ├── reporters/       # Backward-compatible shims (deprecated)
│   ├── reporting/       # Backward-compatible shims (deprecated)
│   └── utils/           # Utilities
├── tests/               # Test suite
├── config/              # Configuration files
│   ├── schemas/         # JSON schemas
│   ├── templates/       # Mapping templates
│   └── mappings/        # Universal mappings
├── data/                # Data directory
│   └── mappings/        # CSV/Excel mapping templates for new integrations
├── scripts/             # Utility scripts (smoke ETL, setup SQL, helpers)
├── uploads/             # API file uploads
├── logs/                # Log files
└── docs/                # Documentation
    ├── UNIVERSAL_MAPPING_GUIDE.md
    └── architecture.md
```

## Prerequisites

- **Python**: 3.9 or higher
- **pip**: Latest version
- **Oracle Database** access (on-prem, container, or managed)

> Note: the project uses `python-oracledb` and works in **thin mode** by default (no Oracle Instant Client required for basic connectivity).

## Installation

### 1. Clone the Repository

```bash
git clone <repository-url>
cd valdo
```

### 2. Set Up Virtual Environment

```bash
# Create virtual environment
python -m venv venv

# Activate virtual environment
# On Windows:
venv\Scripts\activate
# On Linux/Mac:
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
pip install -e .  # enables valdo CLI command
```

### 4. (Optional) Install Oracle Instant Client

#### Windows

1. Download Oracle Instant Client from [Oracle website](https://www.oracle.com/database/technologies/instant-client/downloads.html)
2. Extract to `C:\oracle\instantclient_19_x`
3. Add to PATH:
   ```cmd
   setx PATH "%PATH%;C:\oracle\instantclient_19_x"
   ```

#### Linux

```bash
# Download and extract
wget https://download.oracle.com/otn_software/linux/instantclient/instantclient-basic-linux.x64-19.x.x.x.zip
unzip instantclient-basic-linux.x64-19.x.x.x.zip -d /opt/oracle

# Set environment variables
export ORACLE_HOME=/opt/oracle/instantclient_19_x
export LD_LIBRARY_PATH=$ORACLE_HOME:$LD_LIBRARY_PATH
export PATH=$ORACLE_HOME:$PATH

# Add to ~/.bashrc for persistence
echo 'export ORACLE_HOME=/opt/oracle/instantclient_19_x' >> ~/.bashrc
echo 'export LD_LIBRARY_PATH=$ORACLE_HOME:$LD_LIBRARY_PATH' >> ~/.bashrc
echo 'export PATH=$ORACLE_HOME:$PATH' >> ~/.bashrc
```

#### macOS

```bash
# Download and extract
curl -O https://download.oracle.com/otn_software/mac/instantclient/instantclient-basic-macos.x64-19.x.x.x.zip
unzip instantclient-basic-macos.x64-19.x.x.x.zip -d ~/oracle

# Set environment variables
export ORACLE_HOME=~/oracle/instantclient_19_x
export DYLD_LIBRARY_PATH=$ORACLE_HOME:$DYLD_LIBRARY_PATH
export PATH=$ORACLE_HOME:$PATH

# Add to ~/.zshrc or ~/.bash_profile
echo 'export ORACLE_HOME=~/oracle/instantclient_19_x' >> ~/.zshrc
echo 'export DYLD_LIBRARY_PATH=$ORACLE_HOME:$DYLD_LIBRARY_PATH' >> ~/.zshrc
echo 'export PATH=$ORACLE_HOME:$PATH' >> ~/.zshrc
```

### 5. Configure Environment Variables

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your Oracle credentials
# ORACLE_USER=your_username
# ORACLE_PASSWORD=your_password
# ORACLE_DSN=hostname:port/service_name
```

## Configuration

### Database Configuration

Edit `config/dev.json` for development settings:

```json
{
  "database": {
    "username": "your_user",
    "dsn": "localhost:1521/XEPDB1",
    "encoding": "UTF-8"
  }
}
```

### Column Mappings

Create mapping files in `config/mappings/` to define file-to-database column mappings:

```json
{
  "file_column_1": "DB_COLUMN_1",
  "file_column_2": "DB_COLUMN_2"
}
```

## Usage

### Basic File Parsing

```python
from src.parsers.pipe_delimited_parser import PipeDelimitedParser

parser = PipeDelimitedParser("data/samples/input.txt")
df = parser.parse()
print(df.head())
```

### Database Connection

```python
from src.database.connection import OracleConnection
from src.database.query_executor import QueryExecutor

# Using environment variables
conn = OracleConnection.from_env()
executor = QueryExecutor(conn)

df = executor.execute_query("SELECT * FROM my_table WHERE rownum <= 10")
```

### File Comparison

```python
from src.comparators.file_comparator import FileComparator
from src.reporters.html_reporter import HTMLReporter

comparator = FileComparator(df1, df2, key_columns=["id"])
results = comparator.compare()

reporter = HTMLReporter()
reporter.generate(results, "reports/comparison_report.html")
```

### Universal Mapping Structure

```python
from src.config.universal_mapping_parser import UniversalMappingParser
from src.config.template_converter import TemplateConverter

# Convert Excel template to universal mapping
converter = TemplateConverter()
mapping = converter.from_excel(
    "data/mappings/my_template.xlsx",
    mapping_name="my_mapping",
    file_format="fixed_width"
)
converter.save("config/mappings/my_mapping.json")

# Use universal mapping
parser = UniversalMappingParser(mapping_path="config/mappings/my_mapping.json")

# Get field positions for fixed-width files
if parser.get_format() == 'fixed_width':
    positions = parser.get_field_positions()
    # Returns: [('FIELD1', 0, 10), ('FIELD2', 10, 25), ...]

# Get column names for delimited files
else:
    columns = parser.get_column_names()
    delimiter = parser.get_delimiter()

# Validate mapping
validation = parser.validate_schema()
if validation['valid']:
    print("✓ Mapping is valid")
```

### REST API Usage

```python
import requests

# Upload Excel template
with open('template.xlsx', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/api/v1/mappings/upload',
        files={'file': f},
        params={'mapping_name': 'my_mapping', 'file_format': 'fixed_width'}
    )
mapping = response.json()

# List all mappings
response = requests.get('http://localhost:8000/api/v1/mappings/')
mappings = response.json()

# Parse file
with open('data.txt', 'rb') as f:
    response = requests.post(
        'http://localhost:8000/api/v1/files/parse',
        files={'file': f},
        json={'mapping_id': 'my_mapping'}
    )
result = response.json()

# Compare files
with open('file1.txt', 'rb') as f1, open('file2.txt', 'rb') as f2:
    response = requests.post(
        'http://localhost:8000/api/v1/files/compare',
        files={'file1': f1, 'file2': f2},
        json={'mapping_id': 'my_mapping', 'key_columns': ['id']}
    )
comparison = response.json()
```

## Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/unit/test_parsers.py

# Run with verbose output
pytest -v
```

## Development

### Code Formatting

```bash
# Format code with Black
black src/ tests/

# Check formatting
black --check src/ tests/
```

### Linting

```bash
# Run flake8
flake8 src/ tests/

# Run pylint
pylint src/
```

### Type Checking

```bash
# Run mypy
mypy src/
```

## Logging

Logs are written to the `logs/` directory. Configure logging in your code:

```python
from src.utils.logger import setup_logger
import logging

logger = setup_logger("my_module", log_dir="logs", level=logging.INFO)
logger.info("Processing started")
```

## Troubleshooting

### Oracle Instant Client Issues

**Error: DPI-1047: Cannot locate a 64-bit Oracle Client library**

- Ensure Oracle Instant Client is installed
- Verify PATH/LD_LIBRARY_PATH includes Instant Client directory
- Restart terminal after setting environment variables

**Error: ORA-12154: TNS:could not resolve the connect identifier**

- Check DSN format: `hostname:port/service_name`
- Verify database is accessible from your network
- Check TNS_ADMIN environment variable if using tnsnames.ora

### Import Errors

- Ensure virtual environment is activated
- Verify all dependencies are installed: `pip install -r requirements.txt`
- Check Python version: `python --version` (should be 3.9+)

## Contributing

1. Create a feature branch
2. Make your changes
3. Run tests and linting
4. Submit a merge request

## License

Internal use only.

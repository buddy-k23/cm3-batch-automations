# Valdo — Claude Instructions

## Project Overview

**Valdo** is a FastAPI + Python CLI tool for validating, comparing, masking, and inspecting batch files against mapping schemas and business rules. It supports fixed-width, CSV, TSV, and pipe-delimited formats, with Oracle database integration for extraction, comparison, and schema reconciliation.

**Key entry points:**
- CLI: `src/main.py` (Click commands — `valdo validate`, `valdo compare`, `valdo mask`, etc.)
- API: `src/api/main.py` (FastAPI app at port 8000)
- Web UI: `src/reports/static/ui.html` (served at `/ui`)

**Test suite:** 1024 unit tests + 46 E2E Playwright tests = 1070 total, 84% coverage

**Active branch:** `main`

---

## CLI Commands

| Command | Description |
|---------|-------------|
| `valdo validate` | Validate a batch file against a mapping |
| `valdo validate --multi-record` | Validate a multi-record-type file (header/detail/trailer) |
| `valdo compare` | Compare two batch files row-by-row |
| `valdo db-compare` | Compare Oracle DB extract against a file |
| `valdo extract` | Extract Oracle table/query to flat file |
| `valdo reconcile` | Validate mapping fields against Oracle schema |
| `valdo reconcile-all` | Bulk reconcile all mappings in a directory |
| `valdo infer-mapping` | Auto-generate mapping draft from sample file |
| `valdo mask` | Mask PII in batch files (6 strategies) |
| `valdo parse` | Parse and inspect a batch file |
| `valdo run-etl-pipeline` | Execute ETL pipeline validation gates from YAML config |
| `valdo serve` | Start the FastAPI server |
| `valdo schedule` | List/run scheduled test suites |
| `valdo submit-task` | Submit a canonical task request |

---

## Key Features

### Validation & Rules Engine
- **Field validation:** not_empty, regex, numeric, date_format, valid_values, min/max_value, exact_length, min_length
- **Cross-field:** validate relationships between fields in the same row
- **Cross-row:** validate across rows grouped by key columns — unique, unique_composite, consistent, sequential, group_count, group_sum
- **Multi-record-type:** validate files containing interleaved record types (e.g. header/detail/trailer) with per-type mappings, cardinality constraints, and 7 cross-type checks (required_companion, header_trailer_count, header_trailer_sum, header_detail_consistent, header_trailer_match, type_sequence, expect_count)
- **PII scrubbing:** `--suppress-pii` flag redacts field values in reports (default: enabled)

### Database Integration
- Oracle via `oracledb` thin mode (configurable via `ORACLE_*` env vars)
- Pluggable secrets provider: env, HashiCorp Vault, Azure Key Vault (`SECRETS_PROVIDER` env var)
- Schema reconciliation and drift detection
- Run history stored in Oracle tables

### Web UI (4 tabs)
- **Quick Test:** upload, validate, compare with metric cards
- **Recent Runs:** sortable table with auto-refresh
- **Mapping Generator:** upload templates, JSON preview, downloadable sample templates
- **API Tester:** method selector, request builder, suite runner
- Dark/light theme toggle, help sidebar with searchable usage guide
- ADA/WCAG 2.1 AA compliant (contrast, keyboard, ARIA, reduced motion)

### CI Pipeline Integration
- GitHub Actions: `.github/actions/cm3-validate/action.yml`
- Azure DevOps: `ci/templates/azure-valdo-validate.yml`
- GitLab CI: `ci/templates/gitlab-valdo-validate.yml`
- Docker: `Dockerfile` with `valdo` entrypoint
- Webhook: `POST /api/v1/webhook/validate` for async validation

### ETL Pipeline Runner (`valdo run-etl-pipeline`)
Multi-gate validation pipelines defined in YAML. Supports template variable expansion, blocking/non-blocking gates, per-step thresholds, and CI/CD integration (exits non-zero on failure).

### Data Masking (`valdo mask`)
6 strategies: preserve, preserve_format, deterministic_hash, random_range, redact, fake_name

### Notifications
Email (SMTP), Teams webhook, Slack webhook — configurable per suite in YAML

### AI Prompt Library (`prompts/`)
Reusable LLM prompts to generate Valdo mapping/rules CSVs from specification documents (Excel, PDF, COBOL copybooks). Works with Copilot, GitLab Duo, Claude, ChatGPT.

---

## Final Implementation Process (Per Issue)

Follow these 12 steps in order for every feature or bug fix:

| # | Step | What happens |
|---|------|--------------|
| 1 | **Explore** | Read relevant source files — understand existing patterns before touching code |
| 2 | **Task list** | Break issue into concrete subtasks using TodoWrite |
| 3 | **Tests first** | Write unit tests that FAIL before writing implementation |
| 4 | **Implement** | Write minimal code to make the tests pass |
| 5 | **Docstrings** | Add Google-style docstrings to all new public functions and classes |
| 6 | **Sphinx RST** | If new public modules added, register them in `docs/sphinx/modules.rst` |
| 7 | **Markdown docs** | Update `docs/USAGE_AND_OPERATIONS_GUIDE.md` and `docs/DOCUMENTATION_INDEX.md` for user-facing changes |
| 8 | **Tests + Sphinx build** | `pytest` all pass ≥80% coverage; `cd docs/sphinx && make html` succeeds |
| 9 | **Spec review** | Tick off every acceptance criterion from the issue |
| 10 | **Code quality** | No hardcoded paths, no `shell=True`, no magic numbers, follows layered architecture |
| 11 | **Architecture review** | Check new code against the 5 architecture principles below |
| 12 | **Commit & push** | `git commit -m "feat/fix/docs(scope): description"` conventional commits |

---

## 5 Architecture Principles

1. **No orchestration sprawl** — CLI commands go through `src/commands/*`, API calls go through `src/api/routers/*` → `src/services/*`. Never add business logic directly to `src/main.py` or routers.

2. **No `shell=True`** — All subprocess calls use argument arrays (`shell=False`). Pipeline runner and test runners must not accept config-injected command strings without validation.

3. **Consistent output contracts** — Validate, compare, and parse produce the same output format whether running chunked or non-chunked. `.json` → machine JSON, `.html` → HTML report.

4. **Clean layer separation** — The stack is: CLI/API → Commands/Routers → Services → Parsing/Validation/Mapping/DB/Reporting. No layer should import from a layer above it.

5. **No hardcoded paths** — Use `Path(__file__).parent` for relative paths. Config-driven directories (uploads, reports, mappings, rules) must come from settings, not string literals.

6. **Manageable file sizes** — Keep individual files under 500 lines where practical. Split large files:
   - `ui.html` (HTML only) + `ui.css` (styles) + `ui.js` (JavaScript) — never combine back
   - Python files: one class/concern per file, new commands go in `src/commands/`, not inline in `src/main.py`
   - `src/main.py` is a thin CLI registration layer — all logic must be in `src/commands/` or `src/services/`
   - Before modifying a file, check if the change belongs in an existing split file (e.g. CSS changes go in `ui.css`, not `ui.html`)

---

## Test Commands

```bash
# Run full unit suite
pytest tests/unit/ -q

# Run with coverage
pytest tests/unit/ --cov=src --cov-report=term-missing -q

# Run E2E tests (requires running server)
pytest tests/e2e/ --browser chromium

# Build Sphinx docs
cd docs/sphinx && make html
```

**Coverage target:** ≥80%

---

## Commit Convention

```
feat(scope): short description
fix(scope): short description
docs(scope): short description
test(scope): short description
refactor(scope): short description
```

Examples:
- `feat(rules): add cross-row validation engine`
- `fix(ui): fix template download buttons`
- `docs(guide): add database integration section`

---

## Key Directories

```
src/
  api/routers/       # FastAPI route handlers (thin — delegate to services)
  commands/          # CLI command handlers (thin — delegate to services)
  services/          # Business logic layer
  validators/        # Rule engine, field validator, cross-row validator, multi-record & cross-type validators
    multi_record_validator.py  # Orchestrates per-type + cross-type validation
    cross_type_validator.py    # 7 cross-record-type checks
  config/            # Mapping/rules converters, DB config, Pydantic models
    multi_record_config.py     # Pydantic models for multi-record YAML config
  database/          # Oracle connection, reconciliation, run history
    adapters/        # Pluggable DB adapters (oracle, postgresql, sqlite)
  pipeline/          # Suite runner, suite config, ETL pipeline runner
  parsers/           # Fixed-width, pipe-delimited, CSV/TSV parsers
  reports/
    renderers/       # HTML/JSON report generators
    static/          # Web UI (ui.html), sample templates
  utils/             # Audit logger, secrets provider, config validator
config/
  mappings/          # Generated mapping JSON files
  rules/             # Generated rules JSON files
  multi-record/      # Multi-record-type YAML configs (e.g. ATOCTRAN, TRANERT)
  suites/            # Test suite YAML definitions
  masking/           # Masking rules JSON
  pipelines/         # ETL pipeline YAML definitions
  generated-mappings/  # (gitignored) locally generated mapping JSON from Excel specs
  generated-rules/     # (gitignored) locally generated rules JSON from Excel specs
prompts/             # AI prompt library for LLM-assisted config generation
docs/
  USAGE_AND_OPERATIONS_GUIDE.md  # Comprehensive guide (2400+ lines)
  USAGE_GUIDE.md                 # Quick reference
  DOCUMENTATION_INDEX.md         # Index of all docs
  CI_INTEGRATION_GUIDE.md        # CI pipeline setup
  CHANGE_MANAGEMENT.md           # Config approval workflow
  splunk-setup.md                # Audit log integration
  sphinx/                        # Auto-generated API reference
tests/
  unit/              # 1024 unit tests (pytest)
  e2e/               # 46 Playwright E2E tests
ci/
  templates/         # Azure DevOps + GitLab CI reusable templates
.github/
  actions/           # GitHub Actions composite action
  workflows/         # CI workflows (test, docker publish, config validation)
  CODEOWNERS         # Config change approval requirements
```

---

## Docstring Style

Google-style for all public functions and classes:

```python
def upload_rules_template(file: UploadFile, rules_name: str = None) -> dict:
    """Upload Excel/CSV rules template and convert to rules JSON.

    Args:
        file: The uploaded template file (.xlsx, .xls, or .csv).
        rules_name: Optional name for the output rules config.
            Defaults to the uploaded filename stem.

    Returns:
        Dict with keys: rules_id, filename, size, message.

    Raises:
        HTTPException: 400 if file extension is not supported.
        HTTPException: 500 if conversion fails.
    """
```

---

## Database Configuration

Configurable via environment variables (defaults for local dev):

| Variable | Default | Description |
|----------|---------|-------------|
| `ORACLE_USER` | `CM3INT` | Oracle username |
| `ORACLE_PASSWORD` | (required) | Oracle password |
| `ORACLE_DSN` | `localhost:1521/FREEPDB1` | Oracle connection string |
| `ORACLE_SCHEMA` | = `ORACLE_USER` | Schema prefix for SQL |
| `DB_ADAPTER` | `oracle` | Database backend: `oracle`, `postgresql`, or `sqlite` |
| `SECRETS_PROVIDER` | `env` | `env`, `vault`, or `azure` |
| `API_KEYS` | (none) | API auth keys (`key:role` format) |
| `AUDIT_LOG_PATH` | `logs/audit.jsonl` | Structured audit log path |

---

## Open Issues

No critical open issues. Issue #151 (pluggable database adapters) has been
completed -- Oracle, PostgreSQL, and SQLite adapters are now supported.

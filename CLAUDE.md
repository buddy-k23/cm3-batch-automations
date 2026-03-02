# CM3 Batch Automations — Claude Instructions

## Project Overview

FastAPI + Python CLI tool for validating, comparing, and parsing batch files against mapping schemas and business rules. Used by BA/QA/dev teams to validate Shaw→C360 data migrations.

**Key entry points:**
- CLI: `src/main.py` (Click commands)
- API: `src/api/main.py` (FastAPI app at port 8000)
- Web UI: `src/reports/static/ui.html` (served at `/ui`)

**Active branch:** `feature/database-validations-pilot`

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
| 7 | **Markdown docs** | Update `docs/USAGE_GUIDE.md` and `docs/DOCUMENTATION_INDEX.md` for user-facing changes |
| 8 | **Tests + Sphinx build** | `pytest` all pass ≥80% coverage; `cd docs/sphinx && make html` succeeds |
| 9 | **Spec review** | Tick off every acceptance criterion from the issue |
| 10 | **Code quality** | No hardcoded paths, no `shell=True`, no magic numbers, follows layered architecture |
| 11 | **Architecture review** | Check new code against the 5 architecture principles below |
| 12 | **Commit & push** | `git commit -m "feat/fix/docs(scope): description"` conventional commits |

---

## 5 Architecture Principles

Check every PR against these (from `docs/ARCHITECTURE_REVIEW_2026-02-20.md`):

1. **No orchestration sprawl** — CLI commands go through `src/commands/*`, API calls go through `src/api/routers/*` → `src/services/*`. Never add business logic directly to `src/main.py` or routers.

2. **No `shell=True`** — All subprocess calls use argument arrays (`shell=False`). Pipeline runner and test runners must not accept config-injected command strings without validation.

3. **Consistent output contracts** — Validate, compare, and parse produce the same output format whether running chunked or non-chunked. `.json` → machine JSON, `.html` → HTML report.

4. **Clean layer separation** — The stack is: CLI/API → Commands/Routers → Services → Parsing/Validation/Mapping/DB/Reporting. No layer should import from a layer above it.

5. **No hardcoded paths** — Use `Path(__file__).parent` for relative paths. Config-driven directories (uploads, reports, mappings, rules) must come from settings, not string literals.

---

## Test Commands

```bash
# Run full unit suite (ignore 3 known-broken pre-existing files)
pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py -q

# Run with coverage
pytest tests/unit/ \
  --ignore=tests/unit/test_contracts_pipeline.py \
  --ignore=tests/unit/test_pipeline_runner.py \
  --ignore=tests/unit/test_workflow_wrapper_parity.py \
  --cov=src --cov-report=term-missing -q

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
- `feat(api): add POST /api/v1/rules/upload endpoint`
- `fix(validation): resolve fixed-width genericity gaps`
- `docs(usage): update USAGE_GUIDE for mapping generator tab`

---

## Key Directories

```
src/
  api/routers/       # FastAPI route handlers (thin — delegate to services)
  commands/          # CLI command handlers (thin — delegate to services)
  services/          # Business logic layer
  config/            # Mapping/rules converters and parsers
  reports/static/    # Web UI (ui.html, chart.umd.min.js)
config/
  mappings/          # Generated mapping JSON files
  rules/             # Generated rules JSON files
docs/
  USAGE_GUIDE.md     # Primary user-facing docs — update for every user-visible change
  DOCUMENTATION_INDEX.md  # Index of all docs
  sphinx/            # Auto-generated API reference
tests/
  unit/              # All unit tests (pytest)
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

## Oracle DB (local dev)

- Username: `CM3INT`, DSN: `localhost:1521/FREEPDB1`
- Credentials in `.env` (gitignored) — recreate if `/tmp` is cleared
- 17 tables: SHAW_SRC_P327, SHAW_SRC_ATOCTRAN, SHAW_SRC_EAC, etc.

## Mapping Files (real data)

- Excel: `/Users/buddy/Downloads/c360-automations-main/mappings/`
- JSON: `/Users/buddy/Downloads/c360-automations-main/config/mappings/`

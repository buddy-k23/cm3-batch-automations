# Pipeline Regression Testing Guide (Java ETL + CM3 Validator)

This guide shows how to use Valdo as a regression framework around an existing multi-step Java batch process.

## 1) Target Architecture

## 0) cm3int Sample Schema + Test Data Setup

Use the provided SQL to create regression sample tables and rows in `cm3int`:

- `sql/cm3int/setup_cm3int_regression_samples.sql`

Example run (SQL*Plus):

```bash
sqlplus user/password@dsn @sql/cm3int/setup_cm3int_regression_samples.sql
```

This creates source-system sample tables:
- `cm3int.src_a_accounts`, `cm3int.src_a_balances`
- `cm3int.src_b_accounts`, `cm3int.src_b_balances`

It inserts both:
- positive rows (expected to pass)
- negative rows (expected to fail rules/quality checks)

Your process:
1. Source sends pipe/fixed input files
2. SQL*Loader loads source-prefixed Oracle tables
3. Java batch transforms table data to target files (e.g. P327)
4. Multiple target files per source system

CM3 integration:
- Run contract checks on outputs (`validate`, strict mode, business rules)
- Optionally compare current outputs against baseline (`compare`)
- Use `run-pipeline` to orchestrate stage-level gates per source system

## 2) Pipeline Profile

Use these starter profiles:
- `config/pipeline/source_profile.sample.json` (generic)
- `config/pipeline/source_profile.SRC_A.sample.json`
- `config/pipeline/source_profile.SRC_B.sample.json`

Key sections:
- `source_system`: logical source key (e.g. `SRC_A`)
- `stages.ingest`: pre-load checks (optional command)
- `stages.sqlloader`: SQL*Loader log evaluation with thresholds
  - `log_file`
  - `max_rejected`
  - `max_discarded`
- `stages.java_batch`: invoke Java batch command
- `stages.output_validation`: target-file regression suite
  - `targets[]` with file/mapping/rules/strict/baseline metadata

## 3) Commands

### Validate profile with dry-run

```bash
python -m src.main run-pipeline \
  --config config/pipeline/source_profile.sample.json \
  --dry-run \
  -o reports/pipeline_dryrun.json \
  --summary-md reports/pipeline_dryrun.md
```

### Generate expected files directly from Oracle transformation SQL

Use manifest:
- `config/pipeline/oracle_expected_manifest.sample.json`
- query files in `config/queries/cm3int/`

Dry-run:

```bash
python -m src.main generate-oracle-expected \
  --manifest config/pipeline/oracle_expected_manifest.sample.json \
  --dry-run \
  -o reports/oracle_expected_dryrun.json
```

Real run (requires ORACLE_USER/ORACLE_PASSWORD/ORACLE_DSN env vars):

```bash
export ORACLE_USER=...
export ORACLE_PASSWORD=...
export ORACLE_DSN=...
python -m src.main generate-oracle-expected \
  --manifest config/pipeline/oracle_expected_manifest.sample.json \
  --run \
  -o reports/oracle_expected_run.json
```

### Execute pipeline stages

```bash
python -m src.main run-pipeline \
  --config config/pipeline/source_profile.sample.json \
  --run \
  -o reports/pipeline_run.json \
  --summary-md reports/pipeline_run.md
```

Exit code semantics:
- `0` => all enabled stages passed
- `1` => one or more stages failed (CI gate fail)

Regression mapping/rules added for cm3int samples:
- `config/mappings/p327_cm3int_regression_mapping.json`
- `config/rules/p327_cm3int_regression_rules.json`

## 4) Output Validation Target Example

```json
{
  "name": "p327",
  "file": "outputs/current/SRC_A/p327.txt",
  "mapping": "config/mappings/P327_full_in_sheet_order.json",
  "rules": "config/rules/p327_business_rules.json",
  "strict_fixed_width": true,
  "strict_level": "all",
  "report": "reports/SRC_A_p327_validation.html",
  "baseline_file": "outputs/baseline/SRC_A/p327.txt",
  "compare_report": "reports/SRC_A_p327_compare.html"
}
```

Behavior:
- Runs `validate` for each target
- Optionally runs `compare` when `baseline_file` exists
- Aggregates pass/fail by target and stage

## 5) SQL*Loader Stage Behavior

The SQL*Loader adapter parses log counters:
- rows loaded
- rows rejected due to data errors
- rows discarded due to WHEN clauses

Stage fails when:
- `rows_rejected > max_rejected`
- or `rows_discarded > max_discarded`

This gives deterministic quality gates before Java ETL output checks.

## 6) CI Integration Pattern

Recommended CI steps:
1. Java build + batch run
2. `run-pipeline --run`
3. Upload reports on failure (or always):
   - `reports/*.html`
   - `reports/*_compare.html`
   - `reports/pipeline_run.json`
   - `reports/pipeline_run.md`

A matrix-based GitHub Actions starter is provided:
- `.github/workflows/pipeline-regression.yml`

It runs pipeline profile dry-runs in parallel per source profile and uploads artifacts for each source.

## 7) Regression Strategy

- Per PR: one source-system smoke profile
- Nightly: full source-system matrix and baseline compares
- Release: strict + business-rules + compare for all critical targets

## 8) Troubleshooting

- Profile fails validation: check required stage keys and target `file/mapping` fields
- SQL*Loader stage fails: inspect `log_file` and threshold settings
- Output validation fails: open generated HTML report and strict CSV overflow file (if present)
- Compare fails: ensure baseline output exists and is aligned to same mapping/version

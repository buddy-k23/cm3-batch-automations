# Great Expectations (GE) - Checkpoint 1 Guide (BA / Non-Developer Friendly)

This guide explains how to run **Great Expectations Checkpoint 1** in Valdo with minimal/no coding.

Checkpoint 1 focuses on practical regression controls:

- Schema / column order checks
- Required field non-null checks
- Key uniqueness checks
- Allowed values checks
- Numeric range checks
- Row-count guardrails

---

## 1) Environment setup (one-time)

Because GE support is best on Python 3.12, use the project's 3.12 environment.

### Switch to Python 3.12 env

```bash
cd /Users/buddy/.openclaw/workspace/cm3-batch-automations
source scripts/env-switch.sh 312
```

### Confirm version

```bash
python --version
# expected: Python 3.12.x
```

---

## 2) Config files you edit (CSV, no Python required)

Use these templates:

- `config/templates/csv/gx_checkpoint1_targets_template.csv`
- `config/templates/csv/gx_checkpoint1_expectations_template.csv`

Example files:

- `config/gx/targets.sample.csv`
- `config/gx/expectations.sample.csv`
- `config/gx/targets.p327_sample.csv`
- `config/gx/expectations.p327_sample.csv`

---

## 3) Targets CSV explained

File: `targets_*.csv`

Columns:

- `target_id` (required): unique dataset name
- `data_file` (required): input file path
- `delimiter`: usually `|` for delimited files
- `has_header`: `true`/`false`
- `mapping_file`: mapping JSON path (recommended)
- `key_columns`: pipe-separated keys (example: `ACCT-NUM|LOCATION-CODE`)
- `required_columns`: pipe-separated required columns
- `enabled`: `true`/`false`

---

## 4) Expectations CSV explained

File: `expectations_*.csv`

Columns:

- `target_id` (required): must match targets CSV
- `expectation_type` (required)
- `column`: for column-level checks
- `column_list`: ordered columns separated by `|`
- `value_set`: allowed values separated by `|`
- `min_value`, `max_value`: range or row-count boundaries
- `mostly`: optional tolerance (for example `0.99`)
- `notes`: free-form business note
- `enabled`: `true`/`false`

Supported `expectation_type` values:

- `expect_table_columns_to_match_ordered_list`
- `expect_column_values_to_not_be_null`
- `expect_column_values_to_be_unique`
- `expect_column_values_to_be_in_set`
- `expect_column_values_to_be_between`
- `expect_table_row_count_to_be_between`

---

## 5) Run commands

## Option A: Simple wrapper script (recommended)

```bash
./scripts/run-gx-checkpoint1.sh \
  config/gx/targets.sample.csv \
  config/gx/expectations.sample.csv \
  reports/gx_summary.json
```

## Option B: Full CLI command (more outputs)

```bash
python -m src.main gx-checkpoint1 \
  --targets config/gx/targets.sample.csv \
  --expectations config/gx/expectations.sample.csv \
  --output reports/gx_summary.json \
  --csv-output reports/gx_summary.csv \
  --html-output reports/gx_summary.html
```

---

## 6) Output files you get

- **JSON** (`--output`): full technical details from GE
- **CSV** (`--csv-output`): flattened BA-friendly results, one row per expectation
- **HTML** (`--html-output`): quick human-readable summary table

Pass/fail behavior:

- If any enabled expectation fails, command exits non-zero (CI friendly)

---

## 7) Example: P327 sample

```bash
python -m src.main gx-checkpoint1 \
  --targets config/gx/targets.p327_sample.csv \
  --expectations config/gx/expectations.p327_sample.csv \
  --output reports/gx_p327_sample_200k_summary.json \
  --csv-output reports/gx_p327_sample_200k_summary.csv \
  --html-output reports/gx_p327_sample_200k_summary.html
```

---

## 8) Common troubleshooting

### Error: "Great Expectations is not installed"

You are likely in the wrong environment. Switch first:

```bash
source scripts/env-switch.sh 312
```

### Error: module path / import issues

Run through `python -m src.main ...` from repo root.

### Fixed-width file parsed incorrectly

Make sure `mapping_file` points to the correct fixed-width mapping with `fields` + `length` metadata.

### Report says expectation failed unexpectedly

Check:
- wrong `column` spelling
- wrong delimiter or header setting
- row-count bounds too strict

---

## 9) Non-developer operating checklist

1. Open `targets` CSV and confirm file path + mapping path.
2. Open `expectations` CSV and confirm enabled rules.
3. Run command from section 5.
4. Open HTML summary.
5. If failed, inspect CSV rows where `success = False`.
6. Update CSV rules and rerun.

---

## 10) Quick command cheat sheet

```bash
# Activate GE-ready environment
source scripts/env-switch.sh 312

# Run with JSON only
python -m src.main gx-checkpoint1 --targets <targets.csv> --expectations <expectations.csv> --output <summary.json>

# Run with JSON + CSV + HTML
python -m src.main gx-checkpoint1 --targets <targets.csv> --expectations <expectations.csv> --output <summary.json> --csv-output <summary.csv> --html-output <summary.html>

# Return to existing 3.14 environment
source scripts/env-switch.sh 314
```

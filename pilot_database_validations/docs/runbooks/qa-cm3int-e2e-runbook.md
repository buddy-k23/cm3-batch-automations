# QA Runbook: CM3INT End-to-End Validation

## Purpose
Run a full Oracle-backed E2E validation flow for CM3INT and generate wave-style reporting artifacts.

## One-command execution
```bash
/Users/buddy/.openclaw/workspace/database-validations/scripts/run_qa_cm3int_e2e.sh
```

## What it does
1. Connects to Oracle (CM3INT) using app Python environment.
2. Creates/loads sample data (100 accounts) in demo tables.
3. Runs source query for multi-transaction output.
4. Generates fixed-width final file.
5. Validates file row-by-row against source-derived expected values.
6. Produces wave-style artifacts (summary, telemetry, promotion evidence).

## Expected success criteria
- `generated/e2e_cm3int/summary.txt` shows `Result: PASS`
- `summary-report.json` shows `status: SUCCESS`
- `promotion-evidence.json` shows `decision: PASS`

## Primary outputs
- `generated/e2e_cm3int/source_query_output.csv`
- `generated/e2e_cm3int/final_output.txt`
- `generated/e2e_cm3int/validation_results.csv`
- `generated/e2e_cm3int/wave_artifacts/summary-report.json`
- `generated/e2e_cm3int/wave_artifacts/telemetry.json`
- `generated/e2e_cm3int/wave_artifacts/promotion-evidence.json`

## Troubleshooting
- Oracle connection issue: verify app venv + ORACLE_* env values in app config.
- Python dependency issue: ensure `oracledb` is available in app venv.
- Non-zero failures: inspect `validation_results.csv` and rerun with same inputs for deterministic replay.

# CM3INT Baseline Report (Pilot Baseline)

Date: 2026-02-25

## Scope
Baseline run for Oracle-connected E2E validation in CM3INT using generated sample data and multi-transaction output.

## Run Summary
- Accounts loaded: **100**
- Source rows produced by query: **400**
- Validation failures: **0**
- Final result: **PASS**

## Artifacts
- Summary: `generated/e2e_cm3int/summary.txt`
- Source query output: `generated/e2e_cm3int/source_query_output.csv`
- Final generated file: `generated/e2e_cm3int/final_output.txt`
- Row validation result: `generated/e2e_cm3int/validation_results.csv`
- Wave-aligned summary: `generated/e2e_cm3int/wave_artifacts/summary-report.json`
- Wave-aligned telemetry: `generated/e2e_cm3int/wave_artifacts/telemetry.json`
- Wave-aligned promotion evidence: `generated/e2e_cm3int/wave_artifacts/promotion-evidence.json`

## Notes
- Oracle connectivity used Python `oracledb` path from app environment.
- SQLcl installation is present but Java setup is pending; not required for this baseline.

## Baseline Declaration
This run is the **pilot baseline reference** for subsequent regression comparisons.

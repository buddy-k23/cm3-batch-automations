# ETL Pipeline Testing Architecture — Design Doc

**Issue:** #156
**Date:** 2026-03-26
**Status:** Proposed

---

## Context

The ETL pipeline has two stages:

```
Stage 1: Source Systems → Staging Tables → Output Files (per mapping rules)
Stage 2: Concatenated Files → Transactional Load → 3rd Party System
```

Each stage has risk zones where data can silently corrupt. The testing
strategy must place validation gates at each transition point.

---

## Pipeline Test Gates

```
Source    Staging     Transform    Concatenate    Load       3rd Party
Feeds    Tables      + Output     by Type        (Txn)      System
  │         │           │             │            │            │
  ▼         ▼           ▼             ▼            ▼            ▼
┌────┐   ┌────┐      ┌────┐       ┌────┐      ┌────┐      ┌────┐
│ G1 │   │ G2 │      │ G3 │       │ G4 │      │ G5 │      │ G6 │
│    │   │    │      │    │       │    │      │    │      │    │
│In- │   │Stg │      │Out-│       │Pre-│      │Post│      │Biz │
│put │   │Rec-│      │put │       │Load│      │Load│      │Rule│
│Val │   │on  │      │Val │       │Rec │      │Chk │      │Smk │
└────┘   └────┘      └────┘       └────┘      └────┘      └────┘
  ✅        ✅          ✅           ✅          ⚠️          ❌
 Valdo    Valdo       Valdo       Valdo     Needs DB    Outside
 today    today       today       today     adapter     scope*
```

*Gate 6 (business rule smoke tests) requires 3rd party API/UI automation —
outside Valdo's scope. Use Playwright or Selenium for UI, API test suites
for REST endpoints.

---

## Gate Details

### Gate 1: Input Validation (Source → Staging)

**What:** Validate raw source files before they're loaded into staging.
**Why:** Catch corrupt, truncated, or schema-drifted files before they contaminate staging.

**Valdo commands:**
```bash
# Per source system
valdo validate \
  --file /batch/incoming/source_a_20260326.txt \
  --mapping config/mappings/source_a.json \
  --rules config/rules/source_a_rules.json \
  --output reports/gate1/source_a_validation.json
```

**Checks:**
- File structure matches mapping (positions, lengths, types)
- Required fields populated
- Row counts within expected range
- No encoding corruption
- Cross-row: unique keys, sequential batch numbers

---

### Gate 2: Staging Reconciliation (Staging Tables ↔ Mapping)

**What:** Verify staging tables match the mapping schema.
**Why:** Schema drift (added/removed columns, type changes) causes silent data loss.

**Valdo commands:**
```bash
# Per source system
valdo reconcile \
  --mapping config/mappings/source_a.json \
  --output reports/gate2/source_a_reconcile.json

# Bulk with drift detection
valdo reconcile-all \
  --mappings-dir config/mappings/ \
  --baseline baselines/staging_schema.json \
  --fail-on-drift \
  --output reports/gate2/reconcile_all.json
```

**Checks:**
- All mapped tables exist
- All mapped columns exist with compatible types
- Column lengths sufficient for mapped field lengths
- No unexpected schema changes since last baseline

---

### Gate 3: Output File Validation (Transform → Output)

**What:** Validate the generated output files against their target specifications.
**Why:** Transformation bugs (wrong field mapping, calculation errors, encoding issues) are the #1 source of downstream failures.

**Valdo commands:**
```bash
# Validate each output file
valdo validate \
  --file /batch/output/tranert_source_a.txt \
  --mapping config/mappings/tranert_target.json \
  --rules config/rules/tranert_rules.json \
  --output reports/gate3/tranert_source_a.json

# Compare against expected output (regression baseline)
valdo compare \
  --file1 /batch/output/tranert_source_a.txt \
  --file2 baselines/tranert_source_a_expected.txt \
  --key-columns ACCT-KEY \
  --output reports/gate3/tranert_source_a_diff.json
```

**Checks:**
- Output file structure matches target mapping
- Field values transformed correctly (default values, lookups, calculations)
- Cross-row rules: unique accounts, sequential batch items, consistent bank codes
- Record counts match source (no records dropped or duplicated)
- Regression: no unexpected changes from baseline

---

### Gate 4: Pre-Load Reconciliation (Concatenated Files ↔ Source DB)

**What:** After files are concatenated by type, verify the merged file against source data.
**Why:** Concatenation can introduce duplicate keys, wrong ordering, or missing records from one source.

**Valdo commands:**
```bash
# Compare concatenated file against DB extract
valdo db-compare \
  --query "SELECT * FROM staging.all_accounts WHERE batch_date = '20260326'" \
  --actual-file /batch/output/concatenated_accounts.txt \
  --mapping config/mappings/accounts_target.json \
  --key-columns ACCT-KEY \
  --output reports/gate4/pre_load_recon.json

# Cross-row validation on concatenated file
valdo validate \
  --file /batch/output/concatenated_accounts.txt \
  --mapping config/mappings/accounts_target.json \
  --rules config/rules/concatenation_rules.json
```

**Concatenation-specific rules:**
```json
{
  "rules": [
    {"id": "CR001", "type": "cross_row", "check": "unique",
     "field": "ACCT-KEY", "severity": "error",
     "message": "No duplicate accounts across concatenated sources"},
    {"id": "CR002", "type": "cross_row", "check": "sequential",
     "key_field": "ACCT-KEY", "sequence_field": "BATCH-SEQ",
     "severity": "error",
     "message": "Batch sequence must restart at 1 per account"},
    {"id": "CR003", "type": "cross_row", "check": "group_count",
     "key_field": "ACCT-KEY", "count_field": "TXN-COUNT",
     "severity": "error",
     "message": "Transaction count must match actual records"}
  ]
}
```

---

### Gate 5: Post-Load Verification (3rd Party System)

**What:** After transactional load, verify records exist in the target system.
**Why:** Load failures, partial rollbacks, and transformation bugs in the loader are invisible without verification.

**Valdo commands (requires #151 for non-Oracle DBs):**
```bash
# Compare loaded records against input file
valdo db-compare \
  --dsn "postgresql://target-host:5432/collections" \
  --query "SELECT account_id, balance, status FROM accounts WHERE load_date = '20260326'" \
  --actual-file /batch/output/concatenated_accounts.txt \
  --mapping config/mappings/target_system.json \
  --key-columns ACCT-KEY \
  --output reports/gate5/post_load_verify.json
```

**Checks:**
- Row counts: source file records = target DB records
- Key fields match (account numbers, dates)
- Numeric fields match within tolerance (balances, amounts)
- Status fields reflect expected business logic

**Limitations:** This gate depends on:
- Database access to the 3rd party system
- Understanding of the target system's schema
- Knowledge of which tables/views to query

---

## Pipeline YAML Configuration

```yaml
pipeline:
  name: nightly-batch-etl
  sources:
    - name: source_a
      mapping: config/mappings/source_a.json
      rules: config/rules/source_a_rules.json
      output_pattern: "output/tranert_source_a_*.txt"
    - name: source_b
      mapping: config/mappings/source_b.json
      rules: config/rules/source_b_rules.json
      output_pattern: "output/tranert_source_b_*.txt"

  gates:
    - name: input_validation
      for_each: source
      blocking: true
      steps:
        - type: validate
          file: "{source.input_path}"
          mapping: "{source.mapping}"
          rules: "{source.rules}"
          thresholds: { max_error_pct: 5 }

    - name: output_validation
      for_each: source
      blocking: true
      steps:
        - type: validate
          file: "{source.output_pattern}"
          mapping: "{source.target_mapping}"
          rules: "{source.target_rules}"
        - type: compare
          file1: "{source.output_pattern}"
          file2: "baselines/{source.name}_expected.txt"
          key_columns: [ACCT-KEY]

    - name: pre_load_reconciliation
      blocking: true
      steps:
        - type: db_compare
          query: "SELECT * FROM staging WHERE batch_date = '{run_date}'"
          file: "output/concatenated_all.txt"
          key_columns: [ACCT-KEY]

    - name: post_load_verification
      blocking: false  # non-blocking — report only
      steps:
        - type: db_compare
          adapter: postgresql
          query: "SELECT * FROM target.accounts WHERE load_date = '{run_date}'"
          file: "output/concatenated_all.txt"
          key_columns: [ACCT-KEY]

  notifications:
    on_failure:
      - type: teams_webhook
        url: "${TEAMS_WEBHOOK_URL}"
```

---

## What Valdo Covers vs What It Doesn't

| Capability | Valdo | External Tool |
|-----------|-------|---------------|
| File structure validation | ✅ | |
| Field-level rules (regex, ranges, valid values) | ✅ | |
| Cross-field rules (IF/THEN within row) | ✅ | |
| Cross-row rules (unique, sequential, consistent) | ✅ | |
| File-to-file comparison | ✅ | |
| DB extract and compare | ✅ | |
| Schema reconciliation | ✅ | |
| PII masking for test data | ✅ | |
| Audit logging (Splunk) | ✅ | |
| Notifications (email, Teams, Slack) | ✅ | |
| CI pipeline templates | ✅ | |
| Pipeline orchestration | 🔨 #156 | CI/CD tools |
| Non-Oracle DB support | 🔨 #151 | |
| 3rd party UI testing | ❌ | Playwright/Selenium |
| 3rd party business rule verification | ❌ | Custom test harness |
| Real-time monitoring/alerting | ❌ | Grafana/Splunk |

---

## Implementation Priority

1. **Now:** Use Valdo for Gates 1-4 via suite YAML + CI pipeline
2. **Next:** #151 (multi-DB) unlocks Gate 5
3. **Next:** #156 (pipeline runner) adds orchestration, regression baselines, source templating
4. **Later:** Gate 6 requires 3rd party system expertise — separate effort

---

## How to Wire Valdo Into Your ETL Today (Without #156)

Use your existing CI/CD pipeline (Azure DevOps, GitHub Actions, or cron) as the orchestrator:

```yaml
# Azure DevOps pipeline example
stages:
  - stage: Gate1_InputValidation
    jobs:
      - job: ValidateSourceA
        steps:
          - template: ci/templates/azure-valdo-validate.yml
            parameters:
              file: $(Pipeline.Workspace)/incoming/source_a.txt
              mapping: config/mappings/source_a.json
              rules: config/rules/source_a_rules.json
              thresholdMaxErrorPct: 5

  - stage: Gate3_OutputValidation
    dependsOn: [ETL_Stage1]  # your actual ETL stage
    jobs:
      - job: ValidateOutput
        steps:
          - template: ci/templates/azure-valdo-validate.yml
            parameters:
              file: $(Pipeline.Workspace)/output/tranert_all.txt
              mapping: config/mappings/tranert_target.json
              rules: config/rules/tranert_rules.json

  - stage: Gate4_PreLoadRecon
    dependsOn: [Gate3_OutputValidation]
    jobs:
      - job: Reconcile
        steps:
          - script: |
              valdo db-compare \
                --query "SELECT * FROM staging WHERE batch_date='$(Build.BuildId)'" \
                --actual-file output/concatenated.txt \
                --mapping config/mappings/target.json \
                --key-columns ACCT-KEY \
                --output reports/gate4_recon.json
```

This gives you Gates 1-4 today, using Azure DevOps as the orchestrator and Valdo as the validation engine at each gate.

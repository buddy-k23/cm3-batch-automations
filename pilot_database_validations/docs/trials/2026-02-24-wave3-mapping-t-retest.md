# Wave 3 Retest Report - `mapping-t.xlsx` (Before vs After)

- **Date/Time:** 2026-02-24 21:38-22:05 EST
- **Workbook (read-only):** `/Users/buddy/Downloads/mapping-t.xlsx`
- **Objective:** Re-run end-to-end flow with Wave 3 parser/generator improvements and compare against Wave 2 trial.

## 1) Commands run

```bash
# Parser-only retest (Wave 3)
python3 tools/template_parser.py \
  --input /Users/buddy/Downloads/mapping-t.xlsx \
  --file-config-input examples/templates/file-config-template.csv \
  --output generated/trials/wave3/template-ingest.direct.json

# End-to-end retest (Wave 3)
python3 tools/e2e_runner.py \
  --input /Users/buddy/Downloads/mapping-t.xlsx \
  --file-config-input examples/templates/file-config-template.csv \
  --out-dir generated/trials/wave3
```

## 2) Before vs After summary

| Area | Wave 2 (before) | Wave 3 (after) | Delta |
|---|---:|---:|---:|
| Parser exit status | Failed (exit 1) | Success (exit 0) | ✅ fixed |
| Primary blocker | `format` misclassified as file_config | Mapping classification works | ✅ fixed |
| Mapping rows emitted | 0 (blocked) | 210 | ✅ +210 |
| Parsed sheets | 4 | 4 | ↔ |
| Meaningful row handling | Inflated raw rows (e.g., 999 on Sheet1) | Empty/trailing rows filtered | ✅ improved |
| Integer coercion (`1.0`-style) | Could fail strict int parse | Accepted as integers | ✅ fixed |
| `required` normalization | `C` / `N/A` caused enum mismatches | Normalized (`C`->`Conditional`, `N/A`->blank) | ✅ fixed |
| E2E conversion status | Not runnable (parser blocked) | Runs, but final status `FAILED` | ⚠ partial |

## 3) Wave 3 coverage (actual output)

From `generated/trials/wave3/template-ingest.json`:

- **mappingRows:** `210`
- **ruleRows:** `0`
- **Sheet distribution:**
  - `Sheet1`: 49
  - `Sheet2`: 35
  - `Sheet3`: 57
  - `Sheet4`: 69
- **dataType coverage:**
  - `string`: 105
  - `numeric`: 79
  - `date`: 24
  - `boolean`: 2
- **field-structure coverage:**
  - rows with `length`: 210/210
  - rows with `positionStart`: 210/210
  - rows with `format`: 103/210

## 4) Remaining gaps (post-Wave 3)

1. **No rules section in workbook** (still open)
   - `ruleRows=0`, but `rules.schema.json` currently requires non-empty array.
   - End-to-end run completes artifact generation, but conversion report status is `FAILED` because rules contract fails:
     - `rules.rules must be a non-empty array`

2. **Source lineage unresolved for all mapping rows** (still open)
   - `sourceField` was auto-filled as placeholders: `UNRESOLVED_SOURCE::<target>` on **210/210** rows.
   - This preserves contract validity but indicates unresolved upstream source mapping decisions.

3. **Transaction code is inferred, not authoritative** (still open)
   - `transactionCode` currently inferred from sheet names (`Sheet1`..`Sheet4`) for **210/210** rows.
   - Needs BA-approved derivation (sheet->txn lookup or explicit column).

## 5) Artifacts produced (Wave 3)

Under `generated/trials/wave3/`:

- `template-ingest.json`
- `template-ingest.direct.json`
- `mapping.json`
- `rules.json`
- `conversion-report.json`
- `report.json`
- `summary-report.json`
- `telemetry.json`
- `detail-violations.csv`
- `e2e.stdout.json`
- `e2e.stderr.json`
- `parser.stdout.json`
- `parser.stderr.json`

## 6) Prioritized next fixes

### P0 (unblock true SUCCESS status)
1. **Support empty rule packs for mapping-only trials**
   - Option A: allow `rules=[]` in schema for specific pipeline mode.
   - Option B: auto-generate a minimal informational placeholder rule for mapping-only inputs.

### P1 (data quality correctness)
2. **Replace placeholder `sourceField` derivation with governed mapping**
   - Add configurable source-derivation strategy (explicit alias table or extraction logic from workbook columns).

3. **Replace sheet-name transaction code inference**
   - Add workbook/sheet metadata mapping config (`sheet_name -> transactionCode`) and validate against approved values.

### P2 (trial observability)
4. **Emit explicit unresolved-fields report**
   - Add machine-readable gap artifact (e.g., `unresolved-fields.json/csv`) with row-level unresolved reasons for BA/QA signoff.

---

## 7) Conclusion

Wave 3 materially improved parser robustness and coverage versus Wave 2 (classification, row filtering, numeric coercion, required-flag normalization). The pipeline now reaches contract generation with full mapping row coverage from the workbook. Remaining blockers are governance/data-completeness issues (rules-empty policy, sourceField/transactionCode derivation), not parser ingestion mechanics.

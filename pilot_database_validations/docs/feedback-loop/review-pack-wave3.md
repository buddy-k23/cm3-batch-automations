# Wave 3 Feedback-Loop Review Pack (`mapping-t.xlsx` trial)

**Date:** 2026-02-24  
**Scope:** Current Wave 3 artifacts under `generated/trials/wave3/` and trial docs in `docs/trials/`  
**Primary input:** `/Users/buddy/Downloads/mapping-t.xlsx` (+ `examples/templates/file-config-template.csv`)

---

## 1) What works (Wave 3)

- Parser now **successfully ingests** `mapping-t.xlsx` and emits canonical payload (`template-ingest.json`) with **210 mapping rows**.
- Wave 2 blocker is fixed: mapping sheets with a `format` column are no longer misclassified as `file_config`.
- **Meaningful-row filtering** removed blank/trailing row inflation.
- **Integer coercion** accepts `N.0` patterns for integer fields.
- **`required` normalization** now handles `C` -> `Conditional` and `N/A` -> blank.
- End-to-end flow now generates all major artifacts (`mapping.json`, `rules.json`, conversion/report/telemetry outputs).
- Mapping coverage by sheet is stable and complete for observed workbook tabs:
  - Sheet1: 49
  - Sheet2: 35
  - Sheet3: 57
  - Sheet4: 69

---

## 2) What is WARN vs FAIL

### Current hard FAIL (run-blocking)
1. **Empty rules pack**
   - Observed: `ruleRows = 0`, `rules.rules = []`
   - Error(s):
     - `rules.rules must be a non-empty array`
     - `rules: $.rules must contain at least 1 items`
   - Impact: overall conversion status remains **FAILED** despite successful mapping generation.

### Current WARN (processable, but governance/data-quality risk)
1. **Source lineage unresolved for all rows**
   - Observed: `210/210` rows use `sourceField = UNRESOLVED_SOURCE::<targetField>`.
2. **Transaction code inferred from sheet name**
   - Observed: all rows derive transactionCode from `Sheet1..Sheet4` (not BA-approved business codes).
3. **Transform logic quality flags**
   - Observed: at least one literal error token in logic (`#ERROR!`) and several blank transform logic entries.
4. **Validation telemetry/report placeholders**
   - Observed: summary/telemetry validation counts are still stub-like (`validated/passed/warned = 0` in summary pipeline path), limiting confidence signals.

---

## 3) Top 5 gaps to close

1. **Rules policy gap (mapping-only mode unsupported):** empty rules currently fail contract validation.
2. **Source derivation policy gap:** unresolved source placeholders are universal and non-production-safe.
3. **Transaction code derivation policy gap:** sheet-name fallback is deterministic but not semantically governed.
4. **Quality signal gap:** WARN conditions are not yet consistently surfaced as first-class decision metrics in all reports.
5. **Business-logic hygiene gap:** raw transform text can contain formula/error artifacts (`#ERROR!`) without dedicated severity handling.

---

## 4) Recommended policy defaults

## A) Derivation mode defaults

### `transactionCode` derivation (default)
- **Default mode:** `column_fallback`
- **Fallback chain:** `explicit column` -> `sheet_to_txn lookup table` -> `sheet_name` (WARN) -> `UNRESOLVED_TXN` (FAIL if any remain)
- **Rationale:** keeps deterministic output while enforcing governed semantics before production.

### `sourceField` derivation (default)
- **Default mode:** `column_fallback`
- **Fallback chain:** `explicit source column` -> `approved alias mapping` -> `target_field mirror` (WARN) -> `UNRESOLVED_SOURCE::<target>` (FAIL above threshold)
- **Rationale:** allows trial continuity, but forces explicit lineage convergence.

## B) Acceptance thresholds (recommended)

### Trial / feedback-loop environment
- **FAIL if:**
  - rules policy not satisfied (unless run is explicitly marked `mapping_only=true`), or
  - unresolved transaction codes > **0%**, or
  - unresolved source fields > **20%**, or
  - schema errors > **0**.
- **WARN if:**
  - unresolved source fields is **>0% and <=20%**,
  - any transform logic contains `#ERROR!` or empty logic where `required=Y/Conditional`,
  - sheet-name fallback used for transaction code.

### Pre-production certification
- **FAIL if any:**
  - unresolved source fields > **0%**,
  - unresolved transaction codes > **0%**,
  - rules pack empty when rules are required,
  - schema/report integrity errors > **0**.

---

## One-page decision checklist (signoff)

**Run reviewed:** `generated/trials/wave3`  
**Reviewer:** ____________________  
**Date:** ____________________

### A. Scope and artifacts
- [ ] Reviewed `template-ingest.json`, `mapping.json`, `rules.json`, `conversion-report.json`.
- [ ] Confirmed workbook coverage (210 rows across Sheet1-4).
- [ ] Confirmed parser hardening fixes from Wave 3 are present.

### B. FAIL gate decisions
- [ ] Decide policy for mapping-only runs:  
      [ ] Allow empty `rules[]` in mapping-only mode  
      [ ] Auto-generate minimal placeholder rule  
      [ ] Keep strict non-empty rules (no exception)
- [ ] Approve/deny current run as production-candidate (expected: **deny** until FAIL items closed).

### C. WARN governance decisions
- [ ] Approve transaction-code derivation default + fallback chain.
- [ ] Approve source-field derivation default + fallback chain.
- [ ] Approve threshold policy (trial vs pre-prod).
- [ ] Decide owner/date for resolving unresolved lineage.

### D. Data quality and remediation
- [ ] Review and triage transform logic anomalies (`#ERROR!`, blank logic).
- [ ] Confirm required fields with blank logic have explicit BA disposition.
- [ ] Confirm telemetry/report WARN and FAIL counts will be operationalized in next wave.

### E. Signoff
- **Decision:**  [ ] Approved for next wave  [ ] Changes required  [ ] Re-test required  
- **Required actions before next signoff:** ____________________________________________  
- **Signer:** ____________________  
- **Notes:** _________________________________________________________________________

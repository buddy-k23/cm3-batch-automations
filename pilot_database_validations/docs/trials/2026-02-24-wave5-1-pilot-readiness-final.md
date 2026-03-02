# Wave 5.1 Pilot Readiness Final Report

- **Timestamp:** 2026-02-24 23:01 EST
- **Scope:** Pilot-readiness decision after Wave 5 multi-workbook validation and policy-threshold lock
- **Primary evidence:**
  - `generated/trials/wave5/consolidated-run-report.json`
  - `generated/trials/wave5/*/promotion-evidence.json`
  - `docs/trials/2026-02-24-wave5-multi-workbook-validation.md`
  - `docs/template-spec.md` (pilot thresholds locked)
  - `docs/stage-gates-checklist.md` (pilot thresholds locked)

## Locked Pilot Thresholds (Wave 5.1)
- `warn_acceptance_mode = review-required`
- `warn_acceptance_max_open = 20`
- `warn_acceptance_expiry_days = 7`
- `min_template_completeness_ba = 95%`
- `min_template_completeness_qa = 95%`
- `min_template_completeness_required_columns = 100%`
- `derivation_default_transaction_code = disabled`
- `derivation_default_source_field = disabled`
- `derivation_mode_on_enable = placeholder`

## Latest Artifact Summary

| Scenario | Pre-gate | Promotion | Errors | WARNs | Against locked pilot policy |
|---|---|---|---:|---:|---|
| `mapping-t-external` | FAILED | FAIL | 2 | 420 | **FAIL** (`2` hard errors; WARN `420 > 20`; missing BA/QA signoff; missing waiver age) |
| `fixture-clean-pass` | SUCCESS | PASS | 0 | 0 | PASS |
| `fixture-warn-pass-with-signoff` | WARN | PASS | 0 | 8 | PASS (`8 <= 20` with signoff + waiver age) |

Suite totals from consolidated report:
- Scenarios: `3`
- Promotion PASS: `2`
- Promotion FAIL: `1`
- Total hard errors: `2`
- Total warnings: `428`

Go-live checklist rollup:
- `allPromotionPass`: **false**
- `hardErrorsResolved`: **false**
- `multiWorkbookCoverage`: true
- `includesExternalWorkbook`: true
- `warnsGoverned`: **false**

## Final Decision

## **NO-GO for pilot release (current state)**

The pilot is not ready because required gate conditions are not met for the external workbook scenario.

## Remaining Blockers (must clear before GO)
1. **Hard-error path still open on external workbook**
   - `ruleRows=0` triggers hard failure in current rules contract path.
2. **WARN volume far above locked pilot threshold**
   - External workbook produces `420` WARNs vs locked max `20`.
3. **Required WARN governance evidence missing for external workbook**
   - No BA signoff, no QA signoff, and no waiver age provided under `review-required` policy.
4. **Suite-level promotion does not fully pass**
   - `2/3` scenarios pass; pilot requires full pass for readiness call.

## Exit Criteria to Flip to GO
- External workbook scenario reaches `preGateStatus in {SUCCESS, WARN}` with **0 hard errors**.
- External workbook promotion decision becomes `PASS` under locked thresholds (`warn_count <= 20`, BA+QA signoff present when WARNs exist, waiver age provided).
- Consolidated checklist flags become true for `allPromotionPass`, `hardErrorsResolved`, and `warnsGoverned`.

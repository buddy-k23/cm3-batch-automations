# Wave 5.1 Stabilization — Consolidated Baseline Re-run Comparison

- **Date/Time:** 2026-02-24 23:02 EST
- **Workspace:** `/Users/buddy/.openclaw/workspace/database-validations`
- **Objective:** Re-run consolidated multi-workbook Wave 5 baseline after Wave 5.1 changes and compare against prior Wave 5 consolidated trial.
- **Current harness/pipeline:** `python3 tools/wave5_multiworkbook_harness.py`

## 1) Baseline re-run execution

Command:

```bash
python3 tools/wave5_multiworkbook_harness.py
```

Primary output artifact:
- `generated/trials/wave5/consolidated-run-report.json` (updated at 23:02 EST)

## 2) Current consolidated results (post Wave 5.1)

| Scenario | Pre-gate status | Promotion decision | Errors | WARNs |
|---|---|---|---:|---:|
| `mapping-t-external` | FAILED | FAIL | 2 | 420 |
| `fixture-clean-pass` | SUCCESS | PASS | 0 | 0 |
| `fixture-warn-pass-with-signoff` | WARN | PASS | 0 | 8 |

Totals:
- scenarios: 3
- preGate: SUCCESS 1 / WARN 1 / FAILED 1
- promotion: PASS 2 / FAIL 1
- totalErrors: 2
- totalWarnings: 428

## 3) Comparison vs prior Wave 5 report

Prior reference:
- `docs/trials/2026-02-24-wave5-multi-workbook-validation.md`

### Delta (current - prior)

| Metric | Prior | Current | Delta |
|---|---:|---:|---:|
| Promotion PASS | 2 | 2 | 0 |
| Promotion FAIL | 1 | 1 | 0 |
| Pre-gate SUCCESS | 1 | 1 | 0 |
| Pre-gate WARN | 1 | 1 | 0 |
| Pre-gate FAILED | 1 | 1 | 0 |
| Total errors | 2 | 2 | 0 |
| Total warnings | 428 | 428 | 0 |

### Scenario-level deltas

| Scenario | Δ Errors | Δ WARNs | Pre-gate change | Promotion change |
|---|---:|---:|---|---|
| `mapping-t-external` | 0 | 0 | no change (FAILED) | no change (FAIL) |
| `fixture-clean-pass` | 0 | 0 | no change (SUCCESS) | no change (PASS) |
| `fixture-warn-pass-with-signoff` | 0 | 0 | no change (WARN) | no change (PASS) |

## 4) Blocking/error status confirmation

External workbook scenario remains blocked for same reasons:
- `HARD_ERRORS_PRESENT`
- `WARN_COUNT_ABOVE_THRESHOLD`
- `WARN_SIGNOFF_REQUIRED`
- `WARN_WAIVER_AGE_MISSING`

No regression detected relative to prior Wave 5 consolidated run; no net improvement in PASS/FAIL/WARN/errors in this re-run.

## 5) Conclusion

Wave 5.1 stabilization baseline re-run is **stable** versus prior Wave 5 consolidated baseline (all requested delta dimensions unchanged).
Go-live posture remains unchanged from prior report: **NOT READY FOR GO-LIVE**.

# Pilot Orchestrator 5-Round Report

- Timestamp (UTC): 2026-02-26T06:35:24Z
- Orchestrator: `tools/pilot_orchestrator.py`
- Scope: 5 deterministic rounds across onboarded source tables from `generated/pilot_contracts/index.json`
- Tables covered (7):
  - `SHAW_SRC_ATOCTRAN`
  - `SHAW_SRC_EAC`
  - `SHAW_SRC_ESA`
  - `SHAW_SRC_EST`
  - `SRC_SRC_NAS_TRANERT_MAPPING_SH`
  - `SHAW_SRC_P327`
  - `SHAW_SRC_TRANERT`

## Execution model
Each round executes, per table, three scenarios:
1. **positive**: expected `preGate=SUCCESS`, promotion `PASS`
2. **negative**: expected `preGate=FAILED`, promotion `FAIL`
3. **edge**: expected `preGate=WARN`, promotion `PASS` under locked policy (`warn <= 20`, signoff + waiver age)

Locked policy reused from existing gate logic (`tools/promotion_gate.py`):
- `warn_acceptance_mode=review-required`
- `warn_acceptance_max_open=20`
- `warn_acceptance_expiry_days=7`
- completeness mins: BA `95`, QA `95`, required columns `100`

## Round trend table

| Round | Status (PASS/WARN/FAIL) | Errors | Warnings | Promotion decision |
|---:|---|---:|---:|---|
| 1 | WARN | 14 | 140 | CONDITIONAL |
| 2 | WARN | 7 | 126 | CONDITIONAL |
| 3 | WARN | 14 | 140 | CONDITIONAL |
| 4 | WARN | 7 | 126 | CONDITIONAL |
| 5 | WARN | 14 | 140 | CONDITIONAL |

## Interpretation
- All rounds were deterministic and expectation checks passed (`expectationMismatches=0` in every round).
- Round status is `WARN` by design because each round includes explicit negative scenarios that must remain blocking (`FAIL`) for control coverage.
- Promotion decision stays `CONDITIONAL` at round level for the same reason (negative controls intentionally present).

## Artifacts
- Consolidated JSON: `generated/trials/pilot-orchestrator/consolidated-pilot-report.json`
- Per-round summaries:
  - `generated/trials/pilot-orchestrator/round-01/round-summary.json`
  - `generated/trials/pilot-orchestrator/round-02/round-summary.json`
  - `generated/trials/pilot-orchestrator/round-03/round-summary.json`
  - `generated/trials/pilot-orchestrator/round-04/round-summary.json`
  - `generated/trials/pilot-orchestrator/round-05/round-summary.json`
- Per-table/per-scenario evidence (example path pattern):
  - `generated/trials/pilot-orchestrator/round-0X/<table>/<scenario>/promotion-evidence.json`
  - `generated/trials/pilot-orchestrator/round-0X/<table>/<scenario>/scenario-result.json`

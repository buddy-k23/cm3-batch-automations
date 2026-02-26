# Production Cutover Checklist

## 1) Technical Readiness
- [ ] 3 consecutive pilot runs completed
- [ ] No hard validation errors in pilot window
- [ ] Deterministic rerun behavior confirmed
- [ ] Latest baseline report reviewed (`docs/trials/2026-02-25-cm3int-baseline-report.md`)

## 2) Governance Readiness
- [ ] WARN acceptance policy approved
- [ ] Waiver owner and expiry policy documented
- [ ] BA and QA signoff flow confirmed
- [ ] Promotion gate evidence reviewed for latest runs

## 3) Operational Readiness
- [ ] QA runbook published and validated
- [ ] On-call owner for validation failures assigned
- [ ] Escalation channel documented
- [ ] Rollback trigger defined (e.g., sustained FAIL or rising hard errors)

## 4) Data & Reporting Readiness
- [ ] Summary/telemetry artifacts archived per run
- [ ] Violation detail retention policy set
- [ ] Trend monitoring owner assigned

## 5) Final Go/No-Go
- [ ] Go/No-Go meeting completed
- [ ] Decision recorded
- [ ] Production run window approved

## Rollback Conditions (minimum)
- Any hard error in production cutover run
- Promotion evidence decision = FAIL
- Missing mandatory signoff/waiver evidence

## Rollback Action
- Revert to last known-good validated mapping/rule pack
- Freeze new promotions until root cause and fix verification

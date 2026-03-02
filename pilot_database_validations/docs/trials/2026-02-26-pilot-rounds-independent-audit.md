# Independent Audit — 5 Pilot Rounds (Correctness, Consistency, Flakiness)

- **Date/Time:** 2026-02-26 01:34 EST
- **Auditor:** independent subagent audit
- **Scope:** Verify 5 pilot rounds for correctness and consistency; validate promotion evidence; check summary/telemetry alignment; identify flaky behavior; produce go/no-go recommendation with blockers and prioritized fixes.

## Rounds Audited

1. **Wave 2 real workbook trial** (`docs/trials/2026-02-24-wave2-mapping-t-trial.md`, `generated/trials/wave2/*`)
2. **Wave 3 retest** (`docs/trials/2026-02-24-wave3-mapping-t-retest.md`, `generated/trials/wave3/*`)
3. **Wave 5.1 baseline rerun comparison (NO-GO claim)** (`docs/trials/2026-02-24-wave5-1-baseline-rerun-comparison.md`)
4. **Wave 5.1 stabilized multi-workbook rerun (PASS claim)** (`docs/trials/2026-02-24-wave5-multi-workbook-validation.md`, `generated/trials/wave5/*`)
5. **CM3INT baseline** (`docs/trials/2026-02-25-cm3int-baseline-report.md`, `generated/e2e_cm3int/wave_artifacts/*`)

---

## Audit Results by Round

| Round | Expected from docs | Artifact-verified outcome | Audit result |
|---|---|---|---|
| Wave 2 | Parser blocked on section misclassification | `parser.stderr.json` exists; no complete ingest emitted | ✅ consistent |
| Wave 3 | Parser fixed; E2E still FAIL due empty rules | `conversion-report.json.status=FAILED`, `errors=2` (`rules.rules must be non-empty`) | ✅ consistent |
| Wave 5.1 baseline rerun | External scenario FAIL; suite 2/3 PASS; NO-GO | **Not reproducible from current machine artifacts** (current consolidated report shows 3/3 PASS) | ❌ evidence drift |
| Wave 5.1 stabilized rerun | External scenario WARN/PASS under governance; suite 3/3 PASS | `consolidated-run-report.json`: promotionPass=3, totalErrors=0, totalWarnings=428 | ✅ consistent with current artifacts |
| CM3INT baseline | PASS with 400 validated rows | `summary-report.json` + `telemetry.json` both show validated=400, failed=0, warned=0; promotion PASS | ✅ consistent |

---

## Promotion Evidence Verification

### Wave 5 scenarios (current artifact state)
- `mapping-t-external/promotion-evidence.json`
  - `preGateStatus=WARN`, `decision=PASS`, `hardErrorCount=0`, `openWarnCount=420`
  - Policy used: `warnAcceptanceMaxOpen=500`, signoffs present, waiver age present.
- `fixture-clean-pass`: `SUCCESS -> PASS`, 0 errors, 0 warnings.
- `fixture-warn-pass-with-signoff`: `WARN -> PASS`, 8 warnings with signoff/waiver evidence.

### Key consistency finding
- **Policy mismatch vs “locked pilot thresholds” documentation:**
  - `docs/trials/2026-02-24-wave5-1-pilot-readiness-final.md` states locked threshold `warn_acceptance_max_open=20` and NO-GO.
  - Current executed Wave 5 external scenario used `warnAcceptanceMaxOpen=500` (from harness config), resulting in PASS.
- This is a governance/evidence inconsistency, not a math error in the promotion engine.

---

## Summary ↔ Telemetry Alignment

- **CM3INT baseline:** aligned (`validated=400`, `failed=0`, `warned=0` across summary + telemetry).
- **Wave 3/Wave 5:** summary and telemetry align with each other for runtime validation counts (both mostly zeros in these template-runner contexts).
- **Important nuance:** `conversion-report.summary.warnings` (template/derivation warnings) is **not** reflected in `summary-report.warned` (runtime rule-validation warnings). This is currently behaviorally consistent, but easy to misread and should be explicitly documented.

---

## Flaky Behavior / Reliability Findings

1. **Round evidence overwrite (high risk):**
   - Same path `generated/trials/wave5/consolidated-run-report.json` is reused for different Wave 5.1 calls.
   - Historical NO-GO state cannot be reconstructed from current artifacts alone.
2. **Policy drift disguised as stability (high risk):**
   - External scenario changed from FAIL to PASS primarily through threshold/governance inputs and placeholder-rule handling.
   - Without immutable run snapshots, this can look like non-deterministic “flakiness.”
3. **Promotion evidence schema inconsistency (medium):**
   - Wave runner uses `evaluation/observed/policy` structure.
   - CM3INT uses a different shape (`policyVersion/enabled/preGateStatus/decision/reasonCodes/metrics`).
   - Complicates cross-round automated auditing.

---

## Go / No-Go Recommendation

## **NO-GO (for strict pilot policy claim); CONDITIONAL GO only under explicitly governed relaxed policy**

### Blocking defects
1. **Governance contradiction:** locked pilot threshold claims (`max_open=20`) conflict with executed PASS policy (`max_open=500`) for external workbook.
2. **Non-immutable evidence chain:** multiple rounds overwrite identical artifact paths, preventing independent reproducibility of prior decisions.
3. **Metric semantics ambiguity:** conversion warnings vs runtime warnings are split across artifacts without explicit normalized rollup.

### Prioritized fixes

**P0 (must do before pilot decision signoff)**
1. **Freeze policy in executable config** (single source of truth; fail run if docs policy and runtime flags diverge).
2. **Version run outputs by round/run-id/timestamp** (no overwrite; preserve all pilot round evidence).
3. **Emit normalized audit manifest** per run with: pre-gate status, promotion decision, hard errors, conversion warnings, runtime warnings, and effective policy.

**P1**
4. **Unify/standardize promotion-evidence schema** across Wave and CM3INT flows.
5. **Add explicit field-level definitions** in docs for `conversion warnings` vs `summary warned` semantics.

**P2**
6. Reduce external workbook WARN volume (420) through lineage/source derivation hardening so PASS is not threshold-dependent.

---

## Auditor conclusion
- Engine behavior appears deterministic for current inputs.
- Primary risk is **governance and evidence consistency**, not core arithmetic.
- Treat current state as **not audit-ready for final pilot signoff** until P0 items are complete.

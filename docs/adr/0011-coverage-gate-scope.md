# ADR 0011: Coverage Gate Scope

- Status: Proposed (flip to Accepted on MR merge)
- Date: 2026-06-03
- Supersedes: none
- Related: `docs/ARCHITECTURE_REVIEW_2026-06-03.md` (recommendation R-02),
  `pytest.ini`, `docs/handover/ARCH_REVIEW_TEST_BASELINE_2026-06-03.md`

## Context

`pytest.ini` enforces `--cov-fail-under=80`, but `--cov` was scoped to only
four packages: `src/api`, `src/commands`, `src/comparators`, `src/reports`. The
**core engine** — `src/validators`, `src/services`, `src/pipeline` — and the
`src/database` layer were **outside the measured/enforced scope**. As the
2026-06-03 architecture review (R-02) noted, the AGENTS.md claim of "80%+ over
1,063 tests" therefore did not describe what CI actually enforced: the most
load-bearing code (validation, reconciliation services, the pipeline runner)
could regress in coverage without tripping the gate.

The E2E harness (`scripts/e2e_lib/`) is also unmeasured; that is handled
separately in R-02-followup (#27) so a harness-scope decision does not couple to
this core-scope one.

## Decision

Widen the coverage scope in `pytest.ini` to include the core engine and the
database layer, and **keep the bar at 80%**:

```
--cov=src/api
--cov=src/commands
--cov=src/comparators
--cov=src/reports
--cov=src/validators     # added
--cov=src/services       # added
--cov=src/pipeline       # added
--cov=src/database       # added
--cov-fail-under=80
```

### Why 80% is kept (not lowered)

Measured on the dev box (Windows / Python 3.14 / no Oracle / no server — the
degraded environment documented in
`docs/handover/ARCH_REVIEW_TEST_BASELINE_2026-06-03.md`), the **wider** scope
reports **TOTAL = 80%** *even with* the 43 environmental test failures (buckets
A–G) skipping their coverage contribution. On the AGENTS.md target environment
(RHEL 8.9 / Python 3.11 / reachable Oracle / running server) those tests pass,
so the real enforced number is **≥ 80%**. Keeping the bar at 80% is therefore
honest and non-regressive; it is not lowered to accommodate the widening.

### Per-area note (informational, from the dev-box run)

High-coverage core areas: `comparators` (93–97%), most `services` (85–99%),
`validators/multi_record_reader` (99%), `reports/validation_renderer` (91%).
Known low spots are dominated by env-gated code paths (CLI command modules that
need a CLI/server: `etl_pipeline_command`, `mask_command`,
`multi_record_command` at 0% on this box; `src/database/extractor`/
`reconciliation` which need Oracle). These rise on the target environment and
are tracked as future test-uplift opportunities, not gate changes.

### Rejected alternatives

- **A. Lower the bar to match the unmeasured reality.** Rejected: masks the
  problem and violates the runner's "never lower a bar silently" rule.
- **B. Document a deliberate exclusion list and leave the 4-package scope.**
  Rejected: the whole point of R-02 is to put the engine under the gate.
- **C. Widen scope AND raise the bar (e.g. 85%).** Rejected for this slice:
  changing scope and threshold together makes regressions ambiguous to triage.
  A future story may ratchet the bar once the target-environment number is
  confirmed in CI.

## Consequences

### Positive
- The validation/reconciliation engine and pipeline runner are now under the
  enforced coverage gate; a coverage regression there fails CI.
- The published "80%+" claim now matches what CI measures for the core.

### Negative / accepted
- On the **degraded dev box** the number sits exactly at the 80% floor because
  the 43 environmental failures don't contribute coverage; a developer adding a
  small uncovered branch locally could dip below 80% there even though CI (on
  the target env) would pass. Mitigation: the authoritative gate is the target
  environment / CI; local runs use the targeted-suite policy from the baseline
  doc. This is acceptable and self-correcting once CI runs on RHEL.

### Migration
- `pytest.ini` only. No code or data change. No test modified.

## Follow-ups
- **R-02-followup (#27):** add a separate `scripts/e2e_lib/` coverage job with
  its own (initially lower, ratcheting) threshold.
- A future story may raise the core bar above 80% once the target-environment
  number is confirmed green in CI.

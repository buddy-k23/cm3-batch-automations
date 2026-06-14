# Arch-Review Story Handoff — R-02-followup (#27)

**Generated:** 2026-06-03
**Story:** Add a scripts/e2e_lib coverage job
**Predecessor:** docs/handover/ARCH_REVIEW_R-02_HANDOFF.md
**Trunk:** feature/valdo-engine-v3 (commit <set on push>, pushed: yes)
**Safety snapshot:** arch-review-snapshot/R-02-followup @ 41db6cc — deleted after green: yes
**Depends-on satisfied:** #26 (R-02)

## 1. What shipped (scope delivered)
- `scripts/run_harness_coverage.sh` — a standalone coverage job for the E2E
  harness (`scripts/e2e_lib/`), independent of the `src/` gate. Runs
  `pytest tests/unit -k e2e -o addopts="" --cov=scripts/e2e_lib
  --cov-fail-under=$HARNESS_MIN_COVERAGE` (default floor 80, overridable via
  env, intended to ratchet up). Mirrors the house style of `run_tests.sh`.
- `docs/TEST_EXECUTION.md` — new "Coverage gates (two independent gates)"
  section documenting both the core gate (ADR 0011) and the harness gate +
  how CI / a developer invokes it.

## 2. Why a script (not a `.gitlab-ci.yml` edit)
- Valdo's own CI config is **external** (`ci_config_path =
  APPID-33091157/valdo/.gitlab-ci.yml@ops/pipelines/ci-config`) and not editable
  from this repo. The established pattern here is a runnable wrapper
  (`run_tests.sh`); CI calls it as a stage. So this story ships the
  job-as-script + documentation, which the external CI invokes. (The
  `ci/templates/*` files are consumer-facing "run valdo on my data" templates,
  not Valdo's dev CI — intentionally not touched.)

## 3. AGENTS.md compliance
- src/ touched? **No** (a new `scripts/` wrapper + docs only).
- Secrets: none. Audit table: untouched. Quick actions: none.

## 4. ADR(s)
- None new. Implements the R-02-followup bullet of ADR 0011.

## 5. Verification (actual results, baseline policy)
- The harness gate command (what the script runs) executed directly:
  **`Required test coverage of 80% reached. Total coverage: 83.90%`** → exit 0;
  **493 passed, 10 skipped** (live-Oracle SKIPs expected). Per-module: most
  100–99% (`jsonl_logger`, `secret_resolver`, `extract_tranert_properties`),
  `run_source` 67% / `failure_sink` 64% the lowest (env-gated Oracle paths).
- The `.sh` could not be executed on the Windows dev box (`bash` absent — it
  targets RHEL CI per AGENTS.md); validated by running its exact underlying
  pytest command, which passes. The script body mirrors `run_tests.sh`
  (`set -euo pipefail`, same idioms).
- Core `pytest` gate unaffected (this run uses `-o addopts=""` to isolate the
  harness). Full unit+integration failure count unchanged vs. baseline (43,
  buckets A–G); 0 new failures.

## 6. Docs updated
- `docs/TEST_EXECUTION.md` (two-gates section)
- `CHANGELOG.md`

## 7. Acceptance criteria
- [x] A `scripts/e2e_lib/` coverage number is produced and enforced (floor 80%
      per ADR 0011; ~84% measured).
- [x] CI job is independent of the core gate (separate script, `-o addopts=""`)
      so failures are attributable.
- [x] Existing harness tests still pass without live Oracle (10 skipped cleanly).

## 8. Next story
- #30 — R-04a — Add a mode-parity test asserting one shared core. No depends-on.
  (Last two Wave-1 stories: #30, then #31.)

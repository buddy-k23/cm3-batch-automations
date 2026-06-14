# Test Execution Instructions

## Quick Start

```bash
# full suite with configured coverage gate (src/ engine scope, 80% — ADR 0011)
.venv/bin/pytest -q

# targeted verification packs
.venv/bin/pytest -q -o addopts='' tests/unit/test_workflow_engine.py tests/unit/test_workflow_wrapper_parity.py
```

## Coverage gates (two independent gates)

There are **two** coverage gates, measured and enforced separately so a
regression in one is attributable on its own:

1. **Core engine gate** — configured in `pytest.ini` (`--cov-fail-under=80`
   over `src/api`, `src/commands`, `src/comparators`, `src/reports`,
   `src/validators`, `src/services`, `src/pipeline`, `src/database`; ADR 0011).
   Runs as part of the normal `pytest` invocation.

2. **E2E harness gate** — `scripts/e2e_lib/` (ADR 0011, R-02-followup). Run as
   its own job; it does **not** ride on the core gate:

   ```bash
   ./scripts/run_harness_coverage.sh           # default floor: 80%
   HARNESS_MIN_COVERAGE=82 ./scripts/run_harness_coverage.sh   # ratchet up
   ```

   Under the hood it runs the harness tests with the src/ `--cov` options
   disabled and measures only `scripts/e2e_lib/`:

   ```bash
   pytest tests/unit -k e2e -o addopts="" \
       --cov=scripts/e2e_lib --cov-fail-under=80
   ```

   Measured **~84%** at introduction (2026-06-03). The threshold is a floor
   intended to **ratchet upward**; never lower it silently. CI invokes the
   script as a dedicated stage.

## Current Baseline (feature/architecture-review)

- Full suite: **149 passed**
- Coverage gate: **80% required**
- Latest measured coverage: **~82%** (passes gate)

## Workflow Verification Commands

```bash
# Regression workflow
.venv/bin/python scripts/run_regression_workflow.py \
  --config config/pipeline/regression_workflow.sample.json \
  --summary-out reports/verification/premerge_regression_summary.json

# Manifest workflow
.venv/bin/python scripts/run_manifest_workflow.py \
  --manifest config/validation_manifest_10_scenarios.csv \
  --reports-dir reports/verification_manifest_p2

# Pipeline dry-runs
.venv/bin/python -m src.main run-pipeline \
  --config config/pipeline/source_profile.SRC_A.sample.json --dry-run \
  -o reports/verification/p2_pipeline_src_a.json

.venv/bin/python -m src.main run-pipeline \
  --config config/pipeline/source_profile.SRC_B.sample.json --dry-run \
  -o reports/verification/p2_pipeline_src_b.json
```

## Notes
- Some manifest scenarios are intentionally invalid; these are expected failures and should be captured in telemetry.
- Strict fixed-width validation is available in both non-chunked and chunked validate paths.

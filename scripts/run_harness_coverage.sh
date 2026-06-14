#!/bin/bash
#
# Coverage gate for the E2E batch-testing harness (scripts/e2e_lib/).
#
# Arch-review R-02-followup (#27), ADR 0011. The main pytest.ini coverage gate
# measures the src/ engine; the harness lives under scripts/e2e_lib/ and was
# previously unmeasured. This is a SEPARATE, independent job so a harness
# coverage regression is attributable on its own (it does not affect the
# src/ gate, and the src/ gate does not affect it).
#
# Usage:
#   ./scripts/run_harness_coverage.sh
#
# CI invokes this as its own stage/job (see ci/templates/gitlab-valdo-validate.yml
# and the external ops/pipelines/ci-config). Exit non-zero if the harness
# coverage is below HARNESS_MIN_COVERAGE.
#
# The threshold starts conservative and is intended to RATCHET upward over time
# (raise HARNESS_MIN_COVERAGE as harness tests grow); never lower it silently.

set -euo pipefail

# Minimum harness coverage percentage. Measured 84% on the dev box at
# introduction (2026-06-03); floor set below that to absorb environment
# variance (no Oracle / no server skips some paths). Ratchet upward, never down.
HARNESS_MIN_COVERAGE="${HARNESS_MIN_COVERAGE:-80}"

echo "=========================================="
echo "Valdo E2E harness coverage gate (scripts/e2e_lib/)"
echo "Minimum: ${HARNESS_MIN_COVERAGE}%"
echo "=========================================="

# -o addopts="" disables pytest.ini's src/ --cov options so this run measures
# ONLY the harness. -k e2e selects the harness test modules (tests/unit/test_e2e_*).
# Live-Oracle / SIT tests skip cleanly without credentials.
python -m pytest tests/unit -k e2e \
    -o addopts="" \
    --cov=scripts/e2e_lib \
    --cov-report=term-missing \
    --cov-report=html:htmlcov_harness \
    --cov-fail-under="${HARNESS_MIN_COVERAGE}" \
    -q

echo ""
echo "Harness coverage gate passed (>= ${HARNESS_MIN_COVERAGE}%)."
echo "HTML report: htmlcov_harness/index.html"

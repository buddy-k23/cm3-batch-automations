#!/usr/bin/env bash
# Valdo E2E batch testing — mode-aware wrapper (arch-review R-04b).
#
# Thin adapter that makes the EXECUTION MODE explicit while delegating ALL
# orchestration to the same core (scripts/e2e_lib/run_source.py). It adds no
# validation/comparison logic — it only selects mode-appropriate defaults and
# documents the per-mode contract, so every mode exercises the identical engine
# (the "one engine, four modes" parity guarantee, pinned by
# tests/unit/test_mode_parity.py).
#
# Modes:
#   integration   SIT/integration batch gating run (the default historical
#                 behaviour of run_e2e_source.sh). Blocking gates fail the run.
#   uat           UAT-style run. NOTE: there is NO separate `uat` ENVIRONMENT in
#                 this iteration (AGENTS.md: sit/ait only). "UAT" here is a
#                 PROFILE over the same engine: run against an approved env
#                 (sit|ait) with UAT data/baselines selected by the per-source
#                 config + baseline pinning. Identical engine; reporting/sign-off
#                 is the operator's, downstream of the same exit code.
#   ci            CI/CD pipeline stage. Same engine; the contract is purely the
#                 exit code (0 pass / 2 blocking-failure / 3 infra) consumed by
#                 the pipeline. Java shell-outs are disabled by default in CI
#                 (set VALDO_E2E_DISABLE_JAVA=0 to re-enable on a CI host that
#                 has the Java batch available).
#
# Usage:
#   bash scripts/run_e2e_mode.sh --mode ci --env sit --source SHAW \
#       --run-id "$(date +%Y%m%d_%H%M%S)"
#
# All flags other than --mode are forwarded UNCHANGED to run_source.py
# (--env, --source, --run-id, --paths-yaml, --sources-dir, --pipeline-yaml,
#  --valdo-executable). --env must be one of: sit, ait.
#
# Exit codes (mirrored from run_source.py, identical across all modes):
#   0 = all blocking gates passed
#   2 = a blocking gate failed
#   3 = infrastructure error / invalid usage

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

MODE=""
ENV_NAME=""
FORWARD=()

# Separate --mode (consumed here) from everything else (forwarded). We also
# capture --env so we can validate it without re-implementing the parser.
while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode)
      [[ $# -ge 2 ]] || { echo "error: --mode requires a value" >&2; exit 3; }
      MODE="$2"; shift 2 ;;
    --env)
      [[ $# -ge 2 ]] || { echo "error: --env requires a value" >&2; exit 3; }
      ENV_NAME="$2"; FORWARD+=("--env" "$2"); shift 2 ;;
    *)
      FORWARD+=("$1"); shift ;;
  esac
done

if [[ -z "${MODE}" ]]; then
  echo "error: --mode is required (integration|uat|ci)" >&2
  exit 3
fi

# AGENTS.md: only sit/ait exist this iteration. Refuse anything else so a
# stray 'uat'/'prod' env can never be silently invented here.
case "${ENV_NAME}" in
  sit|ait) : ;;
  "") echo "error: --env is required (sit|ait)" >&2; exit 3 ;;
  *)  echo "error: unsupported --env '${ENV_NAME}' (only sit|ait this iteration)" >&2; exit 3 ;;
esac

# Per-mode defaults. These only set documented env vars that run_source.py
# already honours; the engine path is identical in every mode.
case "${MODE}" in
  integration)
    : "${VALDO_E2E_DISABLE_JAVA:=0}" ;;
  uat)
    # Same engine; UAT data/baselines come from config + baseline pinning, not
    # from a different env. No engine override here by design.
    : "${VALDO_E2E_DISABLE_JAVA:=0}" ;;
  ci)
    # In a CI runner the upstream Java batch is usually unavailable; default to
    # skipping the Java shell-outs. Override with VALDO_E2E_DISABLE_JAVA=0.
    : "${VALDO_E2E_DISABLE_JAVA:=1}" ;;
  *)
    echo "error: unknown --mode '${MODE}' (expected integration|uat|ci)" >&2
    exit 3 ;;
esac
export VALDO_E2E_DISABLE_JAVA

PY="${VALDO_PYTHON:-}"
if [[ -z "${PY}" ]]; then
  if command -v python3 >/dev/null 2>&1; then PY="python3"
  elif command -v python >/dev/null 2>&1; then PY="python"
  else echo "error: no Python interpreter found (set VALDO_PYTHON)" >&2; exit 3; fi
fi

echo "valdo e2e mode=${MODE} env=${ENV_NAME} (engine: scripts.e2e_lib.run_source)" >&2

cd "${REPO_ROOT}"
exec "${PY}" -m scripts.e2e_lib.run_source "${FORWARD[@]}"

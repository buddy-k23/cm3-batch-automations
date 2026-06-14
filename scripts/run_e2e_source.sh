#!/usr/bin/env bash
# Valdo E2E batch testing — single-source wrapper (M4).
#
# Orchestrates one source end-to-end on a RHEL host:
#   1. Resolve paths for (env, source, run_id).
#   2. Create an isolated per-run work dir.
#   3. (Optional, configurable) shell out to java_scripts.load.
#   4. Run `valdo run-etl-pipeline` for the `file_to_staging` gate.
#   5. (Optional, configurable) shell out to java_scripts.generate.
#   6. Run `valdo run-etl-pipeline` for L1/L2/L3 gates.
#   7. Append rows to AUDIT.VALDO_RUN_FAILURES on any failed gate.
#   8. Write a per-source roll-up HTML.
#   9. Exit 0 if every blocking gate passed; 2 on blocking failure; 3 on
#      infrastructure error.
#
# The heavy lifting lives in scripts/e2e_lib/run_source.py so this wrapper
# stays a thin RHEL-specific shim. Python orchestration is unit-testable;
# bash is not.
#
# Usage:
#   bash scripts/run_e2e_source.sh \
#       --env sit --source SRC_A --run-id "$(date +%Y%m%d_%H%M%S)"
#
# Optional flags pass-through to the Python core:
#   --paths-yaml <path>      (default: config/e2e/paths.yml)
#   --sources-dir <path>     (default: config/e2e/sources)
#   --pipeline-yaml <path>   (default: config/e2e/pipelines/<env>/<source>.pipeline.yaml)
#   --valdo-executable <bin> (default: valdo)
#
# Behavior-affecting env vars (read by run_source.py):
#   VALDO_E2E_DISABLE_JAVA=1          skip Java load/generate shell-outs
#   VALDO_E2E_DISABLE_FAILURE_SINK=1  do not write to Oracle (still log JSONL)
#
# Exit codes (mirrored from run_source.py):
#   0 = all blocking gates passed
#   2 = a blocking gate failed
#   3 = infrastructure error (DB unreachable, missing config, etc.)

set -euo pipefail

# Resolve the repo root from this script's location so relative invocations work.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Pick the Python interpreter. Honor an explicit override; otherwise prefer
# python3, fall back to python (Windows dev environments).
PY="${VALDO_PYTHON:-}"
if [[ -z "${PY}" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PY="python3"
  elif command -v python >/dev/null 2>&1; then
    PY="python"
  else
    echo "error: no Python interpreter found (set VALDO_PYTHON)" >&2
    exit 3
  fi
fi

# Forward every argument unchanged. The Python core does its own arg parsing.
cd "${REPO_ROOT}"
exec "${PY}" -m scripts.e2e_lib.run_source "$@"

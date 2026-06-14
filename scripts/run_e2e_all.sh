#!/usr/bin/env bash
# Valdo E2E batch testing — multi-source fan-out wrapper (M6).
#
# Runs scripts/run_e2e_source.sh for every source under config/e2e/sources/
# in parallel (bounded concurrency), waits for all of them, then invokes
# scripts/build_rollup_index.py to produce the global roll-up.
#
# Usage:
#   bash scripts/run_e2e_all.sh --env sit
#       [--run-id 20260514_120000]      # default: $(date -u +%Y%m%d_%H%M%S)
#       [--concurrency 4]                # default: 4
#       [--source SRC_A] [--source SRC_B] ...   # filter (default: all)
#       [--paths-yaml config/e2e/paths.yml]
#       [--sources-dir config/e2e/sources]
#       [--valdo-executable valdo]
#       [--strict]                       # treat ANY failed gate as a fail
#
# Behavior-affecting env vars (forwarded to each per-source run):
#   VALDO_E2E_DISABLE_JAVA, VALDO_E2E_DISABLE_FAILURE_SINK
#
# Exit codes:
#   0 = every source's blocking gates passed
#   2 = at least one source had a blocking-gate failure
#   3 = infrastructure error (config invalid, no sources matched, etc.)
#
# Parallel safety:
#   * Each per-source run uses an isolated work_root and report_root keyed
#     by {run_id, source} (M1 path resolver + M4 wrapper).
#   * Each per-source run writes a dedicated JSONL log
#     logs/e2e_{run_id}_{source}.jsonl, so no log file is shared.
#   * No global temp files are touched by this script.

set -uo pipefail
# Note: do NOT set -e; we explicitly inspect per-job exit codes.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# ---- defaults ---------------------------------------------------------- #
ENV_NAME=""
RUN_ID=""
CONCURRENCY=4
PATHS_YAML="config/e2e/paths.yml"
SOURCES_DIR="config/e2e/sources"
VALDO_EXEC="valdo"
STRICT=0
SOURCE_FILTERS=()

# ---- args -------------------------------------------------------------- #
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env)             ENV_NAME="$2"; shift 2 ;;
    --run-id)          RUN_ID="$2"; shift 2 ;;
    --concurrency)     CONCURRENCY="$2"; shift 2 ;;
    --paths-yaml)      PATHS_YAML="$2"; shift 2 ;;
    --sources-dir)     SOURCES_DIR="$2"; shift 2 ;;
    --valdo-executable) VALDO_EXEC="$2"; shift 2 ;;
    --source)          SOURCE_FILTERS+=("$2"); shift 2 ;;
    --strict)          STRICT=1; shift ;;
    -h|--help)
      sed -n '2,40p' "$0"; exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      exit 3 ;;
  esac
done

if [[ -z "${ENV_NAME}" ]]; then
  echo "error: --env is required" >&2
  exit 3
fi
if [[ ! -f "${PATHS_YAML}" ]]; then
  echo "error: paths.yml not found: ${PATHS_YAML}" >&2
  exit 3
fi
if [[ ! -d "${SOURCES_DIR}" ]]; then
  echo "error: sources dir not found: ${SOURCES_DIR}" >&2
  exit 3
fi
if ! [[ "${CONCURRENCY}" =~ ^[0-9]+$ ]] || [[ "${CONCURRENCY}" -lt 1 ]]; then
  echo "error: --concurrency must be a positive integer (got '${CONCURRENCY}')" >&2
  exit 3
fi
if [[ -z "${RUN_ID}" ]]; then
  RUN_ID="$(date -u +%Y%m%d_%H%M%S)"
fi

# ---- python interpreter ----------------------------------------------- #
PY="${VALDO_PYTHON:-}"
if [[ -z "${PY}" ]]; then
  if   command -v python3 >/dev/null 2>&1; then PY="python3"
  elif command -v python  >/dev/null 2>&1; then PY="python"
  else
    echo "error: no Python interpreter found (set VALDO_PYTHON)" >&2
    exit 3
  fi
fi

# ---- enumerate sources ------------------------------------------------- #
ALL_SOURCES=()
while IFS= read -r f; do
  base="$(basename "${f}" .yml)"
  ALL_SOURCES+=("${base}")
done < <(find "${SOURCES_DIR}" -maxdepth 1 -type f -name '*.yml' | sort)

if [[ ${#ALL_SOURCES[@]} -eq 0 ]]; then
  echo "error: no sources found under ${SOURCES_DIR}" >&2
  exit 3
fi

SOURCES=()
if [[ ${#SOURCE_FILTERS[@]} -eq 0 ]]; then
  SOURCES=("${ALL_SOURCES[@]}")
else
  for want in "${SOURCE_FILTERS[@]}"; do
    found=0
    for have in "${ALL_SOURCES[@]}"; do
      if [[ "${have}" == "${want}" ]]; then
        SOURCES+=("${have}")
        found=1
        break
      fi
    done
    if [[ "${found}" -eq 0 ]]; then
      echo "error: --source ${want} not found under ${SOURCES_DIR}" >&2
      exit 3
    fi
  done
fi

# ---- resolve the global reports dir ----------------------------------- #
# Use the path resolver to derive <reports_root>/<run_id> by trimming the
# {source} segment off the report_root template. We do this in Python to
# avoid duplicating placeholder semantics in bash.
GLOBAL_REPORTS_DIR="$("${PY}" - <<PYEOF
import sys
from pathlib import Path
sys.path.insert(0, "${REPO_ROOT}".replace("\\\\","/"))
from scripts.e2e_lib.path_resolver import PathResolver, PathResolverError
try:
    resolver = PathResolver.from_files(
        Path("${PATHS_YAML}"), Path("${SOURCES_DIR}")
    )
    # Use the first known source as a probe; we only want the parent.
    probe = "${SOURCES[0]}"
    resolved = resolver.resolve(
        "report_root", env="${ENV_NAME}", source=probe, run_id="${RUN_ID}"
    )
    print(str(Path(resolved).parent))
except PathResolverError as exc:
    print(f"PATH_RESOLVER_ERROR: {exc}", file=sys.stderr)
    sys.exit(3)
PYEOF
)"
RESOLVE_RC=$?
if [[ ${RESOLVE_RC} -ne 0 ]] || [[ -z "${GLOBAL_REPORTS_DIR}" ]]; then
  echo "error: failed to resolve global reports dir" >&2
  exit 3
fi

echo "run_id=${RUN_ID} env=${ENV_NAME} sources=${#SOURCES[@]} concurrency=${CONCURRENCY}"
echo "global_reports_dir=${GLOBAL_REPORTS_DIR}"

# ---- fan out ---------------------------------------------------------- #
declare -a JOB_PIDS=()
declare -A PID_TO_SOURCE=()
declare -A SOURCE_RC=()
ACTIVE=0
BLOCKING_FAILURE=0
INFRA_ERROR=0

reap_one() {
  # Wait for any one background job; record its source/exit code.
  local rc pid src
  wait -n
  rc=$?
  # wait -n does not directly tell us which PID finished; we poll JOB_PIDS.
  for i in "${!JOB_PIDS[@]}"; do
    pid="${JOB_PIDS[$i]}"
    if ! kill -0 "${pid}" 2>/dev/null; then
      src="${PID_TO_SOURCE[${pid}]}"
      SOURCE_RC["${src}"]="${rc}"
      unset 'JOB_PIDS[i]'
      ACTIVE=$((ACTIVE - 1))
      case "${rc}" in
        0)  echo "  [PASS] ${src}";;
        2)  echo "  [FAIL] ${src}  (blocking gate failed)"
            BLOCKING_FAILURE=1 ;;
        3)  echo "  [INFRA] ${src} (exit 3)"
            INFRA_ERROR=1 ;;
        *)  echo "  [?]    ${src} (exit ${rc})"
            INFRA_ERROR=1 ;;
      esac
      return 0
    fi
  done
}

for src in "${SOURCES[@]}"; do
  while [[ ${ACTIVE} -ge ${CONCURRENCY} ]]; do
    reap_one
  done
  echo "  [RUN]  ${src}"
  bash "${SCRIPT_DIR}/run_e2e_source.sh" \
       --env "${ENV_NAME}" \
       --source "${src}" \
       --run-id "${RUN_ID}" \
       --paths-yaml "${PATHS_YAML}" \
       --sources-dir "${SOURCES_DIR}" \
       --valdo-executable "${VALDO_EXEC}" \
       >/dev/null 2>&1 &
  pid=$!
  JOB_PIDS+=("${pid}")
  PID_TO_SOURCE["${pid}"]="${src}"
  ACTIVE=$((ACTIVE + 1))
done

# Drain remaining.
while [[ ${ACTIVE} -gt 0 ]]; do
  reap_one
done

# ---- global roll-up --------------------------------------------------- #
ROLLUP_ARGS=(--reports-dir "${GLOBAL_REPORTS_DIR}" --run-id "${RUN_ID}" --env "${ENV_NAME}")
if [[ "${STRICT}" -eq 1 ]]; then
  ROLLUP_ARGS+=(--strict)
fi

set +e
"${PY}" -m scripts.build_rollup_index "${ROLLUP_ARGS[@]}"
ROLLUP_RC=$?
set -e

# ---- final exit ------------------------------------------------------- #
if [[ ${INFRA_ERROR} -ne 0 ]] || [[ ${ROLLUP_RC} -eq 3 ]]; then
  echo "summary: INFRASTRUCTURE ERROR (rollup_rc=${ROLLUP_RC})"
  exit 3
fi
if [[ ${BLOCKING_FAILURE} -ne 0 ]] || [[ ${ROLLUP_RC} -eq 2 ]]; then
  echo "summary: BLOCKING FAILURE in one or more sources"
  exit 2
fi
echo "summary: ALL PASSED"
exit 0

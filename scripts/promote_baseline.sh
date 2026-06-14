#!/usr/bin/env bash
# Valdo E2E batch testing — baseline promotion wrapper (M5).
#
# Promotes a QA-signed-off run's Java-generated outputs to the next baseline
# release and appends a new pin to baselines/manifest.json. CODEOWNERS review
# on the resulting MR is the actual approval gate; this wrapper just records
# the approver's identity in the manifest.
#
# Usage:
#   bash scripts/promote_baseline.sh \
#       --env sit --source SRC_A \
#       --release-tag R2026.06 \
#       --run-output-dir /data/sit/_reports/20260514_120000/SRC_A \
#       --approved-by qa-lead@example.com \
#       --comment "Refresh after mapping bump in MR !1234" \
#       [--file-type P327] [--file-type P328] ... \
#       [--force]      # overwrite an existing baseline file
#       [--dry-run]    # print actions, do not write
#
# Exit codes:
#   0 = success
#   2 = user / config error (e.g. unknown source, no matching file)
#
# Idempotency:
#   Re-running with the same (env, source, file_type, mapping_version,
#   release_tag) tuple will NOT append a duplicate manifest entry; the
#   tuple is skipped and listed under "skipped" in the summary JSON.
#
# Safety:
#   The wrapper refuses to overwrite an existing baseline file unless
#   --force is supplied. This guarantees mapping_version and baseline
#   change atomically — see baselines/manifest.json header comment.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

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

cd "${REPO_ROOT}"
exec "${PY}" -m scripts.e2e_lib.promote_baseline "$@"

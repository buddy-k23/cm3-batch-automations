#!/usr/bin/env bash
# Workbook drift CI guardrail wrapper (EC-S11, Sprint 3).
#
# Thin shell wrapper around scripts/check_workbook_drift.py. The Python
# helper holds ALL the logic (workbook discovery, --check invocation,
# allowlist filter, summary, exit code). Both the GitHub Actions
# workflow (.github/workflows/workbook-drift-check.yml) and local
# developer runs invoke this script, so CI and local cannot drift apart.
#
# Usage:
#   ./scripts/check_workbook_drift.sh
#   ./scripts/check_workbook_drift.sh templates/SHAW_onboarding.xlsx
#
# Exit codes (proxied verbatim from the Python helper):
#   0 -- all workbooks pass (or only allowlisted drift reported).
#   1 -- one or more workbooks reported non-allowlisted drift.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Honour an explicit PYTHON override (CI may pin python3.11 etc.).
PYTHON_BIN="${PYTHON:-python3}"

cd "${REPO_ROOT}"
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/check_workbook_drift.py" "$@"

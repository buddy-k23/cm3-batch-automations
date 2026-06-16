#!/usr/bin/env bash
# ETL templates drift CI guardrail wrapper (S8-5, #384).
#
# Thin shell wrapper around scripts/check_etl_templates.py. The Python
# helper holds ALL the logic (template discovery, SourceConfig parse
# check, paired-sample run, allowlist filter, summary, exit code). Both
# the GitHub Actions workflow (.github/workflows/etl-templates-check.yml)
# and local developer runs invoke this script, so CI and local cannot
# drift apart.
#
# Usage:
#   ./scripts/check_etl_templates.sh
#   ./scripts/check_etl_templates.sh templates/etl/csv_file_comparison.yml
#
# Exit codes (proxied verbatim from the Python helper):
#   0 -- every template passes (or only allowlisted / carve-out drift).
#   1 -- one or more templates reported non-allowlisted drift.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# Honour an explicit PYTHON override (CI may pin python3.11 etc.).
PYTHON_BIN="${PYTHON:-python3}"

cd "${REPO_ROOT}"
exec "${PYTHON_BIN}" "${SCRIPT_DIR}/check_etl_templates.py" "$@"

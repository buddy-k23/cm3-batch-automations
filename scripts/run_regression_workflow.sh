#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

CONFIG="${1:-config/pipeline/regression_workflow.sample.json}"
SUMMARY_OUT="${2:-reports/regression_workflow/summary.json}"

cd "$PROJECT_ROOT"
.venv/bin/python scripts/run_regression_workflow.py --config "$CONFIG" --summary-out "$SUMMARY_OUT"

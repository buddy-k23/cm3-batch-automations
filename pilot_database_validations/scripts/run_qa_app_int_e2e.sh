#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/buddy/.openclaw/workspace/database-validations"
APP_VENV="/Users/buddy/.openclaw/workspace/valdo/.venv/bin/activate"

if [[ ! -f "$APP_VENV" ]]; then
  echo "ERROR: App venv not found at $APP_VENV"
  exit 1
fi

source "$APP_VENV"

echo "[1/2] Running APP_INT Oracle E2E demo..."
python "$ROOT/tools/e2e_app_int_oracle.py"

echo "[2/2] Generating Wave artifacts from E2E output..."
python "$ROOT/tools/e2e_app_int_to_wave_artifacts.py"

echo "Done. Key outputs:"
echo "- $ROOT/generated/e2e_app_int/summary.txt"
echo "- $ROOT/generated/e2e_app_int/wave_artifacts/summary-report.json"
echo "- $ROOT/generated/e2e_app_int/wave_artifacts/telemetry.json"
echo "- $ROOT/generated/e2e_app_int/wave_artifacts/promotion-evidence.json"

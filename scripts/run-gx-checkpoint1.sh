#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

if [[ ! -d ".venv312" ]]; then
  echo "Missing .venv312. Create it first with Python 3.12."
  exit 1
fi

source .venv312/bin/activate
export PYTHONPATH="$REPO_DIR"

python -m src.main gx-checkpoint1 \
  --targets "${1:-config/gx/targets.sample.csv}" \
  --expectations "${2:-config/gx/expectations.sample.csv}" \
  --output "${3:-reports/gx_checkpoint1_summary.json}"

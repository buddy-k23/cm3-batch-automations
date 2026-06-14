#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

MANIFEST="${1:-config/validation_manifest.csv}"
AUTO_DISCOVER="${2:-false}"

cd "$PROJECT_ROOT"

CMD=(.venv/bin/python scripts/validate_data_files.py --manifest "$MANIFEST" --default-chunked)
if [[ "$AUTO_DISCOVER" == "true" ]]; then
  CMD+=(--auto-discover)
fi

"${CMD[@]}"

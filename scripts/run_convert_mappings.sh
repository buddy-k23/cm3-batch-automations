#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INPUT_DIR="${1:-mappings/csv}"
OUTPUT_DIR="${2:-config/mappings}"
FORMAT="${3:-}"

cd "$PROJECT_ROOT"

CMD=(".venv/bin/python" "scripts/bulk_convert_mappings.py" "--input-dir" "$INPUT_DIR" "--output-dir" "$OUTPUT_DIR")
if [[ -n "$FORMAT" ]]; then
  CMD+=("--format" "$FORMAT")
fi

"${CMD[@]}"

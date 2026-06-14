#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

INPUT_DIR="${1:-rules/csv}"
OUTPUT_DIR="${2:-config/rules}"

cd "$PROJECT_ROOT"

.venv/bin/python scripts/bulk_convert_rules.py --input-dir "$INPUT_DIR" --output-dir "$OUTPUT_DIR"

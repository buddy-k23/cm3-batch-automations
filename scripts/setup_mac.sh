#!/usr/bin/env bash
set -euo pipefail

# Beginner-friendly setup for macOS (also works on most Linux distros with bash)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$PROJECT_ROOT"

echo "[1/6] Checking Python..."
if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ python3 not found. Install Python 3.11+ and re-run."
  exit 1
fi

PY_VER=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "✅ Found Python $PY_VER"

echo "[2/6] Creating virtual environment (.venv)..."
python3 -m venv .venv

# shellcheck disable=SC1091
source .venv/bin/activate

echo "[3/6] Upgrading pip..."
python -m pip install --upgrade pip

echo "[4/6] Installing project dependencies..."
pip install -r requirements-dev.txt

echo "[5/6] Installing CLI entrypoint (editable)..."
pip install -e .

echo "[6/6] Creating working folders..."
mkdir -p reports mappings/csv rules/csv data/files

echo ""
echo "🎉 Setup complete"
echo "Try these commands:"
echo "  source .venv/bin/activate"
echo "  valdo --help"
echo "  valdo info"

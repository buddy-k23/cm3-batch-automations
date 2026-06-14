#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'EOF'
Usage:
  source scripts/env-switch.sh 314   # activate existing .venv (py3.14)
  source scripts/env-switch.sh 312   # activate .venv312 (py3.12)
  source scripts/env-switch.sh off   # deactivate current venv
EOF
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "Run this script with: source scripts/env-switch.sh <312|314|off>"
  exit 1
fi

TARGET="${1:-}"
if [[ -z "$TARGET" ]]; then
  usage
  return 1
fi

case "$TARGET" in
  314)
    source "$REPO_DIR/.venv/bin/activate"
    echo "Activated .venv (Python $(python --version 2>&1))"
    ;;
  312)
    source "$REPO_DIR/.venv312/bin/activate"
    echo "Activated .venv312 (Python $(python --version 2>&1))"
    ;;
  off)
    if type deactivate >/dev/null 2>&1; then
      deactivate
      echo "Virtual environment deactivated"
    else
      echo "No active virtual environment"
    fi
    ;;
  *)
    usage
    return 1
    ;;
esac

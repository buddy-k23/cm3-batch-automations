#!/usr/bin/env bash
#
# End-to-end #407 cross-dialect reconcile demo driver.
#
# Runs BOTH demo segments against a freshly-seeded SQLite database:
#   1. Seed the SQLite DB + mapping JSON.
#   2. MCP segment   — calls the real `reconcile_mapping` MCP tool over the
#                      MCP Streamable-HTTP transport; writes a JSON + TXT
#                      transcript under artifacts/.
#   3. UI segment    — boots a real uvicorn server (plain HTTP, auth off) and
#                      drives the DB Compare > Reconcile panel with Playwright;
#                      captures two screenshots under artifacts/.
#
# Usage (from anywhere):
#   ./demo/reconcile_e2e/run_demo.sh
#
# Honours PY (path to a python interpreter); defaults to ./.venv/bin/python
# at the repo root, falling back to `python3`.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
cd "${REPO_ROOT}"

if [[ -n "${PY:-}" ]]; then
  PYBIN="${PY}"
elif [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  PYBIN="${REPO_ROOT}/.venv/bin/python"
else
  PYBIN="python3"
fi

echo "==> repo root  : ${REPO_ROOT}"
echo "==> python     : ${PYBIN}"
echo

echo "==> [1/3] Seeding SQLite DB + mapping"
"${PYBIN}" demo/reconcile_e2e/seed/seed_db.py
echo

echo "==> [2/3] MCP segment (reconcile_mapping over MCP transport)"
"${PYBIN}" demo/reconcile_e2e/run_mcp_demo.py
echo

echo "==> [3/3] UI segment (Playwright -> DB Compare > Reconcile panel)"
"${PYBIN}" demo/reconcile_e2e/run_ui_demo.py
echo

echo "==> Done. Artifacts:"
ls -1 "${SCRIPT_DIR}/artifacts"

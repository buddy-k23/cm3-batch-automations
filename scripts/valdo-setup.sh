#!/usr/bin/env bash
#
# valdo-setup.sh — single, OS-agnostic LOCAL bootstrap for Valdo (S10-3, #398).
#
# One command from a fresh clone to a running Valdo on SQLite with zero
# external infrastructure:
#
#   bash scripts/valdo-setup.sh
#
# What it does (local, the default and only implemented env):
#   1. Resolve the repo root from this script's own location (no hardcoded paths).
#   2. Detect the OS (macOS / Linux / RHEL / WSL); fail clearly on anything else.
#   3. Find a Python 3.11+ interpreter and create/refresh the .venv.
#   4. pip install -e . plus requirements-api.txt (runnable + testable stack).
#   5. Create .env from .env.example ONLY if missing (never clobber); ensure the
#      local SQLite defaults (DB_ADAPTER=sqlite, DB_PATH=valdo.db) so no Oracle
#      is needed.
#   6. Create the working directories the app expects (idempotent).
#   7. Run `alembic upgrade head` against the local SQLite DB.
#   8. Smoke-check the install (python import + `valdo info`) and print next steps.
#
# The script is idempotent and safely re-runnable: re-running refreshes the
# venv/deps and re-migrates, and never overwrites an existing .env.
#
# Environments:
#   --env local        (default) full local setup described above.
#   --env int          deferred — prints a roadmap note and exits 0 (seam only).
#   --env full-stack   deferred — prints a roadmap note and exits 0 (seam only).
#
# Windows note: this script targets bash environments (incl. WSL and Git Bash).
# Native Windows users should use scripts/setup_windows.ps1 or setup-windows.bat.
#
# The per-OS scripts (setup-linux.sh, scripts/setup_mac.sh, scripts/setup_rhel.sh)
# are retained; this is the consolidated, recommended local entry point.

set -euo pipefail

# --- Why requirements-api.txt (not requirements-dev.txt) ---------------------
# requirements-api.txt yields a runnable AND testable install: it pulls the full
# API/MCP runtime (fastapi, uvicorn, mcp, oracledb-thin, click) plus pytest and
# pytest-cov, so `valdo serve`, the MCP server, and `pytest tests/unit/` all
# work after setup. requirements-dev.txt layers on heavy authoring tools
# (jupyter, sphinx, playwright) that a local bootstrap does not need.
REQUIREMENTS_FILE="requirements-api.txt"

# --- Minimum supported Python (3.11+) ---------------------------------------
PY_MIN_MAJOR=3
PY_MIN_MINOR=11

# --- Resolve repo root from this script's own location (principle #5) --------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# --- Defaults / arg parsing --------------------------------------------------
ENV_TARGET="local"

print_help() {
    cat <<'EOF'
Valdo local setup — one command from a fresh clone to a running Valdo.

Usage:
  bash scripts/valdo-setup.sh [--env <local|int|full-stack>] [-h|--help]

Options:
  --env local        Full local setup on SQLite (default). No external DB needed.
  --env int          Deferred to a future sprint (seam only) — exits cleanly.
  --env full-stack   Deferred to a future sprint (seam only) — exits cleanly.
  -h, --help         Show this help and exit.

After a successful local run:
  source .venv/bin/activate     # (.venv/Scripts/activate on Git Bash/Windows)
  valdo serve                   # UI at http://localhost:8000/ui
EOF
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        --env)
            if [ "$#" -lt 2 ]; then
                echo "ERROR: --env requires a value (local|int|full-stack)." >&2
                exit 2
            fi
            ENV_TARGET="$2"
            shift 2
            ;;
        --env=*)
            ENV_TARGET="${1#*=}"
            shift
            ;;
        -h|--help)
            print_help
            exit 0
            ;;
        *)
            echo "ERROR: unknown argument: $1" >&2
            echo "Run 'bash scripts/valdo-setup.sh --help' for usage." >&2
            exit 2
            ;;
    esac
done

# --- Deferred environment seams ---------------------------------------------
case "$ENV_TARGET" in
    local)
        ;;
    int|full-stack)
        echo "The '--env ${ENV_TARGET}' setup is deferred to a future sprint."
        echo "Only '--env local' (SQLite, zero external infra) is implemented today."
        echo "See docs/sprints/SPRINT_10_KICKOFF.md (\"After Sprint 10\" roadmap):"
        echo "  - full-stack: local docker-compose app + Postgres"
        echo "  - int:        INT-region Oracle wiring"
        exit 0
        ;;
    *)
        echo "ERROR: invalid --env value: '${ENV_TARGET}'." >&2
        echo "Valid values: local, int, full-stack." >&2
        exit 2
        ;;
esac

cd "$PROJECT_ROOT"

echo "========================================="
echo " Valdo — local setup (SQLite, zero infra)"
echo "========================================="
echo "Repo root: ${PROJECT_ROOT}"
echo ""

# --- [1/8] Detect OS ---------------------------------------------------------
echo "[1/8] Detecting operating system..."
IS_WINDOWS_BASH=false
OS_LABEL=""
UNAME_S="$(uname -s)"
case "$UNAME_S" in
    Darwin)
        OS_LABEL="macOS"
        ;;
    Linux)
        # WSL reports Linux but exposes "microsoft" in /proc/version.
        if grep -qiE "(microsoft|wsl)" /proc/version 2>/dev/null; then
            OS_LABEL="Linux (WSL)"
        elif [ -f /etc/redhat-release ]; then
            OS_LABEL="Linux (RHEL/CentOS/Rocky)"
        else
            OS_LABEL="Linux"
        fi
        ;;
    MINGW*|MSYS*|CYGWIN*)
        OS_LABEL="Windows (Git Bash)"
        IS_WINDOWS_BASH=true
        ;;
    *)
        echo "ERROR: unsupported operating system: '${UNAME_S}'." >&2
        echo "This script supports macOS, Linux, RHEL, and WSL/Git Bash." >&2
        echo "Native Windows users: use scripts/setup_windows.ps1 or setup-windows.bat." >&2
        exit 1
        ;;
esac
echo "      OS: ${OS_LABEL}"

if [ "$IS_WINDOWS_BASH" = true ]; then
    VENV_ACTIVATE=".venv/Scripts/activate"
    VENV_PY=".venv/Scripts/python"
    VENV_ALEMBIC=".venv/Scripts/alembic"
else
    VENV_ACTIVATE=".venv/bin/activate"
    VENV_PY=".venv/bin/python"
    VENV_ALEMBIC=".venv/bin/alembic"
fi

# --- [2/8] Detect a suitable Python 3.11+ ------------------------------------
echo "[2/8] Locating Python ${PY_MIN_MAJOR}.${PY_MIN_MINOR}+..."
PYTHON_CMD=""
for candidate in python3.13 python3.12 python3.11 python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" --version >/dev/null 2>&1; then
        if "$candidate" -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (${PY_MIN_MAJOR}, ${PY_MIN_MINOR}) else 1)" >/dev/null 2>&1; then
            PYTHON_CMD="$candidate"
            break
        fi
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "ERROR: no Python ${PY_MIN_MAJOR}.${PY_MIN_MINOR}+ interpreter found on PATH." >&2
    echo "Install Python ${PY_MIN_MAJOR}.${PY_MIN_MINOR}+ and re-run, e.g.:" >&2
    echo "  macOS:        brew install python@3.11" >&2
    echo "  Debian/Ubuntu: sudo apt install python3.11 python3.11-venv" >&2
    echo "  RHEL/CentOS:  sudo dnf install python3.11" >&2
    exit 1
fi
echo "      Using '$PYTHON_CMD' ($("$PYTHON_CMD" --version 2>&1))"

# --- [3/8] Create / reuse the virtual environment ----------------------------
echo "[3/8] Preparing virtual environment (.venv)..."
if [ -d ".venv" ]; then
    echo "      .venv already exists — reusing (re-run refreshes deps below)."
else
    "$PYTHON_CMD" -m venv .venv
    echo "      Created .venv"
fi

# Use the venv interpreter directly rather than relying on `source`, so the
# script works identically whether or not the venv is pre-activated.
if [ ! -x "$VENV_PY" ] && [ ! -f "$VENV_PY" ]; then
    echo "ERROR: virtual environment Python not found at ${VENV_PY}." >&2
    exit 1
fi

# --- [4/8] Install dependencies ----------------------------------------------
echo "[4/8] Installing dependencies (this may take a minute)..."
"$VENV_PY" -m pip install --upgrade pip --quiet
echo "      Installing project (pip install -e .)..."
"$VENV_PY" -m pip install -e . --quiet
if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "      Installing ${REQUIREMENTS_FILE}..."
    "$VENV_PY" -m pip install -r "$REQUIREMENTS_FILE" --quiet
else
    echo "      WARNING: ${REQUIREMENTS_FILE} not found — skipping API deps." >&2
fi

# --- [5/8] Create .env (only if missing) with local SQLite defaults ----------
echo "[5/8] Configuring .env (local SQLite defaults)..."
if [ -f ".env" ]; then
    echo "      .env already exists — leaving it untouched (no clobber)."
    echo "      NOTE: for zero-infra local, ensure it has DB_ADAPTER=sqlite and"
    echo "            DB_PATH=valdo.db (the SQLite defaults this script writes for"
    echo "            a fresh .env)."
elif [ -f ".env.example" ]; then
    cp .env.example .env
    # Force the local zero-infra defaults so no external Oracle is needed.
    # We append explicit overrides; the SQLite adapter ignores ORACLE_* vars.
    {
        echo ""
        echo "# === Local setup overrides (written by scripts/valdo-setup.sh) ==="
        echo "# SQLite is the zero-infra local default — no external database needed."
        echo "DB_ADAPTER=sqlite"
        echo "DB_PATH=valdo.db"
    } >> .env
    echo "      Created .env from .env.example with DB_ADAPTER=sqlite, DB_PATH=valdo.db."
else
    echo "      WARNING: .env.example not found — cannot create .env." >&2
fi

# --- [6/8] Create working directories (idempotent) ---------------------------
echo "[6/8] Creating working directories..."
# These match the dirs the app and the existing per-OS scripts expect.
mkdir -p uploads logs reports mappings rules data
echo "      uploads/ logs/ reports/ mappings/ rules/ data/ ready."

# --- [7/8] Run database migrations against local SQLite ----------------------
echo "[7/8] Running database migrations (alembic upgrade head, SQLite)..."
# Run alembic via the venv console entry point and force the SQLite adapter for
# this step so migrations never reach for Oracle even if .env says otherwise.
if [ -f "alembic.ini" ]; then
    if DB_ADAPTER=sqlite DB_PATH="${DB_PATH:-valdo.db}" "$VENV_ALEMBIC" upgrade head; then
        echo "      Migrations applied to ${DB_PATH:-valdo.db}."
    else
        echo "ERROR: 'alembic upgrade head' failed. See output above." >&2
        exit 1
    fi
else
    echo "      WARNING: alembic.ini not found — skipping migrations." >&2
fi

# --- [8/8] Smoke checks -------------------------------------------------------
echo "[8/8] Verifying the installation..."
echo "      - Python import smoke (import src.main)..."
if ! "$VENV_PY" -c "import src.main" >/dev/null 2>&1; then
    echo "ERROR: failed to import src.main — the install is not runnable." >&2
    exit 1
fi
echo "      - CLI smoke (valdo info)..."
if "$VENV_PY" -m src.main info >/dev/null 2>&1; then
    echo "      valdo info OK."
elif "$VENV_PY" -m src.main --help >/dev/null 2>&1; then
    echo "      valdo --help OK (valdo info unavailable, CLI is functional)."
else
    echo "ERROR: CLI smoke check failed (neither 'valdo info' nor 'valdo --help' ran)." >&2
    exit 1
fi

echo ""
echo "========================================="
echo " Setup complete — local Valdo on SQLite"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Activate the virtual environment:"
echo "       source ${VENV_ACTIVATE}"
echo ""
echo "  2. Start the server:"
echo "       valdo serve"
echo "     Then open the Web UI at: http://localhost:8000/ui"
echo ""
echo "  3. (Optional) Run the unit tests:"
echo "       python3 -m pytest tests/unit/ -q"
echo ""
echo "Local DB: SQLite at ${PROJECT_ROOT}/${DB_PATH:-valdo.db} (no external Oracle needed)."
echo "For INT/full-stack environments, see docs/INSTALL.md (deferred this sprint)."

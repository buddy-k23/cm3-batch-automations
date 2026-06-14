#!/bin/bash
# Valdo — Setup Script (Linux / Git Bash on Windows)
# Run from the project root directory.
# Usage: bash setup-linux.sh
#
# Linux:
#   chmod +x setup-linux.sh
#   ./setup-linux.sh
#
# Windows (Git Bash):
#   bash setup-linux.sh
#
# NOTE: When deploying from the CI/CD pipeline (Windows build agent -> RHEL),
# ensure dos2unix has been run on all files and pip.conf is configured for the
# internal Artifactory PyPI mirror before executing this script.
# See azure-pipelines-deploy-onprem.yml Task 6 for reference.

set -e

echo ""
echo "========================================="
echo " Valdo — Setup"
echo "========================================="
echo ""

# --- Detect platform ---
IS_WINDOWS=false
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) IS_WINDOWS=true ;;
esac

if [ "$IS_WINDOWS" = true ]; then
    echo "Platform: Windows (Git Bash)"
    VENV_ACTIVATE=".venv/Scripts/activate"
else
    echo "Platform: Linux"
    VENV_ACTIVATE=".venv/bin/activate"
fi

# --- Detect if already running inside the .venv ---
VENV_ACTIVE=false
if [ -n "$VIRTUAL_ENV" ] && [ -d ".venv" ]; then
    VENV_ACTIVE=true
    echo "Virtual environment is already active."
fi

# --- Resolve python command ---
# On Windows, "python3" may resolve to a Microsoft Store stub that is not a
# real interpreter.  We verify each candidate actually runs before accepting it.
PYTHON_CMD=""
for candidate in python3 python py; do
    if command -v "$candidate" &>/dev/null && "$candidate" --version &>/dev/null; then
        PYTHON_CMD="$candidate"
        break
    fi
done

if [ -z "$PYTHON_CMD" ]; then
    echo "ERROR: A working Python interpreter was not found on your PATH."
    echo ""
    if [ "$IS_WINDOWS" = true ]; then
        echo "Install Python 3.10+ from https://www.python.org/downloads/"
        echo "or via:  winget install Python.Python.3.12"
        echo ""
        echo "TIP: If Python is installed but 'python3' opens the Microsoft Store,"
        echo "disable the app alias in Settings > Apps > App execution aliases."
    else
        echo "Install Python 3.10+ via your package manager, for example:"
        echo "  sudo dnf install python3.11     # RHEL / CentOS"
        echo "  sudo apt install python3.11     # Debian / Ubuntu"
        echo "Or install from an internal mirror if internet access is restricted."
    fi
    exit 1
fi

PYVER=$($PYTHON_CMD --version 2>&1)
echo "Found $PYVER (using '$PYTHON_CMD')"

# --- Create virtual environment if it does not already exist ---
if [ "$VENV_ACTIVE" = false ]; then
    if [ ! -d ".venv" ]; then
        echo ""
        echo "Creating virtual environment in .venv ..."
        $PYTHON_CMD -m venv .venv
        echo "Done."
    else
        echo "Virtual environment already exists, skipping creation."
    fi

    # --- Activate virtual environment ---
    echo ""
    echo "Activating virtual environment ..."
    # shellcheck disable=SC1090
    source "$VENV_ACTIVATE"
fi

# --- Upgrade pip ---
echo "Upgrading pip ..."
pip install --upgrade pip --quiet

# --- Install project dependencies ---
echo ""
echo "Installing dependencies (pip install -e .) ..."
pip install -e .

# --- Install API dependencies ---
if [ -f "requirements-api.txt" ]; then
    echo ""
    echo "Installing API dependencies (requirements-api.txt) ..."
    pip install -r requirements-api.txt --quiet
else
    echo "requirements-api.txt not found, skipping API dependencies."
fi

# --- Copy .env.example to .env if .env does not exist ---
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo ""
        echo "Copying .env.example to .env ..."
        cp .env.example .env
        echo "Done. Edit .env and fill in your Oracle credentials."
    else
        echo "WARNING: .env.example not found. Please create a .env file manually."
    fi
else
    echo ".env already exists, skipping copy."
fi

# --- Create uploads directory if missing ---
if [ ! -d "uploads" ]; then
    mkdir -p uploads
    [ "$IS_WINDOWS" = false ] && chmod 755 uploads
fi

# --- Create logs directory if missing ---
if [ ! -d "logs" ]; then
    mkdir -p logs
    [ "$IS_WINDOWS" = false ] && chmod 755 logs
fi

# --- Run Alembic database migrations ---
if [ -f "alembic.ini" ]; then
    echo ""
    echo "Running database migrations (alembic upgrade head) ..."
    alembic upgrade head || echo "WARNING: Alembic migration failed. Check .env database settings."
fi

# --- Print next steps ---
echo ""
echo "========================================="
echo " Setup complete!"
echo "========================================="
echo ""
echo "Next steps:"
echo "  1. Edit .env and set ORACLE_USER, ORACLE_PASSWORD, and ORACLE_DSN"
echo "     (format: host:port/service_name — no Oracle Instant Client needed)"
echo ""
echo "  2. Activate the virtual environment in each new shell session:"
echo "       source $VENV_ACTIVATE"
echo ""
echo "  3. Verify the installation:"
echo "       valdo --help"
echo ""
echo "  4. Start the API server (optional):"
echo "       uvicorn src.api.main:app --host 0.0.0.0 --port 8000"
echo ""
if [ "$IS_WINDOWS" = false ]; then
    echo "  5. To run as a systemd service, see docs/INSTALL.md — Linux Installation."
    echo ""
fi

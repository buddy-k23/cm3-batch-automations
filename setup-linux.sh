#!/bin/bash
# CM3 Batch Automations — Linux Setup Script
# Run from the project root directory.
# Usage: bash setup-linux.sh
#
# To make it executable first:
#   chmod +x setup-linux.sh
#   ./setup-linux.sh

set -e

echo ""
echo "========================================="
echo " CM3 Batch Automations — Linux Setup"
echo "========================================="
echo ""

# --- Check Python3 is available ---
if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 was not found on your PATH."
    echo ""
    echo "Install Python 3.10+ via your package manager, for example:"
    echo "  sudo dnf install python3.11     # RHEL / CentOS"
    echo "  sudo apt install python3.11     # Debian / Ubuntu"
    echo "Or install from an internal mirror if internet access is restricted."
    exit 1
fi

PYVER=$(python3 --version 2>&1)
echo "Found $PYVER"

# --- Create virtual environment if it does not already exist ---
if [ ! -d ".venv" ]; then
    echo ""
    echo "Creating virtual environment in .venv ..."
    python3 -m venv .venv
    echo "Done."
else
    echo "Virtual environment already exists, skipping creation."
fi

# --- Activate virtual environment ---
echo ""
echo "Activating virtual environment ..."
# shellcheck disable=SC1091
source .venv/bin/activate

# --- Upgrade pip ---
echo "Upgrading pip ..."
pip install --upgrade pip --quiet

# --- Install project dependencies ---
echo ""
echo "Installing dependencies (pip install -e .) ..."
pip install -e .

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
    chmod 755 uploads
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
echo "       source .venv/bin/activate"
echo ""
echo "  3. Verify the installation:"
echo "       cm3-batch --help"
echo ""
echo "  4. Start the API server (optional):"
echo "       uvicorn src.api.main:app --host 0.0.0.0 --port 8000"
echo ""
echo "  5. To run as a systemd service, see docs/INSTALL.md — Linux Installation."
echo ""

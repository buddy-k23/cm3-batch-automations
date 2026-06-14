#!/usr/bin/env bash
set -euo pipefail

# Manual code-only deployment for RHEL
# - Clones repo/branch
# - Creates Python venv and installs dependencies
# - Optionally prunes non-code files/folders
#
# Usage:
#   bash scripts/deploy_rhel_code_only.sh \
#     --repo https://github.com/pkanduri1/valdo.git \
#     --branch feature/app-business-rules-oracle-smoke \
#     --target /opt/valdo \
#     --oracle-user APP_INT \
#     --oracle-password '***' \
#     --oracle-dsn 'dbhost:1521/FREEPDB1' \
#     --prune
#
# Notes:
# - Requires: git, python3, pip
# - Safe to run multiple times; existing target dir will be reused.

REPO_URL="https://github.com/pkanduri1/valdo.git"
BRANCH="main"
TARGET_DIR="/opt/valdo"
PRUNE=false
ORACLE_USER=""
ORACLE_PASSWORD=""
ORACLE_DSN=""
ENVIRONMENT="dev"
LOG_LEVEL="INFO"

print_help() {
  cat <<'EOF'
Manual code-only deploy script for Valdo

Options:
  --repo <url>              Git repo URL (default: pkanduri1/valdo)
  --branch <name>           Git branch to deploy (default: main)
  --target <path>           Target directory (default: /opt/valdo)
  --oracle-user <user>      Oracle username (required)
  --oracle-password <pass>  Oracle password (required)
  --oracle-dsn <dsn>        Oracle DSN host:port/service (required)
  --environment <env>       ENVIRONMENT value for .env (default: dev)
  --log-level <level>       LOG_LEVEL value for .env (default: INFO)
  --prune                   Remove non-code directories/files after install
  --help                    Show this help

Example:
  sudo bash deploy_rhel_code_only.sh \
    --branch feature/app-business-rules-oracle-smoke \
    --target /opt/valdo \
    --oracle-user APP_INT \
    --oracle-password 'secret' \
    --oracle-dsn 'dbhost:1521/FREEPDB1' \
    --prune
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO_URL="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --target) TARGET_DIR="$2"; shift 2 ;;
    --oracle-user) ORACLE_USER="$2"; shift 2 ;;
    --oracle-password) ORACLE_PASSWORD="$2"; shift 2 ;;
    --oracle-dsn) ORACLE_DSN="$2"; shift 2 ;;
    --environment) ENVIRONMENT="$2"; shift 2 ;;
    --log-level) LOG_LEVEL="$2"; shift 2 ;;
    --prune) PRUNE=true; shift ;;
    --help|-h) print_help; exit 0 ;;
    *) echo "Unknown option: $1"; print_help; exit 1 ;;
  esac
done

if [[ -z "$ORACLE_USER" || -z "$ORACLE_PASSWORD" || -z "$ORACLE_DSN" ]]; then
  echo "ERROR: --oracle-user, --oracle-password, and --oracle-dsn are required."
  exit 1
fi

command -v git >/dev/null 2>&1 || { echo "ERROR: git not found"; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "ERROR: python3 not found"; exit 1; }

echo "[1/7] Preparing target directory: $TARGET_DIR"
mkdir -p "$TARGET_DIR"

if [[ -d "$TARGET_DIR/.git" ]]; then
  echo "[2/7] Existing git repo found. Fetching latest..."
  git -C "$TARGET_DIR" fetch --all --prune
else
  echo "[2/7] Cloning repository..."
  git clone "$REPO_URL" "$TARGET_DIR"
fi

echo "[3/7] Checking out branch: $BRANCH"
git -C "$TARGET_DIR" checkout "$BRANCH"
git -C "$TARGET_DIR" pull --ff-only origin "$BRANCH"

echo "[4/7] Creating/updating Python virtual environment"
python3 -m venv "$TARGET_DIR/.venv"
# shellcheck disable=SC1091
source "$TARGET_DIR/.venv/bin/activate"
python -m pip install --upgrade pip
pip install -r "$TARGET_DIR/requirements.txt"
pip install -e "$TARGET_DIR"

echo "[5/7] Writing runtime .env"
cat > "$TARGET_DIR/.env" <<EOF
ORACLE_USER=$ORACLE_USER
ORACLE_PASSWORD=$ORACLE_PASSWORD
ORACLE_DSN=$ORACLE_DSN
ENVIRONMENT=$ENVIRONMENT
LOG_LEVEL=$LOG_LEVEL
EOF
chmod 600 "$TARGET_DIR/.env"

if [[ "$PRUNE" == true ]]; then
  echo "[6/7] Pruning non-code content"

  # Keep-list (top-level)
  KEEP_TOP=(
    .git
    .venv
    src
    config
    scripts
    requirements.txt
    requirements-api.txt
    requirements-dev.txt
    setup.py
    .env
    .env.example
    pytest.ini
    run_tests.sh
    build_pex.sh
    build_rpm.sh
  )

  shopt -s dotglob nullglob
  for p in "$TARGET_DIR"/*; do
    base="$(basename "$p")"
    keep=false
    for k in "${KEEP_TOP[@]}"; do
      if [[ "$base" == "$k" ]]; then
        keep=true
        break
      fi
    done
    if [[ "$keep" == false ]]; then
      rm -rf "$p"
    fi
  done
  shopt -u dotglob nullglob

  # Optional additional cleanup inside retained dirs
  rm -rf "$TARGET_DIR/config/templates" || true
  rm -rf "$TARGET_DIR/data" || true
  rm -rf "$TARGET_DIR/tests" || true
  rm -rf "$TARGET_DIR/docs" || true
  echo "Prune complete."
else
  echo "[6/7] Skipping prune (--prune not set)"
fi

echo "[7/7] Quick sanity checks"
"$TARGET_DIR/.venv/bin/python" -c "import src; print('Python import OK')"
"$TARGET_DIR/.venv/bin/valdo" --help >/dev/null && echo "valdo CLI OK"

echo

echo "Deployment complete."
echo "Target: $TARGET_DIR"
echo "Branch: $BRANCH"
echo "Next:"
echo "  source $TARGET_DIR/.venv/bin/activate"
echo "  python $TARGET_DIR/test_oracle_connection.py"

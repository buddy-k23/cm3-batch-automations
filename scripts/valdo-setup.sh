#!/usr/bin/env bash
#
# valdo-setup.sh — single, OS-agnostic bootstrap for Valdo (S10-3, #398; S11-2, #400).
#
# One command from a fresh clone to a running Valdo on SQLite with zero
# external infrastructure:
#
#   bash scripts/valdo-setup.sh
#
# Or, the full-stack docker-compose path (Valdo app + Postgres), driven by one
# command — preflight Docker, build + start the stack, wait for healthy, smoke:
#
#   bash scripts/valdo-setup.sh --env full-stack
#   bash scripts/valdo-setup.sh --env full-stack --down      # tear the stack down
#   bash scripts/valdo-setup.sh --env full-stack --down -v   # ...and drop the DB volume
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
#   --env full-stack   docker-compose stack (Valdo app + Postgres) — preflight
#                      Docker, `docker compose up -d --build`, wait for the valdo
#                      service to report healthy, then smoke the health endpoint.
#                      Supports both the `docker compose` v2 plugin and the legacy
#                      `docker-compose` binary (prefers v2). Migrations are applied
#                      by the compose `migrate` service (gates the app); the script
#                      confirms it completed rather than double-running them.
#                      `--down` (optionally with `-v`/`--destroy`) tears it down.
#   --env int          INT-region Oracle scaffold + config validation (S11-3).
#                      There is NO live INT environment here, so this is
#                      scaffold + validate (connect only if reachable):
#                        - create .env.int from .env.int.example ONLY if missing
#                          (never clobber); note it if it already exists;
#                        - validate the required INT vars are set to non-
#                          placeholder values (Oracle creds/DSN, the MCP + session
#                          signing keys, and the secrets-provider-specific vars
#                          when SECRETS_PROVIDER != env). Report any still-
#                          placeholder/missing vars precisely and exit non-zero;
#                        - when all required vars are set, do a short, bounded TCP
#                          reachability check on the Oracle DSN host:port. If
#                          reachable → `alembic upgrade head` (DB_ADAPTER=oracle)
#                          + a connection smoke. If NOT reachable → print precise
#                          next steps (complete network/VPN, then re-run) and exit 0.
#                      No `--env` value is "deferred" any more.
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
# full-stack teardown flags (ignored for other envs).
FULLSTACK_DOWN=false
FULLSTACK_DESTROY_VOLUME=false

print_help() {
    cat <<'EOF'
Valdo setup — one command from a fresh clone to a running Valdo.

Usage:
  bash scripts/valdo-setup.sh [--env <local|int|full-stack>] [--down] [-v|--destroy] [-h|--help]

Options:
  --env local        Full local setup on SQLite (default). No external DB needed.
  --env full-stack   Bring up the docker-compose stack (Valdo app + Postgres):
                       preflight Docker, `docker compose up -d --build`, wait for
                       the valdo service to be healthy, then smoke the health
                       endpoint. Supports `docker compose` (v2) and legacy
                       `docker-compose` (prefers v2). Requires a running Docker daemon.
  --env int          INT-region Oracle scaffold + config validation. Creates
                       .env.int from .env.int.example if missing (never clobbers),
                       validates the required INT vars are set to non-placeholder
                       values (reports any that are missing and exits non-zero),
                       and — only when all are set AND the Oracle DSN is reachable
                       — runs migrations + a connection smoke. If the DSN is not
                       reachable it prints next steps and exits 0 (scaffold-complete).
  --down             (full-stack only) Tear the compose stack down and exit.
  -v, --destroy      (full-stack only, with --down) Also remove the Postgres
                     data volume (`docker compose down -v`) — drops all data.
  -h, --help         Show this help and exit.

After a successful local run:
  source .venv/bin/activate     # (.venv/Scripts/activate on Git Bash/Windows)
  valdo serve                   # UI at http://localhost:8000/ui

After a successful full-stack run:
  open http://localhost:8000/ui            # Web UI (Postgres-backed)
  docker compose logs -f valdo             # follow app logs
  docker compose down                      # stop the stack (keeps the DB volume)
  docker compose down -v                   # stop + drop the DB volume
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
        --down)
            FULLSTACK_DOWN=true
            shift
            ;;
        -v|--destroy)
            FULLSTACK_DESTROY_VOLUME=true
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

# =============================================================================
# Full-stack (docker-compose) helpers — used by `--env full-stack` below.
# =============================================================================

# Resolve which compose CLI is available, preferring the v2 plugin
# (`docker compose`) over the legacy v1 binary (`docker-compose`). Sets the
# global COMPOSE_CMD as an array so callers can do `"${COMPOSE_CMD[@]}" up ...`.
COMPOSE_CMD=()
detect_compose_cmd() {
    if docker compose version >/dev/null 2>&1; then
        COMPOSE_CMD=(docker compose)
        return 0
    fi
    if command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
        COMPOSE_CMD=(docker-compose)
        return 0
    fi
    return 1
}

# Preflight: docker installed + daemon running + a compose CLI available.
# On any failure, print an actionable error and exit non-zero (never fall
# through to the local SQLite path).
fullstack_preflight() {
    echo "[1/5] Preflight — checking Docker + compose..."
    if ! command -v docker >/dev/null 2>&1; then
        echo "ERROR: 'docker' is not installed or not on PATH." >&2
        echo "Install Docker Desktop (macOS/Windows) or the Docker Engine (Linux):" >&2
        echo "  https://docs.docker.com/get-docker/" >&2
        echo "Then re-run: bash scripts/valdo-setup.sh --env full-stack" >&2
        exit 1
    fi
    if ! docker info >/dev/null 2>&1; then
        echo "ERROR: the Docker daemon is not running (or not reachable)." >&2
        echo "Start Docker Desktop, or on Linux: 'sudo systemctl start docker'." >&2
        echo "Verify with: docker info" >&2
        exit 1
    fi
    if ! detect_compose_cmd; then
        echo "ERROR: no Docker Compose CLI found." >&2
        echo "Install the Compose v2 plugin (recommended) or legacy docker-compose:" >&2
        echo "  https://docs.docker.com/compose/install/" >&2
        echo "Verify with: docker compose version   (or: docker-compose version)" >&2
        exit 1
    fi
    echo "      docker: $(docker --version 2>/dev/null)"
    echo "      compose: ${COMPOSE_CMD[*]} ($("${COMPOSE_CMD[@]}" version --short 2>/dev/null || echo 'version unknown'))"
}

# Bring the stack up: build images and start detached.
fullstack_up() {
    echo "[2/5] Building images and starting the stack (docker compose up -d --build)..."
    if ! "${COMPOSE_CMD[@]}" up -d --build; then
        echo "ERROR: 'compose up' failed. Inspect the build/start output above." >&2
        echo "Logs: ${COMPOSE_CMD[*]} logs" >&2
        exit 1
    fi
}

# Confirm the one-shot `migrate` service completed successfully. The compose
# dependency gate (valdo depends_on migrate: service_completed_successfully)
# already guarantees this before valdo serves; we only confirm + note it, never
# double-run migrations.
fullstack_confirm_migrations() {
    echo "[3/5] Confirming migrations (one-shot 'migrate' service)..."
    local mid=""
    mid="$("${COMPOSE_CMD[@]}" ps -a -q migrate 2>/dev/null || true)"
    if [ -n "$mid" ]; then
        local exit_code=""
        exit_code="$(docker inspect -f '{{.State.ExitCode}}' "$mid" 2>/dev/null || echo "")"
        if [ "$exit_code" = "0" ]; then
            echo "      'migrate' completed (exit 0) — schema is at head; not re-running."
        elif [ -n "$exit_code" ]; then
            echo "      NOTE: 'migrate' exit code is '${exit_code}' (may still be running or queued)."
            echo "            The compose gate blocks 'valdo' until migrate exits 0, so the"
            echo "            health wait below is authoritative. Inspect: ${COMPOSE_CMD[*]} logs migrate"
        fi
    else
        echo "      NOTE: could not locate the 'migrate' container to confirm; the compose"
        echo "            dependency gate still ensures migrations ran before 'valdo' serves."
    fi
}

# Wait (bounded) for the valdo service container to report a healthy status.
# Polls the container health state via docker inspect; falls back to probing the
# health endpoint from the host if no health status is available.
fullstack_wait_healthy() {
    local deadline timeout_secs=120 interval=5
    echo "[4/5] Waiting for the 'valdo' service to become healthy (up to ${timeout_secs}s)..."
    deadline=$(( $(date +%s) + timeout_secs ))
    local cid="" status=""
    while [ "$(date +%s)" -lt "$deadline" ]; do
        cid="$("${COMPOSE_CMD[@]}" ps -q valdo 2>/dev/null || true)"
        if [ -n "$cid" ]; then
            status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$cid" 2>/dev/null || echo "")"
            case "$status" in
                healthy)
                    echo "      'valdo' is healthy."
                    return 0
                    ;;
                unhealthy)
                    echo "ERROR: the 'valdo' service reported 'unhealthy'." >&2
                    echo "Inspect logs: ${COMPOSE_CMD[*]} logs valdo" >&2
                    exit 1
                    ;;
                none)
                    # No container healthcheck visible — fall back to host probe.
                    if probe_health_endpoint; then
                        echo "      'valdo' health endpoint responded OK (no container health status)."
                        return 0
                    fi
                    ;;
            esac
        fi
        printf '      ... still waiting (status=%s)\n' "${status:-starting}"
        sleep "$interval"
    done
    echo "ERROR: timed out after ${timeout_secs}s waiting for 'valdo' to become healthy." >&2
    echo "Check what went wrong with:" >&2
    echo "  ${COMPOSE_CMD[*]} ps" >&2
    echo "  ${COMPOSE_CMD[*]} logs valdo" >&2
    echo "  ${COMPOSE_CMD[*]} logs migrate" >&2
    exit 1
}

# Probe the health endpoint from the host. Prefers curl; falls back to the
# Python stdlib (urllib) so no curl dependency is required. Returns 0 on a 200.
HEALTH_URL="http://localhost:8000/api/v1/system/health"
probe_health_endpoint() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1
        return $?
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 - "$HEALTH_URL" <<'PY' >/dev/null 2>&1
import sys, urllib.request
url = sys.argv[1]
with urllib.request.urlopen(url, timeout=5) as r:
    sys.exit(0 if r.status == 200 else 1)
PY
        return $?
    fi
    return 1
}

# Host-side smoke check: hit the health endpoint and print the body if possible.
fullstack_smoke() {
    echo "[5/5] Smoke check — GET ${HEALTH_URL} from the host..."
    if command -v curl >/dev/null 2>&1; then
        if curl -fsS --max-time 5 "$HEALTH_URL"; then
            echo ""
            echo "      Smoke check passed."
            return 0
        fi
    elif probe_health_endpoint; then
        echo "      Smoke check passed (urllib probe, curl not installed)."
        return 0
    fi
    echo "ERROR: smoke check failed — ${HEALTH_URL} did not return 200." >&2
    echo "The container reported healthy but the host probe failed; check port" >&2
    echo "mapping (8000:8000) and: ${COMPOSE_CMD[*]} logs valdo" >&2
    exit 1
}

# Tear the stack down (optionally dropping the data volume) and exit.
fullstack_down() {
    echo "Tearing down the full-stack compose stack..."
    if [ "$FULLSTACK_DESTROY_VOLUME" = true ]; then
        echo "  (including the Postgres data volume — all data will be dropped)"
        "${COMPOSE_CMD[@]}" down -v
    else
        "${COMPOSE_CMD[@]}" down
        echo "  Data volume 'valdo-pgdata' preserved (use --down -v to drop it)."
    fi
    echo "Stack stopped."
}

# Orchestrate the full-stack bring-up (or teardown when --down was passed).
run_fullstack() {
    cd "$PROJECT_ROOT"
    echo "========================================="
    echo " Valdo — full-stack setup (docker-compose)"
    echo "========================================="
    echo "Repo root: ${PROJECT_ROOT}"
    echo ""

    fullstack_preflight

    if [ "$FULLSTACK_DOWN" = true ]; then
        echo ""
        fullstack_down
        exit 0
    fi

    fullstack_up
    fullstack_confirm_migrations
    fullstack_wait_healthy
    fullstack_smoke

    echo ""
    echo "========================================="
    echo " Full-stack up — Valdo on PostgreSQL"
    echo "========================================="
    echo ""
    echo "Next steps:"
    echo "  - Web UI:        open http://localhost:8000/ui"
    echo "  - Health:        curl -fsS ${HEALTH_URL}"
    echo "  - Follow logs:   ${COMPOSE_CMD[*]} logs -f valdo"
    echo "  - Stack status:  ${COMPOSE_CMD[*]} ps"
    echo ""
    echo "Teardown:"
    echo "  - Stop (keep data):   ${COMPOSE_CMD[*]} down"
    echo "                        bash scripts/valdo-setup.sh --env full-stack --down"
    echo "  - Stop + drop data:   ${COMPOSE_CMD[*]} down -v"
    echo "                        bash scripts/valdo-setup.sh --env full-stack --down -v"
    echo ""
    echo "Postgres data persists in the named volume 'valdo-pgdata' across 'down' (not '-v')."
}

# =============================================================================
# INT scaffold + validate (`--env int`, S11-3, #401).
#
# No live INT environment exists here, so this path SCAFFOLDS the INT config and
# VALIDATES it. It connects (migrate + smoke) ONLY when the Oracle DSN is
# reachable; otherwise it prints precise next steps and exits 0.
# =============================================================================

INT_ENV_FILE=".env.int"
INT_ENV_EXAMPLE=".env.int.example"

# Read a KEY=VALUE from the INT env file (last occurrence wins). Strips inline
# `export ` prefixes and surrounding quotes; ignores comment/blank lines. Prints
# the value (possibly empty) to stdout. Pure shell — no `source` (so a malformed
# file can never execute), no new Python deps.
int_env_get() {
    local key="$1" file="$2" line val
    [ -f "$file" ] || return 0
    # Grab the last non-comment assignment for this exact key.
    line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" "$file" | tail -n 1 || true)"
    [ -n "$line" ] || return 0
    val="${line#*=}"
    # Strip matching surrounding single/double quotes.
    case "$val" in
        \"*\") val="${val#\"}"; val="${val%\"}" ;;
        \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    printf '%s' "$val"
}

# Decide whether a value still counts as "unset" for validation purposes: empty,
# or still carrying a template placeholder marker (<...> or SET_ME / change-me /
# your_..._here patterns). Returns 0 (true) when the value is a placeholder.
int_is_placeholder() {
    local val="$1"
    [ -z "$val" ] && return 0
    case "$val" in
        *"<"*">"*) return 0 ;;          # <INT_ORACLE_USER>, <SET_ME>, ...
        *SET_ME*|*set_me*) return 0 ;;
        *change-me*|*change_me*|*CHANGE-ME*|*CHANGE_ME*) return 0 ;;
        *your_*_here*|*your-*-here*) return 0 ;;
    esac
    return 1
}

# Validate the required INT vars in $INT_ENV_FILE. Appends the name of every var
# that is still a placeholder/unset to the global INT_MISSING array. The required
# set depends on SECRETS_PROVIDER (vault/azure add their own required vars).
INT_MISSING=()
int_validate_required() {
    local file="$1"
    INT_MISSING=()

    # Always-required core vars.
    local required=(
        ORACLE_USER
        ORACLE_PASSWORD
        ORACLE_DSN
        VALDO_MCP_TOKEN_SIGNING_KEY
        VALDO_SESSION_SIGNING_KEY
    )

    # Secrets-provider-specific required vars (only when not the plain env provider).
    local provider
    provider="$(int_env_get SECRETS_PROVIDER "$file")"
    provider="$(printf '%s' "$provider" | tr '[:upper:]' '[:lower:]')"
    case "$provider" in
        vault)
            required+=(VAULT_ADDR VAULT_ROLE_ID VAULT_SECRET_ID)
            ;;
        azure)
            required+=(AZURE_VAULT_URL)
            ;;
    esac

    local key val
    for key in "${required[@]}"; do
        val="$(int_env_get "$key" "$file")"
        if int_is_placeholder "$val"; then
            INT_MISSING+=("$key")
        fi
    done
}

# Parse host and port out of an Oracle Easy Connect DSN ("host:port/service",
# "host/service", or "host:port"). Sets the globals INT_DSN_HOST / INT_DSN_PORT
# (port defaults to 1521 when absent).
INT_DSN_HOST=""
INT_DSN_PORT=""
int_parse_dsn() {
    local dsn="$1" hostport
    # Drop the /service suffix if present.
    hostport="${dsn%%/*}"
    if [ "$hostport" != "$dsn" ] || [ "${dsn#*/}" != "$dsn" ]; then
        :  # had a slash; hostport already trimmed
    fi
    case "$hostport" in
        *:*)
            INT_DSN_HOST="${hostport%:*}"
            INT_DSN_PORT="${hostport##*:}"
            ;;
        *)
            INT_DSN_HOST="$hostport"
            INT_DSN_PORT="1521"
            ;;
    esac
}

# Bounded TCP reachability check for host:port. Portable: prefers bash
# /dev/tcp with a backgrounded timeout, falls back to the Python stdlib socket
# (no new deps, no sqlplus required). Returns 0 if a connection opened.
int_dsn_reachable() {
    local host="$1" port="$2" timeout_secs="${3:-5}" py="$4"
    [ -n "$host" ] && [ -n "$port" ] || return 1

    # Prefer Python stdlib socket — uniform timeout semantics across shells.
    if [ -n "$py" ] && [ -x "$py" ]; then
        "$py" - "$host" "$port" "$timeout_secs" <<'PY' >/dev/null 2>&1
import socket, sys
host, port, timeout = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
try:
    with socket.create_connection((host, port), timeout=timeout):
        sys.exit(0)
except OSError:
    sys.exit(1)
PY
        return $?
    fi

    # Fallback: bash /dev/tcp with a watchdog so we never hang.
    (
        exec 3<>"/dev/tcp/${host}/${port}"
    ) >/dev/null 2>&1 &
    local pid=$!
    local waited=0
    while kill -0 "$pid" 2>/dev/null; do
        if [ "$waited" -ge "$timeout_secs" ]; then
            kill "$pid" 2>/dev/null || true
            wait "$pid" 2>/dev/null || true
            return 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
    wait "$pid" 2>/dev/null
    return $?
}

# Locate a venv python/alembic if a venv already exists, else fall back to a
# system python so validation-only runs work without a built venv. Sets the
# globals INT_PY and INT_ALEMBIC (the latter may be empty if unavailable).
INT_PY=""
INT_ALEMBIC=""
int_resolve_tools() {
    local candidate
    if [ -x ".venv/bin/python" ]; then
        INT_PY=".venv/bin/python"
        [ -x ".venv/bin/alembic" ] && INT_ALEMBIC=".venv/bin/alembic"
    elif [ -x ".venv/Scripts/python" ] || [ -f ".venv/Scripts/python" ]; then
        INT_PY=".venv/Scripts/python"
        { [ -x ".venv/Scripts/alembic" ] || [ -f ".venv/Scripts/alembic" ]; } && INT_ALEMBIC=".venv/Scripts/alembic"
    else
        for candidate in python3.13 python3.12 python3.11 python3 python; do
            if command -v "$candidate" >/dev/null 2>&1; then
                INT_PY="$(command -v "$candidate")"
                break
            fi
        done
    fi
}

# Orchestrate the INT scaffold + validate flow.
run_int() {
    cd "$PROJECT_ROOT"
    echo "========================================="
    echo " Valdo — INT scaffold + config validation"
    echo "========================================="
    echo "Repo root: ${PROJECT_ROOT}"
    echo ""

    # [1/3] Scaffold .env.int (never clobber).
    echo "[1/3] Scaffolding ${INT_ENV_FILE}..."
    if [ -f "$INT_ENV_FILE" ]; then
        echo "      ${INT_ENV_FILE} already exists — leaving it untouched (no clobber)."
    elif [ -f "$INT_ENV_EXAMPLE" ]; then
        cp "$INT_ENV_EXAMPLE" "$INT_ENV_FILE"
        echo "      Created ${INT_ENV_FILE} from ${INT_ENV_EXAMPLE}."
        echo "      NOTE: it is filled with PLACEHOLDERS — complete it before INT use."
    else
        echo "ERROR: ${INT_ENV_EXAMPLE} not found — cannot scaffold ${INT_ENV_FILE}." >&2
        echo "Restore the committed template and re-run." >&2
        exit 1
    fi

    # [2/3] Validate required vars are set to non-placeholder values.
    echo "[2/3] Validating required INT variables in ${INT_ENV_FILE}..."
    int_validate_required "$INT_ENV_FILE"
    if [ "${#INT_MISSING[@]}" -gt 0 ]; then
        echo ""
        echo "      The following required INT variables are still unset or carry a"
        echo "      placeholder value and MUST be completed:"
        local key
        for key in "${INT_MISSING[@]}"; do
            echo "        - ${key}"
        done
        echo ""
        echo "ERROR: ${INT_ENV_FILE} is incomplete (${#INT_MISSING[@]} required var(s) still placeholder/unset)." >&2
        echo "Next steps:" >&2
        echo "  1. Edit ${PROJECT_ROOT}/${INT_ENV_FILE} and set the variables listed above" >&2
        echo "     to real INT values (signing keys: python -c \"import secrets; print(secrets.token_hex(32))\")." >&2
        echo "  2. Re-run: bash scripts/valdo-setup.sh --env int" >&2
        echo "Complete .env.int then re-run." >&2
        exit 1
    fi
    echo "      All required INT variables are set (non-placeholder)."

    # [3/3] Reachability-gated migrate + smoke.
    echo "[3/3] Checking Oracle DSN reachability..."
    local dsn
    dsn="$(int_env_get ORACLE_DSN "$INT_ENV_FILE")"
    int_parse_dsn "$dsn"
    echo "      DSN: ${dsn}  (host=${INT_DSN_HOST} port=${INT_DSN_PORT})"

    int_resolve_tools
    if [ -z "$INT_PY" ]; then
        echo "      NOTE: no Python interpreter found to perform the reachability probe." >&2
    fi

    if int_dsn_reachable "$INT_DSN_HOST" "$INT_DSN_PORT" 5 "$INT_PY"; then
        echo "      Oracle DSN is reachable (TCP connect to ${INT_DSN_HOST}:${INT_DSN_PORT})."
        int_migrate_and_smoke
        echo ""
        echo "========================================="
        echo " INT setup complete — schema migrated + smoke passed"
        echo "========================================="
        exit 0
    fi

    echo "      Oracle DSN is NOT reachable from here (TCP connect to"
    echo "      ${INT_DSN_HOST}:${INT_DSN_PORT} failed within the timeout)."
    echo ""
    echo "========================================="
    echo " INT scaffold complete — config validated, DB not reachable"
    echo "========================================="
    echo ""
    echo "This is expected when you are off the INT network. The INT config is"
    echo "scaffolded and all required variables are set. To finish the bring-up:"
    echo "  1. Connect to the INT network / VPN so ${INT_DSN_HOST}:${INT_DSN_PORT} is reachable."
    echo "  2. Re-run: bash scripts/valdo-setup.sh --env int"
    echo "     (it will then run 'alembic upgrade head' against Oracle and a connection smoke)."
    echo ""
    echo "No changes were made to the database; nothing further is required now."
    exit 0
}

# Run migrations against INT Oracle then a connection smoke, with the INT env
# loaded and DB_ADAPTER forced to oracle. Called only when the DSN is reachable.
int_migrate_and_smoke() {
    if [ -z "$INT_ALEMBIC" ] && { [ -z "$INT_PY" ] || ! "$INT_PY" -c "import alembic" >/dev/null 2>&1; }; then
        echo "      NOTE: alembic is not available (no built .venv?). Skipping migrate/smoke."
        echo "            Run 'bash scripts/valdo-setup.sh' once to build the venv, then re-run --env int."
        return 0
    fi

    echo "      Running migrations (alembic upgrade head, Oracle)..."
    # Load the validated INT env into this subshell only; force the Oracle
    # adapter. We export every non-comment assignment from .env.int.
    if [ -n "$INT_ALEMBIC" ]; then
        ( set -a; . "./${INT_ENV_FILE}"; set +a; DB_ADAPTER=oracle "$INT_ALEMBIC" upgrade head )
    else
        ( set -a; . "./${INT_ENV_FILE}"; set +a; DB_ADAPTER=oracle "$INT_PY" -m alembic upgrade head )
    fi || {
        echo "ERROR: 'alembic upgrade head' against INT Oracle failed. See output above." >&2
        exit 1
    }
    echo "      Migrations applied."

    echo "      Connection smoke (oracledb thin connect)..."
    if [ -n "$INT_PY" ]; then
        ( set -a; . "./${INT_ENV_FILE}"; set +a; DB_ADAPTER=oracle "$INT_PY" -c \
            "from src.config.db_config import get_connection; c=get_connection(); c.close(); print('ok')" ) \
            && echo "      Connection smoke OK." \
            || { echo "ERROR: INT Oracle connection smoke failed." >&2; exit 1; }
    fi
}

# --- Environment dispatch ----------------------------------------------------
case "$ENV_TARGET" in
    local)
        ;;
    full-stack)
        run_fullstack
        exit 0
        ;;
    int)
        run_int
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
echo "Want Postgres? Run: bash scripts/valdo-setup.sh --env full-stack (docker-compose)."
echo "For the INT environment (Oracle scaffold + validate): bash scripts/valdo-setup.sh --env int"

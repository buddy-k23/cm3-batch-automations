#!/usr/bin/env bash
# Valdo E2E batch testing — SHAW load-step wrapper.
#
# Invoked by scripts/run_e2e_source.sh as the `load_step` Java shell-out for
# the SHAW source. SHAW does not have a single Java loader; it has 16
# per-file sqlload scripts under /app/software/APPS/interfaces/shaw/scripts.
# This wrapper iterates the 16 scripts, runs each only when its input file
# pattern matches in the input directory, and aggregates exit codes.
#
# Failure semantics (per AGENTS.md hard rules and operator request):
#   - Run every script whose input file is present.
#   - Continue past failures; do NOT fail fast.
#   - After all scripts finish, exit non-zero if any sqlload returned
#     non-zero — this fails the `load_step` gate and halts the pipeline
#     before file_to_staging runs (gates.load_step.blocking: true).
#   - Scripts whose input file is absent are skipped silently (logged at
#     DEBUG); a skip is NOT a failure.
#
# Environment variables (all inherited from the caller; NOT defined here):
#   USRPW                   Oracle credentials for sqlldr (resolved upstream)
#   LOG_DIR, BAD_DIR        sqlldr log/bad/discard output dirs
#   DISC_DIR, CTL_DIR       (consumed by individual sqlload scripts)
#   APPS_FTP_OUTPUT_DIR     drop dir used by sqlload_ext_shaw.sh
#   MFRAME_IN_DIR           drop dir used by sqlload_shaw_loss_mitigation_stln.sh
#
# Per AGENTS.md hard rule #3: no secrets in this script. USRPW must come
# from the inherited environment (loaded from .env upstream).
#
# Usage:
#   bash scripts/wrappers/load_SHAW.sh \
#       [--input-dir /app/software/ftp/input/shaw] \
#       [--scripts-dir /app/software/APPS/interfaces/shaw/scripts] \
#       [--dry-run]
#
# Exit codes:
#   0 = every script that ran returned 0 (skips are not failures)
#   1 = at least one sqlload script returned non-zero
#   3 = wrapper-level failure (scripts-dir missing, input-dir missing, etc.)

set -u -o pipefail

# ─────────────────────────────────────────────────────────────────────────────
# Defaults
# ─────────────────────────────────────────────────────────────────────────────
INPUT_DIR="/app/software/ftp/input/shaw"
SCRIPTS_DIR="/app/software/APPS/interfaces/shaw/scripts"
DRY_RUN=0

# ─────────────────────────────────────────────────────────────────────────────
# Per-file pattern → script table.
#
# Each row: "<glob>|<script_basename>"
#   <glob>            Glob evaluated relative to INPUT_DIR. Wrapper skips the
#                     row when the glob matches zero files.
#   <script_basename> Resolved against SCRIPTS_DIR.
#
# Note: sqlload-metro2-file.sh and sqlload-trw_file.sh both consume
# TRW_File_*.txt by design — they load it into different staging targets.
# Both are listed; both will run when the file is present.
#
# Note: sqlload_ext_shaw.sh and sqlload_shaw_loss_mitigation_stln.sh loop
# internally over multiple matching files; the wrapper invokes them once
# when at least one matching file exists in INPUT_DIR.
# ─────────────────────────────────────────────────────────────────────────────
SHAW_LOAD_TABLE=(
  "collateral-master_*.txt|sqlload-collateral-master.sh"
  "ctoatran_master.txt|sqlload-ctoatran-master.sh"
  "fee-master_*.txt|sqlload-fee-master.sh"
  "loans-master_*.txt|sqlload-loan-master.sh"
  "loans-name_*.txt|sqlload-loans-name.sh"
  "TRW_File_*.txt|sqlload-metro2-file.sh"
  "posted-trans_*.txt|sqlload-posted-transaction.sh"
  "loss-mitigation-cust_*.txt|sqlload-shaw-loss-mitigation-ltln.sh"
  "secured-cntl1_*.txt|sqlload-shaw-pool-control-segment.sh"
  "secured-pool_*.txt|sqlload-shaw-pool-investor-segment.sh"
  "secured-cntl2_*.txt|sqlload-shaw-pool-settlement-segment.sh"
  "history_*.txt|sqlload-trans-master.sh"
  "TRW_File_*.txt|sqlload-trw_file.sh"
  "User_Fields_*.txt|sqlload-user-fields.sh"
  "cudtran_*.txt|sqlload_ext_shaw.sh"
  "loss-mitigation-loan_*.txt|sqlload_shaw_loss_mitigation_stln.sh"
  "HSStran_*.txt|sqlload-shaw-hss-trans-daily.sh"
)

# ─────────────────────────────────────────────────────────────────────────────
# Argument parsing
# ─────────────────────────────────────────────────────────────────────────────
usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --input-dir <path>     Directory holding SHAW input files
                         (default: $INPUT_DIR)
  --scripts-dir <path>   Directory holding the sqlload-*.sh scripts
                         (default: $SCRIPTS_DIR)
  --dry-run              Print what would run; invoke nothing.
  -h, --help             Show this help and exit.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input-dir)   INPUT_DIR="$2"; shift 2 ;;
    --scripts-dir) SCRIPTS_DIR="$2"; shift 2 ;;
    --dry-run)     DRY_RUN=1; shift ;;
    -h|--help)     usage; exit 0 ;;
    *)             echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 3 ;;
  esac
done

# ─────────────────────────────────────────────────────────────────────────────
# Pre-flight
# ─────────────────────────────────────────────────────────────────────────────
RUN_ID="${VALDO_E2E_RUN_ID:-$(date -u +%Y%m%d_%H%M%S)}"
log()  { printf '{"ts":"%s","level":"%s","event":"%s","source":"SHAW","run_id":"%s","msg":%s}\n' \
              "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$RUN_ID" "$3" >&2; }
qstr() { printf '"%s"' "${1//\"/\\\"}"; }

if [[ ! -d "$SCRIPTS_DIR" ]]; then
  log "ERROR" "scripts_dir_missing" "$(qstr "$SCRIPTS_DIR")"
  exit 3
fi
if [[ ! -d "$INPUT_DIR" ]]; then
  log "ERROR" "input_dir_missing" "$(qstr "$INPUT_DIR")"
  exit 3
fi

# Switch into INPUT_DIR so the inner scripts' bare-glob/`pwd`-based lookups
# resolve correctly (every sqlload-*.sh in scope uses `ls -t1 *.txt` or
# `find . -maxdepth 1 ...` patterns that depend on cwd).
cd "$INPUT_DIR" || { log "ERROR" "input_dir_unreadable" "$(qstr "$INPUT_DIR")"; exit 3; }

log "INFO" "load_step_started" "$(qstr "input_dir=$INPUT_DIR scripts_dir=$SCRIPTS_DIR dry_run=$DRY_RUN")"

# ─────────────────────────────────────────────────────────────────────────────
# Iterate the table.
# ─────────────────────────────────────────────────────────────────────────────
ran=0
skipped=0
failed=0
declare -a FAILED_SCRIPTS=()

# Enable nullglob so an unmatched glob expands to zero words, not the literal
# pattern. Restore the prior setting on exit.
shopt -s nullglob

for row in "${SHAW_LOAD_TABLE[@]}"; do
  glob="${row%%|*}"
  script_name="${row##*|}"
  script_path="$SCRIPTS_DIR/$script_name"

  # Presence check: does at least one file match this glob in INPUT_DIR?
  matches=( $glob )
  if [[ ${#matches[@]} -eq 0 ]]; then
    log "DEBUG" "load_script_skipped_no_input" \
        "$(qstr "script=$script_name glob=$glob")"
    skipped=$((skipped + 1))
    continue
  fi

  if [[ ! -x "$script_path" ]]; then
    log "ERROR" "load_script_not_executable" \
        "$(qstr "script=$script_path")"
    failed=$((failed + 1))
    FAILED_SCRIPTS+=("$script_name (not executable)")
    continue
  fi

  log "INFO" "load_script_started" \
      "$(qstr "script=$script_name matched_count=${#matches[@]}")"

  if [[ $DRY_RUN -eq 1 ]]; then
    log "INFO" "load_script_dry_run" "$(qstr "script=$script_name")"
    ran=$((ran + 1))
    continue
  fi

  # Run the script. It inherits the current cwd (INPUT_DIR) and full env.
  # We deliberately do NOT use `set -e` semantics here: capture rc, continue.
  rc=0
  "$script_path" || rc=$?
  ran=$((ran + 1))

  if [[ $rc -eq 0 ]]; then
    log "INFO" "load_script_succeeded" "$(qstr "script=$script_name")"
  else
    log "ERROR" "load_script_failed" \
        "$(qstr "script=$script_name exit_code=$rc")"
    failed=$((failed + 1))
    FAILED_SCRIPTS+=("$script_name (rc=$rc)")
  fi
done

shopt -u nullglob

# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
log "INFO" "load_step_finished" \
    "$(qstr "ran=$ran skipped=$skipped failed=$failed")"

if [[ $failed -gt 0 ]]; then
  for entry in "${FAILED_SCRIPTS[@]}"; do
    log "ERROR" "load_step_failure_detail" "$(qstr "$entry")"
  done
  exit 1
fi

exit 0

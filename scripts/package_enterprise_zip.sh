#!/usr/bin/env bash
###############################################################################
# package_enterprise_zip.sh
#
# PURPOSE
#   Package the Valdo WORKING TREE (including uncommitted + untracked files)
#   into a lean .zip suitable for enterprise transfer:
#       manual copy  ->  upload to GitLab  ->  run as an editable MCP app
#       with a real DB backend.
#
#   This deliberately does NOT use `git archive` (which would silently drop
#   uncommitted/untracked work). Instead it rsyncs the live working tree into
#   a temp staging dir applying excludes, zips the staging dir, then cleans up.
#
# USAGE
#   ./scripts/package_enterprise_zip.sh
#
#   Run it from anywhere; it resolves the repo root relative to this script.
#   No arguments. No git commands are executed.
#
# OUTPUT
#   ./dist/valdo-enterprise-<YYYYMMDD-HHMMSS>.zip
#
# EXIT CODES
#   0  success, verification clean
#   1  a forbidden entry (venv / secrets / db / xlsx / git / cache) leaked in
#   2  prerequisite missing (rsync/zip) or staging failure
#
# TWEAKING EXCLUDES
#   Edit the RSYNC_EXCLUDES array below. Patterns are passed verbatim to
#   `rsync --exclude=`. Directory patterns are more reliable here than
#   `zip -x` glob patterns, which is why we stage-then-zip.
###############################################################################

set -euo pipefail

# --- Resolve repo root (parent of this scripts/ dir) -------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# --- Prerequisites -----------------------------------------------------------
command -v rsync >/dev/null 2>&1 || { echo "ERROR: rsync not found" >&2; exit 2; }
command -v zip   >/dev/null 2>&1 || { echo "ERROR: zip not found"   >&2; exit 2; }

# --- Naming / paths ----------------------------------------------------------
TIMESTAMP="$(date +%Y%m%d-%H%M%S)"
DIST_DIR="${REPO_ROOT}/dist"
ZIP_NAME="valdo-enterprise-${TIMESTAMP}.zip"
ZIP_PATH="${DIST_DIR}/${ZIP_NAME}"
STAGE_PARENT="$(mktemp -d "${TMPDIR:-/tmp}/valdo-pkg.XXXXXX")"
# Top-level folder inside the zip:
STAGE_NAME="valdo-enterprise-${TIMESTAMP}"
STAGE_DIR="${STAGE_PARENT}/${STAGE_NAME}"

mkdir -p "${DIST_DIR}" "${STAGE_DIR}"

# Always clean up the temp staging tree, even on error.
cleanup() { rm -rf "${STAGE_PARENT}"; }
trap cleanup EXIT

###############################################################################
# EXCLUDES  --  tweak freely. Passed verbatim to `rsync --exclude=`.
#
# Notes on KEEP-vs-DROP decisions encoded here:
#   * .github is KEPT (CI config is useful); only .git is dropped.
#   * src/reports/ is KEPT (it's source); only a top-level reports/ runtime dir
#     is dropped via the anchored "/reports/" pattern.
#   * scripts/e2e_seed/*.py is KEPT (reference seed script); only the generated
#     ".e2e_seed/" data dir is dropped.
#   * .env.example and .env.int.example are KEPT; .env and other .env.* dropped
#     (handled by include-then-exclude ordering in RSYNC_INCLUDES below).
###############################################################################
RSYNC_EXCLUDES=(
  # ---- Virtualenvs ----------------------------------------------------------
  ".venv/"
  ".venv311/"
  "venv/"
  "env/"
  "*/site-packages/"
  "*.egg-info/"

  # ---- VCS / IDE / agent state ---------------------------------------------
  ".git/"            # keep .github (NOT excluded)
  ".claude/"
  ".idea/"
  ".vscode/"
  ".DS_Store"

  # ---- Caches / coverage / build artifacts ---------------------------------
  "__pycache__/"
  "*.pyc"
  "*.pyo"
  ".pytest_cache/"
  ".mypy_cache/"
  ".ruff_cache/"
  "htmlcov/"
  ".coverage"
  "docs/sphinx/_build/"

  # ---- Secrets / local DB state --------------------------------------------
  # .env / .env.* handled by RSYNC_INCLUDES (keep *.example) then this glob:
  ".env"
  ".env.*"
  "valdo.db"
  "*.db"
  "*.sqlite"
  "*.sqlite3"

  # ---- Runtime output dirs (anchored to repo root with leading slash) ------
  "/uploads/"
  "/reports/"        # keep src/reports/ (not anchored to root)
  "/logs/"

  # ---- Local dry-run synthetic data ----------------------------------------
  ".e2e_seed/"       # keep scripts/e2e_seed/*.py (that's a dir named e2e_seed, not .e2e_seed)

  # ---- Spreadsheets (user requirement: no spreadsheets) --------------------
  "*.xlsx"
  "*.xls"
  "*.xlsm"

  # ---- Node ----------------------------------------------------------------
  "node_modules/"

  # ---- Never package the output of this script itself ----------------------
  "/dist/"
)

# Includes evaluated BEFORE the matching excludes so they win.
# This is how we keep the *.example files while dropping .env / .env.*.
RSYNC_INCLUDES=(
  ".env.example"
  ".env.int.example"
)

# --- Build rsync arg list ----------------------------------------------------
RSYNC_ARGS=(-a --delete)
for inc in "${RSYNC_INCLUDES[@]}"; do
  RSYNC_ARGS+=(--include="${inc}")
done
for exc in "${RSYNC_EXCLUDES[@]}"; do
  RSYNC_ARGS+=(--exclude="${exc}")
done

echo "==> Repo root : ${REPO_ROOT}"
echo "==> Staging   : ${STAGE_DIR}"
echo "==> Output    : ${ZIP_PATH}"
echo "==> Rsyncing working tree into staging (applying excludes) ..."

# Trailing slash on source copies CONTENTS into STAGE_DIR.
rsync "${RSYNC_ARGS[@]}" "${REPO_ROOT}/" "${STAGE_DIR}/" \
  || { echo "ERROR: rsync staging failed" >&2; exit 2; }

echo "==> Zipping ..."
# Zip from the staging parent so the archive has a single clean top folder.
( cd "${STAGE_PARENT}" && zip -r -q "${ZIP_PATH}" "${STAGE_NAME}" )

# =============================================================================
# SUMMARY
# =============================================================================
ZIP_SIZE_H="$(du -h "${ZIP_PATH}" | cut -f1)"
FILE_COUNT="$(unzip -l "${ZIP_PATH}" | tail -1 | awk '{print $2}')"

echo
echo "============================================================"
echo " PACKAGE SUMMARY"
echo "============================================================"
echo " Zip file   : ${ZIP_PATH}"
echo " Zip size   : ${ZIP_SIZE_H}"
echo " File count : ${FILE_COUNT}"
echo "============================================================"

# =============================================================================
# VERIFICATION  --  assert ZERO forbidden entries leaked into the archive.
# =============================================================================
echo
echo "==> VERIFICATION: scanning archive listing for forbidden entries ..."

LISTING="$(unzip -Z1 "${ZIP_PATH}")"

# regex => human label
FORBIDDEN_PATTERNS=(
  "(^|/)\.venv"
  "site-packages"
  "\.git/"
  "\.xlsx?$"
  "__pycache__"
  "(^|/)\.env$"
  "\.db$"
)

VERIFY_FAIL=0
for pat in "${FORBIDDEN_PATTERNS[@]}"; do
  matches="$(printf '%s\n' "${LISTING}" | grep -E "${pat}" || true)"
  if [[ -n "${matches}" ]]; then
    VERIFY_FAIL=1
    cnt="$(printf '%s\n' "${matches}" | grep -c . )"
    echo "  [FAIL] pattern '${pat}' matched ${cnt} entr(y/ies):"
    printf '%s\n' "${matches}" | head -10 | sed 's/^/         /'
  else
    echo "  [PASS] no entries match '${pat}'"
  fi
done

# =============================================================================
# TOP 10 LARGEST ENTRIES  --  operator sanity check.
# =============================================================================
echo
echo "==> TOP 10 LARGEST ENTRIES IN BUNDLE (uncompressed bytes):"
# `unzip -l` columns: Length  Date  Time  Name. We skip the 3-line header and
# the trailing 2 summary lines (separator "-----" + grand total) by only
# accepting rows whose first field is purely numeric AND that are not the
# grand-total row (the total row has no Date/Time, i.e. fewer fields).
# `sort` reads all input (no SIGPIPE); `head` is the last stage so a closed
# pipe there cannot abort an upstream command under pipefail.
unzip -l "${ZIP_PATH}" \
  | awk 'NF>=4 && $1 ~ /^[0-9]+$/ && $2 ~ /^[0-9]{2}-[0-9]{2}-[0-9]{2,4}$/ {
           size=$1; name=$4; for(i=5;i<=NF;i++) name=name" "$i;
           printf "%d\t%s\n", size, name }' \
  | sort -rn \
  | awk -F'\t' 'NR<=10 {
           v=$1; u="B";
           if (v>=1048576){v=v/1048576;u="MB"} else if (v>=1024){v=v/1024;u="KB"}
           printf "   %8.1f %-2s  %s\n", v, u, $2 }'

echo
if [[ "${VERIFY_FAIL}" -ne 0 ]]; then
  echo "RESULT: VERIFICATION FAILED -- forbidden entries present (see above)." >&2
  echo "        The archive at ${ZIP_PATH} is NOT clean." >&2
  exit 1
fi

echo "RESULT: VERIFICATION CLEAN. Bundle ready: ${ZIP_PATH}"
exit 0

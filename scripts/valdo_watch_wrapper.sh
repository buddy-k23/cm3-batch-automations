#!/usr/bin/env bash
# Valdo E2E batch testing — trigger-file watcher wrapper (M7).
#
# Wraps scripts/e2e_lib/watch.py so a trigger-file drop in any source's
# trigger_root is routed to scripts/run_e2e_source.sh within
# --poll-interval seconds. The watcher uses the path resolver's
# classify_filename to map (data file basename) -> (source, file_type,
# direction).
#
# Usage:
#   bash scripts/valdo_watch_wrapper.sh --env sit
#       [--poll-interval 5]                         # default: 5
#       [--paths-yaml config/e2e/paths.yml]
#       [--sources-dir config/e2e/sources]
#       [--valdo-executable valdo]
#       [--once]                                    # one cycle, exit
#       [--max-iterations N]                        # cap iterations (testing)
#
# Trigger-file naming contract
# ----------------------------
# CA ESP drops a sidecar file under
#   <trigger_root>/<arbitrary_name>.trigger
# whose ``trigger_file.data_file_line`` (default: line 1) contains the
# basename of the data file it announces. The basename must match one of
# the regex patterns in ``paths.yml`` ``filename_patterns:`` so the
# resolver can classify it as (source, file_type, direction).
#
# After a poll cycle the watcher renames each sidecar in place:
#   * matched + processed  -> <name>.trigger.processed
#   * could not classify   -> <name>.trigger.unmatched + .reason file
#
# Sidecars that already carry one of those suffixes are skipped on
# subsequent cycles, so re-runs are safe.
#
# Latency
# -------
# With the default 5-second poll interval the worst-case end-to-end
# latency from sidecar drop to run_e2e_source.sh start is <=5 seconds,
# meeting the prompt's acceptance criterion #5 (<=10 seconds).
#
# Daemonization
# -------------
# This wrapper is a foreground process. Run under systemd with
# Restart=on-failure, or under CA ESP as a long-running job. SIGTERM
# and SIGINT cause a graceful exit at the next poll boundary.
#
# Exit codes:
#   0 = clean exit (received SIGTERM/SIGINT or --once / --max-iterations
#       reached without an infrastructure error)
#   3 = infrastructure error (config invalid, env unknown, etc.)

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PY="${VALDO_PYTHON:-}"
if [[ -z "${PY}" ]]; then
  if   command -v python3 >/dev/null 2>&1; then PY="python3"
  elif command -v python  >/dev/null 2>&1; then PY="python"
  else
    echo "error: no Python interpreter found (set VALDO_PYTHON)" >&2
    exit 3
  fi
fi

# Forward every argument unchanged. The Python core does its own parsing
# and signal handling.
exec "${PY}" -m scripts.e2e_lib.watch "$@"

#!/usr/bin/env python3
"""Workbook drift CI guardrail driver (EC-S11, Sprint 3).

Runs ``valdo onboard-source --check`` against every committed source
onboarding workbook under ``templates/*_onboarding.xlsx`` (excluding
the blank ``source_onboarding_template.xlsx``). Captures the drift
output, filters drift lines against
``.github/workflows/workbook-drift-allowlist.txt``, and exits
non-zero if any non-allowlisted drift line remains.

Design rationale
----------------
The same script is invoked by both the GitHub Actions workflow
(``.github/workflows/workbook-drift-check.yml``) and the local
developer wrapper (``scripts/check_workbook_drift.sh``). Keeping the
logic in a single Python module guarantees CI and local behaviour
cannot drift apart (no shell-vs-actions YAML divergence). The filter
logic (:func:`filter_drift_lines`) is the unit-tested core; the
:func:`main` wrapper handles workbook discovery + subprocess
invocation.

Usage::

    python scripts/check_workbook_drift.py
    python scripts/check_workbook_drift.py templates/SHAW_onboarding.xlsx
    python scripts/check_workbook_drift.py templates/SHAW_onboarding.xlsx \\
        --allowlist .github/workflows/workbook-drift-allowlist.txt

Exit codes
----------
* ``0`` -- every workbook either reported no drift, or every reported
  drift line matched an allowlist entry.
* ``1`` -- at least one non-allowlisted drift line was reported, OR a
  workbook subprocess failed with an unexpected error.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 -- arg-array invocation only, no shell=True
import sys
from pathlib import Path
from typing import Iterable

# ---------------------------------------------------------------------------
# Configuration defaults (overridable via CLI flags for testability).
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATES_DIR = REPO_ROOT / "templates"
DEFAULT_ALLOWLIST_PATH = (
    REPO_ROOT / ".github" / "workflows" / "workbook-drift-allowlist.txt"
)
DEFAULT_BLANK_TEMPLATE_NAME = "source_onboarding_template.xlsx"

# Drift lines emitted by ``valdo onboard-source --check`` follow the
# shape ``"  drift <path>: <reason>"``. The summary line ``"  N of M
# artefacts match."`` is informational and is NOT a drift line.
_DRIFT_LINE_PREFIX = "drift "


# ---------------------------------------------------------------------------
# Allowlist parsing.
# ---------------------------------------------------------------------------


def load_allowlist(path: Path) -> list[str]:
    """Load substring entries from the allowlist file.

    Blank lines and lines whose first non-whitespace character is
    ``#`` are skipped (comment convention -- see the allowlist file
    header).

    Args:
        path: Filesystem path to the allowlist file.

    Returns:
        Ordered list of substrings. Empty when the file does not
        exist (so a missing allowlist behaves like "tolerate
        nothing").
    """
    if not path.exists():
        return []
    entries: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        entries.append(stripped)
    return entries


# ---------------------------------------------------------------------------
# Filter logic -- the unit-tested core.
# ---------------------------------------------------------------------------


def is_drift_line(line: str) -> bool:
    """Return ``True`` when *line* is a drift line from ``--check``.

    Drift lines are produced by ``_run_check_mode`` in
    :mod:`src.commands.onboard_source` and have the shape
    ``"  drift <path>: <reason>"``. The summary line ``"  N of M
    artefacts match."`` is intentionally excluded -- it is
    informational and would always be filtered out otherwise.

    Args:
        line: A single output line (with or without leading
            whitespace; trailing newline tolerated).

    Returns:
        ``True`` when *line* is a drift line.
    """
    return line.strip().startswith(_DRIFT_LINE_PREFIX)


def filter_drift_lines(
    output: str,
    allowlist: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Partition ``valdo onboard-source --check`` output by allowlist match.

    Args:
        output: The combined stdout + stderr capture from one or more
            ``valdo onboard-source --check`` invocations.
        allowlist: Iterable of substrings; a drift line is tolerated
            if it contains ANY substring from the iterable.

    Returns:
        Tuple ``(tolerated, unexpected)`` where:

        * ``tolerated`` is the list of drift lines that matched at
          least one allowlist substring.
        * ``unexpected`` is the list of drift lines that matched
          none of the allowlist substrings -- these fail the CI
          guardrail.

        Non-drift lines (summary, progress messages, blank lines) are
        not included in either list.
    """
    # Normalise: discard ``None`` / empty / pure-whitespace entries so a
    # stray blank line in the allowlist file (or a programmer mistake)
    # cannot silently match every drift line via ``"" in line``.
    allowlist_list = [
        entry.strip()
        for entry in allowlist
        if isinstance(entry, str) and entry.strip()
    ]
    tolerated: list[str] = []
    unexpected: list[str] = []
    for raw_line in output.splitlines():
        if not is_drift_line(raw_line):
            continue
        line = raw_line.rstrip()
        if any(entry in line for entry in allowlist_list):
            tolerated.append(line)
        else:
            unexpected.append(line)
    return tolerated, unexpected


# ---------------------------------------------------------------------------
# Workbook discovery.
# ---------------------------------------------------------------------------


def discover_workbooks(
    templates_dir: Path,
    blank_template_name: str = DEFAULT_BLANK_TEMPLATE_NAME,
) -> list[Path]:
    """Return sorted ``*.xlsx`` workbooks under *templates_dir*.

    The blank template (``source_onboarding_template.xlsx``) is
    excluded -- it is an empty scaffold without committed artefacts
    to check against.

    Args:
        templates_dir: Directory to scan (non-recursive).
        blank_template_name: Filename to exclude from discovery.

    Returns:
        Sorted list of workbook paths. Empty when the directory
        does not exist or contains no matching workbooks.
    """
    if not templates_dir.exists():
        return []
    workbooks = [
        wb
        for wb in sorted(templates_dir.glob("*.xlsx"))
        if wb.name != blank_template_name
    ]
    return workbooks


# ---------------------------------------------------------------------------
# Subprocess invocation.
# ---------------------------------------------------------------------------


def run_check_for_workbook(
    workbook: Path,
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[int, str]:
    """Invoke ``valdo onboard-source --check`` for one workbook.

    Args:
        workbook: Filesystem path to the ``.xlsx`` workbook.
        repo_root: Working directory for the subprocess. Required so
            the emitter resolves committed artefact paths relative
            to the repository root.

    Returns:
        Tuple ``(returncode, combined_output)``. Stdout and stderr
        are merged via ``stderr=subprocess.STDOUT`` so the caller
        sees the same stream the CLI user would.
    """
    # Arg-array invocation -- no shell=True. The wrapper script
    # (``scripts/check_workbook_drift.sh``) cannot inject extra args
    # because we hard-code the command line here.
    cmd = [
        sys.executable,
        "-m",
        "src.main",
        "onboard-source",
        str(workbook),
        "--check",
    ]
    completed = subprocess.run(  # nosec B603 -- arg-array, no shell
        cmd,
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, combined


# ---------------------------------------------------------------------------
# Main driver.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for both CI and local invocation.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        ``0`` when every workbook passes (no drift, or only
        allowlisted drift); ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Run `valdo onboard-source --check` for every committed "
            "source workbook and filter drift lines against the "
            "allowlist. CI guardrail for EC-S11 (Sprint 3)."
        ),
    )
    parser.add_argument(
        "workbooks",
        nargs="*",
        type=Path,
        help=(
            "Optional explicit workbook paths. When omitted, every "
            "`templates/*.xlsx` (except the blank template) is "
            "checked."
        ),
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=DEFAULT_ALLOWLIST_PATH,
        help=(
            "Path to the allowlist file (default: "
            ".github/workflows/workbook-drift-allowlist.txt)."
        ),
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=DEFAULT_TEMPLATES_DIR,
        help="Directory to scan for workbooks (default: templates/).",
    )
    args = parser.parse_args(argv)

    workbooks: list[Path] = (
        list(args.workbooks)
        if args.workbooks
        else discover_workbooks(args.templates_dir)
    )
    if not workbooks:
        print("no workbooks discovered -- nothing to check.")
        return 0

    allowlist = load_allowlist(args.allowlist)
    print(f"workbook drift check: {len(workbooks)} workbook(s) discovered.")
    print(f"allowlist entries:    {len(allowlist)}")
    print("")

    total_tolerated = 0
    total_unexpected = 0
    failed_workbooks: list[Path] = []

    for workbook in workbooks:
        print(f"--- {workbook.name} ---")
        returncode, output = run_check_for_workbook(workbook)
        # Echo the raw CLI output verbatim so CI logs are readable.
        print(output.rstrip())

        tolerated, unexpected = filter_drift_lines(output, allowlist)
        total_tolerated += len(tolerated)
        total_unexpected += len(unexpected)

        # An unexpected non-zero exit from valdo (e.g. workbook
        # crashed) without any drift lines is itself a failure.
        if returncode != 0 and not (tolerated or unexpected):
            print(
                f"FAIL: {workbook.name} exited {returncode} without "
                "producing parseable drift output."
            )
            failed_workbooks.append(workbook)
        elif unexpected:
            failed_workbooks.append(workbook)
        print("")

    # ---- Summary block ----
    print("=" * 60)
    print("workbook drift CI guardrail summary (EC-S11)")
    print("=" * 60)
    print(f"  workbooks checked         : {len(workbooks)}")
    print(f"  tolerated drift lines     : {total_tolerated}")
    print(f"  unexpected drift lines    : {total_unexpected}")
    print(f"  workbooks failed          : {len(failed_workbooks)}")

    if failed_workbooks or total_unexpected:
        print("")
        print("RESULT: FAIL")
        print(
            "One or more workbooks reported drift not covered by the "
            "allowlist. Either update the workbook to regenerate the "
            "committed artefact, or add a justified allowlist entry "
            "per the convention in workbook-drift-allowlist.txt."
        )
        return 1

    print("")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

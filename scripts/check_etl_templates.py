#!/usr/bin/env python3
"""ETL-templates drift CI guardrail driver (S8-5, #384).

Discovers every committed BA-facing ETL template under
``templates/etl/*.yml``, validates each through
:class:`src.pipeline.etl_config.SourceConfig`, and -- when a paired
``<name>_sample/`` directory exists -- runs the documented happy-path
sample command and asserts the documented exit code.

Design rationale
----------------

Mirrors the EC-S11 :mod:`scripts.check_workbook_drift` driver: the
same script is invoked by both the GitHub Actions workflow
(``.github/workflows/etl-templates-check.yml``) and the local developer
wrapper (``scripts/check_etl_templates.sh``). Keeping the logic in a
single Python module guarantees CI and local behaviour cannot drift
apart (no shell-vs-actions YAML divergence).

What it checks per template
---------------------------

1. **Pydantic parse.** The committed YAML round-trips through
   ``SourceConfig.model_validate()``. This is the only currently
   wired-up contract: although issue #384 mentions a separate
   ``ReconciliationConfig`` model, no such model exists in the
   codebase today (every committed template validates through
   :class:`SourceConfig`, including ``db_to_file_reconciliation.yml``
   per the S7-1 design note). A TODO below tracks the future split.

2. **Sample run (best-effort).** When the paired ``<name>_sample/``
   directory exists, the driver dispatches the documented command:

   * If ``<name>_sample/build_sample.py`` exists -> run it with the
     project ``python3`` interpreter. Asserts exit ``0``. This is the
     contract for ``fixed_width_single_record`` (the script
     deterministically regenerates ``input.txt``) and
     ``db_to_file_reconciliation`` (the script seeds a SQLite DB,
     runs the documented extract, and asserts zero violations).
   * Else if ``<name>_sample/left.csv`` and ``<name>_sample/right.csv``
     both exist -> run ``valdo compare`` with ``--keys CUSTOMER_ID``
     (the documented CSV-shape worked example). Asserts exit ``0``
     per the CSV README: ``valdo compare`` exits 0 even when row-
     level differences are reported.
   * Else -> emit a notice and skip the sample run. The Pydantic
     parse still gates this template.

   The ``valdo db-compare`` CLI is intentionally NOT invoked from CI:
   it requires Oracle network access (the GitHub Actions runner has
   none), and the SQLite-backed equivalent is what
   ``build_sample.py`` does internally for the DB-to-file template.

R028B-style allowlist
---------------------

Two escape hatches mirror the EC-S11 convention:

* A repo-wide allowlist file
  (``.github/workflows/etl-templates-allowlist.txt``) with one
  substring per line. Any failure line whose text contains a listed
  substring is tolerated. Reused mechanism, parsed identically to
  the workbook-drift allowlist.
* A per-template inline escape hatch: a YAML comment

  .. code-block:: yaml

     # valdo-drift-allowed: <one-line justification>

  as the FIRST line of the template file. The driver skips both the
  Pydantic parse and the sample run for that template (with a clear
  WARN line in the log). Mirrors the EC-S11 inline pattern used to
  carve out templates that intentionally live outside the
  ``SourceConfig`` contract.

Usage::

    python scripts/check_etl_templates.py
    python scripts/check_etl_templates.py templates/etl/csv_file_comparison.yml
    python scripts/check_etl_templates.py --allowlist \\
        .github/workflows/etl-templates-allowlist.txt

Exit codes
----------
* ``0`` -- every discovered template either passed both checks, only
  emitted allowlisted failure lines, or carried a valid inline
  ``valdo-drift-allowed:`` carve-out.
* ``1`` -- at least one non-allowlisted failure line was reported.
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404 -- arg-array invocation only, no shell=True
import sys
from pathlib import Path
from typing import Iterable, Optional

# ---------------------------------------------------------------------------
# Configuration defaults (overridable via CLI flags for testability).
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATES_DIR = REPO_ROOT / "templates" / "etl"
DEFAULT_ALLOWLIST_PATH = (
    REPO_ROOT / ".github" / "workflows" / "etl-templates-allowlist.txt"
)

# Sample-directory suffix convention. Same as the MCP auto-discovery
# module (``src/mcp/resources/etl_templates.py``).
_SAMPLE_DIR_SUFFIX = "_sample"

# Inline per-template escape hatch -- mirrors the R028B-style carve-out
# convention used by the EC-S11 workbook drift workflow. The driver
# scans the FIRST line of each template for this exact comment shape.
_INLINE_ALLOW_PREFIX = "# valdo-drift-allowed:"

# Failure lines emitted by the driver follow this shape so the
# allowlist-substring filter and the readability conventions stay aligned
# with EC-S11's "drift <path>: <reason>" shape.
_DRIFT_LINE_PREFIX = "drift "

# TODO(future-story): The issue body mentions a separate
# ``ReconciliationConfig`` model for the db_to_file_reconciliation
# template family. No such model exists in src/pipeline/etl_config.py
# today -- every committed template validates through SourceConfig per
# the S7-1 dev-agent note. If a ReconciliationConfig is introduced
# later, replace _pick_model_for() with a top-level-key sniffer
# (presence of ``expected_sql:`` + ``reconciliation:`` -> Reconciliation;
# else SourceConfig).


# ---------------------------------------------------------------------------
# Allowlist parsing (identical contract to workbook-drift allowlist).
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


def is_drift_line(line: str) -> bool:
    """Return True when *line* is a failure line emitted by this driver.

    Failure lines have the shape ``"  drift <path>: <reason>"`` --
    identical to the EC-S11 convention so the operator's eyes see the
    same shape across both guardrails.
    """
    return line.strip().startswith(_DRIFT_LINE_PREFIX)


def filter_drift_lines(
    output: str,
    allowlist: Iterable[str],
) -> tuple[list[str], list[str]]:
    """Partition driver output into tolerated and unexpected failure lines.

    Args:
        output: The combined stdout of the per-template loop.
        allowlist: Iterable of substrings; a drift line is tolerated
            when it contains ANY substring from the iterable.

    Returns:
        Tuple ``(tolerated, unexpected)``. Non-drift lines (notices,
        summary, blank lines) appear in neither list.
    """
    # Discard blank entries so a stray empty allowlist line cannot
    # silently match every drift line via ``"" in line``.
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
# Inline carve-out detection.
# ---------------------------------------------------------------------------


def read_inline_carveout(template_path: Path) -> Optional[str]:
    """Return the inline carve-out reason if the template opts out.

    Scans the first non-blank line of *template_path*. When it matches
    the ``# valdo-drift-allowed: <reason>`` shape, returns ``<reason>``
    (with surrounding whitespace stripped). Otherwise returns ``None``.

    Args:
        template_path: Path to the template YAML file.

    Returns:
        The carve-out reason string, or ``None`` when the template
        does not opt out.
    """
    try:
        text = template_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(_INLINE_ALLOW_PREFIX):
            return line[len(_INLINE_ALLOW_PREFIX) :].strip() or "(no reason given)"
        # The first non-blank line was not a carve-out -- stop looking
        # so a stray ``# valdo-drift-allowed`` mid-document cannot
        # silently disable the check.
        return None
    return None


# ---------------------------------------------------------------------------
# Template + sample discovery.
# ---------------------------------------------------------------------------


def discover_templates(templates_dir: Path) -> list[Path]:
    """Return sorted ``*.yml`` templates under *templates_dir*.

    Args:
        templates_dir: Directory to scan (non-recursive).

    Returns:
        Sorted list of YAML template paths. Empty when the directory
        does not exist.
    """
    if not templates_dir.exists():
        return []
    return sorted(templates_dir.glob("*.yml"))


def sample_dir_for(template_path: Path) -> Path:
    """Return the conventional ``<name>_sample/`` path for *template_path*.

    The path is returned whether or not the directory exists; callers
    use ``.is_dir()`` to gate behaviour.
    """
    return template_path.parent / f"{template_path.stem}{_SAMPLE_DIR_SUFFIX}"


# ---------------------------------------------------------------------------
# Pydantic parse check.
# ---------------------------------------------------------------------------


def check_template_parses(template_path: Path) -> Optional[str]:
    """Validate *template_path* through ``SourceConfig.model_validate``.

    Args:
        template_path: Path to the template YAML file.

    Returns:
        ``None`` on success; a one-line failure reason string on
        failure (file unreadable, YAML parse error, schema violation).
    """
    # Local imports so the driver can be loaded without dragging the
    # full pipeline import graph on every invocation (the editable
    # install in CI takes a few seconds to import otherwise).
    #
    # Defensive sys.path insertion: when this script is invoked as
    # ``python scripts/check_etl_templates.py`` (not ``python -m``),
    # ``sys.path[0]`` becomes the ``scripts/`` directory and the
    # editable install of ``src/`` is NOT auto-discovered unless
    # ``pip install -e .`` has populated site-packages. The wrapper
    # already cd's to REPO_ROOT; ensuring REPO_ROOT is on sys.path
    # mirrors the same fallback used by the per-sample
    # ``build_sample.py`` drivers.
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    try:
        import yaml

        from src.pipeline.etl_config import SourceConfig
    except ImportError as exc:
        return f"could not import validator dependencies: {type(exc).__name__}: {exc}"

    try:
        with template_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        return f"YAML parse failed: {type(exc).__name__}: {exc}"

    if not isinstance(raw, dict):
        return "YAML root is not a mapping"

    try:
        SourceConfig.model_validate(raw)
    except Exception as exc:  # pydantic.ValidationError + safety net
        # Trim multi-line Pydantic error to its first line so the
        # failure line stays scannable in CI logs.
        first_line = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return f"SourceConfig validation failed: {first_line}"

    return None


# ---------------------------------------------------------------------------
# Sample run dispatch.
# ---------------------------------------------------------------------------


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: Path,
) -> tuple[int, str]:
    """Run *cmd* and return ``(returncode, combined_output)``.

    Args:
        cmd: Argument-array command line (no ``shell=True``).
        cwd: Working directory for the subprocess.

    Returns:
        Tuple of return code and merged stdout+stderr.
    """
    completed = subprocess.run(  # nosec B603 -- arg-array, no shell
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    combined = (completed.stdout or "") + (completed.stderr or "")
    return completed.returncode, combined


def check_sample_run(
    template_path: Path,
    sample_dir: Path,
) -> tuple[Optional[str], str]:
    """Dispatch the documented sample command for *template_path*.

    Args:
        template_path: Path to the template YAML.
        sample_dir: Path to the paired ``<name>_sample/`` directory
            (caller has already verified it exists).

    Returns:
        Tuple ``(failure_reason, dispatch_note)``:

        * ``failure_reason`` is ``None`` on success, else a one-line
          string suitable for embedding in a drift line.
        * ``dispatch_note`` is a one-line human-readable description
          of which sample command the driver picked (always populated
          for the CI log, even on success).
    """
    build_sample = sample_dir / "build_sample.py"
    if build_sample.is_file():
        note = f"build_sample.py -> python3 {build_sample.relative_to(REPO_ROOT)}"
        returncode, output = _run_subprocess(
            [sys.executable, str(build_sample)],
            cwd=REPO_ROOT,
        )
        if returncode != 0:
            tail = output.strip().splitlines()[-1] if output.strip() else "(no output)"
            return (
                f"build_sample.py exited {returncode}; last line: {tail}",
                note,
            )
        return None, note

    left = sample_dir / "left.csv"
    right = sample_dir / "right.csv"
    mapping = sample_dir / "mapping.json"
    if left.is_file() and right.is_file() and mapping.is_file():
        # Documented CSV-shape happy-path: README pins
        # ``--keys CUSTOMER_ID``. Encoded here as the only currently
        # shipped CSV sample contract; if a future CSV sample uses a
        # different key, it will surface either as a CSV diff or as
        # a parse error -- both are correct CI signals.
        #
        # Exit-code policy (post-S8-1, #392):
        #   * 0 -- files match and no diffs were reported.
        #   * 1 -- differences were reported (the worked CSV sample is
        #     deliberately seeded with one row-level diff + one
        #     only-in-left + one only-in-right; this is the documented
        #     "successful run with documented diff" outcome).
        #   * >= 2 -- fatal error (file unreadable, mapping invalid,
        #     CLI crashed). This is the actual failure surface the
        #     guardrail must catch.
        note = f"valdo compare -> {left.relative_to(REPO_ROOT)} vs {right.relative_to(REPO_ROOT)}"
        cmd = [
            sys.executable,
            "-m",
            "src.main",
            "compare",
            "--file1",
            str(left),
            "--file2",
            str(right),
            "--keys",
            "CUSTOMER_ID",
            "--mapping",
            str(mapping),
        ]
        returncode, output = _run_subprocess(cmd, cwd=REPO_ROOT)
        # Accept 0 (clean compare) and 1 (compare ran, reported diffs
        # per S8-1's exit-code contract). Anything else is a CLI crash.
        if returncode >= 2:
            tail = output.strip().splitlines()[-1] if output.strip() else "(no output)"
            return (
                f"valdo compare exited {returncode} (fatal); last line: {tail}",
                note,
            )
        return None, note

    return (
        None,
        "no runnable sample (skipped sample run; parse-only)",
    )


# ---------------------------------------------------------------------------
# Per-template driver.
# ---------------------------------------------------------------------------


def check_one_template(template_path: Path) -> tuple[list[str], list[str]]:
    """Run all checks for *template_path*.

    Args:
        template_path: Path to the committed template YAML.

    Returns:
        Tuple ``(notices, drift_lines)``:

        * ``notices`` are human-readable single-line strings for the
          CI log (e.g. "checked-out", "parse OK", which sample command
          ran). They are NEVER drift lines.
        * ``drift_lines`` are the EC-S11-style
          ``"drift <path>: <reason>"`` lines, one per failed check.
          Empty when the template fully passes.
    """
    notices: list[str] = []
    drift_lines: list[str] = []

    inline = read_inline_carveout(template_path)
    if inline is not None:
        notices.append(
            f"WARN: inline carve-out active -- skipping all checks "
            f"({_INLINE_ALLOW_PREFIX} {inline})"
        )
        return notices, drift_lines

    parse_failure = check_template_parses(template_path)
    if parse_failure is not None:
        drift_lines.append(
            f"drift {template_path.relative_to(REPO_ROOT)}: {parse_failure}"
        )
        # Skip the sample run if the template itself is broken: the
        # sample run would just compound the noise.
        return notices, drift_lines
    notices.append(f"parse OK: SourceConfig.model_validate accepted the YAML")

    sample_dir = sample_dir_for(template_path)
    if not sample_dir.is_dir():
        notices.append(
            f"sample check skipped: no paired directory at "
            f"{sample_dir.relative_to(REPO_ROOT)}"
        )
        return notices, drift_lines

    sample_failure, dispatch_note = check_sample_run(template_path, sample_dir)
    notices.append(f"sample dispatch: {dispatch_note}")
    if sample_failure is not None:
        drift_lines.append(
            f"drift {sample_dir.relative_to(REPO_ROOT)}: {sample_failure}"
        )
    else:
        notices.append("sample run OK")
    return notices, drift_lines


# ---------------------------------------------------------------------------
# Main driver.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Entry point for both CI and local invocation.

    Args:
        argv: Optional argument list (defaults to ``sys.argv[1:]``).

    Returns:
        ``0`` when every template passes (no drift, only allowlisted
        drift, or inline carve-out); ``1`` otherwise.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Validate every templates/etl/*.yml through SourceConfig "
            "and run the documented sample command. CI guardrail for "
            "S8-5 (issue #384)."
        ),
    )
    parser.add_argument(
        "templates",
        nargs="*",
        type=Path,
        help=(
            "Optional explicit template paths. When omitted, every "
            "`templates/etl/*.yml` is checked."
        ),
    )
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=DEFAULT_ALLOWLIST_PATH,
        help=(
            "Path to the allowlist file (default: "
            ".github/workflows/etl-templates-allowlist.txt)."
        ),
    )
    parser.add_argument(
        "--templates-dir",
        type=Path,
        default=DEFAULT_TEMPLATES_DIR,
        help="Directory to scan for templates (default: templates/etl/).",
    )
    args = parser.parse_args(argv)

    templates: list[Path] = (
        list(args.templates)
        if args.templates
        else discover_templates(args.templates_dir)
    )
    if not templates:
        print("no templates discovered -- nothing to check.")
        return 0

    allowlist = load_allowlist(args.allowlist)
    print(f"etl templates check: {len(templates)} template(s) discovered.")
    print(f"allowlist entries:   {len(allowlist)}")
    print("")

    all_output_lines: list[str] = []
    samples_run = 0
    samples_skipped = 0
    inline_carveouts = 0

    for template in templates:
        try:
            rel = template.resolve().relative_to(REPO_ROOT)
        except ValueError:
            # Explicit out-of-tree path (e.g. test invocation); fall
            # back to the absolute path so the operator still sees
            # which file is being checked.
            rel = template
        print(f"--- {rel} ---")
        notices, drift_lines = check_one_template(template)
        for notice in notices:
            print(f"  {notice}")
            all_output_lines.append(notice)
            if notice.startswith("WARN: inline carve-out active"):
                inline_carveouts += 1
            if "sample check skipped" in notice:
                samples_skipped += 1
            if notice == "sample run OK":
                samples_run += 1
        for drift in drift_lines:
            print(f"  {drift}")
            all_output_lines.append(drift)
        print("")

    combined_output = "\n".join(all_output_lines)
    tolerated, unexpected = filter_drift_lines(combined_output, allowlist)

    print("=" * 60)
    print("etl templates CI guardrail summary (S8-5)")
    print("=" * 60)
    print(f"  templates checked          : {len(templates)}")
    print(f"  inline carve-outs honoured : {inline_carveouts}")
    print(f"  samples run                : {samples_run}")
    print(f"  samples skipped (no dir)   : {samples_skipped}")
    print(f"  tolerated drift lines      : {len(tolerated)}")
    print(f"  unexpected drift lines     : {len(unexpected)}")

    if unexpected:
        print("")
        print("RESULT: FAIL")
        print(
            "One or more templates produced drift not covered by the "
            "allowlist. Either fix the template, add a justified "
            "allowlist entry per the convention in "
            "etl-templates-allowlist.txt, or add an inline "
            f"`{_INLINE_ALLOW_PREFIX} <reason>` carve-out at the top of "
            "the template YAML."
        )
        return 1

    print("")
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

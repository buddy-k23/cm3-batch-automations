"""Build a per-run global roll-up index for the Valdo E2E batch harness.

This is the M6 deliverable from ``prompts/e2e_batch_testing_prompt.md``
(gap **b** in the prompt's ``<valdo_gaps_to_implement>``).

Input
-----
A reports directory holding one subdirectory per source for a single run::

    <reports_root>/<run_id>/
        SRC_A/
            summary.json     <-- written by run_e2e_source.sh (M4)
            index.html       <-- per-source roll-up (M4)
            ...              <-- Valdo's per-file HTML reports
        SRC_B/
            summary.json
            ...

The roll-up scans every immediate subdirectory of the reports directory,
loads each ``summary.json`` (M4's machine-readable per-source summary),
and emits two files at the reports directory's root:

* ``index.html`` — single page linking every source's per-source roll-up
  and listing each gate's pass/fail status, layer, blocking flag, step
  count, and error counters. Uses no external assets so it works from
  the RHEL host without any browser pipeline.
* ``summary.json`` — machine-readable mirror of the same data, useful
  for downstream automation (Splunk, dashboards, notifier hooks).

Both files are atomically replaced on re-run (``write`` then ``replace``).

What this script does NOT do
----------------------------
* It does **not** modify Valdo's per-file HTML report renderer (per the
  prompt's hard rule #1).
* It does **not** invoke Valdo. The wrapper script in M4 has already
  produced the per-source artifacts before this runs.
* It does **not** read the Oracle audit table. Failures are surfaced
  via the per-source ``summary.json`` files; the audit table is a
  separate, durable record consumed by the on-call team's queries.

CLI
---
::

    python scripts/build_rollup_index.py \\
        --reports-dir /data/sit/_reports/20260514_120000 \\
        [--run-id 20260514_120000] [--env sit]
        [--out-dir /data/sit/_reports/20260514_120000]
        [--quiet]

Exit codes:
  0 — success, no blocking failures observed across the scanned sources
  2 — at least one source recorded a blocking-gate failure
  3 — I/O error (reports dir missing, summary.json unreadable, etc.)

Note: a `--strict` mode is offered (default off) that escalates exit code
2 for ANY failed gate, blocking or not. Off by default because the design
treats non-blocking failures as informational (matches acceptance
criterion 3 in the prompt).
"""

from __future__ import annotations

import argparse
import html
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

# Per-source summary keys we rely on. Anything else is preserved verbatim
# under ``extras`` so future additions in run_source.py do not require a
# matching change here.
_SUMMARY_KEYS = ("run_id", "env", "source", "gates")

# Exit codes.
EXIT_OK = 0
EXIT_BLOCKING_FAILURE = 2
EXIT_INFRA_ERROR = 3


class RollupError(RuntimeError):
    """Raised for any roll-up build failure."""


# --------------------------------------------------------------------------- #
# Data model
# --------------------------------------------------------------------------- #


@dataclass
class GateRow:
    """One row of the roll-up index — represents one gate's outcome."""

    source: str
    gate: str
    status: str  # passed | failed | skipped | infra_error | <unknown>
    blocking: bool
    layer: Optional[str]
    step_count: int
    error: str
    file_name: Optional[str] = None
    row_count: Optional[int] = None
    error_count: Optional[int] = None
    report_path: Optional[str] = None


@dataclass
class MultiRecordLink:
    """A discovered multi-record validation report for one file type."""

    file_type: str
    index_html: Path  # absolute path on disk


@dataclass
class SourceSummary:
    """One source's summary.json contents, normalized."""

    source: str
    run_id: str
    env: str
    rollup_html: Optional[Path]
    gates: List[Dict[str, Any]] = field(default_factory=list)
    extras: Dict[str, Any] = field(default_factory=dict)
    multi_record_reports: List["MultiRecordLink"] = field(default_factory=list)

    @property
    def has_blocking_failure(self) -> bool:
        for g in self.gates:
            if g.get("status") == "failed" and g.get("blocking"):
                return True
            if g.get("status") == "infra_error" and g.get("blocking"):
                return True
        return False

    @property
    def has_any_failure(self) -> bool:
        return any(g.get("status") in {"failed", "infra_error"} for g in self.gates)


@dataclass
class RollupReport:
    """Aggregated view across every source scanned."""

    run_id: str
    env: str
    reports_dir: Path
    generated_at: str
    sources: List[SourceSummary]

    @property
    def total_sources(self) -> int:
        return len(self.sources)

    @property
    def total_gates(self) -> int:
        return sum(len(s.gates) for s in self.sources)

    @property
    def gates_passed(self) -> int:
        return self._count_status("passed")

    @property
    def gates_failed(self) -> int:
        return self._count_status("failed")

    @property
    def gates_skipped(self) -> int:
        return self._count_status("skipped")

    @property
    def gates_infra_error(self) -> int:
        return self._count_status("infra_error")

    @property
    def sources_with_blocking_failure(self) -> List[str]:
        return [s.source for s in self.sources if s.has_blocking_failure]

    @property
    def sources_with_any_failure(self) -> List[str]:
        return [s.source for s in self.sources if s.has_any_failure]

    def _count_status(self, status: str) -> int:
        return sum(
            1 for s in self.sources for g in s.gates if g.get("status") == status
        )

    def gate_rows(self) -> List[GateRow]:
        rows: List[GateRow] = []
        for s in self.sources:
            for g in s.gates:
                rows.append(
                    GateRow(
                        source=s.source,
                        gate=str(g.get("name", "?")),
                        status=str(g.get("status", "?")),
                        blocking=bool(g.get("blocking", False)),
                        layer=_layer_for_gate(g.get("name")),
                        step_count=int(g.get("step_count", 0) or 0),
                        error=str(g.get("error") or ""),
                        file_name=g.get("file_name"),
                        row_count=g.get("row_count"),
                        error_count=g.get("error_count"),
                        report_path=g.get("report_path"),
                    )
                )
        return rows

    def to_summary_dict(self) -> Dict[str, Any]:
        """Stable shape consumed by automation and the unit tests."""
        return {
            "schema_version": 1,
            "run_id": self.run_id,
            "env": self.env,
            "generated_at": self.generated_at,
            "totals": {
                "sources": self.total_sources,
                "gates": self.total_gates,
                "gates_passed": self.gates_passed,
                "gates_failed": self.gates_failed,
                "gates_skipped": self.gates_skipped,
                "gates_infra_error": self.gates_infra_error,
            },
            "sources_with_blocking_failure": self.sources_with_blocking_failure,
            "sources_with_any_failure": self.sources_with_any_failure,
            "sources": [
                {
                    "source": s.source,
                    "rollup_html": (
                        os.path.relpath(s.rollup_html, self.reports_dir)
                        if s.rollup_html is not None
                        else None
                    ),
                    "gates": s.gates,
                    "multi_record_reports": [
                        {
                            "file_type": mr.file_type,
                            "index_html": os.path.relpath(
                                mr.index_html, self.reports_dir
                            ),
                        }
                        for mr in s.multi_record_reports
                    ],
                }
                for s in self.sources
            ],
        }


# --------------------------------------------------------------------------- #
# Scanner
# --------------------------------------------------------------------------- #


def _layer_for_gate(gate_name: Optional[str]) -> Optional[str]:
    """Derive the layer label (L1/L2/L3) from a gate name; None for others."""
    if not gate_name:
        return None
    if gate_name == "L1_structural":
        return "L1"
    if gate_name == "L2b_sql_truth":
        return "L2b"
    if gate_name == "L3_baseline_diff":
        return "L3"
    return None


def discover_multi_record_reports(source_dir: Path) -> List[MultiRecordLink]:
    """Discover rendered multi-record reports under ``source_dir``.

    Scans ``<source_dir>/multi_record/<file_type>/index.html`` for every
    ``<file_type>`` subdirectory that contains an ``index.html``.  Returns
    results sorted by ``file_type`` for deterministic output.

    Args:
        source_dir: The per-source report directory (e.g.
            ``<reports_root>/<run_id>/SRC_A``).

    Returns:
        A list of :class:`MultiRecordLink` objects, one per discovered
        file type.  Empty when the source has no multi-record reports.
    """
    mr_root = source_dir / "multi_record"
    if not mr_root.is_dir():
        return []
    links: List[MultiRecordLink] = []
    for child in sorted(mr_root.iterdir()):
        if not child.is_dir():
            continue
        index = child / "index.html"
        if index.is_file():
            links.append(MultiRecordLink(file_type=child.name, index_html=index))
    return links


def load_source_summary(summary_path: Path) -> SourceSummary:
    """Parse one per-source summary.json into a :class:`SourceSummary`.

    Raises:
        RollupError: If the file is missing or malformed.
    """
    if not summary_path.is_file():
        raise RollupError(f"summary.json not found: {summary_path}")
    try:
        data = json.loads(summary_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RollupError(f"failed to parse {summary_path}: {exc}") from exc
    if not isinstance(data, dict):
        raise RollupError(f"{summary_path} top level must be a JSON object")

    missing = [k for k in _SUMMARY_KEYS if k not in data]
    if missing:
        raise RollupError(f"{summary_path} missing required keys: {missing}")

    gates = data.get("gates")
    if not isinstance(gates, list):
        raise RollupError(f"{summary_path} 'gates' must be a list")

    rollup_html = summary_path.parent / "index.html"
    return SourceSummary(
        source=str(data["source"]),
        run_id=str(data["run_id"]),
        env=str(data["env"]),
        rollup_html=rollup_html if rollup_html.is_file() else None,
        gates=[g for g in gates if isinstance(g, dict)],
        extras={k: v for k, v in data.items() if k not in _SUMMARY_KEYS},
        multi_record_reports=discover_multi_record_reports(summary_path.parent),
    )


def scan_reports_dir(
    reports_dir: Path,
    *,
    run_id: Optional[str] = None,
    env: Optional[str] = None,
) -> RollupReport:
    """Scan ``reports_dir`` and return an aggregated :class:`RollupReport`.

    Each immediate subdirectory of ``reports_dir`` is expected to contain
    a ``summary.json``. Subdirectories without one are reported under
    ``extras["sources_without_summary"]`` but do not fail the scan
    (parallel runs may still be in flight when the global roll-up is
    triggered manually).

    Args:
        reports_dir: The per-run reports root (e.g.
            ``/data/sit/_reports/20260514_120000``).
        run_id: Optional run id to stamp on the roll-up. When omitted, the
            scanner uses the run_id declared inside the first summary
            successfully parsed, or the directory's basename as a fallback.
        env: Optional env name; same fallback rules as ``run_id``.

    Raises:
        RollupError: If the reports directory does not exist.
    """
    reports_dir = Path(reports_dir)
    if not reports_dir.is_dir():
        raise RollupError(f"reports dir not found: {reports_dir}")

    summaries: List[SourceSummary] = []
    missing: List[str] = []
    for child in sorted(reports_dir.iterdir()):
        if not child.is_dir():
            continue
        summary_path = child / "summary.json"
        if not summary_path.is_file():
            missing.append(child.name)
            continue
        summaries.append(load_source_summary(summary_path))

    # Resolve run_id / env with fallbacks.
    resolved_run_id = (
        run_id or (summaries[0].run_id if summaries else None) or reports_dir.name
    )
    resolved_env = env or (summaries[0].env if summaries else None) or "unknown"

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    report = RollupReport(
        run_id=resolved_run_id,
        env=resolved_env,
        reports_dir=reports_dir,
        generated_at=generated_at,
        sources=summaries,
    )
    # Stash the orphan list on the report by mutating the dict shape only
    # in the JSON output path (kept off the dataclass to keep the public
    # contract narrow). Callers that need it use the JSON.
    report._missing_summaries = missing  # type: ignore[attr-defined]
    return report


# --------------------------------------------------------------------------- #
# Emit
# --------------------------------------------------------------------------- #


def render_summary_json(report: RollupReport) -> str:
    out = report.to_summary_dict()
    missing = getattr(report, "_missing_summaries", None)
    if missing:
        out["sources_without_summary"] = missing
    return json.dumps(out, indent=2, default=str) + "\n"


def render_index_html(report: RollupReport) -> str:
    """Render the global roll-up HTML. Self-contained — no external assets."""
    rows = report.gate_rows()
    # Per-source quick stats for the top table.
    by_source: Dict[str, Dict[str, int]] = {}
    for r in rows:
        bs = by_source.setdefault(
            r.source,
            {
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "infra_error": 0,
                "blocking_failed": 0,
            },
        )
        bs[r.status] = bs.get(r.status, 0) + 1
        if r.status in {"failed", "infra_error"} and r.blocking:
            bs["blocking_failed"] += 1

    source_rows_html: List[str] = []
    for s in report.sources:
        stats = by_source.get(s.source, {})
        passed = stats.get("passed", 0)
        failed = stats.get("failed", 0)
        skipped = stats.get("skipped", 0)
        infra = stats.get("infra_error", 0)
        blocked = stats.get("blocking_failed", 0)
        cls = "bad" if (failed + infra) > 0 else "warn" if skipped > 0 else "ok"
        rollup_cell = (
            f'<a href="{html.escape(os.path.relpath(s.rollup_html, report.reports_dir))}">open</a>'
            if s.rollup_html is not None
            else "&mdash;"
        )
        if s.multi_record_reports:
            mr_cell = ", ".join(
                f'<a href="{html.escape(os.path.relpath(mr.index_html, report.reports_dir))}">'
                f"{html.escape(mr.file_type)}</a>"
                for mr in s.multi_record_reports
            )
        else:
            mr_cell = "&mdash;"
        source_rows_html.append(
            "<tr class='{cls}'>"
            "<td>{src}</td>"
            "<td>{passed}</td><td>{failed}</td><td>{skipped}</td>"
            "<td>{infra}</td><td>{blocked}</td>"
            "<td>{rollup}</td>"
            "<td>{mr}</td>"
            "</tr>".format(
                cls=cls,
                src=html.escape(s.source),
                passed=passed,
                failed=failed,
                skipped=skipped,
                infra=infra,
                blocked=blocked,
                rollup=rollup_cell,
                mr=mr_cell,
            )
        )

    gate_rows_html: List[str] = []
    for r in rows:
        cls = {
            "passed": "ok",
            "failed": "bad",
            "skipped": "warn",
            "infra_error": "bad",
        }.get(r.status, "warn")
        block_tag = "blocking" if r.blocking else "non-blocking"
        report_cell = (
            f'<a href="{html.escape(r.report_path)}">report</a>'
            if r.report_path
            else "&mdash;"
        )
        layer = r.layer or ""
        gate_rows_html.append(
            "<tr class='{cls}'>"
            "<td>{src}</td><td>{gate}</td><td>{layer}</td>"
            "<td>{status}</td><td>{block}</td>"
            "<td>{steps}</td><td>{rows}</td><td>{errs}</td>"
            "<td>{file}</td>"
            "<td>{report}</td>"
            "<td>{error}</td>"
            "</tr>".format(
                cls=cls,
                src=html.escape(r.source),
                gate=html.escape(r.gate),
                layer=html.escape(layer),
                status=html.escape(r.status),
                block=block_tag,
                steps=r.step_count,
                rows="" if r.row_count is None else r.row_count,
                errs="" if r.error_count is None else r.error_count,
                file=html.escape(r.file_name or ""),
                report=report_cell,
                error=html.escape(r.error[:200]),
            )
        )

    missing = getattr(report, "_missing_summaries", []) or []
    missing_html = ""
    if missing:
        items = "".join(f"<li>{html.escape(m)}</li>" for m in missing)
        missing_html = (
            f"<h2>Sources without summary.json ({len(missing)})</h2>"
            f"<ul class='warn'>{items}</ul>"
        )

    overall_cls = (
        "bad"
        if report.sources_with_blocking_failure
        else ("warn" if report.sources_with_any_failure else "ok")
    )
    overall_label = (
        "BLOCKING FAILURE"
        if report.sources_with_blocking_failure
        else (
            "NON-BLOCKING FAILURES" if report.sources_with_any_failure else "ALL PASSED"
        )
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Valdo E2E roll-up {html.escape(report.env)}/{html.escape(report.run_id)}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 1.5rem; color: #222; }}
 h1 {{ font-size: 1.3rem; margin: 0 0 .2rem; }}
 h2 {{ font-size: 1.05rem; margin-top: 1.3rem; }}
 .meta {{ color: #666; font-size: .9rem; margin-bottom: 1rem; }}
 .banner {{ display: inline-block; padding: .25rem .6rem; border-radius: 4px;
             font-weight: 600; }}
 .banner.ok {{ background: #d2f5d2; color: #074d07; }}
 .banner.warn {{ background: #fff3c4; color: #6a4d00; }}
 .banner.bad {{ background: #f8c8c8; color: #6e0202; }}
 table {{ border-collapse: collapse; margin-top: .4rem; }}
 th, td {{ border: 1px solid #d0d0d0; padding: .25rem .55rem; font-size: .9rem; }}
 th {{ background: #f3f3f3; text-align: left; }}
 tr.ok {{ background: #e6ffe6; }}
 tr.bad {{ background: #ffe6e6; }}
 tr.warn {{ background: #fff7d6; }}
 ul.warn {{ background: #fff7d6; padding: .3rem 1rem; border-radius: 4px; }}
 .totals td {{ text-align: right; }}
 .totals td:first-child {{ text-align: left; }}
 a {{ color: #0a52b3; }}
</style>
</head>
<body>
<h1>Valdo E2E roll-up &mdash; {html.escape(report.env)} / run {html.escape(report.run_id)}</h1>
<div class="meta">
  Generated {html.escape(report.generated_at)} &middot;
  scanned {html.escape(str(report.reports_dir))} &middot;
  <span class="banner {overall_cls}">{html.escape(overall_label)}</span>
</div>

<h2>Totals</h2>
<table class="totals">
<thead><tr><th>Metric</th><th>Count</th></tr></thead>
<tbody>
<tr><td>Sources scanned</td><td>{report.total_sources}</td></tr>
<tr><td>Gates total</td><td>{report.total_gates}</td></tr>
<tr><td>Gates passed</td><td>{report.gates_passed}</td></tr>
<tr class="bad"><td>Gates failed</td><td>{report.gates_failed}</td></tr>
<tr class="warn"><td>Gates skipped</td><td>{report.gates_skipped}</td></tr>
<tr class="bad"><td>Gates infra-error</td><td>{report.gates_infra_error}</td></tr>
<tr class="bad"><td>Sources with blocking failure</td><td>{len(report.sources_with_blocking_failure)}</td></tr>
</tbody>
</table>

<h2>Sources</h2>
<table>
<thead><tr><th>Source</th><th>Passed</th><th>Failed</th><th>Skipped</th>
<th>Infra</th><th>Blocking failed</th><th>Per-source roll-up</th>
<th>Multi-record reports</th></tr></thead>
<tbody>
{''.join(source_rows_html)}
</tbody>
</table>

<h2>Gates ({report.total_gates})</h2>
<table>
<thead><tr><th>Source</th><th>Gate</th><th>Layer</th>
<th>Status</th><th>Blocking</th><th>Steps</th>
<th>Rows</th><th>Errors</th><th>File</th><th>Report</th><th>Error message</th></tr></thead>
<tbody>
{''.join(gate_rows_html)}
</tbody>
</table>

{missing_html}
</body>
</html>
"""


def write_rollup(
    report: RollupReport,
    out_dir: Optional[Path] = None,
) -> Tuple[Path, Path]:
    """Write ``index.html`` + ``summary.json`` to ``out_dir``.

    Writes are atomic: each file is written to a temp sibling and then
    renamed over the target. This guarantees a concurrent reader never
    sees a half-written file (operators tail these in real time).

    Args:
        report: The aggregated roll-up.
        out_dir: Optional override. Default: ``report.reports_dir``.

    Returns:
        ``(index_html_path, summary_json_path)``.
    """
    out_dir = Path(out_dir) if out_dir is not None else report.reports_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    summary_path = out_dir / "summary.json"
    index_path = out_dir / "index.html"
    _atomic_write(summary_path, render_summary_json(report))
    _atomic_write(index_path, render_index_html(report))
    return index_path, summary_path


def _atomic_write(target: Path, content: str) -> None:
    """Write ``content`` to ``target`` via a sibling tmp file + rename."""
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, target)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="build_rollup_index",
        description=(
            "Build a global per-run roll-up index over the per-source "
            "summary.json files produced by run_e2e_source.sh."
        ),
    )
    p.add_argument(
        "--reports-dir",
        required=True,
        type=Path,
        help="Per-run reports root. Usually <env_report_root>/<run_id>.",
    )
    p.add_argument(
        "--run-id",
        default=None,
        help="Optional run id stamped on the roll-up. Defaults to the "
        "first summary's run_id, then the reports dir basename.",
    )
    p.add_argument(
        "--env",
        default=None,
        help="Optional env stamped on the roll-up. Defaults to the first "
        "summary's env.",
    )
    p.add_argument(
        "--out-dir",
        default=None,
        type=Path,
        help="Where to write index.html + summary.json. Defaults to " "--reports-dir.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit 2 for ANY failed gate, not just blocking ones.",
    )
    p.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the human summary printed to stdout.",
    )
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        report = scan_reports_dir(args.reports_dir, run_id=args.run_id, env=args.env)
    except RollupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INFRA_ERROR

    try:
        index_path, summary_path = write_rollup(report, out_dir=args.out_dir)
    except OSError as exc:
        print(f"error: failed to write roll-up: {exc}", file=sys.stderr)
        return EXIT_INFRA_ERROR

    if not args.quiet:
        sys.stdout.write(
            f"wrote {index_path}\n"
            f"wrote {summary_path}\n"
            f"sources={report.total_sources} "
            f"gates={report.total_gates} "
            f"passed={report.gates_passed} "
            f"failed={report.gates_failed} "
            f"skipped={report.gates_skipped} "
            f"infra_error={report.gates_infra_error}\n"
        )

    blocking_failures = bool(report.sources_with_blocking_failure)
    any_failures = bool(report.sources_with_any_failure)
    if args.strict and any_failures:
        return EXIT_BLOCKING_FAILURE
    if blocking_failures:
        return EXIT_BLOCKING_FAILURE
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

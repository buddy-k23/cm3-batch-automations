"""Run-suite MCP tool implementation (S22-5, #442).

Wires the ``run_suite`` MCP tool onto the existing Valdo suite runner — the same
:func:`src.commands.run_tests_command.run_suite_from_path` code path the
``valdo run-tests`` CLI command
(:func:`src.commands.run_tests_command.run_tests_command`) and the webhook /
file-watcher triggers drive. Like the S22-1 / S22-2 / S22-3 / S22-4 tools
(``parse_file`` / ``run_etl_pipeline`` / ``export_failed_rows`` / ``submit_task``),
this module is a THIN adapter — no suite logic lives here. Suite YAML loading,
per-test execution (structural / rules / oracle_vs_file / api_check), threshold
evaluation, report generation, run-history persistence, and baseline updates all
stay in the runner; the tool registration itself stays in :mod:`src.mcp.server`.

This tool COMPLETES Valdo's MCP parity surface: every CLI verb now has a matching
MCP tool. The tool lets an agent run a whole test suite (defined in a YAML file
under ``config/suites/``) over MCP and read back the per-test pass/fail breakdown
plus the overall verdict, without shelling out to the CLI.

A suite whose tests FAIL (or are mixed) is a RESULT, not a tool error: the caller
inspects ``overall_status`` (``PASS`` | ``PARTIAL`` | ``FAIL``) to decide what to
do next. ToolError is reserved for caller-fixable problems: a missing/blank
``suite`` argument, a suite file that does not exist, or malformed suite YAML that
fails schema validation. The response carries no secrets — only the suite name,
the resolved environment, the aggregate counts, and the per-test summary records
the runner already emits (name, type, status, counts, duration, report path).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "run_suite_payload",
    "RUN_SUITE_DESCRIPTION",
]

logger = logging.getLogger(__name__)

# Per-test result keys we surface in the summary. The runner emits a richer dict
# per test; we project a stable, secret-free subset so the tool contract does not
# drift if the runner adds internal bookkeeping fields.
_TEST_SUMMARY_KEYS = (
    "name",
    "type",
    "status",
    "total_rows",
    "error_count",
    "warning_count",
    "duration_seconds",
    "report_path",
    "detail",
)


def _json_default(value: Any) -> Any:
    """Coerce a non-JSON-native scalar to a JSON-serialisable Python type.

    Per-test results may embed numpy/pandas scalars (e.g. ``numpy.int64``) which
    FastMCP cannot serialise over the MCP transport. Any object exposing
    ``.item()`` (the numpy/pandas scalar protocol) is unwrapped to its native
    Python value; anything else falls back to ``str`` so an unexpected type
    degrades to a readable string rather than crashing the transport.

    Args:
        value: The object ``json.dumps`` could not serialise natively.

    Returns:
        A JSON-serialisable representation of *value*.
    """
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return item()
        except (TypeError, ValueError):  # pragma: no cover - defensive
            pass
    return str(value)


def _to_json_native(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Round-trip a payload through JSON to drop non-native scalar types.

    Keeps this module a thin adapter: the summary SHAPE is untouched — we only
    normalise embedded numpy/pandas scalars (surfaced via :func:`_json_default`)
    to JSON-native types so FastMCP can serialise the response.

    Args:
        payload: The summary dict assembled from the runner's per-test results.

    Returns:
        An equivalent dict containing only JSON-native types.
    """
    return json.loads(json.dumps(payload, default=_json_default))


def run_suite_payload(
    suite: str,
    env: Optional[str] = None,
    params: Optional[Dict[str, str]] = None,
    output_dir: str = "reports",
) -> Dict[str, Any]:
    """Run a Valdo test suite over MCP and return its per-test + overall summary.

    Thin adapter over the Valdo suite runner (the same code path the
    ``valdo run-tests`` CLI command drives). The suite definition is loaded from
    *suite*, every configured test is executed in declared order, and the
    per-test results are aggregated into a summary. No suite logic lives here;
    YAML loading, per-test execution, threshold evaluation, report generation,
    run-history persistence, and baseline updates all stay in
    :func:`src.commands.run_tests_command.run_suite_from_path`.

    A suite whose tests fail (or are mixed) is a RESULT, not a tool error: the
    caller inspects ``overall_status`` to decide what to do. ToolError is raised
    only for caller-fixable problems (see below).

    Args:
        suite: Path to the suite YAML file (e.g. ``config/suites/daily.yaml``).
            Required and non-blank.
        env: Optional environment name (e.g. ``"dev"``, ``"staging"``) recorded
            in reports and run history. Defaults to the suite's own
            ``environment`` field when omitted.
        params: Optional dict of substitution parameters (e.g.
            ``{"run_date": "20260615"}``) expanded into ``${var}`` placeholders
            in each test's ``file`` path.
        output_dir: Directory where HTML reports and run history are written.
            Defaults to ``"reports"``.

    Returns:
        The aggregate suite summary dict with keys:

        - ``suite``: str — the suite name from the YAML.
        - ``environment``: str — the effective environment used for the run.
        - ``overall_status``: str — ``"PASS"`` | ``"PARTIAL"`` | ``"FAIL"``.
        - ``total_count``: int — number of tests in the suite.
        - ``pass_count``: int — tests with status ``PASS``.
        - ``fail_count``: int — tests with status ``FAIL`` or ``ERROR``.
        - ``skip_count``: int — tests with status ``SKIPPED``.
        - ``tests``: list[dict] — per-test records, each with ``name``, ``type``,
          ``status``, ``total_rows``, ``error_count``, ``warning_count``,
          ``duration_seconds``, ``report_path``, and ``detail``.

    Raises:
        ToolError: For caller-fixable problems: *suite* missing/blank, the suite
            file not found, or malformed suite YAML that fails schema validation.
    """
    if not suite or not isinstance(suite, str) or not suite.strip():
        raise ToolError("suite is required and must be a non-empty string")

    if not Path(suite).exists():
        raise ToolError(f"Suite file not found: {suite}")

    # Lazy imports keep the heavy runner deps out of module import time and mirror
    # the CLI command's import site.
    import yaml

    from src.commands.run_tests_command import (
        _compute_overall_status,
        run_suite_from_path,
    )

    # Resolve the suite name + effective environment up-front so the summary is
    # well-formed even if the runner's side-effects (archive/baseline) warn.
    try:
        raw = yaml.safe_load(Path(suite).read_text(encoding="utf-8"))
        suite_name = (raw or {}).get("name", suite)
        suite_env = (raw or {}).get("environment", "dev")
    except Exception as exc:  # noqa: BLE001 — malformed YAML is caller-fixable
        raise ToolError(f"Could not read suite YAML: {exc}") from exc

    effective_env = env or suite_env

    try:
        results: List[Dict[str, Any]] = run_suite_from_path(
            suite_path=suite,
            params=params or {},
            env=effective_env,
            output_dir=output_dir,
        )
    except (KeyError, TypeError, ValueError) as exc:
        # A pydantic validation error on TestSuiteConfig, an unresolved
        # ${variable} placeholder, or other malformed-config failures — all
        # caller-fixable. Surface as a tool error rather than crashing the
        # MCP transport.
        raise ToolError(f"Suite is invalid: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        logger.exception("run_suite tool failed for suite=%r", suite)
        raise ToolError(f"Suite run failed: {exc}") from exc

    overall = _compute_overall_status(results) if results else "PASS"
    tests = [
        {k: r.get(k) for k in _TEST_SUMMARY_KEYS}
        for r in results
    ]

    summary: Dict[str, Any] = {
        "suite": suite_name,
        "environment": effective_env,
        "overall_status": overall,
        "total_count": len(results),
        "pass_count": sum(1 for r in results if r.get("status") == "PASS"),
        "fail_count": sum(
            1 for r in results if r.get("status") in ("FAIL", "ERROR")
        ),
        "skip_count": sum(1 for r in results if r.get("status") == "SKIPPED"),
        "tests": tests,
    }

    logger.info(
        "run_suite tool ran suite=%r env=%s -> %s (%d/%d passed)",
        suite_name,
        effective_env,
        overall,
        summary["pass_count"],
        summary["total_count"],
    )

    # Per-test results can embed numpy/pandas scalars from the underlying
    # validate/compare services; normalise to JSON-native types so FastMCP can
    # serialise the response. Shape is preserved — this is not suite logic.
    return _to_json_native(summary)


RUN_SUITE_DESCRIPTION = (
    "Run a Valdo test suite defined in a YAML file (e.g. under config/suites/) "
    "and return its per-test pass/fail breakdown plus the overall verdict — so "
    "an agent can gate a release or a daily data drop without shelling out to "
    "the CLI. Wraps the existing Valdo suite runner — the same code path the "
    "'valdo run-tests' CLI command drives: load the suite YAML, run each "
    "configured test (structural | rules | oracle_vs_file | api_check) in order, "
    "evaluate per-test thresholds, and aggregate the results. Accepts the suite "
    "YAML path (required), an optional 'env' name recorded in reports (defaults "
    "to the suite's own environment), an optional 'params' dict of ${variable} "
    "substitutions for each test's file path (e.g. {\"run_date\": \"20260615\"}), "
    "and an optional 'output_dir' for HTML reports (defaults to 'reports'). "
    "Returns 'suite', 'environment', 'overall_status' ('PASS' | 'PARTIAL' | "
    "'FAIL'), the aggregate 'total_count'/'pass_count'/'fail_count'/'skip_count', "
    "and a 'tests' list of per-test records (each with 'name', 'type', 'status', "
    "'total_rows', 'error_count', 'warning_count', 'duration_seconds', "
    "'report_path', and 'detail'). A suite whose tests FAIL (or are mixed) is a "
    "RESULT, not a tool error — inspect 'overall_status' to decide what to do "
    "next. Note: an oracle_vs_file test is reported SKIPPED when Oracle is not "
    "configured. The response carries no secrets. Raises a tool error only for "
    "caller-fixable problems: a missing/blank suite argument, a suite file that "
    "does not exist, or malformed suite YAML that fails schema validation."
)

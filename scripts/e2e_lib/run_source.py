"""End-to-end orchestrator for a single source, invoked by run_e2e_source.sh.

This module is the M4 deliverable's Python core. The bash wrapper
(``scripts/run_e2e_source.sh``) is intentionally thin — it sets PATH /
venv concerns appropriate to the RHEL target and then delegates here.
Keeping the orchestration in Python makes it unit-testable and
cross-platform (Windows dev, RHEL prod).

Flow (matches §3 ``run_e2e_source.sh`` in the prompt):

  1. Resolve paths for ``(env, source, run_id)``.
  2. Create the isolated per-run work dir under ``work_root``.
  3. (Optional, configurable per source) Shell out to ``java_scripts.load``.
  4. Run ``valdo run-etl-pipeline`` for the ``file_to_staging`` gate
     (writes split pipeline YAML to work_root/pipelines/).
  5. (Optional, configurable) Shell out to ``java_scripts.generate``.
  6. Run ``valdo run-etl-pipeline`` for ``L1_structural`` and
     ``L3_baseline_diff`` gates.
  7. On any failed gate, append rows to ``AUDIT.VALDO_RUN_FAILURES`` via
     :class:`scripts.e2e_lib.failure_sink.FailureSink` — one row per
     failed step within the failed gate.
  8. Emit a per-source roll-up at ``report_root/index.html`` (minimal,
     M6 expands this with the global roll-up).
  9. Exit 0 if every BLOCKING gate passed; 2 if a blocking gate failed;
     3 on infrastructure error (DB unreachable, config invalid, etc.).

Idempotency
-----------
Re-running the same ``(env, source, run_id)`` will OVERWRITE work_root
artifacts but will APPEND to the failure-sink table (duplicates are
acceptable by design — see :class:`FailureSink` docstring). The JSONL
log file is also appended to. The roll-up HTML is rewritten.

Output-truth gates
------------------
Output faithfulness is covered by two gates: the orchestrator-driven
``L2b_sql_truth`` gate (reconciles each output file against the same Oracle
staging data the Java reads, via the generic ``db_truth_comparator`` engine and
a per-source reconciliation YAML) and the Valdo-runnable ``L3_baseline_diff``
gate (diffs against the pinned golden baseline). A separate Valdo-side
``L2_regeneration`` gate was retired (ADR 0012): it was a permanently-skipped
``TODO(valdo-gap)`` and would have re-tested the same SQL-truth axis L2b already
covers. A future, *mapping-driven* ``valdo regenerate`` capability is tracked as
its own backlog story (Option A of ADR 0012).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Type

# Make the repo importable when invoked as a script.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.failure_sink import (  # noqa: E402
    FailureRecord,
    FailureSink,
    FailureSinkError,
)
from scripts.e2e_lib.jsonl_logger import JsonlLogger  # noqa: E402
from scripts.e2e_lib.path_resolver import (  # noqa: E402
    PathResolver,
    PathResolverError,
)
from scripts.e2e_lib.secret_resolver import SecretResolver  # noqa: E402
from scripts.e2e_lib.split_pipeline import (  # noqa: E402
    SplitPipelineError,
    split_pipeline_yaml,
)

# Exit codes per the prompt's <constraints>.
EXIT_OK = 0
EXIT_BLOCKING_FAILURE = 2
EXIT_INFRA_ERROR = 3

# Gate sets used in the two valdo invocations.
_INPUT_PHASE_GATES = ("file_to_staging",)
# L2_regeneration was retired (ADR 0012); L2b SQL-truth + L3 baseline cover the
# output-truth and regression axes.
_OUTPUT_PHASE_GATES = ("L1_structural", "L3_baseline_diff")

# The L2b SQL-Truth gate is NOT a Valdo-runnable gate (it does not go through
# valdo run-etl-pipeline); it is driven directly by this orchestrator via the
# generic db_truth_comparator engine. It runs after the output-phase Valdo
# gates and only for sources that ship a reconciliation YAML.
GATE_L2B_SQL_TRUTH = "L2b_sql_truth"

# The multi-record validation HTML report is also orchestrator-driven (it is
# not a valdo run-etl-pipeline gate). For each multi-record output file it runs
# the standard MultiRecordValidator (mappings + per-type rules) and renders the
# per-record-type HTML subpages plus a cumulative umbrella index via
# scripts/render_multi_record_html.py. It is a REPORT, not a pass/fail gate —
# the underlying rule failures are already gated by L1_structural — so it is
# non-blocking by default. It runs after the L2b gate and only for sources that
# declare it and have multi_record output files.
GATE_MULTI_RECORD_REPORT = "multi_record_report"

# Set this env var to "1" in tests / dev to force-skip Java shell-outs even
# when source config says invoke_java=true. Useful when running on Windows.
ENV_DISABLE_JAVA = "VALDO_E2E_DISABLE_JAVA"
# Set this env var to "1" to skip the failure sink (Oracle unreachable in
# CI / dev). Failures are still logged to JSONL.
ENV_DISABLE_FAILURE_SINK = "VALDO_E2E_DISABLE_FAILURE_SINK"
# Set this env var to "1" to skip the L2b SQL-Truth gate (Oracle unreachable
# in dev / CI). The gate records a skip instead of running the comparator.
ENV_DISABLE_L2B = "VALDO_E2E_DISABLE_L2B"
# Set this env var to "1" to skip rendering the multi-record validation HTML
# report. The step records a skip instead of running the validator/renderer.
ENV_DISABLE_MR_REPORT = "VALDO_E2E_DISABLE_MR_REPORT"


# --------------------------------------------------------------------------- #
# Result types
# --------------------------------------------------------------------------- #


@dataclass
class GateResult:
    """One gate's outcome as observed by this orchestrator."""

    name: str
    status: str  # "passed" | "failed" | "skipped" | "infra_error"
    blocking: bool
    steps: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    report_path: Optional[str] = None


@dataclass
class RunResult:
    """Top-level outcome returned by :func:`run_source`."""

    run_id: str
    env: str
    source: str
    exit_code: int
    work_root: str
    report_root: str
    log_path: str
    gates: List[GateResult] = field(default_factory=list)
    rollup_path: Optional[str] = None


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #


@dataclass
class _Subprocess:
    """Thin shim so tests can inject a fake subprocess runner.

    The real implementation calls :func:`subprocess.run` with sensible
    defaults (text mode, captured output, no shell).
    """

    def run(
        self,
        cmd: Sequence[str],
        *,
        cwd: Optional[Path] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> "subprocess.CompletedProcess[str]":
        return subprocess.run(
            list(cmd),
            cwd=str(cwd) if cwd is not None else None,
            env=env,
            check=False,
            text=True,
            capture_output=True,
        )


def run_source(
    *,
    env: str,
    source: str,
    run_id: str,
    paths_yaml: Path,
    sources_dir: Path,
    pipeline_yaml: Path,
    valdo_executable: str = "valdo",
    subprocess_runner: Optional[_Subprocess] = None,
    failure_sink: Optional[FailureSink] = None,
    secret_resolver: Optional[SecretResolver] = None,
) -> RunResult:
    """Execute one source end-to-end. See module docstring for the flow.

    Args:
        env: ``"sit"`` or ``"ait"``.
        source: Source identifier (matches a ``config/e2e/sources/<X>.yml``).
        run_id: Unique identifier for this run (typically a timestamp).
        paths_yaml: Path to ``config/e2e/paths.yml``.
        sources_dir: Directory of per-source YAMLs.
        pipeline_yaml: Path to the canonical pipeline YAML from M3.
        valdo_executable: Command to invoke for Valdo (``"valdo"`` in prod,
            overridable in tests).
        subprocess_runner: Optional injected runner (tests).
        failure_sink: Optional pre-built sink (tests, or wired by the caller
            so the same sink is reused across sources).
        secret_resolver: Optional pre-built resolver (tests).

    Returns:
        A :class:`RunResult` summarizing every gate. The caller (the bash
        wrapper) uses ``RunResult.exit_code`` as the process exit code.
    """
    runner = subprocess_runner or _Subprocess()
    secret_resolver = secret_resolver or SecretResolver.default()

    # ----- 1. Resolve paths --------------------------------------------- #
    try:
        resolver = PathResolver.from_files(paths_yaml, sources_dir)
        work_root = Path(
            resolver.resolve("work_root", env=env, source=source, run_id=run_id)
        )
        report_root = Path(
            resolver.resolve("report_root", env=env, source=source, run_id=run_id)
        )
        log_root = Path(resolver.resolve("log_root", env=env))
        src_cfg = resolver.source_config(source)
    except PathResolverError as exc:
        # No log path yet; emit to stderr and bail.
        sys.stderr.write(f"error: failed to resolve paths: {exc}\n")
        return RunResult(
            run_id=run_id,
            env=env,
            source=source,
            exit_code=EXIT_INFRA_ERROR,
            work_root="",
            report_root="",
            log_path="",
        )

    # ----- 2. Create work dir and logger -------------------------------- #
    work_root.mkdir(parents=True, exist_ok=True)
    report_root.mkdir(parents=True, exist_ok=True)
    (work_root / "pipelines").mkdir(parents=True, exist_ok=True)
    (work_root / "reports").mkdir(parents=True, exist_ok=True)

    logger = JsonlLogger.for_run(log_root, run_id=run_id, env=env, source=source)
    logger.info(
        "run_started",
        work_root=str(work_root),
        report_root=str(report_root),
        pipeline_yaml=str(pipeline_yaml),
    )

    if not pipeline_yaml.is_file():
        logger.error("pipeline_yaml_missing", path=str(pipeline_yaml))
        return RunResult(
            run_id=run_id,
            env=env,
            source=source,
            exit_code=EXIT_INFRA_ERROR,
            work_root=str(work_root),
            report_root=str(report_root),
            log_path=str(logger.log_path),
        )

    # Build the failure sink lazily so we can run end-to-end on a dev box
    # without an Oracle reachable (set VALDO_E2E_DISABLE_FAILURE_SINK=1).
    sink_ctx = _FailureSinkContext(
        failure_sink=failure_sink,
        env=env,
        resolver=secret_resolver,
        logger=logger,
    )

    gates_cfg = src_cfg.get("gates", {})
    gates: List[GateResult] = []
    overall_blocking_failure = False
    java_disabled = os.environ.get(ENV_DISABLE_JAVA, "0") == "1"

    # ----- 3. Java load step (optional shell-out) ----------------------- #
    load_policy = gates_cfg.get("load_step", {})
    if load_policy.get("invoke_java"):
        result, halted = _run_java_step(
            step_name="load_step",
            script=src_cfg.get("java_scripts", {}).get("load"),
            work_root=work_root,
            logger=logger,
            disabled=java_disabled,
            blocking=bool(load_policy.get("blocking", True)),
            runner=runner,
            sink_ctx=sink_ctx,
            run_id=run_id,
            env=env,
            source=source,
            pipeline_name=_pipeline_name(pipeline_yaml),
        )
        gates.append(result)
        if halted:
            overall_blocking_failure = True
            sink_ctx.close()
            return _finalize(
                gates,
                run_id,
                env,
                source,
                work_root,
                report_root,
                logger,
                overall_blocking_failure,
            )

    # ----- 4. file_to_staging gate -------------------------------------- #
    f2s_result, halted = _run_valdo_phase(
        phase_label="input",
        gate_names=_INPUT_PHASE_GATES,
        pipeline_yaml=pipeline_yaml,
        work_root=work_root,
        report_root=report_root,
        run_id=run_id,
        env=env,
        source=source,
        gates_cfg=gates_cfg,
        valdo_executable=valdo_executable,
        runner=runner,
        logger=logger,
        sink_ctx=sink_ctx,
    )
    gates.extend(f2s_result)
    if halted:
        overall_blocking_failure = True
        sink_ctx.close()
        return _finalize(
            gates,
            run_id,
            env,
            source,
            work_root,
            report_root,
            logger,
            overall_blocking_failure,
        )

    # ----- 5. Java generate step (optional shell-out) ------------------- #
    gen_policy = gates_cfg.get("generate_step", {})
    if gen_policy.get("invoke_java"):
        result, halted = _run_java_step(
            step_name="generate_step",
            script=src_cfg.get("java_scripts", {}).get("generate"),
            work_root=work_root,
            logger=logger,
            disabled=java_disabled,
            blocking=bool(gen_policy.get("blocking", True)),
            runner=runner,
            sink_ctx=sink_ctx,
            run_id=run_id,
            env=env,
            source=source,
            pipeline_name=_pipeline_name(pipeline_yaml),
        )
        gates.append(result)
        if halted:
            overall_blocking_failure = True
            sink_ctx.close()
            return _finalize(
                gates,
                run_id,
                env,
                source,
                work_root,
                report_root,
                logger,
                overall_blocking_failure,
            )

    # ----- 6. Output phase gates (L1, L3) ------------------------------- #
    # L2_regeneration was retired (ADR 0012); the output-truth axis is now
    # covered by the orchestrator-driven L2b SQL-truth gate below and the
    # regression axis by L3_baseline_diff here.
    output_gates: List[str] = list(_OUTPUT_PHASE_GATES)

    if output_gates:
        out_result, halted = _run_valdo_phase(
            phase_label="output",
            gate_names=tuple(output_gates),
            pipeline_yaml=pipeline_yaml,
            work_root=work_root,
            report_root=report_root,
            run_id=run_id,
            env=env,
            source=source,
            gates_cfg=gates_cfg,
            valdo_executable=valdo_executable,
            runner=runner,
            logger=logger,
            sink_ctx=sink_ctx,
        )
        gates.extend(out_result)
        if halted:
            overall_blocking_failure = True

    # ----- 7. L2b SQL-Truth gate (orchestrator-driven, not via Valdo) --- #
    l2b_results = _run_l2b_sql_truth(
        source=source,
        env=env,
        run_id=run_id,
        src_cfg=src_cfg,
        resolver=resolver,
        sources_dir=sources_dir,
        report_root=report_root,
        gates_cfg=gates_cfg,
        logger=logger,
        sink_ctx=sink_ctx,
        pipeline_name=_pipeline_name(pipeline_yaml),
    )
    gates.extend(l2b_results)
    if any(r.status == "failed" and r.blocking for r in l2b_results):
        overall_blocking_failure = True

    # ----- 8. Multi-record validation HTML report (orchestrator-driven) - #
    mr_report_results = _run_multi_record_report(
        source=source,
        report_root=report_root,
        src_cfg=src_cfg,
        resolver=resolver,
        env=env,
        run_id=run_id,
        gates_cfg=gates_cfg,
        logger=logger,
        sink_ctx=sink_ctx,
        pipeline_name=_pipeline_name(pipeline_yaml),
    )
    gates.extend(mr_report_results)
    if any(r.status == "failed" and r.blocking for r in mr_report_results):
        overall_blocking_failure = True

    sink_ctx.close()
    return _finalize(
        gates,
        run_id,
        env,
        source,
        work_root,
        report_root,
        logger,
        overall_blocking_failure,
    )


# --------------------------------------------------------------------------- #
# Phase runners
# --------------------------------------------------------------------------- #


def _run_java_step(
    *,
    step_name: str,
    script: Optional[str],
    work_root: Path,
    logger: JsonlLogger,
    disabled: bool,
    blocking: bool,
    runner: _Subprocess,
    sink_ctx: "_FailureSinkContext",
    run_id: str,
    env: str,
    source: str,
    pipeline_name: str,
) -> Tuple[GateResult, bool]:
    """Run a Java shell-out step. Returns (gate_result, halted_flag)."""
    if disabled:
        logger.warn(
            "java_step_disabled",
            step=step_name,
            reason=f"{ENV_DISABLE_JAVA}=1 in environment",
        )
        return (
            GateResult(name=step_name, status="skipped", blocking=blocking),
            False,
        )
    if not script:
        logger.error("java_step_missing_script", step=step_name)
        gate = GateResult(
            name=step_name,
            status="infra_error",
            blocking=blocking,
            error=f"no java_scripts.{step_name.split('_')[0]} configured",
        )
        sink_ctx.write_failure(
            FailureRecord(
                run_id=run_id,
                env=env,
                source=source,
                pipeline_name=pipeline_name,
                gate_name=step_name,
                gate_stage="java",
                error_type="java_step_failed",
                error_count=1,
                blocking=blocking,
                failure_detail={"reason": "no script configured"},
            )
        )
        return gate, blocking

    logger.info("java_step_started", step=step_name, script=script)
    cp = runner.run(
        [script],
        cwd=work_root,
        env={**os.environ, "VALDO_RUN_ID": run_id, "VALDO_ENV": env},
    )
    if cp.returncode == 0:
        logger.info("java_step_passed", step=step_name)
        return GateResult(name=step_name, status="passed", blocking=blocking), False

    logger.error(
        "java_step_failed",
        step=step_name,
        returncode=cp.returncode,
        stderr_tail=(cp.stderr or "")[-1024:],
    )
    sink_ctx.write_failure(
        FailureRecord(
            run_id=run_id,
            env=env,
            source=source,
            pipeline_name=pipeline_name,
            gate_name=step_name,
            gate_stage="java",
            error_type="java_step_failed",
            error_count=1,
            blocking=blocking,
            failure_detail={
                "returncode": cp.returncode,
                "stderr_tail": (cp.stderr or "")[-512:],
            },
        )
    )
    return (
        GateResult(
            name=step_name,
            status="failed",
            blocking=blocking,
            error=f"exit {cp.returncode}",
        ),
        blocking,
    )


def _run_valdo_phase(
    *,
    phase_label: str,
    gate_names: Tuple[str, ...],
    pipeline_yaml: Path,
    work_root: Path,
    report_root: Path,
    run_id: str,
    env: str,
    source: str,
    gates_cfg: Dict[str, Any],
    valdo_executable: str,
    runner: _Subprocess,
    logger: JsonlLogger,
    sink_ctx: "_FailureSinkContext",
) -> Tuple[List[GateResult], bool]:
    """Run one phase of valdo run-etl-pipeline. Returns (results, halted)."""
    # Build the filtered pipeline YAML for this phase.
    phase_yaml = work_root / "pipelines" / f"{source}.{phase_label}.pipeline.yaml"
    try:
        split_pipeline_yaml(pipeline_yaml, gate_names, phase_yaml)
    except SplitPipelineError as exc:
        logger.error("phase_split_failed", phase=phase_label, error=str(exc))
        return (
            [
                GateResult(
                    name=g,
                    status="infra_error",
                    blocking=bool(gates_cfg.get(g, {}).get("blocking", True)),
                    error=f"split_pipeline_yaml failed: {exc}",
                )
                for g in gate_names
            ],
            any(gates_cfg.get(g, {}).get("blocking", True) for g in gate_names),
        )

    report_json = work_root / "reports" / f"{phase_label}_result.json"
    cmd = [
        valdo_executable,
        "run-etl-pipeline",
        "--config",
        str(phase_yaml),
        "--params",
        json.dumps({"run_id": run_id, "env": env, "source": source}),
        "--output",
        str(report_json),
    ]
    logger.info("phase_started", phase=phase_label, gates=list(gate_names))
    cp = runner.run(cmd)

    # Parse the JSON report Valdo wrote (preferred over scraping stdout).
    parsed: Dict[str, Any] = {}
    if report_json.is_file():
        try:
            parsed = json.loads(report_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error(
                "phase_report_unreadable",
                phase=phase_label,
                error=str(exc),
                path=str(report_json),
            )
    else:
        logger.warn("phase_report_missing", phase=phase_label, path=str(report_json))

    results: List[GateResult] = []
    halted = False
    seen: set = set()
    for gate_dict in parsed.get("gates", []):
        name = gate_dict.get("name", "<unknown>")
        seen.add(name)
        blocking = bool(gates_cfg.get(name, {}).get("blocking", True))
        gate_result = _record_gate_outcome(
            gate_dict=gate_dict,
            blocking=blocking,
            run_id=run_id,
            env=env,
            source=source,
            pipeline_name=parsed.get("pipeline_name", _pipeline_name(pipeline_yaml)),
            report_root=report_root,
            logger=logger,
            sink_ctx=sink_ctx,
        )
        results.append(gate_result)
        if gate_result.status == "failed" and blocking:
            halted = True
            # Valdo halts on first blocking failure too, so further gates
            # in this phase will be absent from the JSON. Stop processing.
            break

    # If Valdo failed before writing a parsable report, manufacture
    # infra-error gate results so the caller still has something.
    if cp.returncode != 0 and not results:
        for g in gate_names:
            blocking = bool(gates_cfg.get(g, {}).get("blocking", True))
            results.append(
                GateResult(
                    name=g,
                    status="infra_error",
                    blocking=blocking,
                    error=(cp.stderr or "")[-512:],
                )
            )
            if blocking:
                halted = True
        logger.error(
            "phase_infra_error",
            phase=phase_label,
            returncode=cp.returncode,
            stderr_tail=(cp.stderr or "")[-1024:],
        )

    # Account for any requested gates the report did not cover (e.g. halted
    # early). Mark them skipped so the caller's roll-up shows the full set.
    for g in gate_names:
        if g not in seen and not any(r.name == g for r in results):
            results.append(
                GateResult(
                    name=g,
                    status="skipped",
                    blocking=bool(gates_cfg.get(g, {}).get("blocking", True)),
                )
            )

    logger.info(
        "phase_finished",
        phase=phase_label,
        returncode=cp.returncode,
        halted=halted,
    )
    return results, halted


def _run_l2b_sql_truth(
    *,
    source: str,
    env: str,
    run_id: str,
    src_cfg: Dict[str, Any],
    resolver: PathResolver,
    sources_dir: Path,
    report_root: Path,
    gates_cfg: Dict[str, Any],
    logger: JsonlLogger,
    sink_ctx: "_FailureSinkContext",
    pipeline_name: str,
) -> List[GateResult]:
    """Run the L2b SQL-Truth gate for ``source``.

    Reconciles each declared output file against the same Oracle staging data
    the Java code reads from, driven by the generic ``db_truth_comparator``
    engine and a per-source/per-file-type reconciliation YAML at
    ``config/e2e/sources/<SOURCE>/reconciliation/<file_type>.yml``.

    Behaviour:

    * If the source declares no ``L2b_sql_truth`` gate policy, or
      ``VALDO_E2E_DISABLE_L2B=1`` is set, or no reconciliation YAML exists for
      any output file, the gate records a single ``skipped`` result with
      ``error_type='l2b_not_configured'`` at INFO level (same shape as the L2
      regeneration skip). It never opens an Oracle connection in that case.
    * Otherwise, one comparison runs per output file that has a YAML. The
      Oracle connection is opened lazily on first reconcile and reused across
      file types, then closed before returning. A connection failure degrades
      to an ``infra_error`` gate result (and a failure-sink row) rather than
      crashing the run.
    * Each reconciliation violation becomes one ``FailureRecord`` (capped to
      keep the audit table sane); the gate fails when any violations are
      found.

    Returns a list of :class:`GateResult` — one per reconciled file type, or a
    single skip/infra-error result.
    """
    gate_policy = gates_cfg.get(GATE_L2B_SQL_TRUTH, {})
    blocking = bool(gate_policy.get("blocking", False))

    if GATE_L2B_SQL_TRUTH not in gates_cfg:
        logger.info(
            "l2b_not_configured",
            source=source,
            reason="source 'gates:' block does not declare L2b_sql_truth",
        )
        return [GateResult(name=GATE_L2B_SQL_TRUTH, status="skipped", blocking=False)]

    if os.environ.get(ENV_DISABLE_L2B, "0") == "1":
        logger.warn(
            "l2b_disabled",
            source=source,
            reason=f"{ENV_DISABLE_L2B}=1 in environment",
        )
        return [
            GateResult(name=GATE_L2B_SQL_TRUTH, status="skipped", blocking=blocking)
        ]

    # Discover which output file types have a reconciliation YAML.
    source_root = Path(sources_dir) / source / "reconciliation"
    specs: List[Tuple[str, Path]] = []  # (file_type, yaml_path)
    for entry in src_cfg.get("output_files", []):
        file_type = entry.get("file_type")
        if not file_type:
            continue
        yaml_path = source_root / f"{str(file_type).lower()}.yml"
        if yaml_path.is_file():
            specs.append((str(file_type), yaml_path))

    if not specs:
        logger.info(
            "l2b_not_configured",
            source=source,
            reason=f"no reconciliation YAML found under {source_root}",
        )
        return [
            GateResult(name=GATE_L2B_SQL_TRUTH, status="skipped", blocking=blocking)
        ]

    # Lazily import the engine + spec loader so a source without L2b never
    # pays the import cost (and the harness still imports cleanly where the
    # engine's optional deps are absent).
    try:
        from scripts.e2e_lib.db_truth_comparator import (
            DbTruthComparatorError,
            reconcile,
        )
        from scripts.e2e_lib.reconciliation_spec import (
            ReconciliationSpecError,
            load_spec,
        )
    except ImportError as exc:  # pragma: no cover — defensive
        logger.error("l2b_engine_unavailable", error=str(exc))
        return [
            GateResult(
                name=GATE_L2B_SQL_TRUTH,
                status="infra_error",
                blocking=blocking,
                error=f"L2b engine import failed: {exc}",
            )
        ]

    # Resolve the output root once; output files are matched by glob there.
    try:
        output_root = Path(resolver.resolve("output_root", env=env, source=source))
    except PathResolverError as exc:
        logger.error("l2b_output_root_unresolved", error=str(exc))
        return [
            GateResult(
                name=GATE_L2B_SQL_TRUTH,
                status="infra_error",
                blocking=blocking,
                error=f"could not resolve output_root: {exc}",
            )
        ]

    conn = _open_l2b_connection(env=env, sink_ctx=sink_ctx, logger=logger)
    if conn is None:
        sink_ctx.write_failure(
            FailureRecord(
                run_id=run_id,
                env=env,
                source=source,
                pipeline_name=pipeline_name,
                gate_name=GATE_L2B_SQL_TRUTH,
                gate_stage="output",
                layer="L2b",
                error_type="l2b_infra_error",
                error_count=1,
                blocking=blocking,
                failure_detail={"reason": "oracle connection unavailable"},
            )
        )
        return [
            GateResult(
                name=GATE_L2B_SQL_TRUTH,
                status="infra_error",
                blocking=blocking,
                error="oracle connection unavailable",
            )
        ]

    results: List[GateResult] = []
    try:
        for file_type, yaml_path in specs:
            results.append(
                _reconcile_one_file_type(
                    file_type=file_type,
                    yaml_path=yaml_path,
                    output_root=output_root,
                    src_cfg=src_cfg,
                    conn=conn,
                    blocking=blocking,
                    run_id=run_id,
                    env=env,
                    source=source,
                    pipeline_name=pipeline_name,
                    logger=logger,
                    sink_ctx=sink_ctx,
                    reconcile=reconcile,
                    load_spec=load_spec,
                    spec_error=ReconciliationSpecError,
                    engine_error=DbTruthComparatorError,
                )
            )
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001 — best effort on teardown
            pass
    return results


def _run_multi_record_report(
    *,
    source: str,
    report_root: Path,
    src_cfg: Dict[str, Any],
    resolver: PathResolver,
    env: str,
    run_id: str,
    gates_cfg: Dict[str, Any],
    logger: JsonlLogger,
    sink_ctx: "_FailureSinkContext",
    pipeline_name: str,
) -> List[GateResult]:
    """Render the multi-record validation HTML report for ``source``.

    For every ``output_files`` entry flagged ``multi_record: true`` this runs
    the standard :class:`~src.validators.multi_record_validator.MultiRecordValidator`
    (per-record-type mappings + rules, including cross-row rules such as the
    SHAW TRANERT ``CIF-REF-NUM-CUS`` countdown) and renders, via
    ``scripts/render_multi_record_html.py``:

    * one HTML subpage per record type (the standard ``ValidationReporter``
      output), and
    * a cumulative umbrella ``index.html`` linking them with an overall verdict.

    Reports land under ``<report_root>/multi_record/<file_type>/index.html``.

    This is a REPORT, not a pass/fail gate: the underlying rule failures are
    already enforced by ``L1_structural``. The step is therefore non-blocking
    by default and a render failure degrades to an ``infra_error`` result
    rather than halting the run. It records a ``skipped`` result (never opening
    a file) when the source does not declare the step, when
    ``VALDO_E2E_DISABLE_MR_REPORT=1``, or when no multi-record output file is
    configured.

    PII suppression follows the harness convention (AGENTS.md hard rule #4:
    non-prod data is already privatized) and is OFF unless the gate policy sets
    ``suppress_pii: true``.

    Returns a list of :class:`GateResult` — one per rendered file type, or a
    single skip/infra-error result.
    """
    gate_policy = gates_cfg.get(GATE_MULTI_RECORD_REPORT, {})
    blocking = bool(gate_policy.get("blocking", False))
    suppress_pii = bool(gate_policy.get("suppress_pii", False))

    if GATE_MULTI_RECORD_REPORT not in gates_cfg:
        logger.info(
            "multi_record_report_not_configured",
            source=source,
            reason="source 'gates:' block does not declare multi_record_report",
        )
        return [
            GateResult(name=GATE_MULTI_RECORD_REPORT, status="skipped", blocking=False)
        ]

    if os.environ.get(ENV_DISABLE_MR_REPORT, "0") == "1":
        logger.warn(
            "multi_record_report_disabled",
            source=source,
            reason=f"{ENV_DISABLE_MR_REPORT}=1 in environment",
        )
        return [
            GateResult(
                name=GATE_MULTI_RECORD_REPORT, status="skipped", blocking=blocking
            )
        ]

    # Which output files are multi-record (have an umbrella mapping)?
    mr_entries = [
        e
        for e in src_cfg.get("output_files", [])
        if e.get("multi_record") and e.get("mapping") and e.get("file_type")
    ]
    if not mr_entries:
        logger.info(
            "multi_record_report_not_configured",
            source=source,
            reason="no multi_record output files declared",
        )
        return [
            GateResult(
                name=GATE_MULTI_RECORD_REPORT, status="skipped", blocking=blocking
            )
        ]

    # Lazily import the renderer so non-multi-record sources never pay the cost
    # (and the orchestrator imports cleanly where src/ optional deps are absent).
    try:
        from scripts.render_multi_record_html import render as _render_mr
    except ImportError as exc:  # pragma: no cover — defensive
        logger.error("multi_record_report_renderer_unavailable", error=str(exc))
        return [
            GateResult(
                name=GATE_MULTI_RECORD_REPORT,
                status="infra_error",
                blocking=blocking,
                error=f"multi-record renderer import failed: {exc}",
            )
        ]

    try:
        output_root = Path(resolver.resolve("output_root", env=env, source=source))
    except PathResolverError as exc:
        logger.error("multi_record_report_output_root_unresolved", error=str(exc))
        return [
            GateResult(
                name=GATE_MULTI_RECORD_REPORT,
                status="infra_error",
                blocking=blocking,
                error=f"could not resolve output_root: {exc}",
            )
        ]

    results: List[GateResult] = []
    for entry in mr_entries:
        file_type = str(entry["file_type"])
        umbrella = entry["mapping"]
        glob = entry.get("glob")
        file_path = _resolve_newest(output_root, glob) if glob else None
        if file_path is None:
            logger.error(
                "multi_record_report_output_file_missing",
                file_type=file_type,
                output_root=str(output_root),
                glob=glob,
            )
            results.append(
                GateResult(
                    name=GATE_MULTI_RECORD_REPORT,
                    status="infra_error",
                    blocking=blocking,
                    error=f"{file_type}: output file not found under {output_root}",
                )
            )
            continue

        out_dir = report_root / "multi_record" / file_type
        logger.info(
            "multi_record_report_started",
            file_type=file_type,
            file=str(file_path),
            umbrella=umbrella,
        )
        try:
            index_path = _render_mr(
                umbrella_yaml_path=umbrella,
                data_file_path=str(file_path),
                output_dir=str(out_dir),
                suppress_pii=suppress_pii,
            )
        except Exception as exc:  # noqa: BLE001 — report render must not crash run
            logger.error(
                "multi_record_report_failed", file_type=file_type, error=str(exc)
            )
            results.append(
                GateResult(
                    name=GATE_MULTI_RECORD_REPORT,
                    status="infra_error",
                    blocking=blocking,
                    error=f"{file_type}: render failed: {exc}",
                )
            )
            continue

        logger.info(
            "multi_record_report_written",
            file_type=file_type,
            report=str(index_path),
        )
        results.append(
            GateResult(
                name=GATE_MULTI_RECORD_REPORT,
                status="passed",
                blocking=blocking,
                report_path=str(index_path),
            )
        )
    return results


def _open_l2b_connection(
    *, env: str, sink_ctx: "_FailureSinkContext", logger: JsonlLogger
) -> Optional[Any]:
    """Open an Oracle connection for the L2b gate, or ``None`` on failure.

    Delegates to :class:`src.database.truth_source.OracleTruthSource` (ADR 0010)
    so Oracle is one implementation behind the :class:`TruthSource` seam. The
    credential convention is unchanged: bare ``ORACLE_DSN`` / ``ORACLE_USER`` /
    ``ORACLE_PASSWORD`` resolved via the run's :class:`SecretResolver`.
    Connection (or credential-resolution) failures are logged and degrade the
    gate to an infra-error rather than raising — behaviour preserved from the
    pre-ADR-0010 inline opener. The distinct ``l2b_*`` log events are retained
    for operator continuity.
    """
    from src.database.truth_source import OracleTruthSource, TruthSourceError

    resolver = sink_ctx._resolver  # reuse the run's resolver
    truth_source = OracleTruthSource(secret_lookup=resolver)
    try:
        return truth_source.connect()
    except TruthSourceError as exc:
        # OracleTruthSource wraps both the missing-secret and the driver/connect
        # failures in TruthSourceError; map them to the historical log events so
        # existing log consumers keep working. The message never contains a
        # secret value (the adapter guarantees this).
        message = str(exc)
        if "could not resolve Oracle credentials" in message:
            logger.error("l2b_secret_unresolved", error=message)
        elif "oracledb driver is not installed" in message:
            logger.error("l2b_oracledb_missing", error=message)
        else:
            logger.error("l2b_connect_failed", error=message)
        return None


def _reconcile_one_file_type(
    *,
    file_type: str,
    yaml_path: Path,
    output_root: Path,
    src_cfg: Dict[str, Any],
    conn: Any,
    blocking: bool,
    run_id: str,
    env: str,
    source: str,
    pipeline_name: str,
    logger: JsonlLogger,
    sink_ctx: "_FailureSinkContext",
    reconcile: Any,
    load_spec: Any,
    spec_error: Type[Exception],
    engine_error: Type[Exception],
) -> GateResult:
    """Reconcile one output file type and return its :class:`GateResult`."""
    gate_name = GATE_L2B_SQL_TRUTH

    # Load + validate the spec.
    try:
        spec = load_spec(yaml_path)
    except spec_error as exc:
        logger.error("l2b_spec_invalid", file_type=file_type, error=str(exc))
        sink_ctx.write_failure(
            FailureRecord(
                run_id=run_id,
                env=env,
                source=source,
                pipeline_name=pipeline_name,
                gate_name=gate_name,
                gate_stage="output",
                layer="L2b",
                file_type=file_type,
                mapping_path=str(yaml_path),
                error_type="l2b_spec_invalid",
                error_count=1,
                blocking=blocking,
                failure_detail={"message": str(exc)},
            )
        )
        return GateResult(
            name=gate_name,
            status="infra_error",
            blocking=blocking,
            error=f"{file_type}: spec invalid: {exc}",
        )

    # Resolve the output file via its glob (newest match on disk).
    glob = _output_glob_for(src_cfg, file_type)
    file_path = _resolve_newest(output_root, glob) if glob else None
    if file_path is None:
        logger.error(
            "l2b_output_file_missing",
            file_type=file_type,
            output_root=str(output_root),
            glob=glob,
        )
        sink_ctx.write_failure(
            FailureRecord(
                run_id=run_id,
                env=env,
                source=source,
                pipeline_name=pipeline_name,
                gate_name=gate_name,
                gate_stage="output",
                layer="L2b",
                file_type=file_type,
                error_type="l2b_infra_error",
                error_count=1,
                blocking=blocking,
                failure_detail={
                    "reason": "output file not found",
                    "output_root": str(output_root),
                    "glob": glob,
                },
            )
        )
        return GateResult(
            name=gate_name,
            status="infra_error",
            blocking=blocking,
            error=f"{file_type}: output file not found under {output_root}",
        )

    logger.info(
        "l2b_reconcile_started",
        file_type=file_type,
        file=str(file_path),
        spec=str(yaml_path),
    )
    try:
        report = reconcile(conn, spec, file_path)
    except engine_error as exc:
        logger.error("l2b_reconcile_error", file_type=file_type, error=str(exc))
        sink_ctx.write_failure(
            FailureRecord(
                run_id=run_id,
                env=env,
                source=source,
                pipeline_name=pipeline_name,
                gate_name=gate_name,
                gate_stage="output",
                layer="L2b",
                file_type=file_type,
                file_name=file_path.name,
                mapping_path=str(yaml_path),
                error_type="l2b_infra_error",
                error_count=1,
                blocking=blocking,
                failure_detail={"message": str(exc)},
            )
        )
        return GateResult(
            name=gate_name,
            status="infra_error",
            blocking=blocking,
            error=f"{file_type}: {exc}",
        )

    violations = report.violations
    if not violations:
        logger.info(
            "l2b_reconcile_passed",
            file_type=file_type,
            rows_compared=report.rows_compared,
        )
        return GateResult(name=gate_name, status="passed", blocking=blocking)

    logger.error(
        "l2b_reconcile_failed",
        file_type=file_type,
        violations=len(violations),
        blocking=blocking,
    )
    # One failure row per violation, capped so a totally-wrong file does not
    # flood the audit table. The roll-up count reflects the true total.
    cap = 500
    for v in violations[:cap]:
        sink_ctx.write_failure(
            FailureRecord(
                run_id=run_id,
                env=env,
                source=source,
                pipeline_name=pipeline_name,
                gate_name=gate_name,
                gate_stage="output",
                layer="L2b",
                file_type=file_type,
                file_name=file_path.name,
                mapping_path=str(yaml_path),
                error_type="compare_diff",
                error_count=1,
                blocking=blocking,
                row_count=report.rows_compared,
                failure_detail={
                    "kind": v.kind,
                    "record_type": v.record_type,
                    "key": list(v.key_values),
                    "field": v.field,
                    "expected": v.expected,
                    "actual": v.actual,
                    "line_number": v.line_number,
                    "message": v.message,
                },
            )
        )
    return GateResult(
        name=gate_name,
        status="failed",
        blocking=blocking,
        error=f"{file_type}: {len(violations)} reconciliation violation(s)",
    )


def _output_glob_for(src_cfg: Dict[str, Any], file_type: str) -> Optional[str]:
    """Return the ``glob`` for ``file_type`` from the source's output_files."""
    for entry in src_cfg.get("output_files", []):
        if str(entry.get("file_type")) == file_type:
            return entry.get("glob")
    return None


def _resolve_newest(root: Path, glob: str) -> Optional[Path]:
    """Return the newest file matching ``glob`` under ``root``, or ``None``."""
    try:
        matches = sorted(root.glob(glob), key=lambda p: p.stat().st_mtime, reverse=True)
    except OSError:
        return None
    for m in matches:
        if m.is_file():
            return m
    return None


def _record_gate_outcome(
    *,
    gate_dict: Dict[str, Any],
    blocking: bool,
    run_id: str,
    env: str,
    source: str,
    pipeline_name: str,
    report_root: Path,
    logger: JsonlLogger,
    sink_ctx: "_FailureSinkContext",
) -> GateResult:
    """Translate one Valdo gate report dict into a GateResult and audit it."""
    name = gate_dict.get("name", "<unknown>")
    status = gate_dict.get("status", "?")
    steps = gate_dict.get("steps", []) or []
    gate_result = GateResult(
        name=name,
        status=status,
        blocking=blocking,
        steps=steps,
        error=gate_dict.get("error"),
    )
    if status != "failed":
        logger.info("gate_passed", gate=name)
        return gate_result

    logger.error("gate_failed", gate=name, blocking=blocking)

    # One failure row per failed STEP within the gate (gives the operator
    # one row per output file rather than one row per gate).
    layer = _layer_for_gate(name)
    for step in steps:
        if step.get("status") == "passed":
            continue
        sink_ctx.write_failure(
            FailureRecord(
                run_id=run_id,
                env=env,
                source=source,
                pipeline_name=pipeline_name,
                gate_name=name,
                gate_stage=gate_dict.get("stage"),
                layer=layer,
                file_name=_basename(step.get("file") or step.get("query")),
                file_type=None,
                mapping_path=step.get("mapping"),
                mapping_version=None,
                error_type=_error_type_for_gate(name),
                error_count=int(
                    step.get("error_count") or len(step.get("errors", []) or [])
                ),
                row_count=step.get("total_rows") or step.get("row_count"),
                report_path=step.get("report_path"),
                blocking=blocking,
                failure_detail={
                    "step_type": step.get("type"),
                    "message": step.get("error") or step.get("message"),
                },
            )
        )
    return gate_result


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _layer_for_gate(gate_name: str) -> Optional[str]:
    if gate_name == "L1_structural":
        return "L1"
    if gate_name == "L3_baseline_diff":
        return "L3"
    if gate_name == GATE_L2B_SQL_TRUTH:
        return "L2b"
    return None


def _error_type_for_gate(gate_name: str) -> str:
    return {
        "L1_structural": "structural",
        "L3_baseline_diff": "compare_diff",
        GATE_L2B_SQL_TRUTH: "compare_diff",
        "file_to_staging": "compare_diff",
        "load_step": "java_step_failed",
        "generate_step": "java_step_failed",
    }.get(gate_name, "unknown")


def _basename(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    return Path(path).name


def _pipeline_name(pipeline_yaml: Path) -> str:
    """Best-effort: derive ``name`` from the YAML without re-parsing twice.

    Falls back to the stem if reading fails.
    """
    try:
        import yaml

        data = yaml.safe_load(pipeline_yaml.read_text(encoding="utf-8")) or {}
        return data.get("name") or pipeline_yaml.stem
    except Exception:  # noqa: BLE001 — best effort
        return pipeline_yaml.stem


# --------------------------------------------------------------------------- #
# Failure-sink lifecycle
# --------------------------------------------------------------------------- #


class _FailureSinkContext:
    """Lazy-open / lazy-disabled wrapper around :class:`FailureSink`.

    The sink is constructed on first ``write_failure`` so a clean run with
    zero failures never opens an Oracle connection. When
    ``VALDO_E2E_DISABLE_FAILURE_SINK=1`` is set, failures are logged to
    JSONL but never sent to Oracle (useful for dev / CI).
    """

    def __init__(
        self,
        *,
        failure_sink: Optional[FailureSink],
        env: str,
        resolver: SecretResolver,
        logger: JsonlLogger,
    ) -> None:
        self._sink = failure_sink
        # Track whether this context OWNS the sink. We must NOT close a
        # caller-supplied sink (the caller may still want to read from the
        # underlying connection — e.g. tests do exactly this).
        self._owns_sink = failure_sink is None
        self._env = env
        self._resolver = resolver
        self._logger = logger
        self._disabled = os.environ.get(ENV_DISABLE_FAILURE_SINK, "0") == "1"
        self._broken = False  # set if a sink open or write raised

    def write_failure(self, record: FailureRecord) -> None:
        # Always log to JSONL, even when the DB sink is disabled or broken.
        self._logger.error(
            "failure_recorded",
            gate=record.gate_name,
            layer=record.layer,
            file_name=record.file_name,
            error_type=record.error_type,
            error_count=record.error_count,
            blocking=record.blocking,
        )
        if self._disabled or self._broken:
            return
        if self._sink is None:
            try:
                self._sink = FailureSink.for_oracle(
                    env=self._env, resolver=self._resolver
                )
                # We opened it; we own it.
                self._owns_sink = True
            except FailureSinkError as exc:
                self._logger.error("failure_sink_unavailable", error=str(exc))
                self._broken = True
                return
        try:
            self._sink.insert_failure(record)
        except FailureSinkError as exc:
            self._logger.error("failure_sink_write_failed", error=str(exc))
            self._broken = True

    def close(self) -> None:
        if self._sink is not None and self._owns_sink:
            self._sink.close()
            self._sink = None


# --------------------------------------------------------------------------- #
# Finalization (roll-up + exit code)
# --------------------------------------------------------------------------- #


def _finalize(
    gates: List[GateResult],
    run_id: str,
    env: str,
    source: str,
    work_root: Path,
    report_root: Path,
    logger: JsonlLogger,
    overall_blocking_failure: bool,
) -> RunResult:
    rollup_path = _write_per_source_rollup(report_root, run_id, env, source, gates)
    exit_code = EXIT_BLOCKING_FAILURE if overall_blocking_failure else EXIT_OK
    logger.info(
        "run_finished",
        exit_code=exit_code,
        gates_passed=sum(1 for g in gates if g.status == "passed"),
        gates_failed=sum(1 for g in gates if g.status == "failed"),
        gates_skipped=sum(1 for g in gates if g.status == "skipped"),
        gates_infra_error=sum(1 for g in gates if g.status == "infra_error"),
        rollup=str(rollup_path),
    )
    return RunResult(
        run_id=run_id,
        env=env,
        source=source,
        exit_code=exit_code,
        work_root=str(work_root),
        report_root=str(report_root),
        log_path=str(logger.log_path),
        gates=gates,
        rollup_path=str(rollup_path),
    )


def _write_per_source_rollup(
    report_root: Path,
    run_id: str,
    env: str,
    source: str,
    gates: List[GateResult],
) -> Path:
    """Write a minimal per-source roll-up HTML + JSON manifest.

    M6 expands this with the global roll-up. The HTML deliberately uses
    no external assets so it works on the RHEL host without a browser
    pipeline.
    """
    report_root.mkdir(parents=True, exist_ok=True)
    summary = {
        "run_id": run_id,
        "env": env,
        "source": source,
        "gates": [
            {
                "name": g.name,
                "status": g.status,
                "blocking": g.blocking,
                "step_count": len(g.steps),
                "error": g.error,
            }
            for g in gates
        ],
    }
    (report_root / "summary.json").write_text(
        json.dumps(summary, indent=2, default=str), encoding="utf-8"
    )

    rows = []
    for g in gates:
        cls = {
            "passed": "ok",
            "failed": "bad",
            "skipped": "warn",
            "infra_error": "bad",
        }.get(g.status, "warn")
        block_tag = "blocking" if g.blocking else "non-blocking"
        rows.append(
            f"<tr class='{cls}'><td>{g.name}</td>"
            f"<td>{g.status}</td>"
            f"<td>{block_tag}</td>"
            f"<td>{len(g.steps)}</td>"
            f"<td>{(g.error or '').replace('<', '&lt;')}</td></tr>"
        )

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>Valdo E2E {env}/{source} {run_id}</title>
<style>
 body {{ font-family: system-ui, sans-serif; margin: 2rem; }}
 table {{ border-collapse: collapse; }}
 th, td {{ border: 1px solid #ccc; padding: .3rem .6rem; }}
 tr.ok {{ background: #e6ffe6; }}
 tr.bad {{ background: #ffe6e6; }}
 tr.warn {{ background: #fff7d6; }}
 h1 {{ font-size: 1.2rem; }}
</style>
</head>
<body>
<h1>Valdo E2E roll-up: {env}/{source} (run {run_id})</h1>
<table>
<thead><tr><th>Gate</th><th>Status</th><th>Blocking</th>
<th>Steps</th><th>Error</th></tr></thead>
<tbody>
{''.join(rows)}
</tbody>
</table>
</body>
</html>
"""
    rollup = report_root / "index.html"
    rollup.write_text(html, encoding="utf-8")
    return rollup


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Sequence[str]) -> Dict[str, Any]:
    """Minimal argparse-free parser; keeps the bash side trivial."""
    args = list(argv)
    out: Dict[str, Any] = {
        "env": None,
        "source": None,
        "run_id": None,
        "paths_yaml": "config/e2e/paths.yml",
        "sources_dir": "config/e2e/sources",
        "pipeline_yaml": None,
        "valdo_executable": "valdo",
    }
    i = 0
    while i < len(args):
        a = args[i]
        if a in (
            "--env",
            "--source",
            "--run-id",
            "--paths-yaml",
            "--sources-dir",
            "--pipeline-yaml",
            "--valdo-executable",
        ):
            if i + 1 >= len(args):
                raise SystemExit(f"error: {a} requires a value")
            key = a.lstrip("-").replace("-", "_")
            out[key] = args[i + 1]
            i += 2
        else:
            raise SystemExit(f"error: unknown argument {a!r}")
    for required in ("env", "source", "run_id"):
        if not out[required]:
            raise SystemExit(f"error: --{required.replace('_', '-')} is required")
    if not out["pipeline_yaml"]:
        out["pipeline_yaml"] = (
            f"config/e2e/pipelines/{out['env']}/{out['source']}.pipeline.yaml"
        )
    return out


def main(argv: Optional[Sequence[str]] = None) -> int:
    parsed = _parse_args(list(argv) if argv is not None else sys.argv[1:])
    result = run_source(
        env=parsed["env"],
        source=parsed["source"],
        run_id=parsed["run_id"],
        paths_yaml=Path(parsed["paths_yaml"]),
        sources_dir=Path(parsed["sources_dir"]),
        pipeline_yaml=Path(parsed["pipeline_yaml"]),
        valdo_executable=parsed["valdo_executable"],
    )
    # Compact human summary on stdout.
    sys.stdout.write(
        f"run_id={result.run_id} env={result.env} source={result.source} "
        f"exit={result.exit_code} rollup={result.rollup_path}\n"
    )
    return result.exit_code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

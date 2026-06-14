"""Generate Valdo ETL pipeline YAML files from E2E source configs.

This is the M3 deliverable from ``prompts/e2e_batch_testing_prompt.md``.

Given:
  * ``config/e2e/paths.yml`` — env-keyed root paths
  * ``config/e2e/sources/<SOURCE>.yml`` — per-source overlay

emit:
  * ``config/e2e/pipelines/<env>/<SOURCE>.pipeline.yaml`` — a fully-resolved
    :class:`~src.pipeline.etl_config.PipelineDefinition` consumable by
    ``valdo run-etl-pipeline --config <yaml>``.

What the emitted pipeline contains
----------------------------------
The harness has six logical gates per source:

  1. ``load_step``        — Java shell-out. Not in the YAML; handled by the
                            wrapper script (M4) because Valdo has no ``shell``
                            step type today.
  2. ``file_to_staging``  — one Valdo step per input file comparing the
                            staging row export to the original input file
                            (file→staging gate).
  3. ``generate_step``    — Java shell-out. Same caveat as ``load_step``.
  4. ``L1_structural``    — one ``validate`` step per output file.
  5. ``L3_baseline_diff`` — one ``compare`` step per output file against the
                            pinned golden baseline.

Only steps 2, 4, 5 land in the YAML. The Java shell-outs (1, 3) are the
wrapper script's job. (A former ``L2_regeneration`` gate was retired in
ADR 0012; the output-truth axis is covered by the orchestrator-driven
``L2b_sql_truth`` gate and the regression axis by ``L3_baseline_diff``.)

Path resolution
---------------
Every path inside the emitted YAML is resolved at generation time using
:class:`scripts.e2e_lib.path_resolver.PathResolver`, with one exception:
``{run_id}`` is intentionally left UNRESOLVED. The pipeline is generated
once per release and then reused across many runs; ``{run_id}`` is
substituted by the Valdo runner at execution time via its ``params`` dict
(``--params run_id=…``). The wrapper script in M4 passes it through.

Why this is config-side, not Valdo-side
---------------------------------------
The prompt's hard rule #1 forbids modifying Valdo internals. This module
emits valid input to the existing :class:`PipelineDefinition` schema and
the existing :class:`ETLPipelineRunner._expand_template` machinery
(which already supports ``{source.<field>}`` and ``{<param>}``). No
new step types are introduced.

CLI
---
::

    python scripts/generate_pipeline_yaml.py \\
        --source SRC_A --env sit \\
        [--paths-yaml config/e2e/paths.yml] \\
        [--sources-dir config/e2e/sources] \\
        [--out-dir config/e2e/pipelines] \\
        [--all-envs] [--all-sources] \\
        [--stdout] [--check]

Exit codes:
  0 — success (file written, or ``--check`` clean)
  2 — config / validation error
  3 — I/O error
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

# Make the repo importable when invoked as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.path_resolver import (  # noqa: E402
    PathResolver,
    PathResolverError,
)

# Gates emitted into the YAML. Order matters — it is the execution order.
# Java shell-out gates (``load_step``, ``generate_step``) are omitted; they
# are the wrapper script's responsibility.
_VALDO_RUNNABLE_GATES: Tuple[str, ...] = (
    "file_to_staging",
    "L1_structural",
    "L3_baseline_diff",
)

# Gates that exist in a source's ``gates:`` block but are intentionally NOT
# emitted into the pipeline YAML because they are driven directly by the M4
# orchestrator (scripts/e2e_lib/run_source.py), not by
# ``valdo run-etl-pipeline``:
#
#   * ``load_step`` / ``generate_step`` — Java shell-outs (no Valdo step type).
#   * ``L2b_sql_truth``               — the SQL-Truth reconciliation gate runs
#                                       via the generic db_truth_comparator
#                                       engine against Oracle staging data;
#                                       Valdo has no step type for it, and it
#                                       needs a live DB connection the pipeline
#                                       runner does not manage. The orchestrator
#                                       runs it after the output-phase Valdo
#                                       gates and only for sources that ship a
#                                       reconciliation YAML.
#
# Listed here so the generator's gate-coverage validation knows these names
# are expected (and not typos) without trying to materialize Valdo steps for
# them.
_ORCHESTRATOR_DRIVEN_GATES: Tuple[str, ...] = (
    "load_step",
    "generate_step",
    "L2b_sql_truth",
)

# Default location for generated YAML files.
_DEFAULT_OUT_DIR = "config/e2e/pipelines"
_DEFAULT_PATHS_YAML = "config/e2e/paths.yml"
_DEFAULT_SOURCES_DIR = "config/e2e/sources"


class PipelineGenerationError(RuntimeError):
    """Raised when the source/paths config cannot be turned into a pipeline."""


# --------------------------------------------------------------------------- #
# Pure builder (no I/O, no argparse) — this is what the unit tests target.
# --------------------------------------------------------------------------- #


def build_pipeline_dict(
    *,
    env: str,
    source: str,
    resolver: PathResolver,
) -> Dict[str, Any]:
    """Build a Python dict representing the pipeline YAML for ``(env, source)``.

    The output is the canonical, stable shape that
    ``yaml.safe_dump`` serializes for the golden-file test. Field order is
    fixed so a diff against the golden file is meaningful.

    Args:
        env: One of the env names declared in ``paths.yml`` (e.g. ``sit``).
        source: The source identifier matching a YAML in ``sources_dir``.
        resolver: A ready :class:`PathResolver`.

    Returns:
        A dict compatible with :class:`PipelineDefinition.model_validate`.

    Raises:
        PipelineGenerationError: If the source config is missing required
            sections, or if a referenced gate is not declared in the source.
    """
    src_cfg = resolver.source_config(source)
    _validate_source_cfg(src_cfg, source)

    pipeline_name = f"e2e_{env}_{source}"
    description = (
        f"E2E batch-testing pipeline for source '{source}' in env '{env}'. "
        "Generated by scripts/generate_pipeline_yaml.py; do not edit by hand."
    )

    # Resolve env-scoped roots once. Any ``{run_id}`` token that survives into
    # an emitted step path is substituted per-run by Valdo's pipeline runner via
    # --params at execute time, keeping a single pipeline file reusable across
    # every run.
    input_root = resolver.resolve("input_root", env=env, source=source)
    output_root = resolver.resolve("output_root", env=env, source=source)
    baseline_root = resolver.resolve("baseline_root", env=env, source=source)
    # ``source=`` is passed so per-source ``staging_schema`` overrides (F1)
    # are honored; sources without an override fall through to the env value.
    staging_schema = resolver.resolve("staging_schema", env=env, source=source)
    audit_schema = resolver.resolve("audit_schema", env=env)

    # Resolve every input/output file into a concrete dict that the gate
    # builders consume. Paths are fully materialized here so the emitted
    # YAML contains no {source.…} placeholders — only {run_id} is deferred.
    input_files = [
        _resolve_input_entry(
            entry,
            source=source,
            input_root=input_root,
            staging_schema=staging_schema,
        )
        for entry in src_cfg.get("input_files", [])
    ]
    output_files = [
        _resolve_output_entry(
            entry,
            source=source,
            output_root=output_root,
            baseline_root=baseline_root,
        )
        for entry in src_cfg.get("output_files", [])
    ]

    # SourceDefinitions: one per file, exposing concrete paths. Steps in
    # this YAML reference these by absolute path, not by {source.<field>},
    # so the SourceDefinition list serves as documentation/manifest only
    # (and satisfies Valdo's schema, which requires non-empty per-source
    # `name` + `mapping`). Keeping the entries is useful for the per-run
    # roll-up index in M6, which can scan `sources:` to enumerate files.
    sources: List[Dict[str, Any]] = []
    for f in input_files:
        sources.append(
            {
                "name": f["source_def_name"],
                "mapping": f["mapping"],
                "rules": "",
                "output_pattern": "",
                "input_path": f["input_path"],
                "target_mapping": "",
                "staging_tables": [f["qualified_staging_table"]],
            }
        )
    for f in output_files:
        sources.append(
            {
                "name": f["source_def_name"],
                "mapping": f["mapping"],
                "rules": f["rules"],
                "output_pattern": f["java_output_path"],
                "input_path": "",
                "target_mapping": f["baseline_path"],
                "staging_tables": [],
            }
        )

    # Gates.
    gates_cfg = src_cfg.get("gates", {})
    gates: List[Dict[str, Any]] = []
    for gate_name in _VALDO_RUNNABLE_GATES:
        gate_policy = gates_cfg.get(gate_name)
        if gate_policy is None:
            raise PipelineGenerationError(
                f"source '{source}' is missing the required gate "
                f"'{gate_name}' in its 'gates:' block"
            )
        gate = _build_gate(
            gate_name=gate_name,
            blocking=bool(gate_policy.get("blocking", True)),
            input_files=input_files,
            output_files=output_files,
            audit_schema=audit_schema,
        )
        gates.append(gate)

    return {
        "name": pipeline_name,
        "description": description,
        "sources": sources,
        "gates": gates,
    }


# --------------------------------------------------------------------------- #
# Per-file path resolution
# --------------------------------------------------------------------------- #


def _resolve_input_entry(
    entry: Dict[str, Any],
    *,
    source: str,
    input_root: str,
    staging_schema: str,
) -> Dict[str, Any]:
    """Materialize a single input_files entry into concrete paths."""
    file_type = _require(entry, "file_type", "input_files entry")
    mapping = _require(entry, "mapping", f"input_files[{file_type}]")
    glob = _require(entry, "glob", f"input_files[{file_type}]")
    target_table = _require(entry, "target_staging_table", f"input_files[{file_type}]")
    qualified_table = (
        target_table if "." in target_table else f"{staging_schema}.{target_table}"
    )
    return {
        "source_def_name": f"{source}__input__{file_type}",
        "file_type": file_type,
        "mapping": mapping,
        # Concrete file path. The glob is preserved so the wrapper script
        # can resolve the latest match on disk at run time; Valdo's existing
        # CLI surfaces accept globs.
        "input_path": f"{input_root}/{glob}",
        "target_staging_table": target_table,
        "qualified_staging_table": qualified_table,
        "thresholds": entry.get("thresholds"),
    }


def _resolve_output_entry(
    entry: Dict[str, Any],
    *,
    source: str,
    output_root: str,
    baseline_root: str,
) -> Dict[str, Any]:
    """Materialize a single output_files entry into concrete paths.

    Per ADR 0005, entries with ``multi_record: true`` carry an umbrella
    YAML in ``mapping`` (consumed at L1 by ``validate_multi_record`` steps).
    A fail-fast sanity check rejects mismatches between the ``multi_record``
    flag and the mapping file extension before the YAML ships, catching
    the class of bug where a ``.yaml`` umbrella is accidentally wired into
    a ``validate`` step that would try to JSON-parse it at run time.
    """
    file_type = _require(entry, "file_type", "output_files entry")
    mapping = _require(entry, "mapping", f"output_files[{file_type}]")
    glob = _require(entry, "glob", f"output_files[{file_type}]")
    rules = entry.get("rules", "") or ""
    multi_record = bool(entry.get("multi_record", False))

    # Sanity check: mapping extension must agree with multi_record flag.
    # Compare on the lower-cased path so callers are not punished for case.
    mapping_lower = mapping.lower()
    if multi_record:
        if not (mapping_lower.endswith(".yaml") or mapping_lower.endswith(".yml")):
            raise PipelineGenerationError(
                f"output_files[{file_type}]: multi_record is true but "
                f"mapping {mapping!r} is not a .yaml/.yml umbrella file. "
                f"Multi-record entries must reference a MultiRecordConfig "
                f"umbrella YAML (see ADR 0005)."
            )
    else:
        if mapping_lower.endswith(".yaml") or mapping_lower.endswith(".yml"):
            raise PipelineGenerationError(
                f"output_files[{file_type}]: mapping {mapping!r} looks like "
                f"a multi-record umbrella (.yaml/.yml) but multi_record is "
                f"false. Set multi_record: true or point at a flat mapping "
                f"JSON (see ADR 0005)."
            )

    return {
        "source_def_name": f"{source}__output__{file_type}",
        "file_type": file_type,
        "mapping": mapping,
        "rules": rules,
        "multi_record": multi_record,
        # Java-generated output (resolved by the wrapper from the glob).
        "java_output_path": f"{output_root}/{glob}",
        # L3 reference: pinned golden baseline.
        "baseline_path": f"{baseline_root}/{file_type}.txt",
        "tolerance": entry.get("tolerance"),
    }


# --------------------------------------------------------------------------- #
# Gate builders
# --------------------------------------------------------------------------- #


def _build_gate(
    *,
    gate_name: str,
    blocking: bool,
    input_files: List[Dict[str, Any]],
    output_files: List[Dict[str, Any]],
    audit_schema: str,
) -> Dict[str, Any]:
    """Build a fully-materialized Gate dict for the requested gate name.

    Every step lands with absolute, concrete paths. ``{run_id}`` is the only
    template token the runner substitutes at execution time, where any step
    path carries it.
    """
    if gate_name == "file_to_staging":
        steps = _steps_file_to_staging(input_files)
        stage = "input"
        description = (
            "Compare each input file against an extract of its target staging "
            "table to verify the Java load step landed every row faithfully."
        )
    elif gate_name == "L1_structural":
        steps = _steps_l1_structural(output_files)
        stage = "output"
        description = (
            "Structural validation of each Java-generated output file against "
            "its mapping and rules (fixed-width widths, valid values, "
            "business rules)."
        )
    elif gate_name == "L3_baseline_diff":
        steps = _steps_l3_baseline_diff(output_files)
        stage = "output"
        description = (
            "Compare the Java-generated output to the pinned per-release "
            "golden baseline. Tolerance fields (timestamps, sequence numbers) "
            "are pre-filtered by the wrapper before this gate runs."
        )
    else:
        raise PipelineGenerationError(f"unsupported gate name: {gate_name}")

    return {
        "name": gate_name,
        "stage": stage,
        "description": description,
        # ``for_each`` stays empty: every step is already materialized
        # for a specific file, so the runner does not need to iterate.
        "for_each": "",
        "blocking": blocking,
        "steps": steps,
    }


def _steps_file_to_staging(
    input_files: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """One ``db_compare`` step per input file (file vs. staging table).

    The wrapper extracts staging rows via ``valdo extract`` before this
    gate runs; the resulting file lives at ``input_path``. The runner's
    ``db_compare`` step type accepts a SQL/table reference in ``query``
    and a file path in ``file``.
    """
    steps: List[Dict[str, Any]] = []
    for f in input_files:
        steps.append(
            {
                "type": "db_compare",
                "file": f["input_path"],
                "mapping": f["mapping"],
                "rules": "",
                "query": f["qualified_staging_table"],
                "key_columns": [],
                "thresholds": _threshold_block(f.get("thresholds")),
            }
        )
    return steps


def _steps_l1_structural(
    output_files: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """One L1 step per output file.

    Single-record entries emit ``type: validate``. Multi-record entries
    (those with ``multi_record: true`` in the source config) emit
    ``type: validate_multi_record`` per ADR 0005 so the runner dispatches
    to ``run_multi_record_validate_service`` against the umbrella YAML.
    L2 and L3 remain plain file-to-file ``compare`` steps in both cases.
    """
    steps: List[Dict[str, Any]] = []
    for f in output_files:
        if f.get("multi_record"):
            step_type = "validate_multi_record"
            # ``rules`` is intentionally empty for multi-record steps:
            # per-record rules live inside the umbrella YAML, not at the
            # pipeline-step level.
            rules = ""
        else:
            step_type = "validate"
            rules = f["rules"]
        steps.append(
            {
                "type": step_type,
                "file": f["java_output_path"],
                "mapping": f["mapping"],
                "rules": rules,
                "query": "",
                "key_columns": [],
                "thresholds": _threshold_block(f.get("tolerance")),
            }
        )
    return steps


def _steps_l3_baseline_diff(
    output_files: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """One ``compare`` step per output file: Java-generated vs. baseline.

    Tolerance-field filtering happens in the wrapper before this gate
    fires (M5 delivers ``ignore_field_filter.py``).
    """
    steps: List[Dict[str, Any]] = []
    for f in output_files:
        steps.append(
            {
                "type": "compare",
                "file": f["java_output_path"],
                "mapping": f["mapping"],
                "rules": "",
                "query": f["baseline_path"],
                "key_columns": [],
                "thresholds": _threshold_block(f.get("tolerance")),
            }
        )
    return steps


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _threshold_block(block: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Normalize a tolerance/thresholds block into ``ThresholdConfig`` shape.

    Missing fields default to ``-1`` (disabled) so the YAML round-trips
    cleanly through :class:`ThresholdConfig`. Negative values supplied by
    the user are preserved verbatim.
    """
    block = block or {}
    return {
        "max_error_pct": _coerce_number(block.get("max_error_pct"), -1),
        "max_errors": _coerce_int(block.get("max_errors"), -1),
        "min_rows": _coerce_int(block.get("min_rows"), -1),
    }


def _coerce_int(value: Any, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise PipelineGenerationError(f"expected integer, got {value!r}") from exc


def _coerce_number(value: Any, default: float) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise PipelineGenerationError(f"expected number, got {value!r}") from exc


def _require(d: Dict[str, Any], key: str, label: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise PipelineGenerationError(
            f"{label}: required field '{key}' is missing or empty"
        )
    return d[key]


def _validate_source_cfg(cfg: Dict[str, Any], source: str) -> None:
    if cfg.get("source") != source:
        raise PipelineGenerationError(
            f"source mismatch: file declares source='{cfg.get('source')}', "
            f"expected '{source}'"
        )
    for required in ("input_files", "output_files", "gates"):
        if required not in cfg:
            raise PipelineGenerationError(
                f"source '{source}' config is missing required section " f"'{required}'"
            )
    if not cfg["input_files"]:
        raise PipelineGenerationError(
            f"source '{source}' must declare at least one input_files entry"
        )
    if not cfg["output_files"]:
        raise PipelineGenerationError(
            f"source '{source}' must declare at least one output_files entry"
        )


# --------------------------------------------------------------------------- #
# YAML emission
# --------------------------------------------------------------------------- #


def render_yaml(pipeline_dict: Dict[str, Any]) -> str:
    """Serialize ``pipeline_dict`` to a stable, human-readable YAML string.

    The output:
      * preserves insertion order (Python 3.7+ dicts, ``sort_keys=False``)
      * uses block style (``default_flow_style=False``)
      * indents lists for readability
      * ends in a single trailing newline

    These are the properties the golden-file test depends on.
    """
    text = yaml.safe_dump(
        pipeline_dict,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=120,
        indent=2,
    )
    if not text.endswith("\n"):
        text += "\n"
    return text


def write_pipeline_file(
    pipeline_dict: Dict[str, Any], out_path: Path, *, check: bool = False
) -> bool:
    """Write the pipeline YAML to ``out_path``.

    When ``check`` is True, do NOT write — return ``True`` if the file
    already matches the rendered content, ``False`` otherwise.
    """
    rendered = render_yaml(pipeline_dict)
    if check:
        if not out_path.is_file():
            return False
        return out_path.read_text(encoding="utf-8") == rendered
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    return True


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="generate_pipeline_yaml",
        description=("Generate Valdo ETL pipeline YAML files from E2E source configs."),
    )
    p.add_argument(
        "--paths-yaml",
        default=_DEFAULT_PATHS_YAML,
        help=f"Path to paths.yml (default: {_DEFAULT_PATHS_YAML})",
    )
    p.add_argument(
        "--sources-dir",
        default=_DEFAULT_SOURCES_DIR,
        help=f"Per-source YAML directory (default: {_DEFAULT_SOURCES_DIR})",
    )
    p.add_argument(
        "--out-dir",
        default=_DEFAULT_OUT_DIR,
        help=f"Output directory (default: {_DEFAULT_OUT_DIR})",
    )
    p.add_argument(
        "--source",
        action="append",
        default=[],
        help=(
            "Source name to generate. May be repeated. "
            "Mutually exclusive with --all-sources."
        ),
    )
    p.add_argument(
        "--env",
        action="append",
        default=[],
        help=(
            "Env to generate for. May be repeated. "
            "Mutually exclusive with --all-envs."
        ),
    )
    p.add_argument(
        "--all-sources",
        action="store_true",
        help="Generate for every *.yml under --sources-dir.",
    )
    p.add_argument(
        "--all-envs",
        action="store_true",
        help="Generate for every env declared in paths.yml.",
    )
    p.add_argument(
        "--stdout",
        action="store_true",
        help="Write to stdout instead of files (requires single env/source).",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help=(
            "Do not write. Exit 0 if every target file is up-to-date, "
            "non-zero otherwise. Useful as a CI guardrail."
        ),
    )
    return p.parse_args(argv)


def _resolve_targets(
    args: argparse.Namespace, resolver: PathResolver
) -> Tuple[List[str], List[str]]:
    """Resolve (envs, sources) lists from CLI flags."""
    if args.all_envs and args.env:
        raise PipelineGenerationError("--all-envs and --env are mutually exclusive")
    if args.all_sources and args.source:
        raise PipelineGenerationError(
            "--all-sources and --source are mutually exclusive"
        )

    envs = resolver.known_envs() if args.all_envs else list(args.env)
    if not envs:
        raise PipelineGenerationError(
            "no envs selected. Pass --env <name> or --all-envs."
        )

    sources_dir = Path(args.sources_dir)
    if args.all_sources:
        sources = sorted(p.stem for p in sources_dir.glob("*.yml"))
    else:
        sources = list(args.source)
    if not sources:
        raise PipelineGenerationError(
            "no sources selected. Pass --source <name> or --all-sources."
        )
    return envs, sources


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    try:
        resolver = PathResolver.from_files(
            Path(args.paths_yaml), Path(args.sources_dir)
        )
    except PathResolverError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        envs, sources = _resolve_targets(args, resolver)
    except PipelineGenerationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.stdout and (len(envs) > 1 or len(sources) > 1):
        print(
            "error: --stdout requires exactly one env and one source",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(args.out_dir)
    all_clean = True
    for env in envs:
        for source in sources:
            try:
                pipeline = build_pipeline_dict(
                    env=env, source=source, resolver=resolver
                )
            except (PathResolverError, PipelineGenerationError) as exc:
                print(
                    f"error: failed to build pipeline for env={env} "
                    f"source={source}: {exc}",
                    file=sys.stderr,
                )
                return 2

            if args.stdout:
                sys.stdout.write(render_yaml(pipeline))
                continue

            out_path = out_dir / env / f"{source}.pipeline.yaml"
            try:
                ok = write_pipeline_file(pipeline, out_path, check=args.check)
            except OSError as exc:
                print(f"error: writing {out_path}: {exc}", file=sys.stderr)
                return 3
            if args.check:
                status = "up-to-date" if ok else "STALE"
                print(f"{status}: {out_path}")
                all_clean = all_clean and ok
            else:
                print(f"wrote {out_path}")

    if args.check and not all_clean:
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

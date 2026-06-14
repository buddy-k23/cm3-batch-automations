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
from pydantic import ValidationError

# Make the repo importable when invoked as a script.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.e2e_lib.path_resolver import (  # noqa: E402
    PathResolver,
    PathResolverError,
)
from src.pipeline.etl_config import (  # noqa: E402
    InputFileConfig,
    OutputFileConfig,
    SourceConfig,
    ThresholdsConfig,
    ToleranceConfig,
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
    ``yaml.safe_dump`` (via :func:`render_yaml`) serializes for the golden-
    file test. Field order is fixed so a diff against the golden file is
    meaningful.

    The raw source YAML is validated through
    :class:`~src.pipeline.etl_config.SourceConfig` (EA-S2) before pipeline
    construction so the generator has access to defaulted-vs-explicit field
    information for each ``input_files[]`` / ``output_files[]`` entry. The
    Pydantic model is also the single source of truth for default values
    emitted in ``# default: <field>=<value>`` comments by
    :func:`render_yaml`.

    The validated :class:`SourceConfig` is attached to the returned dict under
    the ``_source_config`` key so :func:`render_yaml` can read each entry's
    ``model_fields_set`` and decide which fields need a default comment. The
    key is stripped before serialization and never lands on disk.

    Args:
        env: One of the env names declared in ``paths.yml`` (e.g. ``sit``).
        source: The source identifier matching a YAML in ``sources_dir``.
        resolver: A ready :class:`PathResolver`.

    Returns:
        A dict compatible with :class:`PipelineDefinition.model_validate`
        plus the private ``_source_config`` key consumed by
        :func:`render_yaml`.

    Raises:
        PipelineGenerationError: If the source config is missing required
            sections, fails Pydantic validation, or if a referenced gate is
            not declared in the source.
    """
    src_cfg = resolver.source_config(source)
    _validate_source_cfg(src_cfg, source)

    # EA-S2: validate the raw source dict through the Pydantic model so we
    # can track which fields were explicit vs implicitly defaulted. The
    # validated model is the source of truth for both default detection
    # (model_fields_set) and the default values themselves (the model's
    # field defaults), so render_yaml can never drift from etl_config.py.
    try:
        validated_source = SourceConfig.model_validate(src_cfg)
    except ValidationError as exc:
        raise PipelineGenerationError(
            f"source '{source}' failed SourceConfig validation: {exc}"
        ) from exc

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
    # We consume the validated SourceConfig (Pydantic models) instead of
    # raw dicts so multi-record dispatch can use the inferred
    # ``is_multi_record`` computed field rather than re-checking the
    # mapping extension by hand.
    input_files = [
        _resolve_input_entry(
            entry,
            source=source,
            input_root=input_root,
            staging_schema=staging_schema,
        )
        for entry in validated_source.input_files
    ]
    output_files = [
        _resolve_output_entry(
            entry,
            source=source,
            output_root=output_root,
            baseline_root=baseline_root,
        )
        for entry in validated_source.output_files
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

    # EA-S2: ``_source_config`` is a private hand-off to render_yaml so it
    # can read each entry's ``model_fields_set`` and emit
    # ``# default: <field>=<value>`` comments for omitted fields. The key is
    # stripped from the dict before YAML serialization and never lands on
    # disk; ``PipelineDefinition.model_validate`` would ignore it anyway.
    return {
        "name": pipeline_name,
        "description": description,
        "sources": sources,
        "gates": gates,
        "_source_config": validated_source,
    }


# --------------------------------------------------------------------------- #
# Per-file path resolution
# --------------------------------------------------------------------------- #


def _resolve_input_entry(
    entry: InputFileConfig,
    *,
    source: str,
    input_root: str,
    staging_schema: str,
) -> Dict[str, Any]:
    """Materialize a single input_files entry into concrete paths.

    Takes a validated :class:`InputFileConfig` (EA-S2) rather than the raw
    dict; required fields are guaranteed present by Pydantic and the
    ``thresholds`` block carries its implicit defaults.
    """
    file_type = entry.file_type
    target_table = entry.target_staging_table
    qualified_table = (
        target_table if "." in target_table else f"{staging_schema}.{target_table}"
    )
    return {
        "source_def_name": f"{source}__input__{file_type}",
        "file_type": file_type,
        "mapping": entry.mapping,
        # Concrete file path. The glob is preserved so the wrapper script
        # can resolve the latest match on disk at run time; Valdo's existing
        # CLI surfaces accept globs.
        "input_path": f"{input_root}/{entry.glob}",
        "target_staging_table": target_table,
        "qualified_staging_table": qualified_table,
        # Dump the thresholds model to the legacy dict shape expected by
        # _threshold_block. Pass even when implicit so defaults flow through.
        "thresholds": entry.thresholds.model_dump(),
    }


def _resolve_output_entry(
    entry: OutputFileConfig,
    *,
    source: str,
    output_root: str,
    baseline_root: str,
) -> Dict[str, Any]:
    """Materialize a single output_files entry into concrete paths.

    Takes a validated :class:`OutputFileConfig` (EA-S2). Per ADR 0005 /
    EB-S1, multi-record dispatch is inferred from the mapping file
    extension via ``OutputFileConfig.is_multi_record`` -- the
    ``validate``/``validate_multi_record`` switch in
    :func:`_steps_l1_structural` reads that computed flag. Legacy
    ``multi_record:`` / ``discriminator_field:`` keys at the entry level
    are rejected by the model itself (no need for a duplicate fail-fast
    check here).
    """
    file_type = entry.file_type
    return {
        "source_def_name": f"{source}__output__{file_type}",
        "file_type": file_type,
        "mapping": entry.mapping,
        "rules": entry.rules,
        "multi_record": entry.is_multi_record,
        # Java-generated output (resolved by the wrapper from the glob).
        "java_output_path": f"{output_root}/{entry.glob}",
        # L3 reference: pinned golden baseline.
        "baseline_path": f"{baseline_root}/{file_type}.txt",
        # Dump the tolerance model to the legacy dict shape expected by
        # _threshold_block. Pass even when implicit so defaults flow through.
        "tolerance": entry.tolerance.model_dump(),
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
      * emits ``input_files:`` and ``output_files:`` informational manifest
        blocks (EA-S2) with ``# default: <field>=<value>`` comments next to
        every implicitly-defaulted boilerplate field, so an SRE reading the
        generated YAML sees the effective configuration without consulting
        the Pydantic source

    The manifest blocks are silently ignored by
    :class:`PipelineDefinition.model_validate` (Pydantic v2's default
    ``extra='ignore'`` behaviour); they exist purely for human debug.

    These are the properties the golden-file test depends on.
    """
    # EA-S2: lift the private SourceConfig out of the dict so it does not
    # land in the serialized YAML. The SourceConfig is the source of truth
    # for both the defaulted-vs-explicit set and the default values
    # themselves, used to render the manifest blocks below.
    source_cfg: Optional[SourceConfig] = pipeline_dict.get("_source_config")
    core_dict = {k: v for k, v in pipeline_dict.items() if k != "_source_config"}

    text = yaml.safe_dump(
        core_dict,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=120,
        indent=2,
    )
    if not text.endswith("\n"):
        text += "\n"

    if source_cfg is not None:
        manifest = _render_manifests_with_defaults(source_cfg)
        if manifest:
            text = _splice_manifests_after_description(text, manifest)
    return text


# --------------------------------------------------------------------------- #
# EA-S2: defaults-as-comments manifest emission
# --------------------------------------------------------------------------- #


# Canonical default values rendered into ``# default: <field>=<value>``
# comments. Read from the Pydantic models so they stay in lockstep with
# ``src/pipeline/etl_config.py``; any future default change in the model
# automatically updates the generated comments.
def _format_default_value(value: Any) -> str:
    """Format a Pydantic default value for inclusion in a YAML comment.

    Args:
        value: A scalar or list default value.

    Returns:
        A short, deterministic textual representation. Booleans become
        ``"true"``/``"false"`` (YAML convention, not Python ``True``/``False``),
        lists render as JSON-ish ``[]`` / ``[a, b]``, and floats render
        without trailing zeros where safe.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        if not value:
            return "[]"
        return "[" + ", ".join(_format_default_value(v) for v in value) + "]"
    if isinstance(value, str):
        return value
    return repr(value) if not isinstance(value, (int, float)) else str(value)


def _output_file_default_comments(entry: OutputFileConfig) -> List[str]:
    """Return ``# default: <field>=<value>`` comment lines for one entry.

    Inspects ``entry.model_fields_set`` (and the nested
    ``entry.tolerance.model_fields_set``) to decide which fields were
    implicitly defaulted in the source YAML. The values inserted in the
    comments are read from the Pydantic model's field defaults so the
    generator and ``etl_config.py`` can never drift apart.

    Args:
        entry: A validated :class:`OutputFileConfig` instance.

    Returns:
        A list of zero or more comment lines, each beginning with
        ``"# default: "``. Empty when every boilerplate field was explicitly
        declared in the source YAML.
    """
    lines: List[str] = []
    explicit = entry.model_fields_set
    field_defaults = OutputFileConfig.model_fields

    for field_name in ("strict_fixed_width", "strict_level"):
        if field_name not in explicit:
            # ``get_default(call_default_factory=True)`` returns the actual
            # default whether the field uses ``default=...`` or
            # ``default_factory=...``. The bare ``.default`` attribute
            # returns ``PydanticUndefined`` for default-factory fields.
            default = field_defaults[field_name].get_default(
                call_default_factory=True
            )
            lines.append(
                f"# default: {field_name}={_format_default_value(default)}"
            )

    # tolerance: if the entire block was omitted, every sub-field is implicit.
    # If the block was present, check each sub-field individually against the
    # nested ``tolerance.model_fields_set``. Access ``model_fields`` on the
    # class (instance access is deprecated in Pydantic 2.11+).
    tolerance_explicit = "tolerance" in explicit
    tol_fields_set = entry.tolerance.model_fields_set if tolerance_explicit else set()
    tol_defaults = ToleranceConfig.model_fields
    for tol_field in ("ignore_fields", "max_errors", "max_error_pct"):
        if not tolerance_explicit or tol_field not in tol_fields_set:
            default = tol_defaults[tol_field].get_default(
                call_default_factory=True
            )
            lines.append(
                f"# default: tolerance.{tol_field}={_format_default_value(default)}"
            )
    return lines


def _input_file_default_comments(entry: InputFileConfig) -> List[str]:
    """Return ``# default: <field>=<value>`` comment lines for one entry.

    Mirrors :func:`_output_file_default_comments` for the
    ``InputFileConfig`` shape. Currently only ``thresholds.max_errors``
    is implicitly defaulted; the function is structured for future
    additions.

    Args:
        entry: A validated :class:`InputFileConfig` instance.

    Returns:
        A list of zero or one comment lines (``# default:
        thresholds.max_errors=0`` when omitted; empty otherwise).
    """
    lines: List[str] = []
    explicit = entry.model_fields_set
    thresholds_explicit = "thresholds" in explicit
    thr_fields_set = (
        entry.thresholds.model_fields_set if thresholds_explicit else set()
    )
    thr_defaults = ThresholdsConfig.model_fields
    for thr_field in ("max_errors",):
        if not thresholds_explicit or thr_field not in thr_fields_set:
            default = thr_defaults[thr_field].get_default(
                call_default_factory=True
            )
            lines.append(
                f"# default: thresholds.{thr_field}={_format_default_value(default)}"
            )
    return lines


def _render_manifests_with_defaults(source_cfg: SourceConfig) -> str:
    """Render the ``input_files:`` and ``output_files:`` manifest blocks.

    Emits each entry's explicit fields verbatim and appends a
    ``# default: <field>=<value>`` comment for every implicitly-defaulted
    field. The blocks are informational only -- they let SREs see the
    effective configuration inside the generated pipeline YAML.

    Args:
        source_cfg: The validated :class:`SourceConfig` whose entries are
            to be rendered.

    Returns:
        A YAML fragment (already trailing-newline terminated) containing
        the two manifest blocks. Empty string when both ``input_files`` and
        ``output_files`` are empty (defensive; the pipeline generator
        already rejects empty sources upstream).
    """
    if not source_cfg.input_files and not source_cfg.output_files:
        return ""

    out: List[str] = []
    if source_cfg.input_files:
        out.append("input_files:")
        for entry in source_cfg.input_files:
            out.extend(_render_input_entry(entry))
    if source_cfg.output_files:
        out.append("output_files:")
        for entry in source_cfg.output_files:
            out.extend(_render_output_entry(entry))
    return "\n".join(out) + "\n"


def _render_input_entry(entry: InputFileConfig) -> List[str]:
    """Render one input_files entry as a list of YAML lines.

    Explicit fields are emitted as ``key: value`` pairs (one per line,
    quoted as YAML requires); implicit fields are emitted as
    ``# default: <field>=<value>`` comment lines at the end of the entry.

    Args:
        entry: A validated :class:`InputFileConfig` instance.

    Returns:
        An ordered list of YAML lines (no trailing newlines) starting with
        ``"  - file_type: ..."`` and ending with any default comments.
    """
    explicit = entry.model_fields_set
    lines: List[str] = [
        f"  - file_type: {entry.file_type}",
        f"    glob: {_yaml_scalar(entry.glob)}",
        f"    mapping: {_yaml_scalar(entry.mapping)}",
        f"    target_staging_table: {_yaml_scalar(entry.target_staging_table)}",
    ]
    # thresholds block: emit explicit values, then comments for omitted.
    if "thresholds" in explicit:
        thr_set = entry.thresholds.model_fields_set
        if thr_set:
            lines.append("    thresholds:")
            for thr_field in ("max_errors",):
                if thr_field in thr_set:
                    val = getattr(entry.thresholds, thr_field)
                    lines.append(f"      {thr_field}: {_format_yaml_value(val)}")
    for comment in _input_file_default_comments(entry):
        lines.append(f"    {comment}")
    return lines


def _render_output_entry(entry: OutputFileConfig) -> List[str]:
    """Render one output_files entry as a list of YAML lines.

    Explicit fields are emitted verbatim; implicit fields trail as
    ``# default: <field>=<value>`` comment lines. The
    ``# default: tolerance.*`` lines are emitted at the entry level (not
    nested under a ``tolerance:`` key) because YAML comments cannot belong
    to a key that does not exist in the document.

    Args:
        entry: A validated :class:`OutputFileConfig` instance.

    Returns:
        An ordered list of YAML lines starting with
        ``"  - file_type: ..."`` and ending with any default comments.
    """
    explicit = entry.model_fields_set
    lines: List[str] = [
        f"  - file_type: {entry.file_type}",
        f"    glob: {_yaml_scalar(entry.glob)}",
        f"    mapping: {_yaml_scalar(entry.mapping)}",
    ]
    if "rules" in explicit:
        lines.append(f"    rules: {_yaml_scalar(entry.rules)}")
    if "strict_fixed_width" in explicit:
        lines.append(
            f"    strict_fixed_width: {_format_yaml_value(entry.strict_fixed_width)}"
        )
    if "strict_level" in explicit:
        lines.append(f"    strict_level: {entry.strict_level}")
    # tolerance: render explicit sub-fields as a nested mapping, then add
    # default comments at the entry level for omitted sub-fields.
    if "tolerance" in explicit:
        tol_set = entry.tolerance.model_fields_set
        if tol_set:
            lines.append("    tolerance:")
            for tol_field in ("ignore_fields", "max_errors", "max_error_pct"):
                if tol_field in tol_set:
                    val = getattr(entry.tolerance, tol_field)
                    lines.append(f"      {tol_field}: {_format_yaml_value(val)}")
    for comment in _output_file_default_comments(entry):
        lines.append(f"    {comment}")
    return lines


def _yaml_scalar(value: str) -> str:
    """Format a string scalar for safe inclusion in the hand-rendered YAML.

    The hand-rendered manifest blocks need scalar formatting that matches
    what ``yaml.safe_dump`` would produce, so the round-trip test passes.
    Empty strings render as ``''`` (YAML's explicit empty scalar). All
    other strings round-trip via ``yaml.safe_dump`` and the result has
    the trailing newline stripped.

    Args:
        value: The scalar string value.

    Returns:
        A YAML-safe string representation (may include quotes).
    """
    if value == "":
        return "''"
    # Round-trip via yaml.safe_dump so glob patterns / paths / etc. are
    # quoted exactly the way yaml.safe_dump quotes them elsewhere in the
    # rendered text. Strip the trailing newline.
    dumped = yaml.safe_dump(value, default_flow_style=True).rstrip("\n")
    # safe_dump wraps scalars in flow style as e.g. ``'foo'\n`` -- when the
    # scalar needs no quoting, it emits ``foo\n...\n`` (with a document end
    # marker on the next line) for some inputs; trim that.
    if dumped.endswith("\n..."):
        dumped = dumped[: -len("\n...")]
    return dumped


def _format_yaml_value(value: Any) -> str:
    """Format a non-string scalar/list for the hand-rendered manifest.

    Booleans become YAML ``true``/``false``; lists round-trip via
    ``yaml.safe_dump`` so the formatting matches what the rest of the
    rendered YAML uses; numbers are stringified directly.

    Args:
        value: The value to format.

    Returns:
        A YAML-safe representation suitable for placement after ``key: ``.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        # Flow style for short lists matches our manifest aesthetic; safe
        # for empty + simple scalars.
        if not value:
            return "[]"
        return yaml.safe_dump(
            value, default_flow_style=True, width=120
        ).rstrip("\n")
    return str(value)


def _splice_manifests_after_description(yaml_text: str, manifest_text: str) -> str:
    """Insert the manifest blocks immediately after the ``description:`` line.

    Placing the manifest at the top of the generated YAML makes the
    effective per-file configuration the first thing an SRE sees when they
    open the file to debug a run. The manifest is anchored after the
    ``description:`` line so the YAML's top-level key order remains
    deterministic.

    Args:
        yaml_text: The serialized core pipeline YAML produced by
            ``yaml.safe_dump``.
        manifest_text: The hand-rendered manifest YAML fragment ending in
            a single newline.

    Returns:
        The combined YAML text with the manifest spliced in. If a
        ``description:`` block is not found (defensive guard), the manifest
        is prepended to the output instead.
    """
    lines = yaml_text.splitlines(keepends=True)
    # The description value can wrap across multiple lines because
    # yaml.safe_dump folds long values; find the END of the description
    # block, defined as the next line that begins with a non-space char
    # at column 0 (i.e. the next top-level key, typically ``sources:``).
    splice_after_idx: Optional[int] = None
    in_description = False
    for idx, line in enumerate(lines):
        if line.startswith("description:"):
            in_description = True
            continue
        if in_description and line and not line[0].isspace():
            splice_after_idx = idx
            break
    if splice_after_idx is None:
        # Fallback: prepend.
        return manifest_text + yaml_text
    head = "".join(lines[:splice_after_idx])
    tail = "".join(lines[splice_after_idx:])
    return head + manifest_text + tail


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

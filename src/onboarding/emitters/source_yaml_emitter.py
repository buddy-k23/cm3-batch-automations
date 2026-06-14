"""Source YAML emitter (EC-S3).

Convert a parsed :class:`~src.onboarding.models.OnboardingWorkbook` into
the per-source YAML text written under ``config/e2e/sources/<SOURCE>.yml``.

Public API
----------
    >>> from src.onboarding.workbook_reader import read_workbook
    >>> from src.onboarding.emitters.source_yaml_emitter import emit_source_yaml
    >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
    >>> yaml_text = emit_source_yaml(wb)

Design contract (every Sprint 1 contract is honoured)
-----------------------------------------------------
* **EA-S1 defaults are NOT written.** The emitter never emits
  ``strict_fixed_width: true``, ``strict_level: all``,
  ``tolerance.max_errors: 0``, ``tolerance.max_error_pct: 0.0``,
  ``tolerance.ignore_fields: []``, or ``thresholds.max_errors: 0``.
  Default-equal values are simply omitted; the EA-S2 generator restores
  them as ``# default: <k>=<v>`` comments in the downstream pipeline YAML
  for SRE traceability.
* **EA-S3 surgical strip honoured.** If an output file's
  ``tolerance.ignore_fields`` is the only non-default sibling, only that
  sub-field is emitted under ``tolerance:`` -- the default
  ``max_errors`` / ``max_error_pct`` siblings are dropped. Same surgical
  logic for ``thresholds:``.
* **EB-S1 multi-record convention.** The emitter never writes
  ``multi_record:`` or ``discriminator_field:`` keys on output entries.
  Dispatch is inferred from the mapping path's extension
  (``.yaml`` -> umbrella, ``.json`` -> flat).
* **EB-S2 guardrail compliance.** The emitted YAML passes the
  ``tests/unit/test_source_yaml_guardrails.py`` per-file check.

Mapping/rules path conventions
------------------------------
The emitter derives mapping and rules paths from the workbook rather
than carrying them as explicit cells (matches the convention the
committed SHAW.yml uses):

    * Flat mapping (``mapping_sheet`` = ``"<FT>_Mapping"``)
      -> ``config/mappings/<SOURCE>_<FILE_TYPE>.json``.
    * Umbrella mapping (``mapping_sheet`` = ``"(umbrella)"``)
      -> ``config/mappings/<SOURCE>_<FILE_TYPE>.yaml``.
    * Flat rules  (``rules_sheet``  = ``"<FT>_Rules"``)
      -> ``config/rules/<SOURCE>_<FILE_TYPE>.json``.
    * Umbrella rules  (``rules_sheet`` = ``"(umbrella)"``)
      -> ``""`` (empty string; per-record rules live inside the
      umbrella mapping YAML).
    * Blank ``rules_sheet`` (``""``) on a flat output also emits ``""``.

Gates block
-----------
The seven canonical gates are emitted in the order established by the
committed SHAW.yml. Only ``load_step`` and ``generate_step`` derive
``invoke_java`` from the workbook; the other five hardcode
``invoke_java: false`` per the gate semantics established in Sprint 1
(``file_to_staging`` is internal; L1 / L2b / L3 are pure-Python
validators; ``multi_record_report`` is a renderer).

YAML emission style
-------------------
Top-level keys, ``input_files[]``, and ``output_files[]`` entries are
block-style with preserved key order (``sort_keys=False``). The per-gate
``{ blocking: ..., invoke_java: ... }`` mappings are emitted flow-style
to match the committed SHAW.yml shape. This is achieved with a custom
``yaml.SafeDumper`` subclass that intercepts dict representation for
the gate sub-mappings only.
"""

from __future__ import annotations

from typing import Any

import yaml
from pydantic import ValidationError

from src.onboarding.emitters import EmitterError
from src.onboarding.models import (
    InputFileSpec,
    OnboardingWorkbook,
    OutputFileSpec,
    SourceInfo,
)
from src.pipeline.etl_config import (
    SourceConfig,
    ThresholdsConfig,
    ToleranceConfig,
)

# ---------------------------------------------------------------------------
# Default-value lookups -- read from the Pydantic models, never hardcoded.
# Mirrors the EB-S2 guardrail's approach so changing a default in
# ``etl_config.py`` automatically flows through to the emitter's
# omit-when-default logic.
# ---------------------------------------------------------------------------

_TOLERANCE_DEFAULT_MAX_ERRORS: int = ToleranceConfig.model_fields["max_errors"].default
_TOLERANCE_DEFAULT_MAX_ERROR_PCT: float = ToleranceConfig.model_fields[
    "max_error_pct"
].default
_THRESHOLDS_DEFAULT_MAX_ERRORS: int = ThresholdsConfig.model_fields[
    "max_errors"
].default

# Sentinel sheet-name token that means "the umbrella mapping/rules YAML
# contains the per-record entries; this output is multi-record."
# Mirrors the EC-S1 / EC-S2 convention -- see
# templates/source_onboarding_template_README.md.
_UMBRELLA_SHEET_TOKEN = "(umbrella)"


# ---------------------------------------------------------------------------
# Flow-style sentinel + dumper.
# ---------------------------------------------------------------------------


class _FlowDict(dict):
    """Marker subclass for dicts that must emit flow-style.

    The custom dumper (:class:`_SourceYamlDumper`) routes instances of
    this subclass through ``represent_dict(..., flow_style=True)``. All
    other ``dict`` instances render block-style.

    Used exclusively for the per-gate ``{ blocking: ..., invoke_java: ... }``
    sub-mappings inside the ``gates:`` block, matching the committed
    SHAW.yml shape.
    """


class _SourceYamlDumper(yaml.SafeDumper):
    """``SafeDumper`` subclass that honours :class:`_FlowDict` instances.

    Plain ``dict`` instances are emitted block-style (matches the
    project's existing ``yaml.safe_dump(..., default_flow_style=False)``
    convention). :class:`_FlowDict` instances are emitted flow-style.
    """


def _represent_flow_dict(dumper: yaml.SafeDumper, data: _FlowDict) -> yaml.MappingNode:
    """Represent a :class:`_FlowDict` as a flow-style YAML mapping.

    Args:
        dumper: The active ``SafeDumper`` instance.
        data: The flow-style dict to emit.

    Returns:
        A ``yaml.MappingNode`` configured with ``flow_style=True`` so the
        downstream emitter renders it as ``{ k: v, ... }`` on one line.
    """
    return dumper.represent_mapping(
        "tag:yaml.org,2002:map", data.items(), flow_style=True
    )


_SourceYamlDumper.add_representer(_FlowDict, _represent_flow_dict)


# ---------------------------------------------------------------------------
# Source-info helpers.
# ---------------------------------------------------------------------------


def _build_java_scripts_block(source: SourceInfo) -> dict[str, str]:
    """Build the ``java_scripts:`` mapping from a :class:`SourceInfo`.

    Always includes ``load:``. ``generate:`` is included only when the
    workbook carries a non-empty ``java_generate_script`` (SHAW has no
    generate phase this iteration, so the key is omitted).

    Args:
        source: The parsed ``Source`` sheet row.

    Returns:
        A dict (block-style on emission) with ``load`` and optionally
        ``generate`` keys.
    """
    block: dict[str, str] = {"load": source.java_load_script}
    if source.java_generate_script:
        block["generate"] = source.java_generate_script
    return block


def _derive_staging_tables(input_files: list[InputFileSpec]) -> list[str]:
    """Deduplicate target staging tables from input files, preserving order.

    The committed SHAW.yml lists staging tables in input-file order with
    duplicates collapsed. Two input files writing into the same table
    (rare but legal) yield a single entry in the staging_tables list.

    Args:
        input_files: Ordered list of input-file specs from the workbook.

    Returns:
        Deduplicated list of staging-table names, preserving first-seen
        order from ``input_files``.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for spec in input_files:
        table = spec.target_staging_table
        if table and table not in seen:
            seen.add(table)
            ordered.append(table)
    return ordered


# ---------------------------------------------------------------------------
# Input/output entry builders.
# ---------------------------------------------------------------------------


def _input_mapping_path(source_code: str, file_type: str) -> str:
    """Compute the mapping JSON path for a flat input file.

    Input files are always flat (no umbrella convention for inputs in
    Sprint 2). Path layout matches the committed SHAW.yml:
    ``config/mappings/<SOURCE>_<FILE_TYPE>.json``.

    Args:
        source_code: The source code (e.g. ``"SHAW"``).
        file_type: The input-file file type (e.g. ``"COLLATERAL_MASTER"``).

    Returns:
        The relative mapping JSON path.
    """
    return f"config/mappings/{source_code}_{file_type}.json"


def _output_mapping_path(source_code: str, spec: OutputFileSpec) -> str:
    """Compute the mapping path for an output file.

    Per the EC-S1/EC-S2 convention, the ``mapping_sheet`` cell carries
    either:
        * ``"(umbrella)"`` -> ``config/mappings/<SOURCE>_<FT>.yaml``
          (multi-record dispatch is then inferred from the extension per
          ADR 0005 / EB-S1).
        * any other value -> ``config/mappings/<SOURCE>_<FT>.json``
          (flat single-record output).

    Args:
        source_code: The source code (e.g. ``"SHAW"``).
        spec: The output-file spec from the workbook.

    Returns:
        The relative mapping path with the extension chosen from
        ``spec.is_multi_record``.
    """
    extension = "yaml" if spec.is_multi_record else "json"
    return f"config/mappings/{source_code}_{spec.file_type}.{extension}"


def _output_rules_path(source_code: str, spec: OutputFileSpec) -> str:
    """Compute the rules JSON path for an output file (or ``""``).

    Per the SHAW.yml convention:

        * ``rules_sheet == "(umbrella)"`` -> ``""`` (per-record rules
          live inside the umbrella mapping YAML).
        * ``rules_sheet == ""``           -> ``""`` (no rules apply).
        * any other value                 -> ``config/rules/<SOURCE>_<FT>.json``.

    Args:
        source_code: The source code.
        spec: The output-file spec from the workbook.

    Returns:
        The relative rules JSON path or ``""``.
    """
    if not spec.rules_sheet or spec.rules_sheet == _UMBRELLA_SHEET_TOKEN:
        return ""
    return f"config/rules/{source_code}_{spec.file_type}.json"


def _build_input_entry(source_code: str, spec: InputFileSpec) -> dict[str, Any]:
    """Build one ``input_files[]`` entry dict.

    Key order matches the committed SHAW.yml:
    ``file_type, glob, mapping, target_staging_table[, thresholds]``.

    ``thresholds:`` is omitted unless ``thresholds_max_errors`` is set
    AND non-default. When emitted, it carries ONLY non-default sub-fields
    (currently just ``max_errors``).

    Args:
        source_code: The source code (used to compute the mapping path).
        spec: The input-file spec from the workbook.

    Returns:
        Ordered dict (Python 3.7+ dict preserves insertion order) ready
        for ``yaml.dump``.
    """
    entry: dict[str, Any] = {
        "file_type": spec.file_type,
        "glob": spec.glob,
        "mapping": _input_mapping_path(source_code, spec.file_type),
        "target_staging_table": spec.target_staging_table,
    }
    thresholds = _build_thresholds_block(spec)
    if thresholds is not None:
        entry["thresholds"] = thresholds
    return entry


def _build_thresholds_block(spec: InputFileSpec) -> dict[str, Any] | None:
    """Build the ``thresholds:`` sub-block for an input entry, or ``None``.

    Returns ``None`` when the only present value equals the Pydantic
    default; the EB-S2 guardrail rejects default-equal sub-fields, so
    omitting the block entirely is the correct behaviour.

    Args:
        spec: The input-file spec from the workbook.

    Returns:
        A dict with only the non-default sub-fields, or ``None`` to omit
        the ``thresholds:`` key entirely.
    """
    sub: dict[str, Any] = {}
    if (
        spec.thresholds_max_errors is not None
        and spec.thresholds_max_errors != _THRESHOLDS_DEFAULT_MAX_ERRORS
    ):
        sub["max_errors"] = spec.thresholds_max_errors
    return sub if sub else None


def _build_output_entry(source_code: str, spec: OutputFileSpec) -> dict[str, Any]:
    """Build one ``output_files[]`` entry dict.

    Key order matches the committed SHAW.yml:
    ``file_type, glob, mapping, rules[, tolerance]``.

    Never emits ``multi_record``, ``discriminator_field``,
    ``strict_fixed_width``, or ``strict_level`` (all banned by EB-S1 +
    EA-S3 / EB-S2). ``tolerance:`` is only emitted when at least one
    sub-field is non-default; when emitted, only the non-default sub-
    fields appear (surgical strip preserved).

    Args:
        source_code: The source code (used to compute mapping + rules paths).
        spec: The output-file spec from the workbook.

    Returns:
        Ordered dict ready for ``yaml.dump``.
    """
    entry: dict[str, Any] = {
        "file_type": spec.file_type,
        "glob": spec.glob,
        "mapping": _output_mapping_path(source_code, spec),
        "rules": _output_rules_path(source_code, spec),
    }
    tolerance = _build_tolerance_block(spec)
    if tolerance is not None:
        entry["tolerance"] = tolerance
    return entry


def _build_tolerance_block(spec: OutputFileSpec) -> dict[str, Any] | None:
    """Build the ``tolerance:`` sub-block for an output entry, or ``None``.

    Each sub-field is included only when it is set on the workbook AND
    differs from the Pydantic default. When all three sub-fields are
    default-equal (or unset), returns ``None`` so the caller omits the
    ``tolerance:`` key entirely -- matching the EB-S2 guardrail's surgical-
    strip contract.

    Special case: an empty ``tolerance_ignore_fields`` list ``[]``
    coming from the workbook equals the Pydantic default and is dropped.
    The workbook reader projects blank cells to ``None``, not ``[]``,
    so a list-valued cell with values is always a real override.

    Args:
        spec: The output-file spec from the workbook.

    Returns:
        A dict with only the non-default sub-fields, or ``None`` to omit
        the ``tolerance:`` key entirely.
    """
    sub: dict[str, Any] = {}

    if (
        spec.tolerance_ignore_fields is not None
        and spec.tolerance_ignore_fields != []
    ):
        sub["ignore_fields"] = list(spec.tolerance_ignore_fields)

    if (
        spec.tolerance_max_errors is not None
        and spec.tolerance_max_errors != _TOLERANCE_DEFAULT_MAX_ERRORS
    ):
        sub["max_errors"] = spec.tolerance_max_errors

    if (
        spec.tolerance_max_error_pct is not None
        and spec.tolerance_max_error_pct != _TOLERANCE_DEFAULT_MAX_ERROR_PCT
    ):
        sub["max_error_pct"] = spec.tolerance_max_error_pct

    return sub if sub else None


# ---------------------------------------------------------------------------
# Gates block.
# ---------------------------------------------------------------------------


def _build_gates_block(source: SourceInfo) -> dict[str, _FlowDict]:
    """Build the ``gates:`` block from a :class:`SourceInfo`.

    The 7 canonical gates are emitted in the order established by the
    committed SHAW.yml:

        1. ``load_step``        — derives both ``blocking`` and
           ``invoke_java`` from the workbook.
        2. ``file_to_staging``  — internal; ``invoke_java: false``
           hardcoded.
        3. ``generate_step``    — derives both from the workbook.
        4. ``L1_structural``    — pure-Python; ``invoke_java: false``.
        5. ``L3_baseline_diff`` — pure-Python; ``invoke_java: false``.
        6. ``L2b_sql_truth``    — orchestrator-driven; ``invoke_java: false``.
        7. ``multi_record_report`` — renderer; ``invoke_java: false``.

    Each per-gate sub-dict is wrapped in :class:`_FlowDict` so the
    custom dumper renders it as ``{ blocking: ..., invoke_java: ... }``.

    Args:
        source: The parsed ``Source`` sheet row.

    Returns:
        A regular (block-style) ``dict`` whose values are
        :class:`_FlowDict` instances (flow-style on emission).
    """
    return {
        "load_step": _FlowDict(
            blocking=source.gate_load_blocking,
            invoke_java=source.gate_load_invoke_java,
        ),
        "file_to_staging": _FlowDict(
            blocking=source.gate_f2s_blocking,
            invoke_java=False,
        ),
        "generate_step": _FlowDict(
            blocking=source.gate_generate_blocking,
            invoke_java=source.gate_generate_invoke_java,
        ),
        "L1_structural": _FlowDict(
            blocking=source.gate_l1_blocking,
            invoke_java=False,
        ),
        "L3_baseline_diff": _FlowDict(
            blocking=source.gate_l3_blocking,
            invoke_java=False,
        ),
        "L2b_sql_truth": _FlowDict(
            blocking=source.gate_l2b_blocking,
            invoke_java=False,
        ),
        "multi_record_report": _FlowDict(
            blocking=source.gate_mr_report_blocking,
            invoke_java=False,
        ),
    }


# ---------------------------------------------------------------------------
# Top-level document assembly.
# ---------------------------------------------------------------------------


def _build_document(workbook: OnboardingWorkbook) -> dict[str, Any]:
    """Assemble the full top-level YAML document from an :class:`OnboardingWorkbook`.

    Top-level key order matches the committed SHAW.yml:

        schema_version, source, release_tag, description, staging_schema,
        output_root, java_scripts, staging_tables, input_files,
        output_files, gates

    ``description`` is included because it is part of the data the
    workbook carries; the committed SHAW.yml uses it as the
    free-text human-readable iteration label.

    Args:
        workbook: The parsed workbook tree from EC-S2.

    Returns:
        An ordered dict ready for ``yaml.dump``.
    """
    src = workbook.source
    source_code = src.source_code

    return {
        "schema_version": src.schema_version,
        "source": source_code,
        "release_tag": src.release_tag,
        "description": src.description,
        "staging_schema": src.staging_schema,
        "output_root": src.output_root,
        "java_scripts": _build_java_scripts_block(src),
        "staging_tables": _derive_staging_tables(workbook.input_files),
        "input_files": [
            _build_input_entry(source_code, spec) for spec in workbook.input_files
        ],
        "output_files": [
            _build_output_entry(source_code, spec) for spec in workbook.output_files
        ],
        "gates": _build_gates_block(src),
    }


def _validate_through_source_config(document: dict[str, Any]) -> None:
    """Run the assembled document through :class:`SourceConfig.model_validate`.

    Catches schema violations the emitter cannot detect locally (e.g. a
    blank ``source_code`` propagated into ``SourceConfig.source``). The
    Pydantic ``ValidationError`` is chained as ``__cause__`` so the BA
    sees the underlying message without losing emitter-layer context.

    Args:
        document: The assembled top-level dict from :func:`_build_document`.

    Raises:
        EmitterError: If ``SourceConfig.model_validate`` rejects the
            document. The original ``ValidationError`` is chained.
    """
    try:
        SourceConfig.model_validate(document)
    except ValidationError as exc:
        raise EmitterError(
            "Emitted source YAML failed SourceConfig validation. "
            "Fix the workbook cells flagged below and re-run the emitter.\n"
            f"{exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Public emitter class + module-level convenience function.
# ---------------------------------------------------------------------------


class SourceYamlEmitter:
    """Emit a source YAML text from a parsed onboarding workbook.

    Stateless across calls. Subclassable for testing (e.g. to inject a
    fake validator), but production callers should use the module-level
    :func:`emit_source_yaml` helper.

    Usage:

        >>> from src.onboarding.workbook_reader import read_workbook
        >>> from src.onboarding.emitters.source_yaml_emitter import (
        ...     SourceYamlEmitter,
        ... )
        >>> wb = read_workbook("templates/SHAW_onboarding.xlsx")
        >>> text = SourceYamlEmitter().emit(wb)
    """

    def emit(self, workbook: OnboardingWorkbook) -> str:
        """Return the YAML text for ``workbook``.

        The returned string is the full source YAML in block style with
        flow-style ``gates:`` sub-mappings, validated through
        :class:`SourceConfig`. No trailing newline normalisation is
        applied here -- callers that need a specific newline policy can
        ``.rstrip()`` + ``+ "\\n"`` as needed.

        Args:
            workbook: The parsed ``OnboardingWorkbook`` tree from EC-S2.

        Returns:
            The source YAML as a single string.

        Raises:
            EmitterError: If the synthesised YAML fails
                :class:`SourceConfig.model_validate`.
        """
        document = _build_document(workbook)
        _validate_through_source_config(document)
        return yaml.dump(
            document,
            Dumper=_SourceYamlDumper,
            sort_keys=False,
            default_flow_style=False,
            allow_unicode=True,
            width=4096,  # avoid line-wrapping long globs/paths
        )


def emit_source_yaml(workbook: OnboardingWorkbook) -> str:
    """Module-level convenience wrapper around :meth:`SourceYamlEmitter.emit`.

    Args:
        workbook: The parsed ``OnboardingWorkbook`` tree from EC-S2.

    Returns:
        The source YAML as a single string.

    Raises:
        EmitterError: If the synthesised YAML fails ``SourceConfig`` validation.
    """
    return SourceYamlEmitter().emit(workbook)


__all__ = [
    "SourceYamlEmitter",
    "emit_source_yaml",
]

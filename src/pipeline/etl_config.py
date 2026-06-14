"""Pydantic configuration models for the ETL pipeline gate runner (issue #156).

Defines the data structures loaded from a pipeline YAML file:
  - SourceDefinition  — one source feed (mapping, rules, paths)
  - ThresholdConfig   — pass/fail thresholds for a gate step
  - GateStep          — a single validation action within a gate
  - Gate              — a named group of steps, optionally iterated per source
  - PipelineDefinition — top-level model for the entire pipeline YAML
"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field


class SourceDefinition(BaseModel):
    """Describes one source data feed in the pipeline.

    Attributes:
        name: Short machine-readable identifier (used for template expansion
            as ``{source.name}``).
        mapping: Path to the source mapping JSON file.
        rules: Optional path to a rules config JSON file.
        output_pattern: Optional glob pattern for the output file(s) produced
            from this source.
        input_path: Optional path to the raw source input file.
        target_mapping: Optional path to the target mapping JSON (used when
            validating transformed output against a different schema).
        staging_tables: Optional list of Oracle staging table names associated
            with this source.
    """

    name: str
    mapping: str
    rules: str = ""
    output_pattern: str = ""
    input_path: str = ""
    target_mapping: str = ""
    staging_tables: List[str] = Field(default_factory=list)


class ThresholdConfig(BaseModel):
    """Pass/fail thresholds applied to a gate step result.

    A value of ``-1`` for any threshold means "disabled" (no limit).

    Attributes:
        max_error_pct: Maximum acceptable error percentage
            (errors / total_rows * 100). Set to ``-1`` to disable.
        max_errors: Maximum acceptable absolute error count.
            Set to ``-1`` to disable.
        min_rows: Minimum acceptable row count in the processed file.
            Set to ``-1`` to disable.
    """

    max_error_pct: float = -1
    max_errors: int = -1
    min_rows: int = -1


class GateStep(BaseModel):
    """A single validation or comparison action within a gate.

    Attributes:
        type: Step type — one of ``"validate"``, ``"compare"``,
            ``"db_compare"``, or ``"reconcile"``.
        file: Path (or template string) to the data file to process.
        mapping: Path (or template string) to the mapping JSON.
        rules: Path (or template string) to the rules JSON.
        query: SQL SELECT statement or table name for ``db_compare`` steps.
        key_columns: List of column names used as join keys during comparison.
        thresholds: Pass/fail thresholds applied to this step's result.
    """

    type: str
    file: str = ""
    mapping: str = ""
    rules: str = ""
    query: str = ""
    key_columns: List[str] = Field(default_factory=list)
    thresholds: ThresholdConfig = Field(default_factory=ThresholdConfig)


class Gate(BaseModel):
    """A named validation gate containing one or more steps.

    Attributes:
        name: Human-readable gate identifier used in result reporting.
        stage: Optional ETL stage label (e.g. ``"input"``, ``"output"``).
        description: Optional human-readable description of what this gate
            validates.
        for_each: When set to ``"source"``, the gate's steps are executed once
            per entry in ``PipelineDefinition.sources`` with template
            variables expanded per source. An empty string means the steps
            run once without source context.
        blocking: When ``True`` (the default), a gate failure halts the
            pipeline immediately. When ``False``, the failure is recorded
            but execution continues to the next gate.
        steps: Ordered list of :class:`GateStep` objects to execute.
    """

    name: str
    stage: str = ""
    description: str = ""
    for_each: str = ""
    blocking: bool = True
    steps: List[GateStep] = Field(default_factory=list)


class PipelineDefinition(BaseModel):
    """Top-level model for an ETL pipeline YAML configuration file.

    Attributes:
        name: Unique pipeline identifier used in result metadata.
        description: Optional human-readable description of the pipeline.
        sources: Ordered list of :class:`SourceDefinition` objects describing
            the source feeds. Referenced by gates with ``for_each: source``.
        gates: Ordered list of :class:`Gate` objects to execute in sequence.
    """

    name: str
    description: str = ""
    sources: List[SourceDefinition] = Field(default_factory=list)
    gates: List[Gate] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# EA-S1: Per-source YAML schema models (config/e2e/sources/<SOURCE>.yml)
#
# These models describe the per-source YAML configuration consumed by the
# end-to-end batch testing harness (see scripts/e2e_lib/path_resolver.py).
# They are intentionally additive and do not modify any existing model above.
#
# Status: schema-only. As of EA-S1 these models are NOT wired into the
# production loader -- PathResolver.source_config() still returns raw dicts
# via yaml.safe_load(). They exist to:
#   * enforce implicit defaults (strict_fixed_width, strict_level,
#     tolerance.*, thresholds.max_errors) so future committed YAMLs can omit
#     boilerplate;
#   * provide the canonical contract for EA-S2 (defaults-as-comments) and
#     EA-S3 (strip boilerplate from committed YAMLs);
#   * enable a future loader migration to validated models.
#
# TODO(future-story): Migrate PathResolver.source_config() to return a
# SourceConfig instance instead of a raw dict, and tighten extra="allow" to
# extra="forbid" once the full source-YAML schema (filename_patterns,
# trigger_file, reconciliation, gates internals, etc.) is mapped.
# ---------------------------------------------------------------------------


class ToleranceConfig(BaseModel):
    """Per-output-file tolerance block for L1/L3 gate evaluation.

    Used inside :class:`OutputFileConfig` to express acceptable noise when
    comparing a generated output file against a baseline or against the
    mapping/rules contract. All three fields default to "zero tolerance".

    Attributes:
        ignore_fields: List of field names to exclude from comparison
            (e.g. timestamps, run IDs). Defaults to an empty list.
        max_errors: Maximum acceptable absolute error count before the gate
            fails. Defaults to ``0`` (zero tolerance).
        max_error_pct: Maximum acceptable error percentage
            (errors / total_rows * 100). Defaults to ``0.0`` (zero tolerance).
    """

    ignore_fields: List[str] = Field(default_factory=list)
    max_errors: int = 0
    max_error_pct: float = 0.0


class ThresholdsConfig(BaseModel):
    """Per-input-file thresholds block for the file_to_staging gate.

    Used inside :class:`InputFileConfig` to express acceptable load drift
    when ingesting a source delivery into its staging table.

    Attributes:
        max_errors: Maximum acceptable absolute error count before the gate
            fails. Defaults to ``0`` (zero tolerance for load drift).
    """

    max_errors: int = 0


class OutputFileConfig(BaseModel):
    """Configuration entry for one output file produced by a source batch.

    Drives the L1 structural and L3 baseline-diff gates. Boilerplate fields
    (``strict_fixed_width``, ``strict_level``, ``tolerance``) carry
    implicit defaults so source YAMLs can omit them when the defaults apply.

    Attributes:
        file_type: Short identifier (e.g. ``ATOCTRAN``) used for routing
            and report naming.
        glob: Filename glob (relative to the source's ``output_root``)
            matching the produced file(s).
        mapping: Path to the mapping JSON or umbrella YAML.
        rules: Optional path to the rules JSON. Empty when rules live inside
            an umbrella mapping or no rules apply.
        multi_record: Whether the file contains multiple record types
            dispatched by ``discriminator_field``. Defaults to ``False``.
        discriminator_field: Field name used to dispatch records when
            ``multi_record`` is ``True``. Informational when ``multi_record``
            is ``False``.
        strict_fixed_width: When ``True``, fixed-width fields are validated
            for exact length and format. Defaults to ``True``.
        strict_level: Strictness tier for fixed-width validation. One of
            ``"basic"``, ``"format"``, ``"all"``. Defaults to ``"all"``.
            (Legal values mirror ``valdo validate --strict-level``; see
            ``src/main.py`` and ``src/parsers/enhanced_validator.py``.)
        tolerance: Per-output-file tolerance block. Defaults to a zero-
            tolerance :class:`ToleranceConfig`.
    """

    file_type: str
    glob: str
    mapping: str
    rules: str = ""
    multi_record: bool = False
    discriminator_field: str = ""
    strict_fixed_width: bool = True
    strict_level: Literal["basic", "format", "all"] = "all"
    tolerance: ToleranceConfig = Field(default_factory=ToleranceConfig)


class InputFileConfig(BaseModel):
    """Configuration entry for one input file received from the source.

    Drives the file_to_staging gate. The ``thresholds`` block carries an
    implicit zero-tolerance default so source YAMLs can omit it.

    Attributes:
        file_type: Short identifier (e.g. ``COLLATERAL_MASTER``).
        glob: Filename glob matching the incoming file(s) on the watcher
            input path.
        mapping: Path to the mapping JSON used to validate the incoming
            file before loading into staging.
        target_staging_table: Bare table name (no schema qualifier) that
            this file is loaded into. The source-level ``staging_schema``
            qualifies it at resolution time.
        thresholds: Per-input-file thresholds block. Defaults to a zero-
            tolerance :class:`ThresholdsConfig`.
    """

    file_type: str
    glob: str
    mapping: str
    target_staging_table: str
    thresholds: ThresholdsConfig = Field(default_factory=ThresholdsConfig)


class SourceConfig(BaseModel):
    """Root model for a per-source YAML under ``config/e2e/sources/``.

    Models the structure of files such as ``config/e2e/sources/SHAW.yml``.
    All non-required top-level keys carry sensible defaults so a minimal
    YAML (e.g. just ``source: BFIN`` with one output entry) validates
    without forcing the author to spell out boilerplate.

    Note:
        This model is NOT yet wired into
        ``scripts/e2e_lib/path_resolver.py::PathResolver.source_config()``,
        which continues to return raw dicts via ``yaml.safe_load``. The
        model exists for schema enforcement in tests, future loader
        migration, and as the canonical contract for EA-S2
        (defaults-as-comments) and EA-S3 (strip boilerplate from committed
        YAMLs). Tightening ``extra="allow"`` to ``extra="forbid"`` is a
        planned follow-up once the full source-YAML schema (e.g.
        ``filename_patterns``, ``trigger_file``, ``reconciliation``, gate
        internals) is mapped.

    Attributes:
        source: Source code (e.g. ``"SHAW"``, ``"BFIN"``).
        schema_version: Per-source YAML schema version. Defaults to ``1``.
        release_tag: Optional release tag (e.g. ``"2026.M06"``).
        description: Optional human-readable description.
        staging_schema: Optional source-level override for the Oracle
            staging schema (e.g. ``"APP_INT"``).
        output_root: Optional source-level override for the output watcher
            root path.
        java_scripts: Mapping of gate names (e.g. ``"load"``, ``"generate"``)
            to shell-script paths invoked when ``invoke_java=true``.
        staging_tables: Bare staging-table names associated with the source.
        input_files: List of :class:`InputFileConfig` entries describing the
            files received from the source.
        output_files: List of :class:`OutputFileConfig` entries describing
            the files produced by the source batch.
        gates: Per-gate ``blocking`` / ``invoke_java`` policy block (kept
            as a raw dict pending a dedicated GatePolicy model).
    """

    source: str
    schema_version: int = 1
    release_tag: str = ""
    description: str = ""
    staging_schema: str = ""
    output_root: str = ""
    java_scripts: dict = Field(default_factory=dict)
    staging_tables: List[str] = Field(default_factory=list)
    input_files: List[InputFileConfig] = Field(default_factory=list)
    output_files: List[OutputFileConfig] = Field(default_factory=list)
    gates: dict = Field(default_factory=dict)

    model_config = {"extra": "allow"}

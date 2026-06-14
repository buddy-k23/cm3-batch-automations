"""Pydantic configuration models for the ETL pipeline gate runner (issue #156).

Defines the data structures loaded from a pipeline YAML file:
  - SourceDefinition  — one source feed (mapping, rules, paths)
  - ThresholdConfig   — pass/fail thresholds for a gate step
  - GateStep          — a single validation action within a gate
  - Gate              — a named group of steps, optionally iterated per source
  - PipelineDefinition — top-level model for the entire pipeline YAML
"""

from __future__ import annotations

from typing import List

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

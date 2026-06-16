"""ETL pipeline gate orchestrator (issue #156).

Executes a sequence of named validation gates read from a YAML config file.
Each gate may contain one or more steps of type ``validate``,
``validate_multi_record``, ``compare``, ``db_compare``, or ``reconcile``.
Gates can iterate over every declared source (``for_each: source``) and may
optionally block the pipeline on failure (``blocking: true``, the default).

Delegates all validation work to existing services:
  - validate              → :func:`~src.services.validate_service.run_validate_service`
  - validate_multi_record → :func:`~src.services.multi_record_validate_service.run_multi_record_validate_service`
    (added per ADR 0005 to dispatch multi-record fixed-width files to their
    umbrella ``MultiRecordConfig`` YAML)
  - compare               → :func:`~src.services.compare_service.run_compare_service`
  - db_compare            → :func:`~src.services.db_file_compare_service.compare_db_to_file`
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import yaml

from src.pipeline.etl_config import Gate, GateStep, PipelineDefinition, ThresholdConfig
from src.services.compare_service import run_compare_service
from src.services.db_file_compare_service import compare_db_to_file
from src.services.multi_record_validate_service import (
    run_multi_record_validate_service,
)
from src.services.validate_service import run_validate_service

logger = logging.getLogger(__name__)

# Sentinel value used in ThresholdConfig to mean "no threshold".
_THRESHOLD_DISABLED: int = -1

# Regex for template placeholders like {source.name}, {run_date}, {env}.
_PLACEHOLDER_RE = re.compile(r"\{([^}]+)\}")


class ETLPipelineRunner:
    """Orchestrates ETL pipeline validation gates from a YAML config file.

    The runner is a pure orchestrator — it does no validation itself.
    It reads a :class:`~src.pipeline.etl_config.PipelineDefinition` from
    YAML, expands template variables in step fields, delegates each step to
    the correct service, evaluates thresholds, and aggregates results.

    Example::

        runner = ETLPipelineRunner()
        result = runner.run_pipeline(
            "config/pipelines/nightly_etl.yaml",
            run_date="20260326",
            params={"env": "prod"},
        )
        print(result["status"])   # "passed" or "failed"
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_pipeline(
        self,
        config_path: str,
        run_date: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute all gates in the pipeline and return aggregate results.

        Gates are executed in the order they are declared.  For each gate:

        1. If ``gate.for_each == "source"``, steps are run once per source
           with template variables expanded for that source.
        2. Each step is executed by the correct service delegate.
        3. Threshold checks are applied to each step result.
        4. If ``gate.blocking is True`` and the gate failed, execution stops.

        Args:
            config_path: Absolute or relative path to the pipeline YAML file.
            run_date: Optional run date string (e.g. ``"20260326"``) injected
                into template expansions as ``{run_date}``.
            params: Optional dict of extra template variables (e.g.
                ``{"env": "staging"}``).

        Returns:
            Dict with keys:
            - ``pipeline_name``: str — pipeline name from config.
            - ``status``: str — ``"passed"`` or ``"failed"``.
            - ``gates``: list[dict] — per-gate result records.
            - ``started_at``: str — ISO-format UTC timestamp.
            - ``finished_at``: str — ISO-format UTC timestamp.

        Raises:
            FileNotFoundError: When *config_path* does not exist.
            yaml.YAMLError: When the YAML file is malformed.
        """
        started_at = _utcnow()
        effective_params: dict[str, Any] = dict(params or {})
        if run_date is not None:
            effective_params.setdefault("run_date", run_date)

        pipeline = self.load_config(config_path)
        logger.info("Pipeline '%s' starting (%d gates)", pipeline.name, len(pipeline.gates))

        gate_results: list[dict[str, Any]] = []
        pipeline_failed = False

        for gate in pipeline.gates:
            gate_result = self._run_gate(gate, pipeline.sources, effective_params)
            gate_results.append(gate_result)

            if gate_result["status"] == "failed" and gate.blocking:
                logger.warning(
                    "Pipeline '%s' halted — blocking gate '%s' failed",
                    pipeline.name,
                    gate.name,
                )
                pipeline_failed = True
                break

            if gate_result["status"] == "failed":
                pipeline_failed = True

        finished_at = _utcnow()
        overall = "failed" if pipeline_failed else "passed"
        logger.info("Pipeline '%s' finished — %s", pipeline.name, overall)

        return {
            "pipeline_name": pipeline.name,
            "status": overall,
            "gates": gate_results,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
        }

    def load_config(self, config_path: str) -> PipelineDefinition:
        """Load and parse a pipeline YAML file into a PipelineDefinition.

        Args:
            config_path: Path to the YAML config file.

        Returns:
            Validated :class:`~src.pipeline.etl_config.PipelineDefinition`
            instance.

        Raises:
            FileNotFoundError: When the file does not exist.
            yaml.YAMLError: When the YAML is malformed.
            pydantic.ValidationError: When required fields are missing.
        """
        path = Path(config_path)
        if not path.exists():
            raise FileNotFoundError(f"Pipeline config not found: {config_path}")

        with path.open(encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)

        return PipelineDefinition.model_validate(raw)

    def _expand_template(
        self,
        template: str,
        source: dict[str, Any],
        params: dict[str, Any],
    ) -> str:
        """Expand template placeholders in a string.

        Recognised placeholder forms:
        - ``{source.<field>}`` — resolved from the *source* dict using the
          field name after the dot (e.g. ``{source.name}`` → ``"customers"``).
        - ``{<key>}`` — resolved from the *params* dict.
        Unknown placeholders are left unchanged.

        Args:
            template: String that may contain ``{...}`` placeholders.
            source: Dict of source field values (name, mapping, input_path, …).
            params: Dict of extra parameters (run_date, env, …).

        Returns:
            Expanded string with all recognised placeholders replaced.
        """

        def _replace(match: re.Match) -> str:
            key = match.group(1)
            if key.startswith("source."):
                field = key[len("source."):]
                return str(source.get(field, match.group(0)))
            return str(params.get(key, match.group(0)))

        return _PLACEHOLDER_RE.sub(_replace, template)

    def _evaluate_thresholds(
        self,
        result: dict[str, Any],
        thresholds: ThresholdConfig,
    ) -> bool:
        """Check whether a step result satisfies the configured thresholds.

        Extracts ``total_rows`` and ``error_count`` from *result*, handling
        both flat validation results and the ``{workflow, compare}`` wrapper
        returned by ``db_file_compare_service``.

        A threshold of ``-1`` is disabled and always passes.  The
        ``max_error_pct`` check treats zero total rows as 0% error rate.

        Args:
            result: Dict returned by a service call.
            thresholds: :class:`~src.pipeline.etl_config.ThresholdConfig`
                describing the limits to enforce.

        Returns:
            ``True`` when all enabled thresholds are satisfied, else ``False``.
        """
        # Unwrap DB-compare result wrapper.
        if "compare" in result and "workflow" in result:
            compare = result["compare"]
            rows_with_diffs = compare.get(
                "rows_with_differences", compare.get("differences", 0)
            )
            total_rows = compare.get("total_rows_file1", compare.get("total_rows", 0))
            error_count = rows_with_diffs
        else:
            total_rows = result.get("total_rows", result.get("row_count", 0))
            error_count = result.get("error_count", len(result.get("errors", [])))

        # min_rows check.
        if thresholds.min_rows != _THRESHOLD_DISABLED:
            if total_rows < thresholds.min_rows:
                logger.debug(
                    "min_rows threshold breached: %d < %d", total_rows, thresholds.min_rows
                )
                return False

        # max_errors check.
        if thresholds.max_errors != _THRESHOLD_DISABLED:
            if error_count > thresholds.max_errors:
                logger.debug(
                    "max_errors threshold breached: %d > %d",
                    error_count,
                    thresholds.max_errors,
                )
                return False

        # max_error_pct check.
        if thresholds.max_error_pct != _THRESHOLD_DISABLED:
            pct = (error_count / total_rows * 100) if total_rows > 0 else 0.0
            if pct > thresholds.max_error_pct:
                logger.debug(
                    "max_error_pct threshold breached: %.2f%% > %.2f%%",
                    pct,
                    thresholds.max_error_pct,
                )
                return False

        return True

    def _step_handlers(self) -> dict[str, Callable[[GateStep], dict[str, Any]]]:
        """Build the step-type → handler registry for step dispatch.

        Replaces the former ``_execute_step`` type-switch (arch-review R-11):
        a new step type is registered by adding an entry here and a handler
        method, rather than editing a chain of ``if`` branches. Each handler
        takes the (template-expanded) :class:`~src.pipeline.etl_config.GateStep`
        and returns the raw service result dict.

        Returns:
            Mapping of step-type string to its bound handler method. The set of
            keys is the authoritative list of recognised step types.
        """
        return {
            "validate": self._execute_validate_step,
            "validate_multi_record": self._execute_validate_multi_record_step,
            "compare": self._execute_compare_step,
            "db_compare": self._execute_db_compare_step,
            "reconcile": self._execute_reconcile_step,
        }

    def _execute_step(
        self,
        step: GateStep,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Route a gate step to the correct service and return its result.

        Dispatch goes through the registry returned by
        :meth:`_step_handlers` (arch-review R-11) rather than a type-switch.

        Args:
            step: The :class:`~src.pipeline.etl_config.GateStep` to execute.
                All template variables in step fields must have been expanded
                before calling this method.
            params: Runtime parameters (currently unused by this method but
                available for future extension).

        Returns:
            Raw result dict from the delegated service.

        Raises:
            ValueError: When ``step.type`` is not a recognised step type.
        """
        handlers = self._step_handlers()
        handler = handlers.get(step.type)
        if handler is None:
            expected = ", ".join(handlers)
            raise ValueError(
                f"Unknown step type '{step.type}'. Expected one of: {expected}."
            )
        return handler(step)

    # ------------------------------------------------------------------
    # Step handlers (registered in _step_handlers)
    # ------------------------------------------------------------------

    def _execute_validate_step(self, step: GateStep) -> dict[str, Any]:
        """Execute a ``validate`` step via ``run_validate_service``.

        Args:
            step: The gate step with ``type == "validate"``.

        Returns:
            Raw result dict from the validate service.
        """
        return run_validate_service(
            file=step.file,
            mapping=step.mapping or None,
            rules=step.rules or None,
        )

    def _execute_validate_multi_record_step(self, step: GateStep) -> dict[str, Any]:
        """Execute a ``validate_multi_record`` step (ADR 0005).

        Dispatches a multi-record umbrella YAML through the existing service.
        ``step.mapping`` carries the umbrella path; ``step.rules`` is unused
        (per-record rules live inside the umbrella). The raw service result
        lacks a flat ``error_count``, which ``_evaluate_thresholds`` expects,
        so we synthesize one here from cross-type error-severity violations +
        per-type error counts. This keeps the threshold path uniform across
        step types without modifying ``_evaluate_thresholds``.

        Args:
            step: The gate step with ``type == "validate_multi_record"``.

        Returns:
            Raw service result dict augmented with a synthesized
            ``error_count``.
        """
        config_yaml = Path(step.mapping).read_text(encoding="utf-8")
        raw = run_multi_record_validate_service(
            file_path=step.file,
            config_yaml=config_yaml,
        )
        cross_type_errors = sum(
            1
            for v in raw.get("cross_type_violations", [])
            if v.get("severity", "error") == "error"
        )
        per_type_errors = sum(
            int(r.get("error_count", 0) or 0)
            for r in raw.get("record_type_results", {}).values()
        )
        return {
            **raw,
            "error_count": cross_type_errors + per_type_errors,
        }

    def _execute_compare_step(self, step: GateStep) -> dict[str, Any]:
        """Execute a ``compare`` step via ``run_compare_service``.

        Args:
            step: The gate step with ``type == "compare"``.

        Returns:
            Raw result dict from the compare service.
        """
        return run_compare_service(
            file1=step.file,
            file2=step.query or "",  # compare uses 'query' field as file2 when set
            keys=",".join(step.key_columns) if step.key_columns else None,
            mapping=step.mapping or None,
        )

    def _execute_db_compare_step(self, step: GateStep) -> dict[str, Any]:
        """Execute a ``db_compare`` step via ``compare_db_to_file``.

        Args:
            step: The gate step with ``type == "db_compare"``.

        Returns:
            Raw result dict from the DB-compare service.
        """
        mapping_config = _load_mapping_config(step.mapping)
        return compare_db_to_file(
            query_or_table=step.query,
            mapping_config=mapping_config,
            actual_file=step.file,
            key_columns=step.key_columns or None,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _run_gate(
        self,
        gate: Gate,
        sources: list,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Execute a single gate, optionally iterating over sources.

        Args:
            gate: The :class:`~src.pipeline.etl_config.Gate` to run.
            sources: List of :class:`~src.pipeline.etl_config.SourceDefinition`
                from the pipeline config.
            params: Runtime parameters for template expansion.

        Returns:
            Gate result dict with keys:
            - ``name``: gate name.
            - ``status``: ``"passed"`` or ``"failed"``.
            - ``steps``: list of step result dicts.
            - ``error``: error message string when an exception occurred.
        """
        logger.info("Gate '%s' starting", gate.name)
        step_results: list[dict[str, Any]] = []
        gate_failed = False
        gate_error: str | None = None

        if gate.for_each == "source":
            iterations = [src.model_dump() for src in sources]
        else:
            iterations = [{}]

        for source_ctx in iterations:
            for step in gate.steps:
                step_result = self._run_step_with_expansion(step, source_ctx, params)
                step_results.append(step_result)
                if step_result["status"] == "failed":
                    gate_failed = True
                    gate_error = step_result.get("error")

        overall = "failed" if gate_failed else "passed"
        logger.info("Gate '%s' finished — %s", gate.name, overall)

        result: dict[str, Any] = {
            "name": gate.name,
            "status": overall,
            "steps": step_results,
        }
        if gate_error:
            result["error"] = gate_error
        return result

    def _run_step_with_expansion(
        self,
        step: GateStep,
        source: dict[str, Any],
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Expand templates in a step's fields, execute, and check thresholds.

        Args:
            step: The template step definition (fields may contain
                ``{source.*}`` or ``{param}`` placeholders).
            source: Current source context dict for template expansion.
            params: Runtime parameters for template expansion.

        Returns:
            Step result dict with keys:
            - ``type``: step type string.
            - ``status``: ``"passed"`` or ``"failed"``.
            - ``result``: raw service result (omitted on exception).
            - ``error``: error message string (only on failure).
        """
        # Expand templates in step field strings.
        expanded = GateStep(
            type=step.type,
            file=self._expand_template(step.file, source, params),
            mapping=self._expand_template(step.mapping, source, params),
            rules=self._expand_template(step.rules, source, params),
            query=self._expand_template(step.query, source, params),
            key_columns=[
                self._expand_template(k, source, params) for k in step.key_columns
            ],
            thresholds=step.thresholds,
        )

        try:
            svc_result = self._execute_step(expanded, params)
        except Exception as exc:  # noqa: BLE001
            logger.error("Step '%s' raised: %s", step.type, exc)
            return {
                "type": step.type,
                "status": "failed",
                "error": str(exc),
            }

        threshold_ok = self._evaluate_thresholds(svc_result, expanded.thresholds)
        # A step fails if the service marks it invalid OR thresholds are breached.
        service_failed = _is_service_result_failed(svc_result)
        status = "failed" if (service_failed or not threshold_ok) else "passed"

        step_out: dict[str, Any] = {
            "type": step.type,
            "status": status,
            "result": svc_result,
        }
        if status == "failed":
            reasons = []
            if service_failed:
                reasons.append("service reported failure")
            if not threshold_ok:
                reasons.append("threshold breached")
            step_out["error"] = "; ".join(reasons)
        return step_out

    def _execute_reconcile_step(self, step: GateStep) -> dict[str, Any]:
        """Execute a reconcile step using OracleReconciler.

        Args:
            step: The gate step with ``type == "reconcile"`` and a populated
                ``mapping`` field pointing to a mapping JSON file.

        Returns:
            Raw reconciliation result dict from SchemaReconciler.

        Raises:
            ImportError: When Oracle database packages are not available.
        """
        from src.config.loader import ConfigLoader
        from src.config.mapping_parser import MappingParser
        from src.database.adapters.factory import get_database_adapter
        from src.database.reconciliation import SchemaReconciler

        loader = ConfigLoader()
        parser = MappingParser()
        mapping_dict = loader.load_mapping(step.mapping)
        mapping_doc = parser.parse(mapping_dict)
        # Adapter selected via DB_ADAPTER (oracle/postgresql/sqlite) per ADR 0022.
        adapter = get_database_adapter()
        reconciler = SchemaReconciler(adapter)
        return reconciler.reconcile_mapping(mapping_doc)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC datetime with timezone info.

    Returns:
        Timezone-aware datetime object for UTC now.
    """
    return datetime.now(tz=timezone.utc)


def _load_mapping_config(mapping_path: str) -> dict[str, Any]:
    """Load a mapping JSON file and return the parsed dict.

    Args:
        mapping_path: Path to the JSON mapping file.

    Returns:
        Parsed mapping dict.

    Raises:
        FileNotFoundError: When the file does not exist.
        json.JSONDecodeError: When the file is not valid JSON.
    """
    path = Path(mapping_path)
    with path.open(encoding="utf-8") as fh:
        return json.load(fh)


def _is_service_result_failed(result: dict[str, Any]) -> bool:
    """Determine whether a service result represents a failure.

    Handles three result shapes:
    - Validate service: checks ``result["valid"]`` (bool).
    - DB-compare service: checks ``result["workflow"]["status"]``.
    - Compare service: checks ``result["structure_compatible"]`` and
      ``rows_with_differences``.

    Args:
        result: Raw dict returned by any service call.

    Returns:
        ``True`` when the result indicates a failure, ``False`` otherwise.
    """
    # DB-compare wrapper
    if "workflow" in result and "compare" in result:
        return result["workflow"].get("status") != "passed"

    # Validate service
    if "valid" in result:
        return not result["valid"]

    # Compare service
    if "structure_compatible" in result:
        if not result.get("structure_compatible", True):
            return True
        rows_with_diffs = result.get(
            "rows_with_differences", result.get("differences", 0)
        )
        return bool(rows_with_diffs)

    return False

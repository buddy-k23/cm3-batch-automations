"""ETL-pipeline MCP tool implementation (S22-2, #439).

Wires the ``run_etl_pipeline`` MCP tool onto the existing Valdo ETL pipeline
orchestrator — the same :class:`~src.pipeline.etl_pipeline_runner.ETLPipelineRunner`
that the ``valdo run-etl-pipeline`` CLI command
(:func:`src.commands.etl_pipeline_command.run_etl_pipeline_command`) drives. Like
the S21 action tools (``mask_file`` / ``detect_drift`` / ``extract_table``) and the
S22-1 ``parse_file`` tool, this module is a THIN adapter — no pipeline logic lives
here. The gate sequencing, template-variable expansion, per-step threshold
evaluation, service delegation, and result aggregation all stay in the runner; the
tool registration itself stays in :mod:`src.mcp.server`.

The tool lets an agent run a multi-gate ETL validation pipeline (defined in a YAML
config) and read back the structured result — the per-gate pass/fail breakdown plus
the overall pipeline verdict — without shelling out to the CLI. The pipeline config
selects which gates run; a gate that exercises a database step extracts from
whichever backend ``DB_ADAPTER`` selects (ADR 0022). A genuine validation failure
(a gate or the pipeline reporting ``status == "failed"``) is a RESULT, not a tool
error — the caller inspects ``status`` to decide what to do next.

ToolError is reserved for caller-fixable problems: a missing/blank ``config``
argument, a config file that does not exist, malformed pipeline YAML, or an
unexpected runner failure. The response carries no secrets — only the config path
echoed back, the pipeline name/status, the per-gate records, and the run
timestamps the runner already emits.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from mcp.server.fastmcp.exceptions import ToolError

__all__ = [
    "run_etl_pipeline_payload",
    "RUN_ETL_PIPELINE_DESCRIPTION",
]

logger = logging.getLogger(__name__)


def _json_default(value: Any) -> Any:
    """Coerce a non-JSON-native scalar to a JSON-serialisable Python type.

    The runner aggregates raw service results that may embed numpy/pandas
    scalars (e.g. ``numpy.int64``, ``numpy.bool_``) which FastMCP cannot
    serialise over the MCP transport. Any object exposing ``.item()`` (the
    numpy/pandas scalar protocol) is unwrapped to its native Python value;
    anything else falls back to ``str`` so an unexpected type degrades to a
    readable string rather than crashing the transport.

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


def _to_json_native(result: Dict[str, Any]) -> Dict[str, Any]:
    """Round-trip a result dict through JSON to drop non-native scalar types.

    Keeps this module a thin adapter: the runner's logic and result SHAPE are
    untouched — we only normalise embedded numpy/pandas scalars (surfaced via
    :func:`_json_default`) to JSON-native types so FastMCP can serialise the
    response.

    Args:
        result: The aggregate result dict returned by the pipeline runner.

    Returns:
        An equivalent dict containing only JSON-native types.
    """
    return json.loads(json.dumps(result, default=_json_default))


def run_etl_pipeline_payload(
    config: str,
    run_date: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a multi-gate ETL validation pipeline and return its structured result.

    Thin adapter over the Valdo ETL pipeline orchestrator (the same code path
    :func:`src.commands.etl_pipeline_command.run_etl_pipeline_command` drives).
    The pipeline definition is loaded from *config*, every gate is executed in
    declared order, and the aggregate result is returned verbatim. No pipeline
    logic lives here; gate sequencing, template expansion, threshold evaluation,
    and service delegation all stay in
    :class:`~src.pipeline.etl_pipeline_runner.ETLPipelineRunner`.

    A pipeline (or gate) that reports ``status == "failed"`` is a RESULT, not a
    tool error: the caller inspects the returned ``status`` to decide what to do.
    ToolError is raised only for caller-fixable problems (see below).

    Args:
        config: Path to the pipeline YAML configuration file. Required and
            non-blank.
        run_date: Optional run-date string (e.g. ``"20260326"``) injected into
            template placeholders as ``{run_date}``.
        params: Optional dict of extra template variables (e.g.
            ``{"env": "staging"}``) expanded into ``{key}`` placeholders.

    Returns:
        The aggregate pipeline result dict the runner produces, with keys:

        - ``pipeline_name``: str — pipeline name from the config.
        - ``status``: str — ``"passed"`` or ``"failed"`` (overall verdict).
        - ``gates``: list[dict] — per-gate records, each with ``name``,
          ``status`` (``"passed"`` | ``"failed"``), ``steps`` (per-step
          results), and an optional ``error`` string when the gate failed.
        - ``started_at`` / ``finished_at``: str — ISO-8601 UTC timestamps.

    Raises:
        ToolError: For caller-fixable problems: *config* missing/blank, the
            config file not found, malformed pipeline YAML, or an unexpected
            runner failure.
    """
    if not config or not isinstance(config, str):
        raise ToolError("config is required and must be a non-empty string")

    if not Path(config).exists():
        raise ToolError(f"Pipeline config not found: {config}")

    # Lazy import keeps the heavy runner deps out of module import time and
    # mirrors the CLI command's import site.
    from src.pipeline.etl_pipeline_runner import ETLPipelineRunner

    runner = ETLPipelineRunner()

    try:
        result = runner.run_pipeline(
            config_path=config,
            run_date=run_date,
            params=params or {},
        )
    except FileNotFoundError as exc:
        # A referenced artefact (config or a path inside it) was missing —
        # caller-fixable.
        raise ToolError(str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface infra failures as ToolError
        # Malformed YAML, a pydantic validation error on the pipeline schema,
        # or any other runner failure. Surface as a caller-fixable tool error
        # rather than crashing the MCP transport.
        logger.exception("run_etl_pipeline tool failed for config=%r", config)
        raise ToolError(f"Pipeline run failed: {exc}") from exc

    # The runner aggregates raw service results that can embed numpy/pandas
    # scalars; normalise to JSON-native types so FastMCP can serialise the
    # response. Shape is preserved — this is not pipeline logic.
    return _to_json_native(result)


RUN_ETL_PIPELINE_DESCRIPTION = (
    "Run a multi-gate ETL validation pipeline defined in a YAML config and "
    "return its structured result — the per-gate pass/fail breakdown plus the "
    "overall pipeline verdict — so an agent can gate a data flow without "
    "shelling out to the CLI. Accepts the pipeline YAML config path (required), "
    "an optional run-date string injected as the {run_date} template variable, "
    "and an optional dict of extra template variables (e.g. {\"env\": "
    "\"staging\"}). Wraps the existing Valdo ETL pipeline orchestrator — the "
    "same code path the 'valdo run-etl-pipeline' CLI uses; the pipeline config "
    "selects which gates run and a gate's database step extracts from whichever "
    "backend DB_ADAPTER selects. Returns 'pipeline_name', 'status' ('passed' or "
    "'failed' — the overall verdict), 'gates' (a list of per-gate records, each "
    "with 'name', 'status', 'steps', and an optional 'error'), and the "
    "'started_at'/'finished_at' UTC timestamps. A pipeline or gate that reports "
    "status 'failed' is a RESULT, not a tool error — inspect 'status' to decide "
    "what to do next. The response carries no secrets. Raises a tool error only "
    "for caller-fixable problems: a missing/blank config argument, a config file "
    "that does not exist, malformed pipeline YAML, or an unexpected runner "
    "failure."
)

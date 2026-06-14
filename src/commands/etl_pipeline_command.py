"""CLI command handler for the ETL pipeline gate orchestrator (issue #156)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click


def run_etl_pipeline_command(
    config: str,
    run_date: str | None,
    params: str,
    output: str | None,
    logger: Any,
) -> None:
    """Execute an ETL pipeline validation gate run from the CLI.

    Loads the pipeline definition from *config*, delegates execution to
    :class:`~src.pipeline.etl_pipeline_runner.ETLPipelineRunner`, prints a
    human-readable summary, and optionally writes a JSON result report.

    Args:
        config: Path to the pipeline YAML configuration file.
        run_date: Optional run date string (e.g. ``"20260326"``) injected
            into template placeholders as ``{run_date}``.
        params: JSON string of extra template parameters
            (e.g. ``'{"env": "staging"}'``).
        output: Optional file path to write the JSON result report.
        logger: Logger instance used for informational and error messages.

    Raises:
        SystemExit: On configuration errors, pipeline load failures, or when
            the pipeline exits with a ``"failed"`` status.
    """
    from src.pipeline.etl_pipeline_runner import ETLPipelineRunner

    # Parse extra params.
    try:
        extra_params: dict[str, Any] = json.loads(params) if params else {}
    except json.JSONDecodeError as exc:
        logger.error("Invalid JSON in --params: %s", exc)
        raise SystemExit(1)

    runner = ETLPipelineRunner()

    try:
        result = runner.run_pipeline(
            config_path=config,
            run_date=run_date,
            params=extra_params,
        )
    except FileNotFoundError as exc:
        logger.error("Pipeline config not found: %s", exc)
        raise SystemExit(1)
    except Exception as exc:
        logger.error("Pipeline run failed unexpectedly: %s", exc)
        raise SystemExit(1)

    # Print summary.
    _print_summary(result)

    # Write optional report.
    if output:
        output_path = Path(output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8"
        )
        click.echo(f"\nReport written to: {output}")

    # Exit non-zero on failure so CI pipelines can gate on this command.
    if result.get("status") == "failed":
        raise SystemExit(1)


def _print_summary(result: dict[str, Any]) -> None:
    """Print a human-readable pipeline run summary to stdout.

    Args:
        result: Aggregate result dict returned by
            :meth:`~src.pipeline.etl_pipeline_runner.ETLPipelineRunner.run_pipeline`.
    """
    pipeline_name = result.get("pipeline_name", "unknown")
    status = result.get("status", "unknown")
    gates = result.get("gates", [])

    click.echo(f"\nPipeline: {pipeline_name}")
    click.echo(f"Gates run: {len(gates)}")
    click.echo("")

    for gate in gates:
        gate_name = gate.get("name", "?")
        gate_status = gate.get("status", "?")
        steps = gate.get("steps", [])
        passed = sum(1 for s in steps if s.get("status") == "passed")

        colour = "green" if gate_status == "passed" else "red"
        symbol = "PASS" if gate_status == "passed" else "FAIL"
        click.echo(
            click.style(
                f"  [{symbol}] {gate_name}  ({passed}/{len(steps)} steps passed)",
                fg=colour,
            )
        )
        if gate.get("error"):
            click.echo(click.style(f"         {gate['error']}", fg="red"))

    click.echo("")
    if status == "passed":
        click.echo(click.style("PIPELINE PASSED", fg="green", bold=True))
    else:
        click.echo(click.style("PIPELINE FAILED", fg="red", bold=True))
